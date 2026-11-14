"""Prepare and recover reviewed Chat Todo and Monitor creation."""

from __future__ import annotations

from typing import Any, TypedDict

from ..agent_registry import registered_agent_ids_for_goal
from ..chat import apply_prepared_todo_review, plan_reviewed_todo_create, todo_create_was_duplicate
from ..chat_action_store import action_checkpoint_steps
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable, read_canonical_todos_if_promoted,
)
from ..control_plane.todos.mutation_api import add_goal_todo, update_goal_todo
from ..file_lock import try_exclusive_file_lock
from .todo_source import ChatTodoSource, ChatTodoSourceChanged, resolve_chat_todo_source


class ReviewedCreateAuthority(TypedDict):
    provider_revision: str
    store_identity: str
    source_authority: str


class ChatTodoCreationMixin:
    """Own the confirmation checkpoint; canonical storage owns the write."""

    @staticmethod
    def _create_authority(source: ChatTodoSource, goal_id: str) -> ReviewedCreateAuthority:
        authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
        if not authority or not all(isinstance(authority.get(key), str) and authority[key] for key in (
            "provider_revision", "store_identity", "source_authority",
        )):
            raise ValueError("The Goal's Todo authority is unavailable")
        return ReviewedCreateAuthority(provider_revision=authority["provider_revision"],
            store_identity=authority["store_identity"], source_authority=authority["source_authority"])

    @staticmethod
    def _monitor_create_fingerprint(source: ChatTodoSource) -> str:
        from ..chat_actions import _digest

        return _digest({
            "source_identity": source.source_identity,
            "registry_sha256": source.registry_sha256,
        })

    def _save_create_plan(
        self, proposal: dict[str, Any], source: ChatTodoSource, authority: ReviewedCreateAuthority,
        *, fingerprint: str, **metadata: Any,
    ) -> dict[str, Any]:
        proposal_id = proposal["proposal_id"]
        return self.store.save_checkpoint(proposal_id, step="todo_create_prepared", once=True,
            receipt={"operation_id": f"chat-create:{proposal_id}", "preview_id": fingerprint,
                     "source_identity": source.source_identity, "source_authority": authority["source_authority"],
                     "expected_store_identity": authority["store_identity"],
                     "expected_registry_sha256": source.registry_sha256,
                     "expected_provider_revision": authority["provider_revision"], **metadata})

    @staticmethod
    def _prepared_create_arguments(
        proposal: dict[str, Any], source: ChatTodoSource,
    ) -> dict[str, str]:
        plan = action_checkpoint_steps(proposal).get("todo_create_prepared")
        if (not isinstance(plan, dict)
                or plan.get("operation_id") != f"chat-create:{proposal['proposal_id']}"
                or plan.get("preview_id") != proposal["expected_state_fingerprint"]
                or not plan.get("source_identity")):
            raise ValueError("The original confirmation has no complete create plan")
        source.require_review(plan)
        keys = ("operation_id", "expected_provider_revision", "expected_registry_sha256", "expected_store_identity")
        if any(not isinstance(plan.get(key), str) or not plan[key] for key in keys):
            raise ValueError("The original confirmation has no complete create basis")
        return {key: plan[key] for key in keys}

    def _create_no_write(
        self, proposal: dict[str, Any], fingerprint: str,
    ) -> dict[str, Any]:
        stale = self.store.apply(proposal["proposal_id"], current_state_fingerprint=fingerprint, receipt={})
        return {"proposal": stale, "turn": self._turn_from_receipt(stale.get("receipt"))}

    def _create_confirmation_conflict(
        self, proposal: dict[str, Any], error: LocalCoordinationAuthorityUnavailable,
    ) -> dict[str, Any]:
        from ..chat_actions import _digest

        if error.code == "local_authority_provider_identity_mismatch":
            raise ChatTodoSourceChanged("The reviewed Todo store changed. Restore the original store before retrying.") from error
        if error.code not in {"provider_revision_mismatch", "authority_source_changed"}:
            raise error
        # Only the same opened provider, after original receipt lookup, can
        # conclusively reject a write that never landed.
        return self._create_no_write(proposal, _digest({
            "expected": proposal["expected_state_fingerprint"], "conflict": error.code,
            "provider_revision": error.payload.get("current_provider_revision"),
        }))

    def _creation_lock(self, proposal_id: str):
        from ..chat_actions import _opaque

        key = _opaque(proposal_id, field="proposal_id")
        return try_exclusive_file_lock(
            self.store.root / "task-starts" / f"{key}.json", operation="confirm_task_start",
        )

    def _apply_monitor_create(
        self, proposal_id: str, proposal: dict[str, Any], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        from ..chat_actions import _digest, _monitor_metadata, _monitor_text, _opaque

        goal_id = str(parameters["goal_id"])
        source = resolve_chat_todo_source(self, goal_id)
        steps = action_checkpoint_steps(proposal)
        if "todo_create_prepared" not in steps:
            authority = self._create_authority(source, goal_id)
            fingerprint = self._monitor_create_fingerprint(source)
            if fingerprint != proposal["expected_state_fingerprint"]:
                return self._create_no_write(proposal, fingerprint)
        agent_id = str(parameters["agent_id"])
        if agent_id not in registered_agent_ids_for_goal(source.goal):
            raise ValueError("monitor Agent must be registered for the Goal")
        if "todo_create_prepared" not in steps:
            metadata = _monitor_metadata(parameters)
            stop = str(parameters.get("stop_condition") or "").strip()
            resume_when = stop if stop and not (metadata.get("expires_at") or metadata.get("watch_only")) else None
            if not (metadata.get("expires_at") or resume_when or metadata.get("watch_only")):
                metadata["watch_only"] = "true"
            proposal = self._save_create_plan(proposal, source, authority, fingerprint=fingerprint,
                resume_when=resume_when, monitor_metadata=metadata)
        arguments = self._prepared_create_arguments(proposal, source)
        plan = action_checkpoint_steps(proposal)["todo_create_prepared"]
        try:
            result = add_goal_todo(
                **source.writer_scope(), goal_id=goal_id, role="agent",
                text=_monitor_text(parameters) or str(parameters["target"]),
                task_class="continuous_monitor", action_kind="observe",
                claimed_by=agent_id, agent_id=agent_id, resume_when=plan["resume_when"],
                monitor_metadata=plan["monitor_metadata"], dry_run=False, **arguments,
            )
        except LocalCoordinationAuthorityUnavailable as error:
            return self._create_confirmation_conflict(proposal, error)
        todo_id = _opaque(result.get("todo_id"), field="todo_id")
        receipt = {
            "receipt_id": _digest(
                {"proposal_id": proposal_id, "goal_id": goal_id, "todo_id": todo_id}
            )[:32],
            "outcome": "monitor_already_exists" if todo_create_was_duplicate(result) else "monitor_created",
            "projection_verified": True,
            "resource_ids": {"goal_id": goal_id, "todo_id": todo_id, "agent_id": agent_id},
        }
        stored = self.store.apply(proposal_id,
            current_state_fingerprint=proposal["expected_state_fingerprint"], receipt=receipt)
        return {"proposal": stored, "turn": None}

    def _apply_todo_create(
        self, proposal_id: str, proposal: dict[str, Any], parameters: dict[str, Any],
    ) -> dict[str, Any]:
        from ..chat_actions import _opaque

        goal_id = str(parameters["goal_id"])
        source = resolve_chat_todo_source(self, goal_id)
        agent_id = parameters.get("agent_id")
        start_execution = parameters.get("start_execution") is True
        steps = action_checkpoint_steps(proposal)
        current_fingerprint = proposal["expected_state_fingerprint"]
        todo_step = steps.get("todo_created")
        if isinstance(todo_step, dict) and todo_step.get("todo_id"):
            todo_id = _opaque(todo_step.get("todo_id"), field="todo_id")
            canonical_receipt = todo_step.get("canonical_receipt")
            if not isinstance(canonical_receipt, dict):
                raise ValueError("Todo creation checkpoint is malformed")
            binding = todo_step.get("source")
            if not isinstance(binding, dict):
                raise ValueError("The created Task predates source binding. Recover the original Todo before assigning it.")
            source.require_review(binding)
        else:
            if agent_id and agent_id not in registered_agent_ids_for_goal(source.goal):
                raise ValueError("the selected Agent is no longer registered for this Goal")
            if "todo_create_prepared" not in steps:
                authority = self._create_authority(source, goal_id)
                fingerprint, revision = plan_reviewed_todo_create(
                    **source.writer_scope(), goal_id=goal_id, text=str(parameters["text"]),
                    priority=parameters.get("priority"), source_identity=source.source_identity,
                )
                if fingerprint != current_fingerprint:
                    return self._create_no_write(proposal, fingerprint)
                if revision != authority["provider_revision"]:
                    # Never take a preview from one store and prepare it
                    # against an independently observed store or revision.
                    raise ChatTodoSourceChanged("The Todo store changed while confirming. Retry the original preview.")
                proposal = self._save_create_plan(proposal, source, authority, fingerprint=fingerprint)
            arguments = self._prepared_create_arguments(proposal, source)
            try:
                applied = apply_prepared_todo_review(
                    **source.writer_scope(), goal_id=goal_id, text=str(parameters["text"]),
                    priority=parameters.get("priority"), preview_id=current_fingerprint, **arguments,
                )
            except LocalCoordinationAuthorityUnavailable as error:
                return self._create_confirmation_conflict(proposal, error)
            canonical_receipt = applied["receipt"]
            todo_id = _opaque(canonical_receipt["todo_id"], field="todo_id")
            binding = {
                **applied["authority_write"],
                "source_identity": source.source_identity,
                "store_identity": arguments["expected_store_identity"],
                "registry_sha256": arguments["expected_registry_sha256"],
            }
            proposal = self.store.save_checkpoint(proposal_id, step="todo_created", once=True,
                receipt={"todo_id": todo_id, "canonical_receipt": canonical_receipt, "source": binding})
            steps = action_checkpoint_steps(proposal)
        if agent_id and not start_execution and not isinstance(steps.get("todo_assigned"), dict):
            source = resolve_chat_todo_source(self, goal_id)
            source.require_review(binding)
            authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
            source.require_created_todo(binding, authority)
            update_goal_todo(
                **source.writer_scope(),
                goal_id=goal_id,
                todo_id=todo_id,
                claimed_by=str(agent_id),
                agent_id=str(agent_id),
                authority_reason="owner-confirmed typed Chat action",
                update_operation_id=f"chat-assign:{proposal_id}",
                update_expected_provider_revision=binding["provider_revision"],
                update_expected_registry_sha256=binding["registry_sha256"],
                dry_run=False,
            )
            self.store.save_checkpoint(
                proposal_id,
                step="todo_assigned",
                receipt={"todo_id": todo_id, "agent_id": str(agent_id)},
            )
        receipt = {
            "receipt_id": str(canonical_receipt["receipt_id"]),
            "outcome": str(canonical_receipt["outcome"]),
            "projection_verified": True,
            "resource_ids": {
                "goal_id": str(canonical_receipt["goal_id"]),
                "todo_id": todo_id,
                **({"agent_id": str(agent_id)} if agent_id else {}),
            },
            "canonical_receipt": canonical_receipt,
        }
        turn_result = None
        if start_execution:
            source = resolve_chat_todo_source(self, goal_id)
            source.require_review(binding)
            turn_result = self._start_confirmed_task(
                proposal_id, parameters, todo_id, steps, source=source, binding=binding,
            )
            receipt["resource_ids"].update(
                {"session_id": turn_result["session_id"], "turn_id": turn_result["turn_id"]}
            )
            receipt["outcome"] = "task_execution_started"
        stored = self.store.apply(
            proposal_id,
            current_state_fingerprint=current_fingerprint,
            receipt=receipt,
        )
        return {"proposal": stored, "turn": turn_result}
