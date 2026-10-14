"""Real HTTP -> Qt queued signal -> picker -> note response, isolated from user Anki."""
import os
import socket
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QFormLayout, QMessageBox, QTabWidget

from koeminer.core import Mapping, Sentence, Settings
from koeminer.ui import MainWindow


@pytest.fixture(autouse=True)
def stub_github_release(monkeypatch):
    monkeypatch.setattr("koeminer.ui.latest_release_version", lambda: "v0.4.3")


@pytest.mark.parametrize("order", ["audio_only", "audio_first", "image_first"])
def test_http_opens_picker_and_selection_returns_note_id(tmp_path, order):
    with_image = order != "audio_only"
    app = QApplication.instance() or QApplication([])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    Settings(port=port, profiles={"Japanese": Mapping(image="Picture" if with_image else "", image_query="SelectedMeaning",
                                                   tags="added, yomitan")}).save(tmp_path / "settings.json")
    window = MainWindow(tmp_path)
    sentence = Sentence("猫です", "A cat", "fixture", "cat.mp3")
    window.corpus.rows = [sentence]
    audio = tmp_path / "cat.mp3"
    audio.write_bytes(b"test audio")
    window.audio.fetch = lambda _: audio
    window.images.fetch = lambda _: audio
    window.image_search.search = lambda *args: []
    calls = []

    def upstream(request):
        calls.append(request)
        return {"result": {"version": 6, "storeMediaFile": "cat.mp3", "addNote": 98765}[request["action"]], "error": None}

    window.proxy.upstream = upstream
    result = {}

    def client():
        try:
            result["response"] = httpx.post(f"http://127.0.0.1:{port}", timeout=15, trust_env=False, json={
                "action": "addNote", "version": 2, "params": {"note": {
                     "modelName": "Japanese", "tags": ["yomitan"],
                     "fields": {"Expression": "猫", "Sentence": "", "SentenceAudio": "", "Picture": "", "SelectedMeaning": "<b>sleeping cat</b>"}}}}).json()
        except Exception as exc:
            result["error"] = str(exc)

    thread = threading.Thread(target=client, daemon=True)
    thread.start()
    chosen = False
    deadline = time.monotonic() + 12
    try:
        while thread.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            if window.pickers and window.pickers[0].results.count() and not chosen:
                chosen = True
                picker = window.pickers[0]
                if with_image:
                    assert picker.image_query.text() == "sleeping cat"
                    assert picker.query.text() == "猫"
                    from koeminer.images import Picture
                    picture = Picture("cat", "https://upload.wikimedia.org/cat.jpg", "https://commons.wikimedia.org/wiki/File:Cat.jpg")
                    if order == "audio_first":
                        picker.choose(sentence)
                        assert picker.tabs.currentIndex() == 1
                        assert not result
                        picker.choose_picture(picture)
                    else:
                        picker.tabs.setCurrentIndex(1)
                        picker.choose_picture(picture)
                        assert picker.tabs.currentIndex() == 0
                        assert not result
                        picker.choose(sentence)
                else:
                    picker.choose(sentence)
            time.sleep(0.01)
        assert chosen
        assert result == {"response": 98765}
        assert picker.closed
        assert sum(call["action"] == "addNote" for call in calls) == 1
        assert calls[-1]["params"]["note"]["fields"]["SentenceAudio"] == "[sound:cat.mp3]"
        assert calls[-1]["params"]["note"]["tags"] == ["yomitan", "added"]
        if with_image:
            assert '<img src="cat.mp3">' in calls[-1]["params"]["note"]["fields"]["Picture"]
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_tags_setting_is_saved_per_model(tmp_path, monkeypatch):
    from koeminer.proxy import Proxy
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    Settings(profiles={"Japanese": Mapping(tags="old"), "Other": Mapping(tags="other")}).save(tmp_path / "settings.json")
    window = MainWindow(tmp_path)
    try:
        assert window.tags.text() == "old"
        window.tags.setText("new, grammar")
        window.save_settings()
        assert window.settings_status.text() == "Сохранено для «Japanese»."
        assert Settings.load(tmp_path / "settings.json").profiles["Japanese"].tags == "new, grammar"
        window.models.setCurrentText("Other")
        assert window.tags.text() == "other"
        window.models.setCurrentText("Japanese")
        assert window.tags.text() == "new, grammar"
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_config_export_import_updates_ui_and_proxy_without_losing_profiles(tmp_path, monkeypatch):
    from koeminer.proxy import Proxy
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    original = Settings(profiles={"Japanese": Mapping(tags="old")})
    original.save(tmp_path / "settings.json")
    window = MainWindow(tmp_path)
    backup = tmp_path / "my config.koe"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(backup), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(backup), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    try:
        window.tags.setText("unsaved")
        window.export_settings()
        assert Settings.load_backup(backup) == original
        assert "Несохранённые изменения не включены" in window.settings_status.text()
        replacement = Settings(port=9876, upstream="http://localhost:8765", api_key="secret",
                               profiles={"Imported": Mapping(expression="Word", tags="new"), "Other": Mapping()})
        replacement.save_backup(backup)
        window.models_loaded({"Japanese": ["Expression", "Sentence", "SentenceAudio"]})
        window.import_settings()
        assert window.settings == replacement == window.proxy.settings == Settings.load(tmp_path / "settings.json")
        assert window.upstream.text() == replacement.upstream
        assert window.port.value() == 9876
        assert window.key.text() == "secret"
        assert window.address.text().endswith(":9876")
        assert [window.models.itemText(i) for i in range(window.models.count())] == ["Imported", "Other"]
        assert window.fields["expression"].currentText() == "Word"
        assert window.tags.text() == "new"
        window.models.setCurrentText("Other")
        assert window.tags.text() == ""
        window.models.setCurrentText("Imported")
        assert window.tags.text() == "new"
        window.tags.setText("updated")
        window.save_settings()
        saved = Settings.load(tmp_path / "settings.json")
        assert saved.profiles["Imported"].tags == "updated"
        assert saved.profiles["Other"] == replacement.profiles["Other"]
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_config_import_cancel_invalid_and_busy_leave_settings_untouched(tmp_path, monkeypatch):
    from koeminer.proxy import Proxy
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    backup = tmp_path / "settings.koe"
    Settings(port=9876, profiles={"Imported": Mapping()}).save_backup(backup)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(backup), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    try:
        window.import_settings()
        assert window.settings.port == 8766
        assert not (tmp_path / "settings.json").exists()
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        backup.write_text("{}", encoding="utf-8")
        window.import_settings()
        assert "не файл конфигурации" in window.settings_status.text()
        assert window.proxy.settings is window.settings
        Settings(port=9876).save_backup(backup)
        window.proxy.selection_lock.acquire()
        try:
            window.import_settings()
            assert "Завершите создание карточки" in window.settings_status.text()
        finally:
            window.proxy.selection_lock.release()
        assert window.settings.port == 8766
        assert not (tmp_path / "settings.json").exists()
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_config_export_adds_koe_suffix_and_does_not_replace_without_confirmation(tmp_path, monkeypatch):
    from koeminer.proxy import Proxy
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "backup"), ""))
    backup = tmp_path / "backup.koe"
    try:
        window.export_settings()
        assert Settings.load_backup(backup) == window.settings
        backup.write_text("keep", encoding="utf-8")
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
        window.export_settings()
        assert backup.read_text(encoding="utf-8") == "keep"
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_config_import_reverts_on_proxy_restart_failure(tmp_path, monkeypatch):
    from koeminer.proxy import Proxy
    starts = []
    def start(proxy):
        starts.append(proxy.settings.port)
        if proxy.settings.port == 9876:
            raise OSError("порт занят")
    monkeypatch.setattr(Proxy, "start", start)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    original = Settings(profiles={"Japanese": Mapping(tags="original")})
    original.save(tmp_path / "settings.json")
    backup = tmp_path / "settings.koe"
    Settings(port=9876, profiles={"Other": Mapping()}).save_backup(backup)
    window = MainWindow(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(backup), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    try:
        window.import_settings()
        assert "порт занят" in window.settings_status.text()
        assert window.settings == window.proxy.settings == Settings.load(tmp_path / "settings.json") == original
        assert window.models.currentText() == "Japanese"
        assert window.port.value() == original.port
        assert starts == [original.port, 9876, original.port]
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("narrow_width", [360, 420])
def test_small_windows_scroll_and_can_grow(tmp_path, monkeypatch, narrow_width):
    from koeminer.proxy import Proxy
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.corpus.rows = [Sentence("猫", "cat", "fixture", "cat.mp3")]
    window.image_search.search = lambda *args: []
    window.show()
    window.preview()
    picker = window.pickers[0]
    settings_tab = window.content_scroll.widget().findChild(QTabWidget)
    settings_tab.setCurrentIndex(1)
    window.models.addItem("Very long model name " * 20)
    window.fields["expression"].addItem("Very long field name " * 20)
    controls = (window.upstream, window.port, window.key, window.models, *window.fields.values(), window.tags)
    try:
        for widget in (window, picker):
            widget.resize(narrow_width, 320)
        for _ in range(10):
            app.processEvents()
        assert window.width() == picker.width() == narrow_width
        assert window.height() == picker.height() == 320
        assert window.content_scroll.horizontalScrollBar().maximum() == 0
        assert window.settings_form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
        assert picker.content_scroll.horizontalScrollBar().maximum() > 0
        assert window.content_scroll.verticalScrollBar().maximum() > 0
        assert picker.content_scroll.verticalScrollBar().maximum() > 0
        assert window.release_link.isVisible()
        assert window.release_link.geometry().right() <= window.statusBar().width()
        narrow = [control.width() for control in controls]
        assert all(control.width() <= settings_tab.width() for control in controls)
        for widget in (window, picker):
            widget.resize(1200, 1000)
        for _ in range(10):
            app.processEvents()
        for widget in (window, picker):
            assert widget.width() == 1200
            assert widget.content_scroll.horizontalScrollBar().maximum() == 0
        assert window.settings_form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapLongRows
        for control in controls:
            caption = window.settings_form.labelForField(control)
            assert caption.geometry().right() < control.geometry().left()
        assert all(control.width() > width for control, width in zip(controls, narrow))
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_images_load_while_sentence_tab_is_open(tmp_path, monkeypatch):
    from koeminer.proxy import Proxy
    from koeminer.images import Picture
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.corpus.rows = [Sentence("猫", "cat", "fixture", "cat.mp3")]
    window.image_search.sources = ("Wikimedia Commons", "Test source")
    requests = []
    loaded = threading.Event()
    release_slow_source = threading.Event()
    picture = Picture("cat", "https://upload.wikimedia.org/cat.jpg", "https://commons.wikimedia.org/wiki/File:Cat.jpg")
    def search(query, source, page=0):
        requests.append(query)
        if source == "Test source":
            release_slow_source.wait(5)
            raise ValueError("source unavailable")
        return [picture]
    def fetch(_):
        loaded.set()
        return tmp_path / "missing-thumbnail.jpg"
    window.image_search.search = search
    window.images.fetch = fetch
    window.preview()
    picker = window.pickers[0]
    try:
        deadline = time.monotonic() + 5
        while not loaded.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert loaded.is_set()
        assert picker.tabs.currentIndex() == 0
        assert "1 картинок" in picker.source_status["Wikimedia Commons"].text()
        assert "поиск" in picker.source_status["Test source"].text()
        picker.tabs.setCurrentIndex(1)
        app.processEvents()
        assert len(requests) == 2
        release_slow_source.set()
        deadline = time.monotonic() + 5
        while "source unavailable" not in picker.source_status["Test source"].text() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert "source unavailable" in picker.source_status["Test source"].text()
        assert "1 картинок" in picker.source_status["Wikimedia Commons"].text()
    finally:
        release_slow_source.set()
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_image_columns_pagination_retry_and_query_reset(tmp_path, monkeypatch):
    from koeminer.proxy import Proxy
    from koeminer.images import Picture
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.corpus.rows = [Sentence("猫", "cat", "fixture", "cat.mp3")]
    calls = []
    fail = [True]
    def search(query, source, page):
        calls.append((query, source, page))
        if page == 1 and fail[0]:
            fail[0] = False
            raise ValueError("temporary failure")
        return [Picture(str(i), f"https://upload.wikimedia.org/{query}-{i}.jpg", "")
                for i in range(page * 10, min(page * 10 + 10, 13))]
    window.image_search.search = search
    window.images.fetch = lambda _: tmp_path / "missing.jpg"
    def until(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert predicate()
    window.preview()
    picker = window.pickers[0]
    try:
        until(lambda: hasattr(picker, "image_sources") and all(not s["loading"] for s in picker.image_sources.values()))
        picker.tabs.setCurrentIndex(1)
        app.processEvents()
        left, right = [picker.image_results.itemAt(i).widget() for i in range(2)]
        assert left.y() == right.y() and left.x() < right.x()
        source = window.image_search.sources[0]
        state = picker.image_sources[source]
        assert len(state["seen"]) == 10
        assert state["scroll"] is not picker.image_sources[window.image_search.sources[1]]["scroll"]
        state["button"].click()
        until(lambda: not state["loading"])
        assert state["page"] == 1 and state["button"].isEnabled()
        state["button"].click()
        until(lambda: not state["loading"])
        assert len(state["seen"]) == 13
        assert not state["button"].isEnabled()
        assert state["layout"].itemAt(13).widget() is state["button"]
        assert calls[-2:] == [(picker.active_image_query, source, 1)] * 2
        picker.image_query.setText("dog")
        picker.search_images()
        until(lambda: all(not s["loading"] for s in picker.image_sources.values()))
        assert all(s["page"] == 1 and len(s["seen"]) == 10 for s in picker.image_sources.values())
        assert all(q == "dog" and page == 0 for q, _, page in calls[-2:])
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()
