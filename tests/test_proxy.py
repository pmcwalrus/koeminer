import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from koeminer.core import Mapping, Sentence, Settings
from koeminer.proxy import Proxy


@pytest.fixture
def bridge(tmp_path):
    requests = []

    class Anki(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            result = {"version": 6, "addNote": 12345, "storeMediaFile": "saved.mp3", "modelNames": ["Japanese"]}.get(request["action"])
            value = {"result": result, "error": None} if request.get("version", 4) > 4 else result
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Anki)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class Audio:
        def fetch(self, sentence):
            path = tmp_path / "test.mp3"
            path.write_bytes(b"fake audio for isolated protocol test")
            return path

    proxy = Proxy(Settings(upstream=f"http://127.0.0.1:{server.server_port}", profiles={"Japanese": Mapping()}), Audio(), lambda _: None)
    yield proxy, requests
    proxy.stop()
    server.shutdown()
    server.server_close()


def request(version=2):
    return {"action": "addNote", "version": version, "params": {"note": {
        "modelName": "Japanese", "deckName": "Japanese", "tags": ["yomitan"],
        "fields": {"Expression": "猫", "Sentence": "", "SentenceAudio": "", "Meaning": "cat"}}}}


def test_select_upload_and_create(bridge):
    proxy, calls = bridge
    def select(pending):
        pending.sentence = Sentence("猫です", "A cat", "test", "a.mp3")
        pending.cancelled = False
        pending.done.set()
    proxy.on_selection = select
    result = proxy.dispatch(request())
    assert result == {"result": 12345, "error": None}
    assert [x["action"] for x in calls] == ["version", "storeMediaFile", "addNote"]
    note = calls[-1]["params"]["note"]
    assert note["fields"]["SentenceAudio"] == "[sound:saved.mp3]"
    assert note["fields"]["Meaning"] == "cat"


def test_cancel_never_creates_note(bridge):
    proxy, calls = bridge
    proxy.on_selection = lambda pending: pending.done.set()
    assert proxy.dispatch(request())["error"]
    assert [x["action"] for x in calls] == ["version"]


def test_image_upload_and_image_only_note(bridge, tmp_path):
    from koeminer.images import Picture
    proxy, calls = bridge
    proxy.settings.profiles["Japanese"].image = "Picture"
    path = tmp_path / "cat.jpg"
    path.write_bytes(b"isolated image fixture")
    class Images:
        def fetch(self, picture):
            return path
    proxy.images = Images()
    def select(pending):
        pending.picture = Picture("cat", "https://upload.wikimedia.org/cat.jpg", "https://commons.wikimedia.org/wiki/File:Cat.jpg")
        pending.cancelled = False
        pending.done.set()
    proxy.on_selection = select
    payload = request()
    payload["params"]["note"]["fields"]["Picture"] = ""
    assert proxy.dispatch(payload)["result"] == 12345
    assert [x["action"] for x in calls] == ["version", "storeMediaFile", "addNote"]
    assert '<img src="saved.mp3">' in calls[-1]["params"]["note"]["fields"]["Picture"]
    assert calls[-1]["params"]["note"]["fields"]["Sentence"] == ""


def test_skip_preserves_note(bridge):
    proxy, calls = bridge
    def skip(pending):
        pending.cancelled = False
        pending.done.set()
    proxy.on_selection = skip
    original = request()
    assert proxy.dispatch(original)["result"] == 12345
    assert calls[-1] == original


def test_missing_profile_fails_before_anki(bridge):
    proxy, calls = bridge
    proxy.settings.profiles.clear()
    assert proxy.dispatch(request())["error"]
    assert calls == []


def test_bad_key_and_busy(bridge):
    proxy, calls = bridge
    proxy.settings.api_key = "secret"
    assert proxy.dispatch(request())["error"]
    assert calls == []
    proxy.settings.api_key = ""
    proxy.selection_lock.acquire()
    try:
        assert proxy.dispatch(request())["error"]
    finally:
        proxy.selection_lock.release()


def test_http_yomitan_v2_and_modern_v6_and_origin(bridge):
    proxy, calls = bridge
    # Let the OS allocate a test port while keeping production settings validation.
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        proxy.settings.port = sock.getsockname()[1]
    proxy.start()
    url = f"http://127.0.0.1:{proxy.settings.port}"
    with httpx.Client(trust_env=False) as client:
        for version, expected in [(2, 6), (6, {"result": 6, "error": None})]:
            response = client.post(url, json={"action": "version", "version": version},
                                   headers={"Origin": "chrome-extension://test"})
            assert response.json() == expected
            assert response.headers["Access-Control-Allow-Origin"] == "chrome-extension://test"
        assert client.post(url, json=request(), headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.post(url, content="{broken").status_code == 400
        proxy.on_selection = lambda pending: pending.done.set()
        assert client.post(url, json=request()).json().get("error")
