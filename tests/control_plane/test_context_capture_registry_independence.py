"""Explicit capture recovery has no dependency on unrelated Goal routing."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from loopx import paths
from loopx.cli_runtime import HOST_GLOBAL_REGISTRY_SELECTOR
from test_codex_context_lifecycle import project, setup


@pytest.fixture
def conflicting_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOPX_REGISTRY", raising=False)
    for root in (paths.DEFAULT_RUNTIME_ROOT, paths.LEGACY_RUNTIME_ROOT):
        marker = root / "owned-state" / "marker.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"preserved":true}\n')
    assert paths.default_runtime_route()["status"] == "conflict"
    roots = (paths.DEFAULT_RUNTIME_ROOT, paths.LEGACY_RUNTIME_ROOT)

    def snapshot():
        return {str(p.relative_to(root.parent)): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}

    before = snapshot()
    yield snapshot
    assert snapshot() == before, "a captured read must not select, repair or mutate either runtime"


def run_cli(module, cwd, *args):
    return subprocess.run([sys.executable, "-m", module, "--format", "json", *args], cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, encoding="utf-8", timeout=30)


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
def test_explicit_capture_and_selected_details_ignore_conflicting_defaults(tmp_path, conflicting_defaults, module):
    original = {"goal_id": "fixture", "requirements": "Preserve the final stop.", "counter": 9007199254740993}
    raw = json.dumps(original, ensure_ascii=False).encode()
    capture = tmp_path / "original.json"
    capture.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    command = ("quota", "context-read", "--capture-file", str(capture), "--capture-sha256", digest)
    result = run_cli(module, tmp_path, *command)
    assert result.returncode == 0, result.stdout + result.stderr
    page = json.loads(result.stdout)
    assert page["ok"] and page["eof"] and page["sha256"] == digest
    assert json.loads(page["content"]) == original
    result = run_cli(module, tmp_path, *command, "--select", "/counter", "--select", "/requirements")
    assert result.returncode == 0, result.stdout + result.stderr
    selected = json.loads(json.loads(result.stdout)["content"])["selections"]
    assert [row["value"] for row in selected] == [9007199254740993, original["requirements"]]
    wrong = run_cli(module, tmp_path, *command[:-1], "0" * 64)
    assert wrong.returncode == 1 and not json.loads(wrong.stdout)["ok"]
    assert "digest mismatch" in json.loads(wrong.stdout)["reason"].lower()
    partial = run_cli(module, tmp_path, "quota", "context-read", "--capture-file", str(capture))
    assert partial.returncode == 1 and "exact SHA-256" in json.loads(partial.stdout)["reason"]
    partial = run_cli(module, tmp_path, "quota", "context-read", "--capture-sha256", digest)
    assert partial.returncode == 1 and "exact SHA-256" in json.loads(partial.stdout)["reason"]
    assert capture.read_bytes() == raw


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
def test_explicit_native_location_recovers_without_goal_routing(tmp_path, monkeypatch, conflicting_defaults, module):
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    setup(native, monkeypatch)
    original, _ = project(native)
    result = run_cli(module, native, "quota", "context-read", "--codex-context-dir", str(native),
        "--goal-id", "fixture", "--agent-id", "agent")
    assert result.returncode == 0, result.stdout + result.stderr
    page = json.loads(result.stdout)
    assert page["eof"] and json.loads(page["content"]) == original
    for goal, agent in [("other", "agent"), ("fixture", "other")]:
        refused = run_cli(module, native, "quota", "context-read", "--codex-context-dir", str(native),
            "--goal-id", goal, "--agent-id", agent)
        assert refused.returncode == 1 and "no original capture for this Goal/Agent" in json.loads(refused.stdout)["reason"]
    monkeypatch.setenv("CODEX_THREAD_ID", "other-session")
    refused = run_cli(module, native, "quota", "context-read", "--codex-context-dir", str(native),
        "--goal-id", "fixture", "--agent-id", "agent")
    assert refused.returncode == 1 and "same native session" in json.loads(refused.stdout)["reason"]


@pytest.mark.parametrize("command", [("quota", "context-read"),
    ("quota", "should-run", "--goal-id", "fixture"), ("quota", "spend-slot", "--goal-id", "fixture")])
def test_implicit_source_and_authority_commands_preserve_route_conflict(tmp_path, conflicting_defaults, command):
    if command[1] != "context-read":
        # Capture flags alone cannot classify a business command as read-only.
        command = (*command, "--capture-file", str(tmp_path / "unrelated.json"), "--capture-sha256", "0" * 64)
    result = run_cli("loopx.entrypoint", tmp_path, *command)
    assert result.returncode != 0
    assert "Both default LoopX runtime roots contain state" in result.stdout + result.stderr


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_replan_stages_read_original_authority_capture_without_registry(
    tmp_path, monkeypatch, conflicting_defaults, provider,
):
    from canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.control_plane.effect_runtime import restart_effect_runtime
    from test_replan_progress_native_cli import (
        AGENT, GOAL, TODO, TURN, call_progress_cli, make_progress_cli_fixture,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    registry = json.loads(fixture["registry"].read_bytes())
    registry["goals"][0]["explore_graph"] = {"enabled": True}
    fixture["registry"].write_text(json.dumps(registry))
    document = tmp_path / "outcome.json"
    document.write_text(json.dumps({"outcome_id": "original-artifact", "owner_agent_id": AGENT,
        "objective": "Validate the original artifact", "todo_ids": [TODO],
        "requirements": [{"id": "retained-stop", "description": "Stop before publication."}],
        "source": {"ref": "message:original-request", "summary": "Original accepted boundary"}}))
    try:
        revision = call_progress_cli(fixture, "goal-acceptance", "inspect", "--goal-id", GOAL)["provider_revision"]
        call_progress_cli(fixture, "goal-acceptance", "refine", "--goal-id", GOAL, "--agent-id", AGENT,
            "--document", str(document), "--expected-provider-revision", revision,
            "--operation-id", "original-outcome", "--execute")
        guard = call_progress_cli(fixture, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN,
            "--todo-id", TODO, "--decision-output-dir", str(tmp_path / "capture"))
        capture = tmp_path / "capture" / "decision.json"
        source = json.loads(capture.read_bytes())
        identity = source["heartbeat_receipt"]["settlement_identity"]
        assert (identity["goal_id"], identity["agent_id"], identity["turn_instance_id"]) == (GOAL, AGENT, TURN)
        missing = tmp_path / "unrelated-missing-registry.json"

        def snapshot():
            return {str(p.relative_to(tmp_path)): p.read_bytes()
                for root in (fixture["project"], fixture["runtime"], capture.parent)
                for p in root.rglob("*") if p.is_file()} | {"registry": fixture["registry"].read_bytes()}

        before = snapshot()
        for module in ("loopx.entrypoint", "loopx.cli"):
            for route in ((), ("--registry", str(missing)), ("--registry", HOST_GLOBAL_REGISTRY_SELECTOR)):
                def read(command):
                    argv = shlex.split(command)
                    result = run_cli(module, fixture["project"], *route, *argv[argv.index("quota"):])
                    assert result.returncode == 0, result.stdout + result.stderr
                    value = json.loads(result.stdout)
                    assert value["ok"] and (value["goal_id"], value["agent_id"]) == (GOAL, AGENT)
                    assert value["obligation_id"] == source["replan_action_packet"]["obligation_id"]
                    return value

                review = read(guard["replan_action_packet"]["review"])
                writer = read(review["next"]["writeback"])
                assert writer["context_id"] == review["context_id"]
                assert writer["source_hash"] == review["source_hash"]
                assert writer["writeback"]["settlement_plan"]["identity"] == identity
                index = review["acceptance_index"]
                assert len(index["outcomes"]) == 1
                acceptance = read(index["read_action_template"].replace("{outcome_ref}", index["outcomes"][0]["outcome_ref"]))
                assert acceptance["outcome"]["requirements"][0]["description"] == "Stop before publication."
                explore = read(guard["replan_action_packet"]["review"].replace("--replan-stage review", "--replan-stage explore"))
                assert explore["explore_basis"] == source["autonomous_replan_obligation"]["replan_context"].get("explore_basis")
        assert snapshot() == before, "captured readers cannot admit, settle, repair routes or rewrite authority"
        assert not missing.exists()
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
def test_replan_capture_failures_belong_to_reader_not_default_route(tmp_path, conflicting_defaults, module):
    from loopx.control_plane.quota.turn_envelope import _turn_envelope_result
    from test_replan_host_context_projection import _quota_payload

    capture = tmp_path / "source.json"
    source = _quota_payload()
    source["replan_read_path"] = str(capture)

    def command(value, stage="review"):
        capture.write_text(json.dumps(value))
        packet = _turn_envelope_result("replan_cli_view", payload=value)["replan_action_packet"]
        argv = shlex.split(packet[stage])
        return argv[argv.index("quota"):]

    def rejected(argv, reason):
        result = run_cli(module, tmp_path, *argv)
        assert result.returncode == 1, result.stdout + result.stderr
        value = json.loads(result.stdout)
        assert not value["ok"] and reason in value["reason"], value

    argv = command(source)
    rejected(["quota", "replan-view"], "requires --decision-file and --source-hash")
    rejected(["quota", "replan-view", "--decision-file", str(capture)], "requires --decision-file and --source-hash")
    rejected(["quota", "replan-view", "--source-hash", "sha256:" + "0" * 64], "requires --decision-file and --source-hash")
    wrong = list(argv)
    wrong[wrong.index("--source-hash") + 1] = "sha256:" + "0" * 64
    rejected(wrong, "source hash")
    capture.write_text("{")
    rejected(argv, "Expecting")
    capture.unlink()
    rejected(argv, "No such file")
    source["autonomous_replan_obligation"]["replan_context"]["obligation_id"] = "different-obligation"
    rejected(command(source), "matching decision context")
    source = _quota_payload()
    source["replan_read_path"] = str(capture)
    source["interaction_contract"]["schema_version"] = "unsupported-contract-version"
    rejected(command(source, "writeback"), "schema_version")
