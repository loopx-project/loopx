"""Real canonical reads reach both conversation entries without Lark setup."""
import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat_manager_context import manager_turn_context
from loopx.capabilities.manager_context.inspection import manager_index
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.status import collect_status


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


@pytest.mark.parametrize("provider", [None, "file", "sqlite"])
@pytest.mark.parametrize("mixed_failure", [False, True])
def test_status_automatically_rebuilds_stale_run_routing(tmp_path, monkeypatch, provider, mixed_failure):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    if provider:
        registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    else:
        registry, runtime, state = tmp_path / "registry.json", tmp_path / "runtime", tmp_path / "state.md"
        state.write_text("# Goal\n\n## Agent Todo\n", encoding="utf-8")
        registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime),
            "goals": [{"id": "goal-a", "repo": str(tmp_path), "state_file": str(state),
                "coordination": {"registered_agents": ["agent-a"]}}]}), encoding="utf-8")
    config = json.loads(registry.read_text(encoding="utf-8"))
    config["goals"][0]["adapter"] = {"kind": "read_only_project_map_v0", "status": "connected-read-only"}
    registry.write_text(json.dumps(config), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry), "todo", "add", "--goal-id", "goal-a", "--role", "agent",
        "--text", "Verify the current deliverable", *(["--operation-id", "current-work"] if provider else [])],
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    state.write_text("---\nstatus: active\nupdated_at: 2026-10-15T09:00:00Z\n---\n\n"
        + state.read_text(encoding="utf-8"), encoding="utf-8")
    runs = runtime / "goals/goal-a/runs"
    runs.mkdir(parents=True, exist_ok=True)
    record = {"goal_id": "goal-a", "generated_at": "2026-10-01T00:00:00Z",
        "classification": "runtime_error" if mixed_failure else "state_refreshed",
        "delivery_outcome": "outcome_progress", "recommended_action": "Obsolete step",
        "state": {"sha256_16": "0" * 16, "frontmatter": {"updated_at": "2026-10-01T00:00:00Z"}}}
    artifact = runs / "historical.json"
    artifact.write_text(json.dumps(record), encoding="utf-8")
    index = runs / "index.jsonl"
    index.write_text(json.dumps({**record, "json_path": str(artifact), "markdown_path": str(artifact)}) + "\n",
        encoding="utf-8")
    original = {path: path.read_bytes() for path in (state, artifact, index)}
    canonical = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    for _ in range(2):
        status = collect_status(registry_path=registry, runtime_root_override=str(runtime),
            scan_roots=[], goal_id="goal-a", limit=3)
        item = next(item for item in status["attention_queue"]["items"] if item["goal_id"] == "goal-a")
        if mixed_failure:
            assert item["status"] == "runtime_error"
            assert item["source"] == "latest_run"
            assert item["stale_latest_run_warning"]["requires_refresh_state"] is True
        else:
            assert item["source"] == "active_state"
            assert item["status"] == "active_state_agent_todo"
            assert item["recommended_action"] == "[P1] Verify the current deliverable"
            assert item.get("stale_latest_run_warning") is None
        historical = status["run_history"]["goals"][0]["latest_status_run"]
        assert historical["generated_at"] == record["generated_at"]
        assert historical["recommended_action"] == "Obsolete step"
    assert {path: path.read_bytes() for path in original} == original
    assert sorted(path.name for path in runs.iterdir()) == ["historical.json", "index.jsonl"]
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == canonical
