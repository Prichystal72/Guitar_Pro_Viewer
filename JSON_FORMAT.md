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

Formát je **v jádru karaoke text + akordy + bicí** — žádná tabulatura, žádné
struny/pražce/efekty. `tracks[]` obsahuje **pouze** stopy, které něco skutečně
přispívají: co má text nebo akord (typicky zpěv/kytara nesoucí chord chart),
a bicí (kvůli přehrávání samplů) — i když bicí nemají žádný text ani akord.
Basa, klávesy nebo doprovodná kytara bez vlastního textu/akordu se do
**čistě GP nebo web** exportu nedostanou. **Výjimka:** GP sloučení (Ctrl+M,
nebo otevření GP AŽ PO textu/akordech) může navíc přidat **`bass_timeline`**
(§3.5c) — basová stopa jen pro náhled v editoru, do ESP32 přehrávače určená
není.

| Sekce | GP viewer (`guitar_pro_viewer.py`) | Web import (`web_import.py`) |
|-------|-----------------------------------|------------------------------|
| `meta`, `tempo_map`, `tracks` | ✅ (jen přispívající stopy + bicí) | ✅ (1 stopa) |
| `lyrics_timeline`, `chords_timeline`, `karaoke_lines` | ✅ | ✅ |
| `drums_timeline` | ✅ (má-li song bicí stopu) | ❌ (chybí = žádné bicí) |
| `bass_timeline` | jen po GP sloučení (§3.5c) | ❌ |

Žádný producent už negeneruje `tracks[].beats[]`/tabulaturu — pokud narazíš
na starší soubor, který je má, ignoruj je (dopředná kompatibilita).

---

## 3. Kořenová struktura

```jsonc
{
  "meta": { ... },
  "tempo_map": [ { "tick": 960, "bpm": 120 } ],
  "tracks": [ { ... } ],
  "lyrics_timeline": [ { ... } ],
  "chords_timeline": [ { ... } ],
  "drums_timeline": [ { ... } ],
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
| `beats_per_measure` | int | beatů v taktu (web import; vždy `4`) |
| `time_signature` | str | `"4/4"` (web import) |
| `bars_per_line` | int | kolik taktů zabere KAŽDÝ karaoke řádek (web import, výchozí `2`) — pravidelná mřížka, ne odhad z textu |
| `count_in_bars` | int | počet úvodních taktů ticha před prvním řádkem s klikací stopou pro odpočítávání (`drums_timeline`, `drum: "Closed Hi-Hat"`, viz `ESP32_KARAOKE_IMPLEMENTATION.md` §6c). Web import ho nastaví vždy (výchozí `1`); GP sloučení jen když se zavolá `add_count_in()` — nepovinné |
| `count_in_s` | float | totéž v sekundách (`count_in_bars × beats_per_measure × 60/tempo_bpm`) — o tolik je POSUNUTÁ celá časová osa dopředu (žádné záporné časy) |
| `has_line_structure` | bool | `true` u obou producentů vždy — řádky jsou explicitní přes `line`, ne odvozené |
| `merged_web_gp` | bool | `true`, pokud data vznikla GP sloučením (Ctrl+M nebo GP-po-textu) — nepovinné, chybí u čistě GP/web dat |
| `edited_in_timeline` | bool | `true`, pokud JSON prošel `to_json()` editoru časové osy |

### 3.2 `tempo_map`

Pole změn tempa, seřazené podle `tick`. Minimálně jeden záznam.

```jsonc
{ "tick": 960, "bpm": 120 }
```

Parser počítá `time_s` po úsecích mezi změnami. Pokud stačí konstantní tempo,
lze vzít první záznam.

### 3.3 `tracks[]` — stopy (jen přispívající + bicí)

```jsonc
{
  "index": 1,                     // 1-based, cíl pro track_index
  "name": "Pták Rosomák",
  "type": "guitar",               // vocal | guitar | solo_guitar | bass | drums
  "is_drums": false,
  "instrument_midi": 25
}
```

To je **celý** záznam — žádná tabulatura, žádné `beats`/`tuning`. Obsah stopy
je v plochých osách (`lyrics_timeline`/`chords_timeline`/`drums_timeline`),
propojený přes `track_index`. `type` je jen informační štítek (`solo_guitar`
= heuristika „tahle stopa je asi sólo/lead", bez vlivu na to, co se
exportuje).

### 3.4 `lyrics_timeline[]` — časová osa textu (PO ŘÁDCÍCH)

**Jeden záznam = jeden celý řádek** (ne po slovech). `text` je celý řádek,
`time_s` jeho začátek, `duration_s` délka. **`line`** = index řádku (shodný
s `karaoke_lines`). Text jen ze zpěvní stopy (`type: "vocal"`).

```jsonc
{ "time_s": 18.0, "duration_s": 3.6, "text": "Mother Mary comes to me",
  "line": 3, "track_index": 1 }
```

### 3.5 `chords_timeline[]` — časová osa akordů

Akord má **časové razítko** = kdy v řádku zní. Počáteční pozice se počítá z
**hudební mřížky** (ne z odhadu délky textu): řádek má `bars_per_line × 4`
beatů, akord u i-tého slova sedí na nejbližším beatu úměrném pořadí toho
slova (`beat_index = i × beaty_v_řádku / počet_slov`) — pravidelné, žádné
odhady ze slabik. V editoru se dá tažením přemístit/přichytit na mřížku
(**⏱ Na mřížku**). Displej umístí akord vodorovně nad text dle času:
`x = (chord.time_s − line.start_s) / (line.end_s − line.start_s)`.
**`line`** = index řádku, do kterého akord spadá.

```jsonc
{ "time_s": 16.0, "chord": "Am", "line": 3, "track_index": 1 }
```

### 3.5b `drums_timeline[]` — kdy a jaký buben/sample zní

Jeden záznam = jeden úder jednoho bubnu (více bubnů najednou = více záznamů se
stejným `time_s`, např. kick + hi-hat). `drum` je jméno z **GM Percussion Key
Map** (MIDI kanál 10, čísla 35–81 → `GM_PERCUSSION` v `guitar_pro_viewer.py`);
`midi` je totéž číselně, pro přímé mapování na sample bez porovnávání řetězců.

```jsonc
{ "time_s": 13.12, "duration_s": 0.24, "drum": "Acoustic Snare", "midi": 38,
  "line": 3, "track_index": 5 }
```

Časté hodnoty `drum`: `"Acoustic Bass Drum"`, `"Acoustic Snare"`,
`"Closed Hi-Hat"`, `"Open Hi-Hat"`, `"Low/Mid/High Tom"`, `"Crash Cymbal 1/2"`,
`"Ride Cymbal 1/2"`. Přehrávač jen vezme `drum`/`midi`, dohledá odpovídající
sample a spustí ho v `time_s`.

### 3.5c `bass_timeline[]` — basová linka (nepovinné, jen z GP merge)

Vzniká **jen** při sloučení s GP souborem (Ctrl+M, nebo otevřením GP AŽ PO
načtení textu/akordů — viz §6). Jeden záznam = jedna nota. `string` je 1
(nejtenčí) až 4 (nejtlustší) — editor podle něj kreslí basu ve 4 řadách,
podobně jako bicí. Basa se **do exportu ESP32 nepoužívá** (přehrávač ji může
ignorovat) — je to jen referenční vizuální stopa v editoru.

```jsonc
{ "time_s": 12.5, "duration_s": 0.25, "note_name": "E", "midi": 40,
  "string": 4, "fret": 0, "track_index": 3 }
```

### 3.6 `karaoke_lines[]` — text seskupený do řádků

Jeden záznam na řádek — hotový podklad pro karaoke displej, nemusíš nic dál
odvozovat z plochých os.

```jsonc
{
  "line": 4,
  "start_s": 15.0,
  "end_s": 19.5,
  "chords": ["Am", "G"],
  "text": "tam, kde nikdo neuvidí",
  "words": [ { "time_s": 15.0, "duration_s": 4.5, "text": "tam, kde nikdo neuvidí",
               "line": 4, "track_index": 1 } ],
  "track_index": 1
}
```
> `words[]` má **typicky jeden záznam pokrývající celý řádek** (`text` uvnitř
> je shodný s `text` řádku) — text se neláme na jednotlivá slova s vlastním
> časem. Schéma technicky připouští víc záznamů se stejným `line` (např. po
> ruční editaci v timeline editoru), ale žádný producent to automaticky
> negeneruje. Přehrávač proto **nemůže spoléhat na zvýrazňování slovo po
> slově** — viz `ESP32_KARAOKE_IMPLEMENTATION.md` §5.
- `line` je index řádku (0-based), shodný s `line` u slov v `lyrics_timeline`
  a u akordů/bicích v `chords_timeline`/`drums_timeline`.
- `chords` = akordy znějící v tomto řádku (bez duplicit, v pořadí výskytu).
- **`section` (str, nepovinné)** — označení sloky/refrénu (`"Sloka 1"`,
  `"Refrén"`…), odvozené z markerů `1.`/`R:` na začátku textu řádku.
- **Web import**: řádek má **pevnou délku** `bars_per_line` taktů (výchozí 2,
  nastavitelné v dialogu „Nová píseň") — žádný odhad z délky textu. Doplnění
  akordů do dalších slok/refrénu (z první sloky) zůstává zachováno.
  **GP viewer** (samostatné otevření GP, mimo hlavní workflow) řádky odvozuje
  z beat-textů zpěvní stopy / pauz mezi nimi.
- **`start_s`/`end_s` nemusí přesně sedět s mřížkovým rozsahem.** V editoru
  časové osy jde přetáhnout okraj odpovídajícího klipu na master „Displej"
  stopě (§3.7) — řádek pak zůstane na displeji déle/kratčeji. Užitečné u
  rytmických skladeb. Parser to nemusí řešit — prostě vezme `start_s`/`end_s`
  tak, jak jsou.

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
| `lyrics_chords` | text zpěvu s akordy nad ním (výchozí) |
| `lyrics` | jen text |
| `chords` | jen akordy (intro/mezihra bez textu) |

(Módy `tab`/`tab_chords` z dřívějška se už negenerují — žádná stopa nenese
tabulaturu. Parser je pro jistotu nechá jako fallback na `lyrics_chords`,
kdyby na ně narazil ve starším souboru.)

Parser vezme `mode` + `source_track`, dohledá odpovídající eventy v daném
časovém okně a vykreslí je. Neznámý `mode` → fallback `lyrics_chords`.
Nepřekrývající se klipy 1:1 odpovídají řádkům z `karaoke_lines` — `clip.start_s`/
`end_s` **jsou** to, co se má na displeji zobrazit (viz poznámka v §3.6 o ručním
posunu konce řádku).

---

## 4. Co číst pro který účel (návod pro ESP / aplikaci)

| Účel | Potřebné sekce | Lze ignorovat |
|------|----------------|---------------|
| **Řízený karaoke displej** (co+kdy) | `meta`, `tempo_map`, `display_timeline` → dle `mode` sáhni do `lyrics_timeline`/`chords_timeline` | — |
| **Karaoke text + akordy** (ESP) | `meta`, `tempo_map`, `karaoke_lines` **nebo** `lyrics_timeline`+`chords_timeline` | `display_timeline`, `drums_timeline` |
| **Přehrávání bicích samplů** | `meta`, `tempo_map`, `drums_timeline` | vše ostatní |
| **Sync více nástrojů** | timeline eventy + `track_index` → `tracks[].name/type` | — |
| **`bass_timeline`** (jen editor/náhled) | — | ESP32 přehrávač ji vždy může ignorovat — je jen pro vizuální kontrolu linky v editoru, ne pro produkční playback |

Celý soubor jsou jen tenké ploché osy — žádná velká binární/tabulaturní
sekce ke stahování. Bicí `drums_timeline` bývá objemově největší (jeden
záznam na úder), ale pořád jde jen o pár čísel na řádek.

---

## 5. Změny oproti verzi 1

- Přidán `meta.format_version` a `meta.track_count`.
- Eventy v `lyrics_timeline` / `chords_timeline` / `karaoke_lines[].words`
  mají **`track_index` (int)** místo dřívějšího opakovaného `"track": "<jméno>"`.
- `tracks[]` dostal `has_tab` — **později (viz §6) zase odstraněno** spolu s
  celou tabulaturou, takže v aktuálním schématu `has_tab` už není.
- Web import nově exportuje i `tempo_map` a `tracks` (sjednocení schématu).

## 6. Doplňky `format_version: 2` (aditivní, zpětně kompatibilní)

Verze zůstává **2** — jde o nepovinné klíče, které starší parser ignoruje.

- **`display_timeline[]`** (§3.7) — režie karaoke displeje (klipy: co/kdy/jak).
  Produkuje ji editor časové osy. Když chybí, parser si obsah skládá z
  `karaoke_lines` / `lyrics_timeline` / `chords_timeline`. Klip nese i **`line`**
  (index karaoke řádku, který reprezentuje) — díky tomu editor pozná, kam
  patří, i po ručním posunu okrajů.
- **`drums_timeline[]`** (§3.5b) — kdy a jaký buben/sample zní. Chybí, pokud
  song nemá bicí stopu.
- **`bass_timeline[]`** (§3.5c) — basová linka, vzniká jen sloučením s GP
  (Ctrl+M, nebo otevřením GP AŽ PO textu/akordech — **text/akordy se tím
  nepřepíšou**, GP jen přidá bicí/basu). Chybí, pokud song nemá basovou stopu
  nebo žádné GP sloučení neproběhlo. Přehrávač ESP32 ji může ignorovat.
- **`line` (int)** na `lyrics_timeline[]`, `chords_timeline[]`, `drums_timeline[]`,
  `karaoke_lines[]` a `display_timeline[]` — **explicitní seskupení do řádků**:
  eventy se stejným `line` patří do jednoho karaoke řádku. Parser tak nemusí
  odvozovat řádky z pauz — přečte je přímo. Doprovází ho
  **`meta.has_line_structure: true`** (u obou producentů vždy).
- **Žádná tabulatura**: `tracks[]` už negeneruje `beats`/`tuning`/`has_tab` u
  žádné stopy (ani u sóla) — export je jen text, akordy, bicí a (po GP
  sloučení) basa. Klávesy a jiné doprovodné nástroje bez vlastního
  textu/akordu se do `tracks[]` nedostanou vůbec.
- **`karaoke_lines[].section` (str, nepovinné)** — označení sloky/refrénu
  (`"Sloka 1"`, `"Refrén"`…), odvozené z markerů `1.`/`R:` v textu. Slouží
  displeji k oddělovačům sekcí; klipy `display_timeline` ho mají i v `label`.
- **`karaoke_lines[].start_s/end_s` může být ruční** (§3.6) — přetažené přes
  klip na master „Displej" stopě, nezávisle na časování posledního slova.
- **`meta.edited_in_timeline: true`** — příznak, že JSON prošel editorem časové
  osy (přerovnané časy, ruční zalomení řádků, klipy Displeje).
- Časy (`lyrics_timeline[].time_s/duration_s`, `chords_timeline[].time_s`)
  mohou být **přerovnané** editorem (**⏱ Na mřížku** — přichycení na takt/beat)
  — parser s nimi pracuje stejně, jsou to pořád sekundy od začátku. Časování
  je vždy odvozené z tempa/taktu, nikdy z odhadu délky textu.

> Implementaci přehrávače na ESP32 popisuje **`ESP32_KARAOKE_IMPLEMENTATION.md`**.
