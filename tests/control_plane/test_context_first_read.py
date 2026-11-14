"""Initial decision reading and progressive recovery share the original capture."""
from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
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
    def option(name):
        return words[words.index(name) + 1] if name in words else None
    selectors = [words[index + 1] for index, word in enumerate(words) if word == "--select"]
    args = Namespace(capture_file=option("--capture-file"), capture_sha256=option("--capture-sha256"),
        reference_file=option("--reference-file"), format="json", select=selectors or None,
        initial_read="--initial-read" in words,
        offset=0, page_bytes=64000, max_page_output_bytes=64000)
    page = read_context_capture(args)
    assert page["ok"] and page["eof"]
    return page, selected_context_values(page["content"])


def test_complete_guard_protocol_is_explicit_and_saved_selection_retains_it(tmp_path):
    from loopx.cli_commands.quota_context_reader import bind_capture_selection_transport

    original = {"schema_version": "loopx_turn_envelope_v1", "interaction_contract": {"cli_channel": {
        "selection_command": {"command_args_template": "quota should-run --todo-id <todo-id>"}}}}
    legacy = deepcopy(original)
    bind_capture_selection_transport(legacy, Namespace(decision_output_root=str(tmp_path), complete_turn=False))
    command = legacy["interaction_contract"]["cli_channel"]["selection_command"]["command_args_template"]
    assert "--complete-turn" not in shlex.split(command)
    selected = deepcopy(original)
    bind_capture_selection_transport(selected, Namespace(decision_output_root=str(tmp_path), complete_turn=True))
    command = selected["interaction_contract"]["cli_channel"]["selection_command"]["command_args_template"]
    assert shlex.split(command).count("--complete-turn") == 1
    bind_capture_selection_transport(selected, Namespace(decision_output_root=str(tmp_path), complete_turn=False))
    recovered = selected["interaction_contract"]["cli_channel"]["selection_command"]["command_args_template"]
    assert shlex.split(recovered).count("--complete-turn") == 1
    assert "--complete-turn" not in original["interaction_contract"]["cli_channel"]["selection_command"]["command_args_template"]


def pinned_initial_view(payload, directory):
    base = native_context_capture_view(payload, directory)
    from loopx.control_plane.quota import turn_envelope

    plan = turn_envelope._turn_envelope_result("context_select", context=payload, initial_read=True)
    capture = {key: base["recovery"][key] for key in ("path", "sha256")}
    return native_context_capture_view(payload, directory, captured_recovery=base["recovery"],
        initial_read_plan={"initial": True, "plan": plan, "capture": capture})


def test_first_read_keeps_goal_turn_binding_ahead_of_work_context(tmp_path, decision):
    interaction = decision["interaction_contract"]
    action = "loopx quota should-run --runtime-profile codex_cli --turn-instance-id fixture-turn"
    interaction["agent_channel"].update(primary_action=action, must_attempt=True, delivery_allowed=False)
    interaction["cli_channel"] = {
        "turn_binding_required": True,
        "next_cli_actions": [action],
        "spend_after_validation": False,
    }

    view = pinned_initial_view(decision, tmp_path)
    page = read_context_capture(Namespace(
        capture_file=view["recovery"]["path"], capture_sha256=view["recovery"]["sha256"],
        format="json", select=None, initial_read=True, offset=0,
        page_bytes=64000, max_page_output_bytes=64000,
    ))
    assert page["ok"] and page["eof"]
    values = selected_context_values(page["content"])
    assert values["/interaction_contract/cli_channel/turn_binding_required"] is True
    assert values["/interaction_contract/cli_channel/next_cli_actions"] == [action]
    assert list(values).index("/interaction_contract/cli_channel/next_cli_actions") < list(values).index(
        "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    )


@pytest.mark.parametrize("required", [True, False])
def test_initial_read_delivers_explicit_selection_only_at_its_gate(tmp_path, decision, required):
    cli = decision["interaction_contract"]["cli_channel"]
    route = {"schema_version": "action_selection_cli_command_v1", "route_prefix": "loopx --format json",
             "command_args_template": "quota should-run --todo-id <todo-id> --turn-instance-id fixture-turn",
             "candidate_discovery_args": "todo list --goal-id fixture --status open"}
    policy = {"decision_owner": "agent", "suggestions_exhaustive": False}
    candidates = [{"todo_id": "another", "text": "Verify a candidate."}]
    cli.update(selection_required=required, next_cli_actions=[], selection_command=route,
               selection_policy_ref="$.action_portfolio.selection_policy")
    decision["action_portfolio"] = {"selection_policy": policy, "suggested_actions": candidates}
    source = tmp_path / "decision.json"
    source.write_bytes(json.dumps(decision).encode())
    command = shlex.join(["loopx", "quota", "context-read", "--capture-file", str(source),
                          "--capture-sha256", hashlib.sha256(source.read_bytes()).hexdigest(), "--initial-read"])
    _, values = read_selection(command)
    expected = {"/interaction_contract/cli_channel/selection_required": True,
                "/interaction_contract/cli_channel/selection_command": route,
                "/interaction_contract/cli_channel/selection_policy_ref": "$.action_portfolio.selection_policy",
                "/action_portfolio/selection_policy": policy, "/action_portfolio/suggested_actions": candidates}
    for pointer, value in expected.items():
        if required:
            assert values[pointer] == value
        else:
            assert pointer not in values


@pytest.mark.parametrize("scope_count", [1, 290])
def test_native_selection_opening_fits_before_candidate_scope(tmp_path, monkeypatch, decision, scope_count):
    from test_codex_context_lifecycle import setup
    from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference

    setup(tmp_path, monkeypatch)
    turn = "fixture-select-turn"
    decision.update(should_run=True, normal_delivery_allowed=True, effective_action="normal_run",
        agent_identity={"agent_id": "agent"}, execution_obligation={"must_attempt_work": True},
        heartbeat_receipt={"turn_instance_id": turn, "settlement_binding_owed": True})
    decision["selected_todo"]["required_write_scopes"] = [
        f"docs/fixture/area-{index:03d}/public-context-entrypoint/**" for index in range(scope_count)
    ]
    contract = decision["interaction_contract"]
    contract["agent_channel"].update(delivery_allowed=False, must_attempt=True)
    contract["cli_channel"] = {
        "selection_required": True, "next_cli_actions": [],
        "spend_allowed_now": False, "spend_after_validation": False,
        "selection_policy_ref": "$.action_portfolio.selection_policy",
        "selection_command": {"schema_version": "action_selection_cli_command_v1",
            "route_prefix": "loopx --format json",
            "command_args_template": f"quota should-run --todo-id '{{todo_id}}' --turn-instance-id {turn}",
            "candidate_discovery_args": "todo list --goal-id fixture --status open"},
    }
    decision["action_portfolio"] = {"selection_policy": {"mode": "explicit_turn_binding",
        "decision_owner": "agent", "suggestions_exhaustive": False, "requires_explicit_turn_binding": True},
        "suggested_actions": [{"todo_id": "todo_current"}, {"todo_id": "todo_alternative"}]}
    capture = tmp_path / "decision.json"
    raw = json.dumps(decision).encode()
    capture.write_bytes(raw)
    capture.chmod(0o600)
    registry = tmp_path / "absent-registry.json"
    runtime = tmp_path / "unused-runtime"

    def cli(*args):
        output = subprocess.check_output([sys.executable, "-m", "loopx.entrypoint",
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args])
        result = json.loads(output)
        assert result["ok"] and len(output) <= 12000
        return result

    def read(command):
        words = shlex.split(command)
        return cli(*words[words.index("quota"):])

    view = cli("quota", "context-project", "--codex-app", "--context-delivery", "on",
        "--goal-id", "fixture", "--agent-id", "agent", "--turn-instance-id", turn,
        "--capture-file", str(capture), "--capture-sha256", hashlib.sha256(raw).hexdigest(),
        "--decision-output-root", str(tmp_path / "captures"), "--codex-context-dir", str(tmp_path),
        "--max-page-output-bytes", "12000")
    page = read(view["first_read_command"])
    split = scope_count > 1
    assert ("remaining_read_command" in view) is split
    assert page["eof"]
    opening = selected_context_values(page["content"])
    _, reference = read_context_read_reference(view["recovery"]["reference_file"])
    saved = json.loads(Path(reference["path"]).read_bytes())
    saved_cli = saved["interaction_contract"]["cli_channel"]
    prefix = "/interaction_contract/cli_channel/"
    assert opening[prefix + "selection_required"] is True
    assert opening[prefix + "selection_command"] == saved_cli["selection_command"]
    assert opening[prefix + "next_cli_actions"] == []
    assert opening[prefix + "spend_allowed_now"] is False
    assert opening[prefix + "spend_after_validation"] is False
    assert opening["/interaction_contract/agent_channel/delivery_allowed"] is False
    assert opening["/interaction_contract/agent_channel/required_reads"] == contract["agent_channel"]["required_reads"]
    assert opening["/goal_boundary/stop_condition"] == decision["goal_boundary"]["stop_condition"]
    assert opening["/heartbeat_receipt/turn_instance_id"] == turn
    assert "/heartbeat_receipt/settlement_identity" not in opening
    if split:
        assert "/selected_todo" not in opening
        _, opening_ref = read_context_read_reference(page["reference_file"])
        body = bytearray()
        command = view["remaining_read_command"]
        while command:
            page = read(command)
            assert page["offset"] == len(body)
            body.extend(page["content"].encode())
            command = page.get("next_command")
        assert page["eof"] and page["total_bytes"] == len(body)
        _, remaining_ref = read_context_read_reference(page["reference_file"])
        assert opening_ref["sha256"] == remaining_ref["sha256"] == reference["sha256"]
        remaining = selected_context_values(body.decode())
        assert remaining["/selected_todo"] == decision["selected_todo"]
        assert remaining["/action_portfolio/suggested_actions"] == decision["action_portfolio"]["suggested_actions"]
    else:
        assert opening["/selected_todo"] == decision["selected_todo"]
    assert capture.read_bytes() == raw and not registry.exists() and not runtime.exists()


def test_initial_read_keeps_supporting_index_obligations_and_recovers_its_full_list(tmp_path, decision):
    # A complete address inventory is navigation, not the Goal/Task/stop text.
    # Exercise the native reader and exact cold path over the same capture.
    step = decision["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][0]
    step.pop("optional_attachments")
    evidence = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][1]["content"]["graph"]
    evidence["writeback_results"] = [{"detail": f"Original evidence {i}"} for i in range(900)]
    prefix = "/interaction_contract/agent_channel/work_context/sources/1/content/graph/writeback_results"
    pointers = [f"{prefix}/{i}/detail" for i in range(900)]
    source = tmp_path / "complete-source.json"
    source.write_text(json.dumps(decision), encoding="utf-8")
    index = {"coverage_confirmed": False, "read_before_dependent_work": True,
        "source": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}, "pointers": pointers}
    instruction = "Unread supporting fields remain unconfirmed. Read the applicable source before dependent work."
    decision["context_details"] = {"unread_context_index": index, "instruction": instruction}
    capture = tmp_path / "capture"
    capture.mkdir()
    view = native_context_capture_view(decision, capture)
    raw = Path(view["recovery"]["path"]).read_bytes()

    page, values = read_selection(view["first_read_command"])
    base = "/context_details/unread_context_index"
    assert values[base + "/coverage_confirmed"] is False
    assert values[base + "/read_before_dependent_work"] is True
    assert values[base + "/source"] == index["source"]
    assert values["/context_details/instruction"] == instruction
    assert not any(key == base or key.startswith(base + "/pointers") for key in values)
    assert values["/goal_boundary"] == decision["goal_boundary"]
    assert values["/selected_todo"] == decision["selected_todo"]
    goal = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    assert values[goal].endswith("STOP at the original budget.")
    assert values["/interaction_contract/cli_channel/settlement_plan/identity"]["turn_instance_id"] == "original"

    detail = read_context_capture(Namespace(capture_file=view["recovery"]["path"],
        capture_sha256=view["recovery"]["sha256"], format="json", select=[base + "/pointers"],
        offset=0, page_bytes=200000, max_page_output_bytes=200000))
    assert detail["ok"] and detail["eof"]
    assert json.loads(detail["content"])["selections"][0]["value"] == pointers
    assert Path(view["recovery"]["path"]).read_bytes() == raw
    assert json.loads(raw) == decision






@pytest.mark.parametrize("damage", [None, "foreign_plan", "changed_file"])
def test_native_preparation_reuses_only_its_current_digest_bound_navigation(
    tmp_path, monkeypatch, decision, damage,
):
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError
    from tests.control_plane.test_codex_context_lifecycle import setup

    setup(tmp_path, monkeypatch)
    decision["selected_todo"]["future_number"] = 10**45 + 17
    capture = tmp_path / "current"
    capture.mkdir(mode=0o700)
    prepared = codex_context.prepare_codex_context(decision, directory=str(tmp_path), capture_directory=capture,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="current")
    assert prepared.initial_read_plan is not None
    source = Path(prepared.delivery["recovery"]["path"])
    raw = source.read_bytes()
    journal = (tmp_path / "session.json").read_bytes()
    if damage == "foreign_plan":
        prepared.initial_read_plan["capture"] = {"path": str(source), "sha256": "0" * 64}
    elif damage == "changed_file":
        source.write_bytes(raw + b" ")  # Decodes identically; original byte identity still changed.

    request = turn_envelope._turn_envelope_result

    def no_second_selection(operation, **kwargs):
        assert operation != "context_select", "The producer already planned this same capture"
        return request(operation, **kwargs)

    with monkeypatch.context() as publication:
        publication.setattr(turn_envelope, "_turn_envelope_result", no_second_selection)
        if damage is not None:
            with pytest.raises(QuotaDecisionTransportError):
                native_context_capture_view(prepared.delivery, capture, context_directory=str(tmp_path),
                    initial_read_plan=prepared.initial_read_plan)
            assert (tmp_path / "session.json").read_bytes() == journal
            return
        view = native_context_capture_view(prepared.delivery, capture, context_directory=str(tmp_path),
            initial_read_plan=prepared.initial_read_plan)
    _, values = read_selection(model_context_reference(view)["first_read_command"])
    assert values["/selected_todo"]["future_number"] == 10**45 + 17
    assert values["/goal_boundary"] == decision["goal_boundary"]
    assert source.read_bytes() == raw
    assert "initial_read_plan" not in json.loads(raw)


@pytest.mark.parametrize("opaque", [False, True])
def test_prepared_initial_read_keeps_exact_body_without_retransmitting_unused_prose(
    tmp_path, monkeypatch, decision, opaque,
):
    from loopx.control_plane.quota import turn_envelope
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    tmp_path.chmod(0o700)
    decision["unselected_evidence"] = "Historical evidence remains available. " * 10000
    decision["unselected_structure"] = {f"unread_branch_{index}": {"detail": index} for index in range(500)}
    decision["selected_todo"]["future_number"] = 10**45 + 17
    view = pinned_initial_view(decision, tmp_path)
    raw = Path(view["recovery"]["path"]).read_bytes()
    original = read_context_capture(Namespace(capture_file=view["recovery"]["path"],
        capture_sha256=view["recovery"]["sha256"], format="json", initial_read=True,
        offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert original["ok"] and original["eof"]
    observed = []
    project = turn_envelope._turn_envelope_result

    def observe(operation, **params):
        if operation == "context_select":
            if opaque:
                assert not params.get("initial_read"), "The prepared cursor must retain its resolved addresses"
            observed.append(json.dumps(params, ensure_ascii=False).encode())
        return project(operation, **params)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", observe)
    navigation = model_context_reference(view) if opaque else view
    page, values = read_selection(navigation["first_read_command"])
    assert page["content"] == original["content"]
    assert values["/selected_todo"]["future_number"] == 10**45 + 17
    if opaque:
        from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference, write_context_read_reference

        assert observed == []
        words = shlex.split(navigation["first_read_command"])
        _, metadata = read_context_read_reference(words[words.index("--reference-file") + 1])
        legacy = write_context_read_reference(Path(metadata["path"]), metadata["sha256"], selection=metadata["selection"])
        words[words.index("--reference-file") + 1] = str(legacy)
        legacy_page, _ = read_selection(shlex.join(words))
        assert legacy_page["content"] == original["content"]
        assert len(observed) == 1
        assert b"Historical evidence remains available." not in observed[0]
        assert b"unread_branch_" not in observed[0], "Explicit replay needs only the selected address structure"
        assert len(observed[0]) < len(raw) // 2
    else:
        assert len(observed) == 1
        assert b"Historical evidence remains available." in observed[0]
    assert Path(view["recovery"]["path"]).read_bytes() == raw


def test_initial_navigation_keeps_the_original_recipe_when_cursor_metadata_is_unwritable(
    tmp_path, monkeypatch, decision,
):
    import errno
    from loopx.cli_commands import quota_capture

    def unavailable(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic cursor metadata unavailable")

    monkeypatch.setattr(quota_capture, "write_context_read_reference", unavailable)
    view = native_context_capture_view(decision, tmp_path)
    assert "first_read_reference" not in view
    assert "--initial-read" in view["first_read_command"]
    page, values = read_selection(view["first_read_command"])
    assert page["ok"] and page["eof"]
    assert values["/goal_boundary"] == decision["goal_boundary"]
    assert values["/selected_todo"] == decision["selected_todo"]


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
    recovery = shlex.split(rejected["recovery_command"])
    assert recovery[recovery.index("--offset") + 1] == "0"
    assert recovery[recovery.index("--capture-file") + 1] == view["recovery"]["path"]
    assert recovery[recovery.index("--capture-sha256") + 1] == view["recovery"]["sha256"]
    assert not {"--initial-read", "--select", "--selection-sha256"} & set(recovery)
    wrong_capture = recovery.copy()
    wrong_capture[wrong_capture.index("--capture-sha256") + 1] = "0" * 64
    code, invalid = invoke(shlex.join(wrong_capture))
    assert code != 0 and not invalid["ok"] and "recovery_command" not in invalid
    restored = b""
    command = rejected["recovery_command"]
    while True:
        code, page = invoke(command)
        assert code == 0 and page["offset"] == len(restored), page
        restored += page["content"].encode()
        if page["eof"]:
            break
        command = page["next_command"]
    assert restored == raw == Path(view["recovery"]["path"]).read_bytes()
    assert not registry.exists() and not runtime.exists()



@pytest.mark.parametrize("reader", ["explicit", "saved-recipe", "list"])
def test_stale_selected_cursor_restarts_in_the_same_scope(tmp_path, reader):
    from loopx.control_plane.turn_driver.decision_capture import write_context_read_reference

    receipt = {"event_id": "recorded", "turn_instance_id": "original-turn", "note": "原回执" * 90}
    marker = "This unselected work instruction must stay out of the recovery."
    capture = (tmp_path / "capture.json").resolve()
    raw = json.dumps({"heartbeat_receipt": receipt, "interaction_contract": {"primary_action": marker}}).encode()
    capture.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    stale = "0" * 64
    flags = ["--capture-file", str(capture), "--capture-sha256", digest]
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    base = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json"]
    command = ["quota", "context-read", "--offset", "256", "--page-bytes", "4096"]
    pointer = "/interaction_contract" if reader == "list" else "/heartbeat_receipt"
    if reader == "saved-recipe":
        ref = write_context_read_reference(capture, digest, selection={
            "select": [pointer], "initial_read": False, "list_fields": False,
            "list_depth": None, "sha256": stale,
        })
        reference_before = ref.read_bytes()
        command += ["--reference-file", str(ref)]
    else:
        command += [*flags, "--select", pointer, "--selection-sha256", stale]
        if reader == "list":
            command += ["--list", "--list-depth", "2"]

    def run(args):
        process = subprocess.run([*base, *args], cwd=tmp_path, capture_output=True, text=True, check=False)
        return process.returncode, json.loads(process.stdout)

    code, failure = run(command)
    assert code != 0 and "digest changed" in failure["reason"] and "content" not in failure
    recovery = shlex.split(failure["recovery_command"])
    assert recovery[recovery.index("--offset") + 1] == "0"
    assert recovery[recovery.index("--capture-sha256") + 1] == digest
    assert recovery[recovery.index("--select") + 1] == pointer
    assert ("--list" in recovery) == (reader == "list")
    if reader == "list":
        assert recovery[recovery.index("--list-depth") + 1] == "2"
    code, page = run(recovery[recovery.index("quota"):])
    assert code == 0 and page["ok"] and page["eof"] and marker not in page["content"]
    selections = json.loads(page["content"])["selections"]
    assert [selection["pointer"] for selection in selections] == [pointer]
    if reader != "list":
        assert selections[0]["value"] == receipt
    if reader == "saved-recipe":
        assert ref.read_bytes() == reference_before
    assert capture.read_bytes() == raw and not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("bound", [False, True])
def test_first_read_delivers_current_task_operation_with_its_continuation(tmp_path, decision, bound):
    action = {"operation": "read_private_source", "target_ref": "fixture/private-source.json",
              "future_constraint": {"allowed": False, "measurement": 2**60 + 1}}
    commands = ["loopx quota should-run --goal-id example --agent-id worker"]
    decision["interaction_contract"]["agent_channel"]["next_task_action"] = action
    decision["interaction_contract"]["cli_channel"].update(
        turn_binding_required=bound, next_cli_actions=commands)
    original = deepcopy(decision)
    view = native_context_capture_view(decision, tmp_path)
    _page, values = read_selection(view["first_read_command"])

    def delivered(pointer):
        root = max((key for key in values if pointer == key or pointer.startswith(key + "/")), key=len)
        value = values[root]
        for key in pointer[len(root):].split("/")[1:]:
            value = value[key]
        return value

    assert delivered("/interaction_contract/agent_channel/next_task_action") == action
    assert delivered("/interaction_contract/cli_channel/next_cli_actions") == commands
    assert json.loads(Path(view["recovery"]["path"]).read_bytes()) == original
    assert decision == original


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


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(("scope_count", "page_budget", "checkpoint_count"), [
    (165, 12000, 0), (290, 12000, 0), (165, 16000, 0), (290, 12000, 30),
])
def test_ready_opening_keeps_controls_readable_with_a_large_write_scope(
    tmp_path, monkeypatch, provider, warm, scope_count, page_budget, checkpoint_count,
):
    from test_codex_context_lifecycle import deliver, setup
    from test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _configure_read_only_todo, _heartbeat_receipt_count, _initialize_fixture_authority,
        _run_cli, _spend_run_count, _write_fixture,
    )
    from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
    from loopx.boundary_authority import build_checkpointed_boundary_authority_entry
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    narrative = "Keep the current verification requirements visible. " * 130
    state.write_text(state.read_text().replace("## Next Action", narrative + "\n\n## Next Action", 1))
    configuration = json.loads(registry.read_text())
    scopes = [f"docs/areas/{index:03d}/fixture-entrypoints/public/**" for index in range(scope_count)]
    if scope_count == 165:
        assert len(json.dumps(scopes, separators=(",", ":")).encode()) < 8192
    coordination = configuration["goals"][0]["coordination"]
    coordination.update(write_scope=scopes, requires_parent_approval=["publish"])
    if checkpoint_count:
        coordination["checkpointed_boundary_authority"] = [
            build_checkpointed_boundary_authority_entry(
                write_scopes=[scopes[0] if index % 2 == 0 else "docs/expired-approval/**"],
                source="public-fixture-scoped-approval",
                decision_id=f"fixture-approval-{index:02d}",
                recorded_at="2026-01-01T00:00:00Z",
                expires_at="2020-01-01T00:00:00Z" if index % 2 else None,
            )
            for index in range(checkpoint_count)
        ]
    configuration["goals"][0]["quota"]["allowed_slots"] = 3
    registry.write_text(json.dumps(configuration))
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    journal, captures = tmp_path / "journal", tmp_path / "captures"
    journal.mkdir(mode=0o700)
    captures.mkdir(mode=0o700)
    event = setup(journal, monkeypatch)

    def cli(*args):
        code, result = _run_cli(registry, runtime, *args, cwd=project)
        assert code == 0 and result["ok"], result
        return result

    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID)
    cli("todo", "claim", *binding, "--claimed-by", AGENT_ID)
    note_prefix = ("Keep the earlier acceptance evidence. 保留原验收。 " * 180).rstrip()
    note_suffix = " 新的验证已通过 🧪。"

    def update_note(note, operation):
        lease = cli("task-lease", "inspect", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)["lease"]
        cli("todo", "update", *binding, "--note", note, "--update-operation-id", operation,
            "--task-lease-idempotency-key", lease["idempotency_key"],
            "--task-lease-expected-version", str(lease["version"]))

    def capture(turn):
        if checkpoint_count:
            # The Host first saves a complete ready decision, then presents it.
            # This route retains the approval entries in the signed source.
            saved = captures / turn
            ready = cli("quota", "should-run", "--codex-app", "--context-delivery", "on",
                *binding, "--turn-instance-id", turn, "--decision-output-dir", str(saved),
                "--codex-context-dir", str(journal),
                "--defer-context-presentation", "--prepared-capture-reference")
            return cli("quota", "context-project", "--codex-app", "--context-delivery", "on",
                "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn,
                "--capture-file", str(saved / "decision.json"), "--capture-sha256", ready["capture_sha256"],
                "--decision-output-root", str(captures), "--codex-context-dir", str(journal),
                "--max-page-output-bytes", str(page_budget))
        return cli("quota", "should-run", "--codex-app", "--context-delivery", "on",
            *binding, "--turn-instance-id", turn, "--decision-output-root", str(captures),
            "--codex-context-dir", str(journal), "--max-page-output-bytes", str(page_budget))

    def read(command):
        pages, body = [], bytearray()
        while command:
            words = shlex.split(command)
            page = cli(*words[words.index("quota"):])
            assert page["offset"] == len(body)
            body.extend(page["content"].encode())
            pages.append(page)
            command = page.get("next_command")
        assert pages[-1]["eof"] and len(body) == pages[-1]["total_bytes"]
        return pages, selected_context_values(body.decode())

    body_pointer = note_pointer = None
    if warm and checkpoint_count:
        update_note(note_prefix, "seed-wide-note")
    if warm:
        prior = capture("wide-prior")
        _, prior_ref = read_context_read_reference(prior["recovery"]["reference_file"])
        rows = json.loads(Path(prior_ref["path"]).read_bytes())["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        index = next(i for i, source in enumerate(rows) if source["source"] == "goal_state")
        body_pointer = f"/interaction_contract/agent_channel/work_context/sources/{index}/content/text"
        prior_pages, prior_facts = read(prior["recovery"]["command"] + " --select " + body_pointer)
        assert narrative in prior_facts[body_pointer]
        deliver(journal, event, prior_pages)
        if checkpoint_count:
            index = next(i for i, source in enumerate(rows) if source["source"] == "selected_todo")
            note_pointer = f"/interaction_contract/agent_channel/work_context/sources/{index}/content/todo/note"
            note_pages, note_facts = read(prior["recovery"]["command"] + " --select " + note_pointer)
            assert note_facts[note_pointer] == note_prefix
            deliver(journal, event, note_pages)
        cli("refresh-state", *binding, "--turn-instance-id", "wide-prior", "--classification", "fixture_verified",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks")
        cli("quota", "spend-slot", *binding, "--turn-instance-id", "wide-prior",
            "--slots", "1", "--source", "heartbeat", "--execute")
        scopes = [*scopes[:-1], "docs/areas/current-fixture-scope/**"]
        configuration["goals"][0]["coordination"]["write_scope"] = scopes
        registry.write_text(json.dumps(configuration))
        if note_pointer:
            update_note(note_prefix + note_suffix, "append-wide-note")

    view = capture("wide-ready")
    _, source_ref = read_context_read_reference(view["recovery"]["reference_file"])
    path = Path(source_ref["path"])
    raw = path.read_bytes()
    source = json.loads(raw)
    pages, opening = read(view["first_read_command"])
    if page_budget == 16000:
        # A paged body is not itself a reason to add a second reader. The
        # ordinary first page fits the scope, selected Todo and read controls.
        assert "remaining_read_command" not in view and len(pages) > 1
        assert opening["/goal_boundary/write_scope"] == scopes
        assert opening["/selected_todo/todo_id"] == TODO_ID
        assert TODO_ID in pages[0]["content"] and "required_reads" in pages[0]["content"]
        assert "execution_preparation" in pages[0]["content"]
        assert opening["/interaction_contract/cli_channel/execution_preparation/state"] == "ready"
        assert path.read_bytes() == raw and hashlib.sha256(raw).hexdigest() == source_ref["sha256"]
        assert _heartbeat_receipt_count(runtime, "wide-ready") == 1
        assert _spend_run_count(runtime) == int(warm)
        return
    assert "Before work" in view["instruction"] and "remaining_read_command" in view
    assert len(pages) == 1 and pages[0]["eof"]
    assert not pages[0].get("previously_delivered_fields")
    _, opening_ref = read_context_read_reference(pages[0]["reference_file"])
    assert opening_ref["sha256"] == source_ref["sha256"]
    assert opening["/effective_action"] == "normal_run"
    assert opening["/heartbeat_receipt/turn_instance_id"] == "wide-ready"
    assert opening["/selected_todo/todo_id"] == TODO_ID
    assert opening["/interaction_contract/cli_channel/execution_preparation/state"] == "ready"
    assert opening["/goal_boundary/requires_parent_approval"] == ["publish"]
    assert opening["/goal_boundary/stop_condition"] == source["goal_boundary"]["stop_condition"]
    assert "/goal_boundary/write_scope" not in opening
    authority = "/goal_boundary/checkpointed_boundary_authority"
    if checkpoint_count:
        assert opening[authority + "/active_count"] == checkpoint_count // 2
        assert opening[authority + "/inactive_count"] == checkpoint_count // 2
        assert opening[authority + "/active_write_scope"] == [scopes[0]]
        assert authority + "/entries" not in opening
    if warm:
        deliver(journal, event, pages)

    pages, remaining = read(view["remaining_read_command"])
    assert len(pages) > 1 and remaining["/goal_boundary/write_scope"] == scopes
    if checkpoint_count:
        entries = source["goal_boundary"]["checkpointed_boundary_authority"]["entries"]
        assert len(entries) == checkpoint_count
        assert remaining[authority + "/entries"] == entries
    _, remainder_ref = read_context_read_reference(pages[0]["reference_file"])
    assert remainder_ref["sha256"] == source_ref["sha256"]
    _, ordinary = read(view["recovery"]["command"] + " --initial-read")
    # The pinned split and historical one-view selector cover the same leaves.
    def leaves(values):
        return {pointer: value for pointer, value in values.items()
            if not isinstance(value, (dict, list)) or not value}
    assert not opening.keys() & remaining.keys()
    if warm:
        assert body_pointer in pages[0]["previously_delivered_fields"] and body_pointer not in remaining
        assert "/goal_boundary/write_scope" not in pages[0]["previously_delivered_fields"]
        reconstructed = opening | remaining
        if note_pointer:
            assert note_pointer not in pages[0]["previously_delivered_fields"]
            rows = json.loads("".join(page["content"] for page in pages))["selections"]
            note = next(row for row in rows if row["pointer"] == note_pointer)
            assert note["presentation"] == "append_to_observed_text"
            assert note["prefix_bytes"] == len(note_prefix.encode())
            assert note["value"] == note_suffix
            reconstructed[note_pointer] = note_prefix + note["value"]
        assert leaves(reconstructed) == {key: value for key, value in leaves(ordinary).items() if key != body_pointer}
        deliver(journal, event, pages)
        assert "/goal_boundary/write_scope" in codex_context._state(journal)["consumed_pointers"]
        codex_context.codex_context_hook(str(journal), {**event, "hook_event_name": "PostCompact"})
        recovery_pages, restored = read(view["remaining_read_command"])
        assert "previously_delivered_fields" not in recovery_pages[0]
        assert not opening.keys() & restored.keys()
        assert leaves(opening | restored) == leaves(ordinary)
        if note_pointer:
            assert restored[note_pointer] == note_prefix + note_suffix
    else:
        assert leaves(opening | remaining) == leaves(ordinary)
    assert path.read_bytes() == raw and hashlib.sha256(raw).hexdigest() == source_ref["sha256"]
    assert _heartbeat_receipt_count(runtime, "wide-ready") == 1
    assert _spend_run_count(runtime) == int(warm)


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
    code, claim = _run_cli(registry, runtime, "todo", "claim", "--goal-id", GOAL_ID,
        "--todo-id", TODO_ID, "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID, cwd=project)
    assert code == 0 and claim["ok"], claim
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original_state = state.read_bytes()
    turn_id = "native-first-read"
    capture_root = tmp_path / "captures"
    capture_root.mkdir(mode=0o700)
    code, view = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--context-delivery", "on", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", turn_id,
        "--decision-output-root", str(capture_root), cwd=project)
    assert code == 0 and view["mode"] == "full", view
    _, reference = read_context_read_reference(view["recovery"]["reference_file"])
    raw = Path(reference["path"]).read_bytes()
    source = json.loads(raw)
    first = shlex.split(view["first_read_command"])
    _, first_ref = read_context_read_reference(first[first.index("--reference-file") + 1])
    assert first_ref["path"] == reference["path"] and first_ref["sha256"] == reference["sha256"]
    assert first_ref.get("selection_paths")

    def read_initial(command):
        words = shlex.split(command)
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
        return "".join(content)

    content = read_initial(view["first_read_command"])
    values = selected_context_values(content)
    cli_path = "/interaction_contract/cli_channel"
    # New pinned openings prioritize the execution decision. An independent
    # dynamic reader keeps the historical cursor order and the same values.
    historical = selected_context_values(read_initial(view["recovery"]["command"] + " --initial-read"))
    assert values == historical
    preparation_path = cli_path + "/execution_preparation"
    assert list(values).index(preparation_path) < list(values).index("/goal_boundary")
    assert list(historical).index("/goal_boundary") < list(historical).index(preparation_path)
    expected_cli = selected_context_values(json.dumps({"selections": [
        {"pointer": cli_path, "value": source["interaction_contract"]["cli_channel"]}]}))
    vision_path = next(path for path in expected_cli if path.endswith("/vision_authoring"))
    actions_path = cli_path + "/next_cli_actions"
    current_cli = source["interaction_contract"]["cli_channel"]
    plan = current_cli["settlement_plan"]
    assert plan["schema_version"] == "quota_settlement_plan_v1"
    writeback = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    spend = next(step for step in plan["ordered_steps"] if step["kind"] == "quota_spend")
    assert writeback["owner"] == "agent" and "--settle-turn" not in writeback["command_template"]
    assert spend["owner"] == "agent" and "command_template" in spend
    actual_commands = [writeback["command_template"], spend["command_template"]]
    assert current_cli["next_cli_actions"] == actual_commands
    # The ordered plan carries both exact templates and their conditions.
    # A ready first read should not present a second apparent route for it.
    assert not any(path == actions_path or path.startswith(actions_path + "/") for path in values)
    # Every distinct CLI leaf outside the authoring reference is still delivered.
    for path, item in expected_cli.items():
        if path == actions_path or path.startswith(actions_path + "/"):
            continue
        if path.startswith(vision_path + "/") or (isinstance(item, (dict, list)) and item):
            continue
        assert values[path] == item
    assert values[vision_path + "/rule"] == expected_cli[vision_path]["rule"]
    code, detail = _run_cli(registry, runtime, "quota", "context-read", "--reference-file",
        view["recovery"]["reference_file"], "--select", vision_path, "--page-bytes", "8000",
        "--max-page-output-bytes", "9000", cwd=project)
    assert code == 0 and detail["eof"]
    assert selected_context_values(detail["content"])[vision_path] == expected_cli[vision_path]
    code, fallback = _run_cli(registry, runtime, "quota", "context-read", "--reference-file",
        view["recovery"]["reference_file"], "--select", actions_path, cwd=project)
    assert code == 0 and fallback["eof"]
    assert selected_context_values(fallback["content"])[actions_path] == actual_commands
    assert values["/heartbeat_receipt"] == source["heartbeat_receipt"]
    assert values["/selected_todo"] == source["selected_todo"]
    assert _heartbeat_receipt_count(runtime, turn_id) == 1 and _spend_run_count(runtime) == 0
    assert Path(reference["path"]).read_bytes() == raw and state.read_bytes() == original_state


@pytest.mark.parametrize("variant", [
    "different_argument", "different_order", "unready", "other_effect", "future_plan",
    "composed_extra_fallback", "composed_unexpected_runtime_command",
])
def test_initial_read_keeps_fallback_actions_when_the_bound_plan_is_not_equivalent(tmp_path, decision, variant):
    interaction = decision["interaction_contract"]
    cli = interaction["cli_channel"]
    identity = {"schema_version": "quota_settlement_identity_v0", "effect_id": "original-effect",
                "todo_id": decision["selected_todo"]["todo_id"], "turn_instance_id": "original"}
    decision.update(effective_action="normal_run", normal_delivery_allowed=True,
                    heartbeat_receipt={"settlement_identity": identity})
    interaction["agent_channel"]["delivery_allowed"] = True
    writeback = "loopx refresh-state --turn-instance-id original"
    spend = "loopx quota spend-slot --turn-instance-id original --execute"
    cli.update(execution_preparation={"state": "ready", "todo_id": identity["todo_id"]},
               next_cli_actions=[writeback, spend],
               settlement_plan={"schema_version": "quota_settlement_plan_v1", "identity": identity,
                                "ordered_steps": [{"kind": "durable_writeback", "command_template": writeback},
                                                  {"kind": "quota_spend", "command_template": spend}]})
    if variant == "different_argument":
        cli["next_cli_actions"][0] += " --suppress-external-sinks"
    elif variant == "different_order":
        cli["next_cli_actions"].reverse()
    elif variant == "unready":
        cli["execution_preparation"]["state"] = "claim_required"
    elif variant == "other_effect":
        decision["heartbeat_receipt"]["settlement_identity"] = {**identity, "effect_id": "another"}
    elif variant == "future_plan":
        cli["settlement_plan"]["schema_version"] = "unknown_settlement_plan"
    else:
        plan = cli["settlement_plan"]
        plan["schema_version"] = "quota_settlement_plan_v2"
        plan["ordered_steps"][0]["owner"] = "agent"
        plan["ordered_steps"][1]["owner"] = "runtime"
        if variant == "composed_extra_fallback":
            plan["ordered_steps"][1].pop("command_template")
        else:
            cli["next_cli_actions"] = [writeback]

    view = native_context_capture_view(decision, tmp_path)
    _, values = read_selection(view["first_read_command"])
    assert values["/interaction_contract/cli_channel/next_cli_actions"] == cli["next_cli_actions"]


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


@pytest.mark.parametrize("route", [
    "matching", "bounded", "different", "missing", "incomplete", "omitted", "unknown",
    "bad_counts", "small", "held", "missing_instruction", "selection",
    "selection_unavailable_route", "selection_spend_allowed", "selection_different_read",
    "replan", "replan_bounded", "replan_missing_read", "replan_different_read", "replan_no_attempt", "replan_settlement",
])
def test_initial_read_leaves_large_agenda_rows_to_the_matching_required_read(tmp_path, decision, route):
    command = "loopx manager-inbox read --goal-id fixture --agent-id author"
    read = {"source": "turn_start_capability_hook", "kind": "operator_inbox",
        "capability_id": "manager-context", "hook_id": "manager.context_inbox",
        "ordering": "before_work", "command": command}
    purpose = "保留原反馈与验收材料。" * (4 if route == "small" else 48)
    bounded = route in {"bounded", "missing_instruction", "replan_bounded"}
    agenda = {"count_unit": "request", "incoming_count": 3 if bounded or route == "bad_counts" else 2,
        "outgoing_count": 1,
        "items": [{"request_id": f"request-{index}", "next_action": purpose} for index in range(3)],
        "omitted_count": int(bounded or route == "omitted"), "scan_complete": route != "incomplete",
        "unavailable_count": int(route == "incomplete"), "read_command": command,
        "instruction": "Read the full current request before acting."}
    if route == "unknown":
        agenda["future_gate"] = "Check the retained restriction."
    if route == "missing_instruction":
        del agenda["instruction"]
    decision.update(should_run=True, normal_delivery_allowed=True, effective_action="normal_run")
    channel = decision["interaction_contract"]["agent_channel"]
    selecting = route.startswith("selection")
    channel.update(delivery_allowed=not selecting and route != "held", must_attempt=True)
    if selecting:
        decision["interaction_contract"]["cli_channel"] = {
            "selection_required": True, "next_cli_actions": [],
            "spend_allowed_now": route == "selection_spend_allowed", "spend_after_validation": False,
            **({"selection_command": {"schema_version": "action_selection_cli_command_v1",
                "route_prefix": "loopx --format json",
                "command_args_template": "quota should-run --todo-id '{todo_id}' --turn-instance-id fixture-turn",
                "candidate_discovery_args": "todo list --goal-id fixture --status open"}}
                if route != "selection_unavailable_route" else {}),
        }
    if route.startswith("replan"):
        decision.update(normal_delivery_allowed=False, effective_action="autonomous_replan_required",
            execution_obligation={"must_attempt_work": route != "replan_no_attempt"},
            replan_action_packet={"schema_version": "replan_action_packet_v0", "decision": "replan_required",
                "settlement_only": route == "replan_settlement",
                "review": "loopx quota replan-view --replan-stage review"})
        channel.update(delivery_allowed=False, must_attempt=route != "replan_no_attempt")
    channel["work_context"]["sources"].append({**read, "content": {
        "ok": True, "read_complete": False, "unfinished_delegations": agenda,
    }})
    if route not in {"missing", "replan_missing_read"}:
        required = ({**read, "command": command + " --agent-id other"}
                    if route in {"different", "selection_different_read", "replan_different_read"} else read)
        channel["required_reads"].append(required)

    view = native_context_capture_view(decision, tmp_path)
    _, values = read_selection(view["first_read_command"])
    prefix = "/interaction_contract/agent_channel/work_context/sources/3/content"
    assert values[prefix + "/read_complete"] is False
    agenda_path = prefix + "/unfinished_delegations"
    if route in {"matching", "bounded", "selection", "replan", "replan_bounded"}:
        assert agenda_path not in values and agenda_path + "/items" not in values
        for field in ("incoming_count", "outgoing_count", "scan_complete"):
            assert values[f"{agenda_path}/{field}"] == agenda[field]
        if bounded:
            assert values[agenda_path + "/omitted_count"] == 1
            assert values[agenda_path + "/instruction"] == agenda["instruction"]
        else:
            assert agenda_path + "/instruction" not in values
        assert read in values["/interaction_contract/agent_channel/required_reads"]
        if route.startswith("replan"):
            assert values["/replan_action_packet/review"] == decision["replan_action_packet"]["review"]
            assert values["/interaction_contract/agent_channel/delivery_allowed"] is False
    else:
        assert values[agenda_path] == agenda
    detail = read_context_capture(Namespace(capture_file=view["recovery"]["path"],
        capture_sha256=view["recovery"]["sha256"], format="json", select=[agenda_path],
        offset=0, page_bytes=64000, max_page_output_bytes=64000))
    assert detail["ok"] and detail["eof"]
    assert json.loads(detail["content"])["selections"][0]["value"] == agenda


def test_initial_read_surfaces_bounded_peer_return_attention_before_long_sources(tmp_path, decision):
    command = "loopx manager-inbox read --goal-id fixture --agent-id author"
    read = {"source": "turn_start_capability_hook", "kind": "operator_inbox",
        "capability_id": "manager-context", "hook_id": "manager.context_inbox",
        "ordering": "before_work", "command": command}
    attention = {"count": 1, "omitted_count": 0, "items": [{
        "request_id": "a" * 64, "request_revision": 1, "step": "consume_peer_result",
        "unconsumed_return_count": 1,
        "returns": [{"result_key": "conclusion", "request_revision": 1,
            "status": "delivered", "consumed": False}],
    }]}
    items = [{"request_id": f"{index:064x}", "direction": "incoming" if index < 10 else "outgoing",
        "step": "review_request_work", "purpose": "Keep the original evidence. " * 16}
        for index in range(20)]
    agenda = {"count_unit": "request", "incoming_count": 10, "outgoing_count": 11,
        "items": items, "omitted_count": 1, "return_attention": attention,
        "scan_complete": True, "unavailable_count": 0, "read_command": command,
        "instruction": "Read the full current request before acting."}
    decision.update(should_run=True, normal_delivery_allowed=True, effective_action="normal_run")
    channel = decision["interaction_contract"]["agent_channel"]
    channel.update(delivery_allowed=True, must_attempt=True)
    channel["required_reads"].append(read)
    sources = channel["work_context"]["sources"]
    sources.append({**read, "content": {"ok": True, "read_complete": False,
        "unfinished_delegations": agenda}})

    view = native_context_capture_view(decision, tmp_path)
    _, values = read_selection(view["first_read_command"])
    prefix = f"/interaction_contract/agent_channel/work_context/sources/{len(sources) - 1}/content/unfinished_delegations"
    attention_path = prefix + "/return_attention"
    assert values[attention_path] == attention
    assert prefix not in values and prefix + "/items" not in values
    goal_text = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    assert list(values).index(attention_path) < list(values).index(goal_text)


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


@pytest.mark.parametrize("transition", ["same", "corrected", "retired", "empty", "different_hook", "different_registry"])
def test_native_first_read_reuses_only_registered_preference_prose(tmp_path, monkeypatch, decision, transition):
    from test_codex_context_lifecycle import deliver, setup

    event = setup(tmp_path, monkeypatch)
    decision.update(agent_id="agent", registry=str(tmp_path / "registry.json"), runtime_root=str(tmp_path / "runtime"))
    prose = "Use the current verified source for this preference. " * 110
    item = {"key": "review.preference", "state": "active", "statement": prose, "source_ref": "owner-1",
            "source_quote": "Preserve the current source.", "operation_id": "remember-1", "expires_at": None}
    current = {"schema_version": "agent_preferences_v1", "authority": "advisory_only",
               "instructions": "Read the current view before any preference-dependent external action.",
               "revision": "initial", "items": [item], "future_control": "Observe this value on each read."}
    content = {"ok": True, "status": "read", "current": current}
    sources = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    source = {"source": "turn_start_capability_hook", "kind": "agent_preferences",
              "hook_id": "semantic_preference.agent_context", "capability_id": "semantic-preference", "content": content}
    sources.append(source)
    prefix = f"/interaction_contract/agent_channel/work_context/sources/{len(sources) - 1}/content"
    address = prefix + "/current/items/0/statement"

    def capture(name):
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        prepared = codex_context.prepare_codex_context(decision, directory=str(tmp_path), capture_directory=directory,
            goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id=name)
        return model_context_reference(native_context_capture_view(prepared.delivery, directory,
            context_directory=str(tmp_path), initial_read_plan=prepared.initial_read_plan))

    first = capture("first")
    observed, facts = read_selection(first["recovery"]["command"] + " --select " + prefix + "/current")
    assert facts[prefix + "/current"] == current
    deliver(tmp_path, event, [observed])
    current["revision"] = "next"
    if transition == "corrected":
        item.update(statement="Use the revised source.", operation_id="remember-2")
    elif transition == "retired":
        item.update(statement=None, state="retired", operation_id="retire-1")
    elif transition == "empty":
        current["items"] = []
    elif transition == "different_hook":
        source["hook_id"] = "another.preference.reader"
    elif transition == "different_registry":
        decision["registry"] = str(tmp_path / "another-registry.json")

    second = capture("next")
    page, facts = read_selection(second["first_read_command"])
    if transition == "same":
        assert page["previously_delivered_fields"] == [address]
        assert facts[prefix + "/status"] == "read"
        assert facts[prefix + "/current/revision"] == "next"
        assert facts[prefix + "/current/future_control"] == current["future_control"]
        assert facts[prefix + "/current/items/0/state"] == "active"
        assert facts[prefix + "/current/items/0/operation_id"] == "remember-1"
        assert prose not in page["content"]
        _, recovered = read_selection(second["recovery"]["command"] + " --select " + address)
        assert recovered[address] == prose
        codex_context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostCompact"})
        restored, facts = read_selection(second["first_read_command"])
        assert restored["total_bytes"] - page["total_bytes"] > 4000
        page = restored
    assert address not in page.get("previously_delivered_fields", [])
    assert facts[prefix + "/current"] == current


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


def test_pinned_value_pages_replay_the_resolved_paths(tmp_path, monkeypatch):
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.turn_driver.decision_capture import (
        read_context_read_reference, write_context_read_reference, write_decision_capture,
    )

    capture = tmp_path / "decision.json"
    payload = {"a/~": {"0": "原捕获的精确正文。" * 160}, "array": [{"number": 9007199254740993}]}
    digest = write_decision_capture(capture, payload)
    reference = write_context_read_reference(capture, digest)
    start = read_context_capture(Namespace(reference_file=str(reference), format="json", offset=0, page_bytes=256,
        select=["/a~1~0/0", "/array/0/number"], max_page_output_bytes=5000))
    assert start["ok"] and not start["eof"]
    pinned = start["reference_file"]
    _, saved = read_context_read_reference(pinned)
    assert saved["selection_paths"] == [["array", 0, "number"], ["a/~", "0"]]

    legacy = write_context_read_reference(capture, digest, selection=saved["selection"])
    legacy_page = read_context_capture(Namespace(reference_file=str(legacy), format="json", offset=0, page_bytes=256))
    assert legacy_page["ok"] and legacy_page["content"] == start["content"]

    def unavailable(*_args, **_kwargs):
        pytest.fail("a pinned value page should reuse the writer's resolved paths")

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unavailable)
    page, parts = start, [start["content"]]
    while not page["eof"]:
        page = read_context_capture(Namespace(reference_file=pinned, format="json", offset=page["next_offset"],
            page_bytes=256, max_page_output_bytes=5000))
        assert page["ok"]
        parts.append(page["content"])
    assert json.loads("".join(parts))["selections"] == [
        {"pointer": "/array/0/number", "value": payload["array"][0]["number"]},
        {"pointer": "/a~1~0/0", "value": payload["a/~"]["0"]},
    ]


@pytest.mark.parametrize("damage", ["different_path", "wrong_key_type", "view_digest", "capture_digest"])
def test_pinned_value_page_rejects_changed_path_or_bytes(tmp_path, damage):
    from loopx.control_plane.turn_driver.decision_capture import (
        read_context_read_reference, write_context_read_reference, write_decision_capture,
    )

    capture = tmp_path / "decision.json"
    digest = write_decision_capture(capture, {"array": ["value"], "alias": ["value"]})
    original = write_context_read_reference(capture, digest)
    first = read_context_capture(Namespace(reference_file=str(original), format="json", offset=0, page_bytes=256,
        select=["/array/0"]))
    assert first["ok"]
    _, saved = read_context_read_reference(first["reference_file"])
    selection, paths = saved["selection"], saved["selection_paths"]
    if damage == "different_path":
        paths = [["alias", 0]]  # The value itself is identical.
    elif damage == "wrong_key_type":
        paths = [["array", "0"]]
    elif damage == "view_digest":
        selection = {**selection, "sha256": "0" * 64}
    else:
        capture.write_text('{"array":["changed"],"alias":["value"]}', encoding="utf-8")
    reference = write_context_read_reference(capture, digest, selection=selection, selection_paths=paths)
    rejected = read_context_capture(Namespace(reference_file=str(reference), format="json", offset=0, page_bytes=256))
    assert not rejected["ok"] and "content" not in rejected
    reason = "digest" if "digest" in damage else "address"
    assert reason in rejected["reason"]


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


def test_selection_digest_without_a_selected_view_is_rejected_before_authority_access(tmp_path):
    output = []
    code = handle_native_context_command(Namespace(quota_command="context-read", format="json",
        selection_sha256="0" * 64), print_payload=lambda value, *_args: output.append(value),
        registry_path=tmp_path / "unopened-registry.json")
    assert code == 1 and not output[0]["ok"]
    assert "requires context-read with --select or --initial-read" in output[0]["reason"]
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
        wire = codex_context.prepare_codex_context(decision, directory=str(tmp_path), capture_directory=directory,
            goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id=name).delivery
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


@pytest.mark.parametrize("invalidate,retained", [("none", 7), ("compact", 7), ("partial", 7), ("off", 7), ("none", 1)])
def test_native_first_read_reuses_requirement_bodies_after_canonical_correction(
        tmp_path, monkeypatch, navigated_decision, invalidate, retained):
    from test_codex_context_lifecycle import setup, deliver

    event = setup(tmp_path, monkeypatch)
    source = navigated_decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]
    content = source["content"]
    outcome = content["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    outcome.update(objective="Preserve cumulative acceptance.", sources=[], requirements=[
        {"id": f"criterion-{i}", "description": (f"Criterion {i}: preserve original requirements, cancellation, source and independent validation. " * 12),
         "status": "unverified", "planning_satisfied": False} for i in range(8)])
    prefix = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes/0"

    def navigation(name, enabled=True):
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        prepared = codex_context.prepare_codex_context(navigated_decision, directory=str(tmp_path),
            capture_directory=directory, goal_id="fixture", agent_id="agent", surface="quota_packet",
            capture_id=name, progressive_enabled=enabled)
        return model_context_reference(native_context_capture_view(prepared.delivery, directory,
            context_directory=str(tmp_path), progressive_enabled=enabled,
            initial_read_plan=prepared.initial_read_plan, max_page_output_bytes=6000))

    def pages(command):
        result = []
        while command:
            words = shlex.split(command)
            page = json.loads(subprocess.check_output([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
                *words[words.index("quota"):]]))
            assert page["ok"]
            result.append(page)
            command = page.get("next_command")
        return result

    first = pages(navigation("before")["first_read_command"])
    assert len(first) > 1
    deliver(tmp_path, event, first[1:] if invalidate == "partial" else first)
    if invalidate == "compact":
        codex_context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostCompact"})
    # A real canonical correction also changes metadata and may switch from a
    # complete source to a smaller supporting-material projection. Neither is a
    # change to the seven retained descriptions. Reviews and new controls are.
    outcome["revision"] += 1
    outcome["sources"] = [{"ref": "fixture:correction", "summary": "Supporting correction evidence. " * 600}]
    outcome["requirements"][0]["description"] = "Revised cancellation: never publish the result."
    outcome["requirements"][1]["status"] = "accepted"
    outcome["requirements"][2]["new_control"] = "Read the current authorization before acting."
    outcome["requirements"].append({"id": "new", "description": "A newly required independent check."})
    if retained == 1:
        for item in outcome["requirements"][2:8]:
            item["description"] = "Revised requirement: " + item["description"]
    current = navigation("after", invalidate != "off")
    read = pages(current["first_read_command"] if invalidate != "off" else current["recovery"]["command"])
    if invalidate == "off":
        assert "first_read_command" not in current
        assert json.loads("".join(page["content"] for page in read)) == navigated_decision
        assert all("previously_delivered_fields" not in page for page in read)
        return
    body = "".join(page["content"] for page in read)
    facts = selected_context_values(body)
    assert facts[prefix + "/revision"] == 2
    assert facts[prefix + "/requirements/0/description"] == outcome["requirements"][0]["description"]
    assert facts[prefix + "/requirements/1/status"] == "accepted"
    assert facts[prefix + "/requirements/2/new_control"] == outcome["requirements"][2]["new_control"]
    assert facts[prefix + "/requirements/8"] == outcome["requirements"][8]
    assert facts["/goal_boundary"] == navigated_decision["goal_boundary"]
    if invalidate != "none" or retained == 1:
        assert "previously_delivered_fields" not in read[0]
        assert facts[prefix + "/requirements/1/description"] == outcome["requirements"][1]["description"]
        return
    expected = {prefix + f"/requirements/{i}/description" for i in (1, 2)} | {
        prefix + f"/requirements/{i}" for i in range(3, 8)}
    assert set(read[0]["previously_delivered_fields"]) >= expected
    assert prefix + "/requirements/1/description" not in facts
    cold = pages(current["recovery"]["command"] + " --initial-read")
    assert len(body.encode()) < sum(len(page["content"].encode()) for page in cold)
    recovered = pages(current["recovery"]["command"] + " --select " + prefix + "/requirements/1/description")
    assert selected_context_values("".join(page["content"] for page in recovered))[
        prefix + "/requirements/1/description"] == outcome["requirements"][1]["description"]
    before = codex_context._state(tmp_path)["consumed_pointers"][prefix]
    metadata = json.loads(Path(read[0]["reference_file"]).read_text())
    metadata["observed_read"]["fields"][next(iter(metadata["observed_read"]["fields"]))] = "f" * 64
    forged = tmp_path / "forged-reference.json"
    forged.write_text(json.dumps(metadata))
    forged.chmod(0o600)
    deliver(tmp_path, event, [{**page, "reference_file": str(forged)} for page in read])
    assert codex_context._state(tmp_path)["consumed_pointers"][prefix] == before
    # Only complete current pages plus authenticated, exact inherited fields
    # can qualify the revised parent. No full-input baseline is manufactured.
    with monkeypatch.context() as hook_env:
        hook_env.delenv("CODEX_THREAD_ID")
        deliver(tmp_path, event, read[:-1])
        assert codex_context._state(tmp_path)["consumed_pointers"][prefix] == before
        deliver(tmp_path, event, read[-1:])
    assert codex_context._state(tmp_path)["consumed_pointers"][prefix] != before
    next_read = pages(navigation("continued")["first_read_command"])
    assert prefix in next_read[0]["previously_delivered_fields"]
    assert selected_context_values("".join(page["content"] for page in next_read))[prefix + "/revision"] == 2


@pytest.mark.parametrize("invalidate", ["compact", "resume", "partial", "task_switch", "off"])
@pytest.mark.parametrize("coalesced", [False, True])
def test_native_first_read_reuses_only_observed_current_fields(tmp_path, monkeypatch, navigated_decision, invalidate, coalesced):
    from test_codex_context_lifecycle import setup, deliver

    event = setup(tmp_path, monkeypatch)
    source = navigated_decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]
    content = source["content"]
    outcomes = content["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    if coalesced:
        # All outcomes are relevant, so the reader may package them as one
        # complete parent. Delivery still observes each complete outcome.
        outcomes.pop()
    for outcome in outcomes:
        outcome["requirements"][0]["description"] *= 240
    if coalesced:
        outcomes.append({"id": "small", "owner_agent_id": "author", "revision": 1,
            "requirements": [{"id": "short", "description": "Keep this short current fact."}]})
        content["outcome_context"]["suggested_outcome_ids"].append("small")
    prefix = "/interaction_contract/agent_channel/work_context/sources/0/content/goal_acceptance_contract/outcome_requirements/outcomes"
    def navigation(capture, enabled=True):
        directory = tmp_path / capture
        directory.mkdir(mode=0o700)
        prepared = codex_context.prepare_codex_context(navigated_decision, directory=str(tmp_path),
            capture_directory=directory, goal_id="fixture", agent_id="agent", surface="quota_packet",
            capture_id=capture, progressive_enabled=enabled)
        view = native_context_capture_view(prepared.delivery, directory, context_directory=str(tmp_path),
            progressive_enabled=enabled, initial_read_plan=prepared.initial_read_plan, max_page_output_bytes=6000)
        return model_context_reference(view)

    def pages(command):
        found = []
        while command:
            words = shlex.split(command)
            out = subprocess.check_output([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
                *words[words.index("quota"):]])
            page = json.loads(out)
            assert page["ok"], page
            found.append(page)
            command = page.get("next_command")
        return found

    def facts(pages):
        return selected_context_values("".join(page["content"] for page in pages))

    navigated_decision["goal_boundary"]["stop_condition"] = "Preserve every applicable stop. " * 220
    first = pages(navigation("first")["first_read_command"])
    assert len(first) > 1 and facts(first)[prefix + "/0"] == outcomes[0]
    deliver(tmp_path, event, first[1:] if invalidate == "partial" else first)
    if coalesced:
        assert prefix + "/2" not in codex_context._state(tmp_path).get("consumed_pointers", {})
    outcomes[1]["requirements"][0]["description"] = "Revised stop: require the audited result."
    if invalidate == "task_switch":
        navigated_decision["selected_todo"]["todo_id"] = content["outcome_context"]["todo_id"] = "todo_next"
    second = navigation("next", invalidate != "off")
    if invalidate == "off":
        # Independent Delta does not enable Progressive navigation or observed
        # field reuse. Its current recovery still contains every original field.
        assert "first_read_command" not in second
        assert "observed_first_read" not in codex_context._state(tmp_path)["pending"]
        recovery = pages(second["recovery"]["command"])
        assert all("previously_delivered_fields" not in page for page in recovery)
        assert json.loads("".join(page["content"] for page in recovery)) == navigated_decision
        return
    warm = pages(second["first_read_command"])
    observed = facts(warm)
    assert observed[prefix + "/1"] == outcomes[1]
    assert observed["/goal_boundary"] == navigated_decision["goal_boundary"]
    if invalidate not in {"compact", "resume"}:
        assert observed[prefix + "/0"] == outcomes[0]
        assert "previously_delivered_fields" not in warm[0]
        return
    assert warm[0]["previously_delivered_fields"] == [prefix + "/0"]
    assert observed[prefix + "/0/id"] == outcomes[0]["id"] and prefix + "/0" not in observed
    assert facts(pages(second["recovery"]["command"] + " --select " + prefix + "/0"))[prefix + "/0"] == outcomes[0]
    lifecycle = {"hook_event_name": "PostCompact"} if invalidate == "compact" else {"source": "resume"}
    codex_context.codex_context_hook(str(tmp_path), {**event, **lifecycle})
    assert len(warm) > 1
    stale = subprocess.run(shlex.split(warm[0]["next_command"]), capture_output=True, text=True)
    assert stale.returncode != 0
    rejection = json.loads(stale.stdout)
    assert "content" not in rejection
    restarted = pages(rejection["recovery_command"])
    assert facts(restarted)[prefix + "/0"] == outcomes[0]
    recovered = pages(second["first_read_command"])
    assert facts(recovered)[prefix + "/0"] == outcomes[0]


@pytest.mark.parametrize(("transition", "distinct_tool_workdir"), [
    ("unchanged", False), ("corrected", False), ("different_source", False), ("incomplete", False),
    pytest.param("unchanged", True, id="unchanged-other-tool-workdir"),
])
def test_native_first_read_goal_narrative_reuse(tmp_path, monkeypatch, decision, transition, distinct_tool_workdir):
    from test_codex_context_lifecycle import deliver, setup

    event = setup(tmp_path, monkeypatch)
    narrative = "保留完整目标、独立验收、权限边界和停止条件。\n" * 140
    source = decision["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]
    plan = decision["interaction_contract"]["cli_channel"]["settlement_plan"]
    prefix = "/interaction_contract/agent_channel/work_context/sources/0/content"
    body_pointer = prefix + "/text"
    source.update(source="goal_state", ordering="before_work")

    def goal_source(path, text, generated):
        def revision(value):
            return "sha256:" + hashlib.sha256(value.encode()).hexdigest()

        line = len(text.splitlines()) + 1
        return {"state_file": path, "text": text, "revision": revision(text + generated),
                "excluded_todo_regions": [{"role": "agent", "start_line": line, "end_line": line,
                                           "revision": revision(generated)}]}

    def capture(name, content):
        source.update(content=content, command=f"cat -- {content['state_file']}")
        plan["identity"]["turn_instance_id"] = name
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        prepared = codex_context.prepare_codex_context(decision, directory=str(tmp_path),
            capture_directory=directory, goal_id="fixture", agent_id="agent",
            surface="quota_packet", capture_id=name)
        return model_context_reference(native_context_capture_view(prepared.delivery, directory,
            context_directory=str(tmp_path), initial_read_plan=prepared.initial_read_plan))

    def read(command):
        pages, size, offset = [], 0, 0
        while command:
            words = shlex.split(command)
            stdout = subprocess.check_output([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
                *words[words.index("quota"):]])
            page = json.loads(stdout)
            assert page["ok"] and page["offset"] == offset, page
            pages.append(page)
            size += len(stdout)
            offset += len(page["content"].encode())
            command = page.get("next_command")
        assert pages[-1]["eof"] and offset == pages[-1]["total_bytes"]
        return pages, selected_context_values("".join(page["content"] for page in pages)), size

    first = capture("first", goal_source("goal.md", narrative, "old generated todo\n"))
    pages, facts, _ = read(first["first_read_command"])
    assert facts[body_pointer] == narrative and "previously_delivered_fields" not in pages[0]
    read_event = event
    if distinct_tool_workdir:
        tool_workdir = tmp_path / "tool-worktree"
        tool_workdir.mkdir()
        read_event = {**event, "cwd": str(tool_workdir)}
        before = codex_context._state(tmp_path)
        deliver(tmp_path, {**read_event, "session_id": "foreign-native-session"}, pages)
        deliver(tmp_path, read_event, [{**pages[0], "content": pages[0]["content"][:-1]}])
        assert codex_context._state(tmp_path) == before
    deliver(tmp_path, read_event, pages)
    observed = codex_context._state(tmp_path)
    assert body_pointer in observed["consumed_pointers"]
    assert observed["cwd"] == event["cwd"] and "candidate" not in observed

    text = narrative + "Use the revised owner decision.\n" if transition == "corrected" else narrative
    current = goal_source("alternate.md" if transition == "different_source" else "goal.md",
                          text, "new generated todo\n")
    if transition == "incomplete":
        current.update(read_complete=False, recovery="Restore the complete Goal source.")
    decision["goal_boundary"]["stop_condition"] = "Stop before an external write."
    second = capture("second", current)
    pages, facts, warm_bytes = read(second["first_read_command"])
    assert facts[prefix + "/revision"] == current["revision"]
    assert facts["/goal_boundary"] == decision["goal_boundary"]
    assert facts["/interaction_contract/cli_channel/settlement_plan/identity/turn_instance_id"] == "second"
    if transition != "unchanged":
        assert facts[body_pointer] == text and "previously_delivered_fields" not in pages[0]
        if transition == "incomplete":
            assert facts[prefix + "/recovery"] == current["recovery"]
        return

    assert pages[0]["previously_delivered_fields"] == [body_pointer] and body_pointer not in facts
    observed = json.loads(Path(pages[0]["reference_file"]).read_text())["observed_read"]
    ordinary = Path(pages[0]["reference_file"]).with_name(observed["fallback_reference"]["filename"])
    recovery = second["recovery"]["command"]
    _, restored, _ = read(recovery + " --select " + body_pointer)
    assert restored[body_pointer] == narrative
    codex_context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostCompact"})
    fallback, after, fallback_bytes = read(second["first_read_command"])
    assert "previously_delivered_fields" not in fallback[0] and after[body_pointer] == narrative
    assert Path(fallback[0]["reference_file"]) == ordinary
    assert {key: value for key, value in after.items() if key != body_pointer} == facts
    assert fallback_bytes - warm_bytes > 3000


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["none", "partial", "different_todo", "changed_rule", "compact", "rebound"])
def test_native_first_read_identifies_already_observed_vision_authoring(tmp_path, monkeypatch, provider, change):
    from test_codex_context_lifecycle import deliver, setup
    from test_replan_successor_guard_reentry import AGENT, GOAL, _fixture

    call, _, _ = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    added = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact.", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "verify-source-artifact")
    todo = added["todo_id"]
    decision = call("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", "fixture-original-authoring", "--todo-id", todo)
    assert decision["effective_action"] == "normal_run"
    assert decision["heartbeat_receipt"]["closeout_required"] is True
    interaction = decision["interaction_contract"]["cli_channel"]
    steps = interaction["settlement_plan"]["ordered_steps"]
    index, = (i for i, step in enumerate(steps) if step["kind"] == "durable_writeback")
    prefix = "/interaction_contract/cli_channel/settlement_plan"
    address = f"{prefix}/ordered_steps/{index}/vision_authoring"
    original = steps[index]["vision_authoring"]
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    event = setup(native, monkeypatch)

    def navigation(name, payload):
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        prepared = codex_context.prepare_codex_context(payload, directory=str(native),
            capture_directory=directory, goal_id=GOAL, agent_id=AGENT, surface="quota_packet", capture_id=name)
        return model_context_reference(native_context_capture_view(prepared.delivery, directory,
            context_directory=str(native), initial_read_plan=prepared.initial_read_plan))

    first = navigation("original", decision)
    page, _ = read_selection(first["first_read_command"])
    assert "previously_delivered_fields" not in page
    if change == "partial":
        page = read_context_capture(Namespace(reference_file=first["recovery"]["reference_file"], offset=0,
            page_bytes=900, format="json", select=[address]))
        assert page["ok"] and not page["eof"]
    else:
        page, facts = read_selection(first["recovery"]["command"] + " --select " + address)
        assert facts[address] == original
    deliver(native, event, [page])

    current = json.loads(json.dumps(decision).replace("fixture-original-authoring", "fixture-current-authoring"))
    current_plan = current["interaction_contract"]["cli_channel"]["settlement_plan"]
    if change == "different_todo":
        current["selected_todo"]["todo_id"] = current_plan["identity"]["todo_id"] = "todo_distinct"
        current["heartbeat_receipt"]["settlement_identity"]["todo_id"] = "todo_distinct"
    if change == "changed_rule":
        current_plan["ordered_steps"][index]["vision_authoring"]["authoring_hint"] += " New applicable rule."
    if change == "rebound":
        current["registry"] = str(tmp_path / "alternate-registry.json")
    second = navigation("current", current)
    page, facts = read_selection(second["first_read_command"])
    seen = change in {"none", "compact"}
    assert (address in page.get("previously_delivered_fields", [])) is seen
    for key, value in current_plan["identity"].items():
        assert facts[prefix + "/identity/" + key] == value
    command_path = f"{prefix}/ordered_steps/{index}/semantic_closeout_command_template"
    assert facts[command_path] == current_plan["ordered_steps"][index]["semantic_closeout_command_template"]
    assert "fixture-current-authoring" in facts[command_path]
    if change == "compact":
        codex_context.codex_context_hook(str(native), {**event, "hook_event_name": "PostCompact"})
        fallback, _ = read_selection(second["first_read_command"])
        assert "previously_delivered_fields" not in fallback
    if change == "none":
        deliver(native, event, [page])
        next_page, _ = read_selection(navigation("later", current)["first_read_command"])
        assert address in next_page.get("previously_delivered_fields", [])
    _, full = read_selection(second["recovery"]["command"] + " --select " + address)
    assert full[address] == current_plan["ordered_steps"][index]["vision_authoring"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("complete", [False, True], ids=["ordinary", "complete"])
def test_native_vision_authoring_reuse_after_same_turn_todo_selection(tmp_path, monkeypatch, provider, complete):
    from test_codex_context_lifecycle import deliver, setup
    from test_replan_successor_guard_reentry import AGENT, GOAL, _fixture

    call, _, _ = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    added = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact.", "--task-class", "advancement_task",
        "--priority", "P0", "--action-kind", "validate", "--target-key", "verify-source-artifact",
        "--continuation-policy", "same_agent_non_delivery", "--required-capability", "filesystem_read")
    todo = added["todo_id"]
    call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Verify a separate synthetic artifact.", "--task-class", "advancement_task",
        "--priority", "P2", "--action-kind", "validate", "--target-key", "verify-other-artifact",
        "--continuation-policy", "same_agent_non_delivery", "--required-capability", "filesystem_read")
    native, captures = tmp_path / "native", tmp_path / "captures"
    native.mkdir(mode=0o700)
    captures.mkdir(mode=0o700)
    event = setup(native, monkeypatch)
    cli = "/interaction_contract/cli_channel"
    selection = cli + "/selection_command"
    settlement = cli + "/settlement_plan"
    receipt_path = "/heartbeat_receipt"

    def read(navigation, *pointers):
        return read_selection(navigation["recovery"]["command"] +
            "".join(" --select " + shlex.quote(pointer) for pointer in pointers))

    def bind(turn):
        unbound = call("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", turn, "--context-delivery", "on", "--codex-context-dir", str(native),
            "--decision-output-root", str(captures), *(["--complete-turn"] if complete else []))
        unbound_page, facts = read(unbound, receipt_path, selection)
        assert facts[receipt_path]["turn_instance_id"] == turn
        assert facts[receipt_path]["settlement_binding_owed"] is True
        assert "settlement_identity" not in facts[receipt_path]
        deliver(native, event, [unbound_page])
        args = shlex.split(facts[selection]["command_args_template"].replace("{todo_id}", todo))
        bound = call(*args)
        _, facts = read(bound, receipt_path, settlement)
        receipt, plan = facts[receipt_path], facts[settlement]
        assert receipt["status"] == "upgraded" and receipt["closeout_required"] is True
        assert receipt["delivery_workspace_causality"]["requirement"] == "not_required"
        assert receipt["settlement_identity"] == plan["identity"]
        assert plan["identity"]["turn_instance_id"] == turn and plan["identity"]["todo_id"] == todo
        return bound, args, receipt, plan

    first, _, _, first_plan = bind("fixture-selection-original")
    index, = (i for i, step in enumerate(first_plan["ordered_steps"]) if step["kind"] == "durable_writeback")
    step = f"{settlement}/ordered_steps/{index}"
    authoring = step + "/vision_authoring"
    precondition = step + "/precondition"
    _, cold = read_selection(first["first_read_command"])
    assert cli + "/next_cli_actions" not in cold
    assert cold[precondition] == first_plan["ordered_steps"][index]["precondition"]
    _, fallback_actions = read(first, cli + "/next_cli_actions")
    assert first_plan["ordered_steps"][index]["command_template"] in fallback_actions[cli + "/next_cli_actions"]
    original_page, original = read(first, authoring, precondition)
    assert original[authoring] == first_plan["ordered_steps"][index]["vision_authoring"]
    deliver(native, event, [original_page])
    # Settle the original admitted Turn so that the next guard exercises
    # ordinary selection rather than correctly entering unfinished recovery.
    evidence_id = "evidence:synthetic-source-artifact"
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "The first source artifact was checked. Continue the verification."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "A local source artifact was available.",
            "observed_reality": "The artifact was checked; the remaining source still needs verification.",
            "retained": ["The same Todo remains open."], "evidence_refs": [evidence_id]},
    }))
    replacements = {"<validated_progress>": "synthetic_source_verified", "<scale>": "implementation",
                    "<outcome>": "outcome_progress", "<file>": str(vision)}
    args = shlex.split(first_plan["ordered_steps"][index]["semantic_closeout_command_template"])
    result = call(*(replacements.get(word, word) for word in args[args.index("refresh-state"):]),
        "--progress-result-class", "advanced", "--progress-evidence-id", evidence_id,
        "--no-global-sync", "--suppress-external-sinks")
    if result["settlement_progress"]["state"] == "spend_required":
        spend, = (step["command_template"] for step in first_plan["ordered_steps"] if step["kind"] == "quota_spend")
        args = shlex.split(spend)
        result = call(*args[args.index("quota"):])
    assert result["settlement_progress"]["state"] == "settled"

    current, args, receipt, plan = bind("fixture-selection-current")
    command = step + "/semantic_closeout_command_template"
    current_page, facts = read_selection(current["first_read_command"])
    assert authoring in current_page.get("previously_delivered_fields", [])
    assert precondition in current_page.get("previously_delivered_fields", [])
    assert precondition not in facts and cli + "/next_cli_actions" not in facts
    assert "previously_delivered_fields" in current["instruction"]
    assert facts[command] == plan["ordered_steps"][index]["semantic_closeout_command_template"]
    assert "fixture-selection-current" in facts[command]
    assert facts[settlement + "/identity/turn_instance_id"] == plan["identity"]["turn_instance_id"]
    deliver(native, event, [current_page])

    replay = call(*args)
    _, replay_facts = read(replay, receipt_path)
    assert replay_facts[receipt_path]["status"] == "replayed"
    assert replay_facts[receipt_path]["event_id"] == receipt["event_id"]
    replay_page, facts = read_selection(replay["first_read_command"])
    assert authoring in replay_page.get("previously_delivered_fields", [])
    assert facts[command] == plan["ordered_steps"][index]["semantic_closeout_command_template"]
    _, explicit = read(replay, authoring, precondition)
    assert explicit == original
@pytest.mark.parametrize("version", ["quota_settlement_plan_v1", "quota_settlement_plan_v2"])
@pytest.mark.parametrize("transition", ["same", "changed", "wrong_binding", "unknown_version", "rebound"])
def test_native_first_read_reuses_only_observed_writeback_precondition(
    tmp_path, monkeypatch, decision, version, transition,
):
    from test_codex_context_lifecycle import deliver, setup

    event = setup(tmp_path, monkeypatch)
    decision.update(agent_id="agent", should_run=True, normal_delivery_allowed=True, effective_action="normal_run")
    decision.update(registry=str(tmp_path / "registry.json"), runtime_root=str(tmp_path / "runtime"))
    contract = decision["interaction_contract"]
    contract["agent_channel"]["delivery_allowed"] = True
    source = contract["agent_channel"]["work_context"]["sources"][0]
    narrative = source["content"]["text"]
    source.update(source="goal_state", content={
        "state_file": "fixture-goal.md", "full_source": True, "text": narrative,
        "revision": "sha256:" + hashlib.sha256(narrative.encode()).hexdigest(),
    })
    goal_text = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    cli = contract["cli_channel"]
    cli["execution_preparation"] = {"state": "ready", "todo_id": "todo_current", "agent_id": "agent"}
    plan = cli["settlement_plan"]
    plan["schema_version"] = version
    text = "After independent validation, write back under the original identity and respect the current stop. " * 38
    step = {"kind": "durable_writeback", "owner": "agent", "precondition": text}
    plan["ordered_steps"] = [step]
    root = "/interaction_contract/cli_channel/settlement_plan"
    precondition = root + "/ordered_steps/0/precondition"
    command = root + "/ordered_steps/0/command_template"

    def capture(turn):
        identity = {"goal_id": "fixture", "agent_id": "agent", "todo_id": "todo_current",
                    "turn_instance_id": turn, "effect_id": f"fixture:agent:todo_current:{turn}"}
        decision["heartbeat_receipt"] = {"settlement_identity": identity}
        plan["identity"] = {**identity, "effect_id": "mismatch"} if turn == "next" and transition == "wrong_binding" else identity
        step["command_template"] = f"loopx refresh-state --turn-instance-id {turn}"
        directory = tmp_path / turn
        directory.mkdir(mode=0o700)
        prepared = codex_context.prepare_codex_context(decision, directory=str(tmp_path), capture_directory=directory,
            goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id=turn)
        return model_context_reference(native_context_capture_view(prepared.delivery, directory,
            context_directory=str(tmp_path), initial_read_plan=prepared.initial_read_plan))

    first = capture("first")
    page, facts = read_selection(first["first_read_command"])
    assert facts[precondition] == text and "previously_delivered_fields" not in page
    deliver(tmp_path, event, [page])

    if transition == "changed":
        step["precondition"] += " Await the updated external approval."
    elif transition == "unknown_version":
        plan["schema_version"] = "unknown"
    elif transition == "rebound":
        decision["registry"] = str(tmp_path / "another-registry.json")
    decision["goal_boundary"]["stop_condition"] = "Stop before publishing."
    second = capture("next")
    page, facts = read_selection(second["first_read_command"])
    assert facts[command] == "loopx refresh-state --turn-instance-id next"
    assert facts["/goal_boundary"] == decision["goal_boundary"]
    assert facts[root + "/identity/effect_id"] == plan["identity"]["effect_id"]
    if transition != "same":
        assert precondition not in page.get("previously_delivered_fields", [])
        assert facts[precondition] == step["precondition"]
        return

    assert set(page["previously_delivered_fields"]) == {goal_text, precondition}
    assert goal_text not in facts and precondition not in facts
    _, recovered = read_selection(second["recovery"]["command"] + " --select " + precondition)
    assert recovered[precondition] == text
    codex_context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostCompact"})
    page, facts = read_selection(second["first_read_command"])
    assert "previously_delivered_fields" not in page and facts[precondition] == text
    assert facts[goal_text] == narrative







@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_first_read_reuses_only_observed_write_scope(tmp_path, monkeypatch, provider):
    from test_codex_context_lifecycle import deliver, setup
    from test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _heartbeat_receipt_count,
        _initialize_fixture_authority, _run_cli, _spend_run_count, _write_fixture,
    )
    from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    configuration = json.loads(registry.read_text())
    scopes = [f"docs/areas/{i:03d}/entrypoints/public-fixture/**" for i in range(270)]
    configuration["goals"][0]["quota"]["allowed_slots"] = 6
    coordination = configuration["goals"][0]["coordination"]
    coordination["write_scope"] = scopes
    registry.write_text(json.dumps(configuration))
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    journal, captures = tmp_path / "journal", tmp_path / "captures"
    journal.mkdir(mode=0o700)
    captures.mkdir(mode=0o700)
    event = setup(journal, monkeypatch)
    scope_path = "/goal_boundary/write_scope"

    def cli(*args, registry_path=registry):
        code, payload = _run_cli(registry_path, runtime, *args, cwd=project)
        assert code == 0 and payload["ok"], payload
        return payload

    def binding(turn):
        return ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", turn)

    def capture(turn, registry_path=registry):
        return cli("quota", "should-run", "--codex-app", "--context-delivery", "auto", "--delta-delivery", "auto",
                   *binding(turn), "--decision-output-root", str(captures), "--codex-context-dir", str(journal),
                   registry_path=registry_path)

    def settle(turn, registry_path=registry):
        cli("refresh-state", *binding(turn), "--classification", "fixture_validated",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation", "--delivery-workspace-path", str(project),
            "--no-global-sync", "--suppress-external-sinks", registry_path=registry_path)
        spent = cli("quota", "spend-slot", *binding(turn), "--slots", "1", "--source", "heartbeat", "--execute",
                    registry_path=registry_path)
        assert spent["appended"]

    def read(command):
        pages, size, offset = [], 0, 0
        while command:
            words = shlex.split(command)
            stdout = subprocess.check_output([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
                *words[words.index("quota"):]], cwd=project)
            page = json.loads(stdout)
            assert page["ok"] and page["offset"] == offset, page
            pages.append(page)
            size += len(stdout)
            offset += len(page["content"].encode())
            command = page.get("next_command")
        assert pages[-1]["eof"] and offset == pages[-1]["total_bytes"]
        return pages, selected_context_values("".join(page["content"] for page in pages)), size

    def read_initial(view):
        pages, facts, size = read(view["first_read_command"])
        if continuation := view.get("remaining_read_command"):
            more_pages, more_facts, more_size = read(continuation)
            assert not facts.keys() & more_facts.keys()
            return pages + more_pages, facts | more_facts, size + more_size
        return pages, facts, size

    cli("todo", "claim", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--claimed-by", AGENT_ID)
    cold, cold_facts, _ = read_initial(capture("scope-cold"))
    assert cold_facts[scope_path] == scopes and len(cold) > 1
    assert scope_path not in cold[0].get("previously_delivered_fields", [])
    deliver(journal, event, cold[:1])
    settle("scope-cold")

    first_complete, complete_facts, _ = read_initial(capture("scope-unobserved"))
    assert scope_path not in first_complete[0].get("previously_delivered_fields", [])
    assert complete_facts[scope_path] == scopes
    deliver(journal, event, first_complete)
    settle("scope-unobserved")

    guard = "Check the current authorization before writing outside the project."
    configuration["goals"][0]["guards"] = [guard]
    registry.write_text(json.dumps(configuration))
    current = capture("scope-warm")
    _, source_ref = read_context_read_reference(current["recovery"]["reference_file"])
    original = Path(source_ref["path"])
    raw = original.read_bytes()
    boundary = json.loads(raw)["goal_boundary"]
    warm, warm_facts, warm_bytes = read_initial(current)
    _, restored_scope, scope_bytes = read(current["recovery"]["command"] + " --select " + scope_path)
    assert restored_scope[scope_path] == scopes
    codex_context.codex_context_hook(str(journal), {**event, "hook_event_name": "PostCompact"})
    restored, restored_facts, restored_bytes = read_initial(current)
    assert scope_path not in restored[0].get("previously_delivered_fields", [])
    assert restored_facts[scope_path] == scopes and original.read_bytes() == raw
    deliver(journal, event, restored)
    settle("scope-warm")

    alternate_registry = registry.with_name("alternate-registry.json")
    alternate_registry.write_bytes(registry.read_bytes())
    rebound, rebound_facts, _ = read_initial(capture("scope-rebound", alternate_registry))
    assert scope_path not in rebound[0].get("previously_delivered_fields", [])
    assert rebound_facts[scope_path] == scopes
    settle("scope-rebound", alternate_registry)

    coordination["write_scope"] = scopes[:-1] + ["docs/areas/fresh-scope/**"]
    registry.write_text(json.dumps(configuration))
    changed, changed_facts, _ = read_initial(capture("scope-revised"))
    assert scope_path not in changed[0].get("previously_delivered_fields", [])
    assert changed_facts[scope_path] == coordination["write_scope"]
    settle("scope-revised")
    off = cli("quota", "should-run", "--codex-app", "--context-delivery", "off", *binding("scope-off"))
    assert off["goal_boundary"]["write_scope"] == coordination["write_scope"]
    assert "first_read_command" not in off
    assert _heartbeat_receipt_count(runtime, "scope-warm") == 1 and _spend_run_count(runtime) == 5

    print(json.dumps({"provider": provider, "warm_bytes": warm_bytes, "recovery_bytes": restored_bytes,
                      "warm_pages": len(warm), "recovery_pages": len(restored), "scope_selection_bytes": scope_bytes}))
    assert scope_path in warm[0].get("previously_delivered_fields", []) and scope_path not in warm_facts
    assert warm_facts["/goal_boundary/guards"] == [guard]
    for key, value in boundary.items():
        if key != "write_scope":
            assert warm_facts[f"/goal_boundary/{key}"] == value
    assert warm_facts["/interaction_contract/cli_channel/settlement_plan/identity/turn_instance_id"] == "scope-warm"
    restored_selection = {key: value for key, value in restored_facts.items()
        if key != "/goal_boundary" and not key.startswith(scope_path)}
    observed = warm[0].get("previously_delivered_fields", [])
    assert restored_selection == {
        **warm_facts,
        **{key: value for key, value in restored_selection.items()
           if any(key == pointer or key.startswith(pointer + "/") for pointer in observed)},
    }
    assert restored_bytes - warm_bytes > 10_000


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("revision", ["unrelated", "selected_evidence"])
@pytest.mark.parametrize("observation", ["complete", "partial"])
def test_native_first_read_reuses_only_the_observed_selected_todo_note(
    tmp_path, monkeypatch, provider, revision, observation,
):
    from test_codex_context_lifecycle import deliver, setup
    from test_quota_settlement_cli import (
        AGENT_ID, ALTERNATIVE_TODO_ID, GOAL_ID, TODO_ID, _heartbeat_receipt_count,
        _initialize_fixture_authority, _run_cli, _spend_run_count, _write_fixture,
    )
    from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    configuration = json.loads(registry.read_text())
    configuration["goals"][0]["quota"]["allowed_slots"] = 6
    registry.write_text(json.dumps(configuration))
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    if provider == "sqlite":
        # Exercise the full canonical row when its text exceeds the selected
        # summary's display limit. The File fixture covers the referenced row.
        state.write_text(state.read_text().replace(
            "[P1] Validate and settle the selected delivery.",
            "[P1] " + "Validate and settle the selected delivery. " * 12,
        ))
    with state.open("a") as handle:
        handle.write(f"- [ ] [P2] Validate the alternate delivery.\n"
                     f"  <!-- loopx:todo todo_id={ALTERNATIVE_TODO_ID} status=open "
                     "task_class=advancement_task action_kind=validate -->\n")
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    journal = tmp_path / "journal"
    journal.mkdir()
    event = setup(journal, monkeypatch)
    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)

    def cli(*args):
        code, payload = _run_cli(registry, runtime, *args, cwd=project)
        assert code == 0 and payload["ok"], payload
        return payload

    def update(todo_id, note, action):
        key = f"lease-{todo_id}"
        if action == "seed":
            lease = cli("todo", "claim", "--goal-id", GOAL_ID, "--todo-id", todo_id,
                        "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID,
                        "--task-lease-idempotency-key", key)["lease"]
        else:
            lease = cli("task-lease", "inspect", "--goal-id", GOAL_ID, "--todo-id", todo_id)["lease"]
        cli("todo", "update", "--goal-id", GOAL_ID, "--todo-id", todo_id, "--agent-id", AGENT_ID,
            "--note", note, "--task-lease-idempotency-key", key,
            "--task-lease-expected-version", str(lease["version"]), "--update-operation-id", f"{action}-{todo_id}")

    def binding(turn, todo_id=TODO_ID):
        return ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                "--todo-id", todo_id, "--turn-instance-id", turn)

    def capture(turn, todo_id=TODO_ID):
        return cli("quota", "should-run", "--codex-app", "--context-delivery", "on",
                   "--delta-delivery", "auto", *binding(turn, todo_id),
                   "--decision-output-root", str(captures), "--codex-context-dir", str(journal))

    def settle(turn):
        cli("refresh-state", *binding(turn), "--classification", "fixture_validated",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation", "--delivery-workspace-path", str(project),
            "--no-global-sync", "--suppress-external-sinks")
        spent = cli("quota", "spend-slot", *binding(turn), "--slots", "1", "--source", "heartbeat", "--execute")
        assert spent["appended"]

    def read(command):
        pages, size, offset = [], 0, 0
        while command:
            args = shlex.split(command)
            stdout = subprocess.check_output([sys.executable, "-m", "loopx.entrypoint",
                "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                *args[args.index("quota"):]], cwd=project)
            page = json.loads(stdout)
            assert page["ok"] and page["offset"] == offset, page
            pages.append(page)
            size += len(stdout)
            offset += len(page["content"].encode())
            command = page.get("next_command")
        assert pages[-1]["eof"] and offset == pages[-1]["total_bytes"]
        return pages, selected_context_values("".join(p["content"] for p in pages)), size

    note = "原始交付条件与验收材料。Keep the cited acceptance evidence. " * 260
    update(TODO_ID, note.rstrip(), "seed")
    first = capture("note-cold")
    cold, cold_facts, _ = read(first["first_read_command"])
    pointer = next(p for p in cold_facts if p.endswith("/content/todo/note"))
    assert cold_facts[pointer] == note.rstrip()
    updated_at_pointer = pointer.removesuffix("/note") + "/updated_at"
    cold_updated_at = read(first["recovery"]["command"] + " --select " + updated_at_pointer)[1][updated_at_pointer]
    complete = observation == "complete"
    assert len(cold) > 1 and len(note.encode()) > len(cold[0]["content"].encode())
    deliver(journal, event, cold if complete else cold[:1])
    assert (pointer in codex_context._state(journal).get("consumed_pointers", {})) == complete
    settle("note-cold")
    if revision == "unrelated":
        update(ALTERNATIVE_TODO_ID, "Prepare an unrelated result.", "seed")
    else:
        # Canonical timestamps use seconds. Exercise a distinct current-Todo
        # revision while leaving the note itself byte-for-byte unchanged.
        import time

        time.sleep(1.05)
        lease = cli("task-lease", "inspect", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)["lease"]
        cli("todo", "update", "--goal-id", GOAL_ID, "--todo-id", TODO_ID, "--agent-id", AGENT_ID,
            "--evidence", "result:independent-verification", "--task-lease-idempotency-key", f"lease-{TODO_ID}",
            "--task-lease-expected-version", str(lease["version"]), "--update-operation-id", "revise-evidence")
        # The later task switch retains the same independent acceptance path.
        update(ALTERNATIVE_TODO_ID, "Prepare an unrelated result.", "seed")

    warm_note = note.rstrip()
    if not complete:
        warm_note += " Appended after an incomplete observation."
        update(TODO_ID, warm_note, "append-after-partial")
    second = capture("note-warm")
    _, original = read_context_read_reference(second["recovery"]["reference_file"])
    source_path = Path(original["path"])
    source = source_path.read_bytes()
    context = json.loads(source)["interaction_contract"]["agent_channel"]["work_context"]
    assert context["selected_todo_text_verified"]
    assert context.get("selected_todo_ref") == ("selected_todo" if provider == "file" else None)
    source_pointer = pointer.removesuffix("/todo/note")
    _, current_source, _ = read(second["recovery"]["command"] + " --select " + source_pointer)
    current_authority = current_source[source_pointer]["authority_read"]
    cold_authority = cold_facts[source_pointer + "/authority_read"]
    assert current_authority["provider_revision"] != cold_authority["provider_revision"]
    warm, facts, warm_bytes = read(second["first_read_command"])
    assert current_source[source_pointer]["todo"]["note"] == warm_note
    if revision == "selected_evidence":
        current_todo = current_source[source_pointer]["todo"]
        assert current_todo["evidence"] == "result:independent-verification"
        assert current_todo["updated_at"] != cold_updated_at
    codex_context.codex_context_hook(str(journal), {**event, "hook_event_name": "PostCompact"})
    recovered, restored, fallback_bytes = read(second["first_read_command"])
    assert pointer not in recovered[0].get("previously_delivered_fields", [])
    assert restored[pointer] == warm_note and source_path.read_bytes() == source

    assert _spend_run_count(runtime) == 1
    deliver(journal, event, recovered)
    settle("note-warm")
    revised = "Revised acceptance check. " + note.rstrip()
    update(TODO_ID, revised, "revise")
    update(ALTERNATIVE_TODO_ID, revised, "revise")
    corrected, current, _ = read(capture("note-corrected")["first_read_command"])
    assert current[pointer] == revised and pointer not in corrected[0].get("previously_delivered_fields", [])
    deliver(journal, event, corrected)
    settle("note-corrected")

    expected_spends = 3
    if revision == "selected_evidence":
        def appended_turn(turn, prefix, suffix):
            update(TODO_ID, prefix + suffix, turn)
            guard = capture(turn)
            pages, _, wire_bytes = read(guard["first_read_command"])
            body = json.loads("".join(page["content"] for page in pages))
            appended = next(row for row in body["selections"] if row["pointer"] == pointer)
            assert appended == {"pointer": pointer, "value": suffix,
                                "presentation": "append_to_observed_text",
                                "prefix_bytes": len(prefix.encode("utf-8"))}
            assert pointer not in pages[0].get("previously_delivered_fields", [])
            _, exact, _ = read(guard["recovery"]["command"] + " --select " + pointer)
            assert exact[pointer] == prefix + suffix
            reference = Path(pages[0]["reference_file"])
            _, metadata = read_context_read_reference(str(reference))
            fallback_name = metadata["observed_read"]["fallback_reference"]["filename"]
            fallback_command = guard["first_read_command"].replace(
                shlex.quote(str(reference)), shlex.quote(str(reference.with_name(fallback_name))))
            _, _, full_bytes = read(fallback_command)
            assert full_bytes - wire_bytes > 10_000
            print(json.dumps({"provider": provider, "prior_read": observation, "turn": turn,
                              "full_bytes": full_bytes, "append_bytes": wire_bytes}))
            return guard, pages

        _, appended = appended_turn("note-append", revised, " 新增决策 🧪，沿用前面的验收。")
        note_proof = codex_context._state(journal)["consumed_pointers"][pointer]
        assert len(appended) > 1
        deliver(journal, event, appended[-1:])
        assert codex_context._state(journal)["consumed_pointers"][pointer] == note_proof
        deliver(journal, event, appended[:-1])
        assert codex_context._state(journal)["consumed_pointers"][pointer] != note_proof
        settle("note-append")

        revised += " 新增决策 🧪，沿用前面的验收。"
        second_suffix = " Second addition with new evidence."
        chained, _ = appended_turn("note-append-next", revised, second_suffix)
        revised += second_suffix
        codex_context.codex_context_hook(str(journal), {**event, "hook_event_name": "PostCompact"})
        fallback, exact, _ = read(chained["first_read_command"])
        assert exact[pointer] == revised
        assert pointer not in fallback[0].get("previously_delivered_fields", [])
        settle("note-append-next")
        update(ALTERNATIVE_TODO_ID, revised, "copy-final-note")
        expected_spends += 2

    alternate, other, _ = read(capture("note-alternate", ALTERNATIVE_TODO_ID)["first_read_command"])
    assert other[pointer] == revised and other["/selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert pointer not in alternate[0].get("previously_delivered_fields", [])
    assert _heartbeat_receipt_count(runtime, "note-warm") == 1 and _spend_run_count(runtime) == expected_spends

    print(json.dumps({"provider": provider, "revision": revision, "observation": observation,
                      "warm_bytes": warm_bytes, "recovery_bytes": fallback_bytes,
                      "warm_pages": len(warm), "recovery_pages": len(recovered)}))
    assert facts[source_pointer + "/authority_read"] == current_authority
    if complete:
        assert pointer in warm[0].get("previously_delivered_fields", []) and pointer not in facts
        restored_note = {key: value for key, value in restored.items() if key != pointer}
        observed = warm[0].get("previously_delivered_fields", [])
        assert restored_note == {
            **facts,
            **{key: value for key, value in restored_note.items()
               if any(key == address or key.startswith(address + "/") for address in observed)},
        }
        assert fallback_bytes - warm_bytes > 10_000
    else:
        assert pointer not in warm[0].get("previously_delivered_fields", [])
        assert facts[pointer] == restored[pointer] == warm_note
