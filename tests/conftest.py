from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.canary.runner import SMOKE_SUITE_CHOICES  # noqa: E402
from loopx.semantics.production import NPM_DEV_DEPENDENCIES_MISSING  # noqa: E402


def pytest_addoption(parser) -> None:
    group = parser.getgroup("loopx-smoke-suite")
    group.addoption(
        "--loopx-smoke-suite",
        "--smoke-suite",
        dest="loopx_smoke_suite",
        choices=sorted(SMOKE_SUITE_CHOICES),
        default=None,
        help=(
            "Opt in to the canary smoke-suite facade and pass this selector "
            "through to the LoopX runner."
        ),
    )
    group.addoption(
        "--loopx-smoke-module",
        "--smoke-module",
        action="append",
        default=[],
        dest="loopx_smoke_modules",
        help="Module token filter passed through to the LoopX runner. Repeat or comma-separate.",
    )
    group.addoption(
        "--loopx-smoke-exclude-module",
        "--smoke-exclude-module",
        action="append",
        default=[],
        dest="loopx_smoke_exclude_modules",
        help="Module token exclusion passed through to the LoopX runner. Repeat or comma-separate.",
    )
    group.addoption(
        "--loopx-smoke-script",
        "--smoke-script",
        action="append",
        default=[],
        dest="loopx_smoke_scripts",
        help="examples/**/*-smoke.py selector passed through to the LoopX runner. Repeat or comma-separate.",
    )
    group.addoption(
        "--loopx-smoke-profile",
        "--smoke-profile",
        action="append",
        default=[],
        dest="loopx_smoke_profiles",
        help=(
            "Smoke-suite or catalog profile selector passed through to the LoopX "
            "runner. Repeat or comma-separate."
        ),
    )
    group.addoption(
        "--loopx-smoke-family",
        "--smoke-family",
        action="append",
        default=[],
        dest="loopx_smoke_families",
        help="Catalog family selector passed through to the LoopX runner. Repeat or comma-separate.",
    )
    group.addoption(
        "--loopx-smoke-include-deep-checks",
        "--smoke-include-deep-checks",
        action="store_true",
        default=False,
        dest="loopx_smoke_include_deep_checks",
        help="Include deep catalog checks when profile or family selectors are used.",
    )
    group.addoption(
        "--loopx-smoke-limit",
        "--smoke-limit",
        type=int,
        default=0,
        dest="loopx_smoke_limit",
        help="Maximum selected checks to run. Defaults to all selected checks.",
    )
    group.addoption(
        "--loopx-smoke-offset",
        "--smoke-offset",
        type=int,
        default=0,
        dest="loopx_smoke_offset",
        help="Skip this many matched checks before applying the smoke-suite limit.",
    )
    group.addoption(
        "--loopx-smoke-timeout",
        "--smoke-timeout",
        type=float,
        default=120.0,
        dest="loopx_smoke_timeout",
        help="Per-check timeout in seconds for each subprocess smoke.",
    )


def _typescript_dev_dependency_installed() -> bool:
    return any(
        (directory / "node_modules" / "typescript" / "package.json").is_file()
        for directory in (REPO_ROOT, *REPO_ROOT.parents)
    )


def pytest_report_header(config) -> list[str]:
    if _typescript_dev_dependency_installed():
        return []
    return [f"loopx: {NPM_DEV_DEPENDENCIES_MISSING}; TypeScript semantic scans will fail"]


_SETUP_FAILURES: set[str] = set()


def pytest_runtest_logreport(report) -> None:
    if report.failed and NPM_DEV_DEPENDENCIES_MISSING in report.longreprtext:
        _SETUP_FAILURES.add(report.nodeid)


def pytest_unconfigure(config) -> None:
    # Runs after the final totals line, so the remedy is the last thing shown.
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if _SETUP_FAILURES and reporter is not None:
        reporter.write_sep("=", "loopx setup", yellow=True)
        reporter.write_line(
            f"{len(_SETUP_FAILURES)} failure(s) share one cause: {NPM_DEV_DEPENDENCIES_MISSING}."
        )
