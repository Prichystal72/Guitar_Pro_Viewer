"""GUI — prekryvy a orezane texty (okno 1600x950, offscreen, Jasna zprava)."""
from __future__ import annotations

import re

import pytest

from conftest import JASNA_GP, gp_import


@pytest.fixture
def loaded(window, qapp):
    gp_import(window, JASNA_GP)
    qapp.processEvents()
    return window


def test_hlavicky_stop_se_neprekryvaji(loaded):
    """Jmeno stopy nesmi lezet pres popisky radku („Vocal" pres „radky",
    „Drums" pres „Closed Hi-Hat")."""
    from PySide6.QtWidgets import QGraphicsSimpleTextItem
    from timeline_editor import HEADER_W
    scene = loaded.timeline.scene
    items = [i for i in scene.items()
             if isinstance(i, QGraphicsSimpleTextItem) and i.isVisible()
             and i.sceneBoundingRect().left() < HEADER_W and i.text().strip()]
    hits = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            ra, rb = items[a].sceneBoundingRect(), items[b].sceneBoundingRect()
            if ra.intersects(rb) and ra.intersected(rb).height() > 2:
                hits.append((items[a].text(), items[b].text()))
    assert not hits, "prekryte popisky: %s" % hits[:6]


def test_tlacitka_nemaji_orezany_text(loaded):
    """Text tlacitka se musi vejit (spodni audio lista: „Nacist MP3/", „GP bici r")."""
    from PySide6.QtWidgets import QAbstractButton, QCheckBox, QRadioButton
    cut = []
    for btn in loaded.findChildren(QAbstractButton):
        if not btn.isVisible() or not btn.text() or isinstance(btn, (QCheckBox, QRadioButton)):
            continue
        need = btn.sizeHint().width()
        if btn.width() + 2 < need:
            cut.append((btn.text().replace("&", ""), btn.width(), need))
    assert not cut, "orezana tlacitka (text, sirka, potreba): %s" % cut


def test_ovladaci_kolecko_je_cele_videt(loaded):
    """Jog/shuttle nesmi byt uriznute okrajem okna."""
    from jog_shuttle import JogShuttleWidget
    for jog in loaded.findChildren(JogShuttleWidget):
        if not jog.isVisible():
            continue
        r = jog.rect()
        tl = jog.mapTo(loaded, r.topLeft())
        br = jog.mapTo(loaded, r.bottomRight())
        assert loaded.rect().contains(tl) and loaded.rect().contains(br), (
            "kolecko %s..%s mimo okno %s" % (tl, br, loaded.rect()))
        sh = jog.sizeHint()
        assert jog.width() >= sh.width() and jog.height() >= sh.height(), (
            "kolecko %dx%d, potrebuje %dx%d" % (jog.width(), jog.height(), sh.width(), sh.height()))


def test_pravitko_bez_dlouhych_desetinnych_cisel(loaded):
    """„Takt 2 · 1.84615s" — cas na pravitku max. 2 desetinna mista."""
    from PySide6.QtWidgets import QGraphicsSimpleTextItem
    long_nums = [i.text() for i in loaded.timeline.scene.items()
                 if isinstance(i, QGraphicsSimpleTextItem)
                 and re.search(r"\d+[.,]\d{3,}\s*s", i.text())]
    assert not long_nums, long_nums[:5]


def test_typ_stopy_vocal_neni_kytara(loaded):
    """Zpevni stopa ve stromu Stopy ma byt „zpev", ne „kytara"."""
    tree = loaded.track_tree
    for i in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(i)
        if "Vocal" in it.text(0):
            assert it.data(1, 0x0100) == "vocal", it.data(1, 0x0100)   # Qt.UserRole
            return
    pytest.skip("stopa Vocal ve stromu neni")
