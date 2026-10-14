"""Real-state replay and write-boundary checks for the source-checkout demo."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from demo.workspace.__main__ import prepare
from loopx.control_plane.todos.handoff_mode import show_goal_handoff_mode
from loopx.todos import list_goal_todos
from loopx.control_plane.work_items.task_lease import inspect_task_lease


def test_refuses_existing_work_and_symlink(tmp_path):
    existing = tmp_path / "work"
    existing.mkdir()
    sentinel = existing / "important.txt"
    sentinel.write_text("preserve")
    with pytest.raises(ValueError, match="empty directory"):
        prepare(existing)
    link = tmp_path / "link"
    link.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        prepare(link)
    assert sentinel.read_text() == "preserve"


def test_real_state_replay_is_local_and_repeatable(tmp_path, monkeypatch):
    home = tmp_path / "personal-home"
    registry = home / ".codex" / "loopx" / "registry.global.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"sentinel": "personal registry"}')
    current_registry = home / ".loopx" / "registry.global.json"
    current_registry.parent.mkdir(parents=True)
    current_registry.write_text('{"sentinel": "other personal registry"}')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    root = tmp_path / "demo"
    def prepare_from_cli():
        result = subprocess.run(
            [sys.executable, "-m", "demo.workspace", "prepare", "--root", str(root)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    manifest = prepare_from_cli()
    assert prepare_from_cli() == manifest
    assert len(manifest["goals"]) == 3
    assert {
        goal["id"]: show_goal_handoff_mode(
            registry_path=root / "registry.json",
            goal_id=goal["id"],
            runtime_root_arg=str(root / "runtime"),
        )["handoff_mode"]
        for goal in manifest["goals"]
    } == {
        "community-day": "soft_claim",
        "research-brief": "soft_claim",
        "neighborhood-site": "soft_claim",
    }

    def todos(goal_id):
        return list_goal_todos(
            registry_path=root / "registry.json",
            goal_id=goal_id,
            runtime_root_arg=str(root / "runtime"),
        )["todos"]

    before = {g["id"]: todos(g["id"]) for g in manifest["goals"]}
    for goal_id, rows in before.items():
        agents = [t for t in rows if t["role"] == "agent"]
        assert len(agents) == 20
        assert len({t["claimed_by"] for t in agents}) == 4
        assert sum(t["status"] == "done" for t in agents) == 7
        completed = next(t for t in agents if t["status"] == "done")
        lease = inspect_task_lease(
            registry_path=root / "registry.json", runtime_root=root / "runtime",
            goal_id=goal_id, todo_id=completed["todo_id"],
        )
        assert lease["handoff_mode"] == "soft_claim"
        assert lease["lease"] is None, "authored replay must not create execution leases"
        assert sum(t["status"] == "deferred" for t in agents) == 2
        assert sum(t["status"] == "blocked" for t in agents) == 4
        assert len([t for t in rows if t["role"] == "user" and not t["done"]]) == 2
        by_id = {t["todo_id"]: t for t in rows}
        for gate in (t for t in rows if t["role"] == "user"):
            target = by_id[gate["unblocks_todo_id"]]
            assert target["status"] == "blocked"
            assert target["required_decision_scopes"] == [gate["decision_scope"]]
    artifact = json.loads(
        (root / "projects/community-day/calculations.json").read_text()
    )
    assert artifact["sum"] == 5400
    assert artifact["contingency"] == 600
    sensitivity = json.loads(
        (root / "projects/research-brief/calculations.json").read_text()
    )["operating_cost_sensitivity"]
    assert len(sensitivity) == 27
    assert min(r["annual_cost"] for r in sensitivity) == 342.86
    assert max(r["annual_cost"] for r in sensitivity) == 2880
    for goal in manifest["goals"]:
        monitors = [
            t for t in before[goal["id"]] if t["task_class"] == "continuous_monitor"
        ]
        assert len(monitors) == 2
        assert all(t["watch_only"] and t["next_due_at"] for t in monitors)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "demo.workspace",
            "advance",
            "--root",
            str(root),
            "--story",
            "research-brief",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    after = todos("research-brief")
    assert sum(t["done"] for t in after if t["role"] == "user") == 1
    assert sum(t["status"] == "blocked" for t in after) == 3
    assert todos("community-day") == before["community-day"]
    assert todos("neighborhood-site") == before["neighborhood-site"]
    assert registry.read_text() == '{"sentinel": "personal registry"}'
    assert current_registry.read_text() == '{"sentinel": "other personal registry"}'
