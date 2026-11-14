"""Native host schemas and bounded recovery of the original input."""

from argparse import Namespace
import json
import os
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
    kwargs = dict(goal_id="work", agent_id="worker", conversation_id="native-chat")
    config = build_antigravity_sidecar_template(goal_id="work", agent_id="worker")[
        "sidecar_json"
    ]
    assert config["builtin"] == "schedule"
    assert config["args"][0] == "CRON_TZ=UTC */3 * * * *"
    assert config["args"][1:5] == ["agentapi", "new-conversation", "--model=pro", "--"]
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


def test_stop_hook_recovers_a_non_ascii_event_under_a_locale_codec(tmp_path):
    expected = "继续授权的工作"
    script = f'''
import sys
from loopx.agy_goal_mode import host_runtime

def native_input(args, text):
    return ("frozen-input", "RUNNING", 1) if text == {expected!r} else None

host_runtime._native_input = native_input
host_runtime._admitted = lambda args: True
sys.argv = [
    "host_runtime", "stop-hook",
    "--goal-id", "work", "--agent-id", "worker",
    "--conversation-id", "native-chat", "--state-dir", sys.argv[1],
]
raise SystemExit(host_runtime.main())
'''
    payload = json.dumps(event(lastUserInput=expected), ensure_ascii=False).encode("utf-8")
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "gbk",
        "PYTHONUTF8": "0",
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "recovery")],
        input=payload,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert json.loads(result.stdout.decode("utf-8"))["decision"] == "continue"
    assert result.stderr == b""


def test_admission_decodes_utf8_core_output_under_a_locale_codec(tmp_path):
    fake_cli = tmp_path / "fake_core.py"
    fake_cli.write_text(
        "import json, sys\n"
        "sys.stdout.buffer.write(json.dumps("
        "{'should_run': True, 'note': '继续授权的工作'}, ensure_ascii=False"
        ").encode('utf-8'))\n",
        encoding="utf-8",
    )
    script = '''
import argparse
import sys
from loopx.agy_goal_mode import host_runtime

host_runtime._core_args = lambda args, command: [sys.executable, sys.argv[1]]
assert host_runtime._admitted(argparse.Namespace()) is True
'''
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "gbk",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(fake_cli)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == b""


@pytest.mark.parametrize("context_delivery", ["on", "off"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_hook_admission_uses_real_core_ready_user_gate_and_zero_quota(
    tmp_path, monkeypatch, context_delivery, provider,
):
    from examples.control_plane.quota_plan_fixtures import (
        SCOPED_AGENT_ID,
        write_cli_fixture,
    )
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", context_delivery)
    registry, runtime_root, _ = write_cli_fixture(
        tmp_path / "core", scoped_agents=True, provider=provider,
    )
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
    run = subprocess.run
    observations = []

    def observe_cli(*argv, **kwargs):
        result = run(*argv, **kwargs)
        observations.append(json.loads(result.stdout))
        return result

    monkeypatch.setattr(runtime.subprocess, "run", observe_cli)
    monkeypatch.setattr(runtime, "_native_input", lambda *_: ("original-input", "IDLE", 5))
    for goal, expected in (
        ("full-speed", True),
        ("needs-operator", False),
        ("throttled-half", False),
    ):
        args.goal_id = goal
        previous_count = len(observations)
        response = runtime.stop_hook(args, event())
        assert (response.get("decision") == "continue") is expected
        if expected:
            assert "heartbeat-prompt --thin" in response["reason"]
        assert len(observations) == previous_count + 1
        decision = observations[-1]
        assert decision["should_run"] is expected
        assert set(decision["interaction_contract"]) == {"agent_channel"}
        assert "work_context" not in decision["interaction_contract"]["agent_channel"]
        assert "heartbeat_receipt" not in decision
        assert os.environ["LOOPX_CONTEXT_DELIVERY"] == context_delivery
    assert not (runtime_root / "context-delivery").exists()


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
