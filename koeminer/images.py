"""Wikimedia Commons search and local image cache."""
import hashlib
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader

from .core import plain

HEADERS = {"User-Agent": "koeminer/0.2.0 (https://github.com/pmcwalrus/koeminer)"}


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
    hosts = {"upload.wikimedia.org", "thumb.wikimedia.org", "api.openverse.org"} if media else {"commons.wikimedia.org", "openverse.org"}
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.port not in (None, 443):
        raise ValueError("Недопустимый адрес изображения.")
    if media and parsed.hostname == "api.openverse.org":
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["v1", "images"] or parts[3] != "thumb":
            raise ValueError("Недопустимый адрес миниатюры Openverse.")
        uuid.UUID(parts[2])
    return url


class ImageSearch:
    sources = ("Wikimedia Commons", "Openverse")

    def search(self, query: str, source="Wikimedia Commons") -> list[Picture]:
        if not query.strip():
            return []
        if source == "Openverse":
            return self.search_openverse(query)
        if source != "Wikimedia Commons":
            raise ValueError("Неизвестный источник картинок.")
        response = httpx.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": query.strip() + " filetype:bitmap", "gsrnamespace": 6,
            "gsrlimit": 5, "prop": "imageinfo", "iiprop": "url|extmetadata",
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
        return results[:5]

    def search_openverse(self, query):
        response = httpx.get("https://api.openverse.org/v1/images/", params={
            "q": query.strip(), "page_size": 5, "excluded_source": "wikimedia",
        }, headers=HEADERS, timeout=15)
        response.raise_for_status()
        results = []
        for item in response.json().get("results", []):
            if item.get("source") == "wikimedia":
                continue
            identifier = str(uuid.UUID(item["id"]))
            results.append(Picture(plain(item.get("title") or "Изображение"),
                                   f"https://api.openverse.org/v1/images/{identifier}/thumb/",
                                   f"https://openverse.org/image/{identifier}",
                                   plain(item.get("creator") or ""),
                                   str(item.get("license") or ""), "Openverse"))
        return results[:5]


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
