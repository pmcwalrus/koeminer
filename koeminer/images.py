"""Wikimedia and Yandex image search with a local image cache."""
import hashlib
import json
import threading
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader

from .core import plain

HEADERS = {"User-Agent": "koeminer/0.3.0 (https://github.com/pmcwalrus/koeminer)"}


@dataclass(frozen=True)
class Picture:
    title: str
    url: str
    source_url: str
    artist: str = ""
    license: str = ""
    provider: str = "Wikimedia Commons"


def trusted_url(url, media=True):
    parsed = urlparse(url)
    hosts = {"upload.wikimedia.org", "thumb.wikimedia.org", "avatars.mds.yandex.net"} if media else {"commons.wikimedia.org"}
    allowed = parsed.hostname in hosts
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.port not in (None, 443):
        raise ValueError("Недопустимый адрес изображения.")
    return url


class YandexPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = None

    def handle_starttag(self, tag, attrs):
        state = dict(attrs).get("data-state")
        if not state:
            return
        try:
            items = json.loads(state)["initialState"]["serpList"]["items"]
            self.items = [items["entities"][key] for key in items["keys"]]
        except (ValueError, KeyError, TypeError):
            return


def parse_yandex(page, offset=0):
    parser = YandexPage()
    parser.feed(page)
    if parser.items is None:
        if any(marker in page.lower() for marker in ("showcaptcha", "checkboxcaptcha", "smartcaptcha", "подтвердите, что")):
            raise ValueError("Яндекс запросил капчу. Попробуйте позже или выберите Wikimedia.")
        raise ValueError("Не удалось прочитать выдачу Яндекса. Попробуйте позже или измените запрос.")
    results, seen = [], set()
    for item in parser.items:
        url = item.get("image", "")
        if url.startswith("//"):
            url = "https:" + url
        try:
            trusted_url(url)
        except ValueError:
            continue
        identity = item.get("origUrl") or url
        if identity in seen:
            continue
        seen.add(identity)
        results.append(Picture(plain(item.get("alt") or "Картинка"), url,
                               "https://yandex.ru/images/", provider="Яндекс"))
        if len(results) == offset + 10:
            break
    return results[offset:offset + 10]


class ImageSearch:
    sources = ("Wikimedia Commons", "Яндекс")

    def search(self, query: str, source="Wikimedia Commons", page=0) -> list[Picture]:
        if not query.strip():
            return []
        if source == "Яндекс":
            response = httpx.get("https://yandex.ru/images/search", params={"text": query.strip(), "p": page // 3},
                                 headers=HEADERS, timeout=30)
            if response.status_code in (403, 429) or response.is_redirect:
                raise ValueError("Яндекс ограничил запросы или требует капчу. Попробуйте позже или выберите Wikimedia.")
            response.raise_for_status()
            return parse_yandex(response.text, (page % 3) * 10)
        if source != "Wikimedia Commons":
            raise ValueError("Неизвестный источник картинок.")
        response = httpx.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": query.strip() + " filetype:bitmap", "gsrnamespace": 6,
            "gsrlimit": 10, "gsroffset": page * 10, "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": 960, "iiextmetadatafilter": "Artist|LicenseShortName",
        }, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise ValueError(data["error"].get("info", "Ошибка поиска Wikimedia."))
        pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda x: x.get("index", 0))
        results = []
        for page in pages:
            info = next(iter(page.get("imageinfo", [])), {})
            url = info.get("thumburl") or info.get("url")
            if not url or not info.get("descriptionurl"):
                continue
            metadata = info.get("extmetadata", {})
            results.append(Picture(plain(page["title"].removeprefix("File:")), trusted_url(url),
                                   trusted_url(info["descriptionurl"], False),
                                   plain(metadata.get("Artist", {}).get("value", "")),
                                   plain(metadata.get("LicenseShortName", {}).get("value", ""))))
        return results[:10]



class ImageCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()
        self.file_locks = {}
        self.download_slots = threading.BoundedSemaphore(3)

    def fetch(self, picture: Picture) -> Path:
        url = trusted_url(picture.url)
        name = "koeminer_image_" + hashlib.sha256(url.encode()).hexdigest()[:24] + ".jpg"
        target = self.directory / name
        if target.exists():
            return target
        with self.lock:
            file_lock = self.file_locks.setdefault(name, threading.Lock())
        with file_lock, self.download_slots:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / name
            if target.exists():
                return target
            with httpx.Client(headers=HEADERS, timeout=30) as client:
                for _ in range(5):
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = trusted_url(str(response.url.join(response.headers["location"])))
                            continue
                        response.raise_for_status()
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            content.extend(chunk)
                            if len(content) > 12_000_000:
                                raise ValueError("Изображение превышает 12 МБ.")
                        break
                else:
                    raise ValueError("Слишком много перенаправлений изображения.")
            tmp = target.with_suffix(".tmp")
            try:
                buffer = QBuffer()
                buffer.setData(bytes(content))
                buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                reader = QImageReader(buffer)
                reader.setAutoTransform(True)
                size = reader.size()
                if not size.isValid() or size.width() * size.height() > 40_000_000:
                    raise ValueError("Изображение повреждено или слишком велико.")
                if max(size.width(), size.height()) > 1280:
                    reader.setScaledSize(size.scaled(QSize(1280, 1280), Qt.AspectRatioMode.KeepAspectRatio))
                decoded = reader.read()
                if decoded.isNull() or not decoded.save(str(tmp), "JPEG", 88):
                    raise ValueError("Не удалось декодировать изображение.")
                tmp.replace(target)
            finally:
                tmp.unlink(missing_ok=True)
            return target
