"""Markdown-сводка результатов pytest для GitHub Step Summary."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(junit_path: Path) -> str:
    """Собрать Markdown-таблицу по JUnit XML. Отсутствие файла — не ошибка."""
    if not junit_path.exists():
        return "### Результаты тестов\n\nОтчёт не найден.\n"

    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = failures = errors = skipped = 0
    total_time = 0.0
    for suite in suites:
        tests += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        total_time += float(suite.get("time", 0) or 0)

    passed = max(0, tests - failures - errors - skipped)
    status = "OK" if failures == 0 and errors == 0 else "FAIL"
    return "\n".join(
        [
            "### Результаты тестов",
            "",
            "| Показатель | Значение |",
            "|---|---:|",
            f"| Всего | {tests} |",
            f"| Успешно | {passed} |",
            f"| Провалено | {failures} |",
            f"| Ошибок | {errors} |",
            f"| Пропущено | {skipped} |",
            f"| Время | {total_time:.1f} с |",
            f"| Итог | **{status}** |",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сводка pytest (JUnit XML) в формате Markdown."
    )
    parser.add_argument("junit", help="Путь к JUnit XML")
    args = parser.parse_args(argv)
    sys.stdout.write(summarize(Path(args.junit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
