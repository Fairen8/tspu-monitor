"""Согласованность метаданных и файлов репозитория."""

from __future__ import annotations

import tomllib
from pathlib import Path

from tspu_monitor import __version__

ROOT = Path(__file__).resolve().parents[1]


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_matches_pyproject():
    assert pyproject()["project"]["version"] == __version__


def test_license_file():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert pyproject()["project"]["license"] == "MIT"


def test_changelog_mentions_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog


def test_repository_documents_present():
    for name in (
        "README.md",
        "DOCS.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE",
    ):
        assert (ROOT / name).exists(), f"нет файла {name}"


def test_py_typed_marker():
    assert (ROOT / "src" / "tspu_monitor" / "py.typed").exists()


def test_ci_workflows_present():
    workflows = ROOT / ".github" / "workflows"
    for name in (
        "ci.yml",
        "release.yml",
        "codeql.yml",
        "dependency-review.yml",
        "scorecard.yml",
    ):
        assert (workflows / name).exists(), f"нет workflow {name}"
