from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PLAN_SCHEMA_VERSION = "loopx_integration_branch_plan_v0"
STATUS_SCHEMA_VERSION = "loopx_integration_branch_status_v0"
SYNC_SCHEMA_VERSION = "loopx_integration_branch_sync_v0"
DEFAULT_PLAN_PATH = Path(".loopx/integration-branch.json")
ZERO_SHA = "0" * 40


class IntegrationBranchError(ValueError):
    """A fail-closed integration-branch contract error."""


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise IntegrationBranchError(detail)
    return result


def _repository_root(repo_path: str | Path) -> Path:
    requested = Path(repo_path).expanduser().resolve()
    result = _git(requested, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def _plan_path(repo: Path, plan_file: str | Path | None) -> Path:
    state_root = (repo / ".loopx").resolve()
    if not state_root.is_relative_to(repo):
        raise IntegrationBranchError(
            "repository `.loopx` state root must not resolve outside the repository"
        )
    if plan_file is None:
        return state_root / DEFAULT_PLAN_PATH.name
    candidate = Path(plan_file).expanduser()
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
    )
    if resolved == state_root or not resolved.is_relative_to(state_root):
        raise IntegrationBranchError(
            "integration branch plan must remain under the repository `.loopx` "
            "state root"
        )
    return resolved


def _assert_ignored_plan(repo: Path, path: Path) -> None:
    relative = path.relative_to(repo).as_posix()
    tracked = _git(
        repo,
        "ls-files",
        "--error-unmatch",
        "--",
        relative,
        check=False,
    )
    if tracked.returncode == 0:
        raise IntegrationBranchError(
            "integration branch plan must be local ignored state, not a tracked file"
        )
    ignored = _git(repo, "check-ignore", "--quiet", "--", relative, check=False)
    if ignored.returncode != 0:
        raise IntegrationBranchError(
            "integration branch plan must be ignored by the repository before use"
        )


def _resolve_commit(repo: Path, ref: str) -> str:
    result = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return result.stdout.strip()


def _resolve_optional_commit(repo: Path, ref: str) -> str | None:
    result = _git(
        repo,
        "rev-parse",
        "--verify",
        f"{ref}^{{commit}}",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _resolve_tree(repo: Path, commit_sha: str) -> str:
    result = _git(repo, "rev-parse", "--verify", f"{commit_sha}^{{tree}}")
    return result.stdout.strip()


def _validate_branch_name(repo: Path, branch: str) -> str:
    normalized = branch.strip()
    if not normalized:
        raise IntegrationBranchError("integration branch must not be empty")
    _git(repo, "check-ref-format", "--branch", normalized)
    return normalized


def _normalize_source_refs(source_refs: Sequence[str]) -> list[str]:
    normalized = [ref.strip() for ref in source_refs if ref.strip()]
    if not normalized:
        raise IntegrationBranchError("at least one source branch is required")
    if len(set(normalized)) != len(normalized):
        raise IntegrationBranchError("source branches must be unique and ordered")
    return normalized


def _validate_plan(repo: Path, raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise IntegrationBranchError(
            f"integration branch plan must use `{PLAN_SCHEMA_VERSION}`"
        )
    base_ref = str(raw.get("base_ref") or "").strip()
    if not base_ref:
        raise IntegrationBranchError("integration branch plan requires `base_ref`")
    integration_branch = _validate_branch_name(
        repo, str(raw.get("integration_branch") or "")
    )
    source_value = raw.get("source_refs")
    if not isinstance(source_value, list) or not all(
        isinstance(ref, str) for ref in source_value
    ):
        raise IntegrationBranchError(
            "integration branch plan requires string list `source_refs`"
        )
    source_refs = _normalize_source_refs(source_value)
    integration_ref = f"refs/heads/{integration_branch}"
    if base_ref in {integration_branch, integration_ref}:
        raise IntegrationBranchError(
            "integration branch must not also be the configured base ref"
        )
    if integration_branch in source_refs or integration_ref in source_refs:
        raise IntegrationBranchError(
            "integration branch must not also be a source branch"
        )
    last_sync = raw.get("last_sync")
    if last_sync is not None and not isinstance(last_sync, Mapping):
        raise IntegrationBranchError("`last_sync` must be an object or null")
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "base_ref": base_ref,
        "integration_branch": integration_branch,
        "source_refs": source_refs,
        "last_sync": dict(last_sync) if isinstance(last_sync, Mapping) else None,
    }


def _read_plan(repo: Path, path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IntegrationBranchError(
            f"integration branch plan not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise IntegrationBranchError(
            f"integration branch plan is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise IntegrationBranchError("integration branch plan must be a JSON object")
    return _validate_plan(repo, raw)


def _write_plan(path: Path, plan: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(plan, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _configured_remote_refspecs(
    repo: Path,
    plan: Mapping[str, Any],
) -> dict[str, list[str]]:
    known_remotes = sorted(
        _git(repo, "remote").stdout.split(),
        key=len,
        reverse=True,
    )
    configured_refs = [str(plan["base_ref"]), *map(str, plan["source_refs"])]
    refspecs_by_remote: dict[str, list[str]] = {}
    for ref in configured_refs:
        symbolic = _git(repo, "rev-parse", "--symbolic-full-name", ref).stdout.strip()
        if not symbolic.startswith("refs/remotes/"):
            continue
        target = _git(repo, "symbolic-ref", "--quiet", symbolic, check=False)
        if target.returncode == 0:
            symbolic = target.stdout.strip()
        remote_path = symbolic.removeprefix("refs/remotes/")
        remote = next(
            (
                candidate
                for candidate in known_remotes
                if remote_path.startswith(f"{candidate}/")
            ),
            None,
        )
        if remote is None:
            continue
        branch = remote_path.removeprefix(f"{remote}/")
        refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
        remote_refspecs = refspecs_by_remote.setdefault(remote, [])
        if refspec not in remote_refspecs:
            remote_refspecs.append(refspec)

    integration_remote = _integration_remote(repo, plan)
    if integration_remote is not None:
        remote_refspecs = refspecs_by_remote.setdefault(
            str(integration_remote["remote"]), []
        )
        refspec = str(integration_remote["refspec"])
        if refspec not in remote_refspecs:
            remote_refspecs.append(refspec)
    return refspecs_by_remote


def _integration_remote(
    repo: Path,
    plan: Mapping[str, Any],
) -> dict[str, str] | None:
    branch = str(plan["integration_branch"])
    remote_result = _git(
        repo,
        "config",
        "--get",
        f"branch.{branch}.remote",
        check=False,
    )
    merge_result = _git(
        repo,
        "config",
        "--get",
        f"branch.{branch}.merge",
        check=False,
    )
    remote = remote_result.stdout.strip()
    source_ref = merge_result.stdout.strip()
    if (
        remote_result.returncode != 0
        or merge_result.returncode != 0
        or not remote
        or remote == "."
        or not source_ref.startswith("refs/heads/")
    ):
        return None
    remote_branch = source_ref.removeprefix("refs/heads/")
    tracking_ref = f"refs/remotes/{remote}/{remote_branch}"
    return {
        "remote": remote,
        "source_ref": source_ref,
        "tracking_ref": tracking_ref,
        "refspec": f"+{source_ref}:{tracking_ref}",
    }


def _refresh_configured_remotes(
    repo: Path,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    refspecs_by_remote = _configured_remote_refspecs(repo, plan)
    for remote, refspecs in refspecs_by_remote.items():
        _git(
            repo,
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            remote,
            *refspecs,
        )
    return {"requested": True, "remotes": list(refspecs_by_remote)}


def _resolved_state(repo: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    integration_branch = str(plan["integration_branch"])
    integration_remote = _integration_remote(repo, plan)
    resolved_integration_remote = (
        {
            "remote": integration_remote["remote"],
            "ref": integration_remote["tracking_ref"],
            "sha": _resolve_optional_commit(
                repo, integration_remote["tracking_ref"]
            ),
        }
        if integration_remote is not None
        else None
    )
    return {
        "base": {
            "ref": plan["base_ref"],
            "sha": _resolve_commit(repo, str(plan["base_ref"])),
        },
        "integration": {
            "branch": integration_branch,
            "sha": _resolve_optional_commit(repo, f"refs/heads/{integration_branch}"),
            "remote": resolved_integration_remote,
        },
        "sources": [
            {"ref": ref, "sha": _resolve_commit(repo, str(ref))}
            for ref in plan["source_refs"]
        ],
    }


def _drift_reasons(
    repo: Path,
    plan: Mapping[str, Any],
    resolved: Mapping[str, Any],
) -> list[dict[str, Any]]:
    receipt = plan.get("last_sync")
    if not isinstance(receipt, Mapping):
        return [{"kind": "never_synced"}]

    reasons: list[dict[str, Any]] = []
    base = resolved["base"]
    if receipt.get("base_sha") != base["sha"]:
        reasons.append(
            {
                "kind": "base_ref_moved",
                "ref": base["ref"],
                "previous_sha": receipt.get("base_sha"),
                "current_sha": base["sha"],
            }
        )

    integration = resolved["integration"]
    if integration["sha"] is None:
        reasons.append(
            {
                "kind": "integration_branch_missing",
                "branch": integration["branch"],
            }
        )
    elif receipt.get("integration_sha") != integration["sha"]:
        reasons.append(
            {
                "kind": "integration_head_changed",
                "branch": integration["branch"],
                "previous_sha": receipt.get("integration_sha"),
                "current_sha": integration["sha"],
            }
        )

    integration_remote = integration.get("remote")
    remote_sha = (
        integration_remote.get("sha")
        if isinstance(integration_remote, Mapping)
        else None
    )
    local_sha = integration.get("sha")
    if (
        isinstance(remote_sha, str)
        and isinstance(local_sha, str)
        and not _is_ancestor(repo, remote_sha, local_sha)
    ):
        relation = (
            "remote_ahead"
            if _is_ancestor(repo, local_sha, remote_sha)
            else "diverged"
        )
        reasons.append(
            {
                "kind": "integration_remote_head_moved",
                "branch": integration["branch"],
                "remote_ref": integration_remote["ref"],
                "local_sha": local_sha,
                "remote_sha": remote_sha,
                "relation": relation,
            }
        )

    receipt_sources = receipt.get("sources")
    normalized_receipt_sources = (
        receipt_sources if isinstance(receipt_sources, list) else []
    )
    previous_by_ref = {
        str(item.get("ref")): item.get("sha")
        for item in normalized_receipt_sources
        if isinstance(item, Mapping)
    }
    current_refs = [str(item["ref"]) for item in resolved["sources"]]
    if list(previous_by_ref) != current_refs:
        reasons.append(
            {
                "kind": "source_set_changed",
                "previous_refs": list(previous_by_ref),
                "current_refs": current_refs,
            }
        )
    for source in resolved["sources"]:
        previous_sha = previous_by_ref.get(str(source["ref"]))
        if previous_sha != source["sha"]:
            reasons.append(
                {
                    "kind": "source_ref_moved",
                    "ref": source["ref"],
                    "previous_sha": previous_sha,
                    "current_sha": source["sha"],
                }
            )
    return reasons


def configure_integration_branch(
    *,
    repo_path: str | Path,
    base_ref: str,
    integration_branch: str,
    source_refs: Sequence[str],
    plan_file: str | Path | None = None,
    replace: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    repo = _repository_root(repo_path)
    path = _plan_path(repo, plan_file)
    _assert_ignored_plan(repo, path)
    plan = _validate_plan(
        repo,
        {
            "schema_version": PLAN_SCHEMA_VERSION,
            "base_ref": base_ref,
            "integration_branch": integration_branch,
            "source_refs": list(source_refs),
            "last_sync": None,
        },
    )
    resolved = _resolved_state(repo, plan)

    existing: dict[str, Any] | None = None
    if path.exists():
        existing = _read_plan(repo, path)
        same_definition = all(
            existing[key] == plan[key]
            for key in ("base_ref", "integration_branch", "source_refs")
        )
        if same_definition:
            plan = existing
        elif not replace:
            raise IntegrationBranchError(
                "integration branch plan already exists with different values; "
                "pass `--replace` to reset its sync receipt"
            )

    changed = existing != plan
    if execute and changed:
        _write_plan(path, plan)
    return {
        "ok": True,
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "configured" if execute else "preview",
        "changed": changed,
        "executed": execute,
        "plan_file": str(path),
        "plan": plan,
        "resolved": resolved,
        "write_boundary": (
            "local ignored plan only; no branch, source ref, remote, or PR write"
        ),
    }


def integration_branch_status(
    *,
    repo_path: str | Path,
    plan_file: str | Path | None = None,
    refresh_remotes: bool = False,
) -> dict[str, Any]:
    repo = _repository_root(repo_path)
    path = _plan_path(repo, plan_file)
    _assert_ignored_plan(repo, path)
    plan = _read_plan(repo, path)
    remote_refresh = (
        _refresh_configured_remotes(repo, plan)
        if refresh_remotes
        else {"requested": False, "remotes": []}
    )
    resolved = _resolved_state(repo, plan)
    reasons = _drift_reasons(repo, plan, resolved)
    return {
        "ok": True,
        "schema_version": STATUS_SCHEMA_VERSION,
        "status": "in_sync" if not reasons else "drifted",
        "sync_required": bool(reasons),
        "plan_file": str(path),
        "plan": plan,
        "resolved": resolved,
        "drift_reasons": reasons,
        "remote_refresh": remote_refresh,
        "write_boundary": (
            "remote-read-only integration inputs; optional refresh updates only "
            "configured local remote-tracking refs"
        ),
    }


def _worktree_for_branch(repo: Path, branch: str) -> Path | None:
    result = _git(repo, "worktree", "list", "--porcelain")
    current_path: Path | None = None
    target_ref = f"refs/heads/{branch}"
    # `--porcelain` frames records on LF and prints paths verbatim, so a path
    # containing U+0085/U+2028/U+2029 would be split by `str.splitlines()` and
    # the prefix returned as the worktree path. Frame on the writer's separator.
    for line in result.stdout.split("\n"):
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree ")).resolve()
        elif line == f"branch {target_ref}":
            return current_path
        elif not line:
            current_path = None
    return None


def _clean_worktree(path: Path) -> bool:
    return not _git(
        path,
        "status",
        "--porcelain",
        "--untracked-files=all",
    ).stdout.strip()


def _build_candidate(
    repo: Path,
    *,
    start_sha: str,
    sources: Sequence[Mapping[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    temporary_root = Path(tempfile.mkdtemp(prefix="loopx-integration-branch-"))
    worktree = temporary_root / "candidate"
    added = False
    try:
        _git(
            repo,
            "worktree",
            "add",
            "--detach",
            "--quiet",
            str(worktree),
            start_sha,
        )
        added = True
        for source in sources:
            result = _git(
                worktree,
                "-c",
                "commit.gpgSign=false",
                "merge",
                "--no-ff",
                "--no-edit",
                "--no-gpg-sign",
                source["sha"],
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                return None, {
                    "status": "merge_failed",
                    "source_ref": source["ref"],
                    "source_sha": source["sha"],
                    "error": detail,
                }
        return _resolve_commit(worktree, "HEAD"), None
    finally:
        if added:
            _git(repo, "worktree", "remove", "--force", str(worktree), check=False)
        shutil.rmtree(temporary_root, ignore_errors=True)


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        _git(
            repo,
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            check=False,
        ).returncode
        == 0
    )


def _incremental_sources(
    repo: Path,
    *,
    plan: Mapping[str, Any],
    resolved: Mapping[str, Any],
) -> list[Mapping[str, Any]] | None:
    receipt = plan.get("last_sync")
    if not isinstance(receipt, Mapping):
        return None
    integration_sha = resolved["integration"]["sha"]
    if (
        not isinstance(integration_sha, str)
        or receipt.get("integration_sha") != integration_sha
        or receipt.get("base_sha") != resolved["base"]["sha"]
    ):
        return None

    receipt_sources = receipt.get("sources")
    if not isinstance(receipt_sources, list) or len(receipt_sources) != len(
        resolved["sources"]
    ):
        return None

    moved: list[Mapping[str, Any]] = []
    for previous, current in zip(receipt_sources, resolved["sources"], strict=True):
        if not isinstance(previous, Mapping) or previous.get("ref") != current["ref"]:
            return None
        previous_sha = previous.get("sha")
        if previous_sha == current["sha"]:
            continue
        if not isinstance(previous_sha, str) or not _is_ancestor(
            repo,
            previous_sha,
            str(current["sha"]),
        ):
            return None
        moved.append(current)
    return moved or None


def _update_integration_branch(
    repo: Path,
    *,
    branch: str,
    expected_old_sha: str | None,
    candidate_sha: str,
) -> None:
    ref = f"refs/heads/{branch}"
    current_sha = _resolve_optional_commit(repo, ref)
    if current_sha != expected_old_sha:
        raise IntegrationBranchError(
            "integration branch changed while the candidate was being built; rerun sync"
        )
    checked_out_path = _worktree_for_branch(repo, branch)
    if checked_out_path is not None:
        if not _clean_worktree(checked_out_path):
            raise IntegrationBranchError(
                f"integration branch worktree is dirty: {checked_out_path}"
            )
        _git(checked_out_path, "reset", "--hard", candidate_sha)
        return
    _git(repo, "update-ref", ref, candidate_sha, expected_old_sha or ZERO_SHA)


def _assert_inputs_unchanged(
    repo: Path,
    *,
    plan_path: Path,
    plan: Mapping[str, Any],
    resolved: Mapping[str, Any],
) -> None:
    if _read_plan(repo, plan_path) != plan:
        raise IntegrationBranchError(
            "integration branch plan changed while the candidate was being built; "
            "rerun sync"
        )
    if _resolve_commit(repo, str(resolved["base"]["ref"])) != resolved["base"]["sha"]:
        raise IntegrationBranchError(
            "base ref changed while the candidate was being built; rerun sync"
        )
    for source in resolved["sources"]:
        if _resolve_commit(repo, str(source["ref"])) != source["sha"]:
            raise IntegrationBranchError(
                f"source ref `{source['ref']}` changed while the candidate was "
                "being built; rerun sync"
            )
    integration_remote = resolved["integration"].get("remote")
    if isinstance(integration_remote, Mapping) and _resolve_optional_commit(
        repo, str(integration_remote["ref"])
    ) != integration_remote.get("sha"):
        raise IntegrationBranchError(
            "integration remote ref changed while the candidate was being built; "
            "rerun sync"
        )


def _resolve_supplied_candidate(
    repo: Path,
    *,
    candidate_ref: str,
    resolved: Mapping[str, Any],
) -> str:
    candidate_sha = _resolve_commit(repo, candidate_ref)
    required_inputs = [
        ("base", resolved["base"]["ref"], resolved["base"]["sha"]),
        *[("source", source["ref"], source["sha"]) for source in resolved["sources"]],
    ]
    integration_remote = resolved["integration"].get("remote")
    if isinstance(integration_remote, Mapping) and isinstance(
        integration_remote.get("sha"), str
    ):
        required_inputs.append(
            (
                "integration remote",
                integration_remote["ref"],
                integration_remote["sha"],
            )
        )
    for kind, ref, sha in required_inputs:
        result = _git(
            repo,
            "merge-base",
            "--is-ancestor",
            str(sha),
            candidate_sha,
            check=False,
        )
        if result.returncode != 0:
            raise IntegrationBranchError(
                f"supplied candidate `{candidate_ref}` does not contain "
                f"{kind} `{ref}` at `{sha}`"
            )
    return candidate_sha


def _remote_reconciliation_candidate_inputs(
    repo: Path,
    *,
    resolved: Mapping[str, Any],
) -> tuple[str, list[Mapping[str, Any]]] | None:
    integration = resolved["integration"]
    integration_remote = integration.get("remote")
    if not isinstance(integration_remote, Mapping):
        return None
    remote_sha = integration_remote.get("sha")
    local_sha = integration.get("sha")
    if not isinstance(remote_sha, str):
        return None
    if isinstance(local_sha, str) and _is_ancestor(repo, remote_sha, local_sha):
        return None

    required_inputs: list[Mapping[str, Any]] = [
        {"ref": resolved["base"]["ref"], "sha": resolved["base"]["sha"]},
        *resolved["sources"],
    ]
    if not isinstance(local_sha, str) or _is_ancestor(repo, local_sha, remote_sha):
        return remote_sha, required_inputs
    return (
        local_sha,
        [
            {"ref": integration_remote["ref"], "sha": remote_sha},
            *required_inputs,
        ],
    )


def sync_integration_branch(
    *,
    repo_path: str | Path,
    plan_file: str | Path | None = None,
    candidate_ref: str | None = None,
    refresh_remotes: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    repo = _repository_root(repo_path)
    status = integration_branch_status(
        repo_path=repo,
        plan_file=plan_file,
        refresh_remotes=refresh_remotes,
    )
    if not status["sync_required"]:
        integration_sha = status["resolved"]["integration"]["sha"]
        return {
            "ok": True,
            "schema_version": SYNC_SCHEMA_VERSION,
            "status": "already_in_sync",
            "executed": execute,
            "updated": False,
            "candidate_sha": integration_sha,
            "candidate_tree_sha": (
                _resolve_tree(repo, integration_sha)
                if isinstance(integration_sha, str)
                else None
            ),
            "status_packet": status,
        }

    resolved = status["resolved"]
    branch = str(status["plan"]["integration_branch"])

    candidate_source = "supplied" if candidate_ref is not None else "built"
    if candidate_ref is not None:
        candidate_sha = _resolve_supplied_candidate(
            repo,
            candidate_ref=candidate_ref,
            resolved=resolved,
        )
    else:
        remote_reconciliation = _remote_reconciliation_candidate_inputs(
            repo,
            resolved=resolved,
        )
        if remote_reconciliation is not None:
            candidate_source = "remote_reconciled"
            start_sha, sources = remote_reconciliation
        else:
            incremental_sources = _incremental_sources(
                repo,
                plan=status["plan"],
                resolved=resolved,
            )
            if incremental_sources is not None:
                candidate_source = "incremental"
                start_sha = str(resolved["integration"]["sha"])
                sources = incremental_sources
            else:
                start_sha = str(resolved["base"]["sha"])
                sources = resolved["sources"]
        candidate_sha, failure = _build_candidate(
            repo,
            start_sha=start_sha,
            sources=sources,
        )
        if failure is not None:
            return {
                "ok": False,
                "schema_version": SYNC_SCHEMA_VERSION,
                **failure,
                "executed": False,
                "updated": False,
                "integration_unchanged": True,
                "status_packet": status,
            }
    assert candidate_sha is not None
    candidate_tree_sha = _resolve_tree(repo, candidate_sha)
    if not execute:
        return {
            "ok": True,
            "schema_version": SYNC_SCHEMA_VERSION,
            "status": "preview_ready",
            "executed": False,
            "updated": False,
            "candidate_sha": candidate_sha,
            "candidate_tree_sha": candidate_tree_sha,
            "candidate_source": candidate_source,
            "status_packet": status,
            "write_boundary": (
                "candidate commit and optional configured remote-tracking refs only; "
                "integration and source branches unchanged"
            ),
        }

    _assert_inputs_unchanged(
        repo,
        plan_path=Path(status["plan_file"]),
        plan=status["plan"],
        resolved=resolved,
    )
    _assert_ignored_plan(repo, Path(status["plan_file"]))
    branch_updated = candidate_sha != resolved["integration"]["sha"]
    if branch_updated:
        _update_integration_branch(
            repo,
            branch=branch,
            expected_old_sha=resolved["integration"]["sha"],
            candidate_sha=candidate_sha,
        )
    plan = dict(status["plan"])
    plan["last_sync"] = {
        "base_sha": resolved["base"]["sha"],
        "integration_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "candidate_source": candidate_source,
        "sources": [
            {"ref": source["ref"], "sha": source["sha"]}
            for source in resolved["sources"]
        ],
    }
    _write_plan(Path(status["plan_file"]), plan)
    refreshed = integration_branch_status(repo_path=repo, plan_file=plan_file)
    refreshed["remote_refresh"] = status["remote_refresh"]
    if refreshed["sync_required"]:
        raise IntegrationBranchError(
            "integration branch sync did not produce an in-sync readback"
        )
    return {
        "ok": True,
        "schema_version": SYNC_SCHEMA_VERSION,
        "status": "synced",
        "executed": True,
        "updated": branch_updated,
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "candidate_source": candidate_source,
        "status_packet": refreshed,
        "write_boundary": (
            "local integration branch, ignored sync receipt, and optional configured "
            "remote-tracking refs only; source branches and remote repositories unchanged"
        ),
    }
