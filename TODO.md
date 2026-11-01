# TODO — rozpracováno

Stav ke commitu `85575dc` (2026-07-28, pushnuto na `origin/main`).
TODO.md samo přidáno v `7bba6eb`. Body 3–10 (merge fix, basa, ikonky bicích,
expanze repetic, dokumentační audit) vznikly v navazujících sessions
2026-07-28/29 a jsou součástí commitu, který následuje hned za touto
aktualizací TODO.md — viz `git log` pro přesný hash.

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

10. ✅ **HOTOVO (stejný den, mid-audit interrupt)** — uživatel zasáhl uprostřed
    dokumentačního auditu bodu 9 se dvěma NOVÝMI konkrétními bugy, které
    ukázaly, že `timeline_editor.py` mezitím (mimo viditelnou historii této
    session — zjevně buď ruční úprava uživatelem, nebo dřívější kolo, co
    jsem nezaznamenal do TODO) dostalo **skutečné PNG/SVG ikonky bicích**
    (`assets/drum_icons/`, `_drum_icon_key`/`_drum_icon_pixmap`) a
    **tlačítka pro fázový posun stopy bicích** (`_add_drum_shift_controls`/
    `shift_drum_track`) — ŽÁDNÉ z toho nebylo v mém TODO záznamu (bod 7
    mluvil o "note-head tečkách", `DRUM_ROW_MIN_H=20`, realita byla ikonky
    + `DRUM_ROW_MIN_H=74`). **Poučení pro příště:** před editací
    `_draw_drums_lane`/okolí vždy nejdřív PŘEČÍST aktuální kód, ne se
    spoléhat na vlastní TODO poznámky — stav se očividně umí posunout mimo
    zaznamenanou historii.
    - **Bug A — ikonky moc velké, stopa moc vysoká:** `icon_size` se počítal
      z `rows_h` (`max(24,min(64,rows_h-10))`) a `DRUM_ROW_MIN_H=74` navíc
      vynucovalo `max(PER_TRACK, ...)` floor → i 1-2 bubny dělaly obří
      řádky. **Oprava:** nové konstanty `DRUM_ICON_SIZE=32`, `DRUM_ROW_H=40`
      (fixní, ne "minimum"), `icon_size = min(DRUM_ICON_SIZE, rows_h-8)`,
      **floor na `PER_TRACK` úplně odstraněn** pro stopu bicích — výška teď
      roste čistě `8 + n_řádků×40 + TRACK_GAP`, žádné umělé nafouknutí.
      Ověřeno: Let It Be (8 různých bubnů) scene height 812→540px.
    - **Bug B — "bicí končí v půlce písničky" (repetice):** uživatel:
      "při importu bicích se pravdepobně nepočítá s repeticemi". POTVRZENO
      na reálných datech — `Beatles (The) - Let It Be.gp4` má takt 5
      `isRepeatOpen=True`, takt 8 `repeatClose=1, repeatAlternative=1`
      (1. zakončení), takt 9 `repeatAlternative=2` (2. zakončení). Celý kód
      dosud dělal NAIVNÍ lineární `for m in track.measures` — ignoroval
      repeat brackets úplně, takže export byl kratší než reálná nahrávka
      (vše po repetici "ujíždí" dřív a dřív, poslední řádky/bicí chybí).
      **Oprava — 3 nové sdílené funkce v `guitar_pro_viewer.py`:**
      - `expand_measure_order(measures)` — vrátí pořadí INDEXŮ taktů, jak
        by se SKUTEČNĚ přehrálo (repeat open/close + 1./2. zakončení
        bitmaska). Bezpečnostní strop proti nekonečné smyčce na
        poškozených datech. **Nezohledňuje Segno/Coda/D.S./D.C./Fine
        skoky** (`header.direction`) — vzácnější, mnohem složitější
        (skoky přes celou skladbu, ne lokální blok); zdokumentováno jako
        známé omezení v docstringu, ne potichu.
      - `tempo_at_tick(tick, tempo_map)` — tempo platné v surovém ticku.
      - `walk_track_beats(track, tempo_map)` — generátor `(m_idx, beat,
        time_s, duration_s)` v EXPANDOVANÉM pořadí. **Klíčový detail:**
        `time_s` se NEPOČÍTÁ z absolutního GP ticku (`ticks_to_seconds`) —
        při repetici se surové ticky OPAKUJÍ (2. průchod má stejné ticky
        jako 1.), takže absolutní přepočet by dal stejný čas pro oba
        průchody. Místo toho běžící součet (`elapsed`) délek beatů v
        expandovaném pořadí, tempo pro každý beat se čte z `tempo_map`
        podle jeho PŮVODNÍHO surového ticku (zůstává platné i při opakování).
      - Přepojeno do **všech 4 míst**, co dřív dělaly naivní walk:
        `_build_karaoke_json` (hlavní GP export), `_gp_vocal_words`,
        `_gp_drums_for_merge`, `_gp_bass_for_merge`.
      - **Ověřeno na reálných datech:** `expand_measure_order` na Let It Be
        dá přesně očekávanou sekvenci `[...4,5,6,7, 4,5,6,8, 9...]`
        (0-based) = 70 přehraných taktů z 67 napsaných. Basová linka: starý
        naivní poslední čas 199.5s → nový 208.5s (+9.0s = přesně 3 takty ×
        4 beaty × 60/80s — matematicky přesně sedí). Soubor BEZ repetic
        (Guns N' Roses) → `expand_measure_order` dá identitu (žádná
        regrese). `py_compile` OK.
    - README doplněno (ikonky 32×32, `assets/drum_icons/`, tlačítka fázového
      posunu) — nahrazuje moje vlastní chybné "barevné tečky" z bodu 9.
    - **NEOVĚŘENO:** vizuálně na reálném Windows (jen data/offscreen);
      Segno/Coda soubory (vzácnější, neřešeno); `_add_drum_shift_controls`
      UI (tlačítka ◀▶⋯ v hlavičce) neprocházeno vizuálně vůbec, jen čteno.

11. ✅ **HOTOVO (2026-07-29, uživatel dodal reálný testovací materiál)** —
    uživatel: "to nesedí... udělala text v txt jasná zprava a dal i gp5
    soubor... mělo by to být 60 bpm a píseň má cca 240 sekund" + "asi jsi
    to přehnal s repeticemi" + "vytvoř mi json z toho". Testováno na
    `Olympic - Jasná Zpráva.gp5` + `Jasná Zpráva.txt` (oba v repu). Odhaleny
    a opraveny **3 samostatné bugy**:
    - **Bug 1 — bod 10 (`expand_measure_order`) OVĚŘENO ŠPATNĚ:** tenhle
      GP5 má 3 repeat závorky (`isRepeatOpen`/`repeatClose=1`), ale **žádné**
      `repeatAlternative` (na rozdíl od Let It Be). Naivní rozbalení dalo
      76 taktů = 304s, uživatel potvrdil realitu ~240s = přesně 61
      NEROZBALENÝCH taktů. **Oprava:** `expand_measure_order()` teď
      rozbaluje repetice **JEN když je v souboru `repeatAlternative`
      (1./2. zakončení) alespoň jednou přítomné** — to je jednoznačný
      signál (nejde zadat omylem, MUSÍ ho autor tabu záměrně vyplnit pro 2
      různé takty). Holé `repeatClose` bez zakončení je v praxi (komunitní
      GP tabulatury) nespolehlivé — často pozůstatek copy-paste editace,
      i když se sekce hraje jen jednou. Ověřeno na obou souborech
      současně: Jasná zpráva už NEROZBALUJE (61→61, 244s ≈ 240s), Let It
      Be pořád ROZBALUJE (67→70, beze změny — `repeatAlternative` tam je).
    - **Bug 2 — chybí parsování akordů oddělených čárkou:** intro/outro
      řádek `"G, Gmaj7, Emi, C, D, Ami, C, G"` (typický formát ručně psaného
      chart) se rozpoznával jako BĚŽNÝ TEXT, ne akordy — `_chord_tokens()`
      dělil jen podle mezer/`|`, čárka zůstávala nalepená na tokenu (`"G,"`
      selhalo na `is_chord()` regexu). Oprava: `_chord_tokens()` teď dělí
      i podle čárky (`line.replace(',', ' ')`) a ořezává `,` stejně jako
      `:`. `"2x"` marker zůstává správně odfiltrovaný (`_REPEAT_RE`).
    - **Bug 3 — NEJZÁVAŽNĚJŠÍ, špatné párování klipů v `to_json()`:**
      `karaoke_lines[]` v exportu měly TEXT jedné části písně spárovaný s
      ČASEM úplně jiné (např. "Knížku krásně zbytečnou" — správně ~130s —
      vyšlo na 132s OK, ale "píše se v ní" mělo vyjít ~137.5s, vyšlo na
      110s — o celou předchozí sloku dřív). **Příčina:** `_seed_display()`
      otagoval klipy master "Displej" stopy číslem `line` z PŮVODNÍHO
      číslování `karaoke_lines` (to zahrnuje i řádky BEZ textu — čistě
      akordové intro/outro/mezihra). `to_json()` ale čísluje `line_idx`
      ČERSTVĚ, jen podle `lyrics_timeline` (bezeslovné řádky se do něj
      vůbec nepočítají — nemají žádnou textovou událost). Pro řádky ZA
      první bezeslovnou sekcí se číslování rozejde (posun o 1 za každou
      vynechanou bezeslovnou sekci) → `clip_by_line.get(line_idx)` pak
      omylem trefí klip NĚJAKÉHO JINÉHO řádku, o desítky sekund jinde, a
      jeho `start_s/end_s` PŘEPÍŠE správně spočtený čas. **Tohle je obecná
      chyba, ne specifická pro tuhle píseň** — projeví se u každé písně s
      alespoň jedním čistě akordovým (bezeslovným) řádkem PŘED nějakým
      textovým řádkem. Let It Be to nezasáhlo jen náhodou (žádné bezeslovné
      řádky v karaoke_lines). **Oprava v `timeline_editor.py to_json()`:**
      nový `MAX_LINE_CLIP_DRIFT_S = 6.0` — když `clip_by_line[line_idx]`
      najde klip, jehož `start_s` je od PŘIROZENĚ spočteného startu dál
      než 6s, klip se zahodí jako STARÝ/CIZÍ tag a řádek se dohledá
      standardní time-proximity cestou (`_match_unlinked`, self-healing,
      už existovala pro netagované klipy). Bezpečný degrade: když se nic
      nedohledá, řádek prostě dostane svůj přirozený (ze slov spočtený)
      čas — nikdy ne čas cizího řádku.
    - **Ověřeno:** `lyrics_timeline` bylo CELOU DOBU správně (potvrzeno
      trasováním krok za krokem — `retime_web_to_gp` i `load_data()`
      dávaly monotónní pořadí), chyba byla izolovaná výhradně v
      `karaoke_lines`-rebuild kroku `to_json()`. Po opravě: všech 22 řádků
      `Olympic - Jasná Zpráva_test.json` monotónně rostoucí `start_s`
      (4.0 → 168.0s), verše/refrény ve správném pořadí, akordy sedí.
    - **Vytvořen `Olympic - Jasná Zpráva_test.json`** (v repu) — text/akordy
      z `Jasná Zpráva.txt`, časování+bicí z `Olympic - Jasná Zpráva.gp5`
      (basa v tomhle GP5 NENÍ — jen Vocal/Rytm/Solo/Drums, žádná 4strunná
      stopa, `bass_timeline` je tedy prázdné a je to SPRÁVNĚ).
    - **Poučení k zapamatování:** GP soubory z volné distribuce (ne z
      vlastní tvorby) mívají nekonzistentní/pozůstatkové repeat značky —
      NIKDY nevěřit holému `repeatClose` bez `repeatAlternative` jako
      důkazu skutečné repetice. A obecněji: jakékoli číslování řádků
      (`line`), které se cachuje na jednom místě (klip) a znovu počítá na
      jiném místě (to_json), potřebuje buď garantovaně STEJNÉ pravidlo
      počítání na obou místech, nebo (bezpečnější) validaci časovou
      blízkostí před tím, než se mu důvěřuje.

## Obecné pravidlo (důležité, neporušovat)

Časování se počítá **výhradně z tempa (BPM) a taktu** — nikdy z odhadů
založených na textu (slabiky, délka slova apod.). Uživatel to označil za
"kraviny" a trval na "hudební teorii". Pokud je potřeba automatika, vždy
vycházet z beat/bar mřížky, jinak nechat ruční úpravu tažením v editoru.

## 12. Bod 11 (repeat-gate `repeatAlternative`) OTOČEN ZPĚT — a nové věci

Stejný den, přímé navázání na bod 11. **Poučení: nevěřit ani vlastnímu dřívějšímu
"opravenému" chování bez tvrdých dat — a nevěřit ani webovému researchi bez
přímého ověření.**

- **Bod 11 byl špatně.** Gate "trust_repeats jen když má soubor
  `repeatAlternative`" (přidaný kvůli uživatelově odhadu "~240s") jsem
  ZRUŠIL. Důvod: oficiální text (pisnicky-akordy.cz PDF, staženo přímo)
  má explicitně **3× "Sólo: 2x"** přesně na místech 3 repeat závorek v
  GP5 — jednoznačný důkaz, že SE MAJÍ rozbalit, i bez `repeatAlternative`.
  `expand_measure_order()` teď zase bere `repeatClose` vážně vždy
  (bez podmínky). Jasná zpráva: 61→76 taktů po expanzi (zpět na to, co
  bylo PŘED bodem 11).
- **Tempo 60 vs 125 — slepá ulička, uživatel to sám vyřešil.** Uživatel se
  zeptal "nemá být bpm 125 a 4 takty?", při doupřesnění řekl "reálné tempo
  písně je 125, ne 60". Zkusil jsem 125 BPM — vyšlo NESMYSLNĚ krátce (146s
  místo očekávaných ~240-260s, verš na 1.9s misto ~45s) — HORŠÍ shoda než
  s 60 BPM. Místo dalšího hádání jsem se zeptal, uživatel řekl "nechtěj
  hardat, ověřím si to sám z nahrávky" — a hned poté napsal **"60 je
  tempo"** = self-korekce, 60 BPM je správně (GP hodnota byla OK od
  začátku). **Nezkoušet příště sám odhadovat/přepočítávat tempo z
  nejistých webových zdrojů (LRC timestamp z AI-summarizovaného
  vyhledávání, ne přímo staženého souboru — WebFetch na lyricsify.com byl
  20x blokovaný 403) — je to nespolehlivější než se prostě zeptat.**
- **Další (3.) varianta stejného "cizí klip" bugu z bodu 6 (`to_json()`
  clip-matching):** `MAX_LINE_CLIP_DRIFT_S=6.0` z bodu 6 nestačilo — klip
  s `mode="chords"` (bezeslovné intro/mezihra, `text=''`) mohl mít
  NÁHODOU `start_s` do 6s od nějakého TEXTOVÉHO řádku a `clip_by_line`
  ho tam nesprávně dosadil (řádek dostal čas cizí, ale blízké, bezeslovné
  sekce). **Oprava:** `clip_by_line`/`unlinked` teď filtrují jen
  `mode in ("lyrics_chords", "lyrics")` — čistě akordový klip nikdy
  nemůže být kandidát na spárování s textovým `karaoke_lines` řádkem
  (sémanticky nedává smysl, bez ohledu na časovou blízkost). Tohle je
  robustnější a obecnější než pouhé rozšiřování drift-thresholdu.
- **Nová funkce: count-in / odpočítávání (`web_import.add_count_in()`).**
  Uživatel: "nemá metronom, nikdo neví kdy začít... odpočítávání s
  metronomem (mezinárodní = jen čísla)... zavřená hajtka zatím stačí."
  `add_count_in(data, bars=1)` — posune CELOU časovou osu dopředu o
  `bars×beats_per_measure×60/bpm` sekund, vyplní uvolněné okno `[0,
  count_in_s)` klikací stopou (`drum: "Closed Hi-Hat"`) v `drums_timeline`,
  nastaví `meta.count_in_bars`/`count_in_s`. **Žádná zvláštní data pro
  vizuální "4,3,2,1"** — ESP32 si ho dopočítá čistě z metadat (vzorec v
  `ESP32_KARAOKE_IMPLEMENTATION.md` §6c), synchronně s kliky. Použito v
  `Olympic - Jasná Zpráva_test.json` (1 takt = 4 doby = "4,3,2,1").
- **Finální ověřený stav `Olympic - Jasná Zpráva_test.json`:** 60 BPM,
  76 taktů po expanzi (304s), count-in 4s, 25 řádků **monotónně rostoucích**
  (17.0s → 182.0s, po count-inu), 938 úderů bicích, akordy sedí, basa
  prázdná záměrně (GP5 basu nemá). `py_compile` OK na všech 3 modulech.
- **STÁLE NEOVĚŘENO uživatelem proti reálné nahrávce** — sám řekl, že si
  tempo/délku ověří. Neprezentovat čísla v tomhle bodě jako finálně
  potvrzená, dokud se neozve.

---

## 13. Audio, GP mix, auto-sledování Displeje (2026-08-01, commit `4c954b3`)

- **Přehrávání MP3/WAV + jog/shuttle** (`jog_shuttle.py`, `web/jog_shuttle.html`)
  — obě varianty (PySide6 widget + samostatná HTML/CSS/JS pro budoucí ESP32
  webserver). Vyřešeno cestou několik reálných bugů: zpětná vazba
  playhead→seek způsobovala **cukání zvuku** (opraveno `_syncing` guardem),
  `QPen(..., cap=...)` kwarg neexistuje v PySide6 (rozbíjelo vykreslení),
  odpojení Bluetooth sluchátek vyžaduje **znovuvytvoření** `QAudioOutput`
  (ne jen `setDevice()`).
- **Vlnovka** (`waveform.py`) — `QAudioDecoder.setAudioFormat()` na Windows
  **věší dekódování MP3** (WAV projde) → formát se nepřenastavuje, konverze
  se dělá ručně. Audacity-styl (vyplněná obálka), automatické zesílení
  ZOBRAZENÍ u tichých nahrávek (nikdy ne skutečné hlasitosti), posun +
  ±10 % roztažení, automatické zarovnání dle rytmu (onset detekce +
  comb-filter odhad).
- **GP bicí mix** (`render_drums_mixdown()`) — druhý `QMediaPlayer` se
  syntetizovanými bicími přímo z `drums_timeline`, sdílený transport,
  nezávislá hlasitost. **Zásadní pravidlo od uživatele: audio je pro
  VERIFIKACI ČLOVĚKEM, nikdy nesmí nic automaticky řídit/opravovat.**
  `_check_duration_vs_audio()` je proto čistě informativní hláška na
  vyžádání — žádné auto-spuštění, žádné "aplikovat návrh".
- **Auto-sledování Displej klipů** (`auto_track`) — klip živě kopíruje
  rozsah i popisek z obsahu, dokud ho uživatel ručně neposune. Plus
  `rebuild_display_track()` na kompletní přestavbu od nuly (řeší smazané
  řádky). **Bug nalezený uživatelem:** přestavba počítala řádky po JEDNÉ
  stopě → akordy na jiné stopě než zpěv vyráběly falešné samostatné klipy;
  opraveno na globální výpočet napříč stopami (stejně jako `to_json()`).
- **Mix panel** v levém docku (`TrackMixPanel`) — hlavičky uvnitř
  `QGraphicsScene` při vodorovném rolování odjedou pryč, proto je ovládání
  hlasitosti/mute/skrytí stopy jako **samostatné Qt widgety v docku**.
- Ripple výběr rozšířen: text+akordy **dohromady** a strhává i odpovídající
  klipy Displeje (i při rubber-band výběru, ne jen `]`/`[`).

## 14. Přestavba GUI: menu, ikony, dvojjazyčnost (2026-08-02, probíhá)

Zadání: *„normalizace menu, tak jak je zvykem… horní panel předělat na
ikony… vše musí být dvoujazyčně… začni psát návod v angličtině."*

**Hotovo:**
- **`i18n.py`** — lehký vlastní `tr()` slovník (ne Qt Linguist/.ts/.qm),
  **registr naplňovaný při konstrukci widgetu** (`register_tr`/`tr_action`/
  `tr_label`/`tr_dock_title`/`tr_tab`) → přepnutí jazyka překreslí UI **za
  běhu**, bez restartu. Perzistence v `settings.json`. Čeština výchozí.
  Past k zapamatování: `QDockWidget` titulek je JINÝ řetězec než jeho
  `toggleViewAction().setText()` — obojí potřebuje vlastní registraci.
- **Sjednocené menu** — nové **Úpravy** a **Časová osa**; všech ~20 tlačítek
  z bývalého horního panelu `timeline_editor.py` je teď `QAction` **sdílená
  mezi menu i toolbarem** (jeden objekt = jedno místo pro překlad).
  `snap_combo`/`time_lbl` zůstaly widgety (combobox/živý readout do menu
  nepatří), hlavní okno je jen převezme do toolbaru. **Nutná byla
  deduplikace zkratek** — `TimelineView.keyPressEvent` měl Ctrl+Z/Y/Del/S
  natvrdo; po vzniku `QAction` se stejnou zkratkou by to střílelo dvakrát.
- **Ikony** — `assets/icons/*.svg` (19 vlastních plochých, v paletě
  aplikace) + systémové `QStyle.standardIcon()` pro běžné akce; pomocník
  `icons.py`.
- **Oprava exportního bugu (nalezeno při auditu, nehlásil uživatel):**
  `export_json()` při **přímo otevřeném .gp souboru** přestavoval JSON od
  nuly z `guitarpro.Song` → **tiše zahazoval VŠECHNY úpravy z editoru**.
  Teď vede jediná cesta ven přes `timeline.to_json()`.
  `_build_karaoke_json()` zůstává jen pro PRVOTNÍ naplnění.
- **i18n sweep `guitar_pro_viewer.py` kompletní** — dialogy, hlášky,
  taby, docky, tabulka detailu stopy. Ověřeno živým CS→EN→CS testem.

**Zbývá (stav k bodu 14):**
- i18n sweep `timeline_editor.py` a `web_import.py` — ✅ **HOTOVO, viz bod 15.**
- Anglický uživatelský návod `docs/USER_GUIDE.md` + generování screenshotů
  (`docs/make_screenshots.py`, offscreen render, **jen syntetická data** —
  nikdy uživatelovy živé soubory).
- Ověřovací checklist z plánu (kompletní pokrytí překladu, perzistence
  jazyka přes restart).
- **Build-ready pro exe (PyInstaller)** — zatím žádný `.spec`/build skript
  v repu. Než se bude appka balit: (1) `datas` pro `lang/`, `assets/`,
  `web/`, `drum_samples.json` (PyInstaller je sám od sebe nezabalí,
  `lang/*.json` chybějící v buildu = appka bez překladů, `⚠MISSING:` všude);
  (2) `i18n.SETTINGS_PATH` přesměrovat z `os.path.dirname(__file__)` do
  `%APPDATA%` — v `--onefile` módu se `__file__` rozbaluje do dočasného
  adresáře smazaného po zavření appky, takže volba jazyka by se nikdy
  nezapamatovala mezi spuštěními. Odloženo na žádost uživatele, řešit až
  před reálným balením do exe.

## 15. Multi-dokument (karty) + i18n přesunuto do JSON, plný sweep (2026-08-06)

Zadání: *„pro potřeby kopírování mezi json vzájemně je třeba udělat
multidokument… a zároveň je čas udělat vícejazyčnou verzi celého
programu… rozhodni, co bude lepší udělat jako první."* Rozhodnuto:
multi-dokument první (viz plán, `AskUserQuestion` → uživatel zvolil karty
místo split view), pak dokončení i18n. Následně uživatel: *„nebylo by
lepsí třeba více mutací, které by byly mimo py soubor?"* → i18n přesunuto
z inline Python slovníku do `lang/*.json`, jazyky doplněny na
**cs/en/de/es/pl** (uživatel: "česky, anglicky, německy, španělsky a
polsky"), sekvence "nejdřív JSON, pak sweep zbytku appky" (uživatelem
zvolená varianta).

**Hotovo:**
- **Multi-dokument (taby).** `guitar_pro_viewer.py`: `self.doc_tabs`
  (`QTabWidget`) nahradilo jedinou `self.timeline`; `self.timeline` teď
  `@property` → `self.doc_tabs.currentWidget()`, takže ~50 stávajících
  volání funguje beze změny. Identita dokumentu (`song`,
  `current_gp_file`/`current_json_file`, `tempo_map`, `_dirty`) se
  přesunula z hlavního okna na `TimelineEditor` (per-karta). Sdílená
  schránka (`_SHARED_CLIPBOARD` na úrovni modulu `timeline_editor.py`)
  umožňuje kopírovat mezi kartami/písněmi — to byl původní požadavek.
  Doky (Stopy/Náhledy) i toolbar (`snap_combo`/`time_lbl`) se přepínají
  na aktivní kartu (`_on_active_tab_changed`). Zavření karty/okna s
  neuloženými změnami se ptá Uložit/Zahodit/Zrušit. Opraven i latentní
  bug v `LoadWorker` (výsledek načítání se dřív četl z `self._worker` v
  době doručení signálu, ne z workeru, co ho spustil — u souběžného
  načítání do dvou karet mohl skončit na špatné kartě; teď zachyceno
  uzávěrem při spuštění).
- **i18n přesunuto do `lang/*.json`.** `i18n.py` zeštíhlelo z 977 na 229
  řádků — `_load_languages()` glob-scanuje `lang/*.json` při startu,
  `available_languages()` staví nabídku Jazyk v menu DYNAMICKY (přidání
  jazyka = jen nový soubor, žádný zásah do kódu). `FALLBACK_LANGS =
  ("en", "cs")`. Nový `tools/check_translations.py` — validuje shodu
  klíčů/placeholderů napříč jazyky a křížovou kontrolu proti `tr(...)`
  voláním v kódu; spouštět po každé změně překladů.
- **Kompletní i18n sweep `web_import.py` a `timeline_editor.py`** —
  všechny dialogy (tempo/count-in/vlnovka/auto-zarovnání/přichycení na
  mřížku…), kontextová menu, `PropertiesPanel`, `TrackMixPanel`, tooltips
  doplněny přes `tr()`/`register_tr`. `lang/*.json` narostly na 427-428
  klíčů/jazyk (z původních ~225). Pozůstatek: `_section_of()` (generuje
  "Sloka N"/"Refrén" do EXPORTOVANÝCH karaoke dat pro displej ESP32)
  záměrně ponechán česky — je to obsah písně pro hardware, ne UI appky.
- **Bug nalezený uživatelem po commitu: "některé části jsou pouze
  česky, například levý panel."** Příčina: sloupec "Typ" v levém panelu
  (Stopy) se překládal jen JEDNOU — při importu GP/načtení JSON — text
  zůstal napevno v `QTreeWidgetItem`, přepnutí jazyka přeložilo
  záhlaví/titulek doku, ale ne už vyplněné řádky (stejná past, jaká byla
  dřív u popisků karet). **Oprava:** ke každé stopě se teď ukládá i
  strojový klíč druhu (`Qt.UserRole` sloupce 1, `_TRACK_TYPE_KEYS`
  mapování), nová `_retranslate_track_tree()` je zaregistrovaná stejným
  trikem jako `_refresh_all_tab_labels()` a přepočítá popisek při KAŽDÉM
  přepnutí jazyka. Doplněn i chybějící klíč `track.type_vocal` (JSON
  cesta zná i typ "zpěv", GP cesta ho nikdy nepoužívá). Ověřeno živě
  cs/en/de na obou cestách (GP i JSON) bez nutnosti znovu otevřít soubor.
- Regresní sada (45 headless testů ve scratchpadu): 37/45 prochází beze
  změny; 8 selhání jsou PŘEDEXISTUJÍCÍ dluh nesouvisející s touhle prací
  (2 testují starý přejmenovaný i18n klíč `file.open_gp`→`file.import_gp`,
  1 volá starou pre-multidoc metodu `win.export_json`, 5 sdílí zastaralou
  fixturu z 31.7. bez `drums_timeline`/`bass_timeline` z doby před
  funkcí editace bicích/basy) — ověřeno jednotlivě, žádné nesouvisí s
  i18n sweepem ani multi-dokumentem.

**Zbývá (nezměněno od bodu 14):** anglický `docs/USER_GUIDE.md` +
screenshoty, ověřovací checklist perzistence jazyka přes restart
(mechanismus existuje — `settings.json` — ale nebyl explicitně
otestován end-to-end přes reálný restart appky), PyInstaller build
(`datas` pro `lang/`/`assets/`/`web/`/`drum_samples.json`,
`SETTINGS_PATH` do `%APPDATA%`).
**Volitelný úklid (nezadáno, jen poznamenáno):** 8 zastaralých testů výše
by stálo za přepsání/smazání, až bude čas — testují API, které už
neexistuje.

## 16. Analýza 2026-10-31 — bicí nesedí na text ani na nahrávku + GUI

Pozn.: body 1–15 výš popisují ještě starý plochý formát (`format_version 2`);
platný je `schemaVersion 1` podle `JSON_FORMAT.md`.

### Bicí z GP nesedí — změřené příčiny
- **A. Cesta „nejdřív text (web/vlastní), pak otevřít GP"** (`_merge_gp_tracks_into_current`)
  jen přilepí bicí/basu v sekundách z GP, ale:
  1. **tempo dokumentu zůstane z webového dialogu** (default 120), ne z GP.
     `Iron Maiden - Run To the Hills.json`: tempoMap 120 BPM (GP má 136→168),
     3901/6720 událostí (58 %) nese `tMsOverride` = mimo mřížku.
  2. **text se nepřečasuje na GP** (žádné `retime_web_to_gp`/`place_lines_on_structure`,
     na rozdíl od Ctrl+M `merge_with_web`) — řádky zůstanou na umělé mřížce
     `bars_per_line` od `count_in_bars`.
  3. **bicí/basa se neposunou o `count_in_s`** — `format_migrate` pak od všeho
     odečte odpočet: první takt(y) bicích se zahodí jako „klikací stopa"
     (Iron Maiden: 38 úderů) nebo stlačí na 0 (Jasná zpráva `_nova_verze`,
     countInBars 4: 27 úderů na 1:1.0), zbytek hraje o odpočet DŘÍV než text.
- **B. `build_tempo_map` ignoruje změny tempa z GP** — čte jen `header.tempo`,
  které pyguitarpro u GP3/4/5 nevyplňuje (vždy `None`); změny jsou
  v `beat.effect.mixTableChange.tempo`. Iron Maiden: takt 25 → 168 BPM
  ignorováno, takty 25–151 počítané 136 BPM → konec ujede o ~43 s
  (266 s místo ~224 s).
- **C. tempo_map v surových GP tickách vs. rozbalené takty** (`walk_track_beats`,
  `TimeGrid`) — při repeticích + změně tempa padne změna na špatný takt.
  Projeví se až po opravě B.
- **D. Metrum `x/8`**: `TimeGrid` počítá takt jako `num` čtvrťových dob
  (6/8 = 6 dob místo 3) — v ukázkových souborech není, ale u 6/8 písní
  bude takt 2× delší.
- **E. Živá nahrávka** (`20. olympic - jasna zprava (live).mp3`): GP má
  pevné tempo, živák ne → posun+roztažení ±10 % to nespraví. Řešení =
  ruční kotvy (takt X = čas Y v nahrávce) → `tMsOverride`/tempoMap po úsecích.
- Čistý GP import (bez textu předem) je vnitřně konzistentní — ověřeno
  screenshotem Jasné zprávy: bicí, akordy i takty sedí na mřížku.

### GUI — nálezy (offscreen screenshot 1600×950)
- Hlavičky stop: jméno stopy přes popisky řádků („Vocal" přes „řádky",
  „Drums" přes „Closed Hi-Hat").
- Spodní audio lišta: texty tlačítek oříznuté („Načíst MP3/", „GP bicí r",
  „Auto-zarovnat c"), jog/shuttle kolečko uříznuté v rohu; nad lištou prázdný pruh.
- Pravítko „Takt 2 · 1.84615s" — zbytečně 5 desetin.
- Levý panel: stopy 2× (strom „Stopy" + „Stopy/Skrýt" v mixu); sloupec Typ
  oříznutý, Vocal označený jako „kytara"; „Uložit Karaoke JSON" + „Exportovat
  všechny stopy" duplikuje Ctrl+S.
- Toolbar: textové tlačítko „Panel náhledů (Chord Chart, JSON…)" mezi ikonami,
  dvě šipky bez popisu vedle Otevřít.
- Bicí: řádek na každé GM jméno (9–10 řádků) — export stejně mapuje na 8 samplů.
- Panel Vlastnosti zabírá ~15 % šířky i prázdný.
- Dvě cesty „text + GP" (Ctrl+M vs. otevřít GP po textu) se chovají jinak.

### Testy (2026-10-31) — `tests/`
- `test_tempo_meter.py` — změny tempa (B), tempo × repetice (C), takty x/8 a 2/2 (D), export tempa/metra.
- `test_text_then_gp.py` — cesta „text, pak GP" (A1–A3).
- `test_library.py` — bicí/basa pro každý GP (i celá knihovna přes `GP_LIBRARY`). Navíc našel:
  svázané noty (tie) se exportují jako nový úder (basa i bicí); basa se slévá z víc 4strunných
  stop (Let It Be: Piano Bass + Bass); 32iny/trioly dostávají `tMsOverride` (export kvantuje na 16iny);
  `durationMs` končí o takt dřív (Knockin': 341,2 vs 345,0 s).
- `test_gui_layout.py` — překryvy hlaviček, oříznutá tlačítka, uříznuté kolečko, desetiny na pravítku, typ Vocal.
- `test_audio_anchors.py` — přeskočeno, dokud nejsou kotvy z nahrávky.

### Opraveno 2026-10-31 (všechny testy zelené: 54 ok, 8 skip = chybí kotvy z nahrávky)
- B/C: `build_tempo_map` čte mix table; `play_tempo_map` převádí na ticky přehrávání;
  `walk_track_beats` počítá přes mřížku s rozbalenými takty (+ `all_voices`, `duration.time` s triolami).
- D: `TimeGrid` počítá délku taktu ve čtvrtkách (`num*4/den`); `beat` = čtvrťová doba i v x/8.
- A: obě cesty „text + GP" sdílí `_gp_timing_onto_text` (tempo/metrum/repetice z GP,
  řádky na zpěv GP nebo na takty, akordy mimo řádky posunuté s nejbližším řádkem,
  odpočet z dialogu už nemaže bicí).
- Bicí/basa: `gp_drum_events`/`gp_bass_events` — všechny hlasy, bez tie, bicí bez výšek <27,
  basa z jedné stopy (`pick_bass_track`). Export: jemnější kvantizace (32iny, trioly),
  `meta.duration_seconds` = konec posledního taktu.
- GUI: hlavičky stop bez překryvů (jméno zkrácené s tooltipem, bicí mají pruh se jménem),
  spodní lišta ve 2 řádcích, kolečko vždy celé, pravítko 1 desetinné místo, typ Vocal = Zpěv,
  ikony GP/MIDI importu a panelu náhledů, odstraněno nefunkční „Exportovat všechny stopy".

### Zbývá (revize postupu „GP knihovna + text z webu → JSON pro ESP32")
- Přichycení (snap) v editoru používá jedno tempo (`meta.tempo_bpm`) — u písní se změnou
  tempa přichytává za změnou mimo dobu (pravítko i export už jsou správně).
- Volba basové stopy jen automaticky (`pick_bass_track`), v GUI ji nejde přepnout.
- Hromadný export celé GP knihovny (bicí+basa bez textu) zatím jen přes GUI po jedné písni.
- Živé nahrávky: chybí kotvy „takt = čas" (bod E), test `test_audio_anchors.py` čeká na data.
- Parser textu pro web, ze kterého se bude stahovat — čeká na ukázku formátu.

### Test na GP knihovně uživatele (2026-10-31)
- Výběr 79 písní (`tools/select_library.py`): 59 českých/slovenských + 20 rockových klasik
  (AC/DC, Deep Purple, Sabbath, Metallica, Nirvana, Queen, Maiden…). 66 má bicí, 50 basu,
  47 obojí, 35 má text ve zpěvní stopě. **Všech 437 kontrol prošlo** (1 nález opraven:
  odhadnutá délka posledního řádku textu prodlužovala `durationMs`).
- ✅ GP6/GP7 (`.gpx`/`.gp`) — viz níže.
- Ukázkové písně přesunuty z kořene do `pisne/<Interpret - Název>/`.

### GP6 `.gpx` a GP7+ `.gp` (2026-10-31) — `gpif_reader.py`
- Kontejnery: `.gp` = ZIP (`Content/score.gpif`), `.gpx` = BCFZ (bitová LZ77) → BCFS
  (sektory 4 KiB). XML se převádí na objekty `pyguitarpro`, zbytek appky beze změny.
- **Ověřeno nezávisle:** Stairway to Heaven `.gp` (GP8) proti MIDI exportu z GP — 1270/1272
  úderů bicích do 2 ms (max 0,15 ms), 16 změn tempa. Zbytek: ride 59 v MIDI = 51 v souboru
  (stejný vzorek), 2 virbly v souboru nejsou (přidá je až export GP). Tublatanka GP7 vs GP5/GP3
  téže písně: basa 100 %, bicí liší jen v taktech s přírazy.
- Fakta k zapamatování: GPIF struna 0 = nejhlubší; `Repeat count` = celkový počet zaznění;
  tempo v automaci hlavní stopy („BPM jednotka", 2 = čtvrťová); bicí GP7 = `OutputMidiNumber`
  artikulace (ne `Midi` u noty), GP6 většinou pražec = MIDI, jen výjimečně Element/Variation;
  přírazy OnBeat = příraz na dobu + hlavní nota posunutá, BeforeBeat = příraz před dobou;
  text GP7 u beatu (`Lyrics/Line`) po slabikách „la-" „dy" (spojuje `join_syllables`), GP6 často
  ve FreeText; GP6 text bývá cp1250 uložený jako latin-1 (oprava jen při znacích \x80–\x9f).
- Opraveno cestou: repetice na nezahraném taktu zakončení (1.+3./2.+4.) se nevyhodnocuje;
  bicí ze VŠECH bicích stop (Bára 2× Drumkit, Šaman + tamburína), stejný úder ve stejný okamžik
  jen jednou; spodní lišta měla bez stropu výšky a brala místo časové ose.
- Výběr z knihovny 93 písní (16 GP6/7): 507 kontrol prošlo.

### Texty z webů — vzorky formátů (2026-10-31), `tools/stahni_texty.py`
- 8 písní (4 české, 4 rockové), 44 souborů v `pisne/*/texty/`, každý ověřen podle titulku stránky.
  Formáty: karaoketexty (HTML, čistý text `<br>`), Ultimate Guitar (`[ch]G[/ch]` nad textem,
  `[tab]`, `[Verse]`, v JSONu js-store; u Báry české „H"), Genius (HTML `data-lyrics-container`),
  Musixmatch (JSON `__NEXT_DATA__`), lyrics.ovh (JSON prostý text), guitaretab/chordie (akordy nad
  textem v `<pre>`), ChordPro (`[D]` v textu + `{title:}`, `tests/data/formaty_textu/`),
  vlastní txt (Jasná zpráva, akordy oddělené čárkou), PDF (`zdroje/jasná zpráva chords.pdf`).
- NESTAŽENO (zapsáno v TEXTY.txt): pisnicky-akordy.cz 403 a supermusic.cz JS výzva — ochrana
  proti robotům, neobcházet, uložit ručně z prohlížeče; velkyzpevnik.cz 503 a lrclib.net API 502
  (výpadek, zkusit znovu — lrclib je jediný zdroj LRC s časy).
- Další krok (nezadáno): parsery/testy importu pro tyto formáty.
- Otázka uživatele „soubor projektu": zatím NENÍ. JSON nese jen cestu k nahrávce + posun/roztažení;
  cesta ke GP, zdroj textu ani volby (basová stopa, odpočet) se neukládají. Návrh: `projekt.json`
  ve složce písně s relativními cestami (GP, text/URL, nahrávka, export) + volby → čeká na souhlas.

### Zamrznutí + zarovnání nahrávky na bicí z GP (2026-10-31)
- **Zamrznutí (py-spy):** `WaveformItem.paint` počítal vlnovku přes CELOU píseň při každém
  překreslení (bez `ItemUsesExtendedStyleOption` dává Qt celou položku) — při 321 px/s 87 tis.
  sloupců + polygon 175 tis. bodů. Teď jen viditelný výřez (příznak + ořez podle okna): 30 ms.
- **`drum_align.py`:** nárůst spektra po pásmech (kopák 35–120 Hz, virbl 1,5–5 kHz, činely
  6–16 kHz), počítá se při dekódování; korelace (FFT) s údery z GP přes roztažení 90–110 %
  (0,2 % hrubě, 0,01 % jemně); obálka posunutá o půl okna (jinak −21 ms). Umělá nahrávka:
  posun −7,2988 (skutečně −7,300), roztažení přesně 0,985, při kytaře 12× hlasitější než bicí.
- **Skutečné nahrávky Jasné zprávy:** shoda jen 51–71 %, úseky ujíždějí ±0,3–0,5 s — nahrávka
  nedrží tempo a bubeník hraje jinak než přepis (kopáků ~3× víc než v GP). Zkoušeno HPSS
  (oddělení bicích od tónů) — bez zlepšení (F 0,19 vs 0,20), 11 s navíc → nepoužito.
  Pro zarovnání je potřeba nahrávka hraná stejně jako GP (studio, ideálně na klik).
- Vlnovka: barevné čárky úderů z nahrávky (kopák/virbl/činely, prahy 0,7/0,7/0,5 = nejlepší F),
  legenda v hlavičce; načítání přes numpy 2× rychlejší (bit-přesně stejné kbelíky).
- Opraveno cestou: levý panel „Mix" kreslil staré řádky přes nové (`deleteLater` bez skrytí).
- Zbývá (nezadáno): u živáku zarovnávat po úsecích (kotvy) — editor má jen jeden posun+roztažení.

### Jasná zpráva × studiovka (2026-10-31) — chyby v GP a tempo po taktech
- Studiovka `10. Olympic - Jasná zpráva.wav` (4:19,8) vs GP 65 BPM (4:38): GP má o jednu repetici
  navíc — **druhé sólo (takty 34–38) se hraje jen jednou** (potvrzeno zvukem: úsek po něm zní jako
  refrén 0,98, ne sólo 0,79; potvrdil i uživatel). Uživatel opravil v TuxGuitaru do
  `Olympic - Jasná Zpráva (1).gp5` — ten má ale tempo 60 (správně 65) a pořád tom místo virblu.
- **Virbl zapsaný jako Hi-Mid Tom (MIDI 48)** na dobách 2 a 4 — druhá chyba přepisu.
- Studiovka nemá klikačku: tempo 63,8 → 65,7 BPM (měřeno autokorelací nezávisle na GP).
- Nové v aplikaci: **Časová osa ▸ Repetice…** (počet zaznění bloku, `repeat_blocks`/`set_repeat_plays`),
  **pravý klik na řádek bubnu ▸ Změnit všechny údery na…** (`remap_drum`), **Časová osa ▸ Napasovat
  tempo na nahrávku…** (`drum_align.fit_bars`: DP po taktech, 2 průchody 40 ms/±4 s a 10 ms/±0,4 s;
  `apply_bar_times` přepočítá všechny události v rámci taktu, tempo po taktech, Ctrl+Z).
- Ověřeno: umělá nahrávka s tempem ±2,5 % → každý úder do 17 ms (medián 2,9 ms); studiovka:
  shoda 53 % → 83 % (279 z 335 úderů kopáku/virblu do 40 ms), export bez `tMsOverride`.
- Živák `20. olympic - jasna zprava (live).mp3` zmizel z disku (v gitu je) — nerozhodnuto, zda vrátit.
- 🎧 „Nahrávka vlevo, GP vpravo" (panel Mix): Qt přehrávač neumí vyvážení a Windows mixer posouvá
  celou aplikaci → přehrávače dostanou stereo soubory s jedním kanálem (`side_wav`); nahrávka
  z dekódovaného zvuku (`WaveformData.pcm`), GP mix bicích/basy vpravo. Účel (uživatel): srovnat
  rytmus poslechem, pak přesněji posouvat texty pro karaoke.

### Rozpoznání bicích v nahrávce — neuronová síť ADTOF (2026-10-31)
- Uživatel: bicí z GP se liší od nahrávky („otázka GP" — přepis), potřeba lépe rozeznat bicí ve zvuku.
- **Měření bez GP** (GP je nespolehlivé jako pravda): skutečná studiovka Jasné zprávy rozdělená Demucsem,
  ze zbytku (basa+kytary+zpěv, bez bicích) + bicí ze `assets/drums/rock` se známými časy (932 úderů,
  vlastní rytmus s ghost notes, tomy, plovoucí tempo). F-skóre do 30 ms, kopák / virbl+tomy / činely:
  | metoda | bicí 0 dB | bicí −8 dB |
  |---|---|---|
  | nárůst spektra po pásmech (dosavadní) | 0,68 / 0,68 / 0,86 | 0,45 / 0,64 / 0,71 |
  | totéž na stopě bicích z Demucs | 0,82 / 0,61 / 0,93 | — |
  | NMF (šablony kopák/virbl/hi-hat) na Demucs | nestabilní 0,04–0,89 podle nastavení — slepá ulička |
  | **ADTOF na mixu** (3 s / 4 min) | **0,91 / 0,89 / 0,86** | **0,86 / 0,84 / 0,80** |
  | ADTOF na stopě Demucs (+75 s CPU) | 0,92 / 0,91 / 0,92 | 0,93 / 0,87 / 0,92 |
- Časová chyba ADTOF medián < 1 ms (žádná korekce). Studiovka: „kopáků" 1428 → 393 (basa už ne),
  virbly na virblech z GP 17 % → 56 %, činely přesnost 72 → 86 %. Cesta taktů `fit_bars` stejná (medián 10 ms).
- Model: `assets/models/adtof_frame_rnn.onnx` (2 MB, z huggingface.co/thebluescreen/adtof-drums-onnx,
  **CC BY-NC-SA 4.0 = jen nekomerčně**, `ADTOF_NOTICE.md`). Závislost `onnxruntime`. Vstup mono 44,1 kHz,
  výstup 100 snímků/s × 5 tříd (kopák, virbl, tomy, hi-hat, činely).
- `waveform.py`: po dekódování ADTOF ve vlákně; nárůst spektra se počítá dál jako záloha — síť na umělém
  signálu z testů (stálý pilovitý akord + bílý šum) nenajde nic, pak zůstane nárůst spektra (`onset_source`).
- Demucs (`pip install demucs`, torch CPU) je nainstalovaný, do aplikace NEZAPOJEN — zlepší hlavně tiché
  bicí, stojí 75 s/píseň (GPU torch by to zrychlil, nainstalovaný je CPU build).
- Další krok (nezadáno): porovnání nahrávka × GP po taktech („takt 12: GP 2 kopáky, nahrávka 5").

### Přepis bicích z nahrávky (2026-11-01) — Časová osa ▸ Přepsat bicí z nahrávky…
- `drum_align.transcribe` (5 tříd ADTOF, peak picking jako autoři: prahy 0,22/0,24/0,32/0,22/0,30)
  → `quantize_hits` (čas na osu přes zarovnání nahrávky, šestnáctiny nebo osminové trioly, když je
  triola o polovinu blíž; činel se sousedem do půl doby = ride 51, jinak crash 49) → nahradit bicí
  stopu (`replace_drums`, `source: "generated"`, Ctrl+Z) nebo `save_drums_midi` (kanál 10, tempo+metrum osy).
- Umělá nahrávka (F, 30 ms): kopák 0,91, virbl 0,97, hi-hat 0,73 (navíc údery), tomy 0,12, crash 0,00 —
  tomy a crash ze soupravy `assets/drums/rock` síť nebere (crash jako hi-hat); na skutečné studiovce
  crash z GP nalezen v 97 %, virbl v 98 %. Tomy se nerozliší podle výšky (vše 47).
- Studiovka Jasné zprávy: bez napasování tempa odchylka od mřížky 36 ms a 180 falešných triol (dialog
  varuje při > 1/5 šestnáctiny), po napasování 7,6 ms a 18 triol. 1343 úderů. Nahrávka × GP v taktu 9:
  bubeník hraje osminy na ride (GP hi-hat), virbl na 2 a 4 (GP v těch taktech nic), kopák jinak.
- Basa z nahrávky (Demucs + Basic Pitch) — nezačato.

### Opraveno 2026-11-01 — cukání zvuku a posun nahrávky
- Cukání: `.venv` (vzniklo 31.10. 21:14) mělo PySide6 6.11.1, dřív aplikace běžela z globálního Pythonu
  s 6.10.0. Hlavní vlákno při přehrávání měřeno ~1 ms/tik (nebrzdí). `.venv` vráceno na 6.10.0,
  `requirements.txt` zamčeno `PySide6==6.10.0` — uživatel potvrdil, že zvuk zase běží hezky.
- Posun nahrávky: kladný `audio_offset_s` (po „Napasovat tempo" takt 1 v −1,42 s → posun +1,42 s)
  `_seek_audio` ořízl na 0 → nahrávka hrála o celý posun dřív než GP mix. Teď počká (`_rec_delay_timer`).
  Posun/roztažení změněné za běhu se hned projeví i ve zvuku (dřív jen na obrazovce; tažení vlnovky
  přesynchronizuje po puštění myši). Po napasování tempa a přepisu bicích se přegeneruje už načtený
  GP mix (dřív hrál starý mix s původním tempem). Ověřeno na tiché nahrávce (offscreen, bez zvuku).

### Krok 1 vyladěn (2026-11-01) — rozpoznání bicích z nahrávky
- Test: 8 umělých mixů (doprovod bez bicích z Jasné zprávy a z „Gramofon" × souprava rock / Street-Symphony
  × 0/−8 dB), F do 30 ms, celkem: ADTOF na mixu kopák 0,77, virbl 0,82, hi-hat 0,69 → s Demucsem 0,89 / 0,87 / 0,84.
- Prahy (`ADTOF_CLASS_THRESHOLDS` / `ADTOF_STEM_THRESHOLDS`): hi-hat 0,50 (mix) / 0,45 (Demucs), virbl s Demucsem 0,10,
  činely 0,50. Ověřeno na studiovce proti GP (hi-hat 417 → 267 úderů, shoda 64 → 86 %).
- Demucs oslabuje činely (crash z GP 97 → 74 %) → v režimu Demucs činely z celé nahrávky (`combine_stem`).
- Dialog přepisu: „Přesněji (Demucs)…" (~75 s, výsledek v `%LOCALAPPDATA%\Guitar_Pro_Viewer\cache`, podruhé hned).
  `.venv`: torch 2.6.0+cpu, demucs 4.1.0 (volitelné, v requirements jako komentář).
- Tomy a crash ze zkušebních vzorků síť nepozná (F≈0) — na reálné nahrávce chybí pravda k ověření.
- Chybí: ověření na dalších SKUTEČNÝCH nahrávkách (v pisne/ je jen Jasná zpráva) a průvodce „Karaoke z nahrávky".

### Průvodce „Karaoke z nahrávky" (2026-11-01) — Soubor ▸ Karaoke z nahrávky…
- Rozhodnutí uživatele: hlavní cesta = nahrávka (GP jen volitelně). `recording_wizard.py` (nemodální QWizard):
  nahrávka → doby a takty (`beat_grid.py`, Beat This!) → bicí (dialog přepisu) → text+akordy (WebImportDialog,
  `place_text_on_grid`: stejná doba od taktu 1+úvodní takty) → doladit tažením. `set_recording_grid` = tempo+metrum
  po taktech, posun −(takt 1), roztažení 1.
- Beat This vs takty podle GP (7 stažených písní + Jasná): Knockin' 91 %, Bára/Pohoda 100 %, Zimní královna 96 %,
  Jasná 97 % (medián 10 ms). Iron Maiden 2 % a Let It Be 39 % — chybné bylo napasování podle GP (virbl na 2 a 4:
  Beat This 91/94 %, GP 25/84 %). Falešné krátké takty → pevné metrum (výchozí, podle převládajícího počtu dob;
  virbl na 2/4 0,93–1,00), takty o 1 době se slepí vždy, ruční posun fáze ±3 doby, varování při virblu na 2/4 < 50 %.
- Bicí na mřížce z nahrávky: odchylka 12,6 ms (s GP 7,6 ms); tempo po dobách místo po taktech nepomůže (10 vs 10 ms).
- Nahrávky staženy přes yt-dlp do `pisne/*/` (zapsáno v ZDROJE.txt), nesmí do gitu bez rozhodnutí uživatele.
- Text v dialogu je na mřížce 4/4 — u písní ve 3/4 řádky nesednou na takty (neřešeno).

### Průvodce: bicí z GP na takty nahrávky + Přeskočit krok (2026-11-01)
- Uživatel: takty z nahrávky sedí, odhad bubnů (ADTOF) je „nesmysl" → krok 3 má i „Bicí z GP souboru…":
  skutečné údery GP na takty nahrávky (`place_gp_drums_on_grid`), tempo GP se nepoužije.
- `drum_align.align_gp_bars`: DP přes takty nahrávky — pokračovat dalším taktem GP zdarma, skok v GP 0,8,
  takt bez bicích 0,08, bez bicích → GP takt 1 zdarma (delší úvod), posun 0–3 doby. Shoda kopáku/virblu
  s nahrávkou (jeden posun → DP): Iron Maiden 52 → 83 % (GP o 3 doby vedle), Stairway 45 → 68 %,
  Knockin' 91 → 91 % (6 taktů úvodu navíc), Bára 83 → 87 %, Pohoda 94, Zimní královna 91 → 92, Let It Be 86 → 84,
  Jasná 64 → 63 % (pevné 4/4) / 74 % (takty podle nahrávky). Cena skoku 0,8–4 skoro nemění výsledek.
- ZNÁMÉ OMEZENÍ: u písní s takty 2/4 (Jasná) je pevné 4/4 za nimi o půl taktu vedle a virbl na 2/4 to neodhalí;
  DP pak u podobných slok přiřazuje takty GP divně (Jasná: GP takt 1 nikde, skok 74 → 3).
- „Přeskočit krok" (vlastní tlačítko QWizard) u všech kroků; bez nahrávky jdou kroky dál v nové kartě
  (bicí z GP takt po taktu).

### Projekt .kproj, importy v průvodci (2026-11-01)
- Bug: hláška „Export hotov" počítala staré ploché klíče → samé nuly; data v souboru byla v pořádku
  (Zimní královna: 21 řádků, 63 akordů, 709 úderů). Teď počítá ze `sections`.
- Rozhodnutí uživatele: projekt = vlastní přípona `.kproj` (uvnitř JSON podle JSON_FORMAT.md + blok `project`
  s relativními cestami: audio, gp, source_url). Uložit/Uložit jako → .kproj (dříve otevřený .json se dál přepisuje
  jako .json), Otevřít umí obojí, nové „Soubor ▸ Exportovat pro ESP32 / tablety (.json)" = čistý JSON bez `project`.
- Průvodce, krok Bicí: „Bicí ze souboru (GP / MIDI)…" — GP3/4/5/GPX/GP i MIDI (`midi_import.midi_drum_bars`,
  takty podle time_signature) → `place_drum_bars_on_grid` (stejné přiřazení taktů jako u GP).
- Dialog textu: „Ze souboru…" — uložená HTML stránka (stejný parser jako stažená), ChordPro (`chordpro_to_chart`),
  prostý text (utf-8 / cp1250).
- Zbývá: basa (Demucs + Basic Pitch, nebo z GP/MIDI na takty nahrávky stejně jako bicí).

### Basa v průvodci (2026-11-01)
- Krok „4. Basa": ze souboru (GP/MIDI, `place_gp_bass_on_grid` / `place_midi_bass_on_grid` — přiřazení taktů podle
  bicích téhož souboru, `_source_bar_map`) nebo z nahrávky (Demucs + `bass_track.track_notes`, YIN).
- Basic Pitch na Py 3.13 nejde nainstalovat (pkgutil.ImpImporter) → vlastní YIN v numpy.
- Basa z nahrávky vs. basa z GP na taktech nahrávky (5 písní): začátek noty do 60 ms jen 18–33 % → jen hrubý návrh.
- Transpozice: nahrávka zní jinak než GP (GnR a Arakain ladí o půltón níž). `bass_track.transpose_offset` (chroma
  40–400 Hz celé nahrávky v době not GP, bez Demucsu): Knockin' −1, Zimní královna −1, Pohoda/Bára/Let It Be 0,
  jistota 2,3–3,5×. Použije se při jistotě ≥ 1,1; mění se MIDI výška, struna/pražec zůstávají; `meta.bass_transpose`.
- Průvodce měl nečitelné písmo (styl Aero kreslí světlé pozadí pod světlé písmo tmavého tématu) → ClassicStyle.
- Zbývá: akordy z webu můžou být v jiné tónině než nahrávka (neřešeno); lepší basa z nahrávky (neuronová síť).

### Vrátit se (zadal uživatel 2026-11-01): ovládání posunu textu, akordů (a tabulatur/sól)
- Chybí pohodlné ovládání posunu textu a akordů (a sólové tabulatury) v čase — dnes jen tažení, „vybrat dopředu/dozadu"
  (`select_ripple`) a Posun vybraných. Navrhnout: posun o takty/doby, přichycení na doby nahrávky, posun od kurzoru dál.
- Transpozice při zpěvu níž/výš: `meta.bass_transpose` je celkový posun basy proti souboru; ESP32 má jednou
  transponovat akordy i basu NAJEDNOU jedním číslem (zatím jen basa, akordy se netransponují).
- „Vybrat dopředu/dozadu od kurzoru": kód vybírá správně (změřeno), uživatel to vnímal opačně → popisky přejmenovány na
  „Vybrat tento prvek a všechny pozdější/dřívější"; pokud to bylo vůči přehrávacímu kurzoru, je to jiná funkce (neřešeno).
- Text Pohody: v kproj je celá píseň (28 řádků), refrén zdroj zkracuje na „R: Když se u nás chlapi poperou…" — chyba zdroje, ne kódu.

### Revize načítání textu — takty, doby, rozložení (2026-11-01)
Nálezy v cestě dialog → `build_karaoke_json` → `place_text_on_grid`:
1. Značky „1.", „Ref:", „R:" zůstávaly v textu řádku (na displeji) → `split_section_marker`: značka → `section`,
   z textu pryč (bez dvojtečky/tečky jen čísla; „Refrén se nezpívá" zůstane textem).
2. „Intro G-G G-D-Emi-C" / „Sólo …" bylo TEXTOVÝ řádek → akordový řádek. Akordový řádek = takt na akord
   (min. bars_per_line); dřív 8 akordů intra ve 2 taktech.
3. Zkrácený refrén („R: Když se u nás…", „…") → `expand_repeats` rozbalí celý dřívější blok.
4. Přenos na osu počítal DOBY → ve 3/4 / za taktem 2/4 řádky mimo začátky taktů. Teď takt dialogu → takt osy,
   poloha poměrně, akordy přichycené na dobu.
5. Začátky řádků byly jen na umělé mřížce (bars_per_line). Měřeno proti nezávislé pravdě = časované texty lrclib
   (texty/lrclib.lrc, JEN pro měření — uživatel nechce text z lrclib používat), začátek řádku do 1 doby:
   | metoda | Jasná | Let It Be | Pohoda | Stairway |
   |---|---|---|---|---|
   | pevné takty (nejlepší úvodní takt) | 0 % | 3 % | 0 % | 5 % |
   | pauzy ve zpěvu + délka řádku (DP, zahozeno) | 95 % | 13 % | 21 % | 30 % |
   | **zarovnání textu na zpěv (Demucs + MMS-FA)** | **95 %** | **95 %** | (LRC chybné*) | **81 %** |
   *Pohoda: LRC zkracuje refrény a od 2. sloky je ~3 s napřed; zarovnání padá přesně na začátky zpěvu.
   Pravda ze zpěvní stopy GP (přes takty bicích) byla nespolehlivá (Jasná za sólem, Stairway bez bicích v úvodu).
→ ZAMÍTNUTO uživatelem (model 1,2 GB, text nemusí odpovídat zpěvu); smazáno. Místo toho `chord_align.py`:
  akordy textu na akordy nahrávky (chroma po dobách, DP) + přichycení na nástup zpěvu (Demucs): Jasná 77 %,
  Let It Be 63 %; Pohoda/Stairway selhávají (texty UG bez celé struktury). `TimelineEditor.align_text_to_vocals`
  (kotvy = začátky řádků, mezi nimi plynulý přepočet všeho, akordy na dobu; Ctrl+Z), menu Časová osa ▸ Zarovnat text
  na zpěv…, v průvodci zaškrtnuté. Podmínka: text = zpívané (vypsané refrény). Zpěv z Demucsu v mezipaměti.
- Pomalý test: `GPV_SLOW=1 python -m pytest tests -k zarovnany_na_zpev` (~3 min).
- Zbývá: akordy uvnitř řádku jdou dál podle pořadí slova (ne podle sloupce nad textem); čas slov ze zarovnání se
  zatím nepoužije (řádek = jedna jednotka podle formátu).

### Úklid souborů (2026-11-01)
- Staré exporty/pokusy → `pisne/<píseň>/archiv/` (git mv): Jasná (1)_karaoke, _nova_verze, bicí z nahrávky_karaoke;
  Arakain _karaoke; Iron Maiden .json. Uložené projekty z průvodce → `.kproj` ve složce písně (Arakain, Let It Be).
- `.dist/` (prázdná) smazána, `vocal_lines.py` (zahozený pokus) smazán. Nahrávky `pisne/**/*.mp3` a poslechové WAV
  v .gitignore. `drum_samples.json` v kořeni NECHÁN (čte ho format_migrate; kopie v assets/drums).

### Menu a průvodce — nové schéma (2026-11-01, rozhodnutí uživatele)
- Projekt vzniká JEN průvodcem (Soubor ▸ Nový projekt…, Ctrl+N; průvodce předpokládá prázdný projekt).
- Hotový projekt: menu Přepracovat ▸ Bicí / Basa / Text a akordy / Tabulatura (sólo); časování projektu zůstává,
  nahrazované se mu přizpůsobí. Doplňky (transpozice basy, jen zarovnat text) jen v okně z menu.
- Bicí/basa/tab ze souboru na osu po DOBÁCH (skok ve struktuře jen na začátek taktu zdroje): shoda bicích
  Jasná 63 → 78 %, Stairway 68 → 88, Let It Be 84 → 91, Bára 87 → 95, Pohoda 94 → 96, Zimní královna 92 → 96,
  Knockin' 91 → 93, Iron Maiden 83 → 78 (17 skoků — zkontrolovat).
- Takty z nahrávky: okraje mimo ±20 % mediánu nahrazeny pravidelnými (Jasná 126/85 → 65 BPM, Pohoda 55 → 140).
- Název karty: „Unknown" z importu se ignoruje (název souboru / nahrávky).
