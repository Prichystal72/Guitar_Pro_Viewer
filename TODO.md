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

**Zbývá:**
- i18n sweep `timeline_editor.py` (velký soubor, stovky UI řetězců:
  dialogy tempo/count-in/vlnovka, kontextová menu, Panel vlastností,
  `TrackMixPanel`) a `web_import.py`.
- Anglický uživatelský návod `docs/USER_GUIDE.md` + generování screenshotů
  (`docs/make_screenshots.py`, offscreen render, **jen syntetická data** —
  nikdy uživatelovy živé soubory).
- Ověřovací checklist z plánu (kompletní pokrytí překladu, perzistence
  jazyka přes restart).
