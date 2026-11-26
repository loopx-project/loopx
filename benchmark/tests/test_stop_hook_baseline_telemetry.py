from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmark.runtime import native_hooks, worker
from benchmark.runtime.codex import (
    OFFICIAL_STOP_HOOK_MODES,
    Execution,
    aggregate_control_telemetry,
    prepare_codex_home,
    summarize_control_telemetry,
)
from benchmark.runtime.native_hooks import (
    OFFICIAL_STOP_HOOK_REASON,
    baseline_todo_path,
    prepare_baseline_todo_checklist,
    prepare_official_stop_hook,
)
from benchmark.runtime.worker import run_once
from benchmark.tests.test_shared_codex_runtime import _sqlite_heartbeat_env, settings, worker_env


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "plain", "official_stop_hook": True, "official_stop_hook_mode": "unknown"},
        {"mode": "plain", "official_stop_hook": True, "official_stop_hook_mode": 123},
        {"mode": "plain", "official_stop_hook": True, "official_stop_hook_mode": True},
        {"mode": "plain", "official_stop_hook": False, "official_stop_hook_mode": "todo_checklist"},
        {"mode": "plain", "official_stop_hook_mode": "todo_checklist"},
        {"mode": "heartbeat", "official_stop_hook_mode": "todo_checklist"},
        {"mode": "turn", "validation_command": ("true",), "official_stop_hook_mode": "simple"},
        {"mode": "native-goal", "official_stop_hook_mode": "todo_checklist"},
        {"mode": "loopx-goal", "official_stop_hook_mode": "todo_checklist"},
    ],
)
def test_execution_rejects_invalid_stop_hook_modes_and_pairings(kwargs):
    with pytest.raises(ValueError, match="official_stop_hook"):
        Execution(**kwargs)


def test_harbor_rejects_invalid_mode_and_isolates_ambient_opt_in(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    with pytest.raises(ValueError, match="official_stop_hook"):
        BenchmarkCodex(
            logs_dir=tmp_path / "bad",
            model_name="openai/fixture",
            execution_mode="plain",
            official_stop_hook=True,
            official_stop_hook_mode="bad-mode",
        )
    with pytest.raises(ValueError, match="official_stop_hook"):
        BenchmarkCodex(
            logs_dir=tmp_path / "bad-pairing",
            model_name="openai/fixture",
            execution_mode="plain",
            official_stop_hook=False,
            official_stop_hook_mode="todo_checklist",
        )

    monkeypatch.setenv("LOOPX_OFFICIAL_STOP_HOOK", "1")
    monkeypatch.setenv("LOOPX_OFFICIAL_STOP_HOOK_MODE", "todo_checklist")

    plain = BenchmarkCodex(
        logs_dir=tmp_path / "plain",
        model_name="openai/fixture",
        execution_mode="plain",
    )
    simple = BenchmarkCodex(
        logs_dir=tmp_path / "simple",
        model_name="openai/fixture",
        execution_mode="plain",
        official_stop_hook=True,
    )
    checklist = BenchmarkCodex(
        logs_dir=tmp_path / "checklist",
        model_name="openai/fixture",
        execution_mode="plain",
        official_stop_hook=True,
        official_stop_hook_mode="todo_checklist",
    )

    assert plain._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK"] == "0"
    assert plain._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK_MODE"] == ""
    assert simple._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK"] == "1"
    assert simple._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK_MODE"] == ""
    assert checklist._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK"] == "1"
    assert (
        checklist._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK_MODE"]
        == "todo_checklist"
    )


def _write_hooks_list_codex_stub(binary: Path, capture_stdin: Path | None = None) -> None:
    capture_code = (
        f"pathlib.Path({str(capture_stdin)!r}).write_text(sys.stdin.read(), encoding='utf-8')\n"
        if capture_stdin is not None
        else "sys.stdin.read()\n"
    )
    binary.write_text(
        f"#!{sys.executable}\n"
        + """
import hashlib, json, os, pathlib, sys
if "app-server" in sys.argv:
    home = pathlib.Path(os.environ["CODEX_HOME"])
    hooks_path = (home / "hooks.json").resolve()
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    for line in sys.stdin:
        req = json.loads(line)
        if "id" not in req:
            continue
        method = req["method"]
        if method == "initialize":
            print(json.dumps({"id": req["id"], "result": {}}), flush=True)
        elif method == "hooks/list":
            hooks = []
            for event_name, groups in payload["hooks"].items():
                norm_event = event_name[0].lower() + event_name[1:]
                for g_idx, group in enumerate(groups):
                    for h_idx, handler in enumerate(group["hooks"]):
                        cmd = handler["command"]
                        digest = hashlib.sha256(cmd.encode("utf-8")).hexdigest()
                        hooks.append({
                            "sourcePath": str(hooks_path),
                            "eventName": norm_event,
                            "handlerType": handler["type"],
                            "command": cmd,
                            "matcher": group.get("matcher"),
                            "timeoutSec": handler.get("timeout"),
                            "async": handler.get("async", False),
                            "enabled": True,
                            "key": f"{norm_event}:{g_idx}:{h_idx}",
                            "currentHash": digest,
                        })
            print(json.dumps({"id": req["id"], "result": {"data": [{"hooks": hooks}]}}), flush=True)
    sys.exit(0)
home = pathlib.Path(os.environ["CODEX_HOME"])
sessions = home / "sessions"
sessions.mkdir(exist_ok=True)
(sessions / "session-0.jsonl").write_text("{}\\n", encoding="utf-8")
print(json.dumps({"type": "thread.started", "thread_id": "session-0"}))
print(json.dumps({"type": "turn.completed"}))
"""
        + capture_code
    )
    binary.chmod(0o755)


def test_default_off_parity_keeps_plain_and_simple_stop_hook_unchanged(tmp_path):
    assert OFFICIAL_STOP_HOOK_MODES == ("simple", "todo_checklist")

    default_exec = Execution(mode="plain", official_stop_hook=True)
    assert default_exec.official_stop_hook_mode is None

    (tmp_path / "base").mkdir()
    (tmp_path / "hook").mkdir()
    args_base = settings(tmp_path / "base", execution=Execution(mode="plain"), skills=None)
    args_hook = settings(tmp_path / "hook", execution=default_exec, skills=None)
    prepare_codex_home(**args_base)
    prepare_codex_home(**args_hook)
    assert "hooks = true" not in (args_base["home"] / "config.toml").read_text(encoding="utf-8")
    assert "hooks = true" in (args_hook["home"] / "config.toml").read_text(encoding="utf-8")

    for label, stop_flag, extra_env in (
        ("plain", "0", {}),
        ("official_stop_only", "1", {}),
        ("official_stop_explicit_simple", "1", {"LOOPX_OFFICIAL_STOP_HOOK_MODE": "simple"}),
    ):
        sub = tmp_path / label
        sub.mkdir()
        env = worker_env(sub)
        stdin_capture = sub / "stdin.txt"
        _write_hooks_list_codex_stub(Path(env["CODEX_BIN"]), capture_stdin=stdin_capture)
        env["LOOPX_OFFICIAL_STOP_HOOK"] = stop_flag
        env.update(extra_env)
        receipt = run_once(env)
        assert receipt["ok"] is True
        if extra_env:
            assert receipt["official_stop_hook_mode"] == "simple"
        else:
            assert "official_stop_hook_mode" not in receipt
        assert not baseline_todo_path(Path(env["LOOPX_CODEX_HOME"])).parent.exists()
        assert stdin_capture.read_text(encoding="utf-8") == "Synthetic task.\n"
        if stop_flag == "0":
            assert "official_stop_hook" not in receipt
            assert not (Path(env["LOOPX_CODEX_HOME"]) / "hooks.json").exists()
        else:
            assert receipt["official_stop_hook"] is True
            assert receipt["official_stop_hook_verified"] is True
            hooks_cfg = json.loads((Path(env["LOOPX_CODEX_HOME"]) / "hooks.json").read_text())
            cmd = hooks_cfg["hooks"]["Stop"][0]["hooks"][0]["command"]
            assert OFFICIAL_STOP_HOOK_REASON in cmd
        for key in (
            "hook_prepare_seconds",
            "control_overhead_seconds",
            "work_wall_seconds",
            "control_tax_ratio",
        ):
            assert isinstance(receipt[key], float)
            assert receipt[key] >= 0.0
        assert 0.0 <= receipt["control_tax_ratio"] <= 1.0


def test_todo_checklist_isolates_from_git_workspace_and_evaluates_stop_hook(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=workspace, check=True)
    (workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=workspace, check=True)

    run_dir = tmp_path / "trial"
    run_dir.mkdir()
    env = worker_env(run_dir)
    stdin_capture = run_dir / "prompt.txt"
    _write_hooks_list_codex_stub(Path(env["CODEX_BIN"]), capture_stdin=stdin_capture)
    env.update(
        LOOPX_PROJECT=str(workspace),
        LOOPX_OFFICIAL_STOP_HOOK="1",
        LOOPX_OFFICIAL_STOP_HOOK_MODE="todo_checklist",
    )

    receipt = run_once(env)
    assert receipt["ok"] is True
    assert receipt["official_stop_hook"] is True
    assert receipt["official_stop_hook_mode"] == "todo_checklist"
    assert receipt["official_stop_hook_verified"] is True

    # Workspace repository stays completely clean of checklist artifacts.
    git_status = subprocess.check_output(
        ["git", "-C", str(workspace), "status", "--porcelain"], text=True
    )
    assert git_status == ""
    assert not any("TODO" in p.name or "baseline" in p.name for p in workspace.iterdir())

    home = Path(env["LOOPX_CODEX_HOME"])
    checklist = baseline_todo_path(home).resolve()
    assert checklist.is_file()
    assert (checklist.parent.stat().st_mode & 0o777) == 0o700
    assert (checklist.stat().st_mode & 0o777) == 0o600
    assert str(checklist) in stdin_capture.read_text(encoding="utf-8")
    assert "Do not create todo or checklist files inside the workspace" in stdin_capture.read_text(
        encoding="utf-8"
    )

    # Modify checklist with partial progress, quotes, and backslashes; verify hook preserves it
    # cleanly on re-entry without invalidating hooks.json trust hash.
    checklist.write_text(
        "# Trial Task Checklist\n\n"
        "- [x] Inspect files\n"
        '- [ ] Handle "quoted" & \\\\backslash\\\\ edge case\n'
        "- [ ] Run regression tests\n",
        encoding="utf-8",
    )
    hooks_before = (home / "hooks.json").read_text(encoding="utf-8")
    run_once(env)
    assert (home / "hooks.json").read_text(encoding="utf-8") == hooks_before
    assert 'Handle "quoted"' in checklist.read_text(encoding="utf-8")

    hook_command = json.loads(hooks_before)["hooks"]["Stop"][0]["hooks"][0]["command"]

    # 1. With unchecked items remaining -> blocks with actionable JSON reason & quotes escaped.
    blocked = subprocess.run(
        ["/bin/sh", "-c", hook_command],
        input='{"stop_hook_active": true}',
        text=True,
        capture_output=True,
        check=True,
    )
    blocked_payload = json.loads(blocked.stdout)
    assert blocked_payload["decision"] == "block"
    assert "2 unchecked and 1 checked item(s)" in blocked_payload["reason"]
    assert "Handle" in blocked_payload["reason"]
    assert "Run regression tests" in blocked_payload["reason"]

    # 2. Empty checklist (0 checked, 0 open) -> still blocks until explicit verification items pass.
    checklist.write_text("# Empty\n", encoding="utf-8")
    empty_out = subprocess.run(
        ["/bin/sh", "-c", hook_command],
        input="{}",
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(empty_out.stdout)["decision"] == "block"

    # 3. Missing or symlinked checklist -> blocks safely.
    checklist.unlink()
    missing_out = subprocess.run(
        ["/bin/sh", "-c", hook_command],
        input="{}",
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(missing_out.stdout)["decision"] == "block"

    target = workspace / "app.py"
    checklist.symlink_to(target)
    symlink_out = subprocess.run(
        ["/bin/sh", "-c", hook_command],
        input="{}",
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(symlink_out.stdout)["decision"] == "block"
    with pytest.raises(ValueError, match="physical file"):
        prepare_baseline_todo_checklist(home)

    # 4. Control chars and multi-byte UTF-8 near preview boundary produce valid UTF-8 JSON across locales.
    checklist.unlink()
    checklist.write_text(
        "- [ ] Control \x08\x1b\x0c chars " + ("中文界限" * 45) + "\n",
        encoding="utf-8",
    )
    for locale in ("C", "en_US.UTF-8"):
        utf8_out = subprocess.run(
            ["/bin/sh", "-c", hook_command],
            input=b"{}",
            capture_output=True,
            env={"LC_ALL": locale, "PATH": "/usr/bin:/bin"},
            check=True,
        )
        utf8_payload = json.loads(utf8_out.stdout.decode("utf-8"))
        assert utf8_payload["decision"] == "block"
        assert "Control" in utf8_payload["reason"]
        assert "中文" in utf8_payload["reason"]

    # 5. All items checked off -> allows stop ({}).
    checklist.write_text(
        "# Trial Task Checklist\n\n"
        "- [x] Inspect files\n"
        '- [x] Handle "quoted" & \\\\backslash\\\\ edge case\n'
        "- [X] Run regression tests\n",
        encoding="utf-8",
    )
    done_out = subprocess.run(
        ["/bin/sh", "-c", hook_command],
        input="{}",
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(done_out.stdout) == {}


def test_todo_checklist_workspace_write_exposes_only_checklist_dir(tmp_path):
    env = worker_env(tmp_path)
    home = Path(env["LOOPX_CODEX_HOME"])
    execution = Execution(
        mode="plain",
        sandbox="workspace-write",
        official_stop_hook=True,
        official_stop_hook_mode="todo_checklist",
    )
    command = worker.native_command(env, execution, "execute", tmp_path, None)
    expected_root = json.dumps([str(baseline_todo_path(home).parent.resolve())])
    assert f"sandbox_workspace_write.writable_roots={expected_root}" in command


def test_prepare_baseline_todo_checklist_rejects_symlinks_and_unsafe_perms(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    symlink_home = tmp_path / "sym-home"
    symlink_home.symlink_to(target)
    with pytest.raises(ValueError, match="physical trial home"):
        prepare_baseline_todo_checklist(symlink_home)

    home = tmp_path / "home"
    home.mkdir()
    todo_dir = home / "baseline-todo"
    todo_dir.mkdir(mode=0o755)
    todo_dir.chmod(0o755)
    with pytest.raises(ValueError, match="private, physical"):
        prepare_baseline_todo_checklist(home)

    with pytest.raises(ValueError, match="official_stop_hook_mode"):
        prepare_official_stop_hook({}, home, mode="invalid")


def test_worker_control_tax_telemetry_accounts_stop_hook_and_heartbeat_arms(
    tmp_path, monkeypatch
):
    # Case A: Stop-Hook arm with deterministic monotonic clock -> exact control_tax_ratio = 0.2.
    stop_dir = tmp_path / "stop-arm"
    stop_dir.mkdir()
    env = worker_env(stop_dir)
    _write_hooks_list_codex_stub(Path(env["CODEX_BIN"]))
    env.update(LOOPX_OFFICIAL_STOP_HOOK="1", LOOPX_OFFICIAL_STOP_HOOK_MODE="todo_checklist")

    clock = [100.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])

    real_prepare = native_hooks.prepare_official_stop_hook

    def timed_stop_hook(*args, **kwargs):
        clock[0] += 1.5
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(native_hooks, "prepare_official_stop_hook", timed_stop_hook)

    real_prepare_home = worker.prepare_codex_home

    def timed_home(*args, **kwargs):
        clock[0] += 0.5
        return real_prepare_home(*args, **kwargs)

    monkeypatch.setattr(worker, "prepare_codex_home", timed_home)

    real_child_process = worker.child_process
    from contextlib import contextmanager

    @contextmanager
    def timed_child(*args, **kwargs):
        with real_child_process(*args, **kwargs) as proc:
            clock[0] += 8.0
            yield proc

    monkeypatch.setattr(worker, "child_process", timed_child)

    receipt = run_once(env)
    assert receipt["hook_prepare_seconds"] == 1.5
    assert receipt["control_overhead_seconds"] == 2.0
    assert receipt["work_wall_seconds"] == 8.0
    assert receipt["control_tax_ratio"] == 0.2

    # Case B: Early budget exhaustion (work_wall_seconds == 0.0) -> control_tax_ratio == 1.0.
    exhausted_dir = tmp_path / "exhausted"
    exhausted_dir.mkdir()
    env_exhausted = worker_env(exhausted_dir)
    env_exhausted["LOOPX_PHASE_DEADLINE_EPOCH"] = "1.0"
    clock[0] = 500.0

    def slow_recovery(_env):
        clock[0] += 0.25
        return None, ""

    from benchmark.runtime import interruption_recovery

    monkeypatch.setattr(interruption_recovery, "recovery_handoff", slow_recovery)
    exhausted_receipt = run_once(env_exhausted)
    assert exhausted_receipt["budget_exhausted"] is True
    assert exhausted_receipt["work_wall_seconds"] == 0.0
    assert exhausted_receipt["control_overhead_seconds"] == 0.25
    assert exhausted_receipt["control_tax_ratio"] == 1.0


def test_summarize_and_aggregate_control_telemetry_edge_cases():
    assert summarize_control_telemetry(
        hook_prepare_seconds=0.0,
        control_overhead_seconds=0.0,
        work_wall_seconds=0.0,
    ) == {
        "hook_prepare_seconds": 0.0,
        "control_overhead_seconds": 0.0,
        "work_wall_seconds": 0.0,
        "control_tax_ratio": 0.0,
    }
    assert summarize_control_telemetry(
        hook_prepare_seconds=1.5,
        control_overhead_seconds=0.5,
        work_wall_seconds=3.5,
    ) == {
        "hook_prepare_seconds": 1.5,
        "control_overhead_seconds": 1.5,
        "work_wall_seconds": 3.5,
        "control_tax_ratio": 0.3,
    }
    aggregated = aggregate_control_telemetry(
        [
            {"hook_prepare_seconds": 0.5, "control_overhead_seconds": 1.0, "work_wall_seconds": 1.0},
            {"hook_prepare_seconds": 1.0, "control_overhead_seconds": 1.5, "work_wall_seconds": 7.0},
        ],
        extra_control_seconds=0.5,
    )
    assert aggregated == {
        "hook_prepare_seconds": 1.5,
        "control_overhead_seconds": 3.0,
        "work_wall_seconds": 8.0,
        "control_tax_ratio": round(3.0 / 11.0, 6),
        "control_telemetry_verified": True,
        "control_telemetry_status": "verified",
    }
    observed_zero = aggregate_control_telemetry(
        [{"hook_prepare_seconds": 0.25, "control_overhead_seconds": 0.5, "work_wall_seconds": 0.0}],
        extra_control_seconds=0.5,
    )
    assert observed_zero == {
        "hook_prepare_seconds": 0.25,
        "control_overhead_seconds": 1.0,
        "work_wall_seconds": 0.0,
        "control_tax_ratio": 1.0,
        "control_telemetry_verified": True,
        "control_telemetry_status": "verified",
    }
    incomplete = aggregate_control_telemetry(
        [
            {"hook_prepare_seconds": 0.5, "control_overhead_seconds": 1.0, "work_wall_seconds": 1.0},
            {"hook_prepare_seconds": 1.0, "control_overhead_seconds": 1.5, "work_wall_seconds": None},
            {"hook_prepare_seconds": True, "control_overhead_seconds": float("nan"), "work_wall_seconds": "bad"},
        ],
        extra_control_seconds=0.5,
    )
    assert incomplete == {
        "hook_prepare_seconds": 1.5,
        "control_overhead_seconds": 3.0,
        "work_wall_seconds": None,
        "control_tax_ratio": None,
        "control_telemetry_verified": False,
        "control_telemetry_status": "untimed_receipt",
    }
    partial_missing = aggregate_control_telemetry(
        [
            {"hook_prepare_seconds": 0.5, "control_overhead_seconds": 1.0, "work_wall_seconds": 4.0},
        ],
        extra_control_seconds=0.5,
        missing_receipts=1,
    )
    assert partial_missing == {
        "hook_prepare_seconds": 0.5,
        "control_overhead_seconds": 1.5,
        "work_wall_seconds": None,
        "control_tax_ratio": None,
        "control_telemetry_verified": False,
        "control_telemetry_status": "missing_receipt",
    }


def test_loopx_heartbeat_context_delivery_records_hook_prepare_and_control_tax(
    tmp_path, monkeypatch
):
    from contextlib import contextmanager
    from benchmark.runtime import checkpoint_wake, context_delivery, interruption_recovery

    env = _sqlite_heartbeat_env(tmp_path, monkeypatch)
    _write_hooks_list_codex_stub(Path(env["CODEX_BIN"]))
    env.update(LOOPX_ITERATION_CONTEXT="fresh", LOOPX_CONTEXT_DELIVERY="on")

    clock = [1000.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(interruption_recovery, "recovery_handoff", lambda _env: (None, ""))

    class StubCheckpointWake:
        socket_path = None
        listener = None

        def __init__(self, _env, _receipt):
            clock[0] += 0.3

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(checkpoint_wake, "CheckpointWake", StubCheckpointWake)

    def stub_context_delivery(ctx_env, _home):
        clock[0] += 0.7
        ctx_env["LOOPX_CODEX_CONTEXT_DIR"] = str(tmp_path / "ctx")
        ctx_env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"] = "[]"

    monkeypatch.setattr(context_delivery, "prepare_context_delivery", stub_context_delivery)
    monkeypatch.setattr(worker, "heartbeat_body", lambda *_a, **_kw: (clock.__setitem__(0, clock[0] + 1.0) or "prompt"))
    monkeypatch.setattr(
        worker,
        "native_command",
        lambda env, execution, stage, wake, session_wake, native_hook_args=(): [env["CODEX_BIN"], "exec", "-"],
    )

    real_child = worker.child_process

    @contextmanager
    def timed_child(*args, **kwargs):
        with real_child(*args, **kwargs) as proc:
            clock[0] += 8.0
            yield proc

    monkeypatch.setattr(worker, "child_process", timed_child)

    receipt = run_once(env)
    assert receipt["ok"] is True
    assert receipt["context_transport"] == {"available": True}
    assert receipt["hook_prepare_seconds"] == 0.7
    assert receipt["control_overhead_seconds"] == 2.0
    assert receipt["work_wall_seconds"] == 8.0
    assert receipt["control_tax_ratio"] == 0.2


def test_harbor_phase_aggregates_summed_control_tax_ratio_excluding_prior_phases(
    tmp_path, monkeypatch
):
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor

    logs = tmp_path / "logs"
    wakes = logs / "wakes"
    (wakes / "prior-phase-wake").mkdir(parents=True)
    # Earlier phase wake that must NOT be counted into the new phase's diff.
    (wakes / "prior-phase-wake" / "receipt.json").write_text(
        json.dumps(
            {
                "hook_prepare_seconds": 99.0,
                "control_overhead_seconds": 99.0,
                "work_wall_seconds": 1.0,
                "control_tax_ratio": 0.99,
            }
        ),
        encoding="utf-8",
    )

    agent = harbor.BenchmarkCodex(
        logs_dir=logs,
        model_name="openai/fixture",
        execution_mode="plain",
        official_stop_hook=True,
        official_stop_hook_mode="todo_checklist",
    )
    clock = [10.0]
    monkeypatch.setattr(harbor.time, "monotonic", lambda: clock[0])

    async def write_task(_env, _instruction):
        clock[0] += 0.5  # phase prep overhead

    async def exec_as_agent(_env, command="", **kwargs):
        if command == "pwd":
            return SimpleNamespace(stdout="/workspace\n", return_code=0)
        # Worker produced two wakes during this phase with asymmetric durations.
        for name, payload in (
            (
                "wake-plan",
                summarize_control_telemetry(
                    hook_prepare_seconds=0.5,
                    control_overhead_seconds=1.0,
                    work_wall_seconds=1.0,
                ),
            ),
            (
                "wake-exec",
                summarize_control_telemetry(
                    hook_prepare_seconds=1.0,
                    control_overhead_seconds=1.5,
                    work_wall_seconds=7.0,
                ),
            ),
        ):
            d = wakes / name
            d.mkdir(parents=True)
            (d / "receipt.json").write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(stdout="", return_code=0)

    monkeypatch.setattr(agent, "_write_task_document", write_task)
    monkeypatch.setattr(agent, "exec_as_agent", exec_as_agent)

    context = SimpleNamespace()
    asyncio.run(agent.run("Solve task", SimpleNamespace(is_mounted=True), context))

    # Total control = 0.5 (prep) + 1.0 + 1.5 = 3.0; total work = 1.0 + 7.0 = 8.0; ratio = 3.0 / 11.0.
    assert context.metadata["official_stop_hook"] is True
    assert context.metadata["official_stop_hook_mode"] == "todo_checklist"
    assert context.metadata["hook_prepare_seconds"] == 1.5
    assert context.metadata["control_overhead_seconds"] == 3.0
    assert context.metadata["work_wall_seconds"] == 8.0
    assert context.metadata["control_tax_ratio"] == round(3.0 / 11.0, 6)
    assert context.metadata["control_telemetry_verified"] is True
    assert context.metadata["control_telemetry_status"] == "verified"
    assert (
        aggregate_control_telemetry([])
        == {
            "hook_prepare_seconds": 0.0,
            "control_overhead_seconds": 0.0,
            "work_wall_seconds": None,
            "control_tax_ratio": None,
            "control_telemetry_verified": False,
            "control_telemetry_status": "missing_receipt",
        }
    )


@pytest.mark.parametrize(
    ("scenario", "receipt_payload", "expected_status", "expected_verified", "expected_work", "expected_ratio"),
    [
        ("missing_receipt", None, "missing_receipt", False, None, None),
        ("untimed_legacy_receipt", {"ok": True, "mode": "plain"}, "untimed_receipt", False, None, None),
        (
            "observed_zero_execution",
            {"hook_prepare_seconds": 0.0, "control_overhead_seconds": 0.5, "work_wall_seconds": 0.0},
            "verified",
            True,
            0.0,
            1.0,
        ),
        (
            "complete_timed_receipt",
            {"hook_prepare_seconds": 0.5, "control_overhead_seconds": 2.0, "work_wall_seconds": 6.0},
            "verified",
            True,
            6.0,
            round(3.0 / 9.0, 6),
        ),
    ],
)
def test_harbor_phase_distinguishes_missing_untimed_and_observed_zero_telemetry(
    tmp_path,
    monkeypatch,
    scenario,
    receipt_payload,
    expected_status,
    expected_verified,
    expected_work,
    expected_ratio,
):
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor

    logs = tmp_path / f"logs-{scenario}"
    wakes = logs / "wakes"
    (wakes / "prior-phase-wake").mkdir(parents=True)
    (wakes / "prior-phase-wake" / "receipt.json").write_text(
        json.dumps(
            {
                "hook_prepare_seconds": 99.0,
                "control_overhead_seconds": 99.0,
                "work_wall_seconds": 1.0,
                "control_tax_ratio": 0.99,
            }
        ),
        encoding="utf-8",
    )

    agent = harbor.BenchmarkCodex(
        logs_dir=logs,
        model_name="openai/fixture",
        execution_mode="plain",
        official_stop_hook=True,
        official_stop_hook_mode="todo_checklist",
    )
    clock = [100.0]
    monkeypatch.setattr(harbor.time, "monotonic", lambda: clock[0])

    async def write_task(_env, _instruction):
        clock[0] += 1.0

    async def exec_as_agent(_env, command="", **kwargs):
        if command == "pwd":
            return SimpleNamespace(stdout="/workspace\n", return_code=0)
        if receipt_payload is not None:
            cur = wakes / "current-phase-wake"
            cur.mkdir(parents=True)
            (cur / "receipt.json").write_text(json.dumps(receipt_payload), encoding="utf-8")
        return SimpleNamespace(stdout="", return_code=0)

    monkeypatch.setattr(agent, "_write_task_document", write_task)
    monkeypatch.setattr(agent, "exec_as_agent", exec_as_agent)

    context = SimpleNamespace()
    asyncio.run(agent.run("Solve task", SimpleNamespace(is_mounted=True), context))

    assert context.metadata["control_telemetry_verified"] is expected_verified
    assert context.metadata["control_telemetry_status"] == expected_status
    assert context.metadata["work_wall_seconds"] == expected_work
    assert context.metadata["control_tax_ratio"] == expected_ratio
    assert context.metadata["control_overhead_seconds"] >= 1.0
    assert context.metadata["hook_prepare_seconds"] <= 1.0


def _assert_multi_wake_harbor_partial_coverage_case(
    harbor_rt: Any,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context_cls: type[Any],
    execution_mode: str,
    official_stop_hook: bool,
    official_stop_hook_mode: str | None,
    wake2_kind: str,
) -> None:
    """Verify multi-wake Harbor phase telemetry across plain and heartbeat modes."""
    logs = tmp_path / f"multi-{execution_mode}-{wake2_kind}"
    wakes = logs / "wakes"
    (wakes / "prior-wake").mkdir(parents=True)
    (wakes / "prior-wake" / "receipt.json").write_text(
        json.dumps(
            summarize_control_telemetry(
                hook_prepare_seconds=50.0,
                control_overhead_seconds=50.0,
                work_wall_seconds=1.0,
            )
        ),
        encoding="utf-8",
    )

    agent = harbor_rt.BenchmarkCodex(
        logs_dir=logs,
        model_name="openai/fixture",
        execution_mode=execution_mode,
        official_stop_hook=official_stop_hook,
        official_stop_hook_mode=official_stop_hook_mode,
    )
    clock = [300.0]
    monkeypatch.setattr(harbor_rt.time, "monotonic", lambda: clock[0])

    async def _prep(*_args, **_kwargs):
        clock[0] += 1.0

    async def _env_exec(*_args, **_kwargs):
        return SimpleNamespace(
            stdout=json.dumps({"state_readback_verified": True, "status": "ready"}),
            return_code=0,
        )

    def _write_wake1():
        w1 = wakes / "wake-1"
        w1.mkdir(parents=True)
        (w1 / "receipt.json").write_text(
            json.dumps(
                {
                    "hook_prepare_seconds": 0.5,
                    "control_overhead_seconds": 1.5,
                    "work_wall_seconds": 6.0,
                }
            ),
            encoding="utf-8",
        )

    def _write_wake2():
        w2 = wakes / "wake-2"
        w2.mkdir(parents=True)
        if wake2_kind == "corrupt_json":
            (w2 / "receipt.json").write_text('{"work_wall_seconds": 4.0', encoding="utf-8")
        elif wake2_kind == "non_dict_json":
            (w2 / "receipt.json").write_text("[1, 2, 3]", encoding="utf-8")
        elif wake2_kind == "symlink_receipt":
            target = tmp_path / f"symlink-target-{execution_mode}.json"
            target.write_text(
                json.dumps(
                    {
                        "hook_prepare_seconds": 1.0,
                        "control_overhead_seconds": 1.5,
                        "work_wall_seconds": 2.0,
                    }
                ),
                encoding="utf-8",
            )
            (w2 / "receipt.json").symlink_to(target)
        elif wake2_kind == "untimed":
            (w2 / "receipt.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        elif wake2_kind == "complete":
            (w2 / "receipt.json").write_text(
                json.dumps(
                    {
                        "hook_prepare_seconds": 1.0,
                        "control_overhead_seconds": 1.5,
                        "work_wall_seconds": 2.0,
                    }
                ),
                encoding="utf-8",
            )

    async def _exec(_env, command="", env=None, **_kwargs):
        if command == "pwd":
            return SimpleNamespace(stdout="/workspace\n", return_code=0)
        if execution_mode == "heartbeat":
            if (env or {}).get("LOOPX_TASK_STAGE") == "plan":
                _write_wake1()
            else:
                _write_wake2()
        else:
            _write_wake1()
            _write_wake2()
        return SimpleNamespace(stdout="", return_code=0)

    if execution_mode == "heartbeat":
        monkeypatch.setattr(agent, "_prepare_phase", _prep)
    else:
        monkeypatch.setattr(agent, "_write_task_document", _prep)
    monkeypatch.setattr(agent, "exec_as_agent", _exec)

    ctx = context_cls()
    asyncio.run(
        agent.run(
            "Solve task",
            SimpleNamespace(is_mounted=True, exec=_env_exec),
            ctx,
        )
    )

    if wake2_kind == "complete":
        assert ctx.metadata["control_telemetry_verified"] is True
        assert ctx.metadata["control_telemetry_status"] == "verified"
        assert ctx.metadata["hook_prepare_seconds"] == 1.5
        assert ctx.metadata["control_overhead_seconds"] == 4.0
        assert ctx.metadata["work_wall_seconds"] == 8.0
        assert ctx.metadata["control_tax_ratio"] == round(4.0 / 12.0, 6)
    else:
        expected_status = "untimed_receipt" if wake2_kind == "untimed" else "missing_receipt"
        assert ctx.metadata["control_telemetry_verified"] is False
        assert ctx.metadata["control_telemetry_status"] == expected_status
        assert ctx.metadata["hook_prepare_seconds"] == 0.5
        assert ctx.metadata["control_overhead_seconds"] == 2.5
        assert ctx.metadata["work_wall_seconds"] is None
        assert ctx.metadata["control_tax_ratio"] is None

    # Also verify direct post-run population when a phase wake lacks a valid receipt.
    post_ctx = context_cls()
    agent.populate_context_post_run(post_ctx)
    if wake2_kind == "complete":
        assert post_ctx.metadata["control_telemetry_verified"] is True
        assert post_ctx.metadata["control_telemetry_status"] == "verified"
    else:
        assert post_ctx.metadata["control_telemetry_verified"] is False
        assert post_ctx.metadata["control_telemetry_status"] == (
            "untimed_receipt" if wake2_kind == "untimed" else "missing_receipt"
        )
        assert post_ctx.metadata["work_wall_seconds"] is None
        assert post_ctx.metadata["control_tax_ratio"] is None


_MULTI_WAKE_MODES = (
    ("plain", True, "todo_checklist"),
    ("heartbeat", False, None),
)
_MULTI_WAKE_CASES = (
    "missing",
    "corrupt_json",
    "non_dict_json",
    "symlink_receipt",
    "untimed",
    "complete",
)


@pytest.mark.parametrize(
    ("execution_mode", "official_stop_hook", "official_stop_hook_mode"),
    _MULTI_WAKE_MODES,
)
@pytest.mark.parametrize("wake2_kind", _MULTI_WAKE_CASES)
def test_harbor_phase_fails_closed_when_any_started_wake_lacks_valid_receipt(
    tmp_path,
    monkeypatch,
    execution_mode,
    official_stop_hook,
    official_stop_hook_mode,
    wake2_kind,
):
    pytest.importorskip("harbor")
    from benchmark.runtime import harbor

    _assert_multi_wake_harbor_partial_coverage_case(
        harbor,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_cls=SimpleNamespace,
        execution_mode=execution_mode,
        official_stop_hook=official_stop_hook,
        official_stop_hook_mode=official_stop_hook_mode,
        wake2_kind=wake2_kind,
    )


def test_harbor_adapter_executes_mode_isolation_and_telemetry_with_stub_if_uninstalled(
    tmp_path, monkeypatch
):
    import importlib
    import types

    try:
        import harbor  # noqa: F401
        pytest.skip("Covered by real harbor tests above")
    except ImportError:
        pass

    class DummyCodex:
        def __init__(self, *args, logs_dir=None, model_name=None, **_kwargs):
            self.logs_dir = Path(logs_dir) if logs_dir is not None else tmp_path
            self.model_name = model_name
            self._version = None
            self._resolved_flags = {}
            self.logger = SimpleNamespace(info=lambda *_a: None)

        @staticmethod
        def _get_env(_key):
            return None

        async def exec_as_agent(self, *_args, **_kwargs):
            return SimpleNamespace(stdout="", return_code=0)

    class DummyAgentContext:
        pass

    class DummyFinalMetrics:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class DummyTrajectory:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def to_json_dict(self):
            return {}

    harbor_mod = types.ModuleType("harbor")
    agents_mod = types.ModuleType("harbor.agents")
    installed_mod = types.ModuleType("harbor.agents.installed")
    base_mod = types.ModuleType("harbor.agents.installed.base")
    base_mod.with_prompt_template = lambda fn: fn
    codex_mod = types.ModuleType("harbor.agents.installed.codex")
    codex_mod.Codex = DummyCodex
    env_mod = types.ModuleType("harbor.environments")
    env_base_mod = types.ModuleType("harbor.environments.base")
    env_base_mod.BaseEnvironment = object
    models_mod = types.ModuleType("harbor.models")
    agent_mod = types.ModuleType("harbor.models.agent")
    ctx_mod = types.ModuleType("harbor.models.agent.context")
    ctx_mod.AgentContext = DummyAgentContext
    traj_mod = types.ModuleType("harbor.models.trajectories")
    traj_mod.FinalMetrics = DummyFinalMetrics
    traj_mod.Trajectory = DummyTrajectory
    utils_mod = types.ModuleType("harbor.utils")
    traj_utils_mod = types.ModuleType("harbor.utils.trajectory_utils")
    traj_utils_mod.format_trajectory_json = json.dumps

    for name, mod in (
        ("harbor", harbor_mod),
        ("harbor.agents", agents_mod),
        ("harbor.agents.installed", installed_mod),
        ("harbor.agents.installed.base", base_mod),
        ("harbor.agents.installed.codex", codex_mod),
        ("harbor.environments", env_mod),
        ("harbor.environments.base", env_base_mod),
        ("harbor.models", models_mod),
        ("harbor.models.agent", agent_mod),
        ("harbor.models.agent.context", ctx_mod),
        ("harbor.models.trajectories", traj_mod),
        ("harbor.utils", utils_mod),
        ("harbor.utils.trajectory_utils", traj_utils_mod),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "benchmark.runtime.codex_offline", raising=False)
    monkeypatch.delitem(sys.modules, "benchmark.runtime.harbor", raising=False)

    harbor_rt = importlib.import_module("benchmark.runtime.harbor")
    try:
        with pytest.raises(ValueError, match="official_stop_hook"):
            harbor_rt.BenchmarkCodex(
                logs_dir=tmp_path / "bad",
                model_name="openai/fixture",
                execution_mode="plain",
                official_stop_hook=True,
                official_stop_hook_mode="bad-mode",
            )

        monkeypatch.setenv("LOOPX_OFFICIAL_STOP_HOOK_MODE", "todo_checklist")
        plain = harbor_rt.BenchmarkCodex(
            logs_dir=tmp_path / "plain",
            model_name="openai/fixture",
            execution_mode="plain",
        )
        assert plain._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK_MODE"] == ""

        # Verify empty-wakes _populate_context preserves parity without extra telemetry keys.
        empty_ctx = DummyAgentContext()
        plain._populate_context(empty_ctx)
        assert "official_stop_hook_mode" not in empty_ctx.metadata
        assert "control_tax_ratio" not in empty_ctx.metadata

        logs = tmp_path / "phase-logs"
        wakes = logs / "wakes"
        (wakes / "prior-wake").mkdir(parents=True)
        (wakes / "prior-wake" / "receipt.json").write_text(
            json.dumps(
                summarize_control_telemetry(
                    hook_prepare_seconds=50.0,
                    control_overhead_seconds=50.0,
                    work_wall_seconds=1.0,
                )
            ),
            encoding="utf-8",
        )

        checklist = harbor_rt.BenchmarkCodex(
            logs_dir=logs,
            model_name="openai/fixture",
            execution_mode="plain",
            official_stop_hook=True,
            official_stop_hook_mode="todo_checklist",
        )
        assert checklist._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK_MODE"] == "todo_checklist"

        clock = [10.0]
        monkeypatch.setattr(harbor_rt.time, "monotonic", lambda: clock[0])

        async def write_task(_env, _instruction):
            clock[0] += 0.5

        async def exec_as_agent(_env, command="", **kwargs):
            if command == "pwd":
                return SimpleNamespace(stdout="/workspace\n", return_code=0)
            for name, payload in (
                (
                    "wake-1",
                    summarize_control_telemetry(
                        hook_prepare_seconds=0.5,
                        control_overhead_seconds=1.0,
                        work_wall_seconds=1.0,
                    ),
                ),
                (
                    "wake-2",
                    summarize_control_telemetry(
                        hook_prepare_seconds=1.0,
                        control_overhead_seconds=1.5,
                        work_wall_seconds=7.0,
                    ),
                ),
            ):
                (wakes / name).mkdir(parents=True)
                (wakes / name / "receipt.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            return SimpleNamespace(stdout="", return_code=0)

        monkeypatch.setattr(checklist, "_write_task_document", write_task)
        monkeypatch.setattr(checklist, "exec_as_agent", exec_as_agent)

        ctx = DummyAgentContext()
        asyncio.run(checklist.run("Solve task", SimpleNamespace(is_mounted=True), ctx))
        assert ctx.metadata["official_stop_hook"] is True
        assert ctx.metadata["official_stop_hook_mode"] == "todo_checklist"
        assert ctx.metadata["hook_prepare_seconds"] == 1.5
        assert ctx.metadata["control_overhead_seconds"] == 3.0
        assert ctx.metadata["work_wall_seconds"] == 8.0
        assert ctx.metadata["control_tax_ratio"] == round(3.0 / 11.0, 6)
        assert ctx.metadata["control_telemetry_verified"] is True
        assert ctx.metadata["control_telemetry_status"] == "verified"

        # Counterfactual telemetry checks under real BenchmarkCodex.run():
        # missing_receipt (both prep=1.0 and prep=0.0), untimed_legacy_receipt,
        # observed_zero_execution, and complete_timed_receipt must all isolate prior wakes.
        for sc_name, sc_prep, sc_payload, sc_verified, sc_status, sc_ctrl, sc_work, sc_ratio in (
            ("missing_with_prep", 1.0, None, False, "missing_receipt", 1.0, None, None),
            ("missing_zero_prep", 0.0, None, False, "missing_receipt", 0.0, None, None),
            ("untimed_legacy", 1.0, {"ok": True, "mode": "plain"}, False, "untimed_receipt", 1.0, None, None),
            (
                "observed_zero",
                1.0,
                {"hook_prepare_seconds": 0.0, "control_overhead_seconds": 0.5, "work_wall_seconds": 0.0},
                True,
                "verified",
                1.5,
                0.0,
                1.0,
            ),
            (
                "complete_timed",
                1.0,
                {"hook_prepare_seconds": 0.0, "control_overhead_seconds": 2.0, "work_wall_seconds": 6.0},
                True,
                "verified",
                3.0,
                6.0,
                round(3.0 / 9.0, 6),
            ),
        ):
            sc_logs = tmp_path / f"phase-{sc_name}"
            sc_wakes = sc_logs / "wakes"
            (sc_wakes / "prior-wake").mkdir(parents=True)
            (sc_wakes / "prior-wake" / "receipt.json").write_text(
                json.dumps(
                    summarize_control_telemetry(
                        hook_prepare_seconds=50.0,
                        control_overhead_seconds=50.0,
                        work_wall_seconds=1.0,
                    )
                ),
                encoding="utf-8",
            )
            sc_agent = harbor_rt.BenchmarkCodex(
                logs_dir=sc_logs,
                model_name="openai/fixture",
                execution_mode="plain",
                official_stop_hook=True,
                official_stop_hook_mode="todo_checklist",
            )
            clock[0] = 200.0

            async def _sc_write_task(_env, _instruction, delta=sc_prep):
                clock[0] += delta

            async def _sc_exec(_env, command="", payload=sc_payload, wake_root=sc_wakes, **_kwargs):
                if command == "pwd":
                    return SimpleNamespace(stdout="/workspace\n", return_code=0)
                if payload is not None:
                    cur = wake_root / "current-wake"
                    cur.mkdir(parents=True)
                    (cur / "receipt.json").write_text(json.dumps(payload), encoding="utf-8")
                return SimpleNamespace(stdout="", return_code=0)

            monkeypatch.setattr(sc_agent, "_write_task_document", _sc_write_task)
            monkeypatch.setattr(sc_agent, "exec_as_agent", _sc_exec)
            sc_ctx = DummyAgentContext()
            asyncio.run(sc_agent.run("Solve task", SimpleNamespace(is_mounted=True), sc_ctx))
            assert sc_ctx.metadata["control_telemetry_verified"] is sc_verified
            assert sc_ctx.metadata["control_telemetry_status"] == sc_status
            assert sc_ctx.metadata["control_overhead_seconds"] == sc_ctrl
            assert sc_ctx.metadata["work_wall_seconds"] == sc_work
            assert sc_ctx.metadata["control_tax_ratio"] == sc_ratio

        for execution_mode, official_stop_hook, official_stop_hook_mode in _MULTI_WAKE_MODES:
            for wake2_kind in _MULTI_WAKE_CASES:
                _assert_multi_wake_harbor_partial_coverage_case(
                    harbor_rt,
                    tmp_path=tmp_path,
                    monkeypatch=monkeypatch,
                    context_cls=DummyAgentContext,
                    execution_mode=execution_mode,
                    official_stop_hook=official_stop_hook,
                    official_stop_hook_mode=official_stop_hook_mode,
                    wake2_kind=wake2_kind,
                )
    finally:
        sys.modules.pop("benchmark.runtime.harbor", None)
        sys.modules.pop("benchmark.runtime.codex_offline", None)
