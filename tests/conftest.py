"""Общие фикстуры тестов."""

from __future__ import annotations

from pathlib import Path

import pytest

from tspu_monitor.config import (
    DEFAULT_SECRETS,
    DEFAULT_SETTINGS,
    AppConfig,
    deep_merge,
    save_yaml,
)


def make_config(
    tmp_path: Path, settings: dict | None = None, secrets: dict | None = None
) -> AppConfig:
    """Создать конфигурацию с данными в tmp_path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    merged_settings = deep_merge(DEFAULT_SETTINGS, settings or {})
    # Тесты по умолчанию не должны ходить в сеть: гасим телеметрию,
    # если тест не включил её явно.
    explicit_telemetry = (settings or {}).get("telemetry", {})
    if "enabled" not in explicit_telemetry:
        merged_settings.setdefault("telemetry", {})["enabled"] = False
    merged_settings.setdefault("general", {})
    merged_settings["general"]["data_dir"] = str(tmp_path / "data")
    merged_settings["general"]["reports_dir"] = str(tmp_path / "reports")
    merged_settings["general"]["log_dir"] = str(tmp_path / "logs")

    merged_secrets = deep_merge(DEFAULT_SECRETS, secrets or {})
    save_yaml(config_dir / "settings.yaml", merged_settings)
    save_yaml(config_dir / "secrets.yaml", merged_secrets)

    return AppConfig(
        settings=merged_settings,
        secrets=merged_secrets,
        config_dir=config_dir,
        settings_path=config_dir / "settings.yaml",
        secrets_path=config_dir / "secrets.yaml",
    )


@pytest.fixture()
def config(tmp_path: Path) -> AppConfig:
    return make_config(tmp_path)


@pytest.fixture()
def config_factory(tmp_path: Path):
    def factory(settings: dict | None = None, secrets: dict | None = None) -> AppConfig:
        return make_config(tmp_path, settings, secrets)

    return factory
