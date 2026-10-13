"""Disposable real action/registry backend; only model dispatch is synthetic."""
import json
import os
import tempfile
from pathlib import Path
import runpy
import signal
import sys

from loopx.chat_action_store import ChatActionStore
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.chat_actions import ChatActionService, goal_workspace_ref
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.effect_runtime import restart_effect_runtime


root = Path(sys.argv[1]).resolve()
tempfile.tempdir = str(root)
for variable in ("TMPDIR", "TEMP", "TMP"):
    os.environ[variable] = str(root)
fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "loopx-chat-actions-smoke.py"))
registry, _ = fixture["write_registry_fixture"](root / "first")
other_registry, _ = fixture["write_registry_fixture"](root / "second")
data = json.loads(registry.read_text())
other = json.loads(other_registry.read_text())["goals"][0]
data["goals"][0]["id"] = "product-release"
other["id"] = "research-monitor"
data["goals"].append(other)
registry.write_text(json.dumps(data), encoding="utf-8")
runtime = Path(data["common_runtime_root"])
store, sessions = ChatActionStore(runtime / "chat/actions"), ChatSessionStore(runtime)
controller = fixture["FakeRuntimeController"](sessions)
# One Dashboard-configured workspace that no Goal uses yet.
configured = root / "docs-site"
configured.mkdir()
controller.project_contexts = ChatProjectContexts([configured])
service = ChatActionService(store=store, registry_path=registry,
    chat_store=sessions, runtime_controller=controller, workspace_roots=[configured])
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path, server.runtime_root = registry, runtime
server.action_store, server.action_service = store, service
server.chat_store, server.runtime_controller = sessions, controller
server.verbose = False
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print(json.dumps({"port": server.server_port, "registry": str(registry),
    "first": data["goals"][0]["repo"], "second": other["repo"],
    "configured": str(configured.resolve()),
    "configured_ref": goal_workspace_ref(configured.resolve())}), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    restart_effect_runtime()
