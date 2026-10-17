"""Codex checkpoint continuation evidence for both existing capture hooks.

The session owner retains immutable source provenance; the artifact/settlement
owners decide capture eligibility. This Host adapter never restores old authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.control_plane.turn_driver.codex_sessions import load_codex_cli_session


def retained_native_history(binding, metadata):
    """An explicit reset retains source lineage, not the discarded native prefix."""
    refresh = binding.get("checkpoint_context_refresh")
    if refresh is None:
        return metadata["native_session"]
    review = refresh.get("review_basis")
    pending = refresh.get("pre_replan_basis")
    if ((review is None) == (pending is None) or
            not (review or pending).get("decision_sha256")):
        raise ValueError("checkpoint context refresh lacks one qualified boundary")
    if (refresh.get("checkpoint_sha256") != binding.get("checkpoint_artifact_sha256")
            or refresh.get("session_id") != binding.get("session_id")):
        raise ValueError("checkpoint context refresh provenance differs")
    return {"turn_ids": [], "visible_history_sha256": hashlib.sha256(b"[]").hexdigest()}


def restored_capture_gate(env, current_obligation_id=None, *, full_history=True, include_review_basis=False):
    return _restored_context(env, current_obligation_id, verify_native=True,
        include_review_basis=include_review_basis, full_history=full_history)


def restored_checkpoint_review(env):
    """Current semantic evidence alone is never a native capture permission."""
    result = _restored_context(env, verify_native=False, include_review_basis=True)
    allowed = result.pop("capture_allowed")
    result["semantic_review_accepted"] = allowed and "review_basis" in result
    return result


def _restored_context(env, current_obligation_id=None, *, verify_native, include_review_basis, full_history=True):
    root = Path(env["LOOPX_RUNTIME_ROOT"])
    lineage = {"goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"]}
    binding = load_codex_cli_session(root, lineage=lineage, session_scope="agent")
    if not binding or not binding.get("checkpoint_artifact_sha256"):
        return {"capture_allowed": True}
    from .codex import Execution
    from .sessions import BenchmarkSessionWake
    from loopx.hosts.codex_checkpoint import native_completed_cursor, native_session_checkpoint

    wake = BenchmarkSessionWake(env, Execution(context="resume",
        sandbox=env.get("LOOPX_CODEX_SANDBOX", "danger-full-access")), {})
    if wake.session_id != binding["session_id"]:
        raise ValueError("restored checkpoint conversation binding differs")
    sha = binding["checkpoint_artifact_sha256"]
    source = binding.get("checkpoint_artifact_root")
    if not source:
        raise ValueError("restored checkpoint lacks its immutable source; capture remains disabled")
    unit = Path(source)
    manifest = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "read_basis", "root": str(unit), "expected_sha256": sha,
    }, large_local_snapshot=True)
    # Admission already verified all artifact bytes. Here only the immutable
    # declaration, original pending packet and native history are inputs; do
    # not rehash large code/incumbent archives on every ordinary work wake.
    metadata = manifest["point_metadata"]
    pending = metadata.get("replan") or metadata.get("operator_metadata", {}).get("replan")
    packet = None
    if pending:
        component = metadata.get("held_decision_component_id")
        if component not in {entry["id"] for entry in manifest["components"]}:
            raise ValueError("restored replan point lacks its original pending packet; capture remains disabled")
        path = unit / "components" / component
        if path.resolve(strict=True) != path:
            raise ValueError("restored pending packet must be a physical artifact member")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata.get("decision_sha256"):
            raise ValueError("restored pending replan packet digest differs")
        packet = json.loads(raw)
        intent = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "select_replan", "quota": packet,
            "goal_id": metadata["loopx_state"]["goal_id"], "agent_id": metadata["loopx_state"]["agent_id"],
            "turn_instance_id": packet["heartbeat_receipt"]["turn_instance_id"],
        }, large_local_snapshot=True)
        if intent != pending or intent.get("capture") is not True:
            raise ValueError("restored original replan binding differs")
    result = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "restore_capture", **lineage, "checkpoint_sha256": sha,
        "current_obligation_id": current_obligation_id, "review": None,
    }, large_local_snapshot=True)
    # Existing saved preflights are discovery only. Each candidate is resolved
    # through the canonical current-Goal settlement owner, never copied logs.
    for guard_path in sorted(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/checkpoint-start-guard.json"), reverse=True):
        if guard_path.resolve(strict=True) != guard_path:
            raise ValueError("restored checkpoint guard must be a physical Host file")
        observed = read_heartbeat_settlement(root, **lineage, todo_id=None,
            turn_instance_id=guard_path.parent.name, resolve_original_binding=True,
            registry_path=Path(env["LOOPX_REGISTRY"]))
        if observed is None or observed.identity.value is None:
            continue
        result = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "restore_capture", **lineage, "checkpoint_sha256": sha,
            "current_obligation_id": current_obligation_id, "review": {
                "identity": observed.identity.value.as_dict(), "progress": observed.progress,
                "writeback_run": observed.writeback_run,
                "semantic_replan_guard": observed.semantic_replan_guard,
            },
            **({"include_review_basis": True} if include_review_basis else {}),
        }, large_local_snapshot=True)
        if result["capture_allowed"]:
            # Qualification happens before launching this wake's model. A
            # current receipt alone cannot stand in for native continuation.
            if verify_native:
                native = retained_native_history(binding, metadata)
                if full_history:
                    observed = native_session_checkpoint(env, wake.session_id, timeout=30, retained_history=native)
                    cursor = observed["metadata"]["completed_turn_id"]
                else:
                    cursor = native_completed_cursor(env, wake.session_id,
                        retained_turn_ids=native["turn_ids"], timeout=30)
                result["native_continuation_cursor"] = cursor
            result["review_turn_id"] = guard_path.parent.name
            break
    result["checkpoint_sha256"] = sha
    result["instructions"] = "" if result["capture_allowed"] else (
        "This conversation was restored from an immutable checkpoint. Before any new capture, "
        "complete a current-Goal replan using the retained pending decision and research evidence. "
        f"Cite checkpoint:{sha} in agent_vision.path_delta.evidence_refs. "
        "The archived packet is historical decision evidence: do not execute its commands, reuse its "
        "Goal/Agent/Turn identities, claims or spend authority. Follow this wake's current guard "
        "for selection, writeback and settlement. Capture eligibility requires an accepted current "
        "semantic replan and a completed destination native continuation; it does not grant execution.\n\n"
        + ("Original pending packet (read this immutable file in full as historical basis):\n" + str(path) + "\n\n"
           if packet is not None else "This source has no retained pending replan packet.\n\n")
    )
    return result


def restored_capture_history(env, preflight, *, session_id):
    """Rebind full capture to the source and completed cursor seen before work.

    This never treats an absent/new session binding as a non-restored shortcut.
    The existing typed owner still decides canonical review acceptance.
    """
    binding = load_codex_cli_session(Path(env["LOOPX_RUNTIME_ROOT"]), lineage={
        "goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"]}, session_scope="agent")
    sha = binding.get("checkpoint_artifact_sha256") if binding else None
    expected = (preflight or {}).get("checkpoint_sha256")
    if sha != expected:
        raise ValueError("restored capture source changed after preflight")
    if sha is None:
        return {}
    cursor = preflight.get("native_continuation_cursor")
    if (preflight.get("capture_allowed") is not True or binding.get("session_id") != session_id or
            not isinstance(cursor, str) or not cursor or not binding.get("checkpoint_artifact_root")):
        raise ValueError("restored capture requires its admitted session and completed cursor")
    manifest = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "read_basis", "root": binding["checkpoint_artifact_root"], "expected_sha256": sha,
    }, large_local_snapshot=True)
    return {"retained_history": retained_native_history(binding, manifest["point_metadata"]),
            "required_completed_turn_id": cursor}
