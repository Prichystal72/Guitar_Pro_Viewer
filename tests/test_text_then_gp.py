"""[A] Cesta „nejdriv text (web/vlastni), pak otevrit GP" — bici z GP musi
sedet na text i na GP casovani. Vychozi hodnoty jako v dialogu: tempo 120,
2 takty/radek, 1 uvodni takt.

Ocekavane chovani: GP je pan casovani (CLAUDE.md, JSON_FORMAT.md) — tempo,
metrum i polohy radku se po otevreni GP ridi GP, ne mrizkou z dialogu.
"""
from __future__ import annotations

import pytest

from conftest import (IRON_GP, JASNA_GP, JASNA_TXT, parse_gp, text_then_gp,
                      gp_import, export_seconds, gp_drum_times, gp_vocal_first_time)


def test_tempo_dokumentu_prevezme_gp(window):
    """[A1] Po otevreni GP musi mit dokument tempo z GP (65), ne 120 z dialogu."""
    text_then_gp(window, JASNA_TXT, JASNA_GP, tempo=120)
    song = parse_gp(JASNA_GP)
    doc = window.timeline.to_json()
    assert doc["tempoMap"][0]["bpm"] == pytest.approx(song.tempo), doc["tempoMap"]


def test_metrum_dokumentu_prevezme_gp(window):
    """[A1] Jasna zprava ma takt 1 v 2/4 — export musi mit metrum z GP."""
    text_then_gp(window, JASNA_TXT, JASNA_GP)
    doc = window.timeline.to_json()
    assert doc["meterMap"][0] == {"bar": 1, "num": 2, "den": 4}, doc["meterMap"]


def test_bici_bez_tMsOverride(window):
    """[A1] Bici z GP jsou na klik — v exportu nesmi skoro zadny uder nest
    tMsOverride (Iron Maiden JSON od uzivatele: 58 %)."""
    text_then_gp(window, JASNA_TXT, IRON_GP)
    doc = window.timeline.to_json()
    drums = [e for s in doc["sections"] for e in s["drums"]]
    ovr = sum(1 for e in drums if "tMsOverride" in e)
    assert drums and ovr / len(drums) < 0.01, "%d z %d uderu mimo mrizku" % (ovr, len(drums))


@pytest.mark.parametrize("count_in", [1, 4])
def test_zadny_uder_se_neztrati_ani_nestlaci(window, count_in):
    """[A3] Odpocet nesmi zahodit ani stlacit na nulu skutecne udery z GP.
    (Iron Maiden JSON: 38 zahozeno; Jasna zprava: 27 stlaceno na 1:1.)"""
    text_then_gp(window, JASNA_TXT, JASNA_GP, count_in_bars=count_in)
    song = parse_gp(JASNA_GP)
    doc = window.timeline.to_json()
    notes = " | ".join(window.timeline.last_export_notes or [])
    n_export = sum(len(s["drums"]) for s in doc["sections"])
    assert n_export == len(gp_drum_times(song)), notes
    assert "odpočtu" not in notes, notes


def test_bici_vuci_textu_stejne_jako_v_gp(window):
    """[A2+A3] Prvni zpivany radek musi zacinat vuci prvnimu uderu bicich
    stejne jako v GP (zpevni stopa Vocal) — tolerance 1 doba."""
    song = parse_gp(JASNA_GP)
    gp_gap = gp_vocal_first_time(song) - gp_drum_times(song)[0]
    text_then_gp(window, JASNA_TXT, JASNA_GP)
    back = export_seconds(window.timeline.to_json())
    first_lyric = min(e["time_s"] for e in back["lyrics_timeline"]
                      if any(ch.isalpha() for ch in e.get("text", "")))
    first_drum = min(e["time_s"] for e in back["drums_timeline"])
    beat = 60.0 / song.tempo
    assert first_lyric - first_drum == pytest.approx(gp_gap, abs=beat), (
        "text vuci bicim: %.2f s, v GP %.2f s" % (first_lyric - first_drum, gp_gap))


def test_ctrl_m_a_otevreni_gp_davaji_stejne_tempo(window):
    """Obe cesty „text + GP" musi skoncit se stejnym tempem/metrem.
    (merge_with_web pres dialog tu nejde spustit bez klikani — porovnava se
    s cistym GP importem, ktery je referenci casovani.)"""
    gp_import(window, JASNA_GP)
    ref = window.timeline.to_json()
    window._new_tab()
    window.doc_tabs.setCurrentIndex(window.doc_tabs.count() - 1)
    text_then_gp(window, JASNA_TXT, JASNA_GP)
    doc = window.timeline.to_json()
    assert (doc["tempoMap"], doc["meterMap"]) == (ref["tempoMap"], ref["meterMap"])


# ------------------------------------------------ reference: cisty GP import ---

def test_cisty_gp_import_bici_a_text_sedi(window):
    """Reference (prochazi uz ted): cisty GP import Jasne zpravy — export
    zachova vsechny udery a vzdalenost text/bici z GP."""
    song = parse_gp(JASNA_GP)
    gp_import(window, JASNA_GP)
    back = export_seconds(window.timeline.to_json())
    assert len(back["drums_timeline"]) == len(gp_drum_times(song))
    first_lyric = min(e["time_s"] for e in back["lyrics_timeline"])
    first_drum = min(e["time_s"] for e in back["drums_timeline"])
    gp_gap = gp_vocal_first_time(song) - gp_drum_times(song)[0]
    assert first_lyric - first_drum == pytest.approx(gp_gap, abs=0.05)
