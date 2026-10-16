"""Original Turn recovery uses its journal, independently of a fresh frontier."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane import effect_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from tests.test_loopx_turn_driver import (
    _completion_host_and_validation_scripts,
    _turn_run_once_completion_argv,
    _write_live_fixture,
)

REPO = Path(__file__).resolve().parents[2]
GOAL = "loopx-turn-fixture"


@pytest.fixture
def owned_runtime(tmp_path, monkeypatch):
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    yield
    assert effect_runtime._runtime_dir().resolve().is_relative_to(tmp_path.resolve())
    effect_runtime.restart_effect_runtime(wait_for_exit=True)


def _invoke(argv):
    result = subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", *argv],
        cwd=REPO, text=True, capture_output=True, timeout=60,
    )
    assert not result.stderr, result.stderr
    return result.returncode, json.loads(result.stdout)


def _committed_turn(root, provider):
    project, runtime, registry = _write_live_fixture(root, provider=provider)
    host, validator = _completion_host_and_validation_scripts()
    host = host.replace("request = json.load(sys.stdin)",
        'request = json.load(sys.stdin)\npathlib.Path("host-calls").open("a").write("host\\n")')
    argv = _turn_run_once_completion_argv(project, runtime, registry, host, validator)
    code, completed = _invoke(argv)
    assert code == 0 and completed["status"] == "committed", completed
    return argv, project, runtime, registry, completed["resume_turn_key"]


def _grow_backlog(runtime, count):
    todos = build_todo_runtime_shadow_projection(goal_id=GOAL, todos=[{
        "schema_version": "todo_item_v0", "todo_id": f"todo_backlog_{index:05d}",
        "index": index + 2, "role": "agent", "status": "open", "done": False,
        "text": f"[P2] Additional work {index}", "priority": "P2",
        "claimed_by": "codex-fixture", "task_class": "advancement_task",
        "archive_state": "active", "source_section": "Agent Todo",
    } for index in range(count)])["todos"]
    # Exercise real provider CAS while retaining the original completion and receipts.
    script = (
        f"import {{openLocalAuthorityStore}} from {json.dumps((REPO / 'loopx/control_plane/coordination/local_authority_provider.ts').as_uri())};"
        f"import {{reduceCoordinationProjection}} from {json.dumps((REPO / 'loopx/control_plane/coordination/coordination_projection.ts').as_uri())};"
        "let raw='';for await(const chunk of process.stdin)raw+=chunk;const v=JSON.parse(raw);"
        "const store=await openLocalAuthorityStore(v.root,v.goal);const current=await store.loadAuthority();"
        "if(current.status!=='loaded')throw Error('fixture missing');"
        "const next=reduceCoordinationProjection(current.head,v.goal,v.todos.map(todo=>({kind:'todo_upsert',todo})));"
        "const result=await store.commitAuthority({expected_provider_revision:current.provider_revision,"
        "operation_id:'fixture-backlog-growth',events:[],next_projection:next,receipts:[]});"
        "if(result.status!=='applied')throw Error(JSON.stringify(result));"
    )
    subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps({"root": str(runtime), "goal": GOAL, "todos": todos}),
        text=True, capture_output=True, check=True, timeout=45,
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_committed_replay_survives_frontier_growth_beyond_fresh_request_limit(tmp_path, owned_runtime, provider):
    argv, project, runtime, _registry, key = _committed_turn(tmp_path, provider)
    journal = runtime / "goals" / GOAL / "turns" / (key.removeprefix("sha256:") + ".json")
    ledger = runtime / "goals" / GOAL / "runs/index.jsonl"
    original_journal, original_ledger = journal.read_bytes(), ledger.read_bytes()
    _grow_backlog(runtime, 2000)

    code, replay = _invoke([*argv, "--resume-turn-key", key])

    assert code == 0, replay
    assert replay["status"] == "committed" and replay["replayed"] is True
    assert not any(replay["effects"].values())
    assert (project / "host-calls").read_text() == "host\n"
    assert journal.read_bytes() == original_journal
    assert ledger.read_bytes() == original_ledger
    source = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL)
    assert source is not None
    assert source["source_authority"] == ("sqlite_v0" if provider == "sqlite" else "file_v0")
    assert len(source["todos"]) == 2001
    assert next(todo for todo in source["todos"] if todo["todo_id"] == "todo_fixture0001")["status"] == "done"


def test_original_replay_does_not_sync_a_new_inbox_but_fresh_turn_still_does(tmp_path, owned_runtime, monkeypatch):
    argv, project, runtime, registry, key = _committed_turn(tmp_path, "file")
    code, installed = _invoke([
        "--runtime-root", str(runtime), "--format", "json",
        "extension", "install", "--bundled", "loopx-lark", "--execute",
    ])
    assert code == 0, installed
    registered = json.loads(registry.read_text())
    registered["goals"][0].setdefault("control_plane", {})["lark_event_inbox"] = {
        "enabled": True, "config_path": ".loopx/config/inbox.json",
    }
    registry.write_text(json.dumps(registered))
    config = project / ".loopx/config/inbox.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "schema_version": "lark_event_inbox_config_v0", "enabled": True,
        "inbox_dir": ".loopx/inbox/requirements", "capture_scope": "configured_chat_all",
        "topic_root_message_id": "om_fixture_root",
        "material_review": {"enabled": False, "drain_limit": 20},
        "reply": {
            "enabled": True, "sender_profile": "fixture-bot", "sender_identity": "bot",
            "bot_display_name": "Fixture Bot", "chat_id": "oc_fixture",
        },
    }))
    synchronized = project / "new-inbox-sync"

    def external_inbox_sync(**_kwargs):
        # Substitute provider I/O; retain real registration, activation and dispatch.
        synchronized.write_text("new inbox read")
        return {
            "ok": True, "status": "empty", "observation_count": 0,
            "agent_read_required": False, "external_reads_performed": True,
            "external_writes_performed": False, "local_private_state_mutated": False,
            "error_code": None,
        }

    monkeypatch.setattr("loopx.cli_commands.lark_inbox.sync_lark_turn_start_inbox", external_inbox_sync)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([*argv, "--resume-turn-key", key])
    replay = json.loads(output.getvalue())
    assert code == 0 and replay["replayed"] is True, replay
    assert not synchronized.exists()
    assert not any(replay["effects"].values())
    assert (project / "host-calls").read_text() == "host\n"
    with contextlib.redirect_stdout(io.StringIO()):
        main(argv)
    assert synchronized.read_text() == "new inbox read"
