"""Hromadna kontrola GP knihovny — bici a basa (to podstatne pro ESP32).

Spusteni nad vlastni knihovnou:
    set GP_LIBRARY=D:\\cesta\\ke\\knihovne          (PowerShell: $env:GP_LIBRARY="...")
    set GP_LIBRARY_MAX=200                          (nepovinne, vychozi 50)
nebo seznam souboru (vyber ceskych + rockovych, tools/select_library.py):
    $env:GP_LIBRARY="tests/data/library_selection.txt"
    python -m pytest tests/test_library.py -q
Bez GP_LIBRARY se pouziji ukazkove .gp3/.gp4/.gp5/.gpx/.gp ze slozky pisne/.

Pro kazdy soubor:
- vsechny udery ze VSECH bicich stop a hlasu se dostanou do exportu (krome
  vysek pod 27 — v GM bicich neznej; tyz buben ve stejny okamzik jednou),
- basa: vsechny noty krome svazanych (tie = pokracovani, ne novy uder),
- zmeny tempa z GP jsou v exportu (tempoMap),
- bici/basa na klik nenesou tMsOverride (32iny a trioly z GP jsou taky
  „na klik" — export je ted kvantuje jen na 16iny),
- delka exportu odpovida GP (referencni mapa tempa vcetne zmen).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import ROOT, gp_import, oracle_tempo_map, parse_gp


def _library_files() -> list[Path]:
    lib = os.environ.get("GP_LIBRARY")
    if lib and lib.lower().endswith(".txt"):
        # seznam souboru (tools/select_library.py -> tests/data/library_selection.txt)
        lines = Path(lib).read_text(encoding="utf-8").splitlines()
        files = [Path(x.strip()) for x in lines if x.strip() and Path(x.strip()).exists()]
        return files[: int(os.environ.get("GP_LIBRARY_MAX", "500"))]
    base = Path(lib) if lib else ROOT / "pisne"
    pattern = "**/*"
    files = sorted(p for ext in (".gp3", ".gp4", ".gp5", ".gpx", ".gp")
                   for p in base.glob(pattern + ext))
    return files[: int(os.environ.get("GP_LIBRARY_MAX", "50"))]


FILES = _library_files()


def _expected_drums(song):
    """Pocet uderu bicich v PORADI PREHRAVANI: vsechny bici stopy, vsechny
    hlasy, bez svazanych not a vysek pod 27 (v GM bicich neznej); tyz buben
    ve stejny okamzik jen jednou (zdvojeny zapis, dve stopy hrajici totez).
    Nezavisle na exportu: pocita se z tiku partitury, ne z casu."""
    import guitar_pro_viewer as G
    import guitarpro
    perc = [t for t in song.tracks if t.isPercussionTrack]
    if not perc:
        return None
    order = G.expand_measure_order(song.tracks[0].measures)
    hits = set()
    for t in perc:
        for play, src in enumerate(order):
            m = t.measures[src]
            for v in m.voices:
                for b in v.beats:
                    for x in b.notes:
                        if x.type != guitarpro.NoteType.tie and x.value >= 27:
                            hits.add((play, b.start - m.header.start, x.value))
    return len(hits)


def _is_bass(t):
    return not t.isPercussionTrack and len(t.strings) == 4


def _expected_bass(song):
    """Noty JEDNE basove stopy (pick_bass_track: GM basovy nastroj, pak jmeno
    „bass"), bez svazanych not. Ze vic stop se basa neslevá —
    test_basa_jen_z_jedne_stopy."""
    import guitar_pro_viewer as G
    import guitarpro
    vocal = G.GuitarProViewer._detect_vocal_track_index(song)
    bi = G.pick_bass_track(song, exclude=(vocal - 1) if vocal else None)
    if bi is None:
        return None
    t = song.tracks[bi]
    order = G.expand_measure_order(song.tracks[0].measures)
    return sum(1 for src in order for v in t.measures[src].voices
               for b in v.beats for x in b.notes if x.type != guitarpro.NoteType.tie)


@pytest.fixture(scope="module")
def results(qapp):
    """Kazdy soubor jednou naimportovat a exportovat (sdileno mezi testy)."""
    import guitar_pro_viewer as G
    w = G.GuitarProViewer()
    out = {}
    for f in FILES:
        try:
            song = parse_gp(f)
            ed = w._new_tab()
            w.doc_tabs.setCurrentWidget(ed)
            gp_import(w, f)
            doc = w.timeline.to_json()
            out[f] = (song, doc, w.timeline.data)
            ed._dirty = False
            w.doc_tabs.removeTab(w.doc_tabs.indexOf(ed))
        except Exception as ex:          # noqa: BLE001 — chyba importu = vysledek testu
            out[f] = ex
    for i in range(w.doc_tabs.count()):
        w.doc_tabs.widget(i)._dirty = False
    w.close()
    return out


def _get(results, f):
    r = results[f]
    if isinstance(r, Exception):
        pytest.fail("import/export spadl: %r" % r)
    return r


ids = [p.name for p in FILES]


@pytest.mark.parametrize("f", FILES, ids=ids)
def test_vsechny_udery_bicich(results, f):
    song, doc, _back = _get(results, f)
    exp = _expected_drums(song)
    if exp is None:
        pytest.skip("bez bicich")
    got = sum(len(s["drums"]) for s in doc["sections"])
    assert got == exp, "bici: export %d, v GP %d (vsechny bici stopy a hlasy)" % (got, exp)


@pytest.mark.parametrize("f", FILES, ids=ids)
def test_vsechny_noty_basy(results, f):
    song, doc, _back = _get(results, f)
    exp = _expected_bass(song)
    if exp is None:
        pytest.skip("bez basy")
    got = sum(len(s["bass"]) for s in doc["sections"])
    assert got == exp, "basa: export %d, v GP %d (bez svazanych not)" % (got, exp)


@pytest.mark.parametrize("f", FILES, ids=ids)
def test_zmeny_tempa_v_exportu(results, f):
    song, doc, _back = _get(results, f)
    gp_bpms = {bpm for _t, bpm in oracle_tempo_map(song)}
    doc_bpms = {round(e["bpm"]) for e in doc["tempoMap"]}
    assert gp_bpms <= doc_bpms, "GP tempa %s, export %s" % (sorted(gp_bpms), sorted(doc_bpms))


@pytest.mark.parametrize("f", FILES, ids=ids)
def test_bici_a_basa_na_mrizce(results, f):
    _song, doc, _back = _get(results, f)
    evs = [e for s in doc["sections"] for e in s["drums"] + s["bass"]]
    if not evs:
        pytest.skip("bez bicich i basy")
    ovr = sum(1 for e in evs if "tMsOverride" in e)
    assert ovr == 0, "%d z %d not/uderu mimo mrizku (tMsOverride)" % (ovr, len(evs))


@pytest.mark.parametrize("f", FILES, ids=ids)
def test_delka_odpovida_gp(results, f):
    """Konec posledniho taktu dle referencni mapy tempa (vcetne zmen tempa
    a metra x/8) vs. meta.durationMs. Jen soubory bez repetic (reference
    tu repetice neresi)."""
    import guitar_pro_viewer as G
    song, doc, _back = _get(results, f)
    ms = song.tracks[0].measures
    if G.expand_measure_order(ms) != list(range(len(ms))):
        pytest.skip("repetice — pokryva test_tempo_meter")
    last = ms[-1].header
    end_tick = last.start + last.length
    exp = G.ticks_to_seconds(end_tick, oracle_tempo_map(song))
    got = doc["meta"]["durationMs"] / 1000.0
    assert got == pytest.approx(exp, abs=0.5), "delka %.1f s, ma byt %.1f s" % (got, exp)


@pytest.mark.parametrize("f", FILES, ids=ids)
def test_basa_jen_z_jedne_stopy(results, f):
    """ESP32 hraje jednu basovou linku. Ma-li GP vic 4strunnych stop
    (Let It Be: „Piano Bass" + „Bass"), export nesmi slit obe (zdvojene noty)."""
    _song, _doc, data = _get(results, f)
    src = {e.get("track_index") for e in data.get("bass_timeline", [])}
    if not src:
        pytest.skip("bez basy")
    assert len(src) == 1, "basa slita ze stop %s" % sorted(src)


@pytest.mark.parametrize("f", FILES, ids=ids)
def test_bici_jen_zname_vzorky(results, f):
    """Kazdy uder musi mit jmeno bubnu, ne „?" (vyska 0 z GP)."""
    _song, doc, _data = _get(results, f)
    bad = {e["sample"] for s in doc["sections"] for e in s["drums"] if e["sample"] in ("?", "")}
    assert not bad, bad
