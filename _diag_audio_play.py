"""Diagnostický skript — NENÍ součástí appky, jen pro ladění cukání zvuku.

Přehraje soubor úplně "holým" QMediaPlayerem — žádná timeline, žádná scéna,
žádný časovač na aktualizaci kurzoru, žádný scroll. Jen player + tlačítko
Stop. Pokud i TOHLE cuká, není to v timeline_editor.py — je to něco níž
(Qt multimedia backend / zvukové zařízení / Voicemeeter).

Použití:
    python _diag_audio_play.py "cesta\\k\\souboru.wav"
"""
import sys

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

if len(sys.argv) < 2:
    print("Použití: python _diag_audio_play.py \"cesta\\k\\souboru.wav\"")
    sys.exit(1)

path = sys.argv[1]
app = QApplication(sys.argv)

player = QMediaPlayer()
audio_output = QAudioOutput()
audio_output.setVolume(1.0)
player.setAudioOutput(audio_output)
player.setSource(QUrl.fromLocalFile(path))

print("výchozí výstup:", QMediaDevices.defaultAudioOutput().description())
print("hraje:", path)

win = QWidget()
win.setWindowTitle("Diagnostika — holé přehrávání")
lay = QVBoxLayout(win)
lay.addWidget(QLabel(f"Přehrává se:\n{path}"))
status = QLabel("...")
lay.addWidget(status)
stop_btn = QPushButton("Stop")
stop_btn.clicked.connect(player.stop)
lay.addWidget(stop_btn)


def on_error(err, msg):
    status.setText(f"CHYBA: {msg}")
    print("CHYBA:", err, msg)


def on_state(state):
    status.setText(f"stav: {state}")
    print("stav:", state)


player.errorOccurred.connect(on_error)
player.playbackStateChanged.connect(on_state)

win.resize(420, 140)
win.show()
player.play()

sys.exit(app.exec())
