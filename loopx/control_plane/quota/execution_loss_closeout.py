"""Transport an explicit execution-loss settlement to the fenced quota owner."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_cross_runtime_file_lock
from ..coordination.shadow_management import shadow_maintenance_lock_target
from ..effect_runtime import effect_runtime_result
from .execution_preparation import RefreshTaskLeaseUnavailable


def commit_execution_loss_closeout(
    *, registry_path: Path, runtime_root_override: str | None, goal_id: str, agent_id: str,
    todo_id: str, turn_instance_id: str, goal_ref: Mapping[str, Any] | None,
    requested_proof: Mapping[str, Any] | None, progress_observation: Mapping[str, Any] | None,
    delivery_outcome: str | None, delivery_boundary: str | None,
    requested_effects: Mapping[str, Any], dry_run: bool,
) -> dict[str, Any]:
    """Transport one explicit administrative intent; TS owns every verdict/write."""
    from ...file_lock import cross_runtime_lock_witness
    from ...paths import resolve_runtime_root
    from ..effect_runtime import EffectRuntimeRejected
    from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
    from ..projects.registry_codec import load_project_registry
    from ..runtime.time import now_local_iso
    from ..work_items.progress_observation import normalize_progress_observation
    from .accounting_admission import quota_accounting_admission
    from .settlement import SettlementIdentity

    registry_path = registry_path.expanduser().resolve()
    registry = load_project_registry(registry_path)
    root = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    identity = SettlementIdentity(goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        turn_instance_id=turn_instance_id).as_dict()
    progress = (normalize_progress_observation(progress_observation, work_item_id=todo_id)
        if progress_observation is not None else None)
    admission = FirstPartyHostGoalAdmission.for_plan(registry_path=registry_path,
        goal_id=goal_id, planned_goal_ref=goal_ref, agent_id=agent_id)
    with quota_accounting_admission(runtime_root=root, registry_path=registry_path,
            goal_id=goal_id, goal_ref=goal_ref, operation="execution-loss-closeout",
            handoff_legacy_index=True) as source_admission:
        lifetime = (nullcontext() if source_admission is not None
            else admission._legacy_lifetime(operation="execution-loss-closeout", launch=False))
        with lifetime, ExitStack() as locks:
            targets = [shadow_maintenance_lock_target(root, goal_id),
                root / "goals" / goal_id / "rollout-event-log.jsonl"]
            for target in targets:
                # Existing quota requalification can own the rollout lock
                # before asking for index/source; do not wait in reverse order.
                locks.enter_context(exclusive_cross_runtime_file_lock(target,
                    timeout_seconds=0, operation="execution-loss-closeout"))
            raw = registry_path.read_bytes()
            witnesses = [cross_runtime_lock_witness(target) for target in targets]
            lifetime_sources = []
            if source_admission is None:
                witnesses.append(cross_runtime_lock_witness(root / "goals" / goal_id / "runs" / "index.jsonl"))
                witnesses.append(cross_runtime_lock_witness(registry_path))
                upstream = admission.legacy_source_admission
                while upstream is not None:
                    source_path = upstream.registry_path
                    source_bytes = source_path.read_bytes()
                    lifetime_sources.append({"path": str(source_path), "sha256": hashlib.sha256(source_bytes).hexdigest(),
                        "payload": load_project_registry(source_path)})
                    witnesses.append(cross_runtime_lock_witness(source_path))
                    upstream = upstream.legacy_source_admission
            request = {"schema_version": "quota_execution_loss_closeout_commit_v0",
                "execution_loss_closeout": True, "dry_run": dry_run, "runtime_root": str(root), "identity": identity,
                **({"goal_ref": dict(goal_ref)} if goal_ref is not None else {}),
                **({"source_admission": source_admission} if source_admission is not None else {}),
                "registry_source": {"path": str(registry_path), "sha256": hashlib.sha256(raw).hexdigest(),
                    "payload": load_project_registry(registry_path), "lifetime_sources": lifetime_sources}, "locks": witnesses,
                "intent": {"delivery_outcome": delivery_outcome, "delivery_boundary": delivery_boundary,
                    "progress_observation": progress}, "control_writer_proof": dict(requested_proof) if requested_proof else None,
                "requested_effects": dict(requested_effects), "observed_at": now_local_iso()}
            try:
                result = effect_runtime_result("quota.execution_loss_closeout.commit", request)
            except EffectRuntimeRejected as exc:
                error = RefreshTaskLeaseUnavailable(str(exc))
                error.code = exc.diagnostic_code
                raise error from exc
    if not isinstance(result, dict) or result.get("schema_version") != "quota_execution_loss_closeout_result_v0":
        raise RuntimeError("typed execution-loss closeout returned an invalid result")
    return result
