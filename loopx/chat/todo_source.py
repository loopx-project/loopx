"""Route Chat's Todo reads and reviewed mutations to the same Goal source."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..chat import CHAT_TODO_RECEIPT_SCHEMA_VERSION, _stable_digest, apply_todo_review_preview, build_todo_review_preview
from ..control_plane.effect_runtime import effect_runtime_result
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
    routing_registry_witness: dict[str, str] | None = None

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

    def _created_todo_operation(self, operation_id: str, todo_id: str, source_authority: str) -> dict[str, Any]:
        goal_id = str(self.goal["id"])
        result = effect_runtime_result("coordination.local_authority.operation_receipt", {
            "schema_version": "loopx_local_coordination_operation_receipt_request_v0",
            "runtime_root": str(self.runtime_root),
            "goal_id": goal_id,
            "operation_id": operation_id,
        }, timeout=15.0)
        receipts = result.get("receipts", []) if isinstance(result, dict) and result.get("status") == "found" else []
        if (not isinstance(receipts, list) or len(receipts) != 1 or not isinstance(receipts[0], dict)
                or result.get("source_authority") != source_authority
                or not isinstance(result.get("provider_revision"), str)):
            raise ChatTodoSourceChanged("The original Task creation receipt is unavailable. Inspect the original source before retrying.")
        created = receipts[0]
        if (created.get("schema_version") != "loopx_coordination_todo_create_receipt_v0"
                or created.get("goal_id") != goal_id or created.get("todo_id") != todo_id
                or created.get("operation_id") != operation_id or not isinstance(created.get("todo"), dict)):
            raise ChatTodoSourceChanged("The original Task creation receipt does not identify this Todo.")
        return result

    def recover_legacy_creation(
        self, *, todo_id: str, preview_id: str, receipt: dict[str, Any], authority: dict[str, Any],
        assignment_completed: bool,
    ) -> dict[str, Any]:
        goal_id = str(self.goal["id"])
        if (not preview_id or receipt.get("schema_version") != CHAT_TODO_RECEIPT_SCHEMA_VERSION
                or receipt.get("preview_id") != preview_id or receipt.get("goal_id") != goal_id
                or receipt.get("todo_id") != todo_id or receipt.get("status") != "applied"
                or receipt.get("receipt_id") != _stable_digest({k: v for k, v in receipt.items() if k != "receipt_id"})):
            raise ValueError("Todo creation checkpoint is malformed")
        preview_revision = receipt.get("preview_revision")
        if (receipt.get("outcome") != "todo_added" or not isinstance(preview_revision, str) or not preview_revision
                or not authority or not authority.get("store_identity") or not authority.get("source_authority")):
            raise ChatTodoSourceChanged("The original Task creation authority is unavailable.")
        operation_id = f"chat-todo:{preview_id}"
        result = self._created_todo_operation(operation_id, todo_id, authority["source_authority"])
        cursor = result.get("cursor")
        if not isinstance(cursor, str) or not cursor.isdecimal() or int(cursor) < 2:
            raise ChatTodoSourceChanged("The original Task creation revision is unavailable.")

        # The preview revision must be the predecessor of this operation in
        # the same verified store, even if a replacement reused the Todo id.
        original = effect_runtime_result("coordination.local_authority.todo_source", {
            "schema_version": "loopx_local_coordination_todo_source_request_v0",
            "runtime_root": str(self.runtime_root),
            "goal_id": goal_id,
            "source": {
                "source_authority": authority["source_authority"],
                "store_identity": authority["store_identity"],
                "provider_revision": preview_revision,
                "cursor": str(int(cursor) - 1),
            },
        }, timeout=15.0)
        if not isinstance(original, dict) or original.get("status") != "loaded":
            raise ChatTodoSourceChanged("The original Task store could not be verified.")
        # Assignment changes the Todo snapshot after creation. A completed
        # assignment only needs receipt recovery. An unfinished one still
        # requires the current Todo to match the originally reviewed write.
        if not assignment_completed:
            todo = next((row for row in authority["todos"] if row["todo_id"] == todo_id), None)
            if result["receipts"][0]["todo"] != todo:
                raise ChatTodoSourceChanged("The original Task could not be verified.")
        return {
            "operation_id": operation_id,
            "provider_revision": result["provider_revision"],
            "source_authority": authority["source_authority"],
            "source_identity": self.source_identity,
            "store_identity": authority["store_identity"],
            "registry_sha256": self.registry_sha256,
        }

    def current_assignment_revision(self, binding: dict[str, Any], authority: dict[str, Any], todo_id: str) -> str:
        operation_id = binding.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise ChatTodoSourceChanged("The original Task creation operation is unavailable.")
        result = self._created_todo_operation(operation_id, todo_id, binding["source_authority"])
        todo = next((row for row in authority["todos"] if row["todo_id"] == todo_id), None)
        if result["provider_revision"] != binding["provider_revision"] or result["receipts"][0]["todo"] != todo:
            raise ChatTodoSourceChanged("The created Task no longer matches its original receipt. Review it before assigning.")
        return str(authority["provider_revision"])


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
        routing_registry_witness=(
            {"path": str(invoked), "sha256": sha256(snapshots[invoked] or b"").hexdigest()}
            if invoked != source else None
        ),
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
        routing_registry_witness=source.routing_registry_witness,
    )
