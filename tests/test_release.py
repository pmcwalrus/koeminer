"""Version information comes from GitHub without blocking the Qt window."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from koeminer import __version__
from koeminer.proxy import Proxy
from koeminer.ui import MainWindow, RELEASE_API_URL, RELEASE_URL, latest_release_version


def test_latest_release_version_uses_github_api(monkeypatch):
    def get(url, **kwargs):
        assert url == RELEASE_API_URL
        assert kwargs["timeout"] == 5
        return httpx.Response(200, json={"tag_name": "v1.2.3"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    assert latest_release_version() == "v1.2.3"


@pytest.mark.parametrize("payload", [{}, {"tag_name": ""}, {"tag_name": 12}])
def test_latest_release_version_rejects_missing_tag(monkeypatch, payload):
    monkeypatch.setattr(httpx, "get", lambda url, **_: httpx.Response(
        200, json=payload, request=httpx.Request("GET", url)))
    with pytest.raises(ValueError, match="версию релиза"):
        latest_release_version()


def test_version_and_download_link_remain_visible_when_github_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    def unavailable():
        raise httpx.ConnectError("offline")
    monkeypatch.setattr("koeminer.ui.latest_release_version", unavailable)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.show()
    try:
        assert f"Текущая: v{__version__}" in window.version_info.text()
        assert "проверяется" in window.version_info.text()
        assert window.release_link.openExternalLinks()
        assert RELEASE_URL in window.release_link.text()
        deadline = time.monotonic() + 3
        while "недоступна" not in window.version_info.text() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.version_info.text() == f"Текущая: v{__version__} · Последняя: недоступна"
        assert RELEASE_URL in window.release_link.text()
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_latest_release_displayed_in_status_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    monkeypatch.setattr("koeminer.ui.latest_release_version", lambda: "v1.2.3")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.show()
    try:
        deadline = time.monotonic() + 3
        while "v1.2.3" not in window.version_info.text() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.version_info.text() == f"Текущая: v{__version__} · Последняя: v1.2.3"
        assert window.version_info.isVisible()
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_long_release_tag_is_available_in_tooltip(tmp_path, monkeypatch):
    monkeypatch.setattr(Proxy, "start", lambda _: None)
    monkeypatch.setattr(Proxy, "stop", lambda _: None)
    version = "v1.2.3-" + "build" * 10
    monkeypatch.setattr("koeminer.ui.latest_release_version", lambda: version)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.show()
    window.resize(360, 320)
    try:
        deadline = time.monotonic() + 3
        while not window.version_info.toolTip() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.version_info.toolTip() == f"Текущая: v{__version__} · Последняя: {version}"
        assert window.version_info.text().endswith("…")
        assert window.version_info.isVisible() and window.release_link.isVisible()
        assert window.release_link.geometry().right() <= window.statusBar().width()
    finally:
        window.shutdown()
        window.tray.hide()
        window.deleteLater()
        app.processEvents()
