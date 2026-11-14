"""Old preflight callers adopt staged reads through the installed CLI owner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.quota.turn_envelope import _turn_envelope_result
from loopx.control_plane.turn_driver.codex_context import codex_context_hook
from loopx.control_plane.turn_driver.decision_capture import write_decision_capture
from test_context_capture_registry_independence import run_cli
from test_replan_progress_native_cli import (
    AGENT, GOAL, TODO, TURN, call_progress_cli, make_progress_cli_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("entry", [
    "original", "copied", "staged", "numeric", "selected", "off", "delta_only", "projection_failure",
])
def test_saved_replan_reads_keep_original_authority(tmp_path, monkeypatch, provider, entry):
    tmp_path = tmp_path.resolve()
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "saved-replan-session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    try:
        prepared = call_progress_cli(fixture, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
            "--turn-instance-id", TURN, "--decision-output-dir", str(tmp_path / "capture"),
            "--prepared-capture-reference", "--context-delivery", "on",
            "--codex-context-dir", str(native), "--defer-context-presentation")
        capture = tmp_path / "capture" / "decision.json"
        original_path = capture
        reader_path = capture
        original = capture.read_bytes()
        source = json.loads(original)
        assert prepared["capture_sha256"] == hashlib.sha256(original).hexdigest()
        assert "review" not in source["replan_action_packet"]
        identity = source["heartbeat_receipt"]["settlement_identity"]
        obligation = source["autonomous_replan_obligation"]["obligation_id"]
        # SessionStart may follow the original Host guard. Neither projection
        # nor its stage readers may replay admission or mutate durable work.
        codex_context_hook(str(native), dict(session_id="saved-replan-session",
            cwd=str(fixture["project"]), hook_event_name="SessionStart", source="startup"))
        if entry == "copied":
            capture = tmp_path / "copied.json"
            capture.write_bytes(original)
            reader_path = capture
            original_path.unlink()
        if entry in {"numeric", "staged"}:
            # Exercise historical inputs explicitly. The current guard's signed
            # capture cannot be edited into a precision fixture or legacy view.
            source = {key: value for key, value in source.items()
                      if key not in {"schema_version", "contract_binding"}}
            capture = reader_path = tmp_path / "legacy-source.json"
            source["replan_read_path"] = str(reader_path)
            if entry == "staged":
                write_decision_capture(reader_path, source)
        if entry == "numeric":
            # These two acceptance carriers compare equal after JSON.parse.
            # Both exact values must survive this synthetic source and view.
            core = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]
            acceptance = {**(core.get("acceptance_contract") or {}), "precision_probe": 2**53,
                "outcome_requirements": {"schema_version": "loopx_goal_outcomes_v0",
                    "outcomes": [{"id": "precision", "objective": "Keep distinct source values",
                        "revision": 1, "requirements": [], "sources": []}]}}
            core["acceptance_contract"] = acceptance
            distinct = {**acceptance, "precision_probe": 2**53 + 1}
            source["agent_todo_summary"]["goal_acceptance_contract"] = distinct
            source["interaction_contract"]["agent_channel"]["work_context"]["sources"].append({
                "source": "goal_acceptance", "content": {"status": "loaded", "goal_acceptance_contract": distinct},
            })
            source["precision_fixture"] = {"exact_integer": 2**53 + 1, "exact_float": 1.0}
            write_decision_capture(capture, source)
        if entry == "staged":
            capture = tmp_path / "already-staged.json"
            write_decision_capture(capture, _turn_envelope_result("replan_cli_view", payload=source))
        selected_raw = capture.read_bytes()

        def snapshot():
            return {str(p): p.read_bytes() for root in (fixture["project"], fixture["runtime"])
                for p in root.rglob("*") if p.is_file()} | {"registry": fixture["registry"].read_bytes()}

        before = snapshot()
        options = ["--context-delivery", "off", "--delta-delivery", "off"] if entry == "off" else [
            "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(native),
            *(["--select", "/replan_action_packet"] if entry == "selected" else ["--initial-read"])]
        if entry == "delta_only":
            options = ["--context-delivery", "off", "--delta-delivery", "auto", "--codex-app",
                "--codex-context-dir", str(native), "--initial-read"]
        command = ["quota", "context-project", "--capture-file", str(capture),
            "--capture-sha256", hashlib.sha256(selected_raw).hexdigest(),
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN, *options]
        if entry == "projection_failure":
            # Only the presentation provider fails. The ordinary CLI must
            # return this same admitted capture, not hide it or admit again.
            marker = tmp_path / "presentation-failed"
            script = "from pathlib import Path\nmarker = Path(" + repr(str(marker)) + ")\n" + """from unittest.mock import patch
from loopx.control_plane.quota import turn_envelope
from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
from loopx.entrypoint import main
original = turn_envelope._turn_envelope_result
def fail(operation, **kwargs):
    if operation == 'context_delivery' and (kwargs.get('native_projection') or {}).get('captured_decision_path'):
        marker.write_text('context_delivery')
        raise EffectRuntimeStartupError('synthetic presentation outage', diagnostic_code='synthetic_fixture')
    return original(operation, **kwargs)
with patch.object(turn_envelope, '_turn_envelope_result', fail):
    raise SystemExit(main())
"""
            result = subprocess.run([sys.executable, "-c", script, "--format", "json", *command],
                cwd=fixture["project"], env={**os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "LOOPX_USAGE_PING": "0"},
                capture_output=True, text=True, timeout=30)
            response = json.loads(result.stdout)
            assert result.returncode == 1 and response["ok"] is False
            assert response["capture_sha256"] == hashlib.sha256(original).hexdigest()
            assert response["heartbeat_receipt"] == source["heartbeat_receipt"]
            assert response["recovery"]["path"] == str(capture)
            assert response["recovery"]["sha256"] == hashlib.sha256(original).hexdigest()
            assert marker.read_text() == "context_delivery"
            assert "do not rerun admission" in response["reason"]
            assert snapshot() == before and capture.read_bytes() == original
            return
        result = run_cli("loopx.entrypoint", fixture["project"], *command)
        assert result.returncode == 0, result.stdout + result.stderr
        response = json.loads(result.stdout)
        assert response["ok"], response
        if entry == "selected":
            assert response["eof"]
            assert json.loads(response["content"])["selections"] == [
                {"pointer": "/replan_action_packet", "value": source["replan_action_packet"]},
            ]
        first_review = None
        staged = entry in {"original", "copied", "staged", "numeric"}
        if staged:
            first_review = next(row["value"] for row in json.loads(response["content"])["selections"]
                                if row["pointer"] == "/replan_action_packet/review")
        navigation = response.get("navigation", response)
        command, observed = navigation["recovery"]["command"], bytearray()
        while True:
            argv = shlex.split(command)
            output = run_cli("loopx.entrypoint", fixture["project"], *argv[argv.index("quota"):])
            assert output.returncode == 0, output.stdout + output.stderr
            page = json.loads(output.stdout)
            assert page["ok"] and page["offset"] == len(observed)
            observed.extend(page["content"].encode())
            if page["eof"]:
                assert len(observed) == page["total_bytes"]
                break
            command = page["next_command"]
        recovered = json.loads(observed)
        assert recovered["heartbeat_receipt"]["settlement_identity"] == identity
        assert recovered["autonomous_replan_obligation"]["obligation_id"] == obligation
        if staged:
            packet = recovered["replan_action_packet"]
            assert "review" in packet and "writeback" in packet
            assert packet["review"] == first_review
            if entry == "staged":
                assert packet == json.loads(selected_raw)["replan_action_packet"]
            argv = shlex.split(packet["review"])
            assert argv[argv.index("--decision-file") + 1] == str(reader_path)
            assert "replan_context" not in recovered["autonomous_replan_obligation"]

            def read(command):
                argv = shlex.split(command)
                output = run_cli("loopx.entrypoint", fixture["project"], *argv[argv.index("quota"):])
                assert output.returncode == 0, output.stdout + output.stderr
                value = json.loads(output.stdout)
                assert value["ok"] and value["obligation_id"] == obligation
                return value

            review = read(packet["review"])
            writeback = read(packet["writeback"])
            assert review["source_hash"] == writeback["source_hash"]
            if entry == "copied":
                pinned = _turn_envelope_result("replan_cli_view", payload=source)["replan_action_packet"]["review"]
                pinned = shlex.split(pinned)
                assert review["source_hash"] == pinned[pinned.index("--source-hash") + 1]
            assert writeback["writeback"]["settlement_plan"]["identity"] == identity
            assert review["context_id"] == source["autonomous_replan_obligation"]["replan_context"]["context_id"]
            if entry == "numeric":
                assert recovered["precision_fixture"] == {"exact_integer": 2**53 + 1, "exact_float": 1.0}
                assert type(recovered["precision_fixture"]["exact_float"]) is float
                summary = recovered["agent_todo_summary"]
                sources = recovered["interaction_contract"]["agent_channel"]["work_context"]["sources"]
                distinct = sources[-1]["content"]["goal_acceptance_contract"]
                if "goal_acceptance_contract_ref" in summary:
                    # Ordinary CLI compaction may share this exact copy. It
                    # must still point to the distinct value, never the core.
                    assert summary["goal_acceptance_contract_ref"] == (
                        f"$.interaction_contract.agent_channel.work_context.sources[{len(sources) - 1}]"
                        ".content.goal_acceptance_contract"
                    )
                else:
                    assert summary["goal_acceptance_contract"] == distinct
                assert distinct["precision_probe"] == 2**53 + 1
        else:
            # Explicit raw selectors and disabled transport keep their existing
            # content. They are not implicitly converted to a different view.
            assert recovered["replan_action_packet"] == source["replan_action_packet"]
        assert snapshot() == before
        assert capture.read_bytes() == selected_raw
        if entry == "copied":
            assert not original_path.exists()
        else:
            assert original_path.read_bytes() == original
    finally:
        restart_effect_runtime(wait_for_exit=True)
