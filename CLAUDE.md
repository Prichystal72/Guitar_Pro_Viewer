# Guitar_Pro_Viewer – PySide6 aplikace pro karaoke JSON

Komunikace česky, stručně. Prohlíží `.gp3/.gp4/.gp5` (pyguitarpro) i GP6 `.gpx` / GP7+ `.gp` (`gpif_reader.py`), exportuje JSON (text + akordy + bicí) pro `Arduino\guitar_karaoke_system`,
umí import akordů z webu (`web_import.py`), MIDI (`midi_import.py`) a dávkové stažení interpreta.

## Spuštění
- `pip install -r requirements.txt`, `python guitar_pro_viewer.py`. Větev aktuálně `novy-format-a-tri-cesty`.
- Testy: `python -m pytest tests -q` (offscreen Qt, ukázková data v kořeni). Celá GP knihovna:
  `$env:GP_LIBRARY="<cesta>"; python -m pytest tests/test_library.py`. Kotvy z nahrávky: `tests/data/anchors/*.json` (vzor `VZOR.json.txt`).
- Překlady v `lang/*.json` (cs, en, de, es, pl) – nový jazyk = nový soubor, bez zásahu do Pythonu. Nastavení v `settings.json`.

## Pravidla formátu (neměnit bez dohody, ESP32 na tom stojí)
- Formát popisuje `JSON_FORMAT.md` (ploché osy `lyrics_timeline`/`chords_timeline`/`drums_timeline`, `display_timeline`, `section`, `line`).
  Referenční příklad `pisne/Iron Maiden - Run To the Hills/schema_v1_reference_iron_maiden.json`. Migrace starých souborů: `format_migrate.py`.
- Časování **jen z tempa a taktu**, žádné odhady ze slabik. Text = jedna jednotka na řádek, pevný počet taktů (`bars_per_line`), volitelný `count_in_bars`.
- Do `tracks[]` jen zpěv + bicí; tabulatura se neexportuje.
- Rozdělané úkoly: `TODO.md`. Ukázkové písně v `pisne/<Interpret - Název>/` (GP, text, nahrávka, JSON
  pohromadě) – neprocházet, nemazat. Nové písně ukládat taky tam, ne do kořene. Ke každé písni
  `zdroje/` + `ZDROJE.txt` (odkud jsou GP soubory) a `texty/` + `TEXTY.txt` (stažené weby,
  `python tools/stahni_texty.py`). Vzory formátů bez vazby na píseň: `tests/data/formaty_textu/`.
- Weby s ochranou proti robotům (supermusic.cz JS výzva, pisnicky-akordy.cz 403) neobcházet —
  stránku uloží uživatel ručně z prohlížeče.
- GP knihovna uživatele: `C:\Users\prich\Music\Guitar Pro Tabs` (~48 tis. souborů, české v `___czech`,
  19 z 46 českých je `.gp`/`.gpx` = GP6/7, čte `gpif_reader.py`). Testovací výběr: `python tools/select_library.py`
  → `tests/data/library_selection.txt`.
- Audio je jen pro kontrolu člověkem: zarovnání (`drum_align.py`) výsledek NABÍDNE, použije se až po potvrzení.
  VÝJIMKA (rozhodnutí uživatele 2026-10-31): „Časová osa ▸ Napasovat tempo na nahrávku“ — u nahrávek bez
  klikačky smí nahrávka řídit časování (tempo po taktech), ale jen na vyžádání, po potvrzení, s Ctrl+Z.
  Ověřeno: umělá nahrávka (známý posun −7,3 s / 98,5 %) → nalezeno přesně; `jasna_zprava.wav` i živák
  nedrží tempo (úseky ±0,3–0,5 s) — jedním posunem nejdou zarovnat celé, GP 65 BPM sedí v průměru (100,4 %).
- Testy čekající na dekodér zvuku: čekat přes `QEventLoop`, ne `processEvents` + `time.sleep` (dekodér hladoví).
- Rozložení textu z nahrávky: podle AKORDŮ (`chord_align.py`) + nástup zpěvu (Demucs) — Jasná 77 %, Let It Be 63 %
  řádků do 1 doby proti lrclib; pevné takty 0–5 %. Velké modely (MMS-FA 1,2 GB) uživatel ODMÍTL. Text z lrclib
  uživatel NECHCE používat, jen k měření. Zarovnání potřebuje akordy celé písně v pořadí.
  Text musí odpovídat zpívanému — zkrácené refrény rozbaluje `web_import.expand_repeats`.
- Projekt = `.kproj` (JSON + blok `project` s relativními cestami), pro ESP32 export `.json`. Staré pokusy v `pisne/*/archiv/`.
- PySide6 zamčeno na 6.10.0 (6.11.1 cukalo přehrávání).

