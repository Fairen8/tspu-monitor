"""Генерация отчётов: человекочитаемый TXT и машинный JSON."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import AppConfig
from .logging_setup import get_logger, read_last_lines
from .models import (
    Analysis,
    BlockLevel,
    DisconnectLevel,
    RunRecord,
    utc_now_iso,
)
from .utils import fmt_dt, fmt_ms, parse_iso, truncate

WEEKDAY_RU = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}


class Reporter:
    """Собирает отчёты из сохранённых запусков."""

    SEPARATOR = "=" * 62
    SECTION = "-" * 62

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.settings = config.settings
        reports_cfg = self.settings.get("reports", {}) or {}
        self.output_dir = self._prepare_output_dir(config.reports_dir)
        self.include_logs = bool(reports_cfg.get("include_logs", True))
        self.max_log_lines = int(reports_cfg.get("max_log_lines", 500))
        self.logger = get_logger("tspu.reporter")

    @staticmethod
    def _prepare_output_dir(path: Path) -> Path:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            fallback = Path.home() / ".tspu-monitor" / "reports"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    # ------------------------------------------------------------------
    def generate(
        self,
        records: list[RunRecord],
        latest: RunRecord | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        now = datetime.now(UTC)
        window_end = window_end or now
        if window_start is None:
            hours = int(
                self.settings.get("scheduler", {}).get("report_interval_hours", 168)
            )
            window_start = window_end - timedelta(hours=hours)
        windowed = [
            r for r in records if window_start <= parse_iso(r.finished) <= window_end
        ]
        if latest is not None:
            windowed = [r for r in windowed if r.run_id != latest.run_id]
            windowed.append(latest)
        windowed.sort(key=lambda r: r.finished)

        text = self._build_text(windowed, latest, window_start, window_end)
        payload = self._build_json(windowed, latest, window_start, window_end)
        return text, payload

    def save(self, content: str, name: str | None = None) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.output_dir / (name or f"report-{timestamp}.txt")
        path.write_text(content, encoding="utf-8")
        self.logger.info("Отчёт сохранён: %s", path)
        return path

    # ------------------------------------------------------------------
    # TXT
    # ------------------------------------------------------------------
    def _build_text(
        self,
        records: list[RunRecord],
        latest: RunRecord | None,
        window_start: datetime,
        window_end: datetime,
    ) -> str:
        lines: list[str] = [
            self.SEPARATOR,
            "TSPU MONITOR — ОТЧЁТ О БЛОКИРОВКАХ".center(62),
            f"Период: {fmt_dt(window_start)} — {fmt_dt(window_end)}".center(62),
            f"Сгенерирован: {fmt_dt(datetime.now(UTC))}".center(62),
            self.SEPARATOR,
            "",
        ]

        lines += self._section_summary(records, latest)
        lines.append("")
        lines += self._section_profiles(records, latest)
        lines.append("")
        lines += self._section_forecast(records)
        lines.append("")
        lines += self._section_logs()
        lines.append("")
        lines.append(self.SEPARATOR)
        return "\n".join(lines)

    def _section_summary(
        self, records: list[RunRecord], latest: RunRecord | None
    ) -> list[str]:
        lines = ["1. ИТОГОВАЯ ОЦЕНКА", self.SECTION]
        if not records:
            lines.append("   За период нет запусков. Выполните: tspu-monitor check")
            return lines

        max_level = max(r.max_level for r in records)
        max_score = max(r.max_score for r in records)

        by_profile: dict[str, Analysis] = {}
        for record in records:
            for analysis in record.analyses:
                by_profile[analysis.profile] = analysis

        types: list[str] = []
        causes: list[str] = []
        recommendations: list[str] = []
        disconnect = DisconnectLevel.NONE
        for analysis in by_profile.values():
            for title in analysis.type_titles:
                if title not in types:
                    types.append(title)
            for cause in analysis.causes:
                if cause not in causes:
                    causes.append(cause)
            for recommendation in analysis.recommendations:
                if recommendation not in recommendations:
                    recommendations.append(recommendation)
            disconnect = max(disconnect, analysis.disconnect)

        lines.append(f"   Запусков за период: {len(records)}")
        lines.append(
            f"   Максимальный уровень блокировок: {max_level.title} ({max_score}/100)"
        )
        lines.append(f"   Обрывы: {disconnect.title}")
        lines.append(
            "   Типы блокировок: " + ("; ".join(types) if types else "не выявлены")
        )
        lines.append("   Причины:")
        if causes:
            lines.extend(f"      • {c}" for c in causes[:12])
        else:
            lines.append("      нет")
        lines.append("   Рекомендации:")
        if recommendations:
            lines.extend(f"      • {r}" for r in recommendations[:12])
        else:
            lines.append("      нет")
        lines.append("")
        lines.append("   Сводка по сценариям:")
        lines.append("      {:<24} {:>10} {:>9}  {}".format(
            "Сценарий", "Уровень", "Баллы", "Обрывы"
        ))
        for analysis in sorted(by_profile.values(), key=lambda a: -a.score):
            title = truncate(analysis.title, 24)
            lines.append(
                f"      {title:<24} {analysis.level.title:>10} "
                f"{analysis.score:>7}/100  {analysis.disconnect.title}"
            )
        return lines

    def _section_profiles(
        self, records: list[RunRecord], latest: RunRecord | None
    ) -> list[str]:
        lines = ["2. ДЕТАЛИ ПО СЦЕНАРИЯМ", self.SECTION]
        target_record = latest or (records[-1] if records else None)
        if target_record is None or not target_record.analyses:
            lines.append("   Нет данных.")
            return lines

        lines.append(
            f"   Последний запуск: {target_record.run_id} "
            f"({fmt_dt(parse_iso(target_record.finished))})"
        )
        for analysis in target_record.analyses:
            lines.append("")
            lines.append(f"   --- {analysis.title} ({analysis.profile}) ---")
            lines.append(f"   Цель: {analysis.target}")
            lines.append(
                f"   Уровень: {analysis.level.title} ({analysis.score}/100) | "
                f"обрывы: {analysis.disconnect.title}"
            )
            lines.append(
                "   Типы: "
                + ("; ".join(analysis.type_titles) if analysis.types else "нет")
            )
            if analysis.causes:
                lines.append("   Причины:")
                lines.extend(f"      • {c}" for c in analysis.causes)
            if analysis.evidence:
                lines.append("   Доказательства:")
                lines.extend(f"      • {truncate(e, 110)}" for e in analysis.evidence[:10])
            if analysis.recommendations:
                lines.append("   Рекомендации:")
                lines.extend(f"      • {r}" for r in analysis.recommendations)
            lines.append("   Пробы:")
            for result in analysis.results:
                status = "OK" if result.success else "FAIL"
                lines.append(
                    f"      - {result.probe:<20} @ {truncate(result.target, 32):<32} "
                    f"{status:<4} {result.severity.value:<8} {fmt_ms(result.duration_ms)}"
                )
                if result.error:
                    lines.append(f"          ошибка: {truncate(result.error, 100)}")
        return lines

    def _section_forecast(self, records: list[RunRecord]) -> list[str]:
        lines = ["3. ПРОГНОЗ", self.SECTION]
        forecast = compute_forecast(records)
        lines.append(f"   {forecast['text']}")
        if forecast.get("details"):
            for detail in forecast["details"]:
                lines.append(f"   {detail}")
        return lines

    def _section_logs(self) -> list[str]:
        lines = ["4. АНОМАЛИИ В ЖУРНАЛАХ", self.SECTION]
        if not self.include_logs:
            lines.append("   (отключено: reports.include_logs=false)")
            return lines
        log_dir = self.config.log_dir
        found = False
        for log_name in ("main.log", "probes.log", "telegram.log"):
            path = log_dir / log_name
            if not path.exists():
                continue
            anomalies = [
                line
                for line in read_last_lines(path, n=self.max_log_lines)
                if any(level in line for level in ("WARNING", "ERROR", "CRITICAL"))
            ]
            lines.append("")
            lines.append(
                f"   --- {log_name} (аномалии, показано {min(len(anomalies), 50)}) ---"
            )
            if anomalies:
                found = True
                lines.extend(f"      {truncate(line, 160)}" for line in anomalies[-50:])
            else:
                lines.append("      аномалий нет")
        if not found and len(lines) == 2:
            lines.append("   Журналы не найдены.")
        return lines

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------
    def _build_json(
        self,
        records: list[RunRecord],
        latest: RunRecord | None,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[str, Any]:
        by_profile: dict[str, Analysis] = {}
        for record in records:
            for analysis in record.analyses:
                by_profile[analysis.profile] = analysis

        max_level = max((r.max_level for r in records), default=BlockLevel.NONE)
        max_score = max((r.max_score for r in records), default=0)
        types: list[str] = []
        type_titles: list[str] = []
        causes: list[str] = []
        recommendations: list[str] = []
        disconnect = DisconnectLevel.NONE
        for analysis in by_profile.values():
            for block_type in analysis.types:
                if block_type.value not in types:
                    types.append(block_type.value)
                    type_titles.append(block_type.title)
            for cause in analysis.causes:
                if cause not in causes:
                    causes.append(cause)
            for recommendation in analysis.recommendations:
                if recommendation not in recommendations:
                    recommendations.append(recommendation)
            disconnect = max(disconnect, analysis.disconnect)

        target_record = latest or (records[-1] if records else None)
        return {
            "generated_at": utc_now_iso(),
            "source": "tspu-monitor",
            "period": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
            },
            "runs": len(records),
            "summary": {
                "level": max_level.name.lower(),
                "level_title": max_level.title,
                "score": max_score,
                "types": types,
                "type_titles": type_titles,
                "disconnect": disconnect.name.lower(),
                "disconnect_title": disconnect.title,
                "causes": causes,
                "recommendations": recommendations,
            },
            "forecast": compute_forecast(records),
            "profiles": (
                [a.to_dict() for a in target_record.analyses] if target_record else []
            ),
        }


def compute_forecast(records: list[RunRecord]) -> dict[str, Any]:
    """Простая трендовая эвристика по среднему score."""
    if len(records) < 2:
        return {
            "trend": "insufficient",
            "text": "Недостаточно данных для прогноза (нужно ≥ 2 запусков).",
            "details": [],
        }
    ordered = sorted(records, key=lambda r: r.finished)
    mid = len(ordered) // 2
    first, second = ordered[:mid], ordered[mid:]
    avg_first = statistics.mean(r.max_score for r in first)
    avg_second = statistics.mean(r.max_score for r in second)
    details = [
        f"Средний балл блокировок: {avg_first:.1f} → {avg_second:.1f}",
    ]
    if avg_first == 0 and avg_second == 0:
        return {
            "trend": "none",
            "text": "Сильных блокировок не ожидается.",
            "details": details,
        }
    if avg_second > avg_first * 1.3 + 1:
        return {
            "trend": "worsening",
            "text": "Тренд негативный: интенсивность блокировок растёт.",
            "details": details,
        }
    if avg_second < avg_first * 0.7:
        return {
            "trend": "improving",
            "text": "Тренд положительный: интенсивность блокировок снижается.",
            "details": details,
        }
    return {
        "trend": "stable",
        "text": "Уровень блокировок стабилен.",
        "details": details,
    }
