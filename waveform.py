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
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal
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


class WaveformData:
    """Peaky (min/max) v pevných časových kbelících + délka zvuku."""

    TARGET_PEAK = 0.97   # kolik z poloviny výšky stopy má zabrat nejhlasitější místo

    def __init__(self) -> None:
        self.mins = array.array("f")
        self.maxs = array.array("f")
        self.duration_s: float = 0.0
        self.display_gain: float = 1.0   # auto-zesílení JEN pro zobrazení, ne zvuk

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
        self._acc_min = 0.0
        self._acc_max = 0.0
        self._acc_n = 0
        self._bucket_idx = 0
        self._samples_done = 0
        self._sample_rate = 44100

    def load(self, path: str) -> None:
        self.cancel()
        self._data = WaveformData()
        self._acc_min = 0.0
        self._acc_max = 0.0
        self._acc_n = 0
        self._bucket_idx = 0
        self._samples_done = 0

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
        try:
            arr = array.array(typecode)
            arr.frombytes(raw[:usable])
        except (ValueError, TypeError):
            return

        # normalizuj na float −1..1, pak downmixuj na mono (průměr kanálů —
        # hlasitost jednotlivých kanálů zvlášť nás nezajímá, jde jen o
        # obálku signálu). `array` podporuje krokované řezy (arr[c::channels])
        # rychle na úrovni C, takže se vyhneme ruční indexaci po vzorcích.
        norm = array.array("f", ((v - zero_off) / scale for v in arr))
        if channels == 1:
            mono = norm
        else:
            chans = [norm[c::channels] for c in range(channels)]
            mono = array.array("f", (sum(vals) / channels for vals in zip(*chans)))

        for v in mono:
            if v < self._acc_min:
                self._acc_min = v
            if v > self._acc_max:
                self._acc_max = v
            self._acc_n += 1
            if self._acc_n >= samples_per_bucket:
                self._data.mins.append(self._acc_min)
                self._data.maxs.append(self._acc_max)
                self._acc_min = 0.0
                self._acc_max = 0.0
                self._acc_n = 0
        self._samples_done += len(mono)
        self._data.duration_s = self._samples_done / float(rate)

        total_us = dec.duration()
        if total_us > 0:
            self.progress.emit(min(1.0, self._data.duration_s / (total_us / 1000.0)))

    def _on_finished(self) -> None:
        if self._data is None:
            return
        if self._acc_n:          # doklepni poslední nedoplněný kbelík
            self._data.mins.append(self._acc_min)
            self._data.maxs.append(self._acc_max)
        data, self._data = self._data, None
        data.compute_display_gain()
        self.cancel()
        self.finished.emit(data)

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
