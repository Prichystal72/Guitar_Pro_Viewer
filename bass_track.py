"""
bass_track.py — noty basy z nahrávky (oddělená basová stopa z Demucsu).

Basic Pitch (Spotify) na Pythonu 3.13 nejde nainstalovat (stará závislost,
pkgutil.ImpImporter) → vlastní jednohlasé sledování výšky (YIN, de Cheveigné
& Kawahara 2002) jen v numpy. Basa po oddělení hraje skoro vždy jeden tón.

1. stopa → ~8,8 kHz (průměr po 5 vzorcích = hrubá dolní propust),
2. YIN po 10 ms (okno 58 ms, 35–420 Hz), znělost podle hloubky minima
   a hlasitosti,
3. začátky not: nárůst energie (i opakovaný stejný tón) + změna výšky
   o ≥ 0,7 půltónu,
4. nota = úsek ≥ 60 ms, výška = medián (bez prvních 20 ms náběhu).
"""
from __future__ import annotations

import numpy as np

HOP_S = 0.01
DEC = 5                     # 44,1 kHz → 8,82 kHz


def _frames(x: np.ndarray, n: int, hop: int) -> np.ndarray:
    count = 1 + max(0, (x.size - n) // hop)
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return x[idx]


def yin_f0(x: np.ndarray, sr: int, fmin: float = 35.0, fmax: float = 420.0,
           win_s: float = 0.058) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(f0 Hz nebo 0, hloubka minima CMND 0..1, RMS) po HOP_S."""
    hop = max(1, int(round(sr * HOP_S)))
    w = int(round(sr * win_s))
    tmax = int(sr / fmin) + 1
    tmin = max(2, int(sr / fmax))
    n = w + tmax
    nfft = 1
    while nfft < n + w:
        nfft *= 2
    pad = np.concatenate([x, np.zeros(n, dtype=np.float32)])
    f0, depth, rms = [], [], []
    count = 1 + max(0, (x.size - 1) // hop)
    for s in range(0, count, 2048):
        e = min(count, s + 2048)
        idx = np.arange(n)[None, :] + hop * np.arange(s, e)[:, None]
        fr = pad[idx].astype(np.float64)
        head = fr[:, :w]
        # d(τ) = Σ(x_j − x_{j+τ})² = E_0 + E_τ − 2·r(τ)
        A = np.fft.rfft(fr, nfft)
        B = np.fft.rfft(head, nfft)
        r = np.fft.irfft(A * np.conj(B), nfft)[:, :tmax]
        sq = np.concatenate([np.zeros((fr.shape[0], 1)), np.cumsum(fr ** 2, axis=1)], axis=1)
        e0 = sq[:, w:w + 1]
        et = sq[:, w:w + tmax] - sq[:, :tmax]
        d = np.maximum(e0 + et - 2 * r, 0.0)
        d[:, 0] = 0.0
        cm = np.cumsum(d[:, 1:], axis=1)
        cmnd = np.ones_like(d)
        cmnd[:, 1:] = d[:, 1:] * np.arange(1, tmax) / np.maximum(cm, 1e-12)
        sub = cmnd[:, tmin:]
        below = sub < 0.15
        first = np.where(below.any(axis=1), below.argmax(axis=1), sub.argmin(axis=1))
        # dojdi do lokálního minima za prvním podkročením prahu
        for k in range(sub.shape[0]):
            j = first[k]
            while j + 1 < sub.shape[1] and sub[k, j + 1] < sub[k, j]:
                j += 1
            first[k] = j
        tau = first + tmin
        rows = np.arange(sub.shape[0])
        y0 = cmnd[rows, np.clip(tau - 1, 1, tmax - 1)]
        y1 = cmnd[rows, tau]
        y2 = cmnd[rows, np.clip(tau + 1, 1, tmax - 1)]
        den = y0 - 2 * y1 + y2
        frac = np.where(np.abs(den) > 1e-12, 0.5 * (y0 - y2) / den, 0.0)
        tt = tau + np.clip(frac, -1, 1)
        f0.append(sr / tt)
        depth.append(y1)
        rms.append(np.sqrt(np.mean(head ** 2, axis=1)))
    return np.concatenate(f0), np.concatenate(depth), np.concatenate(rms)


def track_notes(stem: np.ndarray, sample_rate: int) -> list[tuple[float, float, int]]:
    """Noty basy [(začátek s, konec s, MIDI)] z oddělené basové stopy."""
    x = np.asarray(stem, dtype=np.float32)
    dec = max(1, int(round(sample_rate / 8820)))
    m = x.size // dec * dec
    x = x[:m].reshape(-1, dec).mean(axis=1)
    sr = sample_rate / dec
    f0, depth, rms = yin_f0(x, int(round(sr)))
    loud = np.percentile(rms, 95) if rms.size else 0.0
    voiced = (depth < 0.3) & (rms > 0.04 * loud)
    midi = np.where(voiced, 69 + 12 * np.log2(np.maximum(f0, 1e-3) / 440.0), np.nan)
    midi = np.where((midi >= 26) & (midi <= 70), midi, np.nan)
    # medián 5 rámců proti oktávovým skokům
    pad = np.pad(midi, 2, mode="edge")
    med = np.nanmedian(np.stack([pad[i:i + midi.size] for i in range(5)]), axis=0)
    # nárůst energie → začátek tónu (i stejný tón opakovaný)
    env = 20 * np.log10(np.maximum(rms, 1e-6))
    rise = np.zeros_like(env)
    rise[3:] = env[3:] - env[:-3]
    onset = (rise > 6.0) & (np.r_[0, rise[:-1]] <= rise) & (np.r_[rise[1:], 0] < rise)
    notes: list[tuple[float, float, int]] = []
    i = 0
    n = med.size
    while i < n:
        if np.isnan(med[i]):
            i += 1
            continue
        j = i + 1
        while j < n and not np.isnan(med[j]) and abs(med[j] - med[i]) < 0.7 and not onset[j]:
            j += 1
        if j - i >= 6:
            seg = med[i + 2:j] if j - i > 4 else med[i:j]
            notes.append((i * HOP_S, j * HOP_S, int(round(float(np.nanmedian(seg))))))
        i = j
    # sloučit sousední stejný tón bez nového náběhu (přerušení znělosti o 1–2 rámce)
    merged: list[tuple[float, float, int]] = []
    for t0, t1, p in notes:
        if merged and merged[-1][2] == p and t0 - merged[-1][1] <= 0.03 \
                and not onset[min(n - 1, int(round(t0 / HOP_S)))]:
            merged[-1] = (merged[-1][0], t1, p)
        else:
            merged.append((t0, t1, p))
    return merged


def transpose_offset(mono: np.ndarray, sample_rate: int,
                     notes: list[tuple[float, float, int]]) -> tuple[int, float]:
    """O kolik půltónů zní nahrávka jinak než noty basy ze souboru (GP/MIDI):
    (posun −6..+5, jistota = skóre nejlepšího / druhého nejlepšího).

    Pro každou notu (čas v NAHRÁVCE) se vezme chroma nízkých frekvencí
    (40–400 Hz) celé nahrávky v době noty; vyhraje posun, při kterém padá
    nejvíc energie na tóny GP. Bez Demucsu (~1 s). Změřeno: Knockin' a Zimní
    královna −1 (kapela ladí o půltón níž, GP v E), Pohoda a Bára 0."""
    x = np.asarray(mono, dtype=np.float32)
    dec = max(1, int(round(sample_rate / 8820)))
    m = x.size // dec * dec
    x = x[:m].reshape(-1, dec).mean(axis=1)
    sr = sample_rate / dec
    n = 4096                                   # ~0,46 s → rozliší E1 a F1
    hop = int(round(sr * 0.05))
    if x.size < n or not notes:
        return 0, 0.0
    win = np.hanning(n).astype(np.float32)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    band = (freqs >= 40) & (freqs <= 400)
    pc = np.round(12 * np.log2(freqs[band] / 440.0) + 69).astype(int) % 12
    chroma = []
    for s in range(0, 1 + (x.size - n) // hop, 512):
        e = min(1 + (x.size - n) // hop, s + 512)
        idx = np.arange(n)[None, :] + hop * np.arange(s, e)[:, None]
        mag = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, band] ** 2
        c = np.zeros((mag.shape[0], 12))
        for k in range(12):
            c[:, k] = mag[:, pc == k].sum(axis=1)
        chroma.append(c / np.maximum(c.sum(axis=1, keepdims=True), 1e-12))
    chroma = np.concatenate(chroma)
    centers = (np.arange(chroma.shape[0]) * hop + n / 2) / sr
    score = np.zeros(12)
    for t0, t1, midi in notes:
        sel = (centers >= t0) & (centers <= max(t1, t0 + 0.05))
        if not sel.any():
            continue
        c = chroma[sel].mean(axis=0) * (t1 - t0)
        for k in range(12):
            score[k] += c[(int(midi) + k) % 12]
    order = np.argsort(score)[::-1]
    k = int(order[0])
    conf = float(score[order[0]] / max(score[order[1]], 1e-12))
    return (k - 12 if k > 5 else k), conf
