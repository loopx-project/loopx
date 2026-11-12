"""Reviewed App creates must retain their revision through the actual write."""
import json
from http.client import HTTPConnection
from threading import Thread

import pytest

import loopx.chat as chat
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.mutation_api import add_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("route", ["serving", "registered", "shared-directory", "shared-unbound"])
@pytest.mark.parametrize("kind", ["todo.create", "monitor.create", "todo-review"])
def test_http_create_uses_goal_runtime(tmp_path, monkeypatch, provider, route, kind):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    source_registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    registry = source_registry
    monkeypatch.setattr("loopx.paths.DEFAULT_RUNTIME_ROOT", tmp_path / "ambient-current")
    monkeypatch.setattr("loopx.paths.LEGACY_RUNTIME_ROOT", tmp_path / "ambient-legacy")
    serving_root = runtime
    decoy = None
    if route == "serving":
        registration = json.loads(registry.read_text())
        del registration["common_runtime_root"]
        registry.write_text(json.dumps(registration))
    else:
        (tmp_path / "other").mkdir()
        _, decoy_root, _ = promoted_create_fixture(tmp_path / "other", provider=provider)
        decoy = read_canonical_todos_if_promoted(runtime_root=decoy_root, goal_id="goal-a")
        serving_root = decoy_root if route == "registered" else tmp_path / "chat-storage"
    if route.startswith("shared"):
        registration = json.loads(registry.read_text())
        registration.update(registry_role="global-local", common_runtime_root=str(decoy_root))
        if route == "shared-unbound":
            del registration["common_runtime_root"]
        registration["goals"][0]["source_registry"] = str(source_registry)
        registry = tmp_path / "directory.json"
        registry.write_text(json.dumps(registration))
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    store = ChatActionStore(serving_root / "chat" / "actions")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = registry, str(serving_root), False
    server.action_store = store
    server.action_service = ChatActionService(store=store, registry_path=registry,
        runtime_root_override=str(serving_root))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection(*server.server_address, timeout=30)

    def request(method, path, body=None):
        connection.request(method, path, None if body is None else json.dumps(body),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    text = "Review the project index"
    try:
        if kind == "todo-review":
            intent = {"goal_id": "goal-a", "text": text}
            code, preview = request("POST", "/api/chat/todo/dry-run", intent)
            assert code == 200, preview
            apply_path, confirmation = "/api/chat/todo/apply", {**intent, "preview_id": preview["preview_id"]}
        else:
            parameters = {"goal_id": "goal-a", "agent_id": "agent-a"}
            if kind == "monitor.create":
                parameters.update(target=text, target_key="index-check", cadence="2h",
                                  timezone="UTC", stop_condition="watch_only")
            else:
                parameters.update(text=text, priority="P2")
            code, preview = request("POST", "/api/actions/preview", {
                "action_kind": kind, "summary": text, "normalized_parameters": parameters,
                "context": {"kind": "goal", "goal_id": "goal-a"}, "idempotency_key": "review-index",
            })
            assert code == 201, preview
            action_path = f"/api/actions/{preview['proposal']['proposal_id']}"
            if kind == "todo.create" and route == "shared-directory":
                original = source_registry.read_text()
                changed = json.loads(original)
                changed["goals"][0]["coordination"]["registered_agents"] = ["another-agent"]
                source_registry.write_text(json.dumps(changed))
                refused_code, refused = request("POST", f"{action_path}/apply", {})
                assert refused_code == 400 and "no longer registered" in refused["error"], refused
                assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
                source_registry.write_text(original)
            if kind == "monitor.create" and route == "shared-directory":
                original = source_registry.read_text()
                source_registry.unlink()
                unavailable_code, unavailable = request("POST", f"{action_path}/apply", {})
                assert unavailable_code == 400 and unavailable["error_code"] == "invalid_action_apply", unavailable
                assert str(tmp_path) not in json.dumps(unavailable)
                assert read_canonical_todos_if_promoted(runtime_root=decoy_root, goal_id="goal-a") == decoy
                source_registry.write_text(json.dumps({**json.loads(original),
                    "common_runtime_root": str(decoy_root)}))
                refused_code, refused = request("POST", f"{action_path}/apply", {})
                assert refused_code == 409 and refused["proposal"]["status"] == "stale", refused
                assert read_canonical_todos_if_promoted(runtime_root=decoy_root, goal_id="goal-a") == decoy
                source_registry.write_text(original)
                code, preview = request("POST", f"{action_path}/regenerate", {})
                assert code == 201, preview
                action_path = f"/api/actions/{preview['proposal']['proposal_id']}"
            apply_path, confirmation = f"{action_path}/apply", {}

        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
        assert text not in state.read_text()
        code, applied = request("POST", apply_path, confirmation)
        assert code == 200, applied
        receipt = applied["receipt"] if kind == "todo-review" else applied["proposal"]["receipt"]
        todo_id = receipt["todo_id"] if kind == "todo-review" else receipt["resource_ids"]["todo_id"]
        saved = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
        assert len(saved["todos"]) == 1
        todo = saved["todos"][0]
        assert todo["todo_id"] == todo_id and text in todo["text"]
        assert text in state.read_text()
        if kind != "todo-review":
            assert todo["claimed_by"] == "agent-a"
            if kind == "monitor.create":
                assert (todo["task_class"], todo["cadence"], todo["target_key"]) == (
                    "continuous_monitor", "2h", "index-check")
                assert todo["next_due_at"]
            else:
                assert todo["priority"] == "P2"
            assert request("POST", apply_path, confirmation) == (code, applied)
            assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == saved
        detail_code, detail = request("GET", f"/api/chat/todo/detail?goal_id=goal-a&todo_id={todo_id}")
        assert detail_code == 200, detail
        assert detail["review_basis"]["provider_revision"] == saved["provider_revision"]
        if decoy is not None:
            assert read_canonical_todos_if_promoted(runtime_root=decoy_root, goal_id="goal-a") == decoy
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("timing", ["before-confirmation", "between-preview-reads", "after-final-preview"])
def test_concurrent_create_refuses_reviewed_intent(tmp_path, monkeypatch, provider, timing):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    preview = chat.build_todo_review_preview(
        registry_path=registry, goal_id="goal-a", text="Reviewed request")

    def independent_write():
        add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
            text="Independent accepted request")

    if timing == "before-confirmation":
        independent_write()
    else:
        original = chat._add_review_todo

        def interleaved(**kwargs):
            if (timing == "after-final-preview" and not kwargs["dry_run"]) or (
                timing == "between-preview-reads" and kwargs["dry_run"] and kwargs.get("operation_id")
            ):
                independent_write()
            return original(**kwargs)

        monkeypatch.setattr(chat, "_add_review_todo", interleaved)
    with pytest.raises(chat.TodoReviewPreviewConflict) as conflict:
        chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
            text="Reviewed request", preview_id=preview["preview_id"])
    assert conflict.value.receipt["status"] == "not_applied"
    assert conflict.value.receipt["write_attempted"] is (timing == "after-final-preview")
    assert ("current_preview_id" in conflict.value.receipt) is (timing == "before-confirmation")
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert [todo["text"] for todo in snapshot["todos"]] == ["[P1] Independent accepted request"]
    if timing != "before-confirmation":
        monkeypatch.setattr(chat, "_add_review_todo", original)
    refreshed = chat.build_todo_review_preview(
        registry_path=registry, goal_id="goal-a", text="Reviewed request")
    applied = chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
        text="Reviewed request", preview_id=refreshed["preview_id"])
    assert applied["todo"]["todo_id"] == refreshed["todo"]["todo_id"]
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert {todo["text"] for todo in snapshot["todos"]} == {"[P1] Independent accepted request", "[P1] Reviewed request"}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_review_creates_once_and_duplicate_remains_noop(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    for _ in range(2):
        before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
        preview = chat.build_todo_review_preview(
            registry_path=registry, goal_id="goal-a", text="Reviewed request")
        assert chat.build_todo_review_preview(
            registry_path=registry, goal_id="goal-a", text="Reviewed request") == preview
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
        result = chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
            text="Reviewed request", preview_id=preview["preview_id"])
        assert result["applied"] is True
        assert result["todo"]["todo_id"] == preview["todo"]["todo_id"]
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert [todo["text"] for todo in snapshot["todos"]] == ["[P1] Reviewed request"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_changed_intent_cannot_apply_another_preview(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    preview = chat.build_todo_review_preview(
        registry_path=registry, goal_id="goal-a", text="Reviewed request")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    for changed in ({"text": "Different request"}, {"text": "Reviewed request", "priority": "P0"}):
        with pytest.raises(chat.TodoReviewPreviewConflict):
            chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
                preview_id=preview["preview_id"], **changed)
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
