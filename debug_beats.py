import sys
sys.stdout.reconfigure(encoding='utf-8')

import importlib.util
spec = importlib.util.spec_from_file_location("ctg", "create_test_gp.py")
ctg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctg)

song = ctg.build_song()
t = song.tracks[0]

print("=== Beats v paměti PŘED zápisem ===")
for m_idx in range(4):
    m = t.measures[m_idx]
    v = m.voices[0]
    print(f"M{m_idx+1}: {len(v.beats)} beats")
    for i, b in enumerate(v.beats):
        print(f"  beat {i}: start={b.start}  dur={b.duration.value}  text={b.text!r}")
