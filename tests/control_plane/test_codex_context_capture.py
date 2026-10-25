"""Native tools execute the actual quota CLI and recover its original capture.

The loopback provider is a deterministic input-boundary observer. This qualifies
the opt-in production lifecycle/CLI composition, not installed adoption or solver benefit.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.turn_envelope import prepare_context_delivery, restore_context_delivery
from test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, _spend_run_count, _write_fixture
from tests.control_plane.codex_checkpoint_fixture import local_inference_provider
from tests.control_plane.test_heartbeat_envelope_transport import private_handoff  # noqa: F401 -- shared private socket fixture
from tests.control_plane.test_codex_context_boundary import _app_server, _configure_provider, _input_text, _native_fixture

pytestmark = pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
    reason="Explicit native Codex binary required; inference uses loopback only")


def _delivered(request, digest):
    # Independent byte-map oracle: recover every model-visible byte, including
    # separate code-mode emissions and overlapping reads at smaller page sizes.
    delivered = {}
    for item in request["input"]:
        output = item.get("output")
        texts = ([part["text"] for part in output if part.get("type") == "input_text"]
                 if item.get("type") == "custom_tool_call_output" and isinstance(output, list)
                 else [output])
        for output in texts:
            if not isinstance(output, str):
                continue
            try:
                page = json.loads(output.split("\nOutput:\n", 1)[-1])
                if isinstance(page, dict) and page.get("exit_code") == 0 and isinstance(page.get("output"), str):
                    page = json.loads(page["output"])
            except (ValueError, TypeError):
                continue
            if not isinstance(page, dict) or page.get("schema_version") != "context_capture_page_v1" or page.get("sha256") != digest:
                continue
            assert page["ok"]
            data = page["content"].encode()
            assert len(data) == (page["total_bytes"] if page["eof"] else page["next_offset"]) - page["offset"]
            for offset, value in enumerate(data, page["offset"]):
                assert offset not in delivered or delivered[offset] == value
                delivered[offset] = value
    if not delivered:
        return ""
    assert set(delivered) == set(range(len(delivered)))
    raw = bytes(delivered[i] for i in range(len(delivered)))
    assert hashlib.sha256(raw).hexdigest() == digest
    return raw.decode().rstrip("\n")


@pytest.mark.parametrize("host,surface,failed_primary,resumed,adaptive", [
    (host, surface, failed, False, False) for host, surface in [("exec", "quota_packet"), ("app_server", "turn_envelope"),
                                                ("code_mode", "quota_packet"), ("code_mode", "turn_envelope")]
    for failed in [False, True]
] + [("benchmark", surface, False, False, False) for surface in ("quota_packet", "turn_envelope")]
  + [(host, surface, False, True, False) for host in ("benchmark", "code_mode")
     for surface in ("quota_packet", "turn_envelope")]
  + [("code_mode", surface, False, True, True) for surface in ("quota_packet", "turn_envelope")])
def test_native_cli_capture_delta_and_compact_recovery(tmp_path, monkeypatch, host, surface, failed_primary, resumed, adaptive, hook_loss=None, shared_root=False, checkpoint_handoff=False, handoff_socket=None, native_pragma=False, native_read_batch=2, native_history_tokens=None):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    binary, env, workspace, home = _native_fixture(tmp_path)
    env.pop("CODEX_ROLLOUT_TRACE_ROOT", None)
    env.update(NODE_OPTIONS="--experimental-sqlite", LOOPX_USAGE_PING="0")
    project, runtime, registry = _write_fixture(workspace)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    body = "\n".join(f"Requirement {i:04d}: preserve this row and its evidence." for i in range(400))
    body += "\nFINAL REQUIREMENT: stop before publication."
    todos = read_fixture_todos(state)
    todos[0].update(text=body, title=body)
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider="sqlite")
    original_state = state.read_bytes()
    root = workspace / "captures"
    root.mkdir()
    wire_paths = {name: workspace / f"{name}.json" for name in ("cold", "warm")}
    lifecycle = workspace / "context"
    lifecycle.mkdir(mode=0o700)
    cli = ["env", f"PYTHONPATH={Path(__file__).resolve().parents[2]}", sys.executable,
        "-m", "loopx.entrypoint", "--cli-bin", str(Path(sys.executable).with_name("loopx"))]
    installed_cli = os.environ.get("LOOPX_NATIVE_CONTEXT_CLI")
    if installed_cli:
        cli = [installed_cli, "--cli-bin", installed_cli]
    context_option = "--codex-context-root" if shared_root else "--codex-context-dir"
    native_configuration = None
    if shared_root:
        prepared = subprocess.run(cli + ["--format", "json", "quota", "context-config",
            "--context-delivery", "on", "--codex-app", context_option, str(lifecycle), "--goal-id", GOAL_ID, "--agent-id", AGENT_ID],
            capture_output=True, text=True, timeout=15)
        assert prepared.returncode == 0, prepared.stderr
        native_configuration = json.loads(prepared.stdout)["native_config"]
    hook_command = (native_configuration["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        if native_configuration else shlex.join(cli + ["quota", "context-hook", "--context-delivery", "on", "--codex-app", context_option, str(lifecycle)]))
    if failed_primary:
        # Fault only the primary journal update. Production code must still
        # return the original full-read reference; no test recovery handler.
        hook = home / "hook.py"
        hook.write_text(
            "import json,sys\nfrom unittest.mock import patch\n"
            "from loopx.control_plane.turn_driver.codex_context import codex_context_hook\n"
            "event=json.load(sys.stdin)\n"
            "if event.get('source')=='compact':\n"
            "    with patch('loopx.control_plane.turn_driver.codex_context._save', side_effect=OSError('synthetic failure')):\n"
            f"        result=codex_context_hook({str(lifecycle)!r},event)\n"
            "else:\n"
            f"    result=codex_context_hook({str(lifecycle)!r},event)\n"
            "print(json.dumps(result))\n")
        hook_command = shlex.join(["env", f"PYTHONPATH={Path(__file__).resolve().parents[2]}", sys.executable, str(hook)])
    loss_marker = home / "lose-context-hook"
    if hook_loss:
        # Stop the whole hook process before product code runs. All later
        # events fail too: recovery must not depend on a second working hook.
        wrapper = home / "loopx-fixture"
        wrapper.write_text("#!" + sys.executable + "\nimport os,signal,sys\n"
            + f"if 'context-hook' in sys.argv and os.path.exists({str(loss_marker)!r}):\n"
            + ("    os.kill(os.getpid(), signal.SIGKILL)\n" if hook_loss == "killed" else "    sys.exit(0)\n")
            + (f"os.execv({installed_cli!r}, [{installed_cli!r}, *sys.argv[1:]])\n" if installed_cli else
               f"os.environ['PYTHONPATH']={str(Path(__file__).resolve().parents[2])!r}\n"
               + f"os.execv({sys.executable!r}, [{sys.executable!r}, '-m', 'loopx.entrypoint', *sys.argv[1:]])\n"))
        wrapper.chmod(0o700)
        hook_command = shlex.join([str(wrapper), "quota", "context-hook", "--context-delivery", "on", "--codex-app", context_option, str(lifecycle)])
    handler = {"type": "command", "command": hook_command, "timeout": 10}
    if host != "benchmark":
        from loopx.control_plane.turn_driver.codex_context import CONTEXT_LIFECYCLE_HOOKS

        (home / "hooks.json").write_text(json.dumps({"hooks": {
            event: [{**({"matcher": "^Bash$"} if event == "PostToolUse" else {}),
                     "hooks": [handler]}] for event in CONTEXT_LIFECYCLE_HOOKS}}))
    errors, snapshots = [], []
    stages = {"phase": "start", "pages": [], "baseline_confirmed": False, "compact_response": None}
    wires, page_digests, read_paths = {}, {}, {}
    command = [*cli, "--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", "native-capture-turn", "--codex-app",
        "--decision-output-root", str(root), "--context-delivery", "on", "--codex-app", context_option, str(lifecycle)]
    if handoff_socket:
        command += ["--decision-handoff-socket", handoff_socket]
    if surface == "turn_envelope":
        command.append("--turn-envelope")
    generated_guard = None
    if shared_root:
        generated = subprocess.run([*cli, "--registry", str(registry), "--runtime-root", str(runtime),
            "--format", "json", "heartbeat-prompt", "--thin", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--decision-output-root", str(root),
            "--context-delivery", "on", "--codex-app", context_option, str(lifecycle), *(["--turn-envelope"] if surface == "turn_envelope" else [])],
            capture_output=True, text=True, timeout=15)
        assert generated.returncode == 0, generated.stderr
        prompt = json.loads(generated.stdout)
        assert prompt["ok"], prompt
        generated_guard = next(line for line in prompt["task_body"].splitlines() if "quota should-run" in line)
        assert '${LOOPX_TURN:?}' in generated_guard and context_option in generated_guard
        generated_guard += " --todo-id " + shlex.quote(TODO_ID)
        env["LOOPX_TURN"] = "native-capture-turn"

    checkpoint_wakes, held_captures = [], []

    def invoke(name):
        argv = list(command)
        if checkpoint_handoff:
            assert argv[argv.index("--decision-handoff-socket") + 1] == checkpoint_wakes[-1].socket_path
        return (generated_guard or shlex.join(argv)) + " > " + shlex.quote(str(wire_paths[name])) + " && printf WIRE_READY"

    def pages(name, path):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        page_digests[name] = digest
        # Plan only read offsets. Every actual model-facing page must
        # come from the public CLI, including digest verification.
        page_size = 3072 if adaptive or host == "code_mode" else 5000
        offsets, start, count = [0], 0, 0
        for character in raw.decode():
            size = len(character.encode())
            if count + size > page_size:
                start += count
                offsets.append(start)
                count = 0
            count += size
        if host == "benchmark" and name in {"cold", "recovery"}:
            # Execute the shipped original recovery route, including its pinned
            # executable; no fixture substitute for installed command routing.
            recovery = wires["cold" if name == "cold" else "warm"]["recovery"]
            routed = shlex.split(recovery["command"])
            assert routed[:3] == [env["LOOPX_CLI"], "--cli-bin", env["LOOPX_CLI"]]
            assert routed[routed.index("--capture-file") + 1] == str(path)
            routed[routed.index("--page-bytes") + 1] = "5000"
            commands = []
            for offset in offsets:
                routed[routed.index("--offset") + 1] = str(offset)
                commands.append(shlex.join(routed))
            return commands
        commands = [shlex.join(cli + ["--format", "json", "quota", "context-read",
            "--capture-file", str(path), "--capture-sha256", digest,
            "--offset", str(offset), "--page-bytes", str(page_size)]) for offset in offsets]
        if adaptive and name == "cold":
            # An intact initial short read remains in current native input when
            # subsequent reads choose different boundaries. Production must
            # accept exact complete coverage without clearing that history.
            initial = shlex.split(commands[0])
            initial[-1] = "256"
            commands.insert(0, shlex.join(initial))
        return commands

    def delivered(request, name):
        return _delivered(request, page_digests[name])

    def response(request, number, message):
        nonlocal command, root
        phase = stages["phase"]
        if stages["pages"]:
            cmd = stages["pages"].pop(0)
        elif phase in {"start", "resuming"}:
            if host == "benchmark":
                # Read the actual worker-rendered guard, including its original
                # wake/capture identity. No substitute dispatcher or guard.
                line = [line for line in _input_text(request).splitlines()
                        if " quota should-run " in line and "--codex-context-dir" in line][-1]
                native_guard = shlex.split(line)
                assert native_guard[0] == env["LOOPX_CLI"]
                assignment = [line for line in _input_text(request).splitlines()
                              if line.startswith("LOOPX_TURN=")][-1]
                turn_id = shlex.split(assignment)[0].split("=", 1)[1]
                command = [turn_id if arg == "${LOOPX_TURN:?}" else arg for arg in native_guard]
                command += ["--todo-id", TODO_ID]
                root = Path(command[command.index("--decision-output-root") + 1])
            name = "cold" if phase == "start" else "warm"
            stages["phase"] = name + "_ready"
            if phase == "resuming":
                assert delivered(request, "cold") == read_paths["cold"].read_text().rstrip("\n")
            cmd = invoke(name)
        elif phase in {"cold_ready", "warm_ready"}:
            name = phase.split("_")[0]
            descriptor = json.loads(wire_paths[name].read_text())
            # The public native CLI now shares context-project's paged reader.
            # Keep the independent full-wire oracle separate from its page view.
            wire_file = Path(descriptor["path"]).parent / "delivery.json"
            wires[name] = json.loads(wire_file.read_text())
            assert descriptor["mode"] == wires[name]["mode"] == ("full" if name == "cold" else "delta")
            capture = Path(wires[name]["recovery"]["path"]).parent / "decision.json"
            snapshots.append((capture, capture.read_bytes()))
            read_paths[name] = Path(wires[name]["recovery"]["path"]) if name == "cold" else wire_file
            stages["pages"] = pages(name, read_paths[name])
            stages["phase"] = name + "_read"
            cmd = stages["pages"].pop(0)
        elif phase == "cold_read":
            assert delivered(request, "cold") == read_paths["cold"].read_text().rstrip("\n")
            stages.update(baseline_confirmed=True, phase="warm_ready")
            if resumed:
                stages["phase"] = "resuming"
                stages["cold_readback_sha256"] = hashlib.sha256(delivered(request, "cold").encode()).hexdigest()
                message["content"][0]["text"] = stages["cold_readback_sha256"]
                return message
            cmd = invoke("warm")
        elif phase == "warm_read":
            assert delivered(request, "cold") == read_paths["cold"].read_text().rstrip("\n")
            assert delivered(request, "warm") == read_paths["warm"].read_text().rstrip("\n")
            stages.update(phase="compact", compact_response=number)
            if hook_loss:
                loss_marker.touch()
            cmd = "printf synthetic-work"
        elif phase == "compact":
            stages["phase"] = "recovery_ready"
            message["content"][0]["text"] = "Summary deliberately omitting all packet source bodies."
            return message
        elif phase == "recovery_ready":
            if hook_loss:
                text = _input_text(request)
                assert "LoopX context baseline invalidated." not in text
                # The recovery entry must really be present after compaction;
                # the fixture may not supply a hidden saved capture path.
                prefix = "If continuing a captured LoopX decision"
                permanent = text[text.index(prefix):]
                cmd = permanent.split("with: ", 1)[1].split(". Follow ", 1)[0]
                stages["phase"] = "lookup_read"
                assert delivered(request, "cold") == "" and delivered(request, "warm") == ""
                return tool(cmd, number)
            assert "LoopX context baseline invalidated." in _input_text(request)
            assert wires["warm"]["recovery"]["command"] in _input_text(request)
            assert delivered(request, "cold") == "" and delivered(request, "warm") == ""
            stages["phase"] = "recovered"
            stages["pages"] = pages("recovery", Path(wires["warm"]["recovery"]["path"]))
            cmd = stages["pages"].pop(0)
        elif phase == "lookup_read":
            # Product lookup delivered the first page; use only its public
            # digest-pinned next command, not the private session journal.
            page = None
            for item in reversed(request["input"]):
                output = item.get("output")
                parts = [p.get("text", "") for p in output] if isinstance(output, list) else [output]
                for part in parts:
                    if not isinstance(part, str):
                        continue
                    try:
                        value = json.loads(part.split("\nOutput:\n", 1)[-1])
                        if isinstance(value, dict) and value.get("exit_code") == 0:
                            value = json.loads(value["output"])
                        if isinstance(value, dict) and value.get("schema_version") == "context_capture_page_v1":
                            page = value
                            break
                    except (ValueError, KeyError):
                        continue
                if page:
                    break
            assert page and page["ok"], page
            assert page["sha256"] == wires["warm"]["recovery"]["sha256"]
            page_digests["recovery"] = page["sha256"]
            if not page["eof"]:
                return tool(page["next_command"], number)
            stages["phase"] = "recovered"
            return response(request, number, message)
        else:
            assert phase == "recovered"
            recovered = delivered(request, "recovery")
            assert recovered == Path(wires["warm"]["recovery"]["path"]).read_text().rstrip("\n")
            stages["phase"] = "done"
            return message
        return tool(cmd, number)

    def tool(cmd, number):
        if host == "code_mode":
            pragma = '// @exec: {"max_output_tokens":12000}\n' if native_pragma else ""
            # Exercise the shipped default page size and bounded batching for
            # both output forms. One model round per page can exhaust the
            # unchanged native turn deadline before same-capture recovery ends.
            commands = [cmd]
            while stages["pages"] and len(commands) < native_read_batch:
                commands.append(stages["pages"].pop(0))
            if adaptive:
                code = "".join("{const r=await tools.exec_command(" + json.dumps({
                    "cmd": command, "max_output_tokens": 2400})
                    + ");text(r.exit_code === 0 ? r.output : r);}" for command in commands)
                return {"type": "custom_tool_call", "id": f"ctc_{number}", "call_id": f"call_{number}",
                    "name": "exec", "input": pragma + code}
            return {"type": "custom_tool_call", "id": f"ctc_{number}", "call_id": f"call_{number}",
                "name": "exec", "input": pragma + "".join("text(await tools.exec_command(" + json.dumps({
                    "cmd": command, "max_output_tokens": 2000}) + "));" for command in commands)}
        return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
            "name": "exec_command", "arguments": json.dumps({"cmd": cmd, "max_output_tokens": 2000})}

    def usage(request, number):
        count = (2000000 if host == "benchmark" else 30000) if number == stages["compact_response"] else 1
        return {"input_tokens": count, "output_tokens": 1, "total_tokens": count + 1}

    def observed_response(request, number, message):
        try:
            return response(request, number, message)
        except Exception as error:
            # End the native fixture and report the original assertion,
            # rather than making Codex retry a broken HTTP connection.
            errors.append(error)
            return message

    with local_inference_provider(observed_response, usage) as (url, calls, observed):
        if host != "benchmark":
            _configure_provider(home, url)
            if host == "code_mode":
                config = home / "config.toml"
                config.write_text(config.read_text().replace("[features]\n", "[features]\ncode_mode=true\n"))
                if native_history_tokens is not None:
                    config.write_text(f"tool_output_token_limit={native_history_tokens}\n" + config.read_text())
            if native_configuration:
                config = home / "config.toml"
                config.write_text("developer_instructions=" + json.dumps(native_configuration["developer_instructions"])
                    + "\n" + config.read_text())
            elif hook_loss:
                from loopx.control_plane.turn_driver.codex_context import codex_context_recovery_instructions
                config = home / "config.toml"
                instructions = codex_context_recovery_instructions(str(lifecycle), goal_id=GOAL_ID,
                    agent_id=AGENT_ID, cli_argv=cli)
                config.write_text("developer_instructions=" + json.dumps(instructions) + "\n" + config.read_text())
        # The real CLI talks to its isolated local TS Effect process.
        # Native sandbox networking is disabled by default; allow this
        # synthetic workspace's loopback transport explicitly.
        if host != "benchmark":
            with (home / "config.toml").open("a") as config:
                config.write("[sandbox_workspace_write]\nnetwork_access=true\n")
        if host == "exec":
            result = subprocess.run([binary, "exec", "--dangerously-bypass-hook-trust",
                "--skip-git-repo-check", "--json", "-s", "workspace-write", "-C", str(workspace),
                "Synthetic CLI context-delivery fixture."], env=env, capture_output=True, text=True, timeout=90)
            assert result.returncode == 0, result.stderr
        elif host == "benchmark":
            from benchmark.runtime.worker import run_once
            skills = workspace / "skills"
            skills.mkdir()
            unrelated = project / "unrelated-hook-executed"
            project_hooks = project / ".codex/hooks.json"
            project_hooks.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{
                "type": "command", "command": shlex.join(["touch", str(unrelated)])}]}]}}))
            env.update(LOOPX_CONTEXT_DELIVERY="on", LOOPX_TURN_ENVELOPE=str(int(surface == "turn_envelope")),
                LOOPX_EXECUTION_MODE="heartbeat", LOOPX_ITERATION_CONTEXT="resume",
                LOOPX_TASK_ENTRY="seeded-todo", LOOPX_TASK_STAGE="execute",
                LOOPX_CODEX_HOME=str(home), LOOPX_PROJECT=str(project), LOOPX_SHARED_SKILLS=str(skills),
                LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
                LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID, LOOPX_WAKE_LOG_DIR=str(workspace / "wakes"),
                LOOPX_CLI=str(wrapper if hook_loss else installed_cli or Path(sys.executable).with_name("loopx")), CODEX_BIN=binary,
                MODEL_NAME="fixture", REASONING_EFFORT="high", OPENAI_BASE_URL=url,
                OPENAI_API_KEY="synthetic-loopback-only", LOOPX_CODEX_TURN_TIMEOUT_SEC="90")
            if checkpoint_handoff:
                from contextlib import contextmanager
                from benchmark.runtime import checkpoint_wake
                original_enter = checkpoint_wake.CheckpointWake.__enter__
                original_hold = checkpoint_wake.held_quota_decision

                def enter(wake):
                    value = original_enter(wake)
                    assert value.listener is not None and value.socket_path
                    checkpoint_wakes.append(value)
                    return value

                @contextmanager
                def held(*args, **kwargs):
                    with original_hold(*args, **kwargs) as capture:
                        # The checkpoint owner sees the original admitted
                        # decision before projection; it releases those bytes.
                        raw = capture["path"].read_bytes()
                        assert hashlib.sha256(raw).hexdigest() == capture["sha256"]
                        assert json.loads(raw)["ok"] is True
                        assert json.loads(raw).get("schema_version") != "context_delivery_v1"
                        held_captures.append((capture["path"], raw))
                        yield capture
                        assert capture["path"].read_bytes() == raw

                monkeypatch.setattr(checkpoint_wake.CheckpointWake, "__enter__", enter)
                monkeypatch.setattr(checkpoint_wake, "held_quota_decision", held)
                declaration = workspace / "checkpoint-request.json"
                declaration.write_text(json.dumps({"trigger": "fresh_replan",
                    "destination": str(workspace / "checkpoints"),
                    "components": [{"id": "task", "source_path": str(state)}], "point_metadata": {}}))
                env["LOOPX_CHECKPOINT_REQUEST"] = str(declaration)
            result = run_once(env)
            assert result["ok"] and result["host_invoked"], result
            assert result["context_transport"] == {"available": True}
            assert result["session"]["session_id"]
            if resumed:
                session_id = result["session"]["session_id"]
                # A later worker wake owns a new Turn. Settle the independently
                # checked first readback through the real CLI, rather than
                # reusing a stale guard/Turn from resumed history to obtain an
                # artificially eligible warm packet.
                from test_quota_settlement_cli import _run_cli
                expected = hashlib.sha256(read_paths["cold"].read_text().rstrip("\n").encode()).hexdigest()
                assert stages["cold_readback_sha256"] == expected
                artifact = project / "native-context-readback.txt"
                artifact.write_text(stages["cold_readback_sha256"])
                assert artifact.read_text() == expected
                cold_turn = json.loads(snapshots[0][1])["heartbeat_receipt"]["turn_instance_id"]
                binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                           "--todo-id", TODO_ID, "--turn-instance-id", cold_turn)
                rc, written = _run_cli(registry, runtime, "refresh-state", *binding,
                    "--classification", "native_context_readback_verified", "--delivery-batch-scale", "single_surface",
                    "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
                    "--no-global-sync", "--suppress-external-sinks", cwd=project)
                assert rc == 0, written
                rc, spent = _run_cli(registry, runtime, "quota", "spend-slot", *binding,
                    "--slots", "1", "--source", "heartbeat", "--execute", cwd=project)
                assert rc == 0 and spent["appended"], spent
                result = run_once(env)
                assert result["ok"] and result["host_invoked"], result
                assert result["context_transport"] == {"available": True}
                assert result["session"]["session_id"] == session_id
            assert not unrelated.exists()  # only exact trial-owned hooks received trust
            if checkpoint_handoff:
                assert len(held_captures) == 2
                assert all(path.read_bytes() == raw for path, raw in held_captures)
                assert result["checkpoint"]["captured"] is False  # no replan was requested
        else:
            # This large semantic fixture reads full, delta and recovery pages.
            # Allow their native hook/CLI IO; this is not a latency qualification.
            session_id = _app_server(binary, env, workspace, home, True, sandbox="workspace-write", turn_timeout_sec=60)
            if resumed:
                assert _app_server(binary, env, workspace, home, True, sandbox="workspace-write",
                                   resume=session_id, turn_timeout_sec=60) == session_id
    assert not errors, errors

    assert stages["phase"] == "done"
    assert len(snapshots) == 2  # compaction/readback never invokes a third guard
    captured = (list((workspace / "wakes").glob("*/decisions/*/decision.json"))
                if host == "benchmark" else list(root.glob("*/decision.json")))
    assert len(captured) == 2
    # Compare complete presentation bytes, not two similarly sized first-page descriptors.
    # Whole CLI/model costs remain a separate benchmark measurement.
    assert len(json.dumps(wires["warm"]).encode()) < len(json.dumps(wires["cold"]).encode())
    assert len(calls) == len(observed) and stages["baseline_confirmed"]
    if native_read_batch > 2:
        visible_batches = []
        for request in observed:
            for item in request["input"]:
                if item.get("type") != "custom_tool_call_output" or not isinstance(item.get("output"), list):
                    continue
                pages_in_output = 0
                for part in item["output"]:
                    try:
                        page = json.loads(part.get("text", ""))
                    except ValueError:
                        continue
                    pages_in_output += isinstance(page, dict) and page.get("schema_version") == "context_capture_page_v1"
                visible_batches.append(pages_in_output)
        assert max(visible_batches, default=0) > 2  # complete bytes were independently checked above
    cache_root = home / "loopx-context" if host == "benchmark" else lifecycle
    assert cache_root.is_dir() and cache_root.stat().st_mode & 0o077 == 0
    assert not (home / "context-traces").exists()
    assert not (workspace / "traces").exists()
    projection = Path(wires["warm"]["recovery"]["path"])
    assert hashlib.sha256(projection.read_bytes()).hexdigest() == wires["warm"]["recovery"]["sha256"]
    current = prepare_context_delivery(json.loads(projection.read_bytes()), scope={k: wires["warm"]["target"][k] for k in ("goal_id", "agent_id", "session_id", "context_epoch")},
        capture_id=wires["warm"]["target"]["capture_id"], surface=surface)
    assert restore_context_delivery(wires["warm"], baseline=wires["cold"], current=current)["context"] == current["context"]
    for document in (wires["cold"]["context"], current["context"]):
        context = (document["work_context"] if surface == "turn_envelope" else
                   document["interaction_contract"]["agent_channel"]["work_context"])
        selected = next(source["content"]["todo"] for source in context["sources"]
                        if source["source"] == "selected_todo")
        assert selected["todo_id"] == TODO_ID and selected["text"] == body
    assert all(path.read_bytes() == raw for path, raw in snapshots)
    if installed_cli:
        expected_cli = str(wrapper) if host == "benchmark" and hook_loss else installed_cli
        for _, raw in snapshots:
            # The original guard's settlement routes reveal its actual CLI;
            # installed page reads alone cannot qualify the worker preflight.
            actions = json.loads(raw)["interaction_contract"]["cli_channel"]["next_cli_actions"]
            assert actions and all(shlex.split(action)[0] == expected_cli for action in actions)
    if host == "benchmark" and resumed:
        assert _spend_run_count(runtime) == 1
        from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
        retained = next(todo for todo in read_canonical_todos_if_promoted(
            runtime_root=runtime, goal_id=GOAL_ID)["todos"] if todo["todo_id"] == TODO_ID)
        assert retained["text"] == body and retained["done"] is False
    else:
        assert state.read_bytes() == original_state and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("host,surface,hook_loss", [
    (host, surface, loss) for host in ("code_mode", "benchmark")
    for surface, loss in (("quota_packet", "missing"), ("turn_envelope", "killed"))
])
def test_native_missing_hook_recovers_from_persistent_cli_route(tmp_path, monkeypatch, host, surface, hook_loss):
    test_native_cli_capture_delta_and_compact_recovery(tmp_path, monkeypatch,
        host, surface, False, True, True, hook_loss)


@pytest.mark.parametrize("surface", ["quota_packet", "turn_envelope"])
def test_native_code_mode_pragma_retains_delta_and_recovers(tmp_path, monkeypatch, surface):
    test_native_cli_capture_delta_and_compact_recovery(tmp_path, monkeypatch,
        "code_mode", surface, False, True, True, native_pragma=True)


@pytest.mark.parametrize("surface", ["quota_packet", "turn_envelope"])
def test_native_known_history_budget_keeps_larger_batches_and_recovery(tmp_path, monkeypatch, surface):
    # The unknown loopback model otherwise inherits a byte-based fallback.
    # Match a declared 10k-token history budget only in this separate case;
    # existing low-budget/truncation coverage and host defaults stay intact.
    test_native_cli_capture_delta_and_compact_recovery(tmp_path, monkeypatch,
        "code_mode", surface, False, True, True, "missing", shared_root=True,
        native_pragma=True, native_read_batch=8, native_history_tokens=10000)


@pytest.mark.parametrize("surface", ["quota_packet", "turn_envelope"])
def test_native_shared_configuration_resume_and_lost_hook(tmp_path, monkeypatch, surface):
    test_native_cli_capture_delta_and_compact_recovery(tmp_path, monkeypatch,
        "code_mode", surface, False, True, True, "missing", shared_root=True)


@pytest.mark.parametrize("surface", ["quota_packet", "turn_envelope"])
def test_native_benchmark_context_keeps_checkpoint_handoff(tmp_path, monkeypatch, surface):
    test_native_cli_capture_delta_and_compact_recovery(tmp_path, monkeypatch,
        "benchmark", surface, False, True, True, "missing", checkpoint_handoff=True)


@pytest.mark.parametrize("private_handoff", [True], indirect=True)
@pytest.mark.parametrize("surface", ["quota_packet", "turn_envelope"])
def test_native_capture_release_preserves_delta_and_recovery(tmp_path, monkeypatch, private_handoff, surface):  # noqa: F811 -- fixture injection
    test_native_cli_capture_delta_and_compact_recovery(tmp_path, monkeypatch,
        "code_mode", surface, False, True, True, handoff_socket=private_handoff)
