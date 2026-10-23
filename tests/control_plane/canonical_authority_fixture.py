"""Initialize an already canonical provider for its independent consumer tests.

This fixture runs the real FileAuthorityStore in a Node process. It deliberately
does not claim that a shadow qualification can promote canonical authority.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import subprocess
import tempfile


def assert_todo_read_unchanged(current, previous):
    """Compare complete readback except validated evaluator/observation clocks."""
    def stable(payload):
        payload = without_resume_observation_time(payload)
        envelope = payload["projection_envelope"]
        assert envelope["schema_version"] == "loopx_projection_envelope_v0"
        for key in ("observed_at", "served_at"):
            assert datetime.fromisoformat(envelope.pop(key).replace("Z", "+00:00")).tzinfo is not None
        for source in envelope["sources"]:
            assert datetime.fromisoformat(source.pop("last_read_at").replace("Z", "+00:00")).tzinfo is not None
        return payload

    assert stable(current) == stable(previous)


def empty_todo_display(state_path: Path) -> None:
    """Remove display work without deleting Goal intent, acceptance or stops.

    Quota admission still requires those Goal sections. Empty Todo sections
    independently prove that the real provider owns work selection; a missing
    entire Goal is a different, deliberately fail-closed health case.
    """
    from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections

    state_path.write_text(render_canonical_todo_sections(
        state_path.read_text(), [], provider_revision="synthetic-empty-display",
    ).markdown)


def without_resume_observation_time(value):
    """Compare all readback facts except the typed evaluator's observation time."""
    if isinstance(value, list):
        return [without_resume_observation_time(item) for item in value]
    if not isinstance(value, dict):
        return value
    is_resume = value.get("schema_version") == "todo_resume_condition_v0"
    if is_resume and "evaluated_at" in value:
        assert datetime.fromisoformat(value["evaluated_at"].replace("Z", "+00:00")).tzinfo is not None
    return {key: without_resume_observation_time(item) for key, item in value.items()
            if not (is_resume and key == "evaluated_at")}


def initialize_canonical_authority(runtime_root: Path, goal_id: str, projection: dict, *, state_path: Path, provider: str = "file") -> dict:
    repository = Path(__file__).resolve().parents[2]
    module = repository / "loopx/control_plane/coordination/file_authority_store.ts"
    fence_module = repository / "loopx/control_plane/coordination/legacy_writer_fence.ts"
    codec_module = repository / "loopx/control_plane/coordination/authority_store_codec.ts"
    script = (
        f"import {{FileAuthorityStore}} from {json.dumps(module.as_uri())};"
        f"import {{selectLocalSqliteAuthority,openLocalAuthorityStore}} from {json.dumps((module.parent / 'local_authority_provider.ts').as_uri())};"
        f"import {{engageLegacyCoordinationWriterFence}} from {json.dumps(fence_module.as_uri())};"
        f"import {{canonicalAuthoritySha256}} from {json.dumps(codec_module.as_uri())};"
        "import {join} from 'node:path';let input='';for await(const chunk of process.stdin)input+=chunk;"
        "const request=JSON.parse(input);"
        "if(request.provider==='sqlite')await selectLocalSqliteAuthority(request.root,request.goal,true);"
        "const store=await openLocalAuthorityStore(request.root,request.goal);"
        "const result=await store.commitAuthority({expected_provider_revision:null,operation_id:'canonical-fixture',"
        "events:[],next_projection:request.projection,receipts:[]});"
        "const fence=await engageLegacyCoordinationWriterFence({schema_version:'loopx_legacy_coordination_writer_fence_engage_request_v0',"
        "runtime_root:request.root,goal_id:request.goal,state_path:request.state_path,fence:{schema_version:'loopx_legacy_coordination_writer_fence_v0',"
        "state:'engaged',goal_id:request.goal,fence_id:'canonical-fixture',source_version:'canonical-fixture',"
        "source_projection_sha256:canonicalAuthoritySha256(request.projection),expected_shadow_provider_revision:result.provider_revision}});"
        "if(fence.status!=='applied')throw new Error(JSON.stringify(fence));process.stdout.write(JSON.stringify(result));"
    )
    process = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps({"root": str(runtime_root), "goal": goal_id, "projection": projection, "state_path": str(state_path), "provider": provider}),
        capture_output=True, text=True, check=False, timeout=45)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["status"] == "applied", result
    return result


def isolate_sqlite_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite")
    # Do not reuse an Effect runtime started by the minimum-Node CI step.
    # CLI subprocesses resolve their root from the environment; the current
    # process may already have cached a different root in tempfile.tempdir.
    # Patch both for the same lifetime, restoring the cache at fixture teardown.
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def single_snapshot_page(result: dict, goal_id: str = "goal-a") -> dict:
    """Encode small provider read fixtures as one complete transport page."""
    return {
        "schema_version": "loopx_canonical_snapshot_page_result_v0", "status": "page",
        "snapshot": {"goal_id": goal_id, "store_identity": "fixture-store",
                     "provider_revision": result["provider_revision"], "cursor": result["cursor"],
                     "query_sha256": "fixture-query", "todo_count": len(result["todos"]),
                     "lease_count": len(result.get("leases", []))},
        "metadata": {key: result[key] for key in (
            "todo_read_model", "goal_acceptance_contract", "handoff_mode", "projection_readback"
        ) if key in result},
        **{key: result[key] for key in (
            "todos", "leases", "goal_acceptance_work_guards", "source_authority",
            "decision_read_from_provider", "legacy_fallback_used"
        ) if key in result},
        "next": None,
    }


def promoted_create_fixture(
    tmp_path: Path,
    *,
    provider: str = "file",
    goal_id: str = "goal-a",
    handoff_mode: str = "soft_claim",
    registered_agents: list[str] | None = None,
) -> tuple[Path, Path, Path]:
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.coordination.coordination_state_contract import (
        TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION, TODO_DOMAIN_RECORD_FIELDS,
    )

    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n## Completed Work Archive\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": goal_id,
                        "repo": str(project),
                        "state_file": f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": (
                                registered_agents
                                if registered_agents is not None
                                else ["agent-a"]
                            )
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id=goal_id, todos=[], handoff_mode=handoff_mode
    )
    projection["todo_read_model"] = {
        **projection["todo_read_model"],
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
    }
    initialize_canonical_authority(
        runtime_root, goal_id, projection, state_path=state_file, provider=provider
    )
    return registry_path, runtime_root, state_file
