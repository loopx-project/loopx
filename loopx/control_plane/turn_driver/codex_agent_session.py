"""Agent-scoped Codex conversation IO through existing session/lifetime owners."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
    capture_first_party_host_goal_ref,
)
from loopx.control_plane.turn_driver.codex_cli import codex_cli_event_session_id
from loopx.control_plane.turn_driver.codex_sessions import (
    _store_codex_cli_session,
    codex_session_profile_digest,
    require_codex_session_profile,
    select_codex_cli_session,
)


class CodexAgentSession:
    """Planning and heartbeat use the same Goal/Agent binding as governed Turns.

    The outer controller serializes wakes. A native ID must be observed even on
    timeout; neither missing history nor a failed resume permits a fresh fork.
    """

    def __init__(
        self, env: dict[str, str], *, context: str, sandbox: str, receipt: dict[str, Any],
    ) -> None:
        from .codex_cli import CODEX_CLI_SANDBOXES

        if context not in {"fresh", "resume"}:
            raise ValueError("unsupported Codex session context")
        if sandbox not in CODEX_CLI_SANDBOXES:
            raise ValueError("unsupported Codex session sandbox")
        self.root = Path(env["LOOPX_RUNTIME_ROOT"])
        self.lineage = {
            "goal_id": env["LOOPX_GOAL_ID"],
            "agent_id": env["LOOPX_AGENT_ID"],
        }
        self.goal_ref = capture_first_party_host_goal_ref(
            registry_path=Path(env["LOOPX_REGISTRY"]),
            goal_id=self.lineage["goal_id"],
        )
        self.admission = FirstPartyHostGoalAdmission.for_plan(
            registry_path=Path(env["LOOPX_REGISTRY"]),
            goal_id=self.lineage["goal_id"],
            planned_goal_ref=self.goal_ref,
        )
        if context == "fresh":
            self.admission.require_current()
        binary = shutil.which(env["CODEX_BIN"])
        if binary is None:
            raise ValueError("Codex CLI executable is unavailable")
        self.digest = codex_session_profile_digest(
            project=Path(env["LOOPX_PROJECT"]),
            codex_bin=binary,
            home=Path(env["CODEX_HOME"]),
            model=env["MODEL_NAME"],
            reasoning_effort=env["REASONING_EFFORT"],
            sandbox=sandbox,
        )
        binding = (
            select_codex_cli_session(
                self.root,
                lineage=self.lineage,
                session_scope="agent",
                goal_admission=self.admission,
            )
            if context == "resume"
            else None
        )
        if binding:
            require_codex_session_profile(binding, self.digest)
            if binding.get("operation_transport"):
                raise ValueError("session requires its original managed transport")
        self.session_id = binding["session_id"] if binding else None
        self.binding = binding
        self.checkpoint_refresh = None
        self.receipt = receipt
        receipt["session"] = {
            "binding_scope": "agent",
            "action": "resume" if binding else "start_new",
            "session_id": self.session_id,
        }

    def observe(self, path: Path) -> None:
        if not path.exists():
            return  # No process was launched.
        ids = set()
        with path.open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                candidate = (
                    codex_cli_event_session_id(event)
                    if isinstance(event, dict)
                    else None
                )
                if candidate:
                    ids.add(candidate)
        if len(ids) != 1 or (
            self.session_id is not None and self.session_id not in ids
        ):
            self.receipt.update(ok=False, error_kind="session_identity_unconfirmed")
            raise RuntimeError("Codex did not confirm the expected session identity")
        observed = ids.pop()
        if self.checkpoint_refresh is not None:
            from .codex_sessions import _replace_checkpoint_codex_session

            self.admission.accept_result(lambda: _replace_checkpoint_codex_session(
                self.root, lineage=self.lineage, expected=self.binding, session_id=observed,
                refresh=self.checkpoint_refresh))
            self.session_id = observed
            self.checkpoint_refresh = None
        else:
            self.admission.accept_result(lambda: _store_codex_cli_session(
                self.root,
                lineage=self.lineage,
                session_scope="agent",
                session_id=observed,
                goal_ref=self.goal_ref,
                session_profile_digest=self.digest,
            ))
        self.receipt["session"]["session_id"] = observed

    def prepare_checkpoint_refresh(self, review_basis: dict | None, attempt: Path, stdout: Path,
                                   *, pre_replan_basis: dict | None = None) -> None:
        """Start once from a qualified checkpoint boundary; recover observed results.

        An uncertain launch without a native identity remains unresolved. Never
        dispatch another fresh conversation merely because its reply was lost.
        The outer Host still serializes wakes and owns the explicit opt-in.
        """
        from loopx.file_lock import exclusive_file_lock

        self.admission.require_current()
        if any(not path.is_absolute() or path.resolve() != path for path in (attempt, stdout)):
            raise ValueError("checkpoint context refresh requires physical absolute attempt/output paths")
        if not self.binding or not self.binding.get("checkpoint_artifact_root"):
            raise ValueError("checkpoint context refresh requires a source-bound conversation")
        if (review_basis is None) == (pre_replan_basis is None):
            raise ValueError("checkpoint refresh requires exactly one qualified boundary basis")
        refresh = {"checkpoint_sha256": self.binding["checkpoint_artifact_sha256"],
            "source_session_id": self.binding["session_id"],
            **({"review_basis": review_basis} if review_basis is not None else
               {"pre_replan_basis": pre_replan_basis})}
        basis = {"binding": self.binding, "refresh": refresh}
        with exclusive_file_lock(attempt):
            if attempt.exists():
                prior = json.loads(attempt.read_bytes())
                if prior.get("basis") != basis:
                    raise ValueError("checkpoint context refresh attempt source differs")
                original_output = Path(prior["stdout"])
                if not original_output.is_absolute() or original_output.resolve() != original_output:
                    raise ValueError("checkpoint context refresh original output is redirected")
                self.checkpoint_refresh = refresh
                self.session_id = None
                self.observe(original_output)
                if self.session_id is None:
                    raise ValueError("checkpoint context refresh outcome is unknown; inspect its original native output")
                self.receipt["session"].update(action="resume", session_id=self.session_id)
                return
            attempt.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(attempt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"basis": basis, "stdout": str(stdout)}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        self.checkpoint_refresh = refresh
        self.session_id = None
        self.receipt["session"].update(action="start_new", session_id=None)

    def _bind_verified_fork(self, session_id: str, source_profile_digest: str,
                            checkpoint_artifact_sha256: str, checkpoint_artifact_root: str) -> None:
        """Bind a controller-verified native fork through the existing session owner.

        This admits the conversation binding only. It does not claim work,
        restore research authority or start a model. Unknown result delivery
        requires readback; it cannot replace an existing binding.
        """
        if self.session_id is not None:
            raise ValueError("checkpoint session adoption requires an absent destination binding")
        require_codex_session_profile({"session_profile_digest": source_profile_digest}, self.digest)
        self.admission.accept_result(
            lambda: _store_codex_cli_session(
                self.root,
                lineage=self.lineage,
                session_scope="agent",
                session_id=session_id,
                goal_ref=self.goal_ref,
                session_profile_digest=self.digest,
                require_absent=True,
                checkpoint_artifact_sha256=checkpoint_artifact_sha256,
                checkpoint_artifact_root=checkpoint_artifact_root,
            )
        )
        self.session_id = session_id
        self.receipt["session"].update(action="resume", session_id=session_id)
