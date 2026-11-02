"""Native host schemas and bounded recovery of the original input."""

from argparse import Namespace
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from loopx.agy_goal_mode import (
    build_antigravity_hooks_template,
    build_antigravity_sidecar_template,
)
from loopx.agy_goal_mode import host_runtime as runtime
from loopx.slash_command_install import (
    ANTIGRAVITY_HOOKS_MARKER_NAME,
    _merge_managed_hooks_json,
)


NETWORK_ERROR = "agent executor error: generating and executing: stream reading error: unexpected EOF"


def bound(tmp_path):
    return Namespace(
        cli_bin="loopx",
        registry=None,
        runtime_root=None,
        goal_id="work",
        agent_id="worker",
        conversation_id="native-chat",
        agentapi="agentapi",
        state_dir=str(tmp_path / "recovery"),
    )


def event(**changes):
    return {
        "conversationId": "native-chat",
        "terminationReason": "ERROR",
        "fullyIdle": True,
        "lastUserInput": "Continue the authorized work",
        "error": NETWORK_ERROR,
        **changes,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"conversationId": "another-chat"},
        {"terminationReason": "USER_CANCELED"},
        {"terminationReason": "NO_TOOL_CALL"},
        {"terminationReason": "MAX_TOKEN_BUDGET_EXCEEDED"},
        {"fullyIdle": False},
        {"lastUserInput": ""},
        {"error": "EOF"},
        {"error": "permission denied"},
        {"error": "quota exhausted"},
    ],
)
def test_unrelated_stop_and_user_stop_never_query_quota_or_continue(
    tmp_path, monkeypatch, change
):
    def forbidden(*_):
        pytest.fail("unrelated native stop must have no Core side effects")

    monkeypatch.setattr(runtime, "_admitted", forbidden)
    assert runtime.stop_hook(bound(tmp_path), event(**change)) == {}
    assert not (tmp_path / "recovery").exists()


def test_native_retry_requires_fresh_admission_and_is_bounded_per_input(
    tmp_path, monkeypatch
):
    args = bound(tmp_path)
    monkeypatch.setattr(runtime, "_native_input", lambda _, text: (text, "RUNNING", 5))
    monkeypatch.setattr(runtime, "_admitted", lambda _: False)
    assert runtime.stop_hook(args, event()) == {}
    monkeypatch.setattr(runtime, "_admitted", lambda _: True)
    clock = [1000]
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])
    for attempt in range(4):
        response = runtime.stop_hook(args, event())
        assert (response.get("decision") == "continue") == (attempt < 2)
        if response:
            assert "heartbeat-prompt --thin" in response["reason"]
            assert "--agent-id worker" in response["reason"]
        clock[0] += 61
    assert (
        runtime.stop_hook(args, event(lastUserInput="A new authorized request"))[
            "decision"
        ]
        == "continue"
    )


def test_repeated_identical_native_inputs_get_distinct_retry_budgets(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runtime, "_admitted", lambda _: True)
    native = ["first-input", "RUNNING", 5]
    monkeypatch.setattr(runtime, "_native_input", lambda *_: tuple(native))
    clock = [1000]
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])
    for _ in range(2):
        assert runtime.stop_hook(bound(tmp_path), event())["decision"] == "continue"
        clock[0] += 61
    assert runtime.stop_hook(bound(tmp_path), event()) == {}
    native[:] = ["second-identical-input", "RUNNING", 8]
    assert runtime.stop_hook(bound(tmp_path), event())["decision"] == "continue"


def test_native_input_change_or_missing_evidence_cannot_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_admitted", lambda _: True)
    monkeypatch.setattr(runtime, "_native_input", lambda *_: None)
    assert runtime.stop_hook(bound(tmp_path), event()) == {}
    snapshots = iter([("input", "RUNNING", 5), ("input", "IDLE", 5)])
    monkeypatch.setattr(runtime, "_native_input", lambda *_: next(snapshots))
    assert runtime.stop_hook(bound(tmp_path), event()) == {}


def test_native_input_anchor_survives_retry_steps_and_distinguishes_identical_inputs(
    tmp_path, monkeypatch
):
    from loopx.capabilities.native_chat import antigravity_adapter as provider

    monkeypatch.setenv("ANTIGRAVITY_LS_ADDRESS", "127.0.0.1:1234")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "synthetic-token")
    text = "Continue"
    user = {
        "type": "CORTEX_STEP_TYPE_USER_INPUT",
        "status": "CORTEX_STEP_STATUS_DONE",
        "metadata": {"createdAt": "2026-01-01T00:00:00Z"},
        "userInput": {"userResponse": text},
    }
    steps = [user]

    def read(url, token, method, payload, **_):
        assert payload["cascadeId"] == "native-chat"
        if method == "GetCascadeTrajectorySteps":
            return {"steps": steps}
        return {
            "trajectory": {"cascadeId": "native-chat"},
            "status": "CASCADE_RUN_STATUS_RUNNING",
            "numTotalSteps": len(steps),
        }

    monkeypatch.setattr(provider, "_rpc_post", read)
    first = runtime._native_input(bound(tmp_path), text)
    assert first is not None
    steps.append(
        {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE"}
    )
    assert runtime._native_input(bound(tmp_path), text)[0] == first[0]
    steps.append({**user, "metadata": {"createdAt": "2026-01-01T00:00:05Z"}})
    assert runtime._native_input(bound(tmp_path), text)[0] != first[0]
    assert runtime._native_input(bound(tmp_path), "A changed message") is None


def test_generated_native_configs_execute_the_stdio_hook_from_another_directory(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ANTIGRAVITY_CONVERSATION_ID", raising=False)
    foreign = tmp_path / "foreign" / "loopx"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text(
        'raise RuntimeError("wrong installed release")'
    )
    monkeypatch.setenv("PYTHONPATH", str(foreign.parent))
    local_package = tmp_path / "loopx"
    local_package.mkdir()
    (local_package / "__init__.py").write_text(
        'raise RuntimeError("wrong working directory")'
    )
    assert build_antigravity_hooks_template()["loopx"]["enabled"] is False
    assert "sidecar_json" in build_antigravity_sidecar_template()
    kwargs = dict(
        goal_id="work",
        agent_id="worker",
        conversation_id="native-chat",
        resolve_local_executable=True,
    )
    config = build_antigravity_sidecar_template(**kwargs)["sidecar_json"]
    assert config["builtin"] == "schedule"
    assert config["args"][0] == "CRON_TZ=UTC */3 * * * *"
    assert "loopx.agy_goal_mode.host_runtime" in config["args"]
    assert "sidecar-tick" in config["args"]
    assert "--conversation-id" in config["args"]
    assert "native-chat" in config["args"]
    assert "new-conversation" not in config["args"]
    hook = build_antigravity_hooks_template(**kwargs)["loopx"]
    assert hook["enabled"] is True
    # The installed native contract consumes one event on stdin and one native
    # JSON result on stdout, not LoopX's arbitrary quota projection.
    result = subprocess.run(
        shlex.split(hook["Stop"][0]["command"]),
        input=json.dumps(event(terminationReason="USER_CANCELED")),
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}


def test_sidecar_tick_checks_session_liveness_syncs_stateful_backoff_and_wakes_in_place(
    tmp_path, monkeypatch
):
    sidecar_path = tmp_path / "sidecar.json"
    sidecar_template = build_antigravity_sidecar_template(
        goal_id="work",
        agent_id="worker",
        conversation_id="native-chat",
    )
    sidecar_path.write_text(json.dumps(sidecar_template["sidecar_json"], indent=2))

    args = bound(tmp_path)
    args.sidecar_config = str(sidecar_path)
    args.agy_home = str(tmp_path)

    # 1. Language server not running -> noop, never calls quota or agentapi
    monkeypatch.setattr(
        runtime,
        "_inspect_conversation_liveness",
        lambda _cid: {
            "alive": False,
            "running": False,
            "idle": False,
            "user_canceled": False,
            "reason": "language_server_not_running",
        },
    )
    res = runtime.sidecar_tick(args)
    assert res["decision"] == "noop"
    assert res["reason"] == "language_server_not_running"

    # 2. Session is actively RUNNING -> noop, do not disturb or double-trigger
    monkeypatch.setattr(
        runtime,
        "_inspect_conversation_liveness",
        lambda _cid: {
            "alive": True,
            "running": True,
            "idle": False,
            "user_canceled": False,
            "reason": "session_running",
        },
    )
    res = runtime.sidecar_tick(args)
    assert res["decision"] == "noop"
    assert res["reason"] == "session_already_running"

    # 3. Session was stopped by user (USER_CANCELED) -> noop, respect user cancellation
    monkeypatch.setattr(
        runtime,
        "_inspect_conversation_liveness",
        lambda _cid: {
            "alive": True,
            "running": False,
            "idle": True,
            "user_canceled": True,
            "reason": "session_user_canceled",
        },
    )
    res = runtime.sidecar_tick(args)
    assert res["decision"] == "noop"
    assert res["reason"] == "session_user_canceled"

    # 4. Session is IDLE, quota should-run=False with stateful backoff interval=12m ->
    #    updates sidecar.json cron to */12 * * * *, executes ack_cli_args, and does not wake
    monkeypatch.setattr(
        runtime,
        "_inspect_conversation_liveness",
        lambda _cid: {
            "alive": True,
            "running": False,
            "idle": True,
            "user_canceled": False,
            "reason": "session_idle",
        },
    )
    ack_calls: list[list[str]] = []
    monkeypatch.setattr(
        runtime,
        "_run_cli_raw",
        lambda _a, cli_args: (ack_calls.append(cli_args) or True),
    )
    monkeypatch.setattr(
        runtime,
        "_run_core_json",
        lambda _a, _cmd: {
            "should_run": False,
            "effective_action": "waiting_external",
            "scheduler_hint": {
                "app_automation": {
                    "action": "keep_or_update_current_heartbeat",
                    "apply_needed": True,
                    "recommended_interval_minutes": 12,
                    "recommended_rrule": "FREQ=MINUTELY;INTERVAL=12",
                    "stateful_backoff": {"consecutive_noops": 2},
                    "ack_hint": {
                        "cli_args": [
                            "quota",
                            "scheduler-ack-current",
                            "--goal-id",
                            "work",
                            "--agent-id",
                            "worker",
                            "--antigravity_app",
                            "--applied-rrule",
                            "FREQ=MINUTELY;INTERVAL=12",
                            "--execute",
                        ]
                    },
                }
            },
        },
    )
    res = runtime.sidecar_tick(args)
    assert res["decision"] == "noop"
    assert res["reason"] == "quota_should_not_run"
    assert res["cron_updated"] is True
    assert res["ack_executed"] is True
    assert len(ack_calls) == 1
    assert "--antigravity_app" in ack_calls[0]
    updated_sidecar = json.loads(sidecar_path.read_text())
    assert updated_sidecar["args"][0] == "CRON_TZ=UTC */12 * * * *"

    # 5. Session is IDLE and quota should-run=True -> resets backoff to */3 * * * * and wakes bound session in-place via agentapi send-message
    wake_cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        wake_cmds.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runtime,
        "_run_core_json",
        lambda _a, _cmd: {
            "should_run": True,
            "effective_action": "normal_run",
            "scheduler_hint": {
                "app_automation": {
                    "action": "keep_or_update_current_heartbeat",
                    "apply_needed": True,
                    "recommended_interval_minutes": 3,
                    "recommended_rrule": "FREQ=MINUTELY;INTERVAL=3",
                    "stateful_backoff": {"consecutive_noops": 0},
                    "ack_hint": {
                        "cli_args": [
                            "quota",
                            "scheduler-ack-current",
                            "--goal-id",
                            "work",
                            "--agent-id",
                            "worker",
                            "--antigravity_app",
                            "--applied-rrule",
                            "FREQ=MINUTELY;INTERVAL=3",
                            "--execute",
                        ]
                    },
                }
            },
        },
    )
    res = runtime.sidecar_tick(args)
    assert res["ok"] is True
    assert res["decision"] == "woke_bound_session"
    assert res["conversation_id"] == "native-chat"
    assert res["cron_updated"] is True
    assert json.loads(sidecar_path.read_text())["args"][0] == "CRON_TZ=UTC */3 * * * *"
    assert len(wake_cmds) == 1
    assert wake_cmds[0][:4] == [
        "agentapi",
        "send-message",
        "--title=LoopX Heartbeat",
        "native-chat",
    ]
    assert "new-conversation" not in wake_cmds[0]


def test_discover_active_conversation_id_and_bind_session_for_new_session_startup(
    tmp_path, monkeypatch
):
    from loopx.capabilities.native_chat import antigravity_adapter as provider

    monkeypatch.delenv("ANTIGRAVITY_CONVERSATION_ID", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_LS_ADDRESS", "127.0.0.1:4321")
    monkeypatch.setenv("ANTIGRAVITY_CSRF_TOKEN", "token-xyz")

    workspace = tmp_path / "repo"
    workspace.mkdir()
    ws_uri = workspace.resolve().as_uri()

    def fake_rpc(_url, _token, method, _payload, **_):
        assert method == "GetAllCascadeTrajectories"
        return {
            "trajectorySummaries": {
                "subagent-cid": {
                    "status": "CASCADE_RUN_STATUS_RUNNING",
                    "lastModifiedTime": "2026-11-02T10:05:00Z",
                    "stepCount": 10,
                    "trajectoryMetadata": {"parentConversationId": "active-root-cid"},
                    "workspaces": [{"workspaceFolderAbsoluteUri": ws_uri}],
                },
                "active-root-cid": {
                    "status": "CASCADE_RUN_STATUS_RUNNING",
                    "lastModifiedTime": "2026-11-02T10:04:00Z",
                    "stepCount": 25,
                    "trajectoryMetadata": {},
                    "workspaces": [{"workspaceFolderAbsoluteUri": ws_uri}],
                },
                "other-idle-cid": {
                    "status": "CASCADE_RUN_STATUS_IDLE",
                    "lastModifiedTime": "2026-11-02T10:06:00Z",
                    "stepCount": 99,
                    "trajectoryMetadata": {},
                    "workspaces": [{"workspaceFolderAbsoluteUri": ws_uri}],
                },
            }
        }

    monkeypatch.setattr(provider, "_rpc_post", fake_rpc)
    discovered = runtime.discover_active_conversation_id(workspace=workspace)
    assert discovered == "active-root-cid"

    agy_cfg = tmp_path / "agy-config"
    bind_res = runtime.bind_session(
        Namespace(
            cli_bin="loopx",
            registry="/tmp/reg.json",
            runtime_root="/tmp/rt",
            goal_id="loopx-goal",
            agent_id="antigravity-app",
            conversation_id=None,
            workspace=str(workspace),
            agy_home=str(agy_cfg),
        )
    )
    assert bind_res["ok"] is True
    assert bind_res["conversation_id"] == "active-root-cid"
    hooks_data = json.loads((agy_cfg / "hooks.json").read_text())
    assert hooks_data["loopx"]["enabled"] is True
    assert "--conversation-id active-root-cid" in hooks_data["loopx"]["Stop"][0]["command"]
    sidecar_data = json.loads(
        (
            agy_cfg
            / "sidecars"
            / "loopx-antigravity-heartbeat"
            / "sidecar.json"
        ).read_text()
    )
    assert "sidecar-tick" in sidecar_data["args"]
    assert "active-root-cid" in sidecar_data["args"]


def test_native_hook_admission_uses_real_core_ready_user_gate_and_zero_quota(tmp_path):
    from examples.control_plane.quota_plan_fixtures import (
        SCOPED_AGENT_ID,
        write_cli_fixture,
    )

    registry, runtime_root, _ = write_cli_fixture(tmp_path / "core", scoped_agents=True)
    data = json.loads(registry.read_text())
    for goal in data["goals"]:
        if goal["id"] == "throttled-half":
            goal["quota"]["allowed_slots"] = 0
    registry.write_text(json.dumps(data))
    args = bound(tmp_path)
    args.cli_bin = str(Path(sys.executable).with_name("loopx"))
    args.registry, args.runtime_root, args.agent_id = (
        str(registry),
        str(runtime_root),
        SCOPED_AGENT_ID,
    )
    for goal, expected in (
        ("full-speed", True),
        ("needs-operator", False),
        ("throttled-half", False),
    ):
        args.goal_id = goal
        assert runtime._admitted(args) is expected


def test_hooks_migrate_only_an_unchanged_owned_legacy_entry(tmp_path):
    path = tmp_path / "hooks.json"
    legacy = {"event": "before_continuation", "command": "loopx"}
    unrelated = {"Stop": [{"type": "command", "command": "echo '{}'"}]}
    original = {"hooks": {"loopx": legacy}, "personal-hook": unrelated}
    path.write_text(json.dumps(original))
    args = dict(config_path=path, cli_bin="loopx", uninstall=False)
    assert (
        _merge_managed_hooks_json(**args, execute=True)
        == "skipped_user_owned_hooks_entry"
    )
    assert json.loads(path.read_text()) == original
    marker = tmp_path / ANTIGRAVITY_HOOKS_MARKER_NAME
    marker.write_text(json.dumps({"hooks": {"loopx": legacy}}))
    assert _merge_managed_hooks_json(**args, execute=False) == "would_write"
    assert json.loads(path.read_text()) == original
    assert _merge_managed_hooks_json(**args, execute=True) == "written"
    result = json.loads(path.read_text())
    assert result["personal-hook"] == unrelated
    assert "hooks" not in result
    assert result["loopx"]["enabled"] is False
    assert "Stop" in result["loopx"]
    assert _merge_managed_hooks_json(**args, execute=True) == "unchanged"
    assert (
        _merge_managed_hooks_json(
            config_path=path, cli_bin="loopx", uninstall=True, execute=True
        )
        == "retired"
    )
    assert json.loads(path.read_text()) == {"personal-hook": unrelated}
