"""Loopback operator transport for the existing acceptance owner.

TypeScript retains configuration, evidence and CAS decisions. This adapter only
parses the local HTTP request and invokes the same host IO as the CLI.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse


class _AcceptanceServer(Protocol):
    registry_path: Path
    runtime_root: Path


class GoalAcceptanceRequestMixin:
    server: _AcceptanceServer
    path: str

    def _read_json(self) -> dict[str, Any]:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, *, status: int, error_code: str) -> None:
        raise NotImplementedError

    def _goal_acceptance_inspect(self) -> None:
        from .acceptance import inspect_goal_acceptance, public_goal_acceptance, goal_acceptance_route

        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if (
                set(query) != {"goal_id"}
                or len(query["goal_id"]) != 1
                or not query["goal_id"][0].strip()
            ):
                raise ValueError("one goal_id is required")
            result = public_goal_acceptance(
                inspect_goal_acceptance(
                    **goal_acceptance_route(self.server.registry_path, query["goal_id"][0],
                                            str(self.server.runtime_root)),
                )
            )
            self._send_json({**result, "goal_id": query["goal_id"][0]})
        except (OSError, TypeError, ValueError, RuntimeError):
            self._send_error(
                "Acceptance is unavailable for this Goal; reload after its local authority is available.",
                status=409,
                error_code="goal_acceptance_unavailable",
            )

    def _goal_acceptance_update(self) -> None:
        from .acceptance import (
            configure_goal_acceptance,
            handoff_goal_outcomes,
            refine_goal_outcome,
            public_goal_acceptance,
            verify_goal_acceptance, goal_acceptance_route,
        )
        from .planning_batch import DirectPlanningBatchBackend, apply_planning_batch

        try:
            body = self._read_json()
            if set(body) - {
                "goal_id",
                "action",
                "agent_id",
                "document",
                "refinement",
                "expected_provider_revision",
                "operation_id",
            }:
                raise ValueError("unknown acceptance request fields")
            goal_id, action = body.get("goal_id"), body.get("action")
            if action == "disable":  # Retained client spelling; no feature switch.
                action = "clear-checks"
            revision = body.get("expected_provider_revision")
            if (
                not isinstance(goal_id, str)
                or not goal_id.strip()
                or not isinstance(revision, str)
                or not revision
            ):
                raise ValueError("Goal and current provider revision are required")
            common: dict[str, Any] = {
                **goal_acceptance_route(self.server.registry_path, goal_id,
                                        str(self.server.runtime_root)),
                "execute": True,
            }
            if action in {"preview-plan-batch", "plan-batch"}:
                allowed = {"goal_id", "action", "agent_id", "document", "expected_provider_revision", "operation_id"}
                if set(body) - allowed or not {"goal_id", "action", "agent_id", "document", "expected_provider_revision"} <= set(body):
                    raise ValueError("plan-batch requires goal_id, agent_id, document and expected_provider_revision")
                agent_id = body.get("agent_id")
                document = body.get("document")
                operation_id = body.get("operation_id")
                if (
                    not isinstance(agent_id, str)
                    or not agent_id.strip()
                    or not isinstance(document, dict)
                    or (operation_id is not None and (not isinstance(operation_id, str) or not operation_id.strip()))
                ):
                    raise ValueError("plan-batch requires a registered agent_id and proposal document")
                if "expected_provider_revision" in document and document["expected_provider_revision"] != revision:
                    raise ValueError("plan-batch expected_provider_revision mismatch")
                proposal = {**document, "expected_provider_revision": revision}
                route = goal_acceptance_route(self.server.registry_path, goal_id, str(self.server.runtime_root))
                batch = apply_planning_batch(
                    proposal=proposal,
                    backend=DirectPlanningBatchBackend(
                        registry_path=route["registry_path"],
                        runtime_root_arg=route["runtime_root"],
                        goal_id=goal_id,
                        agent_id=agent_id,
                    ),
                    execute=action == "plan-batch",
                    operation_id=operation_id,
                )
                result = {
                    **public_goal_acceptance(batch),
                    "agent_id": batch["agent_id"],
                    "outcome_id": batch["outcome_id"],
                    **(
                        {"todo_ids": batch["todo_ids"], "state_readback_verified": batch["state_readback_verified"]}
                        if "todo_ids" in batch
                        else {"planned_todo_count": batch["planned_todo_count"]}
                    ),
                }
            elif action in {"preview-handoff", "handoff"}:
                if set(body) != {"goal_id", "action", "document", "expected_provider_revision", "operation_id"}:
                    raise ValueError("unexpected handoff fields")
                if not isinstance(body["document"], dict) or not isinstance(body["operation_id"], str) or not body["operation_id"]:
                    raise ValueError("handoff requires a selection document and stable operation id")
                common["execute"] = action == "handoff"
                result = public_goal_acceptance(handoff_goal_outcomes(**common, document=body["document"],
                    operation_id=body["operation_id"], expected_provider_revision=revision))
            elif action == "refine":
                if set(body) != {"goal_id", "action", "refinement", "expected_provider_revision", "operation_id"}:
                    raise ValueError("refine requires an exact outcome request")
                if not isinstance(body["refinement"], dict) or not isinstance(body["operation_id"], str) or not body["operation_id"]:
                    raise ValueError("refine requires a document and stable operation id")
                result = public_goal_acceptance(refine_goal_outcome(**common, refinement=body["refinement"],
                    operation_id=body["operation_id"], expected_provider_revision=revision))
            elif action == "verify":
                if set(body) - {"goal_id", "action", "expected_provider_revision"}:
                    raise ValueError("check does not accept declarations or results")
                result = verify_goal_acceptance(
                    **common, expected_provider_revision=revision
                )
            elif action in {"configure", "clear-checks"}:
                if set(body) - {"goal_id", "action", "document", "expected_provider_revision", "operation_id"}:
                    raise ValueError("unexpected configure fields")
                document = body.get("document")
                if (
                    action == "configure"
                    and not isinstance(document, dict)
                    or action == "clear-checks"
                    and document is not None
                ):
                    raise ValueError(
                        "configuration requires a document; clear-checks requires no document"
                    )
                operation_id = body.get("operation_id")
                if not isinstance(operation_id, str) or not operation_id:
                    raise ValueError(
                        "configuration requires a stable operation identity"
                    )
                result = public_goal_acceptance(
                    configure_goal_acceptance(
                        **common,
                        document=document,
                        expected_provider_revision=revision,
                        operation_id=operation_id,
                    )
                )
            else:
                raise ValueError("unknown acceptance operation")
            self._send_json({**result, "goal_id": goal_id})
        except (OSError, TypeError, ValueError, RuntimeError):
            # An uncertain check is never automatically replayed. The caller
            # can inspect the current receipt before explicitly running again.
            self._send_error(
                "Acceptance could not be applied or checked. Read back the current contract and receipt before retrying.",
                status=409,
                error_code="goal_acceptance_operation_failed",
            )
