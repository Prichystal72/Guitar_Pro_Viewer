# TODO — rozpracováno

Stav ke commitu `85575dc` (2026-07-28, pushnuto na `origin/main`).

## Právě dokončeno

Přechod na **pravidelné BPM časování** (žádné odhady ze slabik/délky textu):

- `web_import.py`: `build_karaoke_json()` — smazána slabiková heuristika.
  Nově `bars_per_line` (pevný počet taktů na každý řádek, default 2) a
  `count_in_bars` (úvodní takty ticha pro odklikání metronomu, default 1).
  Akordy se umísťují na nejbližší BEAT podle pořadí slova. Meta nese
  `beats_per_measure`, `time_signature`, `bars_per_line`, `count_in_bars`,
  `count_in_s`.
- `WebImportDialog`: 3 živě propojené panely (text / rozpoznané řádky /
  JSON náhled) + pole "Taktů/řádek" a "Úvodní takty". Primární tlačítko
  **"✅ Použít v editoru"** zavře dialog a rovnou nahraje píseň do timeline
  (přes `_present_karaoke_data`). Uložení GP4+JSON na disk zůstává vedlejší
  volitelné tlačítko.
- `guitar_pro_viewer.py`: nový hlavní vstupní bod **"📝 Nová píseň — z
  webu/vlastní text…" (Ctrl+N)** — první a zvýrazněná akce v menu i toolbaru.
- `timeline_editor.py`: smazány heuristické auto-time metody (`_distribute`,
  `_syllable_weight`, `_auto_time_line_even`, `_auto_time_fit_clips`).
  Zůstává jen mřížkové přichycení ("⏱ Na mřížku"). Pravítko kreslí skutečné
  takty (silná čára + "Takt N") a beaty (tenká čára) podle tempa, ne obecné
  sekundy. Count-in zóna je vizuálně odlišená (žluté šrafování). Snap
  ("Přichytit") počítá z tempa (1/4 beatu…1 takt), ne pevné sekundy.

Ověřeno: Let It Be přes web import (živě stažené, 80 BPM) → všech 21 řádků
má přesně 6.0s (2 takty), count_in_s=3.0. Ověřeno jen na úrovni dat/kódu,
**ne vizuálně** (viz níže).

## Co zbývá

1. **Vizuálně otestovat** Let It Be v timeline editoru (screenshot) — ověřit
   čitelnost nového pravítka (takty/beaty/count-in zóna), že se nepřehušťuje
   při různém zoomu.
2. **Aktualizovat `README.md` a `JSON_FORMAT.md`** — pořád zmiňují staré
   "slabikové časování" (`grep -n slabik` v obou souborech). Popsat nově
   `bars_per_line`, `count_in_bars`/`count_in_s`, beat-grid pro akordy.
3. **Doupřesnit "export dle označení"** — uživatel zmínil, že export musí
   jít "dle označení", přesný požadavek nebyl doupřesněn (možná: export
   respektující count-in/grid marking, nebo export vybraného
   úseku/sekce/rozsahu taktů). Zeptat se při dalším navázání.
4. **GP soubory (bicí/basa/sólo) jsou zatím MIMO SCOPE.** Uživatel
   explicitně: "zatím řešíme stále jen text a akordy a to z webu nebo z
   prostého textu." Nezačínat `gp_extract.py` ani rozšiřovat
   `merge_with_web`, dokud nepotvrdí pokračování tímhle směrem.
5. `Beatles (The) - Let It Be_test.json` v repu je z PŘED touto změnou
   (nepravidelné délky řádků, `merged_web_gp: true`, starý pipeline) —
   možná přegenerovat novým pravidelným výstupem nebo smazat, až uživatel
   potvrdí.

## Obecné pravidlo (důležité, neporušovat)

Časování se počítá **výhradně z tempa (BPM) a taktu** — nikdy z odhadů
založených na textu (slabiky, délka slova apod.). Uživatel to označil za
"kraviny" a trval na "hudební teorii". Pokud je potřeba automatika, vždy
vycházet z beat/bar mřížky, jinak nechat ruční úpravu tažením v editoru.
