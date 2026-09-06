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
    Settings(port=port, profiles={"Japanese": Mapping(image="Picture" if with_image else "", image_query="SelectedMeaning")}).save(tmp_path / "settings.json")
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
                    "modelName": "Japanese", "fields": {"Expression": "猫", "Sentence": "", "SentenceAudio": "", "Picture": "", "SelectedMeaning": "<b>sleeping cat</b>"}}}}).json()
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
