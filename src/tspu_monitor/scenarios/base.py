"""Базовый класс сценариев.

Сценарий — это группа проб, сфокусированная на одном протоколе или
сервисе. Сценарий:

1. собирает список проб (:meth:`get_probes`);
2. запускает их параллельно;
3. передаёт результаты движку классификации;
4. может добавить собственные находки (:meth:`extra_findings`) и
   рекомендации (:meth:`scenario_recommendations`).
"""

from __future__ import annotations

import abc
import asyncio
import uuid
from collections.abc import Sequence
from typing import Any

from ..classification import DiagnosisEngine, Finding
from ..config import deep_merge
from ..logging_setup import get_logger
from ..models import (
    Analysis,
    BlockLevel,
    ProbeResult,
    Severity,
)
from ..probes.base import BaseProbe

GLOBAL_TESTING_KEYS = (
    "timeout_seconds",
    "ping_count",
    "max_ttl",
    "samples",
    "control_hosts",
    "blocked_test_hosts",
    "dns_resolvers",
    "ports_tcp",
    "ports_udp",
)


class BaseScenario(abc.ABC):
    """Абстрактный сценарий."""

    name: str = "base"
    title: str = "Базовый"
    description: str = ""
    version: str = "2.0.0"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        secrets: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        diagnosis: DiagnosisEngine | None = None,
    ) -> None:
        merged = deep_merge(self.get_default_config(), config or {})
        testing = (settings or {}).get("testing", {}) or {}
        for key in GLOBAL_TESTING_KEYS:
            if key in testing and key not in merged:
                merged[key] = testing[key]
        self.config: dict[str, Any] = merged
        self.secrets: dict[str, Any] = secrets or {}
        self.settings: dict[str, Any] = settings or {}
        self.diagnosis: DiagnosisEngine = diagnosis or DiagnosisEngine(settings)
        self.logger = get_logger("tspu.scenario." + self.name)

    # -- контракт ---------------------------------------------------------
    @abc.abstractmethod
    def get_probes(self) -> list[BaseProbe]:
        """Список проб сценария."""

    @abc.abstractmethod
    def get_default_config(self) -> dict[str, Any]:
        """Конфигурация по умолчанию (до merge с options из settings.yaml)."""

    @abc.abstractmethod
    def target(self) -> str:
        """Основная цель сценария (для отображения в отчёте)."""

    def extra_findings(self, results: Sequence[ProbeResult]) -> list[Finding]:
        """Дополнительные находки, специфичные для сценария."""
        return []

    def scenario_recommendations(self, results: Sequence[ProbeResult]) -> list[str]:
        """Дополнительные рекомендации, специфичные для сценария."""
        return []

    # -- запуск -----------------------------------------------------------
    async def run(self, samples: int | None = None) -> Analysis:
        if samples is not None:
            self.config["samples"] = int(samples)
        target = self.target()
        if not target:
            analysis = self.diagnosis.diagnose(self.name, self.title, "не задан", [])
            analysis.causes.append("Целевой сервер/ресурс не задан")
            analysis.recommendations.append(
                f"Укажите адрес в secrets.yaml (targets.{self.name}_server) "
                "или в scenarios.options." + self.name
            )
            return analysis

        probes = self.get_probes()
        self.logger.info("Запуск сценария '%s' (%d проб)", self.name, len(probes))
        gathered = await asyncio.gather(
            *(p.run() for p in probes), return_exceptions=True
        )

        results: list[ProbeResult] = []
        for probe, batch in zip(probes, gathered, strict=True):
            if isinstance(batch, BaseException):
                self.logger.exception(
                    "Проба %s упала: %s", probe.name, batch
                )
                results.append(
                    ProbeResult(
                        probe=probe.name,
                        target=target,
                        success=False,
                        severity=Severity.CRITICAL,
                        error=f"{type(batch).__name__}: {batch}",
                    )
                )
                continue
            if isinstance(batch, ProbeResult):
                # Защита от пользовательских проб, забывших вернуть список.
                self.logger.warning(
                    "Проба %s вернула ProbeResult вместо list[ProbeResult] — оборачиваю",
                    probe.name,
                )
                batch = [batch]
            if not isinstance(batch, (list, tuple)):
                self.logger.error(
                    "Проба %s вернула %s вместо list[ProbeResult]",
                    probe.name,
                    type(batch).__name__,
                )
                results.append(
                    ProbeResult(
                        probe=probe.name,
                        target=target,
                        success=False,
                        severity=Severity.CRITICAL,
                        error=f"некорректный тип результата: {type(batch).__name__}",
                    )
                )
                continue
            results.extend(batch)

        for result in results:
            self.logger.getChild("probes").info(
                "%s @ %s success=%s severity=%s duration=%.0fms err=%s",
                result.probe,
                result.target,
                result.success,
                result.severity.value,
                result.duration_ms,
                result.error or "-",
            )

        analysis = self.diagnosis.diagnose(
            profile=self.name,
            title=self.title,
            target=target,
            results=results,
            extra_findings=self.extra_findings(results),
        )
        for recommendation in self.scenario_recommendations(results):
            if recommendation not in analysis.recommendations:
                analysis.recommendations.append(recommendation)
        if analysis.level == BlockLevel.NONE and not analysis.causes:
            analysis.causes.append("Блокировок не обнаружено")
        self.logger.info(
            "Сценарий '%s' завершён: %s (%d/100)",
            self.name,
            analysis.level.title,
            analysis.score,
        )
        return analysis


def new_run_id() -> str:
    """Короткий идентификатор запуска."""
    return uuid.uuid4().hex[:12]
