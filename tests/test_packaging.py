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
        "auto-release.yml",
        "release-guard.yml",
        "codeql.yml",
        "dependency-review.yml",
        "scorecard.yml",
    ):
        assert (workflows / name).exists(), f"нет workflow {name}"


def test_release_workflow_is_reusable():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_call" in text
    assert "RELEASE_TAG" in text


def test_auto_release_watches_release_branch():
    text = (ROOT / ".github" / "workflows" / "auto-release.yml").read_text(
        encoding="utf-8"
    )
    assert "branches: [release]" in text
    assert "release.yml" in text


def test_release_guard_requires_main():
    text = (ROOT / ".github" / "workflows" / "release-guard.yml").read_text(
        encoding="utf-8"
    )
    assert "branches: [release]" in text
    assert "Source is main" in text


def test_release_script_present():
    script = ROOT / "scripts" / "release.sh"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "release" in content
    assert "gh pr create" in content


def test_installers_present():
    for name in ("install.sh", "install.ps1"):
        assert (ROOT / name).exists(), f"нет установщика {name}"

    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    for flag in ("--version", "--prefix", "--no-service", "--with-web", "--uninstall"):
        assert flag in script
    for distro in ("apt-get", "dnf", "apk", "pacman", "zypper", "brew"):
        assert distro in script
