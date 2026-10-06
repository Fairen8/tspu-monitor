"""Движок выполнения проверок.

``Engine`` связывает менеджер сценариев, классификатор и хранилище
результатов:

1. выбирает сценарии (все включённые или указанные явно);
2. запускает их параллельно;
3. сохраняет :class:`~tspu_monitor.models.RunRecord` в JSON (``data/run-*.json``).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .classification import DiagnosisEngine
from .config import AppConfig
from .logging_setup import get_logger
from .models import Analysis, RunRecord, utc_now_iso
from .scenarios.base import BaseScenario, new_run_id
from .scenarios.manager import ScenarioManager
from .utils import ensure_writable_dir, read_json, write_json_atomic

#: Идентификаторы запусков — только hex (защита от path traversal).
RUN_ID_RE = re.compile(r"^[0-9a-fA-F]{6,64}$")


class Engine:
    """Оркестратор запусков."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.settings = config.settings
        self.secrets = config.secrets
        self.manager = ScenarioManager(
            config.settings, config.secrets, str(config.settings_path)
        )
        self.diagnosis = DiagnosisEngine(config.settings)
        self.data_dir = ensure_writable_dir(
            config.data_dir, Path.home() / ".tspu-monitor" / "data"
        )
        self.logger = get_logger("tspu.engine")
        self.last_record: RunRecord | None = None

    # ------------------------------------------------------------------
    async def run(
        self,
        profiles: list[str] | None = None,
        samples: int | None = None,
        progress: Callable[[Analysis], None] | None = None,
    ) -> RunRecord:
        """Запустить проверки. ``profiles`` — имена сценариев.

        ``progress`` — необязательный колбэк ``fn(analysis)``, вызываемый
        по мере завершения сценариев (для прогресс-вывода в CLI).
        """
        started = utc_now_iso()
        t0 = time.perf_counter()

        if profiles:
            unknown = [name for name in profiles if not self.manager.is_known(name)]
            if unknown:
                raise ValueError(
                    f"Неизвестные сценарии: {', '.join(unknown)}. "
                    f"Доступные: {', '.join(self.manager.known_names())}"
                )
            scenarios: list[BaseScenario] = [
                s for s in (self.manager.get_by_name(n) for n in profiles) if s
            ]
        else:
            scenarios = self.manager.get_enabled()

        self.logger.info(
            "Запуск проверок: сценариев=%d, samples=%s",
            len(scenarios),
            samples if samples is not None else "по умолчанию",
        )
        analyses: list[Analysis] = []
        if scenarios:
            tasks = [
                asyncio.create_task(scenario.run(samples=samples))
                for scenario in scenarios
            ]
            for future in asyncio.as_completed(tasks):
                analysis = await future
                analyses.append(analysis)
                if progress is not None:
                    try:
                        progress(analysis)
                    except Exception:  # noqa: BLE001
                        self.logger.debug("progress callback failed", exc_info=True)

        record = RunRecord(
            run_id=new_run_id(),
            started=started,
            finished=utc_now_iso(),
            duration_seconds=time.perf_counter() - t0,
            analyses=analyses,
        )
        self.last_record = record
        self.save(record)
        self.logger.info(
            "Запуск %s завершён за %.1fs: уровень=%s",
            record.run_id,
            record.duration_seconds,
            record.max_level.title,
        )
        return record

    # ------------------------------------------------------------------
    def save(self, record: RunRecord) -> Path:
        path = self.data_dir / f"run-{record.run_id}.json"
        try:
            write_json_atomic(path, record.to_dict())
        except OSError as exc:
            self.logger.error("Не удалось сохранить %s: %s", path, exc)
        return path

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        files = sorted(
            Path(self.data_dir).glob("run-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        records: list[RunRecord] = []
        for file in files:
            payload = read_json(file)
            if not payload:
                continue
            try:
                records.append(RunRecord.from_dict(payload))
            except (TypeError, ValueError, KeyError) as exc:
                self.logger.warning("Повреждённый run-файл %s: %s", file, exc)
        return records

    def load_run(self, run_id: str) -> RunRecord | None:
        if not RUN_ID_RE.fullmatch(run_id or ""):
            self.logger.warning("Некорректный run_id: %r", run_id)
            return None
        # Путь берём только из glob (не из пользовательского ввода) —
        # это исключает path traversal.
        wanted = f"run-{run_id}.json"
        for path in self.data_dir.glob("run-*.json"):
            if path.name != wanted:
                continue
            payload = read_json(path)
            if not payload:
                return None
            try:
                return RunRecord.from_dict(payload)
            except (TypeError, ValueError, KeyError):
                return None
        return None

    # ------------------------------------------------------------------
    def status_snapshot(self) -> dict[str, Any]:
        """Данные для команды ``status``."""
        last = self.last_record or (self.list_runs(limit=1) or [None])[0]
        return {
            "config_dir": str(self.config.config_dir),
            "data_dir": str(self.data_dir),
            "enabled": self.manager.get_enabled_names(),
            "known": self.manager.known_names(),
            "last_run": last.to_dict() if last else None,
        }
