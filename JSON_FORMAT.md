# Karaoke / Tab JSON — specifikace formátu (`format_version: 2`)

Tento dokument je **závazná specifikace** výstupního JSONu. Slouží jako zadání
pro AI/vývojáře, který píše **parser** (typicky na ESP32 čtoucí z SD karty, nebo
desktopová aplikace zobrazující text + akordy + tabulatury).

> Pravidlo pro parser: **čti podle `meta.format_version`.** Když nesouhlasí
> s verzí, kterou parser umí, odmítni soubor / spusť migraci. Neznámé klíče
> parser **ignoruje** (dopředná kompatibilita).

---

## 1. Kódování a obecné konvence

- Soubor je **UTF-8** (bez závislosti na BOM). Diakritika je v textu přímo.
- Časy (`*_s`) jsou **sekundy** od začátku skladby (`float`, zaokrouhleno).
- `tick` jsou Guitar Pro tiky. **`ticks_per_beat = 960`**, první beat začíná
  na ticku `960` (ne 0). Převod: `time_s = (tick - 960)/960 * 60/bpm`
  (při konstantním tempu; jinak viz `tempo_map`).
- `track_index` je **1-based** a odkazuje na `tracks[].index`. Tím se stopa
  v každém eventu **neopakuje jménem** — jen malým celým číslem.

---

## 2. Dva producenti, jedno schéma

| Sekce | GP viewer (`guitar_pro_viewer.py`) | Web import (`web_import.py`) |
|-------|-----------------------------------|------------------------------|
| `meta`, `tempo_map`, `tracks` | ✅ | ✅ |
| `tracks[].beats[]` (tabulatury) | ✅ plné (struny/pražce/efekty) | ❌ (`has_tab: false`) |
| `lyrics_timeline`, `chords_timeline`, `karaoke_lines` | ✅ | ✅ |

Parser tedy **nesmí předpokládat**, že `tracks[].beats[]` existuje — musí
zkontrolovat `tracks[].has_tab` (u web importu chybí a je `false`).

---

## 3. Kořenová struktura

```jsonc
{
  "meta": { ... },
  "tempo_map": [ { "tick": 960, "bpm": 120 } ],
  "tracks": [ { ... } ],
  "lyrics_timeline": [ { ... } ],
  "chords_timeline": [ { ... } ],
  "karaoke_lines":  [ { ... } ]
}
```

### 3.1 `meta`

| klíč | typ | pozn. |
|------|-----|-------|
| `format_version` | int | **vždy `2`**. Rozhoduje o parsování. |
| `title` | str | název skladby |
| `artist` | str | interpret |
| `album` | str | (jen GP viewer) |
| `tempo_bpm` | int | základní tempo |
| `ticks_per_beat` | int | vždy `960` |
| `total_measures` | int | počet taktů |
| `track_count` | int | počet položek v `tracks[]` |
| `source_file` | str | zdrojový GP soubor (jen GP viewer) |
| `source` | str | zdrojová URL / `"web_import"` (jen web import) |
| `duration_seconds` | float | délka (jen web import) |

### 3.2 `tempo_map`

Pole změn tempa, seřazené podle `tick`. Minimálně jeden záznam.

```jsonc
{ "tick": 960, "bpm": 120 }
```

Parser počítá `time_s` po úsecích mezi změnami. Pokud stačí konstantní tempo,
lze vzít první záznam.

### 3.3 `tracks[]` — stopy (a tabulatury)

```jsonc
{
  "index": 1,                     // 1-based, cíl pro track_index
  "name": "Pták Rosomák",
  "type": "guitar",               // guitar | solo_guitar | bass | drums
  "is_drums": false,
  "instrument_midi": 25,
  "tuning": [ { "string": 1, "midi": 64, "note": "E" }, ... ],  // GP viewer
  "has_tab": true,                // web import: false, bez "beats"
  "beats": [ { ... } ]            // jen když has_tab / GP viewer
}
```

- **Sóla**: stopa se `"type": "solo_guitar"` (heuristika `is_solo_like`).
  Aplikace zobrazující tabulatury sól bere právě tyto stopy.
- `tuning[i].string` je 1 = nejvyšší struna (E4 u kytary).

#### `tracks[].beats[]` — jeden úder (nota/akord/slabika)

```jsonc
{
  "measure": 1,
  "tick": 960,
  "time_s": 0.0,
  "duration": "quarter",          // whole|half|quarter|eighth|sixteenth|...
  "duration_ticks": 960,
  "duration_s": 0.9375,
  "text": "",                     // slabika textu na tomto beatu (může být "")
  "chord": "G5",                  // název akordu nebo "" 
  "notes": [ {                    // tabulatura — 0..N strun znějících na beatu
      "string": 2,                // číslo struny (1 = nejvyšší)
      "fret": 3,                  // pražec
      "midi": 62,
      "note_name": "D",
      "effects": {
        "hammer_on": false, "pull_off": false,
        "vibrato": false, "slide": false, "bend": false
      }
  } ]
}
```

### 3.4 `lyrics_timeline[]` — sloučená časová osa textu

Slova ze **všech** stop, seřazená podle `time_s`. Zdroj identifikuje
`track_index`.

```jsonc
{ "time_s": 18.0, "duration_s": 0.5, "text": "Nemohu,", "measure": 10,
  "tick": 35520, "track_index": 1 }
```

### 3.5 `chords_timeline[]` — sloučená časová osa akordů

```jsonc
{ "time_s": 16.0, "chord": "Am", "measure": 9, "tick": 31680, "track_index": 1 }
```

### 3.6 `karaoke_lines[]` — text seskupený do řádků

Slova rozdělená do řádků podle pauz > 2 s. Hotový podklad pro karaoke displej.

```jsonc
{
  "start_s": 15.0,
  "end_s": 19.5,
  "words": [ { "time_s": 15.0, "duration_s": 0.5, "text": "tam", "track_index": 1 } ]
}
```
> U web importu má řádek navíc `"chords": ["Am","G"]` a `"text": "celý řádek"`.

### 3.7 `display_timeline[]` — režie karaoke displeje (Vegas program)

**Nepovinná** sekce (chybí = parser si obsah skládá sám z `karaoke_lines` /
`lyrics_timeline` / `chords_timeline`). Je to **střihová osa (EDL)** ve stylu
Sony Vegas: seřazený seznam **klipů**, kde každý klip říká, že v okně
`[start_s, end_s)` má displej ukázat obsah zdrojové stopy v daném režimu.
Klipy se **nepřekrývají** (jsou seřazené dle `start_s`); mezera = displej nic
neukazuje. Obsah (slova/akordy/tab noty) klip neduplikuje — jen na něj odkazuje
přes `source_track` a čas.

```jsonc
{
  "id": "clip-1",
  "start_s": 0.0,
  "end_s": 12.5,
  "source_track": 1,          // odkaz na tracks[].index — CO se zobrazí
  "mode": "lyrics_chords",    // JAK se to zobrazí (viz tabulka)
  "label": "Sloka 1"          // volitelný popisek klipu (jen pro editor/UI)
}
```

| `mode` | Význam pro displej |
|--------|--------------------|
| `lyrics_chords` | text zpěvu s akordy nad ním (výchozí pro sloky) |
| `lyrics` | jen text |
| `chords` | jen akordy |
| `tab` | tabulatura zdrojové stopy (`tracks[].beats`) |
| `tab_chords` | tabulatura s akordy nad ní (výchozí pro **sóla**) |

Parser vezme `mode` + `source_track`, dohledá odpovídající eventy/beaty v daném
časovém okně a vykreslí je. Neznámý `mode` → fallback `lyrics_chords`.

---

## 4. Co číst pro který účel (návod pro ESP / aplikaci)

| Účel | Potřebné sekce | Lze ignorovat |
|------|----------------|---------------|
| **Řízený karaoke displej** (co+kdy) | `meta`, `tempo_map`, `display_timeline` → dle `mode` sáhni do `lyrics_timeline`/`chords_timeline`/`tracks[].beats` | — |
| **Karaoke text + akordy** (ESP) | `meta`, `tempo_map`, `karaoke_lines` **nebo** `lyrics_timeline`+`chords_timeline` | celé `tracks[].beats[]`, `display_timeline` |
| **Zobrazení tabulatur / sól** | `tracks[]` (kde `has_tab`), `type == "solo_guitar"` | `karaoke_lines` |
| **Sync více nástrojů** | timeline eventy + `track_index` → `tracks[].name/type` | — |

**Paměťová poznámka pro ESP32:** `tracks[].beats[]` je 80–90 % velikosti
souboru. Pro pouhé karaoke ho parser přeskočí (streamované čtení), nebo se
exportuje samostatný „slim" soubor bez `tracks[].beats[]`.

---

## 5. Změny oproti verzi 1

- Přidán `meta.format_version` a `meta.track_count`.
- Eventy v `lyrics_timeline` / `chords_timeline` / `karaoke_lines[].words`
  mají **`track_index` (int)** místo dřívějšího opakovaného `"track": "<jméno>"`.
- `tracks[]` má `has_tab` (u web importu `false`).
- Web import nově exportuje i `tempo_map` a `tracks` (sjednocení schématu).

## 6. Doplňky `format_version: 2` (aditivní, zpětně kompatibilní)

Verze zůstává **2** — jde o nepovinné klíče, které starší parser ignoruje.

- **`display_timeline[]`** (§3.7) — režie karaoke displeje (klipy: co/kdy/jak).
  Produkuje ji editor časové osy. Když chybí, parser si obsah skládá z
  `karaoke_lines` / `lyrics_timeline` / `chords_timeline`.
- **`meta.edited_in_timeline: true`** — příznak, že JSON prošel editorem časové
  osy (přerovnané časy slabik, ruční zalomení řádků, klipy Displeje).
- Časy slabik (`lyrics_timeline[].time_s/duration_s`) mohou být **přerovnané**
  automatickým časováním (rovnoměrně dle slabik / na beat) — parser s nimi
  pracuje stejně, jsou to pořád sekundy od začátku.

> Implementaci přehrávače na ESP32 popisuje **`ESP32_KARAOKE_IMPLEMENTATION.md`**.
