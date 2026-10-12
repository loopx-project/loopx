"""Real canonical reads reach both conversation entries without Lark setup."""
import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat_manager_context import manager_turn_context
from loopx.capabilities.manager_context.inspection import manager_index
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_blocker_and_owner_request_reach_both_conversations(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    def todo(*args):
        process = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
            "--registry", str(registry), "todo", *args, "--goal-id", "goal-a"],
            capture_output=True, text=True, timeout=45)
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads(process.stdout)
    todo("add", "--role", "agent", "--text", "[P0] Verify the primary deliverable",
         "--status", "blocked",
         "--operation-id", "blocked-fixture")
    created = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    work_id = created["todos"][0]["todo_id"]
    todo("update", "--todo-id", work_id, "--status", "blocked",
         "--reason", "Required input has not arrived", "--update-operation-id", "cause-fixture")
    todo("add", "--role", "user", "--text", "[P0] Choose the public release window",
         "--task-class", "user_gate", "--operation-id", "decision-fixture")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    for channel in ("manager", "goal.goal-a"):
        context = manager_turn_context(registry, {"channel_id": channel, "goal_id": "goal-a"},
                                       runtime, include_details=False)
        full_blocker = context["goals"][0]["attention"]["items"][1]["blocker"]
        assert full_blocker["delivery"]["state"] == "pending"
        assert "have not been assessed" in full_blocker["impact"]
        attention = manager_index(context)["goals"][0]["attention"]
        assert attention["status"] == "read", context
        assert attention["coverage"]["known"] == 2
        decision, blocker = attention["items"]
        assert decision["owner_must_act"] is True
        assert "release window" in decision["request"]["text"]
        assert blocker["owner_must_act"] is False
        assert blocker["blocker"]["cause"] == "Required input has not arrived"
        assert blocker["details_omitted"] is True
        assert blocker["read_reference"] == {"view": "todos", "goal_id": "goal-a", "todo_id": work_id}
    after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert after == before
    assert not list(tmp_path.rglob("goal-channel*.json"))
    todo("update", "--todo-id", work_id, "--status", "open",
         "--update-operation-id", "recovery-fixture")
    recovered = manager_turn_context(registry, {"channel_id": "manager"}, runtime,
                                     include_details=False)
    assert recovered["goals"][0]["attention"]["coverage"]["known"] == 1
