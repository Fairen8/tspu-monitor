"""Тесты редактирования секретов в логах Telegram."""

from __future__ import annotations

from tspu_monitor.telegram_bot import redact_secret


def test_redact_secret_replaces_token():
    token = "123456789:AAEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"
    text = f"Client error for url: https://api.telegram.org/bot{token}/getMe"
    redacted = redact_secret(text, token)
    assert token not in redacted
    assert "***" in redacted


def test_redact_secret_keeps_other_text():
    assert redact_secret("ошибка без токена", "secret") == "ошибка без токена"
    assert redact_secret("текст", None) == "текст"
    assert redact_secret("текст", "") == "текст"
