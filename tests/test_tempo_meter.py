"""Zmeny TEMPA a TAKTU (metra) v pisni — cisty GP import a vymena formatu.

Ocekavane chovani (hudebni teorie, ne odhad):
- tempo v GP je ve ctvrtovych dobach; zmena tempa plati od beatu, kde je
  zapsana (mix table), a pri repetici se hraje znovu se stejnym tempem
  jako v partiture,
- takt x/8 trva num/2 ctvrtovych dob (6/8 = 3 ctvrtky), x/2 = num*2.
"""
from __future__ import annotations

import pytest

from conftest import (IRON_GP, JASNA_GP, LETITBE_GP, GNR_GP, parse_gp,
                      gp_drum_times, oracle_tempo_map)


# ---------------------------------------------------------------- tempo ---

def test_tempo_map_cte_zmeny_tempa_z_gp():
    """[B] Run To the Hills: od taktu 25 je v GP 168 BPM (mix table).
    build_tempo_map ji musi obsahovat."""
    import guitar_pro_viewer as G
    song = parse_gp(IRON_GP)
    bpms = [bpm for _t, bpm in G.build_tempo_map(song)]
    assert 168 in bpms, "zmena tempa 136->168 v GP se ignoruje: %s" % bpms


def test_bici_po_zmene_tempa_konci_ve_spravny_cas():
    """[B] Posledni uder bicich musi padnout tam, kam ho da referencni mapa
    tempa (vcetne zmen). Bez zmen tempa konci o ~40 s pozdeji."""
    import guitar_pro_viewer as G
    song = parse_gp(IRON_GP)
    drums = [t for t in song.tracks if t.isPercussionTrack][0]
    last_tick = max(b.start for m in drums.measures for b in m.voices[0].beats if b.notes)
    expected = G.ticks_to_seconds(last_tick, oracle_tempo_map(song))   # bez repetic -> primo
    got = gp_drum_times(song)[-1]
    assert got == pytest.approx(expected, abs=0.05), (
        "posledni uder %.2f s, ma byt %.2f s (rozdil %.1f s)" % (got, expected, got - expected))


def test_soubor_bez_zmen_tempa_beze_zmeny():
    """Kontrola: Knockin' (bez zmen tempa, bez repetic) — oracle = aktualni
    vystup. Musi prochazet uz ted (hlida, ze oprava nic nerozbije)."""
    import guitar_pro_viewer as G
    song = parse_gp(GNR_GP)
    assert G.build_tempo_map(song) == oracle_tempo_map(song)


def _expected_bar_starts(song, tempo_map):
    """Referencni zacatky taktu V PORADI PREHRAVANI: kazdy prehrany takt trva
    sve metrum v tempu, ktere plati v JEHO miste v partiture."""
    import guitar_pro_viewer as G
    ms = song.tracks[0].measures
    order = G.expand_measure_order(ms)
    out, t = [], 0.0
    for src in order:
        h = ms[src].header
        ts = h.timeSignature
        quarters = ts.numerator * 4 / ts.denominator.value
        out.append(t)
        t += quarters * 60.0 / G.tempo_at_tick(h.start, tempo_map)
    return order, out


def test_zmena_tempa_za_repetici_padne_na_spravny_takt():
    """[C] Let It Be ma repetici (takty 5-8, 1./2. zakonceni). Vlozime zmenu
    tempa 80->160 na zdrojovy takt 10 (za repetici). Po rozbaleni repetic
    musi zrychlit az na tom taktu, ne o 3 takty driv."""
    import guitar_pro_viewer as G
    song = parse_gp(LETITBE_GP)
    ms = song.tracks[0].measures
    tm = [(960, 80), (ms[9].header.start, 160)]
    order, expected = _expected_bar_starts(song, tm)
    drums = [t for t in song.tracks if t.isPercussionTrack][0]
    got = {}
    play = -1
    prev = None
    for m_idx, b, ts, _d in G.walk_track_beats(drums, tm):
        # prvni beat kazdeho prehraneho taktu
        if b is drums.measures[m_idx].voices[0].beats[0] and (prev is None or b is not prev):
            play += 1
            got[play] = ts
        prev = b
    bad = [(p + 1, order[p] + 1, round(got[p], 2), round(expected[p], 2))
           for p in got if abs(got[p] - expected[p]) > 0.01]
    assert not bad, "takt (prehravani, partitura, je, ma byt): %s" % bad[:5]


# ---------------------------------------------------------------- metrum ---

def test_zmena_metra_2_4_na_4_4_cisty_gp():
    """Jasna zprava: takt 1 je 2/4, zbytek 4/4. Zacatky taktu musi sedet na
    referenci (prochazi uz ted — hlida regresi)."""
    import guitar_pro_viewer as G
    from music_grid import TimeGrid, meter_map_from_measures
    song = parse_gp(JASNA_GP)
    tm = G.build_tempo_map(song)
    order, expected = _expected_bar_starts(song, tm)
    ms = song.tracks[0].measures
    grid = TimeGrid([{"tick": t, "bpm": b} for t, b in tm], 4,
                    meter_map=meter_map_from_measures(ms, order))
    got = [grid.bar_start_seconds(i + 1) for i in range(len(order))]
    assert got == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("num,den,quarters", [(6, 8, 3.0), (12, 8, 6.0),
                                              (7, 8, 3.5), (2, 2, 4.0), (3, 4, 3.0)])
def test_delka_taktu_podle_jmenovatele(num, den, quarters):
    """[D] Tempo je ve ctvrtkach. 6/8 pri 120 BPM = 3 ctvrtky = 1,5 s.
    TimeGrid ted jmenovatel ignoruje a pocita 6 ctvrtek = 3 s."""
    from music_grid import TimeGrid
    grid = TimeGrid([{"tick": 960, "bpm": 120}], 4,
                    meter_map=[{"bar": 1, "num": num, "den": den}])
    assert grid.bar_seconds(1) == pytest.approx(quarters * 0.5), (
        "%d/%d: takt %.2f s, ma byt %.2f s" % (num, den, grid.bar_seconds(1), quarters * 0.5))


# ------------------------------------------------- export (schemaVersion 1) ---

def _roundtrip(data: dict) -> dict:
    from format_migrate import migrate, to_editor_model
    doc, _w = migrate(data)
    return doc, to_editor_model(doc)


def _drum_data(tempo_map, meter_map, times):
    return {
        "meta": {"title": "t", "artist": "a", "tempo_bpm": tempo_map[0]["bpm"],
                 "beats_per_measure": meter_map[0]["num"]},
        "tempo_map": tempo_map, "meter_map": meter_map,
        "tracks": [{"index": 1, "name": "Drums", "type": "drums", "is_drums": True}],
        "lyrics_timeline": [], "chords_timeline": [],
        "drums_timeline": [{"time_s": t, "duration_s": 0.1, "drum": "Bass Drum 1",
                            "midi": 36, "track_index": 1} for t in times],
    }


def test_export_zachova_zmenu_tempa():
    """Zmena tempa 120->180 na zacatku taktu 3 (tick 960+8*960). Export musi
    mit 2 zaznamy v tempoMap a udery musi po zpetnem prevodu sedet."""
    tm = [{"tick": 960, "bpm": 120}, {"tick": 960 + 8 * 960, "bpm": 180}]
    times = [0.0, 2.0, 4.0, 4.0 + 1 / 3, 5.0]          # 4.0 s = takt 3, dale 180 BPM
    doc, back = _roundtrip(_drum_data(tm, [{"bar": 1, "num": 4, "den": 4}], times))
    assert len(doc["tempoMap"]) == 2, doc["tempoMap"]
    assert doc["tempoMap"][1]["bar"] == 3
    got = sorted(e["time_s"] for e in back["drums_timeline"])
    assert got == pytest.approx(times, abs=0.002)
    assert not any("tMsOverride" in e for s in doc["sections"] for e in s["drums"]), \
        "udery na klik nesmi potrebovat tMsOverride"


def test_export_zachova_zmenu_metra():
    """2/4 v taktu 1, pak 4/4. Uder na zacatku taktu 2 (1,0 s pri 120) musi
    byt v exportu bar 2 beat 1."""
    mm = [{"bar": 1, "num": 2, "den": 4}, {"bar": 2, "num": 4, "den": 4}]
    doc, back = _roundtrip(_drum_data([{"tick": 960, "bpm": 120}], mm, [0.0, 1.0, 3.0]))
    pos = [(e["bar"], e["beat"]) for s in doc["sections"] for e in s["drums"]]
    assert pos == [(1, 1.0), (2, 1.0), (3, 1.0)], pos
    assert doc["meterMap"] == [{"bar": 1, "num": 2, "den": 4}, {"bar": 2, "num": 4, "den": 4}]


def test_export_6_8_cislo_taktu():
    """[D] 6/8 pri 120 BPM: druhy takt zacina v 1,5 s -> bar 2."""
    doc, _back = _roundtrip(_drum_data([{"tick": 960, "bpm": 120}],
                                       [{"bar": 1, "num": 6, "den": 8}], [0.0, 1.5]))
    bars = [e["bar"] for s in doc["sections"] for e in s["drums"]]
    assert bars == [1, 2], "6/8: uder v 1,5 s vysel v taktu %s" % bars
