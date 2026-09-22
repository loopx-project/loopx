from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

from ..capabilities.pr_review_queue import (
    DEFAULT_REVIEW_PRIORITY,
    build_pull_request_review_queue_observation,
    normalize_fresh_audit_exact_heads,
    normalize_review_priority,
)
from ..capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from ..capabilities.machine_configuration.store import read_machine_configuration
from ..capabilities.pr_review_queue.result_check import check_review_result
from ..capabilities.pr_review_queue.readiness_observation import (
    observation_key,
    read_readiness_observations,
    record_readiness_observation,
)
from ..capabilities.pr_review_queue.github_source import (
    scan_github_pull_request_targets,
)
from ..file_lock import exclusive_file_lock
from ..pr_review import (
    build_pr_review_packet,
    load_pr_fixture,
    normalize_pr_state_filter,
    render_pr_review_markdown,
    resolve_current_github_login,
    resolve_current_github_repository,
    scan_github_pull_requests,
)
from ..pr_review_merge_readiness import (
    build_pr_merge_readiness_packet,
    fetch_github_pull_request,
    fetch_github_review_thread_summary,
)
from ..registry import atomic_write_json, read_json, find_registry_goal
from ..capabilities.pr_review_queue.goal_configuration import resolve_configuration

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
CHECKPOINT_SCHEMA_VERSION = "pull_request_review_monitor_checkpoint_v0"


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be an object")
    return payload


def _read_checkpoint(path: Path) -> tuple[dict[str, object] | None, str | None]:
    with exclusive_file_lock(path, operation="pr_review_checkpoint_read"):
        digest = _file_digest(path)
        if digest is None:
            return None, None
        payload = _read_json_object(path, label="observation state file")
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("observation state file has an unsupported schema_version")
    return payload, digest


def _write_checkpoint(
    path: Path,
    *,
    expected_digest: str | None,
    repository: str | None,
    autonomous_review: dict[str, object],
) -> None:
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "repository": repository,
        "autonomous_review": autonomous_review,
    }
    with exclusive_file_lock(path, operation="pr_review_checkpoint_write"):
        if _file_digest(path) != expected_digest:
            raise RuntimeError(
                "observation state changed concurrently; retry from the latest checkpoint"
            )
        atomic_write_json(path, checkpoint, preserve_mode=True)


def register_pr_review_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "pr-review",
        help="Build a public-safe /loopx-pr-review queue for the current project's pull requests.",
    )
    add_subcommand_format(parser)
    parser.add_argument("--goal-id", help="Use this Goal PR review configuration; otherwise use machine defaults.")
    parser.add_argument(
        "--check-result",
        help="Check a saved review result for verdict/evidence consistency; no GitHub writes.",
    )
    parser.add_argument(
        "--check-merge-readiness",
        metavar="NUMBER@HEAD_OID",
        help=(
            "Re-read one open PR and fail closed unless this exact reviewed head, "
            "its configured CI policy, approval, and review threads are ready immediately before merge; "
            "requires --goal-id and records a compact local Goal readiness observation."
        ),
    )
    parser.add_argument(
        "--packet",
        help="Saved pr-review packet required with --check-result; remote freshness is checked separately.",
    )
    parser.add_argument(
        "--repo",
        help="GitHub owner/repo to review. Defaults to the current project's gh repository context.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum PRs to include per selected lifecycle group.",
    )
    parser.add_argument(
        "--state",
        choices=("open", "merged", "all"),
        default=None,
        help=(
            "PR lifecycle state to include. Ordinary queues default to open; "
            "use merged/all explicitly for lifecycle or post-merge audits."
        ),
    )
    parser.add_argument(
        "--review-priority",
        choices=("other-developers-first", "owner-first"),
        default=None,
        help=(
            "Scheduling preference for actionable PRs. Defaults to the configured "
            "pull_request_review machine capability (other-developers-first when "
            "unset); use owner-first to prioritize the authenticated reviewer's own PRs."
        ),
    )
    parser.add_argument(
        "--since",
        help="Only include PRs active since this ISO timestamp or YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--fixture",
        help="Read public-safe PR metadata from a JSON fixture instead of live gh output.",
    )
    parser.add_argument(
        "--target-exact-head",
        action="append",
        default=[],
        metavar="NUMBER@HEAD_OID",
        help=(
            "Read only this exact PR head instead of scanning a lifecycle queue. "
            "Repeatable for a small explicit review batch."
        ),
    )
    parser.add_argument(
        "--fresh-audit-exact-head",
        action="append",
        default=[],
        metavar="NUMBER@HEAD_OID",
        help=(
            "Explicitly request a fresh evidence audit for one unchanged exact head "
            "that already has a valid conclusion. Repeatable."
        ),
    )
    parser.add_argument(
        "--autonomous-observation",
        action="store_true",
        help="Add a read-only autonomous queue observation and at most one exact-head candidate.",
    )
    parser.add_argument(
        "--previous-observation-json",
        help="Compare against a prior autonomous observation or full pr-review packet.",
    )
    parser.add_argument(
        "--observation-state-file",
        help=(
            "Atomically persist and reuse one public-safe autonomous observation "
            "checkpoint across Codex tasks."
        ),
    )
    parser.add_argument(
        "--projected-exact-head",
        action="append",
        default=[],
        metavar="NUMBER@HEAD_OID",
        help=(
            "Acknowledge that one previously emitted exact-head candidate was "
            "durably projected to its Todo target. Repeatable."
        ),
    )
    parser.add_argument(
        "--handled-exact-head",
        action="append",
        default=[],
        metavar="NUMBER@HEAD_OID",
        help=(
            "Record one externally completed exact-head candidate so an autonomous "
            "monitor can advance to the next unhandled PR. Repeatable."
        ),
    )


def _resolve_pr_review_state_filter(
    raw_state: object,
    *,
    target_exact_heads: Sequence[str],
) -> str:
    if raw_state is None and target_exact_heads:
        return "all"
    return normalize_pr_state_filter(raw_state)


def handle_pr_review_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
    runtime_root: Path | None = None,
    registry_path: Path | None = None,
) -> int | None:
    if args.command != "pr-review":
        return None
    checkpoint_path: Path | None = None
    resolved_review_priority = DEFAULT_REVIEW_PRIORITY
    target_exact_heads = list(getattr(args, "target_exact_head", []) or [])
    readiness_observations: dict[str, dict[str, object]] = {}
    resolved_state_filter = _resolve_pr_review_state_filter(
        getattr(args, "state", None),
        target_exact_heads=target_exact_heads,
    )
    try:
        machine_configuration = (read_machine_configuration(runtime_root, registry=build_builtin_machine_configuration_registry()) if runtime_root is not None else None)
        goal = None
        goal_id = getattr(args, "goal_id", None)
        if goal_id:
            if registry_path is None or not registry_path.exists():
                raise ValueError("--goal-id requires an available Goal registry")
            goal = find_registry_goal(read_json(registry_path), goal_id)
            if goal is None:
                raise ValueError("PR review Goal was not found: " + goal_id)
        review_configuration = resolve_configuration(goal, machine_configuration)
        wait_for_ci = review_configuration["wait_for_ci"]
        if goal_id:
            if runtime_root is None:
                raise ValueError("--goal-id requires an available runtime root")
            readiness_observations = read_readiness_observations(
                runtime_root=runtime_root,
                goal_id=goal_id,
            )
        if args.check_result or args.packet:
            if not (args.check_result and args.packet):
                raise ValueError("--check-result and --packet must be used together")
            if (
                args.autonomous_observation
                or args.observation_state_file
                or args.previous_observation_json
                or args.handled_exact_head
                or args.projected_exact_head
                or args.fixture
                or args.repo
                or args.since
                or args.fresh_audit_exact_head
                or target_exact_heads
                or args.check_merge_readiness
            ):
                raise ValueError(
                    "result checking cannot be combined with scan or observation options"
                )
            try:
                saved_packet = _read_json_object(
                    Path(args.packet), label="review packet"
                )
                saved_result = _read_json_object(
                    Path(args.check_result), label="review result"
                )
            except OSError:
                raise ValueError(
                    "review packet or result is unreadable; check the supplied files"
                ) from None
            payload = check_review_result(saved_packet, saved_result)
            print_payload(
                payload, output_format(args), lambda value: json.dumps(value, indent=2)
            )
            return 0 if payload["ok"] else 1
        if args.check_merge_readiness:
            if not goal_id:
                raise ValueError("merge readiness requires --goal-id")
            if (
                args.autonomous_observation
                or args.observation_state_file
                or args.previous_observation_json
                or args.handled_exact_head
                or args.projected_exact_head
                or args.since
                or args.fresh_audit_exact_head
                or target_exact_heads
            ):
                raise ValueError(
                    "merge readiness cannot be combined with queue or observation options"
                )
            expected = normalize_fresh_audit_exact_heads([args.check_merge_readiness])
            if len(expected) != 1:
                raise ValueError("merge readiness requires one exact NUMBER@HEAD_OID")
            expected_exact_head = next(iter(expected))
            number = int(expected_exact_head.split("@", 1)[0])
            repository = args.repo
            reviewer_login = None
            if args.fixture:
                fixture_repository, pull_requests, reviewer_login = load_pr_fixture(
                    Path(args.fixture).expanduser()
                )
                repository = repository or fixture_repository
                matches = [
                    item for item in pull_requests if item.get("number") == number
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "merge readiness target must match exactly one fixture PR"
                    )
                pull_request = matches[0]
                raw_threads = pull_request.get("review_thread_summary")
                review_threads = (
                    raw_threads
                    if isinstance(raw_threads, dict)
                    else {
                        "schema_version": "github_review_thread_summary_v0",
                        "complete": False,
                        "total_count": 0,
                        "unresolved_count": 0,
                        "failure_code": "fixture_review_thread_summary_missing",
                    }
                )
                source = "fixture"
            else:
                repository = repository or resolve_current_github_repository()
                if not repository:
                    raise RuntimeError("GitHub repository could not be resolved")
                reviewer_login = resolve_current_github_login()
                pull_request = fetch_github_pull_request(
                    repo=repository,
                    number=number,
                    **({"wait_for_ci": False} if not wait_for_ci else {}),
                )
                review_threads = fetch_github_review_thread_summary(
                    repo=repository,
                    number=number,
                )
                source = "github_cli"
            if not repository:
                raise ValueError("repository is required for merge readiness")
            payload = build_pr_merge_readiness_packet(
                pull_request=pull_request,
                repository=repository,
                expected_exact_head=expected_exact_head,
                reviewer_login=reviewer_login,
                review_threads=review_threads,
                source=source,
                wait_for_ci=wait_for_ci,
            )
            observation = record_readiness_observation(
                runtime_root=runtime_root,
                goal_id=goal_id,
                readiness=payload,
            )
            payload["readiness_observation"] = {
                key: observation[key]
                for key in (
                    "schema_version",
                    "goal_id",
                    "repository",
                    "exact_head",
                    "material_fingerprint",
                    "ready",
                    "blocking_reasons",
                    "observed_at",
                )
            }
            payload["local_goal_observation_write_performed"] = True
            print_payload(
                payload,
                output_format(args),
                lambda value: json.dumps(value, indent=2),
            )
            return 0 if payload["ready"] else 1
        if args.previous_observation_json and not args.autonomous_observation:
            raise ValueError(
                "--previous-observation-json requires --autonomous-observation"
            )
        if args.handled_exact_head and not args.autonomous_observation:
            raise ValueError("--handled-exact-head requires --autonomous-observation")
        if args.projected_exact_head and not args.autonomous_observation:
            raise ValueError("--projected-exact-head requires --autonomous-observation")
        if args.observation_state_file and not args.autonomous_observation:
            raise ValueError(
                "--observation-state-file requires --autonomous-observation"
            )
        if args.observation_state_file and args.previous_observation_json:
            raise ValueError(
                "--observation-state-file cannot be combined with "
                "--previous-observation-json"
            )
        if target_exact_heads and args.autonomous_observation:
            raise ValueError(
                "--target-exact-head cannot be combined with --autonomous-observation"
            )
        if target_exact_heads and args.since:
            raise ValueError(
                "--target-exact-head already defines the review window and "
                "cannot be combined with --since"
            )
        explicit_review_priority = getattr(args, "review_priority", None)
        if explicit_review_priority is not None:
            resolved_review_priority = normalize_review_priority(explicit_review_priority)
        else:
            resolved_review_priority = normalize_review_priority(review_configuration["review_priority"])
        previous_observation = None
        checkpoint_digest = None
        if args.observation_state_file:
            checkpoint_path = Path(args.observation_state_file).expanduser()
            previous_observation, checkpoint_digest = _read_checkpoint(checkpoint_path)
        if args.previous_observation_json:
            previous_observation = _read_json_object(
                Path(args.previous_observation_json).expanduser(),
                label="previous observation JSON",
            )
        repository = args.repo
        source = "github_cli"
        reviewer_login = None
        if args.fixture:
            repository_from_fixture, pull_requests, reviewer_login = load_pr_fixture(
                Path(args.fixture).expanduser()
            )
            repository = repository or repository_from_fixture
            if target_exact_heads:
                requested_targets = normalize_fresh_audit_exact_heads(
                    target_exact_heads
                )
                pull_requests = [
                    item
                    for item in pull_requests
                    if (
                        f"{item.get('number')}@"
                        f"{str(item.get('headRefOid') or '').lower()}"
                        in requested_targets
                    )
                ]
            source = "fixture"
            source_scan = None
        else:
            repository = repository or resolve_current_github_repository()
            reviewer_login = resolve_current_github_login()
            if args.autonomous_observation and not reviewer_login:
                raise RuntimeError(
                    "authenticated GitHub reviewer identity is required for "
                    "autonomous author-owned scheduling"
                )
            if target_exact_heads:
                if not repository:
                    raise RuntimeError("GitHub repository could not be resolved")
                source_scan = scan_github_pull_request_targets(
                    repository=repository,
                    exact_heads=target_exact_heads,
                    **({"wait_for_ci": False} if not wait_for_ci else {}),
                )
                source = "github_cli_exact_targets"
            else:
                source_scan = scan_github_pull_requests(
                    repo=repository,
                    limit=max(1, args.limit) + 1,
                    state_filter=resolved_state_filter,
                    since=args.since,
                    **({"wait_for_ci": False} if not wait_for_ci else {}),
                )
            pull_requests = source_scan["pull_requests"]
        if readiness_observations:
            observed_rows = []
            for row in pull_requests:
                number = row.get("number")
                head_oid = str(row.get("headRefOid") or "").strip().casefold()
                exact_head = (
                    f"{number}@{head_oid}"
                    if isinstance(number, int) and head_oid
                    else ""
                )
                if observation_key(str(repository or ""), exact_head) not in readiness_observations:
                    continue
                if args.fixture:
                    raw_threads = row.get("review_thread_summary")
                    if not isinstance(raw_threads, dict):
                        row["review_thread_summary"] = {
                            "schema_version": "github_review_thread_summary_v0",
                            "complete": False,
                            "total_count": 0,
                            "unresolved_count": 0,
                            "failure_code": "fixture_review_thread_summary_missing",
                        }
                else:
                    observed_rows.append(row)
            if observed_rows:
                with ThreadPoolExecutor(max_workers=min(8, len(observed_rows))) as pool:
                    summaries = pool.map(
                        lambda row: fetch_github_review_thread_summary(
                            repo=str(repository or ""),
                            number=row["number"],
                        ),
                        observed_rows,
                    )
                    for row, summary in zip(observed_rows, summaries, strict=True):
                        row["review_thread_summary"] = summary
        if checkpoint_path is not None and previous_observation:
            checkpoint_repository = str(
                previous_observation.get("repository") or ""
            ).strip()
            if (
                checkpoint_repository
                and checkpoint_repository != str(repository or "").strip()
            ):
                raise ValueError(
                    "observation state repository does not match the requested repository"
                )
        payload = build_pr_review_packet(
            pull_requests=pull_requests,
            repository=repository,
            limit=max(1, args.limit),
            source=source,
            state_filter=resolved_state_filter,
            since=args.since,
            source_scan=source_scan,
            reviewer_login=reviewer_login,
            fresh_audit_exact_heads=args.fresh_audit_exact_head,
            target_exact_heads=target_exact_heads,
            review_priority=resolved_review_priority,
            wait_for_ci=wait_for_ci,
            readiness_observations=readiness_observations,
        )
        payload["request"]["goal_id"] = goal_id
        payload["request"]["review_configuration"] = review_configuration
        payload["request"]["readiness_observation_count"] = len(
            readiness_observations
        )
        if args.autonomous_observation:
            autonomous_review = build_pull_request_review_queue_observation(
                repository=repository,
                pull_requests=payload.get("pull_requests") or [],
                result_completeness=payload.get("result_completeness") or {},
                previous_observation=previous_observation,
                handled_exact_heads=args.handled_exact_head,
                projected_exact_heads=args.projected_exact_head,
                authenticated_developer_login=reviewer_login,
                review_priority=resolved_review_priority,
            )
            payload["autonomous_review"] = autonomous_review
            payload["request"]["autonomous_observation"] = True
            payload["request"]["previous_observation_supplied"] = bool(
                previous_observation
            )
            payload["request"]["handled_exact_head_count_supplied"] = len(
                args.handled_exact_head
            )
            payload["request"]["projected_exact_head_count_supplied"] = len(
                args.projected_exact_head
            )
            payload["request"]["observation_state_file_supplied"] = bool(
                checkpoint_path
            )
            payload["request"]["include"].append("autonomous_review")
            if checkpoint_path is not None:
                _write_checkpoint(
                    checkpoint_path,
                    expected_digest=checkpoint_digest,
                    repository=repository,
                    autonomous_review=autonomous_review,
                )
                payload["request"]["local_checkpoint_write_performed"] = True
    except Exception as exc:
        error = str(exc)
        if checkpoint_path is not None:
            path_candidates = {str(checkpoint_path)}
            with suppress(OSError):
                path_candidates.add(str(checkpoint_path.resolve()))
            for path_text in sorted(path_candidates, key=len, reverse=True):
                if path_text:
                    error = error.replace(path_text, "<observation-state-file>")
        payload = {
            "ok": False,
            "schema_version": "loopx_pr_review_command_response_v0",
            "request": {
                "schema_version": "loopx_pr_review_command_request_v0",
                "command": "/loopx-pr-review",
                "cli_command": "loopx pr-review [--repo owner/repo] [--target-exact-head NUMBER@HEAD_OID] [--state open|merged|all] [--review-priority other-developers-first|owner-first] [--since ISO]",
                "repository": args.repo,
                "limit": max(1, args.limit),
                "state_filter": resolved_state_filter,
                "since": args.since,
                "review_priority": resolved_review_priority.value,
                "fresh_audit_exact_heads": list(args.fresh_audit_exact_head),
                "target_exact_heads": target_exact_heads,
                "source": "fixture" if args.fixture else "github_cli",
                "privacy_mode": "public_safe_github_metadata",
                "dry_run": True,
            },
            "error": error,
        }
    print_payload(payload, output_format(args), render_pr_review_markdown)
    return 0 if payload.get("ok") else 1
