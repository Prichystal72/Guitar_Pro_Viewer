import sys
import os
import guitarpro

# UTF-8 výstup (fix pro Windows konzoli s cp850/cp1252)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_beat_text(beat) -> str:
    t = beat.text
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    return getattr(t, 'value', None) or ""


def get_chord_name(beat) -> str:
    try:
        if beat.effect and beat.effect.chord:
            return beat.effect.chord.name or ""
    except Exception:
        pass
    return ""


def print_chord_chart(song, measures_per_line: int = 2):
    """
    Vytiskne chord chart: akordy nad textem.
    Akord se zobrazí pouze:
      a) jako první na daném řádku
      b) při změně uprostřed řádku
    """
    # Najdi stopu s největším počtem textu (nebo první ne-bicí)
    main_track = None
    max_texts = -1
    for t in song.tracks:
        if t.isPercussionTrack:
            continue
        cnt = sum(1 for m in t.measures for v in m.voices[:1]
                  for b in v.beats if get_beat_text(b))
        if cnt > max_texts:
            max_texts = cnt
            main_track = t
    if main_track is None:
        main_track = song.tracks[0] if song.tracks else None
    if main_track is None:
        print("  (žádná stopa)")
        return

    measures = main_track.measures
    active_chord = ""

    for line_start in range(0, len(measures), measures_per_line):
        line_measures = measures[line_start:line_start + measures_per_line]

        beats = [
            (get_beat_text(b), get_chord_name(b))
            for m in line_measures
            for v in m.voices[:1]
            for b in v.beats
        ]

        text_parts = []
        chord_events = []   # (char_pos, chord_name)
        char_pos = 0
        line_chord_shown = False

        for text, chord in beats:
            if chord:
                if not line_chord_shown:
                    chord_events.append((char_pos, chord))
                    active_chord = chord
                    line_chord_shown = True
                elif chord != active_chord:
                    chord_events.append((char_pos, chord))
                    active_chord = chord

            part = (text if text.endswith(('-', ' ')) else text + ' ') if text else ''
            text_parts.append(part)
            char_pos += len(part)

        full_text = ''.join(text_parts).rstrip()

        if not full_text.strip() and not chord_events:
            continue

        # Chord řádek
        if chord_events:
            width = max(
                len(full_text) + 2,
                chord_events[-1][0] + len(chord_events[-1][1]) + 1
            )
            chord_chars = [' '] * width
            for pos, name in chord_events:
                for i, c in enumerate(name):
                    if pos + i < len(chord_chars):
                        chord_chars[pos + i] = c
            print(''.join(chord_chars).rstrip())

        if full_text.strip():
            print(full_text)
        print()


# ---------------------------------------------------------------------------
# Načtení souborů
# ---------------------------------------------------------------------------

FILES = [
    ("Guns N' Roses - Knockin On Heavens Door (ver 5).gp3", 'cp1250'),
    ("skakal_pes.gp4", 'cp1250'),
]

for gp_file, enc in FILES:
    import os
    if not os.path.exists(gp_file):
        continue

    print("=" * 60)
    print(f"Soubor: {gp_file}")
    print("=" * 60)

    song = guitarpro.parse(gp_file, encoding=enc)

    print(f"Název:  {song.title}")
    print(f"Autor:  {song.artist}")
    print(f"Tempo:  {song.tempo} BPM")
    print(f"Stopy:  {len(song.tracks)}")
    for i, t in enumerate(song.tracks):
        tuning = [s.value for s in t.strings]
        print(f"  [{i+1}] {t.name!r:20s} strings={len(t.strings)} measures={len(t.measures)} drums={t.isPercussionTrack}")
    print()

    # Scan text + chords
    found_something = False
    for i, t in enumerate(song.tracks):
        for m_idx, m in enumerate(t.measures):
            for v in m.voices[:1]:
                for b in v.beats:
                    txt = get_beat_text(b)
                    chd = get_chord_name(b)
                    if txt or chd:
                        found_something = True
                        print(f"  Track {i+1} M{m_idx+1}: text={txt!r:15s} chord={chd!r}")
    if not found_something:
        print("  (žádný text ani akordy nenalezeny)")
    print()

    # Chord chart
    print("--- CHORD CHART ---")
    print_chord_chart(song)
    print()
