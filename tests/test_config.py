"""Тесты конфигурации."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.conftest import make_config
from tspu_monitor.config import (
    DEFAULT_SETTINGS,
    AppConfig,
    coerce_config_value,
    deep_merge,
    find_config_dir,
    get_dotted,
    load_config,
    set_dotted,
    validate_config,
)


def test_deep_merge_nested():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"b": 10}, "e": 4}
    merged = deep_merge(base, override)
    assert merged == {"a": {"b": 10, "c": 2}, "d": 3, "e": 4}
    # base не мутируется
    assert base["a"]["b"] == 1


def test_get_set_dotted():
    data = {"a": {"b": {"c": 1}}}
    assert get_dotted(data, "a.b.c") == 1
    assert get_dotted(data, "a.x", "default") == "default"
    set_dotted(data, "a.b.c", 5)
    assert data["a"]["b"]["c"] == 5
    set_dotted(data, "x.y.z", "new")
    assert data["x"]["y"]["z"] == "new"


def test_find_config_dir_explicit(tmp_path: Path):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text("general: {}\n", encoding="utf-8")
    assert find_config_dir(str(config_dir)) == config_dir


def test_load_config_merges_defaults(tmp_path: Path):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text(
        "general:\n  log_level: DEBUG\nscheduler:\n  check_interval_minutes: 15\n",
        encoding="utf-8",
    )
    config = load_config(str(config_dir))
    assert config.get("general.log_level") == "DEBUG"
    assert config.get("scheduler.check_interval_minutes") == 15
    # значение по умолчанию сохранилось
    assert "web" in config.get("scenarios.enabled")
    assert config.get("classification.level_thresholds.high") == 50


def test_config_paths_and_save(config: AppConfig):
    assert config.data_dir.name == "data"
    assert config.reports_dir.name == "reports"
    config.set("scheduler.check_interval_minutes", 30)
    config.save_settings()
    reloaded = load_config(str(config.config_dir))
    assert reloaded.get("scheduler.check_interval_minutes") == 30


def test_scenario_option(tmp_path: Path):
    config = make_config(
        tmp_path,
        settings={"scenarios": {"options": {"wireguard": {"port": 1234}}}},
    )
    assert config.scenario_option("wireguard") == {"port": 1234}
    assert config.scenario_option("missing") == {}


def test_validate_config_detects_problems(tmp_path: Path):
    config = make_config(
        tmp_path,
        settings={
            "scheduler": {"report_time": "25:99", "report_day": "funday"},
            "webhook": {"enabled": True, "url": ""},
        },
    )
    problems = validate_config(config)
    assert any("report_time" in p for p in problems)
    assert any("report_day" in p for p in problems)
    assert any("webhook.url" in p for p in problems)
    assert any("wireguard_server" in p for p in problems)


def test_validate_config_ok(tmp_path: Path):
    config = make_config(
        tmp_path,
        secrets={
            "targets": {
                "wireguard_server": "203.0.113.1",
                "amnezia_server": "203.0.113.1",
                "openvpn_server": "203.0.113.1",
                "shadowsocks_server": "203.0.113.1",
                "xray_server": "203.0.113.1",
            }
        },
    )
    assert validate_config(config) == []


def test_coerce_config_value():
    assert coerce_config_value("scheduler.check_interval_minutes", "30") == 30
    assert coerce_config_value("webhook.enabled", "true") is True
    assert coerce_config_value("webhook.enabled", "0") is False
    assert coerce_config_value("webhook.url", "https://x") == "https://x"
    try:
        coerce_config_value("nope.nope", "1")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("ожидался KeyError")


def test_default_settings_have_required_sections():
    for section in (
        "general",
        "scheduler",
        "scenarios",
        "testing",
        "classification",
        "webhook",
        "reports",
    ):
        assert section in DEFAULT_SETTINGS


def test_yaml_roundtrip_preserves_unicode(config: AppConfig):
    config.set("webhook.url", "https://пример.рф/хук")
    config.save_settings()
    data = yaml.safe_load(config.settings_path.read_text(encoding="utf-8"))
    assert data["webhook"]["url"] == "https://пример.рф/хук"
