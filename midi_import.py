"""
midi_import.py — Import ze standardního MIDI souboru (.mid/.midi) do
karaoke JSON (format_version: 2, stejný tvar jako `_build_karaoke_json()`
v guitar_pro_viewer.py — jde rovnou do `_present_karaoke_data()`).

Na rozdíl od GP importu (guitar_pro_viewer.py, `pyguitarpro` umí jen
GP3–GP5) MIDI soubor nenese chords/lyrics metadata ani přesnou notaci
(string/fret, techniku hry) — jen výšku/čas/rychlost noty. Import proto:

  - bicí/basu/sólo POZNÁ heuristicky podle jména stopy + MIDI kanálu/
    programu (`_classify_track`),
  - u basy/sóla dopočítá string/fret standardním laděním kytary/basy
    (`_note_to_string_fret`) — preferuje pozici blízko PŘEDCHOZÍ noty, aby
    tabulatura nedělala zbytečné skoky po hmatníku,
  - sólo rovnou vygeneruje ASCII tabulaturu (`ascii_tab.
    segment_and_render_tab_blocks`, stejný renderer jako GP import),
  - text ani akordy NEEXTRAHUJE (MIDI je nemá) — uživatel je doplní ručně
    nebo sloučením s webem (`merge_with_web`).

Typický zdroj: placený export ze Songsterr Plus (MIDI/Guitar Pro/MP3/WAV) —
viz diskuze v konverzaci o tom, proč MIDI a ne scraping.
"""

from __future__ import annotations

import bisect
from pathlib import Path

import mido

TICKS_PER_BEAT = 960          # vnitřní rozlišení editoru (viz music_grid.py)
FIRST_BEAT_TICK = 960         # stejná konvence jako u GP (první doba na ticku 960)
DEFAULT_BPM = 120

_DRUM_CHANNEL = 9             # MIDI kanál 10 (1-based) == index 9 (0-based) je GM bicí
_BASS_PROGRAMS = set(range(32, 40))    # GM 33–40: Acoustic/Electric/Fretless/Slap/Synth Bass

_MIDI_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Standardní ladění kytary (6 strun, string 1 = nejvyšší/nejtenčí — stejná
# konvence jako GP/`note.string`) a 4strunné basy — MIDI číslo prázdné struny.
GUITAR_TUNING = [64, 59, 55, 50, 45, 40]   # e4 B3 G3 D3 A2 E2
BASS_TUNING = [43, 38, 33, 28]             # G2 D2 A1 E1


def midi_to_name(midi: int) -> str:
    if midi <= 0:
        return "?"
    return _MIDI_NOTE_NAMES[midi % 12]


# ---------------------------------------------------------------------------
# tick <-> sekundy
# ---------------------------------------------------------------------------

def _merged_tempo_events(mid: mido.MidiFile) -> list[tuple[int, int]]:
    """Sloučí `set_tempo` eventy ze VŠECH stop do jedné mapy (abs_tick, bpm)
    — u vícestopého souboru bývá „dirigentská" tempo mapa zapsaná duplicitně
    ve víc stopách (ověřeno na reálném souboru), takže se prostě dedupluje
    podle ticku."""
    events: dict[int, int] = {}
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                events[abs_tick] = round(mido.tempo2bpm(msg.tempo))
    if not events:
        events[0] = DEFAULT_BPM
    events.setdefault(0, events[min(events)])
    return sorted(events.items())


def _seconds_fn(tempo_events: list[tuple[int, int]], ticks_per_beat: int):
    """Vrátí funkci `tick -> seconds`, po částech podle tempo mapy."""
    marks_tick: list[int] = []
    marks_sec: list[float] = []
    marks_bpm: list[int] = []
    seconds = 0.0
    prev_tick, prev_bpm = 0, tempo_events[0][1]
    for tick, bpm in tempo_events:
        seconds += (tick - prev_tick) * (60.0 / prev_bpm) / ticks_per_beat
        marks_tick.append(tick)
        marks_sec.append(seconds)
        marks_bpm.append(bpm)
        prev_tick, prev_bpm = tick, bpm

    def to_seconds(tick: int) -> float:
        idx = max(0, bisect.bisect_right(marks_tick, tick) - 1)
        return marks_sec[idx] + (tick - marks_tick[idx]) * (60.0 / marks_bpm[idx]) / ticks_per_beat

    return to_seconds


def _bpm_at(tempo_events: list[tuple[int, int]], tick: int) -> int:
    idx = max(0, bisect.bisect_right([t for t, _ in tempo_events], tick) - 1)
    return tempo_events[idx][1]


# ---------------------------------------------------------------------------
# stopy: jméno, program, kanál, noty
# ---------------------------------------------------------------------------

def _track_name(track) -> str:
    for msg in track:
        if msg.type == "track_name":
            return msg.name or ""
    return ""


def _track_program_and_channel(track) -> tuple[int | None, int | None]:
    for msg in track:
        if msg.type == "program_change":
            return msg.program, msg.channel
    for msg in track:
        if msg.type == "note_on" and msg.velocity > 0:
            return None, msg.channel
    return None, None


def _classify_track(track) -> str:
    """Heuristika druhu stopy — jméno má přednost před GM programem, protože
    reálné exporty (viz Songsterr) často nesou popisná jména i tam, kde GM
    nástroj sám o sobě nic neřekne (např. zpěv hraný na saxofonový patch,
    protože GM žádný "vokál" nemá)."""
    name = _track_name(track).lower()
    program, channel = _track_program_and_channel(track)
    is_drum_channel = channel == _DRUM_CHANNEL or any(
        msg.type == "note_on" and msg.velocity > 0 and msg.channel == _DRUM_CHANNEL
        for msg in track)
    if is_drum_channel or any(k in name for k in ("drum", "bicí", "bicich", "percussion")):
        return "drums"
    if any(k in name for k in ("vocal", "vox", "zpěv", "zpev", "voice", "singer")):
        return "vocal"
    if any(k in name for k in ("bass", "basa")) or (program is not None and program in _BASS_PROGRAMS):
        return "bass"
    if any(k in name for k in ("solo", "sólo", "lead guitar", "lead gtr")):
        return "solo_guitar"
    return "guitar"


def _track_notes(track) -> list[tuple[int, int, int, int]]:
    """`(start_tick, end_tick, channel, pitch)` pro jednu MIDI stopu —
    spáruje `note_on`/`note_off` (i `note_on` s velocity 0, což je běžný
    alias pro note-off)."""
    notes: list[tuple[int, int, int, int]] = []
    open_notes: dict[tuple[int, int], list[int]] = {}
    abs_tick = 0
    for msg in track:
        abs_tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            open_notes.setdefault((msg.channel, msg.note), []).append(abs_tick)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (msg.channel, msg.note)
            starts = open_notes.get(key)
            if starts:
                notes.append((starts.pop(0), abs_tick, msg.channel, msg.note))
    return notes


# ---------------------------------------------------------------------------
# výška -> struna/pražec (preferuje pozici blízko předchozí noty)
# ---------------------------------------------------------------------------

# Sdílené s GP importem a s transpozicí ručně psaných bloků — viz ascii_tab.py.
from ascii_tab import note_to_string_fret as _note_to_string_fret


def _tab_string_labels(tuning: list[int]) -> list[str]:
    labels = [midi_to_name(n) for n in tuning]
    if labels:
        labels[0] = labels[0].lower()
    return labels


# ---------------------------------------------------------------------------
# hlavní vstupní bod
# ---------------------------------------------------------------------------

def build_karaoke_json_from_midi(path: str) -> tuple[dict, list[str]]:
    """Vrátí `(data, warnings)` — `data` ve stejném tvaru jako
    `_build_karaoke_json()` (format_version 2), rovnou pro
    `GuitarProViewer._present_karaoke_data()`."""
    from ascii_tab import segment_and_render_tab_blocks

    mid = mido.MidiFile(path)
    warnings: list[str] = []
    tempo_events = _merged_tempo_events(mid)
    to_seconds = _seconds_fn(tempo_events, mid.ticks_per_beat)

    tempo_map = [
        {"tick": FIRST_BEAT_TICK + round(tick / mid.ticks_per_beat * TICKS_PER_BEAT), "bpm": bpm}
        for tick, bpm in tempo_events
    ]

    drums_timeline: list[dict] = []
    bass_timeline: list[dict] = []
    tab_timeline: list[dict] = []
    tracks: list[dict] = []
    max_time_s = 0.0
    n_solo_tracks = 0

    from guitar_pro_viewer import drum_name  # sdílená GM percussion mapa

    for t_idx, track in enumerate(mid.tracks):
        notes = _track_notes(track)
        if not notes:
            continue
        ti = t_idx + 1
        track_type = _classify_track(track)
        name = _track_name(track) or f"Stopa {ti}"

        if track_type == "vocal":
            # MIDI nenese text — stopu zaevidujeme, ale nic z ní neextrahujeme
            # (uživatel doplní text ručně/webem, viz merge_with_web).
            tracks.append({"index": ti, "name": name, "type": "vocal", "is_drums": False})
            max_time_s = max(max_time_s, to_seconds(max(e for _s, e, _c, _p in notes)))
            continue

        if track_type == "drums":
            for start, _end, _ch, pitch in sorted(notes):
                t = to_seconds(start)
                drums_timeline.append({
                    "time_s": t, "duration_s": 0.15,
                    "drum": drum_name(pitch), "midi": pitch,
                    "track_index": ti, "source": "gp",
                })
                max_time_s = max(max_time_s, t)
            tracks.append({"index": ti, "name": name, "type": "drums", "is_drums": True})
            continue

        if track_type == "bass":
            prev_string, prev_fret = None, None
            for start, end, _ch, pitch in sorted(notes):
                t = to_seconds(start)
                dur = max(0.05, to_seconds(end) - t)
                string_no, fret = _note_to_string_fret(pitch, BASS_TUNING, prev_string, prev_fret)
                prev_string, prev_fret = string_no, fret
                bass_timeline.append({
                    "time_s": t, "duration_s": round(dur, 3),
                    "note_name": midi_to_name(pitch), "midi": pitch,
                    "string": string_no, "fret": fret,
                    "track_index": ti, "source": "gp",
                })
                max_time_s = max(max_time_s, t + dur)
            tracks.append({"index": ti, "name": name, "type": "bass", "is_drums": False})
            continue

        if track_type == "solo_guitar":
            n_solo_tracks += 1
            prev_string, prev_fret = None, None
            solo_notes: list[tuple[float, int, int, str, int]] = []
            for start, _end, _ch, pitch in sorted(notes):
                t = to_seconds(start)
                bpm = _bpm_at(tempo_events, start)
                string_no, fret = _note_to_string_fret(pitch, GUITAR_TUNING, prev_string, prev_fret)
                prev_string, prev_fret = string_no, fret
                # MIDI nenese hammer-on/slide/vibrato jako GP `note.effect` —
                # technika se u MIDI importu nedopočítává (v1 omezení).
                solo_notes.append((t, string_no, fret, "", bpm))
                max_time_s = max(max_time_s, t)
            tab_timeline.extend(segment_and_render_tab_blocks(
                solo_notes, _tab_string_labels(GUITAR_TUNING), ti, tuning=GUITAR_TUNING))
            tracks.append({"index": ti, "name": name, "type": "solo_guitar", "is_drums": False})
            continue

        # "guitar"/ostatní — MIDI nemá akordové značky jako GP, takže se
        # tahle stopa jen zaeviduje (uživatel ji může v editoru přeřadit).
        tracks.append({"index": ti, "name": name, "type": track_type, "is_drums": False})
        max_time_s = max(max_time_s, to_seconds(max(e for _s, e, _c, _p in notes)))

    if n_solo_tracks == 0:
        warnings.append("V MIDI souboru se nenašla žádná stopa rozpoznaná jako "
                        "sólo/lead kytara — ASCII tabulatura se nevygenerovala.")
    if not tracks:
        warnings.append("MIDI soubor neobsahuje žádné rozpoznatelné noty.")

    beats_per_bar = 4
    for track in mid.tracks:
        for msg in track:
            if msg.type == "time_signature":
                beats_per_bar = msg.numerator
                break
        break

    result = {
        "meta": {
            "format_version": 2,
            "title": Path(path).stem,
            "artist": "",
            "album": "",
            "tempo_bpm": tempo_events[0][1],
            "ticks_per_beat": TICKS_PER_BEAT,
            "beats_per_measure": beats_per_bar,
            "source_file": Path(path).name,
            "has_line_structure": True,
        },
        "tempo_map": tempo_map,
        "meter_map": [{"bar": 1, "num": beats_per_bar, "den": 4}],
        "linear_map": [],
        "tracks": tracks,
        "lyrics_timeline": [],
        "chords_timeline": [],
        "drums_timeline": sorted(drums_timeline, key=lambda e: e["time_s"]),
        "bass_timeline": sorted(bass_timeline, key=lambda e: e["time_s"]),
        "tab_timeline": sorted(tab_timeline, key=lambda e: e["time_s"]),
        "karaoke_lines": [],
    }
    return result, warnings


def midi_drum_bars(path: str) -> list[tuple[int, float, int]]:
    """Bicí z MIDI po taktech zdroje: [(takt 1-based, poměrná pozice v taktu
    0..1, GM číslo bubnu)] — pro průvodce „Karaoke z nahrávky", který údery
    posadí na takty NAHRÁVKY (tempo MIDI se nepoužije). Takty podle
    `time_signature` (bez něj 4/4); bicí = stopy, které `_classify_track`
    pozná jako bicí, a vůbec všechny noty na kanálu 10."""
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat or 480
    sigs: dict[int, tuple[int, int]] = {}
    for track in mid.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == "time_signature":
                sigs[t] = (msg.numerator, msg.denominator)
    sigs.setdefault(0, sigs[min(sigs)] if sigs else (4, 4))
    marks = sorted(sigs.items())
    # začátky taktů v tickách (úseky metra)
    bars: list[tuple[int, int]] = []          # (tick začátku, délka taktu v tickách)
    end = max((sum(m.time for m in tr) for tr in mid.tracks), default=0)
    for i, (tick, (num, den)) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else end + 1
        length = max(1, int(round(num * tpb * 4 / den)))
        t = tick
        while t < stop:
            bars.append((t, length))
            t += length
    starts = [b[0] for b in bars]
    import bisect
    out: list[tuple[int, float, int]] = []
    seen: set[tuple[int, int]] = set()
    for track in mid.tracks:
        drums_track = _classify_track(track) == "drums"
        for start, _end, ch, pitch in _track_notes(track):
            if not (ch == _DRUM_CHANNEL or drums_track) or pitch < 27:
                continue
            if (start, pitch) in seen:
                continue
            seen.add((start, pitch))
            b = max(0, bisect.bisect_right(starts, start) - 1)
            out.append((b + 1, (start - bars[b][0]) / bars[b][1], pitch))
    out.sort()
    return out
