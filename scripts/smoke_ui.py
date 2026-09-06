"""Exercise the real Qt picker and audio decoder; save screenshots for review."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from koeminer.ui import MainWindow, STYLE

app = QApplication([])
app.setStyleSheet(STYLE)
window = MainWindow(Path(".dev"))
window.show()
window.preview()
picker = window.pickers[0]
outcome = {"loaded": False, "audio": False}


def check():
    if picker.results.count() and not outcome["loaded"]:
        outcome["loaded"] = True
        window.grab().save(".dev/main.png")
        picker.grab().save(".dev/picker.png")
        picker.play(window.corpus.search("食べる")[0])
    if picker.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
        outcome["audio"] = True
    if outcome["audio"] and picker.player.position() > 200:
        finish()


def finish():
    window.grab().save(".dev/main.png")
    picker.grab().save(".dev/picker.png")
    print(outcome, flush=True)
    window.shutdown()
    app.exit(0 if all(outcome.values()) else 1)


timer = QTimer()
timer.timeout.connect(check)
timer.start(150)
QTimer.singleShot(45000, finish)
raise SystemExit(app.exec())
