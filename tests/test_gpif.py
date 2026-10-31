"""Guitar Pro 6 (.gpx) a 7+ (.gp) — gpif_reader.py.

Nezavisle reference:
- MIDI export z Guitar Pro k tomuz souboru (Stairway to Heaven, v repu) —
  skutecne casy uderu bicich tak, jak je hraje samotny Guitar Pro,
- stejna pisen ulozena jako GP5/GP3 (Tublatanka, GP knihovna uzivatele).
Testy nad knihovnou se preskoci, kdyz knihovna na pocitaci neni.
"""
from __future__ import annotations

import bisect
import collections
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import SONGS, parse_gp

STAIRWAY_GP = SONGS / "Led Zeppelin - Stairway to Heaven" / "Led Zeppelin-Stairway to Heaven-08-13-2026.gp"
STAIRWAY_MID = STAIRWAY_GP.with_suffix(".mid")
LIB = Path(r"C:\Users\prich\Music\Guitar Pro Tabs\___czech")
LIB_GPIF = sorted(list(LIB.glob("*.gp")) + list(LIB.glob("*.gpx"))) if LIB.exists() else []


def _lib(name: str) -> Path:
    p = LIB / name
    if not p.exists():
        pytest.skip("GP knihovna neni k dispozici: %s" % name)
    return p


# ------------------------------------------------------------ kontejnery ---

def test_gp7_zip_obsahuje_gpif():
    import gpif_reader as R
    data = R.read_gpif(str(STAIRWAY_GP))
    assert data.lstrip().startswith(b"<?xml") and b"<GPIF" in data[:300]


def test_gpx_bcfz_rozbaleni():
    import gpif_reader as R
    data = R.read_gpif(str(_lib("Kabát - Bára.gpx")))
    assert data.lstrip().startswith(b"<?xml") and b"<GPIF" in data[:300]


@pytest.mark.parametrize("path", LIB_GPIF, ids=[p.name for p in LIB_GPIF])
def test_knihovna_gp6_gp7_nacteni(path):
    """Kazdy .gp/.gpx z knihovny se nacte a zadny takt nepretece (soucet
    delek beatu v hlasu <= delka taktu) — kontrola tiku a tupletu."""
    song = parse_gp(path)
    assert song.tracks and song.measureHeaders
    for t in song.tracks:
        for m in t.measures:
            for v in m.voices:
                if v.beats:
                    end = max(b.start for b in v.beats) - m.header.start
                    assert end < m.header.length, (t.name, m.header.number, end)


# --------------------------------------------- Stairway: GP8 vs MIDI z GP ---

def _midi_drums(path: Path):
    import mido
    t, out = 0.0, []
    for msg in mido.MidiFile(str(path)):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0 and msg.channel == 9:
            out.append((t, msg.note))
    return out


def _unmatched(a, b, tol=0.002):
    by_note = collections.defaultdict(list)
    for t, n in b:
        by_note[n].append(t)
    for n in by_note:
        by_note[n].sort()
    miss = []
    for t, n in a:
        lst = by_note.get(n, [])
        i = bisect.bisect_left(lst, t - tol)
        if i < len(lst) and abs(lst[i] - t) <= tol:
            lst.pop(i)
        else:
            miss.append((round(t, 3), n))
    return miss


def test_stairway_tempo_ze_zmen_v_gp():
    """GP8 ma 16 zmen tempa (71 -> ... -> 102 -> 72 BPM)."""
    import guitar_pro_viewer as G
    tm = G.build_tempo_map(parse_gp(STAIRWAY_GP))
    assert tm[0] == (960, 71) and len(tm) == 16 and tm[-1][1] == 72, tm


def test_stairway_bici_sedi_na_midi_export_z_gp():
    """Kazdy uder bicich v case do 2 ms od MIDI exportu z Guitar Pro.
    Vyjimky (zdokumentovane): ride 59 v MIDI = 51 v souboru (Ride Cymbal 1
    vs 2, stejny vzorek na SD karte) a 2 virbly, ktere v souboru nejsou
    (pridava je az export z GP)."""
    import guitar_pro_viewer as G
    if not STAIRWAY_MID.exists():
        pytest.skip("chybi MIDI export")
    song = parse_gp(STAIRWAY_GP)
    ev, _ = G.gp_drum_events(song, G.build_tempo_map(song))
    gp = [(e["time_s"], e["midi"]) for e in ev]
    midi = [(t, 51 if n == 59 else n) for t, n in _midi_drums(STAIRWAY_MID)]
    only_gp = _unmatched(gp, midi)
    only_midi = _unmatched(midi, gp)
    assert not only_gp, only_gp[:10]
    assert len(only_midi) <= 2, only_midi[:10]


def test_stairway_priraz_na_dobu_posune_hlavni_uder():
    """OnBeat priraz (64ina) zni NA dobu, hlavni uder az po nem — takt 82:
    virbl 259,023 s (priraz) a 259,068 s (hlavni), jako v MIDI z GP."""
    import guitar_pro_viewer as G
    song = parse_gp(STAIRWAY_GP)
    ev, _ = G.gp_drum_events(song, G.build_tempo_map(song))
    snares = [round(e["time_s"], 3) for e in ev if e["midi"] == 38 and 258.9 < e["time_s"] < 259.2]
    assert snares == [259.023, 259.068], snares


# -------------------------------------------- stejna pisen GP7 vs GP5/GP3 ---

def _notes(song, pred):
    out = collections.Counter()
    for t in song.tracks:
        if pred(t):
            for mi, m in enumerate(t.measures):
                for v in m.voices:
                    for b in v.beats:
                        for n in b.notes:
                            key = n.value if t.isPercussionTrack else (n.string, n.value)
                            out[(mi, b.start - m.header.start, key, n.type.name)] += 1
    return out


@pytest.mark.parametrize("new,old", [
    ("Tublatanka - Dnes.gp", "Tublatanka - Dnes.gp5"),
    ("Tublatanka - Môj Starý Dobrý Kabát.gp", "Tublatanka - Môj Starý Dobrý Kabát.gp3"),
])
def test_gp7_shodny_s_gp5_tehoz_songu(new, old):
    import guitarpro
    a = parse_gp(_lib(new))
    b = guitarpro.parse(str(_lib(old)), encoding="cp1250")
    assert len(a.measureHeaders) == len(b.measureHeaders)
    assert a.tempo == b.tempo
    sig = lambda s: [(h.timeSignature.numerator, h.timeSignature.denominator.value)
                     for h in s.measureHeaders]
    assert sig(a) == sig(b)
    bass = lambda t: not t.isPercussionTrack and len(t.strings) == 4
    assert _notes(a, bass) == _notes(b, bass), "basa (struna, prazec, pozice) se lisi"
    da, db = _notes(a, lambda t: t.isPercussionTrack), _notes(b, lambda t: t.isPercussionTrack)
    # Bici se smi lisit JEN v taktech s prirazy: GP7 je zapisuje jako priraz
    # (nezabira cas taktu), starsi soubor jako obycejnou 32inu.
    grace_bars = {mi for t in a.tracks if t.isPercussionTrack
                  for mi, m in enumerate(t.measures) for v in m.voices
                  for bt in v.beats if getattr(bt, "isGrace", False)}
    diff_bars = {k[0] for k in (da - db) + (db - da)}
    assert diff_bars <= grace_bars, (sorted(diff_bars - grace_bars), sorted(grace_bars))


# --------------------------------------------------------- repetice ---

def _fake_measures(spec):
    """spec: [(isRepeatOpen, repeatClose, repeatAlternative)] -> „takty" s hlavickou."""
    return [SimpleNamespace(header=SimpleNamespace(isRepeatOpen=o, repeatClose=c,
                                                   repeatAlternative=a,
                                                   direction=None, fromDirection=None))
            for o, c, a in spec]


def test_zakonceni_1_3_a_2_4_s_repetici_na_obou():
    """Tublatanka – Môj stary dobry kabat (GP7): takty A B, pak zakonceni
    1.+3. a 2.+4., repetice (3 opakovani) na obou. Spravne: A B Z1 A B Z2
    A B Z1 A B Z2 a dal. Driv nezahrany takt Z1 skocil zpet a Z2 nezaznel."""
    import guitar_pro_viewer as G
    ms = _fake_measures([(True, -1, 0), (False, -1, 0), (False, 3, 0b0101),
                         (False, 3, 0b1010), (False, -1, 0)])
    assert G.expand_measure_order(ms) == [0, 1, 2, 0, 1, 3, 0, 1, 2, 0, 1, 3, 4]


def test_zakonceni_klasicke_1_2():
    """Let It Be styl: repetice na 1. zakonceni, 2. zakonceni bez repetice."""
    import guitar_pro_viewer as G
    ms = _fake_measures([(True, -1, 0), (False, 1, 0b01), (False, -1, 0b10), (False, -1, 0)])
    assert G.expand_measure_order(ms) == [0, 1, 0, 2, 3]


# ------------------------------------------------------------- text ---

def test_spojeni_slabik():
    import guitar_pro_viewer as G
    assert G.join_syllables(["la-", "dy", "who's", "Vez-", "mu", "-", "rock-and-roll"]) \
        == "lady who's Vezmu rock-and-roll"


def _beat_texts(song):
    return [b.text for t in song.tracks for m in t.measures for v in m.voices
            for b in v.beats if isinstance(b.text, str) and b.text]


def test_stairway_text_po_beatech():
    """GP8 nese text jako slabiky u beatu zpevni stopy."""
    texts = _beat_texts(parse_gp(STAIRWAY_GP))
    assert texts[:4] == ["There's", "a", "la-", "dy"], texts[:4]


def test_gpx_cp1250_diakritika_opravena():
    """Kabat – Pohoda.gpx: text ulozeny v cp1250 jako latin-1 (tì, v\x9au)."""
    texts = _beat_texts(parse_gp(_lib("Kabát - Pohoda.gpx")))
    joined = " ".join(texts)
    assert not any("\x80" <= ch <= "\x9f" for ch in joined)
    assert "tě" in texts and "Když" in texts and "vůl" in texts, texts[:12]
