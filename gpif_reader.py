"""
gpif_reader.py — čtení Guitar Pro 6 (`.gpx`) a Guitar Pro 7+ (`.gp`).

Oba formáty nesou partituru jako XML `score.gpif`, jen v jiném obalu:

- **GP7+ `.gp`** — obyčejný ZIP, soubor `Content/score.gpif`.
- **GP6 `.gpx`** — vlastní kontejner: `BCFZ` (komprimovaný) nebo `BCFS`
  (nekomprimovaný) „souborový systém" v sektorech po 4 KiB. Komprese je
  jednoduchá LZ77 varianta po bitech (postup převzatý z otevřených
  implementací — alphaTab, TuxGuitar).

Výsledkem `load_song(path)` je `guitarpro.models.Song` — stejné objekty, jaké
vrací `pyguitarpro` pro GP3–GP5, takže zbytek aplikace (časování, bicí, basa,
text, akordy) funguje beze změny. Z GPIF se převádí jen to, co aplikace
používá: takty (metrum, repetice, zakončení, skoky, sekce), stopy (ladění,
bicí, nástroj), beaty (délka, tuplety, text, akord, změna tempa) a noty
(struna/pražec, MIDI výška bicích, ligatura, bend/vibrato/slide/HOPO).
"""
from __future__ import annotations

import io
import struct
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction

import guitarpro.models as gpm

QUARTER = 960   # ticky na čtvrťovou dobu (stejně jako pyguitarpro)


# ---------------------------------------------------------------------------
# kontejnery
# ---------------------------------------------------------------------------

class _BitReader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.bit = pos * 8

    def read_bit(self) -> int:
        byte = self.data[self.bit >> 3]          # IndexError = konec dat
        val = (byte >> (7 - (self.bit & 7))) & 1
        self.bit += 1
        return val

    def read_bits(self, n: int) -> int:           # MSB první
        v = 0
        for i in range(n):
            v |= self.read_bit() << (n - 1 - i)
        return v

    def read_bits_reversed(self, n: int) -> int:  # LSB první
        v = 0
        for i in range(n):
            v |= self.read_bit() << i
        return v


def _bcfz_decompress(data: bytes) -> bytes:
    """`data` = obsah ZA hlavičkou „BCFZ". Vrací rozbalená data (začínají „BCFS")."""
    expected = struct.unpack_from("<i", data, 0)[0]
    src = _BitReader(data, 4)
    out = bytearray()
    try:
        while len(out) < expected:
            if src.read_bits(1):
                word = src.read_bits(4)
                offset = src.read_bits_reversed(word)
                size = src.read_bits_reversed(word)
                start = len(out) - offset
                chunk = out[start:start + min(offset, size)]
                out += chunk
            else:
                size = src.read_bits_reversed(2)
                for _ in range(size):
                    out.append(src.read_bits(8))
    except IndexError:
        pass   # data skončila dřív než hlášená délka — vezmi, co je
    return bytes(out)


def _bcfs_files(data: bytes) -> dict[str, bytes]:
    """`data` = obsah ZA hlavičkou „BCFS". Vrací {jméno: obsah}."""
    sector = 0x1000
    files: dict[str, bytes] = {}
    off = sector
    while off + 3 < len(data):
        if struct.unpack_from("<i", data, off)[0] == 2:          # záznam souboru
            name = data[off + 4: off + 4 + 127].split(b"\0", 1)[0].decode("latin-1")
            size = struct.unpack_from("<i", data, off + 0x8C)[0]
            buf = bytearray()
            k = 0
            while True:
                ptr = off + 0x94 + 4 * k
                if ptr + 4 > len(data):
                    break
                sec = struct.unpack_from("<i", data, ptr)[0]
                if sec == 0:
                    break
                buf += data[sec * sector: sec * sector + sector]
                k += 1
            files[name] = bytes(buf[:size])
        off += sector
    return files


def read_gpif(path: str) -> bytes:
    """Vytáhne `score.gpif` z `.gp` (ZIP) nebo `.gpx` (BCFZ/BCFS)."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith("score.gpif")), None)
            if name is None:
                raise ValueError("V souboru .gp chybí Content/score.gpif")
            return z.read(name)
    if raw[:4] == b"BCFZ":
        raw = _bcfz_decompress(raw[4:])
    if raw[:4] != b"BCFS":
        raise ValueError("Neznámý kontejner Guitar Pro 6 (čekal jsem BCFZ/BCFS)")
    files = _bcfs_files(raw[4:])
    if "score.gpif" not in files:
        raise ValueError("V souboru .gpx chybí score.gpif")
    return files["score.gpif"]


def is_gpif_container(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    return head[:2] == b"PK" or head in (b"BCFZ", b"BCFS")


# ---------------------------------------------------------------------------
# GPIF (XML) -> guitarpro.models.Song
# ---------------------------------------------------------------------------

# GP6 bicí bez MIDI čísla: (Element, Variation) -> artikulace (alphaTab,
# PercussionMapper._gp6ElementAndVariationToArticulation). Řádek = Element,
# sloupec = Variation 0..2.
_GP6_DRUM = [
    (35, 35, 35),     # 0 kopák
    (38, 91, 37),     # 1 virbl: úder, rimshot, sidestick
    (99, 100, 99),    # 2 kravský zvon nízký
    (56, 100, 56),    # 3 kravský zvon střední
    (102, 103, 102),  # 4 kravský zvon vysoký
    (43, 43, 43),     # 5 tom velmi nízký
    (45, 45, 45),     # 6 tom nízký
    (47, 47, 47),     # 7 tom střední
    (48, 48, 48),     # 8 tom vysoký
    (50, 50, 50),     # 9 tom velmi vysoký
    (42, 92, 46),     # 10 hi-hat: zavřený, napůl, otevřený
    (44, 44, 44),     # 11 hi-hat pedál
    (57, 98, 57),     # 12 crash střední
    (49, 97, 49),     # 13 crash vysoký
    (55, 95, 55),     # 14 splash
    (51, 93, 127),    # 15 ride: úder, hrana, zvon
    (52, 96, 52),     # 16 china
]
# Rozšířené artikulace GP (nad rámec GM) -> GM MIDI. Podle `OutputMidiNumber`
# v sadě bicích, kterou GP7 ukládá přímo do souboru (rimshot 91 -> 38 apod.).
_GP_EXT_TO_GM = {91: 38, 92: 46, 93: 51, 95: 55, 96: 52, 97: 49, 98: 57,
                 99: 56, 100: 56, 102: 56, 103: 56, 127: 53}

_NOTE_VALUES = {"Whole": 1, "Half": 2, "Quarter": 4, "Eighth": 8, "16th": 16,
                "32nd": 32, "64th": 64, "128th": 128, "256th": 256}
# druhé číslo v hodnotě automace tempa = nota, ke které se BPM vztahuje
_TEMPO_UNIT_TO_QUARTERS = {1: 0.5, 2: 1.0, 3: 1.5, 4: 2.0, 5: 3.0}


def _txt(el, path: str, default: str = "") -> str:
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None and found.text else default


def _ids(text: str | None) -> list[int]:
    return [int(x) for x in (text or "").split()]


def _camel_to_words(name: str) -> str:
    """'DaSegnoAlCoda' -> 'Da Segno al Coda' (jména jako u pyguitarpro GP5)."""
    import re
    words = re.findall(r"[A-Z][a-z]*", name)
    return " ".join("al" if w == "Al" else w for w in words)


def _props(el) -> dict:
    """{jméno vlastnosti: element Property} z `<Properties>`."""
    out = {}
    props = el.find("Properties") if el is not None else None
    if props is not None:
        for p in props.findall("Property"):
            out[p.get("name")] = p
    return out


def _rhythm_ticks(rh) -> tuple[Fraction, gpm.Duration]:
    """Délka rytmu v tickách (přesně, i dvojitá tečka a vnořené tuplety)
    + nejbližší `Duration` pyguitarpro (ta neumí dvojitou tečku ani vnořené
    tuplety — začátky beatů se proto počítají z přesných ticků, ne z ní)."""
    value = _NOTE_VALUES.get(_txt(rh, "NoteValue"), 4)
    ticks = Fraction(QUARTER * 4, value)
    dot = rh.find("AugmentationDot")
    dots = int(dot.get("count", "1")) if dot is not None else 0
    if dots == 1:
        ticks *= Fraction(3, 2)
    elif dots >= 2:
        ticks *= Fraction(7, 4)
    tuplet = gpm.Tuplet(1, 1)
    for tag in ("PrimaryTuplet", "SecondaryTuplet"):
        t = rh.find(tag)
        if t is not None:
            num, den = int(t.get("num", "1")), int(t.get("den", "1"))
            if num > 0 and den > 0:
                ticks *= Fraction(den, num)
                if tag == "PrimaryTuplet":
                    tuplet = gpm.Tuplet(enters=num, times=den)
    return ticks, gpm.Duration(value=value, isDotted=dots >= 1, tuplet=tuplet)


def _is_percussion(track_el) -> bool:
    gm = track_el.find("GeneralMidi")
    if gm is not None and gm.get("table") == "Percussion":
        return True
    if _txt(track_el, "InstrumentSet/Type").lower() == "drumkit":
        return True
    inst = track_el.find("Instrument")
    if inst is not None and (inst.get("ref") or "").startswith("drmkt"):
        return True
    ch = (_txt(track_el, "GeneralMidi/PrimaryChannel")
          or _txt(track_el, "MidiConnection/PrimaryChannel"))
    return ch == "9"


def _tuning(track_el) -> list[int]:
    for p in track_el.iter("Property"):
        if p.get("name") == "Tuning":
            pitches = _ids(_txt(p, "Pitches"))
            if pitches:
                return pitches
    return []


def _program(track_el) -> int:
    for path in ("GeneralMidi/Program", "Sounds/Sound/MIDI/Program"):
        v = _txt(track_el, path)
        if v.isdigit():
            return int(v)
    return 0


def _chord_names(track_el) -> dict[int, str]:
    """{id diagramu: jméno akordu} z DiagramCollection (GP6 i GP7)."""
    out: dict[int, str] = {}
    for p in track_el.iter("Property"):
        if p.get("name") in ("DiagramCollection", "DiagramWorkingSet"):
            for it in p.iter("Item"):
                try:
                    out.setdefault(int(it.get("id")), (it.get("name") or "").strip())
                except (TypeError, ValueError):
                    pass
    return out


def _drum_midi(props: dict) -> int | None:
    """MIDI číslo bubnu: GP7 `Midi`, GP6 `Element`/`Variation`, jinak (GP6
    stejně jako GP5) pražec = MIDI číslo."""
    midi = None
    if "Midi" in props:
        midi = int(_txt(props["Midi"], "Number") or 0)
    elif "Element" in props:
        el = int(_txt(props["Element"], "Element") or 0)
        var = int(_txt(props["Variation"], "Variation") or 0) if "Variation" in props else 0
        if 0 <= el < len(_GP6_DRUM):
            midi = _GP6_DRUM[el][var if 0 <= var < 3 else 0]
    elif "Fret" in props:
        midi = int(_txt(props["Fret"], "Fret") or 0)
    if midi is None:
        return None
    return _GP_EXT_TO_GM.get(midi, midi)


def _apply_note_effects(note: gpm.Note, note_el, props: dict) -> None:
    e = note.effect
    if note_el.find("Vibrato") is not None:
        e.vibrato = True
    if note_el.find("LetRing") is not None:
        e.letRing = True
    if "PalmMuted" in props:
        e.palmMute = True
    if "HopoOrigin" in props:
        e.hammer = True
    if "Slide" in props:
        e.slides = [gpm.SlideType.shiftSlideTo]
    if "Bended" in props:
        def val(name):
            # GPIF: 100 = celý tón; pyguitarpro BendPoint: 1 = čtvrttón
            return int(round(float(_txt(props[name], "Float") or 0) / 25)) if name in props else 0
        points = [gpm.BendPoint(0, val("BendOriginValue")),
                  gpm.BendPoint(6, val("BendMiddleValue")),
                  gpm.BendPoint(12, val("BendDestinationValue"))]
        e.bend = gpm.BendEffect(type=gpm.BendType.bend,
                                value=max(p.value for p in points) * 25, points=points)


def _distribute_track_lyrics(track: gpm.Track, track_el) -> None:
    """Text stopy GP6 (`<Lyrics><Line><Text>`, slabiky oddělené '-', slova
    mezerou, `<Offset>` = první takt) -> `beat.text` po slovech: celé slovo
    na beat jeho PRVNÍ slabiky (slabiky jdou po notách = rytmus zpěvu)."""
    import re
    line = next((l for l in track_el.findall("Lyrics/Line") if _txt(l, "Text")), None)
    if line is None:
        return
    offset = int(_txt(line, "Offset") or 0)
    words = []
    for w in re.split(r"\s+", _txt(line, "Text")):
        syl = w.split("-")
        if "".join(syl):
            words.append(("".join(syl), max(1, len(syl))))
    beats = [b for m in track.measures[offset:] for b in (m.voices[0].beats if m.voices else [])
             if b.notes and any(n.type != gpm.NoteType.tie for n in b.notes)]
    i = 0
    for word, n_syl in words:
        if i >= len(beats):
            break
        if not beats[i].text:
            beats[i].text = word
        i += n_syl


def _articulation_outputs(track_el) -> list[int | None]:
    """GP7: výstupní MIDI číslo každé artikulace sady bicích, v pořadí, na
    které odkazuje `<InstrumentArticulation>` noty (prvky × jejich artikulace)."""
    out: list[int | None] = []
    for art in track_el.findall("InstrumentSet/Elements/Element/Articulations/Articulation"):
        v = _txt(art, "OutputMidiNumber")
        out.append(int(v) if v.lstrip("-").isdigit() else None)
    return out


def _read_note(beat: gpm.Beat, nel, track: gpm.Track,
               articulations: list[int | None] | None = None) -> gpm.Note | None:
    props = _props(nel)
    note = gpm.Note(beat)
    tie = nel.find("Tie")
    if tie is not None and tie.get("destination") == "true":
        note.type = gpm.NoteType.tie
    elif "Muted" in props:
        note.type = gpm.NoteType.dead
    else:
        note.type = gpm.NoteType.normal
    if track.isPercussionTrack:
        # GP7: hraje se VÝSTUPNÍ číslo artikulace (`Midi` u noty je vstupní —
        # Stairway: ride 51 v notě, 59 v přehrávání i v MIDI exportu z GP)
        midi = None
        art = _txt(nel, "InstrumentArticulation")
        if articulations and art.isdigit() and int(art) < len(articulations):
            midi = articulations[int(art)]
        if midi is None:
            midi = _drum_midi(props)
        else:
            midi = _GP_EXT_TO_GM.get(midi, midi)
        if midi is None:
            return None
        note.value = midi
        note.string = 1
        return note
    if "String" not in props or "Fret" not in props:
        return None
    n_str = len(track.strings)
    gpif_string = int(_txt(props["String"], "String") or 0)
    # GPIF: struna 0 = nejhlubší; pyguitarpro: struna 1 = nejvyšší
    note.string = max(1, min(n_str, n_str - gpif_string))
    note.value = int(_txt(props["Fret"], "Fret") or 0)
    _apply_note_effects(note, nel, props)
    return note


def load_song(path: str) -> gpm.Song:
    """Načte `.gp`/`.gpx` jako `guitarpro.models.Song`."""
    root = ET.fromstring(read_gpif(path))
    score = root.find("Score")
    song = gpm.Song()
    song.title = _txt(score, "Title")
    song.artist = _txt(score, "Artist")
    song.album = _txt(score, "Album")
    song.tracks = []
    song.measureHeaders = []

    def by_id(tag):
        el = root.find(tag)
        return {int(x.get("id")): x for x in el} if el is not None else {}
    rhythms, notes, beats = by_id("Rhythms"), by_id("Notes"), by_id("Beats")
    voices, bars = by_id("Voices"), by_id("Bars")
    mbs_el = root.find("MasterBars")
    master_bars = list(mbs_el) if mbs_el is not None else []

    # --- hlavičky taktů ---
    tick = QUARTER
    for i, mb in enumerate(master_bars):
        num, den = (_txt(mb, "Time") or "4/4").split("/")
        h = gpm.MeasureHeader()
        h.number = i + 1
        h.start = tick
        h.timeSignature = gpm.TimeSignature(numerator=int(num),
                                            denominator=gpm.Duration(value=int(den)))
        rep = mb.find("Repeat")
        if rep is not None:
            h.isRepeatOpen = rep.get("start") == "true"
            if rep.get("end") == "true":
                # GPIF `count` = celkový počet zaznění; pyguitarpro = počet opakování
                h.repeatClose = max(1, int(rep.get("count") or 2) - 1)
        alt = _ids(_txt(mb, "AlternateEndings"))
        if alt:
            h.repeatAlternative = sum(1 << (a - 1) for a in alt if a > 0)
        target = _txt(mb, "Directions/Target")
        jump = _txt(mb, "Directions/Jump")
        if target:
            h.direction = gpm.DirectionSign(_camel_to_words(target))
        if jump:
            h.fromDirection = gpm.DirectionSign(_camel_to_words(jump))
        section = _txt(mb, "Section/Text") or _txt(mb, "Section/Letter")
        if section:
            h.marker = gpm.Marker(title=section)
        song.measureHeaders.append(h)
        tick += h.length

    # --- tempo: automace hlavní stopy (takt + poměrná pozice v taktu) ---
    tempo_changes: list[tuple[int, int]] = []
    for a in root.findall("MasterTrack/Automations/Automation"):
        if _txt(a, "Type") != "Tempo":
            continue
        parts = _txt(a, "Value").split()
        if not parts:
            continue
        bpm = float(parts[0])
        unit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 2
        bpm *= _TEMPO_UNIT_TO_QUARTERS.get(unit, 1.0)
        bar = int(_txt(a, "Bar") or 0)
        pos = float(_txt(a, "Position") or 0)
        if 0 <= bar < len(song.measureHeaders):
            h = song.measureHeaders[bar]
            tempo_changes.append((int(h.start + pos * h.length), int(round(bpm))))
    tempo_changes.sort()
    song.tempo = next((b for t, b in tempo_changes if t <= QUARTER), 120)
    # čte guitar_pro_viewer.build_tempo_map (GP3-5 mají tempo v mix table beatu)
    song.extraTempoChanges = tempo_changes

    # --- stopy ---
    tracks_el = root.find("Tracks")
    for ti, tel in enumerate(list(tracks_el) if tracks_el is not None else []):
        track = gpm.Track(song=song, number=ti + 1)
        track.name = _txt(tel, "Name") or ("Track %d" % (ti + 1))
        track.isPercussionTrack = _is_percussion(tel)
        pitches = _tuning(tel) or ([0] * 6 if track.isPercussionTrack
                                   else [40, 45, 50, 55, 59, 64])
        track.strings = [gpm.GuitarString(number=i + 1, value=v)
                         for i, v in enumerate(reversed(pitches))]
        track.channel = gpm.MidiChannel(channel=9 if track.isPercussionTrack else 0,
                                        effectChannel=9 if track.isPercussionTrack else 1,
                                        instrument=_program(tel))
        chord_names = _chord_names(tel)
        articulations = _articulation_outputs(tel) if track.isPercussionTrack else None
        track.measures = []
        has_beat_lyrics = False
        for mi, mb in enumerate(master_bars):
            header = song.measureHeaders[mi]
            measure = gpm.Measure(track, header)
            measure.voices = []
            bar_ids = _ids(_txt(mb, "Bars"))
            bar_el = bars.get(bar_ids[ti]) if ti < len(bar_ids) else None
            for vid in (_ids(_txt(bar_el, "Voices")) if bar_el is not None else []):
                if vid < 0 or vid not in voices:
                    continue
                voice = gpm.Voice(measure)
                voice.beats = []
                pos = Fraction(0)
                # Přírazy (GraceNotes) nezabírají čas taktu — čekají na hlavní
                # beat a zařadí se podle toho, jak je hraje Guitar Pro (ověřeno
                # na MIDI exportu z GP, Stairway to Heaven):
                #   BeforeBeat — příraz těsně PŘED dobou, hlavní nota na dobu,
                #   OnBeat     — příraz NA dobu, hlavní nota hned po něm
                #                (posunutá o délku přírazu).
                # Vynechat je nejde: příraz u bicích je skutečný úder (flam),
                # basa/kytara by přišly o notu.
                pending_grace: list[tuple[gpm.Beat, Fraction, bool]] = []
                for bid in _ids(_txt(voices[vid], "Beats")):
                    bel = beats.get(bid)
                    if bel is None:
                        continue
                    rh_ref = bel.find("Rhythm")
                    rh = rhythms.get(int(rh_ref.get("ref"))) if rh_ref is not None else None
                    ticks, duration = (_rhythm_ticks(rh) if rh is not None
                                       else (Fraction(QUARTER), gpm.Duration()))
                    beat = gpm.Beat(voice)
                    grace = bel.find("GraceNotes")
                    main_shift = Fraction(0)
                    if grace is None and pending_grace:
                        before = [(g, t) for g, t, on in pending_grace if not on]
                        on_beat = [(g, t) for g, t, on in pending_grace if on]
                        back = sum((t for _g, t in before), Fraction(0))
                        for gbeat, gticks in before:
                            gbeat.start = header.start + int(round(max(Fraction(0), pos - back)))
                            back -= gticks
                            voice.beats.append(gbeat)
                        for gbeat, gticks in on_beat:
                            gbeat.start = header.start + int(round(pos + main_shift))
                            main_shift += gticks
                            voice.beats.append(gbeat)
                        pending_grace = []
                    beat.start = header.start + int(round(pos + main_shift))
                    beat.duration = duration
                    beat.notes = []
                    for nid in _ids(_txt(bel, "Notes")):
                        if nid in notes:
                            note = _read_note(beat, notes[nid], track, articulations)
                            if note is not None:
                                beat.notes.append(note)
                    beat.status = gpm.BeatStatus.normal if beat.notes else gpm.BeatStatus.rest
                    lyric = _txt(bel, "Lyrics/Line")
                    if lyric:
                        beat.text = lyric
                        has_beat_lyrics = True
                    elif _txt(bel, "FreeText"):
                        beat.text = _txt(bel, "FreeText")
                    cid = _txt(bel, "Chord")
                    if cid.isdigit() and chord_names.get(int(cid)):
                        beat.effect.chord = gpm.Chord(length=len(track.strings),
                                                      name=chord_names[int(cid)])
                    if grace is not None:
                        beat.isGrace = True   # příraz (pro kontroly; pyguitarpro ho nezná)
                        pending_grace.append((beat, ticks, (grace.text or "").strip() == "OnBeat"))
                        continue
                    voice.beats.append(beat)
                    pos += ticks
                # příraz na konci taktu bez hlavního beatu — patří na konec taktu
                for gbeat, _gt, _ob in pending_grace:
                    gbeat.start = header.start + int(round(pos))
                    voice.beats.append(gbeat)
                measure.voices.append(voice)
            if not measure.voices:
                empty = gpm.Voice(measure)
                empty.beats = []
                measure.voices = [empty]
            track.measures.append(measure)
        if not has_beat_lyrics and not track.isPercussionTrack:
            _distribute_track_lyrics(track, tel)
        song.tracks.append(track)
    _fix_cp1250_mojibake(song)
    return song


def _fix_cp1250_mojibake(song: gpm.Song) -> None:
    """Texty psané v české Windows (cp1250), ale uložené jako latin-1:
    „tì" místo „tě", „v\\x9au" místo „všu" (Kabát – Pohoda.gpx). Opravuje se
    jen soubor, kde je aspoň jeden znak \\x80–\\x9f — ty v latin-1 nejsou
    tisknutelné, takže v opravdovém textu se nevyskytují. Bez téhle podmínky
    by se rozbil skutečný latin-1 text (francouzské „è" -> „č")."""
    texts = [song.title, song.artist, song.album]
    texts += [t.name for t in song.tracks]
    texts += [h.marker.title for h in song.measureHeaders if h.marker is not None]
    texts += [b.text for t in song.tracks for m in t.measures for v in m.voices
              for b in v.beats if isinstance(b.text, str)]
    if not any(any("\x80" <= ch <= "\x9f" for ch in (x or "")) for x in texts):
        return

    def fix(x):
        if not x:
            return x
        try:
            return x.encode("latin-1").decode("cp1250")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return x
    song.title, song.artist, song.album = fix(song.title), fix(song.artist), fix(song.album)
    for t in song.tracks:
        t.name = fix(t.name)
        for m in t.measures:
            for v in m.voices:
                for b in v.beats:
                    if isinstance(b.text, str):
                        b.text = fix(b.text)
    for h in song.measureHeaders:
        if h.marker is not None:
            h.marker.title = fix(h.marker.title)


def load_any(path: str, encoding: str = "cp1250") -> gpm.Song:
    """GP3-GP5 přes pyguitarpro, GP6 `.gpx` a GP7+ `.gp` přes `load_song`."""
    if is_gpif_container(path):
        return load_song(path)
    import guitarpro
    try:
        return guitarpro.parse(path, encoding=encoding)
    except Exception:
        return guitarpro.parse(path, encoding="utf-8")
