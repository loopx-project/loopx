from __future__ import annotations

import os
import json
import sys
from pathlib import Path


def assert_host_task_context(model_input, source):
    """Check source parity outside the executor-owned presentation boundary.

    Every task field and unknown addition must survive, including backlog,
    scopes, reads and replan authoring. The original commitment is carried
    separately; a task view cannot masquerade as a complete signed source.
    """
    assert model_input["schema_version"] == "loopx_turn_model_context_v0"
    assert model_input["source_schema_version"] == "loopx_turn_host_request_v1"
    assert not {"work_contract", "turn_envelope", "turn_authority"}.intersection(model_input)
    context = model_input["task_context"]
    outer_fields = {"schema_version", "contract_binding", "projection_envelope", "scheduler_hint",
                    "automation_cadence_readback", "automation_liveness", "scheduler_execution_context"}
    expected = {field: value for field, value in source.items() if field not in outer_fields}
    hints = expected.get("todo_write_hint")
    if isinstance(hints, dict):
        commands = {"rule", "section", "agent_todo_command_template", "user_action_command_template",
                    "user_gate_command_template"}
        retained = {k: v for k, v in hints.items() if k not in commands or not isinstance(v, str)}
        if retained:
            expected["todo_write_hint"] = retained
        elif hints:
            del expected["todo_write_hint"]
    heartbeat = expected.get("heartbeat_recommendation")
    if (source.get("normal_delivery_allowed") is True and isinstance(heartbeat, dict)
            and heartbeat.get("recommended_mode") == "steering_audit_then_one_step"
            and isinstance(heartbeat.get("spend_policy"), str)):
        expected["heartbeat_recommendation"] = {k: v for k, v in heartbeat.items() if k != "spend_policy"}
    assert set(context) == set(expected), {
        "unexpected": sorted(context.keys() - expected.keys()),
        "missing": sorted(expected.keys() - context.keys()),
    }
    for field in set(expected) - {"interaction_contract"}:
        assert json.dumps(context[field], sort_keys=True, ensure_ascii=False) == json.dumps(
            expected[field], sort_keys=True, ensure_ascii=False), field
    interaction = source["interaction_contract"]
    assert context["interaction_contract"] == {k: v for k, v in interaction.items() if k != "cli_channel"}
    if "source_decision_hash" in model_input:
        assert model_input["source_decision_hash"] == source["contract_binding"]["content_hash"]
    return context


def write_python_host_fixture(path: Path, source: str) -> Path:
    """Launch the same fixture through the real Host supervisor on each OS."""
    if os.name != "nt":
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)
        return path

    # Windows cannot execute a POSIX shebang. Use a native console launcher,
    # keeping argv, pipes, exit codes and supervision on the real process path.
    from distlib.scripts import ScriptMaker

    script = path.with_suffix(".py")
    script.write_text(source, encoding="utf-8")
    maker = ScriptMaker(str(path.parent), str(path.parent))
    maker.executable = sys.executable
    maker.force = True
    maker.variants = {""}
    maker.make(script.name)
    return path.with_suffix(".exe")


COUNTER_PROCESS_SOURCE = """
import os
import signal
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
pid_path = Path(sys.argv[2])
interval_seconds = float(sys.argv[3])
pause_path = Path(sys.argv[4]) if len(sys.argv) == 5 else None

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid_path.write_text(str(os.getpid()), encoding="utf-8")
staged = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
counter = 0
while True:
    staged.write_text(str(counter), encoding="utf-8")
    if pause_path is not None:
        pause_path.touch()
        signal.pause()
    os.replace(staged, marker)
    counter += 1
    time.sleep(interval_seconds)
"""
