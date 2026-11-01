"""Zarovnání nahrávky na bicí z GP (drum_align.py) + načítání vlnovky.

Umělá nahrávka: bicí z GP (Jasná zpráva) vyrenderované skutečnými vzorky
z assets/drums/rock, se ZNÁMÝM posunem a roztažením, přes ně hlasité
souvislé „kytarové" akordy (12× hlasitější než bicí) a šum. Analýza musí
posun i roztažení najít.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from conftest import JASNA_GP, ROOT, SONGS, gp_import, parse_gp

SR = 44100
OFFSET, STRETCH = -7.3, 0.985
JASNA_WAV = SONGS / "Olympic - Jasná zpráva" / "jasna_zprava.wav"


def _load_wav(path: Path):
    with wave.open(str(path)) as w:
        sw, ch, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    dt = {1: np.uint8, 2: np.int16, 4: np.int32}[sw]
    a = np.frombuffer(raw, dtype=dt).astype(np.float32)
    a = (a - 128) / 128 if sw == 1 else a / float(2 ** (8 * sw - 1))
    return a.reshape(-1, ch).mean(axis=1), rate


def _sample_for(midi: int) -> str:
    if midi in (35, 36):
        return "kick"
    if midi in (37, 38, 39, 40):
        return "snare"
    if midi in (41, 43):
        return "tom_low"
    if midi in (45, 47):
        return "tom_mid"
    if midi in (48, 50):
        return "tom_hi"
    if midi in (42, 44):
        return "hihat_closed"
    if midi == 46:
        return "hihat_open"
    return "crash"


@pytest.fixture(scope="module")
def synth():
    """(mix, drum_events) — umělá nahrávka se známým posunem/roztažením."""
    import guitar_pro_viewer as G
    song = parse_gp(JASNA_GP)
    ev, _ = G.gp_drum_events(song, G.build_tempo_map(song))
    samples = {k: _load_wav(ROOT / "assets" / "drums" / "rock" / (k + ".wav"))[0]
               for k in ("kick", "snare", "hihat_closed", "hihat_open", "crash",
                         "tom_hi", "tom_mid", "tom_low")}
    dur = (ev[-1]["time_s"] - OFFSET) / STRETCH + 3
    drums = np.zeros(int(dur * SR), dtype=np.float32)
    for e in ev:
        i = int((e["time_s"] - OFFSET) / STRETCH * SR)
        s = samples[_sample_for(e["midi"])]
        if 0 <= i < len(drums):
            drums[i:i + len(s)] += s[: len(drums) - i] * 0.6
    t = np.arange(len(drums)) / SR
    gtr = np.zeros_like(drums)
    for f in (110.0, 164.8, 220.0, 329.6):
        gtr += (2 * ((t * f) % 1) - 1).astype(np.float32)
    gtr *= (0.6 + 0.4 * np.sin(2 * np.pi * t / 7)).astype(np.float32) * 0.35
    noise = np.random.default_rng(1).normal(0, 0.05, len(drums)).astype(np.float32)
    return drums + gtr + noise, ev


def _envs(mono, chunk=None):
    import drum_align as A
    acc = A.OnsetAccumulator(SR)
    if chunk:
        for k in range(0, len(mono), chunk):
            acc.feed(mono[k:k + chunk])
    else:
        acc.feed(mono)
    return acc.finish()


def test_synteticka_nahravka_presny_posun_a_roztazeni(synth):
    import drum_align as A
    mix, ev = synth
    res = A.align_to_drums(_envs(mix, 37000), A.gp_hits(ev))
    assert res["stretch"] == pytest.approx(STRETCH, abs=0.0002), res
    assert res["offset"] == pytest.approx(OFFSET, abs=0.005), res
    assert res["match"] >= 0.9 and res["median_err_ms"] < 15, res


def test_synteticka_nahravka_drzi_tempo(synth):
    import drum_align as A
    mix, ev = synth
    envs = _envs(mix)
    hits = A.gp_hits(ev)
    res = A.align_to_drums(envs, hits)
    drift = A.local_drift(envs, hits, res["offset"], res["stretch"])
    assert drift and max(abs(d) for _t, d in drift) <= 0.02, drift


def test_obalka_po_kouscich_shodna_s_celkem(synth):
    """Dekodér posílá zvuk po bufferech — výsledek nesmí záviset na dělení."""
    mix, _ev = synth
    a, b = _envs(mix[: SR * 30]), _envs(mix[: SR * 30], 4097)
    for band in a:
        np.testing.assert_allclose(a[band], b[band], rtol=1e-4, atol=1e-5)


def test_zivak_jasna_zprava_ujizdeni_rozpoznano():
    """jasna_zprava.wav nehraje v pevném tempu: průměrné tempo sedí na GP
    v 65 BPM (roztažení ~100,4 %), ale po 20 s úsecích ujíždí o ±0,3–0,5 s.
    Kontrola musí ujíždění ukázat (ne tvrdit, že nahrávka sedí)."""
    import drum_align as A
    import guitar_pro_viewer as G
    if not JASNA_WAV.exists():
        pytest.skip("chybí nahrávka")
    mono, rate = _load_wav(JASNA_WAV)
    acc = A.OnsetAccumulator(rate)
    acc.feed(mono)
    envs = acc.finish()
    song = parse_gp(JASNA_GP)
    ev, _ = G.gp_drum_events(song, G.build_tempo_map(song))
    hits = A.gp_hits(ev)
    res = A.align_to_drums(envs, hits)
    assert res["stretch"] == pytest.approx(1.0041, abs=0.002), res
    drift = A.local_drift(envs, hits, res["offset"], res["stretch"])
    assert max(abs(d) for _t, d in drift) >= 0.3, drift


# ------------------------------------------------------------ vlnovka ---

def _wait_waveform(qapp, ed, timeout_s=60):
    """Čeká skutečnou smyčkou událostí na signál dekodéru. (Smyčka
    processEvents + time.sleep dekodér hladověla — nahrávka se nenačetla
    ani za 25 s, s event loopem za 1,5 s; aplikace sama běží v event loopu.)"""
    from PySide6.QtCore import QEventLoop, QTimer
    if ed.waveform is None:
        loop = QEventLoop()
        ed._waveform_loader.finished.connect(lambda *_: QTimer.singleShot(0, loop.quit))
        ed._waveform_loader.failed.connect(lambda *_: QTimer.singleShot(0, loop.quit))
        QTimer.singleShot(int(timeout_s * 1000), loop.quit)
        loop.exec()
    assert ed.waveform is not None, "vlnovka se nenačetla"


def test_vlnovka_kbeliky_shodne_s_primym_vypoctem(window, qapp):
    """Načítání přes numpy: min/max kbelíky (400/s, každý obsahuje i nulu)
    musí přesně odpovídat přímému výpočtu z WAV."""
    from waveform import BUCKETS_PER_SECOND
    if not JASNA_WAV.exists():
        pytest.skip("chybí nahrávka")
    ed = window.timeline
    ed.load_audio(str(JASNA_WAV))
    _wait_waveform(qapp, ed)
    mono, rate = _load_wav(JASNA_WAV)
    spb = int(rate / BUCKETS_PER_SECOND)
    full = len(mono) // spb * spb
    blocks = mono[:full].reshape(-1, spb)
    exp_min = np.minimum(blocks.min(axis=1), 0)
    exp_max = np.maximum(blocks.max(axis=1), 0)
    got_min = np.asarray(ed.waveform.mins)[: len(exp_min)]
    got_max = np.asarray(ed.waveform.maxs)[: len(exp_max)]
    np.testing.assert_allclose(got_min, exp_min, atol=1e-6)
    np.testing.assert_allclose(got_max, exp_max, atol=1e-6)
    assert set(ed.waveform.onset_env) == {"kick", "snare", "cymbal"}
    assert all(len(v) for v in ed.waveform.drum_onsets.values())


def test_aplikace_zarovna_na_bici_z_gp(window, qapp, synth, tmp_path, monkeypatch):
    """Celá cesta v aplikaci: GP import, načtení umělé nahrávky, tlačítko
    „Auto-zarovnat" -> potvrzený dialog -> posun a roztažení v editoru."""
    from PySide6.QtWidgets import QDialog
    mix, _ev = synth
    wav = tmp_path / "synth.wav"
    pcm = np.clip(mix / max(1e-9, float(np.abs(mix).max())) * 0.9, -1, 1)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())
    gp_import(window, JASNA_GP)
    ed = window.timeline
    ed.load_audio(str(wav))
    _wait_waveform(qapp, ed)
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    ed.auto_align_audio()
    assert ed.audio_stretch == pytest.approx(STRETCH, abs=0.0002)
    assert ed.audio_offset_s == pytest.approx(OFFSET, abs=0.005)


# ------------------------------------------------- tempo po taktech ---

def _warped_song(drop_solo2=True):
    """GP Jasné zprávy (druhé sólo jednou), začátky taktů a údery po taktech."""
    import guitar_pro_viewer as G
    from music_grid import TimeGrid, meter_map_from_measures
    s = parse_gp(JASNA_GP)
    ms = s.tracks[0].measures
    if drop_solo2:
        G.set_repeat_plays(ms, 37, 1)
    tm = G.build_tempo_map(s)
    order = G.expand_measure_order(ms)
    mm = meter_map_from_measures(ms, order)
    grid = TimeGrid([{"tick": t, "bpm": b} for t, b in G.play_tempo_map(ms, order, tm)],
                    mm[0]["num"], meter_map=mm)
    starts = np.array([grid.bar_start_seconds(i + 1) for i in range(len(order) + 1)])
    ev, _ = G.gp_drum_events(s, tm)
    return starts, ev


@pytest.fixture(scope="module")
def drifting():
    """Umělá nahrávka, kde kapela PLYNE: lokální tempo ±2,5 % (vlna 90 s),
    začátek o 3,1 s později. Vrací (mix, údery, začátky taktů, warp(t))."""
    starts, ev = _warped_song()
    samples = {k: _load_wav(ROOT / "assets" / "drums" / "rock" / (k + ".wav"))[0]
               for k in ("kick", "snare", "hihat_closed", "hihat_open", "crash",
                         "tom_hi", "tom_mid", "tom_low")}
    tt = np.linspace(0, starts[-1] + 5, 20001)
    rate = 1 + 0.025 * np.sin(2 * np.pi * tt / 90)
    warp_v = 3.1 + np.concatenate([[0], np.cumsum(np.diff(tt) / rate[1:])])
    warp = lambda t: np.interp(t, tt, warp_v)
    drums = np.zeros(int((warp(starts[-1]) + 3) * SR), dtype=np.float32)
    for e in ev:
        i = int(warp(e["time_s"]) * SR)
        s = samples[_sample_for(e["midi"])]
        if 0 <= i < len(drums):
            drums[i:i + len(s)] += s[: len(drums) - i] * 0.6
    t = np.arange(len(drums)) / SR
    gtr = sum((2 * ((t * f) % 1) - 1) for f in (110.0, 164.8, 220.0, 329.6)).astype(np.float32) * 0.35
    noise = np.random.default_rng(2).normal(0, 0.05, len(drums)).astype(np.float32)
    return drums + gtr + noise, ev, starts, warp


def test_fit_bars_sleduje_plovouci_tempo(drifting):
    """Každý úder z GP musí po napasování po taktech ležet do 40 ms od
    skutečné polohy v nahrávce (medián < 10 ms), i když jeden celkový
    posun+roztažení ujede o víc než sekundu."""
    import drum_align as A
    mix, ev, starts, warp = drifting
    envs = _envs(mix)
    g = A.align_to_drums(envs, A.gp_hits(ev))
    bar_hits = [[] for _ in range(len(starts) - 1)]
    for e in ev:
        grp = A.drum_group(e["midi"])
        if grp:
            b = min(len(bar_hits) - 1, int(np.searchsorted(starts, e["time_s"] + 1e-6) - 1))
            bar_hits[b].append((e["time_s"] - starts[b], grp))
    path = A.fit_bars(envs, bar_hits, np.diff(starts), -g["offset"] / g["stretch"], g["stretch"])
    err = []
    for b, hb in enumerate(bar_hits):
        r = (path[b + 1] - path[b]) / (starts[b + 1] - starts[b])
        err += [abs(path[b] + off * r - warp(starts[b] + off)) for off, _g in hb]
    err = np.array(err) * 1000
    assert np.median(err) < 10 and err.max() < 40, (np.median(err), err.max())


def test_aplikace_napasuje_tempo_na_nahravku(window, qapp, drifting, tmp_path, monkeypatch):
    """Celá cesta: GP import, druhé sólo jednou (Repetice), umělá plovoucí
    nahrávka, Napasovat tempo -> údery na ose padnou na skutečné údery
    v nahrávce; Ctrl+Z vrátí původní tempo."""
    import guitar_pro_viewer as G
    from PySide6.QtWidgets import QDialog
    mix, _ev, _starts, warp = drifting
    wav = tmp_path / "drift.wav"
    pcm = np.clip(mix / max(1e-9, float(np.abs(mix).max())) * 0.9, -1, 1)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())
    gp_import(window, JASNA_GP)
    ed = window.timeline
    G.set_repeat_plays(window.song.tracks[0].measures, 37, 1)
    window._finish_gp_import(ed)
    # údery se mění NA MÍSTĚ (apply_bar_times) — původní čas u každého zvlášť
    orig = [(e, e["time_s"]) for e in ed.data["drums_timeline"]]
    ed.load_audio(str(wav))
    _wait_waveform(qapp, ed)
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    ed.fit_tempo_to_audio()
    assert ed.audio_stretch == 1.0 and len(ed.data["tempo_map"]) > 60
    # původní čas úderu t -> skutečně v nahrávce warp(t); po napasování
    # musí osa říkat totéž: (nový čas − posun) = čas v nahrávce
    err = np.array([abs(ed._timeline_to_audio_t(e["time_s"]) - float(warp(t0)))
                    for e, t0 in orig]) * 1000
    assert np.median(err) < 10 and np.percentile(err, 99) < 40, (np.median(err), err.max())
    ed.undo()
    assert len(ed.data["tempo_map"]) == 1 and ed.audio_offset_s == 0.0


def test_studiovka_jasna_zprava_po_taktech():
    """Skutečná studiovka (Olympic, bez klikačky, tempo 63,8–65,7 BPM):
    s jedním posunem sedí ~53 % úderů, po taktech aspoň 80 %."""
    import drum_align as A
    wav = SONGS / "Olympic - Jasná zpráva" / "10. Olympic - Jasná zpráva.wav"
    if not wav.exists():
        pytest.skip("chybí studiová nahrávka")
    mono, rate = _load_wav(wav)
    acc = A.OnsetAccumulator(rate)
    acc.feed(mono)
    envs = acc.finish()
    starts, ev = _warped_song()
    g = A.align_to_drums(envs, A.gp_hits(ev))
    bar_hits = [[] for _ in range(len(starts) - 1)]
    for e in ev:
        grp = A.drum_group(e["midi"])
        if grp:
            b = min(len(bar_hits) - 1, int(np.searchsorted(starts, e["time_s"] + 1e-6) - 1))
            bar_hits[b].append((e["time_s"] - starts[b], grp))
    path = A.fit_bars(envs, bar_hits, np.diff(starts), -g["offset"] / g["stretch"], g["stretch"])
    assert A.bar_match(envs, bar_hits, np.diff(starts), path) >= 0.8


def test_adtof_studiovka_basa_neni_kopak():
    """Neuronová síť ADTOF na studiovce Jasné zprávy: basová kytara se
    nesmí brát jako kopák (nárůst spektra: 1428 „kopáků", ADTOF ~390)
    a nalezené virbly mají ležet na virblech z GP (po napasování po taktech:
    nárůst spektra 17 %, ADTOF 56 %)."""
    import drum_align as A
    if not A.adtof_available():
        pytest.skip("chybí onnxruntime nebo model ADTOF")
    wav = SONGS / "Olympic - Jasná zpráva" / "10. Olympic - Jasná zpráva.wav"
    if not wav.exists():
        pytest.skip("chybí studiová nahrávka")
    mono, rate = _load_wav(wav)
    envs = A.adtof_envs(mono, rate)
    on = {b: A.pick_onsets(e, A.ADTOF_THRESHOLDS[b]) for b, e in envs.items()}
    assert 250 < on["kick"].size < 600, on["kick"].size
    starts, ev = _warped_song()
    bar_hits = [[] for _ in range(len(starts) - 1)]
    for e in ev:
        grp = A.drum_group(e["midi"])
        if grp:
            b = min(len(bar_hits) - 1, int(np.searchsorted(starts, e["time_s"] + 1e-6) - 1))
            bar_hits[b].append((e["time_s"] - starts[b], grp))
    bl = np.diff(starts)
    g = A.align_to_drums(envs, A.gp_hits(ev))
    path = A.fit_bars(envs, bar_hits, bl, -g["offset"] / g["stretch"], g["stretch"])
    gp_snare = np.array([path[b] + off * (path[b + 1] - path[b]) / bl[b]
                         for b, hb in enumerate(bar_hits) for off, grp in hb if grp == "snare"])
    near = np.min(np.abs(on["snare"][:, None] - gp_snare[None, :]), axis=1) <= 0.04
    assert near.mean() >= 0.45, near.mean()


# ------------------------------------------- repetice a výměna bubnu ---

def test_repetice_blok_zustane_v_seznamu_i_po_nastaveni_na_1():
    import guitar_pro_viewer as G
    s = parse_gp(JASNA_GP)
    ms = s.tracks[0].measures
    assert G.repeat_blocks(ms) == [(3, 7, 2), (33, 37, 2), (53, 57, 2)]
    G.set_repeat_plays(ms, 37, 1)
    assert G.repeat_blocks(ms) == [(3, 7, 2), (33, 37, 1), (53, 57, 2)]
    assert len(G.expand_measure_order(ms)) == 71        # 76 − 5 taktů
    G.set_repeat_plays(ms, 37, 2)
    assert len(G.expand_measure_order(ms)) == 76


def test_vymena_bubnu_hi_mid_tom_na_virbl(window):
    """Jasná zpráva: virbl na dobách 2 a 4 je v GP zapsaný jako Hi-Mid Tom."""
    gp_import(window, JASNA_GP)
    ed = window.timeline
    evs = ed.data["drums_timeline"]
    ti = evs[0]["track_index"]
    n_tom = sum(1 for e in evs if e["drum"] == "Hi-Mid Tom")
    assert ed.remap_drum(ti, "Hi-Mid Tom", "Acoustic Snare") == n_tom > 0
    evs = ed.data["drums_timeline"]
    assert not any(e["drum"] == "Hi-Mid Tom" for e in evs)
    assert all(e["midi"] == 38 for e in evs if e["drum"] == "Acoustic Snare")
    ed.undo()
    assert sum(1 for e in ed.data["drums_timeline"] if e["drum"] == "Hi-Mid Tom") == n_tom


# ------------------------------------- nahrávka vlevo, GP vpravo ---

def _channel_rms(path: str):
    with wave.open(path) as w:
        assert w.getnchannels() == 2
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2).astype(float)
    return np.sqrt((a ** 2).mean(axis=0))


def test_nahravka_vlevo_gp_vpravo(window, qapp, tmp_path):
    t = np.arange(SR * 3) / SR
    tone = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
    rec, gp = tmp_path / "rec.wav", tmp_path / "gp.wav"
    for p in (rec, gp):
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(tone.tobytes())
    gp_import(window, JASNA_GP)
    ed = window.timeline
    ed.load_audio(str(rec))
    _wait_waveform(qapp, ed)
    ed.gp_audio_path = str(gp)
    ed.set_split_ears(True)
    left = _channel_rms(ed.player.source().toLocalFile())
    right = _channel_rms(ed.gp_player.source().toLocalFile())
    assert left[0] > 5000 and left[1] == 0, left
    assert right[0] == 0 and right[1] > 5000, right
    ed.set_split_ears(False)
    assert Path(ed.player.source().toLocalFile()) == rec
    assert Path(ed.gp_player.source().toLocalFile()) == gp


def test_stop_vrati_kurzor_na_start(window, qapp, tmp_path):
    """Při ladění: Play od X, hraje se dál, Stop -> kurzor zpět na X.
    Pauza + Play start nemění; ruční přesun kurzoru nastaví nový start."""
    t = np.arange(SR * 30) / SR
    wav = tmp_path / "rec.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16).tobytes())
    gp_import(window, JASNA_GP)
    ed = window.timeline
    ed.load_audio(str(wav))
    _wait_waveform(qapp, ed)
    ed.set_playhead(5.0)
    ed.play_audio()
    ed.set_playhead(12.0, seek_audio=False)     # přehrávání běží dál (callback)
    ed.pause_audio()
    ed.play_audio()                              # pokračování po pauze
    ed.set_playhead(14.0, seek_audio=False)
    ed.stop_audio()
    assert ed.playhead_s == pytest.approx(5.0)
    ed.set_playhead(8.0)                         # ruční přesun = nový start
    ed.play_audio()
    ed.set_playhead(9.5, seek_audio=False)
    ed.stop_audio()
    assert ed.playhead_s == pytest.approx(8.0)


# ------------------------------------------- přepis bicích z nahrávky ---

def test_prichyceni_uderu_na_sestnactiny_a_trioly(tmp_path):
    """Údery s chybou hráče ±12 ms padnou na správnou šestnáctinu / triolu,
    stejný buben na stejném místě jen jednou; MIDI má stejné tempo i údery."""
    import mido
    import drum_align as A
    from music_grid import TimeGrid
    grid = TimeGrid([{"tick": 960, "bpm": 100.0}], 4)          # doba = 0,6 s
    rng = np.random.default_rng(1)
    want = [(0.0, 0), (0.25, 3), (0.5, 3), (1.0, 1), (1.75, 0), (2 + 1 / 3, 3), (2 + 2 / 3, 3), (3.0, 1)]
    hits = [(grid.seconds(b) + rng.uniform(-0.012, 0.012), k, 0.8) for b, k in want]
    hits.append((grid.seconds(1.0) + 0.005, 1, 0.5))           # dvojitá detekce téhož virblu
    ev, st = A.quantize_hits(hits, grid)
    assert [(round(e["beat"], 3), e["cls"]) for e in ev] == [(round(b, 3), k) for b, k in want]
    assert st["triplets"] == 2 and st["median_dev_ms"] < 12
    path = tmp_path / "bici.mid"
    A.save_drums_midi(ev, grid, str(path))
    mid = mido.MidiFile(str(path))
    tempos = [m.tempo for m in mid.tracks[0] if m.type == "set_tempo"]
    assert tempos == [mido.bpm2tempo(100.0)]
    t, ons = 0, []
    for m in mid.tracks[1]:
        t += m.time
        if m.type == "note_on" and m.velocity > 0:
            ons.append((round(t / mid.ticks_per_beat, 3), m.note, m.channel))
    assert ons == [(round(b, 3), A.ADTOF_MIDI[k], 9) for b, k in want]


def test_aplikace_prepise_bici_ze_studiovky(window, qapp, monkeypatch):
    """Celá cesta na skutečné studiovce: GP import, Napasovat tempo,
    Přepsat bicí z nahrávky -> údery leží na mřížce (osa sedí na nahrávku),
    virbl na dobách 2 a 4 jako v GP; Ctrl+Z vrátí bicí z GP."""
    import drum_align as A
    import guitar_pro_viewer as G
    from PySide6.QtWidgets import QDialog
    if not A.adtof_available():
        pytest.skip("chybí onnxruntime nebo model ADTOF")
    wav = SONGS / "Olympic - Jasná zpráva" / "10. Olympic - Jasná zpráva.wav"
    if not wav.exists():
        pytest.skip("chybí studiová nahrávka")
    gp_import(window, JASNA_GP)
    ed = window.timeline
    G.set_repeat_plays(window.song.tracks[0].measures, 37, 1)
    window._finish_gp_import(ed)
    ed.load_audio(str(wav))
    _wait_waveform(qapp, ed)
    assert ed.waveform.onset_source == "adtof" and ed.waveform.drum_act.shape[1] == 5
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    ed.fit_tempo_to_audio()
    gp_drums = [dict(e) for e in ed.data["drums_timeline"]]
    ed.transcribe_drums_from_audio()
    st = ed.last_transcription["stats"]
    assert st["median_dev_ms"] < 20, st
    assert st["triplets"] < 0.05 * st["n"], st                 # píseň je ve čtyřčtvrťovém rovném rytmu
    drums = ed.data["drums_timeline"]
    assert all(e["source"] == "generated" for e in drums)
    grid = ed._music_grid()
    snares = [e for e in drums if e["midi"] == 38]
    on_backbeat = [e for e in snares if round(grid.beat(e["time_s"]) % 2, 2) == 1.0]
    assert len(snares) > 100 and len(on_backbeat) >= 0.6 * len(snares)
    ed.undo()
    assert [(e["time_s"], e["midi"]) for e in ed.data["drums_timeline"]] == \
        [(e["time_s"], e["midi"]) for e in gp_drums]


def test_prepis_s_demucsem_studiovka(window, qapp, monkeypatch):
    """„Přesněji (Demucs)" v dialogu přepisu: bicí oddělené z nahrávky,
    výsledek v mezipaměti (podruhé hned), virbl dál na dobách 2 a 4."""
    import drum_align as A
    import guitar_pro_viewer as G
    from PySide6.QtWidgets import QDialog
    if not (A.adtof_available() and A.demucs_available()):
        pytest.skip("chybí onnxruntime/ADTOF nebo demucs")
    wav = SONGS / "Olympic - Jasná zpráva" / "10. Olympic - Jasná zpráva.wav"
    if not wav.exists():
        pytest.skip("chybí studiová nahrávka")
    gp_import(window, JASNA_GP)
    ed = window.timeline
    G.set_repeat_plays(window.song.tracks[0].measures, 37, 1)
    window._finish_gp_import(ed)
    ed.load_audio(str(wav))
    _wait_waveform(qapp, ed)
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    ed.fit_tempo_to_audio()
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    ed.transcribe_drums_from_audio()
    if not ed.last_transcription["stem"]:
        ed._transcribe_demucs()                      # poprvé ~1–2 min, pak z mezipaměti
    lt = ed.last_transcription
    assert lt["stem"] and A.cache_path(str(wav), "adtof_demucs").exists()
    st = lt["stats"]
    assert st["median_dev_ms"] < 20 and st["per_class"]["snare"] > 100, st
    grid = lt["grid"]
    snares = [e for e in lt["events"] if e["cls"] == 1]
    assert sum(round(grid.beat(e["time_s"]) % 2, 2) == 1.0 for e in snares) >= 0.6 * len(snares)
    ed.transcribe_drums_from_audio()                 # podruhé rovnou z mezipaměti
    assert ed.last_transcription["stem"]


# ------------------------------------------- průvodce „Karaoke z nahrávky" ---

def test_pruvodce_karaoke_z_nahravky(window, qapp, monkeypatch):
    """Bez GP: nahrávka → doby a takty (Beat This) → bicí → text na takty.
    Studiovka Jasné zprávy: 4/4, ~65 BPM, virbl na 2 a 4, bicí na mřížce
    z nahrávky do ~13 ms (s GP 7,6 ms), každý řádek textu (2 takty) začíná na taktu."""
    import beat_grid as BG
    import drum_align as A
    import web_import as W
    from format_migrate import migrate, to_editor_model
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QDialog
    from recording_wizard import RecordingWizard
    if not (A.adtof_available() and BG.available()):
        pytest.skip("chybí ADTOF nebo beat-this")
    wav = SONGS / "Olympic - Jasná zpráva" / "10. Olympic - Jasná zpráva.wav"
    if not wav.exists():
        pytest.skip("chybí studiová nahrávka")
    wiz = RecordingWizard(window)
    wiz.audio_page.path.setText(str(wav))
    assert wiz.audio_page.isComplete() and wiz.audio_page.validatePage()
    ed = wiz.ed
    page = wiz.grid_page
    page.initializePage()
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: page.isComplete() and loop.quit())
    poll.start(200)
    QTimer.singleShot(180000, loop.quit)
    loop.exec()
    poll.stop()
    assert page.isComplete(), page.status.text()
    assert page.meter.currentData() == 4
    assert page.validatePage()
    grid = ed._music_grid()
    assert 62 <= ed.data["meta"]["tempo_bpm"] <= 67
    assert abs(ed.audio_offset_s + page.starts[0]) < 1e-3 and ed.audio_stretch == 1.0
    sn = A.peak_pick(ed.waveform.drum_act[:, 1], A.ADTOF_CLASS_THRESHOLDS[1]) * A.HOP_S
    assert BG.snare_on_backbeat(sn, page.beats, page.starts) >= 0.9
    # bicí na mřížce z nahrávky
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    ed.transcribe_drums_from_audio()
    assert ed.last_transcription["stats"]["median_dev_ms"] < 15, ed.last_transcription["stats"]   # změřeno 12,6 ms
    assert sum(1 for e in ed.data["drums_timeline"] if e["source"] == "generated") > 800
    # text a akordy: 2 takty na řádek, 1 úvodní takt
    lines = (SONGS / "Olympic - Jasná zpráva" / "Jasná Zpráva.txt").read_text(encoding="utf-8").splitlines()
    web = W.build_karaoke_json(W.apply_chord_templates(W.detect_chord_chart(lines)), "Jasná zpráva", "Olympic",
                               120, "", bars_per_line=2, count_in_bars=1)
    flat = to_editor_model(migrate(web)[0])
    ed.place_text_on_grid(flat, first_bar=2)
    kls = ed.data["karaoke_lines"]
    assert len(kls) == len(flat["karaoke_lines"]) > 10
    bar_starts = [grid.bar_start_seconds(b) for b in range(1, 200)]
    assert all(min(abs(kl["start_s"] - b) for b in bar_starts) < 0.002 for kl in kls)
    assert len({e["track_index"] for e in ed.data["lyrics_timeline"]}
               & {e["track_index"] for e in ed.data["drums_timeline"]}) == 0
    assert len(ed.data["drums_timeline"]) > 800          # bicí zůstaly
    ed.undo()
    assert not ed.data.get("karaoke_lines")


def test_pruvodce_bici_z_gp_a_preskoceni(window, qapp):
    """Průvodce: nahrávka přeskočena → kroky jdou dál v nové kartě; bicí z GP
    na takty nahrávky opraví strukturu GP (Jasná zpráva: GP s druhým sólem
    dvakrát vs. studiovka, kde je jednou)."""
    import beat_grid as BG
    import drum_align as A
    from PySide6.QtCore import QEventLoop, QTimer
    from recording_wizard import RecordingWizard
    wiz = RecordingWizard(window)
    wiz.restart()
    wiz._skip()                                      # 1. nahrávka přeskočena
    assert wiz.currentPage() is wiz.grid_page and wiz.ed is None
    wiz._skip()                                      # 2. takty přeskočeny
    st = wiz.drums_page.place_gp(parse_gp(JASNA_GP))  # bez nahrávky: takt GP i = takt i
    assert wiz.ed is not None and st["n"] > 900 and st["jumps"] == []          # všech 934 úderů GP
    if not (A.adtof_available() and BG.available()):
        pytest.skip("chybí ADTOF nebo beat-this")
    wav = SONGS / "Olympic - Jasná zpráva" / "10. Olympic - Jasná zpráva.wav"
    if not wav.exists():
        pytest.skip("chybí studiová nahrávka")
    wiz = RecordingWizard(window)
    wiz.audio_page.path.setText(str(wav))
    wiz.audio_page.validatePage()
    page = wiz.grid_page
    page.initializePage()
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: page.isComplete() and loop.quit())
    poll.start(200)
    QTimer.singleShot(180000, loop.quit)
    loop.exec()
    poll.stop()
    page.validatePage()
    st = wiz.drums_page.place_gp(parse_gp(JASNA_GP))
    # pevné 4/4 u Jasné zprávy (má takty 2/4) je za nimi o půl taktu vedle: změřeno 63 %,
    # s „takty podle nahrávky" 74 %; jeden společný posun dával 64 %
    assert st["match"] >= 0.6, st
    assert all(e["source"] == "gp" for e in wiz.ed.data["drums_timeline"])


def test_pruvodce_bici_z_midi_bez_nahravky(window):
    """Průvodce bez nahrávky: bicí z MIDI (Stairway, export z GP) takt po
    taktu — stejný počet úderů a taktů jako GP."""
    import midi_import as MI
    from recording_wizard import RecordingWizard
    mid = SONGS / "Led Zeppelin - Stairway to Heaven" / "Led Zeppelin-Stairway to Heaven-08-13-2026.mid"
    if not mid.exists():
        pytest.skip("chybí MIDI")
    items = MI.midi_drum_bars(str(mid))
    assert len(items) == 1272 and max(b for b, _f, _m in items) == 163
    wiz = RecordingWizard(window)
    st = wiz.drums_page.show_stats(wiz.editor().place_midi_drums_on_grid(str(mid)))
    assert st["n"] == 1272 and st["jumps"] == []
    assert "1272" in wiz.drums_page.result.text()


def test_projekt_kproj_relativni_cesty(window, tmp_path, monkeypatch):
    """Uložit = projekt .kproj s relativními cestami; Otevřít ho načte
    i po přesunu složky; export pro ESP32 je čistý JSON bez `project`."""
    import json
    import shutil
    from PySide6.QtWidgets import QFileDialog
    song_dir = tmp_path / "pisen"
    song_dir.mkdir()
    gp_import(window, JASNA_GP)
    ed = window.timeline
    wav = song_dir / "nahravka.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.zeros(SR, np.int16).tobytes())
    ed.audio_path = str(wav)
    ed.data.setdefault("meta", {})["audio_file"] = str(wav)
    proj = song_dir / "Jasná zpráva.kproj"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(proj), "")))
    window.save_json_as()
    d = json.loads(proj.read_text(encoding="utf-8"))
    assert d["schemaVersion"] == 1 and d["project"]["audio"] == "nahravka.wav"
    assert sum(len(s.get("drums") or []) for s in d["sections"]) > 800
    out = song_dir / "esp32.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    window.export_esp32_json()
    e = json.loads(out.read_text(encoding="utf-8"))
    assert "project" not in e and e["schemaVersion"] == 1
    moved = tmp_path / "jinde"
    shutil.move(str(song_dir), str(moved))
    window._tab_for_new_document()
    window._load_karaoke_json(str(moved / "Jasná zpráva.kproj"))
    assert window.timeline.data["meta"]["audio_file"] == str((moved / "nahravka.wav").resolve())


def test_text_ze_souboru_chordpro_a_html(window):
    """Dialog textu ▸ Ze souboru: ChordPro i uložená webová stránka projdou
    stejně jako text vložený / stažený z webu."""
    from web_import import WebImportDialog
    dlg = WebImportDialog(window)
    dlg.load_text_file(str(ROOT / "tests" / "data" / "formaty_textu" / "swinglow.cho"))
    assert dlg.title_input.text() == "Swing Low Sweet Chariot"
    assert "Swing low, sweet chariot" in dlg.raw_text.toPlainText()
    assert len(dlg._detected) > 10
    page = SONGS / "Beatles - Let It Be" / "texty" / "guitaretab.html"
    if page.exists():
        dlg2 = WebImportDialog(window)
        dlg2.load_text_file(str(page))
        assert len(dlg2._detected) > 10 and "Let it be" in dlg2.raw_text.toPlainText()
