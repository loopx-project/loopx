"""Initial decision reading and progressive recovery share the original capture."""
from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from loopx.cli_commands.quota_capture import model_context_reference, native_context_capture_view, read_context_capture
from loopx.cli_commands.quota_capture import handle_native_context_command
from loopx.control_plane.turn_driver import codex_context
from loopx.control_plane.turn_driver.decision_capture import capture_page_range
from tests.control_plane.codex_checkpoint_fixture import selected_context_values


@pytest.fixture
def decision():
    return {
        "ok": True,
        "goal_id": "fixture",
        "should_run": False,
        "normal_delivery_allowed": False,
        "workspace_repair_allowed": False,
        "execution_obligation": {"must_attempt_work": False},
        "goal_boundary": {"stop_condition": "No external write is authorized."},
        "selected_todo": {"todo_id": "todo_current", "text": "Compare the retained evidence."},
        "agent_lane_next_action": {"next_step": "Read the current criterion before judging completion.", "next_action_basis": "basis-current"},
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "user_channel": {"notify": "DONT_NOTIFY", "action_required": False},
            "agent_channel": {
                "required_reads": ["read the applicable criterion, including its final stop"],
                "work_context": {"instruction": "Sources remain mandatory when needed by the action.", "sources": [
                    {"source": "goal", "command": "read the accepted requirements", "content": {
                        "status": "unavailable", "revision": "current-revision", "recovery": "Restore this source before dependent work.",
                        "text": "完整目标；完整条件。" * 120 + "STOP at the original budget.",
                        "outcome_context": {"outcome_revision": 3},
                        "full_criteria": ["the independently accepted requirement"],
                    }},
                    {"source": "explore", "content": {"graph": {
                        "guidance": "Use existing evidence before repeating work.",
                        "writeback_results": [{"detail": "older-evidence-" * 20000}],
                    }}},
                    {"source": "selected_todo", "content": {"todo": {"note": "Preserve the original failed attempt."}}},
                ]},
            },
            "cli_channel": {"spend_allowed_now": False, "next_cli_actions": ["settle the original identity"],
                "settlement_plan": {"schema_version": "quota_settlement_plan_v1", "identity": {"turn_instance_id": "original"},
                    "ordered_steps": [{"kind": "durable_writeback", "precondition": "Validate first; preserve the original effect identity.",
                        "unknown_obligation": "This new obligation must remain visible.", "optional_attachments": [
                            {"required": False, "capability_id": "example", "option": "--example-json <file>",
                                "new_gate": "Preserve this future attachment constraint.", "guidance": "optional recipe " * 3000},
                            {"required": True, "guidance": "This attachment is required."},
                        ]}]},
            },
        },
    }


def read_selection(command):
    words = shlex.split(command)
    selectors = [words[index + 1] for index, word in enumerate(words) if word == "--select"]
    args = Namespace(capture_file=words[words.index("--capture-file") + 1],
        capture_sha256=words[words.index("--capture-sha256") + 1], format="json", select=selectors or None,
        initial_read="--initial-read" in words,
        offset=0, page_bytes=64000, max_page_output_bytes=64000)
    page = read_context_capture(args)
    assert page["ok"] and page["eof"]
    return page, selected_context_values(page["content"])


@pytest.mark.parametrize("output_limit", [8192, 64000])
def test_cli_initial_pages_prioritize_required_facts_and_keep_the_complete_index(tmp_path, decision, output_limit):
    goal = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]
    goal["text"] = "Complete current Goal; retain the final stop."
    decision["context_details"] = {"unread_context_index": {
        "coverage_confirmed": False, "pointers": [f"/history/{i}" for i in range(6000)]}}
    view = native_context_capture_view(decision, tmp_path)
    raw = Path(view["recovery"]["path"]).read_bytes()
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    base = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json"]

    def invoke(command):
        words = shlex.split(command)
        result = subprocess.run([*base, *words[words.index("quota"):]], cwd=tmp_path,
            capture_output=True, text=True, check=False)
        return result.returncode, json.loads(result.stdout)

    words = shlex.split(view["first_read_command"])
    for option in ("--page-bytes", "--max-page-output-bytes"):
        words[words.index(option) + 1] = str(output_limit)
    command = shlex.join(words)
    content, pages = b"", []
    while True:
        code, page = invoke(command)
        assert code == 0 and page["ok"] and page["offset"] == len(content), page
        assert len((json.dumps(page, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= output_limit
        assert capture_page_range(page, raw=raw, digest=view["recovery"]["sha256"]) is None
        pages.append(page)
        content += page["content"].encode()
        if page["eof"]:
            break
        command = page["next_command"]
    assert len(pages) > 1
    first = pages[0]["content"]
    for value in (decision["goal_boundary"]["stop_condition"], goal["text"],
            "This new obligation must remain visible.", "This attachment is required."):
        assert value in first
    rows = json.loads(content)["selections"]
    assert rows[-1] == {"pointer": "/context_details/unread_context_index",
        "value": decision["context_details"]["unread_context_index"]}
    values = selected_context_values(content)
    assert values["/goal_boundary"] == decision["goal_boundary"]
    assert values["/interaction_contract/agent_channel/work_context/sources/0/content/text"] == goal["text"]
    # The earlier canonical ordering is a different selection, never a cursor
    # that can be silently continued through a reordered view of this capture.
    legacy = json.dumps({"schema_version": "context_capture_selection_v1",
        "selections": sorted(rows, key=lambda row: row["pointer"])},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    digest = hashlib.sha256(legacy).hexdigest()
    assert digest != pages[0]["selection_sha256"]
    original = view["recovery"]["command"]
    code, rejected = invoke(original + " --initial-read --selection-sha256 " + digest
        + " --offset " + str(pages[0]["next_offset"]))
    assert code != 0 and not rejected["ok"] and "digest changed" in rejected["reason"]
    assert "content" not in rejected
    restored = b""
    command = original
    while True:
        code, page = invoke(command)
        assert code == 0 and page["offset"] == len(restored), page
        restored += page["content"].encode()
        if page["eof"]:
            break
        command = page["next_command"]
    assert restored == raw == Path(view["recovery"]["path"]).read_bytes()
    assert not registry.exists() and not runtime.exists()


def test_initial_read_coalesces_whole_contract_with_exact_values_and_unknown_obligations(tmp_path, decision):
    cli = decision["interaction_contract"]["cli_channel"]
    cli["settlement_plan"]["ordered_steps"] = [{
        "kind": "durable_writeback", "precondition": "Keep the original identity and final stop.",
        "unknown_obligation": {"allowed": False, "empty": [], "a/b~c": "Preserve this selected constraint."},
        "measurement": [2**60 + 1, 1.0, -0.0],
    }]
    view = native_context_capture_view(decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    assert values["/interaction_contract/cli_channel"] == cli
    pointers = [item["pointer"] for item in json.loads(page["content"])["selections"]]
    assert "/interaction_contract/cli_channel" in pointers
    assert not any(pointer.startswith("/interaction_contract/cli_channel/") for pointer in pointers)
    assert str(2**60 + 1) in page["content"] and "1.0,-0.0" in page["content"]
    # The complete source, including unrelated deferred evidence, is unchanged.
    raw = Path(view["recovery"]["path"]).read_bytes()
    assert json.loads(raw) == decision
    assert hashlib.sha256(raw).hexdigest() == page["sha256"]
    assert "older-evidence-" not in page["content"]


@pytest.mark.parametrize("complete_sources", [False, True])
def test_cli_current_goal_and_action_precede_complete_scoped_preferences(tmp_path, decision, complete_sources):
    sources = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    goal = sources[0]["content"]
    goal["text"] = "CURRENT GOAL: compare accepted outcomes; STOP at the original budget."
    preferences = {"source": "agent_preferences", "content": {
        "status": "loaded", "revision": "preference-current", "source_authority": "advisory_only",
        "current": {"items": ["complete scoped preference " * 2000], "future_constraint": False},
    }}
    sources.insert(0, preferences)
    if complete_sources:
        # Even when the entire array is selected, merging it back into its
        # original order must not move the large preference ahead of the Goal.
        goal.pop("full_criteria")
        sources[2:] = []
    channel = decision["interaction_contract"]["agent_channel"]
    channel.update(primary_action="Read the current review before writing back.",
        delivery_allowed=False, must_attempt=False, quiet_noop_allowed=False)
    view = native_context_capture_view(decision, tmp_path)
    raw = Path(view["recovery"]["path"]).read_bytes()
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    command = view["first_read_command"]
    content, pages = b"", []
    while True:
        words = shlex.split(command)
        for option in ("--page-bytes", "--max-page-output-bytes"):
            words[words.index(option) + 1] = "8192"
        result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *words[words.index("quota"):]],
            cwd=tmp_path, capture_output=True, text=True, check=False)
        page = json.loads(result.stdout)
        assert result.returncode == 0 and page["ok"] and page["offset"] == len(content), page
        assert len(result.stdout.encode()) <= 8192
        assert capture_page_range(page, raw=raw, digest=view["recovery"]["sha256"]) is None
        pages.append(page)
        content += page["content"].encode()
        if page["eof"]:
            break
        command = page["next_command"]
    first = pages[0]["content"]
    for value in (goal["text"], goal["status"], goal["revision"], goal["recovery"], channel["primary_action"]):
        assert value in first
    assert first.index(goal["text"]) < first.index("complete scoped preference")
    values = selected_context_values(content)
    assert values["/interaction_contract/agent_channel/work_context/sources/0"] == preferences
    assert values["/interaction_contract/agent_channel/primary_action"] == channel["primary_action"]
    assert values["/interaction_contract/agent_channel/delivery_allowed"] is False
    assert json.loads(raw) == decision and Path(view["recovery"]["path"]).read_bytes() == raw
    assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_advertised_native_reader_keeps_the_complete_current_contract_across_pages(tmp_path, monkeypatch, provider):
    from test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _heartbeat_receipt_count, _initialize_fixture_authority,
        _run_cli, _spend_run_count, _write_fixture,
    )
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original_state = state.read_bytes()
    turn_id = "native-first-read"
    code, view = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--context-delivery", "on", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", turn_id, cwd=project)
    assert code == 0 and view["mode"] == "full", view
    _, reference = read_context_read_reference(view["recovery"]["reference_file"])
    raw = Path(reference["path"]).read_bytes()
    source = json.loads(raw)
    words = shlex.split(view["first_read_command"])
    for option in ("--page-bytes", "--max-page-output-bytes"):
        words[words.index(option) + 1] = "4500"
    content = []
    for _ in range(20):
        code, page = _run_cli(registry, runtime, *words[words.index("quota"):], cwd=project)
        assert code == 0 and page["ok"], page
        assert len((json.dumps(page, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= 4500
        assert capture_page_range(page, raw=raw, digest=reference["sha256"]) is None
        content.append(page["content"])
        if page["eof"]:
            break
        words = shlex.split(page["next_command"])
    assert page["eof"] and len(content) > 1
    values = selected_context_values("".join(content))
    assert values["/interaction_contract/cli_channel"] == source["interaction_contract"]["cli_channel"]
    assert values["/heartbeat_receipt"] == source["heartbeat_receipt"]
    assert values["/selected_todo"] == source["selected_todo"]
    assert _heartbeat_receipt_count(runtime, turn_id) == 1 and _spend_run_count(runtime) == 0
    assert Path(reference["path"]).read_bytes() == raw and state.read_bytes() == original_state


def test_initial_read_keeps_current_constraints_whole_and_history_reachable(tmp_path, decision):
    before = json.loads(json.dumps(decision))
    view = native_context_capture_view(decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    assert values["/execution_obligation"] == {"must_attempt_work": False}
    assert values["/should_run"] is False and values["/normal_delivery_allowed"] is False
    assert values["/workspace_repair_allowed"] is False
    assert values["/goal_boundary"] == decision["goal_boundary"]
    assert values["/selected_todo"] == decision["selected_todo"]
    assert values["/interaction_contract/agent_channel/required_reads"] == decision["interaction_contract"]["agent_channel"]["required_reads"]
    assert values["/interaction_contract/cli_channel/spend_allowed_now"] is False
    goal = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    assert values[goal] == decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["text"]
    assert values[goal.removesuffix("text") + "status"] == "unavailable"
    assert values[goal.removesuffix("text") + "revision"] == "current-revision"
    assert values[goal.removesuffix("text") + "recovery"] == "Restore this source before dependent work."
    assert "writeback_results" not in page["content"]
    assert "selected_pointers" not in page and page["selection_sha256"]
    assert "first_read_command" in view and "overview" not in view and "field_index" not in view
    raw = Path(view["recovery"]["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == view["recovery"]["sha256"]
    assert json.loads(raw) == before == decision
    assert capture_page_range(page, raw=raw, digest=page["sha256"]) is None
    # Deferred criteria and history can still be selected, with the same SHA.
    for pointer in [goal.removesuffix("text") + "full_criteria", "/interaction_contract/agent_channel/work_context/sources/1/content/graph/writeback_results"]:
        recovery = read_context_capture(Namespace(capture_file=view["recovery"]["path"],
            capture_sha256=view["recovery"]["sha256"], format="json", select=[pointer], offset=0,
            page_bytes=64000, max_page_output_bytes=64000))
        assert recovery["ok"] and recovery["sha256"] == page["sha256"]


def test_initial_read_keeps_settlement_obligations_and_optional_routes(tmp_path, decision):
    view = native_context_capture_view(decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    step = "/interaction_contract/cli_channel/settlement_plan/ordered_steps/0/"
    assert values[step + "precondition"] == "Validate first; preserve the original effect identity."
    assert values[step + "unknown_obligation"] == "This new obligation must remain visible."
    assert values[step + "optional_attachments/0/required"] is False
    assert values[step + "optional_attachments/0/option"] == "--example-json <file>"
    assert values[step + "optional_attachments/0/new_gate"] == "Preserve this future attachment constraint."
    assert values[step + "optional_attachments/1"]["guidance"] == "This attachment is required."
    assert "optional recipe" not in page["content"]
    # Existing explicit EOF selections retain their wire contract.
    exact = read_context_capture(Namespace(capture_file=view["recovery"]["path"], capture_sha256=view["recovery"]["sha256"],
        format="json", select=["/goal_boundary"], offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert exact["selected_pointers"] == ["/goal_boundary"]


@pytest.mark.parametrize("unavailable", [False, True])
def test_initial_read_preserves_unfinished_requests_and_incomplete_observation(tmp_path, decision, unavailable):
    summary = {
        "count_unit": "request", "incoming_count": 2, "outgoing_count": 1,
        "items": [] if unavailable else [
            {"request_id": "incoming", "direction": "incoming", "decision": "adopt",
             "todo_ids": ["todo_finished"], "step": "review_request_work"},
            {"request_id": "outgoing", "direction": "outgoing", "step": "await_peer_result"},
            {"request_id": "return", "direction": "incoming", "step": "await_return_delivery",
             "returns": [{"status": "queued", "consumed": False}]},
        ],
        "omitted_count": 3 if unavailable else 0,
        "scan_complete": not unavailable, "unavailable_count": int(unavailable),
        "instruction": "A done Todo or a queued return does not complete the request.",
        "read_command": "read the full current request before acting",
    }
    sources = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    sources.append({"source": "turn_start_capability_hook", "kind": "operator_inbox",
        "content": {"ok": not unavailable, "read_complete": False, "unfinished_delegations": summary,
            "items": [{"brief": {"context": "FULL-REQUEST-DETAIL-ON-DEMAND"}}]}})
    before = json.loads(json.dumps(decision))
    view = native_context_capture_view(decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    prefix = "/interaction_contract/agent_channel/work_context/sources/3/content/"
    assert values[prefix + "unfinished_delegations"] == summary
    assert values[prefix + "read_complete"] is False
    assert values[prefix + "ok"] is not unavailable
    assert "FULL-REQUEST-DETAIL-ON-DEMAND" not in page["content"]
    # The starting view does not consume, complete or rewrite any request.
    assert decision == before == json.loads(Path(view["recovery"]["path"]).read_bytes())
    detail = read_context_capture(Namespace(capture_file=view["recovery"]["path"],
        capture_sha256=view["recovery"]["sha256"], format="json", select=[prefix + "items"],
        offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert detail["ok"] and "FULL-REQUEST-DETAIL-ON-DEMAND" in detail["content"]


def test_unknown_settlement_plan_is_not_compacted(tmp_path, decision):
    plan = {"schema_version": "future", "new_stop": "Stop before every new effect."}
    decision["interaction_contract"]["cli_channel"]["settlement_plan"] = plan
    view = native_context_capture_view(decision, tmp_path)
    _, values = read_selection(view["first_read_command"])
    assert values["/interaction_contract/cli_channel/settlement_plan"] == plan


@pytest.mark.parametrize("current", [None, {}, {
    "schema_version": "agent_preferences_v1", "authority": "advisory_only",
    "revision": "empty-current", "instructions": "Replace prior scoped context, including an empty view.",
    "items": [], "future_context": {"a/b~c": [None, False, 0, "Keep this complete current value."]},
}])
def test_initial_read_preserves_empty_and_unknown_current_preference_values(tmp_path, decision, current):
    source = {"source": "turn_start_capability_hook", "kind": "agent_preferences",
        "content": {"ok": current is not None, "status": "read" if current is not None else "unavailable",
            "current": current}}
    sources = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    sources.append(source)
    before = json.loads(json.dumps(decision))
    view = native_context_capture_view(decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    pointer = f"/interaction_contract/agent_channel/work_context/sources/{len(sources) - 1}/content/current"
    assert values[pointer] == current
    wire_pointers = [row["pointer"] for row in json.loads(page["content"])["selections"]]
    assert not any(key.startswith(pointer + "/") for key in wire_pointers)
    raw = Path(view["recovery"]["path"]).read_bytes()
    assert json.loads(raw) == before == decision
    assert capture_page_range(page, raw=raw, digest=page["sha256"]) is None


def test_initial_read_exposes_existing_outcome_addresses_without_loading_all_criteria(tmp_path, decision):
    pointer = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes/0"
    outcome = {"id": "current-work", "supporting_material_pointer": pointer,
        "requirements": [{"description": "Read this whole criterion before claiming success. Final stop."}]}
    decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["goal_acceptance_contract"] = {
        "outcome_requirements": {"outcomes": [outcome]}}
    view = native_context_capture_view(decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    assert values[pointer + "/id"] == "current-work"
    assert values[pointer + "/supporting_material_pointer"] == pointer
    assert "Read this whole criterion" not in page["content"]
    detail = read_context_capture(Namespace(capture_file=view["recovery"]["path"], capture_sha256=view["recovery"]["sha256"],
        format="json", select=[values[pointer + "/supporting_material_pointer"]], offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert json.loads(detail["content"])["selections"][0]["value"] == outcome


@pytest.fixture
def navigated_decision(decision):
    decision["agent_identity"] = {"agent_id": "author"}
    content = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]
    decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["source"] = "goal_acceptance"
    outcomes = [{"id": name, "owner_agent_id": owner, "revision": 1,
        "requirements": [{"id": "tail", "description": text,
            "future_required_fact": "Keep this unknown requirement field whole."}]}
        for name, owner, text in [
            ("current", "author", "Current work must retain the final cancellation condition."),
            ("dependency", "peer", "The peer dependency must be delivered before publication."),
            ("independent", "peer", "Read this other complete criterion when it becomes relevant.")]]
    content.update(status="loaded", ok=True, provider_revision="fixture-current", decision_read_from_provider=True,
        legacy_fallback_used=False,
        goal_acceptance_contract={"outcome_requirements": {"schema_version": "loopx_goal_outcomes_v0",
            "current_lifetime": True, "revision": 7, "outcomes": outcomes}},
        outcome_context={"schema_version": "outcome_context_navigation_v1", "goal_id": "fixture",
            "agent_id": "author", "todo_id": "todo_current", "outcome_revision": 7,
            "provider_revision": "fixture-current", "coverage_complete": True,
            "suggested_outcome_ids": ["current", "dependency"],
            "instruction": "Navigation is guidance; other applicable criteria still require reading."})
    return decision


def test_initial_read_uses_current_navigation_without_hiding_peer_constraints(tmp_path, navigated_decision):
    before = json.loads(json.dumps(navigated_decision))
    view = native_context_capture_view(navigated_decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    prefix = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes"
    outcomes = before["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert values[prefix + "/0"] == outcomes[0]
    assert values[prefix + "/1"] == outcomes[1]  # Shared work crosses Agent ownership.
    wire_pointers = [row["pointer"] for row in json.loads(page["content"])["selections"]]
    assert prefix + "/0/id" not in wire_pointers  # Do not repeat a delivered parent.
    assert values[prefix + "/2/id"] == "independent"
    assert outcomes[2]["requirements"][0]["description"] not in page["content"]
    detail = read_context_capture(Namespace(capture_file=view["recovery"]["path"],
        capture_sha256=view["recovery"]["sha256"], format="json", select=[prefix + "/2"],
        offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert json.loads(detail["content"])["selections"][0]["value"] == outcomes[2]
    assert json.loads(Path(view["recovery"]["path"]).read_bytes()) == before
    assert capture_page_range(page, raw=Path(view["recovery"]["path"]).read_bytes(), digest=page["sha256"]) is None


@pytest.mark.parametrize("field,value", [
    ("goal_id", "other-goal"), ("agent_id", "other-agent"), ("todo_id", "other-todo"),
    ("outcome_revision", 6), ("provider_revision", "older-source"),
    ("coverage_complete", False), ("schema_version", "future-navigation"),
    ("suggested_outcome_ids", ["current", "missing-outcome"]),
])
def test_initial_read_does_not_expand_unbound_navigation(tmp_path, navigated_decision, field, value):
    source = navigated_decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]
    source["content"]["outcome_context"][field] = value
    view = native_context_capture_view(navigated_decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    prefix = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes"
    assert values[prefix + "/0/id"] == "current" and prefix + "/0" not in values
    assert "Current work must retain the final cancellation condition." not in page["content"]
    assert values["/interaction_contract/agent_channel/work_context/sources/0/content/outcome_context"] == source["content"]["outcome_context"]


@pytest.mark.parametrize("failure", ["source_incomplete", "legacy_source", "old_lifetime", "ambiguous_outcome", "conflicting_agent"])
def test_initial_read_keeps_uncertain_sources_on_the_existing_detail_route(tmp_path, navigated_decision, failure):
    content = navigated_decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]
    declaration = content["goal_acceptance_contract"]["outcome_requirements"]
    if failure == "source_incomplete":
        content["read_complete"] = False
    elif failure == "legacy_source":
        content["legacy_fallback_used"] = True
    elif failure == "old_lifetime":
        declaration["current_lifetime"] = False
    elif failure == "ambiguous_outcome":
        declaration["outcomes"].append({"id": "current", "requirements": ["A different captured revision."]})
    else:
        navigated_decision["agent_id"] = "different-agent"
    view = native_context_capture_view(navigated_decision, tmp_path)
    page, values = read_selection(view["first_read_command"])
    prefix = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes"
    assert values[prefix + "/0/id"] == "current" and prefix + "/0" not in values
    assert "Current work must retain the final cancellation condition." not in page["content"]


def test_small_reference_budget_omits_the_whole_initial_command(tmp_path, decision):
    view = native_context_capture_view(decision, tmp_path, max_page_output_bytes=3000)
    assert "first_read_command" not in view
    assert len(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 3000
    assert Path(view["recovery"]["path"]).exists()


def test_initial_command_rejects_tampered_capture_without_reentering_admission(tmp_path, decision):
    view = native_context_capture_view(decision, tmp_path)
    capture = Path(view["recovery"]["path"])
    capture.write_text(json.dumps({**decision, "goal_boundary": {"stop_condition": "tampered"}}))
    words = shlex.split(view["first_read_command"])
    page = read_context_capture(Namespace(capture_file=str(capture),
        capture_sha256=words[words.index("--capture-sha256") + 1], format="json", select=["/goal_boundary"],
        offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert not page["ok"]
    assert "no guard, admission or spend" in page["recovery"]


def test_initial_read_pages_pin_the_exact_selection_for_continuation(tmp_path, decision):
    view = native_context_capture_view(decision, tmp_path)
    args = Namespace(capture_file=view["recovery"]["path"], capture_sha256=view["recovery"]["sha256"],
        format="json", initial_read=True, offset=0, page_bytes=256, max_page_output_bytes=24000)
    page = read_context_capture(args)
    assert page["ok"] and not page["eof"]
    assert "--selection-sha256" in page["next_command"] and "--initial-read" in page["next_command"]
    assert "--select " not in page["next_command"] and "selected_pointers" not in page
    args.selection_sha256 = page["selection_sha256"]
    args.offset = page["next_offset"]
    following = read_context_capture(args)
    assert following["ok"] and following["offset"] == 256
    assert following["selection_sha256"] == page["selection_sha256"]
    args.selection_sha256 = "0" * 64
    changed = read_context_capture(args)
    assert not changed["ok"] and "digest changed" in changed["reason"]


def test_small_initial_pages_progress_without_repeating_the_pointer_vector(tmp_path, decision):
    view = native_context_capture_view(decision, tmp_path, max_page_output_bytes=4500)
    assert "first_read_command" in view
    args = Namespace(capture_file=view["recovery"]["path"], capture_sha256=view["recovery"]["sha256"],
        format="json", initial_read=True, offset=0, page_bytes=4500, max_page_output_bytes=4500)
    content = []
    for _ in range(30):
        page = read_context_capture(args)
        assert page["ok"] and len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 4500
        content.append(page["content"])
        if page["eof"]:
            break
        words = shlex.split(page["next_command"])
        assert "--initial-read" in words and "--select" not in words
        args.offset = int(words[words.index("--offset") + 1])
        args.selection_sha256 = words[words.index("--selection-sha256") + 1]
    assert page["eof"]
    values = {item["pointer"]: item["value"] for item in json.loads("".join(content))["selections"]}
    assert values["/goal_boundary"] == decision["goal_boundary"]
    assert values["/interaction_contract/agent_channel/work_context/sources/0/content/text"].endswith("STOP at the original budget.")


@pytest.mark.parametrize("options", [{"select": ["/goal_boundary"]}, {"list_fields": True},
    {"offset": 256}, {"selection_sha256": "0" * 64}])
def test_initial_read_rejects_ambiguous_scope_or_continuation(tmp_path, decision, options):
    view = native_context_capture_view(decision, tmp_path)
    args = Namespace(**{"capture_file": view["recovery"]["path"], "capture_sha256": view["recovery"]["sha256"],
        "format": "json", "initial_read": True, "offset": 0, "page_bytes": 64000, "max_page_output_bytes": 64000, **options})
    failure = read_context_capture(args)
    assert not failure["ok"] and "no guard, admission or spend" in failure["recovery"]
    assert any(text in failure["reason"] for text in ["cannot be combined", "requires selection-sha256", "digest changed"])


def test_unknown_schema_keeps_generic_discovery_and_rejects_initial_read(tmp_path):
    view = native_context_capture_view({"ok": True, "interaction_contract": {"schema_version": "future"}}, tmp_path)
    assert "first_read_command" not in view and "index_command" in view
    failure = read_context_capture(Namespace(capture_file=view["recovery"]["path"],
        capture_sha256=view["recovery"]["sha256"], format="json", initial_read=True, offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert not failure["ok"] and "generic discovery" in failure["reason"]


def test_initial_read_is_rejected_before_quota_or_machine_access(tmp_path):
    output = []
    code = handle_native_context_command(Namespace(initial_read=True, quota_command="should-run", format="json"),
        print_payload=lambda value, *_args: output.append(value), registry_path=tmp_path / "unopened-registry.json")
    assert code == 1 and not output[0]["ok"] and "only supported by context-read" in output[0]["reason"]
    assert not (tmp_path / "unopened-registry.json").exists()


@pytest.mark.parametrize("opaque", [False, True])
def test_warm_delta_initial_read_uses_current_action_without_the_old_base(tmp_path, monkeypatch, decision, opaque):
    tmp_path.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture-session")
    codex_context.codex_context_hook(str(tmp_path), {
        "session_id": "fixture-session", "cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "startup"})
    for name, marker in [("first", "before"), ("second", "after")]:
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        decision["agent_lane_next_action"]["next_action_basis"] = marker
        decision["effective_action"] = "normal_run" if marker == "before" else "autonomous_replan_required"
        decision["interaction_contract"]["agent_channel"].update(
            primary_action=f"Read {marker} action.", delivery_allowed=marker == "before")
        wire = codex_context.project_codex_context(decision, directory=str(tmp_path), capture_directory=directory,
            goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id=name)
        view = native_context_capture_view(wire, directory, context_directory=str(tmp_path))
    assert view["mode"] == "delta" and "first_read_command" in view
    current = json.loads(Path(view["recovery"]["path"]).read_bytes())
    assert current["agent_lane_next_action"]["next_action_basis"] == "after"
    assert view["sha256"] != view["recovery"]["sha256"]
    baseline = Path(view["baseline_reference"]["path"])
    baseline.rename(baseline.with_suffix(".unavailable"))
    navigation = model_context_reference(view) if opaque else view
    words = shlex.split(navigation["first_read_command"])
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    command = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", *words[words.index("quota"):]]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    page = json.loads(result.stdout)
    assert result.returncode == 0 and page["ok"] and page["eof"], page
    values = selected_context_values(page["content"])
    assert values["/agent_lane_next_action/next_action_basis"] == "after"
    assert values["/effective_action"] == "autonomous_replan_required"
    assert values["/interaction_contract/agent_channel/primary_action"] == "Read after action."
    assert values["/interaction_contract/agent_channel/delivery_allowed"] is False
    if opaque:
        assert "sha256" not in page
    else:
        assert page["sha256"] == view["recovery"]["sha256"]
    assert "Read before action." not in page["content"]
    delta = read_context_capture(Namespace(capture_file=view["path"], capture_sha256=view["sha256"],
        format="json", offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert delta["ok"] and json.loads(delta["content"])["mode"] == "delta"
    Path(view["recovery"]["path"]).write_text("{}\n")
    failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    failure = json.loads(failed.stdout)
    assert failed.returncode != 0 and not failure["ok"] and "content" not in failure
    assert not registry.exists() and not runtime.exists()
