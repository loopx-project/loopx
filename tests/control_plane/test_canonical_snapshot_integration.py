"""Production RPC and CLI over real File/SQLite, including failed mixed reads."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

from loopx.control_plane.coordination import local_authority
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.effect_runtime import (
    MAX_RESPONSE_BYTES,
    EffectRuntimeResponseAmbiguous,
    effect_runtime_result,
)
from loopx.control_plane.todos import provider_projection

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["file", "sqlite"])
def wide_goal(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = (
        tmp_path / "runtime",
        tmp_path / "state.md",
        tmp_path / "registry.json",
    )
    state.write_text(
        "# Snapshot recovery\n\nHuman narrative survives.\n\n## Agent Todo\n"
    )
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "goal-a",
                        "repo": str(tmp_path),
                        "state_file": state.name,
                        "status": "active",
                    },
                ],
            }
        )
    )
    records = [
        {
            "schema_version": "todo_item_v0",
            "todo_id": f"todo_{index:03}",
            "role": "agent",
            "status": "open",
            "done": False,
            "text": f"Retained work {index}",
            "note": "完整🙂" * 1600,
            "archive_state": "active",
            "source_section": "Agent Todo",
            "index": index + 1,
            "task_class": "advancement_task",
        }
        for index in range(160)
    ]
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=records, leases=[], handoff_mode="soft_claim"
    )
    assert len(json.dumps(projection, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES
    initialize_canonical_authority(
        runtime, "goal-a", projection, state_path=state, provider=request.param
    )
    return runtime, state, registry, projection


def native_read(runtime):
    return local_authority.read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id="goal-a", include_leases=True
    )


def test_historical_source_uses_private_snapshot_above_inline_budget(wide_goal):
    runtime, _state, _registry, projection = wide_goal
    original = native_read(runtime)
    request = {
        "schema_version": "loopx_local_coordination_todo_source_request_v0",
        "runtime_root": str(runtime),
        "goal_id": "goal-a",
        "source": {
            key: original[key]
            for key in (
                "source_authority", "store_identity", "provider_revision", "cursor"
            )
        },
    }
    with pytest.raises(EffectRuntimeResponseAmbiguous):
        effect_runtime_result("coordination.local_authority.todo_source", request)
    historical = effect_runtime_result(
        "coordination.local_authority.todo_source", request,
        timeout=15, large_local_snapshot=True,
    )
    assert historical["status"] == "loaded"
    assert historical["todos"] == projection["todos"]
    assert historical["source"] == request["source"]
    assert len(json.dumps(historical, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES


def cli(registry, *args):
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return json.loads(process.stdout)


def test_real_rpc_keeps_budget_and_cli_recovers_complete_display(
    wide_goal, monkeypatch
):
    runtime, state, registry, projection = wide_goal
    runtime_pid = effect_runtime_result("runtime.ping", {})["pid"]
    # Same stored workload: old one-shot endpoint really crosses the fixed budget.
    with pytest.raises(EffectRuntimeResponseAmbiguous) as oversized:
        effect_runtime_result(
            "coordination.local_authority.todo_list",
            {
                "schema_version": "loopx_local_coordination_todo_list_request_v0",
                "runtime_root": str(runtime),
                "goal_id": "goal-a",
                "include_leases": True,
            },
            timeout=15,
        )
    # The server caps a post-dispatch response by closing the connection. The
    # caller cannot classify that lost response as a safe rejection or retry.
    assert oversized.value.diagnostic_code == "runtime_response_ambiguous"
    measured = []

    def capture(method, payload, **kwargs):
        result = effect_runtime_result(method, payload, **kwargs)
        measured.append(
            len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())
        )
        return result

    with monkeypatch.context() as patch:
        patch.setattr(local_authority, "effect_runtime_result", capture)
        result = native_read(runtime)
    assert len(measured) > 1 and max(measured) <= 1792 * 1024
    assert result["todos"] == projection["todos"]
    # A rejected oversized read may close a socket with buffered response bytes.
    # It must not take down the shared runtime used by the paged successor.
    assert effect_runtime_result("runtime.ping", {})["pid"] == runtime_pid
    before_revision = result["provider_revision"]
    listed = cli(registry, "todo", "list", "--goal-id", "goal-a", "--role", "agent")
    assert len(listed["todos"]) == 160
    assert {row["todo_id"] for row in listed["todos"]} == {
        row["todo_id"] for row in projection["todos"]
    }
    delivered = provider_projection.project_current_canonical_todos(
        registry_path=registry, runtime_root=runtime, goal_id="goal-a"
    )
    assert delivered["status"] in {"delivered", "current"}
    rendered = state.read_text()
    assert "Human narrative survives." in rendered
    for record in projection["todos"]:
        assert f"todo_id={record['todo_id']} " in rendered
    assert native_read(runtime)["provider_revision"] == before_revision
    # Missing Markdown must not prevent the canonical CLI read.
    state.unlink()
    assert (
        len(
            cli(registry, "todo", "list", "--goal-id", "goal-a", "--role", "agent")[
                "todos"
            ]
        )
        == 160
    )


def test_real_overlapping_commit_has_no_partial_or_legacy_success(
    wide_goal, monkeypatch
):
    runtime, state, _, _ = wide_goal
    original_display = state.read_bytes()
    calls = []

    def overlap(method, payload, **kwargs):
        result = effect_runtime_result(method, payload, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            # A real writer wins between pages, with unchanged Todo data. Revision still matters.
            script = """import {openLocalAuthorityStore} from './loopx/control_plane/coordination/local_authority_provider.ts';
const store=await openLocalAuthorityStore(process.argv[1],'goal-a');const h=await store.loadAuthority();
const r=await store.commitAuthority({expected_provider_revision:h.provider_revision,operation_id:'between-pages',
next_projection:h.head,events:[],receipts:[]});if(r.status!=='applied')throw new Error(JSON.stringify(r));"""
            subprocess.run(
                [
                    "node",
                    "--no-warnings",
                    "--experimental-strip-types",
                    "--input-type=module",
                    "-e",
                    script,
                    str(runtime),
                ],
                cwd=REPO,
                check=True,
                capture_output=True,
                timeout=45,
            )
        return result

    with monkeypatch.context() as patch:
        patch.setattr(local_authority, "effect_runtime_result", overlap)
        with pytest.raises(LocalCoordinationAuthorityUnavailable) as error:
            native_read(runtime)
    assert error.value.code == "canonical_snapshot_changed"
    assert "todos" not in error.value.payload
    assert len(calls) == 2
    assert state.read_bytes() == original_display
    fresh = native_read(runtime)
    assert len(fresh["todos"]) == 160
    assert fresh["provider_revision"] != calls[0]["snapshot"]["provider_revision"]
