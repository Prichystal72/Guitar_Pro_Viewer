"""jog_shuttle.py — kulatý shuttle/jog ovladač (jako u starých Sony/JVC
kamer): uprostřed Play/Pauza/Stop, okolo otočné mezikruží pro přetáčení
vpřed/vzad různou rychlostí podle úhlu natočení. Po puštění myši mezikruží
"pruží" zpět na střed (rychlost okamžitě spadne na 0).

Samostatný modul — stejná komponenta má i webovou obdobu ve
web/jog_shuttle.html (pro pozdější webserver ESP32 hardwaru).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

INNER_R = 32.0      # poloměr vnitřního kruhu (Play/Pauza/Stop)
OUTER_R = 56.0       # vnější poloměr mezikruží (shuttle)
MAX_ANGLE = 120.0    # ± stupňů, na které lze mezikruží natočit
WIDGET_SIZE = 128

RING_BG = QColor("#e6e6e6")
RING_FWD = QColor("#1a5fb4")     # doprava/vpřed = modrá
RING_REV = QColor("#e08a00")     # doleva/vzad = oranžová
BTN_BG = QColor("#fafafa")
BTN_ACTIVE = QColor("#2d7d2d")
BTN_BORDER = QColor("#999999")


class JogShuttleWidget(QWidget):
    """Signály:
    - playRequested / pauseRequested / stopRequested — klik na vnitřní tlačítko.
    - shuttleChanged(float) — −1..+1 (0 = klid), průběžně během tažení
      mezikruží; při puštění se ihned pošle 0.0 (pružinový návrat).
    """

    playRequested = Signal()
    pauseRequested = Signal()
    stopRequested = Signal()
    shuttleChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(WIDGET_SIZE, WIDGET_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(
            "Táhni mezikruží = přetáčení (doprava = zvuk zrychlený, "
            "doleva = tichý posun zpět). Uprostřed: ▶ přehrát, ⏸ pauza, ⏹ stop."
        )
        self._angle = 0.0            # aktuální (zobrazovaný) úhel, stupně
        self._dragging = False
        self._is_playing = False
        self._btn_rects: dict[str, QRectF] = {}

        self._spring_timer = QTimer(self)
        self._spring_timer.setInterval(15)
        self._spring_timer.timeout.connect(self._spring_tick)

    # --- veřejné API pro editor (zvýraznění aktuálního stavu) ---
    def set_playing(self, playing: bool) -> None:
        self._is_playing = playing
        self.update()

    # --- geometrie ---
    def _center(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height()).center()

    def _angle_of(self, pos) -> float:
        """Úhel pozice myši od středu; 0° = nahoře (12h), + = doprava (CW)."""
        c = self._center()
        dx = pos.x() - c.x()
        dy = pos.y() - c.y()
        return math.degrees(math.atan2(dx, -dy))

    def _dist_of(self, pos) -> float:
        c = self._center()
        return math.hypot(pos.x() - c.x(), pos.y() - c.y())

    # --- vykreslení ---
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self._center()

        # mezikruží — pozadí
        ring_rect = QRectF(c.x() - OUTER_R, c.y() - OUTER_R, OUTER_R * 2, OUTER_R * 2)
        p.setPen(QPen(QColor("#cccccc"), 1))
        p.setBrush(QBrush(RING_BG))
        p.drawEllipse(ring_rect)

        # mezikruží — oblouk aktuálního natočení (od 0° po _angle)
        if abs(self._angle) > 0.5:
            color = RING_FWD if self._angle > 0 else RING_REV
            p.setPen(QPen(color, 10, cap=Qt.RoundCap))
            span = self._angle
            # QPainter úhly: 0° = 3h, kladné = proti směru hod. ruč. -> převod:
            start_qt = 90 - 0            # 12h ve stupních QPainteru
            span_qt = -span              # náš CW+ odpovídá QPainter záporně
            p.drawArc(ring_rect.adjusted(6, 6, -6, -6), int(start_qt * 16), int(span_qt * 16))

        # ukazatel (bod) na okraji mezikruží
        rad = math.radians(self._angle)
        px = c.x() + (OUTER_R - 6) * math.sin(rad)
        py = c.y() - (OUTER_R - 6) * math.cos(rad)
        p.setPen(QPen(QColor("#555555"), 1))
        p.setBrush(QBrush(RING_FWD if self._angle >= 0 else RING_REV))
        p.drawEllipse(QRectF(px - 5, py - 5, 10, 10))

        # vnitřní kruh
        inner_rect = QRectF(c.x() - INNER_R, c.y() - INNER_R, INNER_R * 2, INNER_R * 2)
        p.setPen(QPen(QColor("#aaaaaa"), 1))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(inner_rect)

        # 3 tlačítka vedle sebe uvnitř vnitřního kruhu
        btn_w = INNER_R * 2 / 3.0 - 4
        btn_h = INNER_R * 1.3
        y0 = c.y() - btn_h / 2
        labels = [("stop", "⏹", False), ("play", "▶", self._is_playing), ("pause", "⏸", False)]
        x = c.x() - (btn_w * 3 + 4 * 2) / 2
        self._btn_rects.clear()
        f = QFont("Segoe UI", 10)
        p.setFont(f)
        for key, glyph, active in labels:
            r = QRectF(x, y0, btn_w, btn_h)
            self._btn_rects[key] = r
            p.setPen(QPen(BTN_BORDER, 1))
            p.setBrush(QBrush(BTN_ACTIVE.lighter(160) if active else BTN_BG))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QPen(BTN_ACTIVE.darker(120) if active else QColor("#333333")))
            p.drawText(r, Qt.AlignCenter, glyph)
            x += btn_w + 4

        # číselný readout rychlosti/směru pod ovladačem
        value = self._angle / MAX_ANGLE
        if abs(value) > 0.02:
            txt = f"{'▶' if value > 0 else '◀'} {abs(value) * (4.0 if value > 0 else 8.0):.1f}×"
        else:
            txt = ""
        if txt:
            p.setPen(QPen(RING_FWD if value > 0 else RING_REV))
            p.setFont(QFont("Segoe UI", 8, QFont.Bold))
            p.drawText(QRectF(0, self.height() - 16, self.width(), 14), Qt.AlignCenter, txt)

    # --- interakce ---
    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        pos = ev.position()
        dist = self._dist_of(pos)
        if dist <= INNER_R:
            for key, r in self._btn_rects.items():
                if r.contains(pos):
                    if key == "play":
                        self.playRequested.emit()
                    elif key == "pause":
                        self.pauseRequested.emit()
                    elif key == "stop":
                        self.stopRequested.emit()
                    break
            ev.accept()
            return
        if dist <= OUTER_R + 10:
            self._spring_timer.stop()
            self._dragging = True
            self._angle = max(-MAX_ANGLE, min(MAX_ANGLE, self._angle_of(pos)))
            self.update()
            self.shuttleChanged.emit(self._angle / MAX_ANGLE)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if not self._dragging:
            super().mouseMoveEvent(ev)
            return
        a = self._angle_of(ev.position())
        self._angle = max(-MAX_ANGLE, min(MAX_ANGLE, a))
        self.update()
        self.shuttleChanged.emit(self._angle / MAX_ANGLE)
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if self._dragging:
            self._dragging = False
            self.shuttleChanged.emit(0.0)     # okamžité zastavení (logická rychlost)
            self._spring_timer.start()         # jen vizuální návrat ukazatele na 0°
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def _spring_tick(self) -> None:
        self._angle *= 0.6
        if abs(self._angle) < 0.5:
            self._angle = 0.0
            self._spring_timer.stop()
        self.update()
