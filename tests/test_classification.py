"""Тесты движка классификации — ядро диагностики."""

from __future__ import annotations

from typing import Any

from tspu_monitor.classification import DiagnosisEngine, Finding, ResultIndex
from tspu_monitor.models import (
    BlockLevel,
    BlockType,
    DisconnectLevel,
    ProbeResult,
    Severity,
)


def res(
    probe: str,
    target: str = "target",
    success: bool = True,
    data: dict[str, Any] | None = None,
    severity: Severity = Severity.INFO,
    error: str | None = None,
    duration_ms: float = 10.0,
) -> ProbeResult:
    return ProbeResult(
        probe=probe,
        target=target,
        success=success,
        severity=severity,
        data=data or {},
        error=error,
        duration_ms=duration_ms,
    )


ENGINE = DiagnosisEngine(
    {"classification": {"level_thresholds": {"medium": 25, "high": 50, "full": 70}}}
)


def diagnose(*results: ProbeResult) -> Any:
    return ENGINE.diagnose("test", "TEST", "target", list(results))


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


def test_dns_spoofing_detected():
    analysis = diagnose(
        res(
            "dns.resolve",
            "ya.ru",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "ya.ru", "spoof_suspected": True, "system_answers": ["10.0.0.1"]},
            error="подмена",
        )
    )
    assert BlockType.DNS_SPOOF in analysis.types
    assert analysis.score == 45
    assert analysis.level == BlockLevel.MEDIUM
    assert any("подмен" in cause.lower() for cause in analysis.causes)


def test_dns_system_failure():
    analysis = diagnose(
        res(
            "dns.resolve",
            "ya.ru",
            success=False,
            severity=Severity.WARNING,
            data={"host": "ya.ru", "system_ok": False, "spoof_suspected": False},
        )
    )
    assert BlockType.DNS_FILTER in analysis.types


def test_doh_filtered_while_dns_works():
    analysis = diagnose(
        res("dns.resolve", "ya.ru", success=True, data={"host": "ya.ru", "system_ok": True}),
        res("dns.doh", "https://1.1.1.1/dns-query#ya.ru", success=False, severity=Severity.WARNING),
    )
    assert BlockType.DNS_FILTER in analysis.types
    assert any("DoH" in evidence for evidence in analysis.evidence)


# ---------------------------------------------------------------------------
# TCP / RST
# ---------------------------------------------------------------------------


def test_fast_rst_injection():
    analysis = diagnose(
        res(
            "tcp.connect",
            "vpn:443",
            success=True,
            severity=Severity.CRITICAL,
            data={
                "host": "vpn",
                "port": 443,
                "attempts": 2,
                "successes": 1,
                "fast_rst_count": 1,
                "refused_count": 1,
                "timeout_count": 0,
            },
        )
    )
    assert BlockType.RST_INJECTION in analysis.types
    assert analysis.score >= 45
    assert analysis.level >= BlockLevel.MEDIUM
    assert any("RST" in evidence for evidence in analysis.evidence)


def test_full_ip_block():
    analysis = diagnose(
        res(
            "icmp.ping",
            "srv",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "srv", "packet_loss_percent": 100},
        ),
        res(
            "tcp.connect",
            "srv:443",
            success=False,
            severity=Severity.WARNING,
            data={
                "host": "srv",
                "port": 443,
                "attempts": 2,
                "successes": 0,
                "refused_count": 2,
                "timeout_count": 0,
                "fast_rst_count": 0,
            },
        ),
    )
    assert BlockType.IP_BLOCK in analysis.types
    assert BlockType.ICMP_BLOCK in analysis.types
    assert BlockType.PORT_BLOCK in analysis.types
    assert analysis.level == BlockLevel.FULL
    assert analysis.disconnect == DisconnectLevel.CONSTANT


def test_periodic_disconnects():
    analysis = diagnose(
        res(
            "tcp.connect",
            "vpn:443",
            success=True,
            data={
                "host": "vpn",
                "port": 443,
                "attempts": 3,
                "successes": 1,
                "fast_rst_count": 0,
                "refused_count": 0,
                "timeout_count": 2,
            },
        )
    )
    assert analysis.disconnect == DisconnectLevel.FREQUENT
    assert analysis.level == BlockLevel.LOW


# ---------------------------------------------------------------------------
# MTU / UDP / QUIC
# ---------------------------------------------------------------------------


def test_mtu_filter_large_udp_dropped():
    analysis = diagnose(
        res(
            "udp.probe",
            "srv:51820",
            success=False,
            severity=Severity.WARNING,
            data={
                "host": "srv",
                "port": 51820,
                "payload_size": 1200,
                "control": False,
                "expected_silent": False,
                "replies_received": 0,
            },
        ),
        res(
            "udp.probe",
            "srv:51820",
            success=True,
            data={
                "host": "srv",
                "port": 51820,
                "payload_size": 64,
                "control": False,
                "expected_silent": False,
                "replies_received": 1,
            },
        ),
    )
    assert BlockType.MTU_FILTER in analysis.types
    assert analysis.level == BlockLevel.MEDIUM


def test_udp_control_block():
    analysis = diagnose(
        res(
            "udp.probe",
            "1.1.1.1:53",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "1.1.1.1", "port": 53, "control": True, "replies_received": 0},
        )
    )
    assert BlockType.UDP_BLOCK in analysis.types


def test_quic_blocked_while_tcp_works():
    analysis = diagnose(
        res(
            "quic.initial",
            "blocked:443",
            success=False,
            severity=Severity.WARNING,
            data={"host": "blocked", "port": 443, "replies": 0, "control": False},
        ),
        res(
            "quic.initial",
            "google.com:443",
            success=True,
            data={"host": "google.com", "port": 443, "replies": 2, "control": True},
        ),
        res(
            "tcp.connect",
            "blocked:443",
            success=True,
            data={"host": "blocked", "port": 443, "attempts": 1, "successes": 1},
        ),
    )
    assert BlockType.QUIC_BLOCK in analysis.types


# ---------------------------------------------------------------------------
# HTTP / TLS
# ---------------------------------------------------------------------------


def test_http_plug_page():
    analysis = diagnose(
        res(
            "http.get",
            "https://twitter.com/",
            success=False,
            severity=Severity.CRITICAL,
            data={
                "url": "https://twitter.com/",
                "scheme": "https",
                "plug_page_detected": True,
                "body_signature": "body:доступ ограничен",
                "attempts": 1,
                "successes": 0,
            },
            error="обнаружена страница-заглушка",
        )
    )
    assert BlockType.HTTP_PLUG in analysis.types
    assert any("заглушк" in cause.lower() for cause in analysis.causes)


def test_http_throttle():
    analysis = diagnose(
        res(
            "http.get",
            "https://ya.ru/",
            success=True,
            data={
                "url": "https://ya.ru/",
                "scheme": "https",
                "speed_kbps": 20.0,
                "download_bytes": 100000,
                "attempts": 1,
                "successes": 1,
            },
        )
    )
    assert BlockType.THROTTLE in analysis.types


def test_tls_sni_differential():
    analysis = diagnose(
        res(
            "tls.handshake",
            "blocked:443[blocked.example]",
            success=False,
            severity=Severity.CRITICAL,
            error="TLS alert во время handshake",
            data={"role": "real", "sni": "blocked.example", "error_class": "alert"},
        ),
        res(
            "tls.handshake",
            "blocked:443[bogus]",
            success=True,
            data={"role": "bogus", "sni": "bogus"},
        ),
        res(
            "tls.handshake",
            "ya.ru:443[ya.ru]",
            success=True,
            data={"role": "cover", "sni": "ya.ru"},
        ),
    )
    assert BlockType.SNI_FILTER in analysis.types
    assert BlockType.TLS_INTERFERENCE in analysis.types
    assert analysis.score == 100
    assert analysis.level == BlockLevel.FULL


def test_vpn_protocol_detection_openvpn():
    analysis = diagnose(
        res(
            "openvpn.reset",
            "vpn:1194",
            success=False,
            data={"host": "vpn", "port": 1194, "replies": 0, "refused": False},
        ),
        res(
            "udp.probe",
            "1.1.1.1:53",
            success=True,
            data={"host": "1.1.1.1", "port": 53, "control": True, "replies_received": 1},
        ),
        res(
            "tcp.connect",
            "vpn:443",
            success=True,
            data={"host": "vpn", "port": 443, "attempts": 1, "successes": 1},
        ),
    )
    assert BlockType.PROTOCOL_DETECT in analysis.types
    assert any("OpenVPN" in evidence for evidence in analysis.evidence)


def test_shadowsocks_replay_cache():
    analysis = diagnose(
        res(
            "shadowsocks.entropy",
            "ss:8388",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "ss", "port": 8388, "connected": True, "reset": True},
            duration_ms=200.0,
        ),
        res(
            "shadowsocks.entropy",
            "ss:8388",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "ss", "port": 8388, "connected": True, "reset": True},
            duration_ms=50.0,
        ),
    )
    assert BlockType.PROTOCOL_DETECT in analysis.types
    assert BlockType.REPLAY_CACHE in analysis.types


# ---------------------------------------------------------------------------
# Служебное поведение
# ---------------------------------------------------------------------------


def test_skipped_results_are_ignored():
    analysis = diagnose(
        res(
            "raw.ttl",
            "n/a",
            success=False,
            severity=Severity.CRITICAL,
            data={"skipped": True},
            error="нужен root",
        )
    )
    assert analysis.score == 0
    assert analysis.level == BlockLevel.NONE


def test_fallback_unknown_critical():
    analysis = diagnose(
        res("weird.probe", "x", success=False, severity=Severity.CRITICAL, error="boom")
    )
    assert BlockType.UNKNOWN in analysis.types
    assert analysis.level == BlockLevel.LOW


def test_extra_findings_merged():
    extra = [
        Finding(
            type=BlockType.MISCONFIG,
            weight=5,
            evidence="Reality: сертификат не совпадает",
            cause="Ошибка конфигурации",
        )
    ]
    analysis = ENGINE.diagnose("xray", "XRAY", "srv", [], extra_findings=extra)
    assert BlockType.MISCONFIG in analysis.types
    assert analysis.score == 5


def test_result_index_matching():
    index = ResultIndex(
        [
            res("tls.handshake", "a", data={"role": "real"}),
            res("tls.handshake", "b", data={"role": "cover"}),
            res("icmp.ping", "c", data={"host": "c"}),
        ]
    )
    assert index.first("tls.handshake", role="cover") is not None
    assert index.first("tls.handshake", role="bogus") is None
    assert len(index.by_probe("tls.handshake")) == 2
    assert index.any_success("icmp.ping")


def test_no_findings_means_no_block():
    analysis = diagnose(
        res("icmp.ping", "srv", data={"host": "srv", "packet_loss_percent": 0}),
        res(
            "tcp.connect",
            "srv:443",
            data={"host": "srv", "port": 443, "attempts": 1, "successes": 1},
        ),
    )
    assert analysis.level == BlockLevel.NONE
    assert analysis.score == 0
    assert analysis.types == []


# ---------------------------------------------------------------------------
# Новые технические правила: traceroute, raw TTL, сертификаты
# ---------------------------------------------------------------------------


def test_trace_wall_detected():
    analysis = diagnose(
        res(
            "icmp.trace",
            target="blocked.example",
            success=False,
            severity=Severity.WARNING,
            data={
                "host": "blocked.example",
                "max_consecutive_star_hops": 5,
                "reachable": False,
            },
            error="Подозрительное молчание узлов в маршруте (возможен middlebox)",
        )
    )
    assert BlockType.ICMP_BLOCK in analysis.types
    assert analysis.score >= 12


def test_trace_skipped_ignored():
    analysis = diagnose(
        res(
            "icmp.trace",
            success=False,
            data={
                "skipped": True,
                "host": "x",
                "max_consecutive_star_hops": 9,
                "reachable": False,
            },
        )
    )
    assert BlockType.ICMP_BLOCK not in analysis.types
    assert analysis.score == 0


def test_raw_ttl_differential_detects_injection():
    analysis = diagnose(
        res(
            "raw.ttl",
            success=True,
            data={"host": "srv", "reply_kind": "synack", "reply_ttl": 55},
        ),
        res(
            "raw.ttl",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "srv", "reply_kind": "rst", "reply_ttl": 250},
            error="RST с подозрительно низкой задержкой (инъекция?)",
        ),
    )
    assert BlockType.RST_INJECTION in analysis.types
    assert any("TTL" in e for e in analysis.evidence)


def test_raw_ttl_close_values_no_false_positive():
    analysis = diagnose(
        res(
            "raw.ttl",
            success=True,
            data={"host": "srv", "reply_kind": "synack", "reply_ttl": 55},
        ),
        res(
            "raw.ttl",
            success=False,
            severity=Severity.WARNING,
            data={"host": "srv", "reply_kind": "rst", "reply_ttl": 58},
            error="получен RST",
        ),
    )
    assert BlockType.RST_INJECTION not in analysis.types


def test_tls_cert_verify_error():
    analysis = diagnose(
        res(
            "tls.handshake",
            success=True,
            data={
                "host": "srv.example",
                "role": "real",
                "verify": "error",
                "sni": "srv.example",
            },
        )
    )
    assert BlockType.TLS_INTERFERENCE in analysis.types


def test_tls_cert_verify_ok_no_finding():
    analysis = diagnose(
        res(
            "tls.handshake",
            success=True,
            data={"host": "srv", "role": "real", "verify": "OK"},
        )
    )
    assert BlockType.TLS_INTERFERENCE not in analysis.types
    assert analysis.score == 0


def test_dns_fake_ip_downgraded():
    analysis = diagnose(
        res(
            "dns.resolve",
            success=True,
            data={"host": "ya.ru", "fake_ip": True, "system_ok": True},
        )
    )
    assert BlockType.DNS_SPOOF not in analysis.types
    assert analysis.score <= 2


def test_icmp_dead_hosts_aggregated():
    analysis = diagnose(
        res(
            "icmp.ping",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "a.ru", "packet_loss_percent": 100},
        ),
        res(
            "icmp.ping",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "b.ru", "packet_loss_percent": 100},
        ),
        res(
            "icmp.ping",
            success=False,
            severity=Severity.CRITICAL,
            data={"host": "c.ru", "packet_loss_percent": 100},
        ),
    )
    # Одно агрегированное правило вместо трёх отдельных.
    assert BlockType.ICMP_BLOCK in analysis.types
    assert analysis.score <= 8
