"""Opt-in inert artifact capture after the existing benchmark wake has settled.

The TS backup/artifact owner verifies source stability and materialized bytes.
Python adapts the owned wake, source admission and explicit operator file list;
it never restores claims, starts a model or certifies a complete research point.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.hosts.research_checkpoint import (
    _participant_id, native_binding_participant, research_participants, verify_research_participants,
)
from .checkpoint_handoff import CHECKPOINT_CAPTURE_SECONDS


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
        }, large_local_snapshot=True)
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
    # Use the CLI's original-decision capture, including with TurnEnvelope off.
    # The checkpoint selector needs the full source; the model consumes the
    # ordinary CLI projection of those same bytes, never a second guard.
    capture_root = Path(tempfile.mkdtemp(prefix="checkpoint-start-", dir=guard_path.parent))
    command = [env["LOOPX_CLI"], "--cli-bin", env["LOOPX_CLI"], "--format", "json", "--registry", env["LOOPX_REGISTRY"],
               "--runtime-root", env["LOOPX_RUNTIME_ROOT"], "quota", "should-run",
               "--goal-id", env["LOOPX_GOAL_ID"], "--agent-id", env["LOOPX_AGENT_ID"],
               "--runtime-profile", "generic_cli", "--turn-instance-id", receipt["turn_id"],
               "--available-capability", "shell", "--available-capability", "filesystem_write",
               "--decision-output-root", str(capture_root)]
    guard_run = subprocess.run(command, cwd=env["LOOPX_PROJECT"], env=env, capture_output=True,
                               text=True, timeout=120, check=True)
    projected = json.loads(guard_run.stdout)
    captures = list(capture_root.glob("decision-*/decision.json"))
    if len(captures) != 1:
        raise ValueError("checkpoint requires exactly one captured original quota decision; do not replay the guard")
    raw_guard = captures[0].read_bytes()
    guard = json.loads(raw_guard)
    if (not isinstance(guard, dict) or guard.get("ok") is not True or
            not isinstance(projected, dict) or projected.get("ok") is not True):
        raise ValueError("checkpoint requires a successful original quota guard")
    # A previous preflight on this wake may already have allowed model work,
    # even if its optional attempt marker (or final receipt) could not be saved.
    # Such reentry can verify an existing point, but cannot capture a later one.
    preflight_seen = guard_path.exists()
    guard_path.write_bytes(raw_guard)
    guard_path.chmod(0o600)
    view_path = guard_path.with_name("checkpoint-start-view.json")
    view_path.write_text(guard_run.stdout, encoding="utf-8")
    view_path.chmod(0o600)
    preloaded_body = (f"Host checkpoint preflight has already executed this wake's original quota guard once. "
        f"Read the complete CLI view at {view_path} before work, then follow its interaction_contract "
        "and replan review/writeback reads when present. "
        "Do not repeat the initial guard below; use its selection or recovery command when required.\n\n" + body)
    receipt["checkpoint"] = {"captured": False, "research_point_qualified": False, "phase": "before_replan"}
    try:
        intent = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "select_replan", "quota": guard, "goal_id": env["LOOPX_GOAL_ID"],
            "agent_id": env["LOOPX_AGENT_ID"], "turn_instance_id": receipt["turn_id"],
        }, large_local_snapshot=True)
        from .checkpoint_continuation import restored_capture_gate
        restore_gate = restored_capture_gate(env, intent.get("obligation_id"),
            full_history=not request.get("native_session", False))
        receipt["checkpoint_restore"] = {k: v for k, v in restore_gate.items() if k != "instructions"}
        preloaded_body = restore_gate.get("instructions", "") + preloaded_body
        if not restore_gate["capture_allowed"]:
            receipt["checkpoint"]["reason"] = restore_gate["reason"]
            return preloaded_body
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
                }, large_local_snapshot=True)
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
                capture_after_wake(env, prior, supplied_request=(effective_request, raw), pending_guard=guard_path,
                                   restore_gate=restore_gate)
                receipt["checkpoint"].update(prior["checkpoint"], phase="before_replan")
                receipt["checkpoint"]["source_turn_id"] = prior["turn_id"]
    except Exception as error:
        receipt["checkpoint_restore"] = {"capture_allowed": False, "reason": "capture_basis_unavailable"}
        receipt["checkpoint"].update(reason="capture_failed", error_kind=type(error).__name__, error=str(error)[:500])
    finally:
        receipt["checkpoint"]["elapsed_seconds"] = time.monotonic() - started
    # The model consumes exactly the owner's original decision and keeps its
    # selection, settlement, required-read and recovery instructions intact.
    return preloaded_body


def capture_after_wake(env: dict[str, str], receipt: dict[str, Any], *,
                       supplied_request: tuple[dict[str, Any], bytes] | None = None,
                       pending_guard: Path | None = None,
                       restore_gate: dict[str, Any] | None = None) -> None:
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
        window = CHECKPOINT_CAPTURE_SECONDS if supplied_request and supplied_request[0].get("controller_handoff") else 30
        deadline = time.time() + window
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
        if supplied_request is None:
            from .checkpoint_continuation import restored_capture_gate
            restore_gate = restored_capture_gate(env, full_history=not request.get("native_session", False))
            if not restore_gate["capture_allowed"]:
                receipt["checkpoint"]["reason"] = restore_gate["reason"]
                return
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
        if pending_guard is not None:
            decision_id = _participant_id(components, "held_decision")
            components = [*components, {"id": decision_id, "source_path": str(pending_guard)}]
            metadata.update(held_decision_component_id=decision_id,
                decision_sha256=hashlib.sha256(pending_guard.read_bytes()).hexdigest(),
                replan=request["point_metadata"]["replan"], capture_order="before_model_replan_decision")
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
        # Share the inspection process, not its observations: reread the full
        # native history after the byte probe. Closing can checkpoint native SQLite,
        # so keep the reader alive through sealing/the controller writer barrier.
        with ExitStack() as readers:
            if request.get("native_session"):
                from loopx.hosts.codex_checkpoint import native_checkpoint_reader, native_session_checkpoint
                from .checkpoint_continuation import restored_capture_history
                from .codex import Execution
                from .sessions import BenchmarkSessionWake

                session = receipt.get("session") or {}
                components, source_wake = native_binding_participant(env, components, metadata)
                native_options = restored_capture_history(env, restore_gate,
                    session_id=session.get("session_id", ""))
                reader = readers.enter_context(native_checkpoint_reader(env, timeout=max(.1, deadline - time.time())))
                native = native_session_checkpoint(env, session.get("session_id", ""),
                                                   timeout=max(.1, deadline - time.time()), transport=reader,
                                                   **native_options)
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
            }, timeout=max(.1, deadline - time.time()), retry_safe=False, large_local_snapshot=True)
            if request.get("native_session"):
                # Native readback precedes the byte witness. A completed later Turn
                # in that gap must not be sealed under the earlier cursor. Re-read
                # after the probe; capture's existing witness comparison then guards
                # source writes during this recheck and through atomic publication.
                if restored_capture_history(env, restore_gate,
                        session_id=session.get("session_id", "")) != native_options:
                    raise ValueError("restored capture source changed before sealing")
                confirmed = native_session_checkpoint(env, session.get("session_id", ""),
                                                      timeout=max(.1, deadline - time.time()), transport=reader,
                                                      **native_options)
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
                captured = await_controller_capture(root, components, metadata, probe, deadline,
                    phase_deadline=float(phase_deadline) if phase_deadline is not None else None)
                receipt["checkpoint"] = {"captured": True, **captured}
                return
            captured = effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "capture", "destination": str(destination),
                "components": components, "point_metadata": metadata,
                "expected_source_sha256": probe["source_sha256"],
                "deadline_epoch_ms": deadline * 1000,
            }, timeout=max(.1, deadline - time.time()), retry_safe=False, large_local_snapshot=True)
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
                        interrupt_when=None, deadline: float | None = None, accepted_channel=None):
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
    def remaining():
        limit = 120 if deadline is None else min(120, deadline - time.monotonic())
        if limit <= 0:
            raise TimeoutError("original wake budget expired during decision handoff")
        return limit

    channel = accepted_channel
    if channel is None:
        channel, _ = listener.accept()
    with channel:
        channel.settimeout(remaining())
        with channel.makefile("rb") as stream:
            line = stream.readline(4097)
        # Consume the bounded request before checking Host prerequisites. A
        # refused root must close this channel so the same source can recover;
        # leaving it queued would keep the caller's listener always readable.
        if not capture_root.is_absolute() or capture_root.resolve(strict=True) != capture_root:
            raise ValueError("decision capture root must be a physical absolute directory")
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
        # This is transport dispatch, not capture admission: an explicit quota
        # failure still belongs to its original caller's recovery path. Do not
        # signal that writer or submit its failure to the successful-readback
        # selector. Successful and foreign/malformed packets retain typed checks.
        failed_readback = isinstance(quota, dict) and quota.get("ok") is False and quota.get("goal_id") == goal_id
        interrupted = None
        if not failed_readback and native_process is not None and interrupt_when is None:
            from .worker import interrupt_checkpoint_process

            # Interrupt before the typed selector: even this read can outlive a
            # native tool yield. Keep original bytes for same-identity recovery.
            interrupted = interrupt_checkpoint_process(native_process, timeout=min(interrupt_timeout, remaining()))
        if failed_readback:
            intent = {"capture": False, "reason": "quota_readback_failed", "execution_authority_granted": False}
        else:
            intent = effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "select_replan", "quota": quota, "goal_id": goal_id,
                "agent_id": agent_id, "turn_instance_id": (turn_instance_id if turn_instance_id is not None
                    else (quota.get("heartbeat_receipt") or {}).get("turn_instance_id")),
            }, timeout=remaining(), large_local_snapshot=True)
        if not failed_readback and native_process is not None and interrupt_when is not None and interrupt_when(intent):
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
