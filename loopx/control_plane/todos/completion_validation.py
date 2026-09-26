from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from json import loads as json_loads
from pathlib import Path
from typing import Any, cast

from ...history import load_registry
from ...materials import find_registry_goal, goal_repo
from ..agents.delivery_workspace import normalize_delivery_workspace_snapshot
from ..agents.workspace_guard import (
    capture_delivery_workspace,
    delivery_workspace_repository,
)
from ..runtime.validation_command import (
    CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
    run_caller_validation,
)
from .active_state_editing import find_todo_block
from .completion_policy import (
    build_completion_policy_request,
    linked_successors_from_state,
)
from .completion_transaction import (
    reduce_todo_completion_transaction,
    todo_completion_source_snapshot,
)
from .completion_validation_projection import (
    completion_validation_declaration,
    completion_validation_declaration_sha256,
)
from .completion_validation_store import (
    persist_completion_validation_declaration,
    read_completion_validation_declaration,
)
from .contract import TODO_STATUS_DONE, normalize_todo_status

# Kept safely under the 30s outer CLI/MCP subprocess budget so a timed-out
# validation still produces a typed receipt before the outer call is killed.
_COMPLETION_VALIDATION_TIMEOUT_SECONDS = 20
# Per-todo overrides (declared on `todo add`) must also stay under that outer
# budget for the same reason; the writer-side range check enforces this.
COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS = 29


def normalize_validation_command_json(raw: str | None) -> list[str] | None:
    """Decode the run-once argv form used by Todo completion validation."""

    if raw is None:
        return None
    try:
        argv = json_loads(raw)
    except ValueError as exc:
        raise ValueError(
            "--validation-command-json must be a JSON string array"
        ) from exc
    if not isinstance(argv, list) or not argv or not all(
        isinstance(item, str) and item for item in argv
    ):
        raise ValueError("--validation-command-json must be a JSON string array")
    return argv


def _resolve_goal_repo_workspace(registry_path: Path, goal_id: str) -> Path | None:
    """Resolve the goal's repository directory to use as the validation workspace."""
    goal = find_registry_goal(load_registry(registry_path), goal_id)
    if goal is None:
        return None
    repo = goal_repo(goal)
    if repo is None or not repo.is_dir():
        return None
    return cast(Path, repo)


def _workspace_failure(label: str, *, status: str, summary: str) -> dict[str, Any]:
    """Return a path-free completion-validation workspace receipt."""

    return {
        "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
        "command_label": label,
        "exit_code": None,
        "passed": False,
        "status": status,
        "summary": summary,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }


def _git_workspace_is_clean(path: Path) -> bool | None:
    """Return clean/dirty without exposing local paths or command output."""

    try:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=normal"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return not bool(result.stdout.strip())


def _resolve_completion_validation_workspace(
    *,
    registry_path: Path,
    goal_id: str,
    task_repository: str | None,
    delivery_workspace: Mapping[str, Any] | None,
    validation_workspace_path: Path | None,
    label: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    """Resolve the authority-bound workspace for one validation effect.

    The Goal repository remains the default when a Todo does not select a
    repository.  When the host supplies a path-free, turn-bound delivery
    workspace snapshot, validation must honor that exact clean independent
    worktree even when it has the same canonical repository identity as the
    Goal checkout. The snapshot may come from prior writeback or from the
    host's exact-Turn pre-completion check because completion itself precedes
    refresh. The candidate path is host execution context, never a public CLI
    cwd override.
    """

    goal_workspace = _resolve_goal_repo_workspace(registry_path, goal_id)
    if goal_workspace is None:
        return None, _workspace_failure(
            label,
            status="workspace_unavailable",
            summary=(
                "validation_command is declared but the goal has no repository "
                "workspace to run it in"
            ),
        )
    expected_repository = str(task_repository or "").strip()
    if not expected_repository:
        return goal_workspace, None

    recorded = None
    if delivery_workspace is not None:
        try:
            recorded = normalize_delivery_workspace_snapshot(delivery_workspace)
        except (RuntimeError, TypeError, ValueError):
            # Decoder rejection is an input/receipt failure, not an adapter crash.
            # Keep it inside the path-free completion state model and never expose
            # the TypeScript decoder's internal error text at the CLI/Turn boundary.
            return None, _workspace_failure(
                label,
                status="workspace_receipt_invalid",
                summary=(
                    "the recorded delivery workspace receipt is invalid and cannot "
                    "authorize completion validation"
                ),
            )

    if recorded is None:
        goal_snapshot = capture_delivery_workspace(goal_workspace)
        if delivery_workspace_repository(goal_snapshot) == expected_repository:
            return goal_workspace, None
        return None, _workspace_failure(
            label,
            status="workspace_receipt_unavailable",
            summary=(
                "validation outside the Goal repository requires a verified "
                "delivery workspace receipt for the selected Todo"
            ),
        )
    if (
        delivery_workspace_repository(recorded) != expected_repository
        or recorded.get("workspace_kind") != "independent_git_worktree"
    ):
        return None, _workspace_failure(
            label,
            status="workspace_receipt_mismatch",
            summary=(
                "the recorded delivery workspace does not match the selected "
                "Todo repository and isolation contract"
            ),
        )

    candidate = validation_workspace_path or Path.cwd()
    current = capture_delivery_workspace(
        candidate,
        peer_independent_worktree_required=True,
    )
    if current is None:
        return None, _workspace_failure(
            label,
            status="workspace_unverified",
            summary=(
                "recorded delivery-workspace validation requires a verifiable "
                "independent Git worktree"
            ),
        )
    if (
        delivery_workspace_repository(current) != expected_repository
        or current.get("workspace_kind") != "independent_git_worktree"
        or (
            recorded.get("workspace_revision_digest") is not None
            and current.get("workspace_revision_digest")
            != recorded.get("workspace_revision_digest")
        )
    ):
        return None, _workspace_failure(
            label,
            status="workspace_repository_mismatch",
            summary=(
                "the current validation worktree does not match the recorded "
                "delivery workspace"
            ),
        )
    clean = _git_workspace_is_clean(candidate)
    if clean is not True:
        return None, _workspace_failure(
            label,
            status=("workspace_dirty" if clean is False else "workspace_unverified"),
            summary=(
                "recorded delivery-workspace validation requires a clean, "
                "verifiable delivery worktree"
            ),
        )
    return candidate, None


def _materialized_todo_item(
    *, state_file: Path, todo_id: str, role: str | None
) -> dict[str, Any] | None:
    try:
        lines = state_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    match = find_todo_block(lines, todo_id=todo_id, role=role)
    if not match:
        return None
    item_role, _section, _start, _end, block = match
    item = dict(block)
    item["role"] = item_role
    return item


def _run_declared_completion_validation(
    *,
    validation_command: str | None,
    validation_argv: list[str] | None,
    validation_label: str | None,
    validation_timeout_seconds: int | None,
    registry_path: Path,
    goal_id: str,
    task_repository: str | None = None,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> dict[str, Any] | None:
    """Run a todo's declared caller-approved validation command.

    Returns ``None`` when no command form is declared (the unchanged fast
    path). ``validation_argv`` (the JSON argv form declared on ``todo add``)
    takes the run-once no-shell path; ``validation_command`` keeps the
    legacy shlex form. ``validation_timeout_seconds`` overrides the module
    default when declared on ``todo add``; ``None`` keeps the default.
    Otherwise always returns a privacy-safe receipt whose ``passed`` is True
    only when the command ran and exited zero; setup failures (no repository
    workspace), timeouts, missing executables, and malformed commands are all
    reported as ``passed=False`` receipts rather than raised, so completion
    can surface a typed failure without committing.
    """
    if not validation_command and validation_argv is None:
        return None
    timeout_seconds = (
        validation_timeout_seconds
        if validation_timeout_seconds is not None
        else _COMPLETION_VALIDATION_TIMEOUT_SECONDS
    )
    label = validation_label or "todo completion validation"
    workspace, workspace_failure = _resolve_completion_validation_workspace(
        registry_path=registry_path,
        goal_id=goal_id,
        task_repository=task_repository,
        delivery_workspace=delivery_workspace,
        validation_workspace_path=validation_workspace_path,
        label=label,
    )
    if workspace_failure is not None:
        return workspace_failure
    assert workspace is not None
    try:
        if validation_argv is not None:
            return cast(
                dict[str, Any],
                run_caller_validation(
                    workspace,
                    validation_argv=validation_argv,
                    validation_label=label,
                    timeout_seconds=timeout_seconds,
                ),
            )
        return cast(
            dict[str, Any],
            run_caller_validation(
                workspace,
                validation_command=str(validation_command),
                validation_label=label,
                timeout_seconds=timeout_seconds,
            ),
        )
    except subprocess.TimeoutExpired:
        return {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "timeout",
            "summary": (
                f"validation command timed out after {timeout_seconds}s"
            ),
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }
    except (FileNotFoundError, PermissionError):
        return {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "command_not_run",
            "summary": (
                "validation command could not be launched because its executable "
                "is unavailable"
            ),
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }
    except ValueError as exc:
        # shlex.split rejects malformed (e.g. unbalanced-quote) commands, and
        # an argv form that collapsed to [] (corrupted stored declaration)
        # fails the runner's own empty-command check.
        return {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "command_malformed",
            "summary": f"validation command could not be parsed: {exc}",
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }


def run_declared_completion_validation_effect(
    *,
    effect: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Execute exactly one TypeScript-authorized validation effect.

    This is an effect adapter only: the TS completion transaction owns whether
    validation is required and validates the returned privacy-safe receipt on
    re-entry. No Todo or authority state is read or changed here.
    """

    if effect.get("kind") != "caller_validation":
        raise ValueError("unsupported Todo completion validation effect")
    raw_argv = effect.get("validation_argv")
    if raw_argv is not None and not (
        isinstance(raw_argv, list)
        and raw_argv
        and all(isinstance(item, str) and item for item in raw_argv)
    ):
        raise ValueError("validation_effect.validation_argv must be a string array")
    declaration_digest = effect.get("validation_declaration_sha256")
    if declaration_digest is not None and (
        not isinstance(declaration_digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", declaration_digest)
    ):
        raise ValueError(
            "validation_effect.validation_declaration_sha256 must be a SHA-256 digest"
        )
    receipt = _run_declared_completion_validation(
        validation_command=(
            str(effect["validation_command"])
            if effect.get("validation_command") is not None
            else None
        ),
        validation_argv=list(raw_argv) if isinstance(raw_argv, list) else None,
        validation_label=(
            str(effect["validation_label"])
            if effect.get("validation_label") is not None
            else None
        ),
        validation_timeout_seconds=(
            int(effect["validation_timeout_seconds"])
            if effect.get("validation_timeout_seconds") is not None
            else None
        ),
        registry_path=registry_path,
        goal_id=goal_id,
        task_repository=(
            str(effect["task_repository"])
            if effect.get("task_repository") is not None
            else None
        ),
        delivery_workspace=delivery_workspace,
        validation_workspace_path=validation_workspace_path,
    )
    if receipt is None:
        raise RuntimeError("authorized validation effect produced no receipt")
    if declaration_digest is not None:
        receipt["validation_declaration_sha256"] = declaration_digest
    return receipt


def resolve_private_completion_validation_declaration(
    *,
    canonical_todo: Mapping[str, Any],
    state_file: Path,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    role: str | None,
    persist_if_resolved: bool,
) -> dict[str, Any] | None:
    """Resolve private effect detail and bind it to the canonical public digest."""

    required = canonical_todo.get("completion_validation_required") is True
    expected = canonical_todo.get("completion_validation_sha256")
    if not required:
        if expected is not None:
            raise ValueError(
                "canonical Todo has a completion validation digest without authority"
            )
        return None
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(
            "canonical Todo requires completion validation but omits its digest"
        )
    # The canonical digest selects immutable private content, including after
    # a lost write response. Older installs may still use a per-Todo sidecar.
    # Corruption in the selected source fails closed; only absence permits
    # digest-checked rehydration from a materialized projection below.
    declaration = read_completion_validation_declaration(
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
        expected_digest=expected,
    )
    if declaration is None:
        source = _materialized_todo_item(
            state_file=state_file,
            todo_id=todo_id,
            role=role,
        )
        declaration = (
            completion_validation_declaration(source)
            if isinstance(source, dict)
            else None
        )
    if declaration is None:
        raise ValueError(
            "private completion validation declaration is unavailable for canonical Todo"
        )
    if completion_validation_declaration_sha256(declaration) != expected:
        raise ValueError(
            "private completion validation declaration does not match canonical Todo digest"
        )
    if persist_if_resolved:
        persist_completion_validation_declaration(
            runtime_root=runtime_root,
            goal_id=goal_id,
            todo_id=todo_id,
            declaration=declaration,
        )
    return declaration


def run_completion_validation_gate_with_source(
    *,
    state_file: Path,
    todo_id: str,
    role: str | None,
    registry_path: Path,
    goal_id: str,
    dry_run: bool,
    no_followup: bool = False,
    completion_turn_key: str | None = None,
    completion_identity_source: str | None = None,
    requested_has_successor: bool = False,
    completion_policy_facts: Mapping[str, Any] | None = None,
    requested_successor_todo_ids: list[str] | None = None,
    completion_delivery_workspace: Mapping[str, Any] | None = None,
    completion_validation_workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Run the caller-approved completion validation gate, OUTSIDE the mutation lock.

    Returns one envelope containing the source snapshot and typed transaction.
    ``failure`` is a ``validation_blocked_completion`` payload when a declared
    command does not pass, otherwise ``None``. The caller returns failures
    unchanged so durable writeback and quota spend are skipped. Dry runs and
    terminal replays do not execute validation. This function is read-only
    w.r.t. the state file and safe to call before acquiring the mutation lock,
    so a multi-second validation command does not block concurrent Todo writes.
    """
    from ...history import load_registry
    from ...registry import registry_goals
    from ..goals.legacy_event_source import require_no_legacy_todo_events
    goal = next((item for item in registry_goals(load_registry(registry_path)) if item.get("id") == goal_id), {})
    require_no_legacy_todo_events(goal, state_path=state_file)
    projection_source = "materialized"
    source_authority: dict[str, Any] | None = None
    todo = _materialized_todo_item(state_file=state_file, todo_id=todo_id, role=role)
    if todo is None:
        return {"todo": None, "validation": None, "failure": None,
            "source_authority": None, "source_snapshot": None, "transaction": None}
    source_snapshot = todo_completion_source_snapshot(todo)
    completion_policy_source = None
    if completion_policy_facts is not None:
        try:
            lines = state_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        completion_policy_source = completion_policy_source_from_state(
            registry_path=registry_path,
            goal_id=goal_id,
            lines=lines,
            successor_todo_ids=requested_successor_todo_ids or [],
            facts=completion_policy_facts,
        )
    transaction = reduce_todo_completion_transaction(
        todo=todo,
        projection_source=projection_source,
        completion_turn_key=completion_turn_key,
        no_followup=no_followup,
        dry_run=dry_run,
        completion_identity_source=completion_identity_source,
        goal_id=goal_id,
        todo_id=todo_id,
        requested_has_successor=requested_has_successor,
        validation_receipt=None,
        # The coarse reducer returns policy success or typed failure as data.
        # The public writer consumes that projection only after actor and lease
        # admission, preserving legacy error priority without a second IPC.
        completion_policy_request=completion_policy_source,
    )
    completion_validation = None
    if transaction["decision"] == "execute_validation":
        effect = transaction["validation_effect"]
        completion_validation = run_declared_completion_validation_effect(
            effect=effect,
            registry_path=registry_path,
            goal_id=goal_id,
            delivery_workspace=completion_delivery_workspace,
            validation_workspace_path=completion_validation_workspace_path,
        )
        if completion_validation is None:
            raise RuntimeError("Todo completion validation effect produced no receipt")
        transaction = reduce_todo_completion_transaction(
            todo=todo,
            projection_source=projection_source,
            completion_turn_key=completion_turn_key,
            no_followup=no_followup,
            dry_run=dry_run,
            completion_identity_source=completion_identity_source,
            goal_id=goal_id,
            todo_id=todo_id,
            requested_has_successor=requested_has_successor,
            validation_receipt=completion_validation,
            completion_policy_request=completion_policy_source,
        )
    if transaction["decision"] != "reject":
        return {
            "failure": None,
            "source_authority": source_authority,
            "source_snapshot": source_snapshot,
            "completion_policy_source": completion_policy_source,
            "transaction": transaction,
        }
    failure_payload = transaction["failure"]
    completion_validation = dict(failure_payload["validation_receipt"])
    failure = {
        "ok": False,
        "dry_run": dry_run,
        "completed": False,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "changed": False,
        "validation": completion_validation,
        "validation_blocked_completion": True,
    }
    return {
        "failure": failure,
        "source_authority": source_authority,
        "source_snapshot": source_snapshot,
        "completion_policy_source": completion_policy_source,
        "transaction": transaction,
    }


def completion_policy_source_from_state(
    *,
    registry_path: Path,
    goal_id: str,
    lines: list[str],
    successor_todo_ids: list[str],
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Project lock-comparable facts for the TS completion policy."""

    return cast(
        dict[str, Any],
        build_completion_policy_request(
            registry_path=registry_path,
            goal_id=goal_id,
            claimed_by=facts.get("claimed_by"),
            next_claimed_by=facts.get("next_claimed_by"),
            next_agent_todo=facts.get("next_agent_todo"),
            next_action_kind=facts.get("next_action_kind"),
            next_continuation_policy=facts.get("next_continuation_policy"),
            next_excluded_agents=facts.get("next_excluded_agents") or [],
            self_merged=bool(facts.get("self_merged")),
            evidence=facts.get("evidence"),
            linked_successors=linked_successors_from_state(
                lines=lines,
                successor_todo_ids=successor_todo_ids,
            ),
        ),
    )


def prepare_user_todo_update_completion(
    *,
    status: str | None,
    state_file: Path,
    todo_id: str,
    role: str | None,
    registry_path: Path,
    goal_id: str,
    dry_run: bool,
    no_followup: bool,
    requested_has_successor: bool,
) -> dict[str, Any] | None:
    """Prepare the coarse transaction for a direct user-Todo completion."""

    if normalize_todo_status(status) != TODO_STATUS_DONE:
        return None
    match = find_todo_block(
        state_file.read_text(encoding="utf-8").splitlines(),
        todo_id=todo_id,
        role=role,
    )
    if match is None or (role or match[0]) == "agent":
        return None
    return run_completion_validation_gate_with_source(
        state_file=state_file,
        todo_id=todo_id,
        role=role,
        registry_path=registry_path,
        goal_id=goal_id,
        dry_run=dry_run,
        no_followup=no_followup,
        requested_has_successor=requested_has_successor,
    )


def locked_todo_completion_source(
    *,
    lines: list[str],
    state_file: Path,
    project: Path | None,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    role: str | None,
) -> tuple[Any, dict[str, Any] | None]:
    """Resolve the materialized Todo under the write lock."""

    match = find_todo_block(lines, todo_id=todo_id, role=role)
    if match:
        item_role, _section, _start, _end, block = match
        todo = dict(block)
        todo["role"] = item_role
        return match, todo
    return None, None


def execute_completion_validation_effects(
    result: Mapping[str, Any], *, registry_path: Path, goal_id: str,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Execute only the declared effects; the TS caller owns admission/resume."""
    updates: dict[str, Any] = {}
    effect = result.get("validation_effect")
    acceptance_effects = result.get("goal_acceptance_validation_effects")
    if not isinstance(effect, Mapping) and not isinstance(acceptance_effects, list):
        raise RuntimeError("Todo terminal validation effect shape mismatch")
    if isinstance(effect, Mapping):
        updates["validation_receipt"] = run_declared_completion_validation_effect(
            effect=effect,
            registry_path=registry_path,
            goal_id=goal_id,
            delivery_workspace=delivery_workspace,
            validation_workspace_path=validation_workspace_path,
        )
    if acceptance_effects is not None:
        from ..goals.acceptance import run_goal_acceptance_validation_effect

        if not isinstance(acceptance_effects, list) or not acceptance_effects:
            raise RuntimeError("Goal acceptance validation effects are missing")
        source_binding = result.get("goal_acceptance_source_binding")
        if not isinstance(source_binding, Mapping):
            raise RuntimeError("Goal acceptance validation omitted its source basis")
        receipts = []
        for row in acceptance_effects:
            if not isinstance(row, Mapping) or not isinstance(row.get("effect"), Mapping):
                raise RuntimeError("Goal acceptance validation effect shape mismatch")
            receipt = run_goal_acceptance_validation_effect(
                effect=row["effect"], registry_path=registry_path, goal_id=goal_id,
                delivery_workspace=delivery_workspace,
                validation_workspace_path=validation_workspace_path,
            )
            receipts.append({"criterion_id": row.get("criterion_id"), "receipt": receipt})
        updates["goal_acceptance_source_binding"] = dict(source_binding)
        updates["goal_acceptance_validation_receipts"] = receipts
    return updates


def completion_validation_failure(
    result: Mapping[str, Any], *, goal_id: str, todo_id: str, dry_run: bool
) -> dict[str, Any] | None:
    if result.get("status") != "failed":
        return None
    if result.get("reason_code") not in {
        "validation_declaration_invalid",
        "validation_failed",
    }:
        return None
    return {
        "ok": False,
        "dry_run": dry_run,
        "completed": False,
        "changed": False,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "validation_blocked_completion": True,
        "reason": result.get("reason"),
        "validation_failure": result.get("validation_failure"),
        **dict(result),
    }
