"""Opt-in inert artifact capture after the existing benchmark wake has settled.

The TS backup/artifact owner verifies source stability and materialized bytes.
Python adapts the owned wake, source admission and explicit operator file list;
it never restores claims, starts a model or certifies a complete research point.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.quota.settlement import read_heartbeat_settlement


def normalize_checkpoint_request_path(value: str) -> str:
    """An explicit guest path; no ambient host variable enables capture."""
    if (not isinstance(value, str) or not value or
            not PurePosixPath(value).is_absolute() or str(PurePosixPath(value)) != value or
            ".." in PurePosixPath(value).parts):
        raise ValueError("checkpoint_request_path must be a normalized absolute guest file path")
    return value


def read_checkpoint_request(path: str) -> tuple[dict[str, Any], bytes]:
    raw = Path(path).read_bytes()
    return parse_checkpoint_request(raw), raw


def parse_checkpoint_request(raw: bytes) -> dict[str, Any]:
    """Validate the same explicit declaration before staging and before capture."""
    request = json.loads(raw)
    if (not isinstance(request, dict) or
            set(request) - {"destination", "components", "point_metadata", "native_session", "evaluation_opportunity", "loopx_state", "trigger", "controller_handoff"} or
            not {"destination", "components", "point_metadata"}.issubset(request)):
        raise ValueError("checkpoint request requires destination, components and point_metadata")
    if "native_session" in request and type(request["native_session"]) is not bool:
        raise ValueError("checkpoint native_session must be an explicit boolean")
    if "evaluation_opportunity" in request and type(request["evaluation_opportunity"]) is not bool:
        raise ValueError("checkpoint evaluation_opportunity must be an explicit boolean")
    if "loopx_state" in request and type(request["loopx_state"]) is not bool:
        raise ValueError("checkpoint loopx_state must be an explicit boolean")
    if "controller_handoff" in request and type(request["controller_handoff"]) is not bool:
        raise ValueError("checkpoint controller_handoff must be an explicit boolean")
    if request.get("controller_handoff") and (
            request.get("trigger") != "fresh_replan" or not request.get("native_session") or
            not request.get("loopx_state") or not request.get("evaluation_opportunity")):
        raise ValueError("controller handoff requires fresh_replan and all three state participants")
    if "trigger" in request and request["trigger"] != "fresh_replan":
        raise ValueError("checkpoint trigger must be fresh_replan or omitted")
    if not isinstance(request["components"], list) or not request["components"]:
        raise ValueError("checkpoint requires explicit components")
    if not isinstance(request["point_metadata"], dict):
        raise ValueError("checkpoint point_metadata must be an object")
    return request


def _participant_id(components, preferred):
    """Automatic participants cannot reserve an otherwise legal caller ID."""
    occupied = {entry["id"] for entry in components}
    candidate, suffix = preferred, 0
    while candidate in occupied:
        suffix += 1
        candidate = f"{preferred}_{suffix}"
    return candidate


def _loopx_state_components(env, components, output_dir):
    """Adapt the existing backup owner's inventory for one isolated trial.

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
        raise ValueError("LoopX state capture requires this worker's single-Goal trial registry")
    # Reject lexical route aliases before backup discovery resolves them.
    goal = goals[0]
    if Path(goal["repo"]) != project or Path(source.get("common_runtime_root", "")) != runtime:
        raise ValueError("LoopX checkpoint routes differ from the registered trial")
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
    from .codex import Execution
    from .sessions import BenchmarkSessionWake
    source_wake = BenchmarkSessionWake(env, Execution(context="resume",
        sandbox=env.get("LOOPX_CODEX_SANDBOX", "danger-full-access")), {})
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
    return components, source_wake


def research_participants(env, components, metadata, destination, evidence_path, deadline):
    """Use the same inventory and historical context at either owned boundary."""
    research = None
    components, metadata["loopx_state"] = _loopx_state_components(env, components, destination)
    from loopx.control_plane.coordination.local_authority import local_authority_is_promoted
    if local_authority_is_promoted(runtime_root=Path(env["LOOPX_RUNTIME_ROOT"]), goal_id=env["LOOPX_GOAL_ID"]):
        from .research_checkpoint import research_context, _encoded
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
    from .research_checkpoint import research_context, verify_context_sources
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


def _mark_replan_attempt(root: Path, obligation_id: str, turn_id: str, request_sha256: str | None) -> bool:
    """The first boundary wins even when its optional declaration was lost."""
    if not root.is_absolute() or root != root.resolve():
        raise ValueError("replan checkpoint root must be a normalized physical path")
    attempts = root / ".replan-attempts"
    attempts.mkdir(parents=True, exist_ok=True, mode=0o700)
    if attempts != attempts.resolve():
        raise ValueError("replan attempt directory must not contain symlinks")
    attempt_path = attempts / (obligation_id + ".json")
    attempt = {"obligation_id": obligation_id, "turn_id": turn_id,
               "request_sha256": request_sha256}
    try:
        with attempt_path.open("x", encoding="utf-8") as marker:
            marker.write(json.dumps(attempt))
        attempt_path.chmod(0o600)
        return True
    except FileExistsError:
        if attempt_path.is_symlink():
            raise ValueError("replan attempt must not be a symlink")
        prior_attempt = json.loads(attempt_path.read_text())
        # None means the first declaration was unavailable, not permission to
        # replace that lost point with a repaired file and later model state.
        if (prior_attempt["obligation_id"] != obligation_id or
                prior_attempt["request_sha256"] not in (None, request_sha256)):
            raise ValueError("existing replan attempt has a different declaration")
        return False


def _prior_replan_guard_seen(env: dict[str, str], current: Path, obligation_id: str) -> bool:
    """An absent optional index does not erase the original Host preflight.

    Reuse saved guard packets and the typed replan selector. They are already
    written before optional capture and before the model can decide or work.
    No separate journal, model-text classifier or new replan owner is needed.
    """
    for path in sorted(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/checkpoint-start-guard.json")):
        if path == current:
            continue
        if path.resolve(strict=True) != path:
            raise ValueError("prior checkpoint guard must be a physical Host file")
        guard = json.loads(path.read_text())
        prior = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "select_replan", "quota": guard, "goal_id": env["LOOPX_GOAL_ID"],
            "agent_id": env["LOOPX_AGENT_ID"], "turn_instance_id": path.parent.name,
        })
        if prior["capture"] and prior["obligation_id"] == obligation_id:
            return True
    return False


def capture_before_replan(env: dict[str, str], receipt: dict[str, Any], body: str) -> str:
    """Preload the original guard once, then capture before invoking the model.

    The typed owner selects only an admitted replan identity. The prior wake's
    existing settlement owner still qualifies the source boundary. A missed
    point is explicit and cannot be replaced by an after-decision capture.
    """
    path = env.get("LOOPX_CHECKPOINT_REQUEST")
    if not path:
        return body
    declaration_error = None
    try:
        request, raw = read_checkpoint_request(path)
    except (OSError, ValueError) as error:
        declaration_error = error
        request, raw = {}, None
    if declaration_error is None and request.get("trigger") != "fresh_replan":
        return body
    if receipt.get("mode") != "heartbeat" or receipt.get("stage") != "execute":
        raise ValueError("fresh_replan checkpoint trigger requires a heartbeat execute wake")
    started = time.monotonic()
    guard_path = Path(env["LOOPX_WAKE_LOG_DIR"]) / receipt["turn_id"] / "checkpoint-start-guard.json"
    command = [env["LOOPX_CLI"], "--format", "json", "--registry", env["LOOPX_REGISTRY"],
               "--runtime-root", env["LOOPX_RUNTIME_ROOT"], "quota", "should-run",
               "--goal-id", env["LOOPX_GOAL_ID"], "--agent-id", env["LOOPX_AGENT_ID"],
               "--runtime-profile", "generic_cli", "--turn-instance-id", receipt["turn_id"],
               "--available-capability", "shell", "--available-capability", "filesystem_write"]
    guard_run = subprocess.run(command, cwd=env["LOOPX_PROJECT"], env=env, capture_output=True,
                               text=True, timeout=120, check=True)
    guard = json.loads(guard_run.stdout)
    if not isinstance(guard, dict) or guard.get("ok") is not True:
        raise ValueError("checkpoint requires a successful original quota guard")
    # A previous preflight on this wake may already have allowed model work,
    # even if its optional attempt marker (or final receipt) could not be saved.
    # Such reentry can verify an existing point, but cannot capture a later one.
    preflight_seen = guard_path.exists()
    guard_path.write_text(guard_run.stdout, encoding="utf-8")
    guard_path.chmod(0o600)
    preloaded_body = (f"Host checkpoint preflight has already executed this wake's original quota guard once. "
        f"Read the complete packet at {guard_path} before work, then follow its interaction_contract. "
        "Do not repeat the initial guard below; use its selection or recovery command when required.\n\n" + body)
    receipt["checkpoint"] = {"captured": False, "research_point_qualified": False, "phase": "before_replan"}
    try:
        intent = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "select_replan", "quota": guard, "goal_id": env["LOOPX_GOAL_ID"],
            "agent_id": env["LOOPX_AGENT_ID"], "turn_instance_id": receipt["turn_id"],
        })
        root = Path(env["LOOPX_WAKE_LOG_DIR"]).parent / "checkpoints"
        if declaration_error is not None:
            if intent["capture"]:
                _mark_replan_attempt(root, intent["obligation_id"], receipt["turn_id"], None)
            raise declaration_error
        if not intent["capture"]:
            receipt["checkpoint"]["reason"] = intent["reason"]
        else:
            destination = root / intent["obligation_id"]
            if destination.parent != root or destination != destination.resolve():
                raise ValueError("replan checkpoint identity must select a direct child")
            if not destination.exists() and (preflight_seen or
                    _prior_replan_guard_seen(env, guard_path, intent["obligation_id"]) or
                    not _mark_replan_attempt(root, intent["obligation_id"], receipt["turn_id"],
                                             hashlib.sha256(raw).hexdigest())):
                receipt["checkpoint"]["reason"] = "pre_decision_capture_already_attempted"
                return preloaded_body
            requested_root = Path(request["destination"])
            if not requested_root.is_absolute() or requested_root != requested_root.resolve() or requested_root != root:
                raise ValueError("fresh_replan destination must be this trial's checkpoints directory")
            if destination.exists():
                # Readback of the first point, never overwrite it with later work.
                manifest = json.loads((destination / "checkpoint.json").read_text())
                metadata = manifest["point_metadata"]
                if (metadata["request_sha256"] != hashlib.sha256(raw).hexdigest() or
                        metadata["operator_metadata"]["replan"]["obligation_id"] != intent["obligation_id"]):
                    raise ValueError("existing replan point has a different declaration")
                verified = effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "verify", "root": str(destination), "expected_sha256": manifest["sha256"],
                })
                receipt["checkpoint"].update(reason="replan_already_captured", destination=str(destination), **verified)
            else:
                # A failed first capture is a missed point. Once its model is
                # allowed to decide/work, a retry must not relabel later state
                # as the original pre-decision checkpoint.
                prior_paths = sorted(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("wake-*/receipt.json"))
                prior_paths = [p for p in prior_paths if p.parent.name != receipt["turn_id"]]
                if not prior_paths:
                    raise ValueError("replan capture has no completed prior Host wake")
                prior = json.loads(prior_paths[-1].read_text())
                if prior.get("stage") != "execute" or prior.get("mode") != "heartbeat" or not prior.get("ok"):
                    raise ValueError("replan capture requires the latest successful work wake")
                effective_request = {**request, "destination": str(destination),
                    "point_metadata": {**request["point_metadata"], "replan": intent,
                                       "capture_order": "before_model_replan_decision"}}
                effective_request.pop("trigger")
                capture_after_wake(env, prior, supplied_request=(effective_request, raw))
                receipt["checkpoint"].update(prior["checkpoint"], phase="before_replan")
                receipt["checkpoint"]["source_turn_id"] = prior["turn_id"]
    except Exception as error:
        receipt["checkpoint"].update(reason="capture_failed", error_kind=type(error).__name__, error=str(error)[:500])
    finally:
        receipt["checkpoint"]["elapsed_seconds"] = time.monotonic() - started
    # The model consumes exactly the owner's original decision and keeps its
    # selection, settlement, required-read and recovery instructions intact.
    return preloaded_body


def capture_after_wake(env: dict[str, str], receipt: dict[str, Any], *,
                       supplied_request: tuple[dict[str, Any], bytes] | None = None) -> None:
    request_path = env.get("LOOPX_CHECKPOINT_REQUEST")
    if not request_path:
        return  # Feature-off path performs no additional read or write.
    if supplied_request is None and (receipt.get("checkpoint") or {}).get("phase") == "before_replan":
        return  # The after-wake hook cannot replace the pre-decision point.
    receipt["checkpoint"] = {"captured": False, "research_point_qualified": False}
    if (not receipt.get("ok") or receipt.get("stage") != "execute" or
            receipt.get("mode") not in {"heartbeat", "turn"} or receipt.get("budget_exhausted")):
        receipt["checkpoint"]["reason"] = "wake_not_successful_work"
        return
    started = time.monotonic()
    try:
        phase_deadline = env.get("LOOPX_PHASE_DEADLINE_EPOCH")
        deadline = time.time() + 30
        if phase_deadline is not None:
            original_deadline = float(phase_deadline)
            if not math.isfinite(original_deadline):
                raise ValueError("checkpoint requires a finite original phase deadline")
            deadline = min(deadline, original_deadline)
        if deadline <= time.time():
            receipt["checkpoint"]["reason"] = "capture_window_closed"
            return
        if supplied_request is None:
            request, raw = read_checkpoint_request(request_path)
        else:
            request, raw = supplied_request
        if request.get("trigger") == "fresh_replan":
            receipt["checkpoint"]["reason"] = "pre_replan_hook_required"
            return
        # This is a private evidence artifact below the runner's log owner.
        destination = Path(request["destination"])
        root = Path(env["LOOPX_WAKE_LOG_DIR"]).parent / "checkpoints"
        if not destination.is_absolute() or destination != destination.resolve() or destination.parent != root:
            raise ValueError("checkpoint destination must be a normalized direct child of this trial's checkpoints directory")
        if destination.exists():
            receipt["checkpoint"]["reason"] = "destination_already_present_no_replay"
            return
        observed = read_heartbeat_settlement(
            Path(env["LOOPX_RUNTIME_ROOT"]), goal_id=env["LOOPX_GOAL_ID"],
            agent_id=env["LOOPX_AGENT_ID"], todo_id=None,
            turn_instance_id=receipt["turn_id"], resolve_original_binding=True,
            registry_path=Path(env["LOOPX_REGISTRY"]),
        )
        if observed is None or (observed.progress or {}).get("state") != "settled":
            receipt["checkpoint"]["reason"] = "owned_turn_not_settled"
            return
        identity = observed.identity.value
        if identity is None:
            raise ValueError("checkpoint settlement identity is unavailable")
        # Only this returned typed owner decision admits the capture boundary.
        # Point metadata remains evidence, never authority or scientific validity.
        metadata = {
            "operator_metadata": request["point_metadata"],
            "wake_turn_id": receipt["turn_id"],
            "settlement_effect_id": identity.effect_id,
            "session": receipt.get("session"),
            "request_sha256": hashlib.sha256(raw).hexdigest(),
        }
        components = request["components"]
        if request.get("loopx_state"):
            evidence = Path(env["LOOPX_WAKE_LOG_DIR"]) / receipt["turn_id"] / "checkpoint-research-context.json"
            components, research = research_participants(env, components, metadata, destination, evidence, deadline)
        opportunity = None
        if request.get("evaluation_opportunity") and not request.get("controller_handoff"):
            from .sforge_checkpoint import capture_opportunity

            evidence = Path(env["LOOPX_WAKE_LOG_DIR"]) / receipt["turn_id"] / "checkpoint-opportunity.json"
            incumbent = evidence.with_name("checkpoint-incumbent.tar.gz")
            opportunity = capture_opportunity(env, timeout=max(.1, deadline - time.time()), incumbent_path=incumbent)
            evidence.write_text(json.dumps(opportunity), encoding="utf-8")
            evidence.chmod(0o600)
            opportunity_id = _participant_id(components, "evaluation_opportunity")
            components = [*components, {"id": opportunity_id, "source_path": str(evidence)}]
            incumbent_id = None
            if opportunity["native_incumbent"] is not None:
                incumbent_id = _participant_id(components, "evaluation_incumbent")
                components = [*components, {"id": incumbent_id, "source_path": str(incumbent)}]
            # Local provider evidence mapping, not evaluation admission. Keep
            # caller IDs intact and bind the witness to the allocated archive.
            metadata["evaluation_opportunity"] = {"component_id": opportunity_id,
                                                   "incumbent_component_id": incumbent_id}
        if request.get("native_session"):
            from .native_checkpoint import native_session_checkpoint
            from .codex import Execution
            from .sessions import BenchmarkSessionWake

            session = receipt.get("session") or {}
            components, source_wake = native_binding_participant(env, components, metadata)
            native = native_session_checkpoint(env, session.get("session_id", ""),
                                               timeout=max(.1, deadline - time.time()))
            # Native names identify provider files, not reserved artifact IDs.
            # Allocate in the same namespace as caller and other participants;
            # retain the provider observation unchanged for the stability reread.
            native_ids = {}
            for entry in native["components"]:
                component_id = _participant_id(components, entry["id"])
                native_ids[entry["id"]] = component_id
                components = [*components, {**entry, "id": component_id}]
            metadata["native_session"] = {**native["metadata"], "store_members": [
                {**member, "id": native_ids[member["id"]]}
                for member in native["metadata"]["store_members"]]}
        probe = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "probe", "components": components,
            "deadline_epoch_ms": deadline * 1000,
        }, timeout=max(.1, deadline - time.time()), retry_safe=False)
        if request.get("native_session"):
            # Native readback precedes the byte witness. A completed later Turn
            # in that gap must not be sealed under the earlier cursor. Re-read
            # after the probe; capture's existing witness comparison then guards
            # source writes during this recheck and through atomic publication.
            confirmed = native_session_checkpoint(env, session.get("session_id", ""),
                                                  timeout=max(.1, deadline - time.time()))
            if confirmed != native:
                raise ValueError("native checkpoint cursor or participant basis moved before sealing")
            confirmed_wake = BenchmarkSessionWake(env, Execution(context="resume",
                sandbox=env.get("LOOPX_CODEX_SANDBOX", "danger-full-access")), {})
            if ((confirmed_wake.session_id, confirmed_wake.goal_ref, confirmed_wake.digest) !=
                    (source_wake.session_id, source_wake.goal_ref, source_wake.digest)):
                raise ValueError("native checkpoint source session binding moved before sealing")
        if request.get("loopx_state"):
            verify_research_participants(env, components, metadata, research, probe["source_witness"], deadline)
        if opportunity is not None and opportunity["native_incumbent"] is not None:
            selected = opportunity["native_incumbent"]
            witness = probe["source_witness"].get(metadata["evaluation_opportunity"]["incumbent_component_id"], [])
            if (len(witness) != 1 or witness[0].get("path") != "" or witness[0].get("kind") != "file" or
                    witness[0].get("sha256") != selected["archive_sha256"] or
                    witness[0].get("size") != selected["archive_bytes"]):
                raise ValueError("native incumbent participant differs from its captured result")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if request.get("controller_handoff"):
            if research is None:
                raise ValueError("controller checkpoint requires complete canonical research context")
            from .checkpoint_handoff import await_controller_capture
            captured = await_controller_capture(root, components, metadata, probe, deadline)
            receipt["checkpoint"] = {"captured": True, **captured}
            return
        captured = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "capture", "destination": str(destination),
            "components": components, "point_metadata": metadata,
            "expected_source_sha256": probe["source_sha256"],
            "deadline_epoch_ms": deadline * 1000,
        }, timeout=max(.1, deadline - time.time()), retry_safe=False)
        receipt["checkpoint"] = {"captured": True, "destination": str(destination), **captured}
    except Exception as error:
        # Capture failure is explicit but cannot rewrite an already settled work
        # outcome or replay its model/evaluation. Keep the source trial running.
        receipt["checkpoint"].update(reason="capture_failed", error_kind=type(error).__name__, error=str(error)[:500])
    finally:
        receipt["checkpoint"]["elapsed_seconds"] = time.monotonic() - started


@contextmanager
def held_quota_decision(listener, capture_root: Path, *, goal_id: str, agent_id: str,
                        turn_instance_id: str | None, native_process=None, interrupt_timeout: float = 10,
                        interrupt_when=None, deadline: float | None = None):
    """Host-side counterpart of the explicit quota pre-output transport.

    The existing typed owner decides whether this exact packet admits a fresh
    replan. The caller must capture while inside the context. Normal exit only
    releases the original tool response; it certifies no research checkpoint.
    Failure/disconnection never retries the guard or substitutes a new point.
    The listener must already be bound by the Host in a private directory.

    Passing the live child_process-owned native CLI explicitly interrupts and
    reaps it before selection/capture. Its pending tool is aborted; normal exit
    returns a source-only continuation body containing the original packet,
    rather than acknowledging a dead socket. This is not a complete workspace
    barrier, destination admission, or a qualified interrupted native cursor.
    interrupt_when selects capture candidates through the typed intent before
    interruption; false releases ordinary/duplicate guards without a native
    restart. Selection is not a pre-decision proof: interrupted cursor
    qualification must still reject any native work beyond the pending guard.
    Without native_process this is output ordering only; a slow capture can
    outlive the native tool's yield interval and does not stop inference.
    """
    if not capture_root.is_absolute() or capture_root.resolve(strict=True) != capture_root:
        raise ValueError("decision capture root must be a physical absolute directory")
    def remaining():
        limit = 120 if deadline is None else min(120, deadline - time.monotonic())
        if limit <= 0:
            raise TimeoutError("original wake budget expired during decision handoff")
        return limit

    channel, _ = listener.accept()
    with channel:
        channel.settimeout(remaining())
        with channel.makefile("rb") as stream:
            line = stream.readline(4097)
        message = json.loads(line)
        if (not isinstance(message, dict) or set(message) != {"path", "sha256"} or
                not isinstance(message["path"], str) or not isinstance(message["sha256"], str)):
            raise ValueError("invalid decision handoff request")
        path = Path(message["path"])
        if (path.name != "decision.json" or path.parent.parent != capture_root or
                path.resolve(strict=True) != path):
            raise ValueError("decision handoff must reference this Host's capture root")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != message["sha256"]:
            raise ValueError("decision handoff digest mismatch")
        quota = json.loads(raw)
        interrupted = None
        if native_process is not None and interrupt_when is None:
            from .worker import interrupt_checkpoint_process

            # Interrupt before the typed selector: even this read can outlive a
            # native tool yield. Keep original bytes for same-identity recovery.
            interrupted = interrupt_checkpoint_process(native_process, timeout=min(interrupt_timeout, remaining()))
        intent = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "select_replan", "quota": quota, "goal_id": goal_id,
            "agent_id": agent_id, "turn_instance_id": (turn_instance_id if turn_instance_id is not None
                else (quota.get("heartbeat_receipt") or {}).get("turn_instance_id")),
        }, timeout=remaining())
        if native_process is not None and interrupt_when is not None and interrupt_when(intent):
            from .worker import interrupt_checkpoint_process

            interrupted = interrupt_checkpoint_process(native_process, timeout=min(interrupt_timeout, remaining()))
        held = {"path": path, "sha256": message["sha256"], "quota": quota, "intent": intent}
        if interrupted is not None:
            held["native_process_return_code"] = interrupted
        yield held
        if path.read_bytes() != raw:
            raise ValueError("original decision changed while held")
        if interrupted is None:
            channel.sendall(json.dumps({"released": True, "sha256": message["sha256"]}).encode() + b"\n")
        else:
            # Expose only after capture/readback succeeds. A failing consumer
            # cannot mistake the interruption for ordinary continuation.
            held["continuation_body"] = (
                "The Host interrupted this source session while its quota tool response was held. "
                "The pending tool was aborted; its guard already committed. Consume the original "
                "packet below in this same source session and preserve its Turn identity, required "
                "reads, selection and settlement instructions. Do not rerun that guard or infer "
                "a successful checkpoint from this message. This grants no destination authority.\n\n"
                + raw.decode("utf-8")
            )
