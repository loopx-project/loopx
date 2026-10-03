from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.provider_projection import (
    project_current_canonical_todos,
)
from loopx.control_plane.testing.canary_harness import write_fixture_registry

REPO_ROOT = Path(__file__).resolve().parents[2]

BASE_MARKDOWN = """# Goal

Narrative before the projection stays owner-authored.

## User Todo / Owner Review Reading Queue
<!-- loopx:todo-region-v0 role=user begin -->
<!-- loopx:todo-region-v0 role=user end -->

## Agent Todo
<!-- loopx:todo-region-v0 role=agent begin -->
<!-- loopx:todo-region-v0 role=agent end -->

## Next Action

- Keep this line unchanged and outside the Todo sections.
"""


def _cli(project_root: Path, registry: Path, runtime: Path, provider: str, *arguments: str):
    env = dict(os.environ)
    env.setdefault("LOOPX_USAGE_PING", "0")
    if provider == "sqlite":
        env["NODE_OPTIONS"] = f"{env.get('NODE_OPTIONS', '')} --experimental-sqlite".strip()
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *arguments,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    return process.returncode, json.loads(process.stdout)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_completion_result_stays_canonical_and_projection_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    state = project / "STATE.md"
    state.write_text(BASE_MARKDOWN, encoding="utf-8")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    write_fixture_registry(
        project=project, runtime_root=runtime, registry_path=registry,
        goal_id="goal-a", domain="acceptance",
        adapter_kind="generic_project_goal_v0", state_file=str(state),
        registered_agents=["agent-a"],
    )
    todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_result",
        "role": "agent",
        "text": "Write the accepted result",
        "status": "open",
        "done": False,
        "task_class": "advancement_task",
        "action_kind": "implement",
        "index": 1,
        "source_section": "Agent Todo",
        "archive_state": "active",
        "claimed_by": "agent-a",
    }
    seed_projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=[todo], handoff_mode="soft_claim"
    )
    initialize_canonical_authority(
        runtime, "goal-a", seed_projection, state_path=state, provider=provider
    )

    code, before = _cli(
        REPO_ROOT, registry, runtime, provider,
        "goal-acceptance", "inspect", "--goal-id", "goal-a",
    )
    assert code == 0, before
    document = {
        "scope": {"kind": "all_advancement"},
        "objective": "Deliver a synthetic accepted result",
        "non_goals": [],
        "criteria": [
            {
                "id": "result",
                "description": "The accepted result contains the expected bytes",
                "validation_argv": [
                    sys.executable, "-c",
                    "from pathlib import Path; "
                    "assert Path('completion.md').read_text() == "
                    "'# Synthetic completion\\n\\nPublic-safe bytes.\\n'",
                ],
                "validation_timeout_seconds": 5,
            }
        ],
        "bindings": [{"todo_id": "todo_result", "criterion_ids": ["result"]}],
    }
    document_path = tmp_path / "acceptance.json"
    document_path.write_text(json.dumps(document), encoding="utf-8")
    code, configured = _cli(
        REPO_ROOT, registry, runtime, provider,
        "goal-acceptance", "configure", "--goal-id", "goal-a",
        "--document", str(document_path),
        "--expected-provider-revision", before["provider_revision"],
        "--execute",
    )
    assert code == 0, configured

    result_body = "# Synthetic completion\n\nPublic-safe bytes.\n"
    result_file = project / "completion.md"
    result_file.write_text(result_body, encoding="utf-8")
    code, completed = _cli(
        REPO_ROOT, registry, runtime, provider,
        "todo", "complete", "--goal-id", "goal-a", "--todo-id", "todo_result",
        "--agent-id", "agent-a", "--no-follow-up",
        "--evidence", "Accepted synthetic artifact",
        "--result-file", str(result_file),
    )
    assert code == 0 and completed["changed"] is True, json.dumps(completed, indent=2)
    code, added_next = _cli(
        REPO_ROOT, registry, runtime, provider,
        "todo", "add", "--goal-id", "goal-a", "--role", "agent",
        "--text", "[P1] Next independent work", "--claimed-by", "agent-a",
        "--execute",
    )
    assert code == 0, added_next

    readback = read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id="goal-a"
    )
    completed_todo = next(
        row for row in readback["todos"] if row["todo_id"] == "todo_result"
    )
    assert completed_todo["status"] == "done"
    assert len(readback["todos"]) == 2
    binding = completed_todo["completion_result"]
    assert binding["content_type"] == "text/markdown"
    assert binding["producer_agent_id"] == "agent-a"
    assert binding["acceptance_contract_revision"] == 1

    projection = project_current_canonical_todos(
        registry_path=registry,
        runtime_root=runtime,
        goal_id="goal-a",
        expected_provider_revision=readback["provider_revision"],
        execute=True,
        project=project,
        state_file=state,
    )
    # Todo add already delivers the projection; explicit readback is idempotent.
    assert projection["changed"] is False
    assert projection["parse_render_parity"] is True
    assert projection["narrative_preserved"] is True
    displayed = state.read_text(encoding="utf-8")
    assert "Narrative before the projection stays owner-authored." in displayed
    assert "Keep this line unchanged and outside the Todo sections." in displayed
    assert "Write the accepted result" in displayed
    assert "Next independent work" in displayed
    assert "completion_result" not in displayed
    assert result_body not in displayed
    assert binding["sha256"] not in displayed

    code, completion_read = _cli(
        REPO_ROOT, registry, runtime, provider,
        "todo", "result-read", "--goal-id", "goal-a",
        "--todo-id", "todo_result",
    )
    assert code == 0, completion_read
    assert completion_read["text"] == result_body
    assert completion_read["result"]["sha256"] == binding["sha256"]
    assert (
        read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"]
        == readback["todos"]
    )
