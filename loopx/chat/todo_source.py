"""Route Chat's Todo reads and reviewed mutations to the same Goal source."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..chat import _stable_digest, apply_todo_review_preview, build_todo_review_preview
from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..history import load_registry
from ..registry import find_registry_goal
from .status_api import goal_read_runtime_root


class ChatTodoSourceChanged(ValueError):
    """The current route cannot recover a reviewed operation's historical receipt."""


@dataclass(frozen=True)
class ChatTodoSource:
    registry_path: Path
    runtime_root: Path
    source_identity: str
    registry_sha256: str
    goal: dict[str, Any]

    def writer_scope(self) -> dict[str, Any]:
        return {"registry_path": self.registry_path, "runtime_root_arg": str(self.runtime_root)}

    def require_review(self, basis: dict[str, Any]) -> None:
        identity = basis.get("source_identity")
        if identity is None:
            # Older reviewed actions carry an exact provider revision and
            # registry digest. The same canonical transaction still verifies
            # both before it can authorize a new mutation.
            return
        if identity != self.source_identity:
            raise ChatTodoSourceChanged("The Task's Goal source changed. Restore its original source before retrying.")

    def require_created_todo(self, binding: dict[str, Any], authority: dict[str, Any]) -> None:
        self.require_review(binding)
        if (binding.get("source_authority") != authority["source_authority"]
                or binding.get("store_identity") != authority["store_identity"]):
            raise ChatTodoSourceChanged("The created Task's Todo store changed. Restore its original store before retrying.")


def resolve_chat_todo_source(host: Any, goal_id: str) -> ChatTodoSource:
    invoked = host.registry_path.expanduser().resolve()
    snapshots: dict[Path, bytes | None] = {}
    directory = load_registry(invoked, source_snapshots=snapshots)
    if find_registry_goal(directory, goal_id) is None:
        raise ValueError("goal_id was not found in the active LoopX registry")
    route = resolve_goal_source_runtime_route(
        registry_path=invoked, goal_id=goal_id, registry=directory, source_snapshots=snapshots,
        runtime_root_override=goal_read_runtime_root(host, registry=directory),
    )
    source = Path(route["source_registry"])
    registry = load_registry(source, source_snapshots=snapshots)
    goal = find_registry_goal(registry, goal_id)
    if goal is None:
        raise ValueError("The Goal is unavailable in its source registry")
    for path in {invoked, source}:
        if path.read_bytes() != snapshots[path]:
            raise ValueError("Todo authority registration changed while reading; retry")
    runtime = Path(route["source_runtime_root"])
    return ChatTodoSource(
        source, runtime, _stable_digest({"registry": str(source), "runtime": str(runtime)}, length=64),
        sha256(snapshots[source] or b"").hexdigest(), goal,
    )


def review_todo_create(
    host: Any, *, goal_id: str, text: str, preview_id: str | None,
) -> dict[str, Any]:
    source = resolve_chat_todo_source(host, goal_id)
    if preview_id is None:
        return build_todo_review_preview(
            **source.writer_scope(), goal_id=goal_id, text=text, source_identity=source.source_identity,
        )
    return apply_todo_review_preview(
        **source.writer_scope(), goal_id=goal_id, text=text, preview_id=preview_id,
        source_identity=source.source_identity, expected_registry_sha256=source.registry_sha256,
    )
