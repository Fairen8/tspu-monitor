"""Модели данных TSPU Monitor.

Единый словарь типов, которыми обмениваются пробы, движок классификации,
генератор отчётов и CLI. Все объекты сериализуются в JSON без потерь.
"""

from __future__ import annotations

import dataclasses
import enum
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Утилиты времени
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    """Текущее время в UTC (aware)."""
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Текущее время в ISO-8601 UTC с суффиксом ``Z``."""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Степень серьёзности отдельной пробы
# ---------------------------------------------------------------------------


class Severity(str, enum.Enum):
    """Серьёзность результата одной пробы."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def title(self) -> str:
        return {"info": "ИНФО", "warning": "ВНИМАНИЕ", "critical": "КРИТИЧНО"}[self.value]

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


# ---------------------------------------------------------------------------
# Уровень блокировки
# ---------------------------------------------------------------------------


class BlockLevel(enum.IntEnum):
    """Итоговый уровень блокировки (0 — нет, 4 — полная)."""

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    FULL = 4

    @property
    def title(self) -> str:
        return BLOCK_LEVEL_TITLES[self]

    @property
    def icon(self) -> str:
        return {
            "none": "[OK]",
            "low": "[~]",
            "medium": "[!]",
            "high": "[!!]",
            "full": "[BLOCKED]",
        }[self.name.lower()]


BLOCK_LEVEL_TITLES: dict[BlockLevel, str] = {
    BlockLevel.NONE: "нет",
    BlockLevel.LOW: "низкий",
    BlockLevel.MEDIUM: "средний",
    BlockLevel.HIGH: "высокий",
    BlockLevel.FULL: "полный",
}

DEFAULT_LEVEL_THRESHOLDS: dict[str, int] = {"medium": 25, "high": 50, "full": 70}


def level_from_score(
    score: int, thresholds: dict[str, Any] | None = None
) -> BlockLevel:
    """Перевести баллы (0..100) в :class:`BlockLevel`.

    ``thresholds`` может переопределять границы ``medium``/``high``/``full``.
    """
    t = dict(DEFAULT_LEVEL_THRESHOLDS)
    if thresholds:
        for key, value in thresholds.items():
            if key in t:
                try:
                    t[key] = int(value)
                except (TypeError, ValueError):
                    continue
    if score >= t["full"]:
        return BlockLevel.FULL
    if score >= t["high"]:
        return BlockLevel.HIGH
    if score >= t["medium"]:
        return BlockLevel.MEDIUM
    if score > 0:
        return BlockLevel.LOW
    return BlockLevel.NONE


# ---------------------------------------------------------------------------
# Тип блокировки
# ---------------------------------------------------------------------------


class BlockType(str, enum.Enum):
    """Классифицированный тип блокировки/аномалии."""

    DNS_SPOOF = "dns_spoof"
    DNS_FILTER = "dns_filter"
    SNI_FILTER = "sni_filter"
    RST_INJECTION = "rst_injection"
    TLS_INTERFERENCE = "tls_interference"
    IP_BLOCK = "ip_block"
    PORT_BLOCK = "port_block"
    UDP_BLOCK = "udp_block"
    PROTOCOL_DETECT = "protocol_detect"
    THROTTLE = "throttle"
    MTU_FILTER = "mtu_filter"
    HTTP_PLUG = "http_plug"
    QUIC_BLOCK = "quic_block"
    ICMP_BLOCK = "icmp_block"
    REPLAY_CACHE = "replay_cache"
    COVER_BLOCK = "cover_block"
    MISCONFIG = "misconfig"
    UNKNOWN = "unknown"

    @property
    def title(self) -> str:
        return BLOCK_TYPE_TITLES[self]


BLOCK_TYPE_TITLES: dict[BlockType, str] = {
    BlockType.DNS_SPOOF: "Подмена DNS",
    BlockType.DNS_FILTER: "Фильтрация DNS/DoH",
    BlockType.SNI_FILTER: "Фильтрация по SNI",
    BlockType.RST_INJECTION: "Инъекция RST",
    BlockType.TLS_INTERFERENCE: "Вмешательство в TLS",
    BlockType.IP_BLOCK: "Блокировка IP",
    BlockType.PORT_BLOCK: "Блокировка порта",
    BlockType.UDP_BLOCK: "Блокировка UDP",
    BlockType.PROTOCOL_DETECT: "Детект VPN-протокола",
    BlockType.THROTTLE: "Шейпинг / ограничение скорости",
    BlockType.MTU_FILTER: "Фильтрация по размеру пакета (MTU)",
    BlockType.HTTP_PLUG: "Страница-заглушка",
    BlockType.QUIC_BLOCK: "Блокировка QUIC (UDP/443)",
    BlockType.ICMP_BLOCK: "Блокировка ICMP",
    BlockType.REPLAY_CACHE: "Кэш повторов (replay-cache)",
    BlockType.COVER_BLOCK: "Блокировка cover-ресурса",
    BlockType.MISCONFIG: "Ошибка конфигурации (не блокировка)",
    BlockType.UNKNOWN: "Неизвестная аномалия",
}


# ---------------------------------------------------------------------------
# Уровень обрывов
# ---------------------------------------------------------------------------


class DisconnectLevel(enum.IntEnum):
    """Насколько часто рвутся соединения (0 — никогда, 4 — постоянно)."""

    NONE = 0
    RARE = 1
    PERIODIC = 2
    FREQUENT = 3
    CONSTANT = 4

    @property
    def title(self) -> str:
        return DISCONNECT_LEVEL_TITLES[self]


DISCONNECT_LEVEL_TITLES: dict[DisconnectLevel, str] = {
    DisconnectLevel.NONE: "обрывов не обнаружено",
    DisconnectLevel.RARE: "редкие обрывы",
    DisconnectLevel.PERIODIC: "периодические обрывы",
    DisconnectLevel.FREQUENT: "частые обрывы",
    DisconnectLevel.CONSTANT: "постоянные обрывы",
}


def disconnect_from_ratio(fail_ratio: float, attempts: int = 1) -> DisconnectLevel:
    """Оценить уровень обрывов по доле неудачных попыток.

    Одна попытка не позволяет судить о стабильности — возвращается ``NONE``.
    """
    if attempts < 2 or fail_ratio <= 0:
        return DisconnectLevel.NONE
    if fail_ratio >= 0.75:
        return DisconnectLevel.CONSTANT
    if fail_ratio >= 0.5:
        return DisconnectLevel.FREQUENT
    if fail_ratio >= 0.25:
        return DisconnectLevel.PERIODIC
    return DisconnectLevel.RARE


# ---------------------------------------------------------------------------
# Результат пробы
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProbeResult:
    """Структурированный результат одной пробы.

    ``data`` — свободный словарь; имена ключей для встроенных проб описаны
    в документации (DOCS.md, раздел «Пробы»). Условные обозначения:

    * ``data["skipped"] = True`` — проба не выполнялась (нет прав/бинарника),
      движок классификации её игнорирует;
    * ``data["control"] = True`` — контрольная проба (заведомо рабочий узел);
    * ``data["expected_silent"] = True`` — молчание является нормой.
    """

    probe: str
    target: str
    success: bool
    severity: Severity = Severity.INFO
    duration_ms: float = 0.0
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    raw: str = ""
    error: str | None = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = utc_now_iso()

    @property
    def skipped(self) -> bool:
        return bool(self.data.get("skipped"))

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProbeResult:
        payload = dict(data)
        severity = payload.get("severity", "info")
        payload["severity"] = Severity(severity) if isinstance(severity, str) else severity
        known = {f.name for f in dataclasses.fields(cls)}
        payload = {k: v for k, v in payload.items() if k in known}
        return cls(**payload)


# ---------------------------------------------------------------------------
# Итоговая оценка сценария
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Analysis:
    """Результат анализа одного сценария (ансамбль проб + диагноз)."""

    profile: str
    title: str
    target: str
    score: int = 0
    level: BlockLevel = BlockLevel.NONE
    types: list[BlockType] = dataclasses.field(default_factory=list)
    disconnect: DisconnectLevel = DisconnectLevel.NONE
    causes: list[str] = dataclasses.field(default_factory=list)
    evidence: list[str] = dataclasses.field(default_factory=list)
    recommendations: list[str] = dataclasses.field(default_factory=list)
    results: list[ProbeResult] = dataclasses.field(default_factory=list)
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = utc_now_iso()

    @property
    def status_line(self) -> str:
        """Короткая человекочитаемая строка статуса."""
        return f"{self.title} — {self.level.title} уровень блокировки ({self.score}/100)"

    @property
    def type_titles(self) -> list[str]:
        return [t.title for t in self.types]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "title": self.title,
            "target": self.target,
            "score": self.score,
            "level": self.level.name.lower(),
            "level_title": self.level.title,
            "types": [t.value for t in self.types],
            "type_titles": self.type_titles,
            "disconnect": self.disconnect.name.lower(),
            "disconnect_title": self.disconnect.title,
            "causes": list(self.causes),
            "evidence": list(self.evidence),
            "recommendations": list(self.recommendations),
            "generated_at": self.generated_at,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Analysis:
        payload = dict(data)
        payload["level"] = BlockLevel[str(payload.get("level", "none")).upper()]
        payload["types"] = [BlockType(t) for t in payload.get("types", [])]
        payload["disconnect"] = DisconnectLevel[
            str(payload.get("disconnect", "none")).upper()
        ]
        payload["results"] = [
            ProbeResult.from_dict(r) for r in payload.get("results", []) or []
        ]
        known = {f.name for f in dataclasses.fields(cls)}
        payload = {k: v for k, v in payload.items() if k in known}
        return cls(**payload)


# ---------------------------------------------------------------------------
# Запись о запуске (сохраняется в data/run-*.json)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RunRecord:
    """Совокупный результат одного запуска ``tspu-monitor check``."""

    run_id: str
    started: str
    finished: str
    duration_seconds: float
    analyses: list[Analysis] = dataclasses.field(default_factory=list)
    command: str = "check"

    @property
    def max_level(self) -> BlockLevel:
        if not self.analyses:
            return BlockLevel.NONE
        return max(a.level for a in self.analyses)

    @property
    def max_score(self) -> int:
        if not self.analyses:
            return 0
        return max(a.score for a in self.analyses)

    @property
    def disconnected(self) -> list[Analysis]:
        return [a for a in self.analyses if a.disconnect != DisconnectLevel.NONE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started": self.started,
            "finished": self.finished,
            "duration_seconds": self.duration_seconds,
            "command": self.command,
            "max_level": self.max_level.name.lower(),
            "max_score": self.max_score,
            "analyses": [a.to_dict() for a in self.analyses],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        payload = dict(data)
        payload["analyses"] = [
            Analysis.from_dict(a) for a in payload.get("analyses", []) or []
        ]
        known = {f.name for f in dataclasses.fields(cls)}
        payload = {k: v for k, v in payload.items() if k in known}
        return cls(**payload)


#: Коды возврата CLI.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNSTABLE = 2
EXIT_BLOCKED = 3


def exit_code_for_level(level: BlockLevel) -> int:
    """Преобразовать уровень блокировки в код возврата CLI."""
    if level >= BlockLevel.HIGH:
        return EXIT_BLOCKED
    if level >= BlockLevel.MEDIUM:
        return EXIT_UNSTABLE
    return EXIT_OK
