"""Full-source frontier replay on the shared complex fixture and real file store."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from test_goal_amendment_proposal import _write_fixture, GOAL_ID
from loopx.control_plane.goals.goal_frontier.long_todo_chain import evaluate_long_todo_chain
from loopx.control_plane.goals.shared_goal_alignment import project_shared_goal_alignment
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.control_plane.work_items.progress_observation import semantic_delta_from_writeback
from loopx.status import active_state_todo_fields


def _fixture():
    module = (Path(__file__).resolve().parents[2] /
              "tests/control_plane_ts/production_scale_coordination_fixture.ts").as_uri()
    process = subprocess.run([
        "node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
        f"import {{productionScaleCoordinationFixture}} from {json.dumps(module)};"
        "process.stdout.write(JSON.stringify(productionScaleCoordinationFixture(process.argv[1])));", GOAL_ID,
    ], check=True, capture_output=True, text=True, timeout=30)
    return json.loads(process.stdout)["projection"]


def _commit_variant(paths, operation, todo_id):
    module = (Path(__file__).resolve().parents[2] / "loopx/control_plane/coordination")
    script = (
        f"import {{FileAuthorityStore}} from {json.dumps((module / 'file_authority_store.ts').as_uri())};"
        f"import {{canonicalAuthoritySha256}} from {json.dumps((module / 'authority_store_codec.ts').as_uri())};"
        "const s=new FileAuthorityStore(process.argv[1],process.argv[2]);const h=await s.loadAuthority();"
        "const t=h.head.todos.find(t=>t.todo_id===process.argv[4]);"
        "if(process.argv[3]==='remove-exclusion')delete t.excluded_agents;else t.priority='P0';"
        "h.head.todo_read_model.records_sha256=canonicalAuthoritySha256(h.head.todos);"
        "const r=await s.commitAuthority({expected_provider_revision:h.provider_revision,"
        "operation_id:process.argv[3],events:[],receipts:[],next_projection:h.head});"
        "if(r.status!=='applied')throw Error(JSON.stringify(r));"
    )
    subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
                    script, str(paths["runtime"] / "authority/file-v0"), GOAL_ID, operation, todo_id],
                   check=True, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("display", ["stale", "missing"])
@pytest.mark.parametrize("ack_scope", ["revision_only", "owned"])
def test_complex_canonical_frontier_ack_tracks_only_selectable_material_changes(tmp_path, display, ack_scope):
    paths = _write_fixture(tmp_path)
    projection = _fixture()
    # The shared fixture intentionally includes incomplete historical timestamps.
    # This variant exercises a complete checkpoint without changing that fixture's contract.
    for record in projection["todos"]:
        record.setdefault("updated_at", "2026-09-01T00:00:00.000001Z")
    for index in range(30):
        projection["todos"].append({
            "schema_version": "todo_item_v0", "todo_id": f"todo_zz_frontier_{index:03}",
            "role": "agent", "status": "open", "done": False, "task_class": "advancement_task",
            "text": "Synthetic independent work", "archive_state": "active", "source_section": "Agent Todo",
            "index": len(projection["todos"]) + 1, "updated_at": "2026-09-01T00:00:00.000002Z",
            # Reach the lane-owned threshold; retain a shared tail for ACK parity.
            **({"claimed_by": "agent-a"} if index < 15 else {}),
            **({"excluded_agents": ["agent-a"]} if index == 29 else {}),
        })
    from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
    from hashlib import sha256
    projection["todo_read_model"].update(todo_count=len(projection["todos"]),
        records_sha256=sha256(canonical_bytes(projection["todos"])).hexdigest())
    initialize_canonical_authority(paths["runtime"], GOAL_ID, projection, state_path=paths["state_file"])
    if display == "missing":
        paths["state_file"].unlink()
    before = paths["state_file"].read_bytes() if paths["state_file"].exists() else None
    goal = json.loads(paths["registry"].read_text())["goals"][0]

    def observe(ack=None):
        summary = active_state_todo_fields(goal, runtime_root=paths["runtime"])["agent_todos"]
        # The material edit starts outside the hot-path executable backlog.
        if ack is None:
            assert not any(item.get("todo_id") == "todo_zz_frontier_028"
                           for item in summary["executable_backlog_items"])
        alignment = project_shared_goal_alignment(goal_id=GOAL_ID, agent_id="agent-a", project=paths["project"])
        return evaluate_long_todo_chain(agent_todo_summary=summary, agent_counts={},
            frontier_counts=alignment["frontier_counts"], agent_id="agent-a", latest_replan_ack=ack)

    initial, _ = observe()
    assert initial is not None and initial.frontier_revision_complete
    ack = {"recorded": True, "semantic_delta": {"accepted": True,
        "obligation_id": "replan-0123456789abcdef", "trigger_kinds": ["long_todo_chain"],
        "trigger_checkpoints": [{"kind": "long_todo_chain", "frontier_revision": initial.frontier_revision}]}}
    if ack_scope == "owned":
        ack["semantic_delta"] = semantic_delta_from_writeback(obligation={
            "obligation_id": "replan-0123456789abcdef", "triggers": [initial.trigger],
        }, progress_observation={"result_class": "advanced", "surface_id": "canonical-frontier",
            "evidence_ids": ["evidence:source-readback"]})
        assert ack["semantic_delta"]["accepted"] and initial.frontier_owned_identity
    assert observe(ack)[1].acknowledged
    _commit_variant(paths, "excluded-edit", "todo_zz_frontier_029")
    assert observe(ack)[1].acknowledged
    _commit_variant(paths, "eligible-edit", "todo_zz_frontier_028")
    changed, decision = observe(ack)
    assert decision.acknowledged is (ack_scope == "owned")
    assert decision.rearmed_after_obligation_id == (None if ack_scope == "owned" else "replan-0123456789abcdef")
    assert changed.frontier_revision != initial.frontier_revision
    next_ack = deepcopy(ack)
    next_ack["semantic_delta"]["trigger_checkpoints"][0]["frontier_revision"] = changed.frontier_revision
    assert observe(next_ack)[1].acknowledged
    _commit_variant(paths, "remove-exclusion", "todo_zz_frontier_029")
    assert observe(next_ack)[1].acknowledged is (ack_scope == "owned")
    _commit_variant(paths, "owned-edit", "todo_zz_frontier_000")
    assert not observe(next_ack)[1].acknowledged
    code, result = run_json_cli_result("quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", "agent-a", registry_path=paths["registry"])
    assert code == 0, result
    assert (paths["state_file"].read_bytes() if paths["state_file"].exists() else None) == before
