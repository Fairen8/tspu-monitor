"""Уведомления: webhook о критических блокировках.

Формат payload — JSON, пригодный для интеграции с n8n, Make, Mattermost,
Zabbix и т.п. Авторизация — заголовок ``Authorization: Bearer <token>``
или произвольные заголовки из ``settings.webhook.headers``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import __version__
from .config import AppConfig
from .logging_setup import get_logger
from .models import BlockLevel, RunRecord

_LEVEL_BY_NAME = {
    "low": BlockLevel.LOW,
    "medium": BlockLevel.MEDIUM,
    "high": BlockLevel.HIGH,
    "full": BlockLevel.FULL,
}


class WebhookNotifier:
    """POST-уведомления о блокировках."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        cfg = config.settings.get("webhook", {}) or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.url: str = str(cfg.get("url", "") or "")
        self.min_level: BlockLevel = _LEVEL_BY_NAME.get(
            str(cfg.get("min_level", "medium")).lower(), BlockLevel.MEDIUM
        )
        self.timeout: float = float(cfg.get("timeout_seconds", 10))
        self.headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"tspu-monitor/{__version__}",
        }
        self.headers.update(
            {str(k): str(v) for k, v in (cfg.get("headers", {}) or {}).items()}
        )
        token = config.secret("webhook.token")
        if token:
            self.headers.setdefault("Authorization", f"Bearer {token}")
        self.logger = get_logger("tspu.webhook")

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.url)

    def should_notify(self, record: RunRecord) -> bool:
        return (
            self.active
            and record.max_level != BlockLevel.NONE
            and record.max_level >= self.min_level
        )

    def build_payload(self, record: RunRecord) -> dict[str, Any]:
        profiles = []
        for analysis in record.analyses:
            profiles.append(
                {
                    "profile": analysis.profile,
                    "title": analysis.title,
                    "target": analysis.target,
                    "level": analysis.level.name.lower(),
                    "score": analysis.score,
                    "types": [t.value for t in analysis.types],
                    "disconnect": analysis.disconnect.name.lower(),
                    "causes": analysis.causes,
                }
            )
        recommendations: list[str] = []
        for analysis in record.analyses:
            for recommendation in analysis.recommendations:
                if recommendation not in recommendations:
                    recommendations.append(recommendation)
        return {
            "event": "tspu.blocking",
            "source": "tspu-monitor",
            "version": __version__,
            "run_id": record.run_id,
            "finished": record.finished,
            "level": record.max_level.name.lower(),
            "level_title": record.max_level.title,
            "score": record.max_score,
            "profiles": profiles,
            "recommendations": recommendations,
        }

    async def notify(self, record: RunRecord) -> bool:
        if not self.should_notify(record):
            return False
        import aiohttp

        payload = self.build_payload(record)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(1, 4):
                try:
                    async with session.post(
                        self.url, json=payload, headers=self.headers
                    ) as response:
                        if 200 <= response.status < 300:
                            self.logger.info(
                                "Webhook отправлен (%s), статус %s",
                                self.url,
                                response.status,
                            )
                            return True
                        self.logger.warning(
                            "Webhook %s вернул HTTP %s (попытка %d/3)",
                            self.url,
                            response.status,
                            attempt,
                        )
                except TimeoutError:
                    self.logger.warning("Webhook timeout (попытка %d/3)", attempt)
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(
                        "Ошибка webhook (попытка %d/3): %s", attempt, exc
                    )
                if attempt < 3:
                    await asyncio.sleep(attempt)
        self.logger.error("Webhook %s: все попытки исчерпаны", self.url)
        return False
