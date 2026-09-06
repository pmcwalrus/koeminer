from __future__ import annotations

import base64
import copy
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx

from .core import AudioCache, Mapping, Sentence, Settings, enrich, plain


@dataclass
class Selection:
    note: dict
    mapping: Mapping
    done: threading.Event = field(default_factory=threading.Event)
    sentence: Sentence | None = None
    cancelled: bool = True
    expired: bool = False


class Proxy:
    def __init__(self, settings: Settings, audio: AudioCache, on_selection, on_status=lambda _: None):
        self.settings = settings
        self.audio = audio
        self.on_selection = on_selection
        self.on_status = on_status
        self.selection_lock = threading.Lock()
        self.pending: Selection | None = None
        self.server = None

    def upstream(self, request):
        payload = copy.deepcopy(request)
        if self.settings.api_key:
            payload["key"] = self.settings.api_key
        try:
            response = httpx.post(self.settings.upstream, json=payload, timeout=60, trust_env=False)
            response.raise_for_status()
            result = response.json()
        except httpx.ConnectError as exc:
            raise ValueError("Нет соединения с Anki. Запустите Anki с AnkiConnect и проверьте адрес в настройках.") from exc
        if payload.get("version", 4) <= 4:
            if isinstance(result, dict) and "error" in result:
                return {"result": None, "error": result["error"]}
            return {"result": result, "error": None}
        if not isinstance(result, dict) or "error" not in result or "result" not in result:
            raise ValueError("AnkiConnect вернул неожиданный ответ.")
        return result

    def call(self, action, **params):
        result = self.upstream({"action": action, "version": 6, "params": params})
        if result["error"]:
            raise ValueError(result["error"])
        return result["result"]

    def dispatch(self, request):
        try:
            if not isinstance(request, dict) or not isinstance(request.get("action"), str):
                raise ValueError("Некорректный запрос AnkiConnect.")
            if self.settings.api_key and request.get("key") != self.settings.api_key:
                raise ValueError("Неверный API-ключ. Укажите тот же ключ в Yomitan и koeminer.")
            action = request["action"]
            if action == "addNote":
                return self.add_note(request)
            if action == "addNotes":
                raise ValueError("koeminer: добавляйте карточки по одной. Для массового импорта используйте AnkiConnect напрямую.")
            if action == "multi" and self.has_creation(request):
                raise ValueError("koeminer: создание карточек внутри multi не поддерживается; используйте addNote.")
            return self.upstream(request)
        except Exception as exc:
            self.on_status(str(exc))
            return {"result": None, "error": str(exc)}

    @classmethod
    def has_creation(cls, request):
        return request.get("action") in ("addNote", "addNotes", "guiAddCards") or any(
            cls.has_creation(x) for x in request.get("params", {}).get("actions", []))

    def add_note(self, request):
        if not self.selection_lock.acquire(blocking=False):
            raise ValueError("Сначала завершите выбор предложения в открытом окне koeminer.")
        try:
            note = request["params"]["note"]
            mapping = self.settings.profiles.get(note["modelName"])
            if mapping is None:
                raise ValueError(f"Настройте поля для типа заметки «{note['modelName']}» в koeminer.")
            mapping = copy.deepcopy(mapping)
            mapping.validate()
            for name in (mapping.expression, mapping.sentence, mapping.audio, mapping.translation):
                if name and name not in note["fields"]:
                    raise ValueError(f"В запросе Yomitan отсутствует поле «{name}».")
            if not plain(note["fields"][mapping.expression]):
                raise ValueError("Поле слова пустое. Проверьте маркер expression / character в Yomitan.")
            # Verify connectivity before asking the user to spend time choosing.
            self.call("version")
            selection = Selection(copy.deepcopy(note), mapping)
            self.pending = selection
            self.on_status("Ожидание выбора предложения…")
            self.on_selection(selection)
            if not selection.done.wait(600):
                selection.expired = True
                raise ValueError("Время выбора истекло (10 минут). Нажмите + в Yomitan снова.")
            if selection.cancelled:
                raise ValueError("Создание карточки отменено в koeminer.")
            payload = copy.deepcopy(request)
            if selection.sentence is not None:
                self.on_status("Сохранение аудио и карточки…")
                path = self.audio.fetch(selection.sentence)
                filename = self.call("storeMediaFile", filename=path.name,
                                     data=base64.b64encode(path.read_bytes()).decode())
                if not isinstance(filename, str) or not filename or any(x in filename for x in "[]/\\"):
                    raise ValueError("AnkiConnect не подтвердил сохранение аудиофайла.")
                payload["params"]["note"] = enrich(note, mapping, selection.sentence, filename)
            response = self.upstream(payload)
            self.on_status(f"Карточка создана · {response['result']}" if not response["error"] else response["error"])
            return response
        finally:
            self.pending = None
            self.selection_lock.release()

    def start(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def allowed(self):
                host = self.headers.get("Host", "").split(":")[0]
                origin = self.headers.get("Origin")
                return host in ("127.0.0.1", "localhost") and (
                    not origin or urlparse(origin).scheme in ("chrome-extension", "moz-extension"))

            def reply(self, value, status=200):
                data = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                origin = self.headers.get("Origin")
                if origin and self.allowed():
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_OPTIONS(self):
                self.reply({}, 200 if self.allowed() else 403)

            def do_GET(self):
                self.reply({"application": "koeminer", "version": "0.1.0"}, 200 if self.allowed() else 403)

            def do_POST(self):
                if not self.allowed():
                    self.reply({"result": None, "error": "Origin is not allowed"}, 403)
                    return
                try:
                    self.connection.settimeout(15)
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 32_000_000:
                        raise ValueError("Invalid request size")
                    request = json.loads(self.rfile.read(length))
                    result = proxy.dispatch(request)
                    if isinstance(request, dict) and request.get("version", 4) <= 4:
                        result = {"error": result["error"]} if result["error"] else result["result"]
                    self.reply(result)
                except Exception as exc:
                    self.reply({"result": None, "error": str(exc)}, 400)

        self.settings.validate()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.settings.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        if self.pending:
            self.pending.cancelled = True
            self.pending.done.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
