"""Stáhne texty/akordy písní z různých webů jako VZORKY FORMÁTŮ pro import.

    python tools/stahni_texty.py              # všechny písně
    python tools/stahni_texty.py "Kabát"      # jen písně, jejichž složka obsahuje text

Ukládá SUROVOU odpověď (HTML/JSON/TXT) do `pisne/<píseň>/texty/<web>.<přípona>`
a do `pisne/<píseň>/texty/TEXTY.txt` zapíše adresu, HTTP stav, datum a co
v souboru je. Neúspěch se zapíše taky (blokace proti robotům, 404, výpadek) —
ať je vidět, odkud zkusit stáhnout ručně z prohlížeče.

Weby s ochranou proti robotům (JS výzva, 403) se NEobcházejí: zaznamená se
stav a odkaz pro ruční uložení stránky (Ctrl+S v prohlížeči).

Stažené stránky jsou cizí data — nic z nich se nespouští.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote, quote_plus

import requests

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
      "Accept-Language": "cs,en;q=0.8"}
PAUSE_S = 1.5        # mezi požadavky — nezatěžovat weby

SONGS = [  # (složka v pisne/, interpret, název, jazyk)
    ("Olympic - Jasná zpráva", "Olympic", "Jasná zpráva", "cs"),
    ("Arakain - Zimní královna", "Arakain", "Zimní královna", "cs"),
    ("Kabát - Pohoda", "Kabát", "Pohoda", "cs"),
    ("Kabát - Bára", "Kabát", "Bára", "cs"),
    ("Beatles - Let It Be", "The Beatles", "Let It Be", "en"),
    ("Iron Maiden - Run To the Hills", "Iron Maiden", "Run to the Hills", "en"),
    ("Guns N Roses - Knockin on Heavens Door", "Guns N' Roses", "Knockin' on Heaven's Door", "en"),
    ("Led Zeppelin - Stairway to Heaven", "Led Zeppelin", "Stairway to Heaven", "en"),
]


def norm(s: str) -> str:
    """bez diakritiky, malá písmena, jen písmena/číslice"""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


class Downloader:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(UA)
        self._last = 0.0

    def get(self, url: str, **kw) -> requests.Response | None:
        wait = PAUSE_S - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        try:
            return self.s.get(url, timeout=25, **kw)
        except requests.RequestException as ex:
            print("   ! %s: %s" % (url, type(ex).__name__))
            return None


def blocked(r: requests.Response | None) -> str:
    """Rozpozná ochranu proti robotům / nedostupnost — vrátí popis, jinak ''."""
    if r is None:
        return "nedostupné (chyba spojení)"
    if r.status_code in (403, 429):
        return "HTTP %d — ochrana proti robotům" % r.status_code
    if r.status_code >= 500:
        return "HTTP %d — web nedostupný" % r.status_code
    if r.status_code == 404:
        return "HTTP 404 — stránka neexistuje"
    if 'data-n="' in r.text[:800] and "smc.js" in r.text[:800]:
        return "JS výzva proti robotům (supermusic)"
    if "captcha" in r.text[:3000].lower() and len(r.text) < 20000:
        return "captcha"
    return ""


# --- weby: každý vrací [(přípona_souboru, obsah_bytes, url, popis)] nebo [] + log --------

def site_karaoketexty(d, artist, title, lang, log):
    r = d.get("https://www.karaoketexty.cz/search?q=" + quote_plus(title))
    if blocked(r):
        return log("karaoketexty", r.url if r else "", blocked(r))
    a = slug(artist.replace("The ", ""))
    cands = [u for u in re.findall(r'href="(/texty-pisni/[^"]+-\d+)"', r.text)
             if u.split("/")[2].startswith(a.split("-")[0]) and norm(title)[:8] in norm(u)]
    if not cands:
        return log("karaoketexty", r.url, "nenalezeno ve výsledcích")
    url = "https://www.karaoketexty.cz" + cands[0]
    r = d.get(url)
    if blocked(r):
        return log("karaoketexty", url, blocked(r))
    return [("karaoketexty.html", r.content, url, "HTML — čistý text (bez akordů), řádky <br>")]


def site_ultimate_guitar(d, artist, title, lang, log):
    r = d.get("https://www.ultimate-guitar.com/search.php?search_type=title&value="
              + quote(artist + " " + title))
    if blocked(r):
        return log("ultimate-guitar", r.url if r else "", blocked(r))
    m = re.search(r'class="js-store" data-content="([^"]+)"', r.text)
    if not m:
        return log("ultimate-guitar", r.url, "chybí js-store ve výsledcích")
    data = json.loads(_html.unescape(m.group(1)))
    res = [x for x in data["store"]["page"]["data"].get("results", []) if isinstance(x, dict)]
    res = [x for x in res if x.get("type") == "Chords" and norm(artist.replace("The ", ""))[:6]
           in norm(x.get("artist_name", "")) and norm(title)[:8] in norm(x.get("song_name", ""))]
    if not res:
        return log("ultimate-guitar", r.url, "žádné akordy (Chords) ve výsledcích")
    best = max(res, key=lambda x: (x.get("votes") or 0) * (x.get("rating") or 0))
    url = best["tab_url"]
    r = d.get(url)
    if blocked(r):
        return log("ultimate-guitar", url, blocked(r))
    out = [("ultimate-guitar.html", r.content, url, "HTML — obsah v JSONu js-store")]
    m = re.search(r'class="js-store" data-content="([^"]+)"', r.text)
    if m:
        tab = json.loads(_html.unescape(m.group(1)))["store"]["page"]["data"]
        content = (tab.get("tab_view", {}).get("wiki_tab", {}) or {}).get("content", "")
        if content:
            out.append(("ultimate-guitar.ug.txt", content.encode("utf-8"), url,
                        "TXT — značkování UG: [ch]akord[/ch] nad textem, [tab]…[/tab], [Verse]"))
    return out


def site_genius(d, artist, title, lang, log):
    r = d.get("https://genius.com/api/search/multi?q=" + quote(artist + " " + title))
    if blocked(r):
        return log("genius", r.url if r else "", blocked(r))
    hits = [h["result"] for sec in r.json()["response"]["sections"] for h in sec.get("hits", [])
            if h.get("type") == "song"]
    hits = [h for h in hits if norm(title)[:8] in norm(h.get("title", ""))
            and norm(artist.replace("The ", ""))[:5] in norm(h.get("primary_artist", {}).get("name", ""))]
    if not hits:
        return log("genius", r.url, "nenalezeno")
    url = hits[0]["url"]
    r = d.get(url)
    if blocked(r):
        return log("genius", url, blocked(r))
    return [("genius.html", r.content, url,
             "HTML — text v <div data-lyrics-container>, sekce [Verse 1]/[Chorus], bez akordů")]


def site_lyrics_ovh(d, artist, title, lang, log):
    url = "https://api.lyrics.ovh/v1/%s/%s" % (quote(artist.replace("The ", "")), quote(title))
    r = d.get(url)
    if blocked(r) or r.status_code != 200:
        return log("lyrics.ovh", url, blocked(r) or "HTTP %s" % (r.status_code if r else "?"))
    return [("lyrics-ovh.json", r.content, url, "JSON — {lyrics: prostý text, \\r\\n/\\n}")]


def site_lrclib(d, artist, title, lang, log):
    url = ("https://lrclib.net/api/get?artist_name=%s&track_name=%s"
           % (quote(artist), quote(title)))
    r = d.get(url)
    if blocked(r) or r.status_code != 200:
        return log("lrclib", url, blocked(r) or "HTTP %s" % (r.status_code if r else "?"))
    out = [("lrclib.json", r.content, url, "JSON — plainLyrics + syncedLyrics (LRC)")]
    synced = (r.json() or {}).get("syncedLyrics")
    if synced:
        out.append(("lrclib.lrc", synced.encode("utf-8"), url, "LRC — [mm:ss.xx] řádky s časem"))
    return out


def site_guitaretab(d, artist, title, lang, log):
    r = d.get("https://www.guitaretab.com/fetch/?type=tab&query=" + quote_plus(title))
    if blocked(r):
        return log("guitaretab", r.url if r else "", blocked(r))
    a = norm(artist.replace("The ", ""))
    cands = []
    for m in re.finditer(r'<a[^>]+href="(/[a-z0-9]/([^/"]+)/\d+\.html)"[^>]*>(.*?)</a>', r.text, re.S):
        label = text_of(m.group(3))
        if norm(m.group(2))[:6] == a[:6] and norm(title)[:8] in norm(label) and "chords" in label.lower():
            cands.append((m.group(1), label))
    if not cands:
        return log("guitaretab", r.url, "žádné akordy ve výsledcích")
    url = "https://www.guitaretab.com" + cands[0][0]
    r = d.get(url)
    if blocked(r):
        return log("guitaretab", url, blocked(r))
    return [("guitaretab.html", r.content, url, "HTML — akordy nad textem v <pre>, %s" % cands[0][1])]


def site_chordie(d, artist, title, lang, log):
    r = d.get("https://www.chordie.com/results.php?q=" + quote_plus(title + " " + artist))
    if blocked(r):
        return log("chordie", r.url if r else "", blocked(r))
    a = norm(artist.replace("The ", ""))
    for m in re.finditer(r'<a[^>]+href="(/chord\.pere/[^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
        label = text_of(m.group(2))
        if norm(title)[:8] in norm(label) and a[:5] in norm(label):
            url = "https://www.chordie.com" + m.group(1)
            r2 = d.get(url)
            if blocked(r2):
                return log("chordie", url, blocked(r2))
            return [("chordie.html", r2.content, url,
                     "HTML — akordy vložené do textu [C] (ChordPro styl) nebo nad textem")]
    return log("chordie", r.url, "nenalezeno ve výsledcích")


def site_musixmatch(d, artist, title, lang, log):
    url = "https://www.musixmatch.com/lyrics/%s/%s" % (
        "-".join(re.sub(r"[^\w ]", "", artist).split()), "-".join(re.sub(r"[^\w ]", "", title).split()))
    r = d.get(url)
    if blocked(r):
        return log("musixmatch", url, blocked(r))
    return [("musixmatch.html", r.content, r.url, "HTML — text v JSONu __NEXT_DATA__")]


def site_blocked_cz(d, artist, title, lang, log):
    """České weby s ochranou proti robotům — jen zkusit a zapsat, neobcházet."""
    out = []
    for site, url in (
            ("pisnicky-akordy", "https://pisnicky-akordy.cz/%s/%s" % (slug(artist), slug(title))),
            ("supermusic", "https://www.supermusic.cz/najdi.php?hladane=%s&typhladania=piesen&fraza=off"
             % quote_plus(title)),
            ("velkyzpevnik", "https://www.velkyzpevnik.cz/%s/%s" % (slug(artist), slug(title)))):
        r = d.get(url)
        why = blocked(r)
        if site == "supermusic" and not why:
            # výsledky hledání projdou, stránka písně má JS výzvu
            ids = re.findall(r'href="(piesen\.php\?id=\d+)"', r.text)
            if ids:
                url2 = "https://www.supermusic.cz/" + ids[0]
                r2 = d.get(url2)
                why = blocked(r2)
                if not why:
                    out.append(("supermusic.html", r2.content, url2, "HTML — akordy v <sup>"))
                    continue
                url = url2
            else:
                why = "nenalezeno ve výsledcích"
        if why:
            log(site, url, why)
        else:
            out.append((site + ".html", r.content, url, "HTML"))
    return out


SITES = [site_karaoketexty, site_ultimate_guitar, site_genius, site_lyrics_ovh, site_lrclib,
         site_guitaretab, site_chordie, site_musixmatch, site_blocked_cz]


def main(argv) -> int:
    only = argv[1] if len(argv) > 1 else ""
    d = Downloader()
    today = _dt.date.today().isoformat()
    for folder, artist, title, lang in SONGS:
        if only and only.lower() not in folder.lower():
            continue
        dst = ROOT / "pisne" / folder / "texty"
        dst.mkdir(parents=True, exist_ok=True)
        print("==", folder)
        index = ["Texty a akordy k písni „%s – %s“ (staženo %s)." % (artist, title, today),
                 "Soubor | web | adresa | co v něm je / proč chybí", ""]

        def log(site, url, why):
            index.append("—  | %s | %s | NESTAŽENO: %s" % (site, url, why))
            print("   - %-16s %s" % (site, why))
            return []

        for fn in SITES:
            try:
                results = fn(d, artist, title, lang, log) or []
            except Exception as ex:   # noqa: BLE001 — jeden web nesmí shodit ostatní
                results = log(fn.__name__[5:].replace("_", "-"), "", "chyba zpracování: %s" % ex)
            for name, content, url, what in results:
                (dst / name).write_bytes(content)
                index.append("%s | %s | %s | %s" % (name, fn.__name__[5:].replace("_", "-"), url, what))
                print("   + %-24s %7d B" % (name, len(content)))
        (dst / "TEXTY.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
