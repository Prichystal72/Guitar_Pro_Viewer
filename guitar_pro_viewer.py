"""
Guitar Pro Viewer & Karaoke Exporter
PySide6 aplikace pro procházení .gp3/.gp4/.gp5 souborů a export do JSON pro karaoke systémy.
"""

import sys
import json
from html import escape as html_escape
from pathlib import Path
from typing import Optional, Any

import guitarpro
import guitarpro.models as gpm
from web_import import WebImportDialog
from timeline_editor import TimelineEditor

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QLabel, QTextEdit,
    QPushButton, QFileDialog, QTabWidget, QTableWidget, QTableWidgetItem,
    QScrollArea, QStatusBar, QHeaderView, QMessageBox,
    QCheckBox, QFrame, QLineEdit, QComboBox, QToolBar, QSizePolicy,
    QTextBrowser, QDockWidget,
)
from PySide6.QtCore import Qt, QSize, QThread, Signal
from PySide6.QtGui import QFont, QColor, QAction, QIcon, QBrush, QPalette


# ---------------------------------------------------------------------------
# Pomocné funkce
# ---------------------------------------------------------------------------

DURATION_NAMES = {1: "whole", 2: "half", 4: "quarter", 8: "eighth",
                  16: "16th", 32: "32nd", 64: "64th"}


def get_beat_text(beat) -> str:
    """Bezpečně vrátí text beatu — může být string, objekt s .value, nebo None."""
    t = beat.text
    if t is None:
        return ""
    if isinstance(t, str):
        return t
    if hasattr(t, 'value'):
        return t.value or ""
    return str(t)


def get_chord_name(beat) -> str:
    """Bezpečně vrátí název akordu z BeatEffect."""
    try:
        if beat.effect and beat.effect.chord:
            return beat.effect.chord.name or ""
    except Exception:
        pass
    return ""

MIDI_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

STANDARD_STRING_NAMES = {
    # MIDI note → note name (bez oktávy)
}


def midi_to_name(midi: int) -> str:
    if midi <= 0:
        return "?"
    return MIDI_NOTE_NAMES[midi % 12]


def duration_name(value: int) -> str:
    return DURATION_NAMES.get(value, str(value))


def build_tempo_map(song) -> list[tuple[int, int]]:
    """Vrátí seznam (tick, tempo_bpm) seřazený podle ticku.
    GP3 nemá per-takt tempo → stačí globální. GP4/5 mohou mít změny v MeasureHeader.
    """
    tempo_map = [(960, song.tempo)]  # GP tick začíná na 960
    for track in song.tracks[:1]:
        for m in track.measures:
            h = m.header
            # GP4/5 mají h.tempo jako Tempo objekt; GP3 tenhle atribut nemá
            h_tempo = getattr(h, "tempo", None)
            if h_tempo is not None:
                val = getattr(h_tempo, "value", h_tempo)  # Tempo obj nebo int
                if isinstance(val, int) and val > 0 and val != tempo_map[-1][1]:
                    tempo_map.append((h.start, val))
    return sorted(set(tempo_map))


def ticks_to_seconds(tick: int, tempo_map: list[tuple[int, int]]) -> float:
    """Převede absolutní tick na sekundy od začátku skladby."""
    ORIGIN = 960  # GP origin tick
    TICKS_PER_BEAT = 960

    seconds = 0.0
    prev_tick = ORIGIN
    prev_tempo = tempo_map[0][1]

    for map_tick, map_tempo in tempo_map:
        if tick <= map_tick:
            break
        # Přičti čas od prev_tick do map_tick při prev_tempo
        dt = (map_tick - prev_tick) / TICKS_PER_BEAT * (60.0 / prev_tempo)
        seconds += dt
        prev_tick = map_tick
        prev_tempo = map_tempo

    # Zbytek od posledního tempo bodu
    if tick > prev_tick:
        dt = (tick - prev_tick) / TICKS_PER_BEAT * (60.0 / prev_tempo)
        seconds += dt

    return round(seconds, 4)


def note_to_midi(note, track) -> int:
    try:
        string_idx = note.string - 1
        if string_idx < len(track.strings):
            return track.strings[string_idx].value + note.value
    except Exception:
        pass
    return 0


def beat_to_tab_str(beat, track) -> str:
    if not beat.notes:
        return "—"
    parts = []
    for note in sorted(beat.notes, key=lambda n: n.string):
        string_idx = note.string - 1
        open_midi = track.strings[string_idx].value if string_idx < len(track.strings) else 0
        sname = midi_to_name(open_midi)
        parts.append(f"{sname}:{note.value}")
    return "  ".join(parts)


def note_effects_str(note) -> str:
    if not note.effect:
        return ""
    fx = []
    e = note.effect
    if e.hammer:
        fx.append("H/P")  # GP3: hammer attr zahrnuje hammer-on i pull-off
    if e.vibrato:
        fx.append("~")
    if e.slides:
        fx.append("S")
    if e.bend and e.bend.points:
        fx.append("B")
    if e.harmonic:
        fx.append("harm")
    return ",".join(fx)


def is_solo_like(track) -> bool:
    """Heuristika: má-li stopa hodně efektů, jde nejspíš o sólo."""
    name_lower = track.name.lower()
    if any(kw in name_lower for kw in ["solo", "lead", "sólo"]):
        return True
    bend_count = 0
    total_notes = 0
    for m in track.measures:
        for v in m.voices:
            for b in v.beats:
                for n in b.notes:
                    total_notes += 1
                    if n.effect and n.effect.bend and n.effect.bend.points:
                        bend_count += 1
    if total_notes > 0 and bend_count / total_notes > 0.05:
        return True
    return False


# ---------------------------------------------------------------------------
# Worker vlákno pro načítání (aby UI nezmrzlo)
# ---------------------------------------------------------------------------

class LoadWorker(QThread):
    done = Signal(object)   # Song
    error = Signal(str)

    def __init__(self, path: str, encoding: str = 'cp1250'):
        super().__init__()
        self.path = path
        self.encoding = encoding

    def run(self):
        try:
            song = guitarpro.parse(self.path, encoding=self.encoding)
            self.done.emit(song)
        except Exception as ex:
            # Záložní pokus s utf-8 (pro soubory z jiných nástrojů)
            try:
                song = guitarpro.parse(self.path, encoding='utf-8')
                self.done.emit(song)
            except Exception:
                self.error.emit(str(ex))


# ---------------------------------------------------------------------------
# Widget: detail stopy
# ---------------------------------------------------------------------------

class TrackDetailWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Filtr
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Filtr takt:"))
        self.filter_from = QLineEdit()
        self.filter_from.setPlaceholderText("od")
        self.filter_from.setMaximumWidth(60)
        self.filter_to = QLineEdit()
        self.filter_to.setPlaceholderText("do")
        self.filter_to.setMaximumWidth(60)
        filter_bar.addWidget(self.filter_from)
        filter_bar.addWidget(QLabel("–"))
        filter_bar.addWidget(self.filter_to)
        self.filter_btn = QPushButton("Zobrazit")
        filter_bar.addWidget(self.filter_btn)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # Tabulka
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Takt", "Čas (s)", "Délka", "Tabulatura", "Efekty", "Akord", "Text/Slova", "MIDI noty"
        ])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        self._rows_cache: list = []
        self.filter_btn.clicked.connect(self._apply_filter)

    def load_track(self, track, tempo_map: list):
        self.filter_from.clear()
        self.filter_to.clear()
        rows = []
        for m_idx, m in enumerate(track.measures):
            for v_idx, v in enumerate(m.voices):
                if v_idx > 0:
                    continue  # GP3 má jen 1 hlas; přeskočíme prázdné hlasy
                for b in v.beats:
                    chord_name = get_chord_name(b)
                    text = get_beat_text(b)
                    tab = beat_to_tab_str(b, track)
                    effects_all = []
                    midis = []
                    for n in b.notes:
                        efx = note_effects_str(n)
                        if efx:
                            effects_all.append(efx)
                        m_val = note_to_midi(n, track)
                        if m_val:
                            midis.append(f"{midi_to_name(m_val)}{m_val // 12 - 1}")
                    time_s = ticks_to_seconds(b.start, tempo_map)
                    rows.append({
                        "measure": m_idx + 1,
                        "time_s": time_s,
                        "duration": duration_name(b.duration.value),
                        "tab": tab,
                        "effects": ",".join(effects_all),
                        "chord": chord_name,
                        "text": text,
                        "midi": " ".join(midis),
                    })
        self._rows_cache = rows
        self._render_rows(rows)

    def _apply_filter(self):
        try:
            lo = int(self.filter_from.text()) if self.filter_from.text() else 1
            hi = int(self.filter_to.text()) if self.filter_to.text() else 999999
        except ValueError:
            return
        filtered = [r for r in self._rows_cache if lo <= r["measure"] <= hi]
        self._render_rows(filtered)

    def _render_rows(self, rows: list):
        self.table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            def cell(v):
                item = QTableWidgetItem(str(v))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                return item

            self.table.setItem(ri, 0, cell(r["measure"]))
            self.table.setItem(ri, 1, cell(f"{r['time_s']:.3f}"))
            self.table.setItem(ri, 2, cell(r["duration"]))
            self.table.setItem(ri, 3, cell(r["tab"]))
            self.table.setItem(ri, 4, cell(r["effects"]))
            self.table.setItem(ri, 5, cell(r["chord"]))
            self.table.setItem(ri, 6, cell(r["text"]))
            self.table.setItem(ri, 7, cell(r["midi"]))

            # Zvýraznění
            if r["text"]:
                for col in range(8):
                    it = self.table.item(ri, col)
                    if it:
                        it.setBackground(QBrush(QColor(180, 255, 180)))
            elif r["chord"]:
                for col in [5]:
                    it = self.table.item(ri, col)
                    if it:
                        it.setBackground(QBrush(QColor(180, 210, 255)))
            if r["effects"]:
                it = self.table.item(ri, 4)
                if it:
                    it.setBackground(QBrush(QColor(255, 240, 180)))


# ---------------------------------------------------------------------------
# Hlavní okno
# ---------------------------------------------------------------------------

class GuitarProViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.song: Optional[Any] = None
        self.current_file: Optional[str] = None
        self.tempo_map: list = []
        self._worker: Optional[LoadWorker] = None
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI stavba
    # ------------------------------------------------------------------

    def _setup_ui(self):
        self.setWindowTitle("Guitar Pro Viewer — Karaoke Exporter")
        self.setMinimumSize(1300, 820)

        # Menu
        menubar = self.menuBar()
        file_menu = menubar.addMenu("Soubor")
        act_open = QAction("Otevřít Guitar Pro soubor…", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self.open_file)
        file_menu.addAction(act_open)
        act_export = QAction("Exportovat Karaoke JSON…", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.export_json)
        file_menu.addAction(act_export)
        file_menu.addSeparator()
        act_web = QAction("🌐  Import z webu…", self)
        act_web.setShortcut("Ctrl+W")
        act_web.triggered.connect(self._open_web_import)
        file_menu.addAction(act_web)
        file_menu.addSeparator()
        act_quit = QAction("Konec", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # Toolbar
        tb = QToolBar("Hlavní panel")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(act_open)
        tb.addSeparator()
        tb.addAction(act_export)
        tb.addSeparator()
        tb.addAction(act_web)

        # ===== CENTRÁLNÍ PLOCHA OKNA = ČASOVÁ OSA (Sony Vegas styl) =====
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Info pruh (tenký, nad osou)
        info_frame = QFrame()
        info_frame.setFrameShape(QFrame.StyledPanel)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(8, 3, 8, 3)
        self.lbl_title = QLabel("—")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 15px;")
        self.lbl_artist = QLabel("")
        self.lbl_artist.setStyleSheet("font-size: 13px; color: #555;")
        self.lbl_meta = QLabel("")
        self.lbl_meta.setStyleSheet("font-size: 12px; color: #777;")
        info_layout.addWidget(self.lbl_title)
        info_layout.addWidget(QLabel(" — "))
        info_layout.addWidget(self.lbl_artist)
        info_layout.addStretch()
        info_layout.addWidget(self.lbl_meta)
        root.addWidget(info_frame)

        # Časová osa vyplní celé centrum
        self.timeline = TimelineEditor()
        self.timeline.export_callback = self._export_timeline_json
        root.addWidget(self.timeline, 1)

        # ---- Levý DOCK: seznam stop ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)
        self.track_tree = QTreeWidget()
        self.track_tree.setHeaderLabels(["Název", "Typ"])
        self.track_tree.setColumnWidth(0, 150)
        self.track_tree.itemClicked.connect(self._on_track_selected)
        left_layout.addWidget(self.track_tree)
        self.chk_export_all = QCheckBox("Exportovat všechny stopy")
        self.chk_export_all.setChecked(True)
        left_layout.addWidget(self.chk_export_all)
        export_btn = QPushButton("💾  Export Karaoke JSON")
        export_btn.setStyleSheet(
            "QPushButton { background: #2d7d2d; color: white; padding: 7px 12px; "
            "border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3a9e3a; }"
        )
        export_btn.clicked.connect(self.export_json)
        left_layout.addWidget(export_btn)

        self.dock_tracks = QDockWidget("Stopy", self)
        self.dock_tracks.setWidget(left)
        self.dock_tracks.setMinimumWidth(200)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_tracks)

        # ---- Spodní DOCK: náhledy (výchozí SKRYTÝ, ať má osa celé okno) ----
        self.tabs = QTabWidget()
        self.dock_previews = QDockWidget("Náhledy — Chord Chart, Noty, JSON…", self)
        self.dock_previews.setWidget(self.tabs)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_previews)
        self.dock_previews.hide()

        # View menu + přepínače v toolbaru
        view_menu = menubar.addMenu("Zobrazit")
        act_tracks = self.dock_tracks.toggleViewAction()
        act_tracks.setText("Panel stop")
        act_previews = self.dock_previews.toggleViewAction()
        act_previews.setText("Panel náhledů (Chord Chart, JSON…)")
        view_menu.addAction(act_tracks)
        view_menu.addAction(act_previews)
        tb.addSeparator()
        tb.addAction(act_previews)

        # Záložka 1: Chord Chart (text + akordy nad ním)
        chord_chart_widget = QWidget()
        cc_layout = QVBoxLayout(chord_chart_widget)
        cc_layout.setContentsMargins(0, 0, 0, 0)
        self.chord_chart_browser = QTextBrowser()
        self.chord_chart_browser.setFont(QFont("Courier New", 13))
        self.chord_chart_browser.setOpenLinks(False)
        cc_layout.addWidget(self.chord_chart_browser)
        self.tabs.addTab(chord_chart_widget, "🎸 Chord Chart")

        # Záložka 2: Detail stopy
        self.track_detail = TrackDetailWidget()
        self.tabs.addTab(self.track_detail, "Noty / Tabulatura")

        # Záložka 2: Přehled skladby
        overview_widget = QWidget()
        ov_layout = QVBoxLayout(overview_widget)
        self.overview_text = QTextEdit()
        self.overview_text.setReadOnly(True)
        self.overview_text.setFont(QFont("Consolas", 11))
        ov_layout.addWidget(self.overview_text)
        self.tabs.addTab(overview_widget, "Přehled skladby")

        # Záložka 3: Text / Slova
        lyrics_widget = QWidget()
        ly_layout = QVBoxLayout(lyrics_widget)
        self.lyrics_text = QTextEdit()
        self.lyrics_text.setReadOnly(True)
        self.lyrics_text.setFont(QFont("Segoe UI", 12))
        ly_layout.addWidget(self.lyrics_text)
        self.tabs.addTab(lyrics_widget, "Text / Slova")

        # Záložka 4: Akordy
        chords_widget = QWidget()
        ch_layout = QVBoxLayout(chords_widget)
        self.chords_text = QTextEdit()
        self.chords_text.setReadOnly(True)
        self.chords_text.setFont(QFont("Consolas", 12))
        ch_layout.addWidget(self.chords_text)
        self.tabs.addTab(chords_widget, "Akordy")

        # Záložka 5: JSON náhled
        json_widget = QWidget()
        jl = QVBoxLayout(json_widget)
        self.json_preview = QTextEdit()
        self.json_preview.setReadOnly(True)
        self.json_preview.setFont(QFont("Consolas", 10))
        jl.addWidget(self.json_preview)
        self.tabs.addTab(json_widget, "JSON náhled")

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Připraven — Otevřete Guitar Pro soubor (Ctrl+O)")

    # ------------------------------------------------------------------
    # Načítání souboru
    # ------------------------------------------------------------------

    def _open_web_import(self) -> None:
        dlg = WebImportDialog(self)
        dlg.exec()

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Otevřít Guitar Pro soubor", "",
            "Guitar Pro (*.gp3 *.gp4 *.gp5 *.gpx *.gp);;Všechny soubory (*)"
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        self.status.showMessage(f"Načítám: {path} …")
        self._worker = LoadWorker(path)
        self._worker.done.connect(self._on_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()

    def _on_loaded(self, song):
        self.song = song
        self.current_file = self._worker.path
        self.tempo_map = build_tempo_map(song)
        self._populate_ui()
        self.status.showMessage(f"Načteno: {self.current_file}")

    def _on_load_error(self, msg: str):
        QMessageBox.critical(self, "Chyba načítání", f"Nepodařilo se načíst soubor:\n\n{msg}")
        self.status.showMessage("Chyba načítání.")

    # ------------------------------------------------------------------
    # Naplnění UI
    # ------------------------------------------------------------------

    def _populate_ui(self):
        song = self.song
        title = song.title or (Path(self.current_file).stem if self.current_file else "")
        artist = song.artist or "—"
        album = song.album or ""
        tempo = song.tempo
        n_tracks = len(song.tracks)
        n_measures = len(song.tracks[0].measures) if song.tracks else 0

        self.lbl_title.setText(title)
        self.lbl_artist.setText(artist)
        meta = f"Album: {album}  |  Tempo: {tempo} BPM  |  {n_tracks} stop  |  {n_measures} taktů"
        self.lbl_meta.setText(meta)

        # Track tree
        self.track_tree.clear()
        for i, t in enumerate(song.tracks):
            typ = "bicí" if t.isPercussionTrack else ("basa" if len(t.strings) == 4 else "kytara")
            if is_solo_like(t):
                typ = "sólo kytara"
            item = QTreeWidgetItem([f"{i+1}. {t.name}", typ])
            item.setData(0, Qt.UserRole, i)
            if t.isPercussionTrack:
                item.setForeground(0, QBrush(QColor("#8B4513")))
            elif "sólo" in typ:
                item.setForeground(0, QBrush(QColor("#8B0000")))
            self.track_tree.addTopLevelItem(item)
        self.track_tree.expandAll()

        # Přehled
        self._build_overview()

        # Chord chart (hlavní pohled)
        self._build_chord_chart()

        # Lyrics & Chords
        self._build_lyrics()
        self._build_chords()

        # JSON náhled
        self._update_json_preview()

        # Časová osa (editor) — naplň z karaoke dat
        self.timeline.load_data(self._build_karaoke_json(preview_only=False))

        # Zobraz chord chart jako výchozí záložku
        self.tabs.setCurrentIndex(0)

        # Zobraz první stopu
        if song.tracks:
            self._show_track(0)
            self.track_tree.setCurrentItem(self.track_tree.topLevelItem(0))

    def _build_chord_chart(self):
        """Chord chart: akordy nad textem v notové osnově, 2 takty na řádek."""
        if not self.song:
            self.chord_chart_browser.setHtml("<p>Žádná data</p>")
            return

        song = self.song

        # Vyber stopu s nejvíce textem
        main_track = None
        max_texts = -1
        for t in song.tracks:
            if t.isPercussionTrack:
                continue
            count = sum(
                1 for m in t.measures
                for v in m.voices[:1]
                for b in v.beats
                if get_beat_text(b)
            )
            if count > max_texts:
                max_texts = count
                main_track = t

        if main_track is None and song.tracks:
            main_track = song.tracks[0]
        if main_track is None:
            self.chord_chart_browser.setHtml("<p>Žádná stopa</p>")
            return

        measures = main_track.measures
        MEASURES_PER_LINE = 2

        html = []
        html.append("""
<html><body style="background:#fafafa; margin:0; padding:0;">
<div style="font-family:'Courier New',monospace; font-size:14px; line-height:1.0;
            padding:20px 30px; white-space:pre;">""")

        # Hlavička
        title  = html_escape(song.title  or "")
        artist = html_escape(song.artist or "")
        html.append(
            f'<span style="font-size:18px; font-weight:bold;">{title}</span>'
            f'  <span style="font-size:14px; color:#555;">— {artist}</span>'
            f'  <span style="font-size:12px; color:#888;">Tempo: {song.tempo} BPM</span>\n\n'
        )

        has_any_content = False
        active_chord = ""   # aktuálně platný akord (sledujeme přes řádky)

        for line_start in range(0, len(measures), MEASURES_PER_LINE):
            line_measures = measures[line_start:line_start + MEASURES_PER_LINE]

            # Sbíráme (text, chord) pro každý beat v tomto řádku
            beats = []
            for m in line_measures:
                for v in m.voices[:1]:
                    for b in v.beats:
                        beats.append((get_beat_text(b), get_chord_name(b)))

            # Logika: chord se zobrazí POUZE:
            #   a) jako první akord na řádku (i když je stejný jako předchozí řádek)
            #   b) když se změní uprostřed řádku
            text_parts = []
            chord_events = []   # [(char_pos, chord_name)]
            char_pos = 0
            line_chord_shown = False   # byl už na tomto řádku zobrazen první akord?

            for text, chord in beats:
                if chord:
                    if not line_chord_shown:
                        # Začátek řádku — vždy zobrazit
                        chord_events.append((char_pos, chord))
                        active_chord = chord
                        line_chord_shown = True
                    elif chord != active_chord:
                        # Změna uprostřed řádku — zobrazit
                        chord_events.append((char_pos, chord))
                        active_chord = chord
                    # else: stejný akord uprostřed řádku → přeskočit

                if text:
                    part = text if text.endswith(('-', ' ')) else text + ' '
                else:
                    part = ''
                text_parts.append(part)
                char_pos += len(part)

            full_text = ''.join(text_parts).rstrip()

            # Přeskočit řádky bez obsahu
            if not full_text.strip() and not chord_events:
                continue

            has_any_content = True

            # Řádek s akordy (vždy zobrazíme, i když je prázdný)
            if chord_events:
                width = max(len(full_text) + 4,
                            chord_events[-1][0] + len(chord_events[-1][1]) + 1)
                chord_chars = [' '] * width
                for pos, name in chord_events:
                    for i, c in enumerate(name):
                        if pos + i < len(chord_chars):
                            chord_chars[pos + i] = c
                chord_line = ''.join(chord_chars).rstrip()
                html.append(
                    f'<span style="color:#1a5fb4; font-weight:bold;">'
                    f'{html_escape(chord_line)}</span>\n'
                )
            else:
                html.append('\n')   # prázdný chord řádek

            # Řádek s textem
            if full_text.strip():
                html.append(f'{html_escape(full_text)}\n')
            else:
                html.append('<span style="color:#aaa;">(—)</span>\n')

            html.append('\n')   # mezera mezi řádky

        if not has_any_content:
            html.append(
                '<span style="color:#999; font-style:italic;">'
                'Tato stopa neobsahuje text ani akordy.\n'
                'Zkus jinou stopu nebo otevři soubor s textem (beat text).\n'
                '</span>\n'
            )

        html.append('</div></body></html>')
        self.chord_chart_browser.setHtml(''.join(html))

    def _build_overview(self):
        song = self.song
        lines = []
        lines.append(f"=== {song.title} ===")
        lines.append(f"Interpret: {song.artist}")
        lines.append(f"Album: {song.album}")
        lines.append(f"Tempo: {song.tempo} BPM")
        lines.append(f"Počet taktů: {len(song.tracks[0].measures) if song.tracks else 0}")
        lines.append("")
        lines.append("STOPY:")
        for i, t in enumerate(song.tracks):
            tuning_names = [midi_to_name(s.value) for s in t.strings]
            lines.append(
                f"  [{i+1}] {t.name}"
                f"  — {len(t.strings)} strun"
                f"  — ladění: {', '.join(tuning_names)}"
                f"  — pražce: {getattr(t, 'fretCount', getattr(t, 'frets', 24))}"
                f"  {'(BICÍ)' if t.isPercussionTrack else ''}"
                f"  {'[SÓLO]' if is_solo_like(t) else ''}"
            )
        lines.append("")

        # Tempo changes
        if len(self.tempo_map) > 1:
            lines.append("ZMĚNY TEMPA:")
            for tick, tempo in self.tempo_map:
                t_s = ticks_to_seconds(tick, self.tempo_map)
                lines.append(f"  Takt-tick {tick} ({t_s:.1f}s): {tempo} BPM")
            lines.append("")

        # Celková délka (odhadovaná)
        if song.tracks:
            last_track = song.tracks[0]
            last_measure = last_track.measures[-1]
            last_voice = last_measure.voices[0]
            if last_voice.beats:
                last_beat = last_voice.beats[-1]
                last_tick = last_beat.start
                dur_ticks = 960 * 4 // last_beat.duration.value
                if last_beat.duration.isDotted:
                    dur_ticks = int(dur_ticks * 1.5)
                total_s = ticks_to_seconds(last_tick + dur_ticks, self.tempo_map)
                lines.append(f"Odhadovaná délka: {int(total_s // 60)}:{int(total_s % 60):02d} min")

        self.overview_text.setText("\n".join(lines))

    def _build_lyrics(self):
        song = self.song
        lines = []

        # Song-level lyrics object
        if song.lyrics and hasattr(song.lyrics, 'lines'):
            for ll in song.lyrics.lines:
                if ll.lyrics and ll.lyrics.strip():
                    lines.append("=== Lyrics (Song-level) ===")
                    lines.append(ll.lyrics)
                    lines.append("")

        # Beat-level text
        beat_texts = []
        for t in song.tracks:
            for m_idx, m in enumerate(t.measures):
                for v in m.voices[:1]:
                    for b in v.beats:
                        txt = get_beat_text(b)
                        if txt:
                            time_s = ticks_to_seconds(b.start, self.tempo_map)
                            beat_texts.append((time_s, m_idx + 1, t.name, txt))

        if beat_texts:
            lines.append("=== Text vázaný na noty (beat text) ===")
            for time_s, m_num, track_name, text in sorted(beat_texts):
                lines.append(f"  [{time_s:7.2f}s | takt {m_num:3d} | {track_name}]  {text}")
        else:
            lines.append("(Žádný text vázaný na noty nenalezen.)")
            lines.append("")
            lines.append("Tip: Text 'beat text' se v Guitar Pro přidává přes")
            lines.append("nástrojovou lištu > Text. V GP3 souborech bývá vzácný.")

        self.lyrics_text.setText("\n".join(lines))

    def _build_chords(self):
        song = self.song
        chord_events: list[tuple] = []
        chords_dict: dict[str, int] = {}

        for t in song.tracks:
            for m_idx, m in enumerate(t.measures):
                for v in m.voices[:1]:
                    for b in v.beats:
                        name = get_chord_name(b)
                        if name:
                            time_s = ticks_to_seconds(b.start, self.tempo_map)
                            chord_events.append((time_s, m_idx + 1, t.name, name))
                            chords_dict[name] = chords_dict.get(name, 0) + 1

        lines = []
        if chords_dict:
            lines.append("=== Nalezené akordy ===")
            for name, count in sorted(chords_dict.items()):
                lines.append(f"  {name:<12}  ({count}×)")
            lines.append("")
            lines.append("=== Časová osa akordů ===")
            for time_s, m_num, track_name, name in sorted(chord_events):
                lines.append(f"  [{time_s:7.2f}s | takt {m_num:3d} | {track_name}]  {name}")
        else:
            lines.append("(Žádné akordy nenalezeny.)")
            lines.append("")
            lines.append("Tip: Akordy se přidávají v Guitar Pro přes")
            lines.append("Chord Diagram (symbol nad notami).")

        self.chords_text.setText("\n".join(lines))

    def _update_json_preview(self):
        data = self._build_karaoke_json(preview_only=True)
        self.json_preview.setText(json.dumps(data, indent=2, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Výběr stopy v stromu
    # ------------------------------------------------------------------

    def _on_track_selected(self, item: QTreeWidgetItem, col: int):
        idx = item.data(0, Qt.UserRole)
        if isinstance(idx, int):
            self._show_track(idx)

    def _show_track(self, track_idx: int):
        if not self.song or track_idx >= len(self.song.tracks):
            return
        track = self.song.tracks[track_idx]
        self.track_detail.load_track(track, self.tempo_map)
        self.tabs.setCurrentIndex(0)
        self.status.showMessage(
            f"Stopa: {track.name}  |  {len(track.measures)} taktů  |  "
            f"{'Bicí' if track.isPercussionTrack else f'{len(track.strings)} strun'}"
        )

    # ------------------------------------------------------------------
    # Stavba karaoke JSON
    # ------------------------------------------------------------------

    def _build_karaoke_json(self, preview_only: bool = False) -> dict:
        song = self.song
        if not song:
            return {}

        TICKS_PER_BEAT = 960

        result = {
            "meta": {
                "format_version": 2,
                "title": song.title or "",
                "artist": song.artist or "",
                "album": song.album or "",
                "tempo_bpm": song.tempo,
                "ticks_per_beat": TICKS_PER_BEAT,
                "total_measures": len(song.tracks[0].measures) if song.tracks else 0,
                "track_count": len(song.tracks),
                "source_file": Path(self.current_file).name if self.current_file else "",
            },
            "tempo_map": [{"tick": t, "bpm": v} for t, v in self.tempo_map],
            "tracks": [],
            "lyrics_timeline": [],
            "chords_timeline": [],
            "karaoke_lines": [],
        }

        all_lyrics_events: list[dict] = []
        all_chord_events: list[dict] = []

        for t_idx, track in enumerate(song.tracks):
            track_data = {
                "index": t_idx + 1,
                "name": track.name,
                "type": (
                    "drums" if track.isPercussionTrack else
                    "bass" if len(track.strings) == 4 else
                    "solo_guitar" if is_solo_like(track) else
                    "guitar"
                ),
                "is_drums": track.isPercussionTrack,
                "instrument_midi": track.channel.instrument if track.channel else 0,
                "tuning": [
                    {"string": i + 1, "midi": s.value, "note": midi_to_name(s.value)}
                    for i, s in enumerate(track.strings)
                ],
                "beats": [],
            }

            for m_idx, m in enumerate(track.measures):
                for v_idx, v in enumerate(m.voices):
                    if v_idx > 0:
                        continue
                    for b in v.beats:
                        dur_ticks = TICKS_PER_BEAT * 4 // b.duration.value
                        if b.duration.isDotted:
                            dur_ticks = int(dur_ticks * 1.5)

                        chord_name = get_chord_name(b)

                        time_s = ticks_to_seconds(b.start, self.tempo_map)

                        beat_data = {
                            "measure": m_idx + 1,
                            "tick": b.start,
                            "time_s": time_s,
                            "duration": duration_name(b.duration.value),
                            "duration_ticks": dur_ticks,
                            "duration_s": round(dur_ticks / TICKS_PER_BEAT * (60.0 / song.tempo), 4),
                            "text": get_beat_text(b),
                            "chord": chord_name,
                            "notes": [
                                {
                                    "string": n.string,
                                    "fret": n.value,
                                    "midi": note_to_midi(n, track),
                                    "note_name": midi_to_name(note_to_midi(n, track)),
                                    "effects": {
                                        "hammer_on": bool(n.effect and n.effect.hammer),
                                        "pull_off": bool(n.effect and getattr(n.effect, 'pullOff', False)),
                                        "vibrato": bool(n.effect and n.effect.vibrato),
                                        "slide": bool(n.effect and n.effect.slides),
                                        "bend": bool(n.effect and n.effect.bend and n.effect.bend.points),
                                    }
                                }
                                for n in b.notes
                            ],
                        }

                        if not preview_only:
                            track_data["beats"].append(beat_data)

                        beat_txt = get_beat_text(b)
                        if beat_txt:
                            ev = {
                                "time_s": time_s,
                                "duration_s": beat_data["duration_s"],
                                "text": beat_txt,
                                "measure": m_idx + 1,
                                "tick": b.start,
                                "track_index": t_idx + 1,
                            }
                            all_lyrics_events.append(ev)

                        if chord_name:
                            ev = {
                                "time_s": time_s,
                                "chord": chord_name,
                                "measure": m_idx + 1,
                                "tick": b.start,
                                "track_index": t_idx + 1,
                            }
                            all_chord_events.append(ev)

            if preview_only:
                # V náhledu zobrazíme jen prvních 5 beatů
                sample_beats = []
                for m_idx, m in enumerate(track.measures[:3]):
                    for v in m.voices[:1]:
                        for b in v.beats:
                            if len(sample_beats) >= 5:
                                break
                            sample_beats.append({
                                "measure": m_idx + 1,
                                "time_s": ticks_to_seconds(b.start, self.tempo_map),
                                "text": get_beat_text(b),
                                "chord": get_chord_name(b),
                                "notes_count": len(b.notes),
                            })
                track_data["beats_preview"] = sample_beats
                track_data["total_beats"] = sum(
                    len(v.beats)
                    for m in track.measures
                    for v in m.voices[:1]
                )

            result["tracks"].append(track_data)

        # Seřadit a deduplikovat
        all_lyrics_events.sort(key=lambda e: e["time_s"])
        all_chord_events.sort(key=lambda e: e["time_s"])

        result["lyrics_timeline"] = all_lyrics_events
        result["chords_timeline"] = all_chord_events

        # Karaoke řádky — skupiny slov oddělené pauzami > 2s
        if all_lyrics_events:
            lines: list[dict] = []
            current_line: list[dict] = []
            prev_end = 0.0
            GAP_THRESHOLD = 2.0

            for ev in all_lyrics_events:
                if current_line and (ev["time_s"] - prev_end) > GAP_THRESHOLD:
                    lines.append({
                        "start_s": current_line[0]["time_s"],
                        "end_s": prev_end,
                        "words": current_line,
                    })
                    current_line = []
                current_line.append({
                    "time_s": ev["time_s"],
                    "duration_s": ev["duration_s"],
                    "text": ev["text"],
                    "track_index": ev["track_index"],
                })
                prev_end = ev["time_s"] + ev["duration_s"]

            if current_line:
                lines.append({
                    "start_s": current_line[0]["time_s"],
                    "end_s": prev_end,
                    "words": current_line,
                })

            result["karaoke_lines"] = lines

        return result

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self):
        if not self.song:
            QMessageBox.warning(self, "Varování", "Nejprve otevřete Guitar Pro soubor.")
            return

        default_name = ""
        if self.current_file:
            default_name = (Path(self.current_file).stem + "_karaoke.json") if self.current_file else "karaoke.json"

        out_path, _ = QFileDialog.getSaveFileName(
            self, "Uložit Karaoke JSON", default_name,
            "JSON soubory (*.json);;Všechny soubory (*)"
        )
        if not out_path:
            return

        self.status.showMessage("Exportuji JSON…")
        try:
            data = self._build_karaoke_json(preview_only=False)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            n_tracks = len(data["tracks"])
            n_lyrics = len(data["lyrics_timeline"])
            n_chords = len(data["chords_timeline"])
            n_lines = len(data["karaoke_lines"])
            total_beats = sum(len(t.get("beats", [])) for t in data["tracks"])

            msg = (
                f"Exportováno: {out_path}\n\n"
                f"  Stopy: {n_tracks}\n"
                f"  Celkem beatů/not: {total_beats}\n"
                f"  Lyrics events: {n_lyrics}\n"
                f"  Chord events: {n_chords}\n"
                f"  Karaoke řádků: {n_lines}"
            )
            QMessageBox.information(self, "Export hotov", msg)
            self.status.showMessage(f"Exportováno → {out_path}")
        except Exception as ex:
            QMessageBox.critical(self, "Chyba exportu", str(ex))
            self.status.showMessage("Chyba při exportu.")

    def _export_timeline_json(self, data: dict) -> None:
        """Uloží JSON upravený v časové ose (volá se z tlačítka v editoru)."""
        default_name = ""
        if self.current_file:
            default_name = Path(self.current_file).stem + "_edited.json"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Uložit upravený Karaoke JSON", default_name,
            "JSON soubory (*.json);;Všechny soubory (*)"
        )
        if not out_path:
            return
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            n_l = len(data.get("lyrics_timeline", []))
            n_c = len(data.get("chords_timeline", []))
            QMessageBox.information(
                self, "Export hotov",
                f"Uloženo: {out_path}\n\n  Text events: {n_l}\n  Akord events: {n_c}")
            self.status.showMessage(f"Upravená osa exportována → {out_path}")
        except Exception as ex:
            QMessageBox.critical(self, "Chyba exportu", str(ex))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Tmavá/světlá paleta dle systému (Fusion respektuje systémové téma)
    window = GuitarProViewer()
    window.show()

    # Pokud je soubor předán jako argument
    if len(sys.argv) > 1:
        window._load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
