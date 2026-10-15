"""Opt-in native CLI rendezvous inside an existing heartbeat wake.

This adapter owns process/transport ordering, not replan admission or complete
research-point qualification. Typed quota/artifact owners retain those rules.
"""
from __future__ import annotations

import hashlib
import select
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from loopx.control_plane.effect_runtime import effect_runtime_result
from .checkpoint import _mark_replan_attempt, _participant_id, held_quota_decision, read_checkpoint_request


class CheckpointWake:
    """One private listener survives all source-session resumptions in a wake."""

    def __init__(self, env, receipt):
        self.env, self.receipt = env, receipt
        self.stack = ExitStack()
        self.listener = None
        self.socket_path = None
        self.request = None
        self.raw = None
        self.root = Path(env["LOOPX_WAKE_LOG_DIR"]).parent / "checkpoints"
        self.capture_root = Path(env["LOOPX_WAKE_LOG_DIR"]) / receipt["turn_id"] / "decisions"

    def __enter__(self):
        path = self.env.get("LOOPX_CHECKPOINT_REQUEST")
        if not path or self.receipt["mode"] != "heartbeat" or self.receipt["stage"] != "execute":
            return self
        try:
            self.request, self.raw = read_checkpoint_request(path)
        except (OSError, ValueError):
            return self  # The existing preflight records declaration failure.
        if self.request.get("trigger") != "fresh_replan":
            return self
        if Path(self.request["destination"]) != self.root or self.root.resolve() != self.root:
            raise ValueError("fresh_replan destination must be this trial's checkpoints directory")
        try:
            # AF_UNIX paths are short; trial roots need not be. No ambient socket
            # or socket authority is inherited from the caller's environment.
            directory = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="loopx-ckpt-", dir="/tmp"))
            self.socket_path = str(Path(directory).resolve() / "host.sock")
            self.listener = self.stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            self.listener.bind(self.socket_path)
            self.listener.listen(1)
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def _first_replan(self, intent):
        # The preflight verifies immutable source, current canonical review
        # and completed native continuation before launching this wake. A
        # mid-wake guard cannot bypass that shared restoration barrier.
        from loopx.control_plane.turn_driver.codex_sessions import load_codex_cli_session
        binding = load_codex_cli_session(Path(self.env["LOOPX_RUNTIME_ROOT"]), lineage={
            "goal_id": self.env["LOOPX_GOAL_ID"], "agent_id": self.env["LOOPX_AGENT_ID"]}, session_scope="agent")
        restored = bool(binding and binding.get("checkpoint_artifact_sha256"))
        gate = self.receipt.get("checkpoint_restore", {})
        if (not gate.get("capture_allowed", not restored) or
                intent.get("obligation_id") == gate.get("review_obligation_id")):
            return False
        if not intent["capture"] or not _mark_replan_attempt(
                self.root, intent["obligation_id"], self.receipt["turn_id"],
                hashlib.sha256(self.raw).hexdigest()):
            return False
        if self.request.get("native_session") and sys.platform == "darwin":
            # The current Darwin CLI can persist only the user item after an
            # interrupted tool, losing the cursor needed for a verified fork.
            # Refuse before signalling it; neither a delay nor a relaxed history
            # predicate can recover that point. Completed-wake capture is separate.
            self.receipt.setdefault("checkpoint_handoffs", []).append({
                "obligation_id": intent["obligation_id"], "captured": False,
                "research_point_qualified": False, "error_kind": "ValueError",
                "error": "Interrupted native checkpoint capture is not qualified on Darwin; original tool continues.",
            })
            return False
        return True

    def _capture(self, held, deadline):
        """Capture declared files/native history; never downgrade research/effects."""
        if (any(self.request.get(key) for key in ("loopx_state", "evaluation_opportunity"))
                and not self.request.get("controller_handoff")):
            raise ValueError("Interrupted full-state capture requires the controller writer barrier")
        destination = self.root / held["intent"]["obligation_id"]
        metadata = {"operator_metadata": self.request["point_metadata"],
                    "request_sha256": hashlib.sha256(self.raw).hexdigest(),
                    "session": self.receipt["session"], "replan": held["intent"],
                    "decision_sha256": held["sha256"],
                    "capture_order": "after_native_interruption",
                    "writer_barrier": "owned_native_process_group_only"}
        window = min(time.time() + 30, time.time() + deadline - time.monotonic())
        if window <= time.time():
            raise TimeoutError("original wake budget expired before capture")
        components = list(self.request["components"])
        native = None
        research = None
        if self.request.get("loopx_state"):
            from .checkpoint import research_participants
            evidence = held["path"].with_name(held["path"].stem + "-research.json")
            components, research = research_participants(self.env, components, metadata, destination, evidence, window)
            if research is None:
                raise ValueError("controller checkpoint requires complete canonical research context")
        if self.request.get("native_session"):
            from loopx.control_plane.turn_driver.codex_checkpoint import native_session_checkpoint
            from .checkpoint import native_binding_participant
            components, source_wake = native_binding_participant(self.env, components, metadata)
            guard = {"cli": self.env["LOOPX_CLI"], "workspace": self.env["LOOPX_PROJECT"],
                     "registry": self.env["LOOPX_REGISTRY"], "runtime_root": self.env["LOOPX_RUNTIME_ROOT"],
                     "goal_id": self.env["LOOPX_GOAL_ID"], "agent_id": self.env["LOOPX_AGENT_ID"],
                     "turn_instance_id": held["quota"]["heartbeat_receipt"]["turn_instance_id"],
                     "capture_root": str(self.capture_root), "socket_path": self.socket_path,
                     "decision_sha256": held["sha256"]}
            native = native_session_checkpoint(self.env, self.receipt["session"]["session_id"],
                timeout=max(.1, window - time.time()), interrupted_guard=guard)
            allocated = {}
            for entry in native["components"]:
                component_id = _participant_id(components, entry["id"])
                allocated[entry["id"]] = component_id
                components.append({**entry, "id": component_id})
            decision_id = _participant_id(components, "held_decision")
            components.append({"id": decision_id, "source_path": str(held["path"])})
            metadata["native_session"] = {**native["metadata"], "store_members": [
                {**member, "id": allocated[member["id"]]} for member in native["metadata"]["store_members"]]}
            metadata["held_decision_component_id"] = decision_id
        probe = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "probe", "components": components, "deadline_epoch_ms": window * 1000,
        }, timeout=max(.1, window - time.time()), retry_safe=False)
        if native is not None:
            confirmed = native_session_checkpoint(self.env, self.receipt["session"]["session_id"],
                timeout=max(.1, window - time.time()), interrupted_guard=guard)
            from .sessions import BenchmarkSessionWake
            from .codex import Execution
            confirmed_wake = BenchmarkSessionWake(self.env, Execution(context="resume",
                sandbox=self.env.get("LOOPX_CODEX_SANDBOX", "danger-full-access")), {})
            if ((confirmed_wake.session_id, confirmed_wake.goal_ref, confirmed_wake.digest) !=
                    (source_wake.session_id, source_wake.goal_ref, source_wake.digest)):
                raise ValueError("interrupted source session binding moved before sealing")
            if confirmed != native:
                raise ValueError("interrupted native cursor moved before sealing")
        if self.request.get("loopx_state"):
            from .checkpoint import verify_research_participants
            verify_research_participants(self.env, components, metadata, research, probe["source_witness"], window)
        if self.request.get("controller_handoff"):
            from .checkpoint_handoff import await_controller_capture
            return await_controller_capture(self.root, components, metadata, probe, window)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "capture", "components": components, "destination": str(destination),
            "expected_source_sha256": probe["source_sha256"], "point_metadata": metadata,
            "deadline_epoch_ms": window * 1000,
        }, timeout=max(.1, window - time.time()), retry_safe=False)

    def run(self, execution, body, wake, session_wake, stdout, stderr, timeout):
        from .worker import child_process, native_command
        from .sessions import BenchmarkSessionWake

        deadline = time.monotonic() + timeout if timeout is not None else float("inf")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("checkpoint source wake", timeout)
            command = native_command(self.env, execution, "execute", wake, session_wake)
            continuation = None
            with child_process(command, env=self.env, stdout=stdout, stderr=stderr) as process:
                process.stdin.write(body)
                process.stdin.close()
                while process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    if not select.select([self.listener], [], [], min(.1, remaining))[0]:
                        continue
                    with held_quota_decision(self.listener, self.capture_root,
                            goal_id=self.env["LOOPX_GOAL_ID"], agent_id=self.env["LOOPX_AGENT_ID"],
                            turn_instance_id=None, native_process=process,
                            interrupt_timeout=min(10, remaining), interrupt_when=self._first_replan,
                            deadline=deadline) as held:
                        if "native_process_return_code" not in held:
                            continue
                        stdout.flush()
                        session_wake.observe(wake / "stdout.jsonl")
                        event = {"obligation_id": held["intent"]["obligation_id"],
                                 "decision_path": str(held["path"]), "decision_sha256": held["sha256"],
                                 "captured": False, "research_point_qualified": False}
                        self.receipt.setdefault("checkpoint_handoffs", []).append(event)
                        try:
                            event.update(self._capture(held, deadline), captured=True)
                        except Exception as error:
                            # A missed optional point cannot discard the original
                            # committed guard or turn a later retry into its capture.
                            event.update(error_kind=type(error).__name__, error=str(error)[:500])
                    continuation = held.get("continuation_body")
                    if continuation is not None:
                        break
                return_code = process.wait(timeout=max(.1, deadline - time.monotonic()) if timeout is not None else None)
            if continuation is None:
                return return_code
            # A fresh-context profile still resumes THIS source session after an
            # interruption. Only the next ordinary Host wake uses its own policy.
            source_session_id = self.receipt["session"]["session_id"]
            execution = replace(execution, context="resume")
            session_wake = BenchmarkSessionWake(self.env, execution, self.receipt)
            if not source_session_id or session_wake.session_id != source_session_id:
                raise ValueError("checkpoint source continuation lost its native session")
            body = continuation
