"""Тесты редактирования секретов в логах Telegram."""

from __future__ import annotations

from tspu_monitor.telegram_bot import redact_secret, safe_proxy


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


def test_safe_proxy_hides_credentials():
    assert safe_proxy("http://user:pass@10.0.0.5:3128") == "http://***@10.0.0.5:3128"
    assert safe_proxy("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert safe_proxy(None) == "нет"
    assert safe_proxy("") == "нет"
