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
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

BUCKETS_PER_SECOND = 200      # rozlišení vlnovky (5 ms na kbelík)


class WaveformData:
    """Peaky (min/max) v pevných časových kbelících + délka zvuku."""

    def __init__(self) -> None:
        self.mins = array.array("f")
        self.maxs = array.array("f")
        self.duration_s: float = 0.0

    def __len__(self) -> int:
        return len(self.mins)

    def peak_range(self, t0: float, t1: float) -> tuple[float, float]:
        """Min/max v časovém intervalu [t0, t1) sekund zvuku."""
        if not self.mins:
            return 0.0, 0.0
        i0 = max(0, int(t0 * BUCKETS_PER_SECOND))
        i1 = min(len(self.mins), max(i0 + 1, int(t1 * BUCKETS_PER_SECOND)))
        if i0 >= len(self.mins):
            return 0.0, 0.0
        return min(self.mins[i0:i1]), max(self.maxs[i0:i1])


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
        # mono float je pro peaky nejjednodušší — hlasitost obou kanálů
        # nás nezajímá zvlášť, jde jen o obálku signálu
        fmt = QAudioFormat()
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        fmt.setChannelCount(1)
        fmt.setSampleRate(22050)      # na vlnovku bohatě stačí
        dec.setAudioFormat(fmt)
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
        rate = fmt.sampleRate() or 22050
        self._sample_rate = rate
        samples_per_bucket = max(1, int(rate / BUCKETS_PER_SECOND))

        data = buf.data()
        try:
            arr = array.array("f")
            arr.frombytes(bytes(data)[: (len(data) // 4) * 4])
        except (ValueError, TypeError):
            return

        for v in arr:
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
        self._samples_done += len(arr)
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
