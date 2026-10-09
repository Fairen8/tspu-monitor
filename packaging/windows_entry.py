"""Точка входа портативной сборки ``tspu-monitor.exe`` (PyInstaller).

Держит все данные рядом с exe: ``<папка exe>/tspu-monitor-data`` — удалили
папку, и на компьютере не осталось следов. Если каталог exe недоступен для
записи (например, Program Files), используется обычный профиль пользователя.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _setup_portable() -> None:
    if not getattr(sys, "frozen", False) or os.environ.get("TSPU_CONFIG_DIR"):
        return
    base = Path(sys.executable).resolve().parent / "tspu-monitor-data"
    config_dir = base / "config"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        if not (config_dir / "settings.yaml").exists():
            import yaml

            from tspu_monitor.config import (
                DEFAULT_SECRETS,
                DEFAULT_SETTINGS,
                deep_merge,
            )

            settings = deep_merge(DEFAULT_SETTINGS, {})
            settings["general"]["data_dir"] = str(base / "data")
            settings["general"]["reports_dir"] = str(base / "reports")
            settings["general"]["log_dir"] = str(base / "logs")
            (config_dir / "settings.yaml").write_text(
                yaml.safe_dump(settings, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            (config_dir / "secrets.yaml").write_text(
                yaml.safe_dump(DEFAULT_SECRETS, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        os.environ["TSPU_CONFIG_DIR"] = str(config_dir)
    except OSError:
        # Каталог только для чтения — работаем с профилем пользователя.
        return


def main() -> int:
    _setup_portable()
    from tspu_monitor.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
