"""Real HTTP -> Qt queued signal -> picker -> note response, isolated from user Anki."""
import os
import socket
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from koeminer.core import Mapping, Sentence, Settings
from koeminer.ui import MainWindow


@pytest.mark.parametrize("order", ["audio_only", "audio_first", "image_first"])
def test_http_opens_picker_and_selection_returns_note_id(tmp_path, order):
    with_image = order != "audio_only"
    app = QApplication.instance() or QApplication([])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    Settings(port=port, profiles={"Japanese": Mapping(image="Picture" if with_image else "")}).save(tmp_path / "settings.json")
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
                    "modelName": "Japanese", "fields": {"Expression": "猫", "Sentence": "", "SentenceAudio": "", "Picture": ""}}}}).json()
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
        if with_image:
            assert '<img src="cat.mp3">' in calls[-1]["params"]["note"]["fields"]["Picture"]
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_small_windows_scroll_and_can_grow(tmp_path, monkeypatch):
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
    try:
        for widget in (window, picker):
            widget.resize(420, 320)
        for _ in range(10):
            app.processEvents()
        for widget in (window, picker):
            assert widget.width() == 420
            assert widget.height() == 320
            assert widget.content_scroll.horizontalScrollBar().maximum() > 0
            assert widget.content_scroll.verticalScrollBar().maximum() > 0
            widget.resize(1200, 1000)
        for _ in range(10):
            app.processEvents()
        for widget in (window, picker):
            assert widget.width() == 1200
            assert widget.content_scroll.horizontalScrollBar().maximum() == 0
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
    requests = []
    loaded = threading.Event()
    release_slow_source = threading.Event()
    picture = Picture("cat", "https://upload.wikimedia.org/cat.jpg", "https://commons.wikimedia.org/wiki/File:Cat.jpg")
    def search(query, source):
        requests.append(query)
        if source == "Openverse":
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
        assert "1 из 5" in picker.source_status["Wikimedia Commons"].text()
        assert "поиск" in picker.source_status["Openverse"].text()
        picker.tabs.setCurrentIndex(1)
        app.processEvents()
        assert len(requests) == 2
        release_slow_source.set()
        deadline = time.monotonic() + 5
        while "недоступен" not in picker.source_status["Openverse"].text() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert "недоступен" in picker.source_status["Openverse"].text()
        assert "1 из 5" in picker.source_status["Wikimedia Commons"].text()
    finally:
        release_slow_source.set()
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()
