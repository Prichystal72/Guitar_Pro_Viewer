"""
icons.py — pomocné funkce pro ikony menu/toolbarů (viz plán "Přestavba GUI").

Mix zdrojů: `QStyle` standardní ikony pro běžné akce (otevřít/uložit/smazat…),
vlastní ploché SVG (`assets/icons/*.svg`) pro doménově specifické akce
(bicí, akordy, displej klipy, tempo…), v paletě barev aplikace.
"""
import os

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyle

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")


def icon(name: str) -> QIcon:
    """Vlastní SVG ikonka z assets/icons/<name>.svg."""
    return QIcon(os.path.join(ICON_DIR, f"{name}.svg"))


def std_icon(widget, standard_pixmap: QStyle.StandardPixmap) -> QIcon:
    """Systémová (QStyle) ikonka — `widget` je jen zdroj aktuálního stylu
    (typicky hlavní okno), nic víc se z něj nebere."""
    return widget.style().standardIcon(standard_pixmap)
