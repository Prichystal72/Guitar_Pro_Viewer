"""
chord_align.py — rozložení textu podle AKORDŮ v nahrávce (bez velkých modelů).

1. chroma nahrávky (FFT, 55–2000 Hz → 12 tónů) průměrovaná po dobách,
2. akord z textu → šablona (základ, tercie, kvinta, příp. septima),
3. dynamické programování: akordy textu v pořadí na rostoucí doby nahrávky,
   každý akord ≥ 1 doba, doby mimo text (sólo, mezihra) za malou pokutu;
   zkouší se všech 12 transpozic (kapela může hrát jinde než text).
Začátek řádku = doba prvního akordu řádku, pak přichycení na nejbližší
nástup zpěvu (Demucs „vocals") do 2,5 doby.

Změřeno 2026-11-01 proti časovaným textům lrclib, začátek řádku do 1 doby:
Jasná zpráva 77 %, Let It Be 63 % (bez zpěvu 68 / 44 %). Pohoda a Stairway
selhávají: texty z Ultimate Guitar nemají celou strukturu (zkrácené
refrény, sloky bez akordů) — zarovnání potřebuje akordy celé písně v pořadí.
Transpozice se nehledá (C/G mají skoro stejné tóny → vybíralo +7): bere se
z `meta.bass_transpose`, jinak 0. Bez stahování modelů (vynucené zarovnání
textu MMS-FA dávalo 81–95 %, ale model má 1,2 GB — uživatel odmítl).
"""
from __future__ import annotations

import re

import numpy as np

_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "H": 11, "B": 11}
_CHORD = re.compile(r"^([A-H])(#|b|is|es|s)?(.*)$")


def chord_template(sym: str, czech_b: bool = False) -> np.ndarray | None:
    """Šablona akordu (12 hodnot). czech_b: „B" = B♭ (česká notace s H)."""
    m = _CHORD.match((sym or "").split("/")[0].strip())
    if not m:
        return None
    root = _NOTE[m.group(1)]
    if m.group(1) == "B" and czech_b:
        root = 10
    acc = m.group(2) or ""
    if acc in ("#", "is"):
        root += 1
    elif acc in ("b", "es", "s"):
        root -= 1
    q = m.group(3).lower()
    minor = q.startswith(("m", "mi", "min")) and not q.startswith("maj")
    t = np.zeros(12)
    t[root % 12] = 1.0
    t[(root + (3 if minor else 4)) % 12] = 0.8
    t[(root + 7) % 12] = 0.8
    if "maj7" in q:
        t[(root + 11) % 12] = 0.5
    elif "7" in q:
        t[(root + 10) % 12] = 0.5
    if "sus4" in q:
        t[(root + (3 if minor else 4)) % 12] = 0
        t[(root + 5) % 12] = 0.8
    return t / np.linalg.norm(t)


def beat_chroma(mono: np.ndarray, sample_rate: int, beats: np.ndarray) -> np.ndarray:
    """Chroma (doby × 12) — průměr spektra mezi sousedními dobami."""
    x = np.asarray(mono, dtype=np.float32)
    dec = max(1, int(round(sample_rate / 11025)))
    m = x.size // dec * dec
    x = x[:m].reshape(-1, dec).mean(axis=1)
    sr = sample_rate / dec
    n = 4096
    hop = int(sr * 0.05)
    win = np.hanning(n).astype(np.float32)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    band = (freqs >= 55) & (freqs <= 2000)
    pc = np.round(12 * np.log2(freqs[band] / 440.0) + 69).astype(int) % 12
    frames = []
    count = 1 + max(0, (x.size - n) // hop)
    for s in range(0, count, 512):
        e = min(count, s + 512)
        idx = np.arange(n)[None, :] + hop * np.arange(s, e)[:, None]
        mag = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, band]
        mag = np.log1p(mag * 10)
        c = np.zeros((mag.shape[0], 12))
        for k in range(12):
            c[:, k] = mag[:, pc == k].sum(axis=1)
        frames.append(c)
    ch = np.concatenate(frames)
    t = (np.arange(ch.shape[0]) * hop + n / 2) / sr
    out = np.zeros((len(beats) - 1, 12))
    for i in range(len(beats) - 1):
        sel = (t >= beats[i]) & (t < beats[i + 1])
        v = ch[sel].mean(axis=0) if sel.any() else np.zeros(12)
        v = v - v.mean()
        out[i] = v / (np.linalg.norm(v) + 1e-9)
    return out


def align_chords(chroma: np.ndarray, chords: list[str], skip_pen: float = 0.15,
                 transpositions=range(12)) -> tuple[list[int], int]:
    """Doba začátku každého akordu textu (index doby) a transpozice (půltóny).
    Akordy v pořadí textu, každý ≥ 1 doba; doby před prvním a za posledním
    akordem se přeskočí za `skip_pen` za dobu (úvod/outro mimo text)."""
    czech = any(c.strip().startswith("H") for c in chords)
    temps = [chord_template(c, czech) for c in chords]
    keep = [i for i, t in enumerate(temps) if t is not None]
    nb, nc = chroma.shape[0], len(keep)
    if nc == 0 or nb < nc:
        return [0] * len(chords), 0
    T = np.array([temps[i] for i in keep])
    NEG = -1e18
    best = None
    for tr in transpositions:
        S = chroma @ np.roll(T, tr, axis=1).T                 # [doba, akord]
        V = np.full(nc, NEG)
        V[0] = S[0, 0]
        came_new = np.zeros((nb, nc), dtype=bool)             # akord j začal na době i
        end_val, end_at = V[-1] - skip_pen * (nb - 1), 0
        for i in range(1, nb):
            cont = V
            new = np.r_[-skip_pen * i, V[:-1]]               # akord 0 po přeskočeném úvodu
            came_new[i] = new > cont
            V = np.maximum(cont, new) + S[i]
            e = V[-1] - skip_pen * (nb - 1 - i)
            if e > end_val:
                end_val, end_at = e, i
        if best is None or end_val > best[0]:
            best = (end_val, tr, came_new, end_at)
    _v, tr, came_new, end_at = best
    start = [0] * nc
    j, i = nc - 1, end_at
    while j >= 0 and i >= 0:
        if i == 0 or came_new[i, j]:
            start[j] = i
            j -= 1
        i -= 1
    out = [0] * len(chords)
    for k, idx in enumerate(keep):
        out[idx] = start[k]
    return out, tr


def vocal_onsets(vocals: np.ndarray, sample_rate: int, drop_db: float = 24.0,
                 merge_gap: float = 0.12, min_len: float = 0.15) -> np.ndarray:
    """Nástupy zpěvu (s): začátky úseků, kde je zpěv hlasitější než
    95. percentil − drop_db (krátké mezery se slijí)."""
    x = np.asarray(vocals, dtype=np.float32)
    hop = max(1, int(round(sample_rate * 0.01)))
    n = x.size // hop
    if n < 10:
        return np.zeros(0)
    rms = np.sqrt(np.mean(x[:n * hop].reshape(n, hop).astype(np.float64) ** 2, axis=1))
    db = np.convolve(20 * np.log10(np.maximum(rms, 1e-7)), np.ones(5) / 5, mode="same")
    on = db > np.percentile(db, 95) - drop_db
    segs: list[list[float]] = []
    i = 0
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j < n and on[j]:
            j += 1
        if segs and i * 0.01 - segs[-1][1] < merge_gap:
            segs[-1][1] = j * 0.01
        else:
            segs.append([i * 0.01, j * 0.01])
        i = j
    return np.array([a for a, b in segs if b - a >= min_len])
