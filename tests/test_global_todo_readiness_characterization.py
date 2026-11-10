"""Temporary cutover corpus: remove after the Python display facade exits."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

import loopx.global_todos as todos
import loopx.goal_portfolio as portfolio
import loopx.control_plane.effect_runtime as runtime

FIXTURE = Path(__file__).parent / "fixtures/global_todo_readiness.json"
NOW = "2026-11-02T00:00:00+00:00"
BASE_REVISION = "d4aaaaefbd37a83a30f10681de839b45d631eeb9"


def observe(case, monkeypatch, tmp_path):
    quota = deepcopy(case["quota"])
    status = {
        "ok": True,
        "attention_queue": {"items": [{"goal_id": "sample"}]},
        "run_history": {"goals": [{"id": "sample", "latest_status_run": {
            "generated_at": NOW,
        }}]},
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"goals": [{"id": "sample", "coordination": {
        "registered_agents": ["alpha", "beta"],
    }}]}))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(NOW)

    monkeypatch.setattr(portfolio, "datetime", Clock)
    monkeypatch.setattr(todos, "now_utc_iso", lambda: NOW)
    monkeypatch.setattr(todos, "collect_status", lambda **_: deepcopy(status))
    monkeypatch.setattr(portfolio, "collect_status", lambda **_: deepcopy(status))
    monkeypatch.setattr(portfolio, "_source_versions", lambda *_: {"run_index": None})
    monkeypatch.setattr(todos, "build_quota_should_run", lambda *_, **__: deepcopy(quota))
    monkeypatch.setattr(portfolio, "build_quota_should_run", lambda *_, agent_id, **__: {
        **deepcopy(quota), "agent_identity": {"agent_id": agent_id},
    })
    calls = []
    request = runtime.effect_runtime_request

    def counted(method, *args, **kwargs):
        calls.append(method)
        return request(method, *args, **kwargs)

    monkeypatch.setattr(runtime, "effect_runtime_request", counted)
    global_payload = todos.build_global_todos(
        registry_path=path, runtime_root_override=None, agent_id=None, limit=40,
    )
    global_calls = list(calls)
    calls.clear()
    portfolio_payload = portfolio.build_goal_portfolio(
        registry_path=path, now=datetime.fromisoformat(NOW).astimezone(timezone.utc),
    )
    return {
        "global_json": json.dumps(global_payload, ensure_ascii=False, separators=(",", ":")),
        "global_markdown": todos.render_global_todos_markdown(global_payload),
        "portfolio_json": json.dumps(portfolio_payload, ensure_ascii=False, separators=(",", ":")),
        "portfolio_markdown": portfolio.render_goal_portfolio(portfolio_payload),
    }, {"global": global_calls, "portfolio": calls}


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text()), ids=lambda c: c["name"])
def test_complete_outputs_and_crossings(case, monkeypatch, tmp_path):
    observed, calls = observe(case, monkeypatch, tmp_path)
    assert observed == case["expected"]
    # Both measurements are pinned before cutover; output bytes never change.
    assert calls == case["crossings_after"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_production_cli_archive_parity(provider, tmp_path):
    import os
    import subprocess
    import sys

    from loopx.control_plane.testing.canary_harness import (
        import_fixture_authority,
        project_state_path,
        write_fixture_registry,
    )

    source = Path(__file__).resolve().parents[1]
    base = tmp_path / "base"
    base.mkdir()
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASE_REVISION, "HEAD"],
        cwd=source,
    ).returncode == 0
    archive = subprocess.run(
        ["git", "archive", BASE_REVISION], cwd=source, capture_output=True, check=True,
    )
    subprocess.run(["tar", "-xf", "-", "-C", str(base)], input=archive.stdout, check=True)
    home = tmp_path / "home"
    project, runtime_root = home / "project", home / "runtime"
    registry = project / ".loopx/registry.json"
    write_fixture_registry(
        project=project, runtime_root=runtime_root, registry_path=registry,
        goal_id="sample", domain="synthetic", adapter_kind="generic_project_goal_v0",
        registered_agents=["alpha", "beta"], quota_allowed_slots=10,
    )
    state = project_state_path(project, "sample")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        "---\nstatus: active\nupdated_at: 2026-11-02T00:00:00+00:00\n---\n\n"
        "# Synthetic goal\n\n## Agent Todo\n\n"
        "- [ ] [P1] Deliver synthetic change.\n"
        "  <!-- loopx:todo todo_id=todo_sample status=open "
        "task_class=advancement_task claimed_by=alpha -->\n\n"
        "- [ ] [P2] Review synthetic change.\n"
        "  <!-- loopx:todo todo_id=todo_review status=blocked "
        "task_class=advancement_task action_kind=review_pr claimed_by=beta -->\n\n"
        "## User Todo\n\n- [ ] Confirm synthetic scope.\n"
        "  <!-- loopx:todo todo_id=todo_gate status=open task_class=user_gate "
        "blocks_agent=beta unblocks_todo_id=todo_review -->\n"
    )
    import_fixture_authority(registry, "sample", provider=provider)
    # Freeze only the clock, before importing the real public CLI. Both copies
    # read the same physical files, preserving source versions and their hashes.
    launcher = """
import datetime, sys, time
sys.path.insert(0, sys.argv.pop(1))
class Clock(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 2, tzinfo=datetime.timezone.utc)
datetime.datetime = Clock
time.time = lambda: 1793577600.0
from loopx.entrypoint import main
raise SystemExit(main())
"""
    for command in ("global-todos", "goal-portfolio"):
        for output_format in ("json", "markdown"):
            outputs = []
            for tree in (base, source):
                result = subprocess.run(
                    [sys.executable, "-c", launcher, str(tree), "--registry", str(registry),
                     "--runtime-root", str(runtime_root), "--format", output_format, command],
                    cwd=tree, env={**os.environ, "HOME": str(home)}, capture_output=True,
                    timeout=60,
                )
                assert result.returncode == 0, result.stdout + result.stderr
                outputs.append(result.stdout)
            if command == "goal-portfolio" and output_format == "json":
                # Source revisions hash the complete status read. Different
                # status schemas can identify the same portfolio projection.
                normalized = []
                for output in outputs:
                    snapshot = json.loads(output)
                    assert len(snapshot["goals"]) == 1
                    for digest in (snapshot.pop("snapshot_id"), snapshot["goals"][0]["source"].pop("revision")):
                        assert digest.startswith("sha256:") and len(digest) == 71
                    normalized.append(snapshot)
                assert normalized[0] == normalized[1]
            else:
                assert outputs[0] == outputs[1]
            if output_format == "json":
                payload = json.loads(outputs[1])
                if command == "goal-portfolio":
                    assert len(payload["goals"][0]["agents"]) == 2
                    assert any(agent["todos"] for agent in payload["goals"][0]["agents"])
                else:
                    assert payload["todos"]
            print(f"CLI parity: {provider} {command} {output_format} bytes={len(outputs[1])}")
