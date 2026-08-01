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
    QTextBrowser, QDockWidget, QInputDialog, QDialog,
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

# General MIDI Percussion Key Map (MIDI kanál 10). GP ukládá u bicí stopy toto
# číslo přímo do note.value (ladění struny se nepřičítá) — je to identifikátor
# konkrétního bubnu/sample, ne hudební výška.
GM_PERCUSSION = {
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


def drum_name(midi: int) -> str:
    """Jméno bubnu/samplu podle GM percussion mapy, jinak 'Perc <midi>'."""
    return GM_PERCUSSION.get(midi, f"Perc {midi}" if midi else "?")


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


def expand_measure_order(measures) -> list[int]:
    """Vrátí pořadí INDEXŮ do `measures` tak, jak by se skladba SKUTEČNĚ
    přehrála — respektuje repeat značky (`isRepeatOpen`/`repeatClose`) a
    1./2. zakončení (`repeatAlternative`, bitmaska průchodů). Bere `repeatClose`
    vždy vážně (i bez `repeatAlternative`) — bez tohohle (naivní lineární
    `for m in measures`) je exportovaná časová osa u písní s repeticemi
    KRATŠÍ než reálná nahrávka a vše po repetici "ujíždí".

    (Dřívější verze tohle dělala jen s `repeatAlternative` přítomným kdekoli
    v souboru — domněnka, že holý `repeatClose` bez zakončení je v komunitních
    GP tabech nespolehlivý. Vyvráceno na "Jasná zpráva" (Olympic): oficiální
    text (pisnicky-akordy.cz) má explicitně 3× "Sólo: 2x" přesně na místech
    3 repeat závorek v GP5 (žádné `repeatAlternative`), a synchronizovaný
    LRC text řadí první zpívaný verš na 0:45 — s PLNOU expanzí vychází verš
    na 52s, BEZ expanze na 32s. 52s sedí řádově líp (i když ne dokonale —
    přepis v tabu není nikdy zvukově přesný na sekundu). Uživatelův odhad
    "cca 240s" byl jen hrubý odhad, ne přesná data.)

    Nezohledňuje Segno/Coda/D.S./D.C./Fine skoky (`header.direction`) —
    vzácnější a podstatně složitější (skoky přes celou skladbu, ne jen
    lokální blok)."""
    n = len(measures)
    if n == 0:
        return []

    order: list[int] = []
    repeat_start = 0
    pass_count: dict[int, int] = {}
    i = 0
    SAFETY = n * 8 + 64   # tvrdý strop proti nekonečné smyčce na poškozených datech
    steps = 0
    while i < n and steps < SAFETY:
        steps += 1
        h = measures[i].header
        alt = getattr(h, "repeatAlternative", 0) or 0
        cur_pass = pass_count.get(repeat_start, 1)
        if alt == 0 or (alt & (1 << (cur_pass - 1))):
            order.append(i)
        if getattr(h, "isRepeatOpen", False):
            repeat_start = i
            pass_count.setdefault(repeat_start, 1)
        close = getattr(h, "repeatClose", -1) or -1
        if close > 0:
            done = pass_count.get(repeat_start, 1)
            if done <= close:
                pass_count[repeat_start] = done + 1
                i = repeat_start
                continue
        i += 1
    return order


def tempo_at_tick(tick: int, tempo_map: list[tuple[int, int]]) -> int:
    """Tempo (BPM) platné v daném absolutním (surovém, nerozbaleném) GP ticku."""
    bpm = tempo_map[0][1]
    for map_tick, map_tempo in tempo_map:
        if tick < map_tick:
            break
        bpm = map_tempo
    return bpm


def walk_track_beats(track, tempo_map: list[tuple[int, int]]):
    """Generuje `(measure_idx0, beat, time_s, duration_s)` pro stopu **v
    reálném pořadí přehrávání** — repetice rozbalené (`expand_measure_order`).

    `time_s` NEJDE spočítat z absolutního GP ticku (`ticks_to_seconds`) —
    při repetici se surové ticky opakují (2. průchod repeatovaným úsekem má
    stejné ticky jako 1.), takže absolutní přepočet by dal STEJNÝ čas pro
    oba průchody. Místo toho se `time_s` kumuluje sekvenčně: běžící součet
    délek beatů v EXPANDOVANÉM pořadí. Tempo pro délku beatu se čte z
    `tempo_map` podle jeho původního (surového) ticku — to zůstává platné
    i při opakování, protože říká „jaké tempo je v tomhle místě partitury"."""
    TICKS_PER_BEAT = 960
    order = expand_measure_order(track.measures)
    elapsed = 0.0
    for m_idx in order:
        m = track.measures[m_idx]
        for v_idx, v in enumerate(m.voices):
            if v_idx > 0:
                continue
            for b in v.beats:
                dur_ticks = TICKS_PER_BEAT * 4 // b.duration.value
                if b.duration.isDotted:
                    dur_ticks = int(dur_ticks * 1.5)
                bpm = tempo_at_tick(b.start, tempo_map)
                duration_s = round(dur_ticks / TICKS_PER_BEAT * (60.0 / bpm), 4)
                yield m_idx, b, round(elapsed, 4), duration_s
                elapsed += duration_s


def note_to_midi(note, track) -> int:
    if getattr(track, "isPercussionTrack", False):
        # U bicích je note.value přímo GM percussion číslo (jaký buben/sample zní),
        # ladění struny se nepřičítá.
        return note.value
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
    if getattr(track, "isPercussionTrack", False):
        return "  ".join(drum_name(n.value) for n in sorted(beat.notes, key=lambda n: n.string))
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
        self._loaded_json: Optional[dict] = None
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

        # --- Kompletní postup pro NOVOU píseň (nejdůležitější akce, proto první) ---
        act_web = QAction("📝  Nová píseň — z webu / vlastní text…", self)
        act_web.setShortcut("Ctrl+N")
        act_web.setToolTip(
            "Kompletní postup: načti text z webu NEBO ho vlož/napiš ručně → uprav "
            "řádky a akordy v dialogu → OK ('Použít v editoru') → píseň se rovnou "
            "zobrazí na časové ose v aktuálním tempu (řádek = jeden časový úsek)."
        )
        act_web.triggered.connect(self._open_web_import)
        file_menu.addAction(act_web)
        act_merge = QAction("🎵➕🥁  Sloučit s webem (text+akordy) + bicí z GP…", self)
        act_merge.setShortcut("Ctrl+M")
        act_merge.setToolTip("Otevři GP soubor (kvůli bicím), pak sem vlož URL "
                             "písně z webu — text, řádky, akordy a sloky se vezmou "
                             "z webu, bicí a časování z GP.")
        act_merge.triggered.connect(self.merge_with_web)
        file_menu.addAction(act_merge)
        file_menu.addSeparator()

        act_open = QAction("Otevřít Guitar Pro soubor…", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self.open_file)
        file_menu.addAction(act_open)
        act_open_json = QAction("Otevřít Karaoke JSON…", self)
        act_open_json.setShortcut("Ctrl+J")
        act_open_json.setToolTip("Načte karaoke JSON (např. z web importu) rovnou "
                                 "do editoru — se správnými řádky a časováním.")
        act_open_json.triggered.connect(self.open_json)
        file_menu.addAction(act_open_json)
        act_export = QAction("Exportovat Karaoke JSON…", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.export_json)
        file_menu.addAction(act_export)
        file_menu.addSeparator()
        act_quit = QAction("Konec", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # Toolbar — postup nové písně první a zvýrazněný, ať je hned vidět
        tb = QToolBar("Hlavní panel")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(act_web)
        tb.addAction(act_merge)
        tb.addSeparator()
        tb.addAction(act_open)
        tb.addAction(act_open_json)
        tb.addSeparator()
        tb.addAction(act_export)

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

        # ---- Mix časové osy: VŽDY viditelný (na rozdíl od hlaviček uvnitř
        # scény, které při vodorovném rolování odjedou pryč — proto sem, do
        # levého docku), s hlasitostí/mute nahrávky+GP mixu bicích a
        # skrýt/zobrazit pro jednotlivé stopy. Viz TrackMixPanel. ----
        mix_label = QLabel("Mix časové osy")
        mix_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        left_layout.addWidget(mix_label)
        mix_scroll = QScrollArea()
        mix_scroll.setWidgetResizable(True)
        mix_scroll.setWidget(self.timeline.mix_panel)
        left_layout.addWidget(mix_scroll, 1)

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
        """Kompletní postup pro novou píseň: web/vlastní text → úprava v dialogu
        → OK ('Použít v editoru') → rovnou na časovou osu (aktuální tempo, řádek
        = jeden časový úsek). Uložení GP4+JSON na disk zůstává volitelné, uvnitř
        dialogu, dialog se tím nezavírá."""
        dlg = WebImportDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.result_json:
            self.current_file = None
            self._present_karaoke_data(dlg.result_json, source_desc="Nová píseň (web/text)")

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

    # ------------------------------------------------------------------
    # Načtení karaoke JSON přímo do editoru (např. z web importu)
    # ------------------------------------------------------------------

    def open_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Otevřít Karaoke JSON", "",
            "Karaoke JSON (*.json);;Všechny soubory (*)"
        )
        if path:
            self._load_karaoke_json(path)

    def _load_karaoke_json(self, path: str):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            QMessageBox.critical(self, "Chyba", f"Nelze načíst JSON:\n\n{ex}")
            self.status.showMessage("Chyba načítání JSON.")
            return

        # JSON nemá GP song (tabulatury/detaily), pracujeme jen s karaoke daty
        self.current_file = path
        self._present_karaoke_data(data, source_desc=f"JSON: {path}")

    def _present_karaoke_data(self, data: dict, source_desc: str = "") -> None:
        """Naplní UI a editor karaoke daty (z JSON souboru nebo ze sloučení
        s webem). GP song se dál nepoužívá — pracuje se s těmito daty."""
        self.song = None
        self.tempo_map = []
        self._loaded_json = data

        meta = data.get("meta", {}) or {}
        self.lbl_title.setText(meta.get("title") or (self.current_file and Path(self.current_file).stem) or "")
        self.lbl_artist.setText(meta.get("artist") or "")
        bass_n = len(data.get("bass_timeline", []))
        self.lbl_meta.setText(
            f"Tempo: {meta.get('tempo_bpm', '?')} BPM  |  "
            f"{len(data.get('tracks', []))} stop  |  "
            f"{len(data.get('karaoke_lines', []))} řádků  |  "
            f"{len(data.get('drums_timeline', []))} úderů bicích"
            + (f"  |  {bass_n} not basy" if bass_n else "")
        )

        self.track_tree.clear()
        for t in data.get("tracks", []):
            item = QTreeWidgetItem([f"{t.get('index', '?')}. {t.get('name', '')}",
                                    t.get("type", "")])
            self.track_tree.addTopLevelItem(item)

        note = "(Karaoke data — tabulatury a detaily stop nejsou k dispozici.)"
        for w in (self.overview_text, self.lyrics_text, self.chords_text):
            w.setPlainText(note)
        self.chord_chart_browser.setHtml(f"<p style='color:#888'>{note}</p>")
        self.json_preview.setText(json.dumps(data, indent=2, ensure_ascii=False))

        self.timeline.load_data(data)
        n_lines = len(self.timeline.data.get("karaoke_lines", []))
        self.status.showMessage(f"Načteno ({source_desc}) — {n_lines} karaoke řádků")

    def merge_with_web(self):
        """Sloučí OTEVŘENÝ GP soubor (bicí + basa + časování) s textem/akordy
        z ZADANÉ URL. Na rozdíl od `_merge_gp_tracks_into_current` (kterou
        spustí `_on_loaded`, když GP otevřeš AŽ PO existujících datech) tahle
        metoda text/akordy PŘEPÍŠE čerstvě staženými z webu — je to jednorázová
        akce na vyžádání (Ctrl+M), ne automatický merge. Web je mistr struktury
        (řádky, sloky/refrén, akordy); GP dodá bicí/basu a napasuje časy řádků
        na reálný zpěv."""
        if not self.song:
            QMessageBox.warning(
                self, "Nejdřív Guitar Pro soubor",
                "Nejprve otevři Guitar Pro soubor (Ctrl+O) — z něj se vezmou "
                "bicí a časování.\nPak sem vlož URL písně z webu (text + akordy).")
            return
        url, ok = QInputDialog.getText(
            self, "Sloučit s webem",
            "URL písně (např. pisnicky-akordy.cz/…):", QLineEdit.Normal, "")
        if not ok or not url.strip():
            return
        url = url.strip()
        self.status.showMessage("Stahuji a slučuji s webem…")
        try:
            import web_import as W
            import requests
            gp_title, gp_artist = self.song.title, self.song.artist
            gp_words = self._gp_vocal_words()
            drums, drum_track = self._gp_drums_for_merge()
            bass, bass_track = self._gp_bass_for_merge()

            r = requests.get(url, timeout=25, headers=W.BROWSER_HEADERS)
            r.encoding = W.detect_encoding(r)
            title, artist, det = W.parse_pisnicky_akordy(r.text, url=url)
            web = W.build_karaoke_json(det, title or gp_title, artist or gp_artist,
                                       self.song.tempo, url)
            W.retime_web_to_gp(web, gp_words)
            W.attach_drums(web, drums, drum_track)
            W.attach_bass(web, bass, bass_track)
            web.setdefault("meta", {})
            web["meta"]["source_file"] = Path(self.current_file).name if self.current_file else ""
            web["meta"]["merged_web_gp"] = True
        except Exception as ex:
            QMessageBox.critical(self, "Chyba slučování", str(ex))
            self.status.showMessage("Chyba slučování s webem.")
            return

        self._present_karaoke_data(web, source_desc=f"GP+web: {url}")
        QMessageBox.information(
            self, "Sloučeno",
            f"Sloučeno s webem:\n\n"
            f"  Řádků: {len(web.get('karaoke_lines', []))}\n"
            f"  Akordů: {len(web.get('chords_timeline', []))}\n"
            f"  Úderů bicích: {len(web.get('drums_timeline', []))}\n"
            f"  Not basy: {len(web.get('bass_timeline', []))}\n\n"
            f"Uprav řádky/akordy v editoru a exportuj (Ctrl+E).")

    def _on_loaded(self, song):
        path = self._worker.path
        if self._loaded_json is not None:
            # Text/akordy už jsou načtené (web/JSON/merge) — tenhle GP soubor
            # jen PŘIDÁ své stopy (bicí), text/akordy zůstávají beze změny.
            self._merge_gp_tracks_into_current(song, path)
            return
        self.song = song
        self.current_file = path
        self.tempo_map = build_tempo_map(song)
        self._populate_ui()
        self.status.showMessage(f"Načteno: {self.current_file}")

    def _merge_gp_tracks_into_current(self, song, path: str) -> None:
        """GP otevřený AŽ PO existujících karaoke datech (web/JSON) — text a
        akordy zůstávají beze změny, z GP se přidají bicí a basa (jediné dva
        typy stopy, které tento merge z GP bere — viz `_gp_drums_for_merge`
        / `_gp_bass_for_merge`)."""
        self.song = song
        self.tempo_map = build_tempo_map(song)
        drums, drum_track = self._gp_drums_for_merge()
        bass, bass_track = self._gp_bass_for_merge()

        import web_import as W
        data = self.timeline.data if self.timeline.data else self._loaded_json
        if drum_track:
            W.attach_drums(data, drums, drum_track)
        if bass_track:
            W.attach_bass(data, bass, bass_track)
        meta = data.setdefault("meta", {})
        meta["source_file"] = Path(path).name
        meta["merged_web_gp"] = True
        self.current_file = path

        gp_name = Path(path).name
        self._present_karaoke_data(data, source_desc=f"web+GP bicí/basa: {gp_name}")
        added = []
        if drum_track:
            added.append(f"{len(drums)} úderů bicích")
        if bass_track:
            added.append(f"{len(bass)} not basy")
        if added:
            QMessageBox.information(
                self, "Přidány stopy z GP",
                f"Text a akordy zůstaly beze změny.\n"
                f"Přidáno z GP ({gp_name}): " + ", ".join(added) + ".")
        else:
            QMessageBox.information(
                self, "GP bez bicích/basy",
                f"Text a akordy zůstaly beze změny.\n"
                f"GP soubor ({gp_name}) neobsahuje bicí ani basovou stopu — nic se nepřidalo.")

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
        """Sestaví zjednodušený karaoke JSON — jen **text, akordy a bicí**.

        - **Text** se bere JEN ze zpěvní stopy (detekce podle jména/počtu
          textů), aby se do něj nemíchaly hráčské poznámky ("pull-off",
          "let ring") z ostatních stop. Celé řádky na jednom úderu se
          rozsekají na slova a rozprostřou v čase (word-level karaoke).
        - **Akordy** se berou z jakékoli stopy s akordovými značkami.
        - **Bicí** se exportují vždy (do `drums_timeline`).
        - Do `tracks[]` jde jen zpěv, bicí a stopy nesoucí akordy.
        """
        song = self.song
        if not song:
            return {}

        TICKS_PER_BEAT = 960
        vocal_ti = self._detect_vocal_track_index(song)

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
                "has_line_structure": True,
            },
            "tempo_map": [{"tick": t, "bpm": v} for t, v in self.tempo_map],
            "tracks": [],
            "lyrics_timeline": [],
            "chords_timeline": [],
            "drums_timeline": [],
            "karaoke_lines": [],
        }

        all_chord_events: list[dict] = []
        all_drum_events: list[dict] = []
        vocal_beats: list[dict] = []   # {time_s, duration_s, text} ze zpěvní stopy
        contributes: dict[int, bool] = {}

        for t_idx, track in enumerate(song.tracks):
            ti = t_idx + 1
            track_type = (
                "drums" if track.isPercussionTrack else
                "vocal" if ti == vocal_ti else
                "bass" if len(track.strings) == 4 else
                "solo_guitar" if is_solo_like(track) else
                "guitar"
            )
            track_data = {
                "index": ti,
                "name": track.name,
                "type": track_type,
                "is_drums": track.isPercussionTrack,
                "instrument_midi": track.channel.instrument if track.channel else 0,
            }
            contributes[ti] = track.isPercussionTrack or (ti == vocal_ti)

            # walk_track_beats = repetice rozbalené (viz jeho docstring) —
            # naivní `for m in track.measures` dělalo časovou osu kratší
            # než reálná nahrávka u písní s repeat značkami.
            for m_idx, b, time_s, duration_s in walk_track_beats(track, self.tempo_map):
                if track.isPercussionTrack:
                    for n in b.notes:
                        midi = note_to_midi(n, track)
                        all_drum_events.append({
                            "time_s": time_s, "duration_s": duration_s,
                            "drum": drum_name(midi), "midi": midi,
                            "track_index": ti,
                        })
                    continue

                chord_name = get_chord_name(b)
                if chord_name:
                    all_chord_events.append({
                        "time_s": time_s, "chord": chord_name,
                        "measure": m_idx + 1, "track_index": ti,
                    })
                    contributes[ti] = True

                if ti == vocal_ti:
                    beat_txt = get_beat_text(b)
                    if beat_txt and beat_txt.strip():
                        vocal_beats.append({
                            "time_s": time_s, "duration_s": duration_s,
                            "text": beat_txt.strip(),
                        })

            if contributes[ti]:
                result["tracks"].append(track_data)

        all_chord_events.sort(key=lambda e: e["time_s"])
        all_drum_events.sort(key=lambda e: e["time_s"])
        vocal_beats.sort(key=lambda e: e["time_s"])

        all_lyrics_events, line_ranges = self._vocal_beats_to_lines(vocal_beats, vocal_ti)

        def line_for(t: float):
            """Řádek znějící v čase t — poslední řádek začínající <= t."""
            li = None
            for s, _e, idx in line_ranges:
                if s <= t:
                    li = idx
                else:
                    break
            return li if li is not None else (line_ranges[0][2] if line_ranges else None)

        for ev in all_chord_events:
            ev["line"] = line_for(ev["time_s"])
        for ev in all_drum_events:
            ev["line"] = line_for(ev["time_s"])

        result["lyrics_timeline"] = all_lyrics_events
        result["chords_timeline"] = all_chord_events
        result["drums_timeline"] = all_drum_events

        # karaoke_lines — jeden záznam na řádek
        karaoke_lines: list[dict] = []
        for s, e, li in line_ranges:
            words = [
                {"time_s": ev["time_s"], "duration_s": ev["duration_s"], "text": ev["text"],
                 "line": li, "track_index": ev["track_index"]}
                for ev in all_lyrics_events if ev["line"] == li
            ]
            chords = []
            for ev in all_chord_events:
                if ev["line"] == li and ev["chord"] not in chords:
                    chords.append(ev["chord"])
            karaoke_lines.append({
                "line": li,
                "start_s": s,
                "end_s": e,
                "chords": chords,
                "text": " ".join(w["text"] for w in words),
                "words": words,
                "track_index": words[0]["track_index"] if words else (vocal_ti or 1),
            })
        result["karaoke_lines"] = karaoke_lines

        return result

    @staticmethod
    def _detect_vocal_track_index(song) -> int | None:
        """Index (1-based) zpěvní stopy. Nejdřív podle jména, jinak stopa
        s nejvíce beat-texty (a aspoň jedním)."""
        NAME_HINTS = ("voice", "vocal", "zpěv", "zpev", "zang", "gesang", "canto")
        for i, t in enumerate(song.tracks):
            nm = (t.name or "").lower()
            if any(h in nm for h in NAME_HINTS):
                return i + 1
        best_ti, best_cnt = None, 0
        for i, t in enumerate(song.tracks):
            if t.isPercussionTrack:
                continue
            cnt = sum(1 for m in t.measures for v in m.voices[:1]
                      for b in v.beats if get_beat_text(b))
            if cnt > best_cnt:
                best_ti, best_cnt = i + 1, cnt
        return best_ti if best_cnt > 0 else None

    @staticmethod
    def _vocal_beats_to_lines(vocal_beats: list[dict], vocal_ti):
        """Z beat-textů zpěvní stopy vyrobí (lyrics_events, line_ranges) —
        **text po ŘÁDCÍCH** (jeden event = celý řádek, ne rozsekaný na slova).

        GP často ukládá celý řádek na jeden úder → ten úder = jeden řádek.
        Když jsou beaty po jednotlivých slovech, seskupí se do řádků dle pauz."""
        GAP = 2.0
        events: list[dict] = []
        line_ranges: list[tuple] = []
        if not vocal_beats:
            return events, line_ranges

        multiword = sum(1 for b in vocal_beats if len(b["text"].split()) > 1)
        line_mode = multiword >= max(1, len(vocal_beats) * 0.3)

        if line_mode:
            for bi, b in enumerate(vocal_beats):
                text = b["text"].strip()
                if not text:
                    continue
                li = len(line_ranges)
                t0 = b["time_s"]
                next_t = vocal_beats[bi + 1]["time_s"] if bi + 1 < len(vocal_beats) \
                    else t0 + 2.0
                end = next_t if (next_t - t0) < 8 else round(t0 + 2.0, 3)
                end = max(end, t0 + 0.3)
                events.append({"time_s": round(t0, 3), "duration_s": round(end - t0, 3),
                               "text": text, "line": li, "track_index": vocal_ti})
                line_ranges.append((round(t0, 3), round(end, 3), li))
        else:
            groups: list[list[dict]] = []
            prev_end = None
            for b in vocal_beats:
                if not groups or (prev_end is not None and b["time_s"] - prev_end > GAP):
                    groups.append([])
                groups[-1].append(b)
                prev_end = b["time_s"] + b["duration_s"]
            for li, g in enumerate(groups):
                t0 = g[0]["time_s"]
                end = g[-1]["time_s"] + g[-1]["duration_s"]
                events.append({"time_s": round(t0, 3), "duration_s": round(max(0.3, end - t0), 3),
                               "text": " ".join(b["text"] for b in g),
                               "line": li, "track_index": vocal_ti})
                line_ranges.append((round(t0, 3), round(end, 3), li))

        return events, line_ranges

    def _gp_vocal_words(self) -> list[dict]:
        """Slova zpěvní stopy GP jako [{time_s, text}] — jen pro ZAROVNÁNÍ webu
        na reálné časy (rozsekání celého řádku na slova rozprostřená v čase)."""
        song = self.song
        if not song:
            return []
        vocal_ti = self._detect_vocal_track_index(song)
        if vocal_ti is None:
            return []
        beats = []
        track = song.tracks[vocal_ti - 1]
        for _m_idx, b, time_s, _dur in walk_track_beats(track, self.tempo_map):
            txt = get_beat_text(b)
            if txt and txt.strip():
                beats.append((time_s, txt.strip()))
        beats.sort()
        words: list[dict] = []
        for bi, (t0, txt) in enumerate(beats):
            parts = txt.split()
            if not parts:
                continue
            next_t = beats[bi + 1][0] if bi + 1 < len(beats) else t0 + len(parts) * 0.4
            span = min(len(parts) * 0.4, max(0.3, (next_t - t0) * 0.9))
            step = span / len(parts)
            for wi, w in enumerate(parts):
                words.append({"time_s": round(t0 + wi * step, 3), "text": w})
        return words

    def _gp_drums_for_merge(self):
        """Vrátí (drums_timeline, drum_track_dict) z aktuálního GP songu.
        `walk_track_beats` = repetice rozbalené — jinak bicí u písní s
        repeat značkami (1./2. zakončení apod.) skončí uprostřed písně."""
        song = self.song
        if not song:
            return [], None
        drums: list[dict] = []
        drum_track = None
        for t_idx, track in enumerate(song.tracks):
            if not track.isPercussionTrack:
                continue
            ti = t_idx + 1
            drum_track = {"index": ti, "name": track.name, "type": "drums",
                          "is_drums": True,
                          "instrument_midi": track.channel.instrument if track.channel else 0}
            for _m_idx, b, time_s, duration_s in walk_track_beats(track, self.tempo_map):
                for n in b.notes:
                    midi = note_to_midi(n, track)
                    drums.append({"time_s": time_s, "duration_s": duration_s,
                                  "drum": drum_name(midi), "midi": midi,
                                  "track_index": ti})
            break
        drums.sort(key=lambda e: e["time_s"])
        return drums, drum_track

    def _gp_bass_for_merge(self):
        """Vrátí (bass_timeline, bass_track_dict) z aktuálního GP songu —
        basová stopa (4 struny), noty s výškou (midi/note_name), ne bicí.
        `walk_track_beats` = repetice rozbalené (viz `_gp_drums_for_merge`)."""
        song = self.song
        if not song:
            return [], None
        bass_notes: list[dict] = []
        bass_track = None
        for t_idx, track in enumerate(song.tracks):
            if track.isPercussionTrack or len(track.strings) != 4:
                continue
            ti = t_idx + 1
            bass_track = {"index": ti, "name": track.name, "type": "bass",
                         "is_drums": False,
                         "instrument_midi": track.channel.instrument if track.channel else 0}
            for _m_idx, b, time_s, duration_s in walk_track_beats(track, self.tempo_map):
                if not b.notes:
                    continue
                for n in b.notes:
                    midi = note_to_midi(n, track)
                    bass_notes.append({"time_s": time_s, "duration_s": duration_s,
                                       "note_name": midi_to_name(midi), "midi": midi,
                                       "string": n.string, "fret": n.value,
                                       "track_index": ti})
            break
        bass_notes.sort(key=lambda e: e["time_s"])
        return bass_notes, bass_track

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self):
        if not self.song:
            # Načteno z JSON → exportuj upravená data z editoru časové osy
            if self._loaded_json is not None:
                self._export_timeline_json(self.timeline.to_json())
                return
            QMessageBox.warning(self, "Varování", "Nejprve otevřete Guitar Pro soubor nebo JSON.")
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
            n_drums = len(data["drums_timeline"])
            n_lines = len(data["karaoke_lines"])

            msg = (
                f"Exportováno: {out_path}\n\n"
                f"  Stopy (text/akordy/bicí): {n_tracks}\n"
                f"  Lyrics events: {n_lyrics}\n"
                f"  Chord events: {n_chords}\n"
                f"  Drum hitů: {n_drums}\n"
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
