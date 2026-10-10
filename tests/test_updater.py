# -*- coding: utf-8 -*-
"""更新器单测：版本比较、附件选型（zip/exe）、_internal 形态判定。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manga_splitter import updater  # noqa: E402


class _Resp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_release(assets):
    return json.dumps({
        "tag_name": "v9.9.9",
        "body": "notes",
        "assets": [{"name": n, "browser_download_url": f"https://x/{n}",
                    "size": 123} for n in assets],
    }).encode()


def test_is_newer():
    assert updater.is_newer("v2.0.1", "2.0.0")
    assert updater.is_newer("2.1", "2.0.9")
    assert not updater.is_newer("v2.0.0", "2.0.0")
    assert not updater.is_newer("v1.9.9", "2.0.0")
    assert updater.is_newer("V2.0.10", "2.0.9")


def test_check_latest_picks_exe_for_onefile(monkeypatch):
    monkeypatch.setattr(updater, "is_onedir", lambda: False)
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(_fake_release(
                            ["MangaSplitter-9.9.9-win64.zip",
                             "MangaSplitter-9.9.9.exe"])))
    info = updater.check_latest()
    assert info["ok"] and info["latest"]
    assert info["asset_name"].endswith(".exe")


def test_check_latest_picks_zip_for_onedir(monkeypatch):
    monkeypatch.setattr(updater, "is_onedir", lambda: True)
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(_fake_release(
                            ["MangaSplitter-9.9.9.exe",
                             "MangaSplitter-9.9.9-win64.zip"])))
    info = updater.check_latest()
    assert info["asset_name"].endswith(".zip")


def test_check_latest_network_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no net")
    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    info = updater.check_latest()
    assert info["ok"] is False and "no net" in info["error"]


def test_check_latest_falls_back_when_no_matching_asset(monkeypatch):
    monkeypatch.setattr(updater, "is_onedir", lambda: True)
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(_fake_release(["MangaSplitter.exe"])))
    info = updater.check_latest()
    assert info["asset_name"] == "MangaSplitter.exe"   # 退而求其次


def test_is_onedir_reflects_internal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "app_dir", lambda: str(tmp_path))
    assert updater.is_onedir() is False
    os.makedirs(tmp_path / "_internal")
    assert updater.is_onedir() is True
