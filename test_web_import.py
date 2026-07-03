"""Rychlý test web_import bez GUI."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')

from web_import import detect_chord_chart, align_chords_to_words, chart_to_gp_song, save_gp_and_json

TEXT = """
D          D
Skákal pes přes oves,
D            A7
přes zelenou louku,
A7         A7
šel za ním myslivec,
A7         D
péro na klobouku.

D           D
Pejsku náš, co děláš
D             A7
žes tak vesel skáčeš.
A7            A7
Řek bych vám, nevím sám…
A7             D
Hop – a skákal dále.
"""

detected = detect_chord_chart(TEXT.splitlines())

print("=== Detekce ===")
for d in detected:
    if d['type'] != 'blank':
        print(f"  [{d['type']:6s}] {d['text']}")

print("\n=== Column alignment (první pár) ===")
chord_l = "D          D"
lyric_l = "Skákal pes přes oves,"
pairs = align_chords_to_words(chord_l, lyric_l)
for word, chord in pairs:
    print(f"  {word!r:15s} chord={chord!r}")

print("\n=== Chord chart ===")
from explore_gp import print_chord_chart as _pcc

# Simulate using detected data
active = ""
for d in detected:
    if d['type'] == 'blank':
        print()
    elif d['type'] == 'chord':
        print(f"\033[34m{d['text']}\033[0m")
    elif d['type'] == 'lyric':
        print(d['text'])

print("\n=== Budování GP songu ===")
song = chart_to_gp_song(detected, title="Skákal pes", artist="Lidová", tempo=100)
print(f"  Taktů: {len(song.measureHeaders)}")
print(f"  Beats v M1: {len(song.tracks[0].measures[0].voices[0].beats)}")
for b in song.tracks[0].measures[0].voices[0].beats:
    print(f"    start={b.start} text={b.text!r} chord={b.effect.chord.name if b.effect and b.effect.chord else ''!r}")

print("\n=== Ukládám test_web_import.gp4 + .json ===")
try:
    gp, js = save_gp_and_json(song, detected, "Skákal pes", "Lidová", 100, "test_web_import", "test://local")
    print(f"  GP4:  {gp}")
    print(f"  JSON: {js}")
    data = json.load(open(js, encoding='utf-8'))
    print(f"  karaoke_lines: {len(data['karaoke_lines'])}")
    print(f"  chords:        {len(data['chords_timeline'])}")
    print(f"  lyrics:        {len(data['lyrics_timeline'])}")
    print("\n  Ukázka karaoke_lines[0]:")
    print(json.dumps(data['karaoke_lines'][0], indent=4, ensure_ascii=False))
except Exception as e:
    import traceback; traceback.print_exc()
