"""Обнаружение и управление сценариями.

Встроенные сценарии лежат в ``tspu_monitor/scenarios/*.py``, пользовательские —
в ``tspu_monitor/scenarios/custom/`` (файлы, начинающиеся с ``_``,
игнорируются). Список включённых сценариев хранится в
``settings.yaml`` → ``scenarios.enabled``.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil
from pathlib import Path
from typing import Any

from ..config import save_yaml
from ..logging_setup import get_logger
from .base import BaseScenario

logger = get_logger("tspu.scenarios")

_EXCLUDED_MODULES = {"base", "manager", "__init__"}

#: Встроенные модули сценариев. Явный список нужен для замороженной
#: сборки (PyInstaller): внутри exe нет .py-файлов на диске, и
#: ``pkgutil.iter_modules`` их не находит.
BUILTIN_MODULES = (
    "amnezia",
    "dns",
    "openvpn",
    "quic",
    "shadowsocks",
    "web",
    "wireguard",
    "xray",
)


class ScenarioManager:
    """Найти, создать и включить/выключить сценарии."""

    def __init__(
        self,
        settings: dict[str, Any],
        secrets: dict[str, Any],
        settings_path: str | None = None,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.settings_path = settings_path
        self._classes: dict[str, type[BaseScenario]] = {}
        self._discover()

    # -- обнаружение ------------------------------------------------------
    def _discover(self) -> None:
        import tspu_monitor.scenarios as scenarios_pkg

        module_names: set[str] = set(BUILTIN_MODULES)
        for _, module_name, is_pkg in pkgutil.iter_modules(scenarios_pkg.__path__):
            if module_name in _EXCLUDED_MODULES or module_name.startswith("_"):
                continue
            if is_pkg:
                continue
            module_names.add(module_name)

        for module_name in sorted(module_names):
            try:
                module = importlib.import_module(
                    f"{scenarios_pkg.__name__}.{module_name}"
                )
            except ImportError as exc:
                logger.warning("Не удалось импортировать сценарий %s: %s", module_name, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Сценарий %s сломан: %s", module_name, exc)
                continue
            self._register_module(module)

        custom_dir = Path(scenarios_pkg.__file__).parent / "custom"
        if custom_dir.is_dir():
            for file in sorted(custom_dir.glob("*.py")):
                if file.name.startswith("_") or file.name == "__init__.py":
                    continue
                module_name = f"tspu_monitor.scenarios.custom.{file.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(module_name, file)
                    if spec is None or spec.loader is None:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Не удалось импортировать %s: %s", file, exc)
                    continue
                self._register_module(module)

        logger.debug("Обнаружены сценарии: %s", ", ".join(sorted(self._classes)))

    def _register_module(self, module: Any) -> None:
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is BaseScenario or not issubclass(cls, BaseScenario):
                continue
            if cls.__module__ != module.__name__:
                continue
            name = getattr(cls, "name", None)
            if not name or name == "base":
                continue
            if name in self._classes:
                logger.warning(
                    "Сценарий '%s' уже зарегистрирован (%s) — пропускаю %s",
                    name,
                    self._classes[name].__module__,
                    cls.__module__,
                )
                continue
            self._classes[name] = cls

    # -- публичный API ----------------------------------------------------
    def list_all(self) -> list[dict[str, Any]]:
        enabled = set(self.get_enabled_names())
        out = []
        for name, cls in sorted(self._classes.items()):
            out.append(
                {
                    "name": name,
                    "title": getattr(cls, "title", name),
                    "description": getattr(cls, "description", ""),
                    "version": getattr(cls, "version", "1.0.0"),
                    "enabled": name in enabled,
                    "custom": cls.__module__.startswith("tspu_monitor.scenarios.custom."),
                }
            )
        return out

    def get_enabled_names(self) -> list[str]:
        return list(self.settings.get("scenarios", {}).get("enabled", []) or [])

    def known_names(self) -> list[str]:
        return sorted(self._classes)

    def is_known(self, name: str) -> bool:
        return name in self._classes

    def get_by_name(self, name: str) -> BaseScenario | None:
        cls = self._classes.get(name)
        if cls is None:
            return None
        return cls(
            config=self.settings.get("scenarios", {}).get("options", {}).get(name, {}),
            secrets=self.secrets,
            settings=self.settings,
        )

    def get_enabled(self) -> list[BaseScenario]:
        scenarios = []
        for name in self.get_enabled_names():
            if not self.is_known(name):
                logger.warning("Сценарий '%s' включён, но не найден", name)
                continue
            scenario = self.get_by_name(name)
            if scenario is not None:
                scenarios.append(scenario)
        return scenarios

    # -- изменение состава -------------------------------------------------
    def enable(self, name: str) -> bool:
        if not self.is_known(name):
            return False
        enabled = self.get_enabled_names()
        if name in enabled:
            return True
        enabled.append(name)
        self.settings.setdefault("scenarios", {})["enabled"] = enabled
        self._persist()
        return True

    def disable(self, name: str) -> bool:
        enabled = self.get_enabled_names()
        if name not in enabled:
            return False
        enabled.remove(name)
        self.settings.setdefault("scenarios", {})["enabled"] = enabled
        self._persist()
        return True

    def _persist(self) -> None:
        if not self.settings_path:
            return
        try:
            save_yaml(self.settings_path, self.settings)
        except OSError as exc:
            logger.error("Не удалось сохранить settings.yaml: %s", exc)
