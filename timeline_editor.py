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

import array
import copy
import math
import os
import random
import struct
import tempfile
import wave
from typing import Any, Optional

from PySide6.QtCore import Qt, QRectF, QPointF, QUrl, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QPen, QFont, QPainter, QPixmap, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtMultimedia import (
    QMediaPlayer, QAudioOutput, QMediaDevices, QSoundEffect,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsItem,
    QGraphicsSimpleTextItem, QGraphicsPixmapItem, QInputDialog, QCheckBox,
    QSplitter, QFormLayout, QLineEdit, QDoubleSpinBox, QSpinBox,
    QDialog, QDialogButtonBox, QMenu, QFileDialog, QSlider, QStyle,
    QGraphicsLineItem,
)

from jog_shuttle import JogShuttleWidget
from waveform import WaveformData, WaveformLoader, estimate_alignment
from i18n import tr, tr_action, register_tr, tr_label
from icons import icon, std_icon
import ascii_tab

# --- ikonky bicích (assets/drum_icons/*), klíč = _drum_icon_key() ---
DRUM_ICON_DIR = os.path.join(os.path.dirname(__file__), "assets", "drum_icons")

# --- reálné WAV vzorky bicích (stejné jako na SD kartě ESP32, viz
# drum_samples.json) — pokud tu jsou, editor je hraje MÍSTO syntetického
# cvaknutí (_DRUM_CLICK_PARAMS níže), ať už interaktivně (_play_drum_sound)
# nebo v celkovém mixdownu (render_drums_mixdown).
DRUM_SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "assets", "drums", "rock")
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

# Výchozí řádky bicí stopy, když na ní ještě NEJSOU žádné údery (viz
# `_drum_names_for` — jinak by prázdná stopa neměla ani jeden interaktivní
# řádek, takže by na ni nešlo nic pravým klikem přidat).
DEFAULT_DRUM_ROWS = ["Closed Hi-Hat", "Acoustic Snare", "Bass Drum 1"]

# Standardní ladění 4strunné basy — MIDI notu otevřené struny (string 1 =
# nejtenčí/nejvyšší, string 4 = nejtlustší/nejnižší, viz `_draw_bass_lane`).
# Předpoklad standardního ladění (E-A-D-G) — skutečné ladění konkrétní GP
# stopy (alternativní ladění, 5strunná basa…) se sem nepromítá. Používá se
# jak pro popisek noty v editoru, tak jako VÝŠKA pro syntetizované
# přehrávání (`_bass_tone_samples`/`_play_bass_note`) — u nestandardně
# laděné basy tak může znít jinak, než jak skutečně hraje v GP souboru.
BASS_STRING_OPEN_MIDI = {1: 43, 2: 38, 3: 33, 4: 28}   # G2 D2 A1 E1
_NOTE_LETTERS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _bass_note_name(midi: int) -> str:
    return _NOTE_LETTERS[midi % 12] if midi > 0 else "?"
DRUM_HIT_NAMES = list(DRUM_MIDI_NAMES.values())

# --- rozměry rozvržení (px) ---
HEADER_W = 150      # levý sloupec s názvy stop
RULER_H = 30        # horní pravítko s časem
LINE_H = 24         # výška pruhu karaoke ŘÁDKŮ
CHORD_H = 26        # výška pruhu akordů v rámci stopy
LYRIC_H = 30        # výška pruhu textu (slov) v rámci stopy
TRACK_GAP = 12
PER_TRACK = LINE_H + CHORD_H + LYRIC_H + TRACK_GAP
BLOCK_MIN_W = 14
EDGE = 6            # zóna u okraje pro resize
DRUM_ICON_SIZE = 22     # px — fixní velikost ikonky bubnu v pruhu bicích
DRUM_ROW_H = 26.0       # px — výška JEDNOHO řádku bicí stopy (ikona + okraj)
TAB_BLOCK_H = 100.0     # px — výška bloku sólo tabulatury (6 řádků ASCII + okraj)
TAB_BLOCK_DEFAULT_DUR_S = 4.0   # výchozí délka nově přidaného ASCII bloku sóla
UNDO_LIMIT = 50         # max. počet kroků historie (deepcopy self.data na krok)
WAVEFORM_MIN_H = 24.0    # px — nejmenší výška stopy vlnovky (tažením dolů)
WAVEFORM_MAX_H = 200.0   # px — největší výška (ať je vidět i tichý nástup bicích)
WAVEFORM_DEFAULT_H = 64.0
AUDIO_STRETCH_MIN = 0.90   # −10 %
AUDIO_STRETCH_MAX = 1.10   # +10 %

LINE_COLOR = QColor("#9141ac")   # fialová = karaoke řádky

CHORD_COLOR = QColor("#1a5fb4")
LYRIC_COLOR = QColor("#2d7d2d")
TAB_COLOR = QColor("#7c3fc9")
SEL_COLOR = QColor("#e5a50a")

def _track_fallback_name(ti) -> str:
    return tr("track.fallback_name", n=ti)


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

# Schránka kopírovat/vyjmout/vložit — SDÍLENÁ napříč VŠEMI otevřenými
# kartami (viz plán "Multi-dokument"), schválně modulová (ne instanční na
# TimelineEditor), aby "kopírovat v písni A → přepnout kartu → vložit v
# písni B" fungovalo bez jakékoliv zvláštní mezikartové komunikace.
_SHARED_CLIPBOARD: list[dict] = []


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


_drum_sample_map: Optional[dict[str, str]] = None


def _drum_sample_basename(drum_name: str) -> Optional[str]:
    """GM jméno bicího (např. "Acoustic Snare") -> jméno reálného vzorku
    bez přípony (např. "snare") podle drum_samples.json — stejné mapování,
    jaké se ukládá do JSONu při exportu, viz format_migrate._load_drum_samples."""
    global _drum_sample_map
    if _drum_sample_map is None:
        import format_migrate
        _drum_sample_map = format_migrate._load_drum_samples(
            os.path.dirname(os.path.abspath(__file__))
        )
    return _drum_sample_map.get(drum_name)


def _real_drum_sample_path(drum_name: str) -> Optional[str]:
    """Cesta k reálnému WAV vzorku daného bicího, pokud existuje na disku
    (assets/drums/rock/*.wav) — jinak None (padá se zpátky na syntetické
    cvaknutí, viz _play_drum_sound/render_drums_mixdown)."""
    base = _drum_sample_basename(drum_name)
    if not base:
        return None
    path = os.path.join(DRUM_SAMPLE_DIR, base + ".wav")
    return path if os.path.isfile(path) else None


_wav_sample_cache: dict[str, tuple[int, array.array]] = {}


def _load_wav_samples(path: str) -> tuple[int, array.array]:
    """Načte mono 16bit vzorky z WAV souboru (vícekanálové se smíchají na
    mono průměrem). Výsledek se cachuje podle cesty — v jedné session se
    stejný vzorek hraje/mixuje opakovaně."""
    cached = _wav_sample_cache.get(path)
    if cached is not None:
        return cached
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise ValueError(f"nepodporovaná hloubka vzorku ({sw*8} bit) v {path}")
    samples = array.array("h")
    samples.frombytes(raw)
    if ch > 1:
        n = len(samples) // ch
        mono = array.array("h", bytes(2 * n))
        for i in range(n):
            mono[i] = sum(samples[i * ch:(i + 1) * ch]) // ch
        samples = mono
    _wav_sample_cache[path] = (rate, samples)
    return rate, samples


# --- syntetizované zvuky bicích pro sluchovou zpětnou vazbu při editaci
# (přidání/přesun/změna typu úderu — viz TimelineEditor._play_drum_sound).
# Žádné reálné sample soubory nejsou v repozitáři (ty jsou na SD kartě
# ESP32, viz drum_samples.json) — tohle je jen orientační "cvaknutí", ne
# finální zvuk hardwaru.
_DRUM_CLICK_PARAMS = {
    # klíč (_drum_icon_key): (tón Hz nebo None, podíl šumu 0..1, doznívání s, délka s, hlasitost)
    "kick":         (55.0,  0.0, 0.12, 0.25, 0.9),
    "snare":        (180.0, 0.6, 0.08, 0.15, 0.8),
    "tom_high":     (220.0, 0.0, 0.15, 0.25, 0.8),
    "tom_mid":      (150.0, 0.0, 0.18, 0.30, 0.8),
    "tom_low":      (100.0, 0.0, 0.20, 0.35, 0.8),
    "hihat_closed": (None,  1.0, 0.03, 0.06, 0.5),
    "hihat_open":   (None,  1.0, 0.15, 0.30, 0.5),
    "cymbal":       (None,  1.0, 0.35, 0.60, 0.5),
    "perc":         (300.0, 0.3, 0.10, 0.20, 0.7),
}
_drum_click_paths: dict[str, str] = {}


def _drum_click_wav(key: str) -> str:
    """Vygeneruje (a přes proces cachuje) krátký perkusní zvuk pro daný
    typ bubnu — tón (pokud je zadaný) + bílý šum, s exponenciálním
    doznívámím. Stejný princip pro všechny kategorie, jen jiné parametry
    v `_DRUM_CLICK_PARAMS`."""
    cached = _drum_click_paths.get(key)
    if cached and os.path.isfile(cached):
        return cached
    tone_hz, noise_amt, tau, dur, amp = _DRUM_CLICK_PARAMS.get(key, _DRUM_CLICK_PARAMS["perc"])
    rate = 22050
    n = int(rate * dur)
    fd, path = tempfile.mkstemp(suffix=f"_drum_{key}.wav", prefix="timeline_editor_")
    os.close(fd)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = []
        for i in range(n):
            t = i / rate
            env = math.exp(-t / tau)
            v = 0.0
            if tone_hz:
                v += (1.0 - noise_amt) * math.sin(2 * math.pi * tone_hz * t)
            if noise_amt:
                v += noise_amt * (random.random() * 2 - 1)
            v = max(-1.0, min(1.0, v * env * amp))
            frames.append(int(v * 32767))
        wf.writeframes(struct.pack(f"<{n}h", *frames))
    _drum_click_paths[key] = path
    return path


def render_drums_mixdown(data: dict, duration_s: float, rate: int = 44100) -> str:
    """Offline "mixdown" bicích z `drums_timeline` do jednoho WAV souboru —
    pro bicí, co mají reálný vzorek na disku (assets/drums/rock/*.wav,
    viz _real_drum_sample_path), se namixuje SKUTEČNÉ audio; pro ostatní
    (bonga/conga/... bez vzorku) syntéza (tón+šum, exponenciální
    doznívání) jako sluchová zpětná vazba při editaci (`_drum_click_wav`).
    Všechny údery se smíchají na jejich reálný `time_s`.

    Slouží k tomu, aby šlo ČLOVĚKU poslechem porovnat "co říká GP soubor"
    (tenhle mixdown) se skutečnou nahrávkou (druhý přehrávač vedle sebe,
    viz `TimelineEditor.gp_player`) — čistě informativní pomůcka, nic
    sama neopravuje ani neřídí."""
    n_total = max(1, int(rate * (duration_s + 1.0)))
    buf = array.array("d", bytes(n_total * 8))   # zero-filled doubles
    for ev in data.get("drums_timeline", []) or []:
        drum_name = ev.get("drum", "")
        start = int(float(ev.get("time_s", 0.0)) * rate)
        real_path = _real_drum_sample_path(drum_name)
        if real_path:
            src_rate, samples = _load_wav_samples(real_path)
            n = len(samples)
            for i in range(n):
                idx = start + (i if src_rate == rate else i * rate // src_rate)
                if idx < 0 or idx >= n_total:
                    continue
                buf[idx] += samples[i] / 32768.0
            continue
        key = TimelineEditor._drum_icon_key(drum_name)
        tone_hz, noise_amt, tau, dur, amp = _DRUM_CLICK_PARAMS.get(key, _DRUM_CLICK_PARAMS["perc"])
        n = int(rate * dur)
        for i in range(n):
            idx = start + i
            if idx < 0 or idx >= n_total:
                continue
            t = i / rate
            env = math.exp(-t / tau)
            v = 0.0
            if tone_hz:
                v += (1.0 - noise_amt) * math.sin(2 * math.pi * tone_hz * t)
            if noise_amt:
                v += noise_amt * (random.random() * 2 - 1)
            buf[idx] += v * env * amp
    peak = max((abs(x) for x in buf), default=0.0)
    gain = (0.9 / peak) if peak > 0.9 else 1.0
    fd, path = tempfile.mkstemp(suffix="_gp_drums_mix.wav", prefix="timeline_editor_")
    os.close(fd)
    out = array.array("h", bytes(n_total * 2))
    for i in range(n_total):
        v = buf[i] * gain
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        out[i] = int(v * 32767)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(out.tobytes())
    return path


# --- syntetizovaná basa (zatím jediná "reálná" možnost — na rozdíl od
# bicích tu nejsou WAV vzorky, basa hraje libovolnou výšku podle
# struny+pražce, takže samplovat by šlo jen s celou bankou not) ---

def _bass_freq(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _bass_tone_samples(midi: int, render_s: float, rate: int = 44100) -> array.array:
    """Syntetizovaný 'drnk' basové struny — základní tón + pár tišších
    harmonických (přibližuje barvu struny, ne čistý sinus) s rychlým
    náběhem a exponenciálním dozníváním. `render_s` je CELKOVÁ délka
    vykresleného zvuku — nota doznívá i kus po `duration_s` z dat, jako
    skutečná struna."""
    freq = _bass_freq(midi)
    n = max(1, int(rate * render_s))
    out = array.array("d", bytes(n * 8))
    harmonics = ((1.0, 1.0), (2.0, 0.5), (3.0, 0.22), (4.0, 0.1))
    tau = max(0.2, render_s * 0.55)
    attack = min(0.008, render_s * 0.15)
    for i in range(n):
        t = i / rate
        env = (t / attack) if t < attack else math.exp(-(t - attack) / tau)
        v = 0.0
        for mult, amp in harmonics:
            v += amp * math.sin(2 * math.pi * freq * mult * t)
        out[i] = v * env
    return out


_bass_synth_cache: dict[tuple[int, float], array.array] = {}


def _bass_tone_samples_cached(midi: int, render_s: float, rate: int = 44100) -> array.array:
    """`_bass_tone_samples` cachovaný podle (nota, délka) — reálná basová
    linka opakuje stejné noty/délky velmi často (viz `render_bass_mixdown`),
    přepočítávat trigonometrii pro každý výskyt zvlášť by bylo zbytečné."""
    key = (midi, round(render_s, 2))
    cached = _bass_synth_cache.get(key)
    if cached is not None:
        return cached
    samples = _bass_tone_samples(midi, render_s, rate)
    _bass_synth_cache[key] = samples
    return samples


_bass_note_paths: dict[int, str] = {}


def _bass_note_wav(midi: int, dur: float = 0.6) -> str:
    """Krátký syntetizovaný 'drnk' jako dočasný WAV soubor pro sluchovou
    zpětnou vazbu při editaci (přidání/přetažení/změna výšky) — cachováno
    podle MIDI noty, viz `TimelineEditor._play_bass_note`."""
    cached = _bass_note_paths.get(midi)
    if cached and os.path.isfile(cached):
        return cached
    samples = _bass_tone_samples(midi, dur)
    peak = max((abs(x) for x in samples), default=0.0)
    gain = (0.85 / peak) if peak > 0 else 1.0
    fd, path = tempfile.mkstemp(suffix=f"_bass_{midi}.wav", prefix="timeline_editor_")
    os.close(fd)
    n = len(samples)
    out = array.array("h", bytes(n * 2))
    for i in range(n):
        v = max(-1.0, min(1.0, samples[i] * gain))
        out[i] = int(v * 32767)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(out.tobytes())
    _bass_note_paths[midi] = path
    return path


def render_bass_mixdown(data: dict, duration_s: float, rate: int = 44100) -> str:
    """Offline mixdown BASY z `bass_timeline` do jednoho WAV souboru —
    syntetizovaný 'drnk' (`_bass_tone_samples`) na reálném `time_s` každé
    noty, doznívající o kus déle než `duration_s` (jako skutečná struna).
    Slouží k poslechovému ověření basové linky z GP dat vedle skutečné
    nahrávky (viz `TimelineEditor._load_bass_mix`) — čistě informativní
    pomůcka, nic sama neopravuje."""
    n_total = max(1, int(rate * (duration_s + 1.5)))
    buf = array.array("d", bytes(n_total * 8))
    for ev in data.get("bass_timeline", []) or []:
        midi = int(ev.get("midi", 40))
        render_s = max(0.15, float(ev.get("duration_s", 0.3)) + 0.3)   # trocha dozvuku navíc
        start = int(float(ev.get("time_s", 0.0)) * rate)
        samples = _bass_tone_samples_cached(midi, render_s, rate)
        for i, v in enumerate(samples):
            idx = start + i
            if idx < 0 or idx >= n_total:
                continue
            buf[idx] += v
    peak = max((abs(x) for x in buf), default=0.0)
    gain = (0.9 / peak) if peak > 0.9 else 1.0
    fd, path = tempfile.mkstemp(suffix="_gp_bass_mix.wav", prefix="timeline_editor_")
    os.close(fd)
    out = array.array("h", bytes(n_total * 2))
    for i in range(n_total):
        v = buf[i] * gain
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        out[i] = int(v * 32767)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(out.tobytes())
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
        self.setToolTip(tr("playhead.tooltip"))
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


def _snap_bypassed() -> bool:
    """True, když je při tažení podržený Alt — dočasně vypne přichytávání
    na mřížku (konvence z DAW: Ableton/FL/Logic mají obdobu na Ctrl/Alt).
    Čte se přímo aktuální stav klávesnice, ne z konkrétní QMouseEvent
    (itemChange při tažení žádnou nedostává)."""
    return bool(QApplication.keyboardModifiers() & Qt.AltModifier)


def _apply_group_snap(item, editor: "TimelineEditor", new_pos: QPointF, lane_y: float) -> QPointF:
    """Společná snap logika pro BlockItem/DrumHitItem/BassNoteItem při
    tažení myší. Když je vybraných víc položek, Qt je posouvá VŠECHNY
    stejným syrovým (nesnapovaným) posunem a volá itemChange pro každou
    zvlášť — kdyby si každá nezávisle přichytávala svůj VLASTNÍ výsledný
    čas na mřížku, položky s různou fází vůči mřížce by se posunuly různě
    a jejich vzájemná vzdálenost by se rozjela. Proto se snapuje jen
    `editor._drag_anchor` (položka fyzicky tažená pod myší) a ostatní
    vybrané dostanou STEJNÝ (nesnapovaný/snapovaný stejně jako kotva)
    delta-posun vůči svému vlastnímu výchozímu času, viz
    `TimelineEditor._drag_group_begin`. Podržený Alt (`_snap_bypassed`)
    snap úplně vypne — kotva i celá skupina se pak posune na pixel přesně."""
    x = max(HEADER_W, new_pos.x())
    if editor._drag_anchor is None or editor._drag_anchor is item:
        raw_t = (x - HEADER_W) / editor.pps
        t = raw_t if _snap_bypassed() else editor.snap_time(raw_t)
        start = editor._drag_group_start.get(id(item))
        if start is not None:
            editor._drag_group_delta = t - start
    else:
        start = editor._drag_group_start.get(id(item), float(item.event.get("time_s", 0.0)))
        t = max(0.0, start + editor._drag_group_delta)
    x = HEADER_W + t * editor.pps
    item.event["time_s"] = round(t, 3)
    return QPointF(x, lane_y)


class BlockItem(QGraphicsRectItem):
    """Jeden blok na časové ose (text, akord, nebo — `kind="tab"` — ASCII
    blok sólo tabulatury), navázaný na event dict."""

    def __init__(self, editor: "TimelineEditor", event: dict, kind: str, lane_y: float, height: float):
        super().__init__()
        self.editor = editor
        self.event = event          # odkaz do lyrics_timeline / chords_timeline
        self.kind = kind            # 'lyric' | 'chord'
        self.lane_y = lane_y
        self.h = height
        self._resizing = False
        self._undo_pushed = False   # jeden undo krok za celé tažení, ne za pixel
        self._syncing = False       # True = setPos přichází Z DAT, viz _sync_from_event
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self._sync_from_event()     # setPos PŘED ItemSendsGeometryChanges
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    # --- geometrie podle času/délky ---
    def _sync_from_event(self) -> None:
        """Nastaví polohu/velikost PODLE `self.event` (po parametrické editaci
        — panel vlastností, hromadný posun, zarovnání…). `self._syncing`
        musí být True po dobu `setPos`, jinak `itemChange` níž přichytí čas
        na mřížku a PŘEPÍŠE přesnou hodnotu, kterou právě dostal — tichý
        bug, který by rozbil každou "na číslo přesnou" editaci (přesně tohle
        se dělo: `shift_selected()` nastavilo přesný čas, ale zavolání téhle
        metody ho hned zase přeslapalo přichycením na mřížku)."""
        self._syncing = True
        try:
            pps = self.editor.pps
            t = float(self.event.get("time_s", 0.0))
            if self.kind == "lyric":
                dur = float(self.event.get("duration_s", 0.5))
            elif self.kind == "tab":
                dur = float(self.event.get("duration_s", TAB_BLOCK_DEFAULT_DUR_S))
            else:
                dur = float(self.event.get("duration_s", self.editor.default_chord_dur))
            w = max(BLOCK_MIN_W, dur * pps)
            self.setRect(0, 0, w, self.h)
            self.setPos(HEADER_W + t * pps, self.lane_y)
        finally:
            self._syncing = False

    def _label(self) -> str:
        if self.kind == "chord":
            return self.event.get("chord", "")
        return self.event.get("text", "")   # 'lyric' i 'tab' (ASCII blok, viz JSON_FORMAT.md §3.6)

    # --- vykreslení ---
    def paint(self, p: QPainter, opt, widget=None):
        base = TAB_COLOR if self.kind == "tab" else CHORD_COLOR if self.kind == "chord" else LYRIC_COLOR
        if self.isSelected():
            base = SEL_COLOR
        r = self.rect()
        p.setBrush(QBrush(base.lighter(150)))
        p.setPen(QPen(base, 1.5))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QPen(QColor("#1a1a1a")))
        if self.kind == "tab":
            # ASCII tabulatura — monospace, řádek po řádku (6 linek strun,
            # jak to napsal uživatel/GP), ne jeden vystředěný nápis
            f = QFont("Consolas", 9)
            p.setFont(f)
            fm = p.fontMetrics()
            lines = self._label().split("\n") or [""]
            y = r.top() + fm.ascent() + 2
            for line in lines:
                p.drawText(QPointF(r.left() + 4, y), line)
                y += fm.height()
            return
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
        self.editor._drag_group_begin(self)

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
            raw_end = start + raw_w / pps
            # přichyť KONEC na mřížku — podržený Alt to dočasně vypne (viz _snap_bypassed)
            end = raw_end if _snap_bypassed() else self.editor.snap_time(raw_end)
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
        self.editor._drag_group_finalize()
        self.editor._drag_group_end()

    def mouseDoubleClickEvent(self, ev):
        self.editor.edit_block(self)
        ev.accept()

    def contextMenuEvent(self, ev):
        self.editor._ensure_selected_for_context_menu(self)
        menu = QMenu()
        break_act = None
        first = None
        is_break = False
        if self.kind == "lyric":
            first = self.editor.first_word(self.event.get("track_index", 1))
            is_break = bool(self.event.get("line_start")) and self.event is not first
            if is_break:
                break_act = menu.addAction(tr("menu.unbreak_line"))
            else:
                break_act = menu.addAction(tr("menu.start_new_line"))
            menu.addSeparator()
        fwd_act = menu.addAction(tr("menu.select_forward"))
        back_act = menu.addAction(tr("menu.select_backward"))
        track_act = menu.addAction(tr("menu.select_track"))
        menu.addSeparator()
        copy_act = menu.addAction(tr("menu.copy"))
        cut_act = menu.addAction(tr("menu.cut"))
        del_act = menu.addAction(tr("menu.delete_generic"))
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
        elif chosen is track_act:
            self.editor.select_track_content(self)
        elif chosen is copy_act:
            self.editor.copy_selected()
        elif chosen is cut_act:
            self.editor.cut_selected()
        elif chosen is del_act:
            self.editor.delete_selected()
        ev.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if self._syncing:
                return value   # posun Z DAT (_sync_from_event) — nic nepočítej/nepřepisuj
            # undo se pushuje jen za CELOU skupinu (jednou, z kotvy tažení)
            # — viz TimelineEditor._drag_group_begin/_apply_group_snap
            if not self._resizing and self.editor._drag_anchor in (None, self):
                self._mark_dirty()
            return _apply_group_snap(self, self.editor, value, self.lane_y)
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
        self._syncing = False       # True = setPos přichází Z DAT, viz _sync_from_event
        self.setOffset(-icon_size / 2, -icon_size / 2)
        self.setZValue(15)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(tr("drum_hit.tooltip", drum=event.get('drum', '?')))
        self._sync_from_event()     # setPos PŘED ItemSendsGeometryChanges
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    def _sync_from_event(self) -> None:
        """Nastaví polohu PODLE `self.event` (parametrická editace/posun).
        `self._syncing` musí být True po dobu `setPos`, jinak `itemChange`
        níž přichytí čas na mřížku a PŘEPÍŠE přesnou hodnotu — viz shodný
        komentář u `BlockItem._sync_from_event`."""
        self._syncing = True
        try:
            t = float(self.event.get("time_s", 0.0))
            self.setPos(HEADER_W + t * self.editor.pps, self.row_y)
        finally:
            self._syncing = False

    def mousePressEvent(self, ev):
        self._undo_pushed = False   # nové tažení = nová undo hranice
        super().mousePressEvent(ev)
        self.editor._drag_group_begin(self)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        self.editor._drag_group_finalize()
        self.editor._drag_group_end()
        if self._undo_pushed:   # skutečně se táhlo (posunul se čas) — přehraj zvuk na novém místě
            self.editor._play_drum_sound(self.event.get("drum", "?"))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if self._syncing:
                return value   # posun Z DAT (_sync_from_event) — nic nepočítej/nepřepisuj
            # undo se pushuje jen za CELOU skupinu (jednou, z kotvy tažení)
            # — viz TimelineEditor._drag_group_begin/_apply_group_snap
            if not self._undo_pushed and self.editor._drag_anchor in (None, self):
                self.editor._push_undo()
                self._undo_pushed = True
            return _apply_group_snap(self, self.editor, value, self.row_y)
        return super().itemChange(change, value)

    def contextMenuEvent(self, ev):
        self.editor._ensure_selected_for_context_menu(self)
        menu = QMenu()
        sub = menu.addMenu(tr("menu.change_drum"))
        cur = self.event.get("drum", "?")
        for dn in DRUM_HIT_NAMES:
            act = sub.addAction(("● " if dn == cur else "   ") + dn)
            act.setData(dn)
        menu.addSeparator()
        fwd_act = menu.addAction(tr("menu.select_forward"))
        back_act = menu.addAction(tr("menu.select_backward"))
        track_act = menu.addAction(tr("menu.select_track"))
        menu.addSeparator()
        copy_act = menu.addAction(tr("menu.copy"))
        cut_act = menu.addAction(tr("menu.cut"))
        del_act = menu.addAction(tr("menu.delete_drum_hit"))
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
        elif chosen is track_act:
            self.editor.select_track_content(self)
        elif chosen is copy_act:
            self.editor.copy_selected()
        elif chosen is cut_act:
            self.editor.cut_selected()
        elif chosen.data():
            self.editor._push_undo()
            self.event["drum"] = chosen.data()
            self.event["midi"] = DRUM_NAME_TO_MIDI.get(chosen.data(), self.event.get("midi", 0))
            self.editor._relayout_and_reselect(self.event)
            self.editor._play_drum_sound(chosen.data())
        ev.accept()


class BassNoteItem(QGraphicsLineItem):
    """Jedna basová nota (event v bass_timeline), obdoba `DrumHitItem` —
    lze přetáhnout v čase (struna/Y je zamčená na vlastní řádek, mění se
    jen přes menu). Pravý klik = změna struny/pražce nebo smazání; Delete
    smaže vybranou notu (viz `delete_selected`). Editace hraje syntetizovaný
    'drnk' na správné výšce (viz `TimelineEditor._play_bass_note`)."""

    def __init__(self, editor: "TimelineEditor", event: dict, row_y: float, pps: float):
        super().__init__()
        self.editor = editor
        self.event = event
        self.row_y = row_y
        self._undo_pushed = False
        self._syncing = False
        p = QPen(QColor("#1a5fb4"), 3)
        p.setCapStyle(Qt.RoundCap)
        self.setPen(p)
        self.setZValue(15)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(tr("bass_note.tooltip", note=event.get('note_name', '?'),
                           string=event.get('string', '?'), fret=event.get('fret', '?')))
        self._sync_from_event()
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )

    def _sync_from_event(self) -> None:
        self._syncing = True
        try:
            t0 = float(self.event.get("time_s", 0.0))
            dur = max(0.02, float(self.event.get("duration_s", 0.2)))
            x0 = HEADER_W + t0 * self.editor.pps
            x1 = x0 + max(2.0, dur * self.editor.pps)
            self.setLine(0.0, 0.0, x1 - x0, 0.0)
            self.setPos(x0, self.row_y)
        finally:
            self._syncing = False

    def mousePressEvent(self, ev):
        self._undo_pushed = False
        super().mousePressEvent(ev)
        self.editor._drag_group_begin(self)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        self.editor._drag_group_finalize()
        self.editor._drag_group_end()
        if self._undo_pushed:   # skutečně se táhlo (posunul se čas) — přehraj notu na novém místě
            self.editor._play_bass_note(self.event.get("midi", 40))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if self._syncing:
                return value
            # undo se pushuje jen za CELOU skupinu (jednou, z kotvy tažení)
            # — viz TimelineEditor._drag_group_begin/_apply_group_snap
            if not self._undo_pushed and self.editor._drag_anchor in (None, self):
                self.editor._push_undo()
                self._undo_pushed = True
            return _apply_group_snap(self, self.editor, value, self.row_y)
        return super().itemChange(change, value)

    def contextMenuEvent(self, ev):
        self.editor._ensure_selected_for_context_menu(self)
        menu = QMenu()
        sub = menu.addMenu(tr("menu.change_string"))
        cur_s = int(self.event.get("string", 4))
        string_acts = []
        for s in (1, 2, 3, 4):
            act = sub.addAction(("● " if s == cur_s else "   ") + tr("lane.string", n=s))
            act.setData(s)
            string_acts.append(act)
        fret_act = menu.addAction(tr("menu.fret_ellipsis", fret=self.event.get('fret', 0)))
        menu.addSeparator()
        fwd_act = menu.addAction(tr("menu.select_forward"))
        back_act = menu.addAction(tr("menu.select_backward"))
        track_act = menu.addAction(tr("menu.select_track"))
        menu.addSeparator()
        copy_act = menu.addAction(tr("menu.copy"))
        cut_act = menu.addAction(tr("menu.cut"))
        del_act = menu.addAction(tr("menu.delete_bass_note"))
        chosen = menu.exec(ev.screenPos())
        if chosen is None:
            ev.accept()
            return
        if chosen is del_act:
            self.editor._push_undo()
            self.editor.remove_bass_note(self)
        elif chosen is fwd_act:
            self.editor.select_ripple(self, "forward")
        elif chosen is back_act:
            self.editor.select_ripple(self, "backward")
        elif chosen is track_act:
            self.editor.select_track_content(self)
        elif chosen is copy_act:
            self.editor.copy_selected()
        elif chosen is cut_act:
            self.editor.cut_selected()
        elif chosen is fret_act:
            fret, ok = QInputDialog.getInt(None, tr("dialog.fret_title"), tr("dialog.fret_label"),
                                           int(self.event.get("fret", 0)), 0, 24)
            if ok:
                self.editor._push_undo()
                self.editor.set_bass_note_pitch(self.event, cur_s, fret)
                self.editor._relayout_and_reselect(self.event)
                self.editor._play_bass_note(self.event.get("midi", 40))
        elif chosen in string_acts:
            self.editor._push_undo()
            self.editor.set_bass_note_pitch(self.event, chosen.data(), int(self.event.get("fret", 0)))
            self.editor._relayout_and_reselect(self.event)
            self.editor._play_bass_note(self.event.get("midi", 40))
        ev.accept()


class BassLaneBackground(QGraphicsRectItem):
    """Pozadí JEDNOHO řádku (jedné struny) basové stopy. Pravým kliknutím
    na PRÁZDNÉ místo přidá novou notu na téhle struně v daném čase
    (přichycen na aktuální mřížku) — obdoba `DrumRowBackground`."""

    def __init__(self, editor: "TimelineEditor", ti: int, string: int,
                 x: float, y: float, w: float, h: float):
        super().__init__(0, 0, w, h)
        self.editor = editor
        self.ti = ti
        self.string = string
        self.setPos(x, y)
        self.setBrush(QBrush(QColor("#eaf3ff")))
        self.setPen(QPen(Qt.NoPen))
        self.setToolTip(tr("bass_lane_bg.tooltip", string=string))

    def contextMenuEvent(self, ev):
        t = self.editor.snap_time(ev.pos().x() / self.editor.pps)
        menu = QMenu()
        act = menu.addAction(tr("menu.add_bass_note_here", string=self.string))
        paste_act = _add_paste_action(menu, self.editor)
        chosen = menu.exec(ev.screenPos())
        if chosen is act:
            fret, ok = QInputDialog.getInt(None, tr("dialog.fret_title"), tr("dialog.fret_label"),
                                           0, 0, 24)
            if ok:
                new_ev = self.editor.add_bass_note(self.ti, self.string, fret, t)
                self.editor._play_bass_note(new_ev["midi"])
        elif chosen is paste_act:
            self.editor.paste_at(t, target_ti=self.ti)
        ev.accept()


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


def _add_paste_action(menu: QMenu, editor: "TimelineEditor"):
    """Sdílená položka „Vložit sem“ pro kontextová menu pozadí stop
    (`DrumRowBackground`/`BassLaneBackground`/`TabLaneBackground`) — vrátí
    přidanou akci, nebo `None`, když je schránka prázdná (pak se do menu
    vůbec nepřidá nic navíc, viz `copy_selected`/`cut_selected`)."""
    if not editor._clipboard:
        return None
    menu.addSeparator()
    return menu.addAction(tr("menu.paste_here", n=len(editor._clipboard)))


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
        self.setToolTip(tr("drum_row_bg.tooltip", drum=drum_name))

    def contextMenuEvent(self, ev):
        t = self.editor.snap_time(ev.pos().x() / self.editor.pps)
        menu = QMenu()
        act = menu.addAction(tr("menu.add_drum_hit_here", drum=self.drum_name))
        paste_act = _add_paste_action(menu, self.editor)
        chosen = menu.exec(ev.screenPos())
        if chosen is act:
            self.editor.add_drum_hit(self.ti, self.drum_name, t)
        elif chosen is paste_act:
            self.editor.paste_at(t, target_ti=self.ti)
        ev.accept()


class WaveformItem(QGraphicsRectItem):
    """„Chlupatá čára" — tvar vlny nahraného MP3/WAV nad zdrojovými stopami
    (ať je vidět, kdy přesně nastupují bicí apod., proti mřížce tempa).

    Záměrně NEjde stříhat ani editovat jako ostatní prvky — jen:
      • táhnout celou stopu (posun v čase → `editor.audio_offset_s`),
      • táhnout dolní okraj (výška řádku → `editor.waveform_h`),
      • pravým kliknout → dialog s přesným posunem a ±10% roztažením
        (`editor.waveform_dialog`), pro dolazení na kulaté BPM.
    """

    def __init__(self, editor: "TimelineEditor", top: float, height: float, total_w: float):
        super().__init__()
        self.editor = editor
        self.top = top
        self.h = height
        self.setRect(0, 0, total_w - HEADER_W, height)
        self.setPos(HEADER_W, top)
        self.setPen(QPen(Qt.NoPen))
        self.setZValue(5)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(tr("waveform.tooltip"))
        self._dragging_offset = False
        self._resizing_height = False
        self._drag_start_x = 0.0
        self._drag_start_offset = 0.0
        self._undo_pushed = False

    def paint(self, p: QPainter, option, widget=None):
        r = self.rect()
        p.fillRect(r, QBrush(QColor("#f5f5f5")))
        editor = self.editor
        wf = editor.waveform
        exposed = option.exposedRect if option is not None else r
        x0 = max(0.0, exposed.left())
        x1 = min(r.width(), exposed.right())

        if not wf or len(wf) == 0:
            p.setPen(QPen(QColor("#8a97a3")))
            p.setFont(QFont("Segoe UI", 8))
            msg = (tr("waveform.loading") if editor._waveform_loading else
                  tr("waveform.no_preview"))
            p.drawText(r, Qt.AlignCenter, msg)
        elif x1 > x0:
            pps = editor.pps
            mid = self.h / 2
            amp = self.h / 2 * 0.94

            # Plný, SOUVISLÝ obrys (jako Audacity) — jeden bod na KAŽDÝ pixel
            # sloupce, žádné přeskakování/mezery. Vykreslí se jako vyplněný
            # polygon (horní obálka + dolní obálka pozpátku), ne jednotlivé
            # čárky — to dřívější řídnutí při větším zoomu vypadalo "rozbité".
            xi0 = int(x0)
            xi1 = int(x1) + 2
            upper: list[QPointF] = []
            lower: list[QPointF] = []
            for xi in range(xi0, xi1):
                a0 = editor._timeline_to_audio_t(xi / pps)
                a1 = editor._timeline_to_audio_t((xi + 1) / pps)
                if a1 >= 0 and a0 <= wf.duration_s:
                    mn, mx = wf.peak_range(max(0.0, a0), max(a0 + 1e-4, a1))
                else:
                    mn = mx = 0.0
                upper.append(QPointF(xi, mid - mx * amp))
                lower.append(QPointF(xi, mid - mn * amp))

            poly = QPolygonF(upper + lower[::-1])
            p.setPen(QPen(QColor("#1a5fb4"), 1))
            p.setBrush(QBrush(QColor("#5b9bd9")))
            p.drawPolygon(poly)

            p.setPen(QPen(QColor("#0d3b66"), 1))
            p.drawLine(QPointF(x0, mid), QPointF(x1, mid))

        # značky bodů zarovnání A/B (viz editor.set_align_point) — nakreslí
        # se VŽDY, i bez načtené vlnovky (bod jde nastavit i bez ní)
        for label, pt, color in (("A", editor._align_a, QColor("#2d7d2d")),
                                 ("B", editor._align_b, QColor("#9141ac"))):
            if pt is None:
                continue
            gx = pt[0] * editor.pps
            if x0 - 20 <= gx <= x1 + 20:
                pen = QPen(color, 2)
                p.setPen(pen)
                p.drawLine(QPointF(gx, 0), QPointF(gx, self.h))
                p.setBrush(QBrush(color))
                p.setPen(QPen(color.darker(130), 1))
                p.drawEllipse(QPointF(gx, 8), 7, 7)
                p.setPen(QPen(QColor("#ffffff")))
                p.setFont(QFont("Segoe UI", 7, QFont.Bold))
                p.drawText(QRectF(gx - 7, 1, 14, 14), Qt.AlignCenter, label)

    # --- interakce: posun celé stopy (tělo) / výška (dolní okraj) ---
    def hoverMoveEvent(self, ev):
        near_bottom = ev.pos().y() >= self.h - EDGE
        self.setCursor(Qt.SizeVerCursor if near_bottom else Qt.OpenHandCursor)
        super().hoverMoveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            super().mousePressEvent(ev)
            return
        self._undo_pushed = False
        if ev.pos().y() >= self.h - EDGE:
            self._resizing_height = True
            ev.accept()
            return
        self._dragging_offset = True
        self._drag_start_x = ev.scenePos().x()
        self._drag_start_offset = self.editor.audio_offset_s
        ev.accept()

    def mouseMoveEvent(self, ev):
        if self._resizing_height:
            new_h = max(WAVEFORM_MIN_H, min(WAVEFORM_MAX_H, ev.pos().y()))
            self.h = new_h
            self.prepareGeometryChange()
            self.setRect(0, 0, self.rect().width(), new_h)
            self.update()
            ev.accept()
            return
        if self._dragging_offset:
            if not self._undo_pushed:
                self.editor._push_undo()
                self._undo_pushed = True
            dt = (ev.scenePos().x() - self._drag_start_x) / self.editor.pps
            new_offset = self._drag_start_offset + dt   # smí i do záporna
            self.editor.set_audio_offset(new_offset, relayout=False)
            self.update()
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._resizing_height:
            self._resizing_height = False
            self.editor.waveform_h = self.h
            self.editor.data.setdefault("meta", {})["waveform_track_height"] = self.h
            self.editor._relayout()   # přepočítá pozice stop POD vlnovkou
            ev.accept()
            return
        if self._dragging_offset:
            self._dragging_offset = False
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self.editor.waveform_dialog()
        ev.accept()

    def contextMenuEvent(self, ev):
        t_click = ev.pos().x() / self.editor.pps
        menu = QMenu()
        auto_act = menu.addAction(tr("menu.auto_align_rhythm"))
        menu.addSeparator()
        a_act = menu.addAction(
            ("↻ " if self.editor._align_a else "📍 ") + tr("menu.set_align_point_a"))
        b_act = menu.addAction(
            ("↻ " if self.editor._align_b else "📍 ") + tr("menu.set_align_point_b"))
        apply_act = None
        clear_act = None
        if self.editor._align_a and self.editor._align_b:
            menu.addSeparator()
            apply_act = menu.addAction(tr("menu.apply_align_points"))
        if self.editor._align_a or self.editor._align_b:
            clear_act = menu.addAction(tr("menu.clear_align_points"))
        menu.addSeparator()
        edit_act = menu.addAction(tr("menu.waveform_position"))
        chosen = menu.exec(ev.screenPos())
        if chosen is auto_act:
            self.editor.auto_align_audio()
        elif chosen is a_act:
            self.editor.set_align_point("A", t_click)
        elif chosen is b_act:
            self.editor.set_align_point("B", t_click)
        elif chosen is apply_act:
            self.editor.apply_alignment()
        elif chosen is clear_act:
            self.editor.clear_align_points()
        elif chosen is edit_act:
            self.editor.waveform_dialog()
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
        title = QLabel()
        tr_label(title, "properties_panel.title")
        title.setStyleSheet("font-weight:bold; padding:4px;")
        root.addWidget(title)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        root.addWidget(self.form_host)
        self.placeholder = QLabel()
        tr_label(self.placeholder, "properties_panel.placeholder")
        self.placeholder.setStyleSheet("color:#999999; padding:8px;")
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
        known = isinstance(item, (BlockItem, DrumHitItem))
        self.form_host.setVisible(known)
        self.placeholder.setVisible(not known)
        if not known:
            return
        if isinstance(item, BlockItem):
            self._build_block_form(item)
        elif isinstance(item, DrumHitItem):
            self._build_drum_form(item)

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

        self.form.addRow(tr("properties_panel.chord_label") if is_chord else tr("properties_panel.text_label"), text)
        self.form.addRow(tr("properties_panel.time_label"), t_sb)
        self.form.addRow(tr("properties_panel.duration_label"), d_sb)

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
            self.editor._play_drum_sound(v)
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

        self.form.addRow(tr("properties_panel.drum_label"), combo)
        self.form.addRow(tr("properties_panel.time_label"), t_sb)
        self.form.addRow(tr("properties_panel.duration_label"), d_sb)

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
        # Ctrl+Z/Ctrl+Shift+Z/Ctrl+Y/Delete/Backspace/S dřív byly řešené
        # natvrdo tady — teď existují jako QAction se stejnou zkratkou na
        # úrovni hlavního okna (menu Úpravy/Časová osa), takže tady zůstaly
        # by způsobily DVOJITÉ vyvolání (např. dvojité undo na jeden
        # stisk). `]`/`[` (ripple-select) do menu nejdou, zůstávají tady.
        if ev.key() == Qt.Key_BracketRight:   # ] — vyber od vybraného prvku DÁL v čase
            self.editor.select_ripple_from_selection("forward")
            ev.accept()
            return
        if ev.key() == Qt.Key_BracketLeft:    # [ — vyber od vybraného prvku DŘÍV v čase
            self.editor.select_ripple_from_selection("backward")
            ev.accept()
            return
        super().keyPressEvent(ev)


class TrackMixPanel(QWidget):
    """Zamrzlý sloupec s řádky časové osy pro levý dock ("Stopy") —
    na rozdíl od hlaviček uvnitř QGraphicsScene (ty odjedou pryč při
    vodorovném rolování časové osy, protože jsou obyčejný obsah scény,
    viz `HEADER_W`), tohle je samostatný Qt widget, co zůstává VŽDY
    vidět bez ohledu na to, kam je timeline odscrollovaná.

    v1 rozsah (viz konverzace): jen seznam řádků + ovládání, které už
    dává smysl mít — hlasitost/mute přehrávače nahrávky a GP mixu bicích,
    hlasitost cvaknutí bicích při editaci, skrýt/zobrazit zdrojovou
    stopu. NENÍ pixel-přesně svisle synchronizovaný se scrollem časové
    osy (to by vyžadovalo mnohem těžší přestavbu) — je to prostý,
    vždy-viditelný seznam, ne zarovnaný "frozen header" po řádcích."""

    def __init__(self, editor: "TimelineEditor", parent=None):
        super().__init__(parent)
        self.editor = editor
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(2, 2, 2, 2)
        self.vbox.setSpacing(2)

    def _clear(self) -> None:
        while self.vbox.count():
            item = self.vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _add_row(self, widgets: list) -> None:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(2, 1, 2, 1)
        h.setSpacing(4)
        for w in widgets:
            h.addWidget(w)
        self.vbox.addWidget(row)

    def _vol_slider(self, value: float, on_change) -> QSlider:
        sb = QSlider(Qt.Horizontal)
        sb.setRange(0, 100)
        sb.setValue(int(round(value * 100)))
        sb.setMaximumWidth(90)
        sb.valueChanged.connect(on_change)
        return sb

    def refresh(self) -> None:
        """Znovu postaví všechny řádky podle AKTUÁLNÍHO stavu editoru
        (seznam stop, hlasitosti…). Voláno z `TimelineEditor._relayout()`
        — stopy se v běžné editaci nepřidávají/neubírají často, takže
        plné přestavění není drahé."""
        self._clear()
        ed = self.editor

        name = QLabel(tr("mix_panel.recording"))
        mute = QCheckBox(tr("mix_panel.mute"))
        mute.setChecked(ed._rec_muted)
        mute.toggled.connect(ed._on_rec_mute)
        vol = self._vol_slider(ed._rec_volume, ed._on_rec_volume)
        self._add_row([name, mute, vol])

        name = QLabel(tr("mix_panel.gp_drum_mix"))
        mute = QCheckBox(tr("mix_panel.mute"))
        mute.setChecked(ed._gp_muted)
        mute.toggled.connect(ed._on_gp_mute)
        vol = self._vol_slider(ed._gp_volume, ed._on_gp_volume)
        self._add_row([name, mute, vol])

        name = QLabel(tr("mix_panel.gp_bass_mix"))
        mute = QCheckBox(tr("mix_panel.mute"))
        mute.setChecked(ed._bass_muted)
        mute.toggled.connect(ed._on_bass_mute)
        vol = self._vol_slider(ed._bass_volume, ed._on_bass_volume)
        self._add_row([name, mute, vol])

        name = QLabel(tr("mix_panel.drums_editing"))
        vol = self._vol_slider(ed._drum_click_volume, ed._on_drum_click_volume)
        self._add_row([name, vol])

        name = QLabel(tr("mix_panel.bass_editing"))
        vol = self._vol_slider(ed._bass_click_volume, ed._on_bass_click_volume)
        self._add_row([name, vol])

        sep = QLabel(tr("mix_panel.tracks_separator"))
        sep.setStyleSheet("color:#8a8a8a; font-size:10px;")
        self.vbox.addWidget(sep)

        names = ed._track_names()
        for ti in ed._track_order():
            is_drums = ed._track_is_drums(ti)
            label = ("🥁 " if is_drums else "") + names.get(ti, _track_fallback_name(ti))
            name = QLabel(label)
            name.setWordWrap(True)
            hide = QCheckBox(tr("mix_panel.hide"))
            hide.setChecked(ti in ed._hidden_tracks)
            hide.toggled.connect(lambda checked, t=ti: ed._on_track_hide_toggle(t, checked))
            self._add_row([name, hide])

        self.vbox.addStretch()


class TimelineEditor(QWidget):
    """Widget časové osy. Napojení: load_data(dict) → editace → to_json()."""

    # Signalizuje změnu `_dirty` (neuložené změny) — hlavní okno na to
    # napojuje živé přeznačení titulku karty (• prefix), viz
    # guitar_pro_viewer.py._new_tab/_refresh_tab_label.
    dirty_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        # "Identita dokumentu" — dřív žila jako singleton na GuitarProViewer,
        # teď patří SEM, protože každá karta (viz plán "Multi-dokument") je
        # vlastní TimelineEditor instance a musí si nést svoje vlastní GP
        # song/cesty/tempo mapu, ne sdílet je s ostatními otevřenými písněmi.
        self.song = None                              # guitarpro.Song | None
        self.current_gp_file: Optional[str] = None    # jen import — NIKDY cíl pro Uložit
        self.current_json_file: Optional[str] = None  # skutečný cíl pro "Uložit"
        self.tempo_map: list = []
        self._loaded_json: Optional[dict] = None
        self._dirty: bool = False   # True od první změny po načtení/uložení — viz _push_undo/load_data
        # Nastaví guitar_pro_viewer.py._on_loaded, když GP dokončí načítání
        # na pozadí zatímco tahle karta NENÍ aktivní — dokreslení doků se
        # pak odloží na okamžik, kdy se na kartu skutečně přepne (viz
        # _on_active_tab_changed), místo přepisování docků jiné, právě
        # zobrazené karty.
        self._pending_gp_populate: bool = False
        self.data: dict[str, Any] = {}
        self.tracks: list[dict] = []
        self.pps: float = 60.0                # px za sekundu (zoom)
        self.snap_s: float = 0.0              # 0 = bez přichycení
        self.default_chord_dur: float = 1.0
        self.playhead_s: float = 0.0             # pozice kurzoru (s)
        self._playhead: Optional[PlayheadItem] = None
        self.blocks: list[BlockItem] = []
        self.drum_hit_items: list[DrumHitItem] = []   # úhozy bicí stopy
        self.bass_note_items: list[BassNoteItem] = []   # noty basové stopy
        # POZOR: schránka je SDÍLENÁ napříč všemi kartami (modulová
        # `_SHARED_CLIPBOARD`, viz konec souboru) — NENÍ to instanční
        # atribut, schválně, aby "kopírovat v písni A, vložit v písni B"
        # fungovalo beze změny copy_selected/cut_selected/paste_at.
        self._track_lane: dict[int, dict] = {}   # track_index → {'chord_y','lyric_y'}
        self.export_callback = None              # nastaví hlavní okno: fn(dict, path, silent)
        # Co poslední `to_json()` musel na Displej stopě uklidit, aby JSON
        # splnil invariant formátu (nepřekrývající se klipy) — hlavní okno
        # to po uložení vypíše uživateli, ať se klipy nemažou potichu.
        self._drum_icon_pixmaps: dict[tuple[str, int], QPixmap] = {}
        self.bpm: float = 120.0
        self.beats_per_measure: int = 4
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        # skupinové tažení myší (viz _drag_group_begin/_apply_group_snap)
        self._drag_anchor = None
        self._drag_group_items: list = []
        self._drag_group_start: dict[int, float] = {}
        self._drag_group_delta: float = 0.0
        # stopy dočasně skryté z rozvržení (session-only, ne v JSONu) —
        # nastaveno z TrackMixPanel v levém docku, viz _on_track_hide_toggle
        self._hidden_tracks: set[int] = set()
        # levý sloupec s názvy stop — PŘIMRAZENÝ: [(item, základní x), …],
        # při vodorovném rolování se posouvá s výřezem, ať zůstane vidět
        # (viz _register_header_item/_pin_headers)
        self._header_items: list[tuple[QGraphicsItem, float]] = []

        # --- přehrávání MP3/WAV synchronizované s playheadem (viz set_playhead) ---
        self.audio_path: Optional[str] = None
        self._rec_volume: float = 1.0        # hlasitost nahrávky (mix panel "🎵 Nahrávka")
        self._rec_muted: bool = False
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(self._rec_volume)
        self.audio_output.setMuted(self._rec_muted)
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_audio_error)

        # --- druhý přehrávač: syntetizovaný mixdown bicích PŘÍMO Z GP DAT
        # (drums_timeline), aby šlo poslechem porovnat "co říká GP soubor"
        # se skutečnou nahrávkou (self.player) vedle sebe — viz
        # render_drums_mixdown/_load_gp_mix. Sdílí transport (play/pause/
        # stop/seek/shuttle) s nahrávkou, ale má NEZÁVISLOU hlasitost (mix
        # slider "GP bicí"), aby šlo obojí zeslabit/ztlumit a A/B poslouchat.
        # ČISTĚ informativní pomůcka pro člověka — nic sama neopravuje.
        self.gp_audio_path: Optional[str] = None
        self._gp_volume: float = 1.0
        self._gp_muted: bool = False
        self.gp_player = QMediaPlayer(self)
        self.gp_audio_output = QAudioOutput(self)
        self.gp_audio_output.setVolume(self._gp_volume)
        self.gp_audio_output.setMuted(self._gp_muted)
        self.gp_player.setAudioOutput(self.gp_audio_output)
        self.gp_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.gp_player.errorOccurred.connect(self._on_gp_audio_error)

        # --- třetí přehrávač: syntetizovaný mixdown BASY z GP dat
        # (bass_timeline) — samostatné tlačítko/hlasitost/mute NEZÁVISLE
        # na bicích (viz `_load_bass_mix`/`render_bass_mixdown`), ale sdílí
        # stejný transport (play/pause/stop/seek/shuttle) se zbylými dvěma.
        self.bass_audio_path: Optional[str] = None
        self._bass_volume: float = 1.0
        self._bass_muted: bool = False
        self.bass_player = QMediaPlayer(self)
        self.bass_audio_output = QAudioOutput(self)
        self.bass_audio_output.setVolume(self._bass_volume)
        self.bass_audio_output.setMuted(self._bass_muted)
        self.bass_player.setAudioOutput(self.bass_audio_output)
        self.bass_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.bass_player.errorOccurred.connect(self._on_bass_audio_error)

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

        # --- vlnovka nahrávky (WaveformItem) nad zdrojovými stopami ---
        self.waveform: Optional[WaveformData] = None
        self._waveform_loading = False
        self.waveform_item: Optional[WaveformItem] = None
        self.waveform_h: float = WAVEFORM_DEFAULT_H
        self.audio_offset_s: float = 0.0     # posun celé nahrávky v čase
        self.audio_stretch: float = 1.0      # ±10 % natažení/stlačení délky
        self._waveform_loader = WaveformLoader(self)
        self._waveform_loader.finished.connect(self._on_waveform_ready)
        self._waveform_loader.failed.connect(self._on_waveform_failed)
        # zarovnání dle 2 bodů (klik na vlnovku poblíž špičky → najde
        # skutečný úder) — session stav, neukládá se do JSONu, viz
        # set_align_point/apply_alignment
        self._align_a: Optional[tuple[float, float]] = None   # (grid_t, audio_t)
        self._align_b: Optional[tuple[float, float]] = None

        # --- syntetizované zvuky bicích pro sluchovou zpětnou vazbu při
        # editaci (přidání/přesun/změna typu úderu) — viz _play_drum_sound ---
        self._drum_click_sounds: dict[str, QSoundEffect] = {}
        self._drum_real_sounds: dict[str, QSoundEffect] = {}   # klíč = cesta k WAV, viz _real_drum_sample_path
        self._drum_click_volume: float = 0.7   # mix panel "🥁 Bicí (editace)"

        # --- syntetizovaný zvuk basové noty pro sluchovou zpětnou vazbu při
        # editaci (přidání/přetažení/změna výšky) — viz _play_bass_note ---
        self._bass_note_sounds: dict[int, QSoundEffect] = {}   # klíč = MIDI nota
        self._bass_click_volume: float = 0.7   # mix panel "🎸 Basa (editace)"

        self._setup_ui()
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.mix_panel = TrackMixPanel(self)
        self.mix_panel.refresh()

    @property
    def _clipboard(self) -> list[dict]:
        return _SHARED_CLIPBOARD

    @_clipboard.setter
    def _clipboard(self, value: list[dict]) -> None:
        _SHARED_CLIPBOARD[:] = value

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # Horní panel tlačítek byl přesunut do hlavního menu/toolbaru okna
        # (guitar_pro_viewer.py — menu Úpravy/Časová osa + toolbar "Časová
        # osa"), viz plán "Přestavba GUI". `snap_combo`/`time_lbl` sem ale
        # patří obsahově (řídí/zobrazují stav TÉTO scény) — zůstávají tu
        # jako widgety, hlavní okno je jen PŘEVEZME do svého toolbaru
        # (`toolbar.addWidget(self.timeline.snap_combo)` reparentuje, to je
        # v Qt běžné a bezpečné).
        self.snap_combo = QComboBox()
        self.snap_combo.setMaximumHeight(24)
        register_tr(self.snap_combo.setToolTip, "timeline.snap.tooltip")
        _snap_items = [("timeline.snap_off", None), ("timeline.snap_q_beat", "q_beat"),
                       ("timeline.snap_h_beat", "h_beat"), ("timeline.snap_beat", "beat"),
                       ("timeline.snap_bar", "bar")]

        def _resync_snap_combo_labels(_unused=None):
            # QComboBox položky nejdou "setText"-ovat jednotlivě přes
            # register_tr — přestaví se celé, se zachováním výběru.
            idx = self.snap_combo.currentIndex()
            self.snap_combo.blockSignals(True)
            self.snap_combo.clear()
            for label_key, key in _snap_items:
                self.snap_combo.addItem(tr(label_key), key)
            self.snap_combo.setCurrentIndex(max(0, idx))
            self.snap_combo.blockSignals(False)

        register_tr(_resync_snap_combo_labels, "timeline.snap_beat")
        self.snap_combo.setCurrentIndex(3)   # výchozí: 1 beat — hudebně smysluplné
        self.snap_combo.currentIndexChanged.connect(self._update_snap_s)

        self.time_lbl = QLabel("⏱ 0,00 s")
        self.time_lbl.setMaximumHeight(24)
        self.time_lbl.setStyleSheet(
            "color:#e01b24; font-weight:bold; font-family:Consolas; "
            "padding:1px 6px; border:1px solid #e0a0a0; border-radius:3px;")
        register_tr(self.time_lbl.setToolTip, "timeline.time_label.tooltip")

        # styly použité dál dole u tlačítek spodní audio lišty
        btn_style = ("QPushButton{padding:2px 8px;}")
        colored_style = ("QPushButton{{background:{bg};color:white;padding:2px 8px;"
                         "border-radius:3px;font-weight:bold;}}"
                         "QPushButton:hover{{background:{hov};}}")

        self.scene = QGraphicsScene(self)
        self.view = TimelineView(self.scene, self)
        # levý sloupec s názvy stop zůstává přilepený vlevo i při
        # vodorovném rolování (viz _pin_headers)
        self.view.horizontalScrollBar().valueChanged.connect(
            lambda _v: self._pin_headers())
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
        # musí se vejít celý jog ovladač (128 px) i s okraji layoutu —
        # nižší strop mu ořezával spodek mezikruží
        audio_widget.setMaximumHeight(142)
        audio_bar = QHBoxLayout(audio_widget)
        audio_bar.setContentsMargins(8, 4, 8, 4)

        load_audio_btn = QPushButton()
        tr_label(load_audio_btn, "audio.load")
        load_audio_btn.setStyleSheet(btn_style)
        load_audio_btn.setIcon(std_icon(self, QStyle.StandardPixmap.SP_DirOpenIcon))
        load_audio_btn.clicked.connect(self._load_audio_dialog)
        audio_bar.addWidget(load_audio_btn)

        self.audio_label = QLabel()
        tr_label(self.audio_label, "audio.no_audio")
        self.audio_label.setStyleSheet("color:#999999;")
        audio_bar.addWidget(self.audio_label)

        audio_bar.addSpacing(12)
        output_lbl = QLabel()
        tr_label(output_lbl, "audio.output_label")
        audio_bar.addWidget(output_lbl)
        self.device_combo = QComboBox()
        self.device_combo.setMaximumHeight(24)
        self.device_combo.setMinimumWidth(160)
        register_tr(self.device_combo.setToolTip, "audio.device_combo.tooltip")
        self._populate_device_combo()
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        audio_bar.addWidget(self.device_combo)

        refresh_btn = QPushButton("↻")
        refresh_btn.setMaximumWidth(28)
        refresh_btn.setStyleSheet(btn_style)
        refresh_btn.setIcon(std_icon(self, QStyle.StandardPixmap.SP_BrowserReload))
        register_tr(refresh_btn.setToolTip, "audio.refresh.tooltip")
        refresh_btn.clicked.connect(self._populate_device_combo)
        audio_bar.addWidget(refresh_btn)

        test_btn = QPushButton()
        tr_label(test_btn, "audio.test_tone")
        test_btn.setStyleSheet(btn_style)
        test_btn.setIcon(std_icon(self, QStyle.StandardPixmap.SP_MediaVolume))
        register_tr(test_btn.setToolTip, "audio.test_tone.tooltip")
        test_btn.clicked.connect(self._play_test_tone)
        audio_bar.addWidget(test_btn)

        waveform_btn = QPushButton()
        tr_label(waveform_btn, "audio.waveform_pos")
        waveform_btn.setStyleSheet(btn_style)
        waveform_btn.setIcon(icon("waveform_align"))
        register_tr(waveform_btn.setToolTip, "audio.waveform_pos.tooltip")
        waveform_btn.clicked.connect(self.waveform_dialog)
        audio_bar.addWidget(waveform_btn)

        auto_align_btn = QPushButton()
        tr_label(auto_align_btn, "audio.auto_align")
        auto_align_btn.setStyleSheet(btn_style)
        auto_align_btn.setIcon(icon("auto_align_rhythm"))
        register_tr(auto_align_btn.setToolTip, "audio.auto_align.tooltip")
        auto_align_btn.clicked.connect(self.auto_align_audio)
        audio_bar.addWidget(auto_align_btn)

        check_dur_btn = QPushButton()
        tr_label(check_dur_btn, "audio.check_duration")
        check_dur_btn.setStyleSheet(btn_style)
        check_dur_btn.setIcon(std_icon(self, QStyle.StandardPixmap.SP_DialogApplyButton))
        register_tr(check_dur_btn.setToolTip, "audio.check_duration.tooltip")
        check_dur_btn.clicked.connect(self._check_duration_vs_audio)
        audio_bar.addWidget(check_dur_btn)

        audio_bar.addSpacing(12)
        gp_mix_btn = QPushButton()
        tr_label(gp_mix_btn, "audio.gp_mix")
        gp_mix_btn.setStyleSheet(btn_style)
        gp_mix_btn.setIcon(icon("gp_drum_mix"))
        register_tr(gp_mix_btn.setToolTip, "audio.gp_mix.tooltip")
        gp_mix_btn.clicked.connect(self._load_gp_mix)
        audio_bar.addWidget(gp_mix_btn)

        self.gp_audio_label = QLabel()
        tr_label(self.gp_audio_label, "audio.gp_mix_not_generated")
        self.gp_audio_label.setStyleSheet("color:#999999;")
        audio_bar.addWidget(self.gp_audio_label)

        audio_bar.addSpacing(12)
        bass_mix_btn = QPushButton()
        tr_label(bass_mix_btn, "audio.bass_mix")
        bass_mix_btn.setStyleSheet(btn_style)
        bass_mix_btn.setIcon(icon("gp_drum_mix"))
        register_tr(bass_mix_btn.setToolTip, "audio.bass_mix.tooltip")
        bass_mix_btn.clicked.connect(self._load_bass_mix)
        audio_bar.addWidget(bass_mix_btn)

        self.bass_audio_label = QLabel()
        tr_label(self.bass_audio_label, "audio.bass_mix_not_generated")
        self.bass_audio_label.setStyleSheet("color:#999999;")
        audio_bar.addWidget(self.bass_audio_label)

        audio_bar.addSpacing(8)
        audio_bar.addWidget(QLabel("🎵"))
        rec_vol = QSlider(Qt.Horizontal)
        rec_vol.setRange(0, 100)
        rec_vol.setValue(100)
        rec_vol.setMaximumWidth(70)
        rec_vol.setToolTip(tr("audio.rec_volume.tooltip"))
        rec_vol.valueChanged.connect(self._on_rec_volume)
        audio_bar.addWidget(rec_vol)

        audio_bar.addWidget(QLabel("🥁"))
        gp_vol = QSlider(Qt.Horizontal)
        gp_vol.setRange(0, 100)
        gp_vol.setValue(100)
        gp_vol.setMaximumWidth(70)
        gp_vol.setToolTip(tr("audio.gp_volume.tooltip"))
        gp_vol.valueChanged.connect(self._on_gp_volume)
        audio_bar.addWidget(gp_vol)

        audio_bar.addWidget(QLabel("🎸"))
        bass_vol = QSlider(Qt.Horizontal)
        bass_vol.setRange(0, 100)
        bass_vol.setValue(100)
        bass_vol.setMaximumWidth(70)
        bass_vol.setToolTip(tr("audio.bass_volume.tooltip"))
        bass_vol.valueChanged.connect(self._on_bass_volume)
        audio_bar.addWidget(bass_vol)

        audio_bar.addStretch()

        self.jog = JogShuttleWidget()
        self.jog.playPauseRequested.connect(self.toggle_play_pause)
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

    @staticmethod
    def _normalize_input(data: dict) -> dict:
        """Cokoli, co přijde zvenčí, převede na vnitřní tvar editoru.

        - `schemaVersion: 1` (soubor z disku) → rozbalí se do plochých
          sekundových os a konce se dopočítají; v novém formátu se
          neukládají.
        - Cokoli jiného je **plochý pracovní tvar** rovnou od producenta
          (web import, GP import) — ten se nikam nepřevádí, protože JE
          vnitřní model editoru. Na disk se stejně dostane až přes
          `to_json()`, které vydá nový formát.

        Žádný převod starých souborů tu schválně není: na disk se od teď
        zapisuje jen nový model. Kdo potřebuje překlopit starší soubor,
        má na to `format_migrate.py` jako samostatný nástroj — do běhu
        aplikace nepatří.
        """
        if not data:
            return {}
        if int(data.get("schemaVersion", 0) or 0) == 1:
            from format_migrate import to_editor_model
            return to_editor_model(data)
        return data

    def load_data(self, data: dict) -> None:
        self._dirty = False   # čerstvě načtená/prezentovaná data = zatím žádné neuložené změny
        self._stop_shuttle_timer()
        self.player.stop()
        self.audio_path = None
        self.waveform = None
        self._waveform_loading = False
        self._waveform_loader.cancel()
        self._align_a = None
        self._align_b = None
        if hasattr(self, "audio_label"):
            self.audio_label.setText(tr("audio.no_audio"))
            self.audio_label.setStyleSheet("color:#999999;")
        # GP mix je vyrenderovaný ze STARÝCH dat — po načtení jiné/nové
        # písně už neplatí, nesmí zůstat hrát/nabídnutý ke starým bicím.
        self.gp_player.stop()
        self.gp_player.setSource(QUrl())
        self.gp_audio_path = None
        if hasattr(self, "gp_audio_label"):
            self.gp_audio_label.setText(tr("audio.gp_mix_not_generated"))
            self.gp_audio_label.setStyleSheet("color:#999999;")
        self.bass_player.stop()
        self.bass_player.setSource(QUrl())
        self.bass_audio_path = None
        if hasattr(self, "bass_audio_label"):
            self.bass_audio_label.setText(tr("audio.bass_mix_not_generated"))
            self.bass_audio_label.setStyleSheet("color:#999999;")
        # Vstupní hranice formátu: editor uvnitř pracuje s plochým sekundovým
        # tvarem, ale na disku je nový model (`schemaVersion: 1`). Starý
        # `format_version: 2` se cestou převede, takže se dřívější soubory
        # otevřou beze změny chování a uloží se už v novém formátu.
        self.data = self._normalize_input(data or {})
        self.tracks = self.data.get("tracks", []) or []
        self._undo_stack.clear()   # nová/jiná píseň → historie z předchozí neplatí
        self._redo_stack.clear()
        self._hidden_tracks.clear()   # indexy stop z předchozí písně by tu jinak sedly na jiné
        # hudební mřížka — VŽDY dle tempa (žádné odhady z textu)
        meta = self.data.get("meta", {}) or {}
        self.bpm = meta.get("tempo_bpm", 120) or 120
        self.beat_s = 60.0 / self.bpm
        self.beats_per_measure = int(meta.get("beats_per_measure", 4) or 4)
        self.bar_s = self.beat_s * self.beats_per_measure
        self.count_in_s = float(meta.get("count_in_s", 0.0) or 0.0)
        self.default_chord_dur = round(self.beat_s, 3)
        # poloha/roztažení nahrávky + výška vlnovky — perzistentní přes meta
        # (viz set_audio_offset/set_audio_stretch/WaveformItem)
        self.audio_offset_s = float(meta.get("audio_offset_s", 0.0) or 0.0)
        self.audio_stretch = float(meta.get("audio_stretch", 1.0) or 1.0)
        self.waveform_h = float(meta.get("waveform_track_height", WAVEFORM_DEFAULT_H)
                                or WAVEFORM_DEFAULT_H)
        self._update_snap_s()
        self._seed_line_starts()
        self._relayout(full=True)
        # cesta k audiu se ukládá do meta.audio_file (viz load_audio) — pokud
        # z tohoto počítače existuje, rovnou ho napoj; jinak nech tiše
        # "(žádné audio)" (cesta z jiného stroje nesmí shodit otevření JSONu)
        audio_file = meta.get("audio_file")
        if audio_file and os.path.isfile(audio_file):
            self.load_audio(audio_file)
        # AŽ NAKONEC — posluchač (titulek karty) čte `self.data`/meta.title,
        # takže emit musí přijít až po jejich naplnění, ne na začátku metody.
        self.dirty_changed.emit(False)

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

    def _music_grid(self):
        """Mřížka taktů a dob pro aktuální píseň (tempo + metrum).

        Jediná implementace převodu sekundy ↔ takt je v `music_grid.py` a
        používá ji i konvertor formátu — dvě mírně odlišná číslování taktů
        by se dřív nebo později rozešla a hledalo by se to špatně.

        Cachuje se, protože pravítko se překresluje při každém posunu, ale
        zneplatní se, jakmile se změní tempo nebo metrum.
        """
        from music_grid import TimeGrid
        tempo_map = self.data.get("tempo_map") or [{"tick": 960, "bpm": self.bpm}]
        meter_map = self.data.get("meter_map") or None
        key = (id(tempo_map), len(tempo_map), self.bpm, self.beats_per_measure,
               len(meter_map or ()), repr(meter_map))
        if getattr(self, "_grid_key", None) != key:
            self._grid_cache = TimeGrid(tempo_map, self.beats_per_measure,
                                        meter_map=meter_map)
            self._grid_key = key
        return self._grid_cache

    def _track_names(self) -> dict[int, str]:
        names = {t.get("index", i + 1): (t.get("name") or _track_fallback_name(i + 1))
                 for i, t in enumerate(self.tracks)}
        for ti in self._track_order():
            names.setdefault(ti, _track_fallback_name(ti))
        return names

    def _song_end(self) -> float:
        max_t = 1.0
        for ev in self.data.get("lyrics_timeline", []) + self.data.get("chords_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 1)))
        # bicí/basa (dřív chyběly — instrumentální outro může mít bicí déle,
        # než sahá poslední text/akord, a zrovna DÉLKA bicích z GP je to,
        # co chceme umět ověřit proti reálné nahrávce — viz set_bpm/waveform)
        for ev in (self.data.get("drums_timeline", []) + self.data.get("bass_timeline", [])
                   + self.data.get("tab_timeline", [])):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 0)))
        for t in self.tracks:
            for b in t.get("beats", []) or []:
                max_t = max(max_t, float(b.get("time_s", 0)) + float(b.get("duration_s", 0)))
        return round(max_t, 3)

    # --- karaoke řádky: seskupení slov, hranice ---

    def _lyrics_of(self, ti: int) -> list[dict]:
        return sorted(
            (e for e in self.data.get("lyrics_timeline", []) if e.get("track_index", 1) == ti),
            key=lambda e: e["time_s"],
        )

    def first_word(self, ti: int):
        evs = self._lyrics_of(ti)
        return evs[0] if evs else None

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

    @staticmethod
    def _build_chord_line_for_words(words_with_times: list[tuple[str, float]],
                                     chords: list[dict]) -> tuple[str, str]:
        """Sestaví (řádek akordů, textový řádek) tak, aby `align_chords_to_words()`
        (web_import.py) při zpětném parsování přiřadila akord ke SPRÁVNÉMU
        slovu — akord se umístí do sloupce, kde v textovém řádku začíná
        slovo časově nejbližší danému akordu (přesná inverze toho, jak
        `align_chords_to_words` čte sloupce zpátky)."""
        word_cols: list[int] = []
        col = 0
        for w, _t in words_with_times:
            word_cols.append(col)
            col += len(w) + 1
        lyric_line = " ".join(w for w, _t in words_with_times)

        positions: list[tuple[int, str]] = []
        for c in sorted(chords, key=lambda e: float(e.get("time_s", 0.0))):
            name = c.get("chord", "")
            if not name:
                continue
            ctime = float(c.get("time_s", 0.0))
            idx = min(range(len(words_with_times)),
                      key=lambda i: abs(words_with_times[i][1] - ctime))
            positions.append((word_cols[idx], name))

        chord_line = ""
        for want_col, name in positions:
            start = max(want_col, len(chord_line) + (1 if chord_line else 0))
            chord_line += " " * (start - len(chord_line)) + name
        return chord_line, lyric_line

    def export_chord_chart_text(self) -> str:
        """Rekonstruuje syrový chord-chart text (akordy nad textem — přesně
        formát dialogu "Nová píseň — text z webu/vlastní") z AKTUÁLNÍHO
        obsahu lyrics_timeline/chords_timeline. Základ pro
        "✎ Upravit text a akordy…": načíst → opravit → poskládat zpátky,
        aniž by se sáhlo na bicí/basu/audio.

        Přiřazení akordů k řádkům je GLOBÁLNÍ napříč stopami a osamocené
        (žádnému řádku nepřiřaditelné) akordy se shlukují po mezerách.
        Díky tomu se do textu dostanou i čistě akordové takty mezi řádky
        (typicky přesně ten případ, který vedl k vytvoření téhle funkce —
        chybějící instrumentální takt)."""
        grouped: list[tuple[int, list[dict]]] = []
        for ti in self._track_order():
            for line in self._group_lines(ti):
                if line:
                    grouped.append((ti, line))
        grouped.sort(key=lambda g: g[1][0]["time_s"])
        line_ranges = [(min(e["time_s"] for e in line),
                        max(e["time_s"] + e.get("duration_s", 0.5) for e in line))
                       for _ti, line in grouped]

        def line_idx_at(t: float):
            return next((li for li, (s, e) in enumerate(line_ranges) if s <= t < e), None)

        chords_all = sorted(self.data.get("chords_timeline", []) or [], key=lambda e: e["time_s"])
        chords_by_line: dict[int, list[dict]] = {}
        unassigned: list[dict] = []
        for c in chords_all:
            li = line_idx_at(float(c.get("time_s", 0.0)))
            (chords_by_line.setdefault(li, []) if li is not None else unassigned).append(c)

        units: list[tuple[float, str, object]] = []
        for line_idx, (ti, line) in enumerate(grouped):
            units.append((line_ranges[line_idx][0], "line", (line, chords_by_line.get(line_idx, []))))
        for ti in self._track_order():
            track_unassigned = sorted(
                (c for c in unassigned if c.get("track_index", 1) == ti), key=lambda e: e["time_s"])
            for t0, t1, cluster in self._cluster_events_by_gap(track_unassigned, gap=1.5):
                units.append((t0, "chords_only", cluster))
        units.sort(key=lambda u: u[0])

        out: list[str] = []
        for _t, kind, payload in units:
            if kind == "chords_only":
                seen: list[str] = []
                for c in payload:
                    nm = c.get("chord", "")
                    if nm and nm not in seen:
                        seen.append(nm)
                if seen:
                    out.append(", ".join(seen))
                    out.append("")
                continue
            line, chords = payload
            words_with_times = [(e.get("text", ""), float(e.get("time_s", 0.0)))
                                 for e in line if e.get("text", "").strip()]
            if words_with_times and chords:
                chord_line, lyric_line = self._build_chord_line_for_words(words_with_times, chords)
                if chord_line.strip():
                    out.append(chord_line)
                out.append(lyric_line)
            elif words_with_times:
                out.append(" ".join(w for w, _t in words_with_times))
            elif chords:
                seen = []
                for c in sorted(chords, key=lambda e: e["time_s"]):
                    nm = c.get("chord", "")
                    if nm and nm not in seen:
                        seen.append(nm)
                if seen:
                    out.append(", ".join(seen))
            out.append("")
        return "\n".join(out).rstrip("\n") + "\n"

    def replace_text_and_chords(self, new_data: dict) -> list[int]:
        """Nahradí JEN text a akordy (lyrics_timeline/chords_timeline/
        karaoke_lines) opraveným obsahem z "✎ Upravit text a akordy…" —
        bicí, basa i napojení na audio zůstávají BEZE ZMĚNY (na výslovné
        přání uživatele: "bicí, basa zůstane").

        Vrací seznam track_index, které v PŮVODNÍCH datech nesly text/akordy
        (pro volitelné varování volajícího, když jich bylo víc než jedna —
        nová data jsou vždy jen na stopě 1, viz `build_karaoke_json`)."""
        old_ti = {e.get("track_index", 1) for e in
                  (self.data.get("lyrics_timeline", []) or []) +
                  (self.data.get("chords_timeline", []) or [])}
        self._push_undo()
        self.data["lyrics_timeline"] = new_data.get("lyrics_timeline", [])
        self.data["chords_timeline"] = new_data.get("chords_timeline", [])
        self.data["karaoke_lines"] = new_data.get("karaoke_lines", [])
        new_meta = new_data.get("meta", {})
        meta = self.data.setdefault("meta", {})
        if new_meta.get("title"):
            meta["title"] = new_meta["title"]
        if new_meta.get("artist"):
            meta["artist"] = new_meta["artist"]
        self._relayout()
        return sorted(old_ti)

    def _track_order(self) -> list[int]:
        idxs = [t.get("index", i + 1) for i, t in enumerate(self.tracks)]
        # doplň i indexy vyskytující se jen v eventech — VČETNĚ bicích/basy
        # (dřív scházely: track_index vložený z jiného souboru přes
        # Kopírovat/Vložit napříč kartami, který v TÉHLE stopě neexistoval,
        # tak neměl žádný řádek → úder se přidal do dat, ale nikdy se
        # nevykreslil, viz oprava „vložit bicí z jiného souboru")
        for ev in (self.data.get("lyrics_timeline", []) + self.data.get("chords_timeline", [])
                   + self.data.get("drums_timeline", []) + self.data.get("bass_timeline", [])
                   + self.data.get("tab_timeline", [])):
            ti = ev.get("track_index", 1)
            if ti not in idxs:
                idxs.append(ti)
        return sorted(set(idxs))

    def _relayout(self, full: bool = False) -> None:
        self.scene.clear()
        self.blocks.clear()
        self.drum_hit_items.clear()
        self.bass_note_items.clear()
        self.waveform_item = None
        self._track_lane.clear()
        self._header_items.clear()   # scene.clear() je smazala, seznam musí taky

        order = self._track_order()
        names = self._track_names()

        # celková délka pro šířku scény
        max_t = 1.0
        for ev in self.data.get("lyrics_timeline", []) + self.data.get("chords_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 1)))
        for ev in self.data.get("drums_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)))
        for ev in self.data.get("bass_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 0)))
        for ev in self.data.get("tab_timeline", []):
            max_t = max(max_t, float(ev.get("time_s", 0)) + float(ev.get("duration_s", 0)))
        if self.waveform is not None:
            max_t = max(max_t, self._audio_to_timeline_t(self.waveform.duration_s))

        # výška stopy: normální stopy PER_TRACK; bicí rostou podle počtu
        # RŮZNÝCH bubnů — fixní kompaktní výška řádku (ikona 32×32 + okraj),
        # žádné umělé napasování na PER_TRACK (to dělalo i 1-2 bubny zbytečně
        # vysoké). Skryté stopy (viz TrackMixPanel „skrýt“) nezaberou vůbec
        # žádné místo — 0.
        lane_h: dict[int, float] = {}
        for ti in order:
            if ti in self._hidden_tracks:
                lane_h[ti] = 0.0
            elif self._track_is_drums(ti):
                lane_h[ti] = 8 + self._drum_row_count(ti) * DRUM_ROW_H + TRACK_GAP
            elif self._track_is_tab(ti):
                lane_h[ti] = TAB_BLOCK_H + TRACK_GAP
            else:
                lane_h[ti] = PER_TRACK

        total_h = RULER_H + self.waveform_h + sum(lane_h.values()) + 20
        total_w = HEADER_W + max_t * self.pps + 200
        self.scene.setSceneRect(0, 0, total_w, total_h)

        # pozadí pruhů + names + pravítko
        self._draw_ruler(max_t, total_h)

        # vlnovka nahrávky — NAD všemi zdrojovými stopami (tedy i nad bicími),
        # aby šlo očima porovnat nástup bicích/zpěvu proti mřížce tempa
        self._draw_waveform_lane(RULER_H, total_w)

        # zdrojové stopy (posunuté pod vlnovku, proměnná výška řádku)
        top = RULER_H + self.waveform_h
        for ti in order:
            h_slot = lane_h[ti]
            if ti in self._hidden_tracks:
                continue
            if self._track_is_drums(ti):
                self._draw_drums_lane(ti, top, names.get(ti, _track_fallback_name(ti)), total_w, h_slot)
            elif self._track_is_bass(ti):
                self._draw_bass_lane(ti, top, names.get(ti, _track_fallback_name(ti)), total_w, h_slot)
            elif self._track_is_tab(ti):
                self._track_lane[ti] = {"tab_y": top + 2}
                self._draw_tab_track_bg(ti, top, names.get(ti, _track_fallback_name(ti)), total_w)
            else:
                line_y = top + 2
                chord_y = line_y + LINE_H
                lyric_y = chord_y + CHORD_H
                self._track_lane[ti] = {"line_y": line_y, "chord_y": chord_y, "lyric_y": lyric_y}
                self._draw_lane_bg(ti, top, names.get(ti, _track_fallback_name(ti)), total_w)
            top += h_slot

        # bloky (akordy, slova)
        for ev in self.data.get("chords_timeline", []):
            self._add_block_item(ev, "chord")
        for ev in self.data.get("lyrics_timeline", []):
            self._add_block_item(ev, "lyric")
        for ev in self.data.get("tab_timeline", []):
            self._add_block_item(ev, "tab")

        # kurzor (playhead) navrchu všeho
        self._playhead = PlayheadItem(self, total_h)
        self.scene.addItem(self._playhead)
        self._update_playhead_label()

        self._pin_headers()   # hlavičky hned na správné místo dle scrollu

        if hasattr(self, "mix_panel"):
            self.mix_panel.refresh()

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
        for it in self.bass_note_items:
            if it.event is event_or_clip:
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

    # ------------------------------------------------------------------
    # Skupinové tažení myší (BlockItem/DrumHitItem/BassNoteItem) — udrží
    # vzájemné rozestupy víc vybraných položek při společném tažení, viz
    # `_apply_group_snap`. `_drag_anchor` je položka, na kterou uživatel
    # skutečně klikl (tu jedinou Qt informuje mousePress/Move/Release —
    # ostatní vybrané dostávají jen itemChange přes Qt vnitřní moveBy).
    # ------------------------------------------------------------------

    def _drag_group_begin(self, anchor) -> None:
        self._drag_anchor = anchor
        sel = [it for it in self.scene.selectedItems()
               if isinstance(it, (BlockItem, DrumHitItem, BassNoteItem))]
        self._drag_group_items = [it for it in sel if it is not anchor]
        self._drag_group_start = {id(it): float(it.event.get("time_s", 0.0)) for it in sel}
        self._drag_group_delta = 0.0

    def _drag_group_finalize(self) -> None:
        """Po skončení tažení dorovná VŠECHNY 'cestující' položky přesně
        na (jejich vlastní výchozí čas + finální posun kotvy) — pořadí,
        ve kterém Qt volá itemChange jednotlivých položek v POSLEDNÍM
        kroku tažení, není zaručené, takže bez týhle dorovnávky by mohla
        zůstat nepatrná odchylka o zlomek kroku."""
        if self._drag_anchor is None:
            return
        for it in self._drag_group_items:
            start = self._drag_group_start.get(id(it))
            if start is None:
                continue
            it.event["time_s"] = round(max(0.0, start + self._drag_group_delta), 3)
            it._sync_from_event()

    def _drag_group_end(self) -> None:
        self._drag_anchor = None
        self._drag_group_items = []
        self._drag_group_start = {}
        self._drag_group_delta = 0.0

    def _push_undo(self) -> None:
        self._undo_stack.append(copy.deepcopy(self.data))
        if len(self._undo_stack) > UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        was_dirty = self._dirty
        self._dirty = True   # jakákoliv editace = neuložené změny (viz karta title/zavírací dialog)
        if not was_dirty:
            self.dirty_changed.emit(True)

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
        # posun/roztažení nahrávky + výška vlnovky jsou taky v meta (viz
        # load_data) — undo/redo musí i tyhle vrátit, jinak by se offset po
        # Ctrl+Z rozjel od dat (přesně tenhle případ chytá test_waveform_track.py)
        self.audio_offset_s = float(meta.get("audio_offset_s", 0.0) or 0.0)
        self.audio_stretch = float(meta.get("audio_stretch", 1.0) or 1.0)
        self.waveform_h = float(meta.get("waveform_track_height", self.waveform_h)
                                or self.waveform_h)
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
    # Přimrazený levý sloupec (názvy stop)
    # ------------------------------------------------------------------
    # Hlavičky stop jsou běžné položky scény na x < HEADER_W, takže při
    # vodorovném rolování časové osy odjedou pryč a člověk pak nepozná,
    # která stopa je která. Řešení: každou takovou položku si zapamatujeme
    # i s její PŮVODNÍ x-souřadnicí a při rolování jim všem posuneme x o
    # aktuální offset scrollbaru — vizuálně tak zůstanou přilepené k
    # levému okraji výřezu. Dostanou vysoké zValue, aby obsah stop, který
    # pod nimi projíždí, byl schovaný ZA nimi, ne přes ně.

    HEADER_Z = 40.0   # nad obsahem stop (bloky ~10-15), pod playheadem (60)

    def _register_header_item(self, item, base_x: float = 0.0):
        """Zaregistruje položku levého sloupce jako „přimrazenou“."""
        item.setZValue(self.HEADER_Z)
        self._header_items.append((item, base_x))
        return item

    def _pin_headers(self) -> None:
        """Posune přimrazené položky tak, aby seděly na levém okraji výřezu.

        `valueChanged` na scrollbaru může (vzácně, typicky při zavírání
        okna/ukončování appky, kdy Qt ještě doručí zaframtou signálovou
        frontu) přijít i POTÉ, co `_relayout()` scénu i tenhle seznam
        smazal — položky pak jsou C++ straně už zrušené a `setX()` by
        spadlo na `RuntimeError`. Bezpečně to přeskočit, nejde o chybu dat."""
        if not self._header_items:
            return
        offset = float(self.view.horizontalScrollBar().value())
        for item, base_x in self._header_items:
            try:
                item.setX(base_x + offset)
            except RuntimeError:
                pass

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
                label += "  " + tr("audio.device_default_suffix")
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
        """Přepojí OBA přehrávače (nahrávka + GP mix) i testovací tón na
        dané zařízení. QAudioOutput vytváříme ZNOVU — `setDevice()` na
        objektu, jehož endpoint mezitím zmizel (odpojená BT sluchátka),
        se u Windows backendu nemusí zotavit a zůstane tichý."""
        pos = self.player.position()
        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        old = self.audio_output
        self.audio_output = QAudioOutput(self)
        self.audio_output.setDevice(dev)
        self.audio_output.setVolume(self._rec_volume)
        self.audio_output.setMuted(self._rec_muted)
        self.player.setAudioOutput(self.audio_output)
        if old is not None:
            old.deleteLater()
        if was_playing:
            self.player.setPosition(pos)
            self.player.play()

        gp_pos = self.gp_player.position()
        gp_was_playing = self.gp_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        old_gp = self.gp_audio_output
        self.gp_audio_output = QAudioOutput(self)
        self.gp_audio_output.setDevice(dev)
        self.gp_audio_output.setVolume(self._gp_volume)
        self.gp_audio_output.setMuted(self._gp_muted)
        self.gp_player.setAudioOutput(self.gp_audio_output)
        if old_gp is not None:
            old_gp.deleteLater()
        if gp_was_playing:
            self.gp_player.setPosition(gp_pos)
            self.gp_player.play()

        bass_pos = self.bass_player.position()
        bass_was_playing = self.bass_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        old_bass = self.bass_audio_output
        self.bass_audio_output = QAudioOutput(self)
        self.bass_audio_output.setDevice(dev)
        self.bass_audio_output.setVolume(self._bass_volume)
        self.bass_audio_output.setMuted(self._bass_muted)
        self.bass_player.setAudioOutput(self.bass_audio_output)
        if old_bass is not None:
            old_bass.deleteLater()
        if bass_was_playing:
            self.bass_player.setPosition(bass_pos)
            self.bass_player.play()

        # QSoundEffect přepojení zvládá bez znovuvytvoření, ale zdroj je
        # potřeba nastavit znovu, aby se načetl do nového zařízení
        self._test_sound.setAudioDevice(dev)
        self._test_sound.setSource(QUrl.fromLocalFile(_beep_wav()))
        for key, snd in self._drum_click_sounds.items():
            snd.setAudioDevice(dev)
            snd.setSource(QUrl.fromLocalFile(_drum_click_wav(key)))
        for real_path, snd in self._drum_real_sounds.items():
            snd.setAudioDevice(dev)
            snd.setSource(QUrl.fromLocalFile(real_path))
        for midi, snd in self._bass_note_sounds.items():
            snd.setAudioDevice(dev)
            snd.setSource(QUrl.fromLocalFile(_bass_note_wav(midi)))

    def _play_drum_sound(self, drum_name: str) -> None:
        """Zvuková zpětná vazba při editaci úderu (přidání/přesun/změna
        typu) — reálný vzorek (assets/drums/rock/*.wav), pokud existuje,
        jinak syntetické 'cvaknutí' (`_DRUM_CLICK_PARAMS`). Nezávislé na
        QMediaPlayer (jako testovací tón), takže nepřeruší případné
        přehrávání písně."""
        real_path = _real_drum_sample_path(drum_name)
        if real_path:
            snd = self._drum_real_sounds.get(real_path)
            if snd is None:
                snd = QSoundEffect(self)
                snd.setAudioDevice(self.audio_output.device())
                snd.setSource(QUrl.fromLocalFile(real_path))
                snd.setVolume(self._drum_click_volume)
                self._drum_real_sounds[real_path] = snd
            snd.stop()
            snd.play()
            return
        key = self._drum_icon_key(drum_name)
        snd = self._drum_click_sounds.get(key)
        if snd is None:
            snd = QSoundEffect(self)
            snd.setAudioDevice(self.audio_output.device())
            snd.setSource(QUrl.fromLocalFile(_drum_click_wav(key)))
            snd.setVolume(self._drum_click_volume)
            self._drum_click_sounds[key] = snd
        snd.stop()
        snd.play()

    def _play_bass_note(self, midi: int) -> None:
        """Zvuková zpětná vazba při editaci basové noty (přidání/přetažení/
        změna výšky) — syntetizovaný 'drnk' na dané MIDI notě (zatím jediná
        možnost, basa nemá reálné vzorky, viz `_bass_tone_samples`).
        Nezávislé na QMediaPlayer, takže nepřeruší přehrávání písně."""
        snd = self._bass_note_sounds.get(midi)
        if snd is None:
            snd = QSoundEffect(self)
            snd.setAudioDevice(self.audio_output.device())
            snd.setSource(QUrl.fromLocalFile(_bass_note_wav(midi)))
            snd.setVolume(self._bass_click_volume)
            self._bass_note_sounds[midi] = snd
        snd.stop()
        snd.play()

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
            self, tr("dialog.load_audio_title"), "", tr("dialog.load_audio_filter"))
        if path:
            self.load_audio(path)

    def load_audio(self, path: str) -> None:
        """Nastaví zdroj přehrávače a uloží cestu do `meta.audio_file`
        (perzistence mezi otevřeními — viz `load_data`). Volba souboru
        NENÍ editace karaoke dat → nejde do undo historie.

        Vedle přehrávače spustí i ASYNCHRONNÍ načtení tvaru vlny
        (`WaveformLoader`) pro vlnovku nad zdrojovými stopami — viz
        `_on_waveform_ready`/`WaveformItem`."""
        self.audio_path = path
        self.player.setSource(QUrl.fromLocalFile(path))
        self.audio_label.setText(os.path.basename(path))
        self.audio_label.setStyleSheet("color:#2d7d2d;")
        self.data.setdefault("meta", {})["audio_file"] = path

        self.waveform = None
        self._waveform_loading = True
        if self.waveform_item is not None:
            self.waveform_item.update()
        self._waveform_loader.load(path)

    def _load_gp_mix(self) -> None:
        """Vygeneruje syntetizovaný mixdown bicích z aktuálních GP dat
        (`drums_timeline`) a nahraje ho do `gp_player` — druhý přehrávač
        vedle skutečné nahrávky, aby šlo poslechem porovnat "co říká GP
        soubor" vs. realitu (viz `render_drums_mixdown`). Na vyžádání
        (tlačítko „🎼 GP bicí mix"), NIKDY automaticky — jde jen o pomůcku
        pro ČLOVĚKA, nemá nic sama od sebe ovlivňovat."""
        if not self.data.get("drums_timeline"):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("dialog.gp_drum_mix_title"),
                                   tr("dialog.gp_drum_mix_no_data"))
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            duration = self._song_end()
            path = render_drums_mixdown(self.data, duration)
        finally:
            QApplication.restoreOverrideCursor()
        self.gp_audio_path = path
        self.gp_player.setSource(QUrl.fromLocalFile(path))
        self.gp_audio_label.setText(tr("audio.gp_mix_generated", dur=f"{duration:.1f}", bpm=f"{self.bpm:g}"))
        self.gp_audio_label.setStyleSheet("color:#2d7d2d;")
        self._seek_audio(self.playhead_s)

    def _load_bass_mix(self) -> None:
        """Vygeneruje syntetizovaný mixdown BASY z aktuálních GP dat
        (`bass_timeline`) a nahraje ho do `bass_player` — samostatný
        přehrávač vedle nahrávky i GP bicí mixu (viz `render_bass_mixdown`).
        Na vyžádání (tlačítko „🎸 GP basa mix“), NIKDY automaticky."""
        if not self.data.get("bass_timeline"):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("dialog.gp_bass_mix_title"),
                                   tr("dialog.gp_bass_mix_no_data"))
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            duration = self._song_end()
            path = render_bass_mixdown(self.data, duration)
        finally:
            QApplication.restoreOverrideCursor()
        self.bass_audio_path = path
        self.bass_player.setSource(QUrl.fromLocalFile(path))
        self.bass_audio_label.setText(tr("audio.bass_mix_generated", dur=f"{duration:.1f}", bpm=f"{self.bpm:g}"))
        self.bass_audio_label.setStyleSheet("color:#2d7d2d;")
        self._seek_audio(self.playhead_s)

    def _on_rec_volume(self, val: int) -> None:
        self._rec_volume = val / 100.0
        self.audio_output.setVolume(self._rec_volume)

    def _on_gp_volume(self, val: int) -> None:
        self._gp_volume = val / 100.0
        self.gp_audio_output.setVolume(self._gp_volume)

    def _on_bass_volume(self, val: int) -> None:
        self._bass_volume = val / 100.0
        self.bass_audio_output.setVolume(self._bass_volume)

    def _on_rec_mute(self, checked: bool) -> None:
        self._rec_muted = checked
        self.audio_output.setMuted(checked)

    def _on_gp_mute(self, checked: bool) -> None:
        self._gp_muted = checked
        self.gp_audio_output.setMuted(checked)

    def _on_bass_mute(self, checked: bool) -> None:
        self._bass_muted = checked
        self.bass_audio_output.setMuted(checked)

    def _on_drum_click_volume(self, val: int) -> None:
        self._drum_click_volume = val / 100.0
        for snd in self._drum_click_sounds.values():
            snd.setVolume(self._drum_click_volume)

    def _on_bass_click_volume(self, val: int) -> None:
        self._bass_click_volume = val / 100.0
        for snd in self._bass_note_sounds.values():
            snd.setVolume(self._bass_click_volume)

    def _on_track_hide_toggle(self, ti: int, checked: bool) -> None:
        if checked:
            self._hidden_tracks.add(ti)
        else:
            self._hidden_tracks.discard(ti)
        self._relayout()

    def _on_gp_audio_error(self, error, error_string: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.gp_audio_label.setText(tr("audio.gp_mix_error", error=error_string))
        self.gp_audio_label.setStyleSheet("color:#c01c28;")

    def _on_bass_audio_error(self, error, error_string: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.bass_audio_label.setText(tr("audio.bass_mix_error", error=error_string))
        self.bass_audio_label.setStyleSheet("color:#c01c28;")

    def _on_waveform_ready(self, data: WaveformData) -> None:
        self.waveform = data
        self._waveform_loading = False
        if self.waveform_item is not None:
            self.waveform_item.update()
        # ZÁMĚRNĚ se tu NEVOLÁ _check_duration_vs_audio() automaticky —
        # nahrávka je pro ČLOVĚKA, aby si mohl OVĚŘIT/POSLECHNOUT, jestli
        # editor sedí na skutečnost, ne aby cokoliv sama od sebe měnila
        # nebo vyskakovala s návrhy. Kontrola je jen na vyžádání (tlačítko
        # „🔎 Ověřit délku"), a i pak nic sama neaplikuje.

    def _check_duration_vs_audio(self) -> None:
        """Ukáže (jen na vyžádání, tlačítko „🔎 Ověřit délku") srovnání
        vypočtené délky písně (`_song_end`, dle aktuálního tempa/taktů) proti
        SKUTEČNÉ délce načtené nahrávky. Čistě INFORMATIVNÍ — nic sám od
        sebe nenavrhuje ani nemění; slouží k tomu, aby si člověk mohl
        rozhodnutí (přetypovat tempo, ověřit repetice v GP…) udělat sám."""
        from PySide6.QtWidgets import QMessageBox
        if self.waveform is None or len(self.waveform) == 0:
            QMessageBox.information(self, tr("dialog.check_duration_title"),
                                   tr("dialog.check_duration_no_audio"))
            return
        computed_end = self._song_end()
        computed_audio_end = self._timeline_to_audio_t(computed_end)
        real_dur = self.waveform.duration_s
        if real_dur <= 0.01:
            return
        ratio = computed_audio_end / real_dur
        pct_off = (ratio - 1.0) * 100.0

        QMessageBox.information(self, tr("dialog.check_duration_result_title"),
            tr("dialog.check_duration_result_body", computed_end=f"{computed_end:.1f}",
               bpm=f"{self.bpm:g}", real_dur=f"{real_dur:.1f}", pct_off=f"{pct_off:+.1f}"))

    def _on_waveform_failed(self, msg: str) -> None:
        self.waveform = None
        self._waveform_loading = False
        if self.waveform_item is not None:
            self.waveform_item.update()

    def _on_audio_error(self, error, error_string: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.audio_label.setText(tr("audio.error", error=error_string))
        self.audio_label.setStyleSheet("color:#c01c28;")

    def _on_playback_state_changed(self, _state=None) -> None:
        """Napojeno na VŠECHNY přehrávače (nahrávka + GP bicí + GP basa)
        — přehrává se, pokud hraje ASPOŇ JEDEN z nich (ne podle argumentu
        signálu, ten by u jiného přehrávače přepsal stav prvního); viz
        `toggle_play_pause` pro stejný princip "ověř skutečný stav, ne co
        si widget pamatuje")."""
        playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                   or self.gp_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                   or self.bass_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
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

    # --- mapování ČAS NA ČASOVÉ OSE ↔ ČAS UVNITŘ MP3/WAV SOUBORU ---
    # Nahrávku lze na časové ose CELOU posunout (`audio_offset_s`) a mírně
    # natáhnout/stlačit (`audio_stretch`, ±10 % — viz `waveform_dialog`) kvůli
    # sladění s kulatým BPM. Střih/dělení nahrávky NEJDE — jen tohle dvojí.

    def _timeline_to_audio_t(self, t: float) -> float:
        return (t - self.audio_offset_s) / (self.audio_stretch or 1.0)

    def _audio_to_timeline_t(self, audio_t: float) -> float:
        return self.audio_offset_s + audio_t * (self.audio_stretch or 1.0)

    def set_audio_offset(self, offset_s: float, relayout: bool = True) -> None:
        # Záporný offset je legitimní: znamená, že t=0 nahrávky leží PŘED
        # začátkem časové osy — tedy že v čase t=0 osy se má přehrávat
        # nahrávka už o kus dál (typicky: nahrávka má na začátku něco
        # navíc — potlesk, ladění, delší předehru — co se do časové osy
        # nevejde). `_seek_audio`/`WaveformItem.paint` s tím počítají
        # (`_timeline_to_audio_t` je prostý součet, žádné místo v playbacku
        # zápornou hodnotu nepředpokládá jako chybu) — jen se v takovém
        # případě reálně přehraje/vykreslí až OD té pozdější chvíle
        # nahrávky, začátek souboru zůstane "uřezaný" před osou.
        self.audio_offset_s = round(offset_s, 3)
        self.data.setdefault("meta", {})["audio_offset_s"] = self.audio_offset_s
        if relayout and self.waveform_item is not None:
            self.waveform_item.update()

    def set_audio_stretch(self, stretch: float, relayout: bool = True) -> None:
        self.audio_stretch = max(AUDIO_STRETCH_MIN, min(AUDIO_STRETCH_MAX, stretch))
        self.data.setdefault("meta", {})["audio_stretch"] = round(self.audio_stretch, 4)
        if relayout and self.waveform_item is not None:
            self.waveform_item.update()

    def waveform_dialog(self) -> None:
        """Přesný (parametrický) posun + roztažení nahrávky — totéž, co jde
        tažením myší po `WaveformItem`, ale na čísla, ne od oka. Cíl: dolaď
        offset/roztažení tak, ať BPM z GP/webu vyjde na kulaté číslo proti
        reálné nahrávce.

        ŽIVÉ náhled: hodnoty se aplikují a vlnovka se překreslí HNED při
        každé změně spinboxu (`valueChanged`, ne až po OK) — dřív bylo
        potřeba měnit % naslepo a teprve po zavření dialogu bylo vidět, jak
        se vlna doopravdy roztáhla, což bylo nepohodlné/pomalé dolaďování.
        Zrušení (Cancel) vrátí vše přesně na stav před otevřením dialogu
        (přes `undo()` — první živá změna push-ne jeden undo krok, další
        živé změny už ne, takže Cancel = jeden Ctrl+Z, ne hromada kroků)."""
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialog.waveform_title"))
        form = QFormLayout(dlg)

        info = QLabel(tr("dialog.waveform_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color:#aaaaaa;")
        form.addRow(info)

        offset_sb = QDoubleSpinBox()
        offset_sb.setRange(-99999.0, 99999.0)
        offset_sb.setDecimals(3)
        offset_sb.setSuffix(" s")
        offset_sb.setValue(self.audio_offset_s)
        form.addRow(tr("dialog.waveform_offset_label"), offset_sb)

        stretch_sb = QDoubleSpinBox()
        stretch_sb.setRange(AUDIO_STRETCH_MIN * 100.0, AUDIO_STRETCH_MAX * 100.0)
        stretch_sb.setDecimals(2)
        stretch_sb.setSingleStep(0.1)
        stretch_sb.setSuffix(" %")
        stretch_sb.setValue(self.audio_stretch * 100.0)
        form.addRow(tr("dialog.waveform_stretch_label"), stretch_sb)

        dur = self.waveform.duration_s if self.waveform else None
        preview = QLabel()
        form.addRow(preview)

        def refresh_preview():
            if dur:
                new_dur = dur * (stretch_sb.value() / 100.0)
                preview.setText(tr("dialog.waveform_preview", dur=f"{dur:.1f}", new_dur=f"{new_dur:.1f}",
                                   speed=f"{100.0 / stretch_sb.value():.3f}"))
            else:
                preview.setText(tr("dialog.waveform_preview_pending"))

        undo_pushed = False

        def live_update():
            nonlocal undo_pushed
            if not undo_pushed:
                self._push_undo()
                undo_pushed = True
            self.set_audio_offset(offset_sb.value(), relayout=False)
            self.set_audio_stretch(stretch_sb.value() / 100.0, relayout=False)
            if self.waveform_item is not None:
                self.waveform_item.update()   # jen překreslit vlnu, ne celou scénu
            refresh_preview()

        offset_sb.valueChanged.connect(live_update)
        stretch_sb.valueChanged.connect(live_update)
        refresh_preview()

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec():
            self._relayout()   # potvrzeno — plné překreslení (konzistentní s ostatními dialogy)
        elif undo_pushed:
            self.undo()   # Cancel po živé změně -> vrátit přesně na stav před dialogem

    # --- zarovnání nahrávky dle 2 bodů: klikni blízko špičky na dvou
    # různých místech (ideálně co nejdál od sebe), systém najde skutečný
    # úder (`find_onset_near`) a spočítá TAKOVÉ offset+stretch, aby oba
    # body přesně seděly na mřížku — přesnější a rychlejší než odhadovat
    # ±10 % roztažení od oka (viz WaveformItem.contextMenuEvent) ---

    def set_align_point(self, which: str, t_click: float) -> None:
        """`t_click` = čas na ČASOVÉ OSE, kam uživatel kliknul (blízko
        špičky). Bod A/B se uloží jako (mřížkový čas, skutečný čas úderu
        v souboru) — mřížkový čas se přichytí na aktuální mřížku
        (`snap_time`), protože smysl bodu je „tahle špička patří NA TENHLE
        TAKT/BEAT", ne na náhodný pixel kliku."""
        grid_t = self.snap_time(t_click)
        audio_guess = self._timeline_to_audio_t(t_click)
        onset = self.waveform.find_onset_near(audio_guess, window_s=0.3) if self.waveform else None
        audio_t = onset if onset is not None else max(0.0, audio_guess)
        if which == "A":
            self._align_a = (grid_t, audio_t)
        else:
            self._align_b = (grid_t, audio_t)
        if self.waveform_item is not None:
            self.waveform_item.update()

    def clear_align_points(self) -> None:
        self._align_a = None
        self._align_b = None
        if self.waveform_item is not None:
            self.waveform_item.update()

    def apply_alignment(self) -> None:
        """Spočítá offset+stretch ze dvou nastavených bodů a použije je.
        Dva body jednoznačně určují lineární zobrazení čas-v-souboru →
        čas-na-ose (`_audio_to_timeline_t`): `stretch` = poměr rozestupů,
        `offset` dopočítá zbytek."""
        if self._align_a is None or self._align_b is None:
            return
        g1, a1 = self._align_a
        g2, a2 = self._align_b
        if abs(a2 - a1) < 0.05:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, tr("dialog.align_audio_title"),
                               tr("dialog.align_points_too_close_body"))
            return
        stretch = (g2 - g1) / (a2 - a1)
        stretch_clamped = max(AUDIO_STRETCH_MIN, min(AUDIO_STRETCH_MAX, stretch))
        offset = g1 - stretch_clamped * a1
        if offset < 0:
            # NESMÍ se prostě oříznout na 0 — to by o zlomek beatu rozbilo
            # přesné zarovnání, které jsme právě spočítali (viz stejná
            # oprava ve waveform.estimate_alignment). Posun o CELÝ beat
            # zarovnání zachová, jen ho posune o pár beatů dál.
            offset += math.ceil(-offset / self.beat_s) * self.beat_s
        self._push_undo()
        self.set_audio_offset(offset, relayout=False)
        self.set_audio_stretch(stretch_clamped, relayout=False)
        self._relayout()
        if abs(stretch - stretch_clamped) > 0.001:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("dialog.align_audio_title"),
                                   tr("dialog.align_stretch_clamped_body",
                                      stretch=f"{stretch * 100:.1f}",
                                      stretch_clamped=f"{stretch_clamped * 100:.1f}"))

    def auto_align_audio(self) -> None:
        """PLNĚ AUTOMATICKÉ zarovnání — žádné ruční klikání na body. Najde
        VŠECHNY výrazné údery v nahrávce (`WaveformData.detect_onsets`) a
        spočítá takové natažení+posun, aby jich na mřížku (danou už
        nastaveným tempem, `self.beat_s`) padlo co nejvíc
        (`waveform.estimate_alignment` — "comb filter" odhad)."""
        if self.waveform is None or len(self.waveform) == 0:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("dialog.auto_align_title"),
                                   tr("dialog.auto_align_no_audio_body"))
            return

        onsets = self.waveform.detect_onsets()
        result = estimate_alignment(onsets, self.beat_s, AUDIO_STRETCH_MIN, AUDIO_STRETCH_MAX)
        if result is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, tr("dialog.auto_align_title"),
                                   tr("dialog.auto_align_too_few_onsets_body", n=len(onsets)))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialog.auto_align_rhythm_title"))
        form = QFormLayout(dlg)
        pct = result["confidence"] * 100.0
        form.addRow(QLabel(
            tr("dialog.auto_align_summary", n=len(onsets), matched=result['matched'], pct=f"{pct:.0f}")))
        form.addRow(tr("dialog.auto_align_stretch_label"), QLabel(f"{result['stretch'] * 100:.2f} %"))
        form.addRow(tr("dialog.auto_align_offset_label"), QLabel(f"{result['offset']:.3f} s"))
        if pct < 50:
            warn = QLabel(tr("dialog.auto_align_low_confidence_warning"))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#c64600;")
            form.addRow(warn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(tr("dialog.auto_align_apply_btn"))
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec():
            self._push_undo()
            self.set_audio_offset(result["offset"], relayout=False)
            self.set_audio_stretch(result["stretch"], relayout=False)
            self._relayout()

    def _seek_audio(self, t: float) -> None:
        """`t` je čas na ČASOVÉ OSE — převede se na čas uvnitř souboru
        (`_timeline_to_audio_t`, respektuje posun i roztažení nahrávky).
        GP mix (`gp_player`) žádný posun/roztažení nemá — je vyrenderovaný
        přímo na mřížku časové osy, takže tam je čas 1:1."""
        if self.player.source().isValid():
            audio_t = max(0.0, self._timeline_to_audio_t(t))
            self.player.setPosition(int(round(audio_t * 1000)))
        if self.gp_player.source().isValid():
            self.gp_player.setPosition(max(0, int(round(t * 1000))))
        if self.bass_player.source().isValid():
            self.bass_player.setPosition(max(0, int(round(t * 1000))))

    def _ensure_device_alive(self) -> None:
        """Zkontroluje, že zařízení, na které hrajeme, v systému pořád je —
        po odpojení/připojení sluchátek by se jinak hrálo do prázdna."""
        cur = self.audio_output.device()
        if not any(d.id() == cur.id() for d in QMediaDevices.audioOutputs()):
            self._on_audio_devices_changed()

    def play_audio(self) -> None:
        """Spustí VŠECHNY přehrávače (nahrávka + GP bicí/basa mix), pokud
        mají zdroj — sdílejí transport, aby šlo poslouchat souběžně/A-B
        (viz hlasitostní slidery „Nahrávka“/„GP bicí“/„GP basa“)."""
        if (not self.player.source().isValid() and not self.gp_player.source().isValid()
                and not self.bass_player.source().isValid()):
            return
        self._ensure_device_alive()
        self._stop_shuttle_timer()
        self.player.setPlaybackRate(1.0 / (self.audio_stretch or 1.0))
        self.gp_player.setPlaybackRate(1.0)     # GP mix je bez roztažení, vždy 1×
        self.bass_player.setPlaybackRate(1.0)   # stejně tak basa
        self._seek_audio(self.playhead_s)
        if self.player.source().isValid():
            self.player.play()
        if self.gp_player.source().isValid():
            self.gp_player.play()
        if self.bass_player.source().isValid():
            self.bass_player.play()

    def pause_audio(self) -> None:
        self._stop_shuttle_timer()
        self.player.pause()
        self.gp_player.pause()
        self.bass_player.pause()

    def toggle_play_pause(self) -> None:
        """Spojené tlačítko ▶/⏸ na ovladači — rozhoduje se podle SKUTEČNÉHO
        stavu PŘEHRÁVAČŮ (ne podle toho, co si pamatuje widget), aby se stav
        tlačítka nemohl rozejít s realitou. Hraje-li ASPOŇ JEDEN z party
        (nahrávka/GP bicí/GP basa), tlačítko pauzíruje všechny; jinak
        všechny spustí."""
        playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                   or self.gp_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                   or self.bass_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        if playing:
            self.pause_audio()
        else:
            self.play_audio()

    def stop_audio(self) -> None:
        """Zastaví přehrávání (všechny přehrávače). Qt `stop()` interně
        vrací pozici na 0 — editor to hned poté vrátí zpět na `playhead_s`,
        aby "Stop" v editačním nástroji nepřekvapil skokem na začátek."""
        self._stop_shuttle_timer()
        self.player.stop()
        self.gp_player.stop()
        self.bass_player.stop()
        self._seek_audio(self.playhead_s)

    # --- shuttle (mezikruží ovladače) ---

    def _on_shuttle(self, value: float) -> None:
        """`value` −1..+1 z `JogShuttleWidget.shuttleChanged` (0 = klid).
        Doprava (+): reálný zvuk zrychlený 1×–4× (`setPlaybackRate`) — OBA
        přehrávače, GP mix bez roztažení (jeho báze je vždy 1×).
        Doleva (−): TICHÝ posun playheadu (Qt/FFmpeg neumí přehrát zvuk
        pozpátku) — až 8× rychlostí timeline za reálnou sekundu."""
        if abs(value) < 0.02:
            self._stop_shuttle_timer()
            self.player.pause()
            self.player.setPlaybackRate(1.0)
            self.gp_player.pause()
            self.gp_player.setPlaybackRate(1.0)
            self.bass_player.pause()
            self.bass_player.setPlaybackRate(1.0)
            return
        if value > 0:
            self._stop_shuttle_timer()
            mult = 1.0 + value * 3.0   # 1×–4× nad rámec roztažení
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState \
                    and self.player.source().isValid():
                self._seek_audio(self.playhead_s)
                self.player.play()
            if self.gp_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState \
                    and self.gp_player.source().isValid():
                self._seek_audio(self.playhead_s)
                self.gp_player.play()
            if self.bass_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState \
                    and self.bass_player.source().isValid():
                self._seek_audio(self.playhead_s)
                self.bass_player.play()
            base_rate = 1.0 / (self.audio_stretch or 1.0)
            self.player.setPlaybackRate(base_rate * mult)
            self.gp_player.setPlaybackRate(mult)
            self.bass_player.setPlaybackRate(mult)
        else:
            self.player.pause()
            self.gp_player.pause()
            self.bass_player.pause()
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
        která může způsobit zvukové zádrhele/cukání).

        Hodiny (co udává aktuální čas) jsou přednostně skutečná nahrávka
        (`self.player`, respektuje offset/stretch) — pokud není načtená,
        řídí se GP bicí mixem, pak GP basa mixem; všechny jsou 1:1 s
        časovou osou (bez převodu)."""
        if self.player.source().isValid():
            t = self._audio_to_timeline_t(self.player.position() / 1000.0)
        elif self.gp_player.source().isValid():
            t = self.gp_player.position() / 1000.0
        elif self.bass_player.source().isValid():
            t = self.bass_player.position() / 1000.0
        else:
            return
        self.set_playhead(t, seek_audio=False)
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

    def _draw_waveform_lane(self, top: float, total_w: float) -> None:
        """„Chlupatá čára" nahrávky nad zdrojovými stopami — viz `WaveformItem`.
        Vykreslí se VŽDY (i bez načteného audia — ukáže placeholder), ať je
        vidět, že prvek existuje a jde tam přetáhnout/vybrat soubor."""
        h = self.waveform_h
        # hlavička (přimrazená vlevo — viz _pin_headers)
        self._register_header_item(
            self.scene.addRect(0, top, HEADER_W, h,
                               QPen(QColor("#a3c2e6")), QBrush(QColor("#eef2f5"))))
        nm = self.scene.addSimpleText(tr("lane.recording"))
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        nm.setBrush(QBrush(QColor("#2f6690")))
        self._register_header_item(nm, 6)
        if self.audio_path:
            sub = self.scene.addSimpleText(os.path.basename(self.audio_path))
            sub.setFont(QFont("Segoe UI", 7))
            sub.setPos(6, top + h - 15)
            sub.setBrush(QBrush(QColor("#6a8aa8")))
            self._register_header_item(sub, 6)
            if abs(self.audio_stretch - 1.0) > 0.001:
                st = self.scene.addSimpleText(f"×{self.audio_stretch * 100:.1f}%")
                st.setFont(QFont("Segoe UI", 7, QFont.Bold))
                st.setPos(6, top + 18)
                st.setBrush(QBrush(QColor("#c64600")))
                self._register_header_item(st, 6)
            if self.waveform is not None and self.waveform.display_gain > 1.05:
                g = self.scene.addSimpleText(tr("waveform.display_gain_label", gain=f"{self.waveform.display_gain:.1f}"))
                g.setFont(QFont("Segoe UI", 7))
                g.setPos(6, top + 31)
                g.setToolTip(tr("waveform.display_gain.tooltip"))
                g.setBrush(QBrush(QColor("#888888")))
                self._register_header_item(g, 6)

        self.waveform_item = WaveformItem(self, top, h, total_w)
        self.scene.addItem(self.waveform_item)

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
            lbl = self.scene.addSimpleText(tr("ruler.count_in"))
            lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
            lbl.setPos(HEADER_W + 4, 5)
            lbl.setBrush(QBrush(QColor("#8a5600")))

        bar_px = bar_s * self.pps
        show_beats = (beat_s * self.pps) >= 6          # ať se beaty nepřehustí
        label_every = max(1, -(-40 // max(1, int(bar_px))))   # ceil(40/bar_px)

        # Takty se NESMÍ krokovat konstantou: metrum se v písni může měnit
        # (Jasná zpráva má takt 1 ve 2/4 a zbytek ve 4/4) a přičítání jedné
        # délky by od druhého taktu dál kreslilo čáry mimo noty. Délku i
        # počet dob dodá mřížka, která zná mapu metra.
        grid = self._music_grid()
        for bar_i, (bar_no, t, bar_len) in enumerate(grid.iter_bars(max_t + 1.0)):
            x = HEADER_W + t * self.pps
            self.scene.addLine(x, 0, x, total_h, pen_bar)
            if bar_i % label_every == 0:
                lbl = self.scene.addSimpleText(tr("ruler.bar_label", bar=bar_no, t=f"{t:g}"))
                lbl.setFont(QFont("Segoe UI", 7, QFont.Bold))
                lbl.setPos(x + 3, RULER_H - 13)
                lbl.setBrush(QBrush(QColor("#555")))
            if show_beats:
                n_beats = grid.beats_in_bar(bar_no)
                for bi in range(1, n_beats):
                    bx = x + (bar_len / n_beats) * bi * self.pps
                    self.scene.addLine(bx, RULER_H * 0.35, bx, total_h, pen_beat)

        # Roh pravítka nad sloupcem hlaviček — přimrazený, aby popisky taktů
        # neprojížděly skrz levý sloupec, když se osa odroluje doprava.
        self._register_header_item(
            self.scene.addRect(0, 0, HEADER_W, RULER_H,
                               QPen(Qt.NoPen), QBrush(QColor("#eeeeee"))))
        # oddělovač hlavičky — taky přimrazený (je to pravá hrana sloupce)
        self._register_header_item(
            self.scene.addLine(HEADER_W, 0, HEADER_W, total_h, pen_bar))

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
        # hlavička (přimrazená vlevo — viz _pin_headers)
        self._register_header_item(
            self.scene.addRect(0, top, HEADER_W, PER_TRACK - TRACK_GAP + 2,
                               QPen(QColor("#dddddd")), QBrush(QColor("#fafafa"))))
        nm = self.scene.addSimpleText(name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        self._register_header_item(nm, 6)
        for txt, y, col in [(tr("lane.rows"), line_y + 4, "#9141ac"),
                            (tr("lane.chords"), chord_y + 4, "#1a5fb4"),
                            (tr("lane.lyrics"), lyric_y + 6, "#2d7d2d")]:
            it = self.scene.addSimpleText(txt)
            it.setPos(12, y); it.setBrush(QBrush(QColor(col)))
            self._register_header_item(it, 12)
        self._add_lyrics_shift_controls(ti, top)

    def _draw_tab_track_bg(self, ti: int, top: float, name: str, total_w: float) -> None:
        """Vykreslí stopu se sólo tabulaturou — jeden vysoký pruh pro ASCII
        bloky (`BlockItem` s `kind="tab"`, viz `JSON_FORMAT.md` §3.6), NE
        řádky/akordy/text jako `_draw_lane_bg`. Obsah bloku je přímo
        vložený/editovaný ASCII text (6 řádků strun), ne odvozený
        z jednotlivých not na časové ose — přidává se přes kontextové menu
        prázdného místa v pruhu (`+ Sólo` / dvojklik na blok = editace)."""
        tab_y = top + 2
        w = total_w - HEADER_W
        self.scene.addRect(HEADER_W, tab_y, w, TAB_BLOCK_H,
                           QPen(Qt.NoPen), QBrush(QColor("#f6f0fc")))
        self._register_header_item(
            self.scene.addRect(0, top, HEADER_W, TAB_BLOCK_H - TRACK_GAP + 2,
                               QPen(QColor("#c9b3e6")), QBrush(QColor("#f1e8fb"))))
        nm = self.scene.addSimpleText("🎸 " + name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        nm.setBrush(QBrush(QColor("#7c3fc9")))
        self._register_header_item(nm, 6)

    def _add_lyrics_shift_controls(self, ti: int, top: float) -> None:
        """Tlačítka v hlavičce textové stopy pro posun VŠECH slov i akordů
        dané stopy v čase najednou — obdoba `_add_drum_shift_controls`,
        pro případ, kdy GP (samo se sebou konzistentní) sedí proti reálné
        nahrávce jako celek jinde, než mají bicí/basa."""
        x0 = HEADER_W - 72
        y0 = top + 3
        specs = [
            ("◀", tr("lyrics_shift.earlier.tooltip"),
             lambda: self.shift_lyrics_track(ti, -self._nudge_step())),
            ("▶", tr("lyrics_shift.later.tooltip"),
             lambda: self.shift_lyrics_track(ti, self._nudge_step())),
            ("⋯", tr("lyrics_shift.exact.tooltip"),
             lambda: self._shift_lyrics_track_dialog(ti)),
        ]
        for i, (label, tip, cb) in enumerate(specs):
            btn = DrumShiftButton(label, tip, cb)
            btn.setPos(x0 + i * 22, y0)
            self.scene.addItem(btn)
            self._register_header_item(btn, x0 + i * 22)

    def _track_is_drums(self, ti: int) -> bool:
        for t in self.tracks:
            if t.get("index") == ti:
                return bool(t.get("is_drums") or t.get("type") == "drums")
        # stopa vůbec není deklarovaná v self.tracks — typicky úder vložený
        # NAPŘÍČ SOUBORY (Kopírovat/Vložit z jiné karty, viz paste_at), jehož
        # track_index v tomhle souboru nikdy nepatřil žádné GP stopě. Pozná
        # se podle toho, že v drums_timeline s tímhle track_index něco je —
        # jinak by se vykreslila jako obyčejná (ne-bicí) stopa a úder by
        # nikdy nedostal DrumHitItem, tedy zůstal neviditelný.
        return any(ev.get("track_index") == ti for ev in self.data.get("drums_timeline", []))

    def _drum_names_for(self, ti: int) -> list[str]:
        """Distinct jména bubnů použitá touto stopou, seřazená pro zobrazení
        (činely/hi-hat nahoře, snare/tom uprostřed, kick dole; abecedně
        v rámci skupiny). Každé dostane VLASTNÍ řádek — žádné 2 různé bubny
        se nikdy nepřekrývají v jednom řádku.

        Prázdná stopa (žádný úder zatím neexistuje) by jinak neměla ani
        jeden řádek → ani jedno interaktivní pozadí (`DrumRowBackground`) →
        nešlo by na ni pravým klikem vůbec nic přidat. Proto se v tom
        případě vrátí `DEFAULT_DRUM_ROWS` (základní sada) místo prázdného
        seznamu — jakmile na stopu přibude první úder jiného bubnu, řádky
        se přepočítají znovu podle SKUTEČNÉHO obsahu (viz volání odsud)."""
        names = {ev.get("drum", "?") for ev in self.data.get("drums_timeline", [])
                 if ev.get("track_index") == ti}
        if not names:
            return list(DEFAULT_DRUM_ROWS)
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
        # hlavička (přimrazená vlevo — viz _pin_headers)
        self._register_header_item(
            self.scene.addRect(0, top, HEADER_W, h + 2,
                               QPen(QColor("#e6c9a3")), QBrush(QColor("#fbf0e0"))))
        nm = self.scene.addSimpleText("🥁 " + name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        nm.setBrush(QBrush(QColor("#b56b1e")))
        self._register_header_item(nm, 6)
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
            self._register_header_item(it, 12)
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
            ("◀", tr("drum_shift.earlier.tooltip"),
             lambda: self.shift_drum_track(ti, -self._nudge_step())),
            ("▶", tr("drum_shift.later.tooltip"),
             lambda: self.shift_drum_track(ti, self._nudge_step())),
            ("⋯", tr("drum_shift.exact.tooltip"),
             lambda: self._shift_drum_track_dialog(ti)),
        ]
        for i, (label, tip, cb) in enumerate(specs):
            btn = DrumShiftButton(label, tip, cb)
            btn.setPos(x0 + i * 22, y0)
            self.scene.addItem(btn)
            self._register_header_item(btn, x0 + i * 22)

    def _nudge_step(self) -> float:
        """Krok posunu stopy = aktuální mřížka (Přichytit), jinak 0,1 s."""
        return self.snap_s if self.snap_s > 0 else 0.1

    def shift_drum_track(self, ti: int, delta_s: float) -> None:
        """Posune ČASY VŠECH úhozů bicích dané stopy o delta_s (+ později,
        − dřív). Jen bicí (drums_timeline) — text/akordy zůstávají beze
        změny, protože právě jejich vzájemný posun je cílem.

        Časy nesmí jít do záporna (formát/mřížka zápornou pozici nezná —
        `bar`/`beat` začíná na 1). Dřív se to hlídalo PO JEDNOM úderu
        (`max(0.0, ...)`), takže při větším posunu doleva se všechny
        úhozy před hranicí SLILY na čas 0 — vizuálně to vypadalo, že
        zbytek stopy „zmizel" (zůstal jeden bod na nule, ne posunuté
        rozestupy). Teď se místo toho ořízne DELTA jako celek, takže
        se buď posune CELÁ stopa se zachovanými rozestupy, nebo (když by
        to poslalo nejstarší úder pod nulu) se posun zastaví přesně na
        hranici — nic se nikdy nepřekryje ani neztratí."""
        evs = [ev for ev in self.data.get("drums_timeline", []) if ev.get("track_index") == ti]
        if not evs or not delta_s:
            return
        if delta_s < 0:
            min_t = min(float(ev.get("time_s", 0.0)) for ev in evs)
            delta_s = max(delta_s, -min_t)
        if not delta_s:
            return
        self._push_undo()
        for ev in evs:
            ev["time_s"] = round(float(ev.get("time_s", 0.0)) + delta_s, 3)
        self._relayout()

    def _shift_drum_track_dialog(self, ti: int) -> None:
        delta, ok = QInputDialog.getDouble(
            self, tr("dialog.shift_drum_track_title"),
            tr("dialog.shift_drum_track_label"),
            0.0, -60.0, 60.0, 3)
        if ok and delta:
            self.shift_drum_track(ti, delta)

    def shift_lyrics_track(self, ti: int, delta_s: float) -> None:
        """Posune ČASY textu i akordů dané stopy společně o delta_s — obdoba
        `shift_drum_track`, jen pro `lyrics_timeline`/`chords_timeline`.
        Stejná ochrana proti záporným časům: posun se ořízne jako celek,
        ne po jednotlivých událostech (viz `shift_drum_track`)."""
        evs = [ev for key in ("lyrics_timeline", "chords_timeline")
               for ev in self.data.get(key, []) if ev.get("track_index") == ti]
        if not evs or not delta_s:
            return
        if delta_s < 0:
            min_t = min(float(ev.get("time_s", 0.0)) for ev in evs)
            delta_s = max(delta_s, -min_t)
        if not delta_s:
            return
        self._push_undo()
        for ev in evs:
            ev["time_s"] = round(float(ev.get("time_s", 0.0)) + delta_s, 3)
        self._relayout()

    def _shift_lyrics_track_dialog(self, ti: int) -> None:
        delta, ok = QInputDialog.getDouble(
            self, tr("dialog.shift_lyrics_track_title"),
            tr("dialog.shift_lyrics_track_label"),
            0.0, -60.0, 60.0, 3)
        if ok and delta:
            self.shift_lyrics_track(ti, delta)

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
            "source": "manual",   # naklikal uživatel, viz JSON_FORMAT.md §6
        }
        self.data.setdefault("drums_timeline", []).append(ev)
        self._relayout()
        self._play_drum_sound(drum_name)

    def remove_drum_hit(self, item: "DrumHitItem") -> None:
        try:
            self.data.get("drums_timeline", []).remove(item.event)
        except ValueError:
            pass
        self.scene.removeItem(item)
        if item in self.drum_hit_items:
            self.drum_hit_items.remove(item)

    def add_bass_note(self, ti: int, string: int, fret: int, time_s: float) -> dict:
        """Přidá novou basovou notu na dané struně v daném čase — volá se z
        kontextového menu prázdného místa v řádku (`BassLaneBackground`).
        Vrací vytvořený event (`midi` se hodí volajícímu pro `_play_bass_note`)."""
        self._push_undo()
        string = max(1, min(4, int(string)))
        midi = BASS_STRING_OPEN_MIDI.get(string, 40) + max(0, int(fret))
        ev = {
            "time_s": round(max(0.0, time_s), 3),
            "duration_s": 0.3,
            "string": string,
            "fret": max(0, int(fret)),
            "midi": midi,
            "note_name": _bass_note_name(midi),
            "track_index": ti,
            "source": "manual",
        }
        self.data.setdefault("bass_timeline", []).append(ev)
        self._relayout()
        return ev

    def remove_bass_note(self, item: "BassNoteItem") -> None:
        try:
            self.data.get("bass_timeline", []).remove(item.event)
        except ValueError:
            pass
        self.scene.removeItem(item)
        if item in self.bass_note_items:
            self.bass_note_items.remove(item)

    @staticmethod
    def set_bass_note_pitch(event: dict, string: int, fret: int) -> None:
        string = max(1, min(4, int(string)))
        fret = max(0, int(fret))
        midi = BASS_STRING_OPEN_MIDI.get(string, 40) + fret
        event["string"] = string
        event["fret"] = fret
        event["midi"] = midi
        event["note_name"] = _bass_note_name(midi)

    def _track_is_bass(self, ti: int) -> bool:
        for t in self.tracks:
            if t.get("index") == ti:
                return t.get("type") == "bass"
        # viz komentář u _track_is_drums — stopa nedeklarovaná v self.tracks
        # (typicky vloženo napříč soubory), pozná se podle bass_timeline
        return any(ev.get("track_index") == ti for ev in self.data.get("bass_timeline", []))

    def _track_is_tab(self, ti: int) -> bool:
        for t in self.tracks:
            if t.get("index") == ti:
                return t.get("type") == "solo_guitar"
        # viz komentář u _track_is_drums — stopa nedeklarovaná v self.tracks
        # (typicky vloženo napříč soubory), pozná se podle tab_timeline
        return any(ev.get("track_index") == ti for ev in self.data.get("tab_timeline", []))

    def _draw_bass_lane(self, ti: int, top: float, name: str, total_w: float,
                        h_slot: float | None = None) -> None:
        """Vykreslí basovou stopu: noty jako úsečky (délka = duration_s) ve
        4 řadách podle struny (1 = nejtenčí nahoře … 4 = nejtlustší dole).
        Editovatelná stejně jako bicí (přidat/přesunout/smazat/změnit
        strunu+pražec přes `BassNoteItem`/`BassLaneBackground`); editace
        hraje syntetizovaný 'drnk' (`_play_bass_note`), celá linka jde
        namíchat a přehrát tlačítkem „🎸 GP basa mix“ (`_load_bass_mix`)."""
        h = (h_slot if h_slot is not None else PER_TRACK) - TRACK_GAP
        y = top + 2
        w = total_w - HEADER_W
        # hlavička (přimrazená vlevo — viz _pin_headers)
        self._register_header_item(
            self.scene.addRect(0, top, HEADER_W, h + 2,
                               QPen(QColor("#a3c2e6")), QBrush(QColor("#e0ecfb"))))
        nm = self.scene.addSimpleText("🎸 " + name)
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setPos(6, top + 3)
        nm.setBrush(QBrush(QColor("#1a5fb4")))
        self._register_header_item(nm, 6)

        n_strings = 4
        rows_h = (h - 8) / n_strings
        row_y_of: dict[int, float] = {}
        for s in range(1, n_strings + 1):
            ry = y + 4 + (s - 1) * rows_h
            row_y_of[s] = ry + rows_h / 2
            # interaktivní pozadí řádku — pravý klik = přidat notu téhle struny
            bg = BassLaneBackground(self, ti, s, HEADER_W, ry, w, rows_h)
            self.scene.addItem(bg)
            it = self.scene.addSimpleText(tr("lane.string", n=s))
            it.setFont(QFont("Segoe UI", 7))
            it.setPos(12, ry - 2)
            it.setBrush(QBrush(QColor("#4a7fc9")))
            self._register_header_item(it, 12)
            self.scene.addLine(HEADER_W, ry + rows_h / 2, HEADER_W + w, ry + rows_h / 2,
                               QPen(QColor("#cfe0f5"), 1))

        for ev in self.data.get("bass_timeline", []):
            if ev.get("track_index") != ti:
                continue
            s = max(1, min(n_strings, int(ev.get("string", 4))))
            item = BassNoteItem(self, ev, row_y_of[s], self.pps)
            self.scene.addItem(item)
            self.bass_note_items.append(item)

    def _add_block_item(self, ev: dict, kind: str) -> None:
        ti = ev.get("track_index", 1)
        lane = self._track_lane.get(ti)
        if lane is None:
            return
        if kind == "chord":
            y, h = lane["chord_y"], CHORD_H - 4
        elif kind == "tab":
            y, h = lane["tab_y"], TAB_BLOCK_H - 6
        else:
            y, h = lane["lyric_y"], LYRIC_H - 4
        block = BlockItem(self, ev, kind, y + 2, h)
        self.scene.addItem(block)
        self.blocks.append(block)

    # ------------------------------------------------------------------
    # Akce
    # ------------------------------------------------------------------

    def zoom(self, factor: float) -> None:
        self.pps = max(8.0, min(400.0, self.pps * factor))
        self._relayout()

    def _do_export(self, path: str | None = None, silent: bool = False) -> None:
        """`path=None` → hlavní okno nabídne dialog ("Uložit jako…"); jinak
        přepíše rovnou tuhle cestu, beze ptaní ("Uložit"). `silent=True` =
        bez potvrzovacího dialogu po uložení, jen stavový řádek (běžné
        "Uložit" nemá zahlcovat popup oknem při každém Ctrl+S)."""
        self._push_undo()   # to_json() přestavuje karaoke_lines/klipy — bezpečnostní krok
        if callable(self.export_callback):
            self.export_callback(self.to_json(), path, silent)

    def edit_block(self, block: BlockItem) -> None:
        if block.kind == "chord":
            text, ok = QInputDialog.getText(self, tr("dialog.edit_chord_title"), tr("dialog.edit_chord_label"),
                                            text=block.event.get("chord", ""))
            if ok:
                self._push_undo()
                block.event["chord"] = text.strip()
        elif block.kind == "tab":
            # ASCII tabulatura je víceřádková — jednořádkový QInputDialog.getText
            # by ji rozbil, proto getMultiLineText (viz JSON_FORMAT.md §3.6:
            # obsah je přímo vložený text, ne odvozený z jednotlivých not)
            text, ok = QInputDialog.getMultiLineText(
                self, tr("dialog.edit_tab_title"), tr("dialog.edit_tab_label"),
                block.event.get("text", ""))
            if ok:
                self._push_undo()
                block.event["text"] = text.rstrip("\n")
                self._regen_tab_variants(block.event)
        else:
            text, ok = QInputDialog.getText(self, tr("dialog.edit_text_title"), tr("dialog.edit_text_label"),
                                            text=block.event.get("text", ""))
            if ok:
                self._push_undo()
                block.event["text"] = text.strip()
        block.update()

    def _regen_tab_variants(self, ev: dict) -> None:
        """Po každé úpravě ručně psaného ASCII bloku (`edit_block`/
        `add_block`) přegeneruje transpoziční varianty
        (`ascii_tab.generate_transpose_variants`) — staré varianty by po
        změně textu odpovídaly JINÉMU obsahu, takže se musí buď
        přepočítat, nebo (když text nejde bezpečně rozebrat zpátky na
        noty — jiná konvence zápisu než appka generuje) smazat úplně, aby
        firmware neukázal zastaralý/nesouhlasící posun."""
        variants = ascii_tab.generate_transpose_variants(ev.get("text", ""))
        if variants is None:
            ev.pop("variants", None)
        else:
            ev["variants"] = variants

    # Výchozí ASCII šablona nového bloku sóla — standardní ladění (e nejtenčí
    # nahoře), uživatel/GP si obsah přepíše přes edit_block()/import.
    _DEFAULT_TAB_TEXT = (
        "e|--------------------|\n"
        "B|--------------------|\n"
        "G|--------------------|\n"
        "D|--------------------|\n"
        "A|--------------------|\n"
        "E|--------------------|"
    )

    def add_block(self, kind: str) -> None:
        self._push_undo()
        order = self._track_order()
        if kind == "chord":
            ti = order[0] if order else 1
            ev = {"time_s": 0.0, "duration_s": self.default_chord_dur,
                  "chord": "C", "measure": 1, "track_index": ti, "source": "manual"}
            self.data.setdefault("chords_timeline", []).append(ev)
        elif kind == "tab":
            # cílí na stopu typu "solo_guitar" — vytvoří ji, pokud song
            # zatím žádnou nemá (čistě GP import/web bez sóla)
            ti = next((t for t in order if self._track_is_tab(t)), None)
            if ti is None:
                ti = (max(order) + 1) if order else 1
                self.tracks.append({"index": ti, "name": tr("track.solo_fallback_name"),
                                    "type": "solo_guitar", "is_drums": False})
                self.data["tracks"] = self.tracks
            ev = {"time_s": 0.0, "duration_s": TAB_BLOCK_DEFAULT_DUR_S,
                  "text": self._DEFAULT_TAB_TEXT, "track_index": ti, "source": "manual"}
            self._regen_tab_variants(ev)
            self.data.setdefault("tab_timeline", []).append(ev)
        else:
            ti = order[0] if order else 1
            ev = {"time_s": 0.0, "duration_s": 0.5, "text": "text",
                  "measure": 1, "track_index": ti, "source": "manual"}
            self.data.setdefault("lyrics_timeline", []).append(ev)
        self._relayout()

    # --- přichycení na mřížku (jediný correctní způsob časování — dle tempa,
    #     žádné odhady z délky textu/slabik) ---

    def auto_time_dialog(self) -> None:
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QComboBox, QDialogButtonBox, QLabel,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialog.auto_time_title"))
        form = QFormLayout(dlg)
        info = QLabel(tr("dialog.auto_time_info"))
        info.setStyleSheet("color:#aaaaaa;")
        form.addRow(info)

        sub = QComboBox()
        for lbl, v in [(tr("dialog.auto_time_grid_bar"), 0.25), (tr("dialog.auto_time_grid_beat"), 1),
                       (tr("dialog.auto_time_grid_half_beat"), 2), (tr("dialog.auto_time_grid_quarter_beat"), 4)]:
            sub.addItem(lbl, v)
        sub.setCurrentIndex(1)
        form.addRow(tr("dialog.auto_time_grid_label"), sub)

        bpm = (self.data.get("meta", {}) or {}).get("tempo_bpm", 120) or 120
        form.addRow(QLabel(tr("dialog.auto_time_tempo_info", bpm=bpm, beat_s=f"{60.0 / bpm:.3f}")))

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
        dlg.setWindowTitle(tr("dialog.bpm_title"))
        form = QFormLayout(dlg)

        spin = QSpinBox()
        spin.setRange(20, 300)
        spin.setValue(int(round(self.bpm)))
        form.addRow(tr("dialog.bpm_new_label"), spin)

        rescale = QCheckBox(tr("dialog.bpm_rescale_checkbox"))
        rescale.setChecked(True)
        rescale.setToolTip(tr("dialog.bpm_rescale.tooltip"))
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
        dlg.setWindowTitle(tr("dialog.count_in_title"))
        form = QFormLayout(dlg)

        info = QLabel(tr("dialog.count_in_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color:#aaaaaa;")
        form.addRow(info)

        bars_sb = QSpinBox()
        bars_sb.setRange(0, 8)
        bars_sb.setToolTip(tr("dialog.count_in_bars.tooltip"))
        bars_sb.setValue(int(meta.get("count_in_bars", 0) or 0) or 1)
        form.addRow(tr("dialog.count_in_bars_label"), bars_sb)

        artist_edit = QLineEdit(meta.get("artist", "") or "")
        title_edit = QLineEdit(meta.get("title", "") or "")
        form.addRow(tr("dialog.count_in_artist_label"), artist_edit)
        form.addRow(tr("dialog.count_in_title_label"), title_edit)

        form.addRow(QLabel(tr("dialog.count_in_tempo_info", bpm=f"{self.bpm:g}",
                              beats_per_measure=self.beats_per_measure, bar_s=f"{self.bar_s:.3f}")))

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec():
            self._push_undo()
            self.set_count_in(bars_sb.value(), artist_edit.text(), title_edit.text())

    def set_count_in(self, bars: int, artist: str, title: str) -> None:
        """Vloží/upraví odpočet před písní — klik-track (zavřená hajtka).
        Zobrazení odpočtu (číslo, interpret, název) si displej dopočítá
        za běhu z `meta.count_in_s`/`count_in_bars`/`artist`/`title` —
        žádná další data netřeba.

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
            # NAHRÁVKA se posouvá taky — je ukotvená přes `audio_offset_s`
            # (kolikátou sekundou časové osy začíná t=0 souboru), takže bez
            # tohohle by odpočet posunul VŠECHNO ostatní, ale nahrávka by
            # dál začínala hned na nule → bicí a audio by se rozešly přesně
            # o délku odpočtu (přesně nahlášený bug).
            self.set_audio_offset(self.audio_offset_s + delta, relayout=False)

        # nové klikací údery na bicí stopu (zavřená hajtka, GM 42)
        if drum_ti is not None and bars > 0:
            clicks = self.data.setdefault("drums_timeline", [])
            for i in range(bars * self.beats_per_measure):
                clicks.append({
                    "time_s": round(i * self.beat_s, 3),
                    "duration_s": round(self.beat_s * 0.3, 3),
                    "drum": "Closed Hi-Hat", "midi": 42, "track_index": drum_ti,
                    "source": "generated",   # klikací stopa, ne obsah písně
                })
            clicks.sort(key=lambda e: e["time_s"])

        artist = (artist or "").strip()
        title = (title or "").strip()
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
            if isinstance(it, DrumHitItem):
                self.remove_drum_hit(it)
                continue
            if isinstance(it, BassNoteItem):
                self.remove_bass_note(it)
                continue
            if not isinstance(it, BlockItem):
                continue
            key = ("chords_timeline" if it.kind == "chord" else
                   "tab_timeline" if it.kind == "tab" else "lyrics_timeline")
            try:
                self.data.get(key, []).remove(it.event)
            except ValueError:
                pass
            self.scene.removeItem(it)
            if it in self.blocks:
                self.blocks.remove(it)

    # --- kopírovat / vyjmout / vložit — funguje na LIBOVOLNĚ smíšený výběr
    # (text+akordy+bicí+basa najednou), zachová relativní časové rozestupy
    # mezi zkopírovanými prvky (vlož = posuň VŠECHNY najednou o stejnou
    # dobu), vkládá se vždy na PŮVODNÍ stopu/pražec/strunu/mód každého prvku ---

    def copy_selected(self) -> None:
        """Zkopíruje aktuální výběr do vnitřní schránky. Uloží se čas
        KAŽDÉHO prvku RELATIVNĚ k nejdřívějšímu z výběru (`rel_t`), aby šlo
        celou skupinu vložit jinam v čase se ZACHOVANÝMI vzájemnými
        rozestupy (`paste_at`) — ne jen jeden prvek zvlášť."""
        sel = self.scene.selectedItems()
        entries = []
        for it in sel:
            if isinstance(it, BlockItem):
                entries.append((it.kind, dict(it.event), self._item_time(it)))
            elif isinstance(it, DrumHitItem):
                entries.append(("drum", dict(it.event), self._item_time(it)))
            elif isinstance(it, BassNoteItem):
                entries.append(("bass", dict(it.event), self._item_time(it)))
        if not entries:
            return
        anchor = min(t for _k, _d, t in entries)
        self._clipboard = [{"kind": k, "data": d, "rel_t": round(t - anchor, 3)}
                           for k, d, t in entries]

    def cut_selected(self) -> None:
        self.copy_selected()
        if self._clipboard:
            self.delete_selected()

    def _select_by_identity(self, targets: list[dict]) -> None:
        self.scene.clearSelection()
        by_id = {id(d) for d in targets}
        for it in (self.blocks + self.drum_hit_items + self.bass_note_items):
            ev = getattr(it, "event", None)
            if ev is not None and id(ev) in by_id:
                it.setSelected(True)

    def paste_at(self, t: float, target_ti: Optional[int] = None) -> None:
        """Vloží aktuální schránku tak, aby nejdřívější zkopírovaný prvek
        přistál PŘESNĚ na `t` (přichyceno na mřížku) — ostatní prvky výběru
        se posunou o STEJNOU dobu, takže si zachovají svoje vzájemné
        rozestupy (ne mřížkové zaokrouhlení KAŽDÉHO zvlášť, to by mohlo
        rytmicky "rozjet" zkopírovanou pasáž).

        `target_ti`: když je zadané (vloženo přes kontextové menu
        KONKRÉTNÍHO řádku bicích/basy, viz `DrumRowBackground`/
        `BassLaneBackground`), bicí/basové údery se přemapují na TUHLE
        stopu. Bez toho by si každý nesl svůj PŮVODNÍ `track_index` ze
        zdrojové karty — při vkládání NAPŘÍČ SOUBORY (jiná karta s jinou
        skladbou) v cílovém souboru typicky neodpovídá žádné existující
        stopě, takže se úder přidal do dat, ale nikdy se nevykreslil —
        viz `_track_order`."""
        if not self._clipboard:
            return
        self._push_undo()
        anchor = self.snap_time(max(0.0, t))
        new_dicts: list[dict] = []
        for entry in self._clipboard:
            new_t = round(max(0.0, anchor + entry["rel_t"]), 3)
            d = dict(entry["data"])
            kind = entry["kind"]
            if kind == "lyric":
                d["time_s"] = new_t
                d.pop("line", None)
                self.data.setdefault("lyrics_timeline", []).append(d)
            elif kind == "chord":
                d["time_s"] = new_t
                d.pop("line", None)
                self.data.setdefault("chords_timeline", []).append(d)
            elif kind == "tab":
                d["time_s"] = new_t
                if target_ti is not None:
                    d["track_index"] = target_ti
                self.data.setdefault("tab_timeline", []).append(d)
            elif kind == "drum":
                d["time_s"] = new_t
                if target_ti is not None:
                    d["track_index"] = target_ti
                self.data.setdefault("drums_timeline", []).append(d)
            elif kind == "bass":
                d["time_s"] = new_t
                if target_ti is not None:
                    d["track_index"] = target_ti
                self.data.setdefault("bass_timeline", []).append(d)
            new_dicts.append(d)
        self._relayout()
        self._select_by_identity(new_dicts)

    # --- hromadný výběr/posun ("ripple") — vyber tenhle prvek a vše
    #     STEJNÉHO druhu na stejné stopě PŘED/PO něm v čase, pak přetáhni
    #     myší (Qt posune celý výběr najednou) nebo použij přesný posun
    #     tlačítkem "↔ Posunout vybrané…" ---

    @staticmethod
    def _item_time(item) -> float:
        return float(item.event.get("time_s", 0.0))

    def _ripple_candidates(self, item) -> list:
        if isinstance(item, BlockItem):
            # ZÁMĚRNĚ bez filtru na `kind` — text i akordy na stejné stopě
            # patří k sobě (řádek + akordy nad ním) a musí jít vybrat a
            # posouvat SPOLEČNĚ jedním výběrem, ne zvlášť pro každý druh.
            ti = item.event.get("track_index", 1)
            return [b for b in self.blocks if b.event.get("track_index", 1) == ti]
        if isinstance(item, DrumHitItem):
            ti = item.event.get("track_index", 1)
            return [d for d in self.drum_hit_items if d.event.get("track_index", 1) == ti]
        if isinstance(item, BassNoteItem):
            ti = item.event.get("track_index", 1)
            return [b for b in self.bass_note_items if b.event.get("track_index", 1) == ti]
        return []

    def select_ripple(self, item, direction: str) -> None:
        """Vybere `item` (obsažen, protože t0 splňuje >=/<=) a všechny
        odpovídající prvky na stejné stopě PŘED (`backward`) nebo PO
        (`forward`) něm v čase — pro hromadný posun zbytku časové osy.
        U textu/akordů (`BlockItem`) jde o text i akordy DOHROMADY (patří k
        sobě) PLUS odpovídající klip(y) na master "Displej" stopě (ať se
        posune i to, co se skutečně ukáže na karaoke displeji — viz
        `_ripple_candidates`); u bicích jen bicí, u klipů jen klipy."""
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

    def select_track_content(self, item) -> None:
        """„☰ Vybrat celou stopu“ — jako `select_ripple` naráz OBĚMA směry
        (bez ohledu na čas `item`u), tedy VŠE odpovídající na stejné stopě/
        druhu (u klipů: všechny klipy, Displej je jen jedna master stopa)."""
        self.scene.clearSelection()
        for other in self._ripple_candidates(item):
            other.setSelected(True)

    def select_all(self) -> None:
        """`Ctrl+A` — vybere VŠECHNY editovatelné prvky napříč VŠEMI
        stopami (text, akordy, bicí, basa) najednou. Vlnovka nahrávky se
        nezahrnuje (není "kus obsahu" k výběru/kopírování, jde jen celá
        táhnout/roztahovat — viz `WaveformItem`)."""
        self.scene.clearSelection()
        for it in (self.blocks + self.drum_hit_items + self.bass_note_items):
            it.setSelected(True)

    def _ensure_selected_for_context_menu(self, item) -> None:
        """Pravý klik na prvek, který NENÍ součástí aktuálního výběru, ho
        udělá jediným vybraným (standardní konvence) — aby „📋 Kopírovat“/
        „✂ Vyjmout“/„🗑 Smazat“ v jeho kontextovém menu vždy sáhly na
        SMYSLUPLNOU sadu (buď na existující víceprvkový výběr, pokud do něj
        `item` patřil, nebo jen na tenhle jeden prvek)."""
        if item not in self.scene.selectedItems():
            self.scene.clearSelection()
            item.setSelected(True)

    def shift_selected(self, delta_s: float) -> None:
        """Posune VŠECHNY aktuálně vybrané prvky (text/akord/buben) o
        `delta_s` (+ později, − dřív) — přesný, parametrický ekvivalent
        tažení myší, funguje na libovolně velký výběr (viz `select_ripple`)."""
        sel = self.scene.selectedItems()
        if not delta_s or not sel:
            return
        self._push_undo()   # jeden krok za celý hromadný posun
        blocks = [it for it in sel if isinstance(it, (BlockItem, DrumHitItem, BassNoteItem))]
        for it in blocks:
            it.event["time_s"] = round(max(0.0, float(it.event.get("time_s", 0.0)) + delta_s), 3)
            it._sync_from_event()

    def shift_selected_dialog(self) -> None:
        sel = self.scene.selectedItems()
        if not sel:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialog.shift_selected_title", n=len(sel)))
        form = QFormLayout(dlg)
        spin = QDoubleSpinBox()
        spin.setRange(-9999.0, 9999.0)
        spin.setDecimals(3)
        spin.setSuffix(" s")
        spin.setValue(0.0)
        form.addRow(tr("dialog.shift_selected_label", n=len(sel)), spin)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            self.shift_selected(spin.value())

    def paste_at_dialog(self) -> None:
        """'Vložit na přesný čas…' — jako `paste_at()`, ale cíl se zadá
        číslem místo odhadu myší přes přehrávací hlavu. Hlavně pro
        vkládání NAPŘÍČ KARTAMI (zkopírováno v jiném souboru): playhead
        cílové karty nemusí být tam, kde uživatel čeká, takže by mu
        Ctrl+V tiše přistálo jinde, než chtěl."""
        if not self._clipboard:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialog.paste_at_title", n=len(self._clipboard)))
        form = QFormLayout(dlg)
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 99999.0)
        spin.setDecimals(3)
        spin.setSuffix(" s")
        spin.setValue(self.playhead_s)
        form.addRow(tr("dialog.paste_at_label", n=len(self._clipboard)), spin)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            self.paste_at(spin.value())

    # ------------------------------------------------------------------
    # Výstup
    # ------------------------------------------------------------------

    def to_json(self) -> dict:
        """Vrátí upravený JSON slovník (setříděné osy + přepočtené karaoke_lines).

        `karaoke_lines[].start_s/end_s` je vždy PŘIROZENÝ rozsah odvozený ze
        slov řádku — žádná samostatná "displej" vrstva to dál neupravuje
        (klipy/obrazovky byly zrušené: firmware si za běhu samo odvodí, co
        zobrazit — aktivní zpívaný řádek → text+akordy, jinak aktivní akord
        → jen akord, jinak nic)."""
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

        # Přiřaď GLOBÁLNÍ index řádku `line` slovům a spočítej přirozený
        # (ze slov odvozený) rozsah — používá se pro přiřazení akordů
        # k řádku i pro zobrazovaný rozsah v `karaoke_lines`.
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

        karaoke: list[dict] = []
        for line_idx, (ti, _line) in enumerate(grouped):
            words = line_words[line_idx]
            start, end = line_ranges[line_idx]

            line_chords: list[str] = []
            for c in chords:
                if c.get("line") == line_idx and c.get("chord") not in line_chords:
                    line_chords.append(c["chord"])

            text = " ".join(w["text"] for w in words)
            kl = {
                "line": line_idx,
                "start_s": round(start, 3),
                "end_s": round(end, 3),
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

        meta = data.setdefault("meta", {})
        meta["edited_in_timeline"] = True
        meta["has_line_structure"] = True   # slova nesou index řádku `line`

        # Výstupní hranice formátu: na disk jde VŽDY nový model. Vnitřní
        # sekundový tvar výš je jen pracovní — konce, `karaoke_lines` ani
        # ploché osy se do souboru nedostanou (viz `_normalize_input`).
        from format_migrate import migrate
        new_doc, warnings = migrate(data)
        self.last_export_notes = warnings
        return new_doc
