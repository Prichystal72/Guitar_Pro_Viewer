"""Vyber testovaci sady z GP knihovny -> tests/data/library_selection.txt

    python tools/select_library.py "C:\\Users\\prich\\Music\\Guitar Pro Tabs"

Ceske kapely (slozka ___czech + slozky ceskych interpretu) a vyber rockovych
klasik. Z vic verzi tehoz songu se bere ta „kapelova" — ma bici i basu
a nejvic stop; verze solo/acoustic/remix/live/unplugged se preskakuji.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from gpif_reader import load_any   # noqa: E402 — GP3-5 i GP6 .gpx / GP7+ .gp

CZECH_DIRS = ["___czech", "K/Kabát, V pekle sudy válej", "A/Arakain", "Ln/Landa, Daniel",
              "Z/Zámek, Visací", "V/Vypsana Fixa", "S/Skwor", "B/Brichta, Ales",
              "K/Krystof", "B/Buty", "D/Divokej, Bill", "H/Horkyze Slize"]
ROCK = [  # (slozka, regex nazvu songu)
    ("A/ACDC", r"back in black"), ("A/ACDC", r"highway to hell"), ("A/ACDC", r"thunderstruck"),
    ("D/Deep Purple", r"smoke on the water"), ("D/Deep Purple", r"highway star"),
    ("B/Black Sabbath", r"paranoid"), ("B/Bon Jovi", r"livin.{0,3} on a prayer"),
    ("E/Eagles", r"hotel california"), ("M/Metallica", r"enter sandman"),
    ("N/Nirvana", r"smells like teen spirit"), ("G/Guns N' Roses", r"sweet child"),
    ("Q/Queen", r"we will rock you"), ("Q/Queen", r"another one bites"),
    ("L/Led Zeppelin", r"whole lotta love"), ("L/Led Zeppelin", r"black dog"),
    ("I/Iron Maiden", r"the trooper"), ("L/Lynyrd Skynyrd", r"sweet home alabama"),
    ("S/Scorpions", r"still loving you"), ("R/Red Hot Chili Peppers", r"californication"),
    ("P/Pink Floyd", r"another brick"), ("Z/ZZ Top", r"la grange"),
    ("V/Van Halen", r"\bjump\b"), ("R/Rolling Stones", r"satisfaction"),
]
SKIP = re.compile(r"solo|acoustic|remix|live|unplugged|bass|drum|intro|cover|karaoke", re.I)
EXT = {".gp3", ".gp4", ".gp5", ".gpx", ".gp"}


def score(path: Path):
    try:
        song = load_any(str(path))
    except Exception:
        return None
    drums = any(t.isPercussionTrack for t in song.tracks)
    bass = any(not t.isPercussionTrack and len(t.strings) == 4 for t in song.tracks)
    return (drums, bass, len(song.tracks), -len(path.name))


def best(paths: list[Path]) -> Path | None:
    scored = [(score(p), p) for p in paths]
    scored = [(s, p) for s, p in scored if s is not None]
    return max(scored)[1] if scored else None


def song_key(p: Path) -> str:
    """„Olympic - Jasná Zpráva (1).gp5" -> „olympic - jasná zpráva"."""
    return re.sub(r"\s*\(\d+\)$", "", p.stem).strip().lower()


def main(root: str) -> int:
    base = Path(root)
    out: list[Path] = []
    for d in CZECH_DIRS:
        groups: dict[str, list[Path]] = {}
        for p in sorted((base / d).glob("*")):
            if p.suffix.lower() in EXT:
                groups.setdefault(song_key(p), []).append(p)
        out += [b for b in (best(g) for g in groups.values()) if b]
    for d, rx in ROCK:
        folder = base / d
        if not folder.exists():
            # jmena slozek se lisi („Guns N' Roses" vs. „Guns N Roses") — zkus podobnou
            cands = [f for f in folder.parent.glob("*")
                     if re.sub(r"\W", "", f.name.lower()).startswith(re.sub(r"\W", "", folder.name.lower())[:8])]
            folder = cands[0] if cands else folder
        paths = [p for p in folder.glob("*") if p.suffix.lower() in EXT
                 and re.search(rx, p.stem, re.I) and not SKIP.search(p.stem)]
        b = best(paths)
        print(("+ " if b else "- ") + d + " / " + rx + ((" -> " + b.name) if b else ""))
        if b:
            out.append(b)
    dst = Path(__file__).resolve().parent.parent / "tests" / "data" / "library_selection.txt"
    dst.write_text("\n".join(str(p) for p in out) + "\n", encoding="utf-8")
    print("%d souboru -> %s" % (len(out), dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\prich\Music\Guitar Pro Tabs"))
