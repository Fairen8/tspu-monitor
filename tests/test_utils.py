"""Тесты утилит."""

from __future__ import annotations

from tspu_monitor.utils import ensure_writable_dir


def test_ensure_writable_dir_uses_requested_path(tmp_path):
    target = tmp_path / "data"
    result = ensure_writable_dir(target, tmp_path / "fallback")
    assert result == target
    assert result.is_dir()


def test_ensure_writable_dir_falls_back(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    fallback = tmp_path / "fallback"

    result = ensure_writable_dir(blocker, fallback)
    assert result == fallback
    assert result.is_dir()
