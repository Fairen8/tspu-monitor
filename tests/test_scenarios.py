"""Тесты сценариев и менеджера сценариев."""

from __future__ import annotations

import types

from tspu_monitor.classification import Finding
from tspu_monitor.config import AppConfig, load_config
from tspu_monitor.models import BlockLevel, BlockType, ProbeResult, Severity
from tspu_monitor.probes.base import BaseProbe
from tspu_monitor.scenarios.base import BaseScenario
from tspu_monitor.scenarios.manager import ScenarioManager

BUILTIN = {
    "web",
    "dns",
    "quic",
    "wireguard",
    "amnezia",
    "openvpn",
    "shadowsocks",
    "xray",
}


def make_manager(config: AppConfig) -> ScenarioManager:
    return ScenarioManager(
        config.settings, config.secrets, str(config.settings_path)
    )


def test_discovery_finds_builtin_scenarios(config: AppConfig):
    manager = make_manager(config)
    assert BUILTIN.issubset(set(manager.known_names()))


def test_list_all_metadata(config: AppConfig):
    manager = make_manager(config)
    items = manager.list_all()
    assert items
    for item in items:
        assert {"name", "title", "description", "version", "enabled", "custom"} <= set(item)


def test_enable_disable_persists(config: AppConfig):
    manager = make_manager(config)
    assert manager.disable("xray") is True
    assert manager.disable("xray") is False
    assert manager.enable("xray") is True
    assert manager.enable("unknown-scenario") is False

    reloaded = load_config(str(config.config_dir))
    assert "xray" in reloaded.get("scenarios.enabled")


def test_get_enabled_instances(config_factory):
    config = config_factory(settings={"scenarios": {"enabled": ["web", "dns"]}})
    manager = make_manager(config)
    scenarios = manager.get_enabled()
    assert {s.name for s in scenarios} == {"web", "dns"}
    assert all(isinstance(s, BaseScenario) for s in scenarios)


def test_every_scenario_has_default_config(config: AppConfig):
    manager = make_manager(config)
    for name in manager.known_names():
        scenario = manager.get_by_name(name)
        assert scenario is not None
        assert isinstance(scenario.get_default_config(), dict)
        assert scenario.title


async def test_unconfigured_scenario_does_not_probe(config: AppConfig):
    manager = make_manager(config)
    scenario = manager.get_by_name("wireguard")
    assert scenario is not None
    analysis = await scenario.run()
    assert analysis.level == BlockLevel.NONE
    assert any("не задан" in cause for cause in analysis.causes)


async def test_scenario_full_pipeline_with_stub_probe(config: AppConfig):
    class StubProbe(BaseProbe):
        name = "stub.probe"

        async def run(self):
            return [
                self.make_result(
                    success=False,
                    target="stub",
                    severity=Severity.CRITICAL,
                    error="тестовая аномалия",
                )
            ]

    manager = make_manager(config)
    scenario = manager.get_by_name("web")
    assert scenario is not None
    scenario.get_probes = lambda: [StubProbe({})]  # type: ignore[method-assign]
    analysis = await scenario.run()
    assert analysis.score > 0
    assert analysis.level != BlockLevel.NONE
    assert analysis.results
    assert analysis.results[0].probe == "stub.probe"


def test_openvpn_extra_findings_missing_tls_cover(config: AppConfig):
    manager = make_manager(config)
    scenario = manager.get_by_name("openvpn")
    assert scenario is not None
    results = [
        ProbeResult(
            probe="tls.handshake",
            target="vpn:443[www.google.com]",
            success=False,
            severity=Severity.CRITICAL,
            data={"role": "cover"},
        ),
        ProbeResult(
            probe="tcp.connect",
            target="vpn:443",
            success=True,
            data={"port": 443},
        ),
    ]
    findings = scenario.extra_findings(results)
    assert findings and findings[0].type == BlockType.MISCONFIG


def test_xray_extra_findings_cert_mismatch(config: AppConfig):
    manager = make_manager(config)
    scenario = manager.get_by_name("xray")
    assert scenario is not None
    results = [
        ProbeResult(
            probe="tls.handshake",
            target="vpn:443",
            success=True,
            data={"role": "real", "subject": "CN=server"},
        ),
        ProbeResult(
            probe="tls.handshake",
            target="www.microsoft.com:443",
            success=True,
            data={"role": "cover", "subject": "CN=microsoft"},
        ),
    ]
    findings = scenario.extra_findings(results)
    assert findings and findings[0].type == BlockType.MISCONFIG


def test_custom_module_registration(config: AppConfig):
    manager = make_manager(config)
    module = types.ModuleType("fake_custom_module")

    class FakeScenario(BaseScenario):
        name = "fake_custom"
        title = "FAKE"

        def get_default_config(self):
            return {}

        def get_probes(self):
            return []

        def target(self):
            return ""

    FakeScenario.__module__ = "fake_custom_module"
    module.FakeScenario = FakeScenario  # type: ignore[attr-defined]
    manager._register_module(module)
    assert "fake_custom" in manager.known_names()
    assert manager.get_by_name("fake_custom").title == "FAKE"


def test_duplicate_scenario_is_ignored(config: AppConfig):
    manager = make_manager(config)
    module = types.ModuleType("duplicate_module")

    class DuplicateWeb(BaseScenario):
        name = "web"
        title = "DUPLICATE"

        def get_default_config(self):
            return {}

        def get_probes(self):
            return []

        def target(self):
            return ""

    DuplicateWeb.__module__ = "duplicate_module"
    module.DuplicateWeb = DuplicateWeb  # type: ignore[attr-defined]
    manager._register_module(module)
    assert manager.get_by_name("web").title == "WEB / HTTPS"


def test_extra_finding_dataclass_contract():
    finding = Finding(
        type=BlockType.RST_INJECTION,
        weight=10,
        evidence="x",
        cause="y",
        recommendation="z",
    )
    assert finding.type == BlockType.RST_INJECTION
