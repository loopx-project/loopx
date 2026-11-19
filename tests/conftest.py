from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
import tempfile
import warnings
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
# Suppress collection before imports and in inherited test subprocesses.
# Telemetry transport tests explicitly opt in against disposable collectors.
os.environ["LOOPX_USAGE_PING"] = "0"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from managed_effect_runtime_fixture import (  # noqa: E402
    capture_disposable_effect_runtimes,
    stop_disposable_effect_runtimes,
)

from loopx import paths  # noqa: E402
from loopx.canary.runner import SMOKE_SUITE_CHOICES  # noqa: E402
from loopx.semantics.production import NPM_DEV_DEPENDENCIES_MISSING  # noqa: E402


# Default runtime routes resolve from HOME at import time. A test that writes
# either one leaves state behind, and once both hold state every later implicit
# default route fails as a conflict, so each test gets its own disposable pair.
_DEFAULT_ROUTE_REFERENCES = (
    ("loopx.paths", "DEFAULT_RUNTIME_ROOT", (".loopx",)),
    ("loopx.paths", "LEGACY_RUNTIME_ROOT", (".codex", "loopx")),
    ("loopx.contract", "DEFAULT_RUNTIME_ROOT", (".loopx",)),
    ("loopx.contract", "LEGACY_RUNTIME_ROOT", (".codex", "loopx")),
    ("loopx.cli_commands.registry_admin_lifecycle", "DEFAULT_RUNTIME_ROOT", (".loopx",)),
    ("loopx.cli_commands.registry_admin_lifecycle", "LEGACY_LOCAL_RUNTIME_ROOT", (".codex", "loopx")),
    ("loopx.control_plane.runtime.local_state_migration", "DEFAULT_RUNTIME_ROOT", (".loopx",)),
    ("loopx.control_plane.runtime.local_state_migration", "LEGACY_RUNTIME_ROOT", (".codex", "loopx")),
)
_REAL_DEFAULT_ROUTES = (paths.DEFAULT_RUNTIME_ROOT, paths.LEGACY_RUNTIME_ROOT)
_CASE_TEMP_DIRS = pytest.StashKey[list[Path]]()
_CASE_FAILED = pytest.StashKey[bool]()
_CASE_SEQUENCE = pytest.StashKey[dict[str, int]]()
_WORKER_TEMP = pytest.StashKey[Path]()
_TEMP_RETENTION = pytest.StashKey[str]()
_TEMP_RETENTION_INI = "tmp_path_retention_policy"
_MIB = 1024 * 1024


def pytest_configure(config):
    if config.getoption("loopx_min_free_tmp_mib") < 0:
        raise pytest.UsageError("--loopx-min-free-tmp-mib must be nonnegative")
    overrides = config.getoption("override_ini") or ()
    with warnings.catch_warnings():
        # Pytest 9's getini() returns the same value for absent and explicit "all".
        warnings.filterwarnings("ignore", message=r"config\.inicfg is deprecated", category=pytest.PytestDeprecationWarning)
        explicit_policy = _TEMP_RETENTION_INI in config.inicfg or any(
            override.split("=", 1)[0].strip() == _TEMP_RETENTION_INI for override in overrides
        )
    config.stash[_TEMP_RETENTION] = config.getini(_TEMP_RETENTION_INI) if explicit_policy else "failed"
    config.stash[_CASE_SEQUENCE] = {}


@pytest.fixture
def case_mktemp(request, tmp_path_factory):
    """Create a named temporary directory owned by the current test case."""
    def make(name):
        # Older pytest versions can reuse a factory name once its directory is removed.
        sequences = request.config.stash[_CASE_SEQUENCE]
        serial = sequences.get(name, 0)
        sequences[name] = serial + 1
        temporary = tmp_path_factory.mktemp(f"{name}-{serial}", numbered=False)
        request.node.stash.setdefault(_CASE_TEMP_DIRS, []).append(temporary)
        return temporary

    return make


@pytest.fixture
def tmp_path(request, case_mktemp):
    # Use pytest's test-name prefix, but own the lifetime through the teardown
    # report. Pytest's fixture finalizer only knows about setup/call at this point.
    name = re.sub(r"\W", "_", request.node.name)[:30]
    return case_mktemp(name)


def _remove_temp_dir(temporary):
    try:
        if temporary.is_symlink():
            temporary.unlink()
        else:
            shutil.rmtree(temporary)
    except FileNotFoundError:
        pass
    except OSError as exc:
        warnings.warn(pytest.PytestWarning(f"Could not clean pytest temporary directory {temporary}: {exc}"),
                      stacklevel=2)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed:
        item.stash[_CASE_FAILED] = True
    if report.when == "teardown":
        policy = item.config.stash[_TEMP_RETENTION]
        if policy == "none" or (policy == "failed" and not item.stash.get(_CASE_FAILED, False)):
            for temporary in item.stash.get(_CASE_TEMP_DIRS, []):
                _remove_temp_dir(temporary)
        for key in (_CASE_TEMP_DIRS, _CASE_FAILED):
            if key in item.stash:
                del item.stash[key]
    return report


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    minimum = item.config.getoption("loopx_min_free_tmp_mib")
    if minimum == 0:
        return
    configured = item.config.getoption("basetemp") or os.environ.get("PYTEST_DEBUG_TEMPROOT")
    target = Path(configured or tempfile.gettempdir()).resolve()
    existing = next(path for path in (target, *target.parents) if path.is_dir())
    available = shutil.disk_usage(existing).free
    if available < minimum * _MIB:
        reason = (f"LoopX pytest temporary storage is low at {existing}: "
                  f"{available // _MIB} MiB available, {minimum} MiB required.")
        # Stop scheduling work after this setup report, including on xdist workers.
        item.session.shouldstop = reason
        pytest.fail(reason, pytrace=False)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    temporary = item.funcargs.get("tmp_path")
    captured = capture_disposable_effect_runtimes(temporary) if isinstance(temporary, Path) else []
    try:
        return (yield)
    finally:
        # Run after fixture finalizers and monkeypatch restoration, including
        # failed tests. Ending a fixture must not leave its copied-source Node
        # processes alive for the runtime's normal five-minute reuse window.
        if isinstance(temporary, Path):
            stop_disposable_effect_runtimes(temporary, captured)


@pytest.fixture(scope="session", autouse=True)
def _isolated_worker_effect_runtime(tmp_path_factory, request):
    # HOME does not namespace the Effect process on POSIX: its locator uses
    # getuid() and the process temp directory. A restart in one xdist worker
    # must not retire another worker's real runtime.
    temporary = tmp_path_factory.mktemp("effect-runtime")
    request.config.stash[_WORKER_TEMP] = temporary
    with pytest.MonkeyPatch.context() as patch:
        for variable in ("TMPDIR", "TEMP", "TMP"):
            patch.setenv(variable, str(temporary))
        patch.setattr(tempfile, "tempdir", str(temporary))
        yield
        from loopx.control_plane.effect_runtime import restart_effect_runtime

        restart_effect_runtime(wait_for_exit=True)


@pytest.fixture
def isolated_effect_runtime(tmp_path, monkeypatch):
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    from loopx.control_plane import effect_runtime

    yield
    # Stop only the real runtime launched in this test's private directory.
    effect_runtime.restart_effect_runtime()


@pytest.fixture(autouse=True)
def _isolated_default_runtime_routes(case_mktemp, monkeypatch):
    home = case_mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # Prevent an active Antigravity host conversation from leaking ambient
    # thread identity into hermetic start-goal / hook template tests.
    monkeypatch.delenv("ANTIGRAVITY_CONVERSATION_ID", raising=False)
    # Tests opt into provider selection; isolate the operator's Chat route.
    monkeypatch.delenv("LOOPX_CHAT_CODEX_MODEL_PROVIDER", raising=False)
    for module_name, attribute, parts in _DEFAULT_ROUTE_REFERENCES:
        monkeypatch.setattr(importlib.import_module(module_name), attribute, home.joinpath(*parts))
    yield


def pytest_sessionstart(session) -> None:
    # Only routes absent at start are guarded; existing developer state may be
    # changed by other local processes during the run.
    session.config._loopx_absent_routes = [
        root for root in _REAL_DEFAULT_ROUTES if not os.path.lexists(root)
    ]


def pytest_sessionfinish(session, exitstatus) -> None:
    created = [
        root for root in getattr(session.config, "_loopx_absent_routes", [])
        if os.path.lexists(root)
    ]
    if not created:
        temporary = session.config.stash.get(_WORKER_TEMP, None)
        if (temporary is not None and exitstatus == pytest.ExitCode.OK
                and session.config.stash[_TEMP_RETENTION] != "all"):
            _remove_temp_dir(temporary)
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "loopx default runtime route leak", red=True)
        reporter.write_line(
            f"Tests created real default runtime routes {', '.join(map(str, created))}; "
            "isolate the writer."
        )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_addoption(parser) -> None:
    parser.getgroup("loopx-tests").addoption(
        "--loopx-min-free-tmp-mib",
        type=int,
        default=2048,
        help="Stop starting tests below this many MiB free on the pytest temporary filesystem (0 disables).",
    )
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
