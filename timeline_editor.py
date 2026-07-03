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
CHORD_H = 26        # výška pruhu akordů v rámci stopy
LYRIC_H = 30        # výška pruhu textu v rámci stopy
TRACK_GAP = 10
PER_TRACK = CHORD_H + LYRIC_H + TRACK_GAP
BLOCK_MIN_W = 14
EDGE = 6            # zóna u pravého okraje pro resize

CHORD_COLOR = QColor("#1a5fb4")
LYRIC_COLOR = QColor("#2d7d2d")
SEL_COLOR = QColor("#e5a50a")


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

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.editor.delete_selected()
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
        self.blocks: list[BlockItem] = []
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
        self.info_lbl = QLabel("Dvojklik = editace · táhni okraj = délka · Ctrl+kolečko = zoom")
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
        self._relayout(full=True)

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
        self._track_lane.clear()

        order = self._track_order()
        names = {t.get("index", i + 1): t.get("name", f"Stopa {i+1}")
                 for i, t in enumerate(self.tracks)}

        # celková délka pro šířku scény
        max_t = 1.0
        for ev in self.data.get("lyrics_timeline", []) + self.data.get("chords_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 1)))
        total_h = RULER_H + len(order) * PER_TRACK + 20
        total_w = HEADER_W + max_t * self.pps + 200
        self.scene.setSceneRect(0, 0, total_w, total_h)

        # pozadí pruhů + názvy + pravítko
        self._draw_ruler(max_t, total_h)
        for row, ti in enumerate(order):
            top = RULER_H + row * PER_TRACK
            chord_y = top + 2
            lyric_y = top + 2 + CHORD_H
            self._track_lane[ti] = {"chord_y": chord_y, "lyric_y": lyric_y}
            self._draw_lane_bg(ti, top, names.get(ti, f"Stopa {ti}"), total_w)

        # bloky
        for ev in self.data.get("chords_timeline", []):
            self._add_block_item(ev, "chord")
        for ev in self.data.get("lyrics_timeline", []):
            self._add_block_item(ev, "lyric")

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
        # pruh akordů (světle modrá) + text (světle zelená)
        self.scene.addRect(HEADER_W, top + 2, total_w - HEADER_W, CHORD_H,
                           QPen(Qt.NoPen), QBrush(QColor("#eef3fb")))
        self.scene.addRect(HEADER_W, top + 2 + CHORD_H, total_w - HEADER_W, LYRIC_H,
                           QPen(Qt.NoPen), QBrush(QColor("#eef7ee")))
        # hlavička
        self.scene.addRect(0, top, HEADER_W, PER_TRACK - TRACK_GAP + 2,
                           QPen(QColor("#dddddd")), QBrush(QColor("#fafafa")))
        nm = self.scene.addSimpleText(name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 4)
        a = self.scene.addSimpleText("akordy")
        a.setPos(10, top + 2 + 4); a.setBrush(QBrush(QColor("#1a5fb4")))
        l = self.scene.addSimpleText("text")
        l.setPos(10, top + 2 + CHORD_H + 6); l.setBrush(QBrush(QColor("#2d7d2d")))

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

    def delete_selected(self) -> None:
        for block in list(self.scene.selectedItems()):
            if not isinstance(block, BlockItem):
                continue
            key = "chords_timeline" if block.kind == "chord" else "lyrics_timeline"
            try:
                self.data.get(key, []).remove(block.event)
            except ValueError:
                pass
            self.scene.removeItem(block)
            if block in self.blocks:
                self.blocks.remove(block)

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
        data["karaoke_lines"] = self._rebuild_karaoke_lines(lyr)
        meta = data.setdefault("meta", {})
        meta["edited_in_timeline"] = True
        return data

    @staticmethod
    def _rebuild_karaoke_lines(lyr: list[dict], gap: float = 2.0) -> list[dict]:
        lines: list[dict] = []
        cur: list[dict] = []
        prev_end = 0.0
        for ev in lyr:
            if cur and (ev["time_s"] - prev_end) > gap:
                lines.append({"start_s": cur[0]["time_s"], "end_s": prev_end, "words": cur})
                cur = []
            cur.append({"time_s": ev["time_s"], "duration_s": ev.get("duration_s", 0.5),
                        "text": ev.get("text", ""), "track_index": ev.get("track_index", 1)})
            prev_end = ev["time_s"] + ev.get("duration_s", 0.5)
        if cur:
            lines.append({"start_s": cur[0]["time_s"], "end_s": prev_end, "words": cur})
        return lines
