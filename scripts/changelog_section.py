"""Извлечение раздела CHANGELOG для релиза.

Используется в workflow Release, чтобы тело GitHub Release формировалось
из CHANGELOG.md, а не из автогенерируемого списка коммитов.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTION_RE = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s*-\s*(?P<date>.+))?\s*$")


def extract_section(text: str, version: str) -> str | None:
    """Вернуть Markdown-раздел ``## [version]`` вместе с подразделами."""
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line.strip())
        if match is not None:
            if match.group("version") == version:
                start = index
                continue
            if start is not None:
                return "\n".join(lines[start:index]).strip() + "\n"
    if start is not None:
        return "\n".join(lines[start:]).strip() + "\n"
    return None


def build_notes(text: str, version: str, fallback: str | None = None) -> str | None:
    """Раздел CHANGELOG или запасной текст."""
    section = extract_section(text, version)
    if section is not None:
        return section
    if fallback is not None:
        return fallback.rstrip() + "\n"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Извлечь раздел CHANGELOG для версии (например, 2.1.0)."
    )
    parser.add_argument("version", help="Версия без префикса v")
    parser.add_argument("--file", default="CHANGELOG.md", help="Путь к CHANGELOG")
    parser.add_argument("--output", default=None, help="Файл для записи (по умолчанию stdout)")
    parser.add_argument(
        "--fallback",
        default=None,
        help="Текст, если раздел версии не найден",
    )
    args = parser.parse_args(argv)

    text = Path(args.file).read_text(encoding="utf-8")
    notes = build_notes(text, args.version, args.fallback)
    if notes is None:
        print(
            f"Раздел '[{args.version}]' не найден в {args.file}",
            file=sys.stderr,
        )
        return 1

    if args.output:
        Path(args.output).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
