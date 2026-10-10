from __future__ import annotations

import hashlib
import json
from pathlib import Path
import select
import subprocess
import threading
from typing import Any

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx import global_registry as global_registry_module
from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_ITEM_SCHEMA_VERSION,
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    canonical_bytes,
)
from loopx.global_registry import (
    retire_global_registry_goals,
    sync_project_registry_to_global,
)
from loopx.paths import global_registry_path
from loopx.registry_writability import probe_registry_write_path


REPO = Path(__file__).resolve().parents[2]
GOAL_ID = "goal-retirement-writer"
TODO_ID = "todo-retirement-writer"

NODE = r"""
import {once} from "node:events";
const input = JSON.parse(process.argv[1]);
const base = new URL(input.module_base);
const {openLocalAuthorityStore} = await import(new URL("local_authority_provider.ts", base));
const {updateLocalCoordinationTodo} = await import(new URL("local_authority_runtime.ts", base));
const store = await openLocalAuthorityStore(input.request.runtime_root, input.request.goal_id);
if (input.mode === "inspect") {
  process.stdout.write(JSON.stringify(await store.loadAuthority()) + "\n");
} else {
  const actualCommit = store.commitAuthority.bind(store);
  store.commitAuthority = async (commit) => {
    process.stdout.write("BARRIER commit\n");
    await once(process.stdin, "data");
    return await actualCommit(commit);
  };
  process.stdout.write(JSON.stringify(
    await updateLocalCoordinationTodo(input.request, {createStore: () => store}),
  ) + "\n");
}
"""


def _node_command(mode: str, request: dict[str, object]) -> list[str]:
    module_base = (REPO / "loopx/control_plane/coordination").as_uri() + "/"
    return [
        "node",
        "--no-warnings",
        "--experimental-sqlite",
        "--experimental-strip-types",
        "--input-type=module",
        "-e",
        NODE,
        json.dumps({"mode": mode, "request": request, "module_base": module_base}),
    ]


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_global_goal_retirement_waits_for_an_admitted_canonical_update(
    tmp_path: Path,
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    state = project / "ACTIVE_GOAL_STATE.md"
    registry.parent.mkdir(parents=True)
    state.write_text("# Goal\n", encoding="utf-8")
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": state.name,
                        "coordination": {
                            "registered_agents": ["agent-a", "agent-b"]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    todo = {
        "schema_version": TODO_DOMAIN_ITEM_SCHEMA_VERSION,
        "todo_id": TODO_ID,
        "text": "Before retirement",
        "role": "agent",
        "status": "open",
        "done": False,
        "archive_state": "active",
        "claimed_by": "agent-a",
        "note": None,
        "required_capabilities": [],
        "excluded_agents": [],
        "evidence": None,
    }
    projection = {
        "goal_id": GOAL_ID,
        "handoff_mode": "soft_claim",
        "todos": [todo],
        "leases": [],
        "todo_read_model": {
            "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
            "todo_count": 1,
            "records_sha256": hashlib.sha256(canonical_bytes([todo])).hexdigest(),
            "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
        },
    }
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        projection,
        state_path=state,
        provider=provider,
    )
    synced = sync_project_registry_to_global(
        registry_path=registry,
        runtime_root_override=str(runtime),
        dry_run=False,
    )
    assert synced["ok"] is True, synced

    request = {
        "schema_version": "loopx_local_coordination_todo_update_request_v0",
        "runtime_root": str(runtime),
        "goal_id": GOAL_ID,
        "todo_id": TODO_ID,
        "role": "agent",
        "actor_agent_id": "agent-a",
        "registered_agents": ["agent-a", "agent-b"],
        "operation_id": f"update-before-{provider}-retirement",
        "patch": {"text": "Committed before retirement"},
        "clear_fields": [],
        "dry_run": False,
        "observed_at": "2026-10-07T06:00:00Z",
    }
    writer = subprocess.Popen(
        _node_command("update", request),
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    retirement_finished = threading.Event()
    retirement_preflight_finished = threading.Event()
    retirement_results: list[dict[str, object]] = []
    retirement_errors: list[BaseException] = []
    actual_probe = probe_registry_write_path

    def observed_probe(
        path: Path,
        *,
        create_parent: bool = True,
    ) -> dict[str, Any]:
        result = actual_probe(path, create_parent=create_parent)
        retirement_preflight_finished.set()
        return result

    monkeypatch.setattr(
        global_registry_module,
        "probe_registry_write_path",
        observed_probe,
    )

    def retire() -> None:
        try:
            retirement_results.append(
                retire_global_registry_goals(
                    runtime_root_override=str(runtime),
                    goal_ids=[GOAL_ID],
                    execute=True,
                )
            )
        except BaseException as error:
            retirement_errors.append(error)
        finally:
            retirement_finished.set()

    thread = threading.Thread(target=retire)
    try:
        assert writer.stdout is not None
        ready, _, _ = select.select([writer.stdout], [], [], 10)
        assert ready and writer.stdout.readline().strip() == "BARRIER commit"
        registry.unlink()
        state.unlink()
        thread.start()
        assert retirement_preflight_finished.wait(timeout=5)
        assert not retirement_finished.wait(timeout=0.2), (
            "the global route retired while an admitted canonical update was "
            "paused before provider commit"
        )

        stdout, stderr = writer.communicate("continue\n", timeout=20)
        assert writer.returncode == 0, stdout + stderr
        update = json.loads(stdout)
        assert update["status"] == "applied", update
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert retirement_errors == []
        assert retirement_results and retirement_results[0]["ok"] is True

        global_goals = json.loads(
            global_registry_path(runtime).read_text(encoding="utf-8")
        )["goals"]
        assert global_goals == []
        inspected = subprocess.run(
            _node_command("inspect", request),
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        head = json.loads(inspected.stdout)
        assert head["head"]["todos"][0]["text"] == "Committed before retirement"
    finally:
        _stop(writer)
        thread.join(timeout=10)
