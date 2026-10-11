"""Тесты утилит."""

from __future__ import annotations

from tspu_monitor.utils import ensure_writable_dir


def test_ensure_writable_dir_uses_requested_path(tmp_path):
    target = tmp_path / "data"
    result = ensure_writable_dir(target, tmp_path / "fallback")
    assert result == target
    assert result.is_dir()


def test_ensure_writable_dir_falls_back(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    fallback = tmp_path / "fallback"

    result = ensure_writable_dir(blocker, fallback)
    assert result == fallback
    assert result.is_dir()


def test_is_benchmark_ip():
    from tspu_monitor.utils import is_benchmark_ip

    assert is_benchmark_ip("198.18.0.1")
    assert is_benchmark_ip("198.19.255.254")
    assert not is_benchmark_ip("198.20.0.1")
    assert not is_benchmark_ip("1.1.1.1")
    assert not is_benchmark_ip("not-an-ip")


def test_parse_dns_answers():
    import struct

    from tspu_monitor.utils import build_dns_query, parse_dns_answers

    query = build_dns_query("example.com")
    txid = query[:2]
    question = query[12:]
    header = txid + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0)
    answer = (
        b"\xc0\x0c"
        + struct.pack(">HHIH", 1, 1, 60, 4)
        + bytes([93, 184, 216, 34])
    )
    assert parse_dns_answers(header + question + answer) == ["93.184.216.34"]
    assert parse_dns_answers(b"") == []
    assert parse_dns_answers(b"short") == []
