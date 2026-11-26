from __future__ import annotations

from pathlib import Path
from typing import Any

from .capabilities.issue_fix.pr_gate_reconcile import (
    reconcile_acknowledged_issue_fix_pr_reviews,
)
from .capabilities.turn_start import extend_turn_start_dispatch
from .control_plane.capability_hooks import (
    capability_hook_request_scope,
    load_scoped_registry,
)
from .control_plane.effect_runtime import (
    EffectRuntimeRemoteError,
    EffectRuntimeStartupError,
    effect_runtime_request_scope,
    effect_runtime_result,
)
from .control_plane.todos.list_readback import list_goal_todos
from .history import load_registry
from .paths import resolve_runtime_root


HEARTBEAT_PRE_QUOTA_SCHEMA_VERSION = "heartbeat_pre_quota_v0"
_WITNESS_REQUIRED_KEYS: tuple[str, ...] = (
    "source_authority",
    "provider_revision",
    "cursor",
)


def _extract_authority_witness(payload: Any) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    authority = payload.get("authority_read")
    if not isinstance(authority, dict):
        return None
    witness: dict[str, str] = {}
    for key in _WITNESS_REQUIRED_KEYS:
        value = authority.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        witness[key] = value
    return witness


def _is_valid_authority_witness(witness: Any) -> bool:
    return isinstance(witness, dict) and all(
        isinstance(witness.get(key), str) and bool(str(witness.get(key)).strip())
        for key in _WITNESS_REQUIRED_KEYS
    )


def _canonical_witness_is_fresh(
    *,
    runtime_root: Path,
    goal_id: str,
    expected_witness: dict[str, str],
) -> bool:
    try:
        result = effect_runtime_result(
            "todo.authority_head.verify",
            {
                "schema_version": "todo_authority_head_verify_v0",
                "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
                "goal_id": goal_id,
                "expected_witness": expected_witness,
            },
        )
    except (
        EffectRuntimeRemoteError,
        EffectRuntimeStartupError,
        OSError,
        RuntimeError,
        ValueError,
    ):
        return False
    return isinstance(result, dict) and result.get("fresh") is True


def run_heartbeat_pre_quota(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_id: str,
    fetch_timeout_seconds: int = 10,
) -> dict[str, Any]:
    with effect_runtime_request_scope(), capability_hook_request_scope():
        runtime = resolve_runtime_root(
            load_scoped_registry(registry_path, loader=load_registry),
            runtime_root_arg,
            registry_path=registry_path,
        )
        shared_todo_cache: dict[str, Any] = {
            "todos": None,
            "witness": None,
            "dirty": False,
        }

        def load_shared_todos() -> list[dict[str, Any]]:
            cached = shared_todo_cache["todos"]
            if cached is None or shared_todo_cache["dirty"]:
                result = list_goal_todos(
                    registry_path=registry_path,
                    runtime_root_arg=str(runtime),
                    goal_id=goal_id,
                    role=None,
                )
                cached = result["todos"]
                shared_todo_cache["todos"] = cached
                shared_todo_cache["witness"] = _extract_authority_witness(result)
                shared_todo_cache["dirty"] = False
            return list(cached)

        def invalidate_shared_todos() -> None:
            shared_todo_cache["todos"] = None
            shared_todo_cache["witness"] = None
            shared_todo_cache["dirty"] = True

        def load_shared_agent_todos() -> list[dict[str, Any]] | None:
            cached = shared_todo_cache["todos"]
            witness = shared_todo_cache["witness"]
            if (
                cached is None
                or shared_todo_cache["dirty"]
                or not _is_valid_authority_witness(witness)
            ):
                invalidate_shared_todos()
                return None
            if not _canonical_witness_is_fresh(
                runtime_root=runtime,
                goal_id=goal_id,
                expected_witness=witness,
            ):
                invalidate_shared_todos()
                return None
            return [
                dict(todo)
                for todo in cached
                if isinstance(todo, dict) and todo.get("role") == "agent"
            ]

        try:
            review_reconciliation = reconcile_acknowledged_issue_fix_pr_reviews(
                registry_path=registry_path,
                runtime_root_arg=str(runtime),
                goal_id=goal_id,
                agent_id=agent_id,
                project=None,
                fetch_metadata=True,
                fetch_timeout_seconds=fetch_timeout_seconds,
                execute=True,
                todo_loader=load_shared_todos,
                on_todo_mutation=invalidate_shared_todos,
            )
            degraded = bool(review_reconciliation.get("degraded"))
            failure_count = int(review_reconciliation.get("failure_count") or 0)
        except Exception as exc:
            degraded = True
            failure_count = 1
            review_reconciliation = {
                "ok": True,
                "degraded": True,
                "failure_count": 1,
                "failure_categories": [type(exc).__name__],
                "external_read_count": 0,
                "write_count": 0,
            }

        dispatch = extend_turn_start_dispatch(
            {},
            registry_path=registry_path,
            runtime_root=runtime,
            goal_id=goal_id,
            agent_id=agent_id,
            available=None,
            provider_options={
                "issue-fix": {
                    "fetch_timeout_seconds": fetch_timeout_seconds,
                    "shared_agent_todos_loader": load_shared_agent_todos,
                }
            },
        )
        # Keep the released command's compatibility projection. Observation itself
        # is registered and permission-resolved by the same lifecycle as execution.
        failed = next((row for row in dispatch.get("failures", [])
            if row.get("capability_id") == "issue-fix"), None)
        dependencies = next((row["readback"] for row in dispatch.get("results", [])
            if row.get("capability_id") == "issue-fix" and isinstance(row.get("readback"), dict)),
            {"ok": not bool(failed), "degraded": bool(failed), "failure_count": 1 if failed else 0,
             "external_read_count": None if failed else 0, "state_written": None if failed else False,
             "status": "observation_unavailable" if failed else "not_configured_or_unavailable"})
        degraded = degraded or bool(dependencies.get("degraded"))
        failure_count += int(dependencies.get("failure_count") or 0)

        return {
            "ok": True,
            "schema_version": HEARTBEAT_PRE_QUOTA_SCHEMA_VERSION,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "degraded": degraded,
            "failure_count": failure_count,
            "checks": {
                "acknowledged_pr_reviews": review_reconciliation,
                "pr_dependencies": dependencies,
            },
            "turn_start_capability_hook_dispatch": dispatch,
            "quota_spend_required": False,
            "continue_to_quota": True,
        }


def render_heartbeat_pre_quota_markdown(payload: dict[str, Any]) -> str:
    raw_checks = payload.get("checks")
    checks: dict[str, Any] = raw_checks if isinstance(raw_checks, dict) else {}
    raw_review = checks.get("acknowledged_pr_reviews")
    review: dict[str, Any] = raw_review if isinstance(raw_review, dict) else {}
    raw_deps = checks.get("pr_dependencies")
    dependencies: dict[str, Any] = raw_deps if isinstance(raw_deps, dict) else {}
    return "\n".join(
        [
            "# LoopX Heartbeat Pre-Quota",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- degraded: `{payload.get('degraded')}`",
            f"- reconciled_count: `{review.get('reconciled_count', 0)}`",
            f"- failure_count: `{payload.get('failure_count')}`",
            f"- PR dependency reads: `{dependencies.get('external_read_count', 0)}`",
            f"- continue_to_quota: `{payload.get('continue_to_quota')}`",
            "- quota_spend_required: `False`",
        ]
    )
