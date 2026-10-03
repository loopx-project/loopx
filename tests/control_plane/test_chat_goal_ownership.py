"""Real HTTP -> existing migration owner -> durable File/SQLite, no active Goals."""

import http.client
import json
import threading

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.todos.provider_handoff_mode import set_canonical_handoff_mode
from loopx.todos import add_goal_todo
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)


@pytest.fixture(params=["file", "sqlite"])
def api(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    rows = []
    for goal in ["example", "other"]:
        state = tmp_path / f"{goal}.md"
        state.write_text("---\nhandoff_mode: legacy\n---\n\n## Agent Todo\n")
        rows.append(
            {
                "id": goal,
                "repo": str(tmp_path),
                "state_file": state.name,
                "coordination": {"registered_agents": ["agent-a"]},
            }
        )
        projection = build_todo_runtime_shadow_projection(
            goal_id=goal, handoff_mode="legacy", todos=[]
        )
        initialize_canonical_authority(
            runtime, goal, projection, state_path=state, provider=request.param
        )
    registry.write_text(
        json.dumps({"common_runtime_root": str(runtime), "goals": rows})
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_root = runtime
    server.registry_path = registry
    server.runtime_root_override = str(runtime)
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(action="", body=None, origin=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=90
        )
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        path = "/api/chat/goal-ownership" + (
            f"/{action}" if action else "?goal_id=example"
        )
        connection.request(
            "POST" if body is not None else "GET",
            path,
            body=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        # No local source or backup filenames, exception text or complete state.
        assert str(tmp_path) not in json.dumps(value)
        return response.status, value

    yield call, runtime, registry
    server.shutdown()
    thread.join(5)
    server.server_close()
    restart_effect_runtime()


def preview(call, mode="hard_lease"):
    code, result = call("preview", {"goal_id": "example", "mode": mode})
    assert code == 200 and result["ok"], result
    return result


def apply(call, plan, **overrides):
    return call(
        "apply",
        {
            "goal_id": "example",
            "preview_id": plan["preview_id"],
            "plan_sha256": plan["plan_sha256"],
            **overrides,
        },
    )


def read(runtime, goal="example"):
    return read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id=goal, include_leases=True
    )


def test_browser_migration_preserves_assigned_metadata_and_original_retry(api):
    call, runtime, registry = api
    todo = add_goal_todo(
        registry_path=registry,
        goal_id="example",
        role="agent",
        text="Preserve assigned work",
        priority="P1",
        claimed_by="agent-a",
        note="Retain nested evidence and task requirements",
        task_class="advancement_task",
        required_write_scopes=["src/**"],
    )
    before = read(runtime)
    plan = preview(call)
    assert plan["previous_mode"] == "legacy" and plan["preserved_claim_count"] == 1
    assert read(runtime) == before
    directory = runtime / "chat/ownership-migrations"
    assert not list(directory.glob("*.backup.jsonl"))
    code, applied = apply(call, plan)
    assert code == 200 and applied["status"] == "applied", applied
    assert (
        applied["backup_verified"] and applied["execution_authority_granted"] is False
    )
    assert applied["current"]["current_mode"] == "hard_lease"
    after = read(runtime)
    assert after["todos"] == before["todos"] and after["leases"] == before["leases"]
    assert after["todos"][0]["todo_id"] == todo["todo_id"]
    assert len(list(directory.glob("*.backup.jsonl"))) == 1
    # Model a lost reply/new connection: same carrier recovers the original receipt.
    code, replay = apply(call, plan)
    assert code == 200 and replay["status"] == "replayed", replay
    assert read(runtime) == after
    # A later valid migration is independent; old success is not current policy.
    later = preview(call, "soft_claim")
    assert apply(call, later)[0] == 200
    final = read(runtime)
    code, replay = apply(call, plan)
    assert code == 200 and replay["handoff_mode"] == "hard_lease", replay
    assert replay["current"]["current_mode"] == "soft_claim"
    assert read(runtime) == final
    assert call()[1]["current_mode"] == "soft_claim"


def test_foreign_goal_digest_stale_source_and_backup_corruption_fail_closed(api):
    call, runtime, _ = api
    plan = preview(call)
    before, other = read(runtime), read(runtime, "other")
    assert apply(call, plan, goal_id="other")[0] == 409
    assert apply(call, plan, plan_sha256="0" * 64)[0] == 409
    assert read(runtime) == before and read(runtime, "other") == other
    set_canonical_handoff_mode(
        runtime_root=runtime,
        goal_id="example",
        mode="soft_claim",
        operation_id="independent",
        dry_run=False,
    )
    changed = read(runtime)
    assert apply(call, plan)[0] == 409
    assert read(runtime) == changed
    fresh = preview(call)
    assert apply(call, fresh)[0] == 200
    applied = read(runtime)
    backup = (
        runtime
        / "chat/ownership-migrations"
        / f"{fresh['preview_id']}.json.backup.jsonl"
    )
    backup.write_text("damaged backup\n")
    assert apply(call, fresh)[0] == 409
    assert read(runtime) == applied


def test_paths_legacy_unknown_fields_and_cross_origin_cannot_write(api):
    call, runtime, _ = api
    before = read(runtime)
    assert call("preview", {"goal_id": "example", "mode": "legacy"})[0] == 409
    assert (
        call(
            "preview",
            {"goal_id": "example", "mode": "hard_lease", "plan": "/tmp/injected"},
        )[0]
        == 400
    )
    assert (
        call(
            "apply",
            {
                "goal_id": "example",
                "preview_id": "../elsewhere",
                "plan_sha256": "a" * 64,
            },
        )[0]
        == 400
    )
    assert (
        call(
            "preview",
            {"goal_id": "example", "mode": "hard_lease"},
            "https://untrusted.example",
        )[0]
        == 403
    )
    assert read(runtime) == before


def test_pending_lease_downgrade_rejected_even_after_clock_expiry(api):
    from loopx.control_plane.work_items.task_lease import acquire_task_lease

    call, runtime, registry = api
    todo = add_goal_todo(
        registry_path=registry,
        goal_id="example",
        role="agent",
        text="Running host",
        claimed_by="agent-a",
        task_class="advancement_task",
    )
    # Use the real acquire owner; an expired lease is not proof the host stopped.
    acquired = acquire_task_lease(
        registry_path=registry,
        runtime_root=runtime,
        goal_id="example",
        todo_id=todo["todo_id"],
        owner="agent-a",
        idempotency_key="host",
        ttl_seconds=1,
    )
    assert acquired["ok"], acquired
    import time

    time.sleep(1.1)
    before = read(runtime)
    code, conflict = call("preview", {"goal_id": "example", "mode": "soft_claim"})
    assert (
        code == 409
        and conflict["reason_code"] == "active_lease_incompatible_with_soft_claim"
    ), conflict
    assert conflict["conflicts"][0]["todo_id"] == todo["todo_id"]
    assert read(runtime) == before
