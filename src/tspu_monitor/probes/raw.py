"""Raw-пробы через scapy: TTL/IP-ID ответного пакета.

Требуют root/CAP_NET_RAW и установленного scapy. Если условий нет,
проба аккуратно деградирует в результат ``skipped`` и не влияет на диагноз.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any

from ..models import ProbeResult, Severity
from .base import BaseProbe


def _load_scapy() -> Any:
    try:
        from scapy.all import IP, TCP, sr1  # type: ignore

        return {"IP": IP, "TCP": TCP, "sr1": sr1}
    except Exception as exc:  # noqa: BLE001
        return {"error": exc}


class RawTtlProbe(BaseProbe):
    """SYN-проба с анализом TTL/IP-ID и признаков инъекции RST."""

    name = "raw.ttl"
    title = "Raw SYN (TTL/IP-ID)"
    requires_root = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.hosts: list[str] = list(self.config.get("hosts", []))
        self.ports: list[int] = [int(p) for p in self.config.get("ports", [443])]
        self.ttl: int = int(self.config.get("ttl", 64))

    async def run(self) -> list[ProbeResult]:
        scapy = _load_scapy()
        if "error" in scapy:
            return [self.skipped_result(f"scapy недоступен: {scapy['error']}")]
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return [self.skipped_result("raw-пробы требуют root/CAP_NET_RAW")]

        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, self._probe, scapy, host, port)
            for host in self.hosts
            for port in self.ports
        ]
        return list(await asyncio.gather(*tasks))

    def _probe(self, scapy: dict[str, Any], host: str, port: int) -> ProbeResult:
        IP, TCP, sr1 = scapy["IP"], scapy["TCP"], scapy["sr1"]
        start = self.time_ms()
        target = f"{host}:{port}"
        packet = IP(dst=host, ttl=self.ttl) / TCP(
            dport=port, flags="S", sport=30000 + random.randint(0, 20000)
        )
        try:
            reply = sr1(packet, timeout=self.timeout, verbose=False)
        except (OSError, PermissionError) as exc:
            return self.make_result(
                success=False,
                target=target,
                data={"host": host, "port": port},
                error=f"{type(exc).__name__}: {exc}",
                severity=Severity.WARNING,
                duration_ms=self.time_ms() - start,
            )
        duration = self.time_ms() - start

        data: dict[str, Any] = {"host": host, "port": port}
        if reply is None:
            data["reply_kind"] = None
            return self.make_result(
                success=False,
                target=target,
                data=data,
                error="нет ответа на SYN",
                severity=Severity.WARNING,
                duration_ms=duration,
            )

        ip_layer = reply.getlayer(IP)
        tcp_layer = reply.getlayer(TCP)
        data["reply_src"] = str(ip_layer.src) if ip_layer is not None else None
        data["reply_ttl"] = int(ip_layer.ttl) if ip_layer is not None else None
        data["reply_ipid"] = int(ip_layer.id) if ip_layer is not None else None

        if tcp_layer is not None:
            flags = int(tcp_layer.flags)
            data["tcp_flags"] = flags
            if flags == 0x12:
                data["reply_kind"] = "synack"
                severity = Severity.INFO
                error = None
                success = True
            elif flags & 0x04:
                data["reply_kind"] = "rst"
                success = False
                if duration < 5.0:
                    severity = Severity.CRITICAL
                    error = "RST с подозрительно низкой задержкой (инъекция?)"
                else:
                    severity = Severity.WARNING
                    error = "получен RST"
            else:
                data["reply_kind"] = "other"
                severity = Severity.WARNING
                error = f"неожиданные TCP-флаги: {tcp_layer.flags}"
                success = False
        else:
            data["reply_kind"] = "icmp"
            severity = Severity.WARNING
            error = "ответ без TCP-уровня (ICMP unreachable?)"
            success = False

        return self.make_result(
            success=success,
            target=target,
            data=data,
            raw=reply.summary() if hasattr(reply, "summary") else repr(reply),
            error=error,
            severity=severity,
            duration_ms=duration,
        )
