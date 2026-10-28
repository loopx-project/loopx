"""Exercise the shipped Pi command handler against the real isolated LoopX CLI.

Only Pi's display/schema SDK is substituted; identity gates, CLI execution and
registry reads/writes are real. No model, user session or live Goal is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.control_plane.host_process_fixture import write_python_host_fixture


REPO = Path(__file__).resolve().parents[2]
HOST = r"""
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
registerHooks({
  resolve(specifier, context, next) {
    if (['typebox', '@earendil-works/pi-ai', '@earendil-works/pi-tui'].includes(specifier))
      return { url: 'pi-test:sdk', shortCircuit: true };
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url === 'pi-test:sdk') return { format: 'module', shortCircuit: true,
      source: `export const Type = new Proxy({}, {get: () => () => ({})});
        export const StringEnum = () => ({}); export class Text {}` };
    return next(url, context);
  }
});
const { default: extension } = await import(pathToFileURL(process.argv[1]));
const commands = new Map(), tools = new Map(), events = new Map();
const packets = [], notices = [];
extension({
  registerCommand: (name, value) => commands.set(name, value),
  registerTool: tool => tools.set(tool.name, tool),
  registerEntryRenderer() {}, appendEntry() {},
  on: (name, handler) => events.set(name, handler),
  sendUserMessage: text => packets.push(JSON.parse(text))
});
const ctx = {
  cwd: process.argv[2], isIdle: () => true,
  sessionManager: { getSessionFile: () => process.argv[3] || undefined },
  ui: { setWidget() {}, notify: (message, kind) => notices.push({message, kind}) }
};
if (process.argv[4]) await commands.get('loopx').handler(process.argv[4], ctx);
await commands.get('loopx').handler('Continue the synthetic documentation task', ctx);
// A model cannot activate with a guessed token, even after a legal startup.
const rejected = await tools.get('loopx_goal_activate').execute('test', {
  activationToken: 'unissued-synthetic-token', objective: 'synthetic task'
}, undefined, undefined, ctx);
await events.get('session_shutdown')?.({}, ctx);
console.log(JSON.stringify({packets, notices, rejected}));
"""


def _thread_id(file: Path) -> str:
    # Independent protocol oracle: full-path digest survives the short label.
    label = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_"
                    for c in file.name)[:48]
    return label + "-" + hashlib.sha256(str(file).encode()).hexdigest()[:16]


@pytest.mark.parametrize("case", ["bound", "bound_after_status", "sole", "multiple", "none", "ephemeral"])
@pytest.mark.parametrize("selected_goal", [False, True])
def test_pi_start_preserves_native_identity_authority(tmp_path, monkeypatch, case, selected_goal):
    preview_command = "status" if case == "bound_after_status" else ""
    if preview_command:
        case = "bound"
    node = shutil.which("node")
    assert node, "Node.js >=22.22.3 is required for the shipped Pi adapter"
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    state = project / ".codex" / "goals" / "pi-test" / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text("# Active Goal State\n\n## Agent Todos\n", encoding="utf-8")
    agents = [] if case == "none" else ["existing-agent"]
    if case == "multiple":
        agents.append("another-agent")
    goal = {
        "id": "pi-test", "status": "active", "repo": str(project),
        "state_file": str(state.relative_to(project)),
        "coordination": {"agent_model": "peer_v1", "registered_agents": agents},
    }
    source = {"schema_version": "0.1", "common_runtime_root": str(runtime), "goals": [goal]}
    registry.write_text(json.dumps(source), encoding="utf-8")
    from canonical_authority_fixture import initialize_canonical_source_fixture
    initialize_canonical_source_fixture(runtime, "pi-test", state_path=state, provider="sqlite")
    global_registry = runtime / "registry.global.json"
    global_registry.write_text(json.dumps({
        **source, "registry_role": "global-local",
        "goals": [{**goal, "source_registry": str(registry)}],
    }), encoding="utf-8")
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    monkeypatch.setenv("PI_SESSION_ID", "inherited-parent-session")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    session = project / "sessions" / "session.jsonl"
    if case == "bound":
        bound = subprocess.run([
            sys.executable, "-m", "loopx.cli", "--runtime-root", str(runtime),
            "--format", "json", "bind-agent-thread", "--goal-id", "pi-test",
            "--thread-id", _thread_id(session), "--host-surface", "pi",
            "--agent-id", "existing-agent", "--execute",
        ], cwd=project, capture_output=True, text=True, timeout=60)
        assert bound.returncode == 0, bound.stdout + bound.stderr
        assert json.loads(bound.stdout)["ok"] is True
    before = [p.read_bytes() for p in (registry, global_registry, state)]
    calls = tmp_path / "calls.jsonl"
    # Reuse the portable native executable fixture, including Windows.
    launcher = write_python_host_fixture(tmp_path / "loopx-test", (
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
        f"with Path({str(calls)!r}).open('a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        # Also exercise the canonical packet after the Goal selection step;
        # this selects a Goal, never an Agent or a fabricated gate result.
        "from loopx.cli import main\n"
        f"extra = ['--goal-id', 'pi-test'] if {selected_goal!r} and 'start-goal' in sys.argv else []\n"
        "raise SystemExit(main(sys.argv[1:] + extra))\n"
    ))
    monkeypatch.setenv("LOOPX_BIN", str(launcher))
    completed = subprocess.run([
        node, "--input-type=module", "-e", HOST,
        str(REPO / "loopx" / "pi_goal_mode" / "loopx-goal.ts"),
        str(project), "" if case == "ephemeral" else str(session),
        preview_command,
    ], capture_output=True, text=True, timeout=90, env=os.environ.copy())
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert len(result["packets"]) == 1, result["notices"]
    packet = result["packets"][0]
    argv = [json.loads(line) for line in calls.read_text().splitlines()]
    if preview_command:
        assert argv.pop(0) == ["--format", "json", "bootstrap-command-pack", "--project", "."]
    assert len(argv) == 1, "startup must not auto-bind/register or probe quota"
    assert argv[0][2] == "start-goal"
    if case == "ephemeral":
        assert "--thread-id" not in argv[0]
    else:
        assert argv[0][argv[0].index("--thread-id") + 1] == _thread_id(session)
        assert str(session.parent) not in argv[0]
        assert "inherited-parent-session" not in argv[0]
    activation = packet["command_pack"]["host_loop_activation"]
    if case == "bound":
        assert activation["activation_state"] == "thread_binding_selected"
        assert activation["activation_allowed"] is True
        assert packet["pi_session_authority"]["agent_id"] == "existing-agent"
    else:
        assert activation["activation_allowed"] is False
        assert "pi_session_authority" not in packet
        if case == "sole":
            choice = activation["identity_selection_gate"]["choices"]
            assert len(choice) == 1
            assert choice[0]["requires_explicit_takeover_intent"] is True
    rejection = json.loads(result["rejected"]["content"][0]["text"])
    assert rejection["ok"] is False
    assert rejection["error_code"] == ("authority_mismatch" if case == "bound" else "authority_not_bound")
    assert before == [p.read_bytes() for p in (registry, global_registry, state)]
