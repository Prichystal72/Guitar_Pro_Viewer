"""
timeline_editor.py — Vizuální editor časové osy (DAW-styl) pro karaoke.

Zobrazí stopy jako vodorovné pruhy, v nich bloky TEXTU (slabiky) a AKORDŮ
umístěné na časové ose. Bloky lze:
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
        for label, val in [("vypnuto", 0.0), ("0,1 s", 0.1), ("0,25 s", 0.25),
                           ("0,5 s", 0.5), ("1 s", 1.0)]:
            self.snap_combo.addItem(label, val)
        self.snap_combo.currentIndexChanged.connect(
            lambda: setattr(self, "snap_s", self.snap_combo.currentData()))
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
        autotime_btn = QPushButton("⏱ Auto-časování")
        autotime_btn.setToolTip("Automaticky přerovná časy slabik "
                                "(rovnoměrně v řádcích / do oken / na beat)")
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
        # základní délka akordu ~ podle tempa
        bpm = (self.data.get("meta", {}) or {}).get("tempo_bpm", 120) or 120
        self.default_chord_dur = round(60.0 / bpm, 3)
        self._seed_line_starts()
        self._seed_display()
        self._relayout(full=True)

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
        """Poprvé sestaví klipy master stopy: řádky zpěvu (text+akordy) a sóla
        (tab+akordy). Když už display_timeline existuje, nechá ho být."""
        data = self.data
        if data.get("display_timeline"):
            return
        clips: list[dict] = []
        n = 0

        # A) EXPLICITNÍ řádky (web import) → klip na každý karaoke řádek 1:1.
        #    Řádek bez slov (intro / mezihra) = klip „jen akordy".
        meta = data.get("meta", {}) or {}
        klines = data.get("karaoke_lines") or []
        explicit = bool(meta.get("has_line_structure")) or any("chords" in l for l in klines)
        if explicit and klines:
            for l in klines:
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
                })
            clips.sort(key=lambda c: c["start_s"])
            data["display_timeline"] = clips
            return

        solo_idx = {t.get("index") for t in self.tracks if t.get("type") == "solo_guitar"}
        # jedno "okno" (klip) na každý karaoke řádek zpěvních stop
        for ti in self._track_order():
            if ti in solo_idx:
                continue
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
        # sólo stopy → tab + akordy přes svůj rozsah
        for t in self.tracks:
            if t.get("type") != "solo_guitar":
                continue
            ti = t.get("index")
            times = [float(b["time_s"]) for b in (t.get("beats") or []) if b.get("time_s") is not None]
            if not times:
                times = [float(e["time_s"]) for e in data.get("chords_timeline", [])
                         if e.get("track_index") == ti]
            if not times:
                continue
            n += 1
            clips.append({
                "id": f"clip-{n}",
                "start_s": round(min(times), 3),
                "end_s": round(max(times) + 2.0, 3),
                "source_track": ti,
                "mode": "tab_chords",
                "label": (t.get("name") or "Sólo")[:24],
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
        total_h = RULER_H + DISPLAY_ROW_H + len(order) * PER_TRACK + 20
        total_w = HEADER_W + max_t * self.pps + 200
        self.scene.setSceneRect(0, 0, total_w, total_h)

        # pozadí pruhů + názvy + pravítko
        self._draw_ruler(max_t, total_h)

        # master "Displej" stopa navrchu
        self._display_lane_y = RULER_H + 4
        self._draw_display_bg(total_w)
        for clip in self.data.get("display_timeline", []):
            self._add_clip_item(clip)

        # zdrojové stopy (posunuté pod master stopu)
        base_top = RULER_H + DISPLAY_ROW_H
        for row, ti in enumerate(order):
            top = base_top + row * PER_TRACK
            line_y = top + 2
            chord_y = line_y + LINE_H
            lyric_y = chord_y + CHORD_H
            self._track_lane[ti] = {"line_y": line_y, "chord_y": chord_y, "lyric_y": lyric_y}
            self._draw_lane_bg(ti, top, names.get(ti, f"Stopa {ti}"), total_w)

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
        pen = QPen(QColor("#cccccc"))
        thin = QPen(QColor("#eeeeee"))
        self.scene.addRect(0, 0, HEADER_W + max_t * self.pps + 200, RULER_H,
                           QPen(Qt.NoPen), QBrush(QColor("#f5f5f5")))
        step = 1.0
        if self.pps < 25:
            step = 5.0
        elif self.pps < 50:
            step = 2.0
        t = 0.0
        while t <= max_t + step:
            x = HEADER_W + t * self.pps
            self.scene.addLine(x, 0, x, total_h, thin if (t % (step * 5)) else pen)
            lbl = self.scene.addSimpleText(f"{t:g}s")
            lbl.setPos(x + 2, 6)
            lbl.setBrush(QBrush(QColor("#888")))
            t += step
        # oddělovač hlavičky
        self.scene.addLine(HEADER_W, 0, HEADER_W, total_h, pen)

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
            text, ok = QInputDialog.getText(self, "Editace textu", "Slabika / slovo:",
                                            text=block.event.get("text", ""))
            if ok:
                block.event["text"] = text.strip()
        block.update()

    def add_block(self, kind: str) -> None:
        order = self._track_order()
        ti = order[0] if order else 1
        if kind == "chord":
            ev = {"time_s": 0.0, "duration_s": self.default_chord_dur,
                  "chord": "C", "measure": 1, "tick": 960, "track_index": ti}
            self.data.setdefault("chords_timeline", []).append(ev)
        else:
            ev = {"time_s": 0.0, "duration_s": 0.5, "text": "text",
                  "measure": 1, "tick": 960, "track_index": ti}
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

    # --- automatické časování slabik ---

    def auto_time_dialog(self) -> None:
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QComboBox, QDialogButtonBox, QLabel,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Automatické časování slabik")
        form = QFormLayout(dlg)
        info = QLabel(
            "Přerovná časy slabik podle zvolené strategie — orientace dle slabik.\n"
            "Tip: nejdřív nastav délky „oken\" (klipů Displeje), pak zvol\n"
            "„Rozprostřít do oken\"."
        )
        info.setStyleSheet("color:#555;")
        form.addRow(info)

        mode = QComboBox()
        mode.addItem("Rovnoměrně v řádcích (dle délky slabik)", "line_even")
        mode.addItem("Rozprostřít do oken Displeje (klipů)", "fit_clips")
        mode.addItem("Přichytit slabiky na beat (tempo)", "beat_snap")
        form.addRow("Strategie:", mode)

        sub = QComboBox()
        for lbl, v in [("1 beat", 1), ("1/2 beatu", 2), ("1/4 beatu", 4)]:
            sub.addItem(lbl, v)
        sub.setCurrentIndex(1)
        form.addRow("Mřížka (jen „na beat\"):", sub)

        bpm = (self.data.get("meta", {}) or {}).get("tempo_bpm", 120) or 120
        form.addRow(QLabel(f"Tempo: {bpm} BPM  →  1 beat = {60.0 / bpm:.3f} s"))

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if not dlg.exec():
            return
        m = mode.currentData()
        if m == "line_even":
            self._auto_time_line_even()
        elif m == "fit_clips":
            self._auto_time_fit_clips()
        else:
            self._auto_time_beat_snap(int(sub.currentData()))
        self._relayout()

    @staticmethod
    def _syllable_weight(e: dict) -> int:
        """Váha slabiky ~ počet znaků (delší slova znějí déle). Min. 1."""
        return max(1, len((e.get("text") or "").strip()))

    def _distribute(self, evs: list[dict], t0: float, t1: float) -> None:
        """Rozprostře slabiky evs do intervalu [t0, t1] vážené délkou textu."""
        if not evs:
            return
        span = max(0.1, t1 - t0)
        if len(evs) == 1:
            evs[0]["time_s"] = round(t0, 3)
            evs[0]["duration_s"] = round(span, 3)
            return
        weights = [self._syllable_weight(e) for e in evs]
        total = sum(weights)
        acc = t0
        for e, w in zip(evs, weights):
            d = span * w / total
            e["time_s"] = round(acc, 3)
            e["duration_s"] = round(d, 3)
            acc += d

    def _auto_time_line_even(self) -> None:
        """Každý karaoke řádek: slabiky rovnoměrně (dle délky) přes jeho rozsah."""
        for ti in self._track_order():
            for line in self._group_lines(ti):
                if len(line) < 2:
                    continue
                t0 = float(line[0]["time_s"])
                t1 = max(float(e["time_s"]) + float(e.get("duration_s", 0.5)) for e in line)
                self._distribute(line, t0, t1)

    def _auto_time_fit_clips(self) -> None:
        """Slabiky zdrojové stopy nacpe do časového okna každého klipu Displeje."""
        for c in self.data.get("display_timeline", []):
            if c.get("mode") in ("tab", "chords"):
                continue
            ti = c.get("source_track")
            start, end = float(c["start_s"]), float(c["end_s"])
            evs = [e for e in self._lyrics_of(ti)
                   if start <= float(e["time_s"]) < end]
            if evs:
                self._distribute(evs, start, end)

    def _auto_time_beat_snap(self, subdiv: int) -> None:
        """Přichytí začátky slabik na mřížku beatu (tempo/subdiv) a dopočítá délky."""
        bpm = (self.data.get("meta", {}) or {}).get("tempo_bpm", 120) or 120
        grid = (60.0 / bpm) / max(1, subdiv)
        for ti in self._track_order():
            evs = self._lyrics_of(ti)
            for e in evs:
                e["time_s"] = round(round(float(e["time_s"]) / grid) * grid, 3)
            # délka = do začátku další slabiky (poslední drží svou)
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
        """Vrátí upravený JSON slovník (setříděné osy + přepočtené karaoke_lines)."""
        data = self.data
        lyr = sorted(data.get("lyrics_timeline", []), key=lambda e: e["time_s"])
        chords = sorted(data.get("chords_timeline", []), key=lambda e: e["time_s"])
        data["lyrics_timeline"] = lyr
        data["chords_timeline"] = chords
        # karaoke řádky dle uživatelských zalomení (line_start), po stopách.
        # Přidáme GLOBÁLNÍ index řádku `line` na řádek, slova i lyrics_timeline —
        # aby seskupení do řádků bylo v JSONu explicitní (ne jen v karaoke_lines).
        grouped: list[tuple[int, list[dict]]] = []
        for ti in self._track_order():
            for line in self._group_lines(ti):
                if line:
                    grouped.append((ti, line))
        grouped.sort(key=lambda g: g[1][0]["time_s"])

        karaoke: list[dict] = []
        line_ranges: list[tuple[float, float, int]] = []
        for line_idx, (ti, line) in enumerate(grouped):
            for e in line:
                e["line"] = line_idx           # obtaguj skutečné lyrics_timeline eventy
            words = [{"time_s": e["time_s"], "duration_s": e.get("duration_s", 0.5),
                      "text": e.get("text", ""), "line": line_idx, "track_index": ti}
                     for e in line]
            start = words[0]["time_s"]
            end = max(w["time_s"] + w["duration_s"] for w in words)
            line_ranges.append((start, end, line_idx))
            karaoke.append({
                "line": line_idx,
                "start_s": start,
                "end_s": end,
                "track_index": ti,
                "text": " ".join(w["text"] for w in words),
                "words": words,
            })
        data["karaoke_lines"] = karaoke

        # akordy dostanou index řádku podle časového překryvu s řádkem
        for c in chords:
            t = float(c.get("time_s", 0.0))
            c["line"] = next((li for s, e, li in line_ranges if s <= t < e), None)

        # master "Displej" stopa (co a kdy uvidí karaoke) — setříděné dle času
        display = sorted(data.get("display_timeline", []),
                         key=lambda c: float(c.get("start_s", 0.0)))
        data["display_timeline"] = display
        meta = data.setdefault("meta", {})
        meta["edited_in_timeline"] = True
        meta["has_line_structure"] = True   # slova nesou index řádku `line`
        return data
