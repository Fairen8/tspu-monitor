"""Самоочистка: удаление всех следов TSPU Monitor на машине.

Удаляются данные (прогоны, телеметрия), отчёты и журналы; конфигурация —
только с ``--purge`` (в ней ``secrets.yaml``). Системная установка (PATH,
shim, venv, systemd-юнит) удаляется установщиком:
``install.ps1 -Uninstall`` / ``install.sh --uninstall --purge``.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from .config import AppConfig


@dataclasses.dataclass
class CleanupItem:
    path: Path
    label: str


def _is_safe(path: Path) -> bool:
    """Не удалять домашний каталог, корень и слишком «короткие» пути."""
    resolved = path.expanduser()
    if resolved == Path.home():
        return False
    if len(resolved.parts) <= 3:
        return False
    return resolved.name not in ("", ".", "..")


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _human(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{int(value)} Б"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def _size_of(item: CleanupItem) -> str:
    try:
        if item.path.is_dir():
            return _human(_dir_size(item.path))
        return _human(item.path.stat().st_size)
    except OSError:
        return "?"


def build_cleanup_plan(config: AppConfig, purge: bool = False) -> list[CleanupItem]:
    """Что будет удалено: данные/отчёты/логи, с ``purge`` — и конфигурация."""
    items = [
        CleanupItem(config.data_dir, "данные (прогоны, telemetry.json)"),
        CleanupItem(config.reports_dir, "отчёты"),
        CleanupItem(config.log_dir, "журналы"),
    ]
    if purge:
        items.append(CleanupItem(config.config_dir, "конфигурация (settings/secrets)"))
    return [item for item in items if item.path.exists() and _is_safe(item.path)]


def run_cleanup(
    config: AppConfig,
    *,
    purge: bool = False,
    assume_yes: bool = False,
    prompt: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Показать план и удалить. Возвращает код выхода CLI."""
    items = build_cleanup_plan(config, purge)
    if not items:
        out("Нечего удалять — связанных файлов не найдено.")
        return 0

    out("Будет удалено:")
    for item in items:
        out(f"  {item.path} — {item.label} [{_size_of(item)}]")
    if not purge:
        out("Конфигурация сохранится (удалить и её: --purge).")

    if not assume_yes:
        try:
            answer = str(prompt("Продолжить удаление? [y/N]: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            out("Отменено.")
            return 0
        if answer not in ("y", "yes", "д", "да"):
            out("Отменено.")
            return 0

    # Windows держит файлы журналов открытыми, пока живы logging-хендлеры —
    # закрываем их перед удалением.
    logging.shutdown()

    failed = 0
    for item in items:
        try:
            if item.path.is_dir():
                shutil.rmtree(item.path)
            else:
                item.path.unlink()
            out(f"  удалено: {item.path}")
        except OSError as exc:
            failed += 1
            out(f"  не удалось удалить {item.path}: {exc}")

    if purge:
        base = config.config_dir.parent
        if base.name == "tspu-monitor-data":
            try:
                base.rmdir()  # удастся, только если пусто
                out(f"  удалено: {base}")
            except OSError:
                pass

    if getattr(sys, "frozen", False):
        out("Осталось удалить вручную: сам exe (и папку рядом с ним, если осталась).")
    else:
        out(
            "Если приложение ставилось установщиком, удалите и его: "
            "install.ps1 -Uninstall (Windows) / install.sh --uninstall --purge."
        )
    return 1 if failed else 0
