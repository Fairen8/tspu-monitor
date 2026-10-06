"""ICMP-пробы: ping, traceroute, определение Path MTU."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..models import ProbeResult, Severity
from .base import BaseProbe

_PING_LOSS_RE = re.compile(r"(\d+(?:[.,]\d+)?)% packet loss")
_PING_STATS_RE = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
    r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)"
)
_PING_TIME_RE = re.compile(r"time[=<]([\d.]+)\s*ms")


class PingProbe(BaseProbe):
    """ICMP echo: потери, RTT, jitter."""

    name = "icmp.ping"
    title = "ICMP ping"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.hosts: list[str] = list(self.config.get("hosts", []))
        self.count: int = int(self.config.get("count", 5))
        self.payload_size: int = int(self.config.get("payload_size", 0))
        self.df: bool = bool(self.config.get("df", False))

    async def run(self) -> list[ProbeResult]:
        return list(
            await asyncio.gather(*(self._ping_one(host) for host in self.hosts))
        )

    async def _ping_one(self, host: str) -> ProbeResult:
        start = self.time_ms()
        argv = [
            "ping",
            "-n",
            "-c",
            str(self.count),
            "-W",
            str(max(1, int(self.timeout))),
        ]
        if self.df:
            argv += ["-M", "do"]
        if self.payload_size:
            argv += ["-s", str(self.payload_size)]
        argv.append(host)

        rc, stdout, stderr = await self.run_cmd(
            argv, timeout=self.timeout * self.count + 5
        )
        duration = self.time_ms() - start

        loss_match = _PING_LOSS_RE.search(stdout)
        stats_match = _PING_STATS_RE.search(stdout)
        rtts = [float(x) for x in _PING_TIME_RE.findall(stdout)]

        loss: int | None = None
        if loss_match:
            loss = int(round(float(loss_match.group(1).replace(",", "."))))

        success = rc == 0 and loss is not None and loss < 100
        if rc == -2:
            severity = Severity.CRITICAL
            error = stderr or "ping не установлен"
        elif rc < 0:
            severity = Severity.CRITICAL
            error = stderr or "ping завершился с ошибкой"
        elif loss is None:
            severity = Severity.CRITICAL
            error = "не удалось разобрать вывод ping"
        elif loss == 100:
            severity = Severity.CRITICAL
            error = "100% потерь ICMP"
        elif loss > 0:
            severity = Severity.WARNING
            error = None
        else:
            severity = Severity.INFO
            error = None

        data: dict[str, Any] = {
            "host": host,
            "packets_sent": self.count,
            "packet_loss_percent": loss,
            "rtt_min_ms": float(stats_match.group(1))
            if stats_match
            else (min(rtts) if rtts else None),
            "rtt_avg_ms": float(stats_match.group(2))
            if stats_match
            else (sum(rtts) / len(rtts) if rtts else None),
            "rtt_max_ms": float(stats_match.group(3))
            if stats_match
            else (max(rtts) if rtts else None),
            "rtt_mdev_ms": float(stats_match.group(4)) if stats_match else None,
            "rtt_samples_ms": rtts,
            "payload_size": self.payload_size,
        }
        return self.make_result(
            success=success,
            target=host,
            data=data,
            raw=stdout + ("\n" + stderr if stderr else ""),
            error=error,
            severity=severity,
            duration_ms=duration,
        )


class TraceProbe(BaseProbe):
    """Traceroute: поиск «стены» из звёздочек (возможный middlebox)."""

    name = "icmp.trace"
    title = "Трассировка маршрута"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.hosts: list[str] = list(self.config.get("hosts", []))
        self.max_ttl: int = int(self.config.get("max_ttl", 30))

    async def run(self) -> list[ProbeResult]:
        return list(
            await asyncio.gather(*(self._trace_one(host) for host in self.hosts))
        )

    async def _trace_one(self, host: str) -> ProbeResult:
        start = self.time_ms()
        argv = [
            "traceroute",
            "-n",
            "-m",
            str(self.max_ttl),
            "-w",
            str(max(1, int(self.timeout))),
            "-q",
            "1",
            host,
        ]
        rc, stdout, stderr = await self.run_cmd(
            argv, timeout=self.timeout * self.max_ttl + 5
        )
        duration = self.time_ms() - start

        hops: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            line = line.strip()
            parts = line.split()
            if not parts or not parts[0].isdigit():
                continue
            ttl = int(parts[0])
            addr: str | None = None
            rtt: float | None = None
            for token in parts[1:]:
                if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", token):
                    addr = token
                elif addr and re.fullmatch(r"[\d.]+", token):
                    try:
                        rtt = float(token)
                    except ValueError:
                        pass
            hops.append({"ttl": ttl, "addr": addr, "rtt_ms": rtt})

        consecutive_stars = 0
        max_stars = 0
        for hop in hops:
            if hop["addr"] is None:
                consecutive_stars += 1
                max_stars = max(max_stars, consecutive_stars)
            else:
                consecutive_stars = 0

        reachable = bool(hops and hops[-1]["addr"] is not None)
        anomaly = max_stars >= 3 and not reachable

        if rc == -2:
            severity = Severity.WARNING
            error = "traceroute не установлен"
        elif rc < 0:
            severity = Severity.WARNING
            error = stderr or "traceroute завершился с ошибкой"
        elif anomaly:
            severity = Severity.WARNING
            error = "Подозрительное «молчание» узлов в маршруте (возможен middlebox)"
        else:
            severity = Severity.INFO
            error = None

        return self.make_result(
            success=reachable,
            target=host,
            data={
                "host": host,
                "hops": hops,
                "hop_count": len(hops),
                "reachable": reachable,
                "max_consecutive_star_hops": max_stars,
            },
            raw=stdout + ("\n" + stderr if stderr else ""),
            error=error,
            severity=severity,
            duration_ms=duration,
        )


class PathMtuProbe(BaseProbe):
    """Определить Path MTU через ping с флагом DF.

    Перебираем размеры ICMP-payload от большего к меньшему, пока пакет
    не пройдёт. PMTU = payload + 28 (IP+ICMP заголовки).
    """

    name = "icmp.mtu"
    title = "Path MTU"

    DEFAULT_SIZES = (1472, 1400, 1280, 1200, 1000, 500, 200)

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.host: str = str(self.config.get("host", ""))
        sizes = self.config.get("sizes") or list(self.DEFAULT_SIZES)
        self.sizes: list[int] = [int(s) for s in sizes]

    async def run(self) -> list[ProbeResult]:
        if not self.host:
            return [self.skipped_result("icmp.mtu: не задан host")]
        start = self.time_ms()
        working: list[int] = []
        pmtu: int | None = None
        messages: list[str] = []
        for size in self.sizes:
            rc, stdout, stderr = await self.run_cmd(
                [
                    "ping",
                    "-n",
                    "-c",
                    "1",
                    "-W",
                    str(max(1, int(self.timeout))),
                    "-M",
                    "do",
                    "-s",
                    str(size),
                    self.host,
                ],
                timeout=self.timeout + 3,
            )
            combined = (stdout + "\n" + stderr).strip()
            ok = rc == 0 and "100% packet loss" not in combined and "0% packet loss" in combined
            if ok:
                working.append(size)
                if pmtu is None:
                    pmtu = size + 28
                    messages.append(f"{size}B payload: OK (PMTU={pmtu})")
            else:
                messages.append(f"{size}B payload: не прошёл")

        duration = self.time_ms() - start
        error = None
        severity = Severity.INFO
        if pmtu is None:
            error = "Ни один размер пакета не прошёл (хост недоступен или ICMP блокирован)"
            severity = Severity.WARNING
        return self.make_result(
            success=pmtu is not None,
            target=self.host,
            data={
                "host": self.host,
                "pmtu": pmtu,
                "working_sizes": working,
                "tested_sizes": list(self.sizes),
            },
            raw="\n".join(messages),
            error=error,
            severity=severity,
            duration_ms=duration,
        )
