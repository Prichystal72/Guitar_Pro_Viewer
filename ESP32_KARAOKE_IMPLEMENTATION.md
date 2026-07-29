# ESP32 Karaoke Displej — implementační návod (obecný)

> **Účel:** závazný návod pro implementaci **přehrávače karaoke JSONu** na ESP32
> (nebo libovolném MCU/displeji). Popisuje, jak z JSONu řídit displej: **které
> řádky ve správný čas**, **zvýrazňování slov**, **akordy nad textem** a
> **přehrávání bicích samplů** (`drums_timeline`). Žádná tabulatura — formát
> nese jen text, akordy a bicí. Návod je **obecný** — musí fungovat pro
> *jakoukoli* skladbu v tomto formátu, ne jen pro přiložený test.
>
> **Vstup:** **výstup z vieweru** — JSON, který vyprodukuje tlačítko
> **„💾 Export JSON"** v editoru časové osy (obsahuje `display_timeline`) nebo
> hlavní **Export Karaoke JSON**. Formát viz `JSON_FORMAT.md`, `format_version: 2`.
> ESP nesmí spoléhat na žádný zvlášť generovaný/ručně upravený soubor — pracuje
> přímo s tím, co viewer vyexportuje.

---

## 0. TL;DR (co udělat)

1. Načti JSON, drž v paměti jen to, co displej potřebuje (řádky/slova/akordy).
2. Měj **hodiny přehrávání** `t` v sekundách od začátku skladby.
3. Renderuj **bezstavově**: každý snímek zavolej `render(t)`, které z dat samo
   spočítá, co má být na displeji. (Umožní to pauzu/přetáčení zdarma.)
4. **Řídící osa = `display_timeline`.** Najdi klip aktivní v čase `t` → podle
   jeho `mode` vykresli obsah `source_track` (text / text+akordy).
   Když `display_timeline` chybí, odvoď řádky z `karaoke_lines`.
5. Text je typicky **jeden blok na celý řádek** (ne po slovech) — zvýrazňuj
   celý řádek (progress bar) podle `time_s`/`duration_s` klipu, ne
   jednotlivá slova (viz §5).
6. **Paralelně** přehrávej `drums_timeline` — nezávisle na displeji (§6b).

---

## 1. Model času a synchronizace

- **Všechny časy `*_s` jsou sekundy** od začátku skladby (`float`).
  `time_s` = začátek události, `duration_s` = její délka.
- **Nemusíš počítat z ticků.** `tick` / `tempo_map` / `ticks_per_beat` jsou jen
  pro rekonstrukci; pro displej **použij hotové `*_s`**. `tempo_map` čti jen
  když bys chtěl vlastní přepočet (typicky netřeba).
- **Hodiny `t`** — tři varianty podle HW:
  - *Volný běh:* `t = (millis() - t0_ms) / 1000.0f;` (start při spuštění písně).
  - *Sync na audio:* odvoď `t` z pozice přehrávače (DFPlayer čas, počítadlo I2S
    vzorků `t = samples / sample_rate`, apod.). **Preferuj tohle**, jinak text
    „ujede" od zvuku.
  - *Ruční kalibrace:* přidej globální offset `t += calib_offset_s;` (posun
    textu proti zvuku, ± v ms).
- **Přehrávání musí být bezstavové vůči `t`.** `render(t)` nesmí záviset na tom,
  jestli se `t` zvětšuje plynule — díky tomu je **seek/pauza/přetáčení** triviální
  (jen změníš `t`). Nedrž „aktuální řádek" jako mutovaný stav; dopočítej ho z `t`.

---

## 2. Které sekce JSONu ESP čte

| Sekce | K čemu | Povinné? |
|-------|--------|----------|
| `meta.format_version` | ověř == 2, jinak odmítni | ✅ |
| `meta.title/artist/tempo_bpm` | úvodní obrazovka, ladění | – |
| `display_timeline[]` | **režie**: co+kdy+jak na displeji | doporučeno |
| `karaoke_lines[]` | řádky textu (fallback, když není `display_timeline`) | ✅* |
| `lyrics_timeline[]` | slova se `time_s`/`duration_s` (zdroj pro zvýraznění) | ✅ |
| `chords_timeline[]` | akordy se `time_s` (nad text / samostatně) | – |
| `drums_timeline[]` | kdy a jaký buben/sample zahrát | jen máš-li reprák/sampler |
| `tempo_map[]` | přepočet ticků (obvykle ignoruj) | – |

Formát je **jen text, akordy a bicí** — žádná tabulatura (`tracks[].beats`),
takže §8 z dřívějška (tab render) odpadá. `tracks[]` obsahuje jen stopy, co
něco skutečně nesou (text/akord), plus bicí.

\* Potřebuješ **buď** `display_timeline` **nebo** `karaoke_lines`/`lyrics_timeline`.

**Pravidla dopředné kompatibility (dle `JSON_FORMAT.md`):**
- Řiď se `meta.format_version`. Neznámou verzi odmítni.
- **Neznámé klíče ignoruj.**
- **Neznámý `mode` → fallback `lyrics_chords`.**
- Chybí `display_timeline` → sestav „okna" z `karaoke_lines`
  (`start_s`,`end_s`,`words`).
- Chybí `duration_s` u slova → odhadni: `next.time_s - time_s`, jinak `0.4 s`.
- Narazíš-li na starší soubor s `mode: "tab"`/`"tab_chords"` nebo
  `tracks[].beats`, ber to jako neznámé/chybějící → degraduj na `chords`/
  `lyrics_chords`, nespadni.

---

## 3. Řídící osa `display_timeline` (Vegas program)

Seřazené, **nepřekrývající se** klipy = „okna" na displeji.

```jsonc
{ "id":"clip-3", "start_s":10.0, "end_s":13.5,
  "source_track":1, "mode":"lyrics_chords", "label":"hodit klíče do kanálu" }
```

- `start_s`/`end_s` — kdy je klip na obrazovce. Mezera mezi klipy = **prázdný /
  idle displej** (nebo náhled dalšího řádku, viz §7).
- `source_track` — z které stopy brát obsah (odkaz na `tracks[].index`).
- `mode` — JAK renderovat:

| `mode` | Render |
|--------|--------|
| `lyrics_chords` | řádek textu + akordy nad ním (výchozí) |
| `lyrics` | jen text |
| `chords` | jen akordy (velké, doprostřed — intro/mezihra) |

(Starší soubory mohly mít i `tab`/`tab_chords` — žádná aktuální stopa už
tabulaturu nenese, takže je stačí ošetřit jako fallback na `lyrics_chords`.)

**Algoritmus výběru aktivního klipu** (klipy jsou seřazené dle `start_s`):
binární hledání posledního klipu s `start_s <= t`; pokud `t < end_s`, je aktivní,
jinak jsme v mezeře.

---

## 4. Renderovací smyčka (frame loop)

```c
void loop_frame() {
    float t = clock_seconds();               // §1
    const Clip* c = active_clip(t);          // §3 (binární hledání)
    if (!c) { draw_idle(t); return; }        // mezera → idle / náhled

    switch (c->mode) {
        case LYRICS:
        case LYRICS_CHORDS:
            draw_lyric_line(c, t, /*chords=*/ c->mode==LYRICS_CHORDS);
            break;
        case CHORDS:
            draw_chords_only(c, t);
            break;
    }
}
```

Volej ~20–60×/s. Kresli do **backbufferu / sprite** a až pak na displej
(anti-flicker). Když se od minulého snímku nezměnil aktivní řádek ani
zvýrazněné slovo, můžeš překreslit jen „karaoke wipe" a šetřit.

---

## 5. Řádek textu (jádro karaoke)

### 5.1 Text řádku — TYPICKY JEDEN BLOK, ne slovo po slovu
Text z `build_karaoke_json()` (web import i GP viewer) je **po řádcích**:
jeden záznam v `lyrics_timeline` = **celý řádek** (`text` = celá věta,
`time_s`/`duration_s` = začátek/délka celého řádku). **Není tam** jemnější
časování jednotlivých slov — `duration_s` pokrývá celý řádek najednou.

Slova klipu = položky `lyrics_timeline` (nebo `karaoke_lines[].words`) se
`track_index == source_track` a `start_s <= time_s < end_s`. Předpočítej si
při načtení pro každý klip rozsah indexů (žádné hledání za běhu).

> **Seskupení do řádků napřímo:** když má JSON `meta.has_line_structure: true`
> (u obou producentů vždy), nese každý záznam v `lyrics_timeline` klíč
> **`line`** (index řádku). Záznamy se stejným `line` = jeden řádek —
> nemusíš řádky odvozovat z pauz ani z klipů. Typicky je na `line` jen JEDEN
> záznam (viz výše); víc záznamů na stejném `line` je legální (ruční editace
> v timeline editoru může řádek rozdělit na víc bloků), ale neočekávej to
> jako běžný případ.

Pokud je na řádku víc než jeden záznam, slož text mezerami: „napadů, aú, co
podporujou…". Pozor: slovo/blok už často obsahuje interpunkci/mezeru —
**nepřidávej mezeru navíc**, pokud končí mezerou/pomlčkou.

### 5.2 Zvýrazňování — jen když TO DATA dovolí
Protože `lyrics_timeline` je typicky **jeden blok na celý řádek**, defaultně
**není** co zvýrazňovat slovo po slově — celý řádek se buď zvýrazní jako
celek (progress bar `(t-start)/(end-start)`, viz §12), nebo se nechá staticky
zobrazený po celou dobu klipu. Kdyby přece jen řádek obsahoval víc bloků
(`line`), zvýrazňuj po nich stejnou logikou jako dřív:

```c
int cur = -1;
for (int i = 0; i < n; i++) if (words[i].time_s <= t) cur = i; else break;
bool active = cur >= 0 && t < words[cur].time_s + words[cur].duration_s;
```
(Za běhu udělej binární hledání místo lineárního.)

### 5.3 Dva styly zvýraznění (jen víc-blokový případ)
- **Celý blok** (jednodušší): obarvi blok `cur` highlight barvou.
- **Karaoke wipe** (hezčí): highlight se „nalévá" podle zlomku času
  `frac = clamp((t - w.time_s)/w.duration_s, 0, 1)`. Kresli highlight
  barvou jen do `x_start + frac * block_px_width`, zbytek základní barvou
  (technika: clipovací obdélník, nebo dvě vykreslení textu s ořezem).
- V typickém (jednoblokovém) případě aplikuj `frac` na **celý řádek** —
  vizuálně stejný efekt jako „progress bar" z §12, jen s doslovným wipe
  přes text.

### 5.4 Layout / zalomení na šířku displeje
Řádek z JSONu **může být širší než displej**. Řeš jedním z:
- *Auto-scroll:* posouvej řádek vodorovně tak, aby aktuální slovo bylo ve středu.
- *Zmenš font* dokud se řádek nevejde.
- *Vizuální podřádky:* rozlom řádek na víc řádků displeje (word-wrap), zvýrazňuj
  napříč. Logické „okno" je pořád jeden klip.

---

## 6. Akordy nad textem (`*_chords`)

Akordy = `chords_timeline` se `track_index == source_track` a časem uvnitř klipu.

**Zarovnání akordu nad slovo (obecně):**
1. Spočítej x-souřadnice začátků slov na displeji (`word_x[i]`).
2. Pro každý akord najdi **slovo s nejbližším `time_s`** (nebo poslední slovo
   s `word.time_s <= chord.time_s`) → nakresli název akordu nad `word_x` toho
   slova, o řádek výš.
3. Když je akordů víc než místa, zobraz jen změny (akord vykresli, jen když se
   liší od předchozího) — stejná logika jako „chord chart" v editoru.

Pro `mode == chords` (bez textu) zobraz **aktuální akord velkým fontem** doprostřed
a případně malý náhled dalšího.

---

## 6b. Bicí samply (`drums_timeline`)

Nezávislé na `display_timeline`/klipech — hraje se **vždy**, i v mezerách mezi
klipy (bicí jedou dál, i když displej zrovna nic neukazuje).

- Každý záznam = jeden úder: `time_s`, `drum` (jméno z GM Percussion mapy,
  např. `"Acoustic Snare"`, `"Closed Hi-Hat"`), `midi` (totéž číselně, 35–81).
- **Naplánuj přehrání samplu v `time_s`**, ne až ho uvidíš v `render(t)` — bicí
  potřebují nižší latenci než text. Vhodné řešení: fronta budoucích úderů,
  při postupu `t` vyjmi a přehraj vše s `time_s <= t` od poslední kontroly.
- Víc úderů se stejným `time_s` (kick+hi-hat současně) = přehraj oba kanály
  najednou (mix nebo víc hlasů přehrávače).
- Bez zvukového výstupu tuhle sekci ignoruj — displej funguje bez ní.

### Mapování jméno bubnu → soubor samplu (`drum_samples.json` na SD kartě)

**Nenapevno v kódu firmwaru** (to by šlo přeřadit jen přeflashováním) — mapa
žije jako **jeden globální soubor na SD kartě**, platný pro všechny písně
(bubny jsou vždy stejná sada jmen z GM Percussion mapy, bez ohledu na song).
Přeřazení = uprav textový soubor na kartě, žádný nový firmware.

Umístění: **`/drums/drum_samples.json`** (samply samotné v `/drums/rock/`).
Reálný vyplněný soubor (pro aktuální sadu 8 samplů) je v repu jako
[`drum_samples.json`](drum_samples.json) — zkopíruj ho na kartu do `/drums/`.

Formát — plochý slovník `"jméno bubnu": "cesta k souboru"`, plus `"_default"`
pro jména, která v mapě nejsou. Žádné jiné speciální klíče (firmware dělá
přímé vyhledání konkrétního jména bubnu, ne iteraci přes všechny klíče —
nehrozí, že by se pokusil otevřít něco jiného jako soubor).

```jsonc
{
  "Acoustic Snare": "/drums/rock/snare.wav",
  "Acoustic Bass Drum": "/drums/rock/kick.wav",
  "Bass Drum 1": "/drums/rock/kick.wav",
  "Closed Hi-Hat": "/drums/rock/hihat_closed.wav",
  "Open Hi-Hat": "/drums/rock/hihat_open.wav",
  "Low Tom": "/drums/rock/tom_mid.wav",
  "High Tom": "/drums/rock/tom_hi.wav",
  "Crash Cymbal 1": "/drums/rock/crash.wav",
  "_default": "/drums/rock/hihat_closed.wav"
}
```

- **Víc jmen bubnů smí mířit na stejný soubor** — běžné, když máš fyzicky
  méně samplů než GM Percussion jmen. Aktuální sada má jen 8 samplů
  (kick/snare/hihat_closed/hihat_open/crash/tom_hi/tom_mid/tom_low) na 47
  možných GM jmen — mapa je tedy silně **N:1** (např. 6 GM tomů → 3 vzorky
  dle výšky, všechny činely/ride → `crash.wav`, protože ride sample není).
  Viz plný soubor v repu pro přesné rozdělení.
- `"_default"` je fallback pro jméno, které v mapě chybí (neznámý/exotický
  buben z GP souboru, např. bonga/conga/agogo — v rockové sadě nejsou) — ať
  přehrávač nikdy neselže, jen zahraje `_default` sample.
- Cesty jsou **absolutní od rootu SD karty** (`/drums/rock/kick.wav`), ne
  relativní — odpovídá tomu, kam se samply reálně kopírují. `rock` je název
  sady/kitu — až budou další sady (jazz, elektronika…), přepíšou se jen
  cesty v tomhle jednom souboru, žádná změna firmwaru.
- **Při startu (jednou):** načti `/drums/drum_samples.json` do paměti (malý
  soubor, pár desítek klíčů), postav si hash mapu jméno→sample. **Za běhu**
  k eventu z `drums_timeline[].drum` jen vyhledej v té mapě (O(1)), přehraj.
- Pokud `/drums/drum_samples.json` na kartě chybí, přehrávač bicí prostě
  nehraje — text/akordy fungují dál (bicí jsou doplněk, ne nutnost).

## 6c. Count-in — odpočet před písní (`meta.count_in_s`)

Bez odpočtu neví nikdo (na displeji ani na vysílání/přenosu), kdy má
zpěv/hraní začít — proto je na začátku každé písně **tichý úvod** s klikací
stopou (viz §6b, `drum: "Closed Hi-Hat"` v `drums_timeline` v okně
`[0, meta.count_in_s)`) a **celá časová osa je posunutá** tak, aby první
skutečná nota/slovo začínala až PO count-inu, ne se s ním nepřekrývala.

- **Vizuální odpočet je vždy jen ČÍSLA** (mezinárodní, nezávislé na jazyce
  displeje) — `4, 3, 2, 1`, ne slova. Žádná zvláštní data v JSONu navíc —
  dopočítá se z `meta.count_in_s` / `meta.count_in_bars` / `meta.tempo_bpm` /
  `meta.beats_per_measure` (výchozí 4):

```c
// t < count_in_s  →  jsme v count-in okně
float beat_s = 60.0f / tempo_bpm;
int total_beats = count_in_bars * beats_per_measure;   // typicky 1×4 = 4
int beats_elapsed = (int)(t / beat_s);                  // 0,1,2,3…
int number = total_beats - beats_elapsed;               // 4,3,2,1
if (number >= 1) draw_big_number(number);                // velký, uprostřed displeje
```

- Klikací zvuk (§6b) a vizuální číslo jsou SYNCHRONNÍ ze stejné mřížky —
  žádné dodatečné párování netřeba, obojí se odvozuje jen z `t`.
- Zvuk je zatím vždy `"Closed Hi-Hat"` — jde přes stejný `drum_samples.json`
  jako běžné bicí. Až budou další zvuky metronomu (klasický „klik", odlišný
  poslední úder apod.), přibude nové jméno do `drum_samples.json`, žádná
  změna schématu ani firmwaru.
- Po `t >= count_in_s` přepni na normální render (§3–§6) — první klip
  `display_timeline` začíná přesně na `count_in_s` (nebo později, pokud má
  song ještě instrumentální úvod navíc).

---

## 7. Idle / mezery / náhled dalšího řádku

- **Mezera mezi klipy** (`active_clip == NULL`): nekresli „zmrzlý" starý řádek.
  Ukaž prázdno, název skladby, nebo **náhled nadcházejícího řádku** + odpočet:
  najdi nejbližší budoucí klip (`start_s > t`), zobraz jeho `label` a
  `countdown = start_s - t` (tři tečky / progress).
- **Před prvním klipem** (intro): počáteční obrazovka `meta.title/artist` +
  odpočet do prvního `start_s`.
- **Za posledním klipem** (outro): „konec".

---

## 8. Datové struktury (návrh v C)

```c
typedef struct { float time_s, dur_s; const char* text; } Word;      // UTF-8!
typedef struct { float time_s; const char* name; } Chord;
typedef enum { M_LYRICS, M_LYRICS_CHORDS, M_CHORDS } Mode;

typedef struct {
    float start_s, end_s;
    uint8_t source_track;
    Mode  mode;
    const char* label;
    uint16_t word_lo, word_hi;   // předpočítaný rozsah do pole Word[]
    uint16_t chord_lo, chord_hi; // předpočítaný rozsah do pole Chord[]
} Clip;

typedef struct {
    Clip*  clips;  uint16_t n_clips;   // seřazené dle start_s, nepřekryv
    Word*  words;  uint16_t n_words;   // seřazené dle time_s
    Chord* chords; uint16_t n_chords;  // seřazené dle time_s
    float  tempo_bpm; float duration_s;
} Show;
```

**Při načtení (jednou):** ověř verzi → seřaď pole dle `time_s`/`start_s` (jsou
už seřazená, ale nespoléhej) → pro každý `Clip` předpočítej `word_lo..hi` a
`chord_lo..hi` binárním hledáním. **Za běhu** už jen binární hledání v čase.

**Fallback bez `display_timeline`:** vyrob `Clip` z každého `karaoke_lines[i]`
(`start_s`,`end_s`, `mode=M_LYRICS_CHORDS`, `source_track` = z prvního slova).

---

## 9. Diakritika, fonty, displej

- JSON je **UTF-8**; text obsahuje českou diakritiku (`ě š č ř ž á í é ú ů…`)
  přímo. Displej **musí mít glyfy** pro Latin-2 / potřebné znaky. U8g2: použij
  font s `_cs`/`_latin`/plný Unicode a UTF-8 API (`drawUTF8`). Pozor na měření
  šířky — počítej v glyfech/pixelech, ne v bajtech (`ě` jsou 2 bajty).
- Základní paleta: text = tlumená barva, **zvýrazněné slovo = kontrastní**
  (např. žlutá/bílá), akordy = odlišná (modrá). Velký font pro 1 řádek + menší
  nad ním pro akordy.

---

## 10. Přetáčení / pauza / start

Protože `render(t)` je bezstavové:
- **Pauza:** zastav růst `t` (drž poslední hodnotu).
- **Seek:** nastav `t` a překresli — aktivní klip/slovo se dopočítá.
- **Restart:** `t = 0`.
Nedrž „index řádku" napříč snímky jako pravdu; ber ho vždy z `t`.

---

## 11. Akceptační test (obecný) + konkrétní příklad

**Obecné podmínky (musí platit pro každý validní JSON):**
1. Odmítne `format_version != 2`; neznámé klíče ignoruje; neznámý `mode` → text.
2. V každém `t` je aktivní ≤ 1 klip (klipy se nepřekrývají).
3. Řádek se objeví v `start_s` a zmizí v `end_s` odpovídajícího klipu (i po
   ručním posunu okraje — viz `JSON_FORMAT.md` §3.6).
4. Zvýrazněný/aktivní je blok, jehož `[time_s, time_s+duration_s)` obsahuje
   `t` — v typickém případě to je **celý řádek** (jeden blok), ne slovo.
5. V mezeře mezi klipy displej „nezamrzne" na starém řádku.
6. Akord se kreslí nad slovem s nejbližším časem; `chords` režim ukáže aktuální.
7. Bicí (`drums_timeline`) hrají nezávisle na displeji, i v mezerách mezi klipy.
8. Seek na libovolný `t` zobrazí správný řádek bez „dohánění".

**Konkrétní příklad — JSON z importu webu** (song „Let It Be", The Beatles,
80 BPM, `bars_per_line=2`, `count_in_bars=1` → `count_in_s=3.0`; 21 karaoke
řádků, každý přesně 6.0 s = 2 takty). Získáš ho: **Ctrl+N** → vlož URL →
✅ Použít v editoru → **💾 Export JSON**. Očekávané chování:
- `t=0.0–3.0 s` → **count-in**, žádný klip aktivní → idle/odpočet (viz §7).
- `t=3.0 s` → klip `clip-1` [3.0–9.0], řádek „1. When I find myself in times
  of trouble" (celý jako jeden blok, žádné zvýrazňování po slovech — viz §5).
- `t≈4.5 s` → nad textem akord **C**; později **G** (viz `chords_timeline`,
  pozice dle pořadí slova na hudební mřížce, ne odhad).
- `t=9.0 s` → přepnutí na `clip-2` [9.0–15.0] „Mother Mary comes to me",
  akordy **Ami**, **F**.
- Žádné mezery mezi řádky (`bars_per_line` je pravidelné, řádky na sebe
  navazují) — pokud uživatel v editoru ručně natáhne/zkrátí klip (§3.6
  `JSON_FORMAT.md`), mezera vzniknout může.
- Seek na libovolné `t` (např. `t=45.0 s`) → rovnou správný klip, žádné
  „dohánění" předchozích řádků.

---

## 12. Doporučené rozšíření (volitelné)
- Dvouřádkový režim: aktuální + náhled dalšího řádku (mnoho karaoke to má).
- Progress bar řádku (`(t-start)/(end-start)`).
- Odpočet „za 3…2…1" před začátkem řádku po delší mezeře.
- Globální kalibrace posunu textu vůči zvuku v nastavení.

---

### Shrnutí filozofie
> **`display_timeline` říká CO/KDY/JAK, `lyrics_timeline`/`chords_timeline`
> dodávají OBSAH, `drums_timeline` hraje nezávisle na displeji, hodiny
> dodávají `t`, `render(t)` je bezstavové.** Drž se toho a přehrávač bude
> fungovat na libovolné skladbě v tomto formátu.
