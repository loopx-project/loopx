"""Goal-scoped event append boundary; legacy codecs remain in the event store.

The outer Todo/state locks serialize registry admission, Markdown projection and
promotion. All event candidates are then locked in canonical path order. A
standalone store is still an unmanaged JSONL writer, like directly editing a
Markdown source; it cannot certify a managed Goal transaction.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_registry
from ...registry import find_registry_goal
from ..coordination import local_authority_shadow_outbox as outbox
from ..coordination.legacy_writer_fence import legacy_todo_write_transaction, require_registry_source_write_allowed
from ..coordination.shadow_management import (
    ShadowManagementError, read_shadow_bootstrap_source_snapshot, require_shadow_primary_write_allowed,
)


@dataclass
class StateEventWriteContext:
    registry_path: Path
    runtime_root: Path
    goal_id: str
    state_path: Path
    primary_lock_held: bool = False
    capture: outbox.TodoPartitionCapture | None = field(default=None, init=False)
    _goal: dict[str, Any] = field(default_factory=dict, init=False)
    _texts: dict[Path, str | None] = field(default_factory=dict, init=False)
    _managed_source: bool = field(default=False, init=False)

    @contextmanager
    def transaction(self, path: Path) -> Iterator[None]:
        from ..status.active_state_projection import state_event_log_candidates
        self._goal = find_registry_goal(load_registry(self.registry_path), self.goal_id)
        candidates = list(dict.fromkeys(p.resolve() for p in state_event_log_candidates(self._goal, state_path=self.state_path)))
        if path.resolve() not in candidates:
            self.capture = None
            self._managed_source = False
            with exclusive_cross_runtime_file_lock(path, operation="state_event_append"):
                yield
            return
        outer = nullcontext() if self.primary_lock_held else legacy_todo_write_transaction(
            self.registry_path, self.goal_id, self.state_path, None,
            "state_event_append", False, runtime_root=self.runtime_root,
        )
        self.capture = None
        with outer, ExitStack() as stack:
            require_registry_source_write_allowed(registry_path=self.registry_path,
                runtime_root=self.runtime_root, goal_id=self.goal_id, state_file=self.state_path)
            binding = require_shadow_primary_write_allowed(self.runtime_root, self.goal_id)
            self._goal = find_registry_goal(load_registry(self.registry_path), self.goal_id)
            candidates = list(dict.fromkeys(p.resolve() for p in state_event_log_candidates(self._goal, state_path=self.state_path)))
            if self.state_path.resolve() in candidates:
                raise ShadowManagementError("event_source_binding_invalid")
            self._managed_source = path.resolve() in candidates
            if not self._managed_source:
                raise ShadowManagementError("event_source_binding_changed")
            if binding is not None and self._managed_source:
                snapshot = read_shadow_bootstrap_source_snapshot(self.runtime_root, self.goal_id, binding)
                if snapshot.get("event_log_paths") != [str(p) for p in candidates]:
                    raise ShadowManagementError("event_source_rebootstrap_required")
            for candidate in sorted(set([*candidates, path.resolve()])):
                stack.enter_context(exclusive_cross_runtime_file_lock(candidate, operation="state_event_append"))
            if binding is not None:
                try:
                    outbox.require_source_recovered(outbox.partition_directory(self.runtime_root, self.goal_id, "todos"))
                except outbox.OutboxError as error:
                    message = (f"drain the unresolved source transaction with `loopx authority-shadow drain --goal-id {self.goal_id}` before retrying"
                        if error.reason_code == "source_recovery_required" else str(error))
                    raise ShadowManagementError(error.reason_code, message) from error
            self._texts = {candidate: candidate.read_bytes().decode("utf-8") if candidate.exists() else None for candidate in candidates}
            yield

    def prepare(self, path: Path, original: str, planned: str) -> None:
        if not self._managed_source:
            return  # Independent supervisor logs do not own the Todo partition.
        from ...rollout_event_log import load_rollout_events, rollout_event_log_path
        from ..todos.todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL
        from ..todos.active_state_todo_parser import parse_active_state_todos
        from ..coordination.local_authority_shadow_adapter import todo_partition_projector
        from ..coordination.runtime_shadow_writer_adapter import require_runtime_shadow_capture_prepared
        from .active_state_event_projection import active_state_event_projection_fields
        from .path_resolution import resolve_goal_local_path

        binding = require_shadow_primary_write_allowed(self.runtime_root, self.goal_id)
        if binding is None:
            return
        state_text = self.state_path.read_text(encoding="utf-8")
        rollout = load_rollout_events(rollout_event_log_path(self.runtime_root, self.goal_id),
            limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL)

        def project(text: str) -> dict[str, Any]:
            texts = {**self._texts, path.resolve(): text}
            fields = active_state_event_projection_fields(self._goal, state_path=self.state_path,
                resolve_goal_local_path=resolve_goal_local_path, parse_active_state_todos=parse_active_state_todos,
                item_limit=None, rollout_events=rollout, event_log_texts=texts)
            if fields.get("state_event_projection_warning"):
                raise ShadowManagementError("event_source_invalid")
            return todo_partition_projector(self._goal, state_path=self.state_path,
                rollout_events=rollout, event_fields=fields)(state_text)

        self.capture = outbox.TodoPartitionCapture.begin(enabled=True, runtime_root=self.runtime_root,
            goal_id=self.goal_id, state_path=self.state_path, write_class="state_event_append",
            original_text=original, projector=project)
        self.capture.prepare(planned, event_log_path=path,
            previous_exists=self._texts[path.resolve()] is not None)
        require_runtime_shadow_capture_prepared(self.capture, runtime_root=self.runtime_root, goal_id=self.goal_id)

    def committed(self) -> None:
        if self.capture is not None:
            self.capture.committed()
