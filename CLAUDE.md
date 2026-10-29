# Guitar_Pro_Viewer – PySide6 aplikace pro karaoke JSON

Komunikace česky, stručně. Prohlíží `.gp3/.gp4/.gp5`, exportuje JSON (text + akordy + bicí) pro `Arduino\guitar_karaoke_system`,
umí import akordů z webu (`web_import.py`), MIDI (`midi_import.py`) a dávkové stažení interpreta.

## Spuštění
- `pip install -r requirements.txt`, `python guitar_pro_viewer.py`. Větev aktuálně `novy-format-a-tri-cesty`.
- Překlady v `lang/*.json` (cs, en, de, es, pl) – nový jazyk = nový soubor, bez zásahu do Pythonu. Nastavení v `settings.json`.

## Pravidla formátu (neměnit bez dohody, ESP32 na tom stojí)
- Formát popisuje `JSON_FORMAT.md` (ploché osy `lyrics_timeline`/`chords_timeline`/`drums_timeline`, `display_timeline`, `section`, `line`).
  Referenční příklad `schema_v1_reference_iron_maiden.json`. Migrace starých souborů: `format_migrate.py`.
- Časování **jen z tempa a taktu**, žádné odhady ze slabik. Text = jedna jednotka na řádek, pevný počet taktů (`bars_per_line`), volitelný `count_in_bars`.
- Do `tracks[]` jen zpěv + bicí; tabulatura se neexportuje.
- Rozdělané úkoly: `TODO.md`. Soubory `.mp3/.gp*/.mid` ve složce jsou ukázková data – neprocházet, nemazat.
