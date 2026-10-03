"""Trial-local conversation continuity through the Codex session owner."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .codex import Execution

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


class BenchmarkSessionWake:
    """Planning and heartbeat use the same Goal/Agent binding as governed Turns.

    The outer controller serializes wakes. A native ID must be observed even on
    timeout; neither missing history nor a failed resume permits a fresh fork.
    """

    def __init__(
        self, env: dict[str, str], execution: Execution, receipt: dict[str, Any],
    ) -> None:
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
        if execution.context == "fresh":
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
            sandbox=execution.sandbox,
        )
        binding = (
            select_codex_cli_session(
                self.root,
                lineage=self.lineage,
                session_scope="agent",
                goal_admission=self.admission,
            )
            if execution.context == "resume"
            else None
        )
        if binding:
            require_codex_session_profile(binding, self.digest)
            if binding.get("operation_transport"):
                raise ValueError("session requires its original managed transport")
        self.session_id = binding["session_id"] if binding else None
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
        self.admission.accept_result(
            lambda: _store_codex_cli_session(
                self.root,
                lineage=self.lineage,
                session_scope="agent",
                session_id=observed,
                goal_ref=self.goal_ref,
                session_profile_digest=self.digest,
            )
        )
        self.receipt["session"]["session_id"] = observed
