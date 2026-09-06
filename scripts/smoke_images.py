"""Manual network smoke test: real search, ten thumbnails, screenshot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel
from koeminer.ui import MainWindow, STYLE

app = QApplication([])
app.setStyleSheet(STYLE)
window = MainWindow(Path(".dev"))
window.show()
window.demo_query.setText("猫")
window.preview()
picker = window.pickers[0]
picker.tabs.setCurrentIndex(1)


def check():
    loaded = sum(1 for image in picker.findChildren(QLabel) if image.pixmap() and not image.pixmap().isNull())
    if loaded == 10:
        picker.grab().save(".dev/images-picker.png")
        print("10 image results and 10 decoded thumbnails displayed", flush=True)
        window.shutdown()
        app.exit(0)


def timeout():
    picker.grab().save(".dev/images-picker.png")
    print("Image load timed out: " + picker.image_info.text(), flush=True)
    window.shutdown()
    app.exit(1)


timer = QTimer()
timer.timeout.connect(check)
timer.start(250)
QTimer.singleShot(180000, timeout)
raise SystemExit(app.exec())
