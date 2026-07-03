"""
web_import.py — Import akordů a textu z webové stránky.
Detekuje chord chart (akordy nad textem), exportuje jako GP4 + karaoke JSON.
"""

from __future__ import annotations

import re
import json
from html import escape as html_escape
from pathlib import Path
from typing import Any

import requests
import guitarpro
import guitarpro.models as gpm
from bs4 import BeautifulSoup

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QGroupBox,
    QLabel, QLineEdit, QPushButton, QTextEdit, QTextBrowser,
    QFileDialog, QMessageBox,
)

# ---------------------------------------------------------------------------
# Chord detekce
# ---------------------------------------------------------------------------

# Podpora českého značení akordů:
#   H = B (české H), mi = moll (Ami, Hmi, Cmi …), basový tón /H
_CHORD_RE = re.compile(
    r'^[A-H][#b]?'
    r'(mi|maj7?|m(?:in)?7?|M7?|aug|dim7?|sus[24]?|add\d+|\d{1,2}|[#+°ø])*'
    r'(/[A-H][#b]?)?$'
)
_SECTION_RE = re.compile(
    r'^\[.+\]$|^(Verse|Chorus|Bridge|Outro|Intro|Pre.?Chorus|Interlude'
    r'|Ref[ré]n|Sloka|Sloha|Refrén)\s*\d*:?\s*$',
    re.IGNORECASE,
)

TICKS_PER_BEAT = 960
MEASURE_4_4 = TICKS_PER_BEAT * 4   # 3840 tiků na 4/4 takt
# Náhradní znaky pro cp1250 (Guitar Pro soubory) — Unicode → cp1250 ekvivalent
_CP1250_REPLACEMENTS = str.maketrans({
    '\u2018': "'", '\u2019': "'",   # curly single quotes → '
    '\u201c': '"', '\u201d': '"',   # curly double quotes → "
    '\u2026': '...', '\u2025': '...',  # ellipsis
    '\u2013': '-', '\u2014': '-',   # en/em dash → -
    '\u00a0': ' ', '\u202f': ' ',   # non-breaking space → space
    '\u00a1': '!', '\u00bf': '?',   # ¡ ¿ → ! ?
    '\u0160': 'S', '\u0161': 's',   # Š š (jen pro jistotu, jsou v cp1250)
})


def sanitize_cp1250(text: str) -> str:
    """Nahradí znaky nezakódovatelné v cp1250."""
    if not text:
        return text
    text = text.translate(_CP1250_REPLACEMENTS)
    # Zbylé nezakódovatelné znaky → '?'
    return text.encode('cp1250', errors='replace').decode('cp1250')

def is_chord(token: str) -> bool:
    return bool(_CHORD_RE.match(token.strip()))


def is_chord_line(line: str) -> bool:
    tokens = line.split()
    if not tokens:
        return False
    return sum(1 for t in tokens if is_chord(t)) >= max(1, len(tokens) * 0.6)


def detect_chord_chart(lines: list[str]) -> list[dict]:
    result = []
    for line in lines:
        s = line  # zachováme whitespace pro column alignment
        stripped = s.rstrip()
        if not stripped.strip():
            result.append({'type': 'blank', 'text': '', 'raw': s})
        elif _SECTION_RE.match(stripped.strip()):
            result.append({'type': 'section', 'text': stripped.strip(), 'raw': s})
        elif is_chord_line(stripped.strip()):
            result.append({'type': 'chord', 'text': stripped, 'raw': s})
        else:
            result.append({'type': 'lyric', 'text': stripped, 'raw': s})
    return result


def apply_chord_templates(detected: list[dict]) -> list[dict]:
    """
    Weby jako pisnicky-akordy.cz mají akordy jen v první sloce.
    Tato funkce najde "orphan lyrics" (lyrické řádky BEZ předcházejícího
    chord řádku) a přiřadí jim akordy z poslední kompletní chord skupiny.

    Algoritmus:
    - Sledujeme "current_chord_group": chord řádky od posledního přerušení
    - Když se poprvé setkáme s orphan lyric → uložíme group jako šablonu
    - Pro každý další orphan lyric vložíme chord z šablony (cyklicky)
    - Když se vrátí chord řádky → začínáme novou group
    """
    if not any(it['type'] == 'chord' for it in detected):
        return detected  # nic k opravě

    current_chord_group: list[str] = []   # chords viditelné od posledního resetu
    last_chord_group: list[str] = []       # šablona pro orphan lyrics
    prev_non_blank: str | None = None
    in_orphan_block = False
    orphan_idx = 0

    result: list[dict] = []

    for item in detected:
        if item['type'] == 'blank':
            result.append(item)
            continue

        if item['type'] == 'chord':
            if in_orphan_block:
                # Vrátili jsme se do chord sekce — nevymaž šablonu, jen resetuj stav
                current_chord_group = []
                in_orphan_block = False
                orphan_idx = 0
            current_chord_group.append(item['text'])
            prev_non_blank = 'chord'
            result.append(item)

        elif item['type'] == 'lyric':
            is_orphan = (prev_non_blank != 'chord')

            if is_orphan:
                if not in_orphan_block:
                    # Začátek orphan bloku — uložíme aktuální chord group jako šablonu
                    if current_chord_group:
                        last_chord_group = current_chord_group.copy()
                    current_chord_group = []
                    orphan_idx = 0
                    in_orphan_block = True

                # Vložíme chord ze šablony
                if last_chord_group:
                    c = last_chord_group[orphan_idx % len(last_chord_group)]
                    result.append({'type': 'chord', 'text': c, 'raw': c})
                    orphan_idx += 1
            else:
                in_orphan_block = False

            prev_non_blank = 'lyric'
            result.append(item)

        else:
            result.append(item)

    return result


def align_chords_to_words(chord_line: str, lyric_line: str) -> list[tuple[str, str]]:
    """
    Mapuje akordy na slova podle sloupcové pozice (jako v Guitar Pro).
    Vrátí [(word, chord_or_empty), ...].
    Akord se přiřadí ke slovu, jehož počátek je nejblíže pozici akordu.
    """
    # Pozice akordů v chord_line
    chord_events: list[tuple[int, str]] = []
    for m in re.finditer(r'\S+', chord_line):
        if is_chord(m.group()):
            chord_events.append((m.start(), m.group()))

    # Pozice slov v lyric_line
    word_positions: list[tuple[int, str]] = [
        (m.start(), m.group()) for m in re.finditer(r'\S+', lyric_line)
    ]

    if not word_positions:
        # Jen akordová linka bez textu → každý akord je "beat" sám
        return [(c, c) for _, c in chord_events] or [('', '')]

    result: list[tuple[str, str]] = [(w, '') for _, w in word_positions]

    for col, chord_name in chord_events:
        # Najdi nejbližší slovo
        best = min(range(len(word_positions)), key=lambda j: abs(word_positions[j][0] - col))
        old_word, old_chord = result[best]
        # Pokud slovo již má akord, preferujeme první (nezměníme)
        if not old_chord:
            result[best] = (old_word, chord_name)

    return result


def pairs_to_measures(pairs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Rozdělí páry slov na takty po 4 čtvrťových notách."""
    measures = []
    for i in range(0, max(1, len(pairs)), 4):
        group = pairs[i:i + 4]
        while len(group) < 4:
            group.append(('', ''))   # doplníme pauzy
        measures.append(group)
    return measures


# ---------------------------------------------------------------------------
# GP song builder
# ---------------------------------------------------------------------------

def chart_to_gp_song(detected: list[dict], title: str, artist: str, tempo: int) -> Any:
    """Vytvoří guitarpro.Song z detekovaného chord chartu."""

    # Zpracujeme detekované řádky na (chord_line, lyric_line) páry
    all_measure_groups: list[list[tuple[str, str]]] = []
    items = [d for d in detected if d['type'] != 'blank']
    i = 0
    while i < len(items):
        item = items[i]
        if item['type'] == 'section':
            i += 1
            continue
        if item['type'] == 'chord':
            chord_line = item['text']
            lyric_line = ''
            if i + 1 < len(items) and items[i + 1]['type'] == 'lyric':
                lyric_line = items[i + 1]['text']
                i += 2
            else:
                i += 1
            pairs = align_chords_to_words(chord_line, lyric_line)
        else:   # lyric bez akordu
            pairs = [(w, '') for w in item['text'].split()]
            i += 1
        if not pairs:
            pairs = [('', '')]
        for grp in pairs_to_measures(pairs):
            all_measure_groups.append(grp)

    if not all_measure_groups:
        all_measure_groups = [[('', '')] * 4]

    n = len(all_measure_groups)

    # Song
    song = gpm.Song()
    song.title = sanitize_cp1250(title)
    song.artist = sanitize_cp1250(artist)
    song.album = ''
    song.tempo = tempo

    # Measure headers
    song.measureHeaders = []
    for idx in range(n):
        hdr = gpm.MeasureHeader()
        hdr.number = idx + 1
        hdr.start = TICKS_PER_BEAT + idx * MEASURE_4_4
        hdr.timeSignature = gpm.TimeSignature()
        hdr.timeSignature.numerator = 4
        hdr.timeSignature.denominator = gpm.Duration(value=4)
        song.measureHeaders.append(hdr)

    # Track
    track = gpm.Track(song=song, number=1)
    track.name = (title[:28] or 'Track 1')
    track.fretCount = 24
    track.strings = [
        gpm.GuitarString(number=1, value=64),
        gpm.GuitarString(number=2, value=59),
        gpm.GuitarString(number=3, value=55),
        gpm.GuitarString(number=4, value=50),
        gpm.GuitarString(number=5, value=45),
        gpm.GuitarString(number=6, value=40),
    ]
    track.channel = gpm.MidiChannel()
    track.channel.instrument = 25   # Acoustic Guitar Steel
    track.measures = []
    song.tracks = [track]

    # Lyrics object (GP4+)
    song.lyrics = gpm.Lyrics()
    song.lyrics.trackChoice = 1
    song.lyrics.lines = [gpm.LyricLine(startingMeasure=1, lyrics='')] * 5

    # Measures
    for idx, beat_pairs in enumerate(all_measure_groups):
        hdr = song.measureHeaders[idx]
        measure = gpm.Measure(track=track, header=hdr)

        voice = gpm.Voice(measure=measure)
        # GP5 potřebuje druhý prázdný hlas
        voice2 = gpm.Voice(measure=measure)
        eb = gpm.Beat(voice=voice2)
        eb.start = hdr.start
        eb.duration = gpm.Duration(value=1)
        eb.status = gpm.BeatStatus.empty
        voice2.beats = [eb]
        measure.voices = [voice, voice2]

        voice.beats = []
        tick = hdr.start

        for word, chord in beat_pairs:
            beat = gpm.Beat(voice=voice)
            beat.start = tick
            beat.duration = gpm.Duration(value=4)   # čtvrťová nota
            beat.status = gpm.BeatStatus.normal
            beat.effect = gpm.BeatEffect()

            if word and word.strip():
                beat.text = sanitize_cp1250(word.strip())

            if chord:
                ch = gpm.Chord(length=6)
                ch.name = sanitize_cp1250(chord)
                ch.firstFret = 1
                ch.strings = [-1] * 6
                beat.effect.chord = ch

            note = gpm.Note(beat=beat)
            note.string = 2
            note.value = 0          # otevřená struna B
            note.type = gpm.NoteType.normal
            note.velocity = gpm.Velocities.forte
            note.duration = 95
            note.effect = gpm.NoteEffect()
            beat.notes = [note]

            voice.beats.append(beat)
            tick += TICKS_PER_BEAT

        track.measures.append(measure)

    return song


# ---------------------------------------------------------------------------
# Karaoke JSON builder
# ---------------------------------------------------------------------------

def build_karaoke_json(
    detected: list[dict],
    title: str = '',
    artist: str = '',
    tempo: int = 120,
    source_url: str = '',
) -> dict:
    lyrics_timeline: list[dict] = []
    chords_timeline: list[dict] = []
    karaoke_lines: list[dict] = []

    tick = TICKS_PER_BEAT
    measure_num = 1
    active_chord = ''

    def ticks_to_s(t: int) -> float:
        return round((t - TICKS_PER_BEAT) / TICKS_PER_BEAT * (60.0 / tempo), 3)

    items = [d for d in detected if d['type'] != 'blank']
    i = 0
    while i < len(items):
        item = items[i]
        if item['type'] == 'section':
            i += 1
            continue

        if item['type'] == 'chord':
            chord_line = item['text']
            lyric_line = ''
            if i + 1 < len(items) and items[i + 1]['type'] == 'lyric':
                lyric_line = items[i + 1]['text']
                i += 2
            else:
                i += 1
            pairs = align_chords_to_words(chord_line, lyric_line)
        else:
            pairs = [(w, '') for w in item['text'].split()]
            i += 1

        if not pairs:
            tick += MEASURE_4_4
            measure_num += 1
            continue

        kline_words: list[dict] = []
        kline_chords: list[str] = []
        line_tick = tick
        # Přidáme aktivní akord na začátek řádku (i bez změny — karaoke potřebuje vědět akord)
        if active_chord:
            kline_chords.append(active_chord)

        for grp in pairs_to_measures(pairs):
            for word, chord in grp:
                if chord and chord != active_chord:
                    chords_timeline.append({'time_s': ticks_to_s(tick), 'chord': chord, 'measure': measure_num, 'track_index': 1})
                    active_chord = chord
                    if chord not in kline_chords:
                        kline_chords.append(chord)
                if word and word.strip():
                    ts = ticks_to_s(tick)
                    dur_s = round(TICKS_PER_BEAT / TICKS_PER_BEAT * (60.0 / tempo), 3)
                    lyrics_timeline.append({'time_s': ts, 'duration_s': dur_s, 'text': word.strip(), 'measure': measure_num, 'track_index': 1})
                    kline_words.append({'time_s': ts, 'text': word.strip(), 'track_index': 1})
                tick += TICKS_PER_BEAT
            measure_num += 1

        lyric_text = ' '.join(w for w, _ in pairs if w.strip())
        if lyric_text or kline_chords:
            karaoke_lines.append({
                'start_s': ticks_to_s(line_tick),
                'chords': kline_chords,
                'text': lyric_text,
                'words': kline_words,
            })

    return {
        'meta': {
            'format_version': 2,
            'title': title,
            'artist': artist,
            'tempo_bpm': tempo,
            'ticks_per_beat': TICKS_PER_BEAT,
            'source': source_url or 'web_import',
            'duration_seconds': round(ticks_to_s(tick), 1),
            'total_measures': measure_num - 1,
            'track_count': 1,
        },
        'tempo_map': [{'tick': TICKS_PER_BEAT, 'bpm': tempo}],
        'tracks': [{
            'index': 1,
            'name': title[:28] or 'Track 1',
            'type': 'guitar',
            'is_drums': False,
            'instrument_midi': 25,
            'has_tab': False,   # web import nese jen akordy + text, ne tabulatury
        }],
        'lyrics_timeline': lyrics_timeline,
        'chords_timeline': chords_timeline,
        'karaoke_lines': karaoke_lines,
    }


def save_gp_and_json(
    song: Any,
    detected: list[dict],
    title: str,
    artist: str,
    tempo: int,
    base_path: str,
    source_url: str = '',
) -> tuple[str, str]:
    """Uloží GP4 soubor a karaoke JSON. Vrátí (gp_path, json_path)."""
    stem = Path(base_path).with_suffix('').as_posix().split('/')[-1]
    parent = Path(base_path).parent

    gp_path = str(parent / (stem + '.gp4'))
    json_path = str(parent / (stem + '.json'))

    song.versionTuple = (4, 0, 6)
    guitarpro.write(song, gp_path, encoding='cp1250')

    data = build_karaoke_json(detected, title=title, artist=artist, tempo=tempo, source_url=source_url)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return gp_path, json_path


# ---------------------------------------------------------------------------
# Parsery pro konkrétní weby
# ---------------------------------------------------------------------------

def _parse_chord_positions(aline_el) -> list[tuple[int, str]]:
    """Extrahuje (sloupcová_pozice, akord) z <el class='aline'> elementu."""
    from bs4 import Tag, NavigableString as NS
    positions: list[tuple[int, str]] = []
    col = 0
    for child in aline_el.children:
        if isinstance(child, Tag) and 'akord' in child.get('class', []):
            positions.append((col, child.get_text()))
            col += len(child.get_text())
        elif isinstance(child, NS):
            col += len(str(child))
    return positions


def parse_pisnicky_akordy(html: str, url: str = '') -> tuple[str, str, list[dict]]:
    """
    Parser pro pisnicky-akordy.cz.
    Vrátí (title, artist, detected_lines).
    Stránka používá <pre> s <el class='aline'> pro chord řádky a text nodes pro lyrics.
    """
    from bs4 import Tag, NavigableString as NS
    soup = BeautifulSoup(html, 'html.parser')

    # Název — pisnicky-akordy.cz: H1[0]="Písničky Akordy" (site), H1[1]=název písně
    title = ''
    artist = ''
    h1s = soup.find_all('h1')
    if len(h1s) >= 2:
        title = h1s[1].get_text().strip()
    elif h1s:
        title = h1s[0].get_text().strip()

    # Fallback z URL slug
    if not title and url:
        from urllib.parse import urlparse
        slug = urlparse(url).path.rstrip('/').split('/')[-1]
        title = slug.replace('-', ' ').title()

    # Interpret — H2 (kategorie/interpret)
    h2 = soup.find('h2')
    if h2:
        artist = h2.get_text().strip()

    pre = soup.find('pre')
    if not pre:
        # Fallback: vyber obecně text
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        raw = soup.get_text(separator='\n')
        lines = [l.rstrip() for l in raw.splitlines()]
        text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        return title, artist, detect_chord_chart(text.splitlines())

    # pisnicky-akordy.cz specifický parsing — <el class="aline"> + text node
    result_text_lines: list[str] = []

    for child in pre.children:
        if isinstance(child, Tag) and 'aline' in child.get('class', []):
            # Rekonstruujeme chord řádek s mezerami
            parts: list[str] = []
            col = 0
            for sub in child.children:
                if isinstance(sub, Tag) and 'akord' in sub.get('class', []):
                    parts.append(sub.get_text())
                    col += len(sub.get_text())
                elif isinstance(sub, NS):
                    spacer = str(sub).rstrip('\n')
                    parts.append(spacer)
                    col += len(spacer)
            chord_line = ''.join(parts).rstrip()
            result_text_lines.append(chord_line)

        elif isinstance(child, NS):
            raw = str(child)
            for part in raw.split('\n'):
                stripped = part.rstrip()
                result_text_lines.append(stripped)

    # Odstraníme přebytečné blank řádky na začátku/konci
    while result_text_lines and not result_text_lines[0].strip():
        result_text_lines.pop(0)
    while result_text_lines and not result_text_lines[-1].strip():
        result_text_lines.pop()

    detected = apply_chord_templates(detect_chord_chart(result_text_lines))
    return title, artist, detected


def parse_generic_site(html: str) -> tuple[str, str, list[dict]]:
    """Obecný parser — hledá <pre> nebo textový obsah stránky."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
        tag.decompose()

    title = ''
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text().strip()

    content = (
        soup.find('pre') or
        soup.find(attrs={'class': re.compile(r'chord|lyric|tab|song|content', re.I)}) or
        soup.find('article') or
        soup.find('main') or
        soup.body
    )
    raw = (content or soup).get_text(separator='\n')
    lines = [l.rstrip() for l in raw.splitlines()]
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    return title, '', detect_chord_chart(text.splitlines())


# ---------------------------------------------------------------------------
# Fetch worker
# ---------------------------------------------------------------------------

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0 Safari/537.36',
    'Accept-Language': 'cs,sk;q=0.9,en;q=0.8',
}


def detect_encoding(r: requests.Response) -> str:
    """Robustní volba kódování pro české weby.

    Priorita: charset z HTTP hlavičky → <meta charset> v HTML → chardet →
    utf-8. Requests defaultuje na ISO-8859-1, když hlavička charset nemá, což
    rozbíjí českou diakritiku — proto ISO-8859-1 z hlavičky ignorujeme.
    """
    ctype = r.headers.get('Content-Type', '').lower()
    if 'charset=' in ctype:
        enc = ctype.split('charset=', 1)[1].split(';')[0].strip()
        if enc and enc.replace('-', '').replace('_', '') != 'iso88591':
            return enc

    # <meta charset="utf-8"> nebo <meta http-equiv ... charset=...>
    m = re.search(br'charset=["\']?\s*([\w\-]+)', r.content[:2048], re.IGNORECASE)
    if m:
        try:
            return m.group(1).decode('ascii')
        except UnicodeDecodeError:
            pass

    return r.apparent_encoding or 'utf-8'


class FetchWorker(QThread):
    done = Signal(str, str)
    error = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self) -> None:
        try:
            r = requests.get(self.url, timeout=15, headers=BROWSER_HEADERS)
            r.raise_for_status()
            r.encoding = detect_encoding(r)
            self.done.emit(self.url, r.text)
        except Exception as ex:
            self.error.emit(str(ex))


# ---------------------------------------------------------------------------
# Stažení celého interpreta
# ---------------------------------------------------------------------------

def extract_artist_songs(html: str, artist_url: str) -> tuple[str, list[tuple[str, str]]]:
    """Z HTML stránky interpreta vytáhne (slug, [(url_písně, název)]).

    pisnicky-akordy.cz: interpret = /<slug>, píseň = /<slug>/<song-slug>.
    Bereme jen odkazy s přesně dvěma segmenty pod slugem, deduplikované.
    """
    from urllib.parse import urlparse, urljoin
    slug = urlparse(artist_url).path.strip('/').split('/')[0] or 'artist'
    soup = BeautifulSoup(html, 'html.parser')
    pat = re.compile(rf'^/{re.escape(slug)}/[^/]+$')
    seen: dict[str, str] = {}
    for a in soup.find_all('a', href=True):
        href = a['href'].split('?')[0].split('#')[0].rstrip('/')
        if pat.match(href):
            seen.setdefault(href, a.get_text(strip=True))
    songs = [(urljoin(artist_url, h), t) for h, t in seen.items()]
    return slug, songs


class ArtistDownloadWorker(QThread):
    """Stáhne všechny písně jednoho interpreta do složky (GP4 + JSON)."""
    progress = Signal(int, int, str)          # hotovo, celkem, aktuální název
    finished_all = Signal(int, int, str)      # ok, chyby, cílová složka
    error = Signal(str)

    def __init__(self, artist_url: str, out_dir: str, tempo: int = 120):
        super().__init__()
        self.artist_url = artist_url
        self.out_dir = out_dir
        self.tempo = tempo
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            r = requests.get(self.artist_url, timeout=20, headers=BROWSER_HEADERS)
            r.raise_for_status()
            r.encoding = detect_encoding(r)
            slug, songs = extract_artist_songs(r.text, self.artist_url)
        except Exception as ex:
            self.error.emit(f"Nelze načíst stránku interpreta: {ex}")
            return

        if not songs:
            self.error.emit("Na stránce nebyly nalezeny žádné odkazy na písně.")
            return

        dest = Path(self.out_dir) / re.sub(r'[^\w\-]', '_', slug)[:60]
        dest.mkdir(parents=True, exist_ok=True)

        total, ok, fail = len(songs), 0, 0
        for i, (url, link_title) in enumerate(songs, 1):
            if self._cancel:
                break
            self.progress.emit(i, total, link_title or url)
            try:
                rr = requests.get(url, timeout=20, headers=BROWSER_HEADERS)
                rr.raise_for_status()
                rr.encoding = detect_encoding(rr)
                title, artist, detected = parse_pisnicky_akordy(rr.text, url=url)
                title = title or link_title or f"song_{i}"
                if not any(d['type'] in ('chord', 'lyric') for d in detected):
                    fail += 1
                    continue
                artist = artist or slug
                song = chart_to_gp_song(detected, title=title, artist=artist, tempo=self.tempo)
                safe = re.sub(r'[^\w\-]', '_', title)[:60] or f"song_{i}"
                save_gp_and_json(
                    song, detected, title=title, artist=artist,
                    tempo=self.tempo, base_path=str(dest / safe), source_url=url,
                )
                ok += 1
            except Exception:
                fail += 1
            self.msleep(400)   # ohleduplná prodleva mezi requesty

        self.finished_all.emit(ok, fail, str(dest))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class WebImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import z webu — GP4 + Karaoke JSON")
        self.setMinimumSize(1200, 720)
        self._worker: FetchWorker | None = None
        self._artist_worker: ArtistDownloadWorker | None = None
        self._detected: list[dict] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # URL
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://  (stránka s akordy — ultimate-guitar, supermusic, …)")
        self.url_input.returnPressed.connect(self._fetch)
        self.fetch_btn = QPushButton("🔄  Načíst")
        self.fetch_btn.clicked.connect(self._fetch)
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.fetch_btn)
        root.addLayout(url_row)

        # Meta
        meta_row = QHBoxLayout()
        meta_row.addWidget(QLabel("Název:"))
        self.title_input = QLineEdit()
        meta_row.addWidget(self.title_input, 2)
        meta_row.addWidget(QLabel("Interpret:"))
        self.artist_input = QLineEdit()
        meta_row.addWidget(self.artist_input, 2)
        meta_row.addWidget(QLabel("Tempo:"))
        self.tempo_input = QLineEdit("120")
        self.tempo_input.setMaximumWidth(60)
        meta_row.addWidget(self.tempo_input)
        root.addLayout(meta_row)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)

        left_box = QGroupBox("Text z webu  (lze editovat před rozpoznáním)")
        ll = QVBoxLayout(left_box)
        self.raw_text = QTextEdit()
        self.raw_text.setFont(QFont("Courier New", 11))
        self.raw_text.setPlaceholderText(
            "Sem vložte nebo načtěte text chord chartu:\n\n"
            "D          D\n"
            "Skákal pes přes oves,\n"
            "D            A7\n"
            "přes zelenou louku,\n"
        )
        ll.addWidget(self.raw_text)
        splitter.addWidget(left_box)

        right_box = QGroupBox("Rozpoznaný chord chart  (preview)")
        rl = QVBoxLayout(right_box)
        self.chart_browser = QTextBrowser()
        self.chart_browser.setFont(QFont("Courier New", 12))
        rl.addWidget(self.chart_browser)
        splitter.addWidget(right_box)

        splitter.setSizes([560, 600])
        root.addWidget(splitter, 1)

        # Buttons
        btn_row = QHBoxLayout()
        parse_btn = QPushButton("🔍  Rozpoznat")
        parse_btn.clicked.connect(self._parse)
        save_btn = QPushButton("💾  Uložit GP4 + JSON")
        save_btn.setStyleSheet(
            "QPushButton{background:#2d7d2d;color:white;padding:6px 16px;"
            "border-radius:4px;font-weight:bold;font-size:13px;}"
            "QPushButton:hover{background:#3a9e3a;}"
        )
        save_btn.clicked.connect(self._save)

        self.artist_btn = QPushButton("📚  Stáhnout celého interpreta")
        self.artist_btn.setToolTip(
            "Zadej do URL stránku interpreta (např. pisnicky-akordy.cz/olympic) "
            "a vyber složku — stáhnou se všechny písně jako GP4 + JSON."
        )
        self.artist_btn.clicked.connect(self._download_artist)

        btn_row.addWidget(parse_btn)
        btn_row.addWidget(self.artist_btn)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

        self.status_lbl = QLabel("Zadejte URL nebo vložte text ručně, pak klikněte na Rozpoznat.")
        root.addWidget(self.status_lbl)

    def _fetch(self) -> None:
        url = self.url_input.text().strip()
        if not url.startswith('http'):
            url = 'https://' + url
            self.url_input.setText(url)
        self.status_lbl.setText(f"Načítám: {url} …")
        self.fetch_btn.setEnabled(False)
        self._worker = FetchWorker(url)
        self._worker.done.connect(self._on_fetched)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_error(self, msg: str) -> None:
        self.status_lbl.setText(f"Chyba: {msg}")
        self.fetch_btn.setEnabled(True)

    def _on_fetched(self, url: str, html: str) -> None:
        self.fetch_btn.setEnabled(True)

        # Výběr parseru podle domény
        if 'pisnicky-akordy.cz' in url:
            title, artist, self._detected = parse_pisnicky_akordy(html, url=url)
        else:
            title, artist, self._detected = parse_generic_site(html)

        # Auto-fill metadat
        if title and not self.title_input.text():
            self.title_input.setText(title[:80])
        if artist and not self.artist_input.text():
            self.artist_input.setText(artist[:60])

        # Zobraz čistý text v levém panelu (chord + lyric řádky)
        clean_lines = [d['text'] for d in self._detected]
        self.raw_text.setPlainText('\n'.join(clean_lines))

        self._render(self._detected)
        n_c = sum(1 for d in self._detected if d['type'] == 'chord')
        n_l = sum(1 for d in self._detected if d['type'] == 'lyric')
        self.status_lbl.setText(
            f"Načteno: {n_c} chord řádků, {n_l} lyric řádků. "
            f"Zkontroluj preview → Uložit GP4 + JSON."
        )

    def _parse(self) -> None:
        text = self.raw_text.toPlainText()
        if not text.strip():
            return
        self._detected = apply_chord_templates(detect_chord_chart(text.splitlines()))
        self._render(self._detected)
        n_c = sum(1 for d in self._detected if d['type'] == 'chord')
        n_l = sum(1 for d in self._detected if d['type'] == 'lyric')
        self.status_lbl.setText(
            f"Rozpoznáno: {n_c} chord řádků, {n_l} lyric řádků. "
            f"Zkontroluj preview → Uložit GP4 + JSON."
        )

    def _render(self, detected: list[dict]) -> None:
        parts = [
            '<html><body style="background:#fafafa;">',
            '<div style="font-family:\'Courier New\',monospace;font-size:13px;'
            'padding:15px;white-space:pre;line-height:1.5;">',
        ]
        for item in detected:
            t = html_escape(item['text'])
            if item['type'] == 'chord':
                parts.append(f'<span style="color:#1a5fb4;font-weight:bold;">{t}</span>\n')
            elif item['type'] == 'lyric':
                parts.append(f'{t}\n')
            elif item['type'] == 'section':
                parts.append(f'\n<span style="color:#888;font-style:italic;font-weight:bold;">{t}</span>\n')
            else:
                parts.append('\n')
        parts.append('</div></body></html>')
        self.chart_browser.setHtml(''.join(parts))

    def _save(self) -> None:
        if not self._detected or not any(d['type'] in ('chord', 'lyric') for d in self._detected):
            QMessageBox.warning(self, "Upozornění", "Nejprve načtěte/rozpoznejte obsah.")
            return

        title = self.title_input.text().strip() or "Unknown"
        artist = self.artist_input.text().strip()
        try:
            tempo = int(self.tempo_input.text())
        except ValueError:
            tempo = 120

        safe = re.sub(r'[^\w\-]', '_', title)[:40]
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Uložit GP4 + JSON (zadejte základ jména)",
            safe,
            "Všechny soubory (*)"
        )
        if not out_path:
            return

        try:
            song = chart_to_gp_song(self._detected, title=title, artist=artist, tempo=tempo)
            gp_path, json_path = save_gp_and_json(
                song, self._detected, title=title, artist=artist,
                tempo=tempo, base_path=out_path,
                source_url=self.url_input.text().strip(),
            )
            n_kl = len(build_karaoke_json(self._detected, title, artist, tempo)['karaoke_lines'])
            self.status_lbl.setText(
                f"Uloženo:  {Path(gp_path).name}  +  {Path(json_path).name}  "
                f"({n_kl} karaoke řádků)"
            )
            QMessageBox.information(
                self, "Hotovo",
                f"GP4:  {gp_path}\nJSON: {json_path}"
            )
        except Exception as ex:
            QMessageBox.critical(self, "Chyba", str(ex))
            raise

    # ------------------------------------------------------------------
    # Stažení celého interpreta
    # ------------------------------------------------------------------

    def _download_artist(self) -> None:
        # Běžící stahování → tlačítko funguje jako Zrušit
        if self._artist_worker is not None and self._artist_worker.isRunning():
            self._artist_worker.cancel()
            self.status_lbl.setText("Ruším stahování…")
            return

        url = self.url_input.text().strip()
        if not url:
            QMessageBox.information(
                self, "Zadej URL interpreta",
                "Do pole URL vlož stránku interpreta, např.:\n"
                "https://pisnicky-akordy.cz/olympic"
            )
            return
        if not url.startswith('http'):
            url = 'https://' + url

        out_dir = QFileDialog.getExistingDirectory(self, "Vyber cílovou složku")
        if not out_dir:
            return

        try:
            tempo = int(self.tempo_input.text())
        except ValueError:
            tempo = 120

        self._artist_worker = ArtistDownloadWorker(url, out_dir, tempo=tempo)
        self._artist_worker.progress.connect(self._on_artist_progress)
        self._artist_worker.finished_all.connect(self._on_artist_done)
        self._artist_worker.error.connect(self._on_artist_error)
        self.artist_btn.setText("⏹  Zrušit stahování")
        self.status_lbl.setText("Načítám seznam písní interpreta…")
        self._artist_worker.start()

    def _on_artist_progress(self, done: int, total: int, name: str) -> None:
        self.status_lbl.setText(f"Stahuji {done}/{total}:  {name}")

    def _on_artist_done(self, ok: int, fail: int, dest: str) -> None:
        self.artist_btn.setText("📚  Stáhnout celého interpreta")
        self.status_lbl.setText(f"Hotovo: {ok} uloženo, {fail} přeskočeno → {dest}")
        QMessageBox.information(
            self, "Stažení dokončeno",
            f"Uloženo: {ok}\nPřeskočeno/chyby: {fail}\n\nSložka:\n{dest}"
        )
        self._artist_worker = None

    def _on_artist_error(self, msg: str) -> None:
        self.artist_btn.setText("📚  Stáhnout celého interpreta")
        self.status_lbl.setText(f"Chyba: {msg}")
        QMessageBox.critical(self, "Chyba", msg)
        self._artist_worker = None


