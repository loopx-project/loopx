"""A confirmed start stays recoverable when its real first admission is held."""
import json

import pytest

from test_creation_task_queue import cart_host as cart_host
from test_creation_task_queue import environment as environment
from test_creation_task_queue import preview_requested_task
from loopx.agent_registry import load_goal_from_registry
from loopx.configure_goal import configure_goal


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("requested", [True, False])
def test_confirmed_creation_retries_original_work_after_initial_quota_hold(environment, cart_host, monkeypatch, provider, requested):
    import loopx.chat_actions as creation_owner
    environment[0](provider)
    actions, request, controller, capture, _, _ = cart_host
    configure = creation_owner.configure_goal
    def pause_after_real_agent_binding(**kwargs):
        result = configure(**kwargs)
        if kwargs.get("goal_id") == "cart-repair" and kwargs.get("registered_agents") == ["codex"]:
            configure_goal(registry_path=controller.registry_path, goal_id="cart-repair",
                quota_compute=0, execute=True)
        return result
    proposal = preview_requested_task(cart_host, requested=requested)
    pid = proposal["proposal_id"]
    path = f"/api/actions/{pid}/apply"
    with monkeypatch.context() as held:
        held.setattr(creation_owner, "configure_goal", pause_after_real_agent_binding)
        status, refused = request(path, {})
    if not requested:
        assert status == 200 and refused["proposal"]["status"] == "applied", refused
        assert "original_task_request" not in refused["proposal"]["receipt"]
        assert "session_id" not in refused["proposal"]["receipt"]["resource_ids"]
        assert refused["proposal"]["receipt"]["child_gate"]["kind"] == "goal_quota_gate"
        assert controller.store.list_sessions(goal_id="cart-repair") == []
        controller.resume_creation_requests()
        assert not controller.session_queue_workers
        return
    assert status == 400, refused
    retained = actions.load(pid)
    assert retained["status"] == "failed" and retained["receipt"] is None
    assert retained["normalized_parameters"]["start_execution"] is True
    steps = retained["checkpoint"]["steps"]
    assert steps["first_turn_gated"]["gate"]["kind"] == "goal_quota_gate"
    assert "first_session_opened" not in steps and "original_task_requested" not in steps
    assert controller.store.list_sessions(goal_id="cart-repair") == []
    before = capture.read_bytes() if capture.exists() else b""
    assert not any(json.loads(row).get("method") == "turn/start" for row in before.splitlines())
    goal = load_goal_from_registry(controller.registry_path, "cart-repair")
    original_grants = goal["coordination"]["checkpointed_boundary_authority"]
    assert len(original_grants) == 1 and original_grants[0]["decision_id"] == pid
    original_todos = steps["todos_created"]["todo_ids"]
    controller.resume_creation_requests()
    assert not controller.session_queue_workers
    assert (capture.read_bytes() if capture.exists() else b"") == before

    # Restore through the real quota owner; retry the original confirmation.
    # The test never manufactures quota permission or another creation intent.
    configure_goal(registry_path=controller.registry_path, goal_id="cart-repair",
        quota_compute=1, execute=True)
    status, resumed = request(path, {})
    assert status == 202, resumed
    committed = resumed["proposal"]
    ids = committed["receipt"]["resource_ids"]
    assert committed["proposal_id"] == pid and ids["todo_ids"] == original_todos
    assert ids["creation_operation_id"] == pid
    assert committed["request_digest"] == retained["request_digest"]
    assert committed["receipt"]["original_task_request"]["session_id"] == ids["session_id"]
    assert committed["receipt"]["original_task_request"]["todo_id"] == original_todos[0]
    first = controller.wait_for_turn(session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=20)
    assert first["status"] == "completed"
    assert len(controller.store.list_sessions(goal_id="cart-repair")) == 1
    assert load_goal_from_registry(controller.registry_path, "cart-repair")["coordination"][
        "checkpointed_boundary_authority"] == original_grants
    after = capture.read_bytes()
    calls = [json.loads(row) for row in after.splitlines()]
    assert sum(row.get("method") == "thread/start" for row in calls) == 1
    assert sum(row.get("method") == "turn/start" for row in calls) == 1
    assert request(path, {})[1]["proposal"]["receipt"] == committed["receipt"]
    assert capture.read_bytes() == after
