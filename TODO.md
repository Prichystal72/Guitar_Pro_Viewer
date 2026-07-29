# TODO — rozpracováno

Stav ke commitu `85575dc` (2026-07-28, pushnuto na `origin/main`).
TODO.md samo přidáno v `7bba6eb`. Body 1+2 z "Co zbývá" dodělány v navazující
session téhož dne — zatím **necommitnuto** (viz `git status`).

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

## Co zbývá (stav k 2026-07-28, navazující session)

1. ✅ **HOTOVO** — vizuálně otestováno (screenshoty přes offscreen render,
   3 zoom úrovně) — čerstvý web-only import Let It Be (`bars_per_line=2,
   count_in_bars=1`, 80 BPM → 6s/řádek, 3s count-in). Layout se nepřehušťuje,
   count-in zóna vizuálně odlišená, oddělovače řádků nekolidují. Text v
   headless renderu vypadá jako obdélníčky (chybí fonty v offscreen Qt
   prostředí) — na reálném Windows s fonty to tak nebude, nejde o bug.
2. ✅ **HOTOVO** — `README.md`, `JSON_FORMAT.md` přepsané na aktuální model
   (`bars_per_line`/`count_in_bars`/`count_in_s`, beat-grid pro akordy, žádné
   slabiky). Opraveny i 2 zapomenuté zbytky přímo v kódu: tooltip
   `DisplayClipItem` a popisek dialogu editace textu v `timeline_editor.py`
   (`grep -rn -i slabik` teď ukazuje jen správné "žádné slabiky" věty +
   TODO.md samotné).
3. **STÁLE NEDOŘEŠENO** — "export dle označení". Uživatel při doupřesnění
   řekl výslovně "nevím, upřesním později" — **nezkoušet uhodnout/impl-
   ementovat, zeptat se znovu při dalším navázání.**
4. **ZASTARALÉ — scope GP byl uživatelem výslovně ROZŠÍŘEN v navazující
   session (stejný den).** Uživatel nahlásil konkrétní bug + zadal rozšíření:
   viz bod 6 níže. `gp_extract.py` pořád nezakládat bez dalšího zadání, ale
   `merge_with_web`/GP-merge cesta se rozšiřovat **smí a má**.
5. ✅ **ZAZÁLOHOVÁNO** (uživatel: "jen ho zazálohuj", rozhodnutí o smazání/
   přegenerování NEPADLO). Původní `Beatles (The) - Let It Be_test.json`
   (starý pre-BPM formát, `merged_web_gp: true`) je **beze změny na místě**;
   kopie navíc v `zaloha/Beatles - Let It Be_test.PRED-BPM-mrizkou.json`
   (nová složka, zatím necommitnutá — `git status` ji ukáže jako `??`).
   Nerozhodovat sám o smazání/přegenerování originálu, dokud uživatel
   neřekne který ze 3 postupů (přegenerovat / smazat / nechat) chce.
6. ✅ **HOTOVO (stejný den, další navázání)** — uživatel nahlásil bug: "po
   otevření gp se smaže text a akordy....ale ty mají zůstat, další stopy
   mají být z gp". Příčina: `_on_loaded()` (handler po dokončení GP loadu)
   bezpodmínečně volal `_populate_ui()` (čistě GP rebuild), i když už byla
   načtená karaoke data z webu — přepsalo to text/akordy. **Oprava:**
   `_on_loaded()` teď kontroluje `self._loaded_json is not None`; pokud ano,
   volá novou `_merge_gp_tracks_into_current()` místo `_populate_ui()` —
   text/akordy/`karaoke_lines` zůstanou přesně jak byly, z GP se jen
   PŘIDAJÍ stopy. Následně uživatel v dalším náva­zání explicitně řekl
   "import bicích a basy z gp ...vše se pridává na timeline" → přidána i
   **basa** (dřív jen bicí):
   - `guitar_pro_viewer.py`: nová `_gp_bass_for_merge()` (mirror
     `_gp_drums_for_merge()`) — basová stopa (4 struny, ne bicí), noty s
     `note_name`/`midi`/`string`/`fret`. Zapojeno do `_merge_gp_tracks_
     into_current()` I do `merge_with_web()` (obě GP-merge cesty).
   - `web_import.py`: nová `attach_bass()` (mirror `attach_drums()`).
   - `timeline_editor.py`: nová vizualizace `_draw_bass_lane()`/
     `_track_is_bass()` — noty jako úsečky ve 4 řadách (podle struny),
     analogicky k `_draw_drums_lane()`. Basa se do ESP32 exportu nepoužívá,
     je to jen referenční stopa v editoru.
   - `JSON_FORMAT.md` §3.5c zdokumentováno.
   - **Ověřeno end-to-end** (headless test, `QMessageBox` mockován ať
     neblokuje): web data → otevři GP → text/akordy beze změny (bit-přesná
     shoda JSON), bicí+basa přidány, `self.song` zůstává `None` (jako u
     čistě-web dat, takže export dál jede přes `timeline.to_json()`), scéna
     editoru se vykreslí bez chyby, `to_json()` export zachová `bass_
     timeline`/`drums_timeline` i nezměněný text/akordy.
   - **NEOVĚŘENO vizuálně na reálném Windows** (jen offscreen render dat/
     layoutu) — stálo by za to při příští session zkontrolovat, že basová
     stopa (4 řady, modré úsečky) vypadá v reálném UI čitelně, ne
     přeplácaně u písní s hodně notami.

7. ✅ **HOTOVO (stejný den, další navázání)** — uživatel sám otestoval v
   appce (`Queen - We Wil Rock You_edited.json`, ruční test soubor s
   jednoduchým stomp-stomp-clap vzorem: `Bass Drum 1` + `hihat_closed`) a
   nahlásil: "vidím bubny pouze jako trigger, chce to aby to bylo čitelné...
   co navrhuješ aby se nepřekrývaly v timeline? Dáme je nad sebe?"
   **Přepracována vizualizace stopy bicích v `timeline_editor.py`:**
   - Dřív: 3 PEVNÉ kategorie-řádky (činely/hi-hat, snare/tom, kick) sdílené
     víc bubny → různé bubny ve stejné kategorii se mohly vizuálně mísit.
   - Teď: **KAŽDÝ konkrétní buben (`ev["drum"]`) má vlastní podepsaný
     řádek** — nová `_drum_names_for(ti)` vrátí distinct jména seřazená dle
     `_drum_family()` (skupina pro pořadí, ne sdílení řádku). Nové
     `_drum_row_count(ti)`.
   - Úhozy: plné kruhové "note-head" tečky (`addEllipse`) místo tenkých
     svislých čárek — čitelnější při hustším rytmu.
   - **Dynamická výška stopy bicích** — `_relayout()` teď počítá výšku
     každé stopy zvlášť (`lane_h` dict, `DRUM_ROW_MIN_H = 20px`/řádek) místo
     pevného `PER_TRACK` pro všechny; běžné stopy/basa beze změny výšky.
     `_draw_drums_lane()`/`_draw_bass_lane()` mají nový nepovinný parametr
     `h_slot`.
   - **Ověřeno vizuálně** (offscreen render, 2 případy): 2-bubnový stomp-
     clap vzor → 2 čisté řádky, jasně vidět boom-boom-clap; 10-bubnová
     plná sada (Guns N' Roses) → 10 čitelně oddělených řádků, výška stopy
     se automaticky zvětšila (scene height ~604px), žádné mačkání popisků.

8. ✅ **HOTOVO (stejný den, další navázání)** — uživatel se zeptal, jak
   přeřadit jméno bubnu na konkrétní `.wav` na SD kartě ("mohu je
   přepřiřadit k bicím na sd kartě? jak se přiřazují?"). Zjištěno: dřív o
   tom `ESP32_KARAOKE_IMPLEMENTATION.md` §6b jen dával PŘÍKLAD natvrdo v C
   (nešlo by přeřadit bez přeflashování). **Navržen a implementován reálný
   mechanismus:**
   - Nový **[`drum_samples.json`](drum_samples.json)`** v repu — jeden
     globální soubor (platí pro všechny písně, uživatel zvolil tuto
     variantu explicitně), plochý slovník jméno bubnu → cesta k `.wav`,
     `"_default"` fallback. Vyplněno pro reálnou sadu **8 samplů**, které
     uživatel má (kick/snare/hihat_closed/hihat_open/crash/tom_hi/tom_mid/
     tom_low) — namapováno všech 47 GM Percussion jmen (25 explicitně +
     `_default` pro exotickou perkusi typu bonga/conga/agogo, co v rockové
     sadě nedává smysl).
   - Cesty: uživatel upřesnil dvakrát — nejdřív `/rock/*.wav` (SD root),
     pak opraveno na **`/drums/rock/*.wav`** (mapovací soubor sám
     `/drums/drum_samples.json`). **Pozor při dalším navázání:** pokud
     uživatel zmíní další sadu/kit (jazz, elektronika…), jde jen o nové
     cesty v tomhle JEDNOM souboru — žádná změna firmwaru ani schématu.
   - `ESP32_KARAOKE_IMPLEMENTATION.md` §6b přepsáno — dřívější "natvrdo v
     C" příklad nahrazen popisem SD-kartového mechanismu s odkazem na
     reálný soubor.

9. ✅ **HOTOVO (stejný den, na žádost "doplň dokumentaci, smaž co není
   pravda")** — širší audit `README.md`/`JSON_FORMAT.md`/
   `ESP32_KARAOKE_IMPLEMENTATION.md` proti skutečnému kódu. Nalezeno a
   opraveno:
   - README: "Do `tracks[]` jde jen zpěv + bicí" bylo neúplné (basa po GP
     mergu) → doplněna výjimka. Ctrl+M bullet byl matoucí (nerozlišoval od
     automatického merge při Ctrl+O po textu) → přepsáno na dvě jasně
     oddělené cesty. Chybělo zmínění nové vizualizace bicí/basa stopy a
     `drum_samples.json` v tabulce souborů.
   - `guitar_pro_viewer.py`: `_merge_gp_tracks_into_current()` nenastavovala
     `meta.merged_web_gp` (na rozdíl od `merge_with_web()`) — sjednoceno,
     obě GP-merge cesty teď flag nastaví konzistentně. Docstringy
     `merge_with_web`/`_merge_gp_tracks_into_current` doplněny o basu a
     jasné odlišení "přepíše" (Ctrl+M) vs. "jen přidá" (Ctrl+O po textu).
   - **JSON_FORMAT.md — nejzávažnější nález:** §3.6 příklad `karaoke_lines`
     měl `words[]` s JEDNÍM slovem ("tam,") místo celého textu řádku —
     pozůstatek ze staré per-slovní éry, neodpovídalo aktuálnímu modelu
     (jeden blok = celý řádek). Opraveno + doplněna poznámka, že `words[]`
     typicky nese jen 1 záznam = celý řádek.
   - **ESP32_KARAOKE_IMPLEMENTATION.md — nejzávažnější nález:** celá §5
     ("Řádek textu + zvýraznění slov") popisovala **zvýrazňování slovo po
     slově** jako výchozí chování — to už neplatí (text je od přechodu na
     bary/beaty jeden blok na řádek, žádná per-slovní timing data z
     výchozího importu). Přepsáno: zvýrazňování celého řádku (progress bar)
     jako výchozí, slovní wipe jen jako okrajový/legální edge-case. Opraven
     i TL;DR bod 5, akceptační test bod 4, a **celý konkrétní příklad v §11**
     (starý, syllable-based "Dej mi víc své lásky" nahrazen ověřeným
     "Let It Be" bars_per_line=2 příkladem s reálnými čísly z téhle session).
   - §5 "Změny oproti verzi 1": tvrzení "`tracks[]` má `has_tab`" bylo
     historicky pravdivé, ale zavádějící vůči SOUČASNÉMU schématu (`has_tab`
     byl později úplně odstraněn) → doplněna poznámka o pozdějším odstranění.

## Obecné pravidlo (důležité, neporušovat)

Časování se počítá **výhradně z tempa (BPM) a taktu** — nikdy z odhadů
založených na textu (slabiky, délka slova apod.). Uživatel to označil za
"kraviny" a trval na "hudební teorii". Pokud je potřeba automatika, vždy
vycházet z beat/bar mřížky, jinak nechat ruční úpravu tažením v editoru.
