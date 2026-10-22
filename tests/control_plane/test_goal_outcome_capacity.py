"""Full declarations remain reviewable through real CLI and conversation paths."""
import json
from pathlib import Path
import subprocess

from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_goal_acceptance_prepared_action import conversation_check as conversation_check


def seed_crowded(project, actor):
    """Synthetic disposable authority only; exercise production readers/writers."""
    state = {"schema_version": "loopx_goal_outcomes_v0", "goal_id": "goal-acceptance",
        "goal_ref": None, "revision": 1, "outcomes": [
            {"id": f"result-{i}", "owner_agent_id": actor, "objective": f"Deliver result {i}",
             "revision": 1, "todo_ids": [],
             "requirements": [{"id": f"r{r}", "description": f"Preserve requirement {r}"} for r in range(5)],
             "sources": [{"ref": f"origin-{i}", "summary": "Owner requested this result"}]} for i in range(20)]}
    def size():
        return len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())
    i = 0
    while size() < 130400:
        state["outcomes"][i % 20]["sources"].append({"ref": f"source-{i}", "summary": "x" * 500})
        i += 1
    source = {"ref": "boundary", "summary": "x"}
    state["outcomes"][0]["sources"].append(source)
    available = 131000 - size()
    assert 0 <= available < 512
    source["summary"] += "x" * available
    assert size() == 131000
    repo = Path(__file__).resolve().parents[2]
    module = repo / "loopx/control_plane/coordination/local_authority_provider.ts"
    script = f"import {{openLocalAuthorityStore}} from {json.dumps(module.as_uri())};" + """
let input = ''; for await (const chunk of process.stdin) input += chunk;
const {root, state} = JSON.parse(input);
const store = await openLocalAuthorityStore(root, state.goal_id);
const prior = await store.loadAuthority();
if (prior.status !== 'loaded') throw new Error('fixture authority missing');
const result = await store.commitAuthority({operation_id: 'seed-capacity-fixture',
  expected_provider_revision: prior.provider_revision, events: [], receipts: [],
  next_projection: {...prior.head, goal_outcomes: state}});
if (result.status !== 'applied') throw new Error('fixture commit failed');
"""
    subprocess.run(["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
        "--input-type=module", "-e", script], input=json.dumps({"root": str(project.parent / "runtime"), "state": state}),
        text=True, capture_output=True, check=True, timeout=45)
    return {row["id"]: row["sources"] for row in state["outcomes"]}


def review_document():
    return {"outcome_id": "result-19", "review": [{"id": f"r{i}", "conclusion": "uncertain",
        "evidence_refs": ["artifact:inspection"], "note": "Independent verification remains pending."} for i in range(5)]}


def assert_reviewed(result, origins):
    rows = result["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert {row["id"]: row["sources"] for row in rows} == origins
    reviewed = next(row for row in rows if row["id"] == "result-19")
    assert all(r["agent_review"]["conclusion"] == "uncertain" and not r["planning_satisfied"] for r in reviewed["requirements"])
    assert all(r["status"] == "unverified" for r in reviewed["requirements"])


def test_crowded_cli_review_preserves_snapshot_and_retry(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    origins = seed_crowded(project, "agent-a")
    before = cli("inspect")[1]
    document.write_text(json.dumps(review_document()))
    args = ("refine", "--agent-id", "agent-a", "--document", str(document), "--operation-id", "review-capacity",
        "--expected-provider-revision", before["provider_revision"], "--execute")
    code, result = cli(*args)
    assert code == 0, result
    assert_reviewed(result, origins)
    current = cli("inspect")[1]
    assert_reviewed(current, origins)
    assert cli(*args)[1]["status"] == "replayed"
    assert cli("inspect")[1] == current


def test_crowded_native_review_uses_same_owner_and_preserves_stop(conversation_check):
    project, cli, controller, sid, _, _, adapter = conversation_check
    from loopx.chat_loopx_mode import TOOL
    origins = seed_crowded(project, "coordinator")
    handler = adapter.session.read_tool_handler
    before = handler(TOOL["name"], {"action": "outcomes"})
    request = {"action": "refine_outcome", "operation_id": "review-capacity-native",
        "expected_provider_revision": before["provider_revision"], "document": review_document()}
    result = handler(TOOL["name"], request)
    assert result["ok"], result
    assert_reviewed(result, origins)
    assert handler(TOOL["name"], request)["status"] == "replayed"
    current = cli("inspect")[1]
    assert_reviewed(current, origins)
    mode = controller.store.load_session(sid)["loopx_mode"]
    controller.store.update_session(sid, loopx_mode={**mode, "paused": True})
    assert handler(TOOL["name"], request)["error"] == "conversation_execution_inactive"
    assert cli("inspect")[1] == current
