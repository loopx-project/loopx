#!/usr/bin/env python3
"""Smoke-test the optional pytest facade over the canary smoke-suite runner."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _pytest_facade_available() -> bool:
    return (
        (REPO_ROOT / "tests" / "test_smoke_suite.py").is_file()
        and (REPO_ROOT / "tests" / "conftest.py").is_file()
    )


def _pytest_command() -> list[str] | None:
    try:
        import pytest  # noqa: F401
    except Exception:
        uvx = shutil.which("uvx")
        if uvx:
            return [uvx, "--python", sys.executable, "--with", "pytest>=8,<9", "pytest"]
        return None
    return [sys.executable, "-m", "pytest"]


def main() -> int:
    if not _pytest_facade_available():
        print(
            "pytest-smoke-suite-facade-smoke skipped: pytest facade tests are "
            "not packaged in this checkout"
        )
        return 0

    command_prefix = _pytest_command()
    if command_prefix is None:
        print("pytest-smoke-suite-facade-smoke skipped: pytest or uvx is unavailable")
        return 0

    with tempfile.TemporaryDirectory(prefix="loopx-pytest-smoke-suite-") as tmp_dir:
        junit_path = Path(tmp_dir) / "smoke-suite.xml"
        completed = subprocess.run(
            [
                *command_prefix,
                "-q",
                "tests/test_smoke_suite.py",
                "--loopx-smoke-suite",
                "catalog-plan",
                "--loopx-smoke-profile",
                "catalog-canary-contract",
                "--loopx-smoke-limit",
                "1",
                "--loopx-smoke-timeout",
                "60",
                "--junitxml",
                str(junit_path),
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "pytest smoke-suite facade failed\n"
                f"stdout:\n{completed.stdout[-2000:]}\n"
                f"stderr:\n{completed.stderr[-2000:]}"
            )
        xml = junit_path.read_text(encoding="utf-8")
        assert "<testsuite" in xml or "<testsuites" in xml, xml[:500]
        assert "test_smoke_suite_script" in xml, xml[:500]

    print("pytest-smoke-suite-facade-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
