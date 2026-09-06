from __future__ import annotations

import copy
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSystemTrayIcon, QTabWidget,
    QVBoxLayout, QWidget,
)

from .core import AudioCache, Corpus, Mapping, Settings, enrich, plain
from .proxy import Proxy, Selection

STYLE = """
QWidget { background: #10181e; color: #e8eff0; font-family: 'Segoe UI'; font-size: 14px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 36px; font-weight: 700; color: #7ae0c3; }
QLabel#title { font-size: 24px; font-weight: 600; }
QLabel#muted { color: #a2b5bd; }
QLabel#japanese { font-size: 23px; }
QFrame#card { background: #1a272f; border: 1px solid #30434d; border-radius: 12px; }
QLineEdit, QComboBox, QSpinBox { background: #1a272f; border: 1px solid #40555e; border-radius: 6px; padding: 9px; }
QPushButton { background: #273a44; border: 1px solid #40555e; border-radius: 7px; padding: 9px 15px; }
QPushButton:hover { background: #36505d; }
QPushButton#primary { background: #79dfc2; color: #10231e; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #a2f0d9; }
QPushButton:disabled { color: #75838a; background: #263039; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 12px 24px; background: #18252d; }
QTabBar::tab:selected { color: #7ae0c3; border-bottom: 2px solid #7ae0c3; }
QScrollArea { border: none; }
QCheckBox { spacing: 10px; }
QToolTip { background: #273a44; color: white; border: none; }
"""


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#10181e"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#79dfc2"))
    for x, height in [(12, 18), (23, 36), (34, 48), (45, 26)]:
        painter.drawRoundedRect(x, (64 - height) // 2, 7, height, 3, 3)
    painter.end()
    return QIcon(pixmap)


class Events(QObject):
    completed = Signal(object, object, object)
    selection = Signal(object)
    status = Signal(str)


class Jobs(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.events = Events(self)
        self.events.completed.connect(self.finish)

    def run(self, operation, success, failure):
        def worker():
            try:
                value = operation()
                self.events.completed.emit(success, value, None)
            except Exception as exc:
                self.events.completed.emit(failure, str(exc), None)
        threading.Thread(target=worker, daemon=True).start()

    def finish(self, callback, value, _):
        callback(value)


class Picker(QDialog):
    def __init__(self, owner, query, selection=None):
        super().__init__(owner)
        self.owner = owner
        self.selection = selection
        self.closed = False
        self.generation = 0
        self.audio_generation = 0
        self.setWindowTitle("koeminer · Выбор предложения")
        self.resize(850, 700)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(lambda *_: self.info.setText("Не удалось воспроизвести аудио: " + self.player.errorString()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(15)
        layout.addWidget(label("Выберите голос для карточки", "title"))
        subtitle = (f"{selection.note['modelName']} · {selection.note.get('deckName', '')}" if selection
                    else "Предварительный просмотр · карточки в Anki не создаются")
        layout.addWidget(label(subtitle, "muted"))
        row = QHBoxLayout()
        self.query = QLineEdit(query)
        self.query.setPlaceholderText("Слово, кандзи или текст предложения")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query)
        row.addWidget(button("Найти", self.search, True))
        row.addWidget(button("■ Стоп", self.stop_audio))
        layout.addLayout(row)
        self.info = label("Загрузка предложений…", "muted")
        layout.addWidget(self.info)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.results = QVBoxLayout(container)
        self.results.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self.preview = label("", "muted")
        layout.addWidget(self.preview)
        footer = QHBoxLayout()
        footer.addWidget(label("Источник: sentencesearch.neocities.org", "muted"))
        footer.addStretch()
        if selection:
            footer.addWidget(button("Без примера", lambda: self.choose(None)))
        footer.addWidget(button("Отменить" if selection else "Закрыть", self.close))
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_expired)
        self.timer.start(1000)
        QTimer.singleShot(0, self.search)

    def check_expired(self):
        if self.selection and self.selection.expired:
            self.close()

    def stop_audio(self):
        self.audio_generation += 1
        self.player.stop()

    def search(self):
        self.generation += 1
        generation = self.generation
        query = self.query.text().strip()
        self.info.setText("Поиск… При первом запуске загружается база сайта.")
        self.owner.jobs.run(lambda: self.owner.corpus.search(query),
                            lambda rows: self.show_results(rows, generation),
                            lambda error: self.search_error(error, generation))

    def search_error(self, error, generation):
        if not self.closed and generation == self.generation:
            self.info.setText("Ошибка загрузки: " + error)

    def show_results(self, rows, generation):
        if self.closed or generation != self.generation:
            return
        while self.results.count():
            item = self.results.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.info.setText(f"Показано {len(rows)} предложений · короткие сначала · максимум 150" if rows
                          else "Ничего не найдено. Попробуйте другой запрос или начальную форму слова.")
        for sentence in rows:
            frame = QFrame()
            frame.setObjectName("card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(18, 15, 18, 15)
            box.addWidget(label(sentence.japanese, "japanese"))
            box.addWidget(label(sentence.english, "muted"))
            actions = QHBoxLayout()
            actions.addWidget(label(sentence.source, "muted"), 1)
            actions.addWidget(button("▶ Слушать", lambda _, s=sentence: self.play(s)))
            actions.addWidget(button("Выбрать", lambda _, s=sentence: self.choose(s), True))
            box.addLayout(actions)
            self.results.addWidget(frame)

    def play(self, sentence):
        self.stop_audio()
        generation = self.audio_generation
        self.info.setText("Загрузка аудио…")
        def ready(path):
            if self.closed or generation != self.audio_generation:
                return
            self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
            self.player.play()
            self.info.setText("Воспроизведение · " + sentence.japanese)
        self.owner.jobs.run(lambda: self.owner.audio.fetch(sentence), ready,
                            lambda error: self.audio_error(error, generation))

    def audio_error(self, error, generation):
        if not self.closed and generation == self.audio_generation:
            self.info.setText("Аудио недоступно: " + error)

    def choose(self, sentence):
        if self.selection:
            if self.selection.expired or self.selection.done.is_set():
                self.close()
                return
            # Fetch first: failure leaves the picker open for another choice.
            if sentence:
                self.info.setText("Подготовка выбранного аудио…")
                self.setEnabled(False)
                self.owner.jobs.run(lambda: self.owner.audio.fetch(sentence),
                                    lambda _: self.commit(sentence), self.choose_error)
            else:
                self.commit(None)
        else:
            self.preview.setText("Выбрано: " + sentence.japanese + "\nВ рабочем режиме текст и MP3 попадут в поля карточки.")
            self.play(sentence)

    def choose_error(self, error):
        if not self.closed:
            self.setEnabled(True)
            self.info.setText("Не удалось загрузить запись. Выберите другую: " + error)

    def commit(self, sentence):
        if self.closed or self.selection.expired or self.selection.done.is_set():
            return
        self.selection.sentence = sentence
        self.selection.cancelled = False
        self.selection.done.set()
        self.close()

    def closeEvent(self, event):
        self.closed = True
        self.player.stop()
        if self.selection and not self.selection.done.is_set():
            self.selection.cancelled = True
            self.selection.done.set()
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        self.config_path = directory / "settings.json"
        self.settings = Settings.load(self.config_path)
        self.corpus = Corpus(directory / "sentences.json")
        self.audio = AudioCache(directory / "audio")
        self.jobs = Jobs(self)
        self.events = Events(self)
        self.events.selection.connect(self.open_selection)
        self.events.status.connect(self.set_status)
        self.proxy = Proxy(self.settings, self.audio, self.events.selection.emit, self.events.status.emit)
        self.pickers = []
        self.setWindowTitle("koeminer")
        self.setWindowIcon(app_icon())
        self.resize(880, 760)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 25, 32, 25)
        layout.setSpacing(16)
        layout.addWidget(label("koeminer", "brand"))
        layout.addWidget(label("Живые предложения. В ваших карточках.", "title"))
        layout.addWidget(label("Yomitan → выбор предложения и аудио → Anki", "muted"))
        tabs = QTabWidget()
        home = QWidget()
        home_layout = QVBoxLayout(home)
        home_layout.setSpacing(18)
        self.status = label("Запуск…")
        home_layout.addWidget(self.status)
        self.address = label("")
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        home_layout.addWidget(self.address)
        home_layout.addWidget(label("1. Запустите Anki с дополнением AnkiConnect.\n\n"
                                    "2. Во вкладке «Настройки» выберите тип заметки и назначьте поля.\n\n"
                                    "3. В Yomitan включите расширенные настройки и замените адрес сервера AnkiConnect на адрес koeminer выше.\n\n"
                                    "4. Нажмите + в Yomitan — откроется выбор предложения.", "muted"))
        row = QHBoxLayout()
        self.demo_query = QLineEdit("食べる")
        self.demo_query.setPlaceholderText("Слово для пробного поиска")
        self.demo_query.returnPressed.connect(self.preview)
        row.addWidget(self.demo_query)
        row.addWidget(button("Попробовать поиск и аудио", self.preview, True))
        home_layout.addLayout(row)
        home_layout.addWidget(label("Предварительный просмотр работает без Anki и не создаёт карточек.", "muted"))
        home_layout.addWidget(button("Проверить AnkiConnect", self.check_anki))
        home_layout.addStretch()
        tabs.addTab(home, "Начало")
        tabs.addTab(self.settings_widget(), "Настройки")
        layout.addWidget(tabs, 1)
        layout.addWidget(label("Sentence Search · аудио сохраняется в медиатеке Anki для офлайн-повторения", "muted"))
        self.setCentralWidget(root)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("koeminer")
        menu = QMenu(self)
        menu.addAction("Открыть koeminer", self.reveal)
        menu.addAction("Выйти", QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.reveal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.start_proxy()

    def start_proxy(self):
        try:
            self.proxy.start()
            self.address.setText(f"Адрес для Yomitan: http://127.0.0.1:{self.settings.port}")
            self.set_status("● koeminer готов · ожидает карточку от Yomitan")
        except Exception as exc:
            self.set_status("Не удалось запустить сервер: " + str(exc))

    def set_status(self, text):
        self.status.setText(text)

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def preview(self):
        self.show_picker(self.demo_query.text())

    def open_selection(self, selection):
        if selection.done.is_set() or selection.expired:
            return
        self.show_picker(plain(selection.note["fields"][selection.mapping.expression]), selection)

    def show_picker(self, query, selection=None):
        picker = Picker(self, query, selection)
        self.pickers.append(picker)
        picker.destroyed.connect(lambda: self.pickers.remove(picker) if picker in self.pickers else None)
        picker.show()
        picker.raise_()
        picker.activateWindow()

    def settings_widget(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setVerticalSpacing(13)
        self.upstream = QLineEdit(self.settings.upstream)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(self.settings.port)
        self.key = QLineEdit(self.settings.api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Адрес AnkiConnect", self.upstream)
        form.addRow("Порт koeminer", self.port)
        form.addRow("API-ключ (если задан)", self.key)
        self.models = QComboBox()
        self.models.setEditable(True)
        self.models.addItems(list(self.settings.profiles))
        self.models.setPlaceholderText("Загрузите из Anki или введите имя типа заметки")
        form.addRow("Тип заметки", self.models)
        form.addRow(button("Загрузить типы и поля из Anki", self.load_models))
        self.fields = {}
        for key, title in [("expression", "Поле слова / кандзи"), ("sentence", "Поле предложения"),
                           ("audio", "Поле аудио предложения"), ("translation", "Поле перевода (необязательно)")]:
            combo = QComboBox()
            combo.setEditable(True)
            self.fields[key] = combo
            form.addRow(title, combo)
        self.append = QCheckBox("Добавлять к содержимому полей вместо замены")
        form.addRow(self.append)
        form.addRow(label("Имена полей должны точно совпадать с Anki. В шаблоне карточки должны отображаться поля предложения и аудио.", "muted"))
        form.addRow(button("Сохранить настройки", self.save_settings, True))
        self.settings_status = label("", "muted")
        form.addRow(self.settings_status)
        self.models.currentTextChanged.connect(self.model_changed)
        self.model_changed(self.models.currentText())
        return widget

    def edited_settings(self):
        settings = copy.deepcopy(self.settings)
        settings.upstream = self.upstream.text().strip().rstrip("/")
        settings.port = self.port.value()
        settings.api_key = self.key.text()
        settings.validate()
        return settings

    def model_changed(self, name):
        mapping = self.settings.profiles.get(name, Mapping())
        for key, combo in self.fields.items():
            combo.setCurrentText(getattr(mapping, key))
        self.append.setChecked(mapping.append)

    def load_models(self):
        try:
            connection = Proxy(self.edited_settings(), self.audio, lambda _: None)
            self.settings_status.setText("Подключение к Anki…")
            self.jobs.run(lambda: {name: connection.call("modelFieldNames", modelName=name)
                                   for name in connection.call("modelNames")}, self.models_loaded,
                          self.settings_status.setText)
        except Exception as exc:
            self.settings_status.setText(str(exc))

    def models_loaded(self, models):
        self.model_fields = models
        current = self.models.currentText()
        self.models.blockSignals(True)
        self.models.clear()
        self.models.addItems(models)
        if current in models:
            self.models.setCurrentText(current)
        self.models.blockSignals(False)
        if not getattr(self, "fields_connected", False):
            self.models.currentTextChanged.connect(self.populate_fields)
            self.fields_connected = True
        self.populate_fields(self.models.currentText())
        self.settings_status.setText("Типы и поля загружены. Назначьте поля и сохраните настройки.")

    def populate_fields(self, name):
        self.model_changed(name)
        for combo in self.fields.values():
            current = combo.currentText()
            combo.clear()
            combo.addItems([""] + self.model_fields.get(name, []))
            combo.setCurrentText(current)

    def save_settings(self):
        try:
            if self.proxy.selection_lock.locked():
                raise ValueError("Завершите создание карточки перед изменением настроек.")
            settings = self.edited_settings()
            name = self.models.currentText().strip()
            if not name:
                raise ValueError("Укажите тип заметки Anki.")
            mapping = Mapping(**{key: combo.currentText().strip() for key, combo in self.fields.items()}, append=self.append.isChecked())
            mapping.validate()
            settings.profiles[name] = mapping
            settings.validate()
            old_settings = self.settings
            self.proxy.stop()
            self.proxy.settings = settings
            try:
                self.proxy.start()
                settings.save(self.config_path)
            except Exception:
                self.proxy.stop()
                self.proxy.settings = old_settings
                self.proxy.start()
                raise
            self.settings = settings
            self.address.setText(f"Адрес для Yomitan: http://127.0.0.1:{settings.port}")
            self.settings_status.setText(f"Сохранено для «{name}».")
        except Exception as exc:
            self.settings_status.setText(str(exc))

    def check_anki(self):
        self.set_status("Проверка AnkiConnect…")
        self.jobs.run(lambda: self.proxy.call("version"),
                      lambda version: self.set_status(f"● AnkiConnect подключён · API {version}"), self.set_status)

    def closeEvent(self, event):
        if self.tray.isVisible():
            self.hide()
            self.tray.showMessage("koeminer", "Работа продолжается в трее. Для завершения выберите «Выйти».")
            event.ignore()
        else:
            QApplication.instance().quit()
            event.accept()

    def shutdown(self):
        for picker in list(self.pickers):
            picker.close()
        self.proxy.stop()
