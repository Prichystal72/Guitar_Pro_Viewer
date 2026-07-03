# Guitar Pro Viewer & Karaoke Exporter

Desktopová aplikace (PySide6) pro procházení souborů **Guitar Pro** (`.gp3/.gp4/.gp5`)
a jejich export do **JSON** pro karaoke / tab systémy (např. přehrávač textu a akordů
na ESP32 ze SD karty). Součástí je i **import akordů a textu z webu** a **dávkové
stažení celého interpreta**.

---

## Funkce

- 📖 **Prohlížeč GP souborů** — stopy, ladění, takty, tabulatury, text, akordy.
- 💾 **Export do JSON** — text + akordy + tabulatury v časové ose. Formát viz
  [JSON_FORMAT.md](JSON_FORMAT.md).
- 🎚️ **Editor časové osy** — DAW-styl: stopy jako pruhy, bloky textu a akordů
  na časové ose. Posun bloků po čase, změna délky tažením okraje, editace
  akordu/textu dvojklikem, přidání/mazání, zoom (Ctrl+kolečko) → export do JSON.
- 🎸 **Podpora více stop** — každý event nese `track_index` (odkaz na stopu),
  sóla se poznají podle `type: "solo_guitar"`.
- 🌐 **Import z webu** — rozpozná chord chart (akordy nad textem) a uloží jako
  GP4 + karaoke JSON. Optimalizováno pro [pisnicky-akordy.cz](https://pisnicky-akordy.cz),
  s obecným fallbackem.
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

### Prohlížení a export
1. **Otevřít** GP soubor (`.gp3/.gp4/.gp5`).
2. Projdi stopy, text a akordy v záložkách.
3. **Uložit Karaoke JSON** — vyexportuje strukturu podle [JSON_FORMAT.md](JSON_FORMAT.md).

### Import z webu
1. V okně **„Import z webu"** vlož URL písně (např. `pisnicky-akordy.cz/olympic/zelva`)
   a klikni **🔄 Načíst**.
2. Text lze v levém panelu **ručně upravit**, pak **🔍 Rozpoznat**.
3. **💾 Uložit GP4 + JSON**.

### Stažení celého interpreta
1. Do URL vlož stránku interpreta (např. `pisnicky-akordy.cz/olympic`).
2. **📚 Stáhnout celého interpreta** → vyber cílovou složku.
3. Všechny písně se uloží do podsložky `<cílová>/<interpret>/` jako GP4 + JSON.
   Průběh je vidět dole, stahování lze zrušit.

---

## Struktura projektu

| Soubor | Popis |
|--------|-------|
| `guitar_pro_viewer.py` | Hlavní GUI aplikace (vstupní bod) |
| `web_import.py` | Import z webu, detekce akordů, JSON export, stažení interpreta |
| `JSON_FORMAT.md` | Specifikace výstupního JSON formátu (`format_version: 2`) |
| `requirements.txt` | Python závislosti |
| `stazeno/` | Stažené písně (generovaný výstup — **není** ve verzování) |

---

## Výstupní formát

Kompletní specifikace včetně polí, konvencí ticků/času a návodu „co číst pro
který účel" (karaoke vs. tabulatury) je v **[JSON_FORMAT.md](JSON_FORMAT.md)**.
Slouží zároveň jako zadání pro parser (např. na ESP32).
