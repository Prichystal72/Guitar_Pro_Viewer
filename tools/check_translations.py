"""
check_translations.py — kontrola překladových souborů `lang/*.json`.

Spuštění (z kořene projektu):
    python tools/check_translations.py

Hlásí:
  • klíče chybějící v některém jazyce (propadnou na náhradní jazyk — appka
    poběží, ale UI bude místy v jiném jazyce),
  • klíče navíc, které nezná referenční jazyk (překlep v názvu klíče →
    text se NIKDY nezobrazí),
  • NESHODU FORMÁTOVACÍCH ZÁSTUPNÝCH SYMBOLŮ (`{path}`, `{n}` …) proti
    referenci — to je jediná chyba, která by za běhu mohla text rozbít
    (`tr()` ji sice zachytí a vypíše text nedosazený, ale uživatel by pak
    viděl doslovné `{path}` místo cesty),
  • klíče, které v kódu nikdo nepoužívá, a naopak klíče použité v kódu,
    které v překladech chybí.

Návratový kód 1 = našel se aspoň jeden PROBLÉM (chybějící klíč / špatné
zástupné symboly / klíč použitý v kódu bez překladu), 0 = vše v pořádku.
Nepoužité klíče se jen vypíšou jako poznámka, kód nezmění (můžou být
připravené dopředu).
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LANG_DIR = os.path.join(ROOT, "lang")
REFERENCE = "en"   # referenční jazyk: proti němu se porovnávají klíče i zástupné symboly
SOURCE_FILES = ("guitar_pro_viewer.py", "timeline_editor.py", "web_import.py")

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
# tr("klic") / tr('klic') a varianty tr_action(self, "klic", tooltip_key="jiny.klic")
_KEY_IN_CODE_RE = re.compile(r"""(?:tr|register_tr|tr_label|tr_action|tr_dock_title|tr_tab|
                                  tr_window_title)\s*\([^)]*?["']([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)["']""",
                              re.VERBOSE | re.DOTALL)
_TOOLTIP_KEY_RE = re.compile(r"""tooltip_key\s*=\s*["']([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)["']""")


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def load_langs() -> dict[str, dict[str, str]]:
    langs: dict[str, dict[str, str]] = {}
    for name in sorted(os.listdir(LANG_DIR)):
        if not name.endswith(".json"):
            continue
        code = name[:-5]
        with open(os.path.join(LANG_DIR, name), encoding="utf-8") as f:
            data = json.load(f)
        data.pop("_name", None)
        langs[code] = data
    return langs


def keys_used_in_code() -> set[str]:
    used: set[str] = set()
    for fname in SOURCE_FILES:
        path = os.path.join(ROOT, fname)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        used |= set(_KEY_IN_CODE_RE.findall(src))
        used |= set(_TOOLTIP_KEY_RE.findall(src))
    return used


def main() -> int:
    langs = load_langs()
    if REFERENCE not in langs:
        print(f"CHYBA: referenční jazyk '{REFERENCE}' (lang/{REFERENCE}.json) neexistuje")
        return 1

    ref = langs[REFERENCE]
    problems = 0
    print(f"Nalezené jazyky: {', '.join(sorted(langs))}   (reference: {REFERENCE}, {len(ref)} klíčů)\n")

    for code in sorted(langs):
        if code == REFERENCE:
            continue
        table = langs[code]
        missing = sorted(set(ref) - set(table))
        extra = sorted(set(table) - set(ref))
        bad_ph = []
        for key in sorted(set(ref) & set(table)):
            want, got = placeholders(ref[key]), placeholders(table[key])
            if want != got:
                bad_ph.append((key, sorted(want), sorted(got)))

        status = "OK" if not (missing or extra or bad_ph) else "PROBLÉM"
        print(f"[{status}] {code}: {len(table)} klíčů")
        if missing:
            problems += len(missing)
            print(f"    chybí {len(missing)}: {missing[:10]}{' …' if len(missing) > 10 else ''}")
        if extra:
            problems += len(extra)
            print(f"    navíc (nezná reference) {len(extra)}: {extra[:10]}{' …' if len(extra) > 10 else ''}")
        for key, want, got in bad_ph:
            problems += 1
            print(f"    ŠPATNÉ ZÁSTUPNÉ SYMBOLY u {key!r}: čekáno {want}, nalezeno {got}")

    used = keys_used_in_code()
    missing_in_langs = sorted(used - set(ref))
    unused = sorted(set(ref) - used)
    print()
    if missing_in_langs:
        problems += len(missing_in_langs)
        print(f"[PROBLÉM] klíče použité v kódu, ale CHYBĚJÍCÍ v překladech ({len(missing_in_langs)}):")
        for key in missing_in_langs:
            print(f"    {key}")
    else:
        print("[OK] všechny klíče použité v kódu mají překlad")
    if unused:
        print(f"[poznámka] {len(unused)} klíčů v překladech nikdo v kódu nepoužívá "
              f"(může být záměr — např. dynamicky skládané klíče):")
        for key in unused[:15]:
            print(f"    {key}")
        if len(unused) > 15:
            print(f"    … a dalších {len(unused) - 15}")

    print()
    print("VŠE V POŘÁDKU" if problems == 0 else f"NALEZENO {problems} PROBLÉMŮ")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
