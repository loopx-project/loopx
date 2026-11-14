"""Typed progress must settle only the admitted replan through the real CLI."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_quota_settlement_cli import _run_generated_cli
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload, selected_context_values
from loopx.control_plane.coordination.canonical_snapshot import read_canonical_snapshot
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.work_items.progress_observation import normalize_progress_observation

GOAL = "typed-progress-cli-fixture"
AGENT = "typed-progress-agent"
TODO = "todo_original_validation"
TURN = "turn-typed-progress-review"


def make_progress_cli_fixture(root: Path, provider: str, scenario: str) -> dict:
    """Create synthetic source/history for source and installed-package replay."""
    project, runtime = root / "project", root / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\nstatus: active\n---\n\n# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P1] Validate the original artifact.\n"
        f"  <!-- loopx:todo todo_id={TODO} status=open task_class=advancement_task "
        f"claimed_by={AGENT} action_kind=validate validation_command=pytest -->\n"
    )
    baseline = {
        "schema_version": "typed_progress_observation_v0", "work_item_id": TODO,
        "surface_id": "surface-existing", "hypothesis_id": "hypothesis-existing",
        "probe_kind": "probe-existing", "result_class": "unchanged",
        "evidence_ids": ["evidence-existing"],
    }
    if scenario.startswith("terminal_"):
        baseline["coverage_scope_id"] = "scope-existing"
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    source_json, source_md = index.parent / "source.json", index.parent / "source.md"
    source_json.write_text('{"fixture":"synthetic-typed-progress"}\n')
    source_md.write_text("# Synthetic prior source audit\n")
    rows = [{
        "classification": "state_refreshed", "agent_id": AGENT,
        "generated_at": f"2026-08-13T00:0{i}:00Z", "turn_instance_id": f"turn-prior-{i}",
        "progress_observation": normalize_progress_observation(baseline),
        "json_path": str(source_json), "markdown_path": str(source_md),
    } for i in range(2)]
    if scenario == "known_blocker":
        rows.insert(0, {
            **rows[0], "generated_at": "2026-08-12T23:59:00Z", "turn_instance_id": "turn-old-blocker",
            "progress_observation": normalize_progress_observation({
                **baseline, "result_class": "blocked", "blocker_id": "blocker-permission",
            }),
        })
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    registry = root / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": "active", "repo": str(project), "state_file": state.name,
        "domain": "synthetic-typed-progress",
        # Individual comparison journeys explicitly enable Stride below.
        "execution_profile": {"stride_strategy": "off"},
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT]},
    }]}))
    todos = parse_active_state_todos(state.read_text(), item_limit=None)["agent_todos"]["items"]
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode="soft_claim", leases=[],
    ), state_path=state, provider=provider)
    observation = {**baseline, "evidence_ids": ["evidence-current"]}
    if scenario in {"advanced", "advanced_no_evidence"}:
        observation.update(result_class="advanced", probe_kind="probe-independent")
        if scenario == "advanced_no_evidence":
            observation.pop("evidence_ids")
    elif scenario == "evidence_only":
        observation["result_class"] = "advanced"
    elif scenario == "known_blocker":
        observation.update(work_item_id="todo_different_work", result_class="blocked", blocker_id="blocker-permission")
    elif scenario.startswith("terminal_"):
        observation.update(result_class="no_followup", coverage_scope_id="scope-independent")
    refresh = [
        "refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
        "--turn-instance-id", TURN, "--classification", "bounded_replan_progress",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks",
    ]
    for field in ("result_class", "work_item_id", "surface_id", "hypothesis_id", "probe_kind", "blocker_id", "coverage_scope_id"):
        if field in observation:
            refresh.extend(["--progress-" + field.replace("_", "-"), observation[field]])
    if observation.get("evidence_ids"):
        refresh.extend(["--progress-evidence-id", "evidence-current"])
    if scenario == "terminal_stop":
        vision = root / "vision.json"
        vision.write_text(json.dumps({
            "goal_id": GOAL, "agent_id": AGENT, "state": "no_followup",
            "vision_patch": {"acceptance_summary": "The bounded source scope has no remaining follow-up."},
            "path_delta": {
                "outcome": "stop", "prior_assumption": "The source scope might contain follow-up work.",
                "observed_reality": "Independent coverage found no follow-up in this bounded scope.",
                "stopped": ["Repeat exploration of the covered source scope."],
                "evidence_refs": ["evidence-current"],
            },
        }))
        refresh.extend(["--agent-vision-json", str(vision)])
    else:
        refresh.extend(["--vision-unchanged-reason", "The original validation remains open."])
    return {"project": project, "runtime": runtime, "registry": registry, "state": state,
            "index": index, "observation": observation, "refresh": refresh}


def call_progress_cli(fixture: dict, *args: str, expected_code: int = 0) -> dict:
    # These tests inspect the authority decision, independently of machine
    # presentation defaults. Preserve explicit context delivery overrides.
    if args[:2] == ("quota", "should-run") and not any(
        arg == "--context-delivery" or arg.startswith("--context-delivery=") for arg in args
    ):
        args = (*args, "--context-delivery", "off")
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(fixture["registry"]),
        "--runtime-root", str(fixture["runtime"]), "--format", "json", *args,
    ], cwd=fixture["project"], env={**os.environ, "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == expected_code, (result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("context_delivery", ["off", "on"])
def test_progress_cli_reads_policy_with_context_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    context_delivery: str,
) -> None:
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    tmp_path = tmp_path.resolve()
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    native, captures = tmp_path / "native", tmp_path / "captures"
    native.mkdir(mode=0o700)
    captures.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "replan-progress-session")
    codex_context_hook(str(native), {"session_id": "replan-progress-session",
        "cwd": str(fixture["project"]), "hook_event_name": "SessionStart", "source": "startup"})
    try:
        presented = call_progress_cli(
            fixture, "quota", "should-run", "--codex-app", "--complete-turn", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", TURN, "--todo-id", TODO,
            "--context-delivery", context_delivery,
            "--codex-context-dir", str(native), "--decision-output-root", str(captures),
        )
        if context_delivery == "on":
            assert presented["schema_version"] == "context_capture_navigation_v1"
        # The fixture keeps raw transport output. This policy observation uses
        # the current complete recovery, without claiming a model read.
        policy = read_cli_policy_payload(json.dumps(presented), returncode=0)
        assert policy["decision"] == "autonomous_replan_required"
        assert policy["heartbeat_receipt"]["turn_instance_id"] == TURN
        identity = policy["heartbeat_receipt"]["settlement_identity"]
        assert (identity["goal_id"], identity["agent_id"]) == (GOAL, AGENT)
        assert identity["todo_id"] == TODO
        assert policy["execution_obligation"]["must_attempt_work"] is True
        [original] = captures.glob("decision-*/decision.json")
        source = json.loads(original.read_bytes())
        assert source["heartbeat_receipt"] == policy["heartbeat_receipt"]
        if context_delivery == "on":
            packet = policy["replan_action_packet"]
            reader = shlex.split(packet["review"])
            assert reader[reader.index("--decision-file") + 1] == str(original)
            assert "replan_context" not in policy["autonomous_replan_obligation"]
            assert "settlement_plan" not in policy["interaction_contract"]["cli_channel"]
        else:
            assert policy["replan_action_packet"] == source["replan_action_packet"]
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.skipif(os.name != "posix" or shutil.which("pwsh") is None,
                    reason="The private capture reader needs a POSIX host with PowerShell 7")
@pytest.mark.parametrize(("provider", "complete"), [("file", False), ("sqlite", True)])
def test_replan_stage_commands_run_verbatim_in_powershell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, complete: bool,
) -> None:
    tmp_path = tmp_path.resolve()
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    workspace = tmp_path / "reader's O’Neil workspace"
    workspace.mkdir()
    fixture = make_progress_cli_fixture(workspace, provider, "advanced")
    launcher = workspace / "loopx.ps1"
    checkout = Path(__file__).resolve().parents[2]
    shutil.copyfile(checkout / "scripts/loopx.ps1", launcher)
    launcher.with_name("loopx-current-release.json").write_text(json.dumps({
        "release_root": str(checkout), "python": sys.executable,
    }), encoding="utf-8")
    monkeypatch.delenv("LOOPX_CURRENT_RELEASE_FILE", raising=False)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    powershell = shutil.which("pwsh")
    assert powershell is not None

    def run(*args: str) -> dict:
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", *args],
            cwd=fixture["project"], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    try:
        opening = run("-File", str(launcher), "--cli-bin", str(launcher), "--format", "json",
            "--registry", str(fixture["registry"]), "--runtime-root", str(fixture["runtime"]),
            "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
            "--context-delivery", "on", "--delta-delivery", "off",
            *(["--complete-turn"] if complete else []))
        page = run("-Command", opening["first_read_command"])
        assert page["eof"]
        command = selected_context_values(page["content"])["/replan_action_packet/review"]
        review = run("-Command", command)
        writeback = run("-Command", review["next"]["writeback"])
        assert review["stage"] == "review" and writeback["stage"] == "writeback"
        assert review["context_id"] == writeback["context_id"]
        assert review["source_hash"] == writeback["source_hash"]
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_captured_replan_preserves_scoped_memory_without_trusting_reader_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, provider: str,
) -> None:
    from copy import deepcopy

    from loopx.cli import main
    from loopx.capabilities.agent_turn_recall.runtime import run_configured_agent_turn_recall_fail_open
    from loopx.capabilities.reward_memory.experiment import resolve_reward_memory_experiment
    from loopx.control_plane.capability_hooks import project_turn_context
    from tests.capabilities.test_agent_turn_recall import RecallProvider, active_record
    from tests.control_plane.reward_memory_host_fixture import enable_live_memory

    tmp_path = tmp_path.resolve()
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    registry, runtime = fixture["registry"], fixture["runtime"]
    enable_live_memory(registry)
    _, config = resolve_reward_memory_experiment(registry_path=registry, goal_id=GOAL, agent_id=AGENT)
    memory = RecallProvider(active_record(config, expires_at="2099-01-01T00:00:00+00:00"))
    monkeypatch.setattr("loopx.capabilities.agent_turn_recall.run_configured_agent_turn_recall_fail_open",
                        lambda **kw: run_configured_agent_turn_recall_fail_open(**kw, provider=memory))
    monkeypatch.setenv("CODEX_THREAD_ID", "replan-memory-fixture")
    monkeypatch.chdir(fixture["project"])
    capture, journal = tmp_path / "capture", tmp_path / "native"
    journal.mkdir(mode=0o700)
    try:
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "quota", "should-run", "--codex-app", "--complete-turn", "--goal-id", GOAL, "--agent-id", AGENT,
            "--todo-id", TODO, "--turn-instance-id", TURN, "--decision-output-dir", str(capture),
            "--context-delivery", "on", "--delta-delivery", "auto", "--codex-context-dir", str(journal),
            "--prepared-capture-reference", "--defer-context-presentation"])
        output = capsys.readouterr().out
        assert code == 0, output
        original = capture / "decision.json"
        captured = original.read_bytes()
        digest = hashlib.sha256(captured).hexdigest()
        assert json.loads(output)["capture_sha256"] == digest
        source = json.loads(captured)
        assert source["replan_action_packet"]["decision"] == "replan_required"
        assert source["reward_memory_recall"]["status"] == "applied" and memory.calls == 1
        projected = project_turn_context(source)
        assert projected["reward_memory_recall"]["context"]["guidance"]
        authority = _authority(fixture)
        moved = original.rename(tmp_path / "other-location.json")
        response = call_progress_cli(fixture, "quota", "context-project", "--codex-app",
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN,
            "--capture-file", str(moved), "--capture-sha256", digest, "--initial-read",
            "--context-delivery", "on", "--delta-delivery", "auto", "--codex-context-dir", str(journal))
        assert response["navigation"]["mode"] == "full"
        assert "baseline_reference" not in response["navigation"]
        view = read_cli_policy_payload(json.dumps(response), returncode=0)
        assert view["reward_memory_recall"] == source["reward_memory_recall"]
        assert view["heartbeat_receipt"] == source["heartbeat_receipt"]
        reader = shlex.split(view["replan_action_packet"]["review"])
        assert reader[reader.index("--decision-file") + 1] == str(moved)
        review = call_progress_cli(fixture, *reader[reader.index("quota"):])
        assert review["source_hash"] == source["contract_binding"]["content_hash"]
        writer = shlex.split(review["next"]["writeback"])
        writeback = call_progress_cli(fixture, *writer[writer.index("quota"):])
        assert writeback["writeback"]["settlement_plan"]["identity"] == (
            source["heartbeat_receipt"]["settlement_identity"])
        assert moved.read_bytes() == captured and not original.exists()
        assert project_turn_context(json.loads(moved.read_bytes())) == projected
        assert _authority(fixture) == authority and memory.calls == 1
        assert not (journal / "session.json").exists()

        # Relocating the file does not edit the signed source. Changing either
        # its recorded path or executable settlement must reject scoped recall.
        assert project_turn_context({**source, "replan_read_path": str(moved)}) == {}
        changed = deepcopy(source)
        plan = changed["interaction_contract"]["cli_channel"]["settlement_plan"]
        plan["ordered_steps"][1]["command_template"] += " --evidence substituted"
        assert project_turn_context(changed) == {}
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native", [False, True], ids=["stateless", "native"])
def test_progressive_replan_uses_the_saved_decision(tmp_path, monkeypatch, provider, native):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    capture = tmp_path / "capture"
    scope = ("--codex-app", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN)
    delivery = ("--context-delivery", "on", "--delta-delivery", "off")
    if native:
        from loopx.control_plane.turn_driver.codex_context import codex_context_hook

        journal = tmp_path / "native"
        journal.mkdir(mode=0o700)
        monkeypatch.setenv("CODEX_THREAD_ID", "saved-replan-session")
        codex_context_hook(str(journal), {"session_id": "saved-replan-session",
            "cwd": str(fixture["project"]), "hook_event_name": "SessionStart", "source": "startup"})
        delivery = ("--context-delivery", "on", "--delta-delivery", "auto", "--codex-context-dir", str(journal))

    def read_stage(command, status=0):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == status, result.stdout + result.stderr
        return json.loads(result.stdout)

    def read_pages(page, limit=64_000):
        body = bytearray()
        while True:
            assert page["ok"] and page["offset"] == len(body)
            assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= limit
            body.extend(page["content"].encode())
            if page["eof"]:
                assert len(body) == page["total_bytes"]
                return bytes(body)
            assert page["next_offset"] == len(body)
            page = read_stage(shlex.split(page["next_command"]))

    def review_for(body):
        fields = {row["pointer"]: row["value"] for row in json.loads(body)["selections"]}
        assert fields["/heartbeat_receipt"] == source["heartbeat_receipt"]
        assert "/replan_action_packet/planning_guidance" not in fields
        return shlex.split(fields["/replan_action_packet/review"])

    try:
        initial = call_progress_cli(fixture, "quota", "should-run", *scope, "--complete-turn", "--todo-id", TODO,
            "--decision-output-dir", str(capture), *delivery)
        original = capture / "decision.json"
        raw = original.read_bytes()
        source = json.loads(raw)
        receipt = source["heartbeat_receipt"]
        assert source["schema_version"] == "loopx_turn_envelope_v1"
        authority = _authority(fixture)
        review_command = review_for(read_pages(read_stage(shlex.split(initial["first_read_command"]))))
        assert review_command[review_command.index("--decision-file") + 1] == str(original)
        review = read_stage(review_command)
        assert review["planning_guidance"] == source["replan_action_packet"]["planning_guidance"]
        writer = read_stage(shlex.split(review["next"]["writeback"]))
        assert writer["writeback"]["settlement_plan"]["identity"] == receipt["settlement_identity"]
        assert writer["required_outcome"] == source["replan_action_packet"]["required_outcome"]

        explore_command = review_command.copy()
        explore_command[explore_command.index("--replan-stage") + 1] = "explore"
        explore = read_stage(explore_command)
        assert explore["explore_basis"] is None
        assert explore["source_hash"] == review["source_hash"]

        recovered_path = original.with_name("recovered.json")
        original.rename(recovered_path)
        digest = hashlib.sha256(raw).hexdigest()
        saved = ("quota", "context-project", *scope, "--capture-file", str(recovered_path))
        if native:
            navigation = call_progress_cli(fixture, *saved, *delivery, "--capture-sha256", digest,
                "--max-page-output-bytes", "8192")
            detached_review = review_for(read_pages(read_stage(shlex.split(navigation["first_read_command"])), 8192))
        first = call_progress_cli(fixture, *saved, "--initial-read", *delivery, "--capture-sha256", digest,
            "--max-page-output-bytes", "8192")
        assert list(first).index("content") < list(first).index("navigation")
        nav = first["navigation"]
        assert nav["mode"] == "full" and "top-level content" in nav["instruction"]
        body = read_pages(first, 8192)
        assert read_pages(read_stage(shlex.split(nav["first_read_command"])), 8192) == body
        recovered_review = review_for(body)
        assert recovered_review[recovered_review.index("--decision-file") + 1] == str(recovered_path)
        assert recovered_review[recovered_review.index("--capture-sha256") + 1] == digest
        if native:
            assert detached_review == recovered_review
        recovered = read_stage(recovered_review)
        assert recovered["source_hash"] == review["source_hash"]
        recovered_writer = shlex.split(recovered["next"]["writeback"])
        assert recovered_writer[recovered_writer.index("--capture-sha256") + 1] == digest
        assert read_stage(recovered_writer)["writeback"]["settlement_plan"]["identity"] == receipt["settlement_identity"]
        wrong_source = recovered_review.copy()
        wrong_source[wrong_source.index("--source-hash") + 1] = "sha256:" + "0" * 64
        refused = read_stage(wrong_source, status=1)
        assert refused["ok"] is False and "matching source hash" in refused["reason"]
        try:
            recovered_path.write_bytes(raw + b" ")
            for reader in (recovered_review, recovered_writer):
                refused = read_stage(reader, status=1)
                assert refused["ok"] is False and "capture digest mismatch" in refused["reason"]
        finally:
            recovered_path.write_bytes(raw)
        wrong_capture = call_progress_cli(fixture, *saved, "--initial-read", *delivery,
            "--capture-sha256", "0" * 64, expected_code=1)
        assert not wrong_capture["ok"] and "content" not in wrong_capture
        disabled = call_progress_cli(fixture, *saved, "--initial-read", "--context-delivery", "off",
            "--delta-delivery", "off", "--capture-sha256", digest, expected_code=1)
        assert not disabled["ok"] and "content" not in disabled
        public_copy = recovered_path.with_name("manual-copy.json")
        public_copy.write_bytes(raw)
        public_copy.chmod(0o644)
        compatible = call_progress_cli(fixture, "quota", "context-project", *scope,
            "--capture-file", str(public_copy), "--initial-read", *delivery, "--capture-sha256", digest)
        assert read_stage(review_for(read_pages(compatible)))["source_hash"] == review["source_hash"]
        assert public_copy.stat().st_mode & 0o077
        assert recovered_path.read_bytes() == raw
        assert _authority(fixture) == authority
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("envelope", [(), ("--complete-turn",)], ids=["quota", "complete"])
def test_progressive_replan_stages_read_private_projection(tmp_path, monkeypatch, provider, envelope):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    scope = ("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT, *envelope)

    def read(command, expected=0, output_limit=None):
        result = subprocess.run(shlex.split(command), cwd=fixture["project"], capture_output=True,
            text=True, timeout=30)
        assert result.returncode == expected, result.stdout + result.stderr
        if output_limit is not None:
            assert len(result.stdout.encode()) <= output_limit
        return json.loads(result.stdout)

    try:
        disabled = call_progress_cli(fixture, *scope, "--context-delivery", "off")
        assert disabled["replan_action_packet"]["planning_guidance"]
        assert "review" not in disabled["replan_action_packet"]
        assert not list(fixture["runtime"].glob("context-delivery/context-*/projection.json"))

        opening = call_progress_cli(fixture, *scope, "--context-delivery", "on", "--delta-delivery", "off")
        authority = _authority(fixture)
        first = read(opening["first_read_command"])
        assert first["eof"]
        selected = selected_context_values(first["content"])
        entry = read_cli_policy_payload(json.dumps(opening), returncode=0)
        packet = entry["replan_action_packet"]
        review_command = selected["/replan_action_packet/review"]
        assert review_command == packet["review"]
        route = shlex.split(review_command)
        path = Path(route[route.index("--decision-file") + 1])
        digest = route[route.index("--capture-sha256") + 1]
        assert path.name == "projection.json" and path.is_relative_to(fixture["runtime"].resolve())
        raw = path.read_bytes()
        assert digest == hashlib.sha256(raw).hexdigest()
        source = json.loads(raw)
        assert "replan_read_path" not in source
        assert entry["execution_obligation"] == source["execution_obligation"]
        assert "planning_guidance" not in packet and "writeback_contract" not in packet

        review = read(review_command)
        context = source["autonomous_replan_obligation"]["replan_context"]
        assert review["planning_guidance"] == source["replan_action_packet"]["planning_guidance"]
        assert review["uncovered_frontier"] == context["uncovered_frontier"]
        assert review["context_id"] == context["context_id"]
        writer_command = review["next"]["writeback"]
        writer = read(writer_command)
        assert writer["source_hash"] == review["source_hash"]
        assert writer["obligation_id"] == review["obligation_id"] == packet["obligation_id"]
        assert writer["required_outcome"] == source["replan_action_packet"]["required_outcome"]
        assert writer["allowed_terminal"] == source["replan_action_packet"]["allowed_terminal"]

        recovery_root = path.parent / "context-delivery"
        prior_recoveries = set(recovery_root.glob("recovery-*"))
        for limit in (80, 96, 256, 400):
            denied = read(f"{writer_command} --max-page-output-bytes {limit}", expected=1, output_limit=limit)
            assert denied["ok"] is False and "command" not in denied
            reason = denied["reason"].lower()
            assert "budget" in reason and "retry" in reason and "read" in reason
        # Below the compact reason, preserve the process error and diagnostic
        # without emitting a partial JSON response.
        minimal_failure = b'{"ok":false}' + os.linesep.encode()
        for limit in (len(minimal_failure) - 1, len(minimal_failure)):
            args = shlex.split(f"{writer_command} --max-page-output-bytes {limit}")
            denied = subprocess.run(args, cwd=fixture["project"], capture_output=True, timeout=30)
            assert denied.returncode == 1 and len(denied.stdout) <= limit
            assert b"output budget" in denied.stderr
            assert denied.stdout == (minimal_failure if limit == len(minimal_failure) else b"")

        # Exercise the production printer with CRLF even on a POSIX test host.
        writer_argv = shlex.split(writer_command)
        cli_args = writer_argv[writer_argv.index("--format"):]
        crlf_cli = (
            "import os, sys; from loopx.entrypoint import main; "
            "os.linesep = '\\r\\n'; sys.stdout.reconfigure(newline=os.linesep); raise SystemExit(main())"
        )
        for limit in (13, 14, 77):
            denied = subprocess.run([sys.executable, "-c", crlf_cli, *cli_args, "--max-page-output-bytes", str(limit)],
                cwd=fixture["project"], capture_output=True, timeout=30)
            assert denied.returncode == 1 and len(denied.stdout) <= limit
            assert b"output budget" in denied.stderr
            if limit == 13:
                assert denied.stdout == b""
            else:
                assert denied.stdout.endswith(b"\r\n") and json.loads(denied.stdout)["ok"] is False
        assert set(recovery_root.glob("recovery-*")) == prior_recoveries

        moved = path.with_name("unavailable-source.json")
        path.rename(moved)
        try:
            missing = subprocess.run(shlex.split(f"{writer_command} --max-page-output-bytes 80"),
                cwd=fixture["project"], capture_output=True, text=True, timeout=30)
            assert missing.returncode == 1 and len(missing.stdout.encode()) <= 80
            refusal = json.loads(missing.stdout)
            assert refusal["ok"] is False and "stderr" in refusal["reason"]
            assert str(path) in missing.stderr
        finally:
            moved.rename(path)

        output_limit = 1450
        bounded = read(f"{writer_command} --max-page-output-bytes {output_limit}", output_limit=output_limit)
        assert bounded["source_hash"] == writer["source_hash"]
        published_recoveries = set(recovery_root.glob("recovery-*"))
        assert len(published_recoveries - prior_recoveries) == 1
        denied = read(f"{writer_command} --max-page-output-bytes 400", expected=1, output_limit=400)
        assert denied["ok"] is False and "command" not in denied
        assert set(recovery_root.glob("recovery-*")) == published_recoveries
        page_command = bounded["command"]
        body = bytearray()
        while True:
            page = read(page_command, output_limit=output_limit)
            assert page["offset"] == len(body)
            body.extend(page["content"].encode())
            if page["eof"]:
                assert len(body) == page["total_bytes"]
                break
            assert page["content"] and page["next_offset"] == len(body)
            page_command = page["next_command"]
        fields = json.loads(body)["selections"]
        assert len(fields) == len(writer)
        restored = {field["pointer"].removeprefix("/"): field["value"] for field in fields}
        assert restored == writer

        # The staged writeback is the executable source for this observation.
        # Long launcher paths and quoted values must survive the presentation.
        original_refresh = next(action for action in source["interaction_contract"]["cli_channel"]["next_cli_actions"]
                                if "refresh-state" in shlex.split(action))
        staged_refresh = next(action for action in restored["writeback"]["next_cli_actions"]
                              if "refresh-state" in shlex.split(action))
        assert len(original_refresh.encode()) > 420
        staged_argv = shlex.split(staged_refresh)
        assert staged_argv == shlex.split(original_refresh)
        assert "--turn-instance-id" not in staged_argv
        values = {"<advanced|blocked|exploration_exhausted|no_followup>": "advanced",
                  "<surface-id>": "surface-existing", "<hypothesis-id>": "hypothesis-existing",
                  "<probe-kind>": "probe-independent", "<evidence-id>": "evidence-current"}
        command = [sys.executable, "-m", "loopx.entrypoint",
            *[values.get(arg, arg) for arg in staged_argv[1:]], "--dry-run", "--no-global-sync",
            "--suppress-external-sinks"]
        index_before = fixture["index"].read_bytes()
        dry_run = subprocess.run(command, cwd=fixture["project"], capture_output=True, text=True, timeout=30)
        assert dry_run.returncode == 0, (dry_run.stdout, dry_run.stderr)
        assert json.loads(dry_run.stdout)["ok"] is True
        assert fixture["index"].read_bytes() == index_before

        # Even a byte-only change that keeps the decoded object identical
        # must reject both stages. Restoring the original enables the retry.
        path.write_bytes(raw + b" ")
        try:
            for command in (packet["review"], writer_command):
                rejected = read(command, expected=1)
                assert rejected["ok"] is False and "capture digest mismatch" in rejected["reason"]
            for limit in (80, 96, 256):
                rejected = read(f"{writer_command} --max-page-output-bytes {limit}", expected=1, output_limit=limit)
                assert rejected["ok"] is False and "command" not in rejected
                reason = rejected["reason"].lower()
                assert all(word in reason for word in ("digest", "restore", "do not rerun admission"))
        finally:
            path.write_bytes(raw)
        assert read(writer_command) == writer
        assert _authority(fixture) == authority
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("context_args,navigation", [
    ((), False),
    (("--context-delivery", "on"), True),
    (("--context-delivery=on",), True),
    (("--context-delivery", "off"), False),
    (("--context-delivery=off",), False),
], ids=["omitted", "on-split", "on-equals", "off-split", "off-equals"])
def test_progress_cli_context_delivery_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    context_args: tuple[str, ...], navigation: bool,
) -> None:
    tmp_path = tmp_path.resolve()
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    captures = tmp_path / "captures"
    captures.mkdir(mode=0o700)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    try:
        result = call_progress_cli(
            fixture, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", TURN,
            "--codex-context-dir", str(native),
            "--decision-output-root", str(captures), *context_args,
        )
        assert (result.get("schema_version") == "context_capture_navigation_v1") is navigation
        if not navigation:
            assert result["decision"] == "autonomous_replan_required", result
    finally:
        restart_effect_runtime(wait_for_exit=True)


def _authority(fixture: dict) -> dict:
    result = read_canonical_snapshot(rpc=effect_runtime_result, runtime_root=str(fixture["runtime"]),
        goal_id=GOAL, include_leases=True, projection_readback=None, timeout=20)
    assert result["status"] == "loaded", result
    return result


def _quota_events(runtime: Path) -> list[dict]:
    path = runtime / "goals" / GOAL / "rollout-event-log.jsonl"
    return [event for line in path.read_text(encoding="utf-8").splitlines()
            if (event := json.loads(line))["event_kind"] in {"quota_should_run", "quota_spend"}]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scenario,expected_outcomes,reason_code", [
    ("advanced", ["new_probe_family"], None),
    ("evidence_only", [], None),
    ("known_blocker", [], "progress_observation_replayed"),
    ("advanced_no_evidence", [], None),
    ("terminal_inconsistent", [], "no_followup_vision_path_inconsistent"),
    ("terminal_stop", ["coverage_backed_no_followup"], None),
])
def test_typed_progress_cli_preserves_replan_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    scenario: str, expected_outcomes: list[str], reason_code: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, scenario)
    try:
        guard_args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                      "--agent-id", AGENT, "--turn-instance-id", TURN,
                      )
        initial = call_progress_cli(fixture, *guard_args)
        assert initial["decision"] == "autonomous_replan_required", initial
        bound = call_progress_cli(fixture, *guard_args, "--todo-id", TODO)
        obligation = bound["autonomous_replan_obligation"]
        assert [trigger["kind"] for trigger in obligation["triggers"]] == ["typed_progress_repeat"]
        receipt = bound["heartbeat_receipt"]
        assert receipt["settlement_identity"]["todo_id"] == TODO
        original = {key: fixture[key].read_bytes() for key in ("registry", "state", "index")}
        original_events = _quota_events(fixture["runtime"])
        authority = _authority(fixture)
        result = call_progress_cli(fixture, *fixture["refresh"],
            expected_code=0 if expected_outcomes else 1)
        if expected_outcomes:
            assert result["ok"] is True and result["appended"] is True
            ack = result["autonomous_replan_ack"]
            assert ack["recorded"] is True
            delta = ack["semantic_delta"]
            fingerprint = hashlib.sha256(json.dumps(
                {key: value for key, value in fixture["observation"].items() if key != "schema_version"},
                sort_keys=True, ensure_ascii=True, separators=(",", ":"),
            ).encode()).hexdigest()[:16]
            assert delta == {
                "schema_version": "replan_semantic_delta_v0", "accepted": True,
                "outcomes": expected_outcomes, "satisfying_outcomes": expected_outcomes,
                "required_any_of": ["new_surface", "new_hypothesis", "new_probe_family",
                    "new_runnable_successor", "coverage_backed_exploration_exhausted",
                    "new_concrete_blocker", "coverage_backed_no_followup"],
                "observation_fingerprint": fingerprint,
                "reason": "writeback changes an outcome accepted by this obligation source",
                "trigger_kinds": ["typed_progress_repeat"], "trigger_checkpoints": [],
                "obligation_id": obligation["obligation_id"],
            }
            persisted = json.loads(Path(result["json_path"]).read_text())
            assert persisted["autonomous_replan_ack"] == ack
            assert persisted["progress_observation"]["fingerprint"] == fingerprint
            compact = json.loads(fixture["index"].read_text().splitlines()[-1])
            assert compact["autonomous_replan_ack"]["semantic_delta"] == delta
            restart_effect_runtime(wait_for_exit=True)
            replay = call_progress_cli(fixture, *fixture["refresh"])
            assert replay["appended"] is False and replay["idempotent_replay"] is True
        else:
            assert result["ok"] is False and result["appended"] is False
            rejection = result["replan_transition"]
            assert rejection["obligation_id"] == obligation["obligation_id"]
            assert rejection["reason_code"] == reason_code
            # A refusal leaves the original Turn admitted. The next action reads
            # its context before any new writeback can address the refusal.
            recovery = result["settlement_recovery"]
            assert rejection["host_action"] == "read_current_replan_context"
            assert recovery["settlement_identity"] == receipt["settlement_identity"]
            assert recovery["settlement_progress"]["next_step"] == "durable_writeback"
            assert "settlement_owed" not in recovery
            assert rejection["next_cli_actions"] == recovery["next_cli_actions"]
            restart_effect_runtime(wait_for_exit=True)
            assert call_progress_cli(fixture, *fixture["refresh"], expected_code=1) == result
            [command] = rejection["next_cli_actions"]
            code, recovered = _run_generated_cli(
                command, registry_path=fixture["registry"], cwd=fixture["project"])
            assert code == 0, recovered
            replay = recovered["heartbeat_receipt"]
            assert replay["status"] == "replayed"
            for key in ("event_id", "recorded_at", "settlement_identity"):
                assert replay[key] == receipt[key]
            assert recovered["autonomous_replan_obligation"]["obligation_id"] == obligation["obligation_id"]
            assert _quota_events(fixture["runtime"]) == original_events
            assert {key: fixture[key].read_bytes() for key in original} == original
        assert fixture["registry"].read_bytes() == original["registry"]
        displayed = parse_active_state_todos(fixture["state"].read_text(), item_limit=None)["agent_todos"]["items"]
        assert [(todo["todo_id"], todo["status"]) for todo in displayed] == [(TODO, "open")]
        assert _authority(fixture) == authority
        assert authority["todos"][0]["status"] == "open"
    finally:
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("comparison_binding", [False, True], ids=["original", "source_bound"])
def test_stalled_stride_review_drives_an_executed_mechanism_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    comparison_binding: bool,
) -> None:
    """A real stall review reaches work and a later decision under one Goal.

    The bounded assignment problem is synthetic, with an unchanged minimum-cost
    objective. It tests the control path, not benchmark or model effectiveness.
    """
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    registry = json.loads(fixture["registry"].read_text())
    registry["goals"][0]["quota"]["allowed_slots"] = 8
    fixture["registry"].write_text(json.dumps(registry))

    def call(*args: str, expected_code: int = 0) -> dict:
        return call_progress_cli(fixture, *args, expected_code=expected_code)

    def guard(turn: str, *, todo: str | None = TODO, capture: Path | None = None) -> dict:
        return call("quota", "should-run", "--codex-app", "--complete-turn", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", turn,
            *(["--todo-id", todo] if todo else []),
            *(["--decision-output-dir", str(capture)] if capture is not None else []))

    def spend(turn: str, *, todo: str | None = TODO, obligation: str | None = None) -> dict:
        return call("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", turn, *(["--todo-id", todo] if todo else []),
            *(["--replan-obligation-id", obligation] if obligation else []),
            "--slots", "1", "--source", "heartbeat", "--execute")

    try:
        call("configure-goal", "--goal-id", GOAL, "--execution-replan-after-turns", "1",
             "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
        if comparison_binding:
            call("configure-goal", "--goal-id", GOAL, "--explore-mode", "planning", "--execute")
            call("explore", "node", "--goal-id", GOAL, "--agent-id", AGENT,
                 "--node-id", "recorded-local-route", "--kind", "question",
                 "--title", "Do the existing observations establish useful progress?")
            call("explore", "finding", "--goal-id", GOAL, "--agent-id", AGENT,
                 "--node", "recorded-local-route", "--finding-id", "finding-recorded-local",
                 "--title", "The recorded local observations are unchanged.", "--status", "tentative",
                 "--summary", "Only the fixture's recorded observations; alternate mechanisms remain untested.",
                 "--tag", "writeback-result", "--evidence-ref", "evidence-existing")
            call("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
                 "--explore-result-node-ref", "recorded-local-route")
        # The proposal must reach the real Todo owner, not only a cadence label.
        complete_scope = ("Implement local, reverse-priority and joint assignment; compare their costs "
            "under the unchanged one-use-per-column constraint and retain the best valid artifact.")
        call("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO,
            "--text", complete_scope)
        guard(TURN, todo=None)
        capture = tmp_path / "original-decision"
        admitted = guard(TURN, capture=capture)
        obligation = admitted["autonomous_replan_obligation"]
        assert [item["kind"] for item in obligation["triggers"]] == ["typed_progress_repeat"]
        assert "fresh_vision_path_outcome" in obligation["replan_context"]["uncovered_frontier"]["required_any_of"]
        assert admitted["replan_action_packet"]["uncovered_frontier"] == obligation["replan_context"]["uncovered_frontier"]
        original_index = fixture["index"].read_bytes()
        refused = call(*fixture["refresh"], expected_code=1)
        assert refused["replan_transition"]["reason_code"] == "stride_decision_required"
        assert fixture["index"].read_bytes() == original_index

        control = admitted["replan_action_packet"]["stride_control"]
        assert control["review_reference_limit"] == 3
        captured = capture / "decision.json"
        source = json.loads(captured.read_bytes())
        original_bytes = captured.read_bytes()
        def read_generated(command):
            words = shlex.split(command)
            return call(*words[words.index("quota"):])

        projected = call("quota", "context-project", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", TURN, "--capture-file", str(captured),
            "--capture-sha256", hashlib.sha256(original_bytes).hexdigest(), "--initial-read",
            "--context-delivery", "on", "--delta-delivery", "off", "--max-page-output-bytes", "64000")
        content = []
        while True:
            content.append(projected["content"])
            if projected["eof"]:
                break
            projected = read_generated(projected["next_command"])
        review_path = "/replan_action_packet/review"
        review_command = next(row["value"] for row in json.loads("".join(content))["selections"]
                              if row["pointer"] == review_path)
        review = read_generated(review_command)
        writer = read_generated(review["next"]["writeback"])
        plan = writer["writeback"]["settlement_plan"]
        index, step = next((i, step) for i, step in enumerate(plan["ordered_steps"])
                           if step["kind"] == "durable_writeback")
        authoring = step["vision_authoring"]
        assert writer["writeback_contract"]["vision_authoring_ref"] == (
            f"$.writeback.settlement_plan.ordered_steps[{index}].vision_authoring")
        assert "vision_authoring" not in writer["writeback_contract"]
        assert "minimal_example" not in writer["stride_control"]
        assert authoring["total_text_limit"] == 1800
        assert "shared" in authoring["authoring_hint"] and "stride_decision" in authoring["authoring_hint"]
        example = authoring["minimal_example"]
        assert example["stride_decision"] == control["minimal_example"]
        assert plan["identity"] == source["heartbeat_receipt"]["settlement_identity"]
        assert captured.read_bytes() == original_bytes and fixture["index"].read_bytes() == original_index
        historical = read_generated(review["next"]["writeback"].replace("--context-delivery on", "--context-delivery off"))
        assert historical["stride_control"]["minimal_example"] == control["minimal_example"]
        assert "vision_authoring" in historical["writeback_contract"]
        decision = {**example["stride_decision"], "basis": "unexplained_gap", "action": "verify",
            "axes": {
                "work_slice": {"plan": "Run and compare three complete assignment methods.",
                               "reason": "Repeated local changes have not resolved the cost gap."},
                "route_distance": {"plan": "Compare local choice, reverse priority and joint assignment.",
                                   "reason": "Row interactions may defeat independent greedy choices."},
                "validation_scope": {"plan": "Use the same costs and one-use-per-column constraint.",
                                     "reason": "A changed objective cannot establish a better route."}},
            "next_probe": {"scope": "One complete bounded assignment comparison.",
                "prediction": "Joint choices can beat independent local choices.",
                "minimum_evidence": "Executable methods, assignments, constraint checks and costs.",
                "adoption_criterion": "A valid lower-cost assignment under the original objective.",
                "cost_bound": "Three methods on a three-row fixture; at most six joint assignments."}}
        choice = None
        if comparison_binding:
            basis = control["explore_basis"]
            card = next(card for card in basis["comparison_candidates"] if card["todo_id"] == TODO)
            choice = {"todo_id": TODO, **card["outcomes"][0]}
            assert choice["finding_id"] == "finding-recorded-local"
            decision["explore_review"] = {"revision": basis["revision"], "disposition": "used",
                "considered_refs": [choice["finding_id"]], "selected_candidate": choice,
                "reason": "Use the recorded outcome to compare mechanisms within the original unfinished task."}
        packet = {**example,
            "vision_patch": {"acceptance_summary": "The original minimum-cost validation remains open."},
            "path_delta": {"outcome": "continue", "prior_assumption": "Local changes can resolve the gap.",
                "observed_reality": "Repeated observations do not establish that explanation.",
                "retained": ["Original objective and unfinished validation"],
                "evidence_refs": ["evidence-existing"]},
            "stride_decision": decision, "todo_delta": [f"retain:{TODO}"]}
        vision = tmp_path / "comparison-review.json"
        vision.write_text(json.dumps(packet))
        refresh_args = ("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
            "--todo-id", TODO, "--turn-instance-id", TURN, "--agent-vision-json", str(vision),
            "--classification", "bounded_replan_progress", "--delivery-batch-scale", "single_surface",
            "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks")
        oversized = {**packet,
            "vision_patch": {**packet["vision_patch"], "vision_summary": "Verified outcome. " * 22,
                             "role_scope": "Keep the original scope. " * 10},
            "path_delta": {**packet["path_delta"], "prior_assumption": "A gap remains. " * 20,
                           "observed_reality": "The check was inconclusive. " * 11}}
        current_state = fixture["state"].read_bytes()
        vision.write_text(json.dumps(oversized))
        over_limit = call(*refresh_args, expected_code=1)
        assert "total_agent_vision" in over_limit["error"] and "limit is 1800" in over_limit["error"]
        assert all(f"{field}=" in over_limit["error"] for field in ("vision_patch", "path_delta", "stride_decision"))
        assert fixture["state"].read_bytes() == current_state and fixture["index"].read_bytes() == original_index
        vision.write_text(json.dumps(packet))
        if comparison_binding:
            # Reuse the admitted current context, but falsify only the source
            # digest. The typed progress exit must not hide this mismatch.
            current_source = _authority(fixture)
            current_state = fixture["state"].read_bytes()
            wrong = json.loads(json.dumps(packet))
            digest = choice["source_sha256"]
            wrong["stride_decision"]["explore_review"]["selected_candidate"]["source_sha256"] = (
                ("0" if digest[0] != "0" else "1") + digest[1:])
            vision.write_text(json.dumps(wrong))
            refused = call(*refresh_args, "--progress-result-class", "advanced",
                "--progress-probe-kind", "probe-independent", "--progress-evidence-id", "evidence-current",
                expected_code=1)
            assert "exact scoped result" in refused["error"], refused
            assert fixture["index"].read_bytes() == original_index
            assert fixture["state"].read_bytes() == current_state and _authority(fixture) == current_source
            vision.write_text(json.dumps(packet))
        accepted = call(*refresh_args)
        assert accepted["autonomous_replan_ack"]["semantic_delta"]["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
        if comparison_binding:
            persisted = json.loads(Path(accepted["json_path"]).read_text())
            assert persisted["agent_vision"]["stride_decision"]["explore_review"]["selected_candidate"] == choice
            restart_effect_runtime(wait_for_exit=True)
        assert call(*refresh_args)["appended"] is False
        spend(TURN)

        work_turn = "turn-executed-route-comparison"
        selected = guard(work_turn)
        assert selected["selected_todo"]["todo_id"] == TODO
        assert next(row for row in _authority(fixture)["todos"] if row["todo_id"] == TODO)["text"].endswith(complete_scope)
        artifact = fixture["project"] / "assignment-comparison.json"
        subprocess.run([sys.executable, "-c", """
import itertools, json, sys
from pathlib import Path
costs = [[1, 2, 9], [1, 9, 9], [9, 1, 2]]
def greedy(order):
    assignment, available = [None] * len(costs), set(range(len(costs)))
    for row in order:
        column = min(available, key=lambda column: (costs[row][column], column))
        assignment[row] = column
        available.remove(column)
    return assignment
def cost(assignment):
    assert sorted(assignment) == list(range(len(costs)))
    return sum(costs[row][column] for row, column in enumerate(assignment))
assignments = {'local': greedy(range(3)), 'reverse_priority': greedy(range(2, -1, -1)),
    'joint': min(itertools.permutations(range(3)), key=cost)}
results = {name: {'assignment': list(value), 'cost': cost(value)} for name, value in assignments.items()}
winner = min(results, key=lambda name: results[name]['cost'])
Path(sys.argv[1]).write_text(json.dumps({'results': results, 'winner': winner,
    'objective': 'minimum_total_cost', 'constraint': 'each_column_once'}))
""", str(artifact)], check=True, cwd=fixture["project"], timeout=10)
        measured = json.loads(artifact.read_text())
        assert measured["results"]["local"]["cost"] == 12
        assert measured["results"]["reverse_priority"]["cost"] == 11
        assert measured["results"]["joint"]["cost"] == 5
        assert measured["winner"] == "joint"
        summary = "Local cost 12; reverse-priority cost 11; joint cost 5. Retain the joint result; validate transfer."
        work = call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
            "--todo-id", TODO, "--turn-instance-id", work_turn,
            "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
            "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
            "--recommended-action", summary, "--no-global-sync", "--suppress-external-sinks")
        spend(work_turn)

        next_turn = "turn-review-executed-comparison"
        next_guard = guard(next_turn, todo=None)
        next_obligation = next_guard["autonomous_replan_obligation"]
        follow = next_obligation["replan_context"]["decision_followthrough"]
        planned = next(item for item in follow["planned_work"] if item["todo_id"] == TODO)
        result_ref = planned["ordinary_evidence_refs"][0]
        readback = call("history", "--goal-id", GOAL, "--agent-id", AGENT,
                        "--evidence-ref", result_ref)["evidence"]
        assert readback["recommended_action"] == summary
        assert readback["generated_at"] == json.loads(Path(work["json_path"]).read_text())["generated_at"]
        assert follow["execution_and_goal_gain"] == "unknown", "Synthetic provenance is not authoritative transfer"
        if comparison_binding:
            assert follow["previous_decision"]["stride_decision"]["explore_review"]["selected_candidate"] == choice

        # The measured choice changes the next executable work, not just prose.
        successor = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
            "--text", f"Validate transfer of {measured['winner']} assignment on independent costs",
            "--task-class", "advancement_task", "--action-kind", "validate",
            "--target-key", "joint-assignment-transfer", "--replan-obligation-id", next_obligation["obligation_id"])
        next_guard = guard(next_turn, todo=successor["todo_id"])
        next_control = next_guard["replan_action_packet"]["stride_control"]
        next_decision = {**decision, "action": "switch", "basis": "useful_new_route",
            "reference_context_id": next_control["reference_context_id"],
            "reference_obligation_id": next_obligation["obligation_id"],
            "previous_review": {"reference_decision_ref": follow["previous_decision"]["evidence_ref"],
                "adoption": "executed", "information_gain": "new_evidence", "goal_gain": "unknown",
                "evidence_refs": [result_ref], "lesson": "Joint choices improved this fixture; transfer remains unproven."}}
        next_decision["axes"] = {**decision["axes"], "route_distance": {
            "plan": "Carry joint assignment into an independent transfer check.",
            "reason": "The paired experiment exposed the local-choice limitation."}}
        if comparison_binding:
            # B is new work justified by the actual comparison. The optional
            # retained-A choice is not a routing command or evidence of B.
            next_decision["explore_review"] = {"revision": next_control["explore_basis"]["revision"],
                "disposition": "deferred", "considered_refs": [],
                "reason": "Transfer remains untested; the next work uses the executed comparison's history evidence."}
        packet = {**packet, "stride_decision": next_decision, "todo_delta": [f"create:{successor['todo_id']}"]}
        vision.write_text(json.dumps(packet))
        adopted = call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
            "--replan-obligation-id", next_obligation["obligation_id"], "--turn-instance-id", next_turn,
            "--agent-vision-json", str(vision), "--classification", "bounded_replan_progress",
            "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks")
        assert adopted["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
        spend(next_turn, todo=None, obligation=next_obligation["obligation_id"])
        if comparison_binding:
            spent = [json.loads(line) for line in fixture["index"].read_text().splitlines()
                     if json.loads(line).get("quota_spend_commit")]
            assert len(spent) == 3
            assert {row["turn_instance_id"] for row in spent} == {TURN, work_turn, next_turn}
    finally:
        restart_effect_runtime(wait_for_exit=True)
