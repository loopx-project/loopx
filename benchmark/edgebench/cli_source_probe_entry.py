"""Isolated read program; imports only the source's explicitly copied kernel."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sys


def main():
    declaration = json.loads(Path(sys.argv[1]).read_bytes())
    root = Path(declaration["kernel_root"])
    sys.path[:] = [str(root), *(p for p in sys.path if p not in ("", "."))]
    import loopx
    from loopx.control_plane.quota.settlement import _read_heartbeat_settlement_payload
    from loopx.control_plane.turn_driver.codex_sessions import load_codex_cli_session
    from loopx.control_plane.projects.registry_codec import load_registry
    from loopx.control_plane.goals.activation import goal_is_stopped
    from loopx.hosts.codex_checkpoint import native_session_checkpoint, _complete_history
    from loopx.control_plane.turn_driver.codex_stdio import native_checkpoint_reader

    if Path(loopx.__file__).resolve() != root / "loopx/__init__.py":
        raise ValueError("Source observer imported a different kernel")
    goal = next(g for g in load_registry(Path(declaration["registry"]))["goals"]
                if g["id"] == declaration["goal_id"])
    if goal_is_stopped(goal):
        print(json.dumps({"probe_status": "stopped", "source_stopped": True}))
        return
    runtime = Path(declaration["runtime_root"])
    binding = load_codex_cli_session(runtime, lineage={key: declaration[key] for key in ("goal_id", "agent_id")},
                                    session_scope="agent")
    if not binding or binding["session_id"] != declaration["session_id"]:
        raise ValueError("Source native session binding differs")
    scheduler = json.loads(Path(declaration["scheduler_state"]).read_bytes())
    scheduler = {"host_attempt": scheduler.get("host_attempt"),
                 "last_wake_status": scheduler.get("last_wake_status"),
                 "last_wake_rc": scheduler.get("last_wake_rc"),
                 "host_pending": json.loads(Path(declaration["scheduler_state"] + ".host-attempt").read_bytes())["pending"]}
    settlement = _read_heartbeat_settlement_payload(runtime, goal_id=declaration["goal_id"], fields={
        "agent_id": declaration["agent_id"], "todo_id": declaration["todo_id"],
        "turn_instance_id": declaration["turn_instance_id"], "replan_obligation_id": None,
        "infer_turn_instance_id": False, "allow_unbound_binding": False},
        registry_path=Path(declaration["registry"]), goal_ref=None)
    env = {**os.environ, "CODEX_BIN": declaration["codex"], "CODEX_HOME": declaration["native_home"],
           "LOOPX_PROJECT": declaration["workspace"]}
    with native_checkpoint_reader(env, timeout=30) as reader:
        native = native_session_checkpoint(env, declaration["session_id"], timeout=30, transport=reader)
        confirmed = _complete_history(reader.request, declaration["session_id"])
        encoded = json.dumps(confirmed, sort_keys=True, separators=(",", ":")).encode()
        if (hashlib.sha256(encoded).hexdigest() != native["metadata"]["visible_history_sha256"] or
                any(item.get("status") in {"inProgress", "running", "pending"}
                    for turn in confirmed for item in turn.get("items", []))):
            raise ValueError("Source native history changed or retains pending effects")
        native["metadata"]["quiescent_readback"] = True
    print(json.dumps({"probe_status": "ready", "scheduler": scheduler, "settlement": settlement,
                      "native": native["metadata"], "observer_kernel": loopx.__file__}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # The existing owner refused or its transport was unavailable. Keep
        # raw native/configuration output inside the observer namespace.
        print(json.dumps({"probe_status": "unavailable", "observer_error_kind": type(error).__name__}))
