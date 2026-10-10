"""DNS-пробы: сравнение резолверов и DNS-over-HTTPS."""

from __future__ import annotations

import asyncio
import json
import re
import socket
from typing import Any

from ..models import ProbeResult, Severity
from ..utils import build_dns_query, is_benchmark_ip, is_private_ip, parse_dns_answers
from .base import BaseProbe

_IP_RE = re.compile(r"^[\d.]+$|^[0-9a-fA-F:]+$")


class DnsResolveProbe(BaseProbe):
    """Разрешить имена системным и публичными резолверами и сравнить ответы.

    Признаки аномалии:

    * системный резолвер вернул приватный IP (заглушка/спуфинг);
    * ответ системного резолвера не пересекается с ответами публичных.
    """

    name = "dns.resolve"
    title = "DNS resolve"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.hosts: list[str] = list(self.config.get("hosts", []))
        self.resolvers: list[str] = list(
            self.config.get("resolvers", ["1.1.1.1", "8.8.8.8", "77.88.8.8"])
        )
        self.dig_available: bool | None = None

    async def run(self) -> list[ProbeResult]:
        return list(
            await asyncio.gather(*(self._resolve_one(host) for host in self.hosts))
        )

    async def _dig(self, host: str, server: str) -> tuple[list[str], str]:
        rc, stdout, stderr = await self.run_cmd(
            ["dig", "+short", "+time=3", "+tries=1", host, f"@{server}"],
            timeout=min(self.timeout, 8),
        )
        if rc == -2:
            # dig отсутствует (типично для Windows) — спрашиваем сами по UDP.
            self.dig_available = False
            return await self._udp_query(host, server)
        self.dig_available = True
        if rc < 0:
            return [], stderr or "dig failed"
        ips = [
            line.strip()
            for line in stdout.splitlines()
            if line.strip() and _IP_RE.match(line.strip())
        ]
        return ips, stdout.strip()

    async def _udp_query(self, host: str, server: str) -> tuple[list[str], str]:
        """Прямой DNS-запрос по UDP/53 без dig (fallback для Windows)."""
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.sendto(build_dns_query(host), (server, 53))
            data = await asyncio.wait_for(
                loop.sock_recv(sock, 4096), timeout=min(self.timeout, 5)
            )
        except (TimeoutError, OSError) as exc:
            return [], f"udp-dns: {type(exc).__name__}: {exc}"
        finally:
            sock.close()
        ips = parse_dns_answers(data)
        return ips, f"udp-dns @{server} -> {ips}"

    async def _system_resolve(self, host: str) -> tuple[list[str], str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, None, family=socket.AF_INET),
                timeout=min(self.timeout, 8),
            )
        except (TimeoutError, socket.gaierror, OSError) as exc:
            return [], f"{type(exc).__name__}: {exc}"
        return sorted({info[4][0] for info in infos}), "system resolver"

    async def _resolve_one(self, host: str) -> ProbeResult:
        start = self.time_ms()
        answers: dict[str, list[str]] = {}
        raw_lines: list[str] = []

        system_ips, system_raw = await self._system_resolve(host)
        answers["system"] = system_ips
        raw_lines.append(f"[system] {system_raw} -> {system_ips}")

        for resolver in self.resolvers:
            ips, raw = await self._dig(host, resolver)
            answers[resolver] = ips
            raw_lines.append(f"[{resolver}] {raw or '-'} -> {ips}")

        public_union: set[str] = set()
        for resolver, ips in answers.items():
            if resolver != "system":
                public_union.update(ips)

        system_set = set(system_ips)
        has_private = any(is_private_ip(ip) for ip in system_set)
        fake_ip = bool(system_set) and all(is_benchmark_ip(ip) for ip in system_set)
        intersection = system_set & public_union
        system_ok = bool(system_set)

        spoof_suspected = False
        reason: str | None = None
        if fake_ip:
            reason = (
                "системный DNS отдаёт fake-ip (198.18.0.0/15) — "
                "активен VPN/TUN, реальные адреса скрыты"
            )
        elif system_ok and public_union:
            if has_private:
                spoof_suspected = True
                reason = "системный резолвер вернул приватный IP — возможна подмена"
            elif not intersection:
                spoof_suspected = True
                reason = "ответ системного резолвера отсутствует у публичных резолверов"

        if not system_ok:
            severity = Severity.WARNING
            reason = reason or "системный резолвер не вернул IP"
        elif spoof_suspected:
            severity = Severity.CRITICAL
        else:
            severity = Severity.INFO

        data: dict[str, Any] = {
            "host": host,
            "answers": answers,
            "system_answers": system_ips,
            "public_answers": sorted(public_union),
            "system_ok": system_ok,
            "system_returned_private": has_private,
            "fake_ip": fake_ip,
            "spoof_suspected": spoof_suspected,
            "intersection_with_public": sorted(intersection),
            "dig_available": self.dig_available,
        }
        if fake_ip:
            raw_lines.append("[fake-ip] VPN/TUN перехватывает DNS (198.18.0.0/15)")
        return self.make_result(
            success=system_ok and not spoof_suspected,
            target=host,
            data=data,
            raw="\n".join(raw_lines),
            error=reason,
            severity=severity,
            duration_ms=self.time_ms() - start,
        )


class DohProbe(BaseProbe):
    """Проверка DNS-over-HTTPS (Cloudflare, Google, Quad9)."""

    name = "dns.doh"
    title = "DNS-over-HTTPS"

    DEFAULT_ENDPOINTS = [
        "https://1.1.1.1/dns-query",
        "https://dns.google/resolve",
        "https://dns.quad9.net:5053/dns-query",
    ]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.hosts: list[str] = list(self.config.get("hosts", ["ya.ru"]))
        self.endpoints: list[str] = list(
            self.config.get("endpoints", self.DEFAULT_ENDPOINTS)
        )

    async def run(self) -> list[ProbeResult]:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False, limit=4)
        tasks: list[asyncio.Task] = []
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector
        ) as session:
            for endpoint in self.endpoints:
                for host in self.hosts:
                    tasks.append(asyncio.create_task(self._probe(session, endpoint, host)))
            return list(await asyncio.gather(*tasks))

    async def _probe(self, session: Any, endpoint: str, host: str) -> ProbeResult:
        start = self.time_ms()
        url = f"{endpoint}?name={host}&type=A"
        status: int | None = None
        ips: list[str] = []
        error: str | None = None
        try:
            async with session.get(
                url, headers={"Accept": "application/dns-json"}
            ) as resp:
                status = resp.status
                if resp.status == 200:
                    body = await resp.json(content_type=None)
                    for answer in body.get("Answer", []) or []:
                        if answer.get("type") == 1:
                            ips.append(answer.get("data"))
                    if not ips:
                        error = "нет A-записей в ответе"
                else:
                    error = f"HTTP {status}"
        except TimeoutError:
            error = "timeout"
        except json.JSONDecodeError as exc:
            error = f"JSON parse error: {exc}"
        except Exception as exc:  # noqa: BLE001 - aiohttp богат на исключения
            error = f"{type(exc).__name__}: {exc}"

        success = bool(ips)
        return self.make_result(
            success=success,
            target=f"{endpoint}#{host}",
            data={
                "endpoint": endpoint,
                "host": host,
                "http_status": status,
                "ips": ips,
            },
            raw=f"GET {url} -> {status}, ips={ips}",
            error=error,
            severity=Severity.INFO if success else Severity.WARNING,
            duration_ms=self.time_ms() - start,
        )
