"""Report bytes live here; canonical User Todos alone own human review state.

The explicit producer freezes one normalized report and asks for review. Reading
it never acknowledges, accepts, executes a model, or advances a delivery cursor.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...chat_store import ChatSessionStore

from ...control_plane.content_digest import BARE_SHA256_PATTERN
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.todos.list_readback import list_goal_todos
from ...control_plane.todos.mutation_api import add_goal_todo
from ...registry import atomic_write_json
from .bindings import build_periodic_report_generation_bundle

MAX_BUNDLE_BYTES = 2 * 1024 * 1024


def _encoded(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _path(root: Path, goal_id: str, digest: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", goal_id) or not BARE_SHA256_PATTERN.fullmatch(digest):
        raise ValueError("Invalid report review identity")
    return root / "goals" / goal_id / "periodic_reports" / "review-bundles" / (digest + ".json")


def _validate(bundle: dict[str, Any], *, new: bool = False) -> dict[str, Any]:
    checked = build_periodic_report_generation_bundle(document=bundle["document"], artifacts=bundle["artifacts"])
    if checked != bundle or len(_encoded(bundle)) > MAX_BUNDLE_BYTES:
        raise ValueError("Report bundle is invalid or exceeds its reading bound")
    if new:
        # Only LoopX's escaped report renderers enter this reader. Arbitrary
        # supplied HTML cannot impersonate a built-in artifact by hashing it.
        from ...presentation.renderers.periodic_report_html import render_periodic_report_html
        from ...presentation.renderers.periodic_report_markdown import render_periodic_report_markdown
        canonical = build_periodic_report_generation_bundle(document=bundle["document"], artifacts=[
            render_periodic_report_markdown(bundle["document"]), render_periodic_report_html(bundle["document"])])
        if canonical != bundle:
            raise ValueError("Review requires matching built-in Markdown and HTML artifacts")
    return checked


def _review_facts(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = ("goal_id", "todo_id", "role", "task_class", "action_kind", "capability_binding_ref", "status",
              "created_by", "bound_agent", "created_at", "updated_at", "completed_at", "superseded_by")
    return [{**{key: todo.get(key) for key in fields},
             "text": str(todo.get("title") or todo.get("text") or "")[:200]} for todo in todos]


def request_report_review(*, registry_path: Path, runtime_root: Path, goal_id: str,
                          agent_id: str, bundle: dict[str, Any], execute: bool = False,
                          session_id: str | None = None, session_store: ChatSessionStore | None = None) -> dict[str, Any]:
    from ...agent_registry import registered_agent_ids_from_registry
    if agent_id not in registered_agent_ids_from_registry(registry_path, goal_id):
        raise ValueError("Report producer must be registered for the original Goal")
    from ...control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    source = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
    if session_id is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise ValueError("Select one original managed report Session")
        from ...chat_store import ChatSessionStore
        from ..native_chat.steward_managed_mode import registered_managed_session_identity
        session = (session_store or ChatSessionStore(runtime_root, startup_maintenance=False)).load_session(session_id)
        if not session or session.get("goal_id") != goal_id or session.get("managed_agent_id") != agent_id:
            raise ValueError("Report feedback must return to its original Goal and Agent Session")
        registered_managed_session_identity(registry_path=registry_path, goal_id=goal_id, agent_id=agent_id, session=session)
    bundle = _validate(bundle)
    material = {"bundle": bundle, "source_session_id": session_id}
    if len(_encoded(material)) > MAX_BUNDLE_BYTES:
        raise ValueError("Report material exceeds its reading bound")
    digest = hashlib.sha256(_encoded(material)).hexdigest()
    plan = effect_runtime_result("presentation.report_review.plan", {
        "goal_id": goal_id, "agent_id": agent_id, "bundle_sha256": digest, "title": bundle["document"]["title"],
        "todos": _review_facts(source["todos"])}, large_local_snapshot=True)
    # A canonical binding already owns these exact bytes. Recovery can restore
    # them after an interrupted write without reinterpreting an older renderer
    # or reopening a human decision. First requests require current renderers.
    if not plan.get("existing_todo_id"):
        _validate(bundle, new=True)
    if execute:
        path = _path(runtime_root, goal_id, digest)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write_json(path, material)
    # The normal writer verifies the registered producer, ownership and current
    # Goal. Its stable operation recovers an unknown create without reopening
    # an already accepted review. A new edition has a different binding.
    result = {"ok": True, "todo_id": plan["existing_todo_id"]} if plan.get("existing_todo_id") else add_goal_todo(registry_path=registry_path, runtime_root_arg=str(runtime_root),
        goal_id=goal_id, agent_id=agent_id, role=plan["role"], text=plan["text"], priority=plan["priority"],
        task_class=plan["task_class"], action_kind=plan["action_kind"],
        capability_binding_ref=plan["capability_binding_ref"], bound_agent=plan["bound_agent"],
        operation_id=plan["operation_id"], dry_run=not execute)
    if result.get("ok") is not True:
        raise ValueError("The canonical report review request was not accepted")
    return {"ok": True, "goal_id": goal_id, "agent_id": agent_id,
        "todo_id": result.get("todo_id"), "bundle_sha256": digest,
        "generation_id": bundle["generation_receipt"]["generation_id"], "dry_run": not execute,
        "review_decision_recorded": False, "model_invoked": False, "external_writes_performed": False}


def report_review_inventory(*, registry_path: Path, runtime_root: Path, goal_id: str) -> list[dict[str, Any]]:
    from ...control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    source = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
    return effect_runtime_result("presentation.report_review.project", {"goal_id": goal_id, "todos": _review_facts(source["todos"])},
                                 large_local_snapshot=True)["items"]


def read_report_review(*, registry_path: Path, runtime_root: Path, goal_id: str, todo_id: str) -> dict[str, Any]:
    source = list_goal_todos(registry_path=registry_path, runtime_root_arg=str(runtime_root), goal_id=goal_id, todo_id=todo_id)
    todo = source.get("todo")
    rows = effect_runtime_result("presentation.report_review.project", {"goal_id": goal_id, "todos": [todo]})["items"]
    if len(rows) != 1:
        raise ValueError("This Todo does not own a report review")
    row = rows[0]
    path = _path(runtime_root, goal_id, row["bundle_sha256"])
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)), "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Report bundle must be a regular file")
        data = handle.read(MAX_BUNDLE_BYTES + 1)
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("Report bundle exceeds its reading bound")
    material = json.loads(data)
    if hashlib.sha256(_encoded(material)).hexdigest() != row["bundle_sha256"]:
        raise ValueError("Report edition changed; original review cannot be reused")
    bundle = _validate(material["bundle"])
    session_id = material["source_session_id"]
    if session_id is not None and not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise ValueError("Invalid original report Session")
    return {"ok": True, "goal_id": goal_id, "review": row, "todo": todo,
        "source_session_id": session_id,
        "document": bundle["document"], "artifacts": bundle["artifacts"],
        "generation_id": bundle["generation_receipt"]["generation_id"]}
