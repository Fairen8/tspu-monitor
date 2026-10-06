"""Загрузка и хранение конфигурации TSPU Monitor.

Конфигурация состоит из двух YAML-файлов:

* ``settings.yaml`` — общие настройки (не секретные);
* ``secrets.yaml`` — токены, ID пользователей, адреса целевых серверов.

Оба файла при загрузке сливаются со значениями по умолчанию, поэтому
отсутствие ключа никогда не приводит к падению.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Значения по умолчанию
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "general": {
        "language": "ru",
        "log_level": "INFO",
        "color": "auto",
        "data_dir": "/var/lib/tspu-monitor/data",
        "reports_dir": "/var/lib/tspu-monitor/reports",
        "log_dir": "/var/log/tspu-monitor",
    },
    "scheduler": {
        "check_interval_minutes": 60,
        "startup_delay_seconds": 15,
        "report_interval_hours": 168,
        "report_day": "monday",
        "report_time": "09:00",
    },
    "scenarios": {
        "enabled": [
            "web",
            "dns",
            "quic",
            "wireguard",
            "amnezia",
            "openvpn",
            "shadowsocks",
            "xray",
        ],
        "options": {},
    },
    "testing": {
        "control_hosts": ["ya.ru", "google.com"],
        "blocked_test_hosts": ["twitter.com", "facebook.com"],
        "dns_resolvers": ["1.1.1.1", "8.8.8.8", "77.88.8.8"],
        "ports_tcp": [80, 443, 8080, 8443, 22],
        "ports_udp": [53, 443, 51820, 1194],
        "timeout_seconds": 10,
        "ping_count": 5,
        "max_ttl": 30,
        "samples": 2,
    },
    "classification": {
        "level_thresholds": {"medium": 25, "high": 50, "full": 70},
        "throttle_min_kbps": 256,
    },
    "webhook": {
        "enabled": False,
        "url": "",
        "min_level": "medium",
        "timeout_seconds": 10,
        "headers": {},
    },
    "reports": {
        "include_logs": True,
        "max_log_lines": 500,
    },
}

DEFAULT_SECRETS: dict[str, Any] = {
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "allowed_users": [],
        "report_chat_id": None,
        "proxy": None,
        "api_base": "https://api.telegram.org",
    },
    "webhook": {"token": None},
    "targets": {
        "wireguard_server": "",
        "wireguard_port": 51820,
        "amnezia_server": "",
        "amnezia_port": 51820,
        "openvpn_server": "",
        "openvpn_port": 1194,
        "shadowsocks_server": "",
        "shadowsocks_port": 8388,
        "xray_server": "",
        "xray_port": 443,
        "xray_reality_sni": "www.microsoft.com",
    },
}

CONFIG_ALLOWED_KEYS: dict[str, Any] = {
    "general.log_level": str,
    "general.color": str,
    "scheduler.check_interval_minutes": int,
    "scheduler.report_interval_hours": int,
    "scheduler.report_day": str,
    "scheduler.report_time": str,
    "classification.throttle_min_kbps": int,
    "webhook.enabled": lambda v: str(v).lower() in ("1", "true", "yes", "on"),
    "webhook.url": str,
    "webhook.min_level": str,
    "reports.include_logs": lambda v: str(v).lower() in ("1", "true", "yes", "on"),
    "reports.max_log_lines": int,
}


# ---------------------------------------------------------------------------
# YAML-хелперы
# ---------------------------------------------------------------------------


def load_yaml(path: os.PathLike | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def save_yaml(path: os.PathLike | str, data: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно слить ``override`` поверх ``base`` (без мутации base)."""
    result: dict[str, Any] = dict(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_dotted(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current: Any = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
        if not isinstance(current, dict):  # защита от конфликтов типов
            raise ValueError(f"Ключ '{dotted}' конфликтует с существующим значением")
    current[parts[-1]] = value


# ---------------------------------------------------------------------------
# Поиск каталога конфигурации
# ---------------------------------------------------------------------------


def find_config_dir(explicit: str | None = None) -> Path:
    """Найти каталог с ``settings.yaml``.

    Порядок поиска: явный аргумент → ``$TSPU_CONFIG_DIR`` → ``./config`` →
    каталог ``config`` рядом с репозиторием → ``/etc/tspu-monitor`` →
    ``~/.config/tspu-monitor``.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("TSPU_CONFIG_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path.cwd() / "config")
    package_root = Path(__file__).resolve().parents[2]
    candidates.append(package_root / "config")
    candidates.append(Path("/etc/tspu-monitor"))
    candidates.append(Path.home() / ".config" / "tspu-monitor")

    for candidate in candidates:
        if (candidate / "settings.yaml").exists():
            return candidate
    if explicit:
        return Path(explicit).expanduser()
    if env:
        return Path(env).expanduser()
    return candidates[1]


def _resolve_path(value: str, fallback: Path) -> Path:
    if not value:
        return fallback
    return Path(os.path.expandvars(os.path.expanduser(value)))


# ---------------------------------------------------------------------------
# Объект конфигурации
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AppConfig:
    """Загруженная конфигурация с удобными аксессорами."""

    settings: dict[str, Any]
    secrets: dict[str, Any]
    config_dir: Path
    settings_path: Path
    secrets_path: Path

    # -- пути -------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        fallback = Path.home() / ".tspu-monitor" / "data"
        return _resolve_path(get_dotted(self.settings, "general.data_dir", ""), fallback)

    @property
    def reports_dir(self) -> Path:
        fallback = Path.home() / ".tspu-monitor" / "reports"
        return _resolve_path(
            get_dotted(self.settings, "general.reports_dir", ""), fallback
        )

    @property
    def log_dir(self) -> Path:
        fallback = Path.home() / ".tspu-monitor" / "logs"
        return _resolve_path(get_dotted(self.settings, "general.log_dir", ""), fallback)

    # -- доступ -----------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        return get_dotted(self.settings, dotted, default)

    def secret(self, dotted: str, default: Any = None) -> Any:
        return get_dotted(self.secrets, dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        set_dotted(self.settings, dotted, value)

    def scenario_option(self, name: str) -> dict[str, Any]:
        options = get_dotted(self.settings, "scenarios.options", {}) or {}
        value = options.get(name, {})
        return dict(value) if isinstance(value, dict) else {}

    # -- сохранение -------------------------------------------------------
    def save_settings(self) -> None:
        save_yaml(self.settings_path, self.settings)

    def save_secrets(self) -> None:
        save_yaml(self.secrets_path, self.secrets)
        try:
            os.chmod(self.secrets_path, 0o600)
        except OSError:
            pass


def load_config(config_dir: str | None = None) -> AppConfig:
    """Загрузить конфигурацию (со значениями по умолчанию)."""
    directory = find_config_dir(config_dir)
    settings_path = directory / "settings.yaml"
    secrets_path = directory / "secrets.yaml"

    settings = deep_merge(DEFAULT_SETTINGS, load_yaml(settings_path))
    secrets = deep_merge(DEFAULT_SECRETS, load_yaml(secrets_path))

    return AppConfig(
        settings=settings,
        secrets=secrets,
        config_dir=directory,
        settings_path=settings_path,
        secrets_path=secrets_path,
    )


def validate_config(config: AppConfig) -> list[str]:
    """Проверить конфигурацию. Возвращает список предупреждений/ошибок."""
    problems: list[str] = []

    log_level = str(config.get("general.log_level", "INFO")).upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        problems.append(f"general.log_level: неизвестный уровень '{log_level}'")

    day = str(config.get("scheduler.report_day", "monday")).lower()
    if day not in (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ):
        problems.append(f"scheduler.report_day: неизвестный день '{day}'")

    report_time = str(config.get("scheduler.report_time", "09:00"))
    parts = report_time.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        problems.append(f"scheduler.report_time: ожидается HH:MM, получено '{report_time}'")
    else:
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            problems.append(f"scheduler.report_time: некорректное время '{report_time}'")

    interval = config.get("scheduler.check_interval_minutes", 60)
    if not isinstance(interval, int) or interval < 1:
        problems.append("scheduler.check_interval_minutes: ожидается целое >= 1")

    min_level = str(config.get("webhook.min_level", "medium")).lower()
    if min_level not in ("low", "medium", "high", "full"):
        problems.append(f"webhook.min_level: неизвестный уровень '{min_level}'")

    webhook_url = str(config.get("webhook.url", ""))
    if config.get("webhook.enabled") and not webhook_url:
        problems.append("webhook.enabled=true, но webhook.url не задан")

    if not config.get("scenarios.enabled"):
        problems.append("scenarios.enabled пуст — проверки не будут выполняться")

    targets = config.secrets.get("targets", {}) or {}
    for key in (
        "wireguard_server",
        "amnezia_server",
        "openvpn_server",
        "shadowsocks_server",
        "xray_server",
    ):
        if not targets.get(key):
            problems.append(f"secrets.targets.{key} не задан")
    return problems


def coerce_config_value(key: str, raw: str) -> Any:
    """Привести строковое значение из CLI к типу ключа whitelist."""
    if key not in CONFIG_ALLOWED_KEYS:
        raise KeyError(key)
    coercer = CONFIG_ALLOWED_KEYS[key]
    if isinstance(coercer, type):
        if coercer is bool:
            return str(raw).lower() in ("1", "true", "yes", "on")
        return coercer(raw)
    return coercer(raw)
