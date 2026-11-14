"""Current requirements stay usable independently of native Delta continuity."""
from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.cli_commands.quota_capture import read_context_capture
from loopx.control_plane.turn_driver import codex_context as native
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference, write_context_read_reference
from test_codex_context_lifecycle import setup, project, deliver, read_pages
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_progressive_native_context import payload, outcome


def required_document(packet):
    _, reference = read_context_read_reference(packet["recovery"]["reference_file"])
    return json.loads(Path(reference["path"]).read_bytes())


def selected_pages(reference, pointer, *, page_bytes=1024):
    reference_file = write_context_read_reference(Path(reference["path"]), reference["sha256"])
    request = Namespace(format="json", reference_file=str(reference_file), select=[pointer],
        offset=0, page_bytes=page_bytes, max_page_output_bytes=6000)
    pages = []
    while True:
        page = read_context_capture(request)
        assert page["ok"], page
        pages.append(page)
        if page["eof"]:
            return pages
        request.reference_file = page["reference_file"]
        request.select = None
        request.offset = page["next_offset"]


@pytest.mark.parametrize("profile", [None, "generic_cli", "trae_app", "antigravity_app", "codex_app_heartbeat"])
def test_progressive_activation_does_not_require_native_continuity(profile):
    result = native.context_delivery_activation("auto", runtime_profile=profile, delta_delivery="off")
    assert result["progressive_enabled"] is True
    assert result["enabled"] is False
    assert result["scenario"] is None
    if profile in (None, "generic_cli", "trae_app", "antigravity_app"):
        assert native.context_delivery_activation("auto", runtime_profile=profile)["enabled"] is False
    else:
        assert native.context_delivery_activation("auto", runtime_profile=profile)["enabled"] is True
    assert native.context_delivery_activation("off", runtime_profile=profile)["progressive_enabled"] is False


@pytest.mark.parametrize("host", [(), ("--runtime-profile", "generic_cli"), ("--trae_app",), ("--antigravity_app",)])
@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_generic_read_only_cli_preserves_default_and_opt_in_without_admission(acceptance_goal, host, output_format):
    import os
    import sys

    from tests.source_cli import run_source_cli

    workspace, document, cli, _ = acceptance_goal
    registry, runtime = workspace.parent / "registry.json", workspace.parent / "runtime"
    environment = {**os.environ, "LOOPX_PYTHON": sys.executable}
    environment.pop("LOOPX_CONTEXT_DELIVERY", None)

    def guard(*arguments):
        # The generic acceptance fixture runs machine-facing raw CLI reads.
        # This case exercises the omitted model-facing option instead.
        response = run_source_cli(["--registry", str(registry), "--runtime-root", str(runtime),
            "--format", output_format, *arguments], env=environment, check=False, timeout=30)
        assert response.returncode == 0, response.stdout + response.stderr
        return response.stdout

    for i in range(8):
        document.write_text(json.dumps({"outcome_id": "export", "owner_agent_id": "agent-a",
            "todo_ids": ["todo_export"], "objective": "Deliver the export.",
            "requirements": [{"id": "tail", "description": "Stop before publication."}],
            "source": {"ref": f"source:{i}", "summary": ("Prior supporting observation. " * 16).strip()}}))
        code, result = cli("refine", "--agent-id", "agent-a", "--document", str(document),
            "--expected-provider-revision", cli("inspect")[1]["provider_revision"],
            "--operation-id", f"observation-{i}", "--execute")
        assert code == 0, result
    before = cli("inspect")[1]
    args = ("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a", *host)
    if output_format == "markdown":
        inline = guard(*args)
        assert inline.startswith("# LoopX Quota Should Run\n")
        assert "Stop before publication." in inline and "Prior supporting observation" in inline
        assert "quota context-read" not in inline
        shown = guard(*args, "--turn-envelope")
        packet = json.loads(shown.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    else:
        packet = json.loads(guard(*args))
    assert packet["ok"] is True, packet
    required = required_document(packet)
    assert "heartbeat_receipt" not in required, "plain observation must not invent a work Turn"
    assert "target" not in packet, "stateless presentation must not invent native continuity"
    assert "context_details" in required
    details = required["context_details"]
    assert details["source_path"] == details["path"]
    source = json.loads(Path(details["source_path"]).read_bytes())
    assert "Stop before publication." in json.dumps(required)
    assert "Prior supporting observation" not in json.dumps(required)
    assert "Prior supporting observation" in json.dumps(source)
    off = guard(*args, "--context-delivery", "off")
    assert "Prior supporting observation" in off
    if output_format == "json":
        assert {"context_details", "reference_file", "contract_binding"}.isdisjoint(json.loads(off))
    else:
        assert off.startswith("# LoopX Quota Should Run\n")
        assert "Stop before publication." in off and "quota context-read" not in off
    assert cli("inspect")[1] == before, "presentation must not mutate acceptance or its provider revision"


def test_observed_selected_reads_survive_delta_but_not_change_or_compaction(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    original = payload()
    _, first = project(tmp_path, payload=original)
    supporting = outcome(first["context"])["supporting_material_pointer"] + "/sources"
    pages = selected_pages(first["context"]["context_details"], supporting)
    assert len(pages) > 1
    deliver(tmp_path, event, pages[1:])
    assert supporting not in native._state(tmp_path).get("consumed_pointers", {}), "EOF alone is not full selection coverage"
    deliver(tmp_path, event, pages[:1])
    state = native._state(tmp_path)
    assert supporting in state["consumed_pointers"]
    assert supporting in state["confirmed_read_sections"]
    assert "candidate" not in state, "selection coverage is not native full-input retention"
    _, warm = project(tmp_path, "warm", payload=original)
    current = json.loads(Path(warm["recovery"]["path"]).read_bytes())
    assert warm["mode"] == "delta"
    assert supporting not in current["context_details"]["unread_context_index"]["pointers"]
    # The current full source always contains the previously unread review.
    source = json.loads(Path(current["context_details"]["source_path"]).read_bytes())
    assert outcome(source)["requirements"][0]["agent_review"]["note"].startswith("Previous failure")
    changed = deepcopy(original)
    outcome(changed)["sources"][0]["summary"] += " A new contrary observation."
    _, update = project(tmp_path, "changed", payload=changed)
    updated = json.loads(Path(update["recovery"]["path"]).read_bytes())
    assert supporting in updated["context_details"]["unread_context_index"]["pointers"]
    native.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreCompact"})
    assert not native._state(tmp_path).get("consumed_pointers")
    _, fresh = project(tmp_path, "fresh", payload=original)
    assert fresh["mode"] == "full"
    assert supporting in fresh["context"]["context_details"]["unread_context_index"]["pointers"]


def test_damaged_or_forged_selected_delivery_never_acknowledges_a_section(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    _, first = project(tmp_path, payload=payload())
    supporting = outcome(first["context"])["supporting_material_pointer"] + "/sources"
    pages = selected_pages(first["context"]["context_details"], supporting)
    broken = deepcopy(pages)
    broken[-1]["content"] = broken[-1]["content"][:-1] + "!"
    deliver(tmp_path, event, broken)
    assert supporting not in native._state(tmp_path).get("consumed_pointers", {})
    # Extra output fields cannot smuggle an observed-read assertion through a
    # legitimate full page's ordinary body verification.
    full = read_pages(tmp_path, first)
    forged = [{**page, "read_sections": {"/forged": "a" * 64},
        "source_sha256": native._state(tmp_path)["source_recovery"]["sha256"]} for page in full]
    deliver(tmp_path, event, forged)
    assert "/forged" not in native._state(tmp_path).get("consumed_pointers", {})
    assert supporting not in native._state(tmp_path).get("consumed_pointers", {}), "required input omits supporting material"
    deliver(tmp_path, event, pages)
    assert supporting in native._state(tmp_path)["consumed_pointers"]


@pytest.mark.parametrize("address", ["/numbers", "/numbers/0", "/object/0"])
def test_selected_read_observation_preserves_exact_source_number_identity(tmp_path, monkeypatch, address):
    event = setup(tmp_path, monkeypatch)
    source = payload()
    source["numbers"] = [1.0, -0.0, 2**60 + 1]
    source["object"] = {"0": 1.0}
    _, first = project(tmp_path, payload=source)
    deliver(tmp_path, event, selected_pages(first["context"]["context_details"], address))
    observed = native._state(tmp_path)["consumed_pointers"][address]
    changed = deepcopy(source)
    changed["numbers"][0] = changed["object"]["0"] = 1
    _, second = project(tmp_path, "second", payload=changed)
    deliver(tmp_path, event, selected_pages(second["context"]["context_details"], address))
    assert native._state(tmp_path)["consumed_pointers"][address] != observed


def test_default_progressive_does_not_admit_conflicting_host_declarations(acceptance_goal):
    _, _, cli, run = acceptance_goal
    before = cli("inspect")[1]
    code, rejected = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a",
        "--codex-app", "--runtime-profile", "generic_cli")
    assert code != 0 and rejected["ok"] is False
    assert "cannot be combined" in str(rejected)
    assert "heartbeat_receipt" not in rejected
    assert cli("inspect")[1] == before


@pytest.mark.parametrize("complete", [False, True], ids=["quota", "complete-turn"])
def test_saved_context_progressive_recovery_keeps_original_turn_without_native_setup(tmp_path, monkeypatch, complete):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import handle_native_context_command
    from loopx.control_plane.turn_driver.decision_capture import write_decision_capture
    import hashlib

    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    original = {**payload(), "agent_identity": {"agent_id": "agent"},
        "heartbeat_receipt": {"turn_instance_id": "original-work"}}
    if complete:
        from loopx.control_plane.quota.turn_envelope import compose_turn_decision
        from test_replan_host_context_projection import _quota_payload

        source = _quota_payload()
        source["planning_horizon"] = original["planning_horizon"]
        source["heartbeat_receipt"] = original["heartbeat_receipt"]
        original = compose_turn_decision(source)
    path = tmp_path / "captured.json"
    write_decision_capture(path, original)
    saved = path.read_bytes()
    args = build_parser().parse_args(["--format", "json", "quota", "context-project",
        "--goal-id", original["goal_id"], "--agent-id", original["agent_identity"]["agent_id"],
        "--turn-instance-id", original["heartbeat_receipt"]["turn_instance_id"],
        "--capture-file", str(path), "--capture-sha256", hashlib.sha256(saved).hexdigest(),
        "--context-delivery", "auto", "--delta-delivery", "off"])
    result = []
    code = handle_native_context_command(args, print_payload=lambda value, *_: result.append(value))
    assert code == 0
    current = required_document(result[0])
    assert current["heartbeat_receipt"] == original["heartbeat_receipt"]
    assert "sources" not in outcome(current)
    required, source = outcome(current)["requirements"][0], outcome(original)["requirements"][0]
    assert required["description"] == source["description"]
    assert required["agent_review"]["conclusion"] == "unmet" and "note" not in required["agent_review"]
    assert json.loads(Path(current["context_details"]["path"]).read_bytes()) == original
    assert path.read_bytes() == saved and not list(tmp_path.rglob("session.json"))
    if complete:
        # The outer file digest can match even when the inner Turn binding no longer does.
        original["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["revision"] += 1
        malformed = tmp_path / "malformed.json"
        args.capture_file = str(malformed)
        args.capture_sha256 = write_decision_capture(malformed, original)
        args.initial_read = True
        result.clear()
        assert handle_native_context_command(args, print_payload=lambda value, *_: result.append(value)) == 1
        assert result[0]["ok"] is False and not ({"first_read_command", "content"} & result[0].keys())
        assert result[0]["recovery"]["sha256"] == args.capture_sha256


def test_heartbeat_keeps_layer_choices_in_the_generated_guard():
    from loopx.control_plane.heartbeat.builder import build_heartbeat_prompt

    for mode, delta in [("auto", "off"), ("off", "auto")]:
        prompt = build_heartbeat_prompt(goal_id="fixture", agent_id="agent", active_state=Path("state.md"),
            thin=True, context_delivery=mode, delta_delivery=delta)
        guard = prompt["quota_guard_command"]
        assert f"--context-delivery {mode}" in guard
        if delta == "off":
            assert "--delta-delivery off" in guard
