"""Necessary native context and immutable supporting detail have distinct coverage."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from loopx.cli_commands.quota_capture import read_context_capture
from loopx.control_plane.turn_driver import codex_context as context
from test_codex_context_lifecycle import setup, project, deliver, read_pages
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_quota_settlement_cli import _run_cli


def payload():
    return {"ok": True, "goal_id": "fixture", "stop": "Stop before publication.",
        "planning_horizon": {"core_goal": {"acceptance_contract": {"outcome_requirements": {
            "schema_version": "loopx_goal_outcomes_v0", "current_lifetime": True, "revision": 1,
            "outcomes": [{"id": "outcome", "owner_agent_id": "agent", "revision": 1,
                "objective": "Deliver the complete requested result.", "todo_ids": ["todo_work"],
                "sources": [{"ref": "original", "summary": "Historical supporting evidence. " * 100}],
                "requirements": [{"id": "last", "description": "Preserve this final requirement.",
                    "status": "unverified", "planning_satisfied": False,
                    "agent_review": {"id": "last", "conclusion": "unmet", "basis_current": False,
                        "independently_verified": False, "note": "Previous failure and its reasons. " * 100,
                        "evidence_refs": ["negative:original"]}}]}]}}}}}


def outcome(document):
    return document["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["outcomes"][0]


def test_cold_warm_and_repeated_compaction_keep_requirements_without_replaying_material(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    original = payload()
    candidate_text = "Preserve this candidate's complete final constraint. " * 120
    original["capability_gate"] = {"schema_version": "capability_gate_v0", "action": "run",
        "runnable_candidates": [{"schema_version": "todo_item_v0", "todo_id": "todo_work",
            "text": candidate_text, "status": "open", "claimed_by": "agent"}], "blocked_candidates": []}
    _, first = project(tmp_path, payload=original)
    view = first["context"]
    assert first["mode"] == "full" and view["stop"] == original["stop"]
    assert outcome(view)["requirements"][0]["description"] == outcome(original)["requirements"][0]["description"]
    review = outcome(view)["requirements"][0]["agent_review"]
    assert review == {"id": "last", "conclusion": "unmet", "basis_current": False, "independently_verified": False}
    assert "sources" not in outcome(view)
    archive = view["context_details"]
    assert json.loads(Path(archive["path"]).read_bytes()) == original
    assert Path(first["recovery"]["path"]).name == "required-context.json"
    assert json.loads(Path(first["recovery"]["path"]).read_bytes()) == view

    # Reading complete selected details cannot acknowledge the required view.
    request = argparse.Namespace(capture_file=archive["path"], capture_sha256=archive["sha256"],
        select=[outcome(view)["supporting_material_pointer"]], offset=0,
        page_bytes=16000, max_page_output_bytes=20000, format="json")
    page = read_context_capture(request)
    assert page["ok"] and page["eof"]
    assert json.loads(page["content"])["selections"][0]["value"] == outcome(original)
    deliver(tmp_path, event, [page])
    assert "candidate" not in context._state(tmp_path)
    deliver(tmp_path, event, read_pages(tmp_path, first))
    assert context._state(tmp_path)["candidate"]["capture_file"] == first["recovery"]["path"]

    _, warm = project(tmp_path, "warm", payload=original)
    assert warm["mode"] == "delta" and candidate_text not in json.dumps(warm["changes"])
    assert warm["target"]["context_epoch"] == first["target"]["context_epoch"]
    deliver(tmp_path, event, read_pages(tmp_path, warm))
    before = context._state(tmp_path)
    for _ in range(2):
        result = context.codex_context_hook(str(tmp_path), {**event, "source": "compact"})
        after = context._state(tmp_path)
        assert after["recovery"] == before["recovery"] and "candidate" not in after
        assert "Do not replay read context or rerun admission solely" in result["hookSpecificOutput"]["additionalContext"]
        assert "Previous failure" not in json.dumps(result)
    _, fresh = project(tmp_path, "fresh", payload={**original, "stop": "Stop all delivery."})
    assert fresh["mode"] == "full" and fresh["context"]["stop"] == "Stop all delivery."
    assert fresh["context"]["capability_gate"] == original["capability_gate"]
    assert json.loads(Path(archive["path"]).read_bytes()) == original


@pytest.mark.parametrize("failure", [None, "wire", "inline_read"])
@pytest.mark.parametrize("output_limit", [None, 4000])
def test_saved_native_projection_recovers_the_required_view(tmp_path, monkeypatch, failure, output_limit):
    from loopx.cli_commands import quota_capture
    from loopx.control_plane.turn_driver import decision_capture

    event = setup(tmp_path, monkeypatch)
    original = {**payload(), "agent_identity": {"agent_id": "agent"},
        "heartbeat_receipt": {"turn_instance_id": "original-turn"}}
    source = tmp_path / "original.json"
    raw = json.dumps(original).encode()
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    command = ["quota", "context-project", "--capture-file", str(source), "--capture-sha256", digest,
        "--goal-id", "fixture", "--agent-id", "agent", "--turn-instance-id", "original-turn",
        "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(tmp_path)]
    if output_limit is not None:
        command += ["--max-page-output-bytes", str(output_limit)]
    write_capture = decision_capture.write_decision_capture

    def fail_wire(path, value, **kwargs):
        if path.name == "delivery.json":
            raise OSError("optional wire unavailable")
        return write_capture(path, value, **kwargs)

    with monkeypatch.context() as patch:
        if failure == "wire":
            patch.setattr(decision_capture, "write_decision_capture", fail_wire)
        elif failure == "inline_read":
            patch.setattr(quota_capture, "read_context_capture", lambda *args, **kwargs: {"ok": False})
        code, view = _run_cli(registry, runtime, *command, cwd=tmp_path)
    assert code == 0 and view["ok"] and view["mode"] == "full", view

    def check_recovery(view):
        journal = context._state(tmp_path)["recovery"]
        assert view["capture_sha256"] == digest
        assert {key: view["recovery"][key] for key in ("path", "sha256")} == {
            key: journal[key] for key in ("path", "sha256")}
        argv = shlex.split(view["recovery"]["command"])
        if output_limit is not None:
            assert int(argv[argv.index("--max-page-output-bytes") + 1]) <= output_limit
        pages, combined = [], b""
        while True:
            code, recovered = _run_cli(registry, runtime, *argv[argv.index("quota"):], cwd=tmp_path)
            assert code == 0 and recovered["offset"] == len(combined)
            if output_limit is not None:
                assert len((json.dumps(recovered, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= output_limit
            pages.append(recovered)
            combined += recovered["content"].encode()
            if recovered["eof"]:
                break
            argv = shlex.split(recovered["next_command"])
        assert combined == Path(view["recovery"]["path"]).read_bytes()
        required = json.loads(combined)
        assert Path(view["recovery"]["path"]).name == "required-context.json"
        assert required["stop"] == original["stop"]
        assert outcome(required)["requirements"][0]["description"] == outcome(original)["requirements"][0]["description"]
        assert "sources" not in outcome(required)
        assert json.loads(Path(required["context_details"]["path"]).read_bytes()) == original
        return pages

    recovered = check_recovery(view)
    if failure is None:
        deliver(tmp_path, event, recovered)
        assert context._state(tmp_path)["candidate"]["capture_file"] == view["recovery"]["path"]
        code, warm = _run_cli(registry, runtime, *command, cwd=tmp_path)
        assert code == 0 and warm["target"]["context_epoch"] == view["target"]["context_epoch"], warm
        check_recovery(warm)
        context.codex_context_hook(str(tmp_path), {**event, "source": "compact"})
        code, compacted = _run_cli(registry, runtime, *command, cwd=tmp_path)
        assert code == 0 and compacted["mode"] == "full", compacted
        check_recovery(compacted)
    assert source.read_bytes() == raw and not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("change", ["unknown_outcome", "unknown_requirement", "unknown_review", "unknown_schema", "signed"])
def test_unknown_semantics_are_never_hidden(tmp_path, monkeypatch, change):
    setup(tmp_path, monkeypatch)
    original = payload()
    if change == "unknown_outcome":
        outcome(original)["new_obligation"] = "Read this."
    elif change == "unknown_requirement":
        outcome(original)["requirements"][0]["new_obligation"] = "Read this."
    elif change == "unknown_review":
        outcome(original)["requirements"][0]["agent_review"]["new_obligation"] = "Read this."
    elif change == "unknown_schema":
        original["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["schema_version"] = "future"
    else:
        original["contract_binding"] = {"signature": "original"}
    _, result = project(tmp_path, payload=original)
    assert result.get("context", result) == original
    assert "context_details" not in result.get("context", result)


def test_off_and_unsupported_surface_remain_exact(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    original, unchanged = payload(), payload()
    assert context.project_codex_context(original, directory=None, capture_directory=tmp_path,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="one") is original
    required, _ = context._capture_required_context(original, directory=tmp_path, surface="turn_envelope")
    assert required is original and original == unchanged


def test_projection_preserves_numbers_and_original_values(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    original = payload()
    original["opaque_counter"] = 9007199254740993
    before = copy.deepcopy(original)
    required, _ = context._capture_required_context(original, directory=tmp_path, surface="quota_packet")
    assert original == before and required["opaque_counter"] == before["opaque_counter"]


def test_saved_cli_references_keep_tail_constraints_and_recover_without_detail_reads(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    row = {"schema_version": "todo_item_v0", "todo_id": "task", "text": "Identical title",
        "note": "Complete work and verify evidence. " * 150 + "Stop before publication.",
        "content_revision": "revision:one", "claimed_by": "peer",
        "resume_condition": {"schema_version": "todo_resume_condition_v0", "satisfied": False}}
    revised = {**row, "content_revision": "revision:two", "note": "New contrary evidence; reconsider."}
    original = {"ok": True, "goal_id": "fixture", "agent_identity": {"agent_id": "agent"},
        "heartbeat_receipt": {"turn_instance_id": "original-turn"},
        "agent_todo_summary": {"schema_version": "todo_summary_v0", "first_open_items": [row, revised],
            "backlog_items": [copy.deepcopy(row), copy.deepcopy(revised)],
            "claimed_open_items": [copy.deepcopy(row)]}}
    path = tmp_path / "original.json"
    raw = json.dumps(original).encode()
    path.write_bytes(raw)
    assert context.project_codex_context(original, directory=None, capture_directory=tmp_path,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="off") is original
    digest = hashlib.sha256(raw).hexdigest()
    registry, runtime = tmp_path / "no-registry.json", tmp_path / "no-runtime"
    command = ["quota", "context-project", "--capture-file", str(path), "--capture-sha256", digest,
        "--goal-id", "fixture", "--agent-id", "agent", "--turn-instance-id", "original-turn",
        "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(tmp_path),
        "--max-page-output-bytes", "4000"]
    for cycle in range(3):
        if cycle == 2:
            context.codex_context_hook(str(tmp_path), {**event, "source": "compact"})
        code, view = _run_cli(registry, runtime, *command, cwd=tmp_path)
        assert code == 0 and view["ok"], view
        assert view["mode"] == ("delta" if cycle == 1 else "full")
        argv = shlex.split(view["recovery"]["command"])
        pages, contents = [], b""
        while True:
            code, page = _run_cli(registry, runtime, *argv[argv.index("quota"):], cwd=tmp_path)
            assert code == 0 and page["offset"] == len(contents), page
            assert len((json.dumps(page, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= 4000
            contents += page["content"].encode()
            pages.append(page)
            if page["eof"]:
                break
            argv = shlex.split(page["next_command"])
        required = json.loads(contents)
        assert "context_details" not in required, "in-view reuse must not create extra evidence reads"
        shown = required["agent_todo_summary"]
        for lane in ("first_open_items", "backlog_items", "claimed_open_items"):
            resolved = []
            for item in shown[lane]:
                if "context_ref" in item:
                    value = required
                    for part in item["context_ref"].split("/")[1:]:
                        value = value[int(part)] if isinstance(value, list) else value[part]
                    assert "context_ref" not in value
                    resolved.append(value)
                else:
                    resolved.append(item)
            assert resolved == original["agent_todo_summary"][lane]
        assert shown["first_open_items"][0]["note"].endswith("Stop before publication.")
        assert shown["first_open_items"][1]["note"] == revised["note"]
        if cycle == 0:
            assert len(pages) > 1
            deliver(tmp_path, event, pages[:1])
            assert "candidate" not in context._state(tmp_path)
        deliver(tmp_path, event, pages)
    assert path.read_bytes() == raw and not registry.exists() and not runtime.exists()


def test_unavailable_typed_plan_keeps_original_capture(tmp_path, monkeypatch):
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError

    def unavailable(*args, **kwargs):
        raise EffectRuntimeStartupError("Synthetic unavailable runtime", diagnostic_code="fixture_unavailable")

    monkeypatch.setattr(turn_envelope, "plan_progressive_context", unavailable)
    original = payload()
    required, recovery = context._capture_required_context(original, directory=tmp_path, surface="quota_packet")
    assert required is original
    assert Path(recovery["path"]).name == "projection.json"
    assert json.loads(Path(recovery["path"]).read_bytes()) == original
    assert not (tmp_path / "required-context.json").exists()


def test_small_material_keeps_full_capture_when_the_detail_index_costs_more(tmp_path):
    original = payload()
    outcome(original)["sources"][0]["summary"] = "Short supporting source."
    review = outcome(original)["requirements"][0]["agent_review"]
    review["note"], review["evidence_refs"] = "Failed.", ["negative:original"]
    required, recovery = context._capture_required_context(original, directory=tmp_path, surface="quota_packet")
    assert required is original
    assert Path(recovery["path"]).name == "projection.json"
    assert not (tmp_path / "required-context.json").exists()


def test_small_duplicate_rows_do_not_gain_reference_overhead(tmp_path):
    row = {"schema_version": "todo_item_v0", "todo_id": "task", "text": "Keep this constraint.", "claimed_by": "peer"}
    original = {"ok": True, "agent_todo_summary": {"schema_version": "todo_summary_v0",
        "first_open_items": [row], "backlog_items": [dict(row)]}}
    required, recovery = context._capture_required_context(original, directory=tmp_path, surface="quota_packet")
    assert required is original
    assert Path(recovery["path"]).name == "projection.json"


def test_public_quota_cli_delivers_required_view_and_original_evidence(acceptance_goal, tmp_path, monkeypatch):
    workspace, document, cli, run = acceptance_goal
    original = {"outcome_id": "export", "owner_agent_id": "agent-a", "todo_ids": ["todo_export"],
        "objective": "Deliver the complete export.", "requirements": [
            {"id": "first", "description": "Retain the original content."},
            {"id": "last", "description": "Stop before publication."}],
        "source": {"ref": "message:original", "summary": ("Original supporting material. " * 16).strip()}}
    sources = []
    # Enough valid history to outweigh the complete digest-bound read command;
    # the small-material counterexample above deliberately keeps full delivery.
    for i in range(8):
        source = {**original["source"], "ref": f"message:original-{i}"}
        sources.append(source)
        document.write_text(json.dumps({**original, "source": source}))
        code, refined = cli("refine", "--agent-id", "agent-a", "--document", str(document),
            "--expected-provider-revision", cli("inspect")[1]["provider_revision"],
            "--operation-id", f"original-outcome-{i}", "--execute")
        assert code == 0, refined
    before = cli("inspect")[1]
    lifecycle = tmp_path / "native"
    lifecycle.mkdir(mode=0o700)
    (tmp_path / "captures").mkdir(mode=0o700)
    setup(lifecycle, monkeypatch)
    code, packet = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a",
        "--todo-id", "todo_export", "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(lifecycle), "--decision-output-root", str(tmp_path / "captures"),
        "--turn-instance-id", "original-turn")
    assert code == 0, {key: packet.get(key) for key in ("error", "error_code", "error_detail", "reason", "reason_code")}
    recovery = context._state(lifecycle)["recovery"]
    assert Path(recovery["path"]).name == "required-context.json"
    required = json.loads(Path(recovery["path"]).read_bytes())
    source = next(row for row in required["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if row["source"] == "goal_acceptance")
    shown = source["content"]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
    assert [row["description"] for row in shown["requirements"]] == [row["description"] for row in original["requirements"]]
    assert "sources" not in shown and "on demand" in source["reason"]
    details = required["context_details"]
    code, page = run("quota", "context-read", "--capture-file", details["path"],
        "--capture-sha256", details["sha256"], "--select", shown["supporting_material_pointer"] + "/sources",
        "--page-bytes", "16000", "--max-page-output-bytes", "20000")
    assert code == 0 and page["eof"], page
    recovered = json.loads(page["content"])["selections"][0]["value"]
    assert recovered == sources
    assert "candidate" not in context._state(lifecycle)
    assert cli("inspect")[1] == before, "presentation must not rewrite canonical acceptance"
