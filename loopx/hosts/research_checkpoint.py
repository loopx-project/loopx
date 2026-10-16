"""Research checkpoint context IO through existing canonical Todo owners.

Captured records are historical input, never a replacement authority store.
Only the existing explicit continuation owner can adopt a note on an already
claimed destination Todo. Verified Explore knowledge may seed a fresh graph;
historical execution observations, effects and vision authority are not revived.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path, PurePosixPath

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.coordination.canonical_snapshot import read_canonical_snapshot


def canonical_context(env: dict[str, str], *, deadline: float) -> dict:
    """Retain the complete revision-bound read, without the execution leases."""
    def rpc(method, payload, **kwargs):
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("research checkpoint capture window closed")
        return effect_runtime_result(method, payload, timeout=remaining)
    result = read_canonical_snapshot(rpc=rpc, runtime_root=env["LOOPX_RUNTIME_ROOT"],
        goal_id=env["LOOPX_GOAL_ID"], include_leases=False, projection_readback=None,
        timeout=max(.1, deadline - time.time()))
    if result.get("status") != "loaded":
        raise ValueError("research checkpoint requires complete canonical Todo context")
    return {"goal_id": env["LOOPX_GOAL_ID"], "snapshot": result}


def research_context(env: dict[str, str], *, deadline: float) -> dict:
    """Retain decision evidence as history, without importing operational state.

    The existing Explore codec owns event validity and topology; run-index
    records own vision/stride history. This transport preserves their values
    instead of inferring a new frontier or reviving a historical decision.
    """
    from loopx.capabilities.explore.result_log import (
        explore_result_log_path, validate_explore_result_event,
    )
    from loopx.history import load_index_snapshot
    from loopx.runtime import validate_goal_id_path_segment

    root = Path(env["LOOPX_RUNTIME_ROOT"])
    goal = validate_goal_id_path_segment(env["LOOPX_GOAL_ID"])
    paths = [explore_result_log_path(root, goal), root / "goals" / goal / "runs/index.jsonl"]
    before = {}
    for path in paths:
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("research evidence source must be a physical absolute path")
        raw = path.read_bytes() if path.exists() else None
        # An incomplete append is not a complete research boundary. The source
        # owners validate event fields; the transport checks complete framing.
        if raw and not raw.endswith(b"\n"):
            raise ValueError("research evidence source has an incomplete tail")
        before[path] = raw
    events = []
    # Decode exactly the witnessed bytes through the existing event validator;
    # rereading the file for decoding would leave an avoidable ABA window.
    for line in (before[paths[0]] or b"").splitlines():
        if line.strip():
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("event must be an object")
                events.append(validate_explore_result_event(row, expected_goal_id=goal))
            except ValueError as error:
                raise ValueError("invalid Explore result in research context") from error
    raw_index = before[paths[1]]
    if raw_index:
        for line in raw_index.splitlines():
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("goal_id", goal) != goal:
                    raise ValueError("research history contains a malformed or foreign record")
    index = load_index_snapshot(paths[1], include_artifact_status=False)
    if index.digest != ("sha256:" + hashlib.sha256(raw_index).hexdigest() if raw_index is not None else None):
        raise ValueError("research history source moved during decoding")
    runs = index.records
    # Preserve every indexed vision, including retired decisions and their
    # explicit provenance; a latest-only display could erase negative evidence.
    visions = [{key: row[key] for key in ("generated_at", "goal_id", "agent_id", "goal_ref",
                "turn_instance_id", "agent_vision", "vision_checkpoint") if key in row}
               for row in runs if row.get("agent_vision") or row.get("vision_checkpoint")]
    result = canonical_context(env, deadline=deadline)
    for path, raw in before.items():
        if (path.read_bytes() if path.exists() else None) != raw:
            raise ValueError("research evidence source moved during the context read")
    return {**result, "research_evidence": {"explore_events": events, "vision_history": visions},
        "source_files": [{"path": str(path), "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None}
                         for path, raw in before.items()]}


def verify_context_sources(context: dict, components: list[dict], witness: dict) -> None:
    """Bind exported history to the artifact owner's actual source witness.

    A second read alone ends too early: a source append immediately before the
    probe must not be captured next to an older exported context. Absence is
    witnessed by the containing component too, so a first append also rejects.
    """
    for source in context.get("source_files", []):
        path = Path(source["path"])
        parent = next((entry for entry in components if path.is_relative_to(Path(entry["source_path"]))), None)
        if parent is None:
            raise ValueError("research evidence source is outside the captured inventory")
        relative = path.relative_to(Path(parent["source_path"]))
        member = "" if str(relative) == "." else relative.as_posix()
        observed = next((item for item in witness[parent["id"]] if item["path"] == member), None)
        if source["sha256"] is None:
            if observed is not None:
                raise ValueError("research evidence source appeared before sealing")
        elif observed is None or observed.get("kind") != "file" or observed.get("sha256") != source["sha256"]:
            raise ValueError("research evidence source differs from the captured context")


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _handoff(env, payload):
    from loopx.agent_registry import registered_agent_ids_from_registry
    request = {"runtime_root": env["LOOPX_RUNTIME_ROOT"], "goal_id": env["LOOPX_GOAL_ID"],
        "agent_id": env["LOOPX_AGENT_ID"], "registered_agents": registered_agent_ids_from_registry(
            Path(env["LOOPX_REGISTRY"]), env["LOOPX_GOAL_ID"]), **payload}
    result = effect_runtime_result("coordination.local_authority.todo_continuation", request)
    if result.get("ok") is not True:
        raise ValueError("checkpoint context continuation rejected: " + str(result.get("reason_code", result.get("status"))))
    return result



def _context_session(env, metadata):
    """Read the admitted native branch before publishing captured Todo context."""
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    from loopx.hosts.codex_checkpoint import native_session_checkpoint

    wake = CodexAgentSession(env, context="resume", sandbox=env["LOOPX_CODEX_SANDBOX"], receipt={})
    native = metadata["native_session"]
    if (not wake.session_id or wake.session_id == native["session_id"] or
            wake.digest != metadata.get("session", {}).get("session_profile_digest")):
        raise ValueError("context adoption requires the admitted destination session profile")
    observed = native_session_checkpoint(env, wake.session_id, timeout=30,
        interrupted_guard=native.get("interrupted_guard"))["metadata"]
    cursor_key = "interrupted_turn_id" if native.get("interrupted_guard") is not None else "completed_turn_id"
    for key in ("visible_history_sha256", "turn_ids", cursor_key, "host_version", "config_sha256"):
        if observed[key] != native[key]:
            raise ValueError("destination native history or configuration moved beyond the captured context")
    return wake.session_id


def adopt_todo_context(env: dict[str, str], unit: Path, digest: str, *, source_todo_id: str,
                       todo_id: str, operation_id: str, expected_revision: str,
                       lease_key: str, lease_version: int) -> dict:
    """Adopt retained context using the destination's current claim and lease.

    No Todo is created, completed or transferred, and no historical authority,
    effect or strategy is installed. Explore facts retain their source identity
    without operational observation grants. Prepare/adopt each use stable original
    operation identities. A failed/uncertain call must be read back through that
    same owner; this adapter never guesses a new identity or starts model work.
    """
    from loopx.control_plane.turn_driver.codex_sessions import select_codex_cli_session
    from loopx.control_plane.todos.provider_projection import (
        settle_canonical_todo_projection, projection_delivery_requires_ack,
    )

    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": digest})
    metadata = json.loads((unit / "checkpoint.json").read_bytes())["point_metadata"]
    if not all(isinstance(metadata.get(key), dict) for key in ("loopx_state", "native_session", "research_context")):
        raise ValueError("checkpoint lacks its canonical Todo or native session context participant")
    source = metadata["loopx_state"]
    if (source["goal_id"] == env["LOOPX_GOAL_ID"] or source["agent_id"] == env["LOOPX_AGENT_ID"]):
        raise ValueError("context adoption requires distinct destination Goal and Agent")
    declared = metadata["research_context"]
    component, member = declared["component_id"], declared["member_path"]
    if (not isinstance(component, str) or Path(component).name != component or component in {".", ".."} or
            not isinstance(member, str) or PurePosixPath(member).is_absolute() or
            ".." in PurePosixPath(member).parts or (member and str(PurePosixPath(member)) != member)):
        raise ValueError("retained context member must stay within its artifact component")
    context_file = unit / "components" / component
    if member:
        context_file /= member
    raw = context_file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != declared["sha256"]:
        raise ValueError("retained canonical context digest differs")
    carrier = json.loads(raw)
    context = carrier["snapshot"]
    if (carrier.get("goal_id") != source["goal_id"] or
            context.get("provider_revision") != declared["provider_revision"] or
            context.get("store_identity") != declared["store_identity"]):
        raise ValueError("retained canonical context revision differs")
    retained = next((row for row in context["todos"] if row["todo_id"] == source_todo_id), None)
    if retained is None:
        raise ValueError("source Todo is absent from the captured context")
    from loopx.capabilities.explore.result_log import (
        append_explore_result_events, explore_result_log_path, rebase_explore_knowledge_events,
    )
    # Validate the entire sealed knowledge source before any destination write.
    # Legacy context-only points keep their original continuation request bytes.
    evidence = carrier.get("research_evidence", {})
    if not isinstance(evidence, dict) or not isinstance(evidence.get("explore_events", []), list):
        raise ValueError("retained Explore knowledge must be a complete event array")
    knowledge = rebase_explore_knowledge_events(evidence.get("explore_events", []),
        source_goal_id=source["goal_id"], destination_goal_id=env["LOOPX_GOAL_ID"],
        source_ref="checkpoint:" + digest)
    session_id = _context_session(env, metadata)
    bound = select_codex_cli_session(Path(env["LOOPX_RUNTIME_ROOT"]), lineage={
        "goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"]}, session_scope="agent")
    if bound is None or bound["session_id"] != session_id:
        raise ValueError("context adoption requires the current distinct destination session binding")
    workspace = Path(env["LOOPX_PROJECT"])
    if not workspace.is_absolute() or workspace.resolve(strict=True) != workspace:
        raise ValueError("destination context workspace must be a physical absolute path")
    proof = {"idempotency_key": lease_key, "expected_version": lease_version}
    common = {"todo_id": todo_id, "session_id": bound["session_id"], "workspace": str(workspace),
              "artifacts": [], "lease_proof": proof}
    # The owner's inspect performs the real current claim/lease/dependency and
    # acceptance checks. Source records only contribute context; they are never
    # passed as operational fields to prepare or adopt.
    inspect = _handoff(env, {**common, "action": "inspect"})
    if (inspect["claimed_by"] != env["LOOPX_AGENT_ID"] or
            inspect.get("execution_authority", {}).get("allowed") is not True):
        raise ValueError("destination context revision, owner or execution authority changed")
    destination = canonical_context(env, deadline=time.time() + 30)["snapshot"]
    current = next((row for row in destination["todos"] if row["todo_id"] == todo_id), None)
    if destination["provider_revision"] != inspect["provider_revision"] or current is None:
        raise ValueError("destination canonical context moved before preparation")
    if current.get("note") and destination["provider_revision"] == expected_revision:
        raise ValueError("destination Todo already has context; replay its original operation and revision")
    if not current.get("note") and destination["provider_revision"] != expected_revision:
        raise ValueError("destination context revision, owner or execution authority changed")
    # With an existing note, the caller's stale ORIGINAL revision can only
    # replay prepare through its typed receipt owner; it cannot overwrite the
    # current note. Changed operation/context/proof must still fail there.
    # Keep the complete source read in a workspace-relative private artifact.
    # Never overwrite a moved artifact on an operation retry.
    relative = Path(".local/checkpoint-context") / (digest + ".json")
    target = workspace / relative
    for directory in (workspace / ".local", target.parent):
        if directory.is_symlink() or (directory.exists() and directory.resolve(strict=True) != directory):
            raise ValueError("destination context artifact must not use a symlink")
        directory.mkdir(exist_ok=True, mode=0o700)
    if target.is_symlink():
        raise ValueError("destination context artifact must not use a symlink")
    try:
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            if file.write(raw) != len(raw):
                raise OSError("destination context artifact write is incomplete")
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError:
        if target.read_bytes() != raw:
            raise ValueError("destination context artifact already differs")
    if target.read_bytes() != raw:
        raise ValueError("destination context artifact readback differs")
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": digest})
    # Keep old context-only request bytes stable for original receipt recovery
    # across an adapter upgrade. New history is disclosed only when captured.
    history_guidance = (" Read its research_evidence for prior branches, negative results and vision/stride decisions."
                        if "research_evidence" in carrier else "")
    note = {"work_summary": "Retained canonical Todo context from the verified checkpoint is available in " + relative.as_posix() +
            ". Historical owners, statuses and operational references are evidence only; current Goal/Todo/lease rules govern work." + history_guidance,
        "source_refs": ["checkpoint:" + digest, "source-todo:" + source_todo_id,
                        "source-revision:" + declared["provider_revision"]],
        "files_touched": [{"path": relative.as_posix(), "action": "read",
                           "summary": "Complete captured canonical Todo population, including retained notes and rejected approaches."}]}
    prepared = _handoff(env, {**common, "action": "prepare", "session_id": metadata["native_session"]["session_id"],
        "operation_id": operation_id + "-prepare", "expected_provider_revision": expected_revision, "context": note})
    if select_codex_cli_session(Path(env["LOOPX_RUNTIME_ROOT"]), lineage={
            "goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"]}, session_scope="agent") != bound:
        raise ValueError("destination session changed; inspect the original prepared context")
    if _context_session(env, metadata) != session_id:
        raise ValueError("destination native branch changed; inspect the original prepared context")
    # Preserve the original prepare commit revision. The outer readback may
    # already include adoption if its response was lost; using that newer head
    # would change the adopt request hash and defeat original-receipt recovery.
    adopted = _handoff(env, {**common, "action": "adopt", "artifacts": [relative.as_posix()],
        "operation_id": operation_id + "-adopt", "expected_provider_revision": prepared["result"]["provider_revision"]})
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": digest})
    if target.read_bytes() != raw:
        raise ValueError("adopted context artifact changed; inspect the original continuation receipt")
    result = settle_canonical_todo_projection(adopted, registry_path=Path(env["LOOPX_REGISTRY"]),
        runtime_root=Path(env["LOOPX_RUNTIME_ROOT"]), goal_id=env["LOOPX_GOAL_ID"])
    if result.get("ok") is not True or not projection_delivery_requires_ack(result["projection_delivery"]):
        raise ValueError("continuation committed; reconcile its original projection receipt")
    knowledge_result = None
    if knowledge:
        # Recheck fresh current ownership after continuation/projection recovery.
        # An exact immutable prefix makes a lost append response recoverable,
        # without reverting evidence recorded by subsequent destination work.
        current_owner = _handoff(env, {**common, "action": "inspect"})
        if (current_owner["claimed_by"] != env["LOOPX_AGENT_ID"] or
                current_owner.get("execution_authority", {}).get("allowed") is not True):
            raise ValueError("destination knowledge adoption lost current execution authority")
        knowledge_result = append_explore_result_events(
            explore_result_log_path(Path(env["LOOPX_RUNTIME_ROOT"]), env["LOOPX_GOAL_ID"]),
            knowledge, expected_goal_id=env["LOOPX_GOAL_ID"], require_ordered_prefix=True)
    return {"context_adopted": True, "session_id": bound["session_id"], "todo_id": todo_id,
        "artifact": str(target), "context_sha256": declared["sha256"],
        **({"explore_knowledge": knowledge_result} if knowledge_result is not None else {}),
        "continuation": result, "model_calls": 0, "research_point_qualified": False}


def _participant_id(components, preferred):
    """Automatic participants cannot reserve an otherwise legal caller ID."""
    occupied = {entry["id"] for entry in components}
    candidate, suffix = preferred, 0
    while candidate in occupied:
        suffix += 1
        candidate = f"{preferred}_{suffix}"
    return candidate


def _loopx_state_components(env, components, output_dir):
    """Adapt the existing backup owner's inventory for one explicitly scoped Goal.

    No second state-discovery rule, archive execution or authority adoption.
    The runtime owns its original evidence, quota and effect journals; this
    adapter declares those private files to the inert artifact owner only.
    """
    from loopx.history import load_registry
    from loopx.state_backup import build_state_backup_plan

    project, runtime, registry = (Path(env[key]) for key in (
        "LOOPX_PROJECT", "LOOPX_RUNTIME_ROOT", "LOOPX_REGISTRY"))
    for path in (project, runtime, registry):
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError("LoopX checkpoint routes must be normalized physical paths")
    raw_registry = registry.read_bytes()
    source = load_registry(registry)
    goals = source.get("goals", [])
    if (len(goals) != 1 or goals[0].get("id") != env["LOOPX_GOAL_ID"] or
            env["LOOPX_AGENT_ID"] not in goals[0].get("coordination", {}).get("registered_agents", [])):
        raise ValueError("LoopX state capture requires an explicitly scoped single-Goal registry")
    # Reject lexical route aliases before backup discovery resolves them.
    goal = goals[0]
    if Path(goal["repo"]) != project or Path(source.get("common_runtime_root", "")) != runtime:
        raise ValueError("LoopX checkpoint routes differ from the registered Goal")
    state = Path(goal["state_file"])
    state = state if state.is_absolute() else Path(goal["repo"]) / state
    if not state.is_file():
        raise ValueError("LoopX checkpoint requires its registered active state source")
    routes = [state, *(project / path for path in (
        ".loopx", ".codex/goals", ".claude/goals", ".local/goals"))]
    if any(path.exists() and path.resolve(strict=True) != path for path in routes):
        raise ValueError("LoopX checkpoint state routes must not contain symlinks")
    plan = build_state_backup_plan(project=project, runtime_root=runtime,
        registry_path=registry, output_dir=output_dir,
        include_registry_projects=False, include_automations=False, include_skills=False)
    if plan["warnings"]:
        raise ValueError("LoopX backup inventory is incomplete; reconcile its source routes before capture")
    # Select physical parent roots once. A caller-declared parent can already
    # cover a discovered file; do not duplicate the same runtime/registry/state.
    entries = list(components)
    targets = sorted(plan["included"], key=lambda entry: len(Path(entry["source_path"]).parts))
    for target in targets:
        path = Path(target["source_path"])
        if any(path.is_relative_to(Path(entry["source_path"])) for entry in entries):
            continue
        if any(Path(entry["source_path"]).is_relative_to(path) for entry in entries):
            raise ValueError("LoopX state inventory overlaps a narrower declared component; declare its parent once")
        entries.append({"id": _participant_id(entries, f"loopx_state_{len(entries)}"), "source_path": str(path)})
    coverage = [{"key": target["key"], "source_path": target["source_path"],
                 "component_id": next(entry["id"] for entry in entries
                     if Path(target["source_path"]).is_relative_to(Path(entry["source_path"]))) }
                for target in plan["included"]]
    if registry.read_bytes() != raw_registry:
        raise ValueError("LoopX registry moved during participant inventory")
    registry_component = next(entry for entry in entries if registry.is_relative_to(Path(entry["source_path"])))
    return entries, {"goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"],
        "source_inventory": coverage, "registry_sha256": hashlib.sha256(raw_registry).hexdigest(),
        "registry_component_id": registry_component["id"],
        "registry_member_path": (registry.relative_to(Path(registry_component["source_path"])).as_posix()
                                 if registry != Path(registry_component["source_path"]) else ""),
        "execution_authority_granted": False}


def native_binding_participant(env, components, metadata):
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
    source_wake = CodexAgentSession(env, context="resume",
        sandbox=env.get("LOOPX_CODEX_SANDBOX", "danger-full-access"), receipt={})
    if source_wake.session_id != metadata["session"].get("session_id"):
        raise ValueError("native checkpoint differs from the current source session binding")
    # This sideband record belongs to the existing session owner. It
    # must participate even when loopx_state was not requested: native
    # store bytes alone cannot witness a binding/profile replacement.
    from loopx.control_plane.turn_driver.codex_sessions import _session_path
    binding_path = _session_path(source_wake.root, source_wake.lineage, "agent")
    if not any(binding_path.is_relative_to(Path(entry["source_path"])) for entry in components):
        components = [*components, {"id": _participant_id(components, "source_session_binding"),
                                   "source_path": str(binding_path)}]
    metadata["session"] = {**metadata["session"], "session_profile_digest": source_wake.digest}
    if source_wake.binding.get("checkpoint_context_refresh") is not None:
        metadata["session"]["checkpoint_context_refresh"] = source_wake.binding["checkpoint_context_refresh"]
    return components, source_wake


def research_participants(env, components, metadata, destination, evidence_path, deadline):
    """Use the same inventory and historical context at either owned boundary."""
    research = None
    components, metadata["loopx_state"] = _loopx_state_components(env, components, destination)
    from loopx.control_plane.coordination.local_authority import local_authority_is_promoted
    if local_authority_is_promoted(runtime_root=Path(env["LOOPX_RUNTIME_ROOT"]), goal_id=env["LOOPX_GOAL_ID"]):
        research = research_context(env, deadline=deadline)
        evidence = evidence_path
        research_raw = _encoded(research)
        with os.fdopen(os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            file.write(research_raw)
        parent = next((entry for entry in components if evidence.is_relative_to(Path(entry["source_path"]))), None)
        if parent is None:
            parent = {"id": _participant_id(components, "research_context"), "source_path": str(evidence)}
            components = [*components, parent]
        metadata["research_context"] = {"component_id": parent["id"],
            "member_path": evidence.relative_to(Path(parent["source_path"])).as_posix() if evidence != Path(parent["source_path"]) else "",
            "sha256": hashlib.sha256(research_raw).hexdigest(),
            "provider_revision": research["snapshot"]["provider_revision"], "store_identity": research["snapshot"]["store_identity"]}
    else:
        metadata["canonical_context_unavailable"] = "Source coordination is unpromoted; its retained inventory remains inert."
    return components, research


def verify_research_participants(env, components, metadata, research, witness, deadline):
    if research is not None:
        if research_context(env, deadline=deadline) != research:
            raise ValueError("canonical research context moved before sealing")
        verify_context_sources(research, components, witness)
        retained = metadata["research_context"]
        member = next((entry for entry in witness[retained["component_id"]]
            if entry["path"] == retained["member_path"]), None)
        if member is None or member.get("sha256") != retained["sha256"]:
            raise ValueError("canonical research context bytes moved before sealing")
    declared = metadata["loopx_state"]
    witness = witness[declared["registry_component_id"]]
    member = next((entry for entry in witness if entry["path"] == declared["registry_member_path"]), None)
    if member is None or member.get("sha256") != declared["registry_sha256"]:
        raise ValueError("LoopX registry differs from the scoped participant inventory")


def capture_research_checkpoint(env: dict[str, str], request: dict, *,
                                turn_instance_id: str, timeout: float = 30) -> dict:
    """Seal a quiescent, settled ordinary Goal boundary through existing owners.

    The caller stops source work while capturing. Session/canonical history and
    immutable byte witnesses are reread; this is not disaster reactivation.
    Domain providers add their own participants through the same artifact owner.
    """
    import math
    import tempfile
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    from loopx.hosts.codex_checkpoint import native_checkpoint_reader, native_session_checkpoint
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession

    if (not isinstance(request, dict) or
            set(request) != {"destination", "components", "point_metadata"} or
            not isinstance(request["components"], list) or not request["components"] or
            not isinstance(request["point_metadata"], dict)):
        raise ValueError("research capture requires destination, explicit components and point_metadata")
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("research capture timeout must be finite and positive")
    deadline = time.time() + timeout
    runtime, registry = Path(env["LOOPX_RUNTIME_ROOT"]), Path(env["LOOPX_REGISTRY"])
    def settled():
        result = read_heartbeat_settlement(runtime, goal_id=env["LOOPX_GOAL_ID"],
            agent_id=env["LOOPX_AGENT_ID"], todo_id=None, turn_instance_id=turn_instance_id,
            resolve_original_binding=True, registry_path=registry)
        if result is None or result.identity.value is None or (result.progress or {}).get("state") != "settled":
            raise ValueError("research capture requires the source's settled current Turn")
        return result
    original = settled()
    destination = Path(request["destination"])
    if not destination.is_absolute() or destination.resolve() != destination or destination.exists():
        raise ValueError("research capture destination must be a new physical absolute path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".loopx-research-", dir=destination.parent) as temporary:
        metadata = {"operator_metadata": request["point_metadata"],
            "wake_turn_id": turn_instance_id, "settlement_effect_id": original.identity.value.effect_id,
            "session": {}}
        session = CodexAgentSession(env, context="resume", sandbox=env["LOOPX_CODEX_SANDBOX"], receipt=metadata)
        if not session.session_id:
            raise ValueError("research capture requires a current source session binding")
        components, research = research_participants(env, request["components"], metadata,
            destination, Path(temporary) / "research-context.json", deadline)
        if research is None:
            raise ValueError("research capture requires canonical source coordination")
        components, observed_session = native_binding_participant(env, components, metadata)
        # Reopening the native reader after probing can checkpoint its history
        # database and invalidate our own byte witness. Keep one inspection
        # process open; the original logical reread and byte fence both remain.
        with native_checkpoint_reader(env, timeout=max(.1, deadline-time.time())) as reader:
            native = native_session_checkpoint(env, session.session_id, timeout=max(.1, deadline-time.time()), transport=reader)
            allocated = {}
            for entry in native["components"]:
                name = _participant_id(components, entry["id"])
                allocated[entry["id"]] = name
                components = [*components, {**entry, "id": name}]
            metadata["native_session"] = {**native["metadata"], "store_members": [
                {**entry, "id": allocated[entry["id"]]} for entry in native["metadata"]["store_members"]]}
            probe = effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "probe", "components": components, "deadline_epoch_ms": deadline*1000},
                timeout=max(.1, deadline-time.time()), retry_safe=False)
            if native_session_checkpoint(env, session.session_id, timeout=max(.1, deadline-time.time()), transport=reader) != native:
                raise ValueError("native checkpoint source moved before sealing")
            _, confirmed = native_binding_participant(env, components, metadata)
            if ((confirmed.session_id, confirmed.goal_ref, confirmed.digest) !=
                    (observed_session.session_id, observed_session.goal_ref, observed_session.digest)):
                raise ValueError("research checkpoint session binding moved before sealing")
            verify_research_participants(env, components, metadata, research, probe["source_witness"], deadline)
            if settled().identity.value != original.identity.value:
                raise ValueError("research checkpoint source settlement moved")
            result = effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "capture", "destination": str(destination), "components": components,
                "expected_source_sha256": probe["source_sha256"], "point_metadata": metadata,
                "deadline_epoch_ms": deadline*1000}, timeout=max(.1, deadline-time.time()), retry_safe=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Adopt checkpoint Todo context on an already owned destination Todo.")
    for name in ("root", "expected-sha256", "source-todo-id", "todo-id", "operation-id", "expected-revision", "lease-key"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--lease-version", type=int, required=True)
    args = parser.parse_args(argv)
    required = ("LOOPX_PROJECT", "LOOPX_REGISTRY", "LOOPX_RUNTIME_ROOT", "LOOPX_GOAL_ID", "LOOPX_AGENT_ID",
                "CODEX_BIN", "CODEX_HOME", "MODEL_NAME", "REASONING_EFFORT", "LOOPX_CODEX_SANDBOX")
    if any(not os.environ.get(key) for key in required):
        parser.error("Explicit destination registry, runtime, workspace, Goal and Agent are required")
    print(json.dumps(adopt_todo_context(dict(os.environ), Path(args.root), args.expected_sha256,
        source_todo_id=args.source_todo_id, todo_id=args.todo_id, operation_id=args.operation_id,
        expected_revision=args.expected_revision, lease_key=args.lease_key, lease_version=args.lease_version)))


if __name__ == "__main__":
    main()
