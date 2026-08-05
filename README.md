# Guitar Pro Viewer & Karaoke Exporter

Desktopová aplikace (PySide6) pro procházení souborů **Guitar Pro** (`.gp3/.gp4/.gp5`)
a jejich export do **JSON** pro karaoke systémy — text, akordy a bicí (např.
přehrávač na ESP32 ze SD karty). Součástí je i **import akordů a textu z webu**
a **dávkové stažení celého interpreta**.

Aplikace je **dvojjazyčná (česky / anglicky)** — přepínač je v menu
**Zobrazit ▸ Jazyk**, volba se pamatuje mezi spuštěními (`settings.json`).

---

## Funkce

- 📖 **Prohlížeč GP souborů** — stopy, ladění, takty, tabulatury, text, akordy.
- 💾 **Export do JSON** — jen **karaoke text (po řádcích) + akordy + bicí**,
  žádná tabulatura. Text je **jedna jednotka na řádek** (ne po slovech).
  Časování je **výhradně z tempa/taktu** — žádné odhady ze slabik nebo délky
  textu (to je nespolehlivé). Každý řádek má **pevný počet taktů**
  (`bars_per_line`, výchozí 2), akordy sedí na nejbližším **beatu** podle
  pořadí slova v řádku; před první notou je volitelný **count-in** (`count_in_bars`
  taktů ticha na odklikání metronomu). V editoru se akordy i konce řádků dají
  tažením doladit. Do `tracks[]` jde jen zpěv + bicí (basa přibude jen po GP
  sloučení — viz níže — a do ESP32 exportu nejde). Ploché osy
  `lyrics_timeline`/`chords_timeline`/`drums_timeline`, **režie displeje**
  (`display_timeline`), **označení slok/refrénu** (`section`) a **seskupení
  do řádků** (`line`). Formát viz [JSON_FORMAT.md](JSON_FORMAT.md).
- 📝 **Nová píseň — z webu / vlastní text** (Ctrl+N, hlavní/první akce v menu)
  — kompletní postup jedním dialogem: načti text z webu (URL) **nebo** ho
  rovnou vlož/napiš ručně → uprav řádky a akordy (typ i obsah, živě, se třemi
  propojenými panely — text, rozpoznané řádky, náhled JSONu), nastav **počet
  taktů na řádek** a **count-in** → **✅ Použít v editoru** píseň rovnou
  nahraje na časovou osu na pravidelné hudební mřížce. Uložení GP4+JSON na
  disk zůstává volitelné tlačítko uvnitř dialogu.
- 🎵➕🥁🎸 **GP + web — dvě různé cesty kombinace**, podle toho, co chceš:
  - **Otevři GP AŽ PO tom, co už máš text/akordy** (z Ctrl+N, Ctrl+J nebo
    předchozího mergu) → GP se **automaticky jen přidá** (bicí + basa), text
    a akordy zůstanou **beze změny**. Žádné potvrzování, žádné URL — funguje
    to prostě tak, že otevřeš GP soubor (Ctrl+O) v okamžiku, kdy editor už
    karaoke data má.
  - **Sloučit s webem** (Ctrl+M) — *na vyžádání, jednorázově.* Vyžaduje už
    otevřený GP soubor; vlož URL písně z webu a text/řádky/akordy/sloky se
    z ní **čerstvě stáhnou a PŘEPÍŠOU** to, co bylo v editoru předtím —
    napasují se na reálné časy zpěvu z GP, přidají se bicí i basa. Hodí se,
    když GP nemá akordy (jen noty) a chceš je z konkrétní webové stránky.
  - V obou případech basa je **jen referenční stopa v editoru** (vizuální
    kontrola linky), do ESP32 exportu nejde — jen bicí.
- 🥁 **Bicí** — detekce perkusní stopy a export `drums_timeline`: kdy a jaký
  buben/sample má znít, se jménem z GM Percussion mapy (`Acoustic Snare`,
  `Closed Hi-Hat`, `Crash Cymbal`…) i MIDI číslem pro přímé mapování na sample.
  Namapování jméno bubnu → konkrétní `.wav` na SD kartě je v
  [`drum_samples.json`](drum_samples.json) (viz `ESP32_KARAOKE_IMPLEMENTATION.md`
  §6b) — jeden globální soubor pro všechny písně, N:1 (víc jmen bubnů může
  mířit na stejný sample), s `"_default"` fallbackem.
- 🎚️ **Editor časové osy (Sony Vegas styl)** — hlavní pracovní plocha okna.
  **Pravítko kreslí skutečné takty a beaty** podle tempa (silná čára + „Takt N"
  na taktu, tenká na beatu) — žádná obecná osa v sekundách. **Count-in** zóna
  je vizuálně odlišená (žluté šrafování). Nahoře **master „Displej" stopa** =
  režie karaoke (co/kdy/jak se ukáže na displeji) skládaná z **klipů**; pod ní
  zdrojové stopy s bloky textu a akordů.
  - **Klipy Displeje** — dvojklik vybere zdrojovou stopu + režim
    (`text+akordy`, `text`, `akordy`), **tažení kteréhokoli okraje posune
    ZAČÁTEK/KONEC řádku na displeji nezávisle na hudební mřížce** — důležité
    u rytmických skladeb. Pravý klik = rychlá změna režimu / smazání.
  - **Kurzor (playhead)** — červená čára, táhni ji nebo klikni do pravítka;
    readout času v liště.
  - **✂ Rozdělení klipu** v pozici kurzoru (tlačítko / klávesa **S** / pravý klik).
  - **⏱ Na mřížku** — přichytí začátky bloků na hudební mřížku (takt / beat /
    1⁄2 beatu / 1⁄4 beatu) podle tempa. Žádné odhady z délky textu.
  - **🥁 Stopa bicích** (objeví se po GP sloučení) — **každý konkrétní buben má
    vlastní podepsaný řádek** (ne sdílenou kategorii), úhozy jako **ikonky
    32×32 px** (`assets/drum_icons/`: kick/snare/tomy/hi-hat/činel, ostatní
    perkuse mají obecnou náhradní ikonku). Výška stopy roste kompaktně podle
    počtu různých bubnů v písni (fixní výška řádku, ne nafouknutá na
    minimum běžné stopy). Tlačítka v hlavičce stopy posunou všechny údery
    dané stopy v čase najednou (oprava fáze, když bicí z GP vyjedou).
  - **🎸 Stopa basy** (objeví se po GP sloučení) — noty jako úsečky ve 4
    řadách podle struny. Jen náhled/reference, do exportu nejde.
  - **🔗 Displej sleduje obsah automaticky** — klip se sám průběžně
    přepočítává tak, aby přesně odpovídal textu/akordům pod ním (tažení slova,
    přesné časy v panelu vlastností, hromadný posun). Jakmile klip **ručně**
    posuneš/roztáhneš, odpojí se a nechá se být; obnovit jde pravým klikem
    („🔄 Obnovit auto-sledování…“) nebo hromadně **🔄 Zarovnat displej (celá
    píseň)**. Když jsi mazal(a)/přeskládal(a) řádky a zarovnání nestačí, je tu
    **🔄🗑 Znovu poskládat Displej stopu** — zahodí všechny klipy a poskládá je
    znovu od nuly z aktuálního obsahu (řádky se počítají napříč všemi stopami,
    takže akordy na jiné stopě než zpěv se správně spojí s textem).
  - Posun bloků, editace dvojklikem, zalomení řádků pravým klikem, zoom
    (Ctrl+kolečko). Náhledy (Chord Chart, Noty, JSON…) jsou ve sbalitelném docku.
  - **Hromadný výběr (ripple)** — `]` / `[` (nebo pravý klik → „Vybrat od zde
    DÁL/DŘÍV v čase“) vybere prvek a vše odpovídající po/před ním na stejné
    stopě (**text i akordy dohromady**, včetně odpovídajících klipů Displeje);
    pak buď táhni myší, nebo **↔ Posunout vybrané…** pro přesný číselný posun.
  - **Mix časové osy** (levý dock, vždy viditelný i při vodorovném rolování) —
    hlasitost/ztlumení nahrávky a GP mixu bicích, hlasitost cvakání bicích při
    editaci, a skrytí/zobrazení jednotlivých zdrojových stop.
- 🔊 **Přehrávání nahrávky + jog/shuttle** — načti k písni MP3/WAV
  („🎵 Načíst MP3/WAV…“) a přehrávej ji synchronizovaně s kurzorem; kruhový
  **jog/shuttle ovladač** (styl Sony) pro přetáčení. Nad zdrojovými stopami se
  kreslí **vlnovka** nahrávky (výška řádku tažením, automatické zesílení
  zobrazení u tichých nahrávek), kterou lze **posunout a ±10 % roztáhnout**
  („🎚 Poloha/roztažení…“) nebo nechat **automaticky zarovnat dle rytmu**
  („🥁 Auto-zarovnat dle rytmu“ — najde údery a spočítá nejlepší posun+roztažení).
- 🎼 **GP bicí mix — poslechové porovnání** — „🎼 GP bicí mix“ vyrobí
  syntetizovaný zvuk bicích **přímo z GP dat** (`drums_timeline`) a přehraje ho
  **druhým přehrávačem vedle skutečné nahrávky**, se sdíleným transportem a
  nezávislou hlasitostí obou. Slouží k tomu, aby si člověk **poslechem ověřil**,
  jestli to, co říká GP soubor, sedí na realitu. „🔎 Ověřit délku“ jen
  informativně porovná vypočtenou délku písně s délkou nahrávky —
  **nic sám neupravuje**, rozhodnutí je vždy na uživateli.
- 🎸 **Podpora více stop** — každý event nese `track_index` (odkaz na stopu).
- 🌐 **Import z webu** (jádro Ctrl+N) — rozpozná chord chart (akordy nad
  textem) z [pisnicky-akordy.cz](https://pisnicky-akordy.cz) i obecných webů.
  Karaoke JSON **respektuje řádkovou strukturu** webu 1:1 a časuje **na pevné
  hudební mřížce** (viz výše — žádné slabiky); intro/mezihry v bar-line zápisu
  (`|Ami G|E| 2x`) pozná jako **řádek jen akordů**; **akordy z první sloky se
  doplní** do dalších slok/refrénů; **sloky/refrén se označí** (`section`) dle
  markerů `1.`/`R:` v textu.
- 📚 **Stažení celého interpreta** — zadáš stránku interpreta a všechny jeho
  písně se stáhnou do složky jako GP4 + JSON.
- 🇨🇿 **České značení akordů** — `H` (= B) a `mi` pro moll (`Ami`, `Hmi`, `Cmi`…).
- 🔤 **Robustní čeština** — správná detekce kódování webu (UTF-8/win-1250) a
  bezpečný zápis GP souborů (cp1250).

---

## Instalace

Vyžaduje **Python 3.11+**.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell/cmd)
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

Závislosti (viz [requirements.txt](requirements.txt)): PySide6, PyGuitarPro,
requests, beautifulsoup4.

---

## Spuštění

```bash
python guitar_pro_viewer.py
```

### Nová píseň — z webu / vlastní text (Ctrl+N, hlavní postup)
1. V dialogu **„Nová píseň"** buď vlož URL a klikni **🔄 Načíst z webu**, nebo
   text rovnou vlož/napiš do panelu 1b vlevo (bez URL). Nastav **taktů/řádek**
   a **úvodní takty** (count-in).
2. Uprav řádky/akordy — typ i obsah se editují přímo v tabulce (panel 2),
   změny se hned promítnou do textu i do náhledu JSONu (panel 3).
3. **✅ Použít v editoru** — píseň se rovnou zobrazí na časové ose, na pravidelné
   hudební mřížce (viz níže). Volitelně lze navíc **💾 Uložit GP4 + JSON…** na
   disk (dialog zůstane otevřený).
4. V **časové ose** (hlavní plocha) doladíš akordy/konce řádků tažením, **⏱ Na
   mřížku** přichytí bloky na takt/beat. Detailní náhledy (stopy, text, akordy,
   JSON) jsou v docku **Zobrazit → Panel náhledů**.
5. **Soubor ▸ Exportovat Karaoke JSON…** (`Ctrl+E`) — vyexportuje strukturu vč.
   `display_timeline` podle [JSON_FORMAT.md](JSON_FORMAT.md). Tento výstup čte
   přehrávač na ESP32 (viz
   [ESP32_KARAOKE_IMPLEMENTATION.md](ESP32_KARAOKE_IMPLEMENTATION.md)).
   Export **vždy uloží aktuální, živě upravená data z editoru** — bez ohledu
   na to, jestli píseň přišla z GP souboru, JSONu nebo z webu.

### Prohlížení GP souborů (doplňkově)
**Otevřít** GP soubor (`.gp3/.gp4/.gp5`, `Ctrl+O`) zobrazí stopy/takty/tabulatury
v docku náhledů. Pro karaoke export z GP samotného ale GP nemá dost informací
(žádná řádková struktura, často ani akordy) — pro text+akordy použij **Nová
píseň**/web. **Otevřít Karaoke JSON** (`Ctrl+J`) načte už hotový karaoke JSON
(např. dřívější export) rovnou do editoru.

### Stažení celého interpreta
1. Do URL vlož stránku interpreta (např. `pisnicky-akordy.cz/olympic`).
2. **📚 Stáhnout celého interpreta** → vyber cílovou složku.
3. Všechny písně se uloží do podsložky `<cílová>/<interpret>/` jako GP4 + JSON.
   Průběh je vidět dole, stahování lze zrušit.

---

## Struktura projektu

| Soubor | Popis |
|--------|-------|
| `guitar_pro_viewer.py` | Hlavní GUI aplikace (vstupní bod), menu bar + toolbary, rozvržení oken |
| `timeline_editor.py` | Editor časové osy — master „Displej" stopa, klipy, playhead, auto-časování, přehrávání audia + GP mix bicích |
| `web_import.py` | Import z webu, detekce akordů, JSON export, stažení interpreta |
| `jog_shuttle.py` | Kruhový jog/shuttle transportní ovladač (styl Sony) |
| `waveform.py` | Dekódování audia, vlnovka, detekce úderů, odhad zarovnání dle rytmu |
| `i18n.py` | Dvojjazyčnost (CS/EN) — `tr()` slovník + živé přepínání za běhu |
| `icons.py` | Pomůcky pro ikony (systémové Qt + vlastní SVG) |
| `assets/icons/` | Vlastní ploché SVG ikony pro menu/toolbary |
| `assets/drum_icons/` | Ikonky bubnů kreslené na časovou osu |
| `web/jog_shuttle.html` | Webová (HTML/CSS/JS) varianta jog/shuttle ovladače — pro pozdější webserver na ESP32 |
| `JSON_FORMAT.md` | Specifikace výstupního JSON formátu (`format_version: 2`) |
| `ESP32_KARAOKE_IMPLEMENTATION.md` | Návod pro přehrávač JSONu na ESP32 (řízení displeje, bicí samply) |
| `drum_samples.json` | Mapa jméno bubnu → `.wav` na SD kartě (zkopíruj do `/drums/` na kartě) |
| `requirements.txt` | Python závislosti |
| `settings.json` | Uživatelské nastavení (jazyk) — generuje se za běhu |
| `stažené/` | Stažené písně (generovaný výstup — **není** ve verzování) |

---

## Menu a klávesové zkratky

Všechny akce jsou v hlavním menu (a zároveň jako ikony v toolbarech
**Hlavní panel** / **Časová osa**):

| Menu | Obsah |
|------|-------|
| **Soubor** | Nová píseň z webu/textu (`Ctrl+N`), Sloučit s webem (`Ctrl+M`) · Otevřít GP (`Ctrl+O`), Otevřít JSON (`Ctrl+J`), Exportovat JSON (`Ctrl+E`) · Konec (`Ctrl+Q`) |
| **Úpravy** | Zpět (`Ctrl+Z`), Znovu (`Ctrl+Shift+Z`/`Ctrl+Y`) · Rozdělit v kurzoru (`S`), Smazat vybrané (`Del`), Posunout vybrané… |
| **Časová osa** | ＋ Klip displeje, 🔄 Zarovnat displej, 🔄🗑 Znovu poskládat Displej stopu · ＋ Text, ＋ Akord · ⏱ Na mřížku…, ✏️ Tempo…, 🥁⏱ Odpočet…, Přichytit k mřížce ▸ |
| **Zobrazit** | Panel stop, Panel náhledů · Přiblížit (`Ctrl+=`), Oddálit (`Ctrl+-`) · **Jazyk ▸ Čeština / English** |
| **Nápověda** | Nápověda k časové ose, O aplikaci |

Přímo v ose navíc: `]` / `[` = hromadný výběr od prvku dál/dříve v čase,
`Ctrl+kolečko` = zoom.

---

## Výstupní formát

Kompletní specifikace včetně polí, konvencí ticků/času a návodu „co číst pro
který účel" (karaoke vs. tabulatury) je v **[JSON_FORMAT.md](JSON_FORMAT.md)**.
Slouží zároveň jako zadání pro parser (např. na ESP32).
