"""
music_grid.py — jediný převod mezi sekundami a pozicí v taktech.

Používá ho editor (pravítko, přichytávání, odpočet) i konvertor formátu.
Schválně na jednom místě: dvě mírně odlišná číslování taktů je přesně ta
třída chyb, kvůli které tenhle modul vznikl.

Umí **proměnné metrum**. Reálný důvod: `Olympic - Jasná Zpráva.gp5` má takt 1
ve 2/4 a takty 2–61 ve 4/4. Když se celá píseň počítá jedním metrem, ten
jeden kratší takt se počítá jako plný a **všechna čísla taktů od druhého dál
sedí o dvě doby vedle** (1,85 s při 65 BPM). V sekundách je hudba pořád
správně, ale pravítko lže proti notám.

Dvě nezávislé mapy:

- **tempo** (`[{tick, bpm}]` nebo `[{bar, beat, bpm}]`) — jak dlouho trvá doba,
- **metrum** (`[{bar, num, den}]`) — kolik dob má takt.

Obě jsou po úsecích konstantní a nesou záznam jen tam, kde se hodnota MĚNÍ.
Chybí-li mapa metra, chová se všechno jako dřív: jedno metrum na celou píseň.
"""
from __future__ import annotations

TICKS_PER_BEAT = 960
FIRST_BEAT_TICK = 960          # GP počítá první dobu od ticku 960, ne od nuly
DEFAULT_BEATS_PER_BAR = 4


class TimeGrid:
    """Převod sekundy ↔ doba ↔ (takt, doba v taktu)."""

    def __init__(self, tempo_map: list[dict] | None,
                 beats_per_bar: int = DEFAULT_BEATS_PER_BAR,
                 meter_map: list[dict] | None = None):
        # --- tempo: (počáteční doba, bpm) ---
        segs: list[tuple[float, float]] = []
        for entry in sorted(tempo_map or [], key=lambda e: e.get("tick", 0)):
            bpm = float(entry.get("bpm", 120) or 120)
            beat = (float(entry.get("tick", FIRST_BEAT_TICK)) - FIRST_BEAT_TICK) / TICKS_PER_BEAT
            segs.append((max(0.0, beat), bpm))
        if not segs or segs[0][0] > 0:
            segs.insert(0, (0.0, segs[0][1] if segs else 120.0))
        self.segments = segs
        self._seg_start_s = [0.0]
        for i in range(1, len(segs)):
            span = segs[i][0] - segs[i - 1][0]
            self._seg_start_s.append(self._seg_start_s[-1] + span * 60.0 / segs[i - 1][1])

        # --- metrum: (počáteční takt, dob v taktu, délka taktu ve ČTVRTKÁCH) ---
        # Tempo je vždy ve čtvrťových dobách (GP i MIDI), takže délka taktu
        # se počítá ve čtvrtkách: 6/8 = 3, 12/8 = 6, 2/2 = 4. „Doba"
        # (`beat` v exportu) je proto vždy čtvrťová doba, i v x/8.
        meters: list[tuple[int, int, float]] = []
        for entry in sorted(meter_map or [], key=lambda e: e.get("bar", 1)):
            num = int(entry.get("num") or beats_per_bar or DEFAULT_BEATS_PER_BAR)
            den = int(entry.get("den") or 4)
            meters.append((max(1, int(entry.get("bar", 1))), max(1, num),
                           max(1, num) * 4.0 / max(1, den)))
        if not meters or meters[0][0] > 1:
            n = max(1, int(beats_per_bar or DEFAULT_BEATS_PER_BAR))
            meters.insert(0, (1, n, float(n)))
        self.meters = meters
        # globální doba na začátku prvního taktu každého úseku metra
        self._meter_beat0 = [0.0]
        for i in range(1, len(meters)):
            span_bars = meters[i][0] - meters[i - 1][0]
            self._meter_beat0.append(self._meter_beat0[-1] + span_bars * meters[i - 1][2])

        # Zpětná kompatibilita: kód, který zná jen jedno číslo, dostane
        # metrum PRVNÍHO taktu. Pro proměnné metrum jsou metody níž.
        self.beats_per_bar = meters[0][1]

    # --- tempo ---------------------------------------------------------
    def seconds(self, beat: float) -> float:
        """Čas v sekundách na dané globální (0-based) době."""
        i = 0
        for j, (start_beat, _bpm) in enumerate(self.segments):
            if beat >= start_beat:
                i = j
            else:
                break
        start_beat, bpm = self.segments[i]
        return self._seg_start_s[i] + (beat - start_beat) * 60.0 / bpm

    def beat(self, seconds: float) -> float:
        """Globální (0-based) doba v daném čase — inverze `seconds()`."""
        i = 0
        for j, seg_start in enumerate(self._seg_start_s):
            if seconds >= seg_start:
                i = j
            else:
                break
        start_beat, bpm = self.segments[i]
        return start_beat + (seconds - self._seg_start_s[i]) * bpm / 60.0

    def bpm_at(self, seconds: float) -> float:
        i = 0
        for j, seg_start in enumerate(self._seg_start_s):
            if seconds >= seg_start:
                i = j
            else:
                break
        return self.segments[i][1]

    # --- metrum --------------------------------------------------------
    def _meter_index(self, bar: int) -> int:
        i = 0
        for j, m in enumerate(self.meters):
            if bar >= m[0]:
                i = j
            else:
                break
        return i

    def beats_in_bar(self, bar: int) -> int:
        """Kolik NOTOVANÝCH dob má daný (1-based) takt (čitatel metra) — pro
        kreslení čar dob. Délku taktu ve čtvrtkách dává `quarters_in_bar`."""
        return self.meters[self._meter_index(bar)][1]

    def quarters_in_bar(self, bar: int) -> float:
        """Délka taktu ve čtvrťových dobách (6/8 = 3, 2/2 = 4)."""
        return self.meters[self._meter_index(bar)][2]

    def beats_before_bar(self, bar: int) -> float:
        """Globální doba (čtvrtky), na které daný takt začíná."""
        bar = max(1, int(bar))
        i = self._meter_index(bar)
        start_bar, _num, q = self.meters[i]
        return self._meter_beat0[i] + (bar - start_bar) * q

    def bar_beat(self, beat: float) -> tuple[int, float]:
        """(takt 1-based, doba v taktu 1-based, ve čtvrtkách) z globální doby."""
        beat = max(0.0, beat)
        i = 0
        for j, b0 in enumerate(self._meter_beat0):
            if beat >= b0 - 1e-9:
                i = j
            else:
                break
        return self.bar_beat_from(self.meters[i][0], beat, i)

    def bar_beat_from(self, start_bar: int, beat: float, i: int) -> tuple[int, float]:
        start_bar_i, _num, q = self.meters[i]
        offset = beat - self._meter_beat0[i]
        bars_in = int((offset + 1e-9) // q)
        return start_bar_i + bars_in, max(0.0, offset - bars_in * q) + 1.0

    def bar_start_seconds(self, bar: int) -> float:
        """Čas v sekundách, kdy daný takt začíná."""
        return self.seconds(self.beats_before_bar(bar))

    def bar_seconds(self, bar: int) -> float:
        """Délka daného taktu v sekundách (u proměnného metra se liší)."""
        return self.bar_start_seconds(bar + 1) - self.bar_start_seconds(bar)

    def bar_of(self, seconds: float) -> int:
        return self.bar_beat(self.beat(seconds))[0]

    def position(self, bar: int, beat_in_bar: float = 1.0) -> float:
        """Čas v sekundách pro (takt, doba v taktu)."""
        return self.seconds(self.beats_before_bar(bar) + (beat_in_bar - 1.0))

    def quantize(self, seconds: float, subdivision: int = 4) -> tuple[int, float, float]:
        """(takt, doba v taktu, odchylka v DOBÁCH) po přichycení na mřížku."""
        raw = self.beat(seconds)
        snapped = max(0.0, round(raw * subdivision) / subdivision)
        bar, in_bar = self.bar_beat(snapped)
        return bar, round(in_bar, 4), abs(snapped - raw)

    def iter_bars(self, until_seconds: float):
        """Postupně vydává `(číslo taktu, začátek v sekundách, délka)` až za
        `until_seconds`. Pro pravítko — délky taktů se můžou lišit, takže je
        nelze jen přičítat konstantou."""
        bar = 1
        guard = 0
        while guard < 100000:
            guard += 1
            start = self.bar_start_seconds(bar)
            if start > until_seconds:
                return
            yield bar, start, self.bar_seconds(bar)
            bar += 1


# Názvy sekcí, které se ZPÍVAJÍ. Zbytek (Intro, Solo, Outro, Mezihra…) je
# instrumentální a text do něj nepatří. Porovnává se bez ohledu na velikost
# písmen a na to, co za názvem následuje ("Verse 2", "Chorus 2").
SUNG_SECTION_WORDS = ("verse", "chorus", "refrain", "sloka", "refrén", "refren",
                      "bridge", "vocal", "zpěv", "zpev")


def is_sung_section(name: str) -> bool:
    low = (name or "").strip().lower()
    return any(w in low for w in SUNG_SECTION_WORDS)


def markers_from_measures(measures, order=None) -> list[dict]:
    """Značky sekcí z GP (`measure.header.marker`) — `[{bar, name, sung}]`.

    Tohle je jediné místo, kde se dá u tabu BEZ zpěvního textu zjistit, kde
    se zpívá a kde hraje sólo. Bez toho se text rozprostře i přes mezihry
    a na displeji visí přes instrumentální pasáže.
    """
    idxs = list(order) if order is not None else list(range(len(measures)))
    out: list[dict] = []
    for play_idx, src_idx in enumerate(idxs):
        mk = getattr(measures[src_idx].header, "marker", None)
        name = (getattr(mk, "title", "") or "").strip() if mk is not None else ""
        if name and (not out or out[-1]["name"] != name):
            out.append({"bar": play_idx + 1, "name": name, "sung": is_sung_section(name)})
    return out


def meter_map_from_measures(measures, order=None) -> list[dict]:
    """Mapa metra z GP taktů — záznam jen tam, kde se metrum MĚNÍ.

    `order` je rozbalené pořadí taktů (`expand_measure_plan`). Musí se
    použít, jinak čísla taktů odpovídají partituře, ne přehrávání — a
    u písně s repeticemi se to rozejde.
    """
    idxs = order if order is not None else range(len(measures))
    out: list[dict] = []
    prev = None
    for play_idx, src_idx in enumerate(idxs):
        ts = measures[src_idx].header.timeSignature
        cur = (int(ts.numerator), int(ts.denominator.value))
        if cur != prev:
            out.append({"bar": play_idx + 1, "num": cur[0], "den": cur[1]})
            prev = cur
    return out
