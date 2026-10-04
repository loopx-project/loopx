"""Attached callers use real CLI processes and the existing canonical acceptance."""

import json
import subprocess
import sys
import time

from test_local_delegation import brief, demo, service as delegation_service

service = delegation_service


def cli(runner, action, *args, actor=None):
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(runner.registry),
               "--runtime-root", str(runner.root), "--format", "json", "delegation", action,
               "--goal-id", runner.goal_id, "--agent-id", actor or runner.agent_id,
               "--execution-config", str(runner.config), *args]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=40)
    return completed.returncode, json.loads(completed.stdout)


def test_attached_cli_disconnect_retry_and_verified_return(service):
    root, runner = service
    (root / "hold").touch()
    source = root / "brief.json"
    source.write_text(json.dumps(brief()))
    status, listing = cli(runner, "list")
    assert status == 0
    assert listing["bindings"] == [{"id": "analysis", "agent_id": "analyst", "todo_id": "todo_analyst-initial"}]
    assert not runner.path("cli-work").exists()
    args = ["--binding-id", "analysis", "--operation-id", "cli-work", "--brief-file", str(source)]
    status, result = cli(runner, "start", *args)
    assert status == 1 and "--execute" in result["error"]
    assert not runner.path("cli-work").exists()
    status, first = cli(runner, "start", *args, "--execute")
    assert status == 0
    deadline = time.monotonic() + 45
    while not (root / "host-started").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert (root / "host-started").exists()
    try:
        # Each CLI has exited. Work belongs to the original detached operation,
        # and a new client resumes/reads it without creating another host turn.
        status, observed = cli(runner, "read", "--operation-id", "cli-work")
        assert status == 0 and observed["request_id"] == first["request_id"]
        status, replay = cli(runner, "start", *args, "--execute")
        assert status == 0 and replay["request_id"] == first["request_id"]
        status, resumed = cli(runner, "resume", "--operation-id", "cli-work", "--execute")
        assert status == 0 and resumed["request_id"] == first["request_id"]
    finally:
        (root / "release").touch()
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        status, result = cli(runner, "wait", "--operation-id", "cli-work")
        assert status == 0, result
        if result["status"] in {"accepted", "rejected"}:
            break
    assert result["status"] == "accepted", result
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    assert demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert result["artifacts"][0]["sha256"]

    # A fresh CLI needs only the requester binding, not remembered operation ids.
    status, inventory = cli(runner, "operations")
    assert status == 0 and inventory["page_readback_complete"]
    assert inventory["items"][0]["operation_id"] == "cli-work"
    assert inventory["items"][0]["status"] == "accepted"
    assert inventory["items"][0]["artifacts"][0]["sha256"] == result["artifacts"][0]["sha256"]
    assert "text" not in inventory["items"][0]["artifacts"][0]
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    status, other = cli(runner, "operations", actor="reviewer")
    assert status == 0 and other["items"] == []

    status, denied = cli(runner, "read", "--operation-id", "cli-work", actor="reviewer")
    assert status == 1 and not denied["ok"]
    # Changing an accepted artifact cannot be hidden behind the saved result.
    output = root / "analyst" / "initial" / "output.json"
    output.write_text("{}")
    status, stale = cli(runner, "read", "--operation-id", "cli-work")
    assert status == 1 and not stale["ok"]
    status, inventory = cli(runner, "operations")
    assert status == 0 and not inventory["page_readback_complete"]
    assert inventory["items"][0]["status"] == "unavailable"
    assert "artifacts" not in inventory["items"][0]


def test_cli_stop_settles_a_running_member_and_refuses_resume(service):
    root, runner = service
    (root / "hold").touch()
    source = root / "brief.json"
    source.write_text(json.dumps(brief()))
    status, started = cli(runner, "start", "--binding-id", "analysis", "--operation-id", "cli-stop",
                          "--brief-file", str(source), "--execute")
    assert status == 0, started
    deadline = time.monotonic() + 45
    while not (root / "host-started").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert (root / "host-started").exists()
    status, refused = cli(runner, "stop", "--operation-id", "cli-stop")
    assert status == 1 and "--execute" in refused["error"]
    assert not runner._stop_path(runner.path("cli-stop")).exists()
    status, stopped = cli(runner, "stop", "--operation-id", "cli-stop", "--execute")
    assert status == 0 and stopped["phase"] == "settled" and stopped["status"] == "stopped", stopped
    assert stopped["stop"]["ack"]["source"] == "SIGTERM"
    status, again = cli(runner, "stop", "--operation-id", "cli-stop", "--execute")
    assert status == 0 and again == stopped
    status, resumed = cli(runner, "resume", "--operation-id", "cli-stop", "--execute")
    assert status == 1 and "start a new operation id" in resumed["error"]
    status, observed = cli(runner, "read", "--operation-id", "cli-stop")
    assert status == 0 and observed["status"] == "stopped" and observed["stop"]["phase"] == "settled"
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]


def test_cli_invalid_inputs_do_not_launch_work(service):
    root, runner = service
    bad = root / "bad.json"
    bad.write_text("{")
    status, result = cli(runner, "start", "--execute", "--binding-id", "analysis",
                         "--operation-id", "invalid", "--brief-file", str(bad))
    assert status == 1 and not result["ok"]
    assert not runner.path("invalid").exists()
    bad.write_text(" " * 128_001)
    status, result = cli(runner, "start", "--execute", "--binding-id", "analysis",
                         "--operation-id", "oversize", "--brief-file", str(bad))
    assert status == 1 and "128000" in result["error"]
    status, result = cli(runner, "resume", "--operation-id", "missing")
    assert status == 1 and "--execute" in result["error"]
    assert not (root / "host-started").exists()
    for arguments in [("--limit", "0"), ("--cursor", "invalid"), ("--execute",), ("--operation-id", "wrong")]:
        status, result = cli(runner, "operations", *arguments)
        assert status == 1 and not result["ok"]
    status, result = cli(runner, "list", "--limit", "5")
    assert status == 1 and not result["ok"]


def test_one_shot_cli_inspection_keeps_original_subprocess(service, monkeypatch, capsys):
    """The real CLI entry must not pay for a worker it cannot reuse."""
    from loopx.cli import main
    from loopx.control_plane.collaboration.delegation_preview_transport import DelegationPreviewTransport

    root, runner = service
    assert runner._preview_transport is None
    before = runner.registry.read_bytes(), runner.config.read_bytes(), demo.canonical_tasks(root)

    def no_supervisor(*args, **kwargs):
        raise AssertionError("single-use CLI started preview reuse")

    monkeypatch.setattr(DelegationPreviewTransport, "__init__", no_supervisor)
    expected = runner.inspect("analysis")
    assert main(["--registry", str(runner.registry), "--runtime-root", str(runner.root),
                 "--format", "json", "delegation", "inspect", "--goal-id", runner.goal_id,
                 "--agent-id", runner.agent_id, "--execution-config", str(runner.config),
                 "--binding-id", "analysis"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, **expected}
    assert (runner.registry.read_bytes(), runner.config.read_bytes(), demo.canonical_tasks(root)) == before
    assert not (root / "host-started").exists()
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))


def test_shared_execution_host_does_not_require_optional_mcp():
    script = """
import importlib.abc, sys
class NoMCP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'mcp' or fullname.startswith('mcp.'):
            raise ImportError('MCP is intentionally absent')
sys.meta_path.insert(0, NoMCP())
from loopx.collaboration_mcp import Delegations
from loopx.cli import build_parser
build_parser().parse_args(['delegation', 'list', '--goal-id', 'goal', '--agent-id', 'lead', '--execution-config', 'bindings.json'])
"""
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_example_prepare_exposes_grants_without_starting_a_lead(tmp_path):
    root = tmp_path / "attached-team"
    prepared = subprocess.run(
        [sys.executable, str(demo.HERE / "research_team.py"), "prepare", str(root),
         "--model", "fixture-model", "--environment-id", "fixture-environment"],
        capture_output=True, text=True, timeout=60,
    )
    assert prepared.returncode == 0, prepared.stderr
    result = json.loads(prepared.stdout)
    assert result["execution_started"] is False
    assert all(not row["done"] for row in demo.canonical_tasks(root).values())
    assert not list((root / "runtime").glob("goals/*/turns/*.json"))
    listed = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", result["registry"],
         "--runtime-root", result["runtime_root"], "delegation", "list",
         "--goal-id", result["goal_id"], "--agent-id", result["agent_id"],
         "--execution-config", result["execution_config"]],
        capture_output=True, text=True, timeout=30,
    )
    assert listed.returncode == 0, listed.stderr
    assert {row["id"] for row in json.loads(listed.stdout)["bindings"]} == {
        "local-analyst/initial", "cloud-reviewer/initial", "cloud-analyst/corrected",
    }
