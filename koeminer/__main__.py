import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .ui import MainWindow, STYLE


def main():
    parser = argparse.ArgumentParser(description="koeminer — Yomitan sentence picker")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("koeminer")
    app.setOrganizationName("koeminer")
    app.setStyleSheet(STYLE)
    directory = args.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "koeminer.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "koeminer", "koeminer уже запущен. Откройте его через значок в трее.")
        return 0
    try:
        window = MainWindow(directory)
    except Exception as exc:
        QMessageBox.critical(None, "koeminer", f"Ошибка запуска: {exc}\nНастройки: {directory / 'settings.json'}")
        return 1
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    if args.preview:
        QTimer.singleShot(250, window.preview)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
