"""waveform.py — načtení tvaru vlny (peaků) z MP3/WAV pro zobrazení na
časové ose.

Dekóduje přes `QAudioDecoder` (součást QtMultimedia — stejný backend, jaký
používá přehrávač, takže žádná nová závislost a zvládne stejné formáty).
Dekódování běží ASYNCHRONNĚ (Qt signály), aby se okno nezaseklo u dlouhé
skladby — hotová data se ohlásí signálem `finished`.

Neukládá surové vzorky (u 5min skladby by to byly desítky MB), ale jen
min/max dvojice v pevných časových „kbelících" (`BUCKETS_PER_SECOND`) —
to úplně stačí na vykreslení a je to řádově méně paměti.
"""

from __future__ import annotations

import array
import math
import threading
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QUrl, Signal

from drum_align import (ADTOF_THRESHOLDS, OnsetAccumulator, adtof_activations, adtof_available,
                        group_envs, pick_onsets)
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

BUCKETS_PER_SECOND = 400      # rozlišení vlnovky (2,5 ms/kbelík — odpovídá
                               # 1 px při max. zoomu editoru, ať je vlnovka
                               # ostrá i při přiblížení, ne rozpixelovaná)

# (typecode pro array.array, bajtů/vzorek, nulový posun, dělitel pro
# normalizaci na float −1..1) — dekodér dodává NATIVNÍ formát zdroje (viz
# WaveformLoader.load, proč se nevyžaduje konkrétní formát), takže se musí
# umět rozparsovat cokoliv rozumného, co QAudioDecoder může vrátit.
_SAMPLE_TYPECODES = {
    QAudioFormat.SampleFormat.UInt8: ("B", 1, 128.0, 128.0),
    QAudioFormat.SampleFormat.Int16: ("h", 2, 0.0, 32768.0),
    QAudioFormat.SampleFormat.Int32: ("i", 4, 0.0, 2147483648.0),
    QAudioFormat.SampleFormat.Float: ("f", 4, 0.0, 1.0),
}
_NP_DTYPES = {"B": np.uint8, "h": np.int16, "i": np.int32, "f": np.float32}
DISPLAY_THRESHOLDS = {"kick": 0.7, "snare": 0.7, "cymbal": 0.5}


class WaveformData:
    """Peaky (min/max) v pevných časových kbelících + délka zvuku."""

    TARGET_PEAK = 0.97   # kolik z poloviny výšky stopy má zabrat nejhlasitější místo

    def __init__(self) -> None:
        self.mins = array.array("f")
        self.maxs = array.array("f")
        self.duration_s: float = 0.0
        self.display_gain: float = 1.0   # auto-zesílení JEN pro zobrazení, ne zvuk
        # obálka úderů po pásmech (kopák/virbl/činely, krok drum_align.HOP_S)
        # pro zarovnání na bicí z GP, a z ní vybrané údery pro zobrazení
        self.onset_env: dict = {}
        self.drum_onsets: dict = {}
        self.onset_source: str = "flux"   # "adtof" = neuronová síť, "flux" = nárůst spektra
        self.drum_act: Optional[np.ndarray] = None   # ADTOF snímky × 5 (drum_align.ADTOF_CLASSES)
        # dekódovaný zvuk (mono int16) — pro „nahrávka vlevo, GP vpravo"
        # (TimelineEditor.set_split_ears); ~23 MB na 4,5 min
        self.pcm: Optional[np.ndarray] = None
        self.sample_rate: int = 44100

    def __len__(self) -> int:
        return len(self.mins)

    def compute_display_gain(self) -> None:
        """Zavolá se jednou po dokončení dekódování (`WaveformLoader._on_finished`).
        Tiché nahrávky (typicky exportované s rezervou/headroomem) by jinak ve
        stopě vypadaly jako plochá čárka uprostřed — automaticky dopočítá
        zesílení tak, ať nejhlasitější místo v celé nahrávce sahá skoro na
        okraj stopy. Netýká se přehrávání, jen kreslení (`WaveformItem`)."""
        if not self.mins:
            self.display_gain = 1.0
            return
        peak = max(abs(min(self.mins)), abs(max(self.maxs)))
        self.display_gain = (self.TARGET_PEAK / peak) if peak > 1e-4 else 1.0

    def peak_range(self, t0: float, t1: float) -> tuple[float, float]:
        """Min/max v časovém intervalu [t0, t1) sekund zvuku, PO auto-zesílení
        pro zobrazení (`display_gain`) a ořezané zpět na ±1 (klip u pár
        extrémních vzorků nad `TARGET_PEAK` je neškodný a lepší než přetékání
        mimo stopu)."""
        if not self.mins:
            return 0.0, 0.0
        i0 = max(0, int(t0 * BUCKETS_PER_SECOND))
        i1 = min(len(self.mins), max(i0 + 1, int(t1 * BUCKETS_PER_SECOND)))
        if i0 >= len(self.mins):
            return 0.0, 0.0
        g = self.display_gain
        mn = max(-1.0, min(self.mins[i0:i1]) * g)
        mx = min(1.0, max(self.maxs[i0:i1]) * g)
        return mn, mx

    def find_onset_near(self, t_center: float, window_s: float = 0.3) -> Optional[float]:
        """Najde v okolí `t_center` (±`window_s`) nejvýraznější NÁSTUP
        (onset) — místo, kde hlasitost v krátkém úseku nejvíc vzroste,
        typicky úder bicích/beat (ne prostě "nejhlasitější místo", to by
        mohlo trefit doznívající tón místo skutečného úderu).

        Použití: ruční zarovnání nahrávky na mřížku taktů — uživatel
        klikne blízko špičky, tohle najde přesný okamžik úderu (viz
        `TimelineEditor.set_align_point`). Vrátí None, když v okně není
        nic výrazného (ticho/plocha)."""
        if not self.mins:
            return None
        i_center = int(t_center * BUCKETS_PER_SECOND)
        half = max(1, int(window_s * BUCKETS_PER_SECOND))
        i0 = max(0, i_center - half)
        i1 = min(len(self.mins), i_center + half)
        if i1 - i0 < 2:
            return None
        lookback = max(1, int(0.015 * BUCKETS_PER_SECOND))   # ~15 ms dozadu
        best_i = None
        best_rise = 0.0
        for i in range(i0, i1):
            env = max(abs(self.mins[i]), abs(self.maxs[i]))
            j = max(0, i - lookback)
            prev_env = max(abs(self.mins[j]), abs(self.maxs[j]))
            rise = env - prev_env
            if rise > best_rise:
                best_rise = rise
                best_i = i
        if best_i is None or best_rise < 0.01:
            return None
        return best_i / BUCKETS_PER_SECOND

    def detect_onsets(self, min_gap_s: float = 0.08, threshold: float = 0.03) -> list[float]:
        """Globální detekce VŠECH výrazných nástupů (úderů) v CELÉ nahrávce
        — stejný princip jako `find_onset_near` (nárůst hlasitosti oproti
        ~15 ms zpátky), ale prohledá celý signál a potlačí duplicity bližší
        než `min_gap_s` (jeden úder = jeden nástup, ne série blízkých
        detekcí kolem jeho náběžné hrany). Použije `estimate_alignment`
        pro automatické zarovnání nahrávky na mřížku taktů."""
        n = len(self.mins)
        if n < 2:
            return []
        lookback = max(1, int(0.015 * BUCKETS_PER_SECOND))
        envs = [max(abs(self.mins[i]), abs(self.maxs[i])) for i in range(n)]
        rises = [envs[i] - envs[max(0, i - lookback)] for i in range(n)]
        min_gap_buckets = max(1, int(min_gap_s * BUCKETS_PER_SECOND))
        onsets: list[float] = []
        i = 0
        while i < n:
            if rises[i] > threshold:
                j1 = min(n, i + min_gap_buckets)
                peak_i = max(range(i, j1), key=lambda k: rises[k])
                onsets.append(peak_i / BUCKETS_PER_SECOND)
                i = peak_i + min_gap_buckets
            else:
                i += 1
        return onsets


def estimate_alignment(onsets: list[float], beat_s: float,
                       stretch_min: float, stretch_max: float,
                       steps: int = 201) -> Optional[dict]:
    """Automaticky najde (`stretch`, `offset`) tak, aby co nejvíc
    detekovaných úderů (`onsets`, časy UVNITŘ SOUBORU — viz `detect_onsets`)
    padlo přesně na mřížku beatů (`beat_s`, ta je dána tempem, které už
    uživatel v projektu má) na časové ose.

    Klasický "comb filter" odhad tempa/fáze beze závislosti na FFT/knihovnách:
    pro každý kandidátní `stretch` v [stretch_min, stretch_max] spočítá
    periodu mřížky V SOUBORU (`beat_s / stretch`) a histogramem najde fázi
    (posun v rámci jedné periody), na kterou padá nejvíc úderů — čím ostřejší
    a vyšší vrchol histogramu, tím lepší shoda. Vrátí `None`, když úderů je
    málo na spolehlivý odhad (< 4)."""
    if len(onsets) < 4:
        return None
    best = None
    n_bins = 48
    for step in range(steps):
        s = stretch_min + (stretch_max - stretch_min) * step / max(1, steps - 1)
        period = beat_s / s
        if period <= 0.01:
            continue
        hist = [0.0] * n_bins
        for t in onsets:
            b = int(((t % period) / period) * n_bins) % n_bins
            hist[b] += 1.0
        # tolerantní skóre — přičti (s poloviční váhou) i sousední binky,
        # ať drobný jitter úderu kolem hranice binu neshodí skóre
        scores = [hist[i] + 0.5 * hist[(i - 1) % n_bins] + 0.5 * hist[(i + 1) % n_bins]
                 for i in range(n_bins)]
        bi = max(range(n_bins), key=lambda i: scores[i])
        if best is None or scores[bi] > best["score"]:
            best = {"stretch": s, "period": period, "bin": bi, "score": scores[bi]}
    if best is None:
        return None

    s = best["stretch"]
    period = best["period"]
    phase_center = (best["bin"] + 0.5) / n_bins * period
    tol = period / n_bins * 1.5

    def phase_dist(t: float) -> float:
        d = abs((t % period) - phase_center)
        return min(d, period - d)

    near = [t for t in onsets if phase_dist(t) <= tol]
    if not near:
        near = onsets

    # ukotvi na PRVNÍM úderu blízko vítězné fáze -> přesný offset (ne jen
    # perioda/fáze, ale konkrétní bod na časové ose)
    anchor = min(near)
    grid_target = round(s * anchor / beat_s) * beat_s
    offset = grid_target - s * anchor
    if offset < 0:
        # NESMÍ se prostě oříznout na 0 (`max(0.0, offset)`) — to by posunulo
        # celou mřížku o zlomek beatu a rozbilo přesné zarovnání, které jsme
        # právě spočítali. Místo toho přičti celé násobky `beat_s` (posun o
        # celý beat/takt zarovnání neruší, jen ho posune "o pár beatů dál").
        offset += math.ceil(-offset / beat_s) * beat_s

    return {
        "stretch": s,
        "offset": offset,
        "matched": len(near),
        "total_onsets": len(onsets),
        "confidence": len(near) / len(onsets),
    }


class WaveformLoader(QObject):
    """Asynchronní dekodér: `load(path)` → signál `finished(WaveformData)`.

    Chyba dekódování (nepodporovaný formát, poškozený soubor) se hlásí
    signálem `failed(str)` — volající pak jen nezobrazí vlnovku, nic se
    nerozbije."""

    finished = Signal(object)     # WaveformData
    failed = Signal(str)
    progress = Signal(float)      # 0..1 (odhad podle dekódované délky)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._decoder: Optional[QAudioDecoder] = None
        self._data: Optional[WaveformData] = None
        self._bucket_idx = 0
        self._samples_done = 0
        self._sample_rate = 44100
        self._carry = np.zeros(0, dtype=np.float32)
        self._onsets: Optional[OnsetAccumulator] = None
        self._pcm_parts: list = []
        self._generation = 0      # zvyšuje cancel(); starý výsledek z vlákna se zahodí

    def load(self, path: str) -> None:
        self.cancel()
        self._data = WaveformData()
        self._bucket_idx = 0
        self._samples_done = 0
        self._carry = np.zeros(0, dtype=np.float32)
        self._onsets = None
        self._pcm_parts = []
        # údery v nahrávce: nárůst spektra po pásmech průběžně při dekódování
        # (levný, záloha) a po dekódování neuronová síť ADTOF ve vlákně
        self._use_adtof = adtof_available()

        dec = QAudioDecoder(self)
        # ZÁMĚRNĚ se NEVOLÁ setAudioFormat() — vyžádané převzorkování/downmix
        # (dřív mono 22050Hz) donutí Windows FFmpeg backend, aby se u MP3
        # (na WAV to nevadilo) NAVŽDY zasekl — ani finished, ani error signál
        # nikdy nepřijde (ověřeno přímo na reálném MP3). Místo toho se
        # přijme NATIVNÍ formát dekodéru a downmix/normalizace se udělá
        # ručně v `_on_buffer` — o něco pomalejší, ale spolehlivé.
        dec.setSource(QUrl.fromLocalFile(path))
        dec.bufferReady.connect(self._on_buffer)
        dec.finished.connect(self._on_finished)
        dec.error.connect(self._on_error)
        self._decoder = dec
        dec.start()

    def cancel(self) -> None:
        # `dec.stop()` může SYNCHRONNĚ vyvolat signál (finished/error), jehož
        # handler zavolá `cancel()` znovu (reentrantně) — proto se nejdřív
        # odpojí od self._decoder (na lokální proměnnou) a signály se odpojí,
        # než se vůbec sáhne na stop()/deleteLater(). Jinak by vnořené volání
        # vynulovalo self._decoder dřív, než to venkovní volání stihlo
        # doběhnout, a `self._decoder.deleteLater()` by spadlo na None.
        self._generation += 1      # běžící ADTOF vlákno výsledek neohlásí
        dec = self._decoder
        self._decoder = None
        if dec is None:
            return
        try:
            dec.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            dec.stop()
        except RuntimeError:
            pass
        dec.deleteLater()

    # --- interní ---

    def _on_buffer(self) -> None:
        dec = self._decoder
        if dec is None or self._data is None:
            return
        buf = dec.read()
        if not buf.isValid():
            return
        fmt = buf.format()
        rate = fmt.sampleRate() or 44100
        channels = max(1, fmt.channelCount())
        self._sample_rate = rate
        samples_per_bucket = max(1, int(rate / BUCKETS_PER_SECOND))

        spec = _SAMPLE_TYPECODES.get(fmt.sampleFormat())
        if spec is None:
            return   # neznámý/nepodporovaný formát vzorků — přeskoč buffer
        typecode, bytesize, zero_off, scale = spec

        raw = bytes(buf.data())
        frame_bytes = bytesize * channels
        usable = (len(raw) // frame_bytes) * frame_bytes
        if usable <= 0:
            return
        # numpy: normalizace na float −1..1 a downmix na mono (průměr kanálů).
        # Dřív to byly Pythonové smyčky po jednotlivých vzorcích — 4min
        # skladba = ~11 mil. vzorků, načítání trvalo zbytečně dlouho.
        arr = np.frombuffer(raw[:usable], dtype=_NP_DTYPES[typecode]).astype(np.float32)
        mono = ((arr - zero_off) / scale).reshape(-1, channels).mean(axis=1)

        # min/max kbelíky po `samples_per_bucket` vzorcích; nedoplněný konec
        # se přenáší do dalšího bufferu. Každý kbelík obsahuje i nulu (min ≤ 0
        # ≤ max) — stejně jako dřívější výpočet.
        data = np.concatenate([self._carry, mono])
        full = (len(data) // samples_per_bucket) * samples_per_bucket
        if full:
            blocks = data[:full].reshape(-1, samples_per_bucket)
            self._data.mins.extend(np.minimum(blocks.min(axis=1), 0.0).tolist())
            self._data.maxs.extend(np.maximum(blocks.max(axis=1), 0.0).tolist())
        self._carry = data[full:]

        # obálka úderů po pásmech pro zarovnání na bicí z GP (drum_align)
        if self._onsets is None:
            self._onsets = OnsetAccumulator(rate)
        self._onsets.feed(mono)
        self._pcm_parts.append((np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16))

        self._samples_done += len(mono)
        self._data.duration_s = self._samples_done / float(rate)

        total_us = dec.duration()
        if total_us > 0:
            self.progress.emit(min(1.0, self._data.duration_s / (total_us / 1000.0)))

    def _on_finished(self) -> None:
        if self._data is None:
            return
        if len(self._carry):     # doklepni poslední nedoplněný kbelík
            self._data.mins.append(min(0.0, float(self._carry.min())))
            self._data.maxs.append(max(0.0, float(self._carry.max())))
            self._carry = np.zeros(0, dtype=np.float32)
        if self._onsets is not None:
            self._data.onset_env = self._onsets.finish()
            # prahy pro ZOBRAZENÍ (čárky na vlnovce): nejlepší shoda s GP
            # naměřená na Jasné zprávě (kopák/virbl 0,7, činely 0,5) — nižší
            # práh u kopáku chytal hlavně basovou kytaru
            self._data.drum_onsets = {b: pick_onsets(e, threshold=DISPLAY_THRESHOLDS.get(b, 0.7))
                                      for b, e in self._data.onset_env.items()}
            self._onsets = None
        if self._pcm_parts:
            self._data.pcm = np.concatenate(self._pcm_parts)
            self._data.sample_rate = self._sample_rate
            self._pcm_parts = []
        data, self._data = self._data, None
        data.compute_display_gain()
        self.cancel()
        if not self._use_adtof or data.pcm is None:
            self.finished.emit(data)
            return
        # ADTOF ~3 s na 4min píseň — ve vlákně, ať okno nezamrzne; signál
        # z vlákna Qt doručí do hlavního vlákna (queued)
        gen = self._generation

        def work():
            try:
                act = adtof_activations(data.pcm.astype(np.float32) / 32767.0, data.sample_rate)
                envs = group_envs(act)
                onsets = {b: pick_onsets(e, threshold=ADTOF_THRESHOLDS[b]) for b, e in envs.items()}
                # síť zná jen skutečnou hudbu — na umělém signálu (stálý
                # pilovitý akord + šum) nenajde nic; pak zůstane nárůst spektra
                if sum(len(v) for v in onsets.values()) >= max(8, 0.2 * data.duration_s):
                    data.onset_env, data.drum_onsets, data.onset_source = envs, onsets, "adtof"
                data.drum_act = act       # 5 tříd pro přepis bicích (i když zarovnání zůstane na nárůstu)
            except Exception:       # model nejde spustit -> zůstane nárůst spektra
                pass
            if gen == self._generation:
                try:
                    self.finished.emit(data)
                except RuntimeError:    # loader mezitím smazán (zavřená karta)
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _on_error(self, *args) -> None:
        msg = "nepodařilo se načíst tvar vlny"
        dec = self._decoder
        if dec is not None:
            try:
                msg = dec.errorString() or msg
            except RuntimeError:
                pass
        self._data = None
        self.cancel()
        self.failed.emit(msg)
