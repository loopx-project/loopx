"""Worker input aliases and liveness polling preserve the original unknown work."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import loopx
from canonical_authority_fixture import isolate_sqlite_runtime
from research_worker_readback_fixture import AGENT, GOAL, _write_authority
from loopx.control_plane.quota.host_poll_receipts import (
    read_host_worker_observations,
    receipt_path_for_goal,
)


REPO = Path(__file__).resolve().parents[2]


def _worker_fixture(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    # Subprocesses exercise this owner's installation, including wheel tests.
    monkeypatch.setenv("PYTHONPATH", str(Path(loopx.__file__).resolve().parent.parent))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "worker-state"))
    runtime, _ = _write_authority(tmp_path, provider=provider)
    registry = tmp_path / "registry.json"
    count = tmp_path / "dispatch-count.txt"
    host = tmp_path / "controlled_host.py"
    host.write_text(
        "from pathlib import Path\nimport sys\n"
        "p=Path(sys.argv[1]); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n"
        "raise SystemExit(1)\n", encoding="utf-8",
    )
    return runtime, registry, count, host


def _run_worker(runtime, registry, count, host, agent):
    return subprocess.run([
        sys.executable, str(REPO / "scripts/external_scheduler_worker.py"),
        "--cli-bin", str(Path(sys.executable).parent / "loopx"),
        "--registry", str(registry), "--runtime-root", str(runtime),
        "--goal-id", GOAL, "--agent-id", agent, "--once",
        "--quota-timeout-seconds", "90", "--wake-timeout-seconds", "15",
        "--wake-cmd", shlex.join([sys.executable, str(host), str(count)]),
    ], cwd=host.parent / "project", capture_output=True, text=True, timeout=120)


def _poll(runtime, registry, agent, *, record=False, extra=(), profile="generic_cli"):
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json",
        "quota", "should-run", "--goal-id", GOAL, "--agent-id", agent,
        "--runtime-profile", profile, *(["--record-host-poll"] if record else []), *extra,
    ], cwd=registry.parent / "project", capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    return payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("wire", ["quota", "envelope", "native"])
@pytest.mark.parametrize("observation", ["recorded", "unreadable", "off"])
def test_original_capture_retains_the_emitted_poll_observation(
    tmp_path, monkeypatch, provider, wire, observation,
):
    """One liveness observation must survive original-evidence recovery."""
    import hashlib

    runtime, registry, count, _ = _worker_fixture(tmp_path, monkeypatch, provider)
    receipt = receipt_path_for_goal({"id": GOAL}, tmp_path / "project" / "ACTIVE_GOAL_STATE.md")
    damaged = b'{"schema_version":"unknown","worker_observations":{"unresolved":true}}'
    if observation == "unreadable":
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_bytes(damaged)
    capture = tmp_path / "original-capture"
    options = ["--turn-instance-id", "capture-poll", "--decision-output-dir", str(capture),
               "--context-delivery", "on" if wire == "native" else "off"]
    if wire == "envelope":
        options.append("--turn-envelope")
    if wire == "native":
        native = tmp_path / "native-context"
        native.mkdir(mode=0o700)
        options.extend(["--codex-context-dir", str(native)])
    emitted = _poll(runtime, registry, AGENT, record=observation != "off", extra=options,
                    profile="codex_app_heartbeat" if wire == "native" else "generic_cli")
    original = (capture / "decision.json").read_bytes()
    source = json.loads(original)
    assert source["quota"]["spent_slots"] == 0
    assert not count.exists(), "A poll or evidence read must not dispatch the Host"
    if observation == "off":
        assert "host_poll_receipt" not in source
        assert "host_poll_receipt" not in emitted
        assert not receipt.exists()
        return
    if observation == "recorded":
        stored = json.loads(receipt.read_bytes())
        expected = {"recorded": True, "poll_count": 1,
                    "last_poll_at": stored["last_poll_at"],
                    "expected_continuation": stored["expected_continuation"]}
        assert stored["poll_count"] == 1
    else:
        expected = {"recorded": False,
                    "error": "host poll receipt is unreadable; repair its source before updating observations"}
        assert receipt.read_bytes() == damaged
    assert source["host_poll_receipt"] == expected
    before = receipt.read_bytes()
    if wire == "native":
        # Read precisely the immutable recovery view published to this caller.
        reader = shlex.split(emitted["recovery"]["command"])
    elif wire == "envelope":
        from loopx.control_plane.quota.turn_envelope import build_turn_envelope

        # Replan readers bind the full observation, including this receipt.
        # Their hash changes without changing the selected work or debit.
        without_poll = {key: value for key, value in source.items() if key != "host_poll_receipt"}
        prior = build_turn_envelope(without_poll)
        actual = emitted["action_signature"]
        for key in ("decision", "should_run", "effective_action"):
            assert emitted[key] == prior[key]
        assert emitted["writeback"]["settlement_identity"] == prior["writeback"]["settlement_identity"]
        assert actual["source_decision_hash"] != prior["action_signature"]["source_decision_hash"]
        assert actual == build_turn_envelope(source)["action_signature"]
        review = subprocess.run(shlex.split(emitted["replan_action_packet"]["review"]),
                                check=True, capture_output=True, text=True, timeout=90)
        assert json.loads(review.stdout)["source_hash"] == actual["source_decision_hash"]
        reader = shlex.split(emitted["detail_ref"]["full_decision"])
    else:
        reader = [sys.executable, "-m", "loopx.cli", "--format", "json", "quota", "context-read",
                  "--capture-file", str(capture / "decision.json"),
                  "--capture-sha256", hashlib.sha256(original).hexdigest()]
    if wire == "envelope":
        recovered = json.loads(subprocess.run(reader, check=True, capture_output=True).stdout)
        assert recovered["host_poll_receipt"] == expected
    else:
        page = subprocess.run([*reader, "--select", "/host_poll_receipt"],
                              check=True, capture_output=True, text=True, timeout=90)
        recovered = json.loads(page.stdout)
        assert recovered["ok"] is True and recovered["eof"] is True
        assert json.loads(recovered["content"])["selections"][0]["value"] == expected
    if wire != "native":
        assert emitted["host_poll_receipt"] == expected
    assert receipt.read_bytes() == before, "Recovery must not record a second poll"
    assert (capture / "decision.json").read_bytes() == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_failed_capture_discloses_the_already_recorded_poll(
    tmp_path, monkeypatch, capsys, provider,
):
    from unittest.mock import patch

    from loopx.cli import main
    from loopx.cli_commands import quota, quota_capture

    runtime, registry, count, _ = _worker_fixture(tmp_path, monkeypatch, provider)
    original_capture = quota_capture.capture_decision

    def failed_publication(directory, payload):
        # Only the capture publisher fails; admission and receipt IO are real.
        with patch.object(quota_capture.os, "replace", side_effect=OSError("synthetic capture outage")):
            original_capture(directory, payload)

    monkeypatch.setattr(quota, "capture_decision", failed_publication)
    monkeypatch.chdir(tmp_path / "project")
    capture = tmp_path / "failed-capture"
    code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                 "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
                 "--runtime-profile", "generic_cli", "--turn-instance-id", "capture-failure",
                 "--record-host-poll", "--decision-output-dir", str(capture),
                 "--context-delivery", "off"])
    failed = json.loads(capsys.readouterr().out)
    assert code == 1 and failed["status"] == "decision_transport_failed"
    assert failed["should_run"] is False and "captured_decision" not in failed
    assert failed["heartbeat_receipt"]["turn_instance_id"] == "capture-failure"
    receipt = receipt_path_for_goal({"id": GOAL}, tmp_path / "project" / "ACTIVE_GOAL_STATE.md")
    stored = json.loads(receipt.read_bytes())
    assert stored["poll_count"] == 1
    assert failed["host_poll_receipt"] == {"recorded": True, "poll_count": 1,
        "last_poll_at": stored["last_poll_at"], "expected_continuation": stored["expected_continuation"]}
    assert not list(capture.iterdir()) and not count.exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_quota_identity_survives_worker_main_and_alias_restart(tmp_path, monkeypatch, provider):
    runtime, registry, count, host = _worker_fixture(tmp_path, monkeypatch, provider)
    admitted = _poll(runtime, registry, AGENT.upper())
    assert admitted["agent_identity"]["agent_id"] == AGENT
    assert admitted["effective_action"] == "autonomous_replan_required"
    first = _run_worker(runtime, registry, count, host, AGENT.upper())
    assert first.returncode == 1, first.stdout + first.stderr  # The controlled Host ran and failed.
    files = list((tmp_path / "worker-state").rglob("*.json"))
    assert len(files) == 1
    original = json.loads(files[0].read_text())["replan_attempts"]
    assert len(original) == 1 and original[0]["agent_id"] == AGENT
    assert original[0]["status"] == "unknown"
    for alias in (AGENT, AGENT.title()):
        resumed = _run_worker(runtime, registry, count, host, alias)
        assert resumed.returncode == 2 and "status=replan_outcome_unknown" in resumed.stdout, resumed.stdout + resumed.stderr
        assert json.loads(files[0].read_text())["replan_attempts"] == original
    assert count.read_text() == "1"
    assert list((tmp_path / "worker-state").rglob("*.json")) == files
    receipt = receipt_path_for_goal({"id": GOAL}, tmp_path / "project" / "ACTIVE_GOAL_STATE.md")
    observations = json.loads(receipt.read_text())["worker_observations"]
    assert list(observations) == [AGENT]
    assert observations[AGENT]["status"] == "replan_outcome_unknown"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_actual_liveness_poll_keeps_corrupt_receipt_and_original_unknown(tmp_path, monkeypatch, provider):
    runtime, registry, count, host = _worker_fixture(tmp_path, monkeypatch, provider)
    first = _run_worker(runtime, registry, count, host, AGENT)
    assert first.returncode == 1, first.stdout + first.stderr
    state_path = next((tmp_path / "worker-state").rglob("*.json"))
    original = json.loads(state_path.read_text())["replan_attempts"]
    goal, state = {"id": GOAL}, tmp_path / "project" / "ACTIVE_GOAL_STATE.md"
    receipt = receipt_path_for_goal(goal, state)
    for damaged in (b"{broken", b"\xff", b'{"schema_version":"unrecognized"}'):
        receipt.write_bytes(damaged)
        polled = _poll(runtime, registry, AGENT, record=True)
        assert polled["host_poll_receipt"]["recorded"] is False
        assert "unreadable" in polled["host_poll_receipt"]["error"]
        observation = read_host_worker_observations(goal, state, registry_goal_digest="unavailable", runtime_root=runtime)
        assert observation == {"goal_id": GOAL, "read_status": "unreadable", "rows": []}
        assert receipt.read_bytes() == damaged
        assert json.loads(state_path.read_text())["replan_attempts"] == original
        assert count.read_text() == "1"
    # The original worker restores its current unknown observation without
    # starting the Host again.
    resumed = _run_worker(runtime, registry, count, host, AGENT)
    assert resumed.returncode == 2 and "status=replan_outcome_unknown" in resumed.stdout
    assert json.loads(receipt.read_text())["worker_observations"][AGENT]["status"] == "replan_outcome_unknown"
    assert json.loads(state_path.read_text())["replan_attempts"] == original
    assert count.read_text() == "1"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("worker_route", ["source", "global"])
def test_actual_registry_sync_preserves_worker_source_association(tmp_path, monkeypatch, provider, worker_route):
    from loopx.global_registry import sync_project_registry_to_global
    from loopx.history import collect_history

    runtime, registry, count, host = _worker_fixture(tmp_path, monkeypatch, provider)

    def sync(timestamp):
        monkeypatch.setattr("loopx.global_registry.now_local", lambda: timestamp)
        result = sync_project_registry_to_global(registry_path=registry,
            runtime_root_override=str(runtime), goal_id=GOAL)
        assert result["ok"] is True, result
        return Path(result["global_registry"])

    global_registry = sync("2026-10-19T01:00:00+00:00")
    first = _run_worker(runtime, registry if worker_route == "source" else global_registry, count, host, AGENT)
    assert first.returncode == 1, first.stdout + first.stderr
    state_path = next((tmp_path / "worker-state").rglob("*.json"))
    original_attempt = state_path.read_bytes()

    def observation(route=global_registry, runtime_root=runtime):
        packet = collect_history(registry_path=route, runtime_root=runtime_root,
            goal_id=GOAL, limit=1, include_runtime_goals=False)
        return packet["goals"][0]["host_worker_observations"]

    before = observation()
    assert before["read_status"] == "read", before
    row = before["rows"][0]
    assert row["agent_id"] == AGENT and row["status"] == "replan_outcome_unknown"
    source_bytes = registry.read_bytes()
    sync("2026-10-19T01:01:00+00:00")
    assert registry.read_bytes() == source_bytes
    assert observation() == before, "A synchronization timestamp does not change the original work"
    assert observation(registry) == before
    assert state_path.read_bytes() == original_attempt and count.read_text() == "1"

    # Identical Goal text in a different source registry is a different route.
    foreign = tmp_path / "foreign-registry.json"
    foreign.write_bytes(source_bytes)
    projected = json.loads(global_registry.read_text())
    projected["goals"][0]["source_registry"] = str(foreign)
    global_registry.write_text(json.dumps(projected))
    assert observation() == {"goal_id": GOAL, "read_status": "stale", "rows": []}
    projected["goals"][0]["source_registry"] = str(registry)
    global_registry.write_text(json.dumps(projected))
    assert observation() == before
    assert observation(runtime_root=tmp_path / "different-runtime")["read_status"] == "stale"

    missing = registry.with_suffix(".unavailable")
    registry.rename(missing)
    assert observation() == {"goal_id": GOAL, "read_status": "stale", "rows": []}
    missing.rename(registry)
    assert observation() == before

    # A real source change remains stale even before the mirror is refreshed.
    changed = json.loads(source_bytes)
    changed["goals"][0]["quota"]["compute"] = 0
    registry.write_text(json.dumps(changed))
    assert observation() == {"goal_id": GOAL, "read_status": "stale", "rows": []}
    sync("2026-10-19T01:02:00+00:00")
    assert observation() == {"goal_id": GOAL, "read_status": "stale", "rows": []}
    assert state_path.read_bytes() == original_attempt and count.read_text() == "1"
