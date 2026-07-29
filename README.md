# Guitar Pro Viewer & Karaoke Exporter

Desktopová aplikace (PySide6) pro procházení souborů **Guitar Pro** (`.gp3/.gp4/.gp5`)
a jejich export do **JSON** pro karaoke systémy — text, akordy a bicí (např.
přehrávač na ESP32 ze SD karty). Součástí je i **import akordů a textu z webu**
a **dávkové stažení celého interpreta**.

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
  - Posun bloků, editace dvojklikem, zalomení řádků pravým klikem, zoom
    (Ctrl+kolečko). Náhledy (Chord Chart, Noty, JSON…) jsou ve sbalitelném docku.
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
5. **💾 Export JSON** (v liště osy) nebo **Uložit Karaoke JSON** — vyexportuje
   strukturu vč. `display_timeline` podle [JSON_FORMAT.md](JSON_FORMAT.md).
   Tento výstup čte přehrávač na ESP32 (viz
   [ESP32_KARAOKE_IMPLEMENTATION.md](ESP32_KARAOKE_IMPLEMENTATION.md)).

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
| `guitar_pro_viewer.py` | Hlavní GUI aplikace (vstupní bod), rozvržení oken |
| `timeline_editor.py` | Editor časové osy — master „Displej" stopa, klipy, playhead, auto-časování |
| `web_import.py` | Import z webu, detekce akordů, JSON export, stažení interpreta |
| `JSON_FORMAT.md` | Specifikace výstupního JSON formátu (`format_version: 2`) |
| `ESP32_KARAOKE_IMPLEMENTATION.md` | Návod pro přehrávač JSONu na ESP32 (řízení displeje, bicí samply) |
| `drum_samples.json` | Mapa jméno bubnu → `.wav` na SD kartě (zkopíruj do `/drums/` na kartě) |
| `requirements.txt` | Python závislosti |
| `stažené/` | Stažené písně (generovaný výstup — **není** ve verzování) |

---

## Výstupní formát

Kompletní specifikace včetně polí, konvencí ticků/času a návodu „co číst pro
který účel" (karaoke vs. tabulatury) je v **[JSON_FORMAT.md](JSON_FORMAT.md)**.
Slouží zároveň jako zadání pro parser (např. na ESP32).
