from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from ..capabilities.change_quality.receipt import verify_change_quality_receipt
from ..canary.planner import (
    build_catalog_canary_coverage_audit,
    build_catalog_canary_plan,
    build_catalog_canary_profiles,
    build_quality_surface_catalog_audit,
    render_catalog_canary_coverage_audit_markdown,
    render_catalog_canary_plan_markdown,
    render_catalog_canary_profiles_markdown,
)
from ..canary.quality_surface_catalog import (
    render_quality_surface_catalog_audit_markdown,
)
from ..canary.premerge import (
    PREMERGE_TIERS,
    apply_change_quality_verification,
    build_premerge_validation_gate,
    render_premerge_validation_gate_markdown,
)
from ..canary.runner import (
    build_canary_smoke_suite_profiles,
    build_canary_smoke_suite_run,
    build_catalog_canary_run,
    render_canary_smoke_suite_profiles_markdown,
    render_canary_smoke_suite_run_markdown,
    render_catalog_canary_run_markdown,
)
from ..canary.smoke_health import (
    build_smoke_fleet_health,
    render_smoke_fleet_health_markdown,
)
from ..control_plane.testing.release_commit_qualification import (
    render_exact_release_commit_qualification_markdown,
)
from ..history import load_registry
from ..paths import resolve_runtime_root
from .canary_release_qualification import (
    build_canary_release_qualification_payload,
    register_canary_release_qualification_command,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _run_git_name_only(repo_root: Path, args: list[str]) -> dict[str, object]:
    # `-z` frames each pathname on NUL. LF framing is only unambiguous while
    # `core.quotePath` escapes non-ASCII pathnames, and a repository may turn
    # that off, at which point a path can carry U+0085/U+2028/U+2029 raw and
    # `str.splitlines()` splits one path into two.
    command = ["git", "-C", str(repo_root), *args, "-z"]
    completed = subprocess.run(
        command,
        check=False,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    files = [name for name in completed.stdout.split("\0") if name]
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "changed_files": files,
        "stderr_tail": completed.stderr[-800:],
    }


def _resolve_git_repo_root(candidate: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        check=False,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    resolved = completed.stdout.strip()
    if completed.returncode == 0 and resolved:
        return Path(resolved).resolve()
    return candidate.resolve()


def collect_git_diff_changed_files(
    *,
    repo_root: Path,
    base_ref: str = "origin/main",
) -> dict[str, object]:
    """Collect committed, staged, unstaged, and untracked paths for canary selection."""

    base_ref = (base_ref or "origin/main").strip() or "origin/main"
    sources = {
        "base": _run_git_name_only(repo_root, ["diff", "--name-only", f"{base_ref}...HEAD"]),
        "staged": _run_git_name_only(repo_root, ["diff", "--name-only", "--cached"]),
        "unstaged": _run_git_name_only(repo_root, ["diff", "--name-only"]),
        "untracked": _run_git_name_only(repo_root, ["ls-files", "--others", "--exclude-standard"]),
    }
    changed_files = _dedupe_preserving_order(
        [
            file
            for source in sources.values()
            for file in (source.get("changed_files") or [])
            if isinstance(file, str)
        ]
    )
    successful_sources = [
        name for name, source in sources.items()
        if source.get("ok")
    ]
    warnings = [
        {
            "source": name,
            "returncode": source.get("returncode"),
            "stderr_tail": source.get("stderr_tail"),
        }
        for name, source in sources.items()
        if not source.get("ok")
    ]
    return {
        "ok": bool(successful_sources),
        "base_ref": base_ref,
        "repo_root": str(repo_root),
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "successful_sources": successful_sources,
        "warnings": warnings,
    }


def _resolve_canary_changed_files(args: argparse.Namespace) -> tuple[list[str], dict[str, object] | None]:
    changed_files = list(args.changed_file or [])
    git_diff_selector = None
    if bool(getattr(args, "from_git_diff", False)):
        repo_root = _resolve_git_repo_root(Path.cwd())
        git_diff_selector = collect_git_diff_changed_files(
            repo_root=repo_root,
            base_ref=str(getattr(args, "git_diff_base", "origin/main") or "origin/main"),
        )
        changed_files.extend(
            file
            for file in (git_diff_selector.get("changed_files") or [])
            if isinstance(file, str)
        )
    return _dedupe_preserving_order(changed_files), git_diff_selector


def _attach_selector_sources(
    payload: dict[str, object],
    *,
    git_diff_selector: dict[str, object] | None,
) -> None:
    if git_diff_selector is None:
        return
    payload["selector_sources"] = {"git_diff": git_diff_selector}


def _print_smoke_suite_progress(event: dict[str, object]) -> None:
    kind = str(event.get("event") or "")
    section = str(event.get("section") or "")
    section_prefix = f"{section} " if section else ""
    index = event.get("check_index")
    total = event.get("check_count")
    if kind == "premerge_started":
        tier = str(event.get("tier") or "")
        changed = event.get("changed_file_count")
        surfaces = ", ".join(str(item) for item in event.get("surfaces") or [])
        print(
            f"[loopx canary] premerge start: tier={tier} "
            f"changed_files={changed} surfaces={surfaces or '-'}",
            file=sys.stderr,
            flush=True,
        )
    elif kind == "premerge_finished":
        status = str(event.get("status") or "")
        failures = event.get("failure_count")
        selected = event.get("selected_check_count")
        print(
            f"[loopx canary] premerge done: {status} selected={selected} failures={failures}",
            file=sys.stderr,
            flush=True,
        )
    elif kind == "section_started":
        selected_hint = event.get("selected_hint")
        hint = f" selected_hint={selected_hint}" if selected_hint is not None else ""
        print(
            f"[loopx canary] start {section_prefix.strip() or 'section'}{hint}",
            file=sys.stderr,
            flush=True,
        )
    elif kind == "section_finished":
        status = str(event.get("status") or "")
        selected = event.get("selected_check_count")
        executed = event.get("executed_check_count")
        failures = event.get("failure_count")
        print(
            f"[loopx canary] done {section_prefix.strip() or 'section'}: {status} "
            f"selected={selected} executed={executed} failures={failures}",
            file=sys.stderr,
            flush=True,
        )
    elif kind == "check_started":
        command = str(event.get("command") or "")
        print(
            f"[loopx canary] start {section_prefix}{index}/{total}: {command}",
            file=sys.stderr,
            flush=True,
        )
    elif kind == "check_finished":
        status = str(event.get("status") or "")
        duration = event.get("duration_seconds")
        print(
            f"[loopx canary] done {section_prefix}{index}/{total}: {status} ({duration}s)",
            file=sys.stderr,
            flush=True,
        )


def _add_canary_selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--catalog",
        type=Path,
        help="Override the interaction-pattern catalog path.",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed path or glob-like surface. Repeat for multiple paths.",
    )
    parser.add_argument(
        "--from-git-diff",
        action="store_true",
        help=(
            "Append changed paths from git diff against --git-diff-base plus "
            "staged, unstaged, and untracked working-tree changes."
        ),
    )
    parser.add_argument(
        "--git-diff-base",
        default="origin/main",
        help="Base ref for --from-git-diff committed changes. Defaults to origin/main.",
    )
    parser.add_argument(
        "--surface",
        action="append",
        default=[],
        help="Changed control-plane or product surface. Repeat for multiple surfaces.",
    )
    parser.add_argument(
        "--family",
        action="append",
        default=[],
        help="Force-select a catalog family such as 'Work Routing'. Repeat for multiple families.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=[],
        help=(
            "Force-select a current-repo catalog profile, or a smoke-suite profile "
            "when used with `canary smoke-suite`, such as core-control-plane. "
            "Repeat for multiple profiles."
        ),
    )
    parser.add_argument(
        "--include-deep-checks",
        action="store_true",
        help="Include deep/browser/integration checks. Defaults stay bounded and fixture-level.",
    )
    parser.add_argument(
        "--max-checks-per-family",
        type=int,
        default=3,
        help="Maximum candidate checks to include per selected family.",
    )
    parser.add_argument(
        "--max-checks-per-profile",
        type=int,
        default=3,
        help="Maximum candidate checks to include per selected current-repo profile.",
    )


def register_canary_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    canary_parser = subparsers.add_parser(
        "canary",
        help="Plan or run catalog-informed canary profiles and smoke suites.",
    )
    canary_sub = canary_parser.add_subparsers(dest="canary_command", required=True)

    profiles_parser = canary_sub.add_parser(
        "profiles",
        help="List canary profiles derived from the interaction-pattern catalog matrix.",
    )
    add_subcommand_format(profiles_parser)
    profiles_parser.add_argument(
        "--catalog",
        type=Path,
        help="Override the interaction-pattern catalog path.",
    )

    smoke_profiles_parser = canary_sub.add_parser(
        "smoke-profiles",
        help="List named smoke-suite profiles used by CLI, pytest facade, and automation.",
    )
    add_subcommand_format(smoke_profiles_parser)

    smoke_health_parser = canary_sub.add_parser(
        "smoke-health",
        help="Audit smoke cadence, targeted ownership, duplicate candidates, and full-public receipts.",
    )
    add_subcommand_format(smoke_health_parser)
    smoke_health_parser.add_argument(
        "--receipt",
        action="append",
        type=Path,
        default=[],
        help=(
            "Read a full-public smoke-suite JSON receipt or directory of receipts. "
            "Repeatable; raw stdout/stderr is never copied into the health report."
        ),
    )
    smoke_health_parser.add_argument(
        "--slow-threshold-seconds",
        type=float,
        default=30.0,
        help="Report observed checks at or above this duration as review candidates.",
    )
    smoke_health_parser.add_argument(
        "--review-limit",
        type=int,
        default=20,
        help="Maximum examples retained in each compact review bucket.",
    )
    smoke_health_parser.add_argument(
        "--include-inventory",
        action="store_true",
        help="Include every smoke row in the explicit diagnostic cold path.",
    )

    register_canary_release_qualification_command(canary_sub, add_subcommand_format)

    plan_parser = canary_sub.add_parser(
        "plan",
        help="Select the smallest useful canary profiles for changed surfaces.",
    )
    add_subcommand_format(plan_parser)
    _add_canary_selector_args(plan_parser)

    run_parser = canary_sub.add_parser(
        "run",
        help="Execute selected fixture-level canary checks from a catalog plan.",
    )
    add_subcommand_format(run_parser)
    _add_canary_selector_args(run_parser)
    run_parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Preview normalized canary commands without running checks.",
    )
    run_parser.add_argument(
        "--check-limit",
        type=int,
        default=3,
        help="Maximum selected checks to execute or preview.",
    )
    run_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Per-check timeout for executed canaries.",
    )

    smoke_parser = canary_sub.add_parser(
        "smoke-suite",
        help="Run or preview public smoke scripts as a full suite, module subset, smoke profile, or catalog profile.",
    )
    add_subcommand_format(smoke_parser)
    _add_canary_selector_args(smoke_parser)
    smoke_parser.add_argument(
        "--suite",
        choices=["default-public", "full-public", "catalog-plan"],
        default="default-public",
        help=(
            "Smoke selection mode. default-public excludes explicit grouped checks; "
            "full-public includes every tracked examples/**/*-smoke.py; catalog-plan "
            "runs only checks selected by catalog/profile inputs."
        ),
    )
    smoke_parser.add_argument(
        "--module",
        action="append",
        default=[],
        help="Filter suite scripts by module token, such as quota, status, or canary. Repeatable.",
    )
    smoke_parser.add_argument(
        "--exclude-module",
        action="append",
        default=[],
        help=(
            "Exclude suite scripts by module token after positive --module/--script selection. "
            "Useful for bounded non-benchmark sweeps."
        ),
    )
    smoke_parser.add_argument(
        "--script",
        action="append",
        default=[],
        help="Run a specific examples/**/*-smoke.py script. Repeatable.",
    )
    smoke_parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Preview normalized smoke commands without running checks.",
    )
    smoke_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum selected checks to execute or preview. Defaults to all selected checks.",
    )
    smoke_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Skip this many matched checks before applying --limit. Useful for "
            "sweeping large smoke profiles in stable batches."
        ),
    )
    smoke_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Per-check timeout for executed smoke scripts.",
    )
    smoke_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop execution at the first failed or timed-out smoke.",
    )
    smoke_parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Run up to this many smoke scripts concurrently; --fail-fast stays serial.",
    )
    smoke_parser.add_argument(
        "--allow-tracked-side-effects",
        action="store_true",
        help=(
            "Allow selected smoke scripts to modify tracked files. Defaults to false; "
            "read-only smoke-suite runs fail and restore generated tracked changes "
            "when the suite starts from a clean tree."
        ),
    )
    smoke_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Do not print per-check smoke-suite progress to stderr during execution.",
    )

    coverage_parser = canary_sub.add_parser(
        "coverage-audit",
        help="Report P0/P1 catalog patterns missing canary profile coverage or explicit exception rationale.",
    )
    add_subcommand_format(coverage_parser)
    coverage_parser.add_argument(
        "--catalog",
        type=Path,
        help="Override the interaction-pattern catalog path.",
    )
    coverage_parser.add_argument(
        "--priority",
        action="append",
        choices=["P0", "P1", "P2"],
        default=[],
        help="Pattern priority to audit. Defaults to P0 and P1; repeat for multiple priorities.",
    )

    quality_audit_parser = canary_sub.add_parser(
        "quality-audit",
        help=(
            "Audit high-risk shipped surfaces for an independent oracle and explicit "
            "unit, smoke, canary, host, model, and release-layer classification."
        ),
    )
    add_subcommand_format(quality_audit_parser)

    premerge_parser = canary_sub.add_parser(
        "premerge",
        help="Run a risk-based pre-merge validation gate for the current diff.",
    )
    add_subcommand_format(premerge_parser)
    _add_canary_selector_args(premerge_parser)
    premerge_parser.add_argument(
        "--tier",
        choices=sorted(PREMERGE_TIERS),
        default="standard",
        help="Validation depth. standard runs bounded catalog and risk-profile checks.",
    )
    premerge_parser.add_argument(
        "--goal-id",
        help=(
            "Apply this goal's change-quality policy and exact-scope receipt gate. "
            "Omit only when no managed goal policy applies."
        ),
    )
    premerge_parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Preview the selected merge gate without running checks.",
    )
    premerge_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Per-check timeout for executed validation checks.",
    )
    premerge_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop smoke-suite sections after the first failed check.",
    )
    premerge_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Do not print premerge validation progress to stderr during execution.",
    )


def handle_canary_command(
    args: argparse.Namespace,
    *,
    registry_path: Path | None = None,
    runtime_root_arg: str | None = None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "canary":
        return None
    if args.canary_command == "profiles":
        payload = build_catalog_canary_profiles(catalog_path=args.catalog)
        renderer = render_catalog_canary_profiles_markdown
    elif args.canary_command == "smoke-profiles":
        payload = build_canary_smoke_suite_profiles()
        renderer = render_canary_smoke_suite_profiles_markdown
    elif args.canary_command == "smoke-health":
        payload = build_smoke_fleet_health(
            receipt_paths=list(args.receipt or []),
            include_inventory=bool(args.include_inventory),
            slow_threshold_seconds=float(args.slow_threshold_seconds or 0.0),
            review_limit=int(args.review_limit or 20),
        )
        renderer = render_smoke_fleet_health_markdown
    elif args.canary_command == "release-qualification":
        payload = build_canary_release_qualification_payload(args)
        renderer = render_exact_release_commit_qualification_markdown
    elif args.canary_command == "plan":
        changed_files, git_diff_selector = _resolve_canary_changed_files(args)
        payload = build_catalog_canary_plan(
            catalog_path=args.catalog,
            changed_files=changed_files,
            surfaces=list(args.surface or []),
            families=list(args.family or []),
            profiles=list(args.profile or []),
            include_deep_checks=bool(args.include_deep_checks),
            max_checks_per_family=int(args.max_checks_per_family or 3),
            max_checks_per_profile=int(args.max_checks_per_profile or 3),
        )
        _attach_selector_sources(payload, git_diff_selector=git_diff_selector)
        renderer = render_catalog_canary_plan_markdown
    elif args.canary_command == "run":
        changed_files, git_diff_selector = _resolve_canary_changed_files(args)
        payload = build_catalog_canary_run(
            catalog_path=args.catalog,
            changed_files=changed_files,
            surfaces=list(args.surface or []),
            families=list(args.family or []),
            profiles=list(args.profile or []),
            include_deep_checks=bool(args.include_deep_checks),
            max_checks_per_family=int(args.max_checks_per_family or 3),
            max_checks_per_profile=int(args.max_checks_per_profile or 3),
            check_limit=int(args.check_limit or 3),
            execute=not bool(args.no_execute),
            timeout_seconds=float(args.timeout_seconds or 120.0),
        )
        _attach_selector_sources(payload, git_diff_selector=git_diff_selector)
        renderer = render_catalog_canary_run_markdown
    elif args.canary_command == "smoke-suite":
        changed_files, git_diff_selector = _resolve_canary_changed_files(args)
        payload = build_canary_smoke_suite_run(
            suite=str(args.suite or "default-public"),
            modules=list(args.module or []),
            exclude_modules=list(args.exclude_module or []),
            scripts=list(args.script or []),
            catalog_path=args.catalog,
            changed_files=changed_files,
            surfaces=list(args.surface or []),
            families=list(args.family or []),
            profiles=list(args.profile or []),
            include_deep_checks=bool(args.include_deep_checks),
            max_checks_per_family=int(args.max_checks_per_family or 3),
            max_checks_per_profile=int(args.max_checks_per_profile or 3),
            offset=int(args.offset or 0),
            limit=int(args.limit or 0),
            execute=not bool(args.no_execute),
            timeout_seconds=float(args.timeout_seconds or 120.0),
            fail_fast=bool(args.fail_fast),
            allow_tracked_side_effects=bool(args.allow_tracked_side_effects),
            parallel_jobs=int(args.jobs or 1),
            progress_callback=(
                None
                if bool(args.no_execute) or bool(args.no_progress)
                else _print_smoke_suite_progress
            ),
        )
        _attach_selector_sources(payload, git_diff_selector=git_diff_selector)
        renderer = render_canary_smoke_suite_run_markdown
    elif args.canary_command == "coverage-audit":
        payload = build_catalog_canary_coverage_audit(
            catalog_path=args.catalog,
            priorities=list(args.priority or []) or None,
        )
        renderer = render_catalog_canary_coverage_audit_markdown
    elif args.canary_command == "quality-audit":
        payload = build_quality_surface_catalog_audit(
            repo_root=_resolve_git_repo_root(Path.cwd())
        )
        renderer = render_quality_surface_catalog_audit_markdown
    elif args.canary_command == "premerge":
        changed_files, git_diff_selector = _resolve_canary_changed_files(args)
        target_repo_root = _resolve_git_repo_root(Path.cwd())
        payload = build_premerge_validation_gate(
            changed_files=changed_files,
            base_ref=str(getattr(args, "git_diff_base", "origin/main") or "origin/main"),
            tier=str(args.tier or "standard"),
            execute=not bool(args.no_execute),
            timeout_seconds=float(args.timeout_seconds or 120.0),
            fail_fast=bool(args.fail_fast),
            include_deep_checks=bool(args.include_deep_checks),
            repo_root=target_repo_root,
            progress_callback=(
                None
                if bool(args.no_execute) or bool(args.no_progress)
                else _print_smoke_suite_progress
            ),
        )
        _attach_selector_sources(payload, git_diff_selector=git_diff_selector)
        goal_id = str(args.goal_id or "").strip()
        if goal_id:
            if registry_path is None:
                raise ValueError("--goal-id receipt verification requires a registry path")
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                runtime_root_arg,
                registry_path=registry_path,
            )
            verification = verify_change_quality_receipt(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                repo_path=target_repo_root,
                base_ref=str(
                    getattr(args, "git_diff_base", "origin/main")
                    or "origin/main"
                ),
            )
            apply_change_quality_verification(payload, verification)
        renderer = render_premerge_validation_gate_markdown
    else:
        raise ValueError(
            "canary requires `profiles`, `smoke-profiles`, `smoke-health`, "
            "`release-qualification`, `plan`, "
            "`run`, `smoke-suite`, "
            "`coverage-audit`, `quality-audit`, or `premerge`"
        )
    print_payload(payload, output_format(args), renderer)
    return 0 if payload.get("ok") else 1
