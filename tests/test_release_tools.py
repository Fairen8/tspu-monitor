"""Тесты инструментов релиза из каталога scripts/."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


changelog_section = load_script("changelog_section")
pytest_summary = load_script("pytest_summary")

SAMPLE = """# Changelog

## [Unreleased]

## [2.1.0] - 2026-11-01

### Added

- Новая проба.

### Fixed

- Исправление.

## [2.0.0] - 2026-10-06

- Первый релиз.
"""


def test_extract_section_stops_before_next_version():
    section = changelog_section.extract_section(SAMPLE, "2.1.0")
    assert section is not None
    assert "## [2.1.0]" in section
    assert "Новая проба" in section
    assert "Первый релиз" not in section


def test_extract_last_section_goes_to_end():
    section = changelog_section.extract_section(SAMPLE, "2.0.0")
    assert section is not None
    assert "Первый релиз" in section


def test_extract_missing_returns_none():
    assert changelog_section.extract_section(SAMPLE, "9.9.9") is None


def test_build_notes_with_fallback():
    assert changelog_section.build_notes(SAMPLE, "9.9.9") is None
    assert (
        changelog_section.build_notes(SAMPLE, "9.9.9", fallback="нет данных")
        == "нет данных\n"
    )


def test_changelog_section_cli(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    output = tmp_path / "notes.md"

    code = changelog_section.main(
        ["2.1.0", "--file", str(changelog), "--output", str(output)]
    )
    assert code == 0
    assert "Новая проба" in output.read_text(encoding="utf-8")

    code = changelog_section.main(["9.9.9", "--file", str(changelog)])
    assert code == 1


def test_pytest_summary_from_junit(tmp_path):
    junit = tmp_path / "results.xml"
    junit.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        "<testsuites>"
        '<testsuite name="pytest" tests="5" failures="1" errors="0" '
        'skipped="1" time="1.5">'
        "</testsuite>"
        "</testsuites>",
        encoding="utf-8",
    )
    summary = pytest_summary.summarize(junit)
    assert "Всего | 5" in summary
    assert "Провалено | 1" in summary
    assert "Пропущено | 1" in summary
    assert "FAIL" in summary


def test_pytest_summary_missing_file(tmp_path):
    summary = pytest_summary.summarize(tmp_path / "none.xml")
    assert "не найден" in summary
    assert summary.startswith("### ")
