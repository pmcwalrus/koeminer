import json

import httpx
import pytest
from PySide6.QtGui import QImage, QColor

from koeminer.core import Mapping, Settings, enrich
from koeminer.images import ImageCache, ImageSearch, Picture


PICTURE = Picture("Cat", "https://upload.wikimedia.org/cat.jpg", "https://commons.wikimedia.org/wiki/File:Cat.jpg", "A & B", "CC BY 4.0")


def test_yandex_search_parses_ranked_results_and_skips_duplicates(monkeypatch):
    from html import escape
    items = [{"image": f"//avatars.mds.yandex.net/i?id={i}&n=13", "origUrl": f"https://example.org/{i}",
              "alt": "猫 & кот"} for i in range(12)]
    items.insert(1, items[0])
    items.insert(0, {"image": "https://127.0.0.1/private"})
    state = {"initialState": {"serpList": {"items": {
        "keys": list(map(str, range(len(items)))), "entities": {str(i): item for i, item in enumerate(items)}}}}}
    page = '<div data-state="' + escape(json.dumps(state), quote=True) + '"></div>'
    def get(url, **kwargs):
        assert kwargs["params"]["text"] == "猫"
        return httpx.Response(200, text=page, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    results = ImageSearch().search(" 猫 ", "Яндекс")
    assert len(results) == 10
    assert results[0].url == "https://avatars.mds.yandex.net/i?id=0&n=13"
    assert results[0].title == "猫 & кот"
    assert results[0].provider == "Яндекс"


@pytest.mark.parametrize("page", ['<div>SmartCaptcha</div>', '<div>Unexpected page</div>'])
def test_yandex_reports_unreadable_response(page):
    from koeminer.images import parse_yandex
    with pytest.raises(ValueError):
        parse_yandex(page)


def test_existing_settings_still_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"profiles": {"Japanese": {"expression": "Expression", "sentence": "Sentence", "audio": "SentenceAudio"}}}))
    assert Settings.load(path).profiles["Japanese"].image == ""
    with pytest.raises(ValueError):
        Mapping(image="Expression").validate()


def test_image_only_preserves_sentence_and_has_no_caption():
    note = {"fields": {"Sentence": "original", "Picture": "old", "WordAudio": "word"},
            "picture": [{"fields": ["Picture", "Other"], "filename": "original.jpg"}]}
    result = enrich(note, Mapping(image="Picture"), None, picture=PICTURE, image_filename="cat.jpg")
    assert result["fields"]["Picture"] == '<img src="cat.jpg">'
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


def test_removed_provider_key_is_discarded(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"serpapi_key": "obsolete-key", "profiles": {}}))
    settings = Settings.load(path)
    settings.save(path)
    assert "serpapi_key" not in json.loads(path.read_text())
    assert ImageSearch.sources == ("Wikimedia Commons", "Яндекс")


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


def test_downloads_overlap_and_same_image_is_fetched_once(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    sample = tmp_path / "fixture.png"
    image.save(str(sample))
    data = sample.read_bytes()
    barrier = threading.Barrier(3)
    calls = []
    real_client = httpx.Client
    def handle(request):
        calls.append(str(request.url))
        barrier.wait(timeout=5)  # Serial fetching cannot pass this barrier.
        return httpx.Response(200, content=data)
    monkeypatch.setattr(httpx, "Client", lambda **_: real_client(transport=httpx.MockTransport(handle)))
    cache = ImageCache(tmp_path / "images")
    pictures = [replace(PICTURE, url=f"https://upload.wikimedia.org/{i}.jpg") for i in range(3)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(cache.fetch, pictures + [pictures[0]]))
    assert len(calls) == 3
    assert paths[0] == paths[3]
    assert all(path.exists() for path in paths)
