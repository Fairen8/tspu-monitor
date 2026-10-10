"""Тесты самоочистки (cleanup)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_config
from tspu_monitor.cleanup import build_cleanup_plan, run_cleanup


def seed(config) -> None:
    """Создать файлы, которые должна удалить самоочистка."""
    for directory in (config.data_dir, config.reports_dir, config.log_dir):
        Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(config.data_dir) / "run-aaaa.json").write_text("{}", encoding="utf-8")
    (Path(config.data_dir) / "telemetry.json").write_text("{}", encoding="utf-8")
    (Path(config.reports_dir) / "report-1.txt").write_text("x", encoding="utf-8")
    (Path(config.log_dir) / "main.log").write_text("log", encoding="utf-8")


def test_plan_includes_data_reports_logs(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    plan = build_cleanup_plan(config)
    labels = {item.label.split()[0] for item in plan}
    assert "данные" in labels
    assert "отчёты" in labels
    assert "журналы" in labels
    assert all("конфигурация" not in item.label for item in plan)


def test_plan_purge_includes_config(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    plan = build_cleanup_plan(config, purge=True)
    assert any("конфигурация" in item.label for item in plan)


def test_cleanup_removes_files_keeps_config(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    code = run_cleanup(config, assume_yes=True, out=lambda _: None)
    assert code == 0
    assert not Path(config.data_dir).exists()
    assert not Path(config.reports_dir).exists()
    assert not Path(config.log_dir).exists()
    assert Path(config.config_dir).exists()
    assert Path(config.settings_path).exists()


def test_cleanup_purge_removes_config(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    code = run_cleanup(config, purge=True, assume_yes=True, out=lambda _: None)
    assert code == 0
    assert not Path(config.config_dir).exists()


def test_cleanup_declined_keeps_everything(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    code = run_cleanup(
        config, assume_yes=False, prompt=lambda _: "n", out=lambda _: None
    )
    assert code == 0
    assert Path(config.data_dir).exists()
    assert (Path(config.data_dir) / "run-aaaa.json").exists()


def test_cleanup_confirmed_with_prompt(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    code = run_cleanup(
        config, assume_yes=False, prompt=lambda _: "да", out=lambda _: None
    )
    assert code == 0
    assert not Path(config.data_dir).exists()


def test_cleanup_nothing_to_do(tmp_path):
    config = make_config(tmp_path)
    messages: list[str] = []
    code = run_cleanup(config, assume_yes=True, out=messages.append)
    assert code == 0
    assert any("Нечего удалять" in message for message in messages)
