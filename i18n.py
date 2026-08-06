"""
i18n.py — lehká vlastní překladová vrstva, bez Qt Linguist/.ts/.qm.

Použití:
    from i18n import tr, tr_action, register_tr, get_language, set_language

    label = QLabel()
    register_tr(label.setText, "some.key")

    act = tr_action(self, "file.open", tooltip_key="file.open.tooltip",
                     shortcut="Ctrl+O", slot=self.open_json)

Retranslace funguje přes REGISTR naplněný PŘI KONSTRUKCI widgetu (ne pár
_build_ui()/retranslate_ui() metod, které by se dřív nebo později rozešly) —
`register_tr()` si zapamatuje (setter, klíč, volitelná kwargs funkce) a hned
zavolá setter poprvé; `set_language()` pak jen znovu přehraje celý registr.

Transientní řetězce (QMessageBox, QInputDialog, stavový řádek) žádný registr
nepotřebují — stačí `tr(key, **kwargs)` přímo na místě volání, protože se
staví nanovo při každém zobrazení a tak přirozeně použijí aktuální jazyk.

PŘIDÁNÍ NOVÉHO JAZYKA = ŽÁDNÝ ZÁSAH DO KÓDU:
    1. zkopíruj `lang/en.json` na `lang/<kód>.json` (např. `lang/it.json`),
    2. přelož hodnoty (klíče nech být) a uprav `"_name"` na název jazyka
       v něm samotném (v menu se zobrazí přesně tenhle text),
    3. spusť appku — jazyk se sám objeví v menu Zobrazit ▸ Jazyk.
Nepřeložené/chybějící klíče tiše propadnou na `FALLBACK_LANGS` (angličtina,
pak čeština), takže rozpracovaný překlad nikdy nerozbije UI — jen se v něm
místy objeví text z náhradního jazyka. Formátovací zástupné symboly
(`{path}`, `{n}` …) musí v překladu zůstat DOSLOVA — `tr()` je dosazuje
přes `str.format`, jinak spadne na `KeyError`/`IndexError` (viz `tr()`).
"""
from __future__ import annotations

import glob
import json
import os
from typing import Callable, Optional

DEFAULT_LANG = "cs"
FALLBACK_LANGS = ("en", "cs")   # pořadí náhrad pro klíč chybějící v aktivním jazyce
_HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_HERE, "settings.json")
LANG_DIR = os.path.join(_HERE, "lang")

_current_lang = DEFAULT_LANG

# {kód jazyka: {klíč: text}} — naplní `_load_languages()` při importu modulu
_STRINGS: dict[str, dict[str, str]] = {}
# {kód jazyka: název jazyka v něm samotném} — pro nabídku v menu
_LANG_NAMES: dict[str, str] = {}


def _load_languages() -> None:
    """Načte VŠECHNY `lang/*.json` — každý soubor = jeden jazyk, název
    souboru = kód jazyka. Klíč `_name` uvnitř souboru je název jazyka v něm
    samotném (do menu), ostatní klíče jsou překlady. Vadný/nečitelný soubor
    se přeskočí (appka pojede dál v ostatních jazycích) — chybějící adresář
    taky ne­shodí start, jen zůstane prázdný slovník a `tr()` bude vracet
    názvy klíčů, což je viditelné, ale ne fatální."""
    _STRINGS.clear()
    _LANG_NAMES.clear()
    for path in sorted(glob.glob(os.path.join(LANG_DIR, "*.json"))):
        code = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        _LANG_NAMES[code] = data.pop("_name", code)
        _STRINGS[code] = data


def available_languages() -> list[tuple[str, str]]:
    """[(kód, název jazyka v něm samotném), …] seřazeno podle názvu — pro
    sestavení nabídky Zobrazit ▸ Jazyk (menu se staví z TOHO, co se našlo
    na disku, ne z natvrdo zapsaného seznamu)."""
    return sorted(((code, _LANG_NAMES.get(code, code)) for code in _STRINGS),
                  key=lambda pair: pair[1].lower())


_load_languages()


# ---------------------------------------------------------------------------
# Perzistence jazyka
# ---------------------------------------------------------------------------

def load_settings() -> None:
    """Načte uložený jazyk ze settings.json (voláno v main() PŘED vytvořením
    hlavního okna, aby první sestavení UI už bylo ve správném jazyce)."""
    global _current_lang
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        lang = data.get("language", DEFAULT_LANG)
        # Jazyk uložený z dřívějška, jehož soubor mezitím zmizel (smazaný
        # `lang/*.json`), nesmí appku zablokovat v prázdném jazyce.
        _current_lang = lang if lang in _STRINGS else DEFAULT_LANG
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _current_lang = DEFAULT_LANG


def save_settings() -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"language": _current_lang}, f, indent=2, ensure_ascii=False)
    except OSError:
        pass   # jazyk se prostě nezapamatuje pro příště, nic fatálního


def get_language() -> str:
    return _current_lang


def set_language(lang: str) -> None:
    global _current_lang
    if lang not in _STRINGS or lang == _current_lang:
        return
    _current_lang = lang
    save_settings()
    _registry.retranslate()


# ---------------------------------------------------------------------------
# Registr pro živé přepínání jazyka za běhu
# ---------------------------------------------------------------------------

class _Registry:
    def __init__(self) -> None:
        self._entries: list[tuple[Callable[[str], None], str, Optional[Callable[[], dict]]]] = []

    def add(self, setter: Callable[[str], None], key: str,
            kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
        self._entries.append((setter, key, kwargs_fn))
        setter(tr(key, **(kwargs_fn() if kwargs_fn else {})))

    def retranslate(self) -> None:
        for setter, key, kwargs_fn in self._entries:
            try:
                setter(tr(key, **(kwargs_fn() if kwargs_fn else {})))
            except RuntimeError:
                # Widget mezitím zanikl (např. zavřená karta v multi-dokumentu
                # — každý TimelineEditor si registruje vlastní snap_combo/
                # time_lbl) — jeho záznam tu zůstane (zanedbatelná pár-tuple
                # paměť), jen se přeskočí, stejný vzor jako `_pin_headers()`.
                pass


_registry = _Registry()


def register_tr(setter: Callable[[str], None], key: str,
                 kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
    """Zaregistruje `setter` (libovolný 1-argumentový callable, např.
    `widget.setText`) tak, aby se při přepnutí jazyka znovu zavolal se
    znovu-přeloženým textem. Zavolá ho i hned napoprvé."""
    _registry.add(setter, key, kwargs_fn)


def tr(key: str, **kwargs) -> str:
    """Přeloží `key` do aktivního jazyka. Chybí-li v něm, propadne postupně
    na `FALLBACK_LANGS` (angličtina → čeština) — rozpracovaný překlad tak
    nikdy nerozbije UI, jen se v něm místy objeví náhradní jazyk. Když klíč
    neexistuje NIKDE, vrátí `⚠MISSING:<klíč>` (viditelné při testech, ne pád).

    Zástupné symboly (`{path}`, `{n}` …) dosazuje `str.format`. Když je
    překlad v jednom jazyce omylem přejmenuje/zahodí, spadlo by to na
    `KeyError`/`IndexError` — takový překlad se místo pádu jen použije
    nedosazený (a `tools/check_translations.py` na to upozorní předem)."""
    for lang in (_current_lang, *FALLBACK_LANGS):
        table = _STRINGS.get(lang)
        if table:
            template = table.get(key)
            if template:
                if not kwargs:
                    return template
                try:
                    return template.format(**kwargs)
                except (KeyError, IndexError, ValueError):
                    return template
    return f"⚠MISSING:{key}"


def tr_action(parent, key: str, tooltip_key: Optional[str] = None,
              shortcut: Optional[str] = None, icon=None, slot=None,
              checkable: bool = False, shortcuts: Optional[list] = None):
    """Vytvoří QAction a rovnou zapojí text (+volitelně tooltip) do
    překladového registru — nahrazuje typický 3-4řádkový blok
    QAction()/setShortcut()/setToolTip()/triggered.connect()."""
    from PySide6.QtGui import QAction
    act = QAction(parent)
    if icon is not None:
        act.setIcon(icon)
    if shortcuts:
        act.setShortcuts(shortcuts)
    elif shortcut:
        act.setShortcut(shortcut)
    if checkable:
        act.setCheckable(True)
    if slot is not None:
        act.triggered.connect(slot)
    register_tr(act.setText, key)
    if tooltip_key:
        register_tr(act.setToolTip, tooltip_key)
    return act


def tr_label(widget, key: str, kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
    register_tr(widget.setText, key, kwargs_fn)


def tr_dock_title(dock, key: str) -> None:
    """QDockWidget titulek NENÍ totéž co jeho toggleViewAction().setText() —
    obojí potřebuje vlastní register_tr volání, jinak po přepnutí jazyka
    zůstane titulek plovoucího docku ve starém jazyce."""
    register_tr(dock.setWindowTitle, key)


def tr_tab(tabs, index: int, key: str) -> None:
    register_tr(lambda t, _tabs=tabs, _i=index: _tabs.setTabText(_i, t), key)


def tr_window_title(window, key: str, kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
    register_tr(window.setWindowTitle, key, kwargs_fn)


