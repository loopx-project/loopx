"""Real disposable Todo preview/apply and status; no model or active Goal access."""
import json
import os
from pathlib import Path
import runpy
import signal
import sys
import tempfile

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.effect_runtime import restart_effect_runtime

root = Path(sys.argv[1]).resolve()
tempfile.tempdir = str(root)
for variable in ("TMPDIR", "TEMP", "TMP"):
    os.environ[variable] = str(root)
fixtures = runpy.run_path(str(Path(__file__).resolve().parents[1] / "loopx-chat-actions-smoke.py"))
registry, state = fixtures["write_registry_fixture"](root)
data = json.loads(registry.read_text())
data["goals"][0].update(id="progress-projection", display_name="Synthetic workspace")
data["goals"][0]["coordination"]["handoff_mode"] = "soft_claim"
registry.write_text(json.dumps(data), encoding="utf-8")
runtime = Path(data["common_runtime_root"])
canonical_fixtures = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "tests/control_plane/canonical_authority_fixture.py")
)
canonical_fixtures["initialize_canonical_authority"](
    runtime,
    "progress-projection",
    build_todo_runtime_shadow_projection(
        goal_id="progress-projection",
        handoff_mode="soft_claim",
        todos=[],
    ),
    state_path=state,
    provider="file",
)
server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
server.registry_path, server.runtime_root, server.runtime_root_override = registry, runtime, str(runtime)
server.selected_goal_id, server.scan_roots, server.limit = "progress-projection", [], 20
server.action_store = ChatActionStore(root / "actions")
server.action_service = ChatActionService(store=server.action_store, registry_path=registry, runtime_root_override=str(runtime))
server.verbose = False
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print(json.dumps({"port": server.server_port, "registry": str(registry)}), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    restart_effect_runtime()
