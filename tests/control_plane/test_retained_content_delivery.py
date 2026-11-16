"""Verified retained bytes reach the existing direction Host without new work."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.test_goal_mode_mcp_settlement import GOAL_ID, AGENT_ID, _run_cli


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_generic_host_receives_retained_bytes_in_its_direction_request(
    provider, tmp_path, monkeypatch
):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    project = state.parent
    host = project / "content_host.py"
    host.write_text(
        r"""import hashlib,json,pathlib,sys
r=json.load(sys.stdin)
if r.get("phase") == "checkpoint_direction":
    c=r["checkpoint_context"]
    body=c["retained_result_content"]
    proof=c["retained_result_binding"]["result_artifact_digests"]
    assert hashlib.sha256(body["json_text"].encode()).hexdigest()==proof["json_sha256"]
    assert hashlib.sha256(body["markdown"].encode()).hexdigest()==proof["markdown_sha256"]
    retained=json.loads(body["json_text"])
    assert retained["classification"]=="retained_content_ready"
    assert pathlib.Path("candidate.txt").read_text()=="original-candidate"
    with pathlib.Path("direction.txt").open("a") as f:f.write(c["read_context_id"]+"\n")
    pathlib.Path("content-metrics.json").write_text(json.dumps({"request_bytes":len(json.dumps(r).encode()),"json_bytes":len(body["json_text"].encode()),"markdown_bytes":len(body["markdown"].encode())}))
    json.dump({"read_context_id":c["read_context_id"],"vision_unchanged_reason":"Retained report classification and original candidate preserve the scoped route."},sys.stdout)
else:
    assert not pathlib.Path("candidate.txt").exists()
    pathlib.Path("candidate.txt").write_text("original-candidate")
    json.dump({"schema_version":"loopx_turn_result_v0","turn_key":r["turn_key"],"result_kind":"validated_progress","completed_phases":["host_execute","typed_result"],"classification":"retained_content_ready","recommended_action":"Judge the retained report.","next_action":"Judge the retained report.","delivery_batch_scale":"implementation","delivery_outcome":"outcome_progress","summary":"Original candidate prepared.","vision_unchanged_reason":"Old pre-result judgment."},sys.stdout)
""",
        encoding="utf-8",
    )
    validator = project / "verify_content.py"
    validator.write_text(
        "import pathlib; assert pathlib.Path('candidate.txt').read_text()=='original-candidate'; pathlib.Path('validation.txt').open('ab').write(b'x')",
        encoding="utf-8",
    )
    rc, result = _run_cli(
        registry,
        "turn",
        "run-once",
        "--host",
        "generic-cli",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--project",
        str(project),
        "--host-adapter-command-json",
        json.dumps([sys.executable, str(host)]),
        "--validation-command-json",
        json.dumps([sys.executable, str(validator)]),
        "--no-global-sync",
        "--execute",
    )
    assert rc == 0 and result.get("status") == "committed", json.dumps(
        result, ensure_ascii=False
    )
    assert len((project / "direction.txt").read_text().splitlines()) == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "runtime/goals" / GOAL_ID / "runs/index.jsonl")
        .read_text()
        .splitlines()
    ]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    retained = [
        row for row in rows if row.get("classification") == "retained_content_ready"
    ]
    assert len(retained) == 2
    assert retained[0]["vision_checkpoint"]["satisfied"] is False
    assert retained[1]["vision_checkpoint"]["satisfied"] is True
    assert retained[0]["settlement_identity"] == retained[1]["settlement_identity"]
    assert "retained_result_content" not in json.dumps(retained[1])

    assert (project / "validation.txt").read_bytes() == b"x"
    index = tmp_path / "runtime/goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    rc, replay = _run_cli(
        registry,
        "turn",
        "run-once",
        "--host",
        "generic-cli",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--project",
        str(project),
        "--host-adapter-command-json",
        json.dumps([sys.executable, str(host)]),
        "--validation-command-json",
        json.dumps([sys.executable, str(validator)]),
        "--resume-turn-key",
        result["resume_turn_key"],
        "--retry-failed-turn",
        "--no-global-sync",
        "--execute",
    )
    assert rc == 0 and replay["replayed"] is True, replay
    assert index.read_bytes() == before
    assert (project / "validation.txt").read_bytes() == b"x"
    assert len((project / "direction.txt").read_text().splitlines()) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("cached", [True, False], ids=["cached", "fresh"])
def test_current_agent_prompt_uses_verified_content_once_and_keeps_receipts_thin(
    provider, cached, tmp_path, monkeypatch
):
    from loopx.control_plane.goals import checkpoint_context_io as context_io
    from loopx.control_plane.quota.settlement import SettlementIdentity
    from loopx.control_plane.turn_driver.checkpoint_continuation import (
        CurrentAgentCheckpoint,
    )
    from loopx.control_plane.turn_driver.host_candidate import render_checkpoint_prompt
    from tests.control_plane.test_retained_result_binding import direction

    runtime, call, prior, turn, context, supplement = direction(
        provider, tmp_path, monkeypatch
    )
    identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])
    project = runtime.parent / "project"
    receipt_path = context_io._receipt_path(runtime, identity)
    if not cached:
        receipt_path.unlink()
    original = {
        key: Path(prior[key]).read_bytes() for key in ("json_path", "markdown_path")
    }
    seen, resolves = [], []
    actual = context_io.effect_runtime_result

    def record(method, payload, **kwargs):
        if method == "goal.checkpoint_read_context.resolve":
            resolves.append(payload["phase"])
        return actual(method, payload, **kwargs)

    monkeypatch.setattr(context_io, "effect_runtime_result", record)
    packet_path = project / "current-direction.json"
    packet = json.loads(packet_path.read_text())

    def decide(current):
        prompt = render_checkpoint_prompt(
            {"phase": "checkpoint_direction", "checkpoint_context": current}
        )
        delivered = json.loads(prompt.split("\n", 1)[1])["checkpoint_context"]
        body = delivered["retained_result_content"]
        assert body["json_text"].encode() == original["json_path"]
        assert body["markdown"].encode() == original["markdown_path"]
        parsed = json.loads(body["json_text"])
        assert parsed["settlement_identity"] == prior["settlement_identity"]
        packet["vision_patch"]["acceptance_summary"] = (
            "Verified retained report: " + parsed["classification"]
        )
        seen.append(current["read_context_id"])
        return {"read_context_id": current["read_context_id"], "agent_vision": packet}

    state = project / f".codex/goals/{identity.goal_id}/ACTIVE_GOAL_STATE.md"
    driver = CurrentAgentCheckpoint(
        project / ".loopx/registry.json", runtime, state, identity, decide
    )
    decided = driver.continue_direction({}, purpose="supplement_checkpoint")
    assert resolves == ["check" if cached else "read"]
    saved = json.loads(receipt_path.read_text())
    assert "retained_result_content" not in saved
    assert "include_retained_content" not in saved
    assert driver.continue_direction({}, purpose="supplement_checkpoint") == decided
    assert seen == [decided["checkpoint_read_context_id"]]
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    from tests.control_plane.test_quota_settlement_cli import (
        _projected_cli_args,
        _spend_run_count,
    )

    command = supplement.replace(
        context["read_context_id"], decided["checkpoint_read_context_id"]
    )
    rc, qualified = call(*_projected_cli_args(command, turn_instance_id=turn))
    assert rc == 0 and qualified["vision_checkpoint"]["satisfied"], qualified
    assert _spend_run_count(runtime) == 0
    assert all(
        Path(prior[key]).read_bytes() == value for key, value in original.items()
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("field", ["json_text", "markdown"])
def test_builtin_launch_refuses_substituted_inline_content(
    provider, field, tmp_path, monkeypatch
):
    from copy import deepcopy
    from loopx.control_plane.goals import checkpoint_context_io as context_io
    from loopx.control_plane.goals.first_party_host_admission import (
        FirstPartyHostRuntimeRejected,
    )
    from loopx.control_plane.quota.settlement import SettlementIdentity
    from tests.control_plane.test_retained_result_binding import direction
    from tests.control_plane.test_quota_settlement_cli import _spend_run_count

    runtime, _, prior, turn, _, _ = direction(provider, tmp_path, monkeypatch)
    identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])
    project = runtime.parent / "project"
    registry = project / ".loopx/registry.json"
    state = project / f".codex/goals/{identity.goal_id}/ACTIVE_GOAL_STATE.md"
    context = context_io.read_checkpoint_context(
        registry_path=registry,
        runtime_root_override=str(runtime),
        goal_id=identity.goal_id,
        agent_id=identity.agent_id,
        todo_id=identity.todo_id,
        turn_instance_id=turn,
        purpose="supplement_checkpoint",
        include_retained_content=True,
    )
    before = context_io._receipt_path(runtime, identity).read_bytes()
    assert context_io.check_direction_launch_context(
        runtime_root=runtime,
        registry_path=registry,
        state_file=state,
        identity=identity,
        context=context,
    )["ok"]
    changed = deepcopy(context)
    changed["retained_result_content"][field] += "substituted"
    with pytest.raises(
        FirstPartyHostRuntimeRejected, match="checkpoint_direction_binding_mismatch"
    ):
        context_io.check_direction_launch_context(
            runtime_root=runtime,
            registry_path=registry,
            state_file=state,
            identity=identity,
            context=changed,
        )
    assert context_io._receipt_path(runtime, identity).read_bytes() == before
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_direction_can_stop_after_reading_inline_r_without_replacing_it(
    provider, tmp_path, monkeypatch
):
    from loopx.control_plane.goals import checkpoint_context_io as context_io
    from loopx.control_plane.quota.settlement import SettlementIdentity
    from loopx.control_plane.turn_driver.checkpoint_continuation import (
        CurrentAgentCheckpoint,
    )
    from loopx.control_plane.turn_driver.host_candidate import render_checkpoint_prompt
    from tests.control_plane.test_retained_result_binding import direction
    from tests.control_plane.test_quota_settlement_cli import _spend_run_count

    runtime, _, prior, _, _, _ = direction(provider, tmp_path, monkeypatch)
    identity = SettlementIdentity.from_runtime_payload(prior["settlement_identity"])
    project = runtime.parent / "project"
    index = runtime / "goals" / identity.goal_id / "runs/index.jsonl"
    before = index.read_bytes()
    original = {
        key: Path(prior[key]).read_bytes() for key in ("json_path", "markdown_path")
    }
    calls = []

    def decide(current):
        delivered = json.loads(
            render_checkpoint_prompt({"checkpoint_context": current}).split("\n", 1)[1]
        )
        record = json.loads(
            delivered["checkpoint_context"]["retained_result_content"]["json_text"]
        )
        calls.append(record["settlement_identity"])
        # An explicit synthetic acceptance condition drives the controller's stop.
        if record["classification"] != "requested_output_ready":
            return {
                "result_kind": "iteration_failed",
                "stop_reason": "retained_result_requires_revision",
            }
        raise AssertionError(
            "Fixture unexpectedly satisfies the separate acceptance condition"
        )

    driver = CurrentAgentCheckpoint(
        project / ".loopx/registry.json",
        runtime,
        project / f".codex/goals/{identity.goal_id}/ACTIVE_GOAL_STATE.md",
        identity,
        decide,
    )
    with pytest.raises(ValueError, match="result retained"):
        driver.continue_direction({}, purpose="supplement_checkpoint")
    assert calls == [prior["settlement_identity"]]
    saved = json.loads(context_io._receipt_path(runtime, identity).read_text())
    assert saved["direction_stopped"] is True
    assert "retained_result_content" not in saved
    assert index.read_bytes() == before and _spend_run_count(runtime) == 0
    assert all(
        Path(prior[key]).read_bytes() == content for key, content in original.items()
    )
