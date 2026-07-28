# Guitar Pro Viewer & Karaoke Exporter

Desktopová aplikace (PySide6) pro procházení souborů **Guitar Pro** (`.gp3/.gp4/.gp5`)
a jejich export do **JSON** pro karaoke systémy — text, akordy a bicí (např.
přehrávač na ESP32 ze SD karty). Součástí je i **import akordů a textu z webu**
a **dávkové stažení celého interpreta**.

---

## Funkce

- 📖 **Prohlížeč GP souborů** — stopy, ladění, takty, tabulatury, text, akordy.
- 💾 **Export do JSON** — jen **karaoke text (po řádcích) + akordy + bicí**,
  žádná tabulatura. Text je **jedna jednotka na řádek** (ne po slovech); akordy
  mají **časová razítka umístěná dle slabik** v rámci řádku (v editoru tažné).
  Do `tracks[]` jde jen zpěv + bicí. Ploché osy `lyrics_timeline`/
  `chords_timeline`/`drums_timeline`, **režie displeje** (`display_timeline`),
  **označení slok/refrénu** (`section`) a **seskupení do řádků** (`line`).
  Formát viz [JSON_FORMAT.md](JSON_FORMAT.md).
- 📝 **Nová píseň — z webu / vlastní text** (Ctrl+N) — kompletní postup jedním
  dialogem: načti text z webu (URL) **nebo** ho rovnou vlož/napiš ručně →
  uprav řádky a akordy (typ i obsah, živě, se třemi propojenými panely — text,
  rozpoznané řádky, náhled JSONu) → **✅ Použít v editoru** píseň rovnou
  nahraje na časovou osu v zadaném tempu (řádek = jeden časový úsek/takt).
  Uložení GP4+JSON na disk zůstává volitelné tlačítko uvnitř dialogu.
- 🎵➕🥁 **Sloučit s webem** (Ctrl+M) — otevři GP soubor (kvůli **bicím** a
  časování), pak vlož URL písně z webu: **text, řádky, akordy a sloky/refrén se
  vezmou z webu**, napasují se na reálné časy zpěvu z GP a přidají se **bicí**.
  Ideální, když GP nemá akordy (jen noty) a web nemá bicí. Vše pak upravíš
  v editoru a exportuješ.
- 🥁 **Bicí** — detekce perkusní stopy a export `drums_timeline`: kdy a jaký
  buben/sample má znít, se jménem z GM Percussion mapy (`Acoustic Snare`,
  `Closed Hi-Hat`, `Crash Cymbal`…) i MIDI číslem pro přímé mapování na sample.
- 🎚️ **Editor časové osy (Sony Vegas styl)** — hlavní pracovní plocha okna.
  Nahoře **master „Displej" stopa** = režie karaoke (co/kdy/jak se ukáže na
  displeji) skládaná z **klipů**; pod ní zdrojové stopy s bloky textu a akordů.
  - **Klipy Displeje** — dvojklik vybere zdrojovou stopu + režim
    (`text+akordy`, `text`, `akordy`), **tažení kteréhokoli okraje posune
    ZAČÁTEK/KONEC řádku na displeji nezávisle na tom, kdy doznívá poslední
    slabika** — důležité u rytmických skladeb, kde má text zůstat/zmizet
    přesně na beat. Pravý klik = rychlá změna režimu / smazání.
  - **Kurzor (playhead)** — červená čára, táhni ji nebo klikni do pravítka;
    readout času v liště.
  - **✂ Rozdělení klipu** v pozici kurzoru (tlačítko / klávesa **S** / pravý klik).
  - **⏱ Auto-časování slabik** — přerovná časy slabik (rovnoměrně v řádcích dle
    délky / do oken klipů / přichycení na beat podle tempa).
  - Posun bloků, editace dvojklikem, zalomení řádků pravým klikem, zoom
    (Ctrl+kolečko). Náhledy (Chord Chart, Noty, JSON…) jsou ve sbalitelném docku.
- 🎸 **Podpora více stop** — každý event nese `track_index` (odkaz na stopu).
- 🌐 **Import z webu** — rozpozná chord chart (akordy nad textem) a uloží jako
  GP4 + karaoke JSON. Optimalizováno pro [pisnicky-akordy.cz](https://pisnicky-akordy.cz),
  s obecným fallbackem. Karaoke JSON **respektuje řádkovou strukturu** webu (1:1,
  s markerem `line` u slov) a časuje **po slabikách** (delší slovo trvá déle);
  intro/mezihry v bar-line zápisu (`|Ami G|E| 2x`) pozná jako **řádek jen akordů**;
  **akordy z první sloky se doplní** do dalších slok/refrénů. Do editoru otevři
  přímo tento **JSON (Ctrl+J)**, ne GP4.
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

### Prohlížení, editace a export
1. **Otevřít** GP soubor (`.gp3/.gp4/.gp5`), nebo **Otevřít Karaoke JSON**
   (`Ctrl+J`) — např. výstup z web importu. **Pozor:** pro karaoke editaci se
   správnými řádky a slabikovým časováním otevírej **JSON**, ne GP4 (GP4 nese
   jen noty v taktech, řádková struktura se do něj neuloží).
2. V **časové ose** (hlavní plocha) uprav režii displeje — klipy master „Displej"
   stopy, časování slabik (**⏱ Auto-časování**), zalomení řádků. Detailní náhledy
   (stopy, text, akordy, JSON) jsou v docku **Zobrazit → Panel náhledů**.
3. **💾 Export JSON** (v liště osy) nebo **Uložit Karaoke JSON** — vyexportuje
   strukturu vč. `display_timeline` podle [JSON_FORMAT.md](JSON_FORMAT.md).
   Tento výstup čte přehrávač na ESP32 (viz
   [ESP32_KARAOKE_IMPLEMENTATION.md](ESP32_KARAOKE_IMPLEMENTATION.md)).

### Nová píseň — z webu / vlastní text (Ctrl+N)
1. V dialogu **„Nová píseň"** buď vlož URL a klikni **🔄 Načíst z webu**, nebo
   text rovnou vlož/napiš do panelu 1b vlevo (bez URL).
2. Uprav řádky/akordy — typ i obsah se editují přímo v tabulce (panel 2),
   změny se hned promítnou do textu i do náhledu JSONu (panel 3).
3. **✅ Použít v editoru** — píseň se rovnou zobrazí na časové ose. Volitelně
   lze navíc **💾 Uložit GP4 + JSON…** na disk (dialog zůstane otevřený).

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
| `ESP32_KARAOKE_IMPLEMENTATION.md` | Návod pro přehrávač JSONu na ESP32 (řízení displeje) |
| `requirements.txt` | Python závislosti |
| `stažené/` | Stažené písně (generovaný výstup — **není** ve verzování) |

---

## Výstupní formát

Kompletní specifikace včetně polí, konvencí ticků/času a návodu „co číst pro
který účel" (karaoke vs. tabulatury) je v **[JSON_FORMAT.md](JSON_FORMAT.md)**.
Slouží zároveň jako zadání pro parser (např. na ESP32).
