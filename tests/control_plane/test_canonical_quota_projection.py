"""Canonical provider-to-quota reads retain complete input and request fences."""
from copy import deepcopy

import pytest

from loopx.control_plane.effect_runtime import effect_runtime_request_scope
from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable, read_canonical_todos_if_promoted
from loopx.control_plane.todos.goal_todo_projection import project_todo_read, todo_summaries_from_fields
from loopx.control_plane.todos.quota_selection import project_quota_planning
from loopx.control_plane.todos.quota_summary import select_quota_todo_contexts
from loopx.control_plane.todos.summary_item import canonical_todo_source_reference
from loopx.control_plane.todos.list_readback import list_goal_todos
from tests.control_plane import test_quota_settlement_cli as fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_provider_projection_uses_complete_source_differences_and_scope_exit(tmp_path, provider):
    project, runtime, registry = fixture._write_fixture(tmp_path, claimed=False)
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    with effect_runtime_request_scope():
        canonical = read_canonical_todos_if_promoted(runtime_root=runtime,
            goal_id=fixture.GOAL_ID, include_summary=True)
        fields = canonical["_todo_fields"]
        source = fields["agent_todos"]
        reference = canonical_todo_source_reference(source)
        assert reference is not None, "the real native read must supply a validated reference"
        assert reference["changes"] == []
        copied = deepcopy(source)
        proof = {"agent_id": fixture.AGENT_ID, "state": "waiting", "evidence": {"revision": 9007199254740993, "ratio": -0.0}}
        copied["vision_wait_states"] = [proof]
        request = project_quota_planning(copied, all_open_items=[], source_open_count=1,
            agent_identity={"agent_id": fixture.AGENT_ID}, filter_user_gate_blocks_agent=False,
            available_capabilities=[], resolve_capacity=True, summary_kind="user", request_only=True)
        assert "source" not in request["summary_request"]
        assert "source_rows" not in request["summary_request"]
        assert request["resume"] is None
        assert request["summary_request"]["source_reference"]["changes"]
        user, agent, views, user_rows = select_quota_todo_contexts(fields["user_todos"], None, copied, None,
            agent_identity={"agent_id": fixture.AGENT_ID}, available_capabilities=[])
        assert user is not None and agent is not None
        assert views["terminal"][0]["text"] == copied["items"][0]["text"]
        assert agent["vision_wait_states"][0] is proof
        assert user_rows == []
        assert canonical_todo_source_reference(deepcopy(copied)) is not None
        invalid = deepcopy(request)
        invalid["summary_request"]["source_reference"]["expected_source"]["cursor"] = "foreign"
        with pytest.raises(ValueError, match="canonical source changed"):
            project_todo_read({"kind": "quota_projection", "requests": [invalid]})
        invalid = deepcopy(request)
        invalid["summary_request"]["source_reference"]["field_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="original complete read"):
            project_todo_read({"kind": "quota_projection", "requests": [invalid]})
        invalid = deepcopy(request)
        invalid["summary_request"]["source_reference"]["changes"].append(
            {"path": ["items", 0, "required_write_scopes"], "value": ["foreign.json"]})
        with pytest.raises(ValueError, match="canonical Todo facts"):
            project_todo_read({"kind": "quota_projection", "requests": [invalid]})
        invalid = deepcopy(request)
        invalid["summary_request"]["source_reference"]["python_values"] = [
            [["items", 0, "claimed_by"], "forged-agent", "'forged-agent'", None]]
        with pytest.raises(ValueError, match="canonical Todo scalar facts"):
            project_todo_read({"kind": "quota_projection", "requests": [invalid]})
        transformed = deepcopy(source)
        transformed["items"][0]["note"] = "Explicit caller transformation"
        assert canonical_todo_source_reference(transformed) is None
        transformed = deepcopy(source)
        transformed["items"][0]["done"] = 0
        assert canonical_todo_source_reference(transformed) is None
        transformed = deepcopy(source)
        transformed["items"] = tuple(transformed["items"])
        assert canonical_todo_source_reference(transformed) is None
        selected = todo_summaries_from_fields(fields=fields, source=canonical["source_authority"],
            rollout_events=[], roles=["user", "agent"], status=None, todo_id=None,
            agent_id=fixture.AGENT_ID, limit=None)
        for role in ["user", "agent"]:
            selected_ref = canonical_todo_source_reference(selected.summaries[f"{role}_todos"])
            assert selected_ref is not None, "the production selector must retain native lineage"
            assert len(selected_ref["read"]["read_pipeline"]) == 1
        repeated = todo_summaries_from_fields(fields=selected.summaries, source=selected.source,
            rollout_events=[], roles=["user", "agent"], status=None, todo_id=None,
            agent_id=fixture.AGENT_ID, limit=None)
        assert len(canonical_todo_source_reference(repeated.summaries["agent_todos"])["read"]["read_pipeline"]) == 2
        _, selected_agent, _, _ = select_quota_todo_contexts(None, None, selected.summaries["agent_todos"], None,
            agent_identity={"agent_id": fixture.AGENT_ID}, available_capabilities=[])
        assert selected_agent is not None
        fresh = list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime), goal_id=fixture.GOAL_ID)
        for role in ["user", "agent"]:
            assert canonical_todo_source_reference(fresh[f"{role}_todos"]) is not None
        user_request = project_quota_planning(fresh["user_todos"], all_open_items=[], source_open_count=0,
            agent_identity={"agent_id": fixture.AGENT_ID}, filter_user_gate_blocks_agent=True,
            available_capabilities=[], summary_kind="user", request_only=True)
        invalid = deepcopy(user_request)
        added = [{"todo_id": "todo_supplied_intent", "role": "user", "status": "open", "done": False,
            "text": "Independently supplied action", "task_class": "user_action"}]
        invalid["summary_request"]["source_reference"]["changes"] = [
            {"path": ["active_next_action_items"], "value": added}]
        with pytest.raises(ValueError, match="canonical Todo facts"):
            project_todo_read({"kind": "quota_projection", "requests": [invalid]})
        transformed = deepcopy(fresh["user_todos"])
        transformed["active_next_action_items"] = added
        assert canonical_todo_source_reference(transformed) is None
        _, fresh_agent, _, _ = select_quota_todo_contexts(fresh["user_todos"], None, fresh["agent_todos"], None,
            agent_identity={"agent_id": fixture.AGENT_ID}, available_capabilities=[])
        assert fresh_agent is not None
    assert canonical_todo_source_reference(source) is None
    assert canonical_todo_source_reference(copied) is None
    after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=fixture.GOAL_ID)
    assert after["provider_revision"] == canonical["provider_revision"]
    assert after["todos"] == canonical["todos"], "source differences grant no provider write"


def test_supplied_tuple_source_retains_the_legacy_list_requirement():
    source = {"schema_version": "todo_summary_v0", "items": (
        {"todo_id": "todo_container", "text": "Required action", "status": "open", "done": False,
         "task_class": "advancement_task"},), "open_count": 1}
    with pytest.raises(ValueError, match="quota summary source lane missing"):
        project_quota_planning(source, all_open_items=[], source_open_count=1,
            agent_identity={"agent_id": "agent-a"}, filter_user_gate_blocks_agent=False,
            available_capabilities=[], summary_kind="user")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_quota_source_outage_preserves_the_canonical_read_diagnostics(tmp_path, provider):
    project, runtime, _ = fixture._write_fixture(tmp_path, claimed=False)
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    with effect_runtime_request_scope():
        canonical = read_canonical_todos_if_promoted(runtime_root=runtime,
            goal_id=fixture.GOAL_ID, include_summary=True)
        request = project_quota_planning(canonical["_todo_fields"]["agent_todos"],
            all_open_items=[], source_open_count=1, agent_identity={"agent_id": fixture.AGENT_ID},
            filter_user_gate_blocks_agent=False, available_capabilities=[],
            summary_kind="user", request_only=True)
        directory = runtime / "authority" / f"{provider}-v0"
        retained = directory.with_name(directory.name + "-retained")
        directory.rename(retained)
        try:
            with pytest.raises(LocalCoordinationAuthorityUnavailable) as original:
                read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=fixture.GOAL_ID)
            with pytest.raises(LocalCoordinationAuthorityUnavailable) as bundled:
                project_todo_read({"kind": "quota_projection", "requests": [request]})
            assert bundled.value.code == original.value.code
            assert bundled.value.payload == original.value.payload
        finally:
            retained.rename(directory)
