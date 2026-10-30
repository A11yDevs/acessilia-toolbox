"""Fail CI when a JUnit report records skipped tests.

A skipped test passes green while testing nothing, so every CI job that
depends on environment-gated tests validates its reports with this script.

Usage: python scripts/check_no_skipped_tests.py REPORT.xml [REPORT.xml ...]
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree


def skipped_tests(reports: list[Path]) -> int:
    return sum(
        int(suite.attrib.get("skipped", "0"))
        for report in reports
        for suite in ElementTree.parse(report).getroot().iter("testsuite")
    )


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_no_skipped_tests.py REPORT.xml [REPORT.xml ...]", file=sys.stderr)
        return 2
    skipped = skipped_tests([Path(arg) for arg in argv])
    if skipped:
        print(
            f"CI incompleto: {skipped} teste(s) foram pulado(s). "
            "Consulte os motivos exibidos pelo pytest.",
            file=sys.stderr,
        )
        return 1
    print("Relatorio JUnit validado: nenhum teste foi pulado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
