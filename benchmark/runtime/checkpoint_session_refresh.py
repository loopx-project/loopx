"""Explicit warm-receiver native-context treatments at verified boundaries.

The existing typed checkpoint owner qualifies the current decision. Native
session IO remains at CodexAgentSession; this adapter composes those owners
without resetting research, native opportunities, phase budget or authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .checkpoint_continuation import retained_native_history


def prepare_session_refresh(env, session, wake):
    declaration = json.loads(Path(env["LOOPX_WARM_START"]).read_bytes())
    if "fresh_after_replan" in declaration:
        raise ValueError("fresh_after_replan is no longer supported; use a pre-replan checkpoint")
    if type(declaration.get("fresh_replan", False)) is not bool:
        raise ValueError("fresh_replan requires an explicit boolean treatment")
    if not declaration.get("fresh_replan", False):
        return ""
    if env.get("LOOPX_ITERATION_CONTEXT") != "resume":
        raise ValueError("checkpoint fresh context requires a resumed warm receiver")
    if any(env[key] != declaration[field] for key, field in
           (("LOOPX_GOAL_ID", "goal_id"), ("LOOPX_AGENT_ID", "agent_id"))):
        raise ValueError("checkpoint fresh context destination lineage differs")
    if not session.binding or session.binding.get("checkpoint_artifact_sha256") != declaration["sha256"]:
        raise ValueError("checkpoint fresh context requires its exact checkpoint binding")
    current = session.binding.get("checkpoint_context_refresh")
    if current is not None:
        if bool(current.get("pre_replan_basis")) != bool(declaration.get("fresh_replan")):
            raise ValueError("checkpoint context treatment differs from committed session")
        retained_native_history(session.binding, {})
        return ""  # This receiver's initial reset is already committed.
    return _prepare_fresh_replan(env, session, wake, declaration)


def _prepare_fresh_replan(env, session, wake, declaration):
    """Compose existing sealed-point selection and native admission before model work.

    The TypeScript artifact owner decides whether this is a pending replan point;
    native admission rejects an already-advanced source fork. No completed review
    is manufactured or fed to the fresh decision maker.
    """
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from benchmark.runtime.sforge_warm_start import admit_warm_session

    unit = Path(declaration["root"])
    manifest = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "read_basis", "root": str(unit), "expected_sha256": declaration["sha256"]})
    metadata = manifest["point_metadata"]
    component = metadata.get("held_decision_component_id")
    if component not in {entry["id"] for entry in manifest["components"]}:
        raise ValueError("fresh replan requires a sealed pre-decision packet")
    packet_path = unit / "components" / component
    if packet_path.resolve(strict=True) != packet_path:
        raise ValueError("fresh replan packet must be a physical sealed member")
    raw = packet_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata.get("decision_sha256"):
        raise ValueError("fresh replan pending packet digest differs")
    packet = json.loads(raw)
    selected = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "select_replan", "quota": packet,
        "goal_id": metadata["loopx_state"]["goal_id"],
        "agent_id": metadata["loopx_state"]["agent_id"],
        "turn_instance_id": packet["heartbeat_receipt"]["turn_instance_id"]})
    if selected.get("capture") is not True or selected != (
            metadata.get("replan") or metadata.get("operator_metadata", {}).get("replan")):
        raise ValueError("fresh replan requires the original pending decision boundary")
    basis = {"decision_sha256": metadata["decision_sha256"], "pending_replan": selected}
    attempt = Path(env["LOOPX_WARM_START"]).parent / "context-refresh" / "pre-replan.json"
    # A lost launch reply is recovered only by the session owner's original
    # output. Re-admitting an advanced fork would hide that outcome.
    if not attempt.exists():
        admitted = admit_warm_session(env, declaration)
        if admitted["session_id"] != session.session_id:
            raise ValueError("fresh replan source session differs")
    session.prepare_checkpoint_refresh(None, attempt, wake / "stdout.jsonl", pre_replan_basis=basis)
    return (
        "This fresh conversation starts from a checkpoint captured BEFORE a pending replan. "
        "No earlier native messages or decision made after that checkpoint are supplied. "
        "Read the original task objective and scoring/acceptance rules from the current task document, "
        "then the retained research evidence, failed alternatives, code and best validated candidate. "
        "Historical plans are evidence to reconsider, not a decision to inherit. Complete the current "
        "preparation and replan under the current guard before choosing research work. "
        "Keep the original submission opportunities, cooldown and cumulative deadline. "
        "Do not resume archived commands or consult archived native conversation messages to choose "
        "the route; use the checkpoint research evidence and original objective.\n\n"
    )
