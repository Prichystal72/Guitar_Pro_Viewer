import sys
sys.stdout.reconfigure(encoding='utf-8')
import requests
import guitarpro
from web_import import parse_pisnicky_akordy, chart_to_gp_song, build_karaoke_json, save_gp_and_json
import json

for slug, tempo in [
    ('lidove-pisne/skakal-pes', 100),
    ('jarek-nohavica/milionar', 120),
]:
    url = f'https://pisnicky-akordy.cz/{slug}'
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    r.encoding = 'utf-8'
    title, artist, detected = parse_pisnicky_akordy(r.text, url=url)

    n_c = sum(1 for d in detected if d['type'] == 'chord')
    n_l = sum(1 for d in detected if d['type'] == 'lyric')
    print(f'\n=== {title} / {artist} ===')
    print(f'Chord řádků: {n_c}, Lyric řádků: {n_l}')

    try:
        song = chart_to_gp_song(detected, title=title, artist=artist, tempo=tempo)
        base = title.replace(' ', '_').replace('/', '_')[:30]
        gp_path, json_path = save_gp_and_json(
            song, detected, title=title, artist=artist,
            tempo=tempo, base_path=base, source_url=url,
        )
        # Ověření
        song2 = guitarpro.parse(gp_path, encoding='cp1250')
        n_text = sum(1 for t in song2.tracks for m in t.measures
                     for v in m.voices for b in v.beats if b.text)
        with open(json_path, encoding='utf-8') as f:
            data = json.load(f)
        print(f'✓ GP4: {gp_path}  ({n_text} text events)')
        print(f'✓ JSON: {json_path}  ({len(data["karaoke_lines"])} karaoke řádků, '
              f'{len(data["chords_timeline"])} chord events)')
        print('Prvních 5 karaoke řádků:')
        for kl in data['karaoke_lines'][:5]:
            print(f'  [{kl["start_s"]:6.2f}s] {kl["chords"]} → {kl["text"]}')
    except Exception as e:
        print(f'✗ CHYBA: {e}')
        import traceback; traceback.print_exc()


print(f'Název:     {title}')
print(f'Interpret: {artist}')
print(f'Řádků:     {len(detected)} ({sum(1 for d in detected if d["type"]=="chord")} chord, '
      f'{sum(1 for d in detected if d["type"]=="lyric")} lyric)')
print()

# Sestav GP song
song = chart_to_gp_song(detected, title=title, artist=artist, tempo=100)
print(f'GP Song: {song.title}, {len(song.tracks)} stopa, {len(song.tracks[0].measures)} taktů')

# Kontrola beatů v prvním taktu
m0 = song.tracks[0].measures[0]
print(f'Takt 1: {len(m0.voices[0].beats)} beatů')
for b in m0.voices[0].beats:
    chord = b.effect.chord.name if b.effect and b.effect.chord else ''
    print(f'  beat start={b.start} text={b.text!r} chord={chord!r}')

# Ulož GP4 + JSON
gp_path, json_path = save_gp_and_json(
    song, detected, title=title, artist=artist, tempo=100,
    base_path='skakal_pes_web',
    source_url='https://pisnicky-akordy.cz/lidove-pisne/skakal-pes'
)
print()
print(f'✓ GP4:  {gp_path}')
print(f'✓ JSON: {json_path}')

# Ověř zpětné načtení GP4
song2 = guitarpro.parse(gp_path, encoding='cp1250')
n_text = sum(1 for t in song2.tracks for m in t.measures
             for v in m.voices for b in v.beats if b.text)
print(f'  GP4 zpětné načtení: {n_text} text events')

# Shrnutí JSON
with open(json_path, encoding='utf-8') as f:
    data = json.load(f)
print(f'  JSON: {len(data["karaoke_lines"])} karaoke řádků, '
      f'{len(data["chords_timeline"])} chord events, '
      f'{len(data["lyrics_timeline"])} lyric events')
print()
print('Karaoke řádky (prvních 5):')
for kl in data['karaoke_lines'][:5]:
    print(f'  [{kl["start_s"]:6.2f}s] {kl["chords"]} → {kl["text"]}')
