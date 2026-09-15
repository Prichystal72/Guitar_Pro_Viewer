"""
ascii_tab.py — Sdílený renderer ASCII sólo tabulatury (viz `BlockItem`
kind="tab" v timeline_editor.py). Používá GP import (guitar_pro_viewer.py)
i MIDI import (midi_import.py) — vstupem jsou obyčejné n-tice (čas, struna,
pražec, technika), ne objekty konkrétního parseru, takže je modul na obou
nezávislý.
"""

from __future__ import annotations

import re

TAB_UNIT_DIVISION = 4       # kolik ASCII sloupců na jednu dobu (šestnáctinová mřížka)
TAB_PHRASE_GAP_BEATS = 1.5  # pauza delší než tolik dob ukončí frázi (nový blok)
TAB_PHRASE_MAX_S = 6.0      # strop délky jedné fráze v sekundách (čitelnost bloku)

# Standardní ladění kytary (6 strun, string 1 = nejvyšší/nejtenčí — stejná
# konvence jako GP `note.string`) — použito pro dopočet "hmatu" při
# transpozici (`note_to_string_fret`) a jako výchozí ladění u ručně psaných
# bloků (`parse_ascii_tab_block`/`generate_transpose_variants`).
STANDARD_GUITAR_TUNING = [64, 59, 55, 50, 45, 40]   # e4 B3 G3 D3 A2 E2

TRANSPOSE_SEMITONES = (-3, -2, -1, 1, 2, 3)   # viz diskuze o transpozici sóla
_TECH_CHARS = set("h~/b")


def render_ascii_tab_block(events: list[tuple[float, int, int, str]],
                            n_strings: int, labels: list[str],
                            unit_s: float) -> str:
    """`events`: (čas od začátku bloku, struna 1..n — 1 je nejvyšší, pražec,
    technika). Noty se "vypíšou" přes výchozí pomlčky na pozici dané
    šestnáctinovou mřížkou (`unit_s`) — víceciferný pražec prostě zabere
    o znak víc, žádné umělé zarovnávání na pevnou šířku sloupce (stejný
    styl jako v uživatelově referenčním příkladu).

    Noty na STEJNÉ struně se nikdy nepřekrývají ANI na sebe nelepí bez
    mezery — u rychlých pasáží (víc not do jedné šestnáctinové buňky, běžné
    třeba u ornamentů/triol v MIDI importu) by přepsání znaků slilo dva
    pražce do jednoho neexistujícího čísla (např. "1" a "12" vedle sebe by
    se slily na "112") a i pouhé nalepení bez pomlčky je opticky
    nečitelné/nejednoznačné (kde končí jeden pražec a začíná další). Místo
    toho se kolidující nota "vytlačí" hned za konec té předchozí PLUS
    jedna oddělovací pomlčka — mřížka se zachová, jen se místy lehce
    "narovná do fronty"."""
    rows: list[list[str]] = [[] for _ in range(n_strings)]
    next_free: list[int] = [0] * n_strings

    def ensure(row: list[str], n: int) -> None:
        while len(row) < n:
            row.append("-")

    for rel_t, string_no, fret, tech in sorted(events, key=lambda e: e[0]):
        row_idx = string_no - 1
        if not (0 <= row_idx < n_strings):
            continue
        pos = max(0, int(round(rel_t / unit_s)), next_free[row_idx])
        text = f"{fret}{tech}"
        row = rows[row_idx]
        ensure(row, pos + len(text))
        for i, ch in enumerate(text):
            row[pos + i] = ch
        next_free[row_idx] = pos + len(text) + 1

    width = max(max((len(r) for r in rows), default=0), 1)
    for r in rows:
        ensure(r, width)
    return "\n".join(f"{labels[i]}|{''.join(rows[i])}|" for i in range(n_strings))


def note_to_string_fret(pitch: int, tuning: list[int], prev_string: int | None,
                        prev_fret: int | None, max_fret: int = 22) -> tuple[int, int]:
    """Vrátí `(string, fret)` — `string` 1-based, 1 = nejvyšší/nejtenčí
    (stejná konvence jako GP `note.string`). Mezi VŠEMI strunami, na
    kterých pitch vůbec leží (0..max_fret), vybere tu s nejmenší
    "vzdáleností" od předchozí pozice na hmatníku (žádná předchozí pozice =
    preferuje nejnižší pražec) — používá se jak pro MIDI import, tak pro
    přepočet "hmatu" při transpozici (`transpose_events`)."""
    candidates = []
    for idx, open_note in enumerate(tuning):
        fret = pitch - open_note
        if 0 <= fret <= max_fret:
            candidates.append((idx + 1, fret))
    if not candidates:
        idx = min(range(len(tuning)), key=lambda i: abs(pitch - tuning[i]))
        fret = max(0, min(max_fret, pitch - tuning[idx]))
        return idx + 1, fret
    if prev_string is None:
        return min(candidates, key=lambda c: c[1])
    def cost(c):
        string_no, fret = c
        return abs(string_no - prev_string) * 2 + abs(fret - (prev_fret or fret))
    return min(candidates, key=cost)


def transpose_events(cur: list[tuple[float, int, int, str, float]], tuning: list[int],
                     semitones: int, max_fret: int = 22) -> list[tuple[float, int, int, str, float]]:
    """`cur`: (time_s, struna, pražec, technika, bpm) jedné fráze. Vrátí
    novou frázi se STEJNÝM rytmem/technikou, ale s hmatem přepočítaným pro
    výšku posunutou o `semitones` — ne jen mechanicky +semitones na čísle
    pražce (to by u nízkých pražců vyjelo do záporných čísel).

    PRIMÁRNĚ zůstává na STEJNÉ struně, jen posune pražec o `semitones` —
    to je přesně to, co dělá kytarista při přehrání stejné fráze jinde na
    hmatníku: IDENTICKÝ tvar ruky/prstoklad, jen posunutý, žádné
    přemapování na jinou strunu. Teprve když by posunutý pražec vyšel mimo
    rozsah nástroje (blízko pražce 0 nebo nejvyššího pražce), sáhne se po
    záložní heuristice (`note_to_string_fret` — nejbližší pozice od
    předchozí noty) a najde se náhradní struna. Bez týhle priority by
    "nejbližší pozice" heuristika mohla u frází blízko okraje hmatníku
    zbytečně přeskakovat mezi strunami i tam, kde stačilo zůstat na
    původní — reálné riziko nehratelného/nepřirozeného prstokladu."""
    out = []
    prev_string, prev_fret = None, None
    for t, string_no, fret, tech, bpm in cur:
        same_string_fret = fret + semitones
        if 0 <= string_no - 1 < len(tuning) and 0 <= same_string_fret <= max_fret:
            new_string, new_fret = string_no, same_string_fret
        else:
            pitch = tuning[string_no - 1] + fret + semitones if 0 <= string_no - 1 < len(tuning) else fret + semitones
            new_string, new_fret = note_to_string_fret(pitch, tuning, prev_string, prev_fret, max_fret)
        prev_string, prev_fret = new_string, new_fret
        out.append((t, new_string, new_fret, tech, bpm))
    return out


def segment_and_render_tab_blocks(notes: list[tuple[float, int, int, str, float]],
                                  labels: list[str], track_index: int,
                                  tuning: list[int] | None = None,
                                  transpose_semitones=TRANSPOSE_SEMITONES) -> list[dict]:
    """`notes`: (time_s, struna, pražec, technika, bpm) JEDNÉ sólo stopy,
    seřazené podle času. Rozdělí je na fráze — pauza >= `TAB_PHRASE_GAP_BEATS`
    dob NEBO celková délka nad `TAB_PHRASE_MAX_S` frázi ukončí — a každou
    vyrenderuje jako samostatný ASCII blok (`render_ascii_tab_block`).
    Vrací hotové `tab_timeline` položky (`time_s`, `duration_s`, `text`,
    `track_index`, `source`).

    Když je zadané `tuning`, ke KAŽDÉMU bloku se navíc dopočítají hotové
    transpoziční varianty (`transpose_events` + `render_ascii_tab_block`
    pro každý půltón v `transpose_semitones`) a uloží se do `item["variants"]`
    (`{"-3": text, ..., "3": text}`) — firmware pak jen PŘEPÍNÁ mezi hotovými
    texty, žádné parsování/rozjíždění sloupců za běhu (viz diskuze o
    transpozici sóla)."""
    if not notes:
        return []
    n_strings = len(labels)
    blocks: list[dict] = []
    cur: list[tuple[float, int, int, str, float]] = [notes[0]]
    block_start = notes[0][0]
    prev_end = notes[0][0]

    def flush(end_s: float) -> None:
        bpm = cur[0][4]
        unit_s = 60.0 / bpm / TAB_UNIT_DIVISION
        events = [(t - block_start, s, f, tech) for t, s, f, tech, _bpm in cur]
        text = render_ascii_tab_block(events, n_strings, labels, unit_s)
        item = {
            "time_s": round(block_start, 3),
            "duration_s": round(max(end_s - block_start, unit_s), 3),
            "text": text,
            "track_index": track_index,
            "source": "gp",
        }
        if tuning is not None:
            variants = {}
            for semi in transpose_semitones:
                t_cur = transpose_events(cur, tuning, semi)
                t_events = [(t - block_start, s, f, tech) for t, s, f, tech, _bpm in t_cur]
                variants[str(semi)] = render_ascii_tab_block(t_events, n_strings, labels, unit_s)
            item["variants"] = variants
        blocks.append(item)

    for t, string_no, fret, tech, bpm in notes[1:]:
        beat_s = 60.0 / bpm
        gap = t - prev_end
        too_long = (t - block_start) > TAB_PHRASE_MAX_S
        if gap > TAB_PHRASE_GAP_BEATS * beat_s or too_long:
            flush(prev_end + beat_s * 0.25)
            cur = []
            block_start = t
        cur.append((t, string_no, fret, tech, bpm))
        prev_end = t
    flush(prev_end + (60.0 / cur[-1][4]) * 0.25)
    return blocks


def parse_ascii_tab_block(text: str) -> tuple[list[str], list[tuple[int, int, int, str]]] | None:
    """Rozebere ASCII text zpátky na (popisky strun, noty) — inverzní
    operace k `render_ascii_tab_block`. Vrací `(labels, [(sloupec, struna,
    pražec, technika), ...])`, kde `labels` jsou PŮVODNÍ popisky z textu
    (zachová i neobvyklé ladění — appka si nic nevymýšlí), nebo `None`,
    když text nejde bezpečně rozpoznat (jiný formát řádků, než appka sama
    generuje — `LABEL|...|`), typicky u textu napsaného úplně jinou
    konvencí. `None` znamená "transpoziční varianty pro tenhle blok
    negenerovat", NE pád — appka pak jen zobrazí zadaný text beze změny."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return [], []
    labels: list[str] = []
    events: list[tuple[int, int, int, str]] = []
    for row_idx, line in enumerate(lines):
        m = re.match(r"^(.*?)\|(.*)\|\s*$", line)
        if not m:
            return None
        labels.append(m.group(1))
        content = m.group(2)
        i = 0
        while i < len(content):
            ch = content[i]
            if ch.isdigit():
                j = i
                while j < len(content) and content[j].isdigit():
                    j += 1
                fret = int(content[i:j])
                k = j
                while k < len(content) and content[k] in _TECH_CHARS:
                    k += 1
                tech = content[j:k]
                events.append((i, row_idx + 1, fret, tech))
                i = k
            else:
                i += 1
    return labels, events


def generate_transpose_variants(text: str, tuning: list[int] = STANDARD_GUITAR_TUNING,
                                semitones_range=TRANSPOSE_SEMITONES) -> dict[str, str] | None:
    """Pro RUČNĚ psaný/upravený blok (žádná podkladová data o výšce not,
    na rozdíl od `segment_and_render_tab_blocks`) — rozebere text
    (`parse_ascii_tab_block`), a pokud se to povede, vrátí hotové
    transpoziční varianty stejně jako auto-generované bloky. `None`, když
    text nejde rozebrat (viz `parse_ascii_tab_block`) — volající pak má
    jen smazat případné staré `variants` z předchozí verze textu."""
    parsed = parse_ascii_tab_block(text)
    if parsed is None:
        return None
    labels, events_in = parsed
    if not labels:
        return {str(s): text for s in semitones_range}
    n_strings = len(labels)
    if n_strings != len(tuning):
        # neobvyklý počet strun (ne standardní 6strunná kytara) — hmat
        # bychom mohli dopočítat špatně, radši negenerovat nic než hádat.
        return None
    # sloupec coby "čas" — unit_s=1.0 znamená `pos == round(rel_t)`, tedy
    # přesně zachovaný sloupec beze změny mřížky (viz render_ascii_tab_block).
    cur = [(float(col), string_no, fret, tech, 999.0) for col, string_no, fret, tech in events_in]
    variants: dict[str, str] = {}
    for semi in semitones_range:
        t_cur = transpose_events(cur, tuning, semi)
        events = [(t, s, f, tech) for t, s, f, tech, _bpm in t_cur]
        variants[str(semi)] = render_ascii_tab_block(events, n_strings, labels, 1.0)
    return variants
