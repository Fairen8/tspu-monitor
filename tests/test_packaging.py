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
    for name in ("install.sh", "install.ps1", "install.cmd"):
        assert (ROOT / name).exists(), f"нет установщика {name}"

    script = (ROOT / "install.sh").read_text(encoding="utf-8")
    for flag in (
        "--version",
        "--prefix",
        "--no-service",
        "--with-web",
        "--no-telemetry",
        "--uninstall",
        "--purge",
        "--no-color",
    ):
        assert flag in script
    for distro in ("apt-get", "dnf", "apk", "pacman", "zypper", "brew"):
        assert distro in script


def test_install_cmd_is_ascii():
    # Кириллица в .cmd ломает разбор команд cmd.exe (проверено).
    data = (ROOT / "install.cmd").read_bytes()
    data.decode("ascii")


def test_install_ps1_is_robust():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    for needle in (
        "py -0p",
        "Get-PythonCandidates",
        "Test-PythonUsable",
        "WaitForExit",
        "python.org",
        "Expand-Archive",
        "WindowsApps",
    ):
        assert needle in text, f"нет {needle} в install.ps1"
    # Окно не должно закрываться при ошибке.
    assert "Wait-OnExit" in text


def test_windows_portable_exe_pipeline():
    entry = ROOT / "packaging" / "windows_entry.py"
    assert entry.exists(), "нет packaging/windows_entry.py"
    assert "TSPU_CONFIG_DIR" in entry.read_text(encoding="utf-8")

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    for needle in ("pyinstaller", "windows_entry.py", "windows-x64.exe"):
        assert needle in workflow, f"нет {needle} в release.yml"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "windows-x64.exe" in readme


def test_release_platform_notes():
    notes = (ROOT / "scripts" / "release_platforms.md").read_text(encoding="utf-8")
    for needle in (
        "Debian",
        "Windows",
        "install-linux-macos.sh",
        "install-windows.ps1",
        "install-windows.cmd",
        ".deb",
        ".pyz",
    ):
        assert needle in notes, f"нет упоминания {needle}"
    assert "НЕ работает" in notes

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "release_platforms.md" in workflow


def test_copilot_review_setup():
    instructions = ROOT / ".github" / "copilot-instructions.md"
    assert instructions.exists()
    assert "ревью" in instructions.read_text(encoding="utf-8").lower()

    script = (ROOT / "scripts" / "protect-repo.sh").read_text(encoding="utf-8")
    assert "copilot_code_review" in script
    assert "review_on_push" in script
    assert "rules/branches" not in script
    # main: PR обязателен, прямой push — только администраторам
    assert "branches/main/protection" in script
    assert '"enforce_admins": false' in script
    assert '"required_approving_review_count": 0' in script
