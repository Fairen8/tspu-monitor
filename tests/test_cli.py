"""Тесты CLI (без сети: сценарии отключены)."""

from __future__ import annotations

import json

from tspu_monitor import __version__
from tspu_monitor.cli import build_parser, main
from tspu_monitor.config import load_config


def test_parser_has_all_commands():
    parser = build_parser()
    for command in (
        "check",
        "report",
        "status",
        "scenarios",
        "config",
        "logs",
        "self-test",
        "daemon",
        "version",
    ):
        assert command in parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_menu_interactive_env(monkeypatch):
    from tspu_monitor.cli import _menu_interactive

    monkeypatch.setenv("TSPU_MENU", "1")
    assert _menu_interactive() is True
    monkeypatch.setenv("TSPU_MENU", "0")
    assert _menu_interactive() is False


def test_run_menu_exit(monkeypatch, capsys):
    from tspu_monitor.cli import run_menu

    monkeypatch.setattr("builtins.input", lambda *args: "0")
    assert run_menu() == 0
    out = capsys.readouterr().out
    assert "меню" in out
    assert "Выход" in out


def test_run_menu_unknown_then_exit(monkeypatch, capsys):
    from tspu_monitor.cli import run_menu

    answers = iter(["что-то", "0"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    assert run_menu() == 0
    assert "Неизвестный пункт" in capsys.readouterr().out


def test_console_encoding_setup_safe():
    from tspu_monitor.cli import _setup_console_encoding

    _setup_console_encoding()  # не должно бросать ни на одной платформе


def test_scenarios_list_json(config_factory, capsys):
    config = config_factory(settings={"scenarios": {"enabled": ["web"]}})
    code = main(
        ["--config-dir", str(config.config_dir), "scenarios", "list", "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in payload}
    assert {"web", "dns", "xray"}.issubset(names)
    web = next(item for item in payload if item["name"] == "web")
    assert web["enabled"] is True


def test_check_without_scenarios(config_factory, capsys):
    config = config_factory(settings={"scenarios": {"enabled": []}})
    code = main(["--config-dir", str(config.config_dir), "check"])
    assert code == 0
    assert "Нет активных сценариев" in capsys.readouterr().out


def test_check_unknown_scenario_fails(config_factory, capsys):
    config = config_factory(settings={"scenarios": {"enabled": []}})
    code = main(
        ["--config-dir", str(config.config_dir), "check", "does-not-exist"]
    )
    assert code == 1
    assert "Неизвестные сценарии" in capsys.readouterr().err


def test_config_set_allowed_key(config_factory):
    config = config_factory()
    code = main(
        [
            "--config-dir",
            str(config.config_dir),
            "config",
            "set",
            "scheduler.check_interval_minutes",
            "30",
        ]
    )
    assert code == 0
    reloaded = load_config(str(config.config_dir))
    assert reloaded.get("scheduler.check_interval_minutes") == 30


def test_config_set_disallowed_key(config_factory, capsys):
    config = config_factory()
    code = main(
        ["--config-dir", str(config.config_dir), "config", "set", "hack.key", "1"]
    )
    assert code == 1
    assert "нельзя" in capsys.readouterr().err


def test_config_get(config_factory, capsys):
    config = config_factory()
    code = main(
        ["--config-dir", str(config.config_dir), "config", "get", "general.log_level"]
    )
    assert code == 0
    assert "INFO" in capsys.readouterr().out


def test_config_validate(config_factory, capsys):
    config = config_factory()
    code = main(["--config-dir", str(config.config_dir), "config", "validate"])
    assert code == 0
    assert "Найдено замечаний" in capsys.readouterr().out


def test_status_json(config_factory, capsys):
    config = config_factory(settings={"scenarios": {"enabled": ["web", "dns"]}})
    code = main(["--config-dir", str(config.config_dir), "status", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == __version__
    assert payload["enabled_scenarios"] == ["web", "dns"]


def test_scenarios_enable_disable(config_factory, capsys):
    config = config_factory(settings={"scenarios": {"enabled": []}})
    assert main(["--config-dir", str(config.config_dir), "scenarios", "enable", "dns"]) == 0
    assert main(["--config-dir", str(config.config_dir), "scenarios", "disable", "dns"]) == 0
    capsys.readouterr()


def test_self_test_json(config_factory, capsys):
    config = config_factory()
    main(["--config-dir", str(config.config_dir), "self-test", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "checks" in payload
    assert any(item["check"].startswith("python") for item in payload["checks"])


def test_logs_missing_file(config_factory, capsys):
    config = config_factory()
    code = main(
        ["--config-dir", str(config.config_dir), "logs", "--file", "main.log"]
    )
    assert code == 1
    capsys.readouterr()
