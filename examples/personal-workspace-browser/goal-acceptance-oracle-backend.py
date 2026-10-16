"""Disposable real SQLite acceptance owner for the packaged browser journey."""

import hashlib
import json
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_loopx_mode import TOOL
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.testing.canary_harness import write_fixture_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "control_plane"))
from canonical_authority_fixture import initialize_canonical_authority

root = Path(sys.argv[1])
project = root / "project"
project.mkdir()
state = project / "state.md"
state.write_text(
    "---\nstatus: active\n---\n# Synthetic acceptance workspace\n## Agent Todo\n"
)
registry, runtime = root / "registry.json", root / "runtime"
write_fixture_registry(
    project=project,
    registry_path=registry,
    runtime_root=runtime,
    goal_id="multi-agent-projection",
    domain="acceptance",
    adapter_kind="generic_project_goal_v0",
    state_file=str(state),
    registered_agents=["coordinator", "agent-a"],
)
registered = json.loads(registry.read_text())
registered["goals"][0]["spawn_policy"] = {
    "mode": "multi_subagent", "allowed": True, "max_children": 1,
    "execution_config": ".loopx/config/delegations.json",
}
registry.write_text(json.dumps(registered))
config = project / ".loopx/config/delegations.json"
config.parent.mkdir(parents=True)
config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": [{
    "id": "export", "agent_id": "agent-a", "todo_id": "todo_work", "requesters": ["coordinator"],
    "workspace": str(project), "host_args": ["--host", "dsh"], "timeout_seconds": 60,
    "output_refs": ["result.txt"],
}]}))
todo = {
    "schema_version": "todo_item_v0",
    "todo_id": "todo_work",
    "role": "agent",
    "text": "Produce the expected export",
    "status": "open",
    "done": False,
    "task_class": "advancement_task",
    "action_kind": "implement",
    "index": 1,
    "source_section": "Agent Todo",
    "archive_state": "active",
    "claimed_by": "agent-a",
}
initialize_canonical_authority(
    runtime,
    "multi-agent-projection",
    build_todo_runtime_shadow_projection(
        goal_id="multi-agent-projection", todos=[todo], handoff_mode="soft_claim"
    ),
    state_path=state,
    provider="sqlite",
)
checker = project / "check.py"
checker.write_text(
    "from pathlib import Path\nassert Path('result.txt').read_text() == Path('expected.txt').read_text()\n"
)
(project / "expected.txt").write_text("usable export")
(project / "result.txt").write_text("usable export")
document = {
    "scope": {"kind": "selected_work", "todo_ids": ["todo_work"]},
    "objective": "Validate the exact export version",
    "non_goals": [
        "This synthetic fixture does not run a model or certify live adoption"
    ],
    "criteria": [
        {
            "id": "export",
            "description": "The export matches the expected input",
            "validation_argv": ["python3", "check.py"],
            "validation_timeout_seconds": 5,
            "validation_files": [
                {
                    "path": "check.py",
                    "sha256": hashlib.sha256(checker.read_bytes()).hexdigest(),
                }
            ],
            "oracle": {
                "kind": "owner_pinned_oracle",
                "source_id": "export-oracle",
                "input_refs": ["expected.txt"],
                "output_refs": ["result.txt"],
            },
        }
    ],
    "bindings": [{"todo_id": "todo_work", "criterion_ids": ["export"]}],
}
(root / "owner-contract.json").write_text(json.dumps(document))
# Exercise the admitted native tool callback, not a pre-seeded CLI proposal.
# Native transport/model interpretation remains scripted; no Host is launched.
store = ChatSessionStore(runtime)
controller = ChatRuntimeController(store=store, codex_bin="unused", registry_path=registry)
session = store.create_session(goal_id="multi-agent-projection", agent_id="codex",
    channel_id="goal.multi-agent-projection", upstream_thread_id="fixture", upstream_mode="chat",
    adapter_kind="codex_app_server")
sid, turn = session["session_id"], "admitted-preparation-turn"
store.update_session(sid, active_turn_id=turn, loopx_mode={"enabled": True, "paused": False,
    "settings": {"agent_id": "coordinator", "token_budget": 10000}})
adapter = SimpleNamespace(goal_driver=SimpleNamespace(stopped=Event()), session=SimpleNamespace())
lock = controller.loopx_mode.prepare(sid, turn, adapter, lambda *_: {}, lambda *_: None)
for operation in ("older-export-plan", "prepare-export-check"):
    prepared = adapter.session.read_tool_handler(TOOL["name"], {
        "action": "prepare_check", "operation_id": operation, "document": document})
    if not prepared.get("ok"):
        raise RuntimeError(f"Conversation preparation failed: {prepared}")
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path, server.runtime_root = registry, runtime
server.runtime_root_override, server.verbose = str(runtime), False
server.selected_goal_id, server.scan_roots, server.limit = None, [], 20
server.goal_subagent_configuration_enabled = False
server.action_store = ChatActionStore(runtime / "chat" / "actions")
server.action_service = ChatActionService(store=server.action_store, registry_path=registry)
print(
    json.dumps(
        {"port": server.server_port, "registry": str(registry), "runtime": str(runtime),
         "prepared": prepared}
    ),
    flush=True,
)
try:
    server.serve_forever()
finally:
    server.server_close()
    lock.__exit__(None, None, None)
    controller.close()
