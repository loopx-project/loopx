"""Follow real CLI recovery instructions through configured delivery gates."""

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from loopx import cli
from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_node_event,
    explore_result_log_path,
)
from loopx.extensions.lark import goal_channel_contracts, goal_channel_runtime
from loopx.extensions.lark.presentation import explore_results
from loopx.extensions.runtime import install_extension
from loopx.global_registry import sync_project_registry_to_global
from loopx.state_refresh import render_state_refresh_markdown
from tests.control_plane.test_refresh_checkpoint_recovery import first_refresh as first_refresh
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, REPO_ROOT, TODO_ID, TURN_ID,
    _append_blocking_user_gate, _spend_run_count, _write_fixture,
)
from tests.extensions.test_lark_goal_channel import _write_binding


def _recovery_argv(stdout, original, vision):
    """Interpret only printed rules; never infer omitted options from product code."""
    sections = {}
    for name in ("Preserve", "Remove", "Add"):
        line, = [line for line in stdout.splitlines() if line.startswith(f"- {name}:")]
        sections[name] = re.findall(r"`(--[a-z*-]+)`", line)
    result = []
    index = 0
    while index < len(original):
        option = original[index]
        end = index + 1
        while end < len(original) and not original[end].startswith("--"):
            end += 1
        if option == "refresh-state":
            result.append(option)
        elif any(fnmatchcase(option, pattern) for pattern in sections["Remove"]):
            pass
        else:
            assert any(fnmatchcase(option, pattern) for pattern in sections["Preserve"]), (
                f"recovery instruction missing for {option}"
            )
            result.extend(original[index:end])
        index = end
    for option in vision:
        if option.startswith("--"):
            assert any(fnmatchcase(option, pattern) for pattern in sections["Add"])
    return [*result, *vision]


def _snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


def _configure_sinks(registry, runtime):
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["goals"][0]["explore_graph"] = {"enabled": True}
    registry.write_text(json.dumps(payload), encoding="utf-8")
    installed = install_extension(
        REPO_ROOT / "loopx/extensions/lark/extension.toml",
        state_file=runtime / "extensions/state.json", execute=True,
    )
    assert installed["doctor"]["verified"]
    append_explore_result_event(
        explore_result_log_path(runtime, GOAL_ID),
        build_explore_node_event(
            goal_id=GOAL_ID, node_id="fix_pr_lane", title="Fix validation",
        ),
    )
    explore_results.write_lark_explore_local_config(
        registry.parent / "lark-explore.json",
        {"board": {"base_token": "PUBLIC_FIXTURE_BASE", "identity": "user",
                   "tables": {"nodes": "tblN", "edges": "tblE", "findings": "tblF"}}},
    )
    binding_path = registry.parent / "goal-channel.json"
    _write_binding(binding_path, registry.parent / "lark-kanban.json")
    bindings = goal_channel_contracts.read_goal_channel_binding(binding_path)
    binding = next(iter(bindings["bindings"].values()))
    binding["goal_id"] = GOAL_ID
    binding["automation"] = {"human_gate_auto_notify_enabled": True}
    bindings["bindings"] = {GOAL_ID: binding}
    # Configuration is fixture input; exercise production reads and activation below.
    binding_path.write_text(json.dumps(bindings), encoding="utf-8")
    marker = goal_channel_contracts.human_gate_auto_notify_marker_path(binding_path, GOAL_ID)
    marker.write_text(json.dumps({
        "schema_version": goal_channel_contracts.HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION,
        "enabled": True,
    }), encoding="utf-8")


@pytest.mark.parametrize("hint_source", ["first", "replay"])
@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("isolated", [True, False], ids=["isolated", "confirmed-resume"])
def test_stdout_recovery_requires_confirmation_to_resume_external_delivery(
    tmp_path, monkeypatch, capsys, hint_source, baseline, isolated,
):
    project, runtime, registry = _write_fixture(tmp_path)
    shared = tmp_path / "shared-runtime"
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(shared))
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    monkeypatch.setattr("loopx.paths.DEFAULT_RUNTIME_ROOT", shared)
    monkeypatch.setattr("loopx.paths.LEGACY_RUNTIME_ROOT", tmp_path / "absent-legacy-runtime")
    monkeypatch.chdir(project)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime)]

    def run(argv, output="json", expected_rc=0):
        rc = cli.main(["--format", output, *argv])
        stdout = capsys.readouterr().out
        assert rc == expected_rc, stdout
        return json.loads(stdout) if output == "json" else stdout

    if baseline:
        run([*prefix, "refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--vision-summary", "Validate the scoped change.",
             "--vision-acceptance", "Focused validation passes.",
             "--no-global-sync", "--suppress-external-sinks"])
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    guard = run([*prefix, "quota", "should-run", "--codex-app", *binding,
                 "--scan-path", str(project)])
    assert guard["decision"] == "run", guard
    _append_blocking_user_gate(project)
    _configure_sinks(registry, runtime)
    sync = sync_project_registry_to_global(
        registry_path=registry, runtime_root_override=str(shared), goal_id=GOAL_ID,
    )
    assert sync["ok"], sync
    shared_before = _snapshot(shared)
    graph_calls, channel_calls = [], []

    def send_rows(config, **kwargs):
        assert kwargs["execute"] is True
        assert kwargs["projection"]["nodes"]
        graph_calls.append(config)
        return {"ok": True, "readback": {"verified": True}}

    monkeypatch.setattr(explore_results, "sync_explore_results_to_lark", send_rows)
    def send_notification(**kwargs):
        # The real lifecycle must select an actionable gate before the adapter sends.
        quota = kwargs["quota_packet"]
        assert quota["state"] == "operator_gate" and quota["notify_user_on_gate"] is True, quota
        assert kwargs["execute"] is True
        channel_calls.append(kwargs["goal_id"])
        return {"ok": True, "status": "sent_verified", "external_write_performed": True,
                "readback_verified": True}

    monkeypatch.setattr(goal_channel_runtime, "notify_lark_goal_channel_gate", send_notification)
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original = [
        *prefix, "refresh-state", *binding,
        "--project", str(project), "--state-file", str(state_path),
        "--progress-scope", "goal",
        "--classification", "validated_change", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-boundary", "semantic_closeout",
        "--next-action", "Verify the scoped delivery evidence.",
    ]
    original += ["--no-global-sync", "--suppress-external-sinks"]
    first_stdout = run(original, "markdown")
    assert "writeback succeeded" in first_stdout
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    first = json.loads(index.read_text(encoding="utf-8").splitlines()[-1])
    assert first["vision_checkpoint"]["decision"] == "missing_required"
    original_record = Path(first["json_path"]).read_bytes()
    original_state = state_path.read_bytes()
    assert b"Verify the scoped delivery evidence." in original_state
    stdout = first_stdout if hint_source == "first" else run(original, "markdown")
    if hint_source == "replay":
        assert "- recovery: `replay`" in stdout
    vision = (
        ["--vision-unchanged-reason", "The accepted scope and evidence remain applicable."]
        if baseline else ["--vision-summary", "Validate the scoped change.",
                          "--vision-acceptance", "Focused validation passes."]
    )
    assert "loopx checkpoint-context" in stdout
    context = run([*prefix, "checkpoint-context", *binding,
                   "--project", str(project), "--state-file", str(state_path)])
    vision += ["--checkpoint-read-context", context["read_context_id"]]
    recovery = _recovery_argv(stdout, original, vision)
    # Independent oracle: recovery retains every fixture argument except this mutation.
    position = original.index("--next-action")
    assert recovery == original[:position] + original[position + 2:] + vision
    if isolated:
        for omitted in ("--no-global-sync", "--suppress-external-sinks", "--next-action"):
            damaged = stdout.replace(f"`{omitted}`", "", 1)
            with pytest.raises(AssertionError, match="recovery instruction missing"):
                _recovery_argv(damaged, original, vision)
    assert graph_calls == [] and channel_calls == []
    assert _snapshot(shared) == shared_before
    if not isolated:
        # Omission must not open either sink or mutate the original writeback.
        recovery = [arg for arg in recovery if arg not in {
            "--no-global-sync", "--suppress-external-sinks",
        }]
        before = index.read_bytes()
        denied = run(recovery, expected_rc=1)
        assert denied["error_code"] == "external_delivery_resume_required"
        assert index.read_bytes() == before
        assert _snapshot(shared) == shared_before
        assert graph_calls == [] and channel_calls == []
        assert state_path.read_bytes() == original_state
        key = denied["external_delivery"]["resume_key"]
        assert f"--resume-external-sinks {key}" in denied["error"]
        wrong = run([*recovery, "--resume-external-sinks", "0" * 64], expected_rc=1)
        assert wrong["error_code"] == "external_delivery_resume_mismatch"
        recovery += ["--resume-external-sinks", key]
    repaired = run(recovery)
    assert repaired["appended"] is True
    assert repaired["vision_checkpoint"]["satisfied"] is True
    assert repaired["settlement_identity"] == first["settlement_identity"]
    assert repaired["progress_scope"] == "goal"
    after = index.read_bytes()
    replay = run(recovery)
    assert replay["idempotent_replay"] is True and replay["appended"] is False
    assert index.read_bytes() == after
    assert Path(first["json_path"]).read_bytes() == original_record
    assert state_path.read_bytes() == original_state
    assert _spend_run_count(runtime) == 0
    assert repaired["explore_graph_sync"]["applicable"] is True
    assert repaired["explore_graph_sync"]["extension_activation"]["doctor_verified"] is True
    assert explore_result_log_path(runtime, GOAL_ID).exists()
    if isolated:
        assert _snapshot(shared) == shared_before
        assert not (runtime / "registry.global.json").exists()
        assert graph_calls == [] and channel_calls == []
        assert repaired["goal_channel_gate_sync"]["status"] == "external_sink_suppressed"
    else:
        assert _snapshot(shared) != shared_before
        assert graph_calls and channel_calls, (graph_calls, channel_calls)
        # Suppression during a pure replay must persist without appending a run.
        paused = run([*recovery[:-2], "--suppress-external-sinks"])
        assert paused["idempotent_replay"] is True
        assert index.read_bytes() == after
        stale = run(recovery, expected_rc=1)
        assert stale["error_code"] == "external_delivery_resume_mismatch"
        assert stale["external_delivery"]["resume_key"] != key
        resumed = run([*recovery[:-1], stale["external_delivery"]["resume_key"]])
        assert resumed["external_sink_delivery_authorized"] is True
        assert index.read_bytes() == after


@pytest.mark.parametrize("hint_source", ["first", "replay"])
def test_hint_preserves_lane_and_repeated_options_without_adding_isolation(first_refresh, hint_source):
    if hint_source == "replay":
        first_refresh["refresh_recovery"] = {"decision": "replay", "reason": "original_writeback_preserved"}
    original = [
        "--registry", "registry with spaces.json", "refresh-state", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--progress-scope", "agent_lane", "--agent-lane", "validation",
        "--available-capability", "filesystem_read", "--available-capability", "shell",
        "--resume-external-sinks", "a" * 64,
    ]
    vision = ["--vision-last-patch", "Validation evidence checked."]
    assert _recovery_argv(render_state_refresh_markdown(first_refresh), original, vision) == original + vision
