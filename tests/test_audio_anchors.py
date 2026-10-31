"""[E] Bici z GP vs. skutecna nahravka — podle rucne zmerenych kotev.

Kazdy soubor `tests/data/anchors/*.json` popisuje jednu pisen:

    {
      "gp": "Olympic - Jasná Zpráva.gp5",       # cesta relativne ke koreni repa
      "audio": "20. olympic - jasna zprava (live).mp3",
      "tolerance_ms": 120,
      "anchors": [                               # takt = CISLO V PREHRAVANI
        {"bar": 1,  "beat": 1, "time_s": 0.84, "note": "prvni uder"},
        {"bar": 13, "beat": 1, "time_s": 52.30, "note": "1. sloka"},
        ...
      ]
    }

Test vezme GP casy (cisty GP import) a nejlepsi jeden posun (offset) —
stejne jako posuvnik v editoru — a kontroluje, ze VSECHNY kotvy sedi
v toleranci. U zivaku s kolisavym tempem to s pevnym tempem nevyjde: to je
presne problem E (potreba kotev / mapy tempa podle nahravky).
"""
from __future__ import annotations

import json

import pytest

from conftest import DATA, ROOT, parse_gp

FILES = sorted((DATA / "anchors").glob("*.json")) if (DATA / "anchors").exists() else []


@pytest.mark.skipif(not FILES, reason="chybi tests/data/anchors/*.json (zmerene kotvy z nahravky)")
@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_gp_sedi_na_nahravku(path):
    import guitar_pro_viewer as G
    from music_grid import TimeGrid, meter_map_from_measures
    spec = json.loads(path.read_text(encoding="utf-8"))
    song = parse_gp(ROOT / spec["gp"])
    tm = G.build_tempo_map(song)
    ms = song.tracks[0].measures
    order = G.expand_measure_order(ms)
    grid = TimeGrid([{"tick": t, "bpm": b} for t, b in tm], 4,
                    meter_map=meter_map_from_measures(ms, order))
    pairs = [(grid.position(a["bar"], a.get("beat", 1)), a["time_s"], a) for a in spec["anchors"]]
    offset = sorted(rec - gp for gp, rec, _a in pairs)[len(pairs) // 2]   # median
    tol = spec.get("tolerance_ms", 120) / 1000.0
    bad = [(a["bar"], a.get("note", ""), round(gp + offset - rec, 2))
           for gp, rec, a in pairs if abs(gp + offset - rec) > tol]
    assert not bad, "posun %.2f s; mimo toleranci (takt, pozn., chyba s): %s" % (offset, bad)
