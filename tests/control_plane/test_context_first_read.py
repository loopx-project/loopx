"""Initial decision reading and progressive recovery share the original capture."""
from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from loopx.cli_commands.quota_capture import native_context_capture_view, read_context_capture
from loopx.cli_commands.quota_capture import handle_native_context_command
from loopx.control_plane.turn_driver import codex_context
from loopx.control_plane.turn_driver.decision_capture import capture_page_range


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
    return page, {item["pointer"]: item["value"] for item in json.loads(page["content"])["selections"]}


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


def test_unknown_settlement_plan_is_not_compacted(tmp_path, decision):
    plan = {"schema_version": "future", "new_stop": "Stop before every new effect."}
    decision["interaction_contract"]["cli_channel"]["settlement_plan"] = plan
    view = native_context_capture_view(decision, tmp_path)
    _, values = read_selection(view["first_read_command"])
    assert values["/interaction_contract/cli_channel/settlement_plan"] == plan


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


def test_warm_delta_keeps_its_existing_reader_and_current_recovery(tmp_path, monkeypatch, decision):
    tmp_path.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture-session")
    codex_context.codex_context_hook(str(tmp_path), {
        "session_id": "fixture-session", "cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "startup"})
    for name, marker in [("first", "before"), ("second", "after")]:
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        decision["agent_lane_next_action"]["next_action_basis"] = marker
        wire = codex_context.project_codex_context(decision, directory=str(tmp_path), capture_directory=directory,
            goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id=name)
        view = native_context_capture_view(wire, directory, context_directory=str(tmp_path))
    assert view["mode"] == "delta" and "first_read_command" not in view
    current = json.loads(Path(view["recovery"]["path"]).read_bytes())
    assert current["agent_lane_next_action"]["next_action_basis"] == "after"
    assert view["sha256"] != view["recovery"]["sha256"]
