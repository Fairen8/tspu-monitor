"""Движок классификации блокировок.

Каждая проба возвращает «сырые» :class:`~tspu_monitor.models.ProbeResult`.
Движок прогоняет их через набор правил (rules). Правило — это функция,
которая находит аномалию и возвращает :class:`Finding` с:

* типом блокировки (:class:`~tspu_monitor.models.BlockType`);
* весом (вклад в итоговый score 0..100);
* причиной и доказательством (человекочитаемо, по-русски);
* рекомендацией;
* возможным уровнем обрывов.

Итог: :class:`~tspu_monitor.models.Analysis` с уровнем блокировки,
типами, уровнем обрывов, причинами и рекомендациями.

Правила намеренно простые и детерминированные — их легко тестировать
и расширять.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any

from .logging_setup import get_logger
from .models import (
    Analysis,
    BlockType,
    DisconnectLevel,
    ProbeResult,
    Severity,
    disconnect_from_ratio,
    level_from_score,
)

logger = get_logger("tspu.classification")


# ---------------------------------------------------------------------------
# Обнаружение (finding)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Finding:
    """Одна найденная аномалия/признак блокировки."""

    type: BlockType | None
    weight: int
    evidence: str
    cause: str | None = None
    recommendation: str | None = None
    disconnect: DisconnectLevel = DisconnectLevel.NONE


class ResultIndex:
    """Удобный доступ к результатам проб по имени и полям ``data``."""

    def __init__(self, results: Sequence[ProbeResult]) -> None:
        self.results: list[ProbeResult] = [r for r in results if not r.skipped]

    def by_probe(self, name: str) -> list[ProbeResult]:
        return [r for r in self.results if r.probe == name]

    def first(self, name: str, **match: Any) -> ProbeResult | None:
        found = self.all(name, **match)
        return found[0] if found else None

    def all(self, name: str, **match: Any) -> list[ProbeResult]:
        out = []
        for r in self.by_probe(name):
            if all(r.data.get(k) == v for k, v in match.items()):
                out.append(r)
        return out

    def has_any(self, name: str) -> bool:
        return bool(self.by_probe(name))

    def any_success(self, name: str) -> bool:
        return any(r.success for r in self.by_probe(name))

    def has_skipped(self, name: str) -> bool:
        return any(r.probe == name and r.skipped for r in self.results)


def _dedupe(items: Sequence[str | None]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Движок
# ---------------------------------------------------------------------------


class DiagnosisEngine:
    """Превращает результаты проб в структурированный диагноз."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        classification = (settings or {}).get("classification", {}) or {}
        self.thresholds: dict[str, Any] = classification.get("level_thresholds", {}) or {}
        self.throttle_min_kbps: float = float(
            classification.get("throttle_min_kbps", 256)
        )

    # -- публичный API ----------------------------------------------------
    def diagnose(
        self,
        profile: str,
        title: str,
        target: str,
        results: Sequence[ProbeResult],
        extra_findings: list[Finding] | None = None,
    ) -> Analysis:
        index = ResultIndex(results)
        findings: list[Finding] = []
        for rule in self._rules():
            try:
                findings.extend(rule(index))
            except Exception as exc:  # noqa: BLE001 - правило не должно ломать анализ
                logger.exception("Правило классификации упало: %s", exc)

        if extra_findings:
            findings.extend(extra_findings)

        if not findings:
            findings.extend(self._fallback(index))

        score = min(100, sum(max(0, f.weight) for f in findings))
        level = level_from_score(score, self.thresholds)

        types: list[BlockType] = []
        for f in findings:
            if f.type is not None and f.type not in types:
                types.append(f.type)

        disconnect = DisconnectLevel.NONE
        if findings:
            disconnect = max(f.disconnect for f in findings)

        return Analysis(
            profile=profile,
            title=title,
            target=target,
            score=score,
            level=level,
            types=types,
            disconnect=disconnect,
            causes=_dedupe([f.cause for f in findings]),
            evidence=_dedupe([f.evidence for f in findings]),
            recommendations=_dedupe([f.recommendation for f in findings]),
            results=list(results),
        )

    # -- правила ----------------------------------------------------------
    def _rules(self) -> list[Callable[[ResultIndex], list[Finding]]]:
        return [
            self._rule_ip_block,
            self._rule_icmp,
            self._rule_mtu,
            self._rule_trace,
            self._rule_raw_ttl,
            self._rule_tcp_connect,
            self._rule_dns,
            self._rule_doh,
            self._rule_tls,
            self._rule_tls_cert,
            self._rule_http,
            self._rule_quic,
            self._rule_udp_vpn,
        ]

    # 1. Полная недоступность IP
    def _rule_ip_block(self, ix: ResultIndex) -> list[Finding]:
        icmp = ix.by_probe("icmp.ping")
        tcp = ix.by_probe("tcp.connect")
        if not icmp or not tcp:
            return []
        icmp_dead = all((r.data.get("packet_loss_percent") or 0) >= 100 for r in icmp)
        tcp_dead = all(not r.success for r in tcp)
        if icmp_dead and tcp_dead:
            hosts = sorted({str(r.data.get("host") or r.target) for r in tcp})
            return [
                Finding(
                    BlockType.IP_BLOCK,
                    70,
                    f"ICMP и все TCP-порты недоступны: {', '.join(hosts)}",
                    cause="IP-адрес полностью заблокирован либо сервер выключен",
                    recommendation=(
                        "Проверьте состояние сервера. Если он работает, "
                        "но недоступен только из вашей сети — требуется смена IP "
                        "или использование промежуточного прокси"
                    ),
                    disconnect=DisconnectLevel.CONSTANT,
                )
            ]
        return []

    # 2. ICMP: потери и jitter
    def _rule_icmp(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        dead_hosts: list[str] = []
        for r in ix.by_probe("icmp.ping"):
            loss = r.data.get("packet_loss_percent")
            if loss is None:
                continue
            host = str(r.data.get("host") or r.target)
            if loss >= 100:
                dead_hosts.append(host)
            elif loss >= 50:
                out.append(
                    Finding(
                        None,
                        8,
                        f"ICMP до {host}: потери {loss}%",
                        cause="Нестабильный маршрут или шейпинг ICMP",
                        disconnect=DisconnectLevel.PERIODIC,
                    )
                )
            elif loss > 0:
                out.append(
                    Finding(
                        None,
                        4,
                        f"ICMP до {host}: потери {loss}%",
                        disconnect=DisconnectLevel.RARE,
                    )
                )
        if dead_hosts:
            # Одно агрегированное правило: иначе N хостов раздувают баллы.
            out.append(
                Finding(
                    BlockType.ICMP_BLOCK,
                    10 if len(dead_hosts) == 1 else 8,
                    "ICMP не отвечает: " + ", ".join(sorted(dead_hosts)),
                    cause=(
                        "Фильтрация ICMP провайдером/DPI или активный VPN "
                        "с fake-ip (домены резолвятся в служебные адреса)"
                    ),
                    recommendation=(
                        "ICMP-молчание само по себе не блокировка — "
                        "ориентируйтесь на TCP/HTTPS-пробы"
                    ),
                )
            )
        return out

    # 3. MTU (Path MTU Discovery через ping с DF)
    def _rule_mtu(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        for r in ix.by_probe("icmp.mtu"):
            pmtu = r.data.get("pmtu")
            if pmtu is None:
                continue
            host = str(r.data.get("host") or r.target)
            if pmtu < 1280:
                out.append(
                    Finding(
                        BlockType.MTU_FILTER,
                        25,
                        f"Path MTU до {host} занижен: {pmtu} байт",
                        cause="Фрагментация/большие пакеты не проходят (MTU-фильтр или туннель)",
                        recommendation=(
                            "Уменьшите MTU клиента (например, до 1280) "
                            "и включите фрагментацию"
                        ),
                    )
                )
            elif pmtu < 1450:
                out.append(
                    Finding(
                        None,
                        6,
                        f"Path MTU до {host}: {pmtu} байт (ниже типичных 1500)",
                        cause="Туннель или PPPoE по пути",
                        recommendation="Учтите заниженный MTU в настройках VPN-клиента",
                    )
                )
        return out

    # 4. Traceroute: «стена» из молчащих хопов
    def _rule_trace(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        for r in ix.by_probe("icmp.trace"):
            if r.skipped:
                continue
            data = r.data or {}
            stars = int(data.get("max_consecutive_star_hops") or 0)
            reachable = bool(data.get("reachable"))
            host = str(data.get("host") or r.target)
            if stars >= 3 and not reachable:
                out.append(
                    Finding(
                        BlockType.ICMP_BLOCK,
                        12,
                        f"Маршрут до {host}: {stars}+ молчащих хопов подряд, "
                        "цель не достигнута",
                        cause=(
                            "Фильтрация TTL/ICMP на промежуточном узле "
                            "(вероятен middlebox/DPI) или цель блокирует ICMP"
                        ),
                        recommendation=(
                            "Если TCP/HTTPS при этом работает — критичного нет; "
                            "сравните маршрут через VPN и без него"
                        ),
                    )
                )
            elif stars >= 3 and reachable:
                out.append(
                    Finding(
                        None,
                        4,
                        f"Маршрут до {host}: промежуточные узлы скрывают ответы "
                        f"({stars} хопов подряд)",
                        cause="ICMP rate-limit или сокрытие узлов провайдером",
                    )
                )
        return out

    # 5. Raw TTL: инъекция RST выдаёт себя неверным TTL
    def _rule_raw_ttl(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        per_host: dict[str, dict[str, int]] = {}
        for r in ix.by_probe("raw.ttl"):
            if r.skipped:
                continue
            data = r.data or {}
            host = str(data.get("host") or r.target)
            kind = data.get("reply_kind")
            ttl = data.get("reply_ttl")
            if kind and isinstance(ttl, int):
                per_host.setdefault(host, {})[kind] = ttl
        for host, ttls in per_host.items():
            synack = ttls.get("synack")
            rst = ttls.get("rst")
            if synack and rst and rst > synack + 10:
                out.append(
                    Finding(
                        BlockType.RST_INJECTION,
                        40,
                        f"RST для {host} пришёл с TTL {rst}, ответ сервера — "
                        f"TTL {synack}: пакет отправлен другим узлом",
                        cause="Инъекция RST промежуточным устройством (TTL выдаёт DPI)",
                        recommendation=(
                            "Маскируйте протокол (TLS/Reality) или смените узел/порт"
                        ),
                    )
                )
        return out

    # 6. TCP: RST-инъекции, закрытые/фильтруемые порты, обрывы
    def _rule_tcp_connect(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        for r in ix.by_probe("tcp.connect"):
            d = r.data
            host = str(d.get("host") or r.target)
            port = d.get("port")
            label = f"{host}:{port}"
            attempts = int(d.get("attempts", 1) or 1)
            successes = int(d.get("successes", 1 if r.success else 0) or 0)
            fast_rst = int(d.get("fast_rst_count", 0) or 0)
            refused = int(d.get("refused_count", 0) or 0)
            timeouts = int(d.get("timeout_count", 0) or 0)
            fails = max(0, attempts - successes)

            if fast_rst > 0:
                out.append(
                    Finding(
                        BlockType.RST_INJECTION,
                        45,
                        f"TCP {label}: мгновенный RST ({fast_rst} из {attempts}) — "
                        "ответ быстрее физически возможного",
                        cause="Инъекция RST со стороны DPI/TSPU",
                        recommendation=(
                            "Маскируйте протокол (TLS/Reality), смените порт "
                            "или используйте обфускацию"
                        ),
                    )
                )
            if successes == 0 and fails > 0:
                if refused > 0 and fast_rst == 0:
                    out.append(
                        Finding(
                            BlockType.PORT_BLOCK,
                            30,
                            f"TCP {label}: соединение отклонено ({refused} из {attempts})",
                            cause="Порт закрыт или блокируется фильтром",
                            recommendation=(
                                "Проверьте, слушает ли сервер порт, "
                                "и попробуйте альтернативный порт"
                            ),
                            disconnect=disconnect_from_ratio(1.0, attempts),
                        )
                    )
                elif timeouts > 0:
                    out.append(
                        Finding(
                            BlockType.PORT_BLOCK,
                            25,
                            f"TCP {label}: таймауты ({timeouts} из {attempts}) — пакеты не доходят",
                            cause="Фильтрация TCP: SYN-пакеты отбрасываются (firewall/DPI)",
                            recommendation=(
                                "Смените порт/протокол или проверьте firewall сервера"
                            ),
                            disconnect=disconnect_from_ratio(1.0, attempts),
                        )
                    )
            elif fails > 0:
                out.append(
                    Finding(
                        None,
                        10,
                        f"TCP {label}: {fails} из {attempts} подключений неудачны",
                        cause="Периодические обрывы TCP-соединений",
                        disconnect=disconnect_from_ratio(fails / attempts, attempts),
                    )
                )
        return out

    # 5. DNS: спуфинг и недоступность
    def _rule_dns(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        for r in ix.by_probe("dns.resolve"):
            d = r.data
            host = str(d.get("host") or r.target)
            if d.get("fake_ip"):
                out.append(
                    Finding(
                        None,
                        2,
                        f"DNS {host}: системный резолвер отдаёт fake-ip (VPN/TUN)",
                        cause=(
                            "Активен VPN/TUN с fake-ip — сравнение с публичными "
                            "DNS ограничено, это не признак подмены"
                        ),
                    )
                )
                continue
            if d.get("spoof_suspected"):
                answers = d.get("system_answers") or []
                out.append(
                    Finding(
                        BlockType.DNS_SPOOF,
                        45,
                        f"DNS {host}: системный резолвер вернул подозрительный IP {answers}",
                        cause="Подмена DNS-ответа (спуфинг) локальным резолвером или TSPU",
                        recommendation=(
                            "Используйте DoH/DoT (Cloudflare, Google) "
                            "или зашифрованный DNS"
                        ),
                    )
                )
            elif not d.get("system_ok", True):
                out.append(
                    Finding(
                        BlockType.DNS_FILTER,
                        25,
                        f"DNS {host}: системный резолвер не смог разрешить имя",
                        cause="Блокировка DNS-запроса",
                        recommendation="Проверьте настройки DNS, попробуйте другой резолвер",
                    )
                )
        return out

    # 6. DoH: работает ли шифрованный DNS
    def _rule_doh(self, ix: ResultIndex) -> list[Finding]:
        doh = ix.by_probe("dns.doh")
        if not doh:
            return []
        plain_dns_ok = ix.any_success("dns.resolve")
        if plain_dns_ok and all(not r.success for r in doh):
            return [
                Finding(
                    BlockType.DNS_FILTER,
                    20,
                    "DoH-эндпоинты недоступны при работающем обычном DNS",
                    cause="Фильтрация DNS-over-HTTPS",
                    recommendation=(
                        "Попробуйте другие DoH-адреса/порты или DNS-over-TLS"
                    ),
                )
            ]
        return []

    # 7. TLS: SNI-фильтрация, RST, вмешательство
    def _rule_tls(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        results = ix.by_probe("tls.handshake")
        for r in results:
            role = str(r.data.get("role", "real"))
            if r.success or role == "cover":
                continue
            err_lower = (r.error or "").lower()
            error_class = r.data.get("error_class")
            label = r.target
            if error_class == "reset" or "reset" in err_lower:
                out.append(
                    Finding(
                        BlockType.RST_INJECTION,
                        40,
                        f"TLS {label}: соединение сброшено (RST)",
                        cause="RST-инъекция на этапе TLS handshake",
                        recommendation="Используйте Reality/XTLS или маскировку трафика",
                    )
                )
            elif error_class in ("alert", "handshake", "cert") or "alert" in err_lower:
                out.append(
                    Finding(
                        BlockType.TLS_INTERFERENCE,
                        35,
                        f"TLS {label}: handshake прерван ({r.error})",
                        cause="Вмешательство в TLS handshake (возможна фильтрация по SNI)",
                        recommendation="Смените SNI/домен или настройте технику Reality",
                    )
                )
            elif error_class == "timeout":
                out.append(
                    Finding(
                        BlockType.PORT_BLOCK,
                        20,
                        f"TLS {label}: таймаут handshake",
                        cause="TLS-порт недоступен",
                        disconnect=DisconnectLevel.RARE,
                    )
                )

        real = ix.first("tls.handshake", role="real")
        bogus = ix.first("tls.handshake", role="bogus")
        cover = ix.first("tls.handshake", role="cover")
        if real and bogus and not real.success and bogus.success:
            out.append(
                Finding(
                    BlockType.SNI_FILTER,
                    50,
                    f"SNI-дифференциал: с SNI={real.data.get('sni')} отказ, "
                    f"с SNI={bogus.data.get('sni')} — успех",
                    cause="Фильтрация по значению SNI",
                    recommendation=(
                        "Не используйте фильтруемый SNI; настройте Reality "
                        "с другим cover-доменом"
                    ),
                )
            )
        if real and cover and not real.success and cover.success:
            out.append(
                Finding(
                    BlockType.SNI_FILTER,
                    45,
                    f"{real.target}: TLS не проходит, тогда как контрольный "
                    f"{cover.target} отвечает",
                    cause="Избирательная фильтрация TLS конкретного ресурса",
                    recommendation="Проверьте домен/SNI в реестре блокировок",
                )
            )
        if cover and not cover.success and real and not real.success:
            out.append(
                Finding(
                    BlockType.COVER_BLOCK,
                    25,
                    f"Cover-ресурс {cover.target} недоступен",
                    cause="Заблокирован домен-прикрытие (cover) или сеть недоступна",
                    recommendation="Выберите другой популярный cover-домен",
                )
            )
        return out

    # 8. TLS-сертификаты: подмена, самоподпись, отсутствие доверия
    def _rule_tls_cert(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        for r in ix.by_probe("tls.handshake"):
            if r.skipped or not r.success:
                continue
            verify = str(r.data.get("verify") or "")
            if not verify.lower().startswith(("error", "fail")):
                continue
            host = str(r.data.get("host") or r.target)
            out.append(
                Finding(
                    BlockType.TLS_INTERFERENCE,
                    10,
                    f"TLS {host}: сертификат не проходит проверку ({verify})",
                    cause=(
                        "Самоподписанный или подменённый сертификат "
                        "(либо отсутствует CA-хранилище в системе)"
                    ),
                    recommendation=(
                        "Проверьте цепочку сертификатов сервера; при MITM — "
                        "используйте пиннинг/Reality"
                    ),
                )
            )
        return out

    # 9. HTTP: заглушки, RST, шейпинг
    def _rule_http(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []
        for r in ix.by_probe("http.get"):
            d = r.data
            url = str(d.get("url") or r.target)
            label = f"{url}: "
            if d.get("plug_page_detected"):
                signature = d.get("body_signature") or d.get("header_signature")
                out.append(
                    Finding(
                        BlockType.HTTP_PLUG,
                        40,
                        f"HTTP-заглушка {url} (сигнатура: {signature})",
                        cause="Запрос перехвачен и подменён страницей-заглушкой ТСПУ/оператора",
                        recommendation="Ресурс заблокирован: используйте VPN/обфускацию",
                    )
                )
            elif d.get("reset") or (r.error and "reset" in r.error.lower()):
                out.append(
                    Finding(
                        BlockType.RST_INJECTION,
                        35,
                        f"{label}соединение сброшено",
                        cause="RST-инъекция при HTTP/TLS-запросе",
                        recommendation="Используйте маскировку трафика",
                    )
                )
            elif not r.success:
                out.append(
                    Finding(
                        BlockType.TLS_INTERFERENCE if d.get("scheme") == "https" else None,
                        18,
                        f"{label}запрос не выполнен ({r.error})",
                        cause="Проблема с HTTPS-соединением"
                        if d.get("scheme") == "https"
                        else "Проблема с HTTP-соединением",
                    )
                )

            if (
                r.success
                and d.get("speed_kbps") is not None
                and int(d.get("download_bytes", 0) or 0) >= 65536
                and float(d["speed_kbps"]) < self.throttle_min_kbps
            ):
                out.append(
                    Finding(
                        BlockType.THROTTLE,
                        15,
                        f"{label}низкая скорость {float(d['speed_kbps']):.0f} КБ/с",
                        cause="Возможен шейпинг/ограничение полосы провайдером",
                        recommendation=(
                            "Сравните скорость на другом канале; "
                            "попробуйте другой протокол/порт"
                        ),
                    )
                )

            attempts = int(d.get("attempts", 1) or 1)
            successes = int(d.get("successes", 1 if r.success else 0) or 0)
            if attempts >= 2 and successes < attempts:
                fails = attempts - successes
                out.append(
                    Finding(
                        None,
                        8,
                        f"{label}{fails} из {attempts} запросов неудачны",
                        cause="Нестабильное соединение (периодические обрывы)",
                        disconnect=disconnect_from_ratio(fails / attempts, attempts),
                    )
                )
        return out

    # 9. QUIC (HTTP/3)
    def _rule_quic(self, ix: ResultIndex) -> list[Finding]:
        quic = ix.by_probe("quic.initial")
        if not quic:
            return []
        tcp443_ok = any(
            r.success and r.data.get("port") == 443 for r in ix.by_probe("tcp.connect")
        ) or ix.any_success("http.get")
        if not tcp443_ok:
            return []
        controls = [r for r in quic if r.data.get("control")]
        targets = [r for r in quic if not r.data.get("control")]
        poll = targets or quic
        if all((r.data.get("replies") or 0) == 0 for r in poll):
            if controls and all((r.data.get("replies") or 0) == 0 for r in controls):
                return []
            hosts = ", ".join(sorted({str(r.data.get("host") or r.target) for r in poll}))
            return [
                Finding(
                    BlockType.QUIC_BLOCK,
                    25,
                    f"QUIC (UDP/443) не отвечает ({hosts}) при работающем TCP/443",
                    cause="Фильтрация QUIC/HTTP3",
                    recommendation=(
                        "Отключите HTTP/3 (QUIC) в браузере/клиенте "
                        "или используйте TCP-режим"
                    ),
                )
            ]
        return []

    # 10. UDP и VPN-протоколы
    def _rule_udp_vpn(self, ix: ResultIndex) -> list[Finding]:
        out: list[Finding] = []

        controls = [r for r in ix.by_probe("udp.probe") if r.data.get("control")]
        for r in controls:
            if not r.success:
                out.append(
                    Finding(
                        BlockType.UDP_BLOCK,
                        25,
                        f"Контрольный UDP-зонд {r.target} не получил ответа",
                        cause="UDP-трафик фильтруется",
                        recommendation="Проверьте, не блокирует ли провайдер UDP целиком",
                    )
                )
        control_ok = any(r.success for r in controls)

        generic = [r for r in ix.by_probe("udp.probe") if not r.data.get("control")]
        large = [r for r in generic if int(r.data.get("payload_size", 0) or 0) >= 1000]
        small = [
            r
            for r in generic
            if 0 < int(r.data.get("payload_size", 0) or 0) < 1000
            and not r.data.get("expected_silent")
        ]
        if large and small and all(not r.success for r in large) and any(
            r.success for r in small
        ):
            out.append(
                Finding(
                    BlockType.MTU_FILTER,
                    30,
                    "Большие UDP-пакеты не проходят, малые — проходят",
                    cause="Фильтрация по размеру пакета / ограничение MTU",
                    recommendation="Снизьте MTU до 1280 и включите фрагментацию",
                )
            )

        for r in ix.by_probe("wireguard.handshake"):
            if r.data.get("refused"):
                out.append(
                    Finding(
                        BlockType.PORT_BLOCK,
                        35,
                        f"WireGuard {r.target}: порт отвергает пакеты (ICMP unreachable)",
                        cause="Порт WireGuard не слушает или блокируется",
                        recommendation="Проверьте конфигурацию сервера и смените порт",
                    )
                )

        tcp_ok = ix.any_success("tcp.connect")
        for r in ix.by_probe("openvpn.reset"):
            if not r.success and (r.data.get("replies") or 0) == 0 and control_ok and tcp_ok:
                out.append(
                    Finding(
                        BlockType.PROTOCOL_DETECT,
                        35,
                        f"OpenVPN {r.target}: на валидный reset-пакет нет ответа, "
                        "но UDP и TCP в целом доступны",
                        cause="DPI распознаёт сигнатуру OpenVPN и отбрасывает пакеты",
                        recommendation=(
                            "Перейдите на TCP/443 с TLS-crypt или используйте "
                            "обфускацию (stunnel/obfsproxy)"
                        ),
                    )
                )

        for r in ix.by_probe("shadowsocks.entropy"):
            if r.data.get("reset"):
                out.append(
                    Finding(
                        BlockType.PROTOCOL_DETECT,
                        30,
                        f"Shadowsocks {r.target}: RST после случайных байтов",
                        cause="Детекция высокоэнтропийного трафика (Shadowsocks)",
                        recommendation="Используйте плагин маскировки (v2ray-plugin/cloak)",
                    )
                )
        entropy = ix.by_probe("shadowsocks.entropy")
        if len(entropy) >= 2:
            first, second = entropy[0], entropy[1]
            d1, d2 = first.duration_ms, second.duration_ms
            if d1 and d2 and d2 < d1 / 2 and second.data.get("reset"):
                out.append(
                    Finding(
                        BlockType.REPLAY_CACHE,
                        20,
                        "Повторное соединение с тем же шумом рвётся быстрее первого",
                        cause="Кэширование сигнатур DPI (replay-cache)",
                        recommendation="Меняйте префикс/энтропию каждого соединения",
                    )
                )
        return out

    # -- запасное правило --------------------------------------------------
    def _fallback(self, ix: ResultIndex) -> list[Finding]:
        critical = [r for r in ix.results if r.severity == Severity.CRITICAL]
        if critical:
            r = critical[0]
            return [
                Finding(
                    BlockType.UNKNOWN,
                    20,
                    f"{r.probe} @ {r.target}: {r.error or 'критическая аномалия'}",
                    cause="Неизвестная критическая аномалия",
                    recommendation="Изучите детальные результаты пробы",
                )
            ]
        warnings = [r for r in ix.results if r.severity == Severity.WARNING]
        if warnings:
            r = warnings[0]
            return [
                Finding(
                    BlockType.UNKNOWN,
                    8,
                    f"{r.probe} @ {r.target}: {r.error or 'аномалия'}",
                    cause="Неизвестная аномалия",
                )
            ]
        return []
