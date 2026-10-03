from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from ..extensions.process_runtime import terminate_process_tree
from .planner import REPO_ROOT, build_catalog_canary_plan, flatten_catalog_canary_checks
from .smoke_profiles import list_smoke_suite_profiles, resolve_smoke_suite_profiles


CANARY_RUN_SCHEMA_VERSION = "catalog_canary_run_v0"
CANARY_SMOKE_SUITE_RUN_SCHEMA_VERSION = "canary_smoke_suite_run_v0"
CANARY_SMOKE_SUITE_PROFILE_SCHEMA_VERSION = "canary_smoke_suite_profiles_v0"
NO_WRITE_ARGS_BY_SCRIPT = {
    "canary-promotion-readiness-smoke.py": [
        "--no-write-evidence",
        "--dashboard-mode=skip",
    ],
    "dashboard-demo-readiness-smoke.py": ["--skip-browser"],
}
EXPLICIT_GROUPED_SMOKES = {
    "canary-promotion-readiness-smoke.py",
    "dashboard-demo-readiness-smoke.py",
}
GIT_REQUIRED_SCRIPTS = {
    "control-plane-maintainability-ratchet-smoke.py": (
        "requires git ls-files so untracked local artifacts do not enter the report"
    ),
    "repository-hygiene-smoke.py": (
        "requires git ls-files and git tag to validate tracked hygiene files and release history"
    ),
}
SERIAL_SMOKE_SCRIPTS = {
    "issue-fix-acceptance-loop-smoke.py": (
        "creates temporary git repositories and can race host git cleanup when "
        "run beside other git-heavy smokes"
    ),
    "issue-fix-workflow-e2e-smoke.py": (
        "creates a temporary git repository and can race host git cleanup when "
        "run beside other git-heavy smokes"
    ),
}
PYTHON_BINARIES = {"python", "python3"}
NODE_BINARIES = {"node"}
SHELL_TOKENS = {"&&", "||", ";", "|", ">", "<", ">>", "2>", "2>>"}
SMOKE_SUITE_CHOICES = {"default-public", "full-public", "catalog-plan"}
ProgressCallback = Callable[[dict[str, Any]], None]


def build_canary_smoke_suite_profiles() -> dict[str, Any]:
    """List named smoke-suite profiles without reading catalog plans or running checks."""

    profiles = list_smoke_suite_profiles()
    return {
        "ok": True,
        "schema_version": CANARY_SMOKE_SUITE_PROFILE_SCHEMA_VERSION,
        "source": "loopx.canary.smoke_profiles.SMOKE_SUITE_PROFILE_MANIFEST",
        "profile_count": len(profiles),
        "profiles": profiles,
        "executes_checks": False,
        "writes_evidence": False,
        "creates_runtime_contract": False,
        "note": (
            "Named smoke-suite profiles expand to the same canary smoke-suite "
            "runner payload used by CLI, pytest facade, and automation. Unknown "
            "profile ids continue to route to catalog-profile selection."
        ),
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize_canary_command(command: str) -> dict[str, Any]:
    """Parse a planner command into a shell-free, repository-local argv."""

    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return {
            "ok": False,
            "command": command,
            "reason": f"could not parse command: {exc}",
            "argv": [],
        }
    if len(parts) < 2:
        return {
            "ok": False,
            "command": command,
            "reason": "command must include an interpreter and examples script",
            "argv": [],
        }
    if any("\n" in part or part in SHELL_TOKENS for part in parts):
        return {
            "ok": False,
            "command": command,
            "reason": "shell control tokens are not allowed in canary commands",
            "argv": [],
        }

    interpreter = parts[0]
    script = (REPO_ROOT / parts[1]).resolve()
    examples_root = (REPO_ROOT / "examples").resolve()
    if not _is_relative_to(script, examples_root):
        return {
            "ok": False,
            "command": command,
            "reason": "canary runner only executes repository-local examples",
            "argv": [],
        }
    if interpreter in PYTHON_BINARIES and script.suffix == ".py":
        argv = [sys.executable, str(script), *parts[2:]]
    elif interpreter in NODE_BINARIES and script.suffix == ".mjs":
        argv = [interpreter, str(script), *parts[2:]]
    else:
        return {
            "ok": False,
            "command": command,
            "reason": "only python examples/*.py and node examples/*.mjs commands are allowed",
            "argv": [],
        }

    injected_args = [
        arg
        for arg in NO_WRITE_ARGS_BY_SCRIPT.get(script.name, [])
        if arg not in argv
    ]
    if injected_args:
        argv.extend(injected_args)
    return {
        "ok": True,
        "command": command,
        "argv": argv,
        "display_argv": _display_argv(argv),
        "injected_args": injected_args,
        "script": str(script.relative_to(REPO_ROOT)),
    }


def _display_argv(argv: list[str]) -> list[str]:
    displayed = list(argv)
    if displayed and Path(displayed[0]).resolve() == Path(sys.executable).resolve():
        displayed[0] = "python3"
    for index, value in enumerate(displayed[1:], start=1):
        path = Path(value)
        if path.is_absolute() and _is_relative_to(path.resolve(), REPO_ROOT.resolve()):
            displayed[index] = str(path.resolve().relative_to(REPO_ROOT.resolve()))
    return displayed


def _tracked_change_paths() -> tuple[bool, list[str], str]:
    git_ok, git_detail = _git_worktree_probe(REPO_ROOT)
    if not git_ok:
        return False, [], git_detail

    paths: set[str] = set()
    stderr_parts: list[str] = []
    ok = True
    for args in (["diff", "--name-only"], ["diff", "--name-only", "--cached"]):
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            check=False,
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            ok = False
            stderr_parts.append(f"git_diff_failed: {completed.stderr[-400:]}")
            continue
        paths.update(line.strip() for line in completed.stdout.splitlines() if line.strip())
    return ok, sorted(paths), "\n".join(part for part in stderr_parts if part)


def _git_worktree_probe(root: Path) -> tuple[bool, str]:
    worktree_probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        check=False,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if worktree_probe.returncode != 0 or worktree_probe.stdout.strip() != "true":
        detail = (
            worktree_probe.stderr
            or worktree_probe.stdout
            or "repository root is not a git worktree"
        ).strip()
        return False, f"not_a_git_worktree: {detail[-400:]}"
    return True, ""


def _git_required_skip(normalized: dict[str, Any]) -> dict[str, Any] | None:
    script_name = Path(str(normalized.get("script") or "")).name
    reason = GIT_REQUIRED_SCRIPTS.get(script_name)
    if not reason:
        return None
    git_ok, git_detail = _git_worktree_probe(REPO_ROOT)
    if git_ok:
        return None
    return {
        "status": "skipped_git_required",
        "ok": True,
        "git_required": True,
        "skip_reason": reason,
        "git_status_ok": False,
        "git_status_unavailable_reason": git_detail,
    }


def _serial_smoke_reason(check: dict[str, Any]) -> str:
    normalized = check.get("normalized")
    if not isinstance(normalized, dict):
        normalized = normalize_canary_command(str(check.get("command") or ""))
    script_name = Path(str(normalized.get("script") or "")).name
    return SERIAL_SMOKE_SCRIPTS.get(script_name, "")


def _restore_tracked_paths(paths: list[str]) -> dict[str, Any]:
    if not paths:
        return {"ok": True, "restored_paths": []}
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "restore", "--staged", "--worktree", "--", *paths],
        check=False,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "restored_paths": paths if completed.returncode == 0 else [],
        "stderr_tail": completed.stderr[-800:],
    }


def _run_check(
    check: dict[str, Any],
    *,
    timeout_seconds: float,
    check_index: int | None = None,
    check_count: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_canary_command(str(check.get("command") or ""))
    result = {**check, "normalized": normalized}
    if check_index is not None and check_count is not None:
        result.update({"check_index": check_index, "check_count": check_count})
    if not normalized.get("ok"):
        result.update({"status": "skipped", "ok": False, "reason": normalized.get("reason")})
        return result
    git_required_skip = _git_required_skip(normalized)
    if git_required_skip:
        result.update(git_required_skip)
        return result

    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="loopx-smoke-") as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            env = {**os.environ, "LOOPX_USAGE_PING": "0", "HOME": str(home),
                   "CODEX_HOME": str(home / ".codex")}
            for name in ("LOOPX_RUNTIME_ROOT", "LOOPX_REGISTRY"):
                env.pop(name, None)
            for name in ("TMPDIR", "TEMP", "TMP"):
                env[name] = str(root)
            for name, directory in (
                ("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                ("XDG_STATE_HOME", ".local/state"), ("XDG_CACHE_HOME", ".cache"),
                ("OPENCODE_CONFIG_DIR", ".config/opencode"),
            ):
                env[name] = str(home / directory)
            try:
                with subprocess.Popen(
                    normalized["argv"], cwd=REPO_ROOT, env=env,
                    text=True, encoding="utf-8", errors="replace",
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=os.name == "posix",
                ) as process:
                    try:
                        stdout, stderr = process.communicate(timeout=timeout_seconds)
                    except BaseException:
                        # Deadline/cancellation must stop the owned children
                        # before deleting their fixture, even after leader exit.
                        # Reuse OS transport; keep the caller's existing budget.
                        terminate_process_tree(process, grace_seconds=0)
                        raise
                    completed = subprocess.CompletedProcess(
                        normalized["argv"], process.returncode, stdout, stderr,
                    )
            finally:
                # Each fixture owns its Effect process as well as its data.
                # Use the existing shutdown owner in the same temporary scope.
                if any(root.glob("loopx-effect-runtime-*/runtime-*.json")):
                    subprocess.run(
                        [sys.executable, "-c", "from loopx.control_plane.effect_runtime "
                         "import restart_effect_runtime; r=restart_effect_runtime(); "
                         "raise SystemExit(r['status'] == 'shutdown_pending')"],
                        cwd=REPO_ROOT, env=env, check=True, timeout=15,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
    except subprocess.TimeoutExpired as exc:
        result.update(
            {
                "status": "timed_out",
                "ok": False,
                "returncode": None,
                "duration_seconds": round(time.monotonic() - started, 3),
                "stdout_tail": (exc.stdout or "")[-800:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": (exc.stderr or "")[-800:] if isinstance(exc.stderr, str) else "",
            }
        )
        return result

    result.update(
        {
            "status": "passed" if completed.returncode == 0 else "failed",
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": completed.stdout[-800:],
            "stderr_tail": completed.stderr[-800:],
        }
    )
    return result


def run_canary_smoke_check(
    check: dict[str, Any],
    *,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Execute one normalized canary smoke-suite check through the runner contract."""

    return _run_check(check, timeout_seconds=max(1.0, timeout_seconds))


def _smoke_script_relative(script: Path) -> str:
    return script.relative_to(REPO_ROOT).as_posix()


def _smoke_script_filter_keys(script: Path) -> set[str]:
    examples_root = REPO_ROOT / "examples"
    return {
        script.name,
        _smoke_script_relative(script),
        script.relative_to(examples_root).as_posix(),
    }


def _smoke_script_check(script: Path, *, source: str = "suite") -> dict[str, Any]:
    return {
        "source": source,
        "profile_id": "smoke-suite",
        "profile_title": "Smoke suite",
        "tier": "default",
        "command": f"python3 {_smoke_script_relative(script)}",
        "reason": "tracked public smoke script",
    }


def _normalize_script_filter(script: str) -> str:
    value = script.strip().replace("\\", "/")
    if not value:
        return ""
    path = Path(value)
    if path.parts and path.parts[0] == "examples":
        if path.suffix and len(path.parts) == 2:
            return path.name
        return path.as_posix()
    if path.suffix and len(path.parts) > 1:
        return path.as_posix()
    return path.name if path.suffix else value


def _matches_modules(script: Path, modules: list[str]) -> bool:
    if not modules:
        return True
    examples_relative = script.relative_to(REPO_ROOT / "examples").as_posix().lower()
    haystack = f"{script.name.lower()} {examples_relative}"
    stem_tokens = {
        token for token in re.split(r"[-_./]+", examples_relative.removesuffix(script.suffix)) if token
    }
    for module in modules:
        needle = module.strip().lower()
        if not needle:
            continue
        needle_variants = {
            needle,
            needle.replace("-", "_"),
            needle.replace("_", "-"),
        }
        needle_tokens = {
            token for token in re.split(r"[-_./]+", needle) if token
        }
        if (
            any(variant in haystack or variant in stem_tokens for variant in needle_variants)
            or (needle_tokens and needle_tokens.issubset(stem_tokens))
        ):
            return True
    return False


def _discover_smoke_suite_checks(
    *,
    suite: str,
    modules: list[str] | None = None,
    exclude_modules: list[str] | None = None,
    scripts: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_suite = suite if suite in SMOKE_SUITE_CHOICES else "default-public"
    modules = list(modules or [])
    exclude_modules = list(exclude_modules or [])
    requested_scripts = {
        script for script in (_normalize_script_filter(item) for item in (scripts or [])) if script
    }
    all_scripts = sorted(path for path in (REPO_ROOT / "examples").rglob("*-smoke.py") if path.is_file())
    if normalized_suite == "default-public":
        all_scripts = [
            script for script in all_scripts
            if script.name not in EXPLICIT_GROUPED_SMOKES
        ]
    selected: list[Path] = []
    missing_scripts = set(requested_scripts)
    for script in all_scripts:
        script_filter_keys = _smoke_script_filter_keys(script)
        if requested_scripts and requested_scripts.isdisjoint(script_filter_keys):
            continue
        if not _matches_modules(script, modules):
            continue
        if exclude_modules and _matches_modules(script, exclude_modules):
            missing_scripts.difference_update(script_filter_keys)
            continue
        selected.append(script)
        missing_scripts.difference_update(script_filter_keys)
    warnings = [
        {
            "kind": "unknown_script",
            "script": script,
            "message": "requested script was not found in selected smoke suite",
        }
        for script in sorted(missing_scripts)
    ]
    return [_smoke_script_check(script) for script in selected], warnings


def _dedupe_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for check in checks:
        command = str(check.get("command") or "")
        if not command or command in seen:
            continue
        seen.add(command)
        deduped.append(check)
    return deduped


def _coerce_parallel_jobs(value: int | None) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _progress_check_started(
    progress_callback: ProgressCallback | None,
    *,
    index: int,
    total: int,
    check: dict[str, Any],
) -> None:
    if not progress_callback:
        return
    normalized_check = normalize_canary_command(str(check.get("command") or ""))
    progress_callback(
        {
            "schema_version": "canary_smoke_suite_progress_v0",
            "event": "check_started",
            "check_index": index,
            "check_count": total,
            "command": " ".join(
                str(part) for part in normalized_check.get("display_argv") or []
            )
            or str(check.get("command") or ""),
        }
    )


def _progress_check_finished(
    progress_callback: ProgressCallback | None,
    *,
    index: int,
    total: int,
    result: dict[str, Any],
) -> None:
    if not progress_callback:
        return
    progress_callback(
        {
            "schema_version": "canary_smoke_suite_progress_v0",
            "event": "check_finished",
            "check_index": index,
            "check_count": total,
            "status": result.get("status"),
            "ok": bool(result.get("ok")),
            "duration_seconds": result.get("duration_seconds"),
        }
    )


def build_canary_smoke_suite_run(
    *,
    suite: str = "default-public",
    modules: list[str] | None = None,
    exclude_modules: list[str] | None = None,
    scripts: list[str] | None = None,
    catalog_path: Path | None = None,
    changed_files: list[str] | None = None,
    surfaces: list[str] | None = None,
    families: list[str] | None = None,
    profiles: list[str] | None = None,
    include_deep_checks: bool = False,
    max_checks_per_family: int = 3,
    max_checks_per_profile: int = 3,
    offset: int = 0,
    limit: int = 0,
    execute: bool = True,
    timeout_seconds: float = 120.0,
    fail_fast: bool = False,
    allow_tracked_side_effects: bool = False,
    parallel_jobs: int = 1,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Build and optionally execute a continue-on-failure smoke suite.

    This runner is intentionally bounded to repository-local `examples/**/*-smoke.py`
    commands plus catalog-plan checks. It gives maintainers a full regression
    sweep without hiding the smaller profile/module loops used while developing.
    """

    modules = list(modules or [])
    exclude_modules = list(exclude_modules or [])
    scripts = list(scripts or [])
    families = list(families or [])
    profiles = list(profiles or [])
    changed_files = list(changed_files or [])
    surfaces = list(surfaces or [])
    profile_resolution = resolve_smoke_suite_profiles(
        suite=suite,
        suite_choices=SMOKE_SUITE_CHOICES,
        modules=modules,
        exclude_modules=exclude_modules,
        profiles=profiles,
    )
    normalized_suite = str(profile_resolution["suite"])
    modules = list(profile_resolution["modules"])
    exclude_modules = list(profile_resolution["exclude_modules"])
    smoke_profiles = list(profile_resolution["smoke_profiles"])
    catalog_profiles = list(profile_resolution["catalog_profiles"])
    profile_expansions = list(profile_resolution["profile_expansions"])
    catalog_selector_requested = bool(families or catalog_profiles or changed_files or surfaces)
    suite_requested = normalized_suite != "catalog-plan"
    if (
        catalog_selector_requested
        and not modules
        and not scripts
        and not smoke_profiles
        and normalized_suite == "default-public"
    ):
        suite_requested = False

    selected: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    plan: dict[str, Any] | None = None
    if suite_requested:
        suite_checks, suite_warnings = _discover_smoke_suite_checks(
            suite=normalized_suite,
            modules=modules,
            exclude_modules=exclude_modules,
            scripts=scripts,
        )
        selected.extend(suite_checks)
        warnings.extend(suite_warnings)
    if catalog_selector_requested or normalized_suite == "catalog-plan":
        plan = build_catalog_canary_plan(
            catalog_path=catalog_path,
            changed_files=changed_files,
            surfaces=surfaces,
            families=families,
            profiles=catalog_profiles,
            include_deep_checks=include_deep_checks,
            max_checks_per_family=max_checks_per_family,
            max_checks_per_profile=max_checks_per_profile,
        )
        catalog_checks = flatten_catalog_canary_checks(plan)
        if catalog_profiles:
            # Explicit catalog profiles are targeted validation, so do not let
            # a global limit get consumed first by broader named-suite matches.
            selected = [*catalog_checks, *selected]
        else:
            selected.extend(catalog_checks)

    selected = _dedupe_checks(selected)
    matched_check_count = len(selected)
    normalized_offset = max(0, int(offset or 0))
    if normalized_offset:
        selected = selected[normalized_offset:]
    if limit and limit > 0:
        selected = selected[:limit]
    normalized = [
        {**check, "normalized": normalize_canary_command(str(check.get("command") or ""))}
        for check in selected
    ]

    results: list[dict[str, Any]] = []
    suite_guard_failures: list[dict[str, Any]] = []
    requested_parallel_jobs = _coerce_parallel_jobs(parallel_jobs)
    effective_parallel_jobs = 1
    serial_check_count = 0
    side_effect_guard: dict[str, Any] = {
        "schema_version": "canary_smoke_suite_side_effect_guard_v0",
        "tracked_side_effects_allowed": allow_tracked_side_effects,
        "enforced": False,
        "enforcement_reason": "not_executed",
        "clean_start": None,
        "tracked_before": [],
        "tracked_side_effects": [],
        "auto_restored": False,
    }
    if execute:
        git_ok, tracked_before, git_stderr = _tracked_change_paths()
        clean_start = git_ok and not tracked_before
        if allow_tracked_side_effects:
            enforcement_reason = "tracked_side_effects_explicitly_allowed"
        elif not git_ok:
            enforcement_reason = "git_worktree_unavailable"
        else:
            enforcement_reason = "tracked_side_effect_guard_active"
        side_effect_guard.update(
            {
                "git_status_ok": git_ok,
                "clean_start": clean_start,
                "tracked_before": tracked_before,
                "enforced": bool(git_ok and not allow_tracked_side_effects),
                "enforcement_reason": enforcement_reason,
            }
        )
        if git_stderr:
            side_effect_guard["git_status_unavailable_reason"] = git_stderr[-800:]
        serial_indexes = {
            index
            for index, check in enumerate(selected, start=1)
            if _serial_smoke_reason(check)
        }
        serial_check_count = len(serial_indexes)
        parallel_check_count = len(selected) - serial_check_count
        if selected and not fail_fast and parallel_check_count:
            effective_parallel_jobs = min(requested_parallel_jobs, parallel_check_count)
        if effective_parallel_jobs <= 1:
            for index, check in enumerate(selected, start=1):
                _progress_check_started(
                    progress_callback,
                    index=index,
                    total=len(selected),
                    check=check,
                )
                result = _run_check(
                    check,
                    timeout_seconds=max(1.0, timeout_seconds),
                    check_index=index,
                    check_count=len(selected),
                )
                if side_effect_guard["enforced"]:
                    after_ok, tracked_after, after_stderr = _tracked_change_paths()
                    side_effects = sorted(set(tracked_after) - set(tracked_before))
                    if side_effects:
                        result.update(
                            {
                                "ok": False,
                                "status": "failed_tracked_side_effect",
                                "tracked_side_effects": side_effects,
                            }
                        )
                        if clean_start:
                            restore = _restore_tracked_paths(side_effects)
                            result["tracked_side_effect_restore"] = restore
                            side_effect_guard["auto_restored"] = (
                                bool(side_effect_guard.get("auto_restored"))
                                or bool(restore.get("ok"))
                            )
                    if not after_ok and after_stderr:
                        result["tracked_side_effect_stderr_tail"] = after_stderr[-800:]
                _progress_check_finished(
                    progress_callback,
                    index=index,
                    total=len(selected),
                    result=result,
                )
                results.append(result)
                if fail_fast and not result.get("ok"):
                    break
        else:
            indexed_results: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=effective_parallel_jobs) as executor:
                futures = {}
                for index, check in enumerate(selected, start=1):
                    if index in serial_indexes:
                        continue
                    _progress_check_started(
                        progress_callback,
                        index=index,
                        total=len(selected),
                        check=check,
                    )
                    future = executor.submit(
                        _run_check,
                        check,
                        timeout_seconds=max(1.0, timeout_seconds),
                        check_index=index,
                        check_count=len(selected),
                    )
                    futures[future] = (index, check)
                for future in as_completed(futures):
                    index, check = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            **check,
                            "status": "failed",
                            "ok": False,
                            "reason": f"smoke runner exception: {exc}",
                            "returncode": None,
                            "duration_seconds": None,
                            "check_index": index,
                            "check_count": len(selected),
                        }
                    indexed_results[index] = result
                    _progress_check_finished(
                        progress_callback,
                        index=index,
                        total=len(selected),
                        result=result,
                    )
            for index, check in enumerate(selected, start=1):
                if index not in serial_indexes:
                    continue
                _progress_check_started(
                    progress_callback,
                    index=index,
                    total=len(selected),
                    check=check,
                )
                result = _run_check(
                    check,
                    timeout_seconds=max(1.0, timeout_seconds),
                    check_index=index,
                    check_count=len(selected),
                )
                result["serial_execution_required"] = True
                result["serial_execution_reason"] = _serial_smoke_reason(check)
                indexed_results[index] = result
                _progress_check_finished(
                    progress_callback,
                    index=index,
                    total=len(selected),
                    result=result,
                )
            results = [indexed_results[index] for index in sorted(indexed_results)]
        if side_effect_guard["enforced"]:
            final_ok, tracked_after, final_stderr = _tracked_change_paths()
            side_effects = sorted(set(tracked_after) - set(tracked_before))
            side_effect_guard.update(
                {
                    "git_status_final_ok": final_ok,
                    "tracked_after": tracked_after,
                    "tracked_side_effects": side_effects,
                }
            )
            if final_stderr:
                side_effect_guard["git_status_final_stderr_tail"] = final_stderr[-800:]
            if effective_parallel_jobs > 1 and side_effects:
                guard_failure = {
                    "schema_version": "canary_smoke_suite_guard_failure_v0",
                    "status": "failed_tracked_side_effect",
                    "ok": False,
                    "reason": (
                        "parallel smoke-suite run produced tracked side effects; "
                        "rerun with --jobs 1 to attribute the script precisely"
                    ),
                    "tracked_side_effects": side_effects,
                }
                if clean_start:
                    restore = _restore_tracked_paths(side_effects)
                    guard_failure["tracked_side_effect_restore"] = restore
                    side_effect_guard["auto_restored"] = (
                        bool(side_effect_guard.get("auto_restored")) or bool(restore.get("ok"))
                    )
                suite_guard_failures.append(guard_failure)

    display_items = results if execute else normalized
    failures = [item for item in results if not item.get("ok")] + suite_guard_failures
    git_required_skips = [
        item for item in results if item.get("status") == "skipped_git_required"
    ]
    timed_out = [item for item in results if item.get("status") == "timed_out"]
    side_effect_failures = [
        item for item in results if item.get("status") == "failed_tracked_side_effect"
    ] + suite_guard_failures
    unsafe = [
        item
        for item in normalized
        if not isinstance(item.get("normalized"), dict) or not item["normalized"].get("ok")
    ]
    ok = not failures and (execute or not unsafe) and not warnings
    return {
        "ok": ok,
        "schema_version": CANARY_SMOKE_SUITE_RUN_SCHEMA_VERSION,
        "suite": normalized_suite,
        "repo_root": str(REPO_ROOT),
        "dry_run": not execute,
        "executes_checks": execute,
        "writes_evidence": bool(allow_tracked_side_effects),
        "creates_runtime_contract": False,
        "timeout_seconds": max(1.0, timeout_seconds),
        "fail_fast": fail_fast,
        "parallel_jobs": requested_parallel_jobs,
        "effective_parallel_jobs": effective_parallel_jobs,
        "serial_check_count": serial_check_count,
        "side_effect_guard": side_effect_guard,
        "offset": normalized_offset,
        "limit": max(0, limit),
        "matched_check_count": matched_check_count,
        "selected_check_count": len(selected),
        "executed_check_count": len(results),
        "failure_count": len(failures),
        "git_required_skip_count": len(git_required_skips),
        "timeout_count": len(timed_out),
        "tracked_side_effect_failure_count": len(side_effect_failures),
        "unsafe_command_count": len(unsafe),
        "warning_count": len(warnings),
        "warnings": warnings,
        "selection_inputs": {
            "suite": normalized_suite,
            "modules": modules,
            "exclude_modules": exclude_modules,
            "scripts": scripts,
            "changed_files": changed_files,
            "surfaces": surfaces,
            "families": families,
            "profiles": profiles,
            "smoke_profiles": smoke_profiles,
            "catalog_profiles": catalog_profiles,
            "profile_expansions": profile_expansions,
            "include_deep_checks": include_deep_checks,
            "max_checks_per_family": max_checks_per_family,
            "max_checks_per_profile": max_checks_per_profile,
            "offset": normalized_offset,
            "limit": max(0, limit),
            "parallel_jobs": requested_parallel_jobs,
            "effective_parallel_jobs": effective_parallel_jobs,
            "serial_check_count": serial_check_count,
        },
        "catalog_plan": {
            "schema_version": plan.get("schema_version"),
            "planned_check_count": len(flatten_catalog_canary_checks(plan)),
            "profiles": plan.get("profiles", []),
            "domain_profiles": plan.get("domain_profiles", []),
        } if isinstance(plan, dict) else None,
        "selected_checks": display_items,
        "failures": failures,
        "git_required_skips": git_required_skips,
        "note": (
            "Smoke-suite run executes repository-local public smoke scripts with "
            "shell-free argv, per-check timeouts, and continue-on-failure reporting. "
            "Use --suite full-public for a full sweep, --module/--script for local "
            "development, recognized --profile values for named smoke-suite profiles, "
            "or catalog selectors such as --profile for canary-plan modules. Use "
            "--offset with --limit to sweep large profiles in stable windows."
        ),
    }


def build_catalog_canary_run(
    *,
    catalog_path: Path | None = None,
    changed_files: list[str] | None = None,
    surfaces: list[str] | None = None,
    families: list[str] | None = None,
    profiles: list[str] | None = None,
    include_deep_checks: bool = False,
    max_checks_per_family: int = 3,
    max_checks_per_profile: int = 3,
    check_limit: int = 3,
    execute: bool = True,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    plan = build_catalog_canary_plan(
        catalog_path=catalog_path,
        changed_files=changed_files,
        surfaces=surfaces,
        families=families,
        profiles=profiles,
        include_deep_checks=include_deep_checks,
        max_checks_per_family=max_checks_per_family,
        max_checks_per_profile=max_checks_per_profile,
    )
    planned = flatten_catalog_canary_checks(plan)
    selected = planned[: max(0, check_limit)]
    normalized = [
        {**check, "normalized": normalize_canary_command(str(check.get("command") or ""))}
        for check in selected
    ]
    results = []
    if execute:
        results = [
            _run_check(check, timeout_seconds=max(1.0, timeout_seconds))
            for check in selected
        ]

    failures = [item for item in results if not item.get("ok")]
    git_required_skips = [
        item for item in results if item.get("status") == "skipped_git_required"
    ]
    unsafe = [
        item
        for item in normalized
        if not isinstance(item.get("normalized"), dict) or not item["normalized"].get("ok")
    ]
    ok = not failures and (execute or not unsafe)
    return {
        "ok": ok,
        "schema_version": CANARY_RUN_SCHEMA_VERSION,
        "plan_schema_version": plan.get("schema_version"),
        "source": plan.get("source"),
        "dry_run": not execute,
        "executes_checks": execute,
        "writes_evidence": False,
        "creates_runtime_contract": False,
        "check_limit": max(0, check_limit),
        "timeout_seconds": max(1.0, timeout_seconds),
        "planned_check_count": len(planned),
        "selected_check_count": len(selected),
        "executed_check_count": len(results),
        "failure_count": len(failures),
        "git_required_skip_count": len(git_required_skips),
        "unsafe_command_count": len(unsafe),
        "selection_inputs": plan.get("selection_inputs"),
        "profiles": plan.get("profiles", []),
        "domain_profiles": plan.get("domain_profiles", []),
        "selected_checks": normalized if not execute else results,
        "git_required_skips": git_required_skips,
        "note": (
            "Canary run consumes the catalog plan and executes only selected "
            "repository-local examples with shell-free argv. It never writes "
            "promotion evidence or creates runtime contracts."
        ),
    }


def render_catalog_canary_run_markdown(payload: dict[str, Any]) -> str:
    mode = "execute" if payload.get("executes_checks") else "preview"
    lines = [
        "# Catalog Canary Run",
        "",
        f"- mode: `{mode}`",
        f"- ok: `{str(payload.get('ok')).lower()}`",
        f"- source: `{payload.get('source')}`",
        f"- planned_checks: `{payload.get('planned_check_count')}`",
        f"- selected_checks: `{payload.get('selected_check_count')}`",
        f"- executed_checks: `{payload.get('executed_check_count')}`",
        f"- git_required_skips: `{payload.get('git_required_skip_count')}`",
        "- writes_evidence: `false`",
        "- creates_runtime_contract: `false`",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    for check in payload.get("selected_checks", []):
        if not isinstance(check, dict):
            continue
        normalized = check.get("normalized") if isinstance(check.get("normalized"), dict) else {}
        command = " ".join(str(part) for part in normalized.get("display_argv") or [])
        status = check.get("status") or ("ready" if normalized.get("ok") else "skipped")
        lines.extend(
            [
                f"## {check.get('profile_title') or check.get('profile_id')}",
                f"- status: `{status}`",
                f"- tier: `{check.get('tier')}`",
                f"- command: `{command or check.get('command')}`",
                f"- reason: {check.get('reason')}",
            ]
        )
        if check.get("injected_args") or normalized.get("injected_args"):
            lines.append(
                "- injected_args: `"
                + ", ".join(str(arg) for arg in normalized.get("injected_args") or check.get("injected_args") or [])
                + "`"
            )
        if check.get("returncode") is not None:
            lines.append(f"- returncode: `{check.get('returncode')}`")
        if check.get("skip_reason"):
            lines.append(f"- skip_reason: {check.get('skip_reason')}")
        if check.get("stderr_tail"):
            lines.append(f"- stderr_tail: `{str(check.get('stderr_tail')).strip()[-300:]}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_canary_smoke_suite_run_markdown(payload: dict[str, Any]) -> str:
    mode = "execute" if payload.get("executes_checks") else "preview"
    guard = payload.get("side_effect_guard")
    guard = guard if isinstance(guard, dict) else {}
    lines = [
        "# Canary Smoke Suite",
        "",
        f"- mode: `{mode}`",
        f"- ok: `{str(payload.get('ok')).lower()}`",
        f"- suite: `{payload.get('suite')}`",
        f"- matched_checks: `{payload.get('matched_check_count')}`",
        f"- offset: `{payload.get('offset')}`",
        f"- limit: `{payload.get('limit')}`",
        f"- selected_checks: `{payload.get('selected_check_count')}`",
        f"- executed_checks: `{payload.get('executed_check_count')}`",
        f"- parallel_jobs: `{payload.get('parallel_jobs')}`",
        f"- effective_parallel_jobs: `{payload.get('effective_parallel_jobs')}`",
        f"- serial_check_count: `{payload.get('serial_check_count')}`",
        f"- failures: `{payload.get('failure_count')}`",
        f"- git_required_skips: `{payload.get('git_required_skip_count')}`",
        f"- timeouts: `{payload.get('timeout_count')}`",
        f"- tracked_side_effect_failures: `{payload.get('tracked_side_effect_failure_count')}`",
        f"- warnings: `{payload.get('warning_count')}`",
        f"- writes_evidence: `{str(payload.get('writes_evidence')).lower()}`",
        "- creates_runtime_contract: `false`",
        f"- read_only_guard_enforced: `{str(guard.get('enforced')).lower()}`",
        f"- read_only_guard_reason: `{guard.get('enforcement_reason')}`",
        f"- read_only_guard_clean_start: `{str(guard.get('clean_start')).lower()}`",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    for warning in payload.get("warnings", []):
        if isinstance(warning, dict):
            lines.append(f"- warning: `{warning.get('kind')}` {warning.get('script')}: {warning.get('message')}")
    if payload.get("warnings"):
        lines.append("")
    for check in payload.get("selected_checks", []):
        if not isinstance(check, dict):
            continue
        normalized = check.get("normalized") if isinstance(check.get("normalized"), dict) else {}
        command = " ".join(str(part) for part in normalized.get("display_argv") or [])
        status = check.get("status") or ("ready" if normalized.get("ok") else "skipped")
        title = check.get("profile_title") or check.get("profile_id") or "smoke"
        lines.extend(
            [
                f"## {title}",
                f"- status: `{status}`",
                f"- source: `{check.get('source')}`",
                f"- command: `{command or check.get('command')}`",
            ]
        )
        if normalized.get("injected_args"):
            lines.append(
                "- injected_args: `"
                + ", ".join(str(arg) for arg in normalized.get("injected_args") or [])
                + "`"
            )
        if check.get("serial_execution_required"):
            lines.append(
                f"- serial_execution_reason: `{check.get('serial_execution_reason')}`"
            )
        if check.get("returncode") is not None:
            lines.append(f"- returncode: `{check.get('returncode')}`")
        if check.get("duration_seconds") is not None:
            lines.append(f"- duration_seconds: `{check.get('duration_seconds')}`")
        if check.get("skip_reason"):
            lines.append(f"- skip_reason: {check.get('skip_reason')}")
        if check.get("tracked_side_effects"):
            lines.append(
                "- tracked_side_effects: `"
                + ", ".join(str(path) for path in check.get("tracked_side_effects") or [])
                + "`"
            )
        restore = check.get("tracked_side_effect_restore")
        if isinstance(restore, dict):
            lines.append(f"- tracked_side_effect_restore_ok: `{str(restore.get('ok')).lower()}`")
        if check.get("stderr_tail"):
            lines.append(f"- stderr_tail: `{str(check.get('stderr_tail')).strip()[-300:]}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_canary_smoke_suite_profiles_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Canary Smoke Suite Profiles",
        "",
        f"- source: `{payload.get('source')}`",
        f"- profiles: `{payload.get('profile_count')}`",
        "- dry_run: `true`",
        "- executes_checks: `false`",
        "- writes_evidence: `false`",
        "- creates_runtime_contract: `false`",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    for profile in payload.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        lines.extend(
            [
                f"## {profile.get('id')}",
                f"- suite: `{profile.get('suite')}`",
                f"- modules: `{', '.join(profile.get('modules') or [])}`",
                f"- exclude_modules: `{', '.join(profile.get('exclude_modules') or [])}`",
                f"- description: {profile.get('description')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
