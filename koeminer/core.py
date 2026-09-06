from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

DATA_URL = "https://sentencesearch.neocities.org/data/all_v11.json"
AUDIO_URL = "https://receptomanijalogi.web.app/audio/"


@dataclass
class Mapping:
    expression: str = "Expression"
    sentence: str = "Sentence"
    audio: str = "SentenceAudio"
    translation: str = ""
    append: bool = False
    image: str = ""

    def validate(self):
        targets = [self.sentence, self.audio] + ([self.translation] if self.translation else [])
        if self.image:
            targets.append(self.image)
        if not self.expression or not all(targets):
            raise ValueError("Укажите поля слова, предложения и аудио.")
        if len(set(targets)) != len(targets) or self.expression in targets:
            raise ValueError("Для слова, предложения, аудио, перевода и картинки нужны разные поля.")


@dataclass
class Settings:
    upstream: str = "http://127.0.0.1:8765"
    port: int = 8766
    api_key: str = ""
    profiles: dict[str, Mapping] = field(default_factory=dict)

    def validate(self):
        url = urlparse(self.upstream)
        if url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("AnkiConnect должен быть на http://127.0.0.1:порт.")
        if not 1024 <= self.port <= 65535 or (url.port or 80) == self.port:
            raise ValueError("Порты koeminer и AnkiConnect должны различаться (1024–65535).")
        for mapping in self.profiles.values():
            mapping.validate()

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["profiles"] = {k: Mapping(**v) for k, v in data.get("profiles", {}).items()}
        settings = cls(**data)
        settings.validate()
        return settings

    def save(self, path: Path):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()


@dataclass(frozen=True)
class Sentence:
    japanese: str
    english: str
    source: str
    audio_path: str

    @property
    def audio_url(self):
        # Corpus paths are relative; never let them override the audio host.
        if self.audio_path.startswith(("/", "\\")) or ".." in self.audio_path.split("/") or ":" in self.audio_path:
            raise ValueError("Недопустимый путь аудио в базе предложений.")
        return AUDIO_URL + quote(self.audio_path, safe="/")


class Corpus:
    def __init__(self, cache: Path):
        self.cache = cache
        self.rows: list[Sentence] = []
        self.lock = threading.Lock()

    def load(self):
        with self.lock:
            if self.rows:
                return len(self.rows)
            if self.cache.exists():
                data = json.loads(self.cache.read_text(encoding="utf-8"))
            else:
                response = httpx.get(DATA_URL, timeout=90, follow_redirects=True)
                response.raise_for_status()
                data = response.json()
            rows = [Sentence(plain(x["jap"]), plain(x.get("eng", "")), str(x.get("source", "")), x["audio_jap"])
                    for x in data if x.get("jap") and x.get("audio_jap")]
            if not rows:
                raise ValueError("Сайт вернул пустую базу предложений.")
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            if not self.cache.exists():
                tmp = self.cache.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.cache)
            self.rows = rows
            return len(rows)

    def search(self, query: str, limit=150):
        self.load()
        query = unicodedata.normalize("NFKC", query).strip().casefold()
        if not query:
            return []
        hits = [x for x in self.rows if query in unicodedata.normalize("NFKC", x.japanese).casefold()
                or query in x.english.casefold()]
        return sorted(hits, key=lambda x: (query not in x.japanese.casefold(), len(x.japanese)))[:limit]


class AudioCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()

    def fetch(self, sentence: Sentence) -> Path:
        url = sentence.audio_url
        name = "koeminer_" + hashlib.sha256(url.encode()).hexdigest()[:24] + ".mp3"
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / name
            if path.exists():
                return path
            with httpx.stream("GET", url, timeout=30, follow_redirects=True) as response:
                response.raise_for_status()
                if "text/" in response.headers.get("content-type", ""):
                    raise ValueError("Вместо аудио сайт вернул текстовую страницу.")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 20_000_000:
                        raise ValueError("Аудиофайл превышает 20 МБ.")
            if len(data) < 64 or not (data[:3] == b"ID3" or data[0] == 255 and data[1] & 224 == 224):
                raise ValueError("Сайт вернул повреждённый MP3.")
            path.write_bytes(data)
            return path


def enrich(note: dict, mapping: Mapping, sentence: Sentence | None, filename: str = "", picture=None, image_filename=""):
    mapping.validate()
    result = copy.deepcopy(note)
    fields = result["fields"]
    updates = {mapping.sentence: html.escape(sentence.japanese), mapping.audio: f"[sound:{filename}]"} if sentence else {}
    if sentence and mapping.translation:
        updates[mapping.translation] = html.escape(sentence.english)
    if picture is not None:
        if not mapping.image:
            raise ValueError("Не настроено поле картинки.")
        from .images import trusted_url
        source = html.escape(trusted_url(picture.source_url, False), quote=True)
        credit = html.escape(" · ".join(x for x in (picture.artist, picture.license) if x))
        updates[mapping.image] = (f'<img src="{html.escape(image_filename, quote=True)}"><br>'
                                  f'<small><a href="{source}">Wikimedia Commons</a> · {credit}</small>')
    for name, value in updates.items():
        if name not in fields:
            raise ValueError(f"В заметке нет поля «{name}». Проверьте настройки koeminer и Yomitan.")
        fields[name] = fields[name] + "<br>" + value if mapping.append and fields[name] else value
    # Preserve Yomitan media except attachments to fields explicitly replaced here.
    if not mapping.append:
        for kind in ("audio", "video", "picture"):
            if kind in result:
                attachments = []
                for item in result[kind]:
                    item["fields"] = [f for f in item.get("fields", []) if f not in updates]
                    if item["fields"]:
                        attachments.append(item)
                result[kind] = attachments
    return result
