"""
format_migrate.py — převod karaoke JSONu z `format_version: 2` na `schemaVersion: 1`.

Bez GUI, bez závislosti na Qt. Dvě rozhraní:

    from format_migrate import migrate
    novy, varovani = migrate(stary_dict)

    python format_migrate.py vstup.json [-o vystup.json]

Co se mění a proč (podrobně v návrhu architektury):

- **Konec se nikde nezapisuje.** Sekce končí tam, kde začíná následující;
  jediná délka v souboru je `meta.durationMs`. Proto mizí `duration_s`
  u textu/akordů/bicích. Délka basové noty zůstává jako `durationBeats`
  — to není hranice, ale skutečná hudební vlastnost.
- **Obsah je v souboru jednou.** `karaoke_lines` byla celá odvozená
  (text tam byl podruhé, ve `words[]` potřetí) — ruší se.
- **Žádná samostatná "displej" vrstva.** Starší verze formátu měla navíc
  `display_timeline`/`screens[]` — explicitní seznam "co a kdy zobrazit",
  budovaný jako zamrzlá kopie toho, co šlo stejně odvodit z
  `lyrics_timeline`/`chords_timeline`. V praxi se to rozcházelo (dva různé
  algoritmy skládání dávaly různé výsledky na týchž datech) a firmware to
  přitom umí spočítat samo za běhu: je-li aktivní zpívaný řádek → text
  (+akordy patřící k němu), není-li řádek, ale zní akord → jen akord,
  jinak nic. Řádek zůstává aktivní od svého `bar`/`beat` do začátku
  dalšího — čtenář tak dostává přirozený předstih zdarma, bez zvláštního
  pole v souboru.
- **Autorita je `bar` + `beat`**, ne sekundy. Čas v ms se dopočítá
  z `tempoMap`. Výjimka jsou nahrávky, které nehrají na klik: když se
  událost po kvantizaci mine o víc než `MAX_GRID_DRIFT_BEATS` dob,
  dostane navíc `tMsOverride` s přesným časem.
- **Count-in se odečítá.** Starý editor posouval celou časovou osu o
  `meta.count_in_s` dopředu; v novém modelu odpočet dopočítá firmware
  z `countInBars` a časy začínají na reálné nule.

**Co se hádalo, je v souboru označené.** Starý formát spoustu věcí nenese
(strukturu sekcí, tóninu), takže je konvertor odvozuje — a nesmí přitom
předstírat jistotu. Proto:

- `sections[].source` — `"manual"` tam, kde to určil člověk (marker
  `section`, marker `1.`/`R:` v textu), `"generated"` tam, kde to
  konvertor odhadl (refrén z opakování textu, dělení v pauzách).
- `meta.migrationNotes` — seznam všeho, co se při převodu odhadlo nebo
  nepřevedlo, uložený přímo v souboru. Konzole se zavře, tohle zůstane.

Editor pak umí zvýraznit, co po převodu potřebuje projít ručně.
"""
from __future__ import annotations

from music_grid import (TimeGrid, TICKS_PER_BEAT, FIRST_BEAT_TICK,
                        DEFAULT_BEATS_PER_BAR)

import argparse
import json
import os
import sys

QUANTIZE = 4                   # dělení doby při kvantizaci (4 = šestnáctiny)

# Kdy událost NENÍ na mřížce a potřebuje přesný `tMsOverride`.
#
# Práh je v DOBÁCH, ne v milisekundách, a schválně velkorysý. Důvod je
# naměřený: zdrojové soubory nesou nasčítané zaokrouhlení. `Jasná Zpráva`
# má 65 BPM (doba 0,923077 s) a časy uložené na 3 desetinná místa, takže
# odchylka od mřížky roste s časem — kolem 270. sekundy dosahuje 22 ms.
# S prahem 10 ms by `tMsOverride` dostalo 47,6 % událostí a zaokrouhlovací
# šum by se natrvalo zapekl do dat, přestože ta hudba na mřížce JE.
#
# 0,05 doby je při 65 BPM 46 ms a při 120 BPM 25 ms — pohodlně nad
# zaokrouhlením, a přitom hluboko pod tím, o kolik se míjí skutečná volná
# nahrávka (tam jdou odchylky do stovek ms, viz kapitola o volných
# nahrávkách v návrhu).
MAX_GRID_DRIFT_BEATS = 0.05
SECTION_GAP_BARS = 2.0         # pauza, která bez markerů ukončí sekci
DEFAULT_SAMPLE_SET = "kit_default_v1"


def _load_drum_samples(base_dir: str) -> dict[str, str]:
    """Jméno GM bubnu -> jméno vzorku (basename bez přípony). Jediný
    existující zdroj tohohle mapování je `drum_samples.json`, který appka
    používá k přehrávání — nezavádíme druhý číselník, který by se rozešel."""
    path = os.path.join(base_dir, "drum_samples.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for drum, sample_path in (raw or {}).items():
        if isinstance(sample_path, str) and sample_path:
            out[drum] = os.path.splitext(os.path.basename(sample_path))[0]
    return out


def _pos(grid: TimeGrid, seconds: float) -> dict:
    """Společný tvar pozice události: `bar` + `beat`, a když se to na
    mřížku netrefí (volná nahrávka), navíc přesné `tMsOverride`."""
    bar, beat, drift_beats = grid.quantize(seconds, QUANTIZE)
    out = {"bar": bar, "beat": beat}
    if drift_beats > MAX_GRID_DRIFT_BEATS:
        out["tMsOverride"] = int(round(seconds * 1000))
    return out


def _build_sections(klines: list[dict], grid: TimeGrid,
                    warnings: list[str]) -> list[dict]:
    """Hranice sekcí, čtyři strategie od nejspolehlivější po nouzovou.
    Vrací [{name, start_s}].

    Pořadí není libovolné — vyplynulo z měření na reálných souborech:

    1. **`karaoke_lines[].section`** — hotové markery (Let It Be 5 z 21,
       Jasná Zpráva 6 z 20).
    2. **Markery v textu řádku** přes `_section_of()` (`1.`, `2.`, `R:`…).
       Ne každý soubor má `section` vyplněné, i když markery v textu jsou —
       `Dej_mi_víc_své_lásky.json` má 0 v `section`, ale 6 v textu.
    3. **Opakující se text = refrén.** Bez markerů (Bon Jovi 0 ze 47) se
       pozná podle toho, že se řádky refrénu v písni opakují: souvislý běh
       opakovaných řádků je „Refrén", běh neopakovaných „Sloka N".
    4. Jediná sekce + poctivé varování, že strukturu je potřeba dodělat ručně.

    Pauzy mezi řádky se jako HLAVNÍ kritérium nedají použít vůbec: web
    import klade řádky na pravidelnou mřížku, takže konec řádku se rovná
    začátku dalšího a mezera je všude nula. Zůstávají jen jako doplňkové
    dělení navíc (u GP importu instrumentální mezihra pauzu mít může)."""
    lines = sorted((l for l in klines if l.get("start_s") is not None),
                   key=lambda l: float(l["start_s"]))
    if not lines:
        warnings.append("Žádné karaoke řádky — vznikla jediná sekce přes celou píseň.")
        return [{"name": "Píseň", "start_s": 0.0, "source": "generated"}]

    from collections import Counter
    try:
        from timeline_editor import _section_of      # jediná kopie té regulérky
    except Exception:                                 # pragma: no cover
        _section_of = lambda _t: None

    texts = [(l.get("text") or "").strip() for l in lines]
    bounds: list[dict] = []

    marked = [l for l in lines if l.get("section")]
    if marked:
        warnings.append("Sekce z markerů section (%d z %d řádků)."
                        % (len(marked), len(lines)))
        for l in marked:
            bounds.append({"name": str(l["section"]), "start_s": float(l["start_s"]),
                           "source": "manual"})
    else:
        from_text = [(i, _section_of(t)) for i, t in enumerate(texts)]
        from_text = [(i, n) for i, n in from_text if n]
        if from_text:
            warnings.append("Sekce z markerů v textu řádků (%d z %d) — pole "
                            "section bylo prázdné." % (len(from_text), len(lines)))
            for i, name in from_text:
                # Marker `1.`/`R:` napsal do textu člověk, takže je to jeho
                # rozhodnutí, jen uložené jinde než v poli `section`.
                bounds.append({"name": name, "start_s": float(lines[i]["start_s"]),
                               "source": "manual"})
        else:
            counts = Counter(t for t in texts if t)
            repeated = [bool(t) and counts[t] > 1 for t in texts]
            runs: list[tuple[bool, int]] = []
            for i, r in enumerate(repeated):
                if not runs or runs[-1][0] != r:
                    runs.append((r, i))
            if any(repeated) and len(runs) > 1:
                warnings.append("Žádné markery — sekce odvozené z opakování textu "
                                "(%d opakovaných řádků = refrén)." % sum(repeated))
                verse = 0
                for is_chorus, i in runs:
                    if is_chorus:
                        name = "Refrén"
                    else:
                        verse += 1
                        name = "Sloka %d" % verse
                    # Odhad z opakování textu — člověk to nepotvrdil.
                    bounds.append({"name": name, "start_s": float(lines[i]["start_s"]),
                                   "source": "generated"})
            else:
                warnings.append("Strukturu se nepodařilo rozpoznat (žádné markery "
                                "ani opakování textu) — jediná sekce, rozděl ji "
                                "v editoru ručně.")
                bounds.append({"name": "Píseň", "start_s": float(lines[0]["start_s"]),
                               "source": "generated"})

    # Doplňkové dělení v dlouhých pauzách (instrumentální mezihra). U web
    # importu se nikdy neuplatní — tam je mezera mezi řádky vždycky nula.
    gap_s = SECTION_GAP_BARS * grid.beats_per_bar * 60.0 / grid.segments[0][1]
    known = {round(b["start_s"], 3) for b in bounds}
    extra = 0
    prev_end = None
    for l in lines:
        start = float(l["start_s"])
        if prev_end is not None and (start - prev_end) > gap_s and round(start, 3) not in known:
            bounds.append({"name": "Mezihra", "start_s": start, "source": "generated"})
            known.add(round(start, 3))
            extra += 1
        prev_end = max(prev_end or 0.0, float(l.get("end_s", start)))
    if extra:
        warnings.append("Navíc %d hranic v pauzách delších než %.1f s." % (extra, gap_s))

    bounds.sort(key=lambda b: b["start_s"])
    # První sekce musí začít na začátku písně, jinak by úvodní akordy/bicí
    # neměly kam patřit (sekce nemá konec, takže „mimo sekce" neexistuje).
    bounds[0]["start_s"] = 0.0
    return bounds


def migrate(data: dict) -> tuple[dict, list[str]]:
    """Vrátí (nový dokument, varování). Vstup nemění."""
    warnings: list[str] = []
    if data.get("schemaVersion"):
        raise ValueError("Vstup už je schemaVersion %s — konvertor je jen z "
                         "format_version 2." % data["schemaVersion"])
    if int(data.get("meta", {}).get("format_version", 0) or 0) != 2:
        warnings.append("Vstup nemá format_version 2 — převádím podle struktury, "
                        "ne podle verze.")

    meta = dict(data.get("meta") or {})
    beats_per_bar = int(meta.get("beats_per_measure") or DEFAULT_BEATS_PER_BAR)
    if not meta.get("beats_per_measure"):
        warnings.append("meta.beats_per_measure chybí — použit default %d/4."
                        % DEFAULT_BEATS_PER_BAR)
    grid = TimeGrid(data.get("tempo_map") or [], beats_per_bar,
                    meter_map=data.get("meter_map"))

    # Count-in: stará osa je o tolik posunutá dopředu, nový model začíná na nule.
    shift = float(meta.get("count_in_s") or 0.0)
    count_in_bars = int(meta.get("count_in_bars") or 0)

    def t(seconds) -> float:
        return float(seconds or 0.0) - shift

    below_zero = 0
    dropped_count_in = 0

    def in_count_in(seconds) -> bool:
        """Událost leží v pásmu odpočtu, tedy PŘED skutečným začátkem písně.

        Ve starém formátu byla celá osa posunutá o `count_in_s` dopředu a do
        vzniklé mezery se vkládala klikací stopa metronomu. Nový model
        odpočet neukládá — firmware si ho dopočítá z `countInBars` — takže
        tyhle údery nemají co převádět. Ořezat je na nulu (dřívější chování)
        znamená slisovat desítky úderů na jeden okamžik: v reálném souboru
        jich takhle skončilo 29 na čase 0.
        """
        return shift > 0 and float(seconds or 0.0) < shift - 0.001

    def pos(seconds) -> dict:
        nonlocal below_zero
        v = t(seconds)
        if v < -0.001:
            below_zero += 1
            v = 0.0
        return _pos(grid, max(0.0, v))

    klines = data.get("karaoke_lines") or []
    bounds = _build_sections(klines, grid, warnings)
    starts = [max(0.0, float(b["start_s"]) - shift) for b in bounds]
    starts[0] = 0.0

    def section_index(seconds: float) -> int:
        v = max(0.0, t(seconds))
        idx = 0
        for i, s in enumerate(starts):
            if v >= s - 1e-6:
                idx = i
        return idx

    sections = [{"name": b["name"], "startBar": grid.bar_of(s),
                 "source": b.get("source", "generated"),
                 "lyrics": [], "chords": [], "drums": [], "bass": [], "tab": []}
                for b, s in zip(bounds, starts)]

    # --- text ---
    for ev in sorted(data.get("lyrics_timeline") or [], key=lambda e: e.get("time_s", 0)):
        if in_count_in(ev.get("time_s")):
            dropped_count_in += 1
            continue
        item = pos(ev.get("time_s"))
        item["text"] = ev.get("text", "")
        if ev.get("source"):
            item["source"] = ev["source"]
        sections[section_index(ev.get("time_s"))]["lyrics"].append(item)

    # --- akordy (napříč stopami, bez duplicit) ---
    seen_chords: set[tuple[int, float, str]] = set()
    merged = 0
    for ev in sorted(data.get("chords_timeline") or [], key=lambda e: e.get("time_s", 0)):
        if in_count_in(ev.get("time_s")):
            dropped_count_in += 1
            continue
        item = pos(ev.get("time_s"))
        chord = ev.get("chord", "")
        key = (item["bar"], item["beat"], chord)
        if key in seen_chords:
            merged += 1
            continue
        seen_chords.add(key)
        item["chord"] = chord
        if ev.get("source"):
            item["source"] = ev["source"]
        sections[section_index(ev.get("time_s"))]["chords"].append(item)
    if merged:
        warnings.append("Sloučeno %d akordů, které zněly současně na víc stopách "
                        "(nový model má jeden seznam akordů na sekci)." % merged)

    # --- bicí ---
    samples = _load_drum_samples(os.path.dirname(os.path.abspath(__file__)))
    unmapped: set[str] = set()
    for ev in sorted(data.get("drums_timeline") or [], key=lambda e: e.get("time_s", 0)):
        if in_count_in(ev.get("time_s")):
            dropped_count_in += 1
            continue
        item = pos(ev.get("time_s"))
        drum = ev.get("drum", "")
        sample = samples.get(drum)
        if sample is None:
            unmapped.add(drum)
            sample = drum          # radši původní jméno než tiše zahodit úder
        item["sample"] = sample
        if ev.get("source"):
            item["source"] = ev["source"]
        sections[section_index(ev.get("time_s"))]["drums"].append(item)
    if unmapped:
        warnings.append("Bicí bez vzorku v drum_samples.json (%s) — ponecháno "
                        "původní jméno, doplň mapování." % ", ".join(sorted(unmapped)))

    # --- basa (délka zůstává, je to hudební vlastnost, ne hranice) ---
    for ev in sorted(data.get("bass_timeline") or [], key=lambda e: e.get("time_s", 0)):
        if in_count_in(ev.get("time_s")):
            dropped_count_in += 1
            continue
        item = pos(ev.get("time_s"))
        item["note"] = ev.get("note_name", "")
        # `midi`/`string`/`fret` se ukládají PŘESNĚ (ne jen odvoditelně
        # z `note`, které je bez oktávy nejednoznačné — "D" může být
        # kterékoliv D) — jinak by se basová linka po uložení a znovu-
        # načtení přehrála v NĚJAKÉ oktávě, ale ne nutně té správné (viz
        # `_play_bass_note`/`render_bass_mixdown` v timeline_editor.py,
        # obojí čte `midi` přímo).
        if ev.get("midi") is not None:
            item["midi"] = int(ev["midi"])
        if ev.get("string") is not None:
            item["string"] = int(ev["string"])
        if ev.get("fret") is not None:
            item["fret"] = int(ev["fret"])
        dur_s = float(ev.get("duration_s") or 0.0)
        if dur_s > 0:
            b0 = grid.beat(max(0.0, t(ev.get("time_s"))))
            item["durationBeats"] = round(grid.beat(max(0.0, t(ev.get("time_s")) + dur_s)) - b0, 4)
        if ev.get("source"):
            item["source"] = ev["source"]
        sections[section_index(ev.get("time_s"))]["bass"].append(item)

    if dropped_count_in:
        warnings.append("Zahozeno %d událostí z pásma odpočtu (klikací stopa "
                        "metronomu) — nový model odpočet neukládá, firmware si "
                        "ho dopočítá z countInBars." % dropped_count_in)
    if below_zero:
        warnings.append("%d událostí vyšlo po odečtení odpočtu do záporného času "
                        "— přichyceny na nulu." % below_zero)

    n_events = sum(len(s[k]) for s in sections for k in ("lyrics", "chords", "drums", "bass"))
    n_override = sum(1 for s in sections for k in ("lyrics", "chords", "drums", "bass")
                     for e in s[k] if "tMsOverride" in e)
    if n_override:
        warnings.append("%d z %d událostí (%.1f %%) nesedí na mřížku a nese "
                        "tMsOverride — u nahrávky bez klikačky je to v pořádku, "
                        "u písně na klik to ukazuje na špatné tempo."
                        % (n_override, n_events, 100.0 * n_override / max(1, n_events)))

    # --- délka písně ---
    ends = [t(meta.get("duration_seconds"))] if meta.get("duration_seconds") else []
    for key in ("lyrics_timeline", "chords_timeline", "drums_timeline", "bass_timeline"):
        for ev in data.get(key) or []:
            ends.append(t(ev.get("time_s")) + float(ev.get("duration_s") or 0.0))
    duration_ms = int(round(max(ends + [0.0]) * 1000))

    new_meta = {
        "title": meta.get("title", ""),
        "artist": meta.get("artist", ""),
        "durationMs": duration_ms,
        "countInBars": count_in_bars,
        "sampleSet": DEFAULT_SAMPLE_SET,
    }
    if meta.get("audio_file"):
        new_meta["audioFile"] = meta["audio_file"]
        new_meta["audioOffsetMs"] = int(round((float(meta.get("audio_offset_s") or 0.0) - shift) * 1000))
        if meta.get("audio_stretch") not in (None, 1.0):
            new_meta["audioStretch"] = float(meta["audio_stretch"])
    warnings.append("originalKey se ve format_version 2 nevyskytuje — doplň tóninu ručně.")

    ts = str(meta.get("time_signature") or "")
    num, den = (beats_per_bar, 4)
    if "/" in ts:
        try:
            num, den = (int(x) for x in ts.split("/", 1))
        except ValueError:
            pass

    out = {
        "schemaVersion": 1,
        "meta": new_meta,
        "tempoMap": [{"bar": grid.bar_beat(b)[0], "beat": grid.bar_beat(b)[1], "bpm": bpm}
                     for b, bpm in grid.segments],
        # Metrum po úsecích — jeden záznam na ZMĚNU. Když zdroj mapu nemá,
        # zůstane jediný záznam jako dřív.
        "meterMap": ([dict(e) for e in data["meter_map"]] if data.get("meter_map")
                     else [{"bar": 1, "num": num, "den": den}]),
        "pickupBeats": 0,
        "sections": sections,
    }
    # Co konvertor odhadl nebo nedokázal převést, musí zůstat vidět i po
    # zavření konzole — jinak se na to zapomene. Editor to ukáže jako
    # seznam „zkontroluj ručně"; `source: "generated"` u sekcí říká,
    # KTERÝCH konkrétních položek se to týká.
    out["meta"]["migrationNotes"] = list(warnings)
    if data.get("linear_map"):
        out["linearMap"] = [{"bar": e.get("bar"), "sourceMeasure": e.get("source_measure"),
                             "pass": e.get("pass")} for e in data["linear_map"]]
    return out, warnings


# --------------------------------------------------------------------------
# Zpětný převod: nový formát -> vnitřní tvar, se kterým pracuje editor
# --------------------------------------------------------------------------
# Editor počítá celou časovou osu v SEKUNDÁCH (pixely na sekundu, vlnovka,
# playhead, přehrávání) a to se měnit nemá — měnit se má formát souboru, ne
# způsob kreslení. Nový model proto žije jen na hranici vstupu a výstupu:
# tady se rozbalí do sekund, `to_json()` ho zase složí zpátky.
#
# Konce se v novém formátu neukládají, takže se tu DOPOČÍTÁVAJÍ: prvek končí
# tam, kde začíná následující. Tím je pravidlo „žádný konec" vidět i uvnitř
# editoru, ne až při ukládání.

VOCAL_TI, DRUMS_TI, BASS_TI = 1, 2, 3


def _abs_seconds(grid: TimeGrid, ev: dict) -> float:
    """Čas události: `tMsOverride` má přednost před dopočtem z mřížky."""
    if "tMsOverride" in ev:
        return float(ev["tMsOverride"]) / 1000.0
    # POZOR: ne (bar-1)*beats_per_bar — u proměnného metra mají takty
    # různý počet dob, takže začátek taktu musí spočítat mřížka.
    g = grid.beats_before_bar(int(ev.get("bar", 1))) + (float(ev.get("beat", 1.0)) - 1.0)
    return grid.seconds(max(0.0, g))


def _derive_durations(events: list[dict], grid: TimeGrid, default_beats: float = 1.0) -> None:
    """Doplní `duration_s` tak, že prvek trvá do začátku dalšího. Poslední
    dostane `default_beats` doby. Tohle NENÍ uložená hodnota — je to jen to,
    co editor potřebuje k vykreslení šířky obdélníku."""
    tail = default_beats * 60.0 / grid.segments[-1][1]
    for i, ev in enumerate(events):
        nxt = events[i + 1]["time_s"] if i + 1 < len(events) else ev["time_s"] + tail
        ev["duration_s"] = round(max(0.05, nxt - ev["time_s"]), 3)


_NOTE_LETTERS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _guess_bass_midi(note_name: str) -> int:
    """Odhad MIDI noty ze samotného jména BEZ oktávy (např. "D") — jen
    záložní cesta pro starší soubory uložené PŘED tím, než `migrate()`
    začal ukládat `midi` přímo (jméno bez oktávy je nejednoznačné, nejde
    rekonstruovat přesně). Vybere oktávu nejblíž basovému rejstříku
    (kolem E2/midi 40), aby přehrávání (`_play_bass_note`) alespoň znělo
    ve správném rozsahu, i když ne nutně na přesné původní výšce."""
    name = (note_name or "").strip().upper().replace("B#", "C")
    try:
        idx = _NOTE_LETTERS.index(name)
    except ValueError:
        return 40
    return min((idx + 12 * o for o in range(6)), key=lambda m: abs(m - 40))


def to_editor_model(doc: dict) -> dict:
    """`schemaVersion: 1` -> vnitřní (plochý, sekundový) tvar editoru."""
    if int(doc.get("schemaVersion", 0) or 0) != 1:
        raise ValueError("to_editor_model umí jen schemaVersion 1")

    meta = doc.get("meta") or {}
    meter = (doc.get("meterMap") or [{}])[0]
    beats_per_bar = int(meter.get("num") or DEFAULT_BEATS_PER_BAR)
    tempos = doc.get("tempoMap") or [{"bar": 1, "beat": 1.0, "bpm": 120}]
    # TimeGrid čte staré `tempo_map` (ticky), tak mu ho z bar/beat složíme
    meter_map = [dict(e) for e in (doc.get("meterMap") or [])] or [
        {"bar": 1, "num": beats_per_bar, "den": int(meter.get("den") or 4)}]
    # Mřížka se staví DVAKRÁT: nejdřív hrubá jen na převod tempa z bar/beat
    # na ticky, pak ostrá i s metrem. Bez toho by se první převod počítal
    # konstantním metrem a u proměnného by tempo sedlo na špatný takt.
    rough = TimeGrid([{"tick": FIRST_BEAT_TICK, "bpm": float(tempos[0].get("bpm", 120))}],
                     beats_per_bar, meter_map=meter_map)
    tempo_map = []
    for e in tempos:
        g = rough.beats_before_bar(int(e.get("bar", 1))) + (float(e.get("beat", 1.0)) - 1.0)
        tempo_map.append({"tick": int(round(g * TICKS_PER_BEAT)) + FIRST_BEAT_TICK,
                          "bpm": float(e.get("bpm", 120))})
    grid = TimeGrid(tempo_map, beats_per_bar, meter_map=meter_map)

    lyrics: list[dict] = []
    chords: list[dict] = []
    drums: list[dict] = []
    bass: list[dict] = []
    rev_samples = {}
    for drum, sample in _load_drum_samples(os.path.dirname(os.path.abspath(__file__))).items():
        rev_samples.setdefault(sample, drum)

    for sec in doc.get("sections") or []:
        for ev in sec.get("lyrics") or []:
            item = {"time_s": round(_abs_seconds(grid, ev), 3), "text": ev.get("text", ""),
                    "track_index": VOCAL_TI}
            if ev.get("source"):
                item["source"] = ev["source"]
            lyrics.append(item)
        for ev in sec.get("chords") or []:
            item = {"time_s": round(_abs_seconds(grid, ev), 3), "chord": ev.get("chord", ""),
                    "track_index": VOCAL_TI}
            if ev.get("source"):
                item["source"] = ev["source"]
            chords.append(item)
        for ev in sec.get("drums") or []:
            sample = ev.get("sample", "")
            item = {"time_s": round(_abs_seconds(grid, ev), 3),
                    "drum": rev_samples.get(sample, sample), "duration_s": 0.2,
                    "track_index": DRUMS_TI}
            if ev.get("source"):
                item["source"] = ev["source"]
            drums.append(item)
        for ev in sec.get("bass") or []:
            item = {"time_s": round(_abs_seconds(grid, ev), 3),
                    "note_name": ev.get("note", ""), "track_index": BASS_TI}
            midi = ev.get("midi")
            item["midi"] = int(midi) if midi is not None else _guess_bass_midi(ev.get("note", ""))
            if ev.get("string") is not None:
                item["string"] = int(ev["string"])
            if ev.get("fret") is not None:
                item["fret"] = int(ev["fret"])
            if ev.get("durationBeats"):
                item["duration_s"] = round(float(ev["durationBeats"]) * 60.0
                                           / grid.segments[0][1], 3)
            if ev.get("source"):
                item["source"] = ev["source"]
            bass.append(item)

    for seq in (lyrics, chords, drums, bass):
        seq.sort(key=lambda e: e["time_s"])
    _derive_durations(lyrics, grid, beats_per_bar)
    _derive_durations(chords, grid, 1.0)
    for ev in bass:
        ev.setdefault("duration_s", 0.3)

    # `line` = index textového řádku; akordy dědí řádek, do jehož času spadají
    for i, ev in enumerate(lyrics):
        ev["line"] = i
        ev["line_start"] = True
    for ev in chords:
        ev["line"] = next((l["line"] for l in lyrics
                           if l["time_s"] <= ev["time_s"] < l["time_s"] + l["duration_s"]), None)

    duration_s = float(meta.get("durationMs", 0) or 0) / 1000.0

    # --- karaoke řádky (editor je čte při prvním rozložení) ---
    klines = []
    for l in lyrics:
        klines.append({"line": l["line"], "start_s": l["time_s"],
                       "end_s": round(l["time_s"] + l["duration_s"], 3),
                       "chords": [c["chord"] for c in chords if c.get("line") == l["line"]],
                       "text": l["text"],
                       "words": [{"time_s": l["time_s"], "duration_s": l["duration_s"],
                                  "text": l["text"], "line": l["line"],
                                  "track_index": VOCAL_TI}],
                       "track_index": VOCAL_TI})

    # Stopy se skládají VŽDY všechny tři, i když jsou zatím prázdné. Dělat je
    # jen podle obsahu vypadá úsporně, ale znamená to, že do prázdné stopy
    # nejde nic přidat — odpočet si nemá kam odložit klikací údery a tiše
    # neudělá nic (chyceno testem `set_count_in`).
    tracks = [{"index": VOCAL_TI, "name": meta.get("title") or "Zpěv",
               "type": "vocal", "is_drums": False},
              {"index": DRUMS_TI, "name": "Bicí", "type": "drums", "is_drums": True},
              {"index": BASS_TI, "name": "Basa", "type": "bass", "is_drums": False}]

    out = {
        "meta": {
            "format_version": 2,
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "tempo_bpm": int(round(grid.segments[0][1])),
            "ticks_per_beat": TICKS_PER_BEAT,
            "beats_per_measure": beats_per_bar,
            "time_signature": "%d/%d" % (beats_per_bar, int(meter.get("den") or 4)),
            "has_line_structure": True,
            # Odpočet už osu NEPOSOUVÁ — v novém modelu je to jen počet taktů
            # a firmware si ho dopočítá sám (`count_in_s` proto zůstává nula).
            "count_in_bars": int(meta.get("countInBars") or 0),
            "count_in_s": 0.0,
            "duration_seconds": duration_s,
        },
        "tempo_map": tempo_map,
        "meter_map": meter_map,
        "tracks": tracks,
        "lyrics_timeline": lyrics,
        "chords_timeline": chords,
        "drums_timeline": drums,
        "karaoke_lines": klines,
    }
    if bass:
        out["bass_timeline"] = bass
    for src, dst in (("audioFile", "audio_file"), ("audioStretch", "audio_stretch")):
        if meta.get(src) is not None:
            out["meta"][dst] = meta[src]
    if meta.get("audioOffsetMs") is not None:
        out["meta"]["audio_offset_s"] = float(meta["audioOffsetMs"]) / 1000.0
    if meta.get("migrationNotes"):
        out["meta"]["migration_notes"] = list(meta["migrationNotes"])
    if doc.get("linearMap"):
        out["linear_map"] = [{"bar": e.get("bar"), "source_measure": e.get("sourceMeasure"),
                              "pass": e.get("pass")} for e in doc["linearMap"]]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Převod karaoke JSONu na schemaVersion 1.")
    ap.add_argument("input", help="vstupní JSON (format_version 2)")
    ap.add_argument("-o", "--output", help="výstupní JSON (výchozí: <vstup>.v1.json)")
    args = ap.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    try:
        new, warnings = migrate(data)
    except ValueError as ex:
        print("CHYBA: %s" % ex, file=sys.stderr)
        return 2

    out_path = args.output or os.path.splitext(args.input)[0] + ".v1.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=2, ensure_ascii=False)

    print("%s -> %s" % (args.input, out_path))
    print("  sekcí: %d, délka: %.1f s"
          % (len(new["sections"]), new["meta"]["durationMs"] / 1000))
    for w in warnings:
        print("  ! %s" % w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
