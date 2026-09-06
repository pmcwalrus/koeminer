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


@pytest.mark.parametrize("with_image", [False, True])
def test_http_opens_picker_and_selection_returns_note_id(tmp_path, with_image):
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
                window.pickers[0].choose(sentence)
                if with_image:
                    from koeminer.images import Picture
                    window.pickers[0].choose_picture(Picture("cat", "https://upload.wikimedia.org/cat.jpg", "https://commons.wikimedia.org/wiki/File:Cat.jpg"))
                    assert not result  # Choosing a sentence alone must not submit the card.
                    window.pickers[0].finish_selection()
            time.sleep(0.01)
        assert chosen
        assert result == {"response": 98765}
        assert calls[-1]["params"]["note"]["fields"]["SentenceAudio"] == "[sound:cat.mp3]"
        if with_image:
            assert '<img src="cat.mp3">' in calls[-1]["params"]["note"]["fields"]["Picture"]
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()
