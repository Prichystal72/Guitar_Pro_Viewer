#!/usr/bin/env python3
"""
Generátor testovacích Guitar Pro souborů: "Skákal pes" (česká lidová).
Vytvoří .gp3, .gp4 a .gp5 soubory s beat-textem (slabiky) a chord markery.

API poznámky pro PyGuitarPro 0.11:
  - Note(beat=<Beat>, ...)       beat je required
  - Chord(length=<int>, ...)     length = počet strun (required)
  - Beat.text = "string"        prostý string (ne objekt)
  - MeasureHeader.start         660 = první takt
"""

import guitarpro
from guitarpro import models as m

QUARTER = 960           # tiky na čtvrťovou notu
MEASURE = QUARTER * 4   # 4/4 takt = 3840 tiků

def t(dur_val: int) -> int:
    """Počet tiků pro danou délku noty (4=čtvrťová, 2=půlová, 1=celá)."""
    return QUARTER * 4 // dur_val

# ---------------------------------------------------------------------------
# Struktura písně: (chord_name, [(text, string, fret, dur_val), ...])
# String 2 (B=59 MIDI): D4=pražec 3, E4=5, F#4=7, G4=8, A4=10, B4=12
# Součet tiků na takt musí být MEASURE (3840)
#   4 čtvrťové:      4 × 960 = 3840
#   2 čtvrťové+půlová: 960+960+1920 = 3840
#   2 půlové:        1920+1920 = 3840
# ---------------------------------------------------------------------------
SONG_DATA = [
    # === SLOKA 1 ===
    # D          D / Skákal pes přes oves,
    ("D",  [("Ská-",  2, 3,  4), ("kal ",  2, 3,  4), ("pes ",  2, 10, 4), ("přes ", 2, 10, 4)]),
    ("D",  [("o-",   2, 12, 4), ("ves,",  2, 12, 4), ("",      2, 10, 2)]),
    # D            A7 / přes zelenou louku,
    ("D",  [("přes ", 2, 8,  4), ("ze-",   2, 8,  4), ("le-",   2, 7,  4), ("nou ",  2, 5,  4)]),
    ("A7", [("lou-",  2, 10, 2), ("ku,",   2, 10, 2)]),
    # A7         A7 / šel za ním myslivec,
    ("A7", [("šel ",  2, 3,  4), ("za ",   2, 3,  4), ("ním ",  2, 10, 4), ("mys-",  2, 10, 4)]),
    ("A7", [("li-",   2, 12, 4), ("vec,",  2, 12, 4), ("",      2, 10, 2)]),
    # A7         D / péro na klobouku.
    ("A7", [("pé-",   2, 8,  4), ("ro ",   2, 8,  4), ("na ",   2, 7,  4), ("klo-",  2, 5,  4)]),
    ("D",  [("bou-",  2, 10, 2), ("ku.",   2, 10, 2)]),

    # === SLOKA 2 ===
    # D           D / Pejsku náš, co děláš
    ("D",  [("Pej-",  2, 3,  4), ("sku ",  2, 3,  4), ("náš, ", 2, 10, 4), ("co ",   2, 10, 4)]),
    ("D",  [("dě-",   2, 12, 4), ("láš",   2, 12, 4), ("",      2, 10, 2)]),
    # D             A7 / žes tak vesel skáčeš.
    ("D",  [("žes ",  2, 8,  4), ("tak ",  2, 8,  4), ("ve-",   2, 7,  4), ("sel ",  2, 5,  4)]),
    ("A7", [("ská-",  2, 10, 2), ("češ.",  2, 10, 2)]),
    # A7            A7 / Řek bych vám, nevím sám...
    ("A7", [("Řek ",  2, 3,  4), ("bych ", 2, 3,  4), ("vám, ", 2, 10, 4), ("ne-",   2, 10, 4)]),
    ("A7", [("vím ",  2, 12, 4), ("sám…",  2, 12, 4), ("",      2, 10, 2)]),
    # A7             D / Hop – a skákal dále.
    ("A7", [("Hop ",  2, 8,  4), ("– ",    2, 8,  4), ("a ",    2, 7,  4), ("ská-",  2, 5,  4)]),
    ("D",  [("kal ",  2, 3,  4), ("dá-",   2, 5,  4), ("le.",   2, 3,  4), ("",      2, 3,  4)]),
]


def build_song() -> m.Song:
    song = m.Song()
    song.title = "Skákal pes"
    song.artist = "Lidová píseň"
    song.album = "Česká lidová"
    song.tempo = 100

    n = len(SONG_DATA)

    # --- Measure headers ---
    song.measureHeaders = []
    for i in range(n):
        hdr = m.MeasureHeader()
        hdr.number = i + 1
        hdr.start = QUARTER + i * MEASURE  # první takt na ticku 960
        hdr.timeSignature = m.TimeSignature()
        hdr.timeSignature.numerator = 4
        hdr.timeSignature.denominator = m.Duration(value=4)
        song.measureHeaders.append(hdr)

    # --- Track ---
    track = m.Track(song=song, number=1)
    track.name = "Kytara / Zpěv"
    track.fretCount = 24
    track.strings = [
        m.GuitarString(number=1, value=64),   # e4 (vysoké E)
        m.GuitarString(number=2, value=59),   # B3
        m.GuitarString(number=3, value=55),   # G3
        m.GuitarString(number=4, value=50),   # D3
        m.GuitarString(number=5, value=45),   # A2
        m.GuitarString(number=6, value=40),   # E2 (nízké E)
    ]
    track.channel = m.MidiChannel()
    track.channel.instrument = 25   # Acoustic Guitar (Steel)
    track.measures = []
    song.tracks = [track]

    # Song-level lyrics (GP4+ pouze — GP3 tuto sekci ignoruje)
    song.lyrics = m.Lyrics()
    song.lyrics.trackChoice = 1
    song.lyrics.lines = [
        m.LyricLine(startingMeasure=1, lyrics=(
            "Skákal pes přes oves přes zelenou louku "
            "šel za ním myslivec péro na klobouku "
            "Pejsku náš co děláš žes tak vesel skáčeš "
            "Řek bych vám nevím sám Hop a skákal dále"
        )),
        m.LyricLine(startingMeasure=1, lyrics=''),
        m.LyricLine(startingMeasure=1, lyrics=''),
        m.LyricLine(startingMeasure=1, lyrics=''),
        m.LyricLine(startingMeasure=1, lyrics=''),
    ]

    # --- Measures, Voices, Beats, Notes ---
    for i, (chord_name, beats_data) in enumerate(SONG_DATA):
        hdr = song.measureHeaders[i]
        measure = m.Measure(track=track, header=hdr)
        voice = m.Voice(measure=measure)
        measure.voices = [voice]

        # GP5 potřebuje 2 hlasy — přidáme prázdný druhý hlas
        voice2 = m.Voice(measure=measure)
        empty_beat = m.Beat(voice=voice2)
        empty_beat.start = hdr.start
        empty_beat.duration = m.Duration(value=1)  # celá nota
        empty_beat.status = m.BeatStatus.empty
        voice2.beats = [empty_beat]
        measure.voices.append(voice2)

        voice.beats = []

        tick = hdr.start
        first_beat = True

        for text, string_num, fret, dur_val in beats_data:
            beat = m.Beat(voice=voice)
            beat.start = tick
            beat.duration = m.Duration(value=dur_val)
            beat.effect = m.BeatEffect()
            beat.status = m.BeatStatus.normal   # KLÍČOVÉ: bez toho writer beat přeskočí

            # Chord marker na první beat taktu
            if first_beat and chord_name:
                chord = m.Chord(length=6)
                chord.name = chord_name
                chord.firstFret = 1          # povinné pro zápis (firstFret=None → TypeError)
                chord.strings = [-1] * 6     # -1 = struna nehraje (jen název, ne diagram)
                beat.effect.chord = chord
                first_beat = False

            # Beat text (slabika/slovo)
            if text and text.strip():
                beat.text = text.strip()

            # Nota (Note.beat je required v PyGuitarPro 0.11)
            note = m.Note(beat=beat)
            note.string = string_num
            note.value = fret
            note.type = m.NoteType.normal
            note.velocity = m.Velocities.forte
            note.duration = 95
            note.effect = m.NoteEffect()
            beat.notes = [note]

            voice.beats.append(beat)
            tick += t(dur_val)

        track.measures.append(measure)

    return song


def main():
    print("Generuji testovací Guitar Pro soubory: 'Skákal pes'")
    print(f"  Taktů: {len(SONG_DATA)}, Tempo: 100 BPM, Ladění: standardní")
    print()

    song = build_song()

    formats = [
        ((3, 0, 0), '.gp3', 'Guitar Pro 3'),
        ((4, 0, 6), '.gp4', 'Guitar Pro 4'),
        ((5, 1, 0), '.gp5', 'Guitar Pro 5'),
    ]

    ok_count = 0
    for version, ext, label in formats:
        filename = f"skakal_pes{ext}"
        song.versionTuple = version
        try:
            guitarpro.write(song, filename, encoding='cp1250')

            # Ověření zpětným načtením
            test = guitarpro.parse(filename, encoding='cp1250')
            n_text = sum(
                1 for tr in test.tracks
                for mes in tr.measures
                for v in mes.voices
                for b in v.beats
                if b.text
            )
            n_chord = sum(
                1 for tr in test.tracks
                for mes in tr.measures
                for v in mes.voices
                for b in v.beats
                if b.effect and b.effect.chord and b.effect.chord.name
            )
            print(f"  ✓ {filename:25s} ({label})")
            print(f"    text events: {n_text}, chord events: {n_chord}")
            ok_count += 1
        except Exception as ex:
            import traceback
            print(f"  ✗ {filename:25s} ({label})")
            print(f"    CHYBA: {ex}")
            traceback.print_exc()
        print()

    print(f"Výsledek: {ok_count}/{len(formats)} souborů vytvořeno.")
    if ok_count:
        print("\nOtevři v Guitar Pro Viewer:")
        for _, ext, _ in formats:
            fn = f"skakal_pes{ext}"
            print(f"  python guitar_pro_viewer.py \"{fn}\"")


if __name__ == '__main__':
    main()
