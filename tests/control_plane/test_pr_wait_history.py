"""Resume evidence survives history growth and fresh production CLI processes."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path
from loopx.todos import add_goal_todo
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_merged_wait_stays_ready_after_history_growth_and_restart(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    data = json.loads(registry.read_text())
    data["goals"][0].update(domain="code", adapter={"kind": "manual"})
    registry.write_text(json.dumps(data))
    todo_id = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Continue the original requirement", claimed_by="agent-a",
        resume_when="pr_merged:owner/repo#42")["todo_id"]
    binary = tmp_path / "bin"
    binary.mkdir()
    count = tmp_path / "provider-reads"
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" +
        "import json, pathlib\n" +
        f"p=pathlib.Path({str(count)!r}); p.write_text(p.read_text()+'read\\n' if p.exists() else 'read\\n')\n" +
        "print(json.dumps({'state':'MERGED', 'mergedAt':'2026-10-01T00:00:00Z', " +
        "'url':'https://github.com/owner/repo/pull/42'}))\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])

    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args],
            capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 0, result.stderr + result.stdout
        return json.loads(result.stdout)

    def read():
        return cli("todo", "list", "--goal-id", "goal-a", "--todo-id", todo_id)["todo"]

    before = read()
    assert before["resume_ready"] is False
    observed = cli("heartbeat-prequota", "--goal-id", "goal-a", "--agent-id", "agent-a")
    assert observed["checks"]["pr_dependencies"]["merged_count"] == 1
    ready = read()
    assert ready["resume_ready"] is True
    proof = ready["resume_condition"]["matched_event_id"]
    log = rollout_event_log_path(runtime, "goal-a")
    for index in range(501):
        append_rollout_event(log, build_rollout_event(goal_id="goal-a", event_kind="validation",
            classification="unrelated_check", summary=f"Unrelated record {index}"))
    # Each call starts a fresh CLI; no process cache can stand in for durable proof.
    restarted = read()
    assert restarted["resume_ready"] is True
    assert restarted["resume_condition"]["matched_event_id"] == proof
    for key in ("resume_when", "text", "status", "claimed_by"):
        assert restarted[key] == before[key]
    quiet = cli("heartbeat-prequota", "--goal-id", "goal-a", "--agent-id", "agent-a")
    assert quiet["checks"]["pr_dependencies"]["external_read_count"] == 0
    assert count.read_text().splitlines() == ["read"]
    assert read()["resume_ready"] is True
    # Production status/attention/index and quota consume the same resume owner.
    status = cli("status", "--goal-id", "goal-a")
    quota = cli("quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a")

    def resume_rows(value):
        if isinstance(value, dict):
            if value.get("todo_id") == todo_id and "resume_ready" in value:
                yield value
            for child in value.values():
                yield from resume_rows(child)
        elif isinstance(value, list):
            for child in value:
                yield from resume_rows(child)

    for payload in (status, quota):
        rows = list(resume_rows(payload))
        assert rows, payload
        assert all(row["resume_ready"] is True for row in rows)
    assert count.read_text().splitlines() == ["read"]
