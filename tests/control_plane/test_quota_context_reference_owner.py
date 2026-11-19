"""Shared context references preserve full instructions across existing entrypoints."""
from copy import deepcopy
import json

import pytest

from loopx.control_plane.quota.cli_projection import _reference_context
from loopx.control_plane.quota.turn_envelope import _turn_envelope_result, build_turn_envelope
from test_replan_host_context_projection import _quota_payload


def packet():
    source = _quota_payload()
    guidance = "Keep original requirements, lease ownership, negative evidence and stop conditions. " * 20
    source["interaction_contract"]["agent_channel"]["work_context"] = {"complete": True, "sources": [{
        "kind": "explore_turn_context", "source": "turn_start_capability_hook",
        "hook_id": "explore.turn_context", "capability_id": "explore",
        "content": {"graph": {"result_writeback_guidance": guidance,
            "writeback_results": [{"status": "refuted", "evidence": "fixture:negative"}]}}}]}
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = {
        "identity": {"turn_instance_id": "original-turn"}, "ordered_steps": [
            {"kind": "validation"}, {"kind": "durable_writeback", "optional_attachments": [
                {"capability_id": "explore", "guidance": guidance, "required": False}]}]}
    return source, guidance


def graph(source):
    return source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["graph"]


def attachment(source):
    return source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["optional_attachments"][0]

@pytest.mark.parametrize("captured", [False, True])
def test_shared_guidance_reaches_existing_readers_without_changing_settlement(captured):
    source, guidance = packet()
    if captured:
        source["replan_read_path"] = "/synthetic/decision.json"
    original = deepcopy(source)
    shared = _reference_context(source)
    assert source == original
    assert _reference_context(shared) == shared
    assert shared["interaction_contract"]["cli_channel"] == source["interaction_contract"]["cli_channel"]
    assert graph(shared)["writeback_results"] == graph(source)["writeback_results"]
    assert "result_writeback_guidance" not in graph(shared)
    envelope = build_turn_envelope(shared)
    assert envelope["schema_version"] == "loopx_turn_envelope_v0"
    assert envelope["work_context"]["sources"][0]["content"]["graph"] == graph(source)
    assert envelope["action_signature"]["matches"] is True
    assert graph(source)["result_writeback_guidance"] == guidance
    if captured:
        projected = _turn_envelope_result("replan_cli_view", payload=shared)
        assert graph(projected) == graph(source)
        # Use the existing source signature, also embedded in captured read
        # commands. Python's numeric spelling is not its canonical hash codec.
        digest = envelope["action_signature"]["source_decision_hash"]
        writer = _turn_envelope_result("replan_view", payload=shared, source_hash=digest, stage="writeback")
        assert writer["writeback"]["settlement_plan"] == source["interaction_contract"]["cli_channel"]["settlement_plan"]
        changed = deepcopy(shared)
        changed["reason"] += " Mutated capture."
        with pytest.raises(RuntimeError, match="matching source hash"):
            _turn_envelope_result("replan_view", payload=changed, source_hash=digest, stage="writeback")


@pytest.mark.parametrize("mutation", ["different", "wrong-step", "wrong-capability", "missing-owner", "short", "wrong-source"])
def test_nonidentical_or_unavailable_guidance_stays_inline(mutation):
    source, _ = packet()
    if mutation == "different":
        attachment(source)["guidance"] += " Extra stop."
    elif mutation == "wrong-step":
        source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["kind"] = "validation"
    elif mutation == "wrong-capability":
        attachment(source)["capability_id"] = "another"
    elif mutation == "missing-owner":
        attachment(source).pop("guidance")
    elif mutation == "short":
        attachment(source)["guidance"] = graph(source)["result_writeback_guidance"] = "Keep evidence."
    else:
        source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["kind"] = "other_context"
    assert graph(_reference_context(source)) == graph(source)


@pytest.mark.parametrize("mutation", ["dangling", "wrong-step", "wrong-capability", "chain", "both", "malformed"])
def test_invalid_reference_is_rejected_before_relocation(mutation):
    source, _ = packet()
    source["replan_read_path"] = "/synthetic/decision.json"
    shared = _reference_context(source)
    if mutation == "dangling":
        graph(shared)["result_writeback_guidance_ref"] = graph(shared)["result_writeback_guidance_ref"].replace("[1]", "[99]")
    elif mutation == "wrong-step":
        shared["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["kind"] = "validation"
    elif mutation == "wrong-capability":
        attachment(shared)["capability_id"] = "another"
    elif mutation == "chain":
        attachment(shared)["guidance_ref"] = "$.unknown"
    elif mutation == "both":
        graph(shared)["result_writeback_guidance"] = "Conflicting instruction."
    else:
        graph(shared)["result_writeback_guidance_ref"] = {"path": "$.unknown"}
    for read in (lambda: _reference_context(shared),
                 lambda: build_turn_envelope(shared),
                 lambda: _turn_envelope_result("replan_cli_view", payload=shared)):
        with pytest.raises(RuntimeError, match="Explore guidance reference"):
            read()


@pytest.mark.parametrize("body", ["🙂" * 90, "🙂" * 140, "完整要求\n" * 100])
def test_title_threshold_preserves_unicode_character_semantics(body):
    path = "$.interaction_contract.agent_channel.work_context.sources[0].content.todo.text"
    lane = {"todo_id": "task-one", "text": "Bounded label", "title": body}
    source = {"selected_todo": {"todo_id": "task-one"}, "agent_lane_next_action": lane,
        "interaction_contract": {"agent_channel": {"work_context": {
            "complete": True, "selected_todo_text_verified": True, "sources": [{
                "source": "selected_todo", "content": {"ok": True, "matched": True,
                    "todo": {"todo_id": "task-one", "status": "open", "text": body}}}]}}}}
    original = deepcopy(source)
    shared = _reference_context(source)["agent_lane_next_action"]
    if len(json.dumps(body, ensure_ascii=False)) > len(path) + 8:
        assert shared == {"todo_id": "task-one", "text": "Bounded label", "title_ref": path}
    else:
        assert shared == lane
    assert source == original


def test_distinct_guidance_owners_restore_each_original_source():
    source, first = packet()
    context = source["interaction_contract"]["agent_channel"]["work_context"]
    second = "Retain this separate capability evidence and its original stop conditions. " * 20
    another = deepcopy(context["sources"][0])
    another["content"]["graph"]["result_writeback_guidance"] = second
    context["sources"].append(another)
    steps = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
    steps[1]["optional_attachments"].append({"capability_id": "explore", "guidance": second})
    original = deepcopy(source)
    shared = _reference_context(source)
    assert source == original
    graphs = [item["content"]["graph"] for item in shared["interaction_contract"]["agent_channel"]["work_context"]["sources"]]
    assert graphs[0]["result_writeback_guidance_ref"].endswith("optional_attachments[0].guidance")
    assert graphs[1]["result_writeback_guidance_ref"].endswith("optional_attachments[1].guidance")
    restored = build_turn_envelope(shared)["work_context"]["sources"]
    assert [item["content"]["graph"]["result_writeback_guidance"] for item in restored] == [first, second]


@pytest.mark.parametrize("detail", ["none", "task", "vision", "both"])
def test_context_sharing_uses_one_boundary_and_keeps_explicit_detail(monkeypatch, detail):
    from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
    from loopx.control_plane.quota import turn_envelope
    source, guidance = packet()
    title = "Retain the complete task requirements and stop conditions. " * 30
    source["selected_todo"] = {"todo_id": "task-one"}
    source["agent_lane_next_action"] = {"todo_id": "task-one", "text": "Short label", "title": title}
    context = source["interaction_contract"]["agent_channel"]["work_context"]
    context["selected_todo_text_verified"] = True
    context["sources"].append({"source": "selected_todo", "content": {
        "ok": True, "matched": True, "todo": {"todo_id": "task-one", "text": title, "status": "open"}}})
    calls = []
    real = turn_envelope._turn_envelope_result
    def observed(operation, **params):
        calls.append(operation)
        return real(operation, **params)
    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", observed)
    task_detail, vision_detail = detail in {"task", "both"}, detail in {"vision", "both"}
    result = compact_quota_should_run_cli_payload(source,
        include_todo_summary_detail=task_detail, include_vision_detail=vision_detail)
    # Agent/vision detail leaves the User summary on its default compact path.
    # That formerly local Python work now shares this single typed request.
    assert calls == ["reference_context"]
    if task_detail:
        assert result["agent_lane_next_action"]["title"] == title
    else:
        assert result["agent_lane_next_action"]["title_ref"].endswith("sources[1].content.todo.text")
    if vision_detail:
        assert graph(result)["result_writeback_guidance"] == guidance
    else:
        assert graph(result)["result_writeback_guidance_ref"].endswith("optional_attachments[0].guidance")
    assert result["interaction_contract"]["agent_channel"]["work_context"]["sources"][1]["content"]["todo"]["text"] == title
    assert result["interaction_contract"]["cli_channel"] == source["interaction_contract"]["cli_channel"]


def test_all_explicit_details_need_no_diagnostic_transport(monkeypatch):
    from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
    from loopx.control_plane.quota import turn_envelope
    source, _ = packet()
    def unavailable(*args, **kwargs):
        raise AssertionError("explicit full summaries and guidance need no projection")
    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unavailable)
    result = compact_quota_should_run_cli_payload(source, include_todo_summary_detail=True,
        include_user_todo_summary_detail=True, include_vision_detail=True)
    assert result == source


def test_no_context_sources_needs_no_transport(monkeypatch):
    from loopx.control_plane.quota import turn_envelope
    def unavailable(*args, **kwargs):
        raise AssertionError("no context carrier requires no transport")
    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unavailable)
    for source in ({"ok": False, "diagnostic": "unavailable"},
                   {"interaction_contract": {"agent_channel": {"work_context": {"sources": []}}}}):
        assert _reference_context(source) is source


@pytest.mark.parametrize("value", [1.0, -0.0, 2**60 + 1, float(2**60), 0.000001])
@pytest.mark.parametrize("entrypoint", ["reference", "diagnostics", "cli-detail"])
def test_context_sharing_keeps_original_numeric_facts(value, entrypoint):
    from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
    source, _ = packet()
    facts = {"future_measurement": value, "nested": [value, False, None]}
    source["future_facts"] = deepcopy(facts)
    attachment(source)["future_facts"] = deepcopy(facts)
    original = json.dumps(source)

    def project(payload):
        if entrypoint == "cli-detail":
            return compact_quota_should_run_cli_payload(payload,
                include_todo_summary_detail=True, include_user_todo_summary_detail=True)
        return _reference_context(payload, **({"diagnostics": {}} if entrypoint == "diagnostics" else {}))

    shared = project(source)
    # JSON spelling distinguishes float/int, negative zero and adjacent large
    # integers; Python equality alone cannot establish that facts survived.
    assert json.dumps(shared["future_facts"]) == json.dumps(facts)
    assert json.dumps(attachment(shared)["future_facts"]) == json.dumps(facts)
    assert graph(shared)["result_writeback_guidance_ref"].endswith("optional_attachments[0].guidance")
    assert json.dumps(project(shared)) == json.dumps(shared)
    assert json.dumps(source) == original


def test_reference_transport_does_not_return_untouched_material(monkeypatch):
    from loopx.control_plane.quota import turn_envelope
    source, _ = packet()
    body = "Untouched supporting material. " * 2048
    source["future_facts"] = {"body": body}
    responses, real = [], turn_envelope._turn_envelope_result

    def observed(operation, **params):
        result = real(operation, **params)
        responses.append(json.dumps(result))
        return result

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", observed)
    shared = _reference_context(source)
    assert shared["future_facts"]["body"] == body
    assert len(responses) == 1
    assert body not in responses[0]
