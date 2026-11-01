"""
drum_align.py — zarovnání nahrávky (MP3/WAV) na bicí z Guitar Pro.

Jak to funguje (žádné odhady z textu, jen porovnání úderů):

1. **Obálka úderů po pásmech** (`OnsetAccumulator`) — z nahrávky se po 10 ms
   počítá spektrum a jeho NÁRŮST (spectral flux) zvlášť v pásmu kopáku,
   virblu a činelů/hi-hatu. Nárůst v daném pásmu = úder toho bubnu; kytary
   a zpěv, které hrají souvisle, nárůst skoro nedělají. Počítá se průběžně
   při dekódování, celý zvuk se v paměti nedrží.
2. **Údery z GP** (`drums_timeline`) se rozdělí do stejných tří skupin.
3. **Hledání** (`align_to_drums`) — pro každé roztažení 90–110 % se korelací
   (FFT) vyzkouší VŠECHNY posuny najednou: kolik úderů z GP padne na
   nárůst ve „svém" pásmu nahrávky. Vyhraje nejvyšší shoda, pak se roztažení
   i posun zjemní.
4. **Kontrola** — kolik úderů z GP má v nahrávce úder do ±40 ms, a jestli
   nahrávka drží tempo (`local_drift`: nejlepší posun po úsecích; živák
   bez klikačky „ujíždí" a jeden společný posun ho nezarovná).

Model posunu je stejný jako v editoru (`TimelineEditor._timeline_to_audio_t`):
    čas_v_nahrávce = (čas_na_ose − posun) / roztažení

**Rozpoznání bicích v nahrávce** (bod 1) — přednostně neuronová síť ADTOF
(`adtof_envs`, `assets/models/adtof_frame_rnn.onnx`, CC BY-NC-SA 4.0, viz
`assets/models/ADTOF_NOTICE.md`): natrénovaná na 359 h skutečné hudby, vrací
pravděpodobnost úderu kopáku/virblu/tomů/hi-hatu/činelů po 10 ms. Změřeno
(umělá nahrávka = skutečná hudba bez bicích + bicí se známými časy, F-skóre):
nárůst spektra po pásmech kopák 0,68 / virbl 0,68 / činely 0,86, ADTOF
0,91 / 0,89 / 0,86; časová chyba medián < 1 ms. Basa už nedělá falešné kopáky
(studiovka Jasné zprávy: 1428 → 393 „kopáků"). Bez `onnxruntime` nebo modelu
se použije `OnsetAccumulator` (nárůst spektra).
"""
from __future__ import annotations

import bisect
from pathlib import Path

import numpy as np

HOP_S = 0.01                      # krok obálky (10 ms)
BANDS = {                         # pásmo nahrávky (Hz) pro skupinu bubnů
    "kick": (35.0, 120.0),
    "snare": (1500.0, 5000.0),
    "cymbal": (6000.0, 16000.0),
}
WEIGHTS = {"kick": 1.0, "snare": 1.0, "cymbal": 0.5}

# GM čísla bubnů -> skupina (toms znějí ve středu jako virbl)
_KICK = {35, 36}
_SNARE = {37, 38, 39, 40, 41, 43, 45, 47, 48, 50}
_CYMBAL = {42, 44, 46, 49, 51, 52, 53, 55, 57, 59}


def drum_group(midi: int) -> str | None:
    if midi in _KICK:
        return "kick"
    if midi in _SNARE:
        return "snare"
    if midi in _CYMBAL:
        return "cymbal"
    return None


# ---------------------------------------------------------------------------
# 1. obálka úderů z nahrávky
# ---------------------------------------------------------------------------

class OnsetAccumulator:
    """Průběžný výpočet nárůstu spektra po pásmech. `feed(mono)` po kouscích
    (vzorky float −1..1, libovolná délka), `finish()` vrátí
    `{pásmo: np.ndarray}` s krokem `HOP_S`."""

    def __init__(self, sample_rate: int):
        self.sr = int(sample_rate)
        self.hop = max(1, int(round(self.sr * HOP_S)))
        n = 1
        while n < self.sr * 0.046:        # ~46 ms okno (2048 při 44,1 kHz)
            n *= 2
        self.frame = n
        self.window = np.hanning(n).astype(np.float32)
        freqs = np.fft.rfftfreq(n, 1.0 / self.sr)
        self.masks = {b: (freqs >= lo) & (freqs < hi) for b, (lo, hi) in BANDS.items()}
        self._buf = np.zeros(0, dtype=np.float32)
        self._prev: np.ndarray | None = None
        self._out: dict[str, list[np.ndarray]] = {b: [] for b in BANDS}

    def feed(self, mono: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, np.asarray(mono, dtype=np.float32)])
        n_frames = 0 if len(self._buf) < self.frame else 1 + (len(self._buf) - self.frame) // self.hop
        if n_frames <= 0:
            return
        idx = np.arange(self.frame)[None, :] + self.hop * np.arange(n_frames)[:, None]
        frames = self._buf[idx] * self.window
        mag = np.log1p(100.0 * np.abs(np.fft.rfft(frames, axis=1)))
        prev = self._prev if self._prev is not None else mag[:1]
        diff = np.diff(np.vstack([prev, mag]), axis=0)
        diff[diff < 0] = 0.0
        for b, m in self.masks.items():
            self._out[b].append(diff[:, m].sum(axis=1).astype(np.float32))
        self._prev = mag[-1:]
        self._buf = self._buf[n_frames * self.hop:]

    def finish(self) -> dict[str, np.ndarray]:
        # Rámec k začíná na vzorku k·hop, ale úder se v jeho spektru projeví
        # až kolem STŘEDU okna — obálka by předbíhala o půl okna (~23 ms,
        # změřeno na syntetické nahrávce: posun −7,279 místo −7,300 s).
        # Proto se o půl okna posune.
        lead = int(round(self.frame / 2 / self.hop))
        out = {}
        for b, parts in self._out.items():
            env = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
            env = np.concatenate([np.zeros(lead, dtype=np.float32), env])
            out[b] = _normalize(env)
        return out


ADTOF_MODEL = Path(__file__).resolve().parent / "assets" / "models" / "adtof_frame_rnn.onnx"
ADTOF_SR = 44100                  # model chce mono 44,1 kHz, výstup 100 snímků/s (= HOP_S)
# prahy pro čárky na vlnovce (nejlepší F na umělé nahrávce)
ADTOF_THRESHOLDS = {"kick": 0.3, "snare": 0.2, "cymbal": 0.5}
_adtof_session = None


def adtof_available() -> bool:
    if not ADTOF_MODEL.exists():
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def adtof_activations(mono: np.ndarray, sample_rate: int) -> np.ndarray:
    """Výstup sítě ADTOF: pole (snímky × 5) — pravděpodobnost úderu
    kopáku, virblu, tomů, hi-hatu a činelů (crash+ride) po HOP_S.
    Na 4min písni ~3 s (CPU), uvolňuje GIL, takže smí běžet ve vlákně."""
    global _adtof_session
    import onnxruntime as ort
    if _adtof_session is None:
        _adtof_session = ort.InferenceSession(str(ADTOF_MODEL), providers=["CPUExecutionProvider"])
    x = np.asarray(mono, dtype=np.float32)
    if int(sample_rate) != ADTOF_SR and x.size:
        n = int(round(x.size * ADTOF_SR / float(sample_rate)))
        x = np.interp(np.arange(n) * (float(sample_rate) / ADTOF_SR),
                      np.arange(x.size), x).astype(np.float32)
    if x.size < ADTOF_SR // 10:
        return np.zeros((0, len(ADTOF_CLASSES)), dtype=np.float32)
    return _adtof_session.run(None, {"waveform": np.clip(x, -1.0, 1.0)[None, :]})[0][0].astype(np.float32)


def group_envs(act: np.ndarray) -> dict[str, np.ndarray]:
    """5 tříd ADTOF -> 3 skupiny pro zarovnání: tomy patří k virblu
    a hi-hat k činelům stejně jako v `drum_group`."""
    return {"kick": act[:, 0].copy(),
            "snare": np.maximum(act[:, 1], act[:, 2]),
            "cymbal": np.maximum(act[:, 3], act[:, 4])}


def demucs_available() -> bool:
    from importlib.util import find_spec
    return find_spec("demucs") is not None and find_spec("torch") is not None


def demucs_drums(mono: np.ndarray, sample_rate: int) -> np.ndarray:
    return demucs_stems(mono, sample_rate, ("drums",))["drums"]


def demucs_stems(mono: np.ndarray, sample_rate: int, names=("drums", "bass")) -> dict[str, np.ndarray]:
    """Oddělí bicí z nahrávky (Demucs htdemucs, CPU; 4min píseň ~75 s,
    poprvé se stáhne model ~80 MB). Vrací mono stopu bicích 44,1 kHz.
    Změřeno (8 umělých mixů): kopák F 0,77 → 0,89, hi-hat 0,69 → 0,84,
    nejvíc u tichých bicích. Smí běžet ve vlákně."""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    model = get_model("htdemucs")
    model.eval()
    x = np.asarray(mono, dtype=np.float32)
    if int(sample_rate) != model.samplerate and x.size:
        n = int(round(x.size * model.samplerate / float(sample_rate)))
        x = np.interp(np.arange(n) * (float(sample_rate) / model.samplerate),
                      np.arange(x.size), x).astype(np.float32)
    wav = torch.from_numpy(np.vstack([x, x]))
    mu, sd = wav.mean(), wav.std() + 1e-8
    with torch.no_grad():
        out = apply_model(model, ((wav - mu) / sd)[None], device="cpu", shifts=0,
                          split=True, overlap=0.25, progress=False)[0] * sd + mu
    return {n: out[model.sources.index(n)].mean(0).numpy().astype(np.float32) for n in names}


def cache_path(audio_path: str, kind: str) -> Path:
    """Mezipaměť výsledků k nahrávce (podle cesty, velikosti a času změny)
    — Demucs se pro jednu nahrávku počítá jen jednou."""
    import hashlib
    import os
    st = os.stat(audio_path)
    key = hashlib.sha1(f"{os.path.abspath(audio_path)}|{st.st_size}|{st.st_mtime_ns}|{kind}".encode()).hexdigest()[:16]
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Guitar_Pro_Viewer" / "cache"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{Path(audio_path).stem[:40]}_{key}_{kind}.npy"


def adtof_envs(mono: np.ndarray, sample_rate: int) -> dict[str, np.ndarray]:
    """Pravděpodobnost úderu po skupinách (krok HOP_S) z ADTOF."""
    return group_envs(adtof_activations(mono, sample_rate))


# ---------------------------------------------------------------------------
# přepis bicích z nahrávky (ADTOF -> údery na mřížce taktů)
# ---------------------------------------------------------------------------

ADTOF_CLASSES = ("kick", "snare", "tom", "hihat", "cymbal")
ADTOF_MIDI = (36, 38, 47, 42, 49)            # GM: Bass Drum 1, Snare, Low-Mid Tom, Closed HH, Crash 1
RIDE_MIDI = 51                               # činel v souvislé řadě (viz quantize_hits)
# Prahy kopák, virbl, tomy, hi-hat, činely. Výchozí od autorů ADTOF (0,22/0,24/0,32/0,22/0,30),
# hi-hat a virbl doladěné na 8 umělých mixech (2 doprovody bez bicích × 2 soupravy × 0/−8 dB,
# F do 30 ms) a ověřené na studiovce Jasné zprávy proti GP: hi-hat 0,22 → 0,50 na mixu
# (F 0,69 → 0,78; studiovka 417 → 267 úderů, shoda s GP 64 → 86 %, ze skutečných ubude 7 %).
# Tomy a crash z testovacích vzorků síť nepozná vůbec (F≈0) — tomy zůstávají na prahu autorů.
# Činely: na doprovodu bez bicích (Demucs) síť při 0,30 našla 212 „činelů", při 0,50 78;
# crash z GP ve studiovce nalezen pořád v 97 % → 0,50.
ADTOF_CLASS_THRESHOLDS = (0.22, 0.24, 0.32, 0.50, 0.50)    # nahrávka (mix)
ADTOF_STEM_THRESHOLDS = (0.22, 0.10, 0.32, 0.45, 0.50)     # oddělené bicí (Demucs): virbl F 0,79 → 0,87


def combine_stem(stem_act: np.ndarray, mix_act: np.ndarray) -> np.ndarray:
    """Aktivace pro režim Demucs: kopák, virbl, tomy a hi-hat z oddělených
    bicích, ČINELY z celé nahrávky — Demucs je oslabí (studiovka: crash z GP
    nalezen 74 % místo 97 %, činelů 98 místo 264)."""
    n = min(len(stem_act), len(mix_act))
    out = stem_act[:n].copy()
    out[:, 4] = mix_act[:n, 4]
    return out


def peak_pick(a: np.ndarray, threshold: float) -> np.ndarray:
    """Indexy úderů v aktivaci jedné třídy — jako madmom
    `NotePeakPickingProcessor` u autorů ADTOF (pre_max 20 ms, post_max
    10 ms, pre_avg 100 ms, combine 20 ms)."""
    n = a.size
    if n < 3:
        return np.zeros(0, dtype=np.int64)
    pad = np.concatenate([np.full(2, -1.0, np.float32), a, np.full(1, -1.0, np.float32)])
    loc = np.max(np.stack([pad[k:k + n] for k in range(4)]), axis=0)        # okno [i-2, i+1]
    c = np.concatenate([[0.0], np.cumsum(a, dtype=np.float64)])
    lo = np.maximum(0, np.arange(n) - 10)
    hi = np.minimum(n, np.arange(n) + 2)
    avg = (c[hi] - c[lo]) / (hi - lo)
    cand = np.where((a >= threshold) & (a >= loc) & (a >= avg))[0]
    keep: list[int] = []
    for i in cand:
        if keep and i - keep[-1] <= 2:
            continue
        keep.append(int(i))
    return np.asarray(keep, dtype=np.int64)


def transcribe(act: np.ndarray, thresholds=ADTOF_CLASS_THRESHOLDS) -> list[tuple[float, int, float]]:
    """Údery z aktivací ADTOF: [(čas v nahrávce s, třída 0–4, síla 0–1)]."""
    out: list[tuple[float, int, float]] = []
    for k in range(min(act.shape[1] if act.ndim == 2 else 0, len(ADTOF_CLASSES))):
        for i in peak_pick(act[:, k], thresholds[k]):
            out.append((i * HOP_S, k, float(act[i, k])))
    out.sort()
    return out


def quantize_hits(hits_t: list[tuple[float, int, float]], grid) -> tuple[list[dict], dict]:
    """Údery (čas na časové ose, třída, síla) přichytí na mřížku `grid`
    (`music_grid.TimeGrid`): šestnáctiny, nebo osminové trioly, když úder
    leží na triole o polovinu blíž. Týž buben na stejném místě jen jednou.
    Vrací (údery {beat, time_s, cls, velocity}, statistiky) — medián
    odchylky od mřížky v ms ukáže, jestli osa sedí na nahrávku."""
    out: dict[tuple[float, int], dict] = {}
    dev_ms: list[float] = []
    n_trip = 0
    for t, k, strength in hits_t:
        if t < 0:
            continue
        b = grid.beat(t)
        q16 = round(b * 4) / 4
        q12 = round(b * 3) / 3
        e16, e12 = abs(b - q16), abs(b - q12)
        trip = e12 < 0.5 * e16
        n_trip += trip
        snapped = round(q12 if trip else q16, 4)
        ts = grid.seconds(snapped)
        dev_ms.append(abs(ts - t) * 1000.0)
        vel = int(np.clip(round(30 + 97 * strength), 1, 127))
        key = (snapped, k)
        if key not in out or out[key]["velocity"] < vel:
            out[key] = {"beat": snapped, "time_s": round(ts, 4), "cls": k, "velocity": vel}
    events = sorted(out.values(), key=lambda e: (e["time_s"], e["cls"]))
    # ADTOF nerozliší crash a ride. Ride se hraje v řadě (osminy na
    # činel místo hi-hatu — Jasná zpráva), crash osamoceně: činel se
    # sousedním činelem do půl doby = ride, jinak crash.
    cym = [e["beat"] for e in events if e["cls"] == 4]
    for e in events:
        e["midi"] = ADTOF_MIDI[e["cls"]]
        if e["cls"] == 4:
            i = bisect.bisect_left(cym, e["beat"])
            near = [cym[j] for j in (i - 1, i + 1) if 0 <= j < len(cym)]
            if any(abs(b - e["beat"]) <= 0.5 + 1e-6 for b in near):
                e["midi"] = RIDE_MIDI
    stats = {"n": len(events), "triplets": n_trip,
             "median_dev_ms": float(np.median(dev_ms)) if dev_ms else 0.0,
             "per_class": {ADTOF_CLASSES[k]: sum(1 for e in events if e["cls"] == k)
                           for k in range(len(ADTOF_CLASSES))},
             "ride": sum(1 for e in events if e["midi"] == RIDE_MIDI)}
    return events, stats


def save_drums_midi(events: list[dict], grid, path: str, ppq: int = 480) -> None:
    """Údery z `quantize_hits` jako MIDI soubor (kanál 10, GM bicí) se
    stejnou mapou tempa a metra jako časová osa."""
    import mido
    mid = mido.MidiFile(ticks_per_beat=ppq)
    meta = mido.MidiTrack()
    mid.tracks.append(meta)
    marks: list[tuple[int, int, mido.MetaMessage]] = []
    for beat, bpm in grid.segments:
        marks.append((int(round(beat * ppq)), 0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))))
    for bar, num, quarters in grid.meters:
        den = max(1, int(round(num * 4 / quarters)))
        marks.append((int(round(grid.beats_before_bar(bar) * ppq)), 1,
                      mido.MetaMessage("time_signature", numerator=num, denominator=den)))
    _write_abs(meta, marks)
    drums = mido.MidiTrack()
    mid.tracks.append(drums)
    drums.append(mido.MetaMessage("track_name", name="Drums (recording)", time=0))
    notes: list[tuple[int, int, mido.Message]] = []
    for e in events:
        tick = int(round(e["beat"] * ppq))
        note = e["midi"]
        notes.append((tick, 1, mido.Message("note_on", channel=9, note=note, velocity=e["velocity"])))
        notes.append((tick + ppq // 4, 0, mido.Message("note_off", channel=9, note=note, velocity=0)))
    _write_abs(drums, notes)
    mid.save(path)


def _write_abs(track, items) -> None:
    """(absolutní tick, pořadí, zpráva) -> zprávy s relativním časem."""
    last = 0
    for tick, _o, msg in sorted(items, key=lambda x: (x[0], x[1])):
        track.append(msg.copy(time=tick - last))
        last = tick


def _normalize(env: np.ndarray) -> np.ndarray:
    """Odečte klouzavý průměr (~0,5 s — pomalé změny hlasitosti nejsou
    údery), záporné hodnoty pryč, měřítko podle 99. percentilu."""
    if env.size == 0:
        return env
    k = max(1, int(0.5 / HOP_S))
    kernel = np.ones(k, dtype=np.float32) / k
    local = np.convolve(env, kernel, mode="same")
    e = env - local
    e[e < 0] = 0.0
    scale = float(np.percentile(e, 99)) if np.any(e > 0) else 1.0
    return (e / scale if scale > 0 else e).astype(np.float32)


def pick_onsets(env: np.ndarray, threshold: float = 0.3, min_gap_s: float = 0.06) -> np.ndarray:
    """Časy (s) lokálních maxim obálky nad prahem — údery v nahrávce
    (pro zobrazení na vlnovce a kontrolu shody)."""
    if env.size < 3:
        return np.zeros(0)
    peaks = np.where((env[1:-1] > env[:-2]) & (env[1:-1] >= env[2:]) & (env[1:-1] > threshold))[0] + 1
    gap = max(1, int(min_gap_s / HOP_S))
    keep: list[int] = []
    for p in peaks:
        if keep and p - keep[-1] < gap:
            if env[p] > env[keep[-1]]:
                keep[-1] = p
            continue
        keep.append(int(p))
    return np.asarray(keep, dtype=np.float64) * HOP_S


# ---------------------------------------------------------------------------
# 2.-4. zarovnání na bicí z GP
# ---------------------------------------------------------------------------

def gp_hits(drums_timeline: list[dict]) -> dict[str, np.ndarray]:
    """Údery z GP po skupinách (časy na časové ose, s)."""
    out: dict[str, list[float]] = {b: [] for b in BANDS}
    for ev in drums_timeline or []:
        midi = ev.get("midi")
        if midi is None:
            continue
        g = drum_group(int(midi))
        if g:
            out[g].append(float(ev.get("time_s", 0.0)))
    return {b: np.sort(np.asarray(v, dtype=np.float64)) for b, v in out.items()}


def _widen(env: np.ndarray, frames: int) -> np.ndarray:
    """Maximum v okolí ±frames — tolerance na drobné nepřesnosti hráče."""
    out = env.copy()
    for k in range(1, frames + 1):
        out[k:] = np.maximum(out[k:], env[:-k])
        out[:-k] = np.maximum(out[:-k], env[k:])
    return out


def _lag_scores(env_fft: np.ndarray, nfft: int, hits_audio: np.ndarray) -> np.ndarray:
    """Kruhová korelace: výsledek[L mod nfft] = Σ_i env[round(h_i/HOP) − L].
    `env_fft` = conj(rfft(env, nfft)); nfft ≥ délka úderů + délka obálky,
    takže se kladné a záporné posuny nepřekrývají."""
    idx = np.round(hits_audio / HOP_S).astype(np.int64)
    x = np.zeros(nfft, dtype=np.float64)
    np.add.at(x, idx[(idx >= 0) & (idx < nfft)], 1.0)
    return np.fft.irfft(np.fft.rfft(x) * env_fft, nfft)


def align_to_drums(envs: dict[str, np.ndarray], hits: dict[str, np.ndarray],
                   stretch_min: float = 0.90, stretch_max: float = 1.10) -> dict | None:
    """Nejlepší (posun, roztažení) nahrávky vůči úderům z GP.
    Vrací dict: offset, stretch, score, match (podíl úderů kopáku/virblu
    s úderem v nahrávce do ±40 ms), median_err_ms, n_hits — nebo None,
    když není s čím porovnávat."""
    bands = [b for b in BANDS if b in hits and hits[b].size and b in envs and envs[b].size]
    if not bands or sum(hits[b].size for b in bands) < 8:
        return None
    n_env = max(envs[b].size for b in bands)
    t_max = max(float(hits[b].max()) for b in bands)
    nfft = 1
    while nfft < int(t_max / stretch_min / HOP_S) + 2 + n_env:
        nfft *= 2
    env_fft = {}
    for b in bands:
        e = np.zeros(nfft, dtype=np.float64)
        e[: envs[b].size] = _widen(envs[b], 2)
        env_fft[b] = np.conj(np.fft.rfft(e))

    def evaluate(s: float) -> tuple[float, int]:
        total = np.zeros(nfft)
        for b in bands:
            total += WEIGHTS[b] * _lag_scores(env_fft[b], nfft, hits[b] / s)
        k = int(np.argmax(total))
        return float(total[k]), (k if k < nfft // 2 else k - nfft)

    # hrubě po 0,2 %, pak jemně po 0,01 % kolem nejlepšího
    best_sc, best_lag, best_s = -1.0, 0, 1.0
    for s in np.arange(stretch_min, stretch_max + 1e-9, 0.002):
        sc, lag = evaluate(float(s))
        if sc > best_sc:
            best_sc, best_lag, best_s = sc, lag, float(s)
    for s in np.arange(max(stretch_min, best_s - 0.003),
                       min(stretch_max, best_s + 0.003) + 1e-9, 0.0001):
        sc, lag = evaluate(float(s))
        if sc > best_sc:
            best_sc, best_lag, best_s = sc, lag, float(s)
    # posun L: čas v nahrávce = h/s − L·HOP = (h − posun)/s  ->  posun = L·HOP·s
    offset = best_lag * HOP_S * best_s
    res = {"offset": round(offset, 4), "stretch": round(best_s, 5), "score": best_sc}
    res.update(match_stats(envs, hits, offset, best_s))
    return res


def match_stats(envs: dict[str, np.ndarray], hits: dict[str, np.ndarray],
                offset: float, stretch: float, tol_s: float = 0.04) -> dict:
    """Podíl úderů z GP (kopák + virbl), ke kterým je v nahrávce úder
    v „jejich" pásmu do ±tol_s, a medián odchylky."""
    errs: list[float] = []
    total = 0
    for b in ("kick", "snare"):
        if b not in hits or not hits[b].size or b not in envs:
            continue
        on = pick_onsets(envs[b])
        audio_t = (hits[b] - offset) / stretch
        inside = (audio_t >= 0) & (audio_t <= envs[b].size * HOP_S)
        audio_t = audio_t[inside]
        total += audio_t.size
        if not on.size or not audio_t.size:
            continue
        j = np.clip(np.searchsorted(on, audio_t), 1, on.size - 1)
        nearest = np.where(np.abs(on[j - 1] - audio_t) < np.abs(on[j] - audio_t), on[j - 1], on[j])
        errs.extend((nearest - audio_t).tolist())
    errs_a = np.abs(np.asarray(errs)) if errs else np.zeros(0)
    ok = int(np.sum(errs_a <= tol_s))
    return {"match": (ok / total) if total else 0.0, "n_hits": total,
            "median_err_ms": float(np.median(errs_a) * 1000) if errs_a.size else None}


def local_drift(envs: dict[str, np.ndarray], hits: dict[str, np.ndarray], offset: float,
                stretch: float, window_s: float = 20.0, search_s: float = 0.6) -> list[tuple[float, float]]:
    """Po úsecích osy (`window_s`) nejlepší DODATEČNÝ posun v rozsahu
    ±search_s při pevném roztažení. Vrací [(začátek úseku na ose, posun s)].
    Stálá hodnota = nahrávka drží tempo; rozjíždějící se = živák/kolísání."""
    out: list[tuple[float, float]] = []
    allh = np.sort(np.concatenate([hits[b] for b in hits if hits[b].size]))
    if not allh.size:
        return out
    step = int(search_s / HOP_S)
    shifts = np.arange(-step, step + 1) * HOP_S
    wide = {b: _widen(envs[b], 2) for b in envs}
    t = float(allh[0])
    while t < allh[-1]:
        scores = np.zeros(shifts.size)
        n = 0
        for b in ("kick", "snare", "cymbal"):
            if b not in hits or b not in wide:
                continue
            h = hits[b][(hits[b] >= t) & (hits[b] < t + window_s)]
            if not h.size:
                continue
            n += h.size
            base = (h - offset) / stretch
            for si, d in enumerate(shifts):
                idx = np.round((base + d) / HOP_S).astype(np.int64)
                idx = idx[(idx >= 0) & (idx < wide[b].size)]
                scores[si] += WEIGHTS[b] * float(wide[b][idx].sum())
        if n >= 8 and scores.max() > 0:
            out.append((t, float(shifts[int(np.argmax(scores))])))
        t += window_s
    return out


# ---------------------------------------------------------------------------
# 5. napasování po TAKTECH (nahrávka bez klikačky — tempo plyne)
# ---------------------------------------------------------------------------

def _bar_dp(wide: dict, n_env: int, hop: float, bar_hits, bar_len: np.ndarray,
            stretch: float, centers: np.ndarray, span: int, ratio_tol: float, reg: float) -> np.ndarray:
    """Jeden průchod dynamického programování na mřížce `hop`: začátek
    taktu b se hledá mezi centers[b] ± span rámců."""
    n = len(bar_len)
    cands = [np.arange(int(round(c / hop)) - span, int(round(c / hop)) + span + 1) for c in centers]
    best = np.zeros(cands[0].size)
    back: list[np.ndarray] = []
    for b in range(n):
        cs, ns = cands[b], cands[b + 1]
        nominal = bar_len[b] / stretch / hop                       # délka taktu v rámcích
        ratio = (ns[:, None] - cs[None, :]) / nominal              # [j, i]
        ok = np.abs(ratio - 1.0) <= ratio_tol
        score = np.where(ok, best[None, :] - reg * np.abs(ratio - 1.0), -np.inf)
        for off, grp in bar_hits[b]:
            if grp not in wide:
                continue
            pos = np.rint(cs[None, :] + off / stretch / hop * ratio).astype(np.int64)
            pos = np.where((pos >= 0) & (pos < n_env), pos, n_env)  # mimo nahrávku -> 0
            score = score + WEIGHTS[grp] * wide[grp][pos]
        arg = np.argmax(score, axis=1)
        best = score[np.arange(ns.size), arg]
        back.append(arg)
    j = int(np.argmax(best))
    path = [cands[n][j]]
    for b in range(n - 1, -1, -1):
        j = int(back[b][j])
        path.append(cands[b][j])
    return np.asarray(path[::-1], dtype=np.float64) * hop


def _pooled(envs: dict[str, np.ndarray], k: int) -> dict[str, np.ndarray]:
    """Obálka zhuštěná k× (maximum v bloku) + rozšířená o ±1 rámec;
    poslední prvek 0 = místo mimo nahrávku."""
    out = {}
    for b, e in envs.items():
        if k > 1:
            m = e.size // k * k
            e = e[:m].reshape(-1, k).max(axis=1)
        out[b] = np.append(_widen(e, 1), 0.0)
    return out


def fit_bars(envs: dict[str, np.ndarray], bar_hits: list[list[tuple[float, str]]],
             bar_len: np.ndarray, first_audio: float, stretch: float,
             ratio_tol: float = 0.06, reg: float = 2.0) -> np.ndarray:
    """Začátek KAŽDÉHO taktu v nahrávce (s), dynamickým programováním.

    bar_hits[b]  údery taktu b: (posun od začátku taktu na ose v s, skupina)
    bar_len[b]   délka taktu b na ose (s)
    first_audio  přibližný čas taktu 1 v nahrávce (z `align_to_drums`)
    stretch      přibližné celkové roztažení (z `align_to_drums`; nahrávka = osa / stretch)

    Takt může být v nahrávce delší/kratší o ±ratio_tol proti `stretch`
    (kapela bez klikačky zrychluje/zpomaluje). Skóre taktu = obálka „svého"
    pásma v místech úderů z GP; malá penalizace odchylky od `stretch` (reg)
    rozhoduje u taktů bez bicích.

    Dva průchody: hrubě (40 ms, okno ±4 s kolem lineárního odhadu) najde
    správnou cestu i když celkový odhad ujede o víc než sekundu (změřeno
    na umělé nahrávce s tempem ±2,5 %: celkový odhad −1,9 s), pak jemně
    (10 ms, ±0,4 s kolem hrubé cesty). Vrací n+1 časů (začátky + konec)."""
    # model editoru: čas v nahrávce = (čas na ose − posun) / roztažení
    expected = first_audio + np.concatenate([[0.0], np.cumsum(bar_len)]) / stretch
    coarse_k = 4
    coarse = _pooled(envs, coarse_k)
    n_c = min(e.size for e in coarse.values()) - 1
    p1 = _bar_dp(coarse, n_c, HOP_S * coarse_k, bar_hits, bar_len, stretch, expected,
                 int(round(4.0 / (HOP_S * coarse_k))), ratio_tol, reg)
    fine = _pooled(envs, 1)
    n_f = min(e.size for e in fine.values()) - 1
    return _bar_dp(fine, n_f, HOP_S, bar_hits, bar_len, stretch, p1,
                   int(round(0.4 / HOP_S)), ratio_tol, reg)


def align_gp_bars(envs: dict[str, np.ndarray], gp_bars: list[list[tuple[float, str]]],
                  rec_starts: np.ndarray, quarters: np.ndarray, jump: float = 0.8,
                  empty: float = 0.08, shifts=range(4), jump_to=None) -> dict:
    """Přiřazení taktů GP taktům NAHRÁVKY (oprava struktury GP podle
    nahrávky — GP mívá repetici navíc/míň, jiný úvod).

    gp_bars[i]   údery GP taktu i: (poměrná pozice v taktu 0..1, skupina)
    rec_starts   začátky taktů nahrávky v čase NAHRÁVKY (n+1 hodnot)
    quarters     čtvrtek v taktu nahrávky (n) — kvůli posunu o dobu

    Skóre S[i, j] = průměrná aktivace „svého" bubnu v místech úderů GP taktu i
    posazeného na takt nahrávky j. Dynamické programování po taktech nahrávky:
    pokračovat dalším taktem GP zdarma, skok kamkoli v GP za `jump`, takt
    nahrávky bez bicích z GP = `empty`. Zkouší se i posun o 0–3 doby (GP
    zapsané o dobu vedle). Vrací {"assign": [index taktu GP nebo −1 pro každý
    takt nahrávky], "shift": doby, "jumps": [(takt nahrávky, z GP taktu, na GP takt)]}."""
    n_rec = len(rec_starts) - 1
    n_gp = len(gp_bars)
    if n_rec < 1 or n_gp < 1:
        return {"assign": [-1] * max(0, n_rec), "shift": 0, "jumps": []}
    starts = np.asarray(rec_starts[:-1], dtype=float)
    lens = np.diff(np.asarray(rec_starts, dtype=float))
    q = np.asarray(quarters[:n_rec], dtype=float)
    wide = {g: np.append(_widen(e, 2), 0.0) for g, e in envs.items()}
    best = None
    can_jump = np.ones(n_gp, bool) if jump_to is None else np.asarray(jump_to, bool)
    jpen = np.where(can_jump, 0.0, 1e9)                  # skok jen na začátek taktu zdroje
    for shift in shifts:
        S = np.full((n_gp, n_rec), empty)
        for i, hb in enumerate(gp_bars):
            hb = [(f, g) for f, g in hb if g in wide]
            if not hb:
                continue
            fr = np.array([f for f, _ in hb]) + shift / np.maximum(q, 1)[:, None]          # [j, h]
            t = starts[:, None] + fr * lens[:, None]
            idx = np.round(t / HOP_S).astype(np.int64)
            tot = np.zeros(n_rec)
            wsum = 0.0
            for h, (_f, g) in enumerate(hb):
                w = wide[g]
                ii = np.where((idx[:, h] >= 0) & (idx[:, h] < w.size - 1), idx[:, h], w.size - 1)
                tot += WEIGHTS[g] * w[ii]
                wsum += WEIGHTS[g]
            S[i] = np.clip(tot / wsum, 0.0, 1.0)
        # DP: stavy 0..n_gp-1 = takt GP, n_gp = bez bicích
        V = np.concatenate([S[:, 0] - jump - jpen, [empty]])
        V[0] += jump                                    # začít taktem 1 GP zdarma
        back = []
        for j in range(1, n_rec):
            k = int(np.argmax(V))
            jump_v = V[k] - jump
            cont = np.full(n_gp + 1, -np.inf)
            cont[0] = V[n_gp]                           # bez bicích → takt GP 1 (delší úvod nahrávky)
            cont[1:n_gp] = V[:n_gp - 1]                 # takt GP i-1 → i
            cont[n_gp] = V[n_gp]                        # bez bicích → zůstat bez bicích
            src = np.where(cont >= jump_v, np.r_[n_gp, np.arange(0, n_gp - 1), n_gp], k)
            jv = np.r_[jump_v - jpen, jump_v]
            src = np.where(cont >= jv, src, k)
            nv = np.maximum(cont, jv)
            nv[:n_gp] += S[:, j]
            nv[n_gp] += empty
            back.append(src)
            V = nv
        total = float(V.max())
        if best is None or total > best[0]:
            st = int(np.argmax(V))
            path = [st]
            for src in reversed(back):
                st = int(src[st])
                path.append(st)
            best = (total, shift, path[::-1])
    _total, shift, path = best
    assign = [p if p < n_gp else -1 for p in path]
    jumps = [(j + 1, assign[j - 1] + 1, assign[j] + 1) for j in range(1, n_rec)
             if assign[j] != -1 and assign[j - 1] != -1 and assign[j] != assign[j - 1] + 1]
    return {"assign": assign, "shift": shift, "jumps": jumps}


def bar_match(envs: dict[str, np.ndarray], bar_hits: list[list[tuple[float, str]]],
              bar_len: np.ndarray, audio_starts: np.ndarray, tol_s: float = 0.04) -> float:
    """Podíl úderů kopáku/virblu z GP, které po napasování po taktech mají
    v nahrávce úder svého pásma do ±tol_s."""
    onsets = {b: pick_onsets(envs[b]) for b in ("kick", "snare") if b in envs}
    ok = total = 0
    for b, hb in enumerate(bar_hits):
        r = (audio_starts[b + 1] - audio_starts[b]) / bar_len[b]
        for off, grp in hb:
            on = onsets.get(grp)
            if on is None or not on.size:
                continue
            t = audio_starts[b] + off * r
            if t < 0 or t > envs[grp].size * HOP_S:
                continue
            total += 1
            j = int(np.clip(np.searchsorted(on, t), 1, on.size - 1))
            ok += min(abs(on[j - 1] - t), abs(on[j] - t)) <= tol_s
    return ok / total if total else 0.0
