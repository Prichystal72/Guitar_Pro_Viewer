"""Spolecne pripravky pro testy (pytest, Qt offscreen).

Spusteni:  python -m pytest tests -v
Testy pouzivaji ukazkove pisne ve slozce `pisne/` (.gp3/.gp5/.txt) — jen je
CTOU, nic nezapisuji. Chybi-li soubor, test se preskoci (ne spadne).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SONGS = ROOT / "pisne"          # ukazkove pisne, kazda ve vlastni slozce
JASNA_GP = SONGS / "Olympic - Jasná zpráva" / "Olympic - Jasná Zpráva.gp5"
JASNA_TXT = SONGS / "Olympic - Jasná zpráva" / "Jasná Zpráva.txt"
IRON_GP = SONGS / "Iron Maiden - Run To the Hills" / "Iron Maiden - Run To the Hills (2).gp3"
LETITBE_GP = SONGS / "Beatles - Let It Be" / "Beatles (The) - Let It Be.gp4"
GNR_GP = SONGS / "Guns N Roses - Knockin on Heavens Door" / "guns-n-roses-knockin_on_heaven_s_door_5.gp3"


def need(path: Path) -> Path:
    if not path.exists():
        pytest.skip("chybi testovaci soubor: %s" % path.name)
    return path


def parse_gp(path: Path):
    """GP3-GP5 (pyguitarpro) i GP6 .gpx / GP7+ .gp (gpif_reader) — stejne jako aplikace."""
    from gpif_reader import load_any
    return load_any(str(need(path)))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    # hlasky by v testu blokovaly — jen je ztlumime
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    return app


@pytest.fixture
def window(qapp):
    import guitar_pro_viewer as G
    w = G.GuitarProViewer()
    w.resize(1600, 950)
    w.show()
    qapp.processEvents()
    yield w
    for i in range(w.doc_tabs.count()):
        w.doc_tabs.widget(i)._dirty = False
    w.close()


def gp_import(window, path: Path) -> None:
    """Cisty GP import do aktivni karty — synchronne (bez LoadWorker vlakna)."""
    song = parse_gp(path)
    window._on_loaded(song, str(path), window.timeline)


def text_then_gp(window, txt: Path, gp: Path, tempo: int = 120,
                 bars_per_line: int = 2, count_in_bars: int = 1) -> dict:
    """Cesta uzivatele „nejdriv text, pak otevrit GP":
    1) vlastni text pres stejne funkce jako WebImportDialog (vychozi hodnoty
       dialogu: tempo 120, 2 takty/radek, 1 uvodni takt),
    2) „Pouzit v editoru" (_present_karaoke_data),
    3) otevreni GP souboru do TE SAME karty (_on_loaded -> merge).
    Vraci web data PRED sloucenim (kopii), pro srovnani."""
    import copy
    import web_import as W
    lines = need(txt).read_text(encoding="utf-8").splitlines()
    detected = W.apply_chord_templates(W.detect_chord_chart(lines))
    web = W.build_karaoke_json(detected, "Test", "Test", tempo, "",
                               bars_per_line=bars_per_line, count_in_bars=count_in_bars)
    before = copy.deepcopy(web)
    window._present_karaoke_data(web, source_desc="test")
    song = parse_gp(gp)
    window._on_loaded(song, str(gp), window.timeline)
    return before


def export_seconds(doc: dict) -> dict:
    """Exportovany dokument (schemaVersion 1) zpet na sekundy — presne tak,
    jak ho cte editor/firmware (bar/beat + tempoMap/meterMap, tMsOverride)."""
    from format_migrate import to_editor_model
    return to_editor_model(doc)


def gp_drum_times(song, tempo_map=None) -> list[float]:
    """Casy vsech uderu bicich z GP (aktualni walk_track_beats)."""
    import guitar_pro_viewer as G
    tm = tempo_map or G.build_tempo_map(song)
    out = []
    for t in song.tracks:
        if t.isPercussionTrack:
            for _m, b, ts, _d in G.walk_track_beats(t, tm):
                out.extend([ts] * len(b.notes))
    return sorted(out)


def gp_vocal_first_time(song) -> float:
    import guitar_pro_viewer as G
    vi = G.GuitarProViewer._detect_vocal_track_index(song)
    tm = G.build_tempo_map(song)
    for _m, b, ts, _d in G.walk_track_beats(song.tracks[vi - 1], tm):
        if (G.get_beat_text(b) or "").strip():
            return ts
    raise AssertionError("GP nema text ve zpevni stope")


def oracle_tempo_map(song) -> list[tuple[int, int]]:
    """NEZAVISLA referencni mapa tempa: song.tempo + vsechny zmeny tempa
    z mix table (tam je GP3/4/5 skutecne uklada) a u GP6/GP7 z automace
    hlavni stopy (gpif_reader -> song.extraTempoChanges). (tick, bpm)
    v surovych tickach partitury. Nezavislou kontrolou casu GP7 je
    test_gpif.py (porovnani s MIDI exportem z Guitar Pro)."""
    changes = {960: song.tempo}
    for t in song.tracks:
        for m in t.measures:
            for v in m.voices:
                for b in v.beats:
                    mc = getattr(b.effect, "mixTableChange", None)
                    if mc is not None and mc.tempo is not None and mc.tempo.value > 0:
                        changes[b.start] = mc.tempo.value
    for tick, bpm in getattr(song, "extraTempoChanges", None) or []:
        changes[int(tick)] = int(bpm)
    out = []
    for tick, bpm in sorted(changes.items()):
        if not out or out[-1][1] != bpm:
            out.append((tick, bpm))
    return out
