from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...domain_packs.issue_fix import (
    default_issue_fix_domain_state_ledger_path,
    default_issue_fix_feasibility_ledger_path,
    promote_issue_fix_feasibility_ledger_jsonl,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from .feasibility import (
    refresh_issue_fix_execution_binding,
    validate_issue_fix_feasibility_packet,
)
from .metadata_preview import (
    normalise_github_issue_link_reference,
    normalise_github_issue_reference,
)
from .pr_lifecycle import validate_issue_fix_pr_lifecycle_monitor_packet

ISSUE_FIX_DISCOVERED_ISSUE_PROMOTION_INPUT_SCHEMA_VERSION = (
    "issue_fix_discovered_issue_promotion_input_v0"
)
ISSUE_FIX_DISCOVERED_ISSUE_PROMOTION_SCHEMA_VERSION = (
    "issue_fix_discovered_issue_promotion_v0"
)
DUPLICATE_SEARCH_SCHEMA_VERSION = "issue_fix_duplicate_search_evidence_v0"
PROMOTION_DECISIONS = {"reuse_existing", "no_equivalent_found"}
_INPUT_FIELDS = {
    "schema_version",
    "repo",
    "source_issue_ref",
    "title",
    "problem_summary",
    "reproduction_summary",
    "expected_behavior",
    "validation_summary",
    "repository_revision",
    "evidence_refs",
    "duplicate_search",
    "pr_url",
}
_DUPLICATE_FIELDS = {
    "schema_version",
    "searched_states",
    "query_summary",
    "candidate_issue_urls",
    "decision",
    "decision_summary",
    "canonical_issue_url",
}
_CLOSING_REFERENCE_PATTERN = re.compile(
    r"(?im)^\s*(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#([1-9][0-9]*)\s*$"
)
_CLOSING_REFERENCE_VERIFY_ATTEMPTS = 3
_CLOSING_REFERENCE_VERIFY_DELAY_SECONDS = 0.5

CommandRunner = Callable[[Sequence[str]], Mapping[str, Any]]


def register_discovered_issue_promotion_command(
    issue_fix_sub: Any,
    *,
    add_subcommand_format: Callable[[Any], None],
    add_generated_at_arg: Callable[..., None],
) -> None:
    parser = issue_fix_sub.add_parser(
        "promote-discovered-issue",
        help=(
            "Create or reuse a canonical public issue for an agent-discovered defect, "
            "verify the PR closing reference, and reconcile placeholder domain state."
        ),
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--promotion-json",
        required=True,
        help=(
            "issue_fix_discovered_issue_promotion_input_v0 path, inline object, "
            "or '-' for stdin."
        ),
    )
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--project", default=".")
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Under active publish authority, create/reuse the issue, update and "
            "verify the PR, and reconcile issue-fix domain state."
        ),
    )
    parser.add_argument(
        "--no-write-domain-state",
        action="store_true",
        help="Verify public state without changing issue-fix domain state.",
    )
    add_generated_at_arg(parser, artifact="the promotion packet")


def build_discovered_issue_promotion_from_cli_args(
    args: Any,
    *,
    load_json_object: Callable[[str], dict[str, Any]],
    boundary_authority_scopes: Sequence[str],
    boundary_authority_resolved: bool,
    generated_at: str,
) -> dict[str, Any]:
    project = Path(args.project).expanduser()
    return build_issue_fix_discovered_issue_promotion_packet(
        promotion_input=load_json_object(args.promotion_json),
        boundary_authority_scopes=boundary_authority_scopes,
        boundary_authority_resolved=boundary_authority_resolved,
        execute=args.execute,
        feasibility_ledger_path=default_issue_fix_feasibility_ledger_path(
            project=project, goal_id=args.goal_id
        ),
        pr_lifecycle_ledger_path=default_issue_fix_domain_state_ledger_path(
            project=project, goal_id=args.goal_id
        ),
        write_domain_state=not args.no_write_domain_state,
        generated_at=generated_at,
    )


def _default_runner(args: Sequence[str]) -> Mapping[str, Any]:
    result = subprocess.run(
        list(args),
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _safe_text(value: Any, *, field: str, limit: int) -> str:
    text = public_safe_compact_text(value, limit=limit)
    if not text:
        raise ValueError(f"{field} must be compact and public-safe")
    return " ".join(text.split())


def _safe_public_ref(value: Any, *, field: str) -> str:
    reference = _safe_text(value, field=field, limit=300)
    if "://" in reference:
        parsed = urlsplit(reference)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"{field} must be a public https URL without user info, query, or fragment"
            )
        return reference
    if reference.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", reference):
        raise ValueError(f"{field} must not be a local path")
    if ".." in Path(reference).parts:
        raise ValueError(f"{field} must not traverse outside the repository")
    return reference


def _normalise_input(value: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(value) - _INPUT_FIELDS)
    if unknown:
        raise ValueError(f"promotion input contains unsupported fields: {unknown}")
    if (
        value.get("schema_version")
        != ISSUE_FIX_DISCOVERED_ISSUE_PROMOTION_INPUT_SCHEMA_VERSION
    ):
        raise ValueError(
            "promotion input schema_version must be "
            "issue_fix_discovered_issue_promotion_input_v0"
        )
    repo = _safe_text(value.get("repo"), field="repo", limit=160)
    if repo.count("/") != 1:
        raise ValueError("repo must use owner/name")
    source_issue_ref = _safe_text(
        value.get("source_issue_ref"), field="source_issue_ref", limit=160
    )
    if not source_issue_ref.lower().startswith("discovered"):
        raise ValueError(
            "source_issue_ref must identify a local discovered-* placeholder"
        )
    revision = _safe_text(
        value.get("repository_revision"), field="repository_revision", limit=120
    )
    raw_refs = value.get("evidence_refs")
    if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes)):
        raise ValueError("evidence_refs must be a list")
    evidence_refs = [
        _safe_public_ref(item, field=f"evidence_refs[{index}]")
        for index, item in enumerate(raw_refs)
    ]
    if not evidence_refs:
        raise ValueError("evidence_refs must name current-checkout evidence")

    raw_duplicate = value.get("duplicate_search")
    if not isinstance(raw_duplicate, Mapping):
        raise ValueError("duplicate_search is required")
    duplicate_unknown = sorted(set(raw_duplicate) - _DUPLICATE_FIELDS)
    if duplicate_unknown:
        raise ValueError(
            f"duplicate_search contains unsupported fields: {duplicate_unknown}"
        )
    if raw_duplicate.get("schema_version") != DUPLICATE_SEARCH_SCHEMA_VERSION:
        raise ValueError(
            "duplicate_search schema_version must be issue_fix_duplicate_search_evidence_v0"
        )
    states = sorted(
        {
            str(item).strip().lower()
            for item in raw_duplicate.get("searched_states") or []
            if str(item).strip()
        }
    )
    if states != ["closed", "open"]:
        raise ValueError("duplicate_search must cover both open and closed issues")
    decision = str(raw_duplicate.get("decision") or "").strip()
    if decision not in PROMOTION_DECISIONS:
        raise ValueError(
            "duplicate_search decision must be reuse_existing or no_equivalent_found"
        )
    raw_candidates = raw_duplicate.get("candidate_issue_urls") or []
    if not isinstance(raw_candidates, Sequence) or isinstance(
        raw_candidates, (str, bytes)
    ):
        raise ValueError("candidate_issue_urls must be a list")
    candidate_urls: list[str] = []
    for index, item in enumerate(raw_candidates):
        candidate = normalise_github_issue_reference(url=str(item))
        if candidate["kind"] != "issue" or candidate["repo"] != repo:
            raise ValueError(
                f"candidate_issue_urls[{index}] must identify an issue in {repo}"
            )
        candidate_urls.append(str(candidate["permalink"]))
    canonical_url = raw_duplicate.get("canonical_issue_url")
    if decision == "reuse_existing":
        if not canonical_url:
            raise ValueError("reuse_existing requires canonical_issue_url")
        canonical = normalise_github_issue_reference(url=str(canonical_url))
        if canonical["kind"] != "issue" or canonical["repo"] != repo:
            raise ValueError("canonical_issue_url must identify an issue in repo")
        canonical_url = str(canonical["permalink"])
        if canonical_url not in candidate_urls:
            raise ValueError(
                "reuse_existing canonical_issue_url must appear in candidate_issue_urls"
            )
    elif canonical_url:
        raise ValueError("no_equivalent_found must not predeclare canonical_issue_url")

    pr_url = value.get("pr_url")
    if pr_url:
        pr = normalise_github_issue_reference(url=str(pr_url))
        if pr["kind"] != "pull_request" or pr["repo"] != repo:
            raise ValueError("pr_url must identify a pull request in repo")
        pr_url = str(pr["permalink"])

    return {
        "schema_version": ISSUE_FIX_DISCOVERED_ISSUE_PROMOTION_INPUT_SCHEMA_VERSION,
        "repo": repo,
        "source_issue_ref": source_issue_ref,
        "title": _safe_text(value.get("title"), field="title", limit=180),
        "problem_summary": _safe_text(
            value.get("problem_summary"), field="problem_summary", limit=500
        ),
        "reproduction_summary": _safe_text(
            value.get("reproduction_summary"), field="reproduction_summary", limit=500
        ),
        "expected_behavior": _safe_text(
            value.get("expected_behavior"), field="expected_behavior", limit=400
        ),
        "validation_summary": _safe_text(
            value.get("validation_summary"), field="validation_summary", limit=400
        ),
        "repository_revision": revision,
        "evidence_refs": evidence_refs[:16],
        "duplicate_search": {
            "schema_version": DUPLICATE_SEARCH_SCHEMA_VERSION,
            "searched_states": states,
            "query_summary": _safe_text(
                raw_duplicate.get("query_summary"),
                field="duplicate_search.query_summary",
                limit=300,
            ),
            "candidate_issue_urls": candidate_urls[:20],
            "decision": decision,
            "decision_summary": _safe_text(
                raw_duplicate.get("decision_summary"),
                field="duplicate_search.decision_summary",
                limit=400,
            ),
            "canonical_issue_url": canonical_url,
        },
        "pr_url": pr_url,
    }


def _issue_body(value: Mapping[str, Any]) -> str:
    evidence = "\n".join(f"- `{reference}`" for reference in value["evidence_refs"])
    return "\n\n".join(
        [
            "## Problem\n\n" + str(value["problem_summary"]),
            "## Reproduction\n\n" + str(value["reproduction_summary"]),
            "## Expected behavior\n\n" + str(value["expected_behavior"]),
            "## Focused validation\n\n" + str(value["validation_summary"]),
            (
                "## Repository evidence\n\n"
                f"Observed at revision `{value['repository_revision']}`.\n\n{evidence}"
            ),
        ]
    )


def _run_json(
    runner: CommandRunner, args: Sequence[str], *, error: str
) -> dict[str, Any]:
    try:
        result = runner(args)
    except (OSError, subprocess.SubprocessError):
        raise ValueError(error) from None
    if result.get("returncode") != 0:
        raise ValueError(error)
    try:
        value = json.loads(str(result.get("stdout") or "{}"))
    except json.JSONDecodeError:
        raise ValueError(error) from None
    if not isinstance(value, dict):
        raise ValueError(error)
    return value


def _verify_issue(
    *, repo: str, issue_url: str, runner: CommandRunner
) -> dict[str, Any]:
    reference = normalise_github_issue_reference(url=issue_url)
    payload = _run_json(
        runner,
        [
            "gh",
            "issue",
            "view",
            str(reference["number"]),
            "--repo",
            repo,
            "--json",
            "number,state,title,url",
        ],
        error="canonical GitHub issue verification failed",
    )
    verified = normalise_github_issue_reference(url=str(payload.get("url") or ""))
    if verified["repo"] != repo or verified["number"] != reference["number"]:
        raise ValueError(
            "canonical GitHub issue verification returned a different issue"
        )
    return {
        "issue_ref": f"issues_{verified['number']}",
        "number": verified["number"],
        "url": verified["permalink"],
        "state": str(payload.get("state") or "UNKNOWN").upper(),
        "title": public_safe_compact_text(payload.get("title"), limit=180),
    }


def _create_issue(*, value: Mapping[str, Any], runner: CommandRunner) -> dict[str, Any]:
    try:
        result = runner(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                str(value["repo"]),
                "--title",
                str(value["title"]),
                "--body",
                _issue_body(value),
            ]
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("GitHub issue creation failed") from None
    if result.get("returncode") != 0:
        raise ValueError("GitHub issue creation failed")
    issue_url = str(result.get("stdout") or "").strip().splitlines()[-1]
    reference = normalise_github_issue_reference(url=issue_url)
    if reference["kind"] != "issue" or reference["repo"] != value["repo"]:
        raise ValueError("GitHub issue creation did not return the expected issue URL")
    return _verify_issue(repo=str(value["repo"]), issue_url=issue_url, runner=runner)


def _ensure_pr_closing_reference(
    *,
    repo: str,
    pr_url: str,
    issue_number: int,
    runner: CommandRunner,
    allow_write: bool = True,
) -> dict[str, Any]:
    reference = normalise_github_issue_reference(url=pr_url)

    def read_pr() -> dict[str, Any]:
        return _run_json(
            runner,
            [
                "gh",
                "pr",
                "view",
                str(reference["number"]),
                "--repo",
                repo,
                "--json",
                "body,closingIssuesReferences,url",
            ],
            error="GitHub PR closing-reference verification failed",
        )

    before = read_pr()
    linked_numbers = {
        item.get("number")
        for item in before.get("closingIssuesReferences") or []
        if isinstance(item, Mapping)
    }
    write_performed = False
    if issue_number not in linked_numbers:
        if not allow_write:
            raise ValueError(
                "GitHub PR does not yet report the closing issue reference"
            )
        raw_body = str(before.get("body") or "").rstrip()
        has_closing_line = any(
            int(match.group(1)) == issue_number
            for match in _CLOSING_REFERENCE_PATTERN.finditer(raw_body)
        )
        if not has_closing_line:
            raw_body = f"{raw_body}\n\nFixes #{issue_number}".strip()
            try:
                result = runner(
                    [
                        "gh",
                        "api",
                        f"repos/{repo}/pulls/{reference['number']}",
                        "--method",
                        "PATCH",
                        "-f",
                        f"body={raw_body}",
                    ]
                )
            except (OSError, subprocess.SubprocessError):
                raise ValueError("GitHub PR closing-reference update failed") from None
            if result.get("returncode") != 0:
                raise ValueError("GitHub PR closing-reference update failed")
            write_performed = True
    verified_numbers: set[Any] = set()
    for attempt in range(_CLOSING_REFERENCE_VERIFY_ATTEMPTS):
        after = read_pr()
        verified_numbers = {
            item.get("number")
            for item in after.get("closingIssuesReferences") or []
            if isinstance(item, Mapping)
        }
        if issue_number in verified_numbers:
            break
        if attempt + 1 < _CLOSING_REFERENCE_VERIFY_ATTEMPTS:
            time.sleep(_CLOSING_REFERENCE_VERIFY_DELAY_SECONDS)
    else:
        raise ValueError(
            "GitHub PR does not visibly report the closing issue reference"
        )
    return {
        "pr_ref": f"pull_{reference['number']}",
        "url": reference["permalink"],
        "issue_ref": f"issues_{issue_number}",
        "verified": True,
        "write_performed": write_performed,
        "body_captured": False,
        "response_payload_captured": False,
    }


def _find_domain_row(
    path: Path, *, repo: str, ref_field: str, ref_value: str
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    match = None
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        observation = row.get("observation") if isinstance(row, dict) else None
        if (
            isinstance(observation, dict)
            and observation.get("repo") == repo
            and observation.get(ref_field) == ref_value
        ):
            match = row
    return match


def promote_issue_fix_pr_lifecycle_packet(
    source_packet: Mapping[str, Any],
    *,
    canonical_issue_ref: str,
) -> dict[str, Any]:
    promoted = copy.deepcopy(dict(source_packet))
    observation = promoted.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("PR lifecycle row is missing observation")
    observation["issue_ref"] = normalise_github_issue_link_reference(
        canonical_issue_ref
    )
    promoted["observation_fingerprint"] = hashlib.sha256(
        json.dumps(observation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    validation = validate_issue_fix_pr_lifecycle_monitor_packet(promoted)
    promoted["validation"] = validation
    promoted["ok"] = validation["ok"]
    if not validation["ok"]:
        raise ValueError("promoted PR lifecycle packet is invalid")
    return promoted


def _promoted_context(
    context: Mapping[str, Any], *, repo: str, issue_ref: str
) -> dict[str, Any]:
    promoted = copy.deepcopy(dict(context))
    promoted["repo"] = repo
    promoted["issue_ref"] = issue_ref
    promoted.pop("context_fingerprint", None)
    fingerprint = hashlib.sha256(
        json.dumps(
            promoted, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:16]
    promoted["context_fingerprint"] = fingerprint
    return promoted


def _promotion_lineage(
    *,
    source_issue_ref: str,
    canonical_issue_ref: str,
    canonical_issue_url: str,
    duplicate_search: Mapping[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": "issue_fix_discovered_issue_promotion_lineage_v0",
        "source_issue_ref": source_issue_ref,
        "canonical_issue_ref": canonical_issue_ref,
        "canonical_issue_url": canonical_issue_url,
        "duplicate_search_decision": duplicate_search.get("decision"),
        "duplicate_search_decision_summary": duplicate_search.get("decision_summary"),
        "candidate_issue_urls": list(
            duplicate_search.get("candidate_issue_urls") or []
        ),
        "promoted_at": generated_at,
        "raw_search_results_captured": False,
    }


def promote_issue_fix_feasibility_packet(
    source_packet: Mapping[str, Any],
    *,
    source_issue_ref: str,
    canonical_issue_url: str,
    duplicate_search: Mapping[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    canonical = normalise_github_issue_reference(url=canonical_issue_url)
    observation = source_packet.get("observation")
    if not isinstance(observation, Mapping):
        raise ValueError("source feasibility row is missing observation")
    repo = str(observation.get("repo") or "")
    if observation.get("issue_ref") != source_issue_ref or canonical["repo"] != repo:
        raise ValueError("source feasibility row does not match promotion input")
    promoted = copy.deepcopy(dict(source_packet))
    promoted_observation = promoted["observation"]
    canonical_ref = f"issues_{canonical['number']}"
    promoted_observation.update(
        {
            "issue_ref": canonical_ref,
            "kind": "issue",
            "number": canonical["number"],
            "permalink": canonical["permalink"],
        }
    )
    context = promoted_observation.get("repository_context")
    if isinstance(context, Mapping):
        promoted_context = _promoted_context(
            context, repo=repo, issue_ref=canonical_ref
        )
        promoted_observation["repository_context"] = promoted_context
        effect = promoted.get("repository_context_effect")
        if isinstance(effect, dict):
            effect["context_fingerprint"] = promoted_context["context_fingerprint"]
    decision = promoted.get("decision")
    if not isinstance(decision, dict):
        raise ValueError("source feasibility row is missing decision")
    decision["observation_fingerprint"] = hashlib.sha256(
        json.dumps(
            {"observation": promoted_observation, "route": decision.get("route")},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    transition = promoted.get("transition")
    if isinstance(transition, dict):
        projected_todo = transition.get("projected_todo")
        if isinstance(projected_todo, dict):
            projected_todo["target_key"] = f"issue-fix:{repo}:{canonical_ref}"
            projected_todo["text"] = str(projected_todo.get("text") or "").replace(
                f"{repo} {source_issue_ref}",
                f"{repo} {canonical_ref}",
            )
    promoted["domain_state_key"] = {"repo": repo, "issue_ref": canonical_ref}
    projection = promoted.get("domain_state_projection")
    if not isinstance(projection, dict):
        raise ValueError("source feasibility row is missing domain_state_projection")
    projection["key"] = {"repo": repo, "issue_ref": canonical_ref}
    projection["write_performed"] = False
    projection.pop("write_result", None)
    projection.pop("write_skipped_reason", None)
    refresh_issue_fix_execution_binding(promoted)
    promoted["promotion_lineage"] = _promotion_lineage(
        source_issue_ref=source_issue_ref,
        canonical_issue_ref=canonical_ref,
        canonical_issue_url=str(canonical["permalink"]),
        duplicate_search=duplicate_search,
        generated_at=generated_at,
    )
    validation = validate_issue_fix_feasibility_packet(promoted)
    promoted["validation"] = validation
    promoted["ok"] = validation["ok"]
    if not validation["ok"]:
        raise ValueError("promoted feasibility packet is invalid")
    return promoted


def build_issue_fix_discovered_issue_promotion_packet(
    *,
    promotion_input: Mapping[str, Any],
    boundary_authority_scopes: Sequence[str] = (),
    boundary_authority_resolved: bool = False,
    execute: bool = False,
    feasibility_ledger_path: str | Path | None = None,
    pr_lifecycle_ledger_path: str | Path | None = None,
    write_domain_state: bool = True,
    runner: CommandRunner = _default_runner,
    generated_at: str = "2026-07-12T00:00:00Z",
) -> dict[str, Any]:
    value = _normalise_input(promotion_input)
    scopes = {str(scope).strip() for scope in boundary_authority_scopes}
    authority_satisfied = bool(boundary_authority_resolved and "publish" in scopes)
    duplicate = value["duplicate_search"]
    planned_action = (
        "reuse_existing_issue"
        if duplicate["decision"] == "reuse_existing"
        else "create_public_issue"
    )
    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": ISSUE_FIX_DISCOVERED_ISSUE_PROMOTION_SCHEMA_VERSION,
        "mode": "issue-fix-discovered-issue-promotion",
        "generated_at": generated_at,
        "repo": value["repo"],
        "source_issue_ref": value["source_issue_ref"],
        "repository_revision": value["repository_revision"],
        "duplicate_search": duplicate,
        "planned_action": planned_action,
        "pr_url": value["pr_url"],
        "authority_gate": {
            "schema_version": "issue_fix_external_write_gate_v0",
            "required_before": ["publish"],
            "authorized_before": ["publish"] if authority_satisfied else [],
            "blocked_before": [] if authority_satisfied else ["publish"],
            "satisfied": authority_satisfied,
            "authority_projection_resolved": boundary_authority_resolved,
            "authority_source": (
                "goal_checkpointed_boundary_authority"
                if boundary_authority_resolved
                else "not_available"
            ),
        },
        "issue": None,
        "pr_closing_reference": None,
        "domain_state_reconciliation": {
            "schema_version": "issue_fix_discovered_issue_reconciliation_v0",
            "write_performed": False,
            "source_placeholder_removed": False,
            "source_placeholder_absent": None,
            "canonical_row_retained": False,
            "duplicate_rows_remaining": None,
            "path_recorded": False,
        },
        "dry_run": not execute,
        "external_reads_performed": False,
        "external_writes_performed": False,
        "issue_body_captured": False,
        "pr_body_captured": False,
        "comment_bodies_captured": False,
        "raw_search_results_captured": False,
        "response_payloads_captured": False,
        "raw_logs_captured": False,
        "local_paths_captured": False,
        "destructive_git_used": False,
    }
    if not execute:
        return packet
    if not authority_satisfied:
        raise ValueError("discovered issue promotion requires active publish authority")
    if feasibility_ledger_path is None:
        raise ValueError("execute mode requires the issue-fix feasibility ledger")
    feasibility_path = Path(feasibility_ledger_path)

    source_row = _find_domain_row(
        feasibility_path,
        repo=value["repo"],
        ref_field="issue_ref",
        ref_value=value["source_issue_ref"],
    )
    expected_canonical_ref = None
    canonical_row = None
    if duplicate["decision"] == "reuse_existing":
        expected_reference = normalise_github_issue_reference(
            url=duplicate["canonical_issue_url"]
        )
        expected_canonical_ref = f"issues_{expected_reference['number']}"
        canonical_row = _find_domain_row(
            feasibility_path,
            repo=value["repo"],
            ref_field="issue_ref",
            ref_value=expected_canonical_ref,
        )
    if source_row is None and canonical_row is None:
        raise ValueError(
            "feasibility ledger has neither the source placeholder nor canonical issue row"
        )
    if not write_domain_state and duplicate["decision"] != "reuse_existing":
        raise ValueError(
            "read-only verification requires reuse_existing; issue creation must reconcile domain state"
        )
    if source_row is not None:
        source_context = (source_row.get("observation") or {}).get("repository_context")
        source_revision = (
            str(source_context.get("repository_revision") or "")
            if isinstance(source_context, Mapping)
            else ""
        )
        if source_revision != value["repository_revision"]:
            raise ValueError(
                "source feasibility row revision does not match promotion input"
            )
    if canonical_row is not None:
        canonical_context = (canonical_row.get("observation") or {}).get(
            "repository_context"
        )
        canonical_revision = (
            str(canonical_context.get("repository_revision") or "")
            if isinstance(canonical_context, Mapping)
            else ""
        )
        if canonical_revision != value["repository_revision"]:
            raise ValueError(
                "canonical feasibility row revision does not match promotion input"
            )

    issue_created = False
    if duplicate["decision"] == "reuse_existing":
        issue = _verify_issue(
            repo=value["repo"],
            issue_url=duplicate["canonical_issue_url"],
            runner=runner,
        )
    else:
        issue = _create_issue(value=value, runner=runner)
        issue_created = True
    packet["issue"] = issue | {
        "created": issue_created,
        "reused": not issue_created,
        "verified": True,
    }
    packet["external_reads_performed"] = True
    packet["external_writes_performed"] = issue_created

    pr_blocker: str | None = None
    if value["pr_url"]:
        try:
            pr_result = _ensure_pr_closing_reference(
                repo=value["repo"],
                pr_url=value["pr_url"],
                issue_number=int(issue["number"]),
                runner=runner,
                allow_write=write_domain_state,
            )
        except ValueError as exc:
            pr_blocker = str(exc)
            packet["pr_closing_reference"] = {
                "url": value["pr_url"],
                "issue_ref": issue["issue_ref"],
                "verified": False,
                "write_performed": False,
                "blocker": pr_blocker,
                "body_captured": False,
                "response_payload_captured": False,
            }
        else:
            packet["pr_closing_reference"] = pr_result
            packet["external_writes_performed"] = bool(
                packet["external_writes_performed"] or pr_result["write_performed"]
            )
        packet["external_reads_performed"] = True

    if canonical_row is None or expected_canonical_ref != issue["issue_ref"]:
        canonical_row = _find_domain_row(
            feasibility_path,
            repo=value["repo"],
            ref_field="issue_ref",
            ref_value=issue["issue_ref"],
        )
    if source_row is not None:
        promoted = promote_issue_fix_feasibility_packet(
            source_row,
            source_issue_ref=value["source_issue_ref"],
            canonical_issue_url=issue["url"],
            duplicate_search=duplicate,
            generated_at=generated_at,
        )
    elif canonical_row is not None:
        promoted = copy.deepcopy(canonical_row)
        existing_lineage = promoted.get("promotion_lineage")
        if not isinstance(existing_lineage, Mapping) or existing_lineage.get(
            "duplicate_search_decision_summary"
        ) != duplicate.get("decision_summary"):
            promoted["promotion_lineage"] = _promotion_lineage(
                source_issue_ref=value["source_issue_ref"],
                canonical_issue_ref=issue["issue_ref"],
                canonical_issue_url=issue["url"],
                duplicate_search=duplicate,
                generated_at=generated_at,
            )
    else:
        promoted = canonical_row
    if write_domain_state:
        reconciliation = promote_issue_fix_feasibility_ledger_jsonl(
            feasibility_path,
            promoted,
            source_issue_ref=value["source_issue_ref"],
        )
        packet["domain_state_reconciliation"].update(reconciliation)
        packet["domain_state_reconciliation"]["canonical_row_retained"] = True
        packet["domain_state_reconciliation"]["source_placeholder_absent"] = True

    if (
        value["pr_url"]
        and pr_lifecycle_ledger_path is not None
        and write_domain_state
        and pr_blocker is None
    ):
        pr_reference = normalise_github_issue_reference(url=value["pr_url"])
        lifecycle_path = Path(pr_lifecycle_ledger_path)
        lifecycle_source = _find_domain_row(
            lifecycle_path,
            repo=value["repo"],
            ref_field="pr_ref",
            ref_value=f"pull_{pr_reference['number']}",
        )
        if lifecycle_source is None:
            packet["domain_state_reconciliation"]["pr_lifecycle"] = {
                "write_performed": False,
                "status": "not_projected",
                "path_recorded": False,
            }
        else:
            lifecycle = promote_issue_fix_pr_lifecycle_packet(
                lifecycle_source,
                canonical_issue_ref=issue["issue_ref"],
            )
            lifecycle_write = upsert_issue_fix_pr_lifecycle_ledger_jsonl(
                lifecycle_path, lifecycle
            )
            packet["domain_state_reconciliation"]["pr_lifecycle"] = {
                "write_performed": lifecycle_write.get("write_performed") is True,
                "status": lifecycle_write.get("status"),
                "path_recorded": False,
            }
    packet["dry_run"] = False
    if pr_blocker is not None:
        packet["ok"] = False
        packet["blocker"] = {
            "schema_version": "issue_fix_discovered_issue_promotion_blocker_v0",
            "reason_code": "pr_closing_reference_unverified",
            "summary": pr_blocker,
            "canonical_issue_url": issue["url"],
            "successor_action": "retry_pr_closing_reference_then_refresh_lifecycle",
        }
    return packet


def render_issue_fix_discovered_issue_promotion_markdown(
    payload: Mapping[str, Any],
) -> str:
    issue = payload.get("issue") if isinstance(payload.get("issue"), Mapping) else {}
    pr = (
        payload.get("pr_closing_reference")
        if isinstance(payload.get("pr_closing_reference"), Mapping)
        else {}
    )
    reconciliation = (
        payload.get("domain_state_reconciliation")
        if isinstance(payload.get("domain_state_reconciliation"), Mapping)
        else {}
    )
    return "\n".join(
        [
            "# LoopX Discovered Issue Promotion",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- repo: `{payload.get('repo')}`",
            f"- source_issue_ref: `{payload.get('source_issue_ref')}`",
            f"- planned_action: `{payload.get('planned_action')}`",
            f"- canonical_issue: `{issue.get('url')}`",
            f"- issue_created: `{issue.get('created')}`",
            f"- pr_closing_reference_verified: `{pr.get('verified')}`",
            f"- domain_state_write_performed: `{reconciliation.get('write_performed')}`",
            f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        ]
    )
