"""
recording_wizard.py — průvodce „Karaoke z nahrávky" (bez GP).

Postup (rozhodnutí uživatele 2026-11-01: vycházet z nahrávky, GP jen jako
volitelné vylepšení):
  1. Nahrávka (MP3/WAV) → nová karta, vlnovka + údery bicích (ADTOF).
  2. Doby a takty z nahrávky (`beat_grid`, Beat This) → mřížka osy
     (`TimelineEditor.set_recording_grid`), tempo po taktech.
  3. Bicí z nahrávky na tu mřížku (stávající dialog přepisu, volitelně Demucs).
  4. Text a akordy (stávající dialog webového importu) → na takty nahrávky
     (`TimelineEditor.place_text_on_grid`).
  5. Doladit tažením při poslechu, uložit (Ctrl+S).
Průvodce je NEMODÁLNÍ — mezi kroky jde přehrávat a posouvat; každý krok jde
vrátit Ctrl+Z.
"""
from __future__ import annotations

import os
import threading
from collections import Counter

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWizard, QWizardPage)

import beat_grid as BG
import drum_align as DA
from i18n import tr


def _label(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    return lbl


class _AudioPage(QWizardPage):
    def __init__(self, wiz: "RecordingWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle(tr("wizard.audio.title"))
        lay = QVBoxLayout(self)
        lay.addWidget(_label(tr("wizard.audio.info")))
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.textChanged.connect(self.completeChanged)
        btn = QPushButton(tr("wizard.audio.browse"))
        btn.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(btn)
        lay.addLayout(row)
        missing = [n for n, ok in (("onnxruntime + ADTOF", DA.adtof_available()),
                                   ("beat-this", BG.available())) if not ok]
        if missing:
            warn = _label(tr("wizard.missing", what=", ".join(missing)))
            warn.setStyleSheet("color:#c64600;")
            lay.addWidget(warn)
        lay.addStretch(1)

    def _browse(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("wizard.audio.browse"), "",
                                           "Audio (*.mp3 *.wav *.flac *.ogg *.m4a)")
        if p:
            self.path.setText(p)

    def isComplete(self) -> bool:
        return os.path.isfile(self.path.text().strip()) and DA.adtof_available() and BG.available()

    def validatePage(self) -> bool:
        w = self.wiz
        if w.skipping:
            return True
        path = self.path.text().strip()
        if w.ed is not None and getattr(w.ed, "audio_path", None) == path:
            return True                      # Zpět a znovu Další — stejná nahrávka, žádná nová karta
        ed = w.main._tab_for_new_document()
        w.main.doc_tabs.setCurrentWidget(ed)
        w.ed = ed
        ed.load_audio(path)
        return True


class _GridPage(QWizardPage):
    """Doby a takty z nahrávky. Výchozí pevné metrum podle převládajícího
    počtu dob (změřeno: lepší než proměnné takty — virbl na 2 a 4 Iron Maiden
    0,91 → 0,93, Let It Be 0,94 → 0,95)."""

    def __init__(self, wiz: "RecordingWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle(tr("wizard.grid.title"))
        lay = QVBoxLayout(self)
        self.status = _label()
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("wizard.grid.meter")))
        self.meter = QComboBox()
        for m in (4, 3, 2, 6):
            self.meter.addItem(tr("wizard.grid.fixed", n=m), m)
        self.meter.addItem(tr("wizard.grid.variable"), 0)
        row.addWidget(self.meter, 1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("wizard.grid.shift")))
        self.shift = QSpinBox()
        self.shift.setRange(-3, 3)
        row2.addWidget(self.shift)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.result = _label()
        lay.addWidget(self.result)
        lay.addStretch(1)
        self.meter.currentIndexChanged.connect(self._recompute)
        self.shift.valueChanged.connect(self._recompute)
        self.beats = self.downs = None
        self.starts, self.nums = np.zeros(0), []
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._check)

    def initializePage(self) -> None:
        self.beats = None
        self.starts, self.nums = np.zeros(0), []
        self.result.setText("")
        self._res: dict = {}
        self._thread = None
        ed = self.wiz.ed
        if ed is None or not getattr(ed, "audio_path", None):
            self.status.setText(tr("wizard.grid.no_audio"))
            return
        self.status.setText(tr("wizard.grid.busy"))
        self._poll.start(200)

    def _check(self) -> None:
        ed = self.wiz.ed
        wf = getattr(ed, "waveform", None) if ed is not None else None
        if self._thread is None:
            if wf is None or getattr(wf, "pcm", None) is None or getattr(wf, "drum_act", None) is None:
                return                       # vlnovka + ADTOF ještě běží
            pcm, rate = wf.pcm, wf.sample_rate

            def work():
                try:
                    self._res["bt"] = BG.track(pcm.astype(np.float32) / 32767.0, rate)
                except Exception as e:
                    self._res["err"] = str(e)

            self._thread = threading.Thread(target=work, daemon=True)
            self._thread.start()
            return
        if self._thread.is_alive():
            return
        self._poll.stop()
        if "err" in self._res:
            self.status.setText(tr("wizard.grid.error", err=self._res["err"]))
            return
        self.beats, self.downs = self._res["bt"]
        _st, nums = BG.bars(self.beats, self.downs, None)
        usual = Counter(nums).most_common(1)[0][0] if nums else 4
        i = self.meter.findData(usual)
        self.meter.blockSignals(True)
        self.meter.setCurrentIndex(i if i >= 0 else 0)
        self.meter.blockSignals(False)
        self.status.setText(tr("wizard.grid.found", beats=len(self.beats), bars=len(nums)))
        self._recompute()

    def _recompute(self) -> None:
        if self.beats is None:
            return
        m = int(self.meter.currentData() or 0)
        beats = self.beats
        if m:
            starts, nums = BG.bars(beats, self.downs, m)
            k = self.shift.value()
            if k:                            # ruční oprava fáze: začátek taktu o k dob vedle
                b0 = int(np.argmin(np.abs(beats - starts[0]))) + k
                b0 %= m
                idx = list(range(b0, beats.size, m))
                ibi = float(np.median(np.diff(beats)))
                starts = np.append(beats[idx], beats[idx[-1]] + m * ibi)
                nums = [m] * len(idx)
        else:
            starts, nums = BG.bars(beats, self.downs, None)
        self.starts, self.nums = starts, nums
        bpm = np.array(nums) * 60.0 / np.maximum(1e-3, np.diff(starts))
        wf = self.wiz.ed.waveform
        sn = DA.peak_pick(wf.drum_act[:, 1], DA.ADTOF_CLASS_THRESHOLDS[1]) * DA.HOP_S
        back = BG.snare_on_backbeat(sn, beats, starts)
        txt = tr("wizard.grid.summary", bars=len(nums), lo=f"{np.percentile(bpm, 5):.1f}",
                 hi=f"{np.percentile(bpm, 95):.1f}", med=f"{np.median(bpm):.1f}",
                 start=f"{starts[0]:.2f}", back=f"{back * 100:.0f}")
        if sn.size >= 16 and back < 0.5:
            txt += "\n" + tr("wizard.grid.phase_warn")
        self.result.setText(txt)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return len(self.nums) > 0

    def validatePage(self) -> bool:
        self._poll.stop()
        if self.wiz.skipping or not self.nums:
            return True
        self.wiz.ed.set_recording_grid(self.starts, self.nums)
        return True


class _DrumsPage(QWizardPage):
    def __init__(self, wiz: "RecordingWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle(tr("wizard.drums.title"))
        lay = QVBoxLayout(self)
        lay.addWidget(_label(tr("wizard.drums.info")))
        btn = QPushButton(tr("timeline.transcribe_drums"))
        btn.clicked.connect(self._run)
        lay.addWidget(btn)
        gp_btn = QPushButton(tr("wizard.drums.gp_button"))
        gp_btn.setToolTip(tr("wizard.drums.gp_tooltip"))
        gp_btn.clicked.connect(self._from_gp)
        lay.addWidget(gp_btn)
        self.result = _label()
        lay.addWidget(self.result)
        lay.addStretch(1)

    def _run(self) -> None:
        ed = self.wiz.editor()
        ed.transcribe_drums_from_audio()
        n = sum(1 for e in ed.data.get("drums_timeline", []) if e.get("source") == "generated")
        self.result.setText(tr("wizard.drums.done", n=n) if n else "")

    def _from_gp(self) -> None:
        """Bicí ze souboru: Guitar Pro (gp3/4/5, gpx, gp) nebo MIDI."""
        from gpif_reader import load_any
        from PySide6.QtWidgets import QMessageBox
        p, _ = QFileDialog.getOpenFileName(self, tr("wizard.drums.gp_button"), "",
                                           "Guitar Pro / MIDI (*.gp3 *.gp4 *.gp5 *.gpx *.gp *.mid *.midi)")
        if not p:
            return
        try:
            if p.lower().endswith((".mid", ".midi")):
                self.show_stats(self.wiz.editor().place_midi_drums_on_grid(p))
            else:
                self.place_gp(load_any(p))
        except Exception as e:
            QMessageBox.warning(self, tr("wizard.drums.title"), str(e))

    def place_gp(self, song) -> dict:
        return self.show_stats(self.wiz.editor().place_gp_drums_on_grid(song))

    def show_stats(self, st: dict) -> dict:
        if not st.get("n"):
            self.result.setText(tr("wizard.drums.gp_none"))
            return st
        txt = tr("wizard.drums.gp_done", n=st["n"], bar=st["gp_bar1"], match=f"{st['match'] * 100:.0f}",
                 gp_bars=st["gp_bars"], rec_bars=st["rec_bars"])
        if st["match"] < 0.6:
            txt += "\n" + tr("wizard.drums.gp_low")
        self.result.setText(txt)
        return st


class _TextPage(QWizardPage):
    def __init__(self, wiz: "RecordingWizard"):
        super().__init__()
        self.wiz = wiz
        self.setTitle(tr("wizard.text.title"))
        lay = QVBoxLayout(self)
        lay.addWidget(_label(tr("wizard.text.info")))
        btn = QPushButton(tr("wizard.text.button"))
        btn.clicked.connect(self._run)
        lay.addWidget(btn)
        self.result = _label()
        lay.addWidget(self.result)
        lay.addStretch(1)

    def _run(self) -> None:
        from format_migrate import migrate, to_editor_model
        from PySide6.QtWidgets import QDialog
        from web_import import WebImportDialog
        dlg = WebImportDialog(self.wiz.main)
        if dlg.exec() != QDialog.Accepted or not dlg.result_json:
            return
        flat = to_editor_model(migrate(dlg.result_json)[0])
        first_bar = 1 + int((flat.get("meta") or {}).get("count_in_bars") or 0)
        self.wiz.editor().place_text_on_grid(flat, first_bar)
        self.result.setText(tr("wizard.text.done", n=len(flat.get("karaoke_lines", []) or []),
                               bar=first_bar))


class _DonePage(QWizardPage):
    def __init__(self, wiz: "RecordingWizard"):
        super().__init__()
        self.setTitle(tr("wizard.done.title"))
        lay = QVBoxLayout(self)
        lay.addWidget(_label(tr("wizard.done.info")))
        lay.addStretch(1)


class RecordingWizard(QWizard):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.ed = None
        self.setWindowTitle(tr("wizard.title"))
        self.setModal(False)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        # „Přeskočit krok" — jde přeskočit cokoliv (i nahrávku); krok se pak neprovede
        self.skipping = False
        self.setOption(QWizard.HaveCustomButton1, True)
        self.setButtonText(QWizard.CustomButton1, tr("wizard.skip"))
        self.customButtonClicked.connect(self._skip)
        self.currentIdChanged.connect(
            lambda _i: self.button(QWizard.CustomButton1).setVisible(self.currentPage() is not self.pages_[-1]))
        self.audio_page = _AudioPage(self)
        self.grid_page = _GridPage(self)
        self.drums_page = _DrumsPage(self)
        self.text_page = _TextPage(self)
        self.pages_ = [self.audio_page, self.grid_page, self.drums_page, self.text_page, _DonePage(self)]
        for p in self.pages_:
            self.addPage(p)
        self.setButtonLayout([QWizard.Stretch, QWizard.BackButton, QWizard.CustomButton1,
                              QWizard.NextButton, QWizard.FinishButton, QWizard.CancelButton])
        self.resize(620, 420)

    def _skip(self, _which=None) -> None:
        self.skipping = True
        try:
            self.next()
        finally:
            self.skipping = False

    def editor(self):
        """Karta, do které průvodce zapisuje — bez nahrávky (krok 1 přeskočen)
        se založí nová."""
        if self.ed is None:
            self.ed = self.main._tab_for_new_document()
            self.main.doc_tabs.setCurrentWidget(self.ed)
        return self.ed
