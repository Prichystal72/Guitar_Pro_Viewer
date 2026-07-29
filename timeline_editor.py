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

from typing import Any, Optional

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QBrush, QPen, QFont, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsItem,
    QInputDialog, QCheckBox,
)

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
}
MODE_ORDER = ["lyrics_chords", "lyrics", "chords", "tab", "tab_chords"]
MODE_COLORS = {
    "lyrics_chords": QColor("#9141ac"),
    "lyrics": QColor("#2d7d2d"),
    "chords": QColor("#1a5fb4"),
    "tab": QColor("#c64600"),
    "tab_chords": QColor("#a51d2d"),
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
        self._sync()

    def _sync(self) -> None:
        self.setPos(HEADER_W + self.editor.playhead_s * self.editor.pps, 0)

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
            self.editor.playhead_s = t
            self.editor._update_playhead_label()
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
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(12)
        self.setToolTip(
            "Táhni okraj = posuň ZAČÁTEK/KONEC řádku na displeji "
            "(nezávisle na hudební mřížce taktů/beatů). "
            "Táhni střed = posuň celý klip po ose."
        )
        self._sync_from_clip()

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

    def mouseMoveEvent(self, ev):
        pps = self.editor.pps
        if self._resizing == "right":
            w = max(BLOCK_MIN_W, ev.pos().x())
            self.prepareGeometryChange()
            self.setRect(0, 0, w, self.h)
            start = float(self.clip.get("start_s", 0.0))
            self.clip["end_s"] = round(start + w / pps, 3)
            ev.accept()
            return
        if self._resizing == "left":
            old_left = self.pos().x()
            right = old_left + self.rect().width()
            new_left = old_left + ev.pos().x()
            new_left = max(HEADER_W, min(new_left, right - BLOCK_MIN_W))
            w = right - new_left
            self.setPos(new_left, self.lane_y)
            self.prepareGeometryChange()
            self.setRect(0, 0, w, self.h)
            self.clip["start_s"] = round((new_left - HEADER_W) / pps, 3)
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
        del_act = menu.addAction("🗑 Smazat klip")
        chosen = menu.exec(ev.screenPos())
        if chosen is None:
            ev.accept()
            return
        if chosen is del_act:
            self.editor.delete_clip(self)
        elif chosen is edit_act:
            self.editor.edit_clip(self)
        elif chosen is split_act:
            self.editor.split_at_playhead(self)
        elif chosen.data() in MODE_LABELS:
            self.clip["mode"] = chosen.data()
            self.update()
        ev.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            new: QPointF = value
            if self._resizing:
                return QPointF(new.x(), self.lane_y)   # při resize jen zamkni Y
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
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self._sync_from_event()

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
        if ev.button() == Qt.LeftButton and ev.pos().x() >= self.rect().width() - EDGE:
            self._resizing = True
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._resizing:
            w = max(BLOCK_MIN_W, ev.pos().x())
            self.prepareGeometryChange()
            self.setRect(0, 0, w, self.h)
            self.event["duration_s"] = round(w / self.editor.pps, 3)
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
        if self.kind != "lyric":
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        first = self.editor.first_word(self.event.get("track_index", 1))
        is_break = bool(self.event.get("line_start")) and self.event is not first
        if is_break:
            act = menu.addAction("⤺ Zrušit zalomení (spojit s předchozím řádkem)")
        else:
            act = menu.addAction("➤ Začít zde nový řádek")
        chosen = menu.exec(ev.screenPos())
        if chosen is act and self.event is not first:
            self.event["line_start"] = not is_break
            self.editor._relayout()
        ev.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            # zamkni Y na pruh, clampni a přichytni X
            new: QPointF = value
            x = max(HEADER_W, new.x())
            t = (x - HEADER_W) / self.editor.pps
            t = self.editor.snap_time(t)
            x = HEADER_W + t * self.editor.pps
            self.event["time_s"] = round(t, 3)
            return QPointF(x, self.lane_y)
        return super().itemChange(change, value)


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
                self.start_event["line_start"] = False
                target["line_start"] = True
        self.editor._relayout()


class TimelineView(QGraphicsView):
    def __init__(self, scene, editor):
        super().__init__(scene)
        self.editor = editor
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)

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
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.editor.delete_selected()
            ev.accept()
            return
        if ev.key() == Qt.Key_S:              # žiletka — rozdělit klip v kurzoru
            self.editor.split_at_playhead()
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
        self._display_lane_y: float = RULER_H + 4
        self._track_lane: dict[int, dict] = {}   # track_index → {'chord_y','lyric_y'}
        self.export_callback = None              # nastaví hlavní okno: fn(dict)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Zoom:"))
        btn_out = QPushButton("−"); btn_out.setMaximumWidth(32)
        btn_in = QPushButton("+"); btn_in.setMaximumWidth(32)
        btn_out.clicked.connect(lambda: self.zoom(1 / 1.25))
        btn_in.clicked.connect(lambda: self.zoom(1.25))
        bar.addWidget(btn_out); bar.addWidget(btn_in)

        bar.addSpacing(12)
        bar.addWidget(QLabel("Přichytit:"))
        self.snap_combo = QComboBox()
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

        bar.addSpacing(12)
        self.time_lbl = QLabel("⏱ 0,00 s")
        self.time_lbl.setStyleSheet(
            "color:#e01b24; font-weight:bold; font-family:Consolas; "
            "padding:2px 6px; border:1px solid #e0a0a0; border-radius:3px;")
        self.time_lbl.setToolTip("Pozice kurzoru — klikni do pravítka nebo táhni červený kurzor")
        bar.addWidget(self.time_lbl)

        bar.addSpacing(12)
        add_clip = QPushButton("＋ Klip displeje")
        add_clip.setStyleSheet(
            "QPushButton{background:#9141ac;color:white;padding:5px 10px;"
            "border-radius:4px;font-weight:bold;}QPushButton:hover{background:#a55bbf;}")
        add_clip.clicked.connect(self.add_clip)
        split_btn = QPushButton("✂ Rozdělit (S)")
        split_btn.setToolTip("Rozdělí vybraný klip v pozici kurzoru (klávesa S)")
        split_btn.clicked.connect(lambda: self.split_at_playhead())
        autotime_btn = QPushButton("⏱ Na mřížku")
        autotime_btn.setToolTip("Přichytí začátky bloků na hudební mřížku "
                                "(takt/beat podle tempa) — žádné odhady.")
        autotime_btn.clicked.connect(self.auto_time_dialog)
        bar.addWidget(add_clip)
        bar.addWidget(split_btn)
        bar.addWidget(autotime_btn)
        add_lyric = QPushButton("＋ Text")
        add_lyric.clicked.connect(lambda: self.add_block("lyric"))
        add_chord = QPushButton("＋ Akord")
        add_chord.clicked.connect(lambda: self.add_block("chord"))
        del_btn = QPushButton("🗑 Smazat")
        del_btn.clicked.connect(self.delete_selected)
        bar.addWidget(add_lyric); bar.addWidget(add_chord); bar.addWidget(del_btn)

        bar.addSpacing(12)
        exp_btn = QPushButton("💾 Export JSON")
        exp_btn.setStyleSheet(
            "QPushButton{background:#2d7d2d;color:white;padding:5px 12px;"
            "border-radius:4px;font-weight:bold;}QPushButton:hover{background:#3a9e3a;}")
        exp_btn.clicked.connect(self._do_export)
        bar.addWidget(exp_btn)

        bar.addStretch()
        self.info_lbl = QLabel("Displej = master stopa (co uvidí karaoke) · dvojklik klip = zdroj+režim · "
                               "táhni okraje = délka · pravý klik = režim/smazat · Ctrl+kolečko = zoom")
        self.info_lbl.setStyleSheet("color:#777;")
        bar.addWidget(self.info_lbl)
        root.addLayout(bar)

        self.scene = QGraphicsScene(self)
        self.view = TimelineView(self.scene, self)
        root.addWidget(self.view)

    # ------------------------------------------------------------------
    # Načtení dat + rozvržení
    # ------------------------------------------------------------------

    def snap_time(self, t: float) -> float:
        if self.snap_s and self.snap_s > 0:
            return round(t / self.snap_s) * self.snap_s
        return max(0.0, t)

    def load_data(self, data: dict) -> None:
        self.data = data or {}
        self.tracks = self.data.get("tracks", []) or []
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
        # RŮZNÝCH bubnů (min. výška řádku, ať se popisky nemačkají)
        DRUM_ROW_MIN_H = 20.0
        lane_h: dict[int, float] = {}
        for ti in order:
            if self._track_is_drums(ti):
                lane_h[ti] = max(PER_TRACK,
                                 8 + self._drum_row_count(ti) * DRUM_ROW_MIN_H + TRACK_GAP)
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

    def set_playhead(self, t: float) -> None:
        self.playhead_s = max(0.0, round(t, 3))
        if self._playhead is not None:
            self._playhead._sync()
        self._update_playhead_label()

    def _update_playhead_label(self) -> None:
        if hasattr(self, "time_lbl"):
            self.time_lbl.setText(f"⏱ {self.playhead_s:.2f} s".replace(".", ","))

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

    def _draw_drums_lane(self, ti: int, top: float, name: str, total_w: float,
                         h_slot: float | None = None) -> None:
        """Vykreslí stopu bicích: KAŽDÝ konkrétní buben má vlastní popsaný
        řádek (ne sdílenou kategorii) — úhozy jsou plné "note-head" tečky, ne
        tenké čárky, takže se dá při hustším rytmu pořád rozeznat, kdy který
        buben hraje. Jen zobrazení."""
        h = (h_slot if h_slot is not None else PER_TRACK) - TRACK_GAP
        y = top + 2
        w = total_w - HEADER_W
        self.scene.addRect(HEADER_W, y, w, h - 2,
                           QPen(Qt.NoPen), QBrush(QColor("#fff4e6")))
        # hlavička
        self.scene.addRect(0, top, HEADER_W, h + 2,
                           QPen(QColor("#e6c9a3")), QBrush(QColor("#fbf0e0")))
        nm = self.scene.addSimpleText("🥁 " + name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        nm.setBrush(QBrush(QColor("#b56b1e")))

        drum_names = self._drum_names_for(ti)
        n_rows = max(1, len(drum_names))
        rows_h = (h - 8) / n_rows
        row_of = {dn: i for i, dn in enumerate(drum_names)}
        colors = {dn: self._drum_family(dn)[1] for dn in drum_names}

        for i, dn in enumerate(drum_names):
            ry = y + 4 + i * rows_h
            it = self.scene.addSimpleText(dn)
            it.setFont(QFont("Segoe UI", 7))
            it.setPos(12, ry + rows_h / 2 - 6)
            it.setBrush(QBrush(colors[dn].darker(140)))
            # jemná vodicí linka řady (skrz střed)
            self.scene.addLine(HEADER_W, ry + rows_h / 2, HEADER_W + w, ry + rows_h / 2,
                               QPen(QColor("#f0e0cc"), 1))
            # oddělovač mezi řádky
            if i > 0:
                self.scene.addLine(HEADER_W, ry, HEADER_W + w, ry, QPen(QColor("#f7e6cc"), 1))

        r_dot = max(2.0, min(5.0, rows_h / 2 - 2))
        for ev in self.data.get("drums_timeline", []):
            if ev.get("track_index") != ti:
                continue
            dn = ev.get("drum", "?")
            i = row_of.get(dn, 0)
            col = colors.get(dn, QColor("#888888"))
            x = HEADER_W + float(ev.get("time_s", 0)) * self.pps
            ry = y + 4 + i * rows_h + rows_h / 2
            self.scene.addEllipse(x - r_dot, ry - r_dot, r_dot * 2, r_dot * 2,
                                  QPen(col.darker(130), 1), QBrush(col))

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
        if callable(self.export_callback):
            self.export_callback(self.to_json())

    def edit_block(self, block: BlockItem) -> None:
        if block.kind == "chord":
            text, ok = QInputDialog.getText(self, "Editace akordu", "Akord:",
                                            text=block.event.get("chord", ""))
            if ok:
                block.event["chord"] = text.strip()
        else:
            text, ok = QInputDialog.getText(self, "Editace textu", "Text řádku:",
                                            text=block.event.get("text", ""))
            if ok:
                block.event["text"] = text.strip()
        block.update()

    def add_block(self, kind: str) -> None:
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

    def delete_selected(self) -> None:
        for it in list(self.scene.selectedItems()):
            if isinstance(it, DisplayClipItem):
                self.delete_clip(it)
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
        # klipy bez tagu dohledej podle stopy a blízkého startu.
        display_clips = data.get("display_timeline", []) or []
        clip_by_line: dict[int, dict] = {
            c["line"]: c for c in display_clips if isinstance(c.get("line"), int)
        }
        unlinked = [c for c in display_clips
                    if not isinstance(c.get("line"), int)
                    and c.get("mode") in ("lyrics_chords", "lyrics")]

        def _match_unlinked(ti: int, start: float):
            for c in unlinked:
                if c.get("source_track") == ti and abs(float(c.get("start_s", -999)) - start) < 0.75:
                    return c
            return None

        karaoke: list[dict] = []
        for line_idx, (ti, _line) in enumerate(grouped):
            words = line_words[line_idx]
            start, end = line_ranges[line_idx]

            clip = clip_by_line.get(line_idx)
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
