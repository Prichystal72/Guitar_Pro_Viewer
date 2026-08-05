"""
i18n.py — lehká vlastní překladová vrstva (CS/EN), bez Qt Linguist/.ts/.qm.

Použití:
    from i18n import tr, tr_action, register_tr, get_language, set_language

    label = QLabel()
    register_tr(label.setText, "some.key")

    act = tr_action(self, "file.open", tooltip_key="file.open.tooltip",
                     shortcut="Ctrl+O", slot=self.open_file)

Retranslace funguje přes REGISTR naplněný PŘI KONSTRUKCI widgetu (ne pár
_build_ui()/retranslate_ui() metod, které by se dřív nebo později rozešly) —
`register_tr()` si zapamatuje (setter, klíč, volitelná kwargs funkce) a hned
zavolá setter poprvé; `set_language()` pak jen znovu přehraje celý registr.

Transientní řetězce (QMessageBox, QInputDialog, stavový řádek) žádný registr
nepotřebují — stačí `tr(key, **kwargs)` přímo na místě volání, protože se
staví nanovo při každém zobrazení a tak přirozeně použijí aktuální jazyk.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Optional

DEFAULT_LANG = "cs"
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

_current_lang = DEFAULT_LANG


# ---------------------------------------------------------------------------
# Perzistence jazyka
# ---------------------------------------------------------------------------

def load_settings() -> None:
    """Načte uložený jazyk ze settings.json (voláno v main() PŘED vytvořením
    hlavního okna, aby první sestavení UI už bylo ve správném jazyce)."""
    global _current_lang
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        lang = data.get("language", DEFAULT_LANG)
        _current_lang = lang if lang in ("cs", "en") else DEFAULT_LANG
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _current_lang = DEFAULT_LANG


def save_settings() -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"language": _current_lang}, f, indent=2, ensure_ascii=False)
    except OSError:
        pass   # jazyk se prostě nezapamatuje pro příště, nic fatálního


def get_language() -> str:
    return _current_lang


def set_language(lang: str) -> None:
    global _current_lang
    if lang not in ("cs", "en") or lang == _current_lang:
        return
    _current_lang = lang
    save_settings()
    _registry.retranslate()


# ---------------------------------------------------------------------------
# Registr pro živé přepínání jazyka za běhu
# ---------------------------------------------------------------------------

class _Registry:
    def __init__(self) -> None:
        self._entries: list[tuple[Callable[[str], None], str, Optional[Callable[[], dict]]]] = []

    def add(self, setter: Callable[[str], None], key: str,
            kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
        self._entries.append((setter, key, kwargs_fn))
        setter(tr(key, **(kwargs_fn() if kwargs_fn else {})))

    def retranslate(self) -> None:
        for setter, key, kwargs_fn in self._entries:
            setter(tr(key, **(kwargs_fn() if kwargs_fn else {})))


_registry = _Registry()


def register_tr(setter: Callable[[str], None], key: str,
                 kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
    """Zaregistruje `setter` (libovolný 1-argumentový callable, např.
    `widget.setText`) tak, aby se při přepnutí jazyka znovu zavolal se
    znovu-přeloženým textem. Zavolá ho i hned napoprvé."""
    _registry.add(setter, key, kwargs_fn)


def tr(key: str, **kwargs) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return f"⚠MISSING:{key}"
    template = entry.get(_current_lang) or entry.get(DEFAULT_LANG) or key
    return template.format(**kwargs) if kwargs else template


def tr_action(parent, key: str, tooltip_key: Optional[str] = None,
              shortcut: Optional[str] = None, icon=None, slot=None,
              checkable: bool = False, shortcuts: Optional[list] = None):
    """Vytvoří QAction a rovnou zapojí text (+volitelně tooltip) do
    překladového registru — nahrazuje typický 3-4řádkový blok
    QAction()/setShortcut()/setToolTip()/triggered.connect()."""
    from PySide6.QtGui import QAction
    act = QAction(parent)
    if icon is not None:
        act.setIcon(icon)
    if shortcuts:
        act.setShortcuts(shortcuts)
    elif shortcut:
        act.setShortcut(shortcut)
    if checkable:
        act.setCheckable(True)
    if slot is not None:
        act.triggered.connect(slot)
    register_tr(act.setText, key)
    if tooltip_key:
        register_tr(act.setToolTip, tooltip_key)
    return act


def tr_label(widget, key: str, kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
    register_tr(widget.setText, key, kwargs_fn)


def tr_dock_title(dock, key: str) -> None:
    """QDockWidget titulek NENÍ totéž co jeho toggleViewAction().setText() —
    obojí potřebuje vlastní register_tr volání, jinak po přepnutí jazyka
    zůstane titulek plovoucího docku ve starém jazyce."""
    register_tr(dock.setWindowTitle, key)


def tr_tab(tabs, index: int, key: str) -> None:
    register_tr(lambda t, _tabs=tabs, _i=index: _tabs.setTabText(_i, t), key)


def tr_window_title(window, key: str, kwargs_fn: Optional[Callable[[], dict]] = None) -> None:
    register_tr(window.setWindowTitle, key, kwargs_fn)


# ---------------------------------------------------------------------------
# Slovník řetězců. Klíče tečkované podle skupin menu/oblastí; `.tooltip`
# přípona pro tooltip stejné akce. Sekce odpovídají pořadí sweep fáze.
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {

    # --- common / sdílené ---
    "common.warning_title": {"cs": "Upozornění", "en": "Warning"},
    "common.error_title": {"cs": "Chyba", "en": "Error"},
    "common.info_title": {"cs": "Informace", "en": "Information"},
    "common.confirm_title": {"cs": "Potvrzení", "en": "Confirm"},

    # --- menu: Soubor / File ---
    "menu.file": {"cs": "&Soubor", "en": "&File"},
    "file.new_from_web": {"cs": "📝 Nová píseň — z webu / vlastní text…",
                            "en": "📝 New Song — from web / custom text…"},
    "file.new_from_web.tooltip": {
        "cs": "Načte text a akordy z webové stránky nebo z vlastního textu.",
        "en": "Loads lyrics and chords from a web page or custom text."},
    "file.merge_web": {"cs": "🎵➕🥁 Sloučit s webem (text+akordy) + bicí z GP…",
                         "en": "🎵➕🥁 Merge with web (lyrics+chords) + drums from GP…"},
    "file.merge_web.tooltip": {
        "cs": "Sloučí text/akordy z webu s bicí (a případně basou) z Guitar Pro souboru.",
        "en": "Merges lyrics/chords from the web with drums (and optionally bass) from a Guitar Pro file."},
    "file.open_gp": {"cs": "Otevřít Guitar Pro soubor…", "en": "Open Guitar Pro File…"},
    "file.open_json": {"cs": "Otevřít Karaoke JSON…", "en": "Open Karaoke JSON…"},
    "file.export": {"cs": "Exportovat Karaoke JSON…", "en": "Export Karaoke JSON…"},
    "file.quit": {"cs": "Konec", "en": "Quit"},

    # --- menu: Úpravy / Edit ---
    "menu.edit": {"cs": "&Úpravy", "en": "&Edit"},
    "edit.undo": {"cs": "↶ Zpět", "en": "↶ Undo"},
    "edit.undo.tooltip": {"cs": "Zpět (Ctrl+Z)", "en": "Undo (Ctrl+Z)"},
    "edit.redo": {"cs": "↷ Znovu", "en": "↷ Redo"},
    "edit.redo.tooltip": {"cs": "Znovu (Ctrl+Y / Ctrl+Shift+Z)", "en": "Redo (Ctrl+Y / Ctrl+Shift+Z)"},
    "edit.split": {"cs": "✂ Rozdělit v kurzoru", "en": "✂ Split at Playhead"},
    "edit.split.tooltip": {"cs": "Rozdělí vybraný klip v pozici kurzoru (klávesa S)",
                             "en": "Splits the selected clip at the playhead position (key S)"},
    "edit.delete": {"cs": "🗑 Smazat vybrané", "en": "🗑 Delete Selected"},
    "edit.shift_selected": {"cs": "↔ Posunout vybrané…", "en": "↔ Shift Selected…"},
    "edit.shift_selected.tooltip": {
        "cs": "Posune VŠECHNY vybrané prvky o přesný čas (parametricky, ne tažením).\n"
              "Nejdřív vyber víc prvků: Shift/Ctrl+klik, tažení obdélníku, nebo pravým "
              "klikem na prvek → „Vybrat od zde DÁL/DŘÍV v čase“ (zkratky ]/[).",
        "en": "Shifts ALL selected elements by an exact amount of time (parametric, not by dragging).\n"
              "First select multiple elements: Shift/Ctrl+click, rubber-band select, or right-click "
              "an element → \"Select from here FORWARD/BACKWARD in time\" (shortcuts ]/[)."},

    # --- menu: Časová osa / Timeline ---
    "menu.timeline": {"cs": "Č&asová osa", "en": "&Timeline"},
    "timeline.add_clip": {"cs": "＋ Klip displeje", "en": "＋ Display Clip"},
    "timeline.align_song": {"cs": "🔄 Zarovnat displej (celá píseň)", "en": "🔄 Re-sync Display (whole song)"},
    "timeline.align_song.tooltip": {
        "cs": "U VŠECH klipů na Displej stopě zruší ruční posun a obnoví "
              "auto-sledování textu/akordů (klip = přesně to, co je na ose) — "
              "trvalé tlačítko v liště, nemusíš klikat na každý klip zvlášť "
              "přes pravé tlačítko myši.",
        "en": "For ALL clips on the Display track, clears manual overrides and restores "
              "auto-tracking of lyrics/chords (clip = exactly what's on the timeline) — "
              "a persistent action, no need to right-click each clip individually."},
    "timeline.add_lyric": {"cs": "＋ Text", "en": "＋ Lyric"},
    "timeline.add_chord": {"cs": "＋ Akord", "en": "＋ Chord"},
    "timeline.autotime": {"cs": "⏱ Na mřížku…", "en": "⏱ Snap to Grid…"},
    "timeline.autotime.tooltip": {
        "cs": "Přichytí začátky bloků na hudební mřížku (takt/beat podle tempa) — žádné odhady.",
        "en": "Snaps block starts to the musical grid (bar/beat based on tempo) — no guessing."},
    "timeline.tempo": {"cs": "✏️ Tempo…", "en": "✏️ Tempo…"},
    "timeline.tempo.tooltip": {
        "cs": "Změní BPM písně. Volitelně přepočítá existující časy proporcionálně "
              "(staré tempo / nové tempo).",
        "en": "Changes the song's BPM. Optionally rescales existing times proportionally "
              "(old tempo / new tempo)."},
    "timeline.count_in": {"cs": "🥁⏱ Odpočet…", "en": "🥁⏱ Count-in…"},
    "timeline.count_in.tooltip": {
        "cs": "Vloží/upraví odpočet (count-in) před písní — automaticky posune "
              "vše ostatní, žádné ruční přesouvání stop.",
        "en": "Inserts/adjusts a count-in before the song — automatically shifts "
              "everything else, no manual track shifting."},
    "timeline.snap_submenu": {"cs": "Přichytit k mřížce", "en": "Snap to Grid"},
    "timeline.snap_off": {"cs": "vypnuto", "en": "off"},
    "timeline.snap_q_beat": {"cs": "1/4 beatu", "en": "1/4 beat"},
    "timeline.snap_h_beat": {"cs": "1/2 beatu", "en": "1/2 beat"},
    "timeline.snap_beat": {"cs": "1 beat", "en": "1 beat"},
    "timeline.snap_bar": {"cs": "1 takt", "en": "1 bar"},
    "timeline.snap_label": {"cs": "Přichytit:", "en": "Snap:"},
    "timeline.snap.tooltip": {
        "cs": "Přichycení tažení k hudební mřížce (dle tempa písně) — ne k pevným sekundám.",
        "en": "Snaps dragging to the musical grid (based on song tempo) — not fixed seconds."},
    "timeline.time_label.tooltip": {
        "cs": "Pozice kurzoru — klikni do pravítka nebo táhni červený kurzor",
        "en": "Playhead position — click the ruler or drag the red cursor"},
    "timeline.zoom_label": {"cs": "Zoom:", "en": "Zoom:"},

    # --- menu: Zobrazit / View ---
    "menu.view": {"cs": "&Zobrazit", "en": "&View"},
    "view.tracks_panel": {"cs": "Panel stop", "en": "Tracks Panel"},
    "view.previews_panel": {"cs": "Panel náhledů (Chord Chart, JSON…)",
                              "en": "Previews Panel (Chord Chart, JSON…)"},
    "view.zoom_in": {"cs": "Přiblížit", "en": "Zoom In"},
    "view.zoom_out": {"cs": "Oddálit", "en": "Zoom Out"},
    "view.language_submenu": {"cs": "Jazyk", "en": "Language"},
    "view.language_cs": {"cs": "Čeština", "en": "Czech"},
    "view.language_en": {"cs": "English", "en": "English"},

    # --- menu: Nápověda / Help ---
    "menu.help": {"cs": "Nápo&věda", "en": "&Help"},
    "help.timeline_help": {"cs": "Nápověda k časové ose", "en": "Timeline Help"},
    "help.timeline_help.body": {
        "cs": "Displej = master stopa (co uvidí karaoke) · dvojklik klip = zdroj+režim · "
              "táhni okraje = délka · pravý klik = režim/smazat · Ctrl+kolečko = zoom\n"
              "] / [ nebo pravý klik → „Vybrat od zde DÁL/DŘÍV v čase“ = vyber prvek a "
              "vše odpovídající po/před ním na stejné stopě (text+akordy dohromady) "
              "→ táhni myší nebo „↔ Posunout vybrané…“ pro přesný posun "
              "(hromadné přeřazení zbytku)",
        "en": "Display = master track (what karaoke shows) · double-click clip = source+mode · "
              "drag edges = length · right-click = mode/delete · Ctrl+wheel = zoom\n"
              "] / [ or right-click → \"Select from here FORWARD/BACKWARD in time\" = select an "
              "element and everything matching after/before it on the same track (lyrics+chords "
              "together) → drag with mouse or \"↔ Shift Selected…\" for an exact shift "
              "(bulk-move the rest)"},
    "help.about": {"cs": "O aplikaci", "en": "About"},
    # --- export (sdíleno mezi menu, toolbarem i tlačítky v editoru) ---
    "export.warn_nothing_loaded": {
        "cs": "Nejprve otevřete Guitar Pro soubor nebo JSON.",
        "en": "First open a Guitar Pro file or JSON."},
    "export.save_dialog_title": {"cs": "Uložit Karaoke JSON", "en": "Save Karaoke JSON"},
    "export.save_dialog_filter": {
        "cs": "JSON soubory (*.json);;Všechny soubory (*)",
        "en": "JSON files (*.json);;All files (*)"},
    "export.status_exporting": {"cs": "Exportuji JSON…", "en": "Exporting JSON…"},
    "export.done_title": {"cs": "Export hotov", "en": "Export Complete"},
    "export.done_body": {
        "cs": "Exportováno: {path}\n\n"
              "  Stopy (text/akordy/bicí): {n_tracks}\n"
              "  Lyrics events: {n_lyrics}\n"
              "  Chord events: {n_chords}\n"
              "  Drum hitů: {n_drums}\n"
              "  Karaoke řádků: {n_lines}",
        "en": "Exported: {path}\n\n"
              "  Tracks (lyrics/chords/drums): {n_tracks}\n"
              "  Lyrics events: {n_lyrics}\n"
              "  Chord events: {n_chords}\n"
              "  Drum hits: {n_drums}\n"
              "  Karaoke lines: {n_lines}"},
    "export.status_done": {"cs": "Exportováno → {path}", "en": "Exported → {path}"},
    "export.error_title": {"cs": "Chyba exportu", "en": "Export Error"},
    "export.status_error": {"cs": "Chyba při exportu.", "en": "Export failed."},

    # --- hlavní okno: titulek, docky, levý panel ---
    "app.window_title": {"cs": "Guitar Pro Viewer — Karaoke Exporter",
                           "en": "Guitar Pro Viewer — Karaoke Exporter"},
    "dock.tracks_title": {"cs": "Stopy", "en": "Tracks"},
    "dock.previews_title": {"cs": "Náhledy — Chord Chart, Noty, JSON…",
                              "en": "Previews — Chord Chart, Tab, JSON…"},
    "left.track_tree_name": {"cs": "Název", "en": "Name"},
    "left.track_tree_type": {"cs": "Typ", "en": "Type"},
    "left.export_all_tracks": {"cs": "Exportovat všechny stopy", "en": "Export all tracks"},
    "left.export_button": {"cs": "💾  Export Karaoke JSON", "en": "💾  Export Karaoke JSON"},
    "left.mix_title": {"cs": "Mix časové osy", "en": "Timeline Mix"},
    "tab.chord_chart": {"cs": "🎸 Chord Chart", "en": "🎸 Chord Chart"},
    "tab.tab_notation": {"cs": "Noty / Tabulatura", "en": "Notation / Tab"},
    "tab.overview": {"cs": "Přehled skladby", "en": "Song Overview"},
    "tab.lyrics": {"cs": "Text / Slova", "en": "Lyrics"},
    "tab.chords": {"cs": "Akordy", "en": "Chords"},
    "tab.json_preview": {"cs": "JSON náhled", "en": "JSON Preview"},
    "status.ready": {"cs": "Připraven — Otevřete Guitar Pro soubor (Ctrl+O)",
                       "en": "Ready — Open a Guitar Pro file (Ctrl+O)"},
    "toolbar.main": {"cs": "Hlavní panel", "en": "Main Toolbar"},
    "toolbar.timeline": {"cs": "Časová osa", "en": "Timeline"},

    # --- spodní audio/přehrávací lišta (timeline_editor.py audio_bar) ---
    "audio.load": {"cs": "🎵 Načíst MP3/WAV…", "en": "🎵 Load MP3/WAV…"},
    "audio.no_audio": {"cs": "(žádné audio)", "en": "(no audio)"},
    "audio.output_label": {"cs": "Výstup:", "en": "Output:"},
    "audio.device_combo.tooltip": {
        "cs": "Zvukové zařízení, na které přehrávač i testovací tón hrají — "
              "Windows „výchozí“ nemusí být to, co zrovna posloucháš "
              "(např. při více připojených sluchátkách/Voicemeeru).",
        "en": "The audio device the player and test tone play through — Windows "
              "\"default\" may not be what you're actually listening on "
              "(e.g. with multiple headphones/Voicemeter connected)."},
    "audio.refresh.tooltip": {"cs": "Znovu načíst seznam zvukových zařízení",
                                "en": "Reload the list of audio devices"},
    "audio.test_tone": {"cs": "🔊 Test tón", "en": "🔊 Test Tone"},
    "audio.test_tone.tooltip": {
        "cs": "Přehraje krátký pípák na vybraném zařízení — ověř TÍMHLE, že "
              "je vůbec slyšet něco, než budeš hledat problém v písničce.",
        "en": "Plays a short beep on the selected device — use THIS to verify "
              "you can hear anything at all before troubleshooting the song."},
    "audio.waveform_pos": {"cs": "🎚 Poloha/roztažení…", "en": "🎚 Position/Stretch…"},
    "audio.waveform_pos.tooltip": {
        "cs": "Přesný posun nahrávky v čase a ±10% roztažení délky (pro "
              "sladění s kulatým BPM) — totéž, co jde tažením myší po "
              "vlnovce nad zdrojovými stopami.",
        "en": "Exact time offset for the recording and ±10% length stretch (to "
              "align with a round BPM) — the same as dragging the waveform "
              "above the source tracks with the mouse."},
    "audio.auto_align": {"cs": "🥁 Auto-zarovnat dle rytmu", "en": "🥁 Auto-Align to Rhythm"},
    "audio.auto_align.tooltip": {
        "cs": "PLNĚ AUTOMATICKY: najde všechny výrazné údery v nahrávce a "
              "spočítá takový posun+roztažení (v rámci ±10 %), aby jich co "
              "nejvíc sedělo na mřížku aktuálního tempa — žádné ruční "
              "klikání na body.",
        "en": "FULLY AUTOMATIC: finds every strong hit in the recording and "
              "computes an offset+stretch (within ±10%) so as many as possible "
              "land on the current tempo's grid — no manual point-clicking."},
    "audio.check_duration": {"cs": "🔎 Ověřit délku", "en": "🔎 Verify Duration"},
    "audio.check_duration.tooltip": {
        "cs": "Jen informativně porovná vypočtenou délku písně (dle tempa/"
              "taktů) se skutečnou délkou nahrávky — nic sám neupravuje, "
              "ověření/rozhodnutí je na tobě.",
        "en": "Purely informational comparison of the computed song length (from "
              "tempo/bars) against the actual recording length — changes nothing "
              "itself, the verification/decision is yours."},
    "audio.gp_mix": {"cs": "🎼 GP bicí mix", "en": "🎼 GP Drum Mix"},
    "audio.gp_mix.tooltip": {
        "cs": "Vygeneruje/přegeneruje syntetizovaný zvuk bicích PŘÍMO z GP "
              "dat (drums_timeline) a přehraje ho druhým přehrávačem vedle "
              "skutečné nahrávky — poslechem porovnáš, co říká GP soubor, "
              "se skutečností. Sdílí play/pauza/stop/shuttle s nahrávkou, "
              "hlasitost obou zvlášť níž. Čistě pro poslechové ověření "
              "ČLOVĚKEM — nic tím sám neopravuji.",
        "en": "Generates/regenerates a synthesized drum sound DIRECTLY from GP "
              "data (drums_timeline) and plays it through a second player "
              "alongside the real recording — listen to compare what the GP "
              "file says against reality. Shares play/pause/stop/shuttle with "
              "the recording; volume for each is set separately below. Purely "
              "for HUMAN listening verification — nothing is auto-corrected."},

    # --- otevírání/import souborů, sloučení s webem ---
    "web.new_song_desc": {"cs": "Nová píseň (web/text)", "en": "New song (web/text)"},
    "file.open_gp.dialog_title": {"cs": "Otevřít Guitar Pro soubor", "en": "Open Guitar Pro File"},
    "file.open_gp.dialog_filter": {
        "cs": "Guitar Pro (*.gp3 *.gp4 *.gp5 *.gpx *.gp);;Všechny soubory (*)",
        "en": "Guitar Pro (*.gp3 *.gp4 *.gp5 *.gpx *.gp);;All files (*)"},
    "status.loading": {"cs": "Načítám: {path} …", "en": "Loading: {path} …"},
    "file.open_json.dialog_title": {"cs": "Otevřít Karaoke JSON", "en": "Open Karaoke JSON"},
    "file.open_json.dialog_filter": {
        "cs": "Karaoke JSON (*.json);;Všechny soubory (*)",
        "en": "Karaoke JSON (*.json);;All files (*)"},
    "json.load_error_body": {"cs": "Nelze načíst JSON:\n\n{ex}", "en": "Could not load JSON:\n\n{ex}"},
    "status.json_load_error": {"cs": "Chyba načítání JSON.", "en": "Error loading JSON."},
    "present.meta_line": {
        "cs": "Tempo: {tempo} BPM  |  {n_tracks} stop  |  {n_lines} řádků  |  {n_drums} úderů bicích",
        "en": "Tempo: {tempo} BPM  |  {n_tracks} tracks  |  {n_lines} lines  |  {n_drums} drum hits"},
    "present.meta_bass_suffix": {"cs": "  |  {n_bass} not basy", "en": "  |  {n_bass} bass notes"},
    "present.no_gp_note": {
        "cs": "(Karaoke data — tabulatury a detaily stop nejsou k dispozici.)",
        "en": "(Karaoke data — tablature and track details are not available.)"},
    "status.loaded_lines": {"cs": "Načteno ({source}) — {n_lines} karaoke řádků",
                              "en": "Loaded ({source}) — {n_lines} karaoke lines"},
    "merge.need_gp_title": {"cs": "Nejdřív Guitar Pro soubor", "en": "Guitar Pro File First"},
    "merge.need_gp_body": {
        "cs": "Nejprve otevři Guitar Pro soubor (Ctrl+O) — z něj se vezmou "
              "bicí a časování.\nPak sem vlož URL písně z webu (text + akordy).",
        "en": "First open a Guitar Pro file (Ctrl+O) — drums and timing are taken "
              "from it.\nThen paste the song's URL from the web here (lyrics + chords)."},
    "merge.url_dialog_title": {"cs": "Sloučit s webem", "en": "Merge with Web"},
    "merge.url_dialog_label": {"cs": "URL písně (např. pisnicky-akordy.cz/…):",
                                 "en": "Song URL (e.g. pisnicky-akordy.cz/…):"},
    "status.merging": {"cs": "Stahuji a slučuji s webem…", "en": "Downloading and merging with web…"},
    "merge.error_title": {"cs": "Chyba slučování", "en": "Merge Error"},
    "status.merge_error": {"cs": "Chyba slučování s webem.", "en": "Error merging with web."},
    "merge.done_title": {"cs": "Sloučeno", "en": "Merged"},
    "merge.done_body": {
        "cs": "Sloučeno s webem:\n\n"
              "  Řádků: {n_lines}\n"
              "  Akordů: {n_chords}\n"
              "  Úderů bicích: {n_drums}\n"
              "  Not basy: {n_bass}\n\n"
              "Uprav řádky/akordy v editoru a exportuj (Ctrl+E).",
        "en": "Merged with web:\n\n"
              "  Lines: {n_lines}\n"
              "  Chords: {n_chords}\n"
              "  Drum hits: {n_drums}\n"
              "  Bass notes: {n_bass}\n\n"
              "Edit lines/chords in the editor and export (Ctrl+E)."},
    "status.loaded_file": {"cs": "Načteno: {path}", "en": "Loaded: {path}"},
    "source.web_gp_drums_bass": {"cs": "web+GP bicí/basa: {gp_name}", "en": "web+GP drums/bass: {gp_name}"},
    "source.gp_web": {"cs": "GP+web: {url}", "en": "GP+web: {url}"},
    "source.json": {"cs": "JSON: {path}", "en": "JSON: {path}"},
    "merge.added_hits": {"cs": "{n} úderů bicích", "en": "{n} drum hits"},
    "merge.added_bass": {"cs": "{n} not basy", "en": "{n} bass notes"},
    "merge.added_title": {"cs": "Přidány stopy z GP", "en": "Tracks Added from GP"},
    "merge.added_body": {
        "cs": "Text a akordy zůstaly beze změny.\nPřidáno z GP ({gp_name}): {added}.",
        "en": "Lyrics and chords stayed unchanged.\nAdded from GP ({gp_name}): {added}."},
    "merge.no_drums_bass_title": {"cs": "GP bez bicích/basy", "en": "GP Has No Drums/Bass"},
    "merge.no_drums_bass_body": {
        "cs": "Text a akordy zůstaly beze změny.\nGP soubor ({gp_name}) neobsahuje "
              "bicí ani basovou stopu — nic se nepřidalo.",
        "en": "Lyrics and chords stayed unchanged.\nThe GP file ({gp_name}) has no "
              "drum or bass track — nothing was added."},
    "load.error_title": {"cs": "Chyba načítání", "en": "Load Error"},
    "load.error_body": {"cs": "Nepodařilo se načíst soubor:\n\n{msg}",
                          "en": "Could not load the file:\n\n{msg}"},
    "status.load_error": {"cs": "Chyba načítání.", "en": "Load failed."},
    "track.type_drums": {"cs": "bicí", "en": "drums"},
    "track.type_bass": {"cs": "basa", "en": "bass"},
    "track.type_guitar": {"cs": "kytara", "en": "guitar"},
    "track.type_solo_guitar": {"cs": "sólo kytara", "en": "solo guitar"},
    "chord_chart.no_data": {"cs": "<p>Žádná data</p>", "en": "<p>No data</p>"},
    "chord_chart.no_track": {"cs": "<p>Žádná stopa</p>", "en": "<p>No track</p>"},
    "chord_chart.tempo_label": {"cs": "Tempo: {tempo} BPM", "en": "Tempo: {tempo} BPM"},
    "overview.title_header": {"cs": "=== {title} ===", "en": "=== {title} ==="},
    "overview.artist": {"cs": "Interpret: {artist}", "en": "Artist: {artist}"},
    "overview.album": {"cs": "Album: {album}", "en": "Album: {album}"},
    "overview.tempo": {"cs": "Tempo: {tempo} BPM", "en": "Tempo: {tempo} BPM"},
    "overview.n_measures": {"cs": "Počet taktů: {n}", "en": "Number of bars: {n}"},
    "overview.tracks_header": {"cs": "STOPY:", "en": "TRACKS:"},
    "overview.track_line": {
        "cs": "  [{i}] {name}  — {n_strings} strun  — ladění: {tuning}  — pražce: {frets}  {drums_tag}  {solo_tag}",
        "en": "  [{i}] {name}  — {n_strings} strings  — tuning: {tuning}  — frets: {frets}  {drums_tag}  {solo_tag}"},
    "overview.drums_tag": {"cs": "(BICÍ)", "en": "(DRUMS)"},
    "overview.solo_tag": {"cs": "[SÓLO]", "en": "[SOLO]"},
    "overview.tempo_changes_header": {"cs": "ZMĚNY TEMPA:", "en": "TEMPO CHANGES:"},
    "overview.tempo_change_line": {"cs": "  Takt-tick {tick} ({t_s}s): {tempo} BPM",
                                     "en": "  Bar tick {tick} ({t_s}s): {tempo} BPM"},
    "overview.estimated_duration": {"cs": "Odhadovaná délka: {mm}:{ss} min",
                                      "en": "Estimated length: {mm}:{ss} min"},
    "lyrics.song_level_header": {"cs": "=== Lyrics (Song-level) ===", "en": "=== Lyrics (Song-level) ==="},
    "lyrics.beat_header": {"cs": "=== Text vázaný na noty (beat text) ===",
                             "en": "=== Note-attached lyrics (beat text) ==="},
    "lyrics.beat_line": {"cs": "  [{time}s | takt {measure} | {track}]  {text}",
                           "en": "  [{time}s | bar {measure} | {track}]  {text}"},
    "lyrics.none_found": {"cs": "(Žádný text vázaný na noty nenalezen.)",
                            "en": "(No note-attached lyrics found.)"},
    "lyrics.tip": {
        "cs": "Tip: Text 'beat text' se v Guitar Pro přidává přes\nnástrojovou lištu > Text. "
              "V GP3 souborech bývá vzácný.",
        "en": "Tip: 'Beat text' is added in Guitar Pro via\nthe toolbar > Text. "
              "It's rare in GP3 files."},
    "chords.found_header": {"cs": "=== Nalezené akordy ===", "en": "=== Chords Found ==="},
    "chords.count_line": {"cs": "  {name:<12}  ({count}×)", "en": "  {name:<12}  ({count}×)"},
    "chords.timeline_header": {"cs": "=== Časová osa akordů ===", "en": "=== Chord Timeline ==="},
    "chords.timeline_line": {"cs": "  [{time}s | takt {measure} | {track}]  {name}",
                               "en": "  [{time}s | bar {measure} | {track}]  {name}"},
    "chords.none_found": {"cs": "(Žádné akordy nenalezeny.)", "en": "(No chords found.)"},
    "detail.filter_label": {"cs": "Filtr takt:", "en": "Bar filter:"},
    "detail.filter_from": {"cs": "od", "en": "from"},
    "detail.filter_to": {"cs": "do", "en": "to"},
    "detail.filter_button": {"cs": "Zobrazit", "en": "Show"},
    "detail.col_bar": {"cs": "Takt", "en": "Bar"},
    "detail.col_time": {"cs": "Čas (s)", "en": "Time (s)"},
    "detail.col_duration": {"cs": "Délka", "en": "Duration"},
    "detail.col_tab": {"cs": "Tabulatura", "en": "Tablature"},
    "detail.col_effects": {"cs": "Efekty", "en": "Effects"},
    "detail.col_chord": {"cs": "Akord", "en": "Chord"},
    "detail.col_lyrics": {"cs": "Text/Slova", "en": "Lyrics"},
    "detail.col_midi": {"cs": "MIDI noty", "en": "MIDI Notes"},
    "status.track_selected": {
        "cs": "Stopa: {name}  |  {n_measures} taktů  |  {detail}",
        "en": "Track: {name}  |  {n_measures} bars  |  {detail}"},
    "status.track_detail_drums": {"cs": "Bicí", "en": "Drums"},
    "status.track_detail_strings": {"cs": "{n} strun", "en": "{n} strings"},
    "chords.tip": {
        "cs": "Tip: Akordy se přidávají v Guitar Pro přes\nChord Diagram (symbol nad notami).",
        "en": "Tip: Chords are added in Guitar Pro via\nthe Chord Diagram (symbol above notes)."},
    "chord_chart.no_content": {
        "cs": "Tato stopa neobsahuje text ani akordy.\n"
              "Zkus jinou stopu nebo otevři soubor s textem (beat text).",
        "en": "This track has no lyrics or chords.\n"
              "Try another track or open a file with beat text."},
    "populate.meta_line": {
        "cs": "Album: {album}  |  Tempo: {tempo} BPM  |  {n_tracks} stop  |  {n_measures} taktů",
        "en": "Album: {album}  |  Tempo: {tempo} BPM  |  {n_tracks} tracks  |  {n_measures} bars"},

    # --- Displej stopa: zarovnání / kompletní znovu-poskládání ---
    "timeline.align_song.result_title": {"cs": "Zarovnat displej", "en": "Re-sync Display"},
    "timeline.align_song.result_body_n": {
        "cs": "Hotovo — {n} klipů na Displej stopě obnoveno na auto-sledování textu/akordů.",
        "en": "Done — {n} clips on the Display track restored to auto-tracking lyrics/chords."},
    "timeline.align_song.result_body_none": {
        "cs": "Všechny textové klipy už byly zarovnané, nic se neměnilo.",
        "en": "All text clips were already in sync, nothing changed."},
    "timeline.rebuild_display": {"cs": "🔄🗑 Znovu poskládat Displej stopu",
                                   "en": "🔄🗑 Rebuild Display Track"},
    "timeline.rebuild_display.tooltip": {
        "cs": "RADIKÁLNÍ oprava: zahodí VŠECHNY klipy na Displej stopě (i ručně "
              "upravené!) a poskládá je znovu od nuly z aktuálního textu/akordů. "
              "Použij, když jsi mazal(a)/přeskládal(a) text a „🔄 Zarovnat displej“ "
              "nestačí (ten jen dolaďuje existující klipy, neřeší smazané řádky).",
        "en": "RADICAL fix: discards ALL clips on the Display track (including "
              "manual edits!) and rebuilds them from scratch from the current "
              "lyrics/chords. Use this when you've deleted/reorganized lyrics and "
              "\"🔄 Re-sync Display\" isn't enough (that one only adjusts existing "
              "clips, it doesn't handle deleted lines)."},
    "timeline.rebuild_display.confirm_title": {"cs": "Znovu poskládat Displej stopu?",
                                                  "en": "Rebuild Display Track?"},
    "timeline.rebuild_display.confirm_body": {
        "cs": "Tohle ZAHODÍ všechny klipy na Displej stopě, včetně ručně "
              "upravených (posunuté/roztažené okraje, vlastní popisky, "
              "režim), a poskládá je znovu od nuly čistě z aktuálního textu "
              "a akordů. Nedá se to vzít zpět jinak než přes Zpět (Ctrl+Z). "
              "Pokračovat?",
        "en": "This will DISCARD all clips on the Display track, including "
              "manually adjusted ones (moved/stretched edges, custom labels, "
              "mode), and rebuild them from scratch purely from the current "
              "lyrics and chords. This can only be undone via Undo (Ctrl+Z). "
              "Continue?"},
    "timeline.rebuild_display.done_title": {"cs": "Displej stopa přestavěna",
                                              "en": "Display Track Rebuilt"},
    "timeline.rebuild_display.done_body": {
        "cs": "Hotovo — {n} klipů znovu poskládáno od nuly z aktuálního textu/akordů.",
        "en": "Done — {n} clips rebuilt from scratch from the current lyrics/chords."},

    "help.about.body": {
        "cs": "Guitar Pro Viewer & Karaoke Exporter\n\n"
              "Prohlížeč Guitar Pro souborů a editor karaoke časové osy pro export "
              "do JSON (ESP32 karaoke přehrávač a další).",
        "en": "Guitar Pro Viewer & Karaoke Exporter\n\n"
              "A Guitar Pro file viewer and karaoke timeline editor that exports "
              "to JSON (for the ESP32 karaoke player and others)."},
}
