
"""
Guitar Pro Viewer & Karaoke Exporter
PySide6 aplikace pro procházení .gp3/.gp4/.gp5 souborů a export do JSON pro karaoke systémy.
"""

import re
import sys
import json
import os
from html import escape as html_escape
from pathlib import Path
from typing import Optional

import guitarpro
import guitarpro.models as gpm
from web_import import WebImportDialog
from timeline_editor import TimelineEditor
from i18n import (tr, tr_action, register_tr, tr_label, tr_dock_title, tr_tab,
                   tr_window_title, get_language, set_language, available_languages)
from icons import icon, std_icon
from music_grid import meter_map_from_measures, TimeGrid

from PySide6.QtWidgets import (
     QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QLabel, QTextEdit,
    QPushButton, QFileDialog, QTabWidget, QTableWidget, QTableWidgetItem,
    QScrollArea, QStatusBar, QHeaderView, QMessageBox,
    QCheckBox, QFrame, QLineEdit, QComboBox, QToolBar, QSizePolicy,
    QTextBrowser, QDockWidget, QInputDialog, QDialog, QStyle, QDialogButtonBox,
    QFormLayout, QSpinBox,
)
from PySide6.QtCore import Qt, QSize, QThread, Signal
from PySide6.QtGui import QFont, QColor, QAction, QActionGroup, QIcon, QBrush, QPalette


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


def join_syllables(tokens) -> str:
    """Slabiky textu z GP po notách -> slova: „la-" + „dy" = „lady".
    Guitar Pro (i GP6/GP7) dělí slovo na slabiky spojovníkem na KONCI
    slabiky; samostatný „-" je jen prodloužení slabiky přes další notu.
    Slovo se spojovníkem uvnitř („rock-and-roll") zůstává beze změny."""
    out = ""
    for tok in tokens:
        tok = (tok or "").strip()
        if not tok or tok == "-":
            continue
        if out.endswith("-"):
            out = out[:-1] + tok
        else:
            out = (out + " " + tok) if out else tok
    return out.rstrip("-")


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
    """Vrátí seznam (tick, tempo_bpm) seřazený podle ticku — ticky jsou
    SUROVÉ ticky partitury (bez rozbalení repetic), viz `play_tempo_map`.

    GP3/4/5 ukládá změny tempa do mix table na konkrétním beatu
    (`beat.effect.mixTableChange.tempo`), `MeasureHeader` je v pyguitarpro
    nenese. Sbírá se ze všech stop — změnu tempa může mít zapsanou kterákoli.
    GP6/GP7 je mají v automaci hlavní stopy (`song.extraTempoChanges`,
    viz gpif_reader.py).
    """
    changes: dict[int, int] = {960: song.tempo}   # GP tick začíná na 960
    for track in song.tracks:
        for m in track.measures:
            h_tempo = getattr(m.header, "tempo", None)
            val = getattr(h_tempo, "value", h_tempo)
            if isinstance(val, int) and val > 0:
                changes[m.header.start] = val
            for v in m.voices:
                for b in v.beats:
                    mc = getattr(b.effect, "mixTableChange", None)
                    t = getattr(mc, "tempo", None) if mc is not None else None
                    if t is not None and getattr(t, "value", 0) > 0:
                        changes[b.start] = t.value
    # GP6/GP7 (gpif_reader) mají tempo v automaci hlavní stopy, ne v beatu
    for tick, bpm in getattr(song, "extraTempoChanges", None) or []:
        if bpm > 0:
            changes[int(tick)] = int(bpm)
    out: list[tuple[int, int]] = []
    for tick, bpm in sorted(changes.items()):
        if not out or out[-1][1] != bpm:
            out.append((tick, bpm))
    return out


def play_tempo_map(measures, order, tempo_map: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Mapa tempa v tickách PŘEHRÁVÁNÍ (repetice rozbalené, `order` =
    `expand_measure_order`). Surová mapa z `build_tempo_map` má ticky
    partitury — po repetici by změna tempa padla o rozbalené takty dřív.
    Každý přehraný takt dostane tempo platné v JEHO místě partitury."""
    if not order:
        return list(tempo_map)
    out: list[tuple[int, int]] = []
    play_tick = 960
    for src in order:
        h = measures[src].header
        start, length = h.start, h.length
        bpm0 = tempo_at_tick(start, tempo_map)
        if not out or out[-1][1] != bpm0:
            out.append((play_tick, bpm0))
        for t, bpm in tempo_map:
            if start < t < start + length and bpm != out[-1][1]:
                out.append((play_tick + t - start, bpm))
        play_tick += length
    return out


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


# --- navigační značky (Segno / Coda / Fine / D.C. / D.S.) ------------------
# GP5 je čte jako pevný seznam jmen (viz `readDirections` v guitarpro/gp5.py):
# KOTVY ("sem skoč") sedí na `header.direction`, SKOKY ("odtud skoč") na
# `header.fromDirection`. Obojí je na ÚROVNI SKLADBY — každá značka je
# v souboru nejvýš jednou, takže se dá celá sada sesbírat předem.

# Kam který skok míří. `None` = úplný začátek skladby (Da Capo).
_JUMP_ANCHOR = {
    "Da Capo": None,
    "Da Capo al Coda": None,
    "Da Capo al Double Coda": None,
    "Da Capo al Fine": None,
    "Da Segno": "Segno",
    "Da Segno al Coda": "Segno",
    "Da Segno al Double Coda": "Segno",
    "Da Segno al Fine": "Segno",
    "Da Segno Segno": "Segno Segno",
    "Da Segno Segno al Coda": "Segno Segno",
    "Da Segno Segno al Double Coda": "Segno Segno",
    "Da Segno Segno al Fine": "Segno Segno",
}
# Skoky „al Coda": (značka, která TEPRVE PO tomhle skoku spustí odskok,
# cílová kotva). Dokud skok neproběhl, je „Da Coda" v partituře mrtvá —
# přehrávání jí projde skrz, přesně jak to funguje v notách.
_JUMP_CODA = {
    "Da Capo al Coda":               ("Da Coda", "Coda"),
    "Da Segno al Coda":              ("Da Coda", "Coda"),
    "Da Segno Segno al Coda":        ("Da Coda", "Coda"),
    "Da Capo al Double Coda":        ("Da Double Coda", "Double Coda"),
    "Da Segno al Double Coda":       ("Da Double Coda", "Double Coda"),
    "Da Segno Segno al Double Coda": ("Da Double Coda", "Double Coda"),
}
# Skoky „al Fine": po skoku se hraje jen do taktu s kotvou Fine.
_JUMP_FINE = {"Da Capo al Fine", "Da Segno al Fine", "Da Segno Segno al Fine"}


def _direction_name(header, attr_name: str):
    """Jméno navigační značky z hlavičky taktu, nebo `None`."""
    sign = getattr(header, attr_name, None)
    return getattr(sign, "name", None) if sign is not None else None


def expand_measure_plan(measures) -> tuple[list[tuple[int, int]], list[str]]:
    """Skutečné pořadí přehrávání jako `[(index_zdrojového_taktu, průchod)]`
    plus seznam varování. `průchod` je 1-based pořadí, kolikáté je to zaznění
    TÉHOŽ zdrojového taktu — přesně to, co potřebuje `linearMap` v exportu
    (stejný takt může při druhém průchodu znít jinak).

    Respektuje:

    - **repetice** (`isRepeatOpen`/`repeatClose`) a **1./2. zakončení**
      (`repeatAlternative`, bitmaska průchodů) — podrobnosti v
      `expand_measure_order`;
    - **navigační skoky** Segno / Coda / Fine / D.C. / D.S. Každý skok se
      provede **nejvýš jednou** — tak to funguje v notaci a zároveň to samo
      o sobě brání zacyklení. Repetice se po skoku už neopakují (jejich
      počitadla zůstávají vyčerpaná), což odpovídá běžné konvenci „na druhý
      průchod bez repetic".

    U **nekonzistentních značek** (D.S. bez Segna, „al Coda" bez Cody) se
    NEHÁDÁ: skok se přeskočí, pokračuje se lineárně a důvod se vrátí ve
    varováních, ať se dá ukázat uživateli."""
    n = len(measures)
    if n == 0:
        return [], []

    warnings: list[str] = []

    # Kotvy a skoky se sesbírají dopředu — jsou to pozice v PARTITUŘE,
    # nezávislé na tom, kudy zrovna přehrávání běží.
    anchors: dict[str, int] = {}
    jumps: dict[int, str] = {}
    for idx, m in enumerate(measures):
        h = m.header
        anchor = _direction_name(h, "direction")
        if anchor and anchor not in anchors:
            anchors[anchor] = idx
        jump = _direction_name(h, "fromDirection")
        if jump:
            jumps[idx] = jump

    plan: list[tuple[int, int]] = []
    played: dict[int, int] = {}        # zdrojový takt -> kolikrát už zazněl
    repeat_start = 0
    pass_count: dict[int, int] = {}
    used_jumps: set[str] = set()
    coda_from: str | None = None       # značka, která teď spustí odskok na Codu
    coda_to: str | None = None
    stop_at_fine = False

    i = 0
    SAFETY = n * 8 + 64   # tvrdý strop proti nekonečné smyčce na poškozených datech
    steps = 0
    while i < n and steps < SAFETY:
        steps += 1
        h = measures[i].header
        alt = getattr(h, "repeatAlternative", 0) or 0
        cur_pass = pass_count.get(repeat_start, 1)
        is_played = alt == 0 or bool(alt & (1 << (cur_pass - 1)))
        if is_played:
            played[i] = played.get(i, 0) + 1
            plan.append((i, played[i]))
        if getattr(h, "isRepeatOpen", False):
            repeat_start = i
            pass_count.setdefault(repeat_start, 1)
        close = getattr(h, "repeatClose", -1) or -1
        # Repetice na taktu, který se v tomhle průchodu NEHRAJE (jiné
        # zakončení), se nevyhodnocuje. Jinak u zakončení 1.+3. / 2.+4.
        # s repeticí na obou taktech (Tublatanka – Môj starý dobrý kabát, GP7)
        # skočil nezahraný takt 1./3. zakončení zpět dřív, než zazněl takt
        # 2./4. zakončení.
        if close > 0 and is_played:
            done = pass_count.get(repeat_start, 1)
            if done <= close:
                pass_count[repeat_start] = done + 1
                i = repeat_start
                continue

        # Navigační značky se řeší AŽ když je repetice na tomhle taktu
        # vyčerpaná — jinak by D.S. odpálilo hned na prvním průchodu.
        if stop_at_fine and _direction_name(h, "direction") == "Fine":
            break

        mark = jumps.get(i)
        if mark is not None and mark == coda_from:
            target = anchors.get(coda_to)
            if target is None:
                warnings.append("Odskok z " + mark + " se přeskočil — cílová "
                                "značka " + str(coda_to) + " v souboru není.")
            else:
                coda_from = coda_to = None
                i = target
                continue
        elif mark in _JUMP_ANCHOR and mark not in used_jumps:
            used_jumps.add(mark)
            anchor_name = _JUMP_ANCHOR[mark]
            target = 0 if anchor_name is None else anchors.get(anchor_name)
            if target is None:
                warnings.append("Skok " + mark + " se přeskočil — značka "
                                + str(anchor_name) + " v souboru není.")
            else:
                if mark in _JUMP_FINE:
                    if "Fine" in anchors:
                        stop_at_fine = True
                    else:
                        warnings.append("Skok " + mark + " nemá značku Fine — "
                                        "hraje se až do konce skladby.")
                elif mark in _JUMP_CODA:
                    coda_mark, coda_anchor = _JUMP_CODA[mark]
                    if coda_anchor in anchors:
                        coda_from, coda_to = coda_mark, coda_anchor
                    else:
                        warnings.append("Skok " + mark + " nemá značku "
                                        + coda_anchor + " — odskok na Codu "
                                        "se neprovede.")
                i = target
                continue
        i += 1

    if steps >= SAFETY:
        warnings.append("Rozbalení taktů narazilo na bezpečnostní strop — "
                        "značky repetic/skoků jsou nejspíš poškozené, zbytek "
                        "skladby se vynechal.")
    return plan, warnings


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

    Zohledňuje i navigační skoky Segno / Coda / Fine / D.C. / D.S.
    (`header.direction` a `header.fromDirection`) — viz `expand_measure_plan`,
    kde je celá logika i vracená varování."""
    plan, _warnings = expand_measure_plan(measures)
    return [idx for idx, _pass in plan]


def repeat_blocks(measures) -> list[tuple[int, int, int]]:
    """Bloky repetic v partituře: [(první takt, poslední takt, počet zaznění)]
    (indexy od 0). Konec bloku si hlavička pamatuje i po nastavení na jedno
    zaznění (`_repeatEnd`) — jinak by blok ze seznamu zmizel a nešel vrátit."""
    out: list[tuple[int, int, int]] = []
    start = 0
    for i, m in enumerate(measures):
        h = m.header
        if getattr(h, "isRepeatOpen", False):
            start = i
        close = getattr(h, "repeatClose", -1) or -1
        if close > 0:
            h._repeatEnd = True
        if close > 0 or getattr(h, "_repeatEnd", False):
            out.append((start, i, max(1, close + 1)))
    return out


def set_repeat_plays(measures, end_idx: int, plays: int) -> None:
    """Počet zaznění bloku repetice končícího taktem `end_idx` (1 = bez opakování)."""
    h = measures[end_idx].header
    h._repeatEnd = True
    h.repeatClose = plays - 1 if plays > 1 else -1


def tempo_at_tick(tick: int, tempo_map: list[tuple[int, int]]) -> int:
    """Tempo (BPM) platné v daném absolutním (surovém, nerozbaleném) GP ticku."""
    bpm = tempo_map[0][1]
    for map_tick, map_tempo in tempo_map:
        if tick < map_tick:
            break
        bpm = map_tempo
    return bpm


def walk_track_beats(track, tempo_map: list[tuple[int, int]], all_voices: bool = False):
    """Generuje `(measure_idx0, beat, time_s, duration_s)` pro stopu **v
    reálném pořadí přehrávání** — repetice rozbalené (`expand_measure_order`).

    `tempo_map` je SUROVÁ mapa z `build_tempo_map` (ticky partitury); tady se
    převede na ticky přehrávání (`play_tempo_map`), takže změna tempa za
    repeticí padne na správný takt.

    Čas beatu = začátek taktu z mřížky (rozbalené pořadí + metrum) + pozice
    beatu v taktu z jeho ticku, obojí přes mřížku tempa (změna tempa uvnitř
    taktu se započítá). Reset na každé taktové čáře: špatně notovaný „tichý"
    takt (vokál přes mezihru s chybějící pauzou) utrhne čas jen uvnitř
    jednoho taktu, ne kumulativně (naměřeno dřív: jinak −37 s u zpěvu).

    `all_voices=True` — i 2.–4. hlas (bicí v GP5 mívají kopák ve 2. hlase)."""
    TICKS_PER_BEAT = 960
    order = expand_measure_order(track.measures)
    if not order:
        return
    meter_map = meter_map_from_measures(track.measures, order)
    beats_per_bar = meter_map[0]["num"] if meter_map else 4
    grid = TimeGrid([{"tick": t, "bpm": b}
                     for t, b in play_tempo_map(track.measures, order, tempo_map)],
                    beats_per_bar, meter_map=meter_map)
    for play_idx, m_idx in enumerate(order):
        m = track.measures[m_idx]
        bar_beat0 = grid.beats_before_bar(play_idx + 1)
        bar_start_tick = m.header.start
        for v_idx, v in enumerate(m.voices):
            if v_idx > 0 and not all_voices:
                continue
            for b in v.beats:
                g0 = bar_beat0 + (b.start - bar_start_tick) / TICKS_PER_BEAT
                g1 = g0 + b.duration.time / TICKS_PER_BEAT
                t0 = grid.seconds(g0)
                yield m_idx, b, round(t0, 4), round(grid.seconds(g1) - t0, 4)


_BASS_NAME_RE = re.compile(r"bass|bas[ay]?|baskytar", re.I)


def pick_bass_track(song, exclude: int | None = None) -> int | None:
    """Index (0-based) JEDNÉ basové stopy, nebo `None`. ESP32 hraje jednu
    basovou linku — víc 4strunných stop (Let It Be: „Piano Bass" + „Bass")
    by se slilo a noty zdvojily. Pořadí: GM basový nástroj (program 32–39),
    pak jméno s „bass"/„basa" (ne „piano"), pak první v souboru.
    `exclude` = 0-based index stopy, která basou být nemůže (zpěv)."""
    cands = [i for i, t in enumerate(song.tracks)
             if not t.isPercussionTrack and len(t.strings) == 4 and i != exclude]
    if not cands:
        return None

    def score(i):
        t = song.tracks[i]
        prog = t.channel.instrument if t.channel else -1
        name = t.name or ""
        return (32 <= prog <= 39,
                bool(_BASS_NAME_RE.search(name)) and "piano" not in name.lower(),
                -i)
    return max(cands, key=score)


def gp_drum_events(song, tempo_map) -> tuple[list[dict], dict | None]:
    """Údery bicích ze VŠECH bicích stop (GP6/GP7 je často dělí — Kabát
    Bára: 2× Drumkit, Šaman: Drumkit + Tamburína), všechny hlasy, repetice
    rozbalené. Vynechané: svázané noty (tie = doznívání), neznějící výšky
    (<27) a duplicity — týž buben ve stejný okamžik jen jednou (příraz
    „na dobu" ve stejném bubnu, dvě stopy hrající totéž). Všechny údery
    nesou index PRVNÍ bicí stopy — ESP32 má jednu bicí linku."""
    perc = [(i, t) for i, t in enumerate(song.tracks) if t.isPercussionTrack]
    if not perc:
        return [], None
    first_idx, first = perc[0]
    ti = first_idx + 1
    info = {"index": ti, "name": first.name, "type": "drums", "is_drums": True,
            "instrument_midi": first.channel.instrument if first.channel else 0}
    out: list[dict] = []
    seen: set[tuple[float, int]] = set()
    for _i, track in perc:
        for _m, b, time_s, duration_s in walk_track_beats(track, tempo_map, all_voices=True):
            for n in b.notes:
                if n.type == gpm.NoteType.tie:
                    continue
                midi = note_to_midi(n, track)
                if midi < 27:
                    # Pod GM/GS perkusemi (27–87) nic nezní — typicky výška 0,
                    # pozůstatek převodu tabu (Run To the Hills: 2184 not).
                    # Exportovaly by se jako sample „?" a firmware je nenajde.
                    continue
                key = (round(time_s, 3), midi)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"time_s": time_s, "duration_s": duration_s,
                            "drum": drum_name(midi), "midi": midi,
                            "track_index": ti,
                            # `source` = odkud událost je: "gp" (z Guitar Pro),
                            # "manual" (napsal člověk), "generated" (dopočítal
                            # program). Editor podle toho pozná, co má uživatel
                            # ještě zkontrolovat sluchem.
                            "source": "gp"})
    out.sort(key=lambda e: e["time_s"])
    return out, info


def gp_bass_events(song, tempo_map, exclude: int | None = None) -> tuple[list[dict], dict | None]:
    """Noty basy z `pick_bass_track`: všechny hlasy, repetice rozbalené.
    Svázaná nota (tie) NEní nový úder — prodlouží předchozí notu na téže
    struně (jinak by basa na ESP32 zahrála tón znovu)."""
    bi = pick_bass_track(song, exclude)
    if bi is None:
        return [], None
    track = song.tracks[bi]
    ti = bi + 1
    info = {"index": ti, "name": track.name, "type": "bass", "is_drums": False,
            "instrument_midi": track.channel.instrument if track.channel else 0}
    out: list[dict] = []
    last_on_string: dict[int, dict] = {}
    for _m, b, time_s, duration_s in walk_track_beats(track, tempo_map, all_voices=True):
        for n in b.notes:
            if n.type == gpm.NoteType.tie:
                prev = last_on_string.get(n.string)
                if prev is not None:
                    prev["duration_s"] = round(max(prev["duration_s"],
                                                   time_s + duration_s - prev["time_s"]), 4)
                continue
            midi = note_to_midi(n, track)
            ev = {"time_s": time_s, "duration_s": duration_s,
                  "note_name": midi_to_name(midi), "midi": midi,
                  "string": n.string, "fret": n.value,
                  "track_index": ti, "source": "gp"}
            out.append(ev)
            last_on_string[n.string] = ev
    out.sort(key=lambda e: e["time_s"])
    return out, info


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


# --- automatická extrakce ASCII sólo tabulatury z GP not ---------------------
#
# Uživatel si tabulaturu obvykle NAPÍŠE ručně (viz `BlockItem` kind="tab" v
# timeline_editor.py), ale u GP souborů se solidním přepisem (typicky
# stažených ze Songsterr Plus apod., kde má string/fret/techniku přesně) se
# vyplatí ASCII blok rovnou vygenerovat — uživatel si ho pak jen doladí,
# místo aby opisoval celé sólo od nuly. Samotný renderer je sdílený s MIDI
# importem (`midi_import.py`) — viz `ascii_tab.py`.

from ascii_tab import segment_and_render_tab_blocks as _segment_and_render_tab_blocks


def _note_tech_char(note) -> str:
    """Krátký textový sufix za pražcem v ASCII tabulatuře (viz
    `ascii_tab.render_ascii_tab_block`) — 'h' hammer-on/pull-off (GP3
    attribut `hammer` nerozlišuje směr), '~' vibrato, '/' slide, 'b' bend."""
    if not note.effect:
        return ""
    e = note.effect
    tech = ""
    if e.hammer:
        tech += "h"
    if e.vibrato:
        tech += "~"
    if e.slides:
        tech += "/"
    if e.bend and e.bend.points:
        tech += "b"
    return tech


def _tab_string_labels(track) -> list[str]:
    """Jedno písmeno na strunu (bez oktávy) pro levý okraj ASCII tabulatury
    — první (nejvyšší, `string==1`) struna malým písmenem podle konvence
    ("e|---" u standardního ladění), zbytek velkým, přesně jako v
    uživatelově referenčním příkladu."""
    labels = [midi_to_name(s.value) for s in track.strings]
    if labels:
        labels[0] = labels[0].lower()
    return labels


# Druh stopy → i18n klíč popisku ve sloupci "Typ" levého panelu. Skutečný
# text se dopočítává přes `_track_type_label()` znovu při KAŽDÉM přepnutí
# jazyka (viz `_retranslate_track_tree`, registrováno v `_setup_ui`) — bez
# toho by řádky stromu zůstaly zamrzlé v jazyce, který byl aktivní v
# okamžiku importu GP/načtení JSON (stejná past jako dřív u popisků karet).
_TRACK_TYPE_KEYS = {
    "drums": "track.type_drums",
    "vocal": "track.type_vocal",
    "bass": "track.type_bass",
    "solo_guitar": "track.type_solo_guitar",
    "guitar": "track.type_guitar",
}


def _track_type_label(kind: str) -> str:
    return tr(_TRACK_TYPE_KEYS.get(kind, kind))


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
        # GP3–GP5 přes pyguitarpro (s náhradním utf-8), GP6 .gpx a GP7+ .gp
        # přes gpif_reader — formát se pozná z hlavičky souboru, ne z přípony.
        from gpif_reader import load_any
        try:
            self.done.emit(load_any(self.path, encoding=self.encoding))
        except Exception as ex:
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
        filter_label = QLabel()
        tr_label(filter_label, "detail.filter_label")
        filter_bar.addWidget(filter_label)
        self.filter_from = QLineEdit()
        register_tr(self.filter_from.setPlaceholderText, "detail.filter_from")
        self.filter_from.setMaximumWidth(60)
        self.filter_to = QLineEdit()
        register_tr(self.filter_to.setPlaceholderText, "detail.filter_to")
        self.filter_to.setMaximumWidth(60)
        filter_bar.addWidget(self.filter_from)
        filter_bar.addWidget(QLabel("–"))
        filter_bar.addWidget(self.filter_to)
        self.filter_btn = QPushButton()
        tr_label(self.filter_btn, "detail.filter_button")
        filter_bar.addWidget(self.filter_btn)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # Tabulka
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        register_tr(lambda _t: self.table.setHorizontalHeaderLabels([
            tr("detail.col_bar"), tr("detail.col_time"), tr("detail.col_duration"),
            tr("detail.col_tab"), tr("detail.col_effects"), tr("detail.col_chord"),
            tr("detail.col_lyrics"), tr("detail.col_midi"),
        ]), "detail.col_bar")
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
        # Repeat-aware: dřív se čas počítal ze SUROVÉHO ticku (`ticks_to_seconds`),
        # takže takt PO repetici ukazoval čas o délku repetice napřed proti
        # tomu, kdy se skutečně hraje — repetice se v surových tickách vůbec
        # neopakuje, jen ji přehrávač čte dvakrát. `walk_track_beats` počítá
        # totéž, co GP import (autoritativní mřížka + pozice v taktu), takže
        # tahle tabulka konečně ukazuje SKUTEČNÝ čas, ne odhad ze čtení shora
        # dolů. Repetice se teď zobrazí jako dva řádky se stejným číslem
        # taktu, ale jiným časem — to je věcně správně, zní to dvakrát.
        rows = []
        for m_idx, b, time_s, _dur in walk_track_beats(track, tempo_map):
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

            # Zvýraznění — vždy nastav i POPŘEDÍ (ne jen pozadí): tyhle
            # pastelové zvýraznění zůstávají SVĚTLÉ i v tmavém tématu
            # (schválně, ať vynikne), takže bez explicitního tmavého textu
            # by na nich byl nečitelný světlý text z tmavé palety.
            dark_fg = QBrush(QColor(20, 20, 20))
            if r["text"]:
                for col in range(8):
                    it = self.table.item(ri, col)
                    if it:
                        it.setBackground(QBrush(QColor(180, 255, 180)))
                        it.setForeground(dark_fg)
            elif r["chord"]:
                for col in [5]:
                    it = self.table.item(ri, col)
                    if it:
                        it.setBackground(QBrush(QColor(180, 210, 255)))
                        it.setForeground(dark_fg)
            if r["effects"]:
                it = self.table.item(ri, 4)
                if it:
                    it.setBackground(QBrush(QColor(255, 240, 180)))
                    it.setForeground(dark_fg)


class HelpDialog(QDialog):
    """Čitelná nápověda — nahrazuje starý jednořádkový `QMessageBox`
    ("tohle se nedá číst"): jedno srolovatelné okno se strukturovanými
    sekcemi (výběr, přidávání objektů, kopírování, zkratky…) místo jednoho
    odstavce splývajícího textu. Obsah je bohaté HTML (nadpisy, seznamy,
    tabulka zkratek) z `i18n.help.full.html` — postavený JEDNOU při
    otevření (ne přes `register_tr`, který by tu při opakovaném
    otevírání/zavírání dialogu hromadil odkazy na už zaniklé C++ objekty —
    stejné riziko jako u dřívějšího teardown race u přimrazených hlaviček)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("help.timeline_help"))
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(tr("help.full.html"))
        layout.addWidget(browser, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        layout.addWidget(bb)


# ---------------------------------------------------------------------------
# Hlavní okno
# ---------------------------------------------------------------------------

class GuitarProViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        # "Identita dokumentu" (song/current_gp_file/current_json_file/
        # tempo_map/_loaded_json) NENÍ tady — žije na `TimelineEditor`
        # (jedna instance = jedna karta, viz plán "Multi-dokument"). Tahle
        # třída k ní přistupuje přes `self.timeline`/`self.song`/… property
        # níže, které se vždy vztahují k PRÁVĚ AKTIVNÍ kartě.
        self._workers: list["LoadWorker"] = []   # běžící načítání GP na pozadí, víc najednou (1 na kartu)
        self._setup_ui()

    # ------------------------------------------------------------------
    # "Aktivní karta" — self.timeline a odvozené property se VŽDY vztahují
    # k právě zobrazené kartě (self.doc_tabs.currentWidget()). Díky tomu
    # funguje veškerý STÁVAJÍCÍ kód, který čte/píše self.timeline/self.song/
    # self.current_gp_file/self.current_json_file/self.tempo_map beze
    # změny — automaticky cílí na aktivní kartu. VÝJIMKA: asynchronní
    # dokončení `LoadWorker` (viz `_on_loaded`) NESMÍ jít přes tyhle
    # property (karta, na kterou se načítalo, už nemusí být aktivní), tam
    # se píše přímo do zachyceného `target` — viz komentář u `_load_file`.
    # ------------------------------------------------------------------

    @property
    def timeline(self) -> TimelineEditor:
        return self.doc_tabs.currentWidget()

    @property
    def song(self):
        return self.timeline.song

    @song.setter
    def song(self, value) -> None:
        self.timeline.song = value

    @property
    def current_gp_file(self) -> Optional[str]:
        return self.timeline.current_gp_file

    @current_gp_file.setter
    def current_gp_file(self, value: Optional[str]) -> None:
        self.timeline.current_gp_file = value

    @property
    def current_json_file(self) -> Optional[str]:
        return self.timeline.current_json_file

    @current_json_file.setter
    def current_json_file(self, value: Optional[str]) -> None:
        self.timeline.current_json_file = value

    @property
    def tempo_map(self) -> list:
        return self.timeline.tempo_map

    @tempo_map.setter
    def tempo_map(self, value: list) -> None:
        self.timeline.tempo_map = value

    @property
    def _loaded_json(self) -> Optional[dict]:
        return self.timeline._loaded_json

    @_loaded_json.setter
    def _loaded_json(self, value: Optional[dict]) -> None:
        self.timeline._loaded_json = value

    # ------------------------------------------------------------------
    # Karty (multi-dokument)
    # ------------------------------------------------------------------

    def _new_tab(self) -> TimelineEditor:
        """Vytvoří novou kartu (nový, prázdný dokument) a udělá ji aktivní.
        Volá se před KAŽDÝM otevřením/importem/novou písní (Nová píseň,
        Otevřít, Importovat z GP) — nikdy nepřepisuje obsah existující
        karty. Sloučení (`merge_with_web`) je výjimka — to záměrně
        pracuje NAD aktivní kartou, ne v nové."""
        ed = TimelineEditor()
        ed.export_callback = self._export_timeline_json
        ed.dirty_changed.connect(lambda _dirty, ed=ed: self._refresh_tab_label(ed))
        idx = self.doc_tabs.addTab(ed, tr("tab.untitled"))
        self.doc_tabs.setCurrentIndex(idx)
        return ed

    def _tab_label_for(self, ed: TimelineEditor) -> str:
        meta = (ed.data.get("meta", {}) or {}) if ed.data else {}
        base = meta.get("title") or ""
        if base.strip().lower() in ("unknown", "neznámý", "t"):   # výchozí název z importu ≠ název písně
            base = ""
        if not base:
            path = ed.current_json_file or ed.current_gp_file
            base = Path(path).stem if path else tr("tab.untitled")
        return ("• " if ed._dirty else "") + base

    def _refresh_tab_label(self, ed: TimelineEditor) -> None:
        idx = self.doc_tabs.indexOf(ed)
        if idx >= 0:
            self.doc_tabs.setTabText(idx, self._tab_label_for(ed))

    def _refresh_all_tab_labels(self) -> None:
        """Přeznačí VŠECHNY karty — volá se i po přepnutí jazyka (viz
        `register_tr` v `_setup_ui`), aby se přeložil i popisek karty bez
        názvu ("Bez názvu"/"Untitled"/…); ten se jinak nastaví jen jednou
        při vytvoření karty a v registru překladů by sám o sobě nebyl."""
        for i in range(self.doc_tabs.count()):
            ed = self.doc_tabs.widget(i)
            if ed is not None:
                self.doc_tabs.setTabText(i, self._tab_label_for(ed))

    def _retranslate_track_tree(self) -> None:
        """Přeznačí sloupec "Typ" levého panelu (Stopy) — volá se i po
        přepnutí jazyka (viz `register_tr` v `_setup_ui`). Text sloupce se
        jinak nastaví jen jednou při importu GP/načtení JSON a zůstal by
        zamrzlý v jazyce z toho okamžiku (stejná past jako u popisků
        karet, viz `_refresh_all_tab_labels`) — druh stopy je proto
        uložený i jako data (`Qt.UserRole` sloupce 1), aby šlo popisek
        kdykoli přepočítat znovu."""
        for i in range(self.track_tree.topLevelItemCount()):
            item = self.track_tree.topLevelItem(i)
            kind = item.data(1, Qt.UserRole)
            if kind:
                item.setText(1, _track_type_label(kind))

    def _confirm_discard(self, ed: TimelineEditor) -> bool:
        """Karta `ed` má neuložené změny — zeptá se Uložit/Zahodit/Zrušit.
        Vrací True, když je bezpečné pokračovat v zavření (uloženo, NEBO
        uživatel schválně zahodil), False když se má zavření karty zrušit."""
        name = self._tab_label_for(ed).lstrip("• ")
        reply = QMessageBox.question(
            self, tr("tab.close_confirm_title"), tr("tab.close_confirm_body", name=name),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Save:
            if ed is not self.timeline:
                self.doc_tabs.setCurrentWidget(ed)
            self.save_json()
            if ed._dirty:   # dialog "Uložit jako" zrušen / uložení selhalo
                return False
        return True

    def _close_tab(self, index: int) -> None:
        ed = self.doc_tabs.widget(index)
        if ed is None:
            return
        if ed._dirty and not self._confirm_discard(ed):
            return
        ed.player.stop()
        ed.gp_player.stop()
        ed.bass_player.stop()
        self.doc_tabs.removeTab(index)
        ed.deleteLater()
        if self.doc_tabs.count() == 0:
            self._new_tab()

    def _on_active_tab_changed(self, index: int) -> None:
        """Přepnutí karty — pauzne přehrávání ostatních karet, přepne mix
        panel + snap-combo/time-label ve sdíleném toolbaru na NOVOU
        aktivní kartu a znovu vykreslí sdílené doky (Stopy/Náhledy) podle
        jejího obsahu (beze změny `active.data` — jen čtení/zobrazení)."""
        for i in range(self.doc_tabs.count()):
            ed = self.doc_tabs.widget(i)
            if ed is not None and i != index:
                ed.player.pause()
                ed.gp_player.pause()
                ed.bass_player.pause()

        active = self.timeline
        if active is None:
            return

        # POZOR: `QScrollArea.setWidget()` SMAŽE předchozí widget, pokud
        # nějaký byl (dokumentované chování) — bez `takeWidget()` by tohle
        # smazalo `mix_panel` ODCHÁZEJÍCÍ karty (ta pořád existuje, jen se
        # skrývá), ne jen ho odpojilo.
        self.mix_scroll.takeWidget()
        self.mix_scroll.setWidget(active.mix_panel)
        self._swap_toolbar_widgets(active)

        if getattr(active, "_pending_gp_populate", False):
            active._pending_gp_populate = False
            self._finish_gp_import(active)
        elif active.song is not None:
            self._populate_ui()
        elif active.data:
            self._render_docks_from_json()
        else:
            self._clear_docks_to_empty()

    def closeEvent(self, event) -> None:
        """Zavření CELÉHO okna s víc kartami — projde je a pro každou
        neuloženou nabídne stejné Uložit/Zahodit/Zrušit jako zavření
        jedné karty (`_confirm_discard`); zrušení kterékoli kartu zruší
        celé zavření okna."""
        for i in range(self.doc_tabs.count()):
            ed = self.doc_tabs.widget(i)
            if ed is not None and ed._dirty:
                self.doc_tabs.setCurrentWidget(ed)
                if not self._confirm_discard(ed):
                    event.ignore()
                    return
        event.accept()

    # ------------------------------------------------------------------
    # UI stavba
    # ------------------------------------------------------------------

    def _setup_ui(self):
        tr_window_title(self, "app.window_title")
        self.setMinimumSize(1300, 820)

        # Menu bar a toolbary se staví AŽ POTÉ, co existují self.timeline a
        # oba dock widgety (viz níže, blok "Menu bar + toolbary") — spousta
        # akcí (Úpravy/Časová osa) volá přímo self.timeline.<metoda>, a
        # View menu potřebuje self.dock_tracks/self.dock_previews.

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
        self.lbl_artist.setStyleSheet("font-size: 13px; color: #aaaaaa;")
        self.lbl_meta = QLabel("")
        self.lbl_meta.setStyleSheet("font-size: 12px; color: #999999;")
        info_layout.addWidget(self.lbl_title)
        info_layout.addWidget(QLabel(" — "))
        info_layout.addWidget(self.lbl_artist)
        info_layout.addStretch()
        info_layout.addWidget(self.lbl_meta)
        root.addWidget(info_frame)

        # Časová osa vyplní celé centrum — karty (self.doc_tabs), jedna
        # TimelineEditor instance = jeden otevřený dokument (viz plán
        # "Multi-dokument"). `self.timeline` výše je property vracející
        # PRÁVĚ AKTIVNÍ kartu (self.doc_tabs.currentWidget()).
        self.doc_tabs = QTabWidget()
        self.doc_tabs.setTabsClosable(True)
        self.doc_tabs.setMovable(True)
        self.doc_tabs.setDocumentMode(True)
        self.doc_tabs.tabCloseRequested.connect(self._close_tab)
        root.addWidget(self.doc_tabs, 1)
        self._new_tab()   # vždy aspoň jedna karta — `currentChanged` se
        # připojí AŽ na konci `_setup_ui()` (viz níže), ať tahle první
        # karta nespustí `_on_active_tab_changed` dřív, než existují
        # doky/toolbar, na které sahá.

        # ---- Levý DOCK: seznam stop ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)
        self.track_tree = QTreeWidget()
        register_tr(lambda _t: self.track_tree.setHeaderLabels(
            [tr("left.track_tree_name"), tr("left.track_tree_type")]), "left.track_tree_name")
        self.track_tree.setColumnWidth(0, 150)
        self.track_tree.itemClicked.connect(self._on_track_selected)
        left_layout.addWidget(self.track_tree)
        export_btn = QPushButton()
        tr_label(export_btn, "left.export_button")
        export_btn.setStyleSheet(
            "QPushButton { background: #2d7d2d; color: white; padding: 7px 12px; "
            "border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3a9e3a; }"
        )
        export_btn.clicked.connect(self.save_json)
        left_layout.addWidget(export_btn)

        # ---- Mix časové osy: VŽDY viditelný (na rozdíl od hlaviček uvnitř
        # scény, které při vodorovném rolování odjedou pryč — proto sem, do
        # levého docku), s hlasitostí/mute nahrávky+GP mixu bicích a
        # skrýt/zobrazit pro jednotlivé stopy. Viz TrackMixPanel. ----
        mix_label = QLabel()
        tr_label(mix_label, "left.mix_title")
        mix_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        left_layout.addWidget(mix_label)
        self.mix_scroll = QScrollArea()
        self.mix_scroll.setWidgetResizable(True)
        self.mix_scroll.setWidget(self.timeline.mix_panel)
        left_layout.addWidget(self.mix_scroll, 1)

        self.dock_tracks = QDockWidget(self)
        tr_dock_title(self.dock_tracks, "dock.tracks_title")
        self.dock_tracks.setWidget(left)
        self.dock_tracks.setMinimumWidth(200)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_tracks)

        # ---- Spodní DOCK: náhledy (výchozí SKRYTÝ, ať má osa celé okno) ----
        self.tabs = QTabWidget()
        self.dock_previews = QDockWidget(self)
        tr_dock_title(self.dock_previews, "dock.previews_title")
        self.dock_previews.setWidget(self.tabs)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_previews)
        self.dock_previews.hide()

        # Záložka 1: Chord Chart (text + akordy nad ním)
        chord_chart_widget = QWidget()
        cc_layout = QVBoxLayout(chord_chart_widget)
        cc_layout.setContentsMargins(0, 0, 0, 0)
        self.chord_chart_browser = QTextBrowser()
        self.chord_chart_browser.setFont(QFont("Courier New", 13))
        self.chord_chart_browser.setOpenLinks(False)
        cc_layout.addWidget(self.chord_chart_browser)
        self.tabs.addTab(chord_chart_widget, "")
        tr_tab(self.tabs, 0, "tab.chord_chart")

        # Záložka 2: Detail stopy
        self.track_detail = TrackDetailWidget()
        self.tabs.addTab(self.track_detail, "")
        tr_tab(self.tabs, 1, "tab.tab_notation")

        # Záložka 2: Přehled skladby
        overview_widget = QWidget()
        ov_layout = QVBoxLayout(overview_widget)
        self.overview_text = QTextEdit()
        self.overview_text.setReadOnly(True)
        self.overview_text.setFont(QFont("Consolas", 11))
        ov_layout.addWidget(self.overview_text)
        self.tabs.addTab(overview_widget, "")
        tr_tab(self.tabs, self.tabs.count() - 1, "tab.overview")

        # Záložka 3: Text / Slova
        lyrics_widget = QWidget()
        ly_layout = QVBoxLayout(lyrics_widget)
        self.lyrics_text = QTextEdit()
        self.lyrics_text.setReadOnly(True)
        self.lyrics_text.setFont(QFont("Segoe UI", 12))
        ly_layout.addWidget(self.lyrics_text)
        self.tabs.addTab(lyrics_widget, "")
        tr_tab(self.tabs, self.tabs.count() - 1, "tab.lyrics")

        # Záložka 4: Akordy
        chords_widget = QWidget()
        ch_layout = QVBoxLayout(chords_widget)
        self.chords_text = QTextEdit()
        self.chords_text.setReadOnly(True)
        self.chords_text.setFont(QFont("Consolas", 12))
        ch_layout.addWidget(self.chords_text)
        self.tabs.addTab(chords_widget, "")
        tr_tab(self.tabs, self.tabs.count() - 1, "tab.chords")

        # Záložka 5: JSON náhled
        json_widget = QWidget()
        jl = QVBoxLayout(json_widget)
        self.json_preview = QTextEdit()
        self.json_preview.setReadOnly(True)
        self.json_preview.setFont(QFont("Consolas", 10))
        jl.addWidget(self.json_preview)
        self.tabs.addTab(json_widget, "")
        tr_tab(self.tabs, self.tabs.count() - 1, "tab.json_preview")

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        register_tr(self.status.showMessage, "status.ready")

        self._build_menus()

        # Připojit AŽ TEĎ (viz komentář u self._new_tab() výše) — v tuhle
        # chvíli už existují doky/toolbar/snap-radio menu, na které
        # `_on_active_tab_changed` sahá.
        self.doc_tabs.currentChanged.connect(self._on_active_tab_changed)

        # Popisky karet nejsou obyčejný `register_tr(widget.setText, …)` —
        # skládají se z názvu písně + značky neuložených změn a jen kartu
        # BEZ názvu je potřeba přeložit. Registrujeme proto přeznačení
        # všech karet na klíč "tab.untitled": při přepnutí jazyka registr
        # zavolá tuhle lambdu a popisky se přepočítají.
        register_tr(lambda _text: self._refresh_all_tab_labels(), "tab.untitled")

        # Stejná past jako u popisků karet výše — sloupec "Typ" levého
        # panelu se jinak přepočítá jen při dalším importu/načtení, ne při
        # pouhém přepnutí jazyka (viz `_retranslate_track_tree`).
        register_tr(lambda _text: self._retranslate_track_tree(), "track.type_drums")

    def _build_menus(self) -> None:
        """Kompletní menu bar + toolbary — sjednocuje dřívější File menu
        (guitar_pro_viewer.py) s tím, co dřív bylo natvrdo napsané tlačítka
        v horním panelu timeline_editor.py (viz plán "Přestavba GUI").
        Volá se z `_setup_ui()` AŽ POTÉ, co existují `self.timeline` a oba
        dock widgety — spousta akcí volá přímo `self.timeline.<metoda>`."""
        menubar = self.menuBar()

        # --- Soubor / File ---
        file_menu = menubar.addMenu("")
        register_tr(file_menu.setTitle, "menu.file")
        act_new = tr_action(self, "file.new", tooltip_key="file.new.tooltip",
                             slot=self._new_blank_file,
                             icon=std_icon(self, QStyle.StandardPixmap.SP_FileIcon))
        # Projekt vzniká JEN průvodcem (rozhodnutí uživatele 2026-11-01): Soubor ▸ Nový projekt…
        # = průvodce; prázdný soubor, import GP/MIDI a nová píseň z webu v menu nejsou
        # (metody zůstávají — testy). Hotový projekt se mění jen přes menu Nahradit.
        act_web = tr_action(self, "file.new_from_web", tooltip_key="file.new_from_web.tooltip",
                             shortcut="Ctrl+Shift+N", slot=self._open_web_import, icon=icon("new_song"))
        # „Nová píseň z webu" a „Sloučit s webem" v menu nejsou (2026-11-01): umí je průvodce
        # a Časová osa ▸ Přegenerovat ▸ Text a akordy; metody zůstávají (testy, Ctrl+…)
        act_wizard = tr_action(self, "file.recording_wizard", tooltip_key="file.recording_wizard.tooltip",
                               shortcut="Ctrl+N", slot=self._open_recording_wizard, icon=icon("new_song"))
        file_menu.addAction(act_wizard)
        act_merge = tr_action(self, "file.merge_web", tooltip_key="file.merge_web.tooltip",
                               shortcut="Ctrl+M", slot=self.merge_with_web, icon=icon("merge"))
        file_menu.addSeparator()
        # "Otevřít"/"Uložit"/"Uložit jako" se týkají VÝHRADNĚ JSONu (vlastní
        # formát appky) — Guitar Pro se do appky jen IMPORTUJE (tabulatura/
        # bicí/basa), appka do něj nikdy nezapisuje zpátky, takže nepatří
        # mezi rovnocenné "Otevřít"/"Uložit" akce (dřív matlo uživatele —
        # viz oprava "New/Open/Save/Save As").
        act_open_json = tr_action(self, "file.open", tooltip_key="file.open.tooltip",
                                   shortcut="Ctrl+O", slot=self.open_json,
                                   icon=std_icon(self, QStyle.StandardPixmap.SP_DialogOpenButton))
        file_menu.addAction(act_open_json)
        act_import_gp = tr_action(self, "file.import_gp", tooltip_key="file.import_gp.tooltip",
                                   shortcut="Ctrl+I", slot=self.open_file,
                                   icon=icon("import_gp"))
        act_import_midi = tr_action(self, "file.import_midi", tooltip_key="file.import_midi.tooltip",
                                     shortcut="Ctrl+Shift+I", slot=self.open_midi,
                                     icon=icon("import_midi"))
        file_menu.addSeparator()
        act_save = tr_action(self, "file.save", tooltip_key="file.save.tooltip",
                              shortcut="Ctrl+S", slot=self.save_json,
                              icon=std_icon(self, QStyle.StandardPixmap.SP_DialogSaveButton))
        file_menu.addAction(act_save)
        act_save_as = tr_action(self, "file.save_as", tooltip_key="file.save_as.tooltip",
                                 shortcut="Ctrl+Shift+S", slot=self.save_json_as,
                                 icon=icon("save_as"))
        file_menu.addAction(act_save_as)
        act_export_json = tr_action(self, "file.export_esp32", tooltip_key="file.export_esp32.tooltip",
                                    slot=self.export_esp32_json)
        file_menu.addAction(act_export_json)
        file_menu.addSeparator()
        act_close_tab = tr_action(self, "file.close", tooltip_key="file.close.tooltip",
                                   shortcut="Ctrl+W", slot=self._close_current_tab,
                                   icon=std_icon(self, QStyle.StandardPixmap.SP_DialogCloseButton))
        file_menu.addAction(act_close_tab)
        file_menu.addSeparator()
        act_quit = tr_action(self, "file.quit", shortcut="Ctrl+Q", slot=self.close,
                              icon=std_icon(self, QStyle.StandardPixmap.SP_TitleBarCloseButton))
        file_menu.addAction(act_quit)

        # --- Úpravy / Edit ---
        edit_menu = menubar.addMenu("")
        register_tr(edit_menu.setTitle, "menu.edit")
        # POZOR: `slot=lambda: self.timeline.X()` — NE `slot=self.timeline.X`
        # (svázaná metoda by se vyhodnotila JEDNOU, při stavbě menu, a
        # navždy zůstala navázaná na PRVNÍ kartu; lambda odkládá vyhledání
        # `self.timeline` — property vracející AKTIVNÍ kartu — až na
        # okamžik kliknutí, viz plán "Multi-dokument").
        act_undo = tr_action(self, "edit.undo", tooltip_key="edit.undo.tooltip",
                              shortcut="Ctrl+Z", slot=lambda: self.timeline.undo(), icon=icon("undo"))
        edit_menu.addAction(act_undo)
        act_redo = tr_action(self, "edit.redo", tooltip_key="edit.redo.tooltip",
                              shortcuts=["Ctrl+Shift+Z", "Ctrl+Y"], slot=lambda: self.timeline.redo(),
                              icon=icon("redo"))
        edit_menu.addAction(act_redo)
        edit_menu.addSeparator()
        act_select_all = tr_action(self, "edit.select_all", tooltip_key="edit.select_all.tooltip",
                                    shortcut="Ctrl+A", slot=lambda: self.timeline.select_all(),
                                    icon=icon("select_all"))
        edit_menu.addAction(act_select_all)
        act_copy = tr_action(self, "edit.copy", shortcut="Ctrl+C", slot=lambda: self.timeline.copy_selected(),
                              icon=icon("copy"))
        edit_menu.addAction(act_copy)
        act_cut = tr_action(self, "edit.cut", shortcut="Ctrl+X", slot=lambda: self.timeline.cut_selected(),
                             icon=icon("cut"))
        edit_menu.addAction(act_cut)
        act_paste = tr_action(self, "edit.paste", tooltip_key="edit.paste.tooltip",
                               shortcut="Ctrl+V",
                               slot=lambda: self.timeline.paste_at(self.timeline.playhead_s),
                               icon=icon("paste"))
        edit_menu.addAction(act_paste)
        act_paste_at = tr_action(self, "edit.paste_at", tooltip_key="edit.paste_at.tooltip",
                                  shortcut="Ctrl+Shift+V",
                                  slot=lambda: self.timeline.paste_at_dialog(),
                                  icon=icon("paste"))
        edit_menu.addAction(act_paste_at)
        edit_menu.addSeparator()
        act_delete = tr_action(self, "edit.delete", shortcut="Del",
                                slot=lambda: self.timeline.delete_selected(),
                                icon=std_icon(self, QStyle.StandardPixmap.SP_TrashIcon))
        edit_menu.addAction(act_delete)
        act_shift_sel = tr_action(self, "edit.shift_selected", tooltip_key="edit.shift_selected.tooltip",
                                   slot=lambda: self.timeline.shift_selected_dialog(), icon=icon("shift_selected"))
        edit_menu.addAction(act_shift_sel)
        edit_menu.addSeparator()
        act_rewrite_text = tr_action(self, "edit.rewrite_text", tooltip_key="edit.rewrite_text.tooltip",
                                      shortcut="Ctrl+Shift+T", slot=self._edit_text_and_chords,
                                      icon=icon("edit_text_chords"))
        edit_menu.addAction(act_rewrite_text)

        # --- Časová osa / Timeline ---
        timeline_menu = menubar.addMenu("")
        register_tr(timeline_menu.setTitle, "menu.timeline")
        act_add_lyric = tr_action(self, "timeline.add_lyric",
                                   slot=lambda: self.timeline.add_block("lyric"),
                                   icon=icon("add_lyric"))
        timeline_menu.addAction(act_add_lyric)
        act_add_chord = tr_action(self, "timeline.add_chord",
                                   slot=lambda: self.timeline.add_block("chord"),
                                   icon=icon("add_chord"))
        timeline_menu.addAction(act_add_chord)
        act_add_tab = tr_action(self, "timeline.add_tab", tooltip_key="timeline.add_tab.tooltip",
                                 slot=lambda: self.timeline.add_block("tab"),
                                 icon=icon("add_lyric"))
        timeline_menu.addAction(act_add_tab)
        timeline_menu.addSeparator()
        act_autotime = tr_action(self, "timeline.autotime", tooltip_key="timeline.autotime.tooltip",
                                  slot=lambda: self.timeline.auto_time_dialog(), icon=icon("ruler"))
        timeline_menu.addAction(act_autotime)
        act_tempo = tr_action(self, "timeline.tempo", tooltip_key="timeline.tempo.tooltip",
                               slot=lambda: self.timeline.bpm_dialog(), icon=icon("tempo"))
        timeline_menu.addAction(act_tempo)
        act_count_in = tr_action(self, "timeline.count_in", tooltip_key="timeline.count_in.tooltip",
                                  slot=lambda: self.timeline.count_in_dialog(), icon=icon("count_in"))
        timeline_menu.addAction(act_count_in)
        act_repeats = tr_action(self, "timeline.repeats", tooltip_key="timeline.repeats.tooltip",
                                 slot=self.edit_repeats)
        timeline_menu.addAction(act_repeats)
        act_fit_tempo = tr_action(self, "timeline.fit_tempo", tooltip_key="timeline.fit_tempo.tooltip",
                                   slot=lambda: self.timeline.fit_tempo_to_audio())
        act_transcribe = tr_action(self, "timeline.transcribe_drums",
                                   tooltip_key="timeline.transcribe_drums.tooltip",
                                   slot=lambda: self.timeline.transcribe_drums_from_audio())
        act_bass_tr = tr_action(self, "timeline.bass_transpose", tooltip_key="timeline.bass_transpose.tooltip",
                                slot=lambda: self.timeline.bass_transpose_dialog())
        act_align_text = tr_action(self, "timeline.align_text", tooltip_key="timeline.align_text.tooltip",
                                   slot=lambda: self.timeline.align_text_to_vocals())
        # Nahradit — jeden krok průvodce v AKTUÁLNÍM projektu; časování projektu
        # zůstává, nahrazované se mu přizpůsobí (takty se proto nenahrazují).
        # Dílčí funkce (přepis bicích, transpozice basy, zarovnání textu) jsou
        # tlačítka v příslušném kroku.
        regen_menu = menubar.addMenu("")
        register_tr(regen_menu.setTitle, "menu.regen")
        for kind in ("drums", "bass", "text", "tab"):
            act = tr_action(self, f"regen.{kind}", slot=lambda _c=False, k=kind: self._open_regen(k))
            regen_menu.addAction(act)

        # Přichytit k mřížce ▸ — radio podmenu, obousměrně zrcadlí
        # snap_combo AKTIVNÍ karty. Combobox samotný je PER KARTA (viz
        # timeline_editor.py _setup_ui) — při přepnutí karty se signál
        # přepojí na combobox NOVÉ karty, viz `_swap_toolbar_widgets`.
        snap_menu = timeline_menu.addMenu(icon("snap_grid"), "")
        register_tr(snap_menu.setTitle, "timeline.snap_submenu")
        snap_group = QActionGroup(self)
        snap_group.setExclusive(True)
        snap_actions = []
        for key in ("timeline.snap_off", "timeline.snap_q_beat", "timeline.snap_h_beat",
                    "timeline.snap_beat", "timeline.snap_bar"):
            act = tr_action(self, key, checkable=True)
            act.setActionGroup(snap_group)
            snap_menu.addAction(act)
            snap_actions.append(act)
        snap_actions[self.timeline.snap_combo.currentIndex()].setChecked(True)
        for i, act in enumerate(snap_actions):
            act.triggered.connect(lambda checked, idx=i: self.timeline.snap_combo.setCurrentIndex(idx))

        self._snap_actions = snap_actions

        def _sync_snap_radio(idx: int) -> None:
            if 0 <= idx < len(snap_actions):
                snap_actions[idx].setChecked(True)
        self._sync_snap_radio = _sync_snap_radio
        self.timeline.snap_combo.currentIndexChanged.connect(self._sync_snap_radio)
        self._connected_snap_combo = self.timeline.snap_combo

        # --- Zobrazit / View ---
        view_menu = menubar.addMenu("")
        register_tr(view_menu.setTitle, "menu.view")
        act_tracks = self.dock_tracks.toggleViewAction()
        register_tr(act_tracks.setText, "view.tracks_panel")
        act_previews = self.dock_previews.toggleViewAction()
        register_tr(act_previews.setText, "view.previews_panel")
        # v toolbaru jen ikona (dlouhý text „Panel náhledů (Chord Chart, JSON…)"
        # mezi ikonami rozbíjel lištu), text zůstává v menu a tooltipu
        act_previews.setIcon(icon("previews"))
        register_tr(act_previews.setToolTip, "view.previews_panel")
        view_menu.addAction(act_tracks)
        view_menu.addAction(act_previews)
        view_menu.addSeparator()
        act_zoom_in = tr_action(self, "view.zoom_in", shortcut="Ctrl+=",
                                 slot=lambda: self.timeline.zoom(1.25), icon=icon("zoom_in"))
        view_menu.addAction(act_zoom_in)
        act_zoom_out = tr_action(self, "view.zoom_out", shortcut="Ctrl+-",
                                  slot=lambda: self.timeline.zoom(1 / 1.25), icon=icon("zoom_out"))
        view_menu.addAction(act_zoom_out)
        view_menu.addSeparator()

        # Nabídka jazyků se staví z TOHO, CO SE NAŠLO v adresáři `lang/`
        # (viz i18n.available_languages) — přidání jazyka je tedy jen
        # přidání souboru `lang/<kód>.json`, BEZ zásahu do kódu. Názvy
        # jazyků se schválně NEpřekládají (každý je psaný sám v sobě —
        # "Deutsch", "Español"…), aby je našel i ten, kdo appku omylem
        # přepnul do jazyka, kterému nerozumí.
        lang_menu = view_menu.addMenu("")
        register_tr(lang_menu.setTitle, "view.language_submenu")
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        for code, name in available_languages():
            act = QAction(name, self)
            act.setCheckable(True)
            act.setChecked(code == get_language())
            act.setActionGroup(lang_group)
            act.triggered.connect(lambda _checked=False, c=code: set_language(c))
            lang_menu.addAction(act)

        # --- Nápověda / Help ---
        help_menu = menubar.addMenu("")
        register_tr(help_menu.setTitle, "menu.help")
        act_help = tr_action(self, "help.timeline_help", slot=lambda: HelpDialog(self).exec(),
            icon=std_icon(self, QStyle.StandardPixmap.SP_MessageBoxInformation))
        help_menu.addAction(act_help)
        act_about = tr_action(self, "help.about", slot=lambda: QMessageBox.information(
            self, tr("help.about"), tr("help.about.body")),
            icon=std_icon(self, QStyle.StandardPixmap.SP_DialogHelpButton))
        help_menu.addAction(act_about)

        # --- Toolbar "Hlavní panel" — zjednodušený (rozhodnutí uživatele
        # 2026-11-01, návrh A): hlavní cesta je průvodce „Karaoke z nahrávky";
        # nová píseň z webu, Ctrl+M, import GP/MIDI a Uložit jako jsou dál
        # v menu Soubor a na zkratkách. ---
        tb = QToolBar()
        register_tr(tb.setWindowTitle, "toolbar.main")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(act_wizard)
        wiz_btn = tb.widgetForAction(act_wizard)
        if wiz_btn is not None:
            wiz_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addAction(act_open_json)
        tb.addAction(act_save)
        tb.addSeparator()
        tb.addAction(act_previews)

        # --- Toolbar "Časová osa" — nahrazuje dřívější ad hoc horní panel
        # tlačítek uvnitř timeline_editor.py; stejné QAction objekty jako
        # menu Úpravy/Časová osa výše (jedna akce = jeden objekt, viz plán).
        tb_timeline = QToolBar()
        register_tr(tb_timeline.setWindowTitle, "toolbar.timeline")
        tb_timeline.setMovable(False)
        self.addToolBar(tb_timeline)
        self.tb_timeline = tb_timeline
        tb_timeline.addWidget(QLabel(" " + tr("timeline.zoom_label")))
        tb_timeline.addAction(act_zoom_out)
        tb_timeline.addAction(act_zoom_in)
        # snap_combo/time_lbl jsou PER KARTA (viz timeline_editor.py) —
        # vložené widgety hlavního okna jen "vypůjčí"; při přepnutí karty
        # se vymění za widgety nové aktivní karty, viz
        # `_swap_toolbar_widgets`/`_on_active_tab_changed`. `_tb_timeline_anchor`
        # (tenhle oddělovač) drží jejich pozici v toolbaru napříč výměnami.
        self._snap_combo_act = tb_timeline.addWidget(self.timeline.snap_combo)
        self._time_lbl_act = tb_timeline.addWidget(self.timeline.time_lbl)
        self._tb_timeline_anchor = tb_timeline.addSeparator()
        tb_timeline.addAction(act_undo)
        tb_timeline.addAction(act_redo)
        tb_timeline.addSeparator()
        # kopírovat/vložit, tempo, odpočet, na mřížku, sólo, mazání, posun
        # a přepis textu jsou v menu Úpravy/Časová osa a na zkratkách
        tb_timeline.addAction(act_add_lyric)
        tb_timeline.addAction(act_add_chord)

    def _swap_toolbar_widgets(self, active: TimelineEditor) -> None:
        """Vymění snap_combo/time_lbl v toolbaru „Časová osa“ za widgety
        `active` karty (QWidget může mít jen jednoho rodiče, jde jen
        vyndat/vložit, ne sdílet) a přepojí radio-menu „Přichytit k
        mřížce“ na její combobox — volá `_on_active_tab_changed`."""
        self.tb_timeline.removeAction(self._snap_combo_act)
        self.tb_timeline.removeAction(self._time_lbl_act)
        self._snap_combo_act = self.tb_timeline.insertWidget(self._tb_timeline_anchor, active.snap_combo)
        self._time_lbl_act = self.tb_timeline.insertWidget(self._tb_timeline_anchor, active.time_lbl)
        try:
            self._connected_snap_combo.currentIndexChanged.disconnect(self._sync_snap_radio)
        except (RuntimeError, TypeError):
            pass   # předchozí combobox už mohl zaniknout (zavřená karta)
        active.snap_combo.currentIndexChanged.connect(self._sync_snap_radio)
        self._connected_snap_combo = active.snap_combo
        self._sync_snap_radio(active.snap_combo.currentIndex())

    # ------------------------------------------------------------------
    # Načítání souboru
    # ------------------------------------------------------------------

    def _new_blank_file(self) -> None:
        """'Nový soubor' — prázdná karta bez jakéhokoli dialogu, na rozdíl
        od 'Nová píseň — z webu' (`_open_web_import`), která rovnou otevírá
        dialog pro import textu. Obsah se pak přidá Otevřením JSONu,
        Importem z GP nebo Novou píseň z webu."""
        self._tab_for_new_document()

    def _close_current_tab(self) -> None:
        """'Zavřít soubor' — zavře aktuálně aktivní kartu (nabídne Uložit/
        Zahodit/Zrušit, pokud má neuložené změny; viz `_close_tab`).
        Stejné jako kliknutí na ✕ na kartě, jen dostupné i z menu/klávesou."""
        self._close_tab(self.doc_tabs.currentIndex())

    def _open_regen(self, kind: str) -> None:
        """Časová osa ▸ Přegenerovat ▸ … — jen jeden krok v aktuální kartě."""
        from recording_wizard import RecordingWizard
        self._recording_wizard = RecordingWizard(self, only=kind)
        self._recording_wizard.show()

    def _open_recording_wizard(self) -> None:
        """Soubor ▸ Karaoke z nahrávky… — průvodce bez GP (`recording_wizard`)."""
        from recording_wizard import RecordingWizard
        self._recording_wizard = RecordingWizard(self)
        self._recording_wizard.show()

    def _open_web_import(self) -> None:
        """Kompletní postup pro novou píseň: web/vlastní text → úprava v dialogu
        → OK ('Použít v editoru') → rovnou na časovou osu (aktuální tempo, řádek
        = jeden časový úsek). Uložení GP4+JSON na disk zůstává volitelné, uvnitř
        dialogu, dialog se tím nezavírá."""
        dlg = WebImportDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.result_json:
            self._tab_for_new_document()   # nová píseň — vlastní (prázdná) karta
            self._present_karaoke_data(dlg.result_json, source_desc=tr("web.new_song_desc"))

    def _edit_text_and_chords(self) -> None:
        """NE 'Nová píseň' — tohle je krok zpět jen pro text/akordy AKTUÁLNĚ
        načtené písně: znovu se otevře stejný dialog, předvyplněný
        rekonstruovaným textem z `self.timeline.export_chord_chart_text()`,
        oprava se pak vrátí přes `replace_text_and_chords()`, která nahradí
        JEN lyrics_timeline/chords_timeline/karaoke_lines — bicí, basa a
        napojení na audio zůstanou beze změny (na výslovné přání uživatele:
        'bicí, basa zůstane')."""
        if not self.timeline.data or not self.timeline.data.get("lyrics_timeline"):
            QMessageBox.warning(self, tr("edit_text.none_title"), tr("edit_text.none_body"))
            return
        initial_text = self.timeline.export_chord_chart_text()
        dlg = WebImportDialog(self, edit_mode=True, initial_text=initial_text,
                               initial_meta=dict(self.timeline.data.get("meta", {})))
        if dlg.exec() != QDialog.Accepted or not dlg.result_json:
            return
        old_tracks = self.timeline.replace_text_and_chords(dlg.result_json)
        if len(old_tracks) > 1:
            QMessageBox.warning(self, tr("edit_text.multi_track_title"), tr("edit_text.multi_track_body"))

    def _tab_for_new_document(self) -> TimelineEditor:
        """Vrátí kartu pro novou/importovanou/otevřenou píseň — POUŽIJE
        aktuální kartu, pokud je úplně prázdná a neuložená (typicky
        jediná výchozí karta hned po startu, nebo hned po „Zavřít kartu“),
        jinak otevře NOVOU (aby se nikdy nepřepsala rozdělaná práce)."""
        cur = self.timeline
        if cur is not None and cur.song is None and not cur.data and not cur._dirty:
            return cur
        return self._new_tab()

    def open_file(self):
        """Importovat z Guitar Pro — VŽDY do AKTIVNÍ karty, nikdy do nové
        (na rozdíl od Nová píseň/Otevřít — viz `_tab_for_new_document`).
        Zachovává existující chování: GP importovaný AŽ PO existujících
        karaoke datech (web/JSON) na tuhle kartu se do nich jen PŘIDÁ
        (bicí/basa, `_merge_gp_tracks_into_current`), GP importovaný do
        prázdné/jen-GP karty ji (pře)naplní od nuly — viz `_on_loaded`."""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("file.import_gp.dialog_title"), "",
            tr("file.import_gp.dialog_filter")
        )
        if path:
            self._load_file(path, self.timeline)

    def _load_file(self, path: str, target: TimelineEditor) -> None:
        """Načte GP soubor NA POZADÍ do `target` — karta zachycená UZÁVĚREM,
        NE „aktuální karta v okamžiku doručení signálu“ (to by se s kartami
        mohlo rozejít, kdyby uživatel mezitím přepnul — oprava dřívějšího
        bugu, kdy `_on_loaded` četl cestu zpátky z `self._worker`, což by
        s více souběžnými načítáními/kartami mohlo přiřadit výsledek
        špatné kartě). `self._workers` drží VŠECHNY běžící načítání
        najednou (dřív jen jedno, `self._worker`, přepisované)."""
        self.status.showMessage(tr("status.loading", path=path))
        worker = LoadWorker(path)
        self._workers.append(worker)

        def _done(song, w=worker) -> None:
            if w in self._workers:
                self._workers.remove(w)
            self._on_loaded(song, path, target)

        def _err(msg: str, w=worker) -> None:
            if w in self._workers:
                self._workers.remove(w)
            self._on_load_error(msg)

        worker.done.connect(_done)
        worker.error.connect(_err)
        worker.start()

    # ------------------------------------------------------------------
    # Načtení karaoke JSON přímo do editoru (např. z web importu)
    # ------------------------------------------------------------------

    def open_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("file.open.dialog_title"), "",
            tr("file.open.dialog_filter")
        )
        if path:
            self._tab_for_new_document()
            self._load_karaoke_json(path)

    def _load_karaoke_json(self, path: str):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            QMessageBox.critical(self, tr("common.error_title"), tr("json.load_error_body", ex=ex))
            self.status.showMessage(tr("status.json_load_error"))
            return

        # Projekt (.kproj): cesty k nahrávce/GP jsou relativní ke složce
        # projektu — složka písně jde přesunout celá
        project = data.pop("project", None) if isinstance(data, dict) else None
        if project:
            base = Path(path).parent
            audio = project.get("audio")
            if audio:
                data.setdefault("meta", {})["audioFile"] = str((base / audio).resolve())
        # JSON nemá GP song (tabulatury/detaily), pracujeme jen s karaoke daty
        self.current_gp_file = None
        self.current_json_file = path
        self._present_karaoke_data(data, source_desc=tr("source.json", path=path))
        if project:
            gp = project.get("gp")
            self.timeline.project_info = {
                "gp": str((Path(path).parent / gp).resolve()) if gp else None,
                "source_url": project.get("source_url")}

    def open_midi(self) -> None:
        """Import ze standardního .mid/.midi souboru (`midi_import.py`) —
        VŽDY do NOVÉ karty (na rozdíl od GP importu, který doplňuje
        aktuální — MIDI nemá GP `Song` objekt, se kterým by šlo cokoliv
        sloučit, takže sloučení do rozpracované karty nedává smysl).
        Typický zdroj: placený export ze Songsterr Plus (MIDI/Guitar Pro/
        MP3/WAV)."""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("file.import_midi.dialog_title"), "",
            tr("file.import_midi.dialog_filter")
        )
        if not path:
            return
        import midi_import as MI
        try:
            data, warnings = MI.build_karaoke_json_from_midi(path)
        except Exception as ex:
            QMessageBox.critical(self, tr("common.error_title"),
                                 tr("midi_import.load_error_body", ex=ex))
            self.status.showMessage(tr("status.load_error"))
            return
        self._tab_for_new_document()
        self.current_gp_file = None
        self.current_json_file = None
        self._present_karaoke_data(data, source_desc=tr("source.midi", path=path))
        if warnings:
            QMessageBox.warning(self, tr("midi_import.warnings_title"), "\n\n".join(warnings))

    def _render_docks_from_json(self) -> None:
        """Vykreslí sdílené doky (Stopy/Náhledy) z `self.timeline.data`
        (AKTIVNÍ karty, JSON cesta — bez GP song). Bezpečné volat
        OPAKOVANĚ (i jen při přepnutí karty) — nic nemění, jen čte."""
        data = self.timeline.data
        meta = data.get("meta", {}) or {}
        display_path = self.current_json_file or self.current_gp_file
        self.lbl_title.setText(meta.get("title") or (display_path and Path(display_path).stem) or "")
        self.lbl_artist.setText(meta.get("artist") or "")
        bass_n = len(data.get("bass_timeline", []))
        meta_line = tr("present.meta_line", tempo=meta.get('tempo_bpm', '?'),
                       n_tracks=len(data.get("tracks", [])),
                       n_lines=len(data.get("karaoke_lines", [])),
                       n_drums=len(data.get("drums_timeline", [])))
        if bass_n:
            meta_line += tr("present.meta_bass_suffix", n_bass=bass_n)
        self.lbl_meta.setText(meta_line)

        self.track_tree.clear()
        for t in data.get("tracks", []):
            kind = t.get("type", "")
            item = QTreeWidgetItem([f"{t.get('index', '?')}. {t.get('name', '')}",
                                    _track_type_label(kind)])
            item.setData(1, Qt.UserRole, kind)
            self.track_tree.addTopLevelItem(item)

        note = tr("present.no_gp_note")
        for w in (self.overview_text, self.lyrics_text, self.chords_text):
            w.setPlainText(note)
        self.chord_chart_browser.setHtml(f"<p>{note}</p>")
        self.json_preview.setText(json.dumps(data, indent=2, ensure_ascii=False))

    def _clear_docks_to_empty(self) -> None:
        """Prázdná/čerstvá karta (žádný song, žádná data) — doky do
        stejného stavu jako hned po startu appky."""
        self.lbl_title.setText("—")
        self.lbl_artist.setText("")
        self.lbl_meta.setText("")
        self.track_tree.clear()
        note = tr("present.no_gp_note")
        for w in (self.overview_text, self.lyrics_text, self.chords_text):
            w.setPlainText(note)
        self.chord_chart_browser.setHtml(f"<p>{note}</p>")
        self.json_preview.setText("")

    def _present_karaoke_data(self, data: dict, source_desc: str = "") -> None:
        """Naplní AKTIVNÍ kartu karaoke daty (z JSON souboru nebo ze
        sloučení s webem) — JEDNORÁZOVĚ (`self.timeline.load_data`, ne
        volat opakovaně, přepsalo by živé úpravy) — a vykreslí doky. GP
        song se dál nepoužívá."""
        self.song = None
        self.tempo_map = []
        self._loaded_json = data
        self.timeline.load_data(data)
        self._render_docks_from_json()
        n_lines = len(self.timeline.data.get("karaoke_lines", []))
        self.status.showMessage(tr("status.loaded_lines", source=source_desc, n_lines=n_lines))

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
                self, tr("merge.need_gp_title"), tr("merge.need_gp_body"))
            return
        # Text se bere ze STEJNÉHO dialogu jako u nové písně — umí vložený
        # vlastní text i stažení z URL, takže sloučení s GP není napevno
        # svázané s webem (dřív šlo jen zadat URL a nic jiného).
        # `edit_mode` tu neznamená „opravuju text": jen zamkne tempo a úvodní
        # takty, což je přesně správně — v tomhle postupu je pánem časování
        # Guitar Pro, ne mřížka z textu.
        dlg = WebImportDialog(self, edit_mode=True, initial_meta={
            "title": self.song.title, "artist": self.song.artist,
            "tempo_bpm": self.song.tempo})
        dlg.ok_btn.setText(tr("web_import.ok_btn_new"))
        dlg.ok_btn.setToolTip(tr("web_import.ok_btn_new.tooltip"))
        if dlg.exec() != QDialog.Accepted or not dlg.result_json:
            return
        url = dlg.url_input.text().strip()
        self.status.showMessage(tr("status.merging"))
        try:
            # Text z dialogu (s jeho odpočtem a mřížkou) → reálná nula,
            # pak stejné napasování na GP jako při otevření GP po textu.
            from format_migrate import migrate, to_editor_model
            web, merge_notes = self._gp_timing_onto_text(
                to_editor_model(migrate(dlg.result_json)[0]))
            web.setdefault("meta", {})
            web["meta"]["source_file"] = Path(self.current_gp_file).name if self.current_gp_file else ""
            web["meta"]["merged_web_gp"] = True
        except Exception as ex:
            QMessageBox.critical(self, tr("merge.error_title"), str(ex))
            self.status.showMessage(tr("status.merge_error"))
            return

        self.current_json_file = None   # nová kombinace GP+web — zatím nikam neuložená
        # Text nemusel přijít z webu (šlo ho i vložit), tak popisek zdroje
        # nesmí hlásit prázdnou URL — pak je zdrojem prostě ten GP soubor.
        origin = url or (Path(self.current_gp_file).name if self.current_gp_file else "GP")
        song, gp_file, raw_tm = self.song, self.current_gp_file, self.tempo_map
        self._present_karaoke_data(web, source_desc=tr("source.gp_web", url=origin))
        # GP zůstává kartě k dispozici (další Ctrl+M, náhledy)
        self.song, self.current_gp_file, self.tempo_map = song, gp_file, raw_tm
        # Jak se text načasoval NENÍ vedlejší detail — rozhoduje o tom, jestli
        # sedí na nahrávku. Musí to být vidět, ne jen v konzoli.
        QMessageBox.information(
            self, tr("merge.done_title"),
            tr("merge.done_body", n_lines=len(web.get('karaoke_lines', [])),
               n_chords=len(web.get('chords_timeline', [])),
               n_drums=len(web.get('drums_timeline', [])),
               n_bass=len(web.get('bass_timeline', [])))
            + "\n\n" + "\n".join("• " + n for n in merge_notes))

    def _on_loaded(self, song, path: str, target: TimelineEditor) -> None:
        """Dokončení `_load_file()` — `target` je karta zachycená UZÁVĚREM
        při spuštění, NE `self.timeline` (aktivní karta TEĎ může být jiná,
        pokud uživatel mezitím přepnul). Když `target` právě NENÍ aktivní,
        vlastní data se do ní přesto zapíšou hned (přímo, ne přes property),
        ale dokreslení sdílených doků se odloží na okamžik, kdy se na ni
        skutečně přepne (`_pending_gp_populate`, viz `_on_active_tab_changed`)
        — ať se neroztrhne obsah docku od právě zobrazené JINÉ karty."""
        if target._loaded_json is not None:
            # Text/akordy už jsou na týhle kartě načtené (web/JSON/merge) —
            # tenhle GP soubor jen PŘIDÁ své stopy (bicí), text/akordy
            # zůstávají beze změny.
            self._merge_gp_tracks_into_current(song, path, target)
            return
        target.song = song
        target.current_gp_file = path
        target.current_json_file = None   # GP je jen import — zatím žádný JSON k přepsání
        target.tempo_map = build_tempo_map(song)
        if target is self.timeline:
            self._finish_gp_import(target)
            self.status.showMessage(tr("status.loaded_file", path=target.current_gp_file))
        else:
            target._pending_gp_populate = True

    def _merge_gp_tracks_into_current(self, song, path: str, target: TimelineEditor) -> None:
        """GP otevřený AŽ PO textu (web/vlastní/JSON) na téhle kartě. GP je
        pán časování (stejně jako Ctrl+M `merge_with_web`): tempo, metrum
        a repetice z GP, řádky textu se napasují na GP (zpěvní stopa, jinak
        taktová struktura), přidají se bicí a basa. Obsah textu a akordů
        (co je napsané) zůstává, mění se jen KDY.

        Dřív se jen přilepily bicí v sekundách z GP k textu na mřížce
        z dialogu (výchozích 120 BPM): tempo nesedělo (58 % úderů mimo
        mřížku), text ležel na umělých `bars_per_line` taktech a odpočet
        z dialogu při uložení zahodil první údery bicích."""
        target.song = song
        target.tempo_map = build_tempo_map(song)
        if target is not self.timeline:
            self.doc_tabs.setCurrentWidget(target)
        # Aktuální stav editoru (i s ručními úpravami), převedený na reálnou
        # nulu bez odpočtu — přes stejný převod jako uložení/otevření souboru.
        from format_migrate import to_editor_model
        flat = to_editor_model(target.to_json())
        data, notes = self._gp_timing_onto_text(flat)
        meta = data.setdefault("meta", {})
        meta["source_file"] = Path(path).name
        meta["merged_web_gp"] = True

        gp_name = Path(path).name
        keep_json = target.current_json_file
        self._present_karaoke_data(data, source_desc=tr("source.web_gp_drums_bass", gp_name=gp_name))
        # _present_karaoke_data nuluje GP song — tady ho karta potřebuje dál
        # (Ctrl+M, náhledy), a cíl „Uložit" se otevřením GP nemění.
        target.song = song
        target.tempo_map = build_tempo_map(song)
        target.current_gp_file = path
        target.current_json_file = keep_json
        # sloučení = neuložená změna (load_data dirty vynuloval)
        target._dirty = True
        target.dirty_changed.emit(True)
        added = []
        if data.get("drums_timeline"):
            added.append(tr("merge.added_hits", n=len(data["drums_timeline"])))
        if data.get("bass_timeline"):
            added.append(tr("merge.added_bass", n=len(data["bass_timeline"])))
        if added:
            QMessageBox.information(
                self, tr("merge.added_title"),
                tr("merge.added_body", gp_name=gp_name, added=", ".join(added))
                + "\n\n" + "\n".join("• " + n for n in notes))
        else:
            QMessageBox.information(
                self, tr("merge.no_drums_bass_title"),
                tr("merge.no_drums_bass_body", gp_name=gp_name))

    def _gp_timing_onto_text(self, flat: dict) -> tuple[dict, list[str]]:
        """Společné jádro obou cest „text + GP" (Ctrl+M i otevření GP po
        textu). `flat` = plochý sekundový tvar editoru s časy od reálné nuly
        (bez odpočtu) — `to_editor_model(...)`. Vrací (data, poznámky).

        1. Mřížka z GP: tempo (i změny, v tickách přehrávání), metrum,
           repetice.
        2. Řádky na GP: zpěvní stopa má text → slova se spárují
           (`retime_web_to_gp`); jinak rozložení na takty podle značek sekcí
           (`place_lines_on_structure`). Akordy mimo řádky (intro, mezihry)
           se posunou stejně jako nejbližší předchozí řádek.
        3. Bicí a basa z GP (nahradí, co v textu bylo — např. klikací stopu)."""
        import web_import as W
        from music_grid import markers_from_measures
        song = self.song
        ms = song.tracks[0].measures
        plan, _lin_warn = expand_measure_plan(ms)
        order = [src for src, _p in plan]
        meter_map = meter_map_from_measures(ms, order)
        play_tm = [{"tick": t, "bpm": b} for t, b in play_tempo_map(ms, order, self.tempo_map)]
        grid = TimeGrid(play_tm, meter_map[0]["num"] if meter_map else 4, meter_map=meter_map)

        lines = flat.get("karaoke_lines") or []
        old_start = {kl.get("line"): float(kl.get("start_s", 0.0)) for kl in lines}
        loose = [c for c in flat.get("chords_timeline", []) if c.get("line") is None]
        loose_old = [float(c["time_s"]) for c in loose]

        gp_words = self._gp_vocal_words()
        if len(gp_words) >= max(4, len(lines) // 2):
            W.retime_web_to_gp(flat, gp_words)
            notes = ["Řádky napasované na zpěvní stopu z GP (%d slov)." % len(gp_words)]
        else:
            markers = markers_from_measures(ms, order)
            flat, notes = W.place_lines_on_structure(flat, grid, markers, len(order))
            notes.insert(0, "GP nemá použitelný zpěvní text (%d slov) — text rozložen "
                            "na taktovou strukturu GP." % len(gp_words))

        new_start = {kl.get("line"): float(kl.get("start_s", 0.0))
                     for kl in flat.get("karaoke_lines") or []}
        anchors = sorted((old_start[li], new_start[li] - old_start[li])
                         for li in old_start if li in new_start)
        for c, t_old in zip(loose, loose_old):
            prev = [d for t0, d in anchors if t0 <= t_old]
            delta = prev[-1] if prev else (anchors[0][1] if anchors else 0.0)
            c["time_s"] = round(max(0.0, t_old + delta), 3)
        flat.get("chords_timeline", []).sort(key=lambda e: e["time_s"])

        drums, drum_track = self._gp_drums_for_merge()
        bass, bass_track = self._gp_bass_for_merge()
        for key, evs, info, is_drums in (("drums_timeline", drums, drum_track, True),
                                         ("bass_timeline", bass, bass_track, False)):
            flat[key] = evs
            if not info:
                continue
            tracks = flat.setdefault("tracks", [])
            own = next((t for t in tracks if bool(t.get("is_drums")) == is_drums
                        and t.get("type") == info["type"]), None)
            if own is None:
                own = dict(info)
                own["index"] = max([t.get("index", 0) for t in tracks] + [0]) + 1
                tracks.append(own)
            else:
                own["name"] = info["name"]
            for ev in evs:
                ev["track_index"] = own["index"]

        last_end = grid.bar_start_seconds(len(order) + 1) if order else 0.0
        meta = flat.setdefault("meta", {})
        meta.update({
            "tempo_bpm": play_tm[0]["bpm"],
            "beats_per_measure": meter_map[0]["num"] if meter_map else 4,
            "time_signature": "%d/%d" % ((meter_map[0]["num"], meter_map[0]["den"])
                                         if meter_map else (4, 4)),
            "count_in_s": 0.0,
            "duration_seconds": round(last_end, 3),
        })
        flat["tempo_map"] = play_tm
        flat["meter_map"] = meter_map
        flat["linear_map"] = [{"bar": i + 1, "source_measure": src + 1, "pass": p}
                              for i, (src, p) in enumerate(plan)]
        return flat, notes

    def edit_repeats(self) -> None:
        """Časová osa ▸ Repetice… — počet zaznění každého bloku repetice
        z GP. Opravuje přepisy, které neodpovídají nahrávce (Jasná zpráva:
        druhé sólo zapsané 2×, kapela ho hraje jednou). Po změně se píseň
        znovu poskládá z GP: samotný GP import od nuly (ruční úpravy se
        ztratí — zeptá se), text z webu/vlastní se znovu napasuje na GP
        (`_merge_gp_tracks_into_current`). Mění se jen načtená píseň v paměti,
        GP soubor na disku zůstává beze změny."""
        song = self.song
        target = self.timeline
        if not song or not song.tracks:
            QMessageBox.information(self, tr("repeats.title"), tr("repeats.need_gp"))
            return
        measures = song.tracks[0].measures
        blocks = repeat_blocks(measures)
        if not blocks:
            QMessageBox.information(self, tr("repeats.title"), tr("repeats.none"))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("repeats.title"))
        form = QFormLayout(dlg)
        info = QLabel(tr("repeats.info"))
        info.setWordWrap(True)
        form.addRow(info)
        spins = []
        for a, b, plays in blocks:
            mk = next((measures[k].header.marker.title for k in range(a, b + 1)
                       if measures[k].header.marker is not None), "")
            sp = QSpinBox()
            sp.setRange(1, 8)
            sp.setValue(plays)
            sp.setSuffix("×")
            form.addRow(tr("repeats.block", a=a + 1, b=b + 1,
                           name=(" — " + mk) if mk else ""), sp)
            spins.append((b, plays, sp))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return
        changes = [(b, sp.value()) for b, plays, sp in spins if sp.value() != plays]
        if not changes:
            return
        pure_gp = target._loaded_json is None
        if pure_gp and target._dirty:
            ans = QMessageBox.question(self, tr("repeats.title"), tr("repeats.discard_edits"),
                                       QMessageBox.Yes | QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        for b, plays in changes:
            set_repeat_plays(measures, b, plays)
        if pure_gp:
            self._finish_gp_import(target)
            target._dirty = True
            target.dirty_changed.emit(True)
        else:
            self._merge_gp_tracks_into_current(song, self.current_gp_file or "", target)
        self.status.showMessage(tr("repeats.done", n=len(changes)))

    def _on_load_error(self, msg: str):
        QMessageBox.critical(self, tr("load.error_title"), tr("load.error_body", msg=msg))
        self.status.showMessage(tr("status.load_error"))

    # ------------------------------------------------------------------
    # Naplnění UI
    # ------------------------------------------------------------------

    def _populate_ui(self):
        """Vykreslí sdílené doky (Stopy/Náhledy) z GP song AKTIVNÍ karty.
        Bezpečné volat OPAKOVANĚ (i jen při přepnutí karty) — NEMĚNÍ
        `self.timeline.data` (na rozdíl od prvotního naplnění po čerstvém
        GP importu, viz `_finish_gp_import`, které volá
        `self.timeline.load_data(...)` — to by při opakování přepsalo
        živé úpravy udělané v editoru)."""
        song = self.song
        title = song.title or (Path(self.current_gp_file).stem if self.current_gp_file else "")
        artist = song.artist or "—"
        album = song.album or ""
        tempo = song.tempo
        n_tracks = len(song.tracks)
        n_measures = len(song.tracks[0].measures) if song.tracks else 0

        self.lbl_title.setText(title)
        self.lbl_artist.setText(artist)
        meta = tr("populate.meta_line", album=album, tempo=tempo, n_tracks=n_tracks,
                  n_measures=n_measures)
        self.lbl_meta.setText(meta)

        # Track tree
        self.track_tree.clear()
        # Stejné rozpoznání jako export (_build_karaoke_json): zpěv, jedna basa.
        vocal_ti = self._detect_vocal_track_index(song)
        bass_idx = pick_bass_track(song, exclude=(vocal_ti - 1) if vocal_ti else None)
        for i, t in enumerate(song.tracks):
            is_solo = is_solo_like(t)
            kind = ("drums" if t.isPercussionTrack else
                    "vocal" if i + 1 == vocal_ti else
                    "bass" if i == bass_idx else
                    "solo_guitar" if is_solo else
                    "guitar")
            item = QTreeWidgetItem([f"{i+1}. {t.name}", _track_type_label(kind)])
            item.setData(0, Qt.UserRole, i)
            item.setData(1, Qt.UserRole, kind)
            if t.isPercussionTrack:
                item.setForeground(0, QBrush(QColor("#d99a5b")))   # světlejší "saddle brown" pro tmavý podklad
            elif is_solo:
                item.setForeground(0, QBrush(QColor("#e07a7a")))   # světlejší tmavě červená pro tmavý podklad
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

    def _finish_gp_import(self, target: TimelineEditor) -> None:
        """Jednorázově po ČERSTVÉM GP importu — `target` JE aktivní karta v
        okamžiku volání (zajišťuje volající, viz `_on_loaded`/
        `_on_active_tab_changed`). Postaví karaoke JSON z GP song a nahraje
        ho do timeline (`load_data` resetuje undo/dirty — proto jen
        JEDNOU, nikdy opakovaně na již rozeditovanou kartu), pak dokreslí
        doky a vybere první stopu."""
        target.load_data(self._build_karaoke_json(preview_only=False))
        self._populate_ui()
        self.tabs.setCurrentIndex(0)
        if target.song and target.song.tracks:
            self._show_track(0)
            self.track_tree.setCurrentItem(self.track_tree.topLevelItem(0))
        # Nekonzistentní repeat/navigační značky (D.S. bez Segna apod.) —
        # rozbalení taktů se v takovém případě nehádá, jede lineárně a řekne
        # proč. Tiše to spolknout nejde: časová osa by byla jinak dlouhá než
        # skladba a uživatel by netušil, čím to je.
        warns = getattr(self, "last_linearize_warnings", None)
        if warns:
            QMessageBox.warning(self, tr("import.linearize_warn_title"),
                                tr("import.linearize_warn_body",
                                   details="\n• " + "\n• ".join(warns)))

    def _build_chord_chart(self):
        """Chord chart: akordy nad textem v notové osnově, 2 takty na řádek."""
        if not self.song:
            self.chord_chart_browser.setHtml(tr("chord_chart.no_data"))
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
            self.chord_chart_browser.setHtml(tr("chord_chart.no_track"))
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
            f'  <span style="font-size:12px; color:#888;">'
            f'{tr("chord_chart.tempo_label", tempo=song.tempo)}</span>\n\n'
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
                + html_escape(tr("chord_chart.no_content")).replace("\n", "<br>")
                + '\n</span>\n'
            )

        html.append('</div></body></html>')
        self.chord_chart_browser.setHtml(''.join(html))

    def _build_overview(self):
        song = self.song
        lines = []
        lines.append(tr("overview.title_header", title=song.title))
        lines.append(tr("overview.artist", artist=song.artist))
        lines.append(tr("overview.album", album=song.album))
        lines.append(tr("overview.tempo", tempo=song.tempo))
        lines.append(tr("overview.n_measures", n=len(song.tracks[0].measures) if song.tracks else 0))
        lines.append("")
        lines.append(tr("overview.tracks_header"))
        for i, t in enumerate(song.tracks):
            tuning_names = [midi_to_name(s.value) for s in t.strings]
            lines.append(tr(
                "overview.track_line", i=i + 1, name=t.name, n_strings=len(t.strings),
                tuning=', '.join(tuning_names),
                frets=getattr(t, 'fretCount', getattr(t, 'frets', 24)),
                drums_tag=tr("overview.drums_tag") if t.isPercussionTrack else '',
                solo_tag=tr("overview.solo_tag") if is_solo_like(t) else ''))
        lines.append("")

        # Tempo changes
        if len(self.tempo_map) > 1:
            lines.append(tr("overview.tempo_changes_header"))
            for tick, tempo in self.tempo_map:
                t_s = ticks_to_seconds(tick, self.tempo_map)
                lines.append(tr("overview.tempo_change_line", tick=tick, t_s=f"{t_s:.1f}", tempo=tempo))
            lines.append("")

        # Celková délka (odhadovaná) — repeat-aware. Počítat z posledního
        # SUROVÉHO taktu by u písně s repeticí ukázalo kratší délku, než
        # píseň skutečně má (repetice se v surových tickách neopakuje,
        # jen ji přehrávač čte vícekrát).
        if song.tracks:
            total_s = max((time_s + dur for t in song.tracks
                          for _m, _b, time_s, dur in walk_track_beats(t, self.tempo_map)),
                         default=0.0)
            if total_s:
                lines.append(tr("overview.estimated_duration",
                                mm=int(total_s // 60), ss=f"{int(total_s % 60):02d}"))

        self.overview_text.setText("\n".join(lines))

    def _build_lyrics(self):
        song = self.song
        lines = []

        # Song-level lyrics object
        if song.lyrics and hasattr(song.lyrics, 'lines'):
            for ll in song.lyrics.lines:
                if ll.lyrics and ll.lyrics.strip():
                    lines.append(tr("lyrics.song_level_header"))
                    lines.append(ll.lyrics)
                    lines.append("")

        # Beat-level text
        beat_texts = []
        for t in song.tracks:
            for m_idx, b, time_s, _dur in walk_track_beats(t, self.tempo_map):
                txt = get_beat_text(b)
                if txt:
                    beat_texts.append((time_s, m_idx + 1, t.name, txt))

        if beat_texts:
            lines.append(tr("lyrics.beat_header"))
            for time_s, m_num, track_name, text in sorted(beat_texts):
                lines.append(tr("lyrics.beat_line", time=f"{time_s:7.2f}", measure=f"{m_num:3d}",
                                track=track_name, text=text))
        else:
            lines.append(tr("lyrics.none_found"))
            lines.append("")
            lines.append(tr("lyrics.tip"))

        self.lyrics_text.setText("\n".join(lines))

    def _build_chords(self):
        song = self.song
        chord_events: list[tuple] = []
        chords_dict: dict[str, int] = {}

        for t in song.tracks:
            for m_idx, b, time_s, _dur in walk_track_beats(t, self.tempo_map):
                name = get_chord_name(b)
                if name:
                    chord_events.append((time_s, m_idx + 1, t.name, name))
                    chords_dict[name] = chords_dict.get(name, 0) + 1

        lines = []
        if chords_dict:
            lines.append(tr("chords.found_header"))
            for name, count in sorted(chords_dict.items()):
                lines.append(tr("chords.count_line", name=name, count=count))
            lines.append("")
            lines.append(tr("chords.timeline_header"))
            for time_s, m_num, track_name, name in sorted(chord_events):
                lines.append(tr("chords.timeline_line", time=f"{time_s:7.2f}", measure=f"{m_num:3d}",
                                track=track_name, name=name))
        else:
            lines.append(tr("chords.none_found"))
            lines.append("")
            lines.append(tr("chords.tip"))

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
        detail = (tr("status.track_detail_drums") if track.isPercussionTrack
                 else tr("status.track_detail_strings", n=len(track.strings)))
        self.status.showMessage(
            tr("status.track_selected", name=track.name,
               n_measures=len(track.measures), detail=detail))

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
        - **Bicí** i **basa** (4strunná, ne bicí) se exportují vždy (do
          `drums_timeline`/`bass_timeline`) — basa nese výšku (`midi`/
          `note_name`/`string`/`fret`), stejně jako v `_gp_bass_for_merge`.
        - **Sólo tabulatura** (stopa detekovaná `is_solo_like()` — jméno
          "solo/lead", nebo hodně bend efektů) se z GP not AUTOMATICKY
          vygeneruje jako ASCII bloky do `tab_timeline` (`_render_ascii_tab_
          block`/`_segment_and_render_tab_blocks`) — noty se rozdělí na
          fráze podle pauz a každá se vykreslí jako textový blok se
          `source: "gp"`. Blok je pak v editoru normální `BlockItem`
          (kind="tab") — text lze ručně doladit/přepsat stejně jako
          kterýkoli jiný ručně napsaný blok.
        - Do `tracks[]` jde zpěv, bicí, basa, sólo a stopy nesoucí akordy.
        """
        song = self.song
        if not song:
            return {}

        TICKS_PER_BEAT = 960
        vocal_ti = self._detect_vocal_track_index(song)

        # Skutečné pořadí přehrávání (repetice, zakončení, Segno/Coda/D.C./D.S.).
        # Ukládá se jako `linear_map`, aby šlo pro každý takt v přehrávání
        # dohledat, ze kterého taktu GP souboru pochází a kolikátý je to
        # průchod — bez toho se zpětný import do GP nemá o co opřít a
        # repetice se nedají očima zkontrolovat proti notám.
        measure_plan, self.last_linearize_warnings = (
            expand_measure_plan(song.tracks[0].measures) if song.tracks else ([], []))

        ms0 = song.tracks[0].measures if song.tracks else []
        order = [src for src, _p in measure_plan]
        play_tm = (play_tempo_map(ms0, order, self.tempo_map) if ms0 else list(self.tempo_map))
        meter_map = meter_map_from_measures(ms0, order) if ms0 else []
        grid = TimeGrid([{"tick": t, "bpm": v} for t, v in play_tm],
                        meter_map[0]["num"] if meter_map else 4, meter_map=meter_map)

        result = {
            "meta": {
                "format_version": 2,
                # Konec posledního přehraného taktu — jinak se délka písně
                # odvodí z poslední noty a doznívající poslední takt se usekne.
                "duration_seconds": round(grid.bar_start_seconds(len(order) + 1), 3) if order else 0.0,
                "beats_per_measure": meter_map[0]["num"] if meter_map else 4,
                "title": song.title or "",
                "artist": song.artist or "",
                "album": song.album or "",
                "tempo_bpm": song.tempo,
                "ticks_per_beat": TICKS_PER_BEAT,
                "total_measures": len(song.tracks[0].measures) if song.tracks else 0,
                "track_count": len(song.tracks),
                "source_file": Path(self.current_gp_file).name if self.current_gp_file else "",
                "has_line_structure": True,
            },
            # Ticky PŘEHRÁVÁNÍ (repetice rozbalené) — stejné číslování taktů
            # jako meter_map a linear_map. Metrum se v GP může měnit (Jasná
            # zpráva: takt 1 je 2/4, zbytek 4/4).
            "tempo_map": [{"tick": t, "bpm": v} for t, v in play_tm],
            "meter_map": meter_map,
            "linear_map": [
                {"bar": play_idx + 1, "source_measure": src_idx + 1, "pass": pass_no}
                for play_idx, (src_idx, pass_no) in enumerate(measure_plan)
            ],
            "tracks": [],
            "lyrics_timeline": [],
            "chords_timeline": [],
            "drums_timeline": [],
            "bass_timeline": [],
            "tab_timeline": [],
            "karaoke_lines": [],
        }

        all_chord_events: list[dict] = []
        all_drum_events: list[dict] = []
        all_bass_events: list[dict] = []
        all_tab_events: list[dict] = []
        vocal_beats: list[dict] = []   # {time_s, duration_s, text} ze zpěvní stopy
        contributes: dict[int, bool] = {}

        bass_idx = pick_bass_track(song, exclude=(vocal_ti - 1) if vocal_ti else None)
        # Bicí ze všech bicích stop jdou do JEDNÉ stopy (gp_drum_events, index
        # první bicí stopy) — další bicí stopy by v editoru byly prázdné.
        first_perc = next((i for i, t in enumerate(song.tracks) if t.isPercussionTrack), None)
        for t_idx, track in enumerate(song.tracks):
            ti = t_idx + 1
            track_type = (
                "drums" if track.isPercussionTrack else
                "vocal" if ti == vocal_ti else
                "bass" if t_idx == bass_idx else
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
            contributes[ti] = ((track.isPercussionTrack and t_idx == first_perc)
                               or (ti == vocal_ti) or track_type in ("bass", "solo_guitar"))
            track_solo_notes: list[tuple[float, int, int, str, float]] = []

            # walk_track_beats = repetice rozbalené (viz jeho docstring) —
            # naivní `for m in track.measures` dělalo časovou osu kratší
            # než reálná nahrávka u písní s repeat značkami.
            for m_idx, b, time_s, duration_s in walk_track_beats(track, self.tempo_map):
                if track.isPercussionTrack or track_type == "bass":
                    continue   # bicí a basa: gp_drum_events / gp_bass_events níž

                if track_type == "solo_guitar":
                    bpm = tempo_at_tick(b.start, self.tempo_map)
                    for n in sorted(b.notes, key=lambda n: n.string):
                        track_solo_notes.append(
                            (time_s, n.string, n.value, _note_tech_char(n), bpm))
                    continue

                chord_name = get_chord_name(b)
                if chord_name:
                    all_chord_events.append({
                        "time_s": time_s, "chord": chord_name,
                        "measure": m_idx + 1, "track_index": ti,
                        "source": "gp",
                    })
                    contributes[ti] = True

                if ti == vocal_ti:
                    beat_txt = get_beat_text(b)
                    if beat_txt and beat_txt.strip():
                        vocal_beats.append({
                            "time_s": time_s, "duration_s": duration_s,
                            "text": beat_txt.strip(),
                        })

            if track_solo_notes:
                track_solo_notes.sort(key=lambda e: e[0])
                all_tab_events.extend(_segment_and_render_tab_blocks(
                    track_solo_notes, _tab_string_labels(track), ti,
                    tuning=[s.value for s in track.strings]))

            if contributes[ti]:
                result["tracks"].append(track_data)

        all_drum_events, _dt = gp_drum_events(song, self.tempo_map)
        all_bass_events, _bt = gp_bass_events(
            song, self.tempo_map, exclude=(vocal_ti - 1) if vocal_ti else None)
        all_chord_events.sort(key=lambda e: e["time_s"])
        all_drum_events.sort(key=lambda e: e["time_s"])
        all_tab_events.sort(key=lambda e: e["time_s"])
        vocal_beats.sort(key=lambda e: e["time_s"])

        all_lyrics_events, line_ranges = self._vocal_beats_to_lines(vocal_beats, vocal_ti)

        def line_for(t: float):
            """Řádek, DO KTERÉHO čas `t` spadá, nebo `None`.

            `None` je správná odpověď pro intro, sólo a mezihry — akord,
            který zní mimo jakýkoli zpívaný řádek, k žádnému řádku nepatří
            (JSON_FORMAT.md §3.5).

            Dřív se tu vracel řádek 0, když byl čas PŘED prvním veršem.
            Tím dostalo intro i sólo Jasné zprávy (17 akordů, 1,8–49,8 s)
            značku `line: 0` a klip prvního verše se k nim natáhl — první
            verš pak visel na displeji od druhého taktu, tedy 45 sekund
            předtím, než se vůbec zazpíval.
            """
            for s, e, idx in line_ranges:
                if s <= t < e:
                    return idx
            return None

        for ev in all_chord_events:
            ev["line"] = line_for(ev["time_s"])
        for ev in all_drum_events:
            ev["line"] = line_for(ev["time_s"])

        all_bass_events.sort(key=lambda e: e["time_s"])

        result["lyrics_timeline"] = all_lyrics_events
        result["chords_timeline"] = all_chord_events
        result["drums_timeline"] = all_drum_events
        result["bass_timeline"] = all_bass_events
        result["tab_timeline"] = all_tab_events

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
                               "text": text, "line": li, "track_index": vocal_ti,
                               "source": "gp"})
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
                               "text": join_syllables(b["text"] for b in g),
                               "line": li, "track_index": vocal_ti,
                               "source": "gp"})
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
        # slabiky po notách („la-", „dy") -> celé slovo na čase PRVNÍ slabiky,
        # jinak se slova z webu („lady") se zpěvem z GP nespárují
        merged: list[tuple[float, str]] = []
        for t0, txt in beats:
            if txt == "-":
                continue
            if merged and merged[-1][1].endswith("-") and len(txt.split()) == 1:
                merged[-1] = (merged[-1][0], merged[-1][1][:-1] + txt)
            else:
                merged.append((t0, txt))
        beats = [(t0, txt.rstrip("-")) for t0, txt in merged if txt.rstrip("-")]
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
        """(drums_timeline, drum_track_dict) z aktuálního GP songu — viz `gp_drum_events`."""
        if not self.song:
            return [], None
        return gp_drum_events(self.song, self.tempo_map)

    def _gp_bass_for_merge(self):
        """(bass_timeline, bass_track_dict) z aktuálního GP songu — viz `gp_bass_events`."""
        if not self.song:
            return [], None
        vi = self._detect_vocal_track_index(self.song)
        return gp_bass_events(self.song, self.tempo_map, exclude=(vi - 1) if vi else None)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def save_json(self) -> None:
        """„💾 Uložit“ (Ctrl+S) — přepíše AKTUÁLNÍ JSON soubor rovnou, TICHE
        (jen stavový řádek, žádný popup), pokud už je nějaký cíl známý
        (`self.current_json_file` — píseň byla otevřena/uložena jako JSON).

        GP soubor se do appky jen IMPORTUJE (tabulatura/bicí/basa) — nikdy
        se do něj nezapisuje zpátky, takže samotné otevření/sloučení GP
        (`self.current_gp_file`) nikdy neurčuje, KAM se má ukládat. Píseň
        načtená/sloučená jen z GP tedy zatím žádný JSON cíl nemá a „Uložit“
        se v tom případě chová jako „Uložit jako…“ (musí se nejdřív zeptat,
        kam) — přesně stejná logika jako Ctrl+S vs. Ctrl+Shift+S v běžných
        aplikacích."""
        if not self.timeline.data:
            QMessageBox.warning(self, tr("common.warning_title"),
                               tr("export.warn_nothing_loaded"))
            return
        self.timeline._do_export(self.current_json_file, silent=bool(self.current_json_file))

    def save_json_as(self) -> None:
        """„💾 Uložit jako…“ (Ctrl+Shift+S) — VŽDY nabídne dialog s cestou,
        i když už JSON cíl existuje (uložení kopie/varianty pod jiným
        jménem). Exportuje AKTUÁLNÍ (živě upravená) data z editoru časové
        osy — bez ohledu na to, jestli se píseň dřív načetla přímo z .gp
        souboru, z JSONu nebo z webu. `_build_karaoke_json()` se používá
        JEN pro prvotní naplnění editoru po importu GP (`_populate_ui`),
        nikdy pro re-export — jinak by se tiše zahodily všechny úpravy
        udělané v editoru (přidaný text, splity, posuny, tempo…)."""
        if not self.timeline.data:
            QMessageBox.warning(self, tr("common.warning_title"),
                               tr("export.warn_nothing_loaded"))
            return
        self.timeline._do_export(None, silent=False)

    def export_esp32_json(self) -> None:
        """Soubor ▸ Exportovat pro ESP32 / tablety (.json) — čistý JSON podle
        JSON_FORMAT.md, bez bloku `project`. Projekt (.kproj) zůstává tím, co
        se otevírá a ukládá; export nemění, kam míří Ctrl+S."""
        if not self.timeline.data:
            QMessageBox.warning(self, tr("common.warning_title"), tr("export.warn_nothing_loaded"))
            return
        base = self.current_json_file or self.timeline.audio_path or self.current_gp_file or "pisen"
        default_name = str(Path(base).with_suffix(".json"))
        path, _ = QFileDialog.getSaveFileName(self, tr("file.export_esp32"), default_name,
                                              tr("export.json_filter"))
        if not path:
            return
        self.timeline._push_undo()
        data = self.timeline.to_json()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as ex:
            QMessageBox.critical(self, tr("export.error_title"), str(ex))
            return
        self.status.showMessage(tr("export.status_done", path=path))

    def _project_block(self, path: str) -> dict:
        """Blok `project` do .kproj: relativní cesty k nahrávce, GP a zdroj textu."""
        base = Path(path).parent
        info = getattr(self.timeline, "project_info", None) or {}

        def rel(p):
            if not p:
                return None
            try:
                return os.path.relpath(p, base).replace("\\", "/")
            except ValueError:           # jiný disk — necháme absolutní
                return str(p)

        meta = self.timeline.data.get("meta", {}) or {}
        return {"version": 1, "audio": rel(self.timeline.audio_path),
                "gp": rel(self.current_gp_file or info.get("gp")),
                "source_url": meta.get("source_url") or info.get("source_url")}

    def _export_timeline_json(self, data: dict, path: str | None = None, silent: bool = False) -> None:
        """Uloží projekt z časové osy (volá se z `save_json`/`save_json_as`
        — JEDNA sdílená cesta). `path=None` → dialog (Uložit jako…); jinak
        zapíše rovnou (Uložit). Nový soubor = projekt `.kproj` (JSON podle
        JSON_FORMAT.md + blok `project` s relativními cestami); dříve otevřený
        `.json` se dál přepisuje jako čistý JSON."""
        if not path:
            if self.current_json_file:
                default_name = str(Path(self.current_json_file).with_suffix(".kproj"))
            else:
                src = self.timeline.audio_path or self.current_gp_file
                title = (self.timeline.data.get("meta", {}) or {}).get("title") or ""
                stem = Path(src).stem if src else (title or "pisen")
                default_name = str(Path(src).parent / (stem + ".kproj")) if src else stem + ".kproj"
            path, _ = QFileDialog.getSaveFileName(
                self, tr("export.save_dialog_title"), default_name,
                tr("export.save_dialog_filter"))
            if not path:
                return
        if path.lower().endswith(".kproj"):
            data = dict(data)
            data["project"] = self._project_block(path)
        if not silent:
            self.status.showMessage(tr("export.status_exporting"))
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.current_json_file = path
            self.timeline._dirty = False
            self._refresh_tab_label(self.timeline)
            if silent:
                self.status.showMessage(tr("export.status_done", path=path))
            else:
                if data.get("schemaVersion"):
                    # nový formát (JSON_FORMAT.md): vše po sekcích — dřív se tu
                    # počítaly staré ploché klíče a hláška ukazovala samé nuly
                    secs = data.get("sections", []) or []
                    cnt = {k: sum(len(sc.get(k) or []) for sc in secs)
                           for k in ("lyrics", "chords", "drums", "bass", "tab")}
                    n_tracks = sum(1 for v in cnt.values() if v)
                    n_lyrics, n_chords, n_drums = cnt["lyrics"], cnt["chords"], cnt["drums"]
                    n_lines = cnt["lyrics"]          # jeden řádek = jedna jednotka textu
                else:
                    n_tracks = len(data.get("tracks", []))
                    n_lyrics = len(data.get("lyrics_timeline", []))
                    n_chords = len(data.get("chords_timeline", []))
                    n_drums = len(data.get("drums_timeline", []))
                    n_lines = len(data.get("karaoke_lines", []))
                body = tr("export.done_body", path=path, n_tracks=n_tracks,
                          n_lyrics=n_lyrics, n_chords=n_chords, n_drums=n_drums, n_lines=n_lines)
                QMessageBox.information(self, tr("export.done_title"), body)
                self.status.showMessage(tr("export.status_done", path=path))
        except Exception as ex:
            QMessageBox.critical(self, tr("export.error_title"), str(ex))
            self.status.showMessage(tr("export.status_error"))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _dark_palette() -> QPalette:
    """Pevná TMAVÁ paleta, bez ohledu na systémové téma (na přání — viz
    zrušená `_light_palette`, která řešila stejnou věc opačně).

    Nejde jen o paletu samotnou — spousta míst v appce nastavuje jen
    POPŘEDÍ nebo jen POZADÍ textu (ne obojí najednou) a spoléhá na to, že
    to druhé doplní právě tahle paleta (`WebImportDialog._recolor_row`,
    zvýrazněné buňky v `TrackDetailWidget`, `HelpDialog` HTML…). Tyhle
    barvy byly napsané pro SVĚTLÉ pozadí, takže je potřeba je zesvětlit
    / ztmavit společně s přechodem palety na tmavou — jinak by zase vyšly
    nečitelné kombinace, jen obráceně (světlý text na světlém zvýraznění
    apod.). Viz doprovodné opravy v `web_import.py`/`timeline_editor.py`
    (`color:#555/#777/#888/#999/#aaa` → zesvětleno) a `i18n.py`
    (`help.full.html`)."""
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(45, 45, 48))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.Base, QColor(30, 30, 32))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(42, 42, 46))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(60, 60, 40))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.Text, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.Button, QColor(55, 55, 58))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 90, 90))
    pal.setColor(QPalette.ColorRole.Link, QColor(110, 170, 255))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(51, 133, 255))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(120, 120, 120))
    return pal


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())

    window = GuitarProViewer()
    window.show()

    # Pokud je soubor předán jako argument
    if len(sys.argv) > 1:
        window._load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
