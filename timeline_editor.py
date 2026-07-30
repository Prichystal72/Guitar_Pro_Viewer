"""
timeline_editor.py — Vizuální editor časové osy (DAW-styl) pro karaoke.

Zobrazí stopy jako vodorovné pruhy, v nich bloky TEXTU (řádky) a AKORDŮ
umístěné na časové ose podle pravidelné mřížky taktů/beatů (dle tempa —
žádné odhady z délky textu). Bloky lze:
  • posouvat po ose (změna času),
  • měnit jim délku tažením pravého okraje,
  • editovat obsah dvojklikem (akord / text),
  • mazat (Delete) a přidávat (tlačítka).

Pracuje nad karaoke JSON slovníkem (viz JSON_FORMAT.md) — vstup i výstup je
stejné schéma, takže úpravy jdou rovnou exportovat do JSON.
"""

from __future__ import annotations

import copy
import math
import os
import struct
import tempfile
import wave
from typing import Any, Optional

from PySide6.QtCore import Qt, QRectF, QPointF, QUrl, QTimer
from PySide6.QtGui import QColor, QBrush, QPen, QFont, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtMultimedia import (
    QMediaPlayer, QAudioOutput, QMediaDevices, QSoundEffect,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsItem,
    QGraphicsSimpleTextItem, QGraphicsPixmapItem, QInputDialog, QCheckBox,
    QSplitter, QFormLayout, QLineEdit, QDoubleSpinBox, QSpinBox,
    QDialog, QDialogButtonBox, QMenu, QFileDialog,
)

from jog_shuttle import JogShuttleWidget

# --- ikonky bicích (assets/drum_icons/*), klíč = _drum_icon_key() ---
DRUM_ICON_DIR = os.path.join(os.path.dirname(__file__), "assets", "drum_icons")
DRUM_ICON_FILES = {
    "kick": "kick.png",
    "snare": "snare.png",
    "tom_high": "tom_high.png",
    "tom_mid": "tom_mid.png",
    "tom_low": "tom_low.png",
    "hihat_closed": "hihat_closed.png",
    "hihat_open": "hihat_open.png",
    "cymbal": "cymbal.png",
    "perc": "perc.svg",   # bez vlastní grafiky (bonga/conga/timbale/…) — SVG placeholder
}

# GM Percussion Key Map — kopie guitar_pro_viewer.GM_PERCUSSION (nejde importovat
# odsud, guitar_pro_viewer už importuje TimelineEditor → kruhový import).
# Použito v panelu vlastností a kontextovém menu úderu (výběr/změna bubnu).
DRUM_MIDI_NAMES = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1",
    37: "Side Stick", 38: "Acoustic Snare", 39: "Hand Clap", 40: "Electric Snare",
    41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom",
    44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat", 47: "Low-Mid Tom",
    48: "Hi-Mid Tom", 49: "Crash Cymbal 1", 50: "High Tom", 51: "Ride Cymbal 1",
    52: "Chinese Cymbal", 53: "Ride Bell", 54: "Tambourine", 55: "Splash Cymbal",
    56: "Cowbell", 57: "Crash Cymbal 2", 58: "Vibraslap", 59: "Ride Cymbal 2",
    60: "Hi Bongo", 61: "Low Bongo", 62: "Mute Hi Conga", 63: "Open Hi Conga",
    64: "Low Conga", 65: "High Timbale", 66: "Low Timbale", 67: "High Agogo",
    68: "Low Agogo", 69: "Cabasa", 70: "Maracas", 71: "Short Whistle",
    72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro", 75: "Claves",
    76: "Hi Wood Block", 77: "Low Wood Block", 78: "Mute Cuica", 79: "Open Cuica",
    80: "Mute Triangle", 81: "Open Triangle",
}
DRUM_NAME_TO_MIDI = {v: k for k, v in DRUM_MIDI_NAMES.items()}
DRUM_HIT_NAMES = list(DRUM_MIDI_NAMES.values())

# --- rozměry rozvržení (px) ---
HEADER_W = 150      # levý sloupec s názvy stop
RULER_H = 30        # horní pravítko s časem
DISPLAY_H = 44      # výška klipů master "Displej" stopy
DISPLAY_ROW_H = DISPLAY_H + 14   # celá master stopa i s hlavičkou/mezerou
LINE_H = 24         # výška pruhu karaoke ŘÁDKŮ
CHORD_H = 26        # výška pruhu akordů v rámci stopy
LYRIC_H = 30        # výška pruhu textu (slov) v rámci stopy
TRACK_GAP = 12
PER_TRACK = LINE_H + CHORD_H + LYRIC_H + TRACK_GAP
BLOCK_MIN_W = 14
EDGE = 6            # zóna u okraje pro resize
HANDLE_W = 8        # šířka tažného oddělovače řádku
DRUM_ICON_SIZE = 32     # px — fixní velikost ikonky bubnu v pruhu bicích
DRUM_ROW_H = 40.0       # px — výška JEDNOHO řádku bicí stopy (ikona + okraj)
UNDO_LIMIT = 50         # max. počet kroků historie (deepcopy self.data na krok)

LINE_COLOR = QColor("#9141ac")   # fialová = karaoke řádky
BREAK_COLOR = QColor("#e5a50a")  # oranžová = tažná hranice řádku

CHORD_COLOR = QColor("#1a5fb4")
LYRIC_COLOR = QColor("#2d7d2d")
SEL_COLOR = QColor("#e5a50a")

# --- režimy zobrazení master "Displej" klipu (co karaoke displej vykreslí) ---
MODE_LABELS = {
    "lyrics_chords": "Text + akordy",
    "lyrics": "Text",
    "chords": "Akordy",
    "tab": "Tabulatura",
    "tab_chords": "Tab + akordy",
    "count_in": "Odpočet (metronom)",
}
MODE_ORDER = ["lyrics_chords", "lyrics", "chords", "tab", "tab_chords", "count_in"]
MODE_COLORS = {
    "lyrics_chords": QColor("#9141ac"),
    "lyrics": QColor("#2d7d2d"),
    "chords": QColor("#1a5fb4"),
    "tab": QColor("#c64600"),
    "tab_chords": QColor("#a51d2d"),
    "count_in": QColor("#e08a00"),
}

PLAYHEAD_COLOR = QColor("#e01b24")   # červená = kurzor / playhead

import re as _re
_SEC_NUM_RE = _re.compile(r'^\s*(\d+)\.')
_SEC_REF_RE = _re.compile(r'^\s*(R[:.]|Ref|Refr|Chorus)', _re.IGNORECASE)


def _section_of(text: str):
    """Označení sloky/refrénu z markeru na začátku řádku (nebo None)."""
    m = _SEC_NUM_RE.match(text or "")
    if m:
        return f"Sloka {m.group(1)}"
    return "Refrén" if _SEC_REF_RE.match(text or "") else None


_beep_wav_path: Optional[str] = None


def _beep_wav() -> str:
    """Krátký (0,4s, 880Hz) testovací tón jako dočasný WAV soubor — pro
    tlačítko „🔊 Test tón" (ověření, že SLYŠÍTELNĚ hraje na aktuálně
    vybrané zvukové zařízení, nezávisle na tom, jestli se povede načíst
    a přehrát konkrétní písnička)."""
    global _beep_wav_path
    if _beep_wav_path and os.path.isfile(_beep_wav_path):
        return _beep_wav_path
    fd, path = tempfile.mkstemp(suffix="_beep.wav", prefix="timeline_editor_")
    os.close(fd)
    rate = 44100
    freq = 880.0
    dur = 0.4
    n = int(rate * dur)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = [int(32767 * 0.35 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
        wf.writeframes(struct.pack(f"<{n}h", *frames))
    _beep_wav_path = path
    return path


class PlayheadItem(QGraphicsItem):
    """Svislý kurzor (playhead) přes celou osu. Táhni myší nebo klikni do pravítka."""

    def __init__(self, editor: "TimelineEditor", total_h: float):
        super().__init__()
        self.editor = editor
        self.total_h = total_h
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(60)
        self.setCursor(Qt.SizeHorCursor)
        self.setToolTip("Kurzor — táhni, nebo klikni do pravítka nahoře")
        self._syncing = False   # True = posun "shora" (z dat), ne tažení myší
        self._sync()

    def _sync(self) -> None:
        """Nastaví pozici PODLE dat (přehrávání, shuttle, …). Musí se odlišit
        od tažení myší — jinak by `itemChange` při každém takovém posunu
        přeskočilo i v přehrávači (`_seek_audio`), a to 25×/s během
        přehrávání = neustálé vyprazdňování dekodéru = CUKÁNÍ ZVUKU."""
        self._syncing = True
        try:
            self.setPos(HEADER_W + self.editor.playhead_s * self.editor.pps, 0)
        finally:
            self._syncing = False

    def boundingRect(self) -> QRectF:
        return QRectF(-7, 0, 14, self.total_h)

    def paint(self, p: QPainter, opt, widget=None):
        from PySide6.QtGui import QPolygonF
        p.setPen(QPen(PLAYHEAD_COLOR, 1.5))
        p.drawLine(QPointF(0, RULER_H), QPointF(0, self.total_h))
        p.setPen(QPen(PLAYHEAD_COLOR))
        p.setBrush(QBrush(PLAYHEAD_COLOR))
        p.drawPolygon(QPolygonF([QPointF(-6, 0), QPointF(6, 0), QPointF(0, 11)]))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            x = max(HEADER_W, value.x())
            t = round((x - HEADER_W) / self.editor.pps, 3)
            if self._syncing:
                # posun "shora" (přehrávání/shuttle) — pozici v přehrávači
                # NEMĚNIT, ta je právě zdrojem téhle hodnoty
                return QPointF(x, 0)
            self.editor.playhead_s = t
            self.editor._update_playhead_label()
            self.editor._seek_audio(t)   # RUČNÍ tažení kurzoru = přeskoč i audio
            return QPointF(HEADER_W + t * self.editor.pps, 0)
        return super().itemChange(change, value)


class DisplayClipItem(QGraphicsRectItem):
    """Klip master 'Displej' stopy (Vegas program).

    Definuje: v čase [start_s, end_s) ukaž obsah zdrojové stopy (source_track)
    v režimu (mode). Lze posouvat po ose i táhnout OBA okraje (změna délky).
    """

    def __init__(self, editor: "TimelineEditor", clip: dict, lane_y: float, height: float):
        super().__init__()
        self.editor = editor
        self.clip = clip
        self.lane_y = lane_y
        self.h = height
        self._resizing = ""          # "" | "left" | "right"
        self._undo_pushed = False    # jeden undo krok za celé tažení, ne za pixel
        self.setAcceptHoverEvents(True)
        self.setZValue(12)
        self.setToolTip(
            "Táhni okraj = posuň ZAČÁTEK/KONEC řádku na displeji "
            "(nezávisle na hudební mřížce taktů/beatů). "
            "Táhni střed = posuň celý klip po ose."
        )
        self._sync_from_clip()        # setPos PŘED ItemSendsGeometryChanges —
        self.setFlags(                # jinak by vlastní inicializace vypadala
            QGraphicsItem.ItemIsMovable          # jako tažení a zbytečně
            | QGraphicsItem.ItemIsSelectable     # spustila undo/snap při
            | QGraphicsItem.ItemSendsGeometryChanges   # KAŽDÉM _relayout()
        )

    def _sync_from_clip(self) -> None:
        pps = self.editor.pps
        t = float(self.clip.get("start_s", 0.0))
        end = float(self.clip.get("end_s", t + 1.0))
        w = max(BLOCK_MIN_W, (end - t) * pps)
        self.setRect(0, 0, w, self.h)
        self.setPos(HEADER_W + t * pps, self.lane_y)

    def _mode(self) -> str:
        m = self.clip.get("mode", "lyrics_chords")
        return m if m in MODE_LABELS else "lyrics_chords"

    def paint(self, p: QPainter, opt, widget=None):
        base = MODE_COLORS.get(self._mode(), LINE_COLOR)
        if self.isSelected():
            base = SEL_COLOR
        r = self.rect()
        p.setBrush(QBrush(base.lighter(160)))
        p.setPen(QPen(base, 2))
        p.drawRoundedRect(r, 5, 5)
        # svislé úchyty na okrajích
        p.setPen(QPen(base.darker(130), 1))
        p.drawLine(r.left() + 3, r.top() + 4, r.left() + 3, r.bottom() - 4)
        p.drawLine(r.right() - 3, r.top() + 4, r.right() - 3, r.bottom() - 4)
        # popisky
        names = self.editor._track_names()
        ti = self.clip.get("source_track", 1)
        label = self.clip.get("label", "") or ""
        if self._mode() == "count_in":
            line1 = "🎵 " + (self.clip.get("artist", "") or "?")
            line2 = (self.clip.get("title", "") or "") + "  ·  odpočet 4-3-2-1…"
        else:
            line1 = label if label else names.get(ti, f"Stopa {ti}")
            line2 = f"{MODE_LABELS[self._mode()]} · {names.get(ti, f'Stopa {ti}')}"
        p.setPen(QPen(QColor("#1a1a1a")))
        f = QFont("Segoe UI", 9, QFont.Bold)
        p.setFont(f)
        p.drawText(r.adjusted(8, 2, -4, -int(self.h / 2)),
                   Qt.AlignVCenter | Qt.AlignLeft, line1)
        f2 = QFont("Segoe UI", 8)
        p.setFont(f2)
        p.setPen(QPen(base.darker(140)))
        p.drawText(r.adjusted(8, int(self.h / 2) - 2, -4, -2),
                   Qt.AlignVCenter | Qt.AlignLeft, line2)

    # --- interakce ---
    def hoverMoveEvent(self, ev):
        x = ev.pos().x()
        near = x <= EDGE or x >= self.rect().width() - EDGE
        self.setCursor(Qt.SizeHorCursor if near else Qt.OpenHandCursor)
        super().hoverMoveEvent(ev)

    def mousePressEvent(self, ev):
        self._undo_pushed = False   # nové tažení = nová undo hranice
        if ev.button() == Qt.LeftButton:
            x = ev.pos().x()
            if x <= EDGE:
                self._resizing = "left"
                ev.accept()
                return
            if x >= self.rect().width() - EDGE:
                self._resizing = "right"
                ev.accept()
                return
        super().mousePressEvent(ev)

    def _mark_dirty(self) -> None:
        if not self._undo_pushed:
            self.editor._push_undo()
            self._undo_pushed = True

    def mouseMoveEvent(self, ev):
        pps = self.editor.pps
        if self._resizing == "right":
            self._mark_dirty()
            start = float(self.clip.get("start_s", 0.0))
            raw_w = max(BLOCK_MIN_W, ev.pos().x())
            end = self.editor.snap_time(start + raw_w / pps)   # přichyť KONEC na mřížku
            end = max(end, start + BLOCK_MIN_W / pps)
            w = (end - start) * pps
            self.prepareGeometryChange()
            self.setRect(0, 0, w, self.h)
            self.clip["end_s"] = round(end, 3)
            ev.accept()
            return
        if self._resizing == "left":
            self._mark_dirty()
            end_t = float(self.clip.get("end_s", 0.0))
            old_left = self.pos().x()
            right = old_left + self.rect().width()   # pevný pravý okraj (invariant napříč tažením)
            raw_left = old_left + ev.pos().x()
            t = self.editor.snap_time((raw_left - HEADER_W) / pps)   # přichyť ZAČÁTEK na mřížku
            t = max(0.0, min(t, end_t - BLOCK_MIN_W / pps))
            new_left = HEADER_W + t * pps
            w = right - new_left
            self.setPos(new_left, self.lane_y)
            self.prepareGeometryChange()
            self.setRect(0, 0, w, self.h)
            self.clip["start_s"] = round(t, 3)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._resizing:
            self._resizing = ""
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self.editor.edit_clip(self)
        ev.accept()

    def contextMenuEvent(self, ev):
        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        sub = menu.addMenu("Režim zobrazení")
        for key in MODE_ORDER:
            act = sub.addAction(("● " if key == self._mode() else "   ") + MODE_LABELS[key])
            act.setData(key)
        menu.addSeparator()
        edit_act = menu.addAction("✎ Upravit klip…")
        split_act = menu.addAction("✂ Rozdělit v kurzoru")
        menu.addSeparator()
        fwd_act = menu.addAction("➤ Vybrat od zde DÁL v čase  (])")
        back_act = menu.addAction("➤ Vybrat od zde DŘÍV v čase  ([)")
        menu.addSeparator()
        del_act = menu.addAction("🗑 Smazat klip")
        chosen = menu.exec(ev.screenPos())
        if chosen is None:
            ev.accept()
            return
        if chosen is del_act:
            self.editor._push_undo()
            self.editor.delete_clip(self)
        elif chosen is edit_act:
            self.editor.edit_clip(self)
        elif chosen is split_act:
            self.editor.split_at_playhead(self)
        elif chosen is fwd_act:
            self.editor.select_ripple(self, "forward")
        elif chosen is back_act:
            self.editor.select_ripple(self, "backward")
        elif chosen.data() in MODE_LABELS:
            self.editor._push_undo()
            self.clip["mode"] = chosen.data()
            self.update()
        ev.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            new: QPointF = value
            if self._resizing:
                return QPointF(new.x(), self.lane_y)   # při resize jen zamkni Y
            self._mark_dirty()
            x = max(HEADER_W, new.x())
            t = self.editor.snap_time((x - HEADER_W) / self.editor.pps)
            dur = float(self.clip.get("end_s", 0.0)) - float(self.clip.get("start_s", 0.0))
            dur = max(0.05, dur)
            self.clip["start_s"] = round(t, 3)
            self.clip["end_s"] = round(t + dur, 3)
            return QPointF(HEADER_W + t * self.editor.pps, self.lane_y)
        return super().itemChange(change, value)


class BlockItem(QGraphicsRectItem):
    """Jeden blok na časové ose (text nebo akord), navázaný na event dict."""

    def __init__(self, editor: "TimelineEditor", event: dict, kind: str, lane_y: float, height: float):
        super().__init__()
        self.editor = editor
        self.event = event          # odkaz do lyrics_timeline / chords_timeline
        self.kind = kind            # 'lyric' | 'chord'
        self.lane_y = lane_y
        self.h = height
        self._resizing = False
        self._undo_pushed = False   # jeden undo krok za celé tažení, ne za pixel
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self._sync_from_event()     # setPos PŘED ItemSendsGeometryChanges (viz DisplayClipItem)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    # --- geometrie podle času/délky ---
    def _sync_from_event(self) -> None:
        pps = self.editor.pps
        t = float(self.event.get("time_s", 0.0))
        dur = float(self.event.get("duration_s", 0.5)) if self.kind == "lyric" else \
            float(self.event.get("duration_s", self.editor.default_chord_dur))
        w = max(BLOCK_MIN_W, dur * pps)
        self.setRect(0, 0, w, self.h)
        self.setPos(HEADER_W + t * pps, self.lane_y)

    def _label(self) -> str:
        return self.event.get("text", "") if self.kind == "lyric" else self.event.get("chord", "")

    # --- vykreslení ---
    def paint(self, p: QPainter, opt, widget=None):
        base = CHORD_COLOR if self.kind == "chord" else LYRIC_COLOR
        if self.isSelected():
            base = SEL_COLOR
        r = self.rect()
        p.setBrush(QBrush(base.lighter(150)))
        p.setPen(QPen(base, 1.5))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QPen(QColor("#1a1a1a")))
        f = QFont("Segoe UI", 9)
        f.setBold(self.kind == "chord")
        p.setFont(f)
        p.drawText(r.adjusted(4, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, self._label())

    # --- interakce ---
    def hoverMoveEvent(self, ev):
        near_edge = ev.pos().x() >= self.rect().width() - EDGE
        self.setCursor(Qt.SizeHorCursor if near_edge else Qt.OpenHandCursor)
        super().hoverMoveEvent(ev)

    def mousePressEvent(self, ev):
        self._undo_pushed = False   # nové tažení = nová undo hranice
        if ev.button() == Qt.LeftButton and ev.pos().x() >= self.rect().width() - EDGE:
            self._resizing = True
            ev.accept()
            return
        super().mousePressEvent(ev)

    def _mark_dirty(self) -> None:
        if not self._undo_pushed:
            self.editor._push_undo()
            self._undo_pushed = True

    def mouseMoveEvent(self, ev):
        if self._resizing:
            self._mark_dirty()
            pps = self.editor.pps
            start = float(self.event.get("time_s", 0.0))
            raw_w = max(BLOCK_MIN_W, ev.pos().x())
            end = self.editor.snap_time(start + raw_w / pps)   # přichyť KONEC na mřížku
            end = max(end, start + BLOCK_MIN_W / pps)
            w = (end - start) * pps
            self.prepareGeometryChange()
            self.setRect(0, 0, w, self.h)
            self.event["duration_s"] = round(end - start, 3)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._resizing:
            self._resizing = False
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self.editor.edit_block(self)
        ev.accept()

    def contextMenuEvent(self, ev):
        menu = QMenu()
        break_act = None
        first = None
        is_break = False
        if self.kind == "lyric":
            first = self.editor.first_word(self.event.get("track_index", 1))
            is_break = bool(self.event.get("line_start")) and self.event is not first
            if is_break:
                break_act = menu.addAction("⤺ Zrušit zalomení (spojit s předchozím řádkem)")
            else:
                break_act = menu.addAction("➤ Začít zde nový řádek")
            menu.addSeparator()
        fwd_act = menu.addAction("➤ Vybrat od zde DÁL v čase  (])")
        back_act = menu.addAction("➤ Vybrat od zde DŘÍV v čase  ([)")
        chosen = menu.exec(ev.screenPos())
        if chosen is None:
            ev.accept()
            return
        if chosen is break_act and self.event is not first:
            self.editor._push_undo()
            self.event["line_start"] = not is_break
            self.editor._relayout()
        elif chosen is fwd_act:
            self.editor.select_ripple(self, "forward")
        elif chosen is back_act:
            self.editor.select_ripple(self, "backward")
        ev.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if not self._resizing:
                self._mark_dirty()
            # zamkni Y na pruh, clampni a přichytni X
            new: QPointF = value
            x = max(HEADER_W, new.x())
            t = (x - HEADER_W) / self.editor.pps
            t = self.editor.snap_time(t)
            x = HEADER_W + t * self.editor.pps
            self.event["time_s"] = round(t, 3)
            return QPointF(x, self.lane_y)
        return super().itemChange(change, value)


class DrumHitItem(QGraphicsPixmapItem):
    """Jeden úder bicích (jedna ikonka), navázaný na event dict v
    drums_timeline. Lze přetáhnout v čase (Y je zamčené na vlastní řádek —
    řádek/buben se mění jen přes menu, ne tažením). Pravý klik = změna typu
    bubnu nebo smazání; Delete smaže vybraný úder (viz `delete_selected`)."""

    def __init__(self, editor: "TimelineEditor", event: dict, row_y: float, icon_size: int):
        pm = editor._drum_icon_pixmap(editor._drum_icon_key(event.get("drum", "?")), icon_size)
        super().__init__(pm)
        self.editor = editor
        self.event = event
        self.row_y = row_y
        self.icon_size = icon_size
        self._undo_pushed = False   # jeden undo krok za celé tažení, ne za pixel
        self.setOffset(-icon_size / 2, -icon_size / 2)
        self.setZValue(15)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(f"{event.get('drum', '?')} — táhni = posun v čase, "
                        "pravý klik = změnit buben / smazat")
        self._sync_from_event()     # setPos PŘED ItemSendsGeometryChanges (viz DisplayClipItem)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    def _sync_from_event(self) -> None:
        t = float(self.event.get("time_s", 0.0))
        self.setPos(HEADER_W + t * self.editor.pps, self.row_y)

    def mousePressEvent(self, ev):
        self._undo_pushed = False   # nové tažení = nová undo hranice
        super().mousePressEvent(ev)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if not self._undo_pushed:
                self.editor._push_undo()
                self._undo_pushed = True
            new: QPointF = value
            x = max(HEADER_W, new.x())
            t = self.editor.snap_time((x - HEADER_W) / self.editor.pps)
            x = HEADER_W + t * self.editor.pps
            self.event["time_s"] = round(t, 3)
            return QPointF(x, self.row_y)
        return super().itemChange(change, value)

    def contextMenuEvent(self, ev):
        menu = QMenu()
        sub = menu.addMenu("Změnit buben")
        cur = self.event.get("drum", "?")
        for dn in DRUM_HIT_NAMES:
            act = sub.addAction(("● " if dn == cur else "   ") + dn)
            act.setData(dn)
        menu.addSeparator()
        fwd_act = menu.addAction("➤ Vybrat od zde DÁL v čase  (])")
        back_act = menu.addAction("➤ Vybrat od zde DŘÍV v čase  ([)")
        menu.addSeparator()
        del_act = menu.addAction("🗑 Smazat úder")
        chosen = menu.exec(ev.screenPos())
        if chosen is None:
            ev.accept()
            return
        if chosen is del_act:
            self.editor._push_undo()
            self.editor.remove_drum_hit(self)
        elif chosen is fwd_act:
            self.editor.select_ripple(self, "forward")
        elif chosen is back_act:
            self.editor.select_ripple(self, "backward")
        elif chosen.data():
            self.editor._push_undo()
            self.event["drum"] = chosen.data()
            self.event["midi"] = DRUM_NAME_TO_MIDI.get(chosen.data(), self.event.get("midi", 0))
            self.editor._relayout_and_reselect(self.event)
        ev.accept()


class LineItem(QGraphicsRectItem):
    """Vizuální span jednoho karaoke řádku (seskupení slov). Jen zobrazení."""

    def __init__(self, editor: "TimelineEditor", events: list[dict], lane_y: float):
        super().__init__()
        self.editor = editor
        self.events = events
        self.lane_y = lane_y
        self.setZValue(3)
        self._resync()

    def _resync(self):
        pps = self.editor.pps
        t0 = min(e["time_s"] for e in self.events)
        t1 = max(e["time_s"] + e.get("duration_s", 0.5) for e in self.events)
        self.setRect(0, 0, max(BLOCK_MIN_W, (t1 - t0) * pps), LINE_H - 6)
        self.setPos(HEADER_W + t0 * pps, self.lane_y)

    def paint(self, p: QPainter, opt, widget=None):
        r = self.rect()
        p.setBrush(QBrush(LINE_COLOR.lighter(175)))
        p.setPen(QPen(LINE_COLOR, 1))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(QPen(LINE_COLOR.darker(120)))
        p.setFont(QFont("Segoe UI", 8))
        txt = " ".join(e.get("text", "") for e in self.events)
        p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft, txt)


class BreakHandle(QGraphicsRectItem):
    """Tažná hranice mezi řádky — posunutím se přesune konec řádku přes slova."""

    def __init__(self, editor: "TimelineEditor", track_index: int, start_event: dict,
                 y: float, height: float):
        super().__init__()
        self.editor = editor
        self.track_index = track_index
        self.start_event = start_event      # slovo, kterým začíná řádek napravo
        self.y0 = y
        self.setRect(-HANDLE_W / 2, 0, HANDLE_W, height)
        self.setBrush(QBrush(BREAK_COLOR))
        self.setPen(QPen(BREAK_COLOR.darker(130), 1))
        self.setZValue(30)
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.SizeHorCursor)
        self.setToolTip("Táhni = posuň konec řádku (kam patří slova)")
        self._sync()

    def _sync(self):
        t = float(self.start_event["time_s"])
        self.setPos(HEADER_W + t * self.editor.pps, self.y0)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(max(HEADER_W, value.x()), self.y0)   # jen vodorovně
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        # přichyť hranici k nejbližšímu začátku slova ve své stopě
        x = self.pos().x()
        t = (x - HEADER_W) / self.editor.pps
        target = self.editor.nearest_word(self.track_index, t)
        if target is not None and target is not self.start_event:
            first = self.editor.first_word(self.track_index)
            if target is not first:                # první slovo nemůže být hranice
                self.editor._push_undo()
                self.start_event["line_start"] = False
                target["line_start"] = True
        self.editor._relayout()


class DrumShiftButton(QGraphicsRectItem):
    """Malé klikatelné tlačítko v hlavičce stopy bicích (posun celé stopy
    v čase). Vykreslené jako obyčejná scénová položka, NE jako vložený
    QWidget/QGraphicsProxyWidget — ten při `scene.clear()` (volá se při
    každém překreslení) občas spadne na dvojitém uvolnění paměti."""

    def __init__(self, label: str, tooltip: str, on_click, w: float = 20.0, h: float = 18.0):
        super().__init__(0, 0, w, h)
        self._on_click = on_click
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(QColor("#d8b98a")))
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptHoverEvents(True)
        self.setZValue(20)
        txt = QGraphicsSimpleTextItem(label, self)
        txt.setFont(QFont("Segoe UI", 8, QFont.Bold))
        txt.setBrush(QBrush(QColor("#8a5a1e")))
        br = txt.boundingRect()
        txt.setPos((w - br.width()) / 2, (h - br.height()) / 2)

    def hoverEnterEvent(self, ev):
        self.setBrush(QBrush(QColor("#ffe9cc")))
        super().hoverEnterEvent(ev)

    def hoverLeaveEvent(self, ev):
        self.setBrush(QBrush(QColor("#ffffff")))
        super().hoverLeaveEvent(ev)

    def mousePressEvent(self, ev):
        ev.accept()

    def mouseReleaseEvent(self, ev):
        ev.accept()
        if self.rect().contains(ev.pos()):
            self._on_click()   # může spustit _relayout() → self zanikne, nic po tomto řádku


class DrumRowBackground(QGraphicsRectItem):
    """Pozadí JEDNOHO řádku bicí stopy (jeden konkrétní buben). Pravým
    kliknutím na PRÁZDNÉ místo v řádku přidá nový úder tohoto bubnu v daném
    čase (přichycen na aktuální mřížku, viz `snap_time`)."""

    def __init__(self, editor: "TimelineEditor", ti: int, drum_name: str,
                 x: float, y: float, w: float, h: float):
        super().__init__(0, 0, w, h)
        self.editor = editor
        self.ti = ti
        self.drum_name = drum_name
        self.setPos(x, y)
        self.setBrush(QBrush(QColor("#fff4e6")))
        self.setPen(QPen(Qt.NoPen))
        self.setToolTip(f"Pravý klik = přidat úder „{drum_name}“ zde")

    def contextMenuEvent(self, ev):
        t = self.editor.snap_time(ev.pos().x() / self.editor.pps)
        menu = QMenu()
        act = menu.addAction(f"➕ Přidat „{self.drum_name}“ zde")
        chosen = menu.exec(ev.screenPos())
        if chosen is act:
            self.editor.add_drum_hit(self.ti, self.drum_name, t)
        ev.accept()


class PropertiesPanel(QWidget):
    """Postranní panel — přesné (parametrické) zadání hodnot vybraného prvku
    časové osy: čas/délka/text/buben/režim na desetiny/tisíciny, ne jen
    tažením myší."""

    def __init__(self, editor: "TimelineEditor", parent=None):
        super().__init__(parent)
        self.editor = editor
        self.target = None
        self.setMinimumWidth(230)
        self.setMaximumWidth(340)
        root = QVBoxLayout(self)
        title = QLabel("Vlastnosti")
        title.setStyleSheet("font-weight:bold; padding:4px;")
        root.addWidget(title)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        root.addWidget(self.form_host)
        self.placeholder = QLabel("Vyber jeden prvek na časové ose (text, "
                                  "akord, úder bicích nebo klip displeje).")
        self.placeholder.setStyleSheet("color:#888; padding:8px;")
        self.placeholder.setWordWrap(True)
        root.addWidget(self.placeholder)
        root.addStretch()
        self._clear_form()
        self.set_target(None)

    def _clear_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)

    def _mark_dirty(self) -> None:
        """Jeden undo krok za celé 'sezení' editace tohoto cíle v panelu —
        i když uživatel změní víc polí (text, čas, délku…) po sobě, patří to
        pod jedno Ctrl+Z. Nová hranice nastane při přepnutí na jiný cíl
        (viz `set_target`)."""
        if not self._undo_pushed_for_target:
            self.editor._push_undo()
            self._undo_pushed_for_target = True

    def set_target(self, item) -> None:
        self.target = item
        self._undo_pushed_for_target = False   # nový cíl = nová undo hranice
        self._clear_form()
        known = isinstance(item, (BlockItem, DrumHitItem, DisplayClipItem))
        self.form_host.setVisible(known)
        self.placeholder.setVisible(not known)
        if not known:
            return
        if isinstance(item, BlockItem):
            self._build_block_form(item)
        elif isinstance(item, DrumHitItem):
            self._build_drum_form(item)
        elif isinstance(item, DisplayClipItem):
            self._build_clip_form(item)

    def _time_spin(self, value: float, on_change) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(0.0, 99999.0)
        sb.setDecimals(3)
        sb.setSingleStep(0.05)
        sb.setSuffix(" s")
        sb.blockSignals(True)
        sb.setValue(value)
        sb.blockSignals(False)
        sb.valueChanged.connect(on_change)
        return sb

    def _build_block_form(self, item: "BlockItem") -> None:
        ev = item.event
        is_chord = item.kind == "chord"
        text = QLineEdit(ev.get("chord", "") if is_chord else ev.get("text", ""))

        def on_text():
            self._mark_dirty()
            ev["chord" if is_chord else "text"] = text.text().strip()
            item.update()
        text.editingFinished.connect(on_text)

        def on_time(v):
            self._mark_dirty()
            ev["time_s"] = round(v, 3)
            item._sync_from_event()

        def on_dur(v):
            self._mark_dirty()
            ev["duration_s"] = round(v, 3)
            item._sync_from_event()

        t_sb = self._time_spin(float(ev.get("time_s", 0.0)), on_time)
        d_sb = self._time_spin(float(ev.get("duration_s", 0.5)), on_dur)

        self.form.addRow("Akord:" if is_chord else "Text:", text)
        self.form.addRow("Čas (s):", t_sb)
        self.form.addRow("Délka (s):", d_sb)

    def _build_drum_form(self, item: "DrumHitItem") -> None:
        ev = item.event
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(DRUM_HIT_NAMES)
        cur = ev.get("drum", "?")
        if cur not in DRUM_HIT_NAMES:
            combo.addItem(cur)
        combo.blockSignals(True)
        combo.setCurrentText(cur)
        combo.blockSignals(False)

        def on_drum(v):
            v = (v or "").strip()
            if not v or v == ev.get("drum"):
                return
            self._mark_dirty()
            ev["drum"] = v
            ev["midi"] = DRUM_NAME_TO_MIDI.get(v, ev.get("midi", 0))
            self.editor._relayout_and_reselect(ev)
        combo.currentTextChanged.connect(on_drum)

        def on_time(v):
            self._mark_dirty()
            ev["time_s"] = round(v, 3)
            item._sync_from_event()

        def on_dur(v):
            self._mark_dirty()
            ev["duration_s"] = round(v, 3)

        t_sb = self._time_spin(float(ev.get("time_s", 0.0)), on_time)
        d_sb = self._time_spin(float(ev.get("duration_s", 0.2)), on_dur)

        self.form.addRow("Buben:", combo)
        self.form.addRow("Čas (s):", t_sb)
        self.form.addRow("Délka (s):", d_sb)

    def _build_clip_form(self, item: "DisplayClipItem") -> None:
        clip = item.clip
        label = QLineEdit(clip.get("label", ""))

        def on_label():
            self._mark_dirty()
            clip["label"] = label.text().strip()
            item.update()
        label.editingFinished.connect(on_label)

        src = QComboBox()
        order = self.editor._track_order()
        names = self.editor._track_names()
        for ti in order:
            src.addItem(f"{ti}. {names.get(ti, f'Stopa {ti}')}", ti)
        cur_ti = clip.get("source_track", order[0] if order else 1)
        src.blockSignals(True)
        if cur_ti in order:
            src.setCurrentIndex(order.index(cur_ti))
        src.blockSignals(False)

        def on_src(idx):
            self._mark_dirty()
            clip["source_track"] = src.itemData(idx)
            item.update()
        src.currentIndexChanged.connect(on_src)

        mode = QComboBox()
        for key in MODE_ORDER:
            mode.addItem(MODE_LABELS[key], key)
        cur_mode = clip.get("mode", "lyrics_chords")
        mode.blockSignals(True)
        if cur_mode in MODE_ORDER:
            mode.setCurrentIndex(MODE_ORDER.index(cur_mode))
        mode.blockSignals(False)

        def on_mode(idx):
            self._mark_dirty()
            clip["mode"] = mode.itemData(idx)
            item.update()
        mode.currentIndexChanged.connect(on_mode)

        def on_start(v):
            self._mark_dirty()
            clip["start_s"] = round(v, 3)
            item._sync_from_clip()

        def on_end(v):
            self._mark_dirty()
            clip["end_s"] = round(v, 3)
            item._sync_from_clip()

        start_sb = self._time_spin(float(clip.get("start_s", 0.0)), on_start)
        end_sb = self._time_spin(float(clip.get("end_s", 1.0)), on_end)

        self.form.addRow("Popisek:", label)
        self.form.addRow("Zdrojová stopa:", src)
        self.form.addRow("Režim:", mode)
        self.form.addRow("Začátek (s):", start_sb)
        self.form.addRow("Konec (s):", end_sb)


class TimelineView(QGraphicsView):
    def __init__(self, scene, editor):
        super().__init__(scene)
        self.editor = editor
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        # Načtená píseň má ve scéně stovky až tisíce položek (údery bicích,
        # bloky textu/akordů). Bez těchhle voleb stojí každé překreslení
        # (posun playheadu při přehrávání) tolik času na hlavním vlákně, že
        # se ZADRHÁVÁ ZVUK — viz `_ensure_playhead_visible`.
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setOptimizationFlags(
            QGraphicsView.DontSavePainterState
            | QGraphicsView.DontAdjustForAntialiasing
        )
        self.setCacheMode(QGraphicsView.CacheBackground)

    def wheelEvent(self, ev):
        # Ctrl + kolečko = zoom časové osy
        if ev.modifiers() & Qt.ControlModifier:
            self.editor.zoom(1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)
            ev.accept()
            return
        super().wheelEvent(ev)

    def mousePressEvent(self, ev):
        # klik do horního pravítka → přesun kurzoru (playhead)
        sp = self.mapToScene(ev.position().toPoint())
        if ev.button() == Qt.LeftButton and sp.y() <= RULER_H and sp.x() >= HEADER_W:
            self.editor.set_playhead((sp.x() - HEADER_W) / self.editor.pps)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Z:
            if ev.modifiers() & Qt.ShiftModifier:
                self.editor.redo()          # Ctrl+Shift+Z
            else:
                self.editor.undo()          # Ctrl+Z
            ev.accept()
            return
        if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Y:
            self.editor.redo()              # Ctrl+Y
            ev.accept()
            return
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.editor.delete_selected()
            ev.accept()
            return
        if ev.key() == Qt.Key_S:              # žiletka — rozdělit klip v kurzoru
            self.editor.split_at_playhead()
            ev.accept()
            return
        if ev.key() == Qt.Key_BracketRight:   # ] — vyber od vybraného prvku DÁL v čase
            self.editor.select_ripple_from_selection("forward")
            ev.accept()
            return
        if ev.key() == Qt.Key_BracketLeft:    # [ — vyber od vybraného prvku DŘÍV v čase
            self.editor.select_ripple_from_selection("backward")
            ev.accept()
            return
        super().keyPressEvent(ev)


class TimelineEditor(QWidget):
    """Widget časové osy. Napojení: load_data(dict) → editace → to_json()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: dict[str, Any] = {}
        self.tracks: list[dict] = []
        self.pps: float = 60.0                # px za sekundu (zoom)
        self.snap_s: float = 0.0              # 0 = bez přichycení
        self.default_chord_dur: float = 1.0
        self.playhead_s: float = 0.0             # pozice kurzoru (s)
        self._playhead: Optional[PlayheadItem] = None
        self.blocks: list[BlockItem] = []
        self.clips: list[DisplayClipItem] = []   # klipy master "Displej" stopy
        self.drum_hit_items: list[DrumHitItem] = []   # úhozy bicí stopy
        self._display_lane_y: float = RULER_H + 4
        self._track_lane: dict[int, dict] = {}   # track_index → {'chord_y','lyric_y'}
        self.export_callback = None              # nastaví hlavní okno: fn(dict)
        self._drum_icon_pixmaps: dict[tuple[str, int], QPixmap] = {}
        self.bpm: float = 120.0
        self.beats_per_measure: int = 4
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []

        # --- přehrávání MP3/WAV synchronizované s playheadem (viz set_playhead) ---
        self.audio_path: Optional[str] = None
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)     # obranně — nespoléhat na výchozí hodnotu
        self.audio_output.setMuted(False)
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_audio_error)
        # Bluetooth/USB sluchátka se odpojují a připojují za běhu a Windows
        # jim přitom přidělí NOVÝ endpoint — držet si QAudioDevice z doby
        # startu appky znamená posílat zvuk do neexistujícího zařízení
        # (ticho, bez chybové hlášky). Proto sledujeme změny a přepojíme se.
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._on_audio_devices_changed)
        self._shuttle_timer = QTimer(self)
        self._shuttle_timer.setInterval(30)
        self._shuttle_timer.timeout.connect(self._shuttle_tick)
        self._shuttle_rate: float = 0.0
        # playhead/scroll při PŘEHRÁVÁNÍ se řídí tímhle vlastním časovačem,
        # ne signálem `positionChanged` — viz `_sync_ui_from_player`
        self._playback_ui_timer = QTimer(self)
        self._playback_ui_timer.setInterval(40)
        self._playback_ui_timer.timeout.connect(self._sync_ui_from_player)

        # krátký testovací tón (tlačítko „🔊 Test tón") — nezávislý na
        # QMediaPlayer, aby šlo ověřit výstupní zařízení i bez načtené písně
        self._test_sound = QSoundEffect(self)
        self._test_sound.setSource(QUrl.fromLocalFile(_beep_wav()))
        self._test_sound.setVolume(1.0)

        self._setup_ui()
        self.scene.selectionChanged.connect(self._on_selection_changed)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        bar_widget = QWidget()
        bar_widget.setMaximumHeight(30)
        bar = QHBoxLayout(bar_widget)
        bar.setContentsMargins(6, 1, 6, 1)
        bar.setSpacing(5)
        bar.addWidget(QLabel("Zoom:"))
        btn_out = QPushButton("−"); btn_out.setMaximumWidth(28); btn_out.setMaximumHeight(24)
        btn_in = QPushButton("+"); btn_in.setMaximumWidth(28); btn_in.setMaximumHeight(24)
        btn_out.clicked.connect(lambda: self.zoom(1 / 1.25))
        btn_in.clicked.connect(lambda: self.zoom(1.25))
        bar.addWidget(btn_out); bar.addWidget(btn_in)

        bar.addWidget(QLabel("Přichytit:"))
        self.snap_combo = QComboBox()
        self.snap_combo.setMaximumHeight(24)
        self.snap_combo.setToolTip(
            "Přichycení tažení k hudební mřížce (dle tempa písně) — "
            "ne k pevným sekundám.")
        for label, key in [("vypnuto", None), ("1/4 beatu", "q_beat"),
                           ("1/2 beatu", "h_beat"), ("1 beat", "beat"),
                           ("1 takt", "bar")]:
            self.snap_combo.addItem(label, key)
        self.snap_combo.setCurrentIndex(3)   # výchozí: 1 beat — hudebně smysluplné
        self.snap_combo.currentIndexChanged.connect(self._update_snap_s)
        bar.addWidget(self.snap_combo)

        self.time_lbl = QLabel("⏱ 0,00 s")
        self.time_lbl.setMaximumHeight(24)
        self.time_lbl.setStyleSheet(
            "color:#e01b24; font-weight:bold; font-family:Consolas; "
            "padding:1px 6px; border:1px solid #e0a0a0; border-radius:3px;")
        self.time_lbl.setToolTip("Pozice kurzoru — klikni do pravítka nebo táhni červený kurzor")
        bar.addWidget(self.time_lbl)

        btn_style = ("QPushButton{padding:2px 8px;}")
        colored_style = ("QPushButton{{background:{bg};color:white;padding:2px 8px;"
                         "border-radius:3px;font-weight:bold;}}"
                         "QPushButton:hover{{background:{hov};}}")

        undo_btn = QPushButton("↶ Zpět")
        undo_btn.setStyleSheet(btn_style)
        undo_btn.setToolTip("Zpět (Ctrl+Z)")
        undo_btn.clicked.connect(self.undo)
        redo_btn = QPushButton("↷ Znovu")
        redo_btn.setStyleSheet(btn_style)
        redo_btn.setToolTip("Znovu (Ctrl+Y / Ctrl+Shift+Z)")
        redo_btn.clicked.connect(self.redo)
        bar.addWidget(undo_btn)
        bar.addWidget(redo_btn)
        bar.addSpacing(8)

        add_clip = QPushButton("＋ Klip displeje")
        add_clip.setStyleSheet(colored_style.format(bg="#9141ac", hov="#a55bbf"))
        add_clip.clicked.connect(self.add_clip)
        split_btn = QPushButton("✂ Rozdělit (S)")
        split_btn.setStyleSheet(btn_style)
        split_btn.setToolTip("Rozdělí vybraný klip v pozici kurzoru (klávesa S)")
        split_btn.clicked.connect(lambda: self.split_at_playhead())
        autotime_btn = QPushButton("⏱ Na mřížku")
        autotime_btn.setStyleSheet(btn_style)
        autotime_btn.setToolTip("Přichytí začátky bloků na hudební mřížku "
                                "(takt/beat podle tempa) — žádné odhady.")
        autotime_btn.clicked.connect(self.auto_time_dialog)
        tempo_btn = QPushButton("✏️ Tempo")
        tempo_btn.setStyleSheet(btn_style)
        tempo_btn.setToolTip("Změní BPM písně. Volitelně přepočítá existující "
                             "časy proporcionálně (staré tempo / nové tempo).")
        tempo_btn.clicked.connect(self.bpm_dialog)
        count_in_btn = QPushButton("🥁⏱ Odpočet…")
        count_in_btn.setStyleSheet(btn_style)
        count_in_btn.setToolTip(
            "Vloží/upraví odpočet (count-in) před písní — automaticky posune "
            "vše ostatní, žádné ruční přesouvání stop.")
        count_in_btn.clicked.connect(self.count_in_dialog)
        bar.addWidget(add_clip)
        bar.addWidget(split_btn)
        bar.addWidget(autotime_btn)
        bar.addWidget(tempo_btn)
        bar.addWidget(count_in_btn)
        add_lyric = QPushButton("＋ Text")
        add_lyric.setStyleSheet(btn_style)
        add_lyric.clicked.connect(lambda: self.add_block("lyric"))
        add_chord = QPushButton("＋ Akord")
        add_chord.setStyleSheet(btn_style)
        add_chord.clicked.connect(lambda: self.add_block("chord"))
        del_btn = QPushButton("🗑 Smazat")
        del_btn.setStyleSheet(btn_style)
        del_btn.clicked.connect(self.delete_selected)
        shift_sel_btn = QPushButton("↔ Posunout vybrané…")
        shift_sel_btn.setStyleSheet(btn_style)
        shift_sel_btn.setToolTip(
            "Posune VŠECHNY vybrané prvky o přesný čas (parametricky, ne tažením).\n"
            "Nejdřív vyber víc prvků: Shift/Ctrl+klik, tažení obdélníku, nebo pravým "
            "klikem na prvek → „Vybrat od zde DÁL/DŘÍV v čase“ (zkratky ]/[).")
        shift_sel_btn.clicked.connect(self.shift_selected_dialog)
        bar.addWidget(add_lyric); bar.addWidget(add_chord); bar.addWidget(del_btn)
        bar.addWidget(shift_sel_btn)

        exp_btn = QPushButton("💾 Export JSON")
        exp_btn.setStyleSheet(colored_style.format(bg="#2d7d2d", hov="#3a9e3a"))
        exp_btn.clicked.connect(self._do_export)
        bar.addWidget(exp_btn)

        bar.addStretch()
        self.info_lbl = QLabel("ⓘ")
        self.info_lbl.setStyleSheet("color:#777; font-weight:bold;")
        self.info_lbl.setToolTip(
            "Displej = master stopa (co uvidí karaoke) · dvojklik klip = zdroj+režim · "
            "táhni okraje = délka · pravý klik = režim/smazat · Ctrl+kolečko = zoom\n"
            "] / [ nebo pravý klik → „Vybrat od zde DÁL/DŘÍV v čase“ = vyber prvek a "
            "vše stejného druhu po/před ním na stejné stopě → táhni myší nebo "
            "„↔ Posunout vybrané…“ pro přesný posun (hromadné přeřazení zbytku)")
        bar.addWidget(self.info_lbl)
        root.addWidget(bar_widget)

        self.scene = QGraphicsScene(self)
        self.view = TimelineView(self.scene, self)
        self.props_panel = PropertiesPanel(self)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(self.props_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1000, 260])
        root.addWidget(splitter)

        # --- spodní lišta: přehrávač MP3/WAV + shuttle/jog ovladač ---
        audio_widget = QWidget()
        audio_widget.setMaximumHeight(96)
        audio_bar = QHBoxLayout(audio_widget)
        audio_bar.setContentsMargins(8, 4, 8, 4)

        load_audio_btn = QPushButton("🎵 Načíst MP3/WAV…")
        load_audio_btn.setStyleSheet(btn_style)
        load_audio_btn.clicked.connect(self._load_audio_dialog)
        audio_bar.addWidget(load_audio_btn)

        self.audio_label = QLabel("(žádné audio)")
        self.audio_label.setStyleSheet("color:#777;")
        audio_bar.addWidget(self.audio_label)

        audio_bar.addSpacing(12)
        audio_bar.addWidget(QLabel("Výstup:"))
        self.device_combo = QComboBox()
        self.device_combo.setMaximumHeight(24)
        self.device_combo.setMinimumWidth(160)
        self.device_combo.setToolTip(
            "Zvukové zařízení, na které přehrávač i testovací tón hrají — "
            "Windows „výchozí“ nemusí být to, co zrovna posloucháš "
            "(např. při více připojených sluchátkách/Voicemeeru).")
        self._populate_device_combo()
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        audio_bar.addWidget(self.device_combo)

        refresh_btn = QPushButton("↻")
        refresh_btn.setMaximumWidth(28)
        refresh_btn.setStyleSheet(btn_style)
        refresh_btn.setToolTip("Znovu načíst seznam zvukových zařízení")
        refresh_btn.clicked.connect(self._populate_device_combo)
        audio_bar.addWidget(refresh_btn)

        test_btn = QPushButton("🔊 Test tón")
        test_btn.setStyleSheet(btn_style)
        test_btn.setToolTip(
            "Přehraje krátký pípák na vybraném zařízení — ověř TÍMHLE, že "
            "je vůbec slyšet něco, než budeš hledat problém v písničce.")
        test_btn.clicked.connect(self._play_test_tone)
        audio_bar.addWidget(test_btn)

        audio_bar.addStretch()

        self.jog = JogShuttleWidget()
        self.jog.playRequested.connect(self.play_audio)
        self.jog.pauseRequested.connect(self.pause_audio)
        self.jog.stopRequested.connect(self.stop_audio)
        self.jog.shuttleChanged.connect(self._on_shuttle)
        audio_bar.addWidget(self.jog)

        root.addWidget(audio_widget)

    # ------------------------------------------------------------------
    # Načtení dat + rozvržení
    # ------------------------------------------------------------------

    def snap_time(self, t: float) -> float:
        if self.snap_s and self.snap_s > 0:
            return round(t / self.snap_s) * self.snap_s
        return max(0.0, t)

    def load_data(self, data: dict) -> None:
        self._stop_shuttle_timer()
        self.player.stop()
        self.audio_path = None
        if hasattr(self, "audio_label"):
            self.audio_label.setText("(žádné audio)")
            self.audio_label.setStyleSheet("color:#777;")
        self.data = data or {}
        self.tracks = self.data.get("tracks", []) or []
        self._undo_stack.clear()   # nová/jiná píseň → historie z předchozí neplatí
        self._redo_stack.clear()
        # hudební mřížka — VŽDY dle tempa (žádné odhady z textu)
        meta = self.data.get("meta", {}) or {}
        self.bpm = meta.get("tempo_bpm", 120) or 120
        self.beat_s = 60.0 / self.bpm
        self.beats_per_measure = int(meta.get("beats_per_measure", 4) or 4)
        self.bar_s = self.beat_s * self.beats_per_measure
        self.count_in_s = float(meta.get("count_in_s", 0.0) or 0.0)
        self.default_chord_dur = round(self.beat_s, 3)
        self._update_snap_s()
        self._seed_line_starts()
        self._seed_display()
        self._relayout(full=True)
        # cesta k audiu se ukládá do meta.audio_file (viz load_audio) — pokud
        # z tohoto počítače existuje, rovnou ho napoj; jinak nech tiše
        # "(žádné audio)" (cesta z jiného stroje nesmí shodit otevření JSONu)
        audio_file = meta.get("audio_file")
        if audio_file and os.path.isfile(audio_file):
            self.load_audio(audio_file)

    def _update_snap_s(self) -> None:
        """Přepočítá `snap_s` z aktuálního tempa podle volby v `snap_combo`
        (beat/takt — ne pevné sekundy, viz `load_data`)."""
        key = self.snap_combo.currentData() if hasattr(self, "snap_combo") else None
        beat_s = getattr(self, "beat_s", 0.5)
        bar_s = getattr(self, "bar_s", 2.0)
        self.snap_s = {
            "q_beat": beat_s / 4, "h_beat": beat_s / 2,
            "beat": beat_s, "bar": bar_s,
        }.get(key, 0.0)

    # --- master "Displej" stopa (Vegas program) ---

    def _track_names(self) -> dict[int, str]:
        names = {t.get("index", i + 1): (t.get("name") or f"Stopa {i + 1}")
                 for i, t in enumerate(self.tracks)}
        for ti in self._track_order():
            names.setdefault(ti, f"Stopa {ti}")
        return names

    def _song_end(self) -> float:
        max_t = 1.0
        for ev in self.data.get("lyrics_timeline", []) + self.data.get("chords_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 1)))
        for t in self.tracks:
            for b in t.get("beats", []) or []:
                max_t = max(max_t, float(b.get("time_s", 0)) + float(b.get("duration_s", 0)))
        return round(max_t, 3)

    def _seed_display(self) -> None:
        """Poprvé sestaví klipy master stopy: jedno okno (text+akordy) na
        každý karaoke řádek. Když už display_timeline existuje, nechá ho být."""
        data = self.data
        if data.get("display_timeline"):
            return
        clips: list[dict] = []
        n = 0

        # A) EXPLICITNÍ řádky (z JSONu) → klip na každý karaoke řádek 1:1.
        #    Řádek bez slov (intro / mezihra) = klip „jen akordy". Klip nese
        #    `line` = index řádku, díky čemuž `to_json()` pozná, že jde o TENTO
        #    řádek, i když se s ním později hne (přetažení konce, viz níže).
        meta = data.get("meta", {}) or {}
        klines = data.get("karaoke_lines") or []
        explicit = bool(meta.get("has_line_structure")) or any("chords" in l for l in klines)
        if explicit and klines:
            for idx, l in enumerate(klines):
                words = l.get("words") or []
                has_words = bool(words)
                ti = words[0].get("track_index", 1) if has_words else 1
                start = float(l.get("start_s", words[0]["time_s"] if has_words else 0.0))
                end = float(l.get("end_s", start + 2.0))
                label = (l.get("text") or " ".join(l.get("chords", []))).strip()
                n += 1
                clips.append({
                    "id": f"clip-{n}",
                    "start_s": round(start, 3),
                    "end_s": round(end, 3),
                    "source_track": ti,
                    "mode": "lyrics_chords" if has_words else "chords",
                    "label": label[:24],
                    "line": l.get("line", idx),
                })
            clips.sort(key=lambda c: c["start_s"])
            data["display_timeline"] = clips
            return

        # B) fallback bez explicitní struktury: jedno okno na každý řádek
        #    odvozený z pauz mezi slovy.
        for ti in self._track_order():
            for line in self._group_lines(ti):
                if not line:
                    continue
                n += 1
                start = min(float(e["time_s"]) for e in line)
                end = max(float(e["time_s"]) + float(e.get("duration_s", 0.5)) for e in line)
                label = " ".join(e.get("text", "") for e in line).strip()
                clips.append({
                    "id": f"clip-{n}",
                    "start_s": round(start, 3),
                    "end_s": round(end, 3),
                    "source_track": ti,
                    "mode": "lyrics_chords",
                    "label": label[:24],
                })
        if not clips:
            order = self._track_order()
            clips.append({
                "id": "clip-1", "start_s": 0.0, "end_s": self._song_end(),
                "source_track": order[0] if order else 1,
                "mode": "lyrics_chords", "label": "Displej",
            })
        clips.sort(key=lambda c: c["start_s"])
        data["display_timeline"] = clips

    # --- karaoke řádky: seskupení slov, hranice ---

    def _lyrics_of(self, ti: int) -> list[dict]:
        return sorted(
            (e for e in self.data.get("lyrics_timeline", []) if e.get("track_index", 1) == ti),
            key=lambda e: e["time_s"],
        )

    def first_word(self, ti: int):
        evs = self._lyrics_of(ti)
        return evs[0] if evs else None

    def nearest_word(self, ti: int, t: float):
        evs = self._lyrics_of(ti)
        if not evs:
            return None
        return min(evs, key=lambda e: abs(e["time_s"] - t))

    def _seed_line_starts(self, gap: float = 1.5, max_words: int = 7,
                          max_span: float = 5.0) -> None:
        """Poprvé rozdělí slova do řádků a označí začátky (line_start).

        1) Když data nesou EXPLICITNÍ řádkovou strukturu (`karaoke_lines`
           z web importu — pozná se podle klíče `chords` nebo `meta.has_line_structure`),
           řádky se převezmou 1:1 — přesně jak jsou na webu.
        2) Jinak fallback: zalomí se při pauze > gap / po max_words slovech /
           když řádek přesáhne max_span s (čitelná „okna" i bez velkých pauz).
        """
        meta = self.data.get("meta", {}) or {}
        klines = self.data.get("karaoke_lines") or []
        lyr = self.data.get("lyrics_timeline", [])
        has_line_field = any("line" in e for e in lyr)
        explicit = (bool(meta.get("has_line_structure")) or has_line_field
                    or any("chords" in l for l in klines))

        if explicit:
            # 1) Nejspolehlivější: každé slovo nese index řádku `line`
            if has_line_field:
                for ti in self._track_order():
                    evs = self._lyrics_of(ti)
                    if any("line_start" in e for e in evs):
                        continue
                    prev = None
                    for idx, e in enumerate(evs):
                        e["line_start"] = bool(idx > 0 and e.get("line") != prev)
                        prev = e.get("line")
                return
            # 2) Fallback: začátky řádků z karaoke_lines podle času prvního slova
            starts: dict[int, set] = {}
            for l in klines:
                words = l.get("words") or []
                if not words:
                    continue
                ti = words[0].get("track_index", 1)
                starts.setdefault(ti, set()).add(round(float(words[0]["time_s"]), 3))
            for ti in self._track_order():
                evs = self._lyrics_of(ti)
                if any("line_start" in e for e in evs):
                    continue
                sset = starts.get(ti, set())
                for idx, e in enumerate(evs):
                    e["line_start"] = bool(idx > 0 and
                                           round(float(e["time_s"]), 3) in sset)
            return

        for ti in self._track_order():
            evs = self._lyrics_of(ti)
            if any("line_start" in e for e in evs):
                continue   # už rozděleno (uživatel editoval)
            prev_end = None
            line_t0 = None
            count = 0
            for i, e in enumerate(evs):
                brk = False
                if i > 0:
                    if prev_end is not None and (e["time_s"] - prev_end) > gap:
                        brk = True
                    elif count >= max_words:
                        brk = True
                    elif line_t0 is not None and (e["time_s"] - line_t0) > max_span:
                        brk = True
                e["line_start"] = brk
                if brk or i == 0:
                    line_t0 = e["time_s"]
                    count = 0
                count += 1
                prev_end = e["time_s"] + e.get("duration_s", 0.5)

    def _group_lines(self, ti: int) -> list[list[dict]]:
        """Rozdělí slova stopy na řádky podle příznaku line_start."""
        lines: list[list[dict]] = []
        cur: list[dict] = []
        for e in self._lyrics_of(ti):
            if cur and e.get("line_start"):
                lines.append(cur)
                cur = []
            cur.append(e)
        if cur:
            lines.append(cur)
        return lines

    def _track_order(self) -> list[int]:
        idxs = [t.get("index", i + 1) for i, t in enumerate(self.tracks)]
        # doplň i indexy vyskytující se jen v eventech
        for ev in self.data.get("lyrics_timeline", []) + self.data.get("chords_timeline", []):
            ti = ev.get("track_index", 1)
            if ti not in idxs:
                idxs.append(ti)
        return sorted(set(idxs))

    def _relayout(self, full: bool = False) -> None:
        self.scene.clear()
        self.blocks.clear()
        self.clips.clear()
        self.drum_hit_items.clear()
        self._track_lane.clear()

        order = self._track_order()
        names = self._track_names()

        # celková délka pro šířku scény
        max_t = 1.0
        for ev in self.data.get("lyrics_timeline", []) + self.data.get("chords_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 1)))
        for c in self.data.get("display_timeline", []):
            max_t = max(max_t, float(c.get("end_s", 0)))
        for ev in self.data.get("drums_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)))
        for ev in self.data.get("bass_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 0)))

        # výška stopy: normální stopy PER_TRACK; bicí rostou podle počtu
        # RŮZNÝCH bubnů — fixní kompaktní výška řádku (ikona 32×32 + okraj),
        # žádné umělé napasování na PER_TRACK (to dělalo i 1-2 bubny zbytečně
        # vysoké).
        lane_h: dict[int, float] = {}
        for ti in order:
            if self._track_is_drums(ti):
                lane_h[ti] = 8 + self._drum_row_count(ti) * DRUM_ROW_H + TRACK_GAP
            else:
                lane_h[ti] = PER_TRACK

        total_h = RULER_H + DISPLAY_ROW_H + sum(lane_h.values()) + 20
        total_w = HEADER_W + max_t * self.pps + 200
        self.scene.setSceneRect(0, 0, total_w, total_h)

        # pozadí pruhů + names + pravítko
        self._draw_ruler(max_t, total_h)

        # master "Displej" stopa navrchu
        self._display_lane_y = RULER_H + 4
        self._draw_display_bg(total_w)
        for clip in self.data.get("display_timeline", []):
            self._add_clip_item(clip)

        # zdrojové stopy (posunuté pod master stopu, proměnná výška řádku)
        top = RULER_H + DISPLAY_ROW_H
        for ti in order:
            h_slot = lane_h[ti]
            if self._track_is_drums(ti):
                self._draw_drums_lane(ti, top, names.get(ti, f"Stopa {ti}"), total_w, h_slot)
            elif self._track_is_bass(ti):
                self._draw_bass_lane(ti, top, names.get(ti, f"Stopa {ti}"), total_w, h_slot)
            else:
                line_y = top + 2
                chord_y = line_y + LINE_H
                lyric_y = chord_y + CHORD_H
                self._track_lane[ti] = {"line_y": line_y, "chord_y": chord_y, "lyric_y": lyric_y}
                self._draw_lane_bg(ti, top, names.get(ti, f"Stopa {ti}"), total_w)
            top += h_slot

        # bloky (akordy, slova)
        for ev in self.data.get("chords_timeline", []):
            self._add_block_item(ev, "chord")
        for ev in self.data.get("lyrics_timeline", []):
            self._add_block_item(ev, "lyric")

        # karaoke řádky: spany + tažné hranice
        for ti in order:
            self._add_line_items(ti)

        # kurzor (playhead) navrchu všeho
        self._playhead = PlayheadItem(self, total_h)
        self.scene.addItem(self._playhead)
        self._update_playhead_label()

    def _on_selection_changed(self) -> None:
        if not hasattr(self, "props_panel"):
            return
        sel = self.scene.selectedItems()
        self.props_panel.set_target(sel[0] if len(sel) == 1 else None)

    def _relayout_and_reselect(self, event_or_clip: dict) -> None:
        """Jako `_relayout()`, ale po přestavění scény znovu vybere prvek
        navázaný na `event_or_clip` (identita dict), aby panel vlastností
        nezmizel jen kvůli změně, která mění rozvržení (např. přesun bubnu
        do jiného řádku)."""
        self._relayout()
        for it in self.drum_hit_items:
            if it.event is event_or_clip:
                it.setSelected(True)
                return
        for it in self.clips:
            if it.clip is event_or_clip:
                it.setSelected(True)
                return
        for it in self.blocks:
            if it.event is event_or_clip:
                it.setSelected(True)
                return

    # ------------------------------------------------------------------
    # Undo/redo — snapshotová historie nad `self.data`. Každá uživatelská
    # akce, která mění data (přesun/resize/přidání/smazání/dialog…), zavolá
    # `_push_undo()` TĚSNĚ PŘED tím, než cokoliv zmutuje — funguje i pro
    # budoucí akce, stačí dodržet stejnou konvenci (push před mutací, jen
    # jednou za "jednu uživatelskou akci", ne za každý pixel tažení).
    # ------------------------------------------------------------------

    def _push_undo(self) -> None:
        self._undo_stack.append(copy.deepcopy(self.data))
        if len(self._undo_stack) > UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self.data))
        self._restore(self._undo_stack.pop())

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self.data))
        self._restore(self._redo_stack.pop())

    def _restore(self, snapshot: dict) -> None:
        self.data = snapshot
        self.tracks = self.data.get("tracks", []) or []
        meta = self.data.get("meta", {}) or {}
        self.bpm = meta.get("tempo_bpm", self.bpm) or self.bpm
        self.beat_s = 60.0 / self.bpm
        self.beats_per_measure = int(meta.get("beats_per_measure", self.beats_per_measure) or self.beats_per_measure)
        self.bar_s = self.beat_s * self.beats_per_measure
        self.count_in_s = float(meta.get("count_in_s", 0.0) or 0.0)
        self.default_chord_dur = round(self.beat_s, 3)
        self._update_snap_s()
        self.scene.clearSelection()
        self._relayout()

    def set_playhead(self, t: float, seek_audio: bool = True) -> None:
        """`seek_audio=True` (výchozí, uživatelské akce — klik do pravítka,
        panel vlastností…) navíc přeskočí i audio na stejný čas.
        `seek_audio=False` používají callbacky, které SAMY reagují na pohyb
        audia/shuttlu (`_on_audio_position_changed`, `_shuttle_tick`) — jinak
        by šlo o zpětnovazební smyčku (seek → position changed → seek → …)."""
        self.playhead_s = max(0.0, round(t, 3))
        if self._playhead is not None:
            self._playhead._sync()
        self._update_playhead_label()
        if seek_audio:
            self._seek_audio(self.playhead_s)

    def _update_playhead_label(self) -> None:
        if hasattr(self, "time_lbl"):
            self.time_lbl.setText(f"⏱ {self.playhead_s:.2f} s".replace(".", ","))

    # ------------------------------------------------------------------
    # Přehrávání MP3/WAV (synchronizované s playheadem) + shuttle/jog
    # ------------------------------------------------------------------

    def _populate_device_combo(self) -> None:
        """Naplní výběr zvukových výstupů (Windows „výchozí" se nemusí
        shodovat s tím, co uživatel opravdu poslouchá — např. více
        sluchátek/Voicemeeter — proto jde vybrat ručně, viz `_on_device_changed`)."""
        current_id = self.audio_output.device().id()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        outputs = QMediaDevices.audioOutputs()
        default_id = QMediaDevices.defaultAudioOutput().id()
        sel = 0
        for i, dev in enumerate(outputs):
            label = dev.description()
            if dev.id() == default_id:
                label += "  (výchozí)"
            self.device_combo.addItem(label, dev)
            if dev.id() == current_id:
                sel = i
        if outputs:
            self.device_combo.setCurrentIndex(sel)
        self.device_combo.blockSignals(False)

    def _on_device_changed(self, idx: int) -> None:
        dev = self.device_combo.itemData(idx)
        if dev is None:
            return
        self._bind_audio_device(dev)

    def _on_audio_devices_changed(self) -> None:
        """Zařízení se v systému objevilo/zmizelo (typicky Bluetooth
        sluchátka). Přenačti nabídku a PŘEPOJ se na čerstvý endpoint —
        starý (z doby startu appky) už nemusí existovat a zvuk by tiše
        odcházel do prázdna."""
        self._populate_device_combo()
        dev = self.device_combo.currentData()
        if dev is not None:
            self._bind_audio_device(dev)

    def _bind_audio_device(self, dev) -> None:
        """Přepojí přehrávač i testovací tón na dané zařízení. QAudioOutput
        vytváříme ZNOVU — `setDevice()` na objektu, jehož endpoint mezitím
        zmizel (odpojená BT sluchátka), se u Windows backendu nemusí
        zotavit a zůstane tichý."""
        pos = self.player.position()
        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        old = self.audio_output
        self.audio_output = QAudioOutput(self)
        self.audio_output.setDevice(dev)
        self.audio_output.setVolume(1.0)
        self.audio_output.setMuted(False)
        self.player.setAudioOutput(self.audio_output)
        if old is not None:
            old.deleteLater()
        if was_playing:
            self.player.setPosition(pos)
            self.player.play()
        # QSoundEffect přepojení zvládá bez znovuvytvoření, ale zdroj je
        # potřeba nastavit znovu, aby se načetl do nového zařízení
        self._test_sound.setAudioDevice(dev)
        self._test_sound.setSource(QUrl.fromLocalFile(_beep_wav()))

    def _play_test_tone(self) -> None:
        # vždy nejdřív ověř, že cílové zařízení pořád existuje (viz
        # `_bind_audio_device`) — jinak by tón tiše spadl do prázdna
        cur = self.audio_output.device()
        alive = any(d.id() == cur.id() for d in QMediaDevices.audioOutputs())
        if not alive:
            self._on_audio_devices_changed()
        self._test_sound.play()

    def _load_audio_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Načíst zvuk k písni", "", "Audio (*.mp3 *.wav)")
        if path:
            self.load_audio(path)

    def load_audio(self, path: str) -> None:
        """Nastaví zdroj přehrávače a uloží cestu do `meta.audio_file`
        (perzistence mezi otevřeními — viz `load_data`). Volba souboru
        NENÍ editace karaoke dat → nejde do undo historie."""
        self.audio_path = path
        self.player.setSource(QUrl.fromLocalFile(path))
        self.audio_label.setText(os.path.basename(path))
        self.audio_label.setStyleSheet("color:#2d7d2d;")
        self.data.setdefault("meta", {})["audio_file"] = path

    def _on_audio_error(self, error, error_string: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.audio_label.setText(f"⚠ audio: {error_string}")
        self.audio_label.setStyleSheet("color:#c01c28;")

    def _on_playback_state_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.jog.set_playing(playing)
        # UI (playhead + scroll) se řídí VLASTNÍM tikajícím časovačem
        # (`_sync_ui_from_player`), ne signálem `positionChanged` — ten umí
        # u některých backendů/zařízení (zvlášť přes Voicemeeter) chodit
        # velmi často, a časté GUI práce (setPos/scrollbar/repaint) na
        # hlavním vlákně pak zadrhává samotné PŘEHRÁVÁNÍ ZVUKU (cukání).
        if playing:
            self._playback_ui_timer.start()
        else:
            self._playback_ui_timer.stop()

    def _seek_audio(self, t: float) -> None:
        if self.player.source().isValid():
            self.player.setPosition(int(round(max(0.0, t) * 1000)))

    def _ensure_device_alive(self) -> None:
        """Zkontroluje, že zařízení, na které hrajeme, v systému pořád je —
        po odpojení/připojení sluchátek by se jinak hrálo do prázdna."""
        cur = self.audio_output.device()
        if not any(d.id() == cur.id() for d in QMediaDevices.audioOutputs()):
            self._on_audio_devices_changed()

    def play_audio(self) -> None:
        if not self.player.source().isValid():
            return
        self._ensure_device_alive()
        self._stop_shuttle_timer()
        self.player.setPlaybackRate(1.0)
        self._seek_audio(self.playhead_s)
        self.player.play()

    def pause_audio(self) -> None:
        self._stop_shuttle_timer()
        self.player.pause()

    def stop_audio(self) -> None:
        """Zastaví přehrávání. Qt `stop()` interně vrací pozici na 0 —
        editor to hned poté vrátí zpět na `playhead_s`, aby "Stop" v
        editačním nástroji nepřekvapil skokem na začátek skladby."""
        self._stop_shuttle_timer()
        self.player.stop()
        self._seek_audio(self.playhead_s)

    # --- shuttle (mezikruží ovladače) ---

    def _on_shuttle(self, value: float) -> None:
        """`value` −1..+1 z `JogShuttleWidget.shuttleChanged` (0 = klid).
        Doprava (+): reálný zvuk zrychlený 1×–4× (`setPlaybackRate`).
        Doleva (−): TICHÝ posun playheadu (Qt/FFmpeg neumí přehrát zvuk
        pozpátku) — až 8× rychlostí timeline za reálnou sekundu."""
        if abs(value) < 0.02:
            self._stop_shuttle_timer()
            self.player.pause()
            self.player.setPlaybackRate(1.0)
            return
        if value > 0:
            self._stop_shuttle_timer()
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self._seek_audio(self.playhead_s)
                self.player.play()
            self.player.setPlaybackRate(1.0 + value * 3.0)   # 1×–4×
        else:
            self.player.pause()
            self._start_shuttle_timer(value * 8.0)           # tichý posun, až 8×

    def _start_shuttle_timer(self, rate: float) -> None:
        self._shuttle_rate = rate
        if not self._shuttle_timer.isActive():
            self._shuttle_timer.start()

    def _stop_shuttle_timer(self) -> None:
        self._shuttle_timer.stop()
        self._shuttle_rate = 0.0

    def _shuttle_tick(self) -> None:
        dt = self._shuttle_timer.interval() / 1000.0
        self.set_playhead(self.playhead_s + self._shuttle_rate * dt, seek_audio=False)
        self._ensure_playhead_visible()

    def _sync_ui_from_player(self) -> None:
        """Tikne ~25×/s (viz `_playback_ui_timer`), dokud přehrávač hraje —
        VLASTNÍ tempo aktualizace GUI, nezávislé na tom, jak často reálně
        chodí `positionChanged` (to je mimo naši kontrolu a u některých
        zařízení/backendů může chodit mnohem častěji, než je pro plynulé
        oko potřeba — a každý navíc tik znamená zátěž hlavního vlákna,
        která může způsobit zvukové zádrhele/cukání)."""
        self.set_playhead(self.player.position() / 1000.0, seek_audio=False)
        self._ensure_playhead_visible()

    def _ensure_playhead_visible(self) -> None:
        """Při přehrávání/shuttlu posune vodorovný scroll, aby playhead
        neutekl mimo viditelnou oblast.

        Roluje se ve VELKÝCH SKOCÍCH (playhead skočí do ~1/4 šířky pohledu),
        ne plynule po pixelech. Plynulé rolování totiž znamenalo překreslení
        CELÉHO výřezu (u načtené písně jsou to stovky až tisíce položek —
        údery bicích, bloky textu, akordy) při každém tiku, tj. 25×/s.
        To hlavnímu vláknu sebralo tolik času, že se ZADRHÁVAL ZVUK.
        Takhle se překresluje jen jednou za mnoho vteřin."""
        if self._playhead is None:
            return
        x = HEADER_W + self.playhead_s * self.pps
        vp = self.view.viewport().width()
        sb = self.view.horizontalScrollBar()
        cur = sb.value()
        margin = 40
        if x > cur + vp - margin or x < cur + HEADER_W:
            sb.setValue(max(0, int(x - HEADER_W - vp * 0.25)))

    def _draw_display_bg(self, total_w: float) -> None:
        y = self._display_lane_y
        w = total_w - HEADER_W
        # pruh master stopy
        self.scene.addRect(HEADER_W, y, w, DISPLAY_H,
                           QPen(Qt.NoPen), QBrush(QColor("#f3e9fb")))
        # hlavička master stopy
        self.scene.addRect(0, y - 4, HEADER_W, DISPLAY_ROW_H - 6,
                           QPen(QColor("#c9a3e0")), QBrush(QColor("#ece0f6")))
        nm = self.scene.addSimpleText("🖥 DISPLEJ")
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(8, y + 2)
        nm.setBrush(QBrush(QColor("#7a2a9a")))
        sub = self.scene.addSimpleText("výstup na karaoke")
        sub.setFont(QFont("Segoe UI", 7))
        sub.setPos(8, y + 20)
        sub.setBrush(QBrush(QColor("#9a6cb5")))

    def _add_clip_item(self, clip: dict) -> None:
        item = DisplayClipItem(self, clip, self._display_lane_y, DISPLAY_H)
        self.scene.addItem(item)
        self.clips.append(item)

    def _draw_ruler(self, max_t: float, total_h: float) -> None:
        """Pravidelná hudební osnova: silná čára + číslo na KAŽDÉM taktu, tenká
        čára na každém beatu (dle tempa — `self.bar_s`/`self.beat_s`, viz
        `load_data`). Žádné odhady z textu — jen tempo/takt."""
        pen_bar = QPen(QColor("#999999")); pen_bar.setWidth(2)
        pen_beat = QPen(QColor("#e2e2e2"))
        self.scene.addRect(0, 0, HEADER_W + max_t * self.pps + 200, RULER_H,
                           QPen(Qt.NoPen), QBrush(QColor("#f5f5f5")))

        bar_s = max(0.05, getattr(self, "bar_s", 2.0))
        beat_s = max(0.01, getattr(self, "beat_s", 0.5))
        beats_per_bar = max(1, getattr(self, "beats_per_measure", 4))

        # count-in (metronom / odklikání) — vizuálně odlišená zóna na začátku
        count_in_s = getattr(self, "count_in_s", 0.0)
        if count_in_s > 0:
            cx = HEADER_W + count_in_s * self.pps
            self.scene.addRect(HEADER_W, 0, cx - HEADER_W, total_h,
                               QPen(Qt.NoPen), QBrush(QColor(255, 196, 84, 55)))
            lbl = self.scene.addSimpleText("🎵 count-in (metronom)")
            lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
            lbl.setPos(HEADER_W + 4, 5)
            lbl.setBrush(QBrush(QColor("#8a5600")))

        bar_px = bar_s * self.pps
        show_beats = (beat_s * self.pps) >= 6          # ať se beaty nepřehustí
        label_every = max(1, -(-40 // max(1, int(bar_px))))   # ceil(40/bar_px)

        bar_i = 0
        t = 0.0
        while t <= max_t + bar_s:
            x = HEADER_W + t * self.pps
            self.scene.addLine(x, 0, x, total_h, pen_bar)
            if bar_i % label_every == 0:
                lbl = self.scene.addSimpleText(f"Takt {bar_i + 1}  ·  {t:g}s")
                lbl.setFont(QFont("Segoe UI", 7, QFont.Bold))
                lbl.setPos(x + 3, RULER_H - 13)
                lbl.setBrush(QBrush(QColor("#555")))
            if show_beats:
                for bi in range(1, beats_per_bar):
                    bx = x + bi * beat_s * self.pps
                    self.scene.addLine(bx, RULER_H * 0.35, bx, total_h, pen_beat)
            t += bar_s
            bar_i += 1

        # oddělovač hlavičky
        self.scene.addLine(HEADER_W, 0, HEADER_W, total_h, pen_bar)

    def _draw_lane_bg(self, ti: int, top: float, name: str, total_w: float) -> None:
        line_y = top + 2
        chord_y = line_y + LINE_H
        lyric_y = chord_y + CHORD_H
        w = total_w - HEADER_W
        # pruhy: řádky (fialová) + akordy (modrá) + text (zelená)
        self.scene.addRect(HEADER_W, line_y, w, LINE_H,
                           QPen(Qt.NoPen), QBrush(QColor("#f6eef9")))
        self.scene.addRect(HEADER_W, chord_y, w, CHORD_H,
                           QPen(Qt.NoPen), QBrush(QColor("#eef3fb")))
        self.scene.addRect(HEADER_W, lyric_y, w, LYRIC_H,
                           QPen(Qt.NoPen), QBrush(QColor("#eef7ee")))
        # hlavička
        self.scene.addRect(0, top, HEADER_W, PER_TRACK - TRACK_GAP + 2,
                           QPen(QColor("#dddddd")), QBrush(QColor("#fafafa")))
        nm = self.scene.addSimpleText(name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        for txt, y, col in [("řádky", line_y + 4, "#9141ac"),
                            ("akordy", chord_y + 4, "#1a5fb4"),
                            ("text", lyric_y + 6, "#2d7d2d")]:
            it = self.scene.addSimpleText(txt)
            it.setPos(12, y); it.setBrush(QBrush(QColor(col)))

    def _track_is_drums(self, ti: int) -> bool:
        for t in self.tracks:
            if t.get("index") == ti:
                return bool(t.get("is_drums") or t.get("type") == "drums")
        return False

    def _drum_names_for(self, ti: int) -> list[str]:
        """Distinct jména bubnů použitá touto stopou, seřazená pro zobrazení
        (činely/hi-hat nahoře, snare/tom uprostřed, kick dole; abecedně
        v rámci skupiny). Každé dostane VLASTNÍ řádek — žádné 2 různé bubny
        se nikdy nepřekrývají v jednom řádku."""
        names = {ev.get("drum", "?") for ev in self.data.get("drums_timeline", [])
                 if ev.get("track_index") == ti}
        return sorted(names, key=lambda n: (self._drum_family(n)[0], n))

    def _drum_row_count(self, ti: int) -> int:
        return max(1, len(self._drum_names_for(ti)))

    @staticmethod
    def _drum_family(name: str):
        """Vrátí (skupina 0-2, barva) podle jména bubnu — určuje POŘADÍ řádků
        (0=nahoře: činely/hi-hat, 1=uprostřed: snare/tom, 2=dole: kick) a barvu."""
        n = (name or "").lower()
        if "kick" in n or "bass drum" in n:
            return 2, QColor("#3a3a3a")
        if "snare" in n:
            return 1, QColor("#c01c28")
        if "tom" in n:
            return 1, QColor("#2d7d2d")
        if "hi-hat" in n or "hihat" in n or "hi hat" in n:
            return 0, QColor("#1a5fb4")
        if any(k in n for k in ("crash", "ride", "cymbal", "splash", "china", "bell")):
            return 0, QColor("#e08a00")
        return 1, QColor("#888888")

    @staticmethod
    def _drum_icon_key(name: str) -> str:
        """Vrátí klíč do DRUM_ICON_FILES podle jména bubnu (konkrétnější
        než _drum_family — rozlišuje otevřenou/zavřenou hi-hat a má vlastní
        'perc' ikonku pro ostatní perkuse, které nemají specifickou ikonu)."""
        n = (name or "").lower()
        if "kick" in n or "bass drum" in n:
            return "kick"
        if "snare" in n or "side stick" in n or "hand clap" in n:
            return "snare"
        if "tom" in n:
            if "floor" in n:
                return "tom_low"
            if "high" in n or "hi-mid" in n or "hi mid" in n:
                return "tom_high"
            return "tom_mid"
        if "open hi-hat" in n or "open hihat" in n or "open hi hat" in n:
            return "hihat_open"
        if "hi-hat" in n or "hihat" in n or "hi hat" in n:
            return "hihat_closed"
        if any(k in n for k in ("crash", "ride", "cymbal", "splash", "china", "bell")):
            return "cymbal"
        return "perc"

    def _drum_icon_pixmap(self, key: str, size_px: int) -> QPixmap:
        """Ikonka bubnu jako QPixmap dané velikosti (cachováno). Zdroj je buď
        PNG (skutečná grafika v resources/, ořezaná bez popisku), nebo SVG
        placeholder (perc.svg) pro bubny bez vlastní grafiky."""
        cache_key = (key, size_px)
        pm = self._drum_icon_pixmaps.get(cache_key)
        if pm is None:
            filename = DRUM_ICON_FILES.get(key, "perc.svg")
            path = os.path.join(DRUM_ICON_DIR, filename)
            if filename.lower().endswith(".svg"):
                renderer = QSvgRenderer(path)
                pm = QPixmap(size_px, size_px)
                pm.fill(Qt.transparent)
                painter = QPainter(pm)
                renderer.render(painter)
                painter.end()
            else:
                src = QPixmap(path)
                pm = src.scaled(size_px, size_px, Qt.KeepAspectRatio,
                                Qt.SmoothTransformation)
            self._drum_icon_pixmaps[cache_key] = pm
        return pm

    def _draw_drums_lane(self, ti: int, top: float, name: str, total_w: float,
                         h_slot: float | None = None) -> None:
        """Vykreslí stopu bicích: KAŽDÝ konkrétní buben má vlastní popsaný
        řádek (ne sdílenou kategorii) — úhozy jsou plné "note-head" tečky, ne
        tenké čárky, takže se dá při hustším rytmu pořád rozeznat, kdy který
        buben hraje. Jen zobrazení."""
        h = (h_slot if h_slot is not None else PER_TRACK) - TRACK_GAP
        y = top + 2
        w = total_w - HEADER_W
        # hlavička
        self.scene.addRect(0, top, HEADER_W, h + 2,
                           QPen(QColor("#e6c9a3")), QBrush(QColor("#fbf0e0")))
        nm = self.scene.addSimpleText("🥁 " + name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        nm.setBrush(QBrush(QColor("#b56b1e")))
        self._add_drum_shift_controls(ti, top)

        drum_names = self._drum_names_for(ti)
        n_rows = max(1, len(drum_names))
        rows_h = (h - 8) / n_rows
        row_of = {dn: i for i, dn in enumerate(drum_names)}
        colors = {dn: self._drum_family(dn)[1] for dn in drum_names}

        for i, dn in enumerate(drum_names):
            ry = y + 4 + i * rows_h
            # interaktivní pozadí řádku — pravý klik = přidat úder tohoto bubnu
            bg = DrumRowBackground(self, ti, dn, HEADER_W, ry, w, rows_h)
            self.scene.addItem(bg)
            it = self.scene.addSimpleText(dn)
            it.setFont(QFont("Segoe UI", 10, QFont.Bold))
            it.setPos(12, ry + rows_h / 2 - 8)
            it.setBrush(QBrush(colors[dn].darker(140)))
            # jemná vodicí linka řady (skrz střed)
            self.scene.addLine(HEADER_W, ry + rows_h / 2, HEADER_W + w, ry + rows_h / 2,
                               QPen(QColor("#f0e0cc"), 1))
            # oddělovač mezi řádky
            if i > 0:
                self.scene.addLine(HEADER_W, ry, HEADER_W + w, ry, QPen(QColor("#f7e6cc"), 1))

        icon_size = min(DRUM_ICON_SIZE, max(12, int(rows_h - 8)))
        for ev in self.data.get("drums_timeline", []):
            if ev.get("track_index") != ti:
                continue
            dn = ev.get("drum", "?")
            i = row_of.get(dn, 0)
            row_y = y + 4 + i * rows_h + rows_h / 2
            item = DrumHitItem(self, ev, row_y, icon_size)
            self.scene.addItem(item)
            self.drum_hit_items.append(item)

    def _add_drum_shift_controls(self, ti: int, top: float) -> None:
        """Tlačítka v hlavičce stopy bicích pro posun VŠECH úhozů dané stopy
        v čase najednou (řeší situaci, kdy bicí z GP souboru vyjedou z fáze
        vůči textu/akordům odjinud)."""
        x0 = HEADER_W - 72
        y0 = top + 3
        specs = [
            ("◀", "Posunout celou stopu bicích dřív o krok mřížky (Přichytit)",
             lambda: self.shift_drum_track(ti, -self._nudge_step())),
            ("▶", "Posunout celou stopu bicích později o krok mřížky (Přichytit)",
             lambda: self.shift_drum_track(ti, self._nudge_step())),
            ("⋯", "Posunout celou stopu bicích o přesný čas…",
             lambda: self._shift_drum_track_dialog(ti)),
        ]
        for i, (label, tip, cb) in enumerate(specs):
            btn = DrumShiftButton(label, tip, cb)
            btn.setPos(x0 + i * 22, y0)
            self.scene.addItem(btn)

    def _nudge_step(self) -> float:
        """Krok posunu stopy = aktuální mřížka (Přichytit), jinak 0,1 s."""
        return self.snap_s if self.snap_s > 0 else 0.1

    def shift_drum_track(self, ti: int, delta_s: float) -> None:
        """Posune ČASY VŠECH úhozů bicích dané stopy o delta_s (+ později,
        − dřív). Jen bicí (drums_timeline) — text/akordy zůstávají beze
        změny, protože právě jejich vzájemný posun je cílem."""
        if not delta_s:
            return
        self._push_undo()
        for ev in self.data.get("drums_timeline", []):
            if ev.get("track_index") == ti:
                ev["time_s"] = round(max(0.0, float(ev.get("time_s", 0.0)) + delta_s), 3)
        self._relayout()

    def _shift_drum_track_dialog(self, ti: int) -> None:
        delta, ok = QInputDialog.getDouble(
            self, "Posunout stopu bicích",
            "Posun v sekundách (kladné = později, záporné = dřív):",
            0.0, -60.0, 60.0, 3)
        if ok and delta:
            self.shift_drum_track(ti, delta)

    def add_drum_hit(self, ti: int, drum_name: str, time_s: float) -> None:
        """Přidá nový úder bicích (jeden konkrétní buben) v daném čase —
        volá se z kontextového menu prázdného místa v řádku (DrumRowBackground)."""
        self._push_undo()
        ev = {
            "time_s": round(max(0.0, time_s), 3),
            "duration_s": 0.2,
            "drum": drum_name,
            "midi": DRUM_NAME_TO_MIDI.get(drum_name, 0),
            "track_index": ti,
        }
        self.data.setdefault("drums_timeline", []).append(ev)
        self._relayout()

    def remove_drum_hit(self, item: "DrumHitItem") -> None:
        try:
            self.data.get("drums_timeline", []).remove(item.event)
        except ValueError:
            pass
        self.scene.removeItem(item)
        if item in self.drum_hit_items:
            self.drum_hit_items.remove(item)

    def _track_is_bass(self, ti: int) -> bool:
        for t in self.tracks:
            if t.get("index") == ti:
                return t.get("type") == "bass"
        return False

    def _draw_bass_lane(self, ti: int, top: float, name: str, total_w: float,
                        h_slot: float | None = None) -> None:
        """Vykreslí basovou stopu: noty jako úsečky (délka = duration_s) ve
        4 řadách podle struny (1 = nejtenčí nahoře … 4 = nejtlustší dole).
        Jen zobrazení — basa se do exportu neposílá."""
        h = (h_slot if h_slot is not None else PER_TRACK) - TRACK_GAP
        y = top + 2
        w = total_w - HEADER_W
        self.scene.addRect(HEADER_W, y, w, h - 2,
                           QPen(Qt.NoPen), QBrush(QColor("#eaf3ff")))
        self.scene.addRect(0, top, HEADER_W, h + 2,
                           QPen(QColor("#a3c2e6")), QBrush(QColor("#e0ecfb")))
        nm = self.scene.addSimpleText("🎸 " + name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        nm.setBrush(QBrush(QColor("#1a5fb4")))

        n_strings = 4
        rows_h = (h - 8) / n_strings
        for s in range(1, n_strings + 1):
            ry = y + 4 + (s - 1) * rows_h
            it = self.scene.addSimpleText(f"struna {s}")
            it.setFont(QFont("Segoe UI", 7))
            it.setPos(12, ry - 2)
            it.setBrush(QBrush(QColor("#4a7fc9")))
            self.scene.addLine(HEADER_W, ry + rows_h / 2, HEADER_W + w, ry + rows_h / 2,
                               QPen(QColor("#cfe0f5"), 1))

        col = QColor("#1a5fb4")
        for ev in self.data.get("bass_timeline", []):
            if ev.get("track_index") != ti:
                continue
            s = max(1, min(n_strings, int(ev.get("string", 4))))
            ry = y + 4 + (s - 1) * rows_h + rows_h / 2
            x0 = HEADER_W + float(ev.get("time_s", 0)) * self.pps
            x1 = x0 + max(2.0, float(ev.get("duration_s", 0.2)) * self.pps)
            p = QPen(col, 3)
            p.setCapStyle(Qt.RoundCap)
            self.scene.addLine(x0, ry, x1, ry, p)

    def _add_line_items(self, ti: int) -> None:
        lane = self._track_lane.get(ti)
        if lane is None:
            return
        lines = self._group_lines(ti)
        for li, line in enumerate(lines):
            span = LineItem(self, line, lane["line_y"] + 2)
            self.scene.addItem(span)
            # tažná hranice na začátku každého řádku kromě prvního
            if li > 0:
                handle = BreakHandle(self, ti, line[0], lane["line_y"],
                                     LINE_H + CHORD_H + LYRIC_H - 2)
                self.scene.addItem(handle)

    def _add_block_item(self, ev: dict, kind: str) -> None:
        ti = ev.get("track_index", 1)
        lane = self._track_lane.get(ti)
        if lane is None:
            return
        y = lane["chord_y"] if kind == "chord" else lane["lyric_y"]
        h = CHORD_H - 4 if kind == "chord" else LYRIC_H - 4
        block = BlockItem(self, ev, kind, y + 2, h)
        self.scene.addItem(block)
        self.blocks.append(block)

    # ------------------------------------------------------------------
    # Akce
    # ------------------------------------------------------------------

    def zoom(self, factor: float) -> None:
        self.pps = max(8.0, min(400.0, self.pps * factor))
        self._relayout()

    def _do_export(self) -> None:
        self._push_undo()   # to_json() přestavuje karaoke_lines/klipy — bezpečnostní krok
        if callable(self.export_callback):
            self.export_callback(self.to_json())

    def edit_block(self, block: BlockItem) -> None:
        if block.kind == "chord":
            text, ok = QInputDialog.getText(self, "Editace akordu", "Akord:",
                                            text=block.event.get("chord", ""))
            if ok:
                self._push_undo()
                block.event["chord"] = text.strip()
        else:
            text, ok = QInputDialog.getText(self, "Editace textu", "Text řádku:",
                                            text=block.event.get("text", ""))
            if ok:
                self._push_undo()
                block.event["text"] = text.strip()
        block.update()

    def add_block(self, kind: str) -> None:
        self._push_undo()
        order = self._track_order()
        ti = order[0] if order else 1
        if kind == "chord":
            ev = {"time_s": 0.0, "duration_s": self.default_chord_dur,
                  "chord": "C", "measure": 1, "track_index": ti}
            self.data.setdefault("chords_timeline", []).append(ev)
        else:
            ev = {"time_s": 0.0, "duration_s": 0.5, "text": "text",
                  "measure": 1, "track_index": ti}
            self.data.setdefault("lyrics_timeline", []).append(ev)
        self._add_block_item(ev, kind)

    # --- klipy master "Displej" stopy ---

    def add_clip(self) -> None:
        self._push_undo()
        order = self._track_order()
        ti = order[0] if order else 1
        clips = self.data.setdefault("display_timeline", [])
        start = max((float(c.get("end_s", 0.0)) for c in clips), default=0.0)
        clip = {
            "id": f"clip-{len(clips) + 1}",
            "start_s": round(start, 3),
            "end_s": round(start + 3.0, 3),
            "source_track": ti,
            "mode": "lyrics_chords",
            "label": "Nový klip",
        }
        clips.append(clip)
        self._add_clip_item(clip)

    def edit_clip(self, item: DisplayClipItem) -> None:
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QDialogButtonBox, QComboBox, QLineEdit,
        )
        clip = item.clip
        order = self._track_order()
        names = self._track_names()

        dlg = QDialog(self)
        dlg.setWindowTitle("Klip displeje — co a odkud se zobrazí")
        form = QFormLayout(dlg)

        src = QComboBox()
        for ti in order:
            src.addItem(f"{ti}. {names.get(ti, f'Stopa {ti}')}", ti)
        cur_ti = clip.get("source_track", order[0] if order else 1)
        src.setCurrentIndex(order.index(cur_ti) if cur_ti in order else 0)

        mode = QComboBox()
        for key in MODE_ORDER:
            mode.addItem(MODE_LABELS[key], key)
        cur_mode = clip.get("mode", "lyrics_chords")
        mode.setCurrentIndex(MODE_ORDER.index(cur_mode) if cur_mode in MODE_ORDER else 0)

        label = QLineEdit(clip.get("label", ""))

        form.addRow("Zdrojová stopa:", src)
        form.addRow("Režim zobrazení:", mode)
        form.addRow("Popisek:", label)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec():
            self._push_undo()
            clip["source_track"] = src.currentData()
            clip["mode"] = mode.currentData()
            clip["label"] = label.text().strip()
            item.update()

    def delete_clip(self, item: DisplayClipItem) -> None:
        try:
            self.data.get("display_timeline", []).remove(item.clip)
        except ValueError:
            pass
        self.scene.removeItem(item)
        if item in self.clips:
            self.clips.remove(item)

    def split_at_playhead(self, item: Optional[DisplayClipItem] = None) -> None:
        """Žiletka: rozdělí klip na dva v pozici kurzoru (playhead)."""
        t = self.playhead_s
        target = item
        if target is None:                      # vybraný klip, jinak klip pod kurzorem
            for it in self.scene.selectedItems():
                if isinstance(it, DisplayClipItem):
                    target = it
                    break
        if target is None:
            for it in self.clips:
                if float(it.clip["start_s"]) < t < float(it.clip["end_s"]):
                    target = it
                    break
        if target is None:
            return
        c = target.clip
        if not (float(c["start_s"]) < t < float(c["end_s"])):
            return   # kurzor musí být uvnitř klipu
        self._push_undo()
        clips = self.data.setdefault("display_timeline", [])
        right = dict(c)
        right["id"] = f"clip-{len(clips) + 1}"
        right["start_s"] = round(t, 3)
        c["end_s"] = round(t, 3)
        clips.append(right)
        self._relayout()

    # --- přichycení na mřížku (jediný correctní způsob časování — dle tempa,
    #     žádné odhady z délky textu/slabik) ---

    def auto_time_dialog(self) -> None:
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QComboBox, QDialogButtonBox, QLabel,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Přichytit na mřížku")
        form = QFormLayout(dlg)
        info = QLabel(
            "Přichytí začátky textových bloků na mřížku podle tempa (beat/takt).\n"
            "Řádky/akordy pak leží přesně na hudební mřížce — žádné odhady."
        )
        info.setStyleSheet("color:#555;")
        form.addRow(info)

        sub = QComboBox()
        for lbl, v in [("1 takt", 0.25), ("1 beat", 1), ("1/2 beatu", 2), ("1/4 beatu", 4)]:
            sub.addItem(lbl, v)
        sub.setCurrentIndex(1)
        form.addRow("Mřížka:", sub)

        bpm = (self.data.get("meta", {}) or {}).get("tempo_bpm", 120) or 120
        form.addRow(QLabel(f"Tempo: {bpm} BPM  →  1 beat = {60.0 / bpm:.3f} s"))

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if not dlg.exec():
            return
        self._push_undo()
        self._auto_time_beat_snap(float(sub.currentData()))
        self._relayout()

    def _auto_time_beat_snap(self, subdiv: float) -> None:
        """Přichytí začátky textových bloků na mřížku beatu (tempo/subdiv) a
        dopočítá délky. `subdiv` = dílků beatu na mřížkovou buňku (0.25 = celý
        takt, 1 = beat, 2 = půl beatu…)."""
        bpm = (self.data.get("meta", {}) or {}).get("tempo_bpm", 120) or 120
        grid = (60.0 / bpm) / max(0.01, subdiv)
        for ti in self._track_order():
            evs = self._lyrics_of(ti)
            for e in evs:
                e["time_s"] = round(round(float(e["time_s"]) / grid) * grid, 3)
            # délka = do začátku dalšího bloku (poslední drží svou)
            for i, e in enumerate(evs):
                if i + 1 < len(evs):
                    nxt = float(evs[i + 1]["time_s"])
                    e["duration_s"] = round(max(0.05, nxt - float(e["time_s"])), 3)

    # --- tempo (BPM) ---

    def bpm_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Změnit tempo (BPM)")
        form = QFormLayout(dlg)

        spin = QSpinBox()
        spin.setRange(20, 300)
        spin.setValue(int(round(self.bpm)))
        form.addRow("Nové BPM:", spin)

        rescale = QCheckBox("Přepočítat existující časy proporcionálně")
        rescale.setChecked(True)
        rescale.setToolTip(
            "Zaškrtnuto: všechny časy (text, akordy, bicí, basa, klipy) se "
            "přepočítají poměrem staré/nové tempo — dosavadní hudební poloha "
            "zůstane zachována.\nOdškrtnuto: jen se změní BPM/mřížka, časy "
            "zůstanou beze změny.")
        form.addRow(rescale)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec():
            self._push_undo()
            self.set_bpm(spin.value(), rescale.isChecked())

    def set_bpm(self, new_bpm: float, rescale: bool) -> None:
        """Změní `self.bpm` a `meta.tempo_bpm`. Když `rescale`, přepočítá
        VŠECHNY existující časy poměrem `old_bpm / new_bpm` (čas je nepřímo
        úměrný tempu — při zrychlení tempa se reálný čas zkrátí)."""
        old_bpm = self.bpm
        if rescale and old_bpm and new_bpm != old_bpm:
            ratio = old_bpm / new_bpm
            for key in ("lyrics_timeline", "chords_timeline",
                       "drums_timeline", "bass_timeline"):
                for ev in self.data.get(key, []):
                    if "time_s" in ev:
                        ev["time_s"] = round(float(ev["time_s"]) * ratio, 3)
                    if "duration_s" in ev:
                        ev["duration_s"] = round(float(ev["duration_s"]) * ratio, 3)
            for kl in self.data.get("karaoke_lines", []):
                kl["start_s"] = round(float(kl.get("start_s", 0.0)) * ratio, 3)
                kl["end_s"] = round(float(kl.get("end_s", 0.0)) * ratio, 3)
                for w in kl.get("words", []):
                    w["time_s"] = round(float(w.get("time_s", 0.0)) * ratio, 3)
                    if "duration_s" in w:
                        w["duration_s"] = round(float(w["duration_s"]) * ratio, 3)
            for c in self.data.get("display_timeline", []):
                c["start_s"] = round(float(c.get("start_s", 0.0)) * ratio, 3)
                c["end_s"] = round(float(c.get("end_s", 0.0)) * ratio, 3)

        self.bpm = new_bpm
        meta = self.data.setdefault("meta", {})
        meta["tempo_bpm"] = new_bpm
        self.beat_s = 60.0 / new_bpm
        self.bar_s = self.beat_s * self.beats_per_measure
        self.default_chord_dur = round(self.beat_s, 3)
        count_in_bars = meta.get("count_in_bars", 0) or 0
        if count_in_bars:
            self.count_in_s = round(count_in_bars * self.beats_per_measure * self.beat_s, 3)
            meta["count_in_s"] = self.count_in_s
        self._update_snap_s()
        self._relayout()

    # --- odpočet (count-in) před písní ---

    def count_in_dialog(self) -> None:
        meta = self.data.get("meta", {}) or {}
        dlg = QDialog(self)
        dlg.setWindowTitle("Odpočet (count-in) před písní")
        form = QFormLayout(dlg)

        info = QLabel(
            "Vloží pár taktů „ťukání“ (zavřená hajtka) před první notu a "
            "automaticky posune VŠECHNY existující stopy/prvky o stejný čas "
            "— nic není třeba ručně přesouvat. Na displeji se v tomto úseku "
            "místo textu/akordů zobrazí interpret + název a odpočet čísel "
            "podle tempa (4, 3, 2, 1…).")
        info.setWordWrap(True)
        info.setStyleSheet("color:#555;")
        form.addRow(info)

        bars_sb = QSpinBox()
        bars_sb.setRange(0, 8)
        bars_sb.setToolTip("0 = odstranit odpočet")
        bars_sb.setValue(int(meta.get("count_in_bars", 0) or 0) or 1)
        form.addRow("Počet taktů:", bars_sb)

        artist_edit = QLineEdit(meta.get("artist", "") or "")
        title_edit = QLineEdit(meta.get("title", "") or "")
        form.addRow("Interpret:", artist_edit)
        form.addRow("Píseň:", title_edit)

        form.addRow(QLabel(f"Tempo: {self.bpm:g} BPM · {self.beats_per_measure}/4  →  "
                           f"1 takt = {self.bar_s:.3f} s"))

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec():
            self._push_undo()
            self.set_count_in(bars_sb.value(), artist_edit.text(), title_edit.text())

    def set_count_in(self, bars: int, artist: str, title: str) -> None:
        """Vloží/upraví odpočet před písní — klik-track (zavřená hajtka) +
        klip displeje s interpretem/názvem (odpočet čísel dopočítá displej
        z `meta.count_in_s`/`count_in_bars`/tempa, žádná další data netřeba).

        Idempotentní: lze zavolat znovu se změněným počtem taktů/textem —
        posune se jen ROZDÍL oproti předchozímu odpočtu, ne celá píseň znovu
        (viz `delta` níže), takže je to bezpečně opakovatelná editační akce."""
        meta = self.data.setdefault("meta", {})
        old_s = float(meta.get("count_in_s", 0.0) or 0.0)
        bars = max(0, int(bars))
        new_s = round(bars * self.beats_per_measure * self.beat_s, 3)
        delta = round(new_s - old_s, 3)

        order = self._track_order()
        drum_ti = next((ti for ti in order if self._track_is_drums(ti)), None)

        # odeber staré klikací údery (byly striktně v [0, old_s) na bicí stopě)
        if drum_ti is not None and old_s > 0:
            self.data["drums_timeline"] = [
                ev for ev in self.data.get("drums_timeline", [])
                if not (ev.get("track_index") == drum_ti and float(ev.get("time_s", 0.0)) < old_s)
            ]

        if delta:
            for key in ("lyrics_timeline", "chords_timeline", "drums_timeline", "bass_timeline"):
                for ev in self.data.get(key, []):
                    if "time_s" in ev:
                        ev["time_s"] = round(max(0.0, float(ev["time_s"]) + delta), 3)
            for kl in self.data.get("karaoke_lines", []):
                kl["start_s"] = round(max(0.0, float(kl.get("start_s", 0.0)) + delta), 3)
                kl["end_s"] = round(max(0.0, float(kl.get("end_s", 0.0)) + delta), 3)
                for w in kl.get("words", []):
                    w["time_s"] = round(max(0.0, float(w.get("time_s", 0.0)) + delta), 3)
            for c in self.data.get("display_timeline", []):
                if c.get("mode") == "count_in":
                    continue   # tenhle klip přestavíme níž od nuly
                c["start_s"] = round(max(0.0, float(c.get("start_s", 0.0)) + delta), 3)
                c["end_s"] = round(max(0.0, float(c.get("end_s", 0.0)) + delta), 3)

        # nové klikací údery na bicí stopu (zavřená hajtka, GM 42)
        if drum_ti is not None and bars > 0:
            clicks = self.data.setdefault("drums_timeline", [])
            for i in range(bars * self.beats_per_measure):
                clicks.append({
                    "time_s": round(i * self.beat_s, 3),
                    "duration_s": round(self.beat_s * 0.3, 3),
                    "drum": "Closed Hi-Hat", "midi": 42, "track_index": drum_ti,
                })
            clicks.sort(key=lambda e: e["time_s"])

        # klip displeje pro odpočet (nahradí případný starý)
        clips = self.data.setdefault("display_timeline", [])
        clips[:] = [c for c in clips if c.get("mode") != "count_in"]
        artist = (artist or "").strip()
        title = (title or "").strip()
        if bars > 0:
            clips.insert(0, {
                "id": "clip-count-in",
                "start_s": 0.0,
                "end_s": new_s,
                "source_track": order[0] if order else 1,
                "mode": "count_in",
                "artist": artist,
                "title": title,
                "label": " / ".join(x for x in (artist, title) if x),
            })
        clips.sort(key=lambda c: float(c.get("start_s", 0.0)))

        meta["count_in_bars"] = bars
        meta["count_in_s"] = new_s
        if artist:
            meta["artist"] = artist
        if title:
            meta["title"] = title

        self.count_in_s = new_s
        self._relayout()

    def delete_selected(self) -> None:
        sel = list(self.scene.selectedItems())
        if not sel:
            return
        self._push_undo()   # jeden krok za celé smazání, i když jde o víc prvků
        for it in sel:
            if isinstance(it, DisplayClipItem):
                self.delete_clip(it)
                continue
            if isinstance(it, DrumHitItem):
                self.remove_drum_hit(it)
                continue
            if not isinstance(it, BlockItem):
                continue
            key = "chords_timeline" if it.kind == "chord" else "lyrics_timeline"
            try:
                self.data.get(key, []).remove(it.event)
            except ValueError:
                pass
            self.scene.removeItem(it)
            if it in self.blocks:
                self.blocks.remove(it)

    # --- hromadný výběr/posun ("ripple") — vyber tenhle prvek a vše
    #     STEJNÉHO druhu na stejné stopě PŘED/PO něm v čase, pak přetáhni
    #     myší (Qt posune celý výběr najednou) nebo použij přesný posun
    #     tlačítkem "↔ Posunout vybrané…" ---

    @staticmethod
    def _item_time(item) -> float:
        if isinstance(item, DisplayClipItem):
            return float(item.clip.get("start_s", 0.0))
        return float(item.event.get("time_s", 0.0))

    def _ripple_candidates(self, item) -> list:
        if isinstance(item, BlockItem):
            ti = item.event.get("track_index", 1)
            return [b for b in self.blocks
                   if b.kind == item.kind and b.event.get("track_index", 1) == ti]
        if isinstance(item, DrumHitItem):
            ti = item.event.get("track_index", 1)
            return [d for d in self.drum_hit_items if d.event.get("track_index", 1) == ti]
        if isinstance(item, DisplayClipItem):
            return list(self.clips)
        return []

    def select_ripple(self, item, direction: str) -> None:
        """Vybere `item` (obsažen, protože t0 splňuje >=/<=) a všechny
        prvky stejného druhu na stejné stopě PŘED (`backward`) nebo PO
        (`forward`) něm v čase — pro hromadný posun zbytku časové osy."""
        t0 = self._item_time(item)
        self.scene.clearSelection()
        for other in self._ripple_candidates(item):
            t = self._item_time(other)
            if (direction == "forward" and t >= t0) or (direction == "backward" and t <= t0):
                other.setSelected(True)

    def select_ripple_from_selection(self, direction: str) -> None:
        """Klávesová zkratka `]`/`[` — použije PRÁVĚ VYBRANÝ prvek jako
        kotvu (musí být vybraný přesně jeden)."""
        sel = self.scene.selectedItems()
        if len(sel) == 1:
            self.select_ripple(sel[0], direction)

    def shift_selected(self, delta_s: float) -> None:
        """Posune VŠECHNY aktuálně vybrané prvky (text/akord/buben/klip) o
        `delta_s` (+ později, − dřív) — přesný, parametrický ekvivalent
        tažení myší, funguje na libovolně velký výběr (viz `select_ripple`)."""
        sel = self.scene.selectedItems()
        if not delta_s or not sel:
            return
        self._push_undo()   # jeden krok za celý hromadný posun
        for it in sel:
            if isinstance(it, (BlockItem, DrumHitItem)):
                it.event["time_s"] = round(max(0.0, float(it.event.get("time_s", 0.0)) + delta_s), 3)
                it._sync_from_event()
            elif isinstance(it, DisplayClipItem):
                dur = max(0.05, float(it.clip.get("end_s", 0.0)) - float(it.clip.get("start_s", 0.0)))
                start = round(max(0.0, float(it.clip.get("start_s", 0.0)) + delta_s), 3)
                it.clip["start_s"] = start
                it.clip["end_s"] = round(start + dur, 3)
                it._sync_from_clip()

    def shift_selected_dialog(self) -> None:
        sel = self.scene.selectedItems()
        if not sel:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Posunout vybrané prvky ({len(sel)})")
        form = QFormLayout(dlg)
        spin = QDoubleSpinBox()
        spin.setRange(-9999.0, 9999.0)
        spin.setDecimals(3)
        spin.setSuffix(" s")
        spin.setValue(0.0)
        form.addRow(f"Posun ({len(sel)}× vybráno, + = později, − = dřív):", spin)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            self.shift_selected(spin.value())

    # ------------------------------------------------------------------
    # Výstup
    # ------------------------------------------------------------------

    def to_json(self) -> dict:
        """Vrátí upravený JSON slovník (setříděné osy + přepočtené karaoke_lines).

        Zobrazovaný rozsah řádku (`karaoke_lines[].start_s/end_s`) může
        uživatel přetáhnout přes odpovídající klip na master "Displej" stopě
        — nezávisle na hudební mřížce (`bars_per_line`). To je potřeba u
        rytmických skladeb, kde má řádek zůstat na displeji déle/kratčeji,
        než odpovídá mřížkovému rozsahu. Klip se s řádkem páruje přes `line`
        (viz `_seed_display`); starší klipy bez tohoto tagu se dohledají
        podle stopy + blízkého startu a tag jim dopíšeme."""
        data = self.data
        lyr = sorted(data.get("lyrics_timeline", []), key=lambda e: e["time_s"])
        chords = sorted(data.get("chords_timeline", []), key=lambda e: e["time_s"])
        data["lyrics_timeline"] = lyr
        data["chords_timeline"] = chords

        grouped: list[tuple[int, list[dict]]] = []
        for ti in self._track_order():
            for line in self._group_lines(ti):
                if line:
                    grouped.append((ti, line))
        grouped.sort(key=lambda g: g[1][0]["time_s"])

        # Pass 1: přiřaď GLOBÁLNÍ index řádku `line` slovům a spočítej
        # přirozený (ze slov odvozený) rozsah — používá se pro přiřazení
        # akordů k řádku (nemění se přetažením zobrazovaného konce).
        line_words: list[list[dict]] = []
        line_ranges: list[tuple[float, float]] = []
        for line_idx, (ti, line) in enumerate(grouped):
            for e in line:
                e["line"] = line_idx
            words = [{"time_s": e["time_s"], "duration_s": e.get("duration_s", 0.5),
                      "text": e.get("text", ""), "line": line_idx, "track_index": ti}
                     for e in line]
            line_words.append(words)
            line_ranges.append((words[0]["time_s"],
                                max(w["time_s"] + w["duration_s"] for w in words)))

        for c in chords:
            t = float(c.get("time_s", 0.0))
            c["line"] = next((li for li, (s, e) in enumerate(line_ranges) if s <= t < e), None)

        # Klipy master "Displej" stopy — spáruj s řádky přes `line`, starší
        # klipy bez tagu dohledej podle stopy a blízkého startu. Klipy s
        # mode="chords" (bezeslovné intro/mezihra) se sem NIKDY nepočítají —
        # `karaoke_lines` z to_json() jsou vždy textové, takže čistě akordový
        # klip nemůže korektně odpovídat žádnému z nich (i kdyby náhodou měl
        # `.line` shodné se STARÝM číslem nějakého textového řádku a byl mu
        # časově blízko — přesně tenhle případ dřív procházel kolem
        # MAX_LINE_CLIP_DRIFT_S kontroly a řádku podstrčil cizí čas).
        display_clips = data.get("display_timeline", []) or []
        TEXT_MODES = ("lyrics_chords", "lyrics")
        clip_by_line: dict[int, dict] = {
            c["line"]: c for c in display_clips
            if isinstance(c.get("line"), int) and c.get("mode") in TEXT_MODES
        }
        unlinked = [c for c in display_clips
                    if not isinstance(c.get("line"), int) and c.get("mode") in TEXT_MODES]

        def _match_unlinked(ti: int, start: float):
            for c in unlinked:
                if c.get("source_track") == ti and abs(float(c.get("start_s", -999)) - start) < 0.75:
                    return c
            return None

        # Kolik smí být klip vzdálený od přirozeného startu řádku, aby se
        # ještě bral jako "tenhle řádek, uživatel jen posunul okraj" — nad
        # tím jde o STARÝ/cizí tag ze zcela jiného čísla řádku (viz níže).
        MAX_LINE_CLIP_DRIFT_S = 6.0

        karaoke: list[dict] = []
        for line_idx, (ti, _line) in enumerate(grouped):
            words = line_words[line_idx]
            start, end = line_ranges[line_idx]

            clip = clip_by_line.get(line_idx)
            if clip is not None and abs(float(clip.get("start_s", start)) - start) > MAX_LINE_CLIP_DRIFT_S:
                # `line_idx` číslování je čerstvě přepočítané z `lyrics_timeline`
                # (řádky beze slov — např. jen akordy — se do něj vůbec
                # nepočítají). Klip svůj `line` tag dostal DŘÍV, v jiném
                # číslování (mohlo zahrnovat i bezeslovné řádky) — pro jiný
                # počet takových řádků PŘED touto pozicí se čísla rozejdou a
                # `clip_by_line[line_idx]` pak omylem trefí klip NĚJAKÉHO
                # jiného řádku (o desítky sekund jinde). Zahodit, dohledat
                # znovu podle stopy + skutečného času (self-healing níže).
                clip = None
            if clip is None:
                clip = _match_unlinked(ti, start)
                if clip is not None:
                    clip["line"] = line_idx
                    unlinked.remove(clip)

            disp_start, disp_end = start, end
            if clip is not None:
                disp_start = float(clip.get("start_s", start))
                disp_end = max(float(clip.get("end_s", end)), disp_start + 0.05)

            line_chords: list[str] = []
            for c in chords:
                if c.get("line") == line_idx and c.get("chord") not in line_chords:
                    line_chords.append(c["chord"])

            text = " ".join(w["text"] for w in words)
            kl = {
                "line": line_idx,
                "start_s": round(disp_start, 3),
                "end_s": round(disp_end, 3),
                "chords": line_chords,
                "text": text,
                "words": words,
                "track_index": ti,
            }
            sec = _section_of(text)
            if sec:
                kl["section"] = sec
            karaoke.append(kl)
        data["karaoke_lines"] = karaoke

        # master "Displej" stopa (co a kdy uvidí karaoke) — setříděné dle času
        data["display_timeline"] = sorted(display_clips, key=lambda c: float(c.get("start_s", 0.0)))
        meta = data.setdefault("meta", {})
        meta["edited_in_timeline"] = True
        meta["has_line_structure"] = True   # slova nesou index řádku `line`
        return data
