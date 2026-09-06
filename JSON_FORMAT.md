# Karaoke JSON — specifikace formátu (`schemaVersion: 1`)

Tento dokument je **závazná specifikace** výstupního JSONu, jak ho dnes
produkuje `format_migrate.py::migrate()` (volané z `TimelineEditor.to_json()`
při každém uložení). Slouží jako zadání pro AI/vývojáře, který píše **parser**
na ESP32 (nebo jinde).

> **Tohle NAHRAZUJE dřívější `format_version: 2`.** Ten formát (ploché
> `lyrics_timeline`/`chords_timeline`/`drums_timeline` v sekundách + řídící
> osa `display_timeline` — "klipy") se v editoru **už negeneruje ani nečte**.
> Pokud narazíš na starší dokumentaci/kód, který mluví o `display_timeline`,
> `format_version: 2` nebo "klipech" — je to **zastaralé**. Viz §6 níže pro
> shrnutí, co přesně zaniklo a proč.

> Pravidlo pro parser: **čti podle `meta` a `schemaVersion`.** `schemaVersion
> != 1` → odmítni / spusť migraci. Neznámé klíče parser **ignoruje** (dopředná
> kompatibilita).

> **Referenční testovací soubor:**
> [`schema_v1_reference_iron_maiden.json`](schema_v1_reference_iron_maiden.json)
> — reálný export z `Iron Maiden - Run To the Hills (2).gp3` (GP import,
> žádný web import), obsahuje **bicí (5168 úderů)**, **basu (1511 not)** a
> count-in (`countInBars: 2`). Použij ho k testování parseru — je bohatší na
> hrany než jakýkoli soubor uložený starší verzí editoru.

---

## 1. Kódování a obecné konvence

- Soubor je **UTF-8** (bez závislosti na BOM).
- **Autorita je `bar` + `beat`, ne sekundy.** Každá událost nese svou pozici
  jako takt (`bar`, 1-based) a dobu v taktu (`beat`, 1-based, desetinné číslo
  — např. `4.5` = půl doby za 4. dobou). Přesný čas v milisekundách se
  dopočítá z `tempoMap`/`meterMap` (viz §3). Firmware si ho spočítá jednou při
  načtení a dál pracuje s milisekundami/sekundami, jak potřebuje.
- **Výjimka — `tMsOverride`.** Nahrávky, které nehrají přesně na klik (volná
  interpretace, mírné tempové výkyvy), mají u události navíc `tMsOverride`
  (int, milisekundy od začátku skladby) — **má přednost** před dopočtem
  z mřížky. Bez toho by taková událost "ujela" o desítky až stovky ms.
- **Konec se (až na basu) NIKDE neukládá.** Text/akordy/bicí končí tam, kde
  začíná další událost stejného druhu — jediná délka písně je
  `meta.durationMs`. Basová nota si drží `durationBeats`, protože jde
  o skutečnou hudební vlastnost (délka tónu), ne o hranici zobrazení.
- **Žádná samostatná "displej" vrstva.** Dřívější `display_timeline`
  (explicitní klipy CO/KDY/JAK) je pryč — firmware si aktivní řádek/akord
  **spočítá sám za běhu** z `sections[].lyrics`/`chords`, viz §5.

---

## 2. Kořenová struktura

```jsonc
{
  "schemaVersion": 1,
  "meta": { ... },
  "tempoMap": [ { "bar": 1, "beat": 1.0, "bpm": 120 } ],
  "meterMap": [ { "bar": 1, "num": 4, "den": 4 } ],
  "linearMap": [ { "bar": 9, "sourceMeasure": 5, "pass": 2 } ],
  "pickupBeats": 0,
  "sections": [ { ... } ]
}
```

### 2.1 `meta`

| klíč | typ | pozn. |
|------|-----|-------|
| `title` | str | název skladby |
| `artist` | str | interpret |
| `durationMs` | int | délka celé skladby v ms — **jediný zdroj konce**, nic jiného se neukládá |
| `countInBars` | int | počet úvodních taktů ticha PŘED prvním taktem hudby (`bar 1` už je hudba). `0` = žádný odpočet. Klikací stopa metronomu se v tomhle formátu **neukládá** — firmware si ji (má-li reprák) dopočítá sám z tempa/metra, viz §5.4 |
| `sampleSet` | str | název sady bicích samplů, dnes vždy `"kit_default_v1"` — **POZOR, viz §7.1**: neodpovídá skutečnému názvu složky na SD kartě (`rock`), je potřeba sjednotit než na tom firmware začne stavět |
| `audioFile` | str | (nepovinné) cesta k MP3/WAV nahrávce — **jen pro editor**, ESP32 ho nepoužívá |
| `audioOffsetMs` | int | (nepovinné) posun nahrávky — **jen pro editor** |
| `audioStretch` | float | (nepovinné) ±10 % roztažení nahrávky — **jen pro editor** |
| `migrationNotes` | str[] | co konvertor odhadl/nedokázal převést — **jen pro editor** (diagnostika), firmware ignoruje |

Žádné `format_version`, žádné `has_line_structure`, žádné `time_signature`
jako string — metrum je v `meterMap` (§2.3).

### 2.2 `tempoMap[]`

Změny tempa, seřazené podle pozice. Minimálně jeden záznam (na `bar: 1,
beat: 1.0`).

```jsonc
{ "bar": 1, "beat": 1.0, "bpm": 120 }
```

### 2.3 `meterMap[]`

Změny metra (počet a hodnota dob v taktu), seřazené podle `bar`. Minimálně
jeden záznam.

```jsonc
{ "bar": 1, "num": 4, "den": 4 }
```

Beze změny metra v písni je tu jeden záznam s metrem od `bar: 1`.

### 2.4 `linearMap[]` (nepovinné, jen z GP importu)

Skutečné pořadí přehrávání takt po taktu — vzniká rozbalením repetic, 1./2.
zakončení a navigačních skoků (Segno/Coda/D.C./D.S.). Firmware ho
**nepotřebuje** (má přímo `bar` = pozici v přehrávání), slouží jen ke
zpětnému dohledání, ze kterého místa partitury takt pochází.

```jsonc
{ "bar": 9, "sourceMeasure": 5, "pass": 2 }
```

### 2.5 `pickupBeats`

Int, dnes vždy `0`. Rezervováno pro anacrusi (upbeat) — takt 1 by mohl mít
míň dob, než říká `meterMap`. Zatím to editor negeneruje — firmware to může
ignorovat, dokud nezačne být nenulové.

---

## 3. `sections[]`

Skladba rozdělená na pojmenované úseky (sloka/refrén/…). Sekce **nemají
uložený konec** — trvá až do `startBar` následující (poslední do konce
skladby).

```jsonc
{
  "name": "Sloka 1",
  "startBar": 5,
  "source": "manual",
  "lyrics": [ { ... } ],
  "chords": [ { ... } ],
  "drums":  [ { ... } ],
  "bass":   [ { ... } ],
  "tab":    []
}
```

- `source` — `"manual"` (člověk označil marker/section), nebo `"generated"`
  (konvertor odhadl z opakování textu / jediná sekce). Jen informační.
- `tab` — **vždy prázdné pole.** Rezervováno, žádný producent do něj dnes nic
  nezapisuje (formát nenese tabulaturu).
- Všechny čtyři obsahové seznamy (`lyrics`/`chords`/`drums`/`bass`) jsou
  seřazené podle pozice (`bar`,`beat`).

### 3.1 Společný tvar položky

Každá položka v `lyrics`/`chords`/`drums`/`bass` má základ:

```jsonc
{ "bar": 4, "beat": 4.5 }
```

a nepovinně navíc:

- `tMsOverride` (int, ms) — přesný čas MÍSTO dopočtu z mřížky, viz §1.
- `source` (str) — `"gp"` / `"manual"` / `"generated"` (odkud událost je,
  viz §7.2). Firmware ignoruje, jen pro editor.

K tomu specifická pole podle typu (níže).

### 3.2 `lyrics[]` — text (PO ŘÁDCÍCH)

**Jeden záznam = jeden celý řádek** (ne po slovech).

```jsonc
{ "bar": 13, "beat": 1.0, "text": "1. Skončili jsme, jasná zpráva," }
```

Řádek je aktivní od svého `bar`/`beat` **do začátku toho dalšího** — žádný
uložený konec, viz §5.

### 3.3 `chords[]` — akordy

```jsonc
{ "bar": 5, "beat": 1.0, "chord": "C" }
```

Akord zní od svého `bar`/`beat` do dalšího akordu (nebo do konce skladby,
je-li poslední).

### 3.4 `drums[]` — bicí

```jsonc
{ "bar": 4, "beat": 2.5, "sample": "snare" }
```

- `sample` — **basename souboru vzorku bez přípony** (`kick`, `snare`,
  `hihat_closed`, `hihat_open`, `tom_low`, `tom_mid`, `tom_hi`, `crash`, …),
  **už přeložené** z GM jména bubnu přes `drum_samples.json` (viz
  `format_migrate.py::_load_drum_samples`). Firmware **nepotřebuje** žádnou
  GM→sample mapu — dostane rovnou jméno vzorku, které má hledat na SD kartě.
  Víc úderů na stejné `bar`/`beat` (kick+hi-hat současně) = víc záznamů.
- Když `drum_samples.json` na desktopu nezná dané GM jméno bubnu, `sample`
  obsahuje **původní GM jméno** (např. `"Bongo"`) místo basename — firmware
  takový vzorek nenajde; ber to jako "bez zvuku", nespadni.

### 3.5 `bass[]` — basová linka

**Nově exportuje se vždy** (dřív jen po ručním "GP sloučení" ve starém
formátu) — viz §7.3. Jeden záznam = jedna nota.

```jsonc
{ "bar": 4, "beat": 4.5, "note": "D", "midi": 38, "string": 2, "fret": 0,
  "durationBeats": 0.5 }
```

| klíč | typ | pozn. |
|------|-----|-------|
| `note` | str | jméno noty BEZ oktávy (`"D"`, `"C#"`…) — **nejednoznačné samo o sobě**, jen pro zobrazení/log |
| `midi` | int | **přesná výška** (0–127, General MIDI číslo) — tohle firmware potřebuje pro syntézu/přehrání, ne `note` |
| `string` | int | 1 (nejtenčí) až 4 (nejtlustší) — nepovinné, jen editor (vizuální řádek) |
| `fret` | int | pražec — nepovinné, jen editor |
| `durationBeats` | float | délka noty v dobách (`60/bpm` na jednu dobu) — **skutečná hudební vlastnost**, ne hranice zobrazení. Chybí-li, firmware může použít rozumný default (~0.3 s) |

**Firmware dnes basu vůbec nezná** (viz §7.3 a `TODO_FORMAT_V2.md` ve
firmware repu) — tahle sekce popisuje **cílový stav**, ne to, co je
implementované.

---

## 4. Co číst pro který účel

| Účel | Potřebné sekce | Lze ignorovat |
|------|----------------|---------------|
| **Karaoke displej** (co+kdy) | `meta`, `tempoMap`, `meterMap`, `sections[].lyrics`/`chords` → runtime odvození (§5) | `linearMap`, `pickupBeats` |
| **Přehrávání bicích** | `meta`, `tempoMap`, `meterMap`, `sections[].drums` | vše ostatní |
| **Přehrávání basy** (cílový stav, zatím neimplementováno) | `sections[].bass` | vše ostatní |
| **Zpětné dohledání not v partituře** | `linearMap` | za běhu nepotřeba |

---

## 5. Jak firmware pozná, CO zobrazit — runtime pravidlo (nahrazuje `display_timeline`)

Žádná uložená režie. Pro aktuální pozici (`bar`,`beat`, dopočtenou z hodin
přehrávání přes `tempoMap`/`meterMap`) firmware **spočítá sám**:

1. **Aktivní řádek** = poslední položka v `sections[*].lyrics` (napříč VŠEMI
   sekcemi, brát jako jeden seřazený seznam), jejíž `bar`/`beat` je `<=`
   aktuální pozici. Řádek je aktivní **od svého `bar`/`beat` do `bar`/`beat`
   toho dalšího** (poslední řádek: do konce skladby, `meta.durationMs`).
2. **Je-li aktivní řádek** → zobraz jeho `text` + všechny `chords` položky,
   jejichž pozice padne do STEJNÉHO okna (od aktivního řádku po další) —
   to je ekvivalent dřívějšího `mode: "lyrics_chords"`.
3. **Není-li aktivní řádek** (pozice PŘED prvním řádkem, nebo PO posledním, nebo
   V MEZEŘE, kterou žádný řádek nepokrývá — intro/mezihra/sólo) → zkus
   **aktivní akord** stejnou logikou (poslední `chords` položka s `bar/beat
   <=` pozice, platí do dalšího akordu). Je-li nějaký → zobraz JEN akord,
   velkým fontem (ekvivalent `mode: "chords"`).
4. **Není-li ani jedno** → prázdný/idle displej (nebo úvodní obrazovka
   `meta.title`/`meta.artist`, náhled dalšího řádku apod. — volitelné, viz
   starší `ESP32_KARAOKE_IMPLEMENTATION.md` §7/§12 pro nápady, i když jsou
   psané pro starý formát).

Tohle je **čistá funkce pozice** — žádný uložený stav, žádné klipy, žádné
"aktivní ID". Seek/pauza/přetáčení jsou zadarmo (přepočítáš pro nové `t`).

**Bicí a basa hrají nezávisle** na tomhle pravidle, pořád podle svého
vlastního `bar`/`beat` — displej na ně nečeká a ony nečekají na displej.

---

## 6. Co přesně zaniklo oproti `format_version: 2` (a proč)

| Zaniklo | Nahrazeno čím |
|---------|---------------|
| `display_timeline[]` (klipy CO/KDY/JAK) | runtime pravidlo, §5 — dřív dva nezávislé algoritmy (klipy vs. odvození z obsahu) dávaly na stejných datech různé výsledky |
| `karaoke_lines[]` (odvozený, duplicitní obsah) | `sections[].lyrics`/`chords` přímo — obsah je v souboru JEDNOU |
| Časy v sekundách (`time_s`/`duration_s`) | `bar`/`beat` + `tempoMap`/`meterMap`; `tMsOverride` pro volné nahrávky |
| `count_in` klip s ručním `artist`/`title`/posunutými hranicemi | `meta.countInBars` — pevné pravidlo bez uloženého stavu, viz §5.4 (rozhodnutí uživatele: *"stačí pevné pravidlo ve fw, nepotřebuju to na píseň"*) |
| Bicí jako `drum` (GM jméno) + `midi` | `sample` — basename vzorku, **už přeložený** na desktopu |
| `bass_timeline` jen po ručním GP sloučení, ESP ji ignoroval | basa se exportuje **vždy** (viz §7.3) a je určená k reálnému přehrání — firmware ji jen ještě neumí (úkol) |

---

## 7. Nedávné opravy a otevřené nesrovnalosti (audit 2026-09)

### 7.1 `meta.sampleSet` neodpovídá skutečné složce na SD kartě

`format_migrate.py::DEFAULT_SAMPLE_SET = "kit_default_v1"`, ale skutečná
sada vzorků, kterou appka i editor reálně používají, leží v
`assets/drums/rock/*.wav` a `drum_samples.json` odkazuje na `/drums/rock/*`.
**`sampleSet` v souboru tedy dnes lže** — firmware, který by z něj skládal
cestu (`/drums/<sampleSet>/…`), by hledal neexistující složku `kit_default_v1`.
Než na tom firmware začne stavět, je potřeba buď (a) přejmenovat
`DEFAULT_SAMPLE_SET` na `"rock"`, nebo (b) přejmenovat SD složku/`assets/drums/`
na `kit_default_v1`. Zatím to nikomu nevadilo, protože firmware `sampleSet`
vůbec nečte (má vlastní pevnou cestu `/drums/rock/`) — jakmile se to
zparametrizuje, je potřeba tohle sjednotit.

### 7.2 `source` na basové notě

Basa nese `source` stejně jako ostatní eventy (`"gp"`/`"manual"`/
`"generated"`) — beze změny oproti staré specifikaci.

### 7.3 Basová linka je nově VŽDY součástí exportu — a měla by se hrát

Do 2026-09 se `bass_timeline` plnila jen ručním "GP sloučením" (Ctrl+M) a
`JSON_FORMAT.md`/`ESP32_KARAOKE_IMPLEMENTATION.md` výslovně říkaly "ESP32
přehrávač ji vždy může ignorovat — je jen pro vizuální kontrolu". To už
**neplatí**:

- `_build_karaoke_json()` (`guitar_pro_viewer.py`) basu extrahuje při KAŽDÉM
  GP importu, ne jen při sloučení.
- `midi`/`string`/`fret` se nově ukládají přesně (dřív se ztrácely při
  uložení — jméno noty bez oktávy je nejednoznačné, viz commit "basa: přesný
  round-trip midi/string/fret").
- Editor umí basu i **slyšitelně přehrát** (syntetizátor, `_play_bass_note`/
  `render_bass_mixdown` v `timeline_editor.py`) — basa přestala být čistě
  vizuální.

**Firmware basu vůbec neparsuje ani nehraje** (`Song.h`/`Song.cpp` — žádná
struktura, žádné pole). To je teď hlavní otevřený úkol, viz
`TODO_FORMAT_V2.md` ve firmware repu.
