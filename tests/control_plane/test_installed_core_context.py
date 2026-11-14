"""Installed CLI context continuity; native events are protocol fixtures, not a model."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

import pytest

from test_core_distribution import AGENT, GOAL, _command, _environment
from test_core_distribution import core_python as core_python
from test_core_distribution import task as task
from test_core_distribution import _run, _spends


@pytest.mark.parametrize("explicit_route", [False, True])
def test_installed_composed_settlement_uses_core_entrypoint(core_python, tmp_path, monkeypatch, explicit_route):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _configure_read_only_todo, _initialize_fixture_authority,
        _spend_run_count, _write_fixture,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    cli = Path(core_python).parent / ("loopx.exe" if os.name == "nt" else "loopx")
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    if explicit_route:
        prefix += ["--cli-bin", str(cli)]

    def command(*args):
        return json.loads(_run(core_python, [*prefix, *args], project).stdout)

    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", "installed-composed-settlement"]
    guard = command("quota", "should-run", "--codex-app", "--context-delivery", "off", *binding)
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert _spend_run_count(runtime) == 0
    writeback = ["refresh-state", *binding, "--classification", "installed_composed_delivery",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks",
        "--settle-turn", "--settlement-receipt"]
    first = command(*writeback)
    assert first["settlement_identity"] == identity and first["settlement_progress"]["state"] == "settled"
    assert first["quota_spend"]["appended"] is True and _spend_run_count(runtime) == 1
    assert "settlement_owed" not in first
    assert [r["step_kind"] for r in first["settlement_result"]["receipts"]] == [
        "validation", "durable_writeback", "quota_spend"]
    replay = command(*writeback)
    assert replay["idempotent_replay"] is True and replay["settlement_identity"] == identity
    assert replay["settlement_result"] == first["settlement_result"] and _spend_run_count(runtime) == 1


def test_installed_lifecycle_review_recovers_a_late_dependent_successor(core_python, task):
    workspace, runtime, registry, arguments = task
    initial = json.loads(_run(core_python, arguments, workspace).stdout)
    assert initial["status"] == "committed"
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    def command(*args):
        return json.loads(_run(core_python, [*prefix, *args], workspace).stdout)

    original = command("todo", "list", "--goal-id", GOAL,
        "--todo-id", initial["receipt"]["lineage"]["todo_id"])["todo"]
    turn = "installed-lifecycle-review"
    admission = ["quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--host-surface", "generic_cli", "--scheduler-owner", "outer_controller",
        "--execution-mode", "isolated_headless", "--context-delivery", "off",
        "--turn-instance-id", turn]
    guard = command(*admission)
    obligation = guard["autonomous_replan_obligation"]["obligation_id"]
    # Canonical updates have whole seconds. Same-second review-bound recovery
    # is pinned by the replan semantics behavior test. This installed path
    # still qualifies a successor stamped on a later whole second.
    time.sleep(1.05)
    successor = command("todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--claimed-by", AGENT, "--text", "Independently recheck the retained squares report.",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--target-key", "retained-squares-check", "--resume-when", f"todo_done:{original['todo_id']}",
        "--replan-obligation-id", obligation, "--operation-id", "installed-dependent-successor")
    recovered = command(*admission)
    packet = recovered["autonomous_replan_obligation"]["settlement_action_packet"]
    assert packet["settlement_only"] is True
    assert packet["successor_todo_id"] == successor["todo_id"]
    assert recovered["heartbeat_receipt"]["settlement_identity"] == guard["heartbeat_receipt"]["settlement_identity"]
    vision = workspace / "recovery-vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": {"acceptance_summary": "Check the retained squares through the real dependent successor."},
        "todo_delta": [f"retain:{successor['todo_id']}"], "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "The completed report still needed a dependent recheck.",
            "observed_reality": "The checked report is retained and a real runnable successor is bound to this review.",
            "retained": ["Original squares, scope and settled work identity."],
            "evidence_refs": ["artifact:squares.json"],
        }}))
    writeback = command("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--classification", "installed_successor_recovery", "--turn-instance-id", turn,
        "--replan-obligation-id", obligation,
        "--agent-vision-json", str(vision), "--delivery-batch-scale", "multi_surface",
        "--delivery-outcome", "outcome_progress")
    assert writeback["ok"] is True
    spent = command("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
        "--replan-obligation-id", obligation, "--turn-instance-id", turn,
        "--slots", "1", "--source", "heartbeat", "--execute")
    assert spent["settlement_progress"]["state"] == "settled"
    assert _spends(runtime) == 2
    retained = command("todo", "list", "--goal-id", GOAL, "--todo-id", original["todo_id"])["todo"]
    assert retained["completion_turn_key"] == original["completion_turn_key"]
    assert retained.get("no_followup") == original.get("no_followup")
    command("todo", "claim", "--goal-id", GOAL, "--todo-id", successor["todo_id"],
        "--agent-id", AGENT, "--claimed-by", AGENT, "--claim-operation-id", "installed-successor-work")
    followup = json.loads(_run(core_python, [*arguments, "--todo-id", successor["todo_id"],
        "--turn-instance-id", "installed-successor-work"], workspace).stdout)
    assert followup["status"] == "committed" and _spends(runtime) == 3
    assert (workspace / "host-invocations").read_text().splitlines() == ["called", "called"]


def test_installed_empty_operation_inbox_keeps_the_workspace_provider_unloaded(core_python, tmp_path):
    """No action store means no operation handoff; a present store needs its provider."""
    script = '''
import importlib.util, json, sys
from pathlib import Path
assert importlib.util.find_spec("loopx.chat_action_store") is None
from loopx.control_plane.collaboration.operation_handoff import pending_operation_handoffs
root = Path(sys.argv[1])
empty = pending_operation_handoffs(root, "synthetic-goal", "synthetic-agent", cursor_scope="synthetic-scope")
assert empty == {"items": [], "pending_count": 0, "overflow": None, "next_cursor": None}
assert "loopx.chat_action_store" not in sys.modules
store = root / "chat/actions/actions.json"
store.parent.mkdir(parents=True)
store.write_text("{}")
try:
    pending_operation_handoffs(root, "synthetic-goal", "synthetic-agent", cursor_scope="synthetic-scope")
except ModuleNotFoundError as error:
    assert error.name == "loopx.chat_action_store"
else:
    raise AssertionError("a present operation store cannot silently disappear without its provider")
print(json.dumps({"empty": empty, "present_store_requires_provider": True}))
'''
    result = subprocess.run([str(core_python), "-I", "-c", script, str(tmp_path / "runtime")],
        env=_environment(core_python), cwd=tmp_path, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["present_store_requires_provider"] is True


def test_installed_context_recovers_partial_delivery_revisions_and_compaction(core_python, tmp_path):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    source = tmp_path / "original.json"
    event = {"session_id": "core-context-session", "cwd": str(tmp_path)}
    environment = _environment(core_python, {"CODEX_THREAD_ID": event["session_id"]})
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    def command(arguments, *, stdin=None, expected=0):
        result = subprocess.run(_command(core_python, [*prefix, *arguments]),
            input=None if stdin is None else json.dumps(stdin), env=environment,
            cwd=tmp_path, capture_output=True, text=True, timeout=45)
        assert result.returncode == expected, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    def hook(name, **fields):
        return command(["quota", "context-hook", "--context-delivery", "on", "--codex-app",
            "--codex-context-dir", str(directory)], stdin={**event, "hook_event_name": name, **fields})

    def state():
        return json.loads((directory / "session.json").read_bytes())

    def acknowledge(pages):
        for page in pages:
            assert hook("PostToolUse", tool_use_id=f"page-{page['offset']}",
                tool_response=json.dumps(page)) == {}

    row = {"schema_version": "todo_item_v0", "todo_id": "todo_context", "text": "Verify the report.",
        "note": "Preserve the complete source and its constraints. " * 150 + "Stop before publication.",
        "content_revision": "revision:one", "claimed_by": AGENT}
    original = {"ok": True, "goal_id": GOAL, "agent_identity": {"agent_id": AGENT},
        "heartbeat_receipt": {"turn_instance_id": "context-fixture"},
        "stop": "Stop before publication.", "opaque_counter": 9007199254740993,
        "agent_todo_summary": {"schema_version": "todo_summary_v0",
            "first_open_items": [row], "backlog_items": [copy.deepcopy(row)],
            "claimed_open_items": [copy.deepcopy(row)]}}

    def project(payload, setting="on"):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        source.write_bytes(raw)
        arguments = ["quota", "context-project", "--capture-file", str(source),
            "--capture-sha256", hashlib.sha256(raw).hexdigest(), "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", "context-fixture",
            "--context-delivery", setting, "--codex-app", "--codex-context-dir", str(directory)]
        if setting == "on":
            arguments += ["--max-page-output-bytes", "4000"]
        return command(arguments)

    def read_required(wire, payload, *, budget=4000):
        arguments = shlex.split(wire.get("recovery", wire)["command"])
        pages, contents = [], b""
        while True:
            page = command(arguments[arguments.index("quota"):])
            assert page["ok"] and page["offset"] == len(contents)
            if budget is not None:
                assert len((json.dumps(page, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= budget
            contents += page["content"].encode()
            pages.append(page)
            if page["eof"]:
                break
            arguments = shlex.split(page["next_command"])
        required = json.loads(contents)
        assert required["stop"] == payload["stop"]
        assert required["opaque_counter"] == 9007199254740993
        for lane, expected in payload["agent_todo_summary"].items():
            if lane == "schema_version":
                continue
            values = []
            for item in required["agent_todo_summary"][lane]:
                if "context_ref" in item:
                    item_source = required
                    for part in item["context_ref"].split("/")[1:]:
                        item_source = item_source[int(part)] if isinstance(item_source, list) else item_source[part]
                    values.append(item_source)
                else:
                    values.append(item)
            assert values == expected
        return pages

    assert hook("SessionStart", source="startup") == {}
    # Feature-off preserves even unknown semantics, without acknowledging them.
    off_pages = read_required(project(original, "off"), original, budget=None)
    assert json.loads("".join(page["content"] for page in off_pages)) == original
    assert "candidate" not in state()
    first = project(original)
    assert first["mode"] == "full"
    pages = read_required(first, original)
    assert len(pages) > 1
    acknowledge(pages[:1])
    assert "candidate" not in state()
    # Lost ACK: publishing another view cannot make an incomplete baseline valid.
    unacknowledged = project(original)
    assert unacknowledged["mode"] == "full"
    acknowledge(read_required(unacknowledged, original))
    assert "candidate" in state()
    warm = project(original)
    assert warm["mode"] in {"delta", "full"}  # Complete framing may make full cheaper.
    acknowledge(read_required(warm, original))

    revised = copy.deepcopy(original)
    revised["stop"] = "Stop all publication and await revised approval."
    for lane in ("first_open_items", "backlog_items", "claimed_open_items"):
        revised["agent_todo_summary"][lane][0].update(content_revision="revision:two",
            note="Contrary evidence changed the work. Preserve the revised constraint.")
    changed = project(revised)
    acknowledge(read_required(changed, revised))
    before = state()
    assert hook("PostCompact") == {}
    assert "candidate" not in state() and state()["recovery"] == before["recovery"]
    compacted = project(revised)
    assert compacted["mode"] == "full"
    acknowledge(read_required(compacted, revised))

    # The advertised reader rejects changed source bytes rather than trusting a digest label.
    arguments = shlex.split(compacted["recovery"]["command"])
    capture = state()["recovery"]["path"]
    pinned = Path(capture)
    pinned.write_bytes(pinned.read_bytes() + b" ")
    refused = command(arguments[arguments.index("quota"):], expected=1)
    assert refused["ok"] is False and "content" not in refused
    assert not registry.exists() and not runtime.exists()
