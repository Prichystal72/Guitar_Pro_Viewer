# ESP32 Karaoke Displej — implementační návod (obecný)

> **Účel:** závazný návod pro implementaci **přehrávače karaoke JSONu** na ESP32
> (nebo libovolném MCU/displeji). Popisuje, jak z JSONu řídit displej: **které
> řádky ve správný čas**, **zvýrazňování slov**, **akordy nad textem** a
> **přehrávání bicích samplů** (`drums_timeline`). Žádná tabulatura — formát
> nese jen text, akordy a bicí. Návod je **obecný** — musí fungovat pro
> *jakoukoli* skladbu v tomto formátu, ne jen pro přiložený test.
>
> **Vstup:** **výstup z vieweru** — JSON, který vyprodukuje uložení
> (**💾 Uložit** / Ctrl+S, **💾 Uložit jako…** / Ctrl+Shift+S, nebo tlačítko
> „💾 Uložit Karaoke JSON" v levém panelu) v editoru časové osy — obsahuje
> `display_timeline`. Formát viz `JSON_FORMAT.md`, `format_version: 2`.
> ESP nesmí spoléhat na žádný zvlášť generovaný/ručně upravený soubor — pracuje
> přímo s tím, co viewer vyexportuje.
>
> **Referenční testovací soubor** (poslední známý stav formátu, se vším
> výše): [`Olympic - Jasná Zpráva_final.json`](Olympic%20-%20Jasn%C3%A1%20Zpr%C3%A1va_final.json)
> — obsahuje posunutý `count_in` klip, samostatné `chords`-only klipy
> (akordy s `line: null`) i běžné `lyrics_chords` řádky. Konkrétní čísla
> z něj používá příklad v §11.

---

## 0. TL;DR (co udělat)

1. Načti JSON, drž v paměti jen to, co displej potřebuje (řádky/slova/akordy).
2. Měj **hodiny přehrávání** `t` v sekundách od začátku skladby.
3. Renderuj **bezstavově**: každý snímek zavolej `render(t)`, které z dat samo
   spočítá, co má být na displeji. (Umožní to pauzu/přetáčení zdarma.)
4. **Řídící osa = `display_timeline`.** Najdi klip aktivní v čase `t` → podle
   jeho `mode` vykresli obsah `source_track` (text / text+akordy). Je-li
   `display_timeline` **přítomná, je autoritativní** — mezera mezi klipy
   znamená idle displej, i kdyby `karaoke_lines` pro ten čas obsah měla
   (§7, konkrétní příklad v §11). Fallback na `karaoke_lines` je jen pro
   případ, že `display_timeline` v souboru **celá chybí**.
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
| `bass_timeline[]` | basová linka (jen po GP sloučení) | ✅ vždy ignoruj — jen pro editor |
| `tempo_map[]` | přepočet ticků (obvykle ignoruj) | – |

Formát je v jádru **text, akordy a bicí** — žádná tabulatura (`tracks[].beats`),
takže §8 z dřívějška (tab render) odpadá. `tracks[]` obsahuje jen stopy, co
něco skutečně nesou (text/akord), plus bicí. **Výjimka:** po GP sloučení
(Ctrl+M v editoru) může přibýt `bass_timeline` — je to čistě vizuální
referenční stopa pro editor, ESP32 přehrávač ji **vždy ignoruje**.

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

Seřazené, **nepřekrývající se** klipy = „okna" na displeji. Na to se dá
**spolehnout**: editor invariant vynucuje při každém uložení (JSON_FORMAT.md
§3.7, „Zaručený invariant"), protože překryv by tvůj `find` níž vyřešil ve
prospěch dřívějšího klipu a ten pozdější by se po celou dobu překryvu
nezobrazil. Starší soubory (uložené dřívější verzí editoru) překryv mít
můžou — chceš-li být robustní, ber prostě **poslední** klip, který vyhoví
`start_s <= t < end_s`.

```jsonc
{ "id":"clip-3", "start_s":10.0, "end_s":13.5,
  "source_track":1, "mode":"lyrics_chords", "label":"hodit klíče do kanálu" }
```

- `start_s`/`end_s` — kdy je klip na obrazovce. Mezera mezi klipy = **prázdný /
  idle displej** (nebo náhled dalšího řádku, viz §7). Editor umí mezery
  **vyplnit** (přepínač „⇥ Vyplnit mezery na Displeji", `meta.display_fill_gaps:
  true`) — pak `end_s` každého klipu sahá až na `start_s` toho následujícího a
  idle stav v podstatě nenastane. **Pro firmware se tím nic nemění**: pořád
  jen čte `start_s`/`end_s`; jen mu dorazí soubor bez děr. Klip může nést
  i editorové pole `unfilled_end_s` (původní konec před výplní) — **ignoruj
  ho**, stejně jako každý jiný neznámý klíč.
- `source_track` — z které stopy brát obsah (odkaz na `tracks[].index`).
- `mode` — JAK renderovat:

| `mode` | Render |
|--------|--------|
| `lyrics_chords` | řádek textu + akordy nad ním (výchozí) |
| `lyrics` | jen text |
| `chords` | jen akordy (velké, doprostřed — intro/mezihra) |
| `count_in` | odpočet před písní — `artist`/`title` místo obsahu stopy, viz §6c |

(Starší soubory mohly mít i `tab`/`tab_chords` — žádná aktuální stopa už
tabulaturu nenese, ale editor je stále nabízí v menu klipu, takže se i dnes
teoreticky mohou objevit v exportu. Ošetři je jako fallback na
`lyrics_chords`. **Neznámý/nerozpoznaný `mode` obecně → fallback
`lyrics_chords`.**)

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
        case COUNT_IN:
            draw_count_in(c, t);   // artist/title z klipu + číslo z §6c
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

**Primárně páruj přes `line`:** klip nese `clip.line` = index odpovídajícího
`karaoke_lines[]` řádku (§3 `JSON_FORMAT.md`). Slova klipu = položky
`lyrics_timeline` se `track_index == source_track` a `word.line == clip.line`.
Tohle je jediný spolehlivý způsob — uživatel může v editoru klip ručně
roztáhnout/zkrátit (§3.6 `JSON_FORMAT.md`), takže `clip.start_s/end_s` se pak
**liší** od skutečného časového rozsahu slov a filtr `start_s <= time_s < end_s`
by řádek buď oříznul, nebo vynechal úplně.

Když klip `line` nemá (starší soubor, nebo klip typu `chords` — ty se s
`line` nepárují, viz §6), spadni na fallback: `track_index == source_track`
a `clip.start_s <= time_s < clip.end_s`.

Předpočítej si při načtení pro každý klip rozsah indexů (žádné hledání za
běhu).

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

Akordy = `chords_timeline` se `track_index == source_track` a `chord.time_s`
uvnitř okna aktivního klipu (`clip.start_s <= time_s < clip.end_s`) — na
rozdíl od slov (§5.1) se akordy **nepárují přes `line`**, protože typický
řádek nese jen jeden textový blok, ale víc akordů; a akordy MIMO libovolný
karaoke řádek (intro/mezihra) mají `line: null` (§3.5 `JSON_FORMAT.md`) — na
displeji je nese samostatný klip `mode: "chords"`, ne textový klip.

> **Pozor — `clip.line` na `chords`-only klipu IGNORUJ.** Editor ho
> u těchhle klipů nepoužívá k párování (`to_json()` je z logiky `line`
> párování výslovně vylučuje) a nechá ho, jak zrovna je — v reálném exportu
> se tak objevují `mode: "chords"` klipy s `line` číslem, které vůbec
> neodpovídá jejich vlastnímu času (např. `line: 0`, i když karaoke řádek 0
> je úplně jinde na ose). `line` čti **jen** u `lyrics_chords`/`lyrics`
> klipů (§5.1); u `chords` ho neexistuje-li nebo je-li přítomné, prostě
> nepoužívej.

**Zarovnání akordu vodorovně nad text** — poziční vzorec z `JSON_FORMAT.md`
§3.5, ne hledání „nejbližšího slova" (typický řádek má stejně jen jeden
blok textu na celou šířku, takže „nejbližší slovo" by dávalo jen jednu
pozici pro všechny akordy řádku):

```c
float frac = (chord.time_s - clip.start_s) / (clip.end_s - clip.start_s);
int x = text_x_start + (int)(frac * text_px_width);
```

Nakresli název akordu na `x`, o řádek výš než text. Když je akordů víc než
místa, zobraz jen změny (akord vykresli, jen když se liší od předchozího) —
stejná logika jako „chord chart" v editoru.

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
zpěv/hraní začít — proto bývá na začátku písně **tichý úvod**, kolem kterého
se **celá časová osa posune** tak, aby první skutečná nota/slovo začínala až
PO count-inu, ne se s ním nepřekrývala.

**Zdroj pravdy je klip, ne `meta`.** Editor při vložení odpočtu postaví klip
`display_timeline` s `mode: "count_in"` na `[0, meta.count_in_s)`, ale je to
obyčejný klip — uživatel ho může (stejně jako kterýkoli jiný) přetáhnout
nebo zkrátit. V reálném exportu proto bývá i posunutý (např.
`[1.846 s, 5.539 s)` při `count_in_s = 3.692 s`). **Vždy počítej odpočet
z `clip.start_s`/`clip.end_s` toho konkrétního klipu**, `meta.count_in_s`
jen jako fallback, když `display_timeline` chybí nebo v něm žádný
`count_in` klip není:

```c
// aktivní klip c má mode == COUNT_IN, t je uvnitř [c->start_s, c->end_s)
float rel_t = t - c->start_s;                            // 0 na začátku klipu
float beat_s = 60.0f / tempo_bpm;
int total_beats = count_in_bars * beats_per_measure;      // typicky 1×4 = 4
int beats_elapsed = (int)(rel_t / beat_s);                 // 0,1,2,3…
int number = total_beats - beats_elapsed;                  // 4,3,2,1
if (number >= 1) draw_big_number(number);                   // velký, uprostřed displeje
```

- **Vizuální odpočet je vždy jen ČÍSLA** (mezinárodní, nezávislé na jazyce
  displeje) — `4, 3, 2, 1`, ne slova. Žádná zvláštní data v JSONu navíc,
  jen vzorec výše.
- **Klikací stopa NENÍ vždy přítomná.** Vzniká jen tehdy, když song má bicí
  stopu (GP soubor/sloučení) — čistý web import bez GP merge `drums_timeline`
  vůbec nemá, časová osa je posunutá, ale nic necvaká. **Nespoléhej na to,
  že v okně count-in klipu bude nějaký `drum` event** — vizuální odpočet
  (kód výše) musí fungovat samostatně, klik (má-li song bicí) je jen bonus
  navíc ze stejné `drums_timeline` fronty jako běžné bicí (§6b), žádné
  extra párování. Zvuk je zatím vždy `"Closed Hi-Hat"`.
- Po opuštění count-in klipu přepni na normální render (§3–§6) podle
  dalšího klipu v `display_timeline` — žádný předpoklad o tom, kde přesně
  začíná (nemusí to být přesně na `end_s` count-in klipu, pokud song má
  ještě instrumentální úvod navíc).

**Interpret + název během odpočtu (volitelný klip `mode: "count_in"`)** — timeline
editor umí do `display_timeline` vložit klip přesně na `[0, count_in_s)` s
`"mode": "count_in"` a poli `"artist"`/`"title"` (např. `"Metallica"` /
`"One"`). Pokud je tenhle klip přítomný:

```jsonc
{ "id": "clip-count-in", "start_s": 0.0, "end_s": 4.0,
  "source_track": 1, "mode": "count_in",
  "artist": "Metallica", "title": "One", "label": "Metallica / One" }
```

- Zobraz **interpreta** tam, kde by jinak byl řádek akordů (horní řádek), a
  **název písně** tam, kde by jinak byl text (spodní řádek) — stejné dvě
  pozice jako u běžného `lyrics_chords` klipu, žádný nový layout netřeba.
- **Velké odpočítávací číslo (4,3,2,1…) se kreslí NAVÍC**, nezávisle na
  těchto dvou textových řádcích (viz kód výše) — typicky uprostřed displeje.
- Klip `mode: "count_in"` je čistě volitelný — pokud chybí (starší JSON /
  uživatel ho nevyplnil), přehrávač v okně `[0, count_in_s)` prostě nekreslí
  žádný text, jen odpočítávací číslo.

---

## 7. Idle / mezery / náhled dalšího řádku

> Pozn.: u souboru s `meta.display_fill_gaps: true` (výplň mezer zapnutá
> v editoru) mezery mezi klipy prakticky neexistují, takže se z celé téhle
> kapitoly uplatní jen „před prvním klipem" a „za posledním klipem".
> Implementuj ji ale i tak — soubory bez výplně jsou pořád platné.

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
typedef struct { float time_s, dur_s; const char* text; int line; } Word;  // UTF-8!
typedef struct { float time_s; const char* name; } Chord;
typedef enum { M_LYRICS, M_LYRICS_CHORDS, M_CHORDS, M_COUNT_IN } Mode;

typedef struct {
    float start_s, end_s;
    uint8_t source_track;
    Mode  mode;
    const char* label;
    int   line;                  // -1 když klip nemá `line` (§5.1/§6) —
                                  // párování slov pak spadne na časové okno
    const char* artist, *title;  // jen mode == M_COUNT_IN
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
už seřazená, ale nespoléhej) → pro každý `Clip` předpočítej `word_lo..hi`
(§5.1: podle `word.line == clip.line`, když `clip.line >= 0`, jinak podle
časového okna) a `chord_lo..hi` (§6: vždy podle časového okna klipu,
`chords_timeline` s `line: null` patří jen do `chords`-only klipů). **Za
běhu** už jen binární hledání v čase.

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
1. Odmítne `format_version != 2`; neznámé klíče ignoruje; neznámý `mode` →
   fallback `lyrics_chords`.
2. V každém `t` je aktivní ≤ 1 klip (klipy se nepřekrývají).
3. Řádek se objeví v `start_s` a zmizí v `end_s` odpovídajícího klipu (i po
   ručním posunu okraje — viz `JSON_FORMAT.md` §3.6).
4. Zvýrazněný/aktivní je blok, jehož `[time_s, time_s+duration_s)` obsahuje
   `t` — v typickém případě to je **celý řádek** (jeden blok), ne slovo.
5. V mezeře mezi klipy displej „nezamrzne" na starém řádku — a to i tehdy,
   když `karaoke_lines`/`lyrics_timeline` pro daný čas obsah MÁ, ale žádný
   `display_timeline` klip ho nepokrývá (viz konkrétní příklad níže —
   `display_timeline`, je-li přítomná, je vždy autoritativní, `karaoke_lines`
   je jen fallback pro její úplnou absenci, ne výplň jejích mezer).
6. Akord se kreslí nad textem podle poziční frakce v okně klipu (§6:
   `x = (chord.time_s − clip.start_s)/(clip.end_s − clip.start_s)`), ne
   podle „nejbližšího slova"; `chords` režim ukáže aktuální akord.
7. Bicí (`drums_timeline`) hrají nezávisle na displeji, i v mezerách mezi
   klipy — a i v okně `count_in` klipu, pokud song má bicí stopu (§6c);
   pokud ji nemá, `drums_timeline` může chybět úplně a zvuk se nehraje.
8. Seek na libovolný `t` zobrazí správný řádek bez „dohánění".

**Konkrétní příklad — reálný export** (`Olympic - Jasná Zpráva_final.json`,
65 BPM, `count_in_bars=1` → `count_in_s=3.692` — ale count-in KLIP je ručně
posunutý na `[1.846, 5.539)`, viz §6c). Očekávané chování:
- `t < 1.846 s` → idle, žádný klip aktivní (song ještě nezačal, klip je
  posunutý dopředu — viz §7).
- `1.846 s ≤ t < 5.539 s` → aktivní klip `clip-count-in` (`mode: "count_in"`).
  `rel_t = t − 1.846`; při `beat_s = 60/65 ≈ 0.923 s` je na `t=1.846`
  zobrazeno číslo **4**, na `t≈2.77` **3**, na `t≈3.69` **2**, na `t≈4.62`
  **1** (§6c). Horní řádek „Olympic", spodní „Jasná zpráva" (`artist`/`title`
  z klipu).
- `5.539 s ≤ t < 7.385 s` → idle (mezera před prvním akordovým klipem).
- `7.385 s ≤ t < 14.769 s` → klip `clip-29`, `mode: "chords"`, žádný `line`
  → zobraz jen akord **G** velkým fontem uprostřed (žádný text, viz §6).
- `14.769 s ≤ t < 20.808 s` → klip `clip-1`, `mode: "chords"`, akordy **G →
  Gmaj7 → Emi → C → D → Ami** postupně. Klip MÁ `line: 0`, ale ten se u
  `chords`-klipů ignoruje (viz §6 poznámka) — `karaoke_lines[0]` je úplně
  jinde na ose (`51.692–55.385 s`), takže spoléhání na `line` by tu vedlo
  ke špatnému výsledku.
- `39.269 s ≤ t < 71.077 s` → **32sekundová mezera bez klipu**, přestože
  `karaoke_lines[0..2]` (řádky „1. Skončili jsme, jasná zpráva,", „proč o
  Tebe zakopávám dál," „projít bytem já abych se bál.", časy
  `51.692–66.462 s`) v souboru existují. `display_timeline` je autoritativní
  → displej v tomhle úseku zůstává idle/prázdný, text z `karaoke_lines`
  se NEVYKRESLÍ (viz bod 5 výše — reálný, ne hypotetický případ).
- `71.077 s ≤ t < 77.538 s` → klip `clip-4`, `mode: "lyrics_chords"`,
  `line: 3` → páruj přes `line` na `karaoke_lines[3]` (ne přes časové okno —
  klip i řádek mají shodné `start_s`/`end_s`, ale spoléhat na to obecně
  nejde, viz §5.1). Řádek 3 je navíc reálný příklad **víc bloků na jednom
  `line`** (§5.1 „neočekávej to jako běžný případ" — tady přesně nastává):
  dva `lyrics_timeline` záznamy se stejným `line: 3` — „2. Dík tobě se vidím
  zvenčí," (`71.077–74.769 s`) a „připadám si starší, menší sám," (od
  `74.769 s`) — slož mezerou na „2. Dík tobě se vidím zvenčí, připadám si
  starší, menší sám," a zvýrazňuj po blocích (§5.2/§5.3), ne jako jeden
  statický celek.
- Seek na libovolné `t` (např. `t=100.0 s`) → rovnou správný klip, žádné
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
