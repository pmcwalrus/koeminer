import json

import httpx
import pytest
from PySide6.QtGui import QImage, QColor

from koeminer.core import Mapping, Settings, enrich
from koeminer.images import ImageCache, ImageSearch, Picture


PICTURE = Picture("Cat", "https://upload.wikimedia.org/cat.jpg", "https://commons.wikimedia.org/wiki/File:Cat.jpg", "A & B", "CC BY 4.0")


def test_existing_settings_still_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"profiles": {"Japanese": {"expression": "Expression", "sentence": "Sentence", "audio": "SentenceAudio"}}}))
    assert Settings.load(path).profiles["Japanese"].image == ""
    with pytest.raises(ValueError):
        Mapping(image="Expression").validate()


def test_image_only_preserves_sentence_and_escapes_credit():
    note = {"fields": {"Sentence": "original", "Picture": "old", "WordAudio": "word"},
            "picture": [{"fields": ["Picture", "Other"], "filename": "original.jpg"}]}
    result = enrich(note, Mapping(image="Picture"), None, picture=PICTURE, image_filename="cat.jpg")
    assert '<img src="cat.jpg">' in result["fields"]["Picture"]
    assert "A &amp; B" in result["fields"]["Picture"]
    assert result["fields"]["Sentence"] == "original"
    assert result["picture"][0]["fields"] == ["Other"]
    assert note["fields"]["Picture"] == "old"


def test_skip_picture_preserves_existing_image():
    note = {"fields": {"Picture": "existing"}, "picture": [{"fields": ["Picture"], "filename": "old.jpg"}]}
    assert enrich(note, Mapping(image="Picture"), None) == note


def test_search_returns_ten_ranked_results(monkeypatch):
    def get(url, **kwargs):
        assert kwargs["params"]["gsrlimit"] == 10
        pages = {str(i): {"title": f"File:cat{i}.jpg", "index": i,
                         "imageinfo": [{"thumburl": PICTURE.url, "descriptionurl": PICTURE.source_url}]}
                 for i in reversed(range(12))}
        return httpx.Response(200, json={"query": {"pages": pages}}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    rows = ImageSearch().search("猫")
    assert len(rows) == 10
    assert rows[0].title == "cat0.jpg"
    assert ImageSearch().search(" ") == []


def test_cache_decodes_image_and_reuses_file(tmp_path, monkeypatch):
    image = QImage(64, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    sample = tmp_path / "source.png"
    image.save(str(sample))
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=sample.read_bytes())
    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "Client", lambda **_: client)
    cache = ImageCache(tmp_path / "images")
    path = cache.fetch(PICTURE)
    assert path.read_bytes().startswith(b"\xff\xd8")
    assert cache.fetch(PICTURE) == path
    assert len(requests) == 1


def test_cache_rejects_html(tmp_path, monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"<html>not an image</html>")))
    monkeypatch.setattr(httpx, "Client", lambda **_: client)
    with pytest.raises(ValueError):
        ImageCache(tmp_path).fetch(PICTURE)
    assert not list(tmp_path.glob("*.jpg"))


def test_cache_rejects_redirect_to_localhost(tmp_path, monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})))
    monkeypatch.setattr(httpx, "Client", lambda **_: client)
    with pytest.raises(ValueError):
        ImageCache(tmp_path).fetch(PICTURE)
