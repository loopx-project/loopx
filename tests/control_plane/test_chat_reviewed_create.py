"""Reviewed App creates must retain their revision through the actual write."""
import json
from http.client import HTTPConnection
from threading import Thread

import pytest

import loopx.chat as chat
from canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime, promoted_create_fixture,
)
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["same", "directory", "runtime", "registration", "store"])
def test_interrupted_assignment_keeps_the_created_todo_source(tmp_path, monkeypatch, provider, change):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    goal_id = "goal-a"
    original_registry, original_root, _ = promoted_create_fixture(tmp_path / "original", provider=provider)
    original_registration = original_registry.read_text()
    replacement_root = tmp_path / "replacement" / "runtime"
    replacement_state = replacement_root.parent / "state.md"
    replacement_state.parent.mkdir()
    replacement_state.write_text("# Other Goal\n\n## Agent Todo\n\n## Completed Work Archive\n")
    replacement_registry = replacement_root.parent / "registry.json"
    replacement_doc = json.loads(original_registration)
    replacement_doc["common_runtime_root"] = str(replacement_root)
    replacement_doc["goals"][0].update(repo=str(replacement_state.parent), state_file=replacement_state.name)
    replacement_registry.write_text(json.dumps(replacement_doc))
    directory = tmp_path / "directory.json"
    directory_doc = json.loads(replacement_registry.read_text())
    directory_doc["registry_role"] = "global-local"
    directory_doc["goals"][0]["source_registry"] = str(original_registry)
    original_directory = json.dumps(directory_doc)
    directory.write_text(original_directory)
    store = ChatActionStore(tmp_path / "chat" / "actions")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = directory, str(tmp_path / "chat"), False
    server.action_store = store
    server.action_service = ChatActionService(store=store, registry_path=directory)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection(*server.server_address, timeout=40)
    checkpoint = store.save_checkpoint

    def write_checkpoint(proposal_id, *, step, receipt, **kwargs):
        saved = checkpoint(proposal_id, step=step, receipt=receipt, **kwargs)
        if step == "todo_created":
            raise OSError("Interrupted after creating the Todo and before assigning it")
        return saved

    def post(path, body):
        connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def read(runtime):
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal_id)

    try:
        code, preview = post("/api/actions/preview", {
            "action_kind": "todo.create",
            "summary": "Prepare the original delivery",
            "normalized_parameters": {
                "goal_id": goal_id, "agent_id": "agent-a", "text": "Prepare the original delivery",
            },
            "context": {"kind": "goal", "goal_id": goal_id}, "idempotency_key": "reviewed-original",
        })
        assert code == 201, preview
        proposal_id = preview["proposal"]["proposal_id"]
        path = f"/api/actions/{proposal_id}/apply"
        with monkeypatch.context() as fault:
            fault.setattr(store, "save_checkpoint", write_checkpoint)
            code, interrupted = post(path, {})
        assert code == 424 and interrupted["error_code"] == "canonical_action_failed", interrupted
        created = store.load(proposal_id)["checkpoint"]["steps"]["todo_created"]
        todo_id = created["todo_id"]
        original = read(original_root)
        assert original["todos"][0]["todo_id"] == todo_id
        assert original["todos"][0].get("claimed_by") is None

        # Same public ids, independently initialized authority and different content.
        replacement_record = {**original["todos"][0], "text": "[P1] Unrelated replacement work",
                              "title": "Unrelated replacement work"}
        initialize_canonical_authority(replacement_root, goal_id,
            build_todo_runtime_shadow_projection(goal_id=goal_id, todos=[replacement_record],
                                                 handoff_mode="soft_claim"),
            state_path=replacement_state, provider=provider)
        replacement = read(replacement_root)
        saved_root = original_root.with_name("saved-runtime")
        if change == "directory":
            directory_doc["goals"][0]["source_registry"] = str(replacement_registry)
            directory.write_text(json.dumps(directory_doc))
        elif change == "runtime":
            changed = json.loads(original_registration)
            changed["common_runtime_root"] = str(replacement_root)
            original_registry.write_text(json.dumps(changed))
        elif change == "registration":
            revoked = json.loads(original_registration)
            revoked["goals"][0]["coordination"]["registered_agents"] = ["another-agent"]
            original_registry.write_text(json.dumps(revoked))
        elif change == "store":
            original_root.rename(saved_root)
            replacement_root.rename(original_root)
        if change != "same":
            code, denied = post(path, {})
            assert code == 400 and "receipt" not in denied, denied
            if change == "store":
                assert read(original_root) == replacement
                original_root.rename(replacement_root)
                saved_root.rename(original_root)
            assert read(original_root) == original and read(replacement_root) == replacement
            saved_steps = store.load(proposal_id)["checkpoint"]["steps"]
            assert set(saved_steps) == {"todo_create_prepared", "todo_created"}
            assert saved_steps["todo_created"] == created
            directory.write_text(original_directory)
            original_registry.write_text(original_registration)
        code, applied = post(path, {})
        assert code == 200 and applied["proposal"]["status"] == "applied", applied
        assert applied["proposal"]["receipt"]["canonical_receipt"] == created["canonical_receipt"]
        final = read(original_root)
        assert len(final["todos"]) == 1 and final["todos"][0]["claimed_by"] == "agent-a"
        assert int(final["cursor"]) == int(original["cursor"]) + 1
        assert post(path, {}) == (code, applied)
        assert read(original_root) == final and read(replacement_root) == replacement
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("operation", ["edit", "complete", "monitor.stop", "gate.resolve"])
def test_existing_chat_actions_and_detail_follow_the_same_goal_source(tmp_path, monkeypatch, provider, operation):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    goal_id = "goal-a"
    source, root, _ = promoted_create_fixture(tmp_path / "source", provider=provider)
    shared, shared_root, _ = promoted_create_fixture(tmp_path / "shared", provider=provider)
    kind = {"monitor.stop": "monitor.update", "gate.resolve": "gate.resolve"}.get(operation, "todo.update")

    def add(registry, text):
        if kind == "monitor.update":
            intent = {"task_class": "continuous_monitor", "claimed_by": "agent-a",
                "monitor_metadata": {"cadence": "2h", "target_key": text, "watch_only": "true"}}
        elif kind == "gate.resolve":
            intent = {"task_class": "user_gate", "blocks_agent": "agent-a"}
        else:
            intent = {"task_class": "advancement_task", "claimed_by": "agent-a"}
        return add_goal_todo(registry_path=registry, goal_id=goal_id, text=text,
            role="user" if kind == "gate.resolve" else "agent", **intent)["todo_id"]

    todo_id = add(source, "Review source task")
    shared_id = add(shared, "Shared decoy task")
    def read(runtime):
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal_id)

    source_before, shared_before = read(root), read(shared_root)
    directory = tmp_path / "directory.json"
    doc = json.loads(shared.read_text())
    doc["registry_role"] = "global-local"
    doc["goals"][0]["source_registry"] = str(source)
    directory.write_text(json.dumps(doc))
    store = ChatActionStore(tmp_path / "chat" / "actions")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = directory, str(tmp_path / "chat"), False
    server.action_store = store
    server.action_service = ChatActionService(store=store, registry_path=directory)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection(*server.server_address, timeout=40)

    def request(method, path, body=None):
        connection.request(method, path, None if body is None else json.dumps(body),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def detail(task_id):
        return request("GET", f"/api/chat/todo/detail?goal_id={goal_id}&todo_id={task_id}")

    def preview(task_id, key, review_basis=None):
        intent = {"goal_id": goal_id, "todo_id": task_id, "agent_id": "agent-a"}
        if kind == "gate.resolve":
            intent.update(decision="approve")
        elif kind == "monitor.update":
            intent.update(operation="stop")
        else:
            intent.update(operation=operation)
            if operation == "edit":
                intent.update(text="Reviewed authoritative requirements")
                if review_basis:
                    intent["review_basis"] = review_basis
            else:
                intent.update(note="Completed the selected task")
        return request("POST", "/api/actions/preview", {
            "action_kind": kind, "summary": "Apply a source Task action",
            "context": {"kind": "goal", "goal_id": goal_id},
            "normalized_parameters": intent, "idempotency_key": key,
        })

    try:
        basis = None
        if kind != "gate.resolve":
            code, shown = detail(todo_id)
            assert code == 200, shown
            basis = shown["review_basis"]
            assert shown["text"] == source_before["todos"][0]["text"]
            assert detail(shared_id)[0] == 404
        code, refused = preview(shared_id, "shared-only")
        assert code == 400 and refused.get("ok") is False, refused

        code, proposal = preview(todo_id, "authoritative", basis)
        assert code == 201, proposal
        preview_basis = proposal["proposal"]["canonical_update_basis"]
        assert preview_basis["provider_revision"] == source_before["provider_revision"]
        if basis is not None:
            assert preview_basis["source_identity"] == basis["source_identity"]
        assert read(root) == source_before and read(shared_root) == shared_before
        path = f"/api/actions/{proposal['proposal']['proposal_id']}/apply"
        code, response = request("POST", path, {})
        assert code == 200 and response["proposal"]["status"] == "applied", response
        after = read(root)
        assert int(after["cursor"]) == int(source_before["cursor"]) + 1
        (updated,) = after["todos"]
        if operation == "edit":
            assert updated["text"] == "[P1] Reviewed authoritative requirements"
            assert detail(todo_id)[1]["text"] == updated["text"]
        else:
            assert updated["status"] == "done"
        assert request("POST", path, {}) == (code, response)
        assert read(root) == after and read(shared_root) == shared_before
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
