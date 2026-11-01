"""
beat_grid.py — doby a takty přímo z nahrávky (bez GP), pro „Karaoke z nahrávky".

Neuronová síť **Beat This!** (CPJKU, ISMIR 2024, MIT, `pip install beat-this`,
model ~77 MB se stáhne při prvním použití do `~/.cache/torch`) najde v nahrávce
doby a začátky taktů. 4min píseň ~5–15 s na CPU.

Změřeno 2026-11-01 (začátky taktů z nahrávky vs. takty napasované podle GP,
`drum_align.fit_bars`): Jasná zpráva 97 %, Knockin' 91 %, Bára a Pohoda 100 %,
Zimní královna 96 % (medián chyby 10 ms). Iron Maiden 2 % a Let It Be 39 % —
tam se ale pletlo napasování podle GP: virbl na dobách 2 a 4 je u Beat This
91 % / 94 %, u GP 25 % / 84 %.
Slabina: občas falešný krátký takt (Iron Maiden 20 taktů o jedné době, Let It Be
25 o dvou) → takty o jedné době se slepí vždy, na přání pevné metrum
(`bars(..., meter=4)`, fáze taktu podle většiny začátků z Beat This).
"""
from __future__ import annotations

import numpy as np

_model = None


def available() -> bool:
    from importlib.util import find_spec
    return find_spec("beat_this") is not None and find_spec("torch") is not None


def track(mono: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """(časy dob, časy začátků taktů) v sekundách nahrávky. Smí běžet ve vlákně."""
    global _model
    from beat_this.inference import Audio2Beats
    if _model is None:
        _model = Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)
    beats, downs = _model(np.asarray(mono, dtype=np.float32), int(sample_rate))
    return np.asarray(beats, dtype=np.float64), np.asarray(downs, dtype=np.float64)


def bars(beats: np.ndarray, downs: np.ndarray, meter: int | None = None) -> tuple[np.ndarray, list[int]]:
    """Začátky taktů v nahrávce (n+1 hodnot, poslední = konec posledního
    taktu) a počet dob v každém taktu (n hodnot).

    meter=None: takty podle Beat This; takt o jedné době se přilepí
    k předchozímu (skutečný takt 1/4 v písničkách skoro není).
    meter=M: pevně M dob v taktu; fáze = zbytek indexu doby, na který
    padne nejvíc začátků taktů z Beat This."""
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 4:
        return np.zeros(0), []
    tol = 0.07
    idx = [int(np.argmin(np.abs(beats - d))) for d in downs if np.min(np.abs(beats - d)) <= tol]
    if meter:
        m = int(meter)
        votes = np.bincount(np.asarray(idx, dtype=np.int64) % m, minlength=m) if idx else np.zeros(m)
        phase = int(np.argmax(votes))
        starts_i = list(range(phase, beats.size, m))
    else:
        starts_i = sorted(set(idx)) or [0]
        merged: list[int] = []
        for i in starts_i:
            nxt = starts_i[starts_i.index(i) + 1] if i != starts_i[-1] else None
            if merged and nxt is not None and nxt - i == 1:
                continue                       # takt o jedné době → patří k předchozímu
            merged.append(i)
        starts_i = merged
    nums = [b - a for a, b in zip(starts_i, starts_i[1:])]
    starts = [float(beats[i]) for i in starts_i]
    if meter and len(starts) > 8:
        starts, nums = _regular_edges(starts, nums, int(meter))
    # poslední takt: stejný počet dob jako obvykle, délka podle posledních dob
    usual = int(np.bincount(nums).argmax()) if nums else (int(meter) if meter else 4)
    ibi = float(np.median(np.diff(beats[-9:]))) if beats.size > 2 else 0.5
    nums.append(usual)
    starts.append(starts[-1] + usual * ibi)
    return np.asarray(starts), nums


def _regular_edges(starts: list[float], nums: list[int], m: int) -> tuple[list[float], list[int]]:
    """Úvod/konec bez bicích: Beat This tam chytá doby v jiném tempu (Jasná
    zpráva: první takty 126 a 85 BPM místo 65, Pohoda 55 místo 140). Takty
    mimo ±20 % mediánu na okrajích se nahradí pravidelnými (délka mediánu)
    od prvního/posledního pravidelného taktu."""
    lens = np.diff(starts)
    med = float(np.median(lens))
    ok = np.abs(lens - med) <= 0.2 * med
    first = next((k for k in range(len(ok) - 2) if ok[k] and ok[k + 1] and ok[k + 2]), 0)
    last = next((k for k in range(len(ok) - 1, 1, -1) if ok[k] and ok[k - 1] and ok[k - 2]), len(ok) - 1)
    t0 = starts[first]
    head = []
    t = t0 - med
    while t > starts[0] - med * 0.5:
        head.insert(0, t)
        t -= med
    core = starts[first:last + 2]
    tail = []
    t = core[-1] + med
    while t < starts[-1] + med * 0.5:
        tail.append(t)
        t += med
    new = head + core + tail
    return new, [m] * (len(new) - 1)


def snare_on_backbeat(snare_times: np.ndarray, beats: np.ndarray, bar_starts: np.ndarray) -> float:
    """Kontrola fáze taktu: podíl úderů virblu na dobách 2 a 4 (rock/pop).
    Pod ~0,5 je začátek taktu nejspíš o dobu vedle."""
    ok = tot = 0
    for t in snare_times:
        i = int(np.searchsorted(bar_starts, t) - 1)
        if i < 0 or i >= len(bar_starts) - 1:
            continue
        bb = beats[(beats >= bar_starts[i] - 0.03) & (beats < bar_starts[i + 1] - 0.03)]
        if bb.size < 3:
            continue
        k = int(np.argmin(np.abs(bb - t)))
        if abs(bb[k] - t) > 0.08:
            continue
        tot += 1
        ok += k in (1, 3)
    return ok / tot if tot else 0.0
