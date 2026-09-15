# ESP32 Karaoke Displej — implementační návod (`schemaVersion: 1`)

> **Účel:** závazný návod pro implementaci **přehrávače karaoke JSONu** na
> ESP32. Popisuje, jak z JSONu řídit displej (**které řádky ve správný čas**,
> **akordy nad textem**, **sólo tabulatura** — `sections[].tab`, **zatím
> NEimplementováno**, viz §3.3b), jak přehrávat **bicí** (`sections[].drums`,
> dnes implementováno) a **basu** (`sections[].bass`, **zatím
> NEimplementováno** — viz §5). Sólo je na rozdíl od basy **jen vizuální**
> (noty pro živého kytaristu, ne zvuk k přehrání) — firmware ho zobrazuje,
> nehraje.
>
> **Vstup:** výstup `TimelineEditor.to_json()` — `schemaVersion: 1`, formát
> viz `JSON_FORMAT.md`. **Tohle nahrazuje dřívější verzi tohoto dokumentu**,
> která popisovala `format_version: 2` a řídící osu `display_timeline`
> ("klipy") — ta v editoru zanikla, viz `JSON_FORMAT.md` §6.
>
> **Referenční testovací soubor:**
> [`schema_v1_reference_iron_maiden.json`](schema_v1_reference_iron_maiden.json)
> — reálný GP import s bicími (5168) i basou (1511 not) a count-inem.

---

## 0. TL;DR (co udělat)

1. Načti JSON, ověř `schemaVersion == 1`.
2. Při načtení (jednou) spočítej z `tempoMap`+`meterMap` mřížku `bar/beat ↔
   ms`, přepočti do ní všechny události (`lyrics`/`chords`/`drums`/`bass`/
   `tab` napříč sekcemi do plochých, podle pozice seřazených polí).
3. Měj **hodiny přehrávání** `t` (ms od začátku skladby, `t < 0` = ještě
   probíhá odpočet `meta.countInBars`).
4. Renderuj **bezstavově**: každý snímek zavolej `render(t)`, které z dat
   samo spočítá, co má být na displeji — text+akordy, NEBO sólo tabulatura
   (jiný layout, §3.3b), NEBO jen akord — **žádná uložená režie** (viz §3,
   nahrazuje dřívější `display_timeline`).
5. **Paralelně** (nezávisle na displeji) přehrávej `sections[].drums`
   (implementováno, §4) a `sections[].bass` (**úkol**, §5). `sections[].tab`
   se **nehraje** — je to vizuální notace pro živého kytaristu (§3.3b).

---

## 1. Model času a synchronizace

- **Autorita je `bar`/`beat`**, ne sekundy — viz `JSON_FORMAT.md` §1. Při
  načtení přepočti KAŽDOU událost na `timeMs` (nebo `timeS`) přes
  `tempoMap`/`meterMap`; `tMsOverride`, je-li přítomné, tenhle přepočet
  přebije.
- **Hodiny `t`** — stejně jako dřív, tři varianty podle HW:
  - *Volný běh:* `t = millis() - t0_ms`.
  - *Sync na audio:* odvoď z pozice přehrávače/počítadla I2S vzorků. **Preferuj
    tohle**, jinak text "ujede" od zvuku.
  - *Ruční kalibrace:* globální offset.
- **`render(t)` musí být bezstavové.** Nedrž "aktuální řádek" jako mutovaný
  stav napříč snímky — dopočítej ho vždy z `t` (binární hledání v seřazeném
  poli). Díky tomu je seek/pauza/přetáčení triviální.
- **Odpočet (`meta.countInBars`)**: `t = 0` je začátek `bar 1` (první
  hudební takt). Odpočet trvá `countInBars` taktů PŘED tím — v hodinách to
  je jednoduše záporné `t` (nebo ekvivalentně posuň `t0_ms` o
  `countInBars × beatsPerBar × 60000/bpm` dozadu). Žádný klip, žádný stav v
  souboru — viz §3.4.

---

## 2. Načtení a předzpracování

Struktura `sections[]` je pohodlná pro **ukládání** (pojmenované úseky), ale
pro **přehrávání** je nejjednodušší si při načtení VŠECHNY položky
`lyrics`/`chords`/`drums`/`bass`/`tab` ze všech sekcí slít do plochých polí,
seřadit podle `timeMs`, a `sections[].startBar`/`name` použít jen tam, kde
je fakt potřeba (název sekce na displeji, pokud to chceš zobrazovat).

```c
typedef struct { uint32_t timeMs; const char* text; } KLine;
typedef struct { uint32_t timeMs; const char* name; } KChord;
typedef struct { uint32_t timeMs; const char* sample; } KDrum;
typedef struct { uint32_t timeMs; uint8_t midi; float durS; } KBass;   // §5
typedef struct {                                                       // §3.3b
    uint32_t timeMs;
    const char* text;        // celý ASCII blok, "\n"-oddělené řádky (6 strun typicky)
    const char* variants[6]; // NULL, nebo hotový alternativní text — index podle
                              // SEMITONE_TO_VARIANT_IDX (viz §3.3b), pořadí ±1..±3
} KTabBlock;

typedef struct {
    KLine*     lines;  uint16_t nLines;    // sections[*].lyrics, seřazené
    KChord*    chords; uint16_t nChords;   // sections[*].chords, seřazené
    KDrum*     drums;  uint16_t nDrums;    // sections[*].drums, seřazené
    KBass*     bass;   uint16_t nBass;     // sections[*].bass, seřazené (§5)
    KTabBlock* tab;    uint16_t nTab;      // sections[*].tab, seřazené (§3.3b)
    float   bpm; uint32_t durationMs; uint16_t countInBars;
} Song;
```

**Při načtení (jednou):** ověř `schemaVersion`, postav mřížku z
`tempoMap`/`meterMap`, přepočti `bar/beat → timeMs` pro každou položku
(respektuj `tMsOverride`), setřiď pole podle `timeMs`. **Za běhu** už jen
binární hledání.

---

## 3. Co zobrazit — runtime pravidlo (žádná uložená režie)

Tohle **nahrazuje** dřívější `display_timeline`/klipy. Algoritmus (viz
`JSON_FORMAT.md` §5) pro aktuální `t`:

```c
void render(uint32_t t) {
    // 1) aktivní řádek = poslední lines[i] s timeMs <= t
    int li = lastLineAtOrBefore(t);   // binární hledání, -1 = žádný

    if (li >= 0) {
        uint32_t lineEnd = (li + 1 < song.nLines) ? song.lines[li+1].timeMs
                                                    : song.durationMs;
        drawLine(song.lines[li].text);
        drawChordsInWindow(song.lines[li].timeMs, lineEnd);   // §3.2
        return;
    }

    // 2) žádný řádek aktivní → zkus aktivní okno sóla (JINÝ layout, §3.3b)
    int ti = lastTabBlockAtOrBefore(t);
    if (ti >= 0) {
        uint32_t tabEnd = (ti + 1 < song.nTab) ? song.tab[ti+1].timeMs
                                                 : song.durationMs;
        drawTabBlock(&song.tab[ti]);                            // §3.3b — hotový text, jen vypsat
        drawChordsInWindow(song.tab[ti].timeMs, tabEnd);        // stejný vzorec, §3.2
        return;
    }

    // 3) ani řádek, ani sólo → zkus aktivní akord
    int ci = lastChordAtOrBefore(t);
    if (ci >= 0) { drawChordOnly(song.chords[ci].name); return; }

    // 4) nic z toho → idle / úvodní obrazovka / náhled dalšího řádku (volitelné)
    drawIdle(t);
}
```

### 3.1 Text řádku

Stejně jako dřív: `lyrics[]` je **typicky jeden blok na celý řádek**
(`text` = celá věta), ne po slovech — není tedy co zvýrazňovat slovo po
slově. Zvýrazni/animuj **celý řádek** jako celek, např. progress bar
`(t - lineStart) / (lineEnd - lineStart)`.

### 3.2 Akordy v okně aktivního řádku

```c
void drawChordsInWindow(uint32_t start, uint32_t end) {
    for (int i = firstChordAtOrAfter(start); i < song.nChords && song.chords[i].timeMs < end; i++) {
        float frac = (float)(song.chords[i].timeMs - start) / (end - start);
        int x = textX + (int)(frac * textWidthPx);
        drawChordLabel(x, song.chords[i].name);   // jen když se liší od předchozí
    }
}
```

Stejný poziční vzorec jako dřív (`JSON_FORMAT.md` §3.3), jen okno teď
NENÍ z klipu, ale z **rozestupu dvou sousedních řádků**.

### 3.3 `mode: "chords"` (bez textu)

Nastává implicitně, když `t` padne do mezery beze řádku i beze sóla, ale je
aktivní akord — zobraz ho velkým fontem uprostřed (žádný text).

### 3.3b Sólo tabulatura (`sections[].tab`) — ÚKOL, zatím neimplementováno

**Revidováno 2026-09** — první verze tohoto oddílu popisovala tabulaturu po
JEDNOTLIVÝCH notách (`string`/`fret`/`tech`). To se ukázalo být špatný model
(uživatel chtěl vizuální tabulaturu jako z papíru/webu, ne strukturovaná
data odvozená z not) a bylo nahrazeno **hotovými ASCII textovými bloky** —
viz `JSON_FORMAT.md` §3.6. Každá položka v `tab[]` je CELÝ blok (typicky
6řádkový text, jeden řádek na strunu), ne jedna nota.

"Aktivní okno" najdeš **stejným binárním hledáním jako u řádku textu**
(poslední `tab[i].timeMs <= t`, platí do `tab[i+1].timeMs`) — v tomhle se
nic nezměnilo. Co se zásadně liší, je VYKRESLENÍ:

- **Statická mřížka, ne rolující/wipe text jako u zpěvu** — proto to
  render() (§3) musí rozeznat jako SAMOSTATNOU větev, ne odvozovat za běhu
  uprostřed kreslení. Přítomnost dat v `tab[]` pro dané okno JE ten signál
  (žádné další pole v JSONu navíc netřeba).
- **`drawTabBlock()` text JEN VYPÍŠE** — rozdělí `song.tab[ti].text` podle
  `\n` a každý řádek napíše na svůj řádek displeje (monospace font,
  ideálně stejná šířka znaku jako na desktopu, ať se nic nezalamuje
  neočekávaně). **Žádné parsování** `string`/`fret`/pozic — to všechno je
  už hotové v samotném textu (viz `JSON_FORMAT.md` §3.6 pro přesný formát
  `LABEL|----7----|`). Počet řádků odpovídá počtu `\n`-oddělených řádků v
  `text` (typicky 6, ale ber to obecně — neházej to na pevných 6).
- **Transpozice** (kapo/jiná tónina zpěváka) — `variants[]` obsahuje
  HOTOVÉ alternativní texty pro ±1 až ±3 půltóny (`JSON_FORMAT.md` §3.6,
  §7.4). Firmware transpozici řeší TRIVIÁLNĚ:
  ```c
  const char* tabTextForSemitones(const KTabBlock* blk, int semitones) {
      if (semitones == 0 || semitones < -3 || semitones > 3) return blk->text;
      int idx = semitones > 0 ? semitones - 1 : semitones + 3;  // -3..-1,1..3 -> 0..5
      const char* v = blk->variants[idx];
      return v ? v : blk->text;   // chybí-li varianta, netransponovaný text jako fallback
  }
  ```
  **Žádné živé přepočítávání ASCII textu na firmwaru** — to už je vyřešené
  na desktopu (přepočet skutečného hracího hmatu pro posunutou výšku, ne
  jen `+N` na čísle pražce). Firmware jen vybere správný `const char*`.
- Číslo pražce je v textu HLAVNÍ obsah — bez něj to není čitelná
  tabulatura. Technické značky (`h`/`~`/`/`/`b` hned za číslem, bez
  mezery) jsou volitelný doplňkový symbol, stejná konvence jako na
  desktopu.
- **Sólo se NEHRAJE jako zvuk** — na rozdíl od bicích/basy (§4/§5) je to
  notace PRO ŽIVÉHO KYTARISTU, ne data k syntéze. Firmware ho jen ukazuje.
  (Appka dřív měla i syntetizátor sóla — byl celý odstraněn, viz
  `JSON_FORMAT.md` §7.4, uživatel chtěl jen vizuální tabulaturu.)
- Akordy nad tabulaturou (má-li song v okně nějaké) se kreslí stejným
  vzorcem jako nad textem (§3.2) — jen zdroj okna je teď sólo, ne řádek.

**Známé omezení `variants`** — dvojhmaty/akordíky v sólu (dvě noty znějící
SOUČASNĚ) nejsou při přepočtu hmatu explicitně kontrolované na hratelnost
(mohly by teoreticky vyjít na stejnou strunu). Firmware s tím nemusí nic
dělat (data přicházejí hotová), ale je to otevřený úkol na desktop straně —
viz `TODO_FORMAT_V2.md`.

### 3.4 Count-in (`meta.countInBars`)

Žádný klip, žádný uložený `artist`/`title` na count-inu — použij přímo
`meta.title`/`meta.artist` (ty existují vždy). Číslo odpočtu:

```c
// t < 0 (před bar 1)
float beatS = 60.0f / song.bpm;
int totalBeats = song.countInBars * beatsPerBar;      // z meterMap
int elapsed = (int)((-t / 1000.0f) / beatS);           // kolik dob uplynulo
int number = totalBeats - elapsed;                     // 4,3,2,1…
if (number >= 1) drawBigNumber(number);
drawTitleArtist(song.title, song.artist);              // stejné 2 řádky jako lyrics_chords
```

`countInBars == 0` → žádný odpočet, `t=0` je rovnou `bar 1`.

### 3.5 Idle / mezery

Stejné nápady jako dřív (dřívější §7/§12 téhle dokumentace) — úvodní
obrazovka, náhled dalšího řádku, "konec" na outru — jsou pořád platné,
jen zdroj dat je jiný (`song.lines[next]` místo `next clip`).

---

## 4. Bicí (`sections[].drums`) — implementováno, beze změny principu

Existující kód (`AudioTest.cpp`) tohle už dělá — dva klíčové mechanismy,
oba beze změny použitelné i pro basu (§5):

- **`Voice` + mixer** (`mixerTask`, jádro 0, I2S 44,1 kHz/16bit): pole
  `NVOICES` slotů `{const int16_t* data; uint32_t len, pos; bool active;}`.
  Každý audio blok (128 rámců) sečte vzorek ze všech aktivních hlasů,
  aplikuje hlasitost (Q8 fixed-point) a soft-knee limiter, zapíše přes
  `i2s_write`. **Nezávisí na tom, odkud `data` pochází** — WAV ze SD karty i
  RAM buffer ze syntézy fungují stejně (klíčové pro §5).
- **Sekvencer** (`sequencerUpdate`): kurzor do `song->drums` (seřazeného
  podle `timeS`), binární `lowerBound` při seeku (skok > 0,3 s), jinak
  lineární postup — spustí `triggerMidi()`/`playSampleIdx()` pro každý úder,
  jehož čas už dozrál.

Jediná změna oproti dřívějšku: `drums[].sample` je **už přeložené jméno
vzorku** (`JSON_FORMAT.md` §3.4) — odpadá GM→sample mapování v C, `Song.cpp`
ho jen předá do `playSampleByName(sample)`.

---

## 5. Basa (`sections[].bass`) — ÚKOL, zatím neimplementováno

`Song.h`/`Song.cpp` dnes basu vůbec neparsují (žádná struktura). Popis níže
je **návrh přístupu**, ne hotová implementace — konkrétní rozhodnutí (kolik
současných hlasů, přesný tvar obálky) je na tom, kdo to bude psát.

### 5.1 Proč to jde bez nové audio architektury

Basa hraje **libovolnou výšku** (`midi`, viz `JSON_FORMAT.md` §3.5) — na
rozdíl od bicích to není fixní one-shot vzorek. Ale existující `Voice`
mechanismus (§4) je lhostejný k PŮVODU dat v bufferu — stačí tedy:

1. **Při startu (nebo líně, při prvním výskytu)** vygenerovat do RAM krátký
   PCM buffer (16bit, 44,1 kHz, ~0,5–0,8 s) pro danou MIDI notu — základní
   tón + pár tišších harmonických + rychlý náběh a exponenciální dozvuk
   ("drnk" struny). **Přesně tenhle výpočet už existuje a je otestovaný** —
   viz `timeline_editor.py::_bass_tone_samples`/`_bass_note_wav` v
   `Guitar_Pro_Viewer` repu (Python referenční implementace algoritmu,
   ne kód k přímému portování, ale hotová matematika: frekvence
   `440 * 2^((midi-69)/12)`, obálka, poměry harmonických).
2. Basa má realisticky nanejvýš pár desítek různých výšek za písničku —
   cache podle `midi` (jako v Pythonu) drží paměťovou stopu nízkou.
3. **Zapoj přes STEJNÝ `Voice` slot mechanismus jako bicí** (§4) — nový
   `playBassNote(midi)` najde/vygeneruje buffer, obsadí volný `Voice` slot.
   Žádná změna `mixerTask`.

### 5.2 Sekvencer basy — stejný vzor jako bicí

```c
// analogie k sequencerUpdate()/drumsLowerBound() v AudioTest.cpp
void bassSequencerUpdate(const Song* song, uint32_t t, bool playing) {
    // stejná logika seek/postup jako u bicích, jen nad song->bass
    // (seřazené podle timeMs) a trigger = playBassNote(bass[i].midi)
}
```

### 5.3 Otevřené otázky pro implementátora

- **Polyfonie**: basová linka je typicky monofonní (jedna nota najednou),
  ale `NVOICES` sdílí bicí i basu — ověř, že fronta má dost slotů, když
  hrají obě najednou (bicí + basa + případně přesah předchozí basové noty
  do dozvuku).
- **Délka not**: `durationBeats` (přepočtené na `durS`) určuje, KDY se má
  spustit DALŠÍ nota, ne nutně délku bufferu — dozvuk klidně přesahuje přes
  začátek další noty (na reálné struně to tak zní taky), buffer pro tón
  může být o kus delší než `durS`.
- **Hlasitost**: samostatný ovládací prvek jako `setDrumVolume()`
  (`AudioTest.cpp`), analogicky `setBassVolume()`.

---

## 6. Diakritika, fonty, displej

Beze změny oproti dřívějšku: JSON je UTF-8, počítej šířku v glyfech (ne
bajtech), displej potřebuje glyfy pro `ě š č ř ž á í é ú ů…`.

---

## 7. Přetáčení / pauza / start

Beze změny: `render(t)` je bezstavové, takže pauza = zastav růst `t`, seek =
nastav `t` a překresli, restart = `t = -countInMs`.

---

## 8. Akceptační test

1. Odmítne `schemaVersion != 1`; neznámé klíče ignoruje.
2. V každém `t` je zobrazen nejvýš jeden řádek (žádné dva najednou).
3. Řádek se objeví přesně na svém `bar`/`beat` a zmizí přesně na `bar`/`beat`
   toho dalšího — žádný uložený konec, žádné "zamrznutí".
4. Akord se kreslí nad textem podle poziční frakce v okně **mezi dvěma
   sousedními řádky** (§3.2), ne podle klipu (ten už neexistuje).
5. Bicí hrají nezávisle na displeji, i v mezerách mezi řádky a v okně
   count-inu.
6. **Basa hraje nezávisle na displeji stejně jako bicí** (až bude
   implementována, §5) — na správné výšce (`midi`), ne jen jako klik.
7. **Sólo tabulatura (`tab[]`) se zobrazí jako statická mřížka (tolik
   řádků, kolik má `text` — typicky 6 strun)** (až bude implementována,
   §3.3b) — NENÍ to rolující text, i když se zobrazuje ve STEJNÉM místě
   displeje jako řádek. Aktivní jen když žádný řádek textu není aktivní
   (§3 bod 2 má přednost).
8. Seek na libovolné `t` zobrazí správný řádek/sólo/akord bez "dohánění".
9. Otestuj na [`schema_v1_reference_iron_maiden.json`](schema_v1_reference_iron_maiden.json)
   — 5168 bicích, 1511 basových not, sólo tabulatura, `countInBars: 2`.

---

### Shrnutí filozofie

> **Firmware si CO/KDY/JAK spočítá sám z `bar`/`beat` pozic v
> `sections[].lyrics`/`chords`/`tab`** (§3) — žádná uložená režie.
> **`drums`/`bass` hrají nezávisle na displeji** (§4/§5), stejným `Voice`
> mixerem. **`tab` se naopak nehraje, jen zobrazuje** — jiný vykreslovací
> režim (statická mřížka strun, §3.3b), ne zvuk. **`render(t)` je
> bezstavové.** Basa a sólo jsou dnes jediné chybějící kusy — data už na
> to čekají (`JSON_FORMAT.md` §3.5/§3.6), zbývá jen napsat parser + (u
> basy) syntézu (§5) / (u sóla) statický render (§3.3b).
