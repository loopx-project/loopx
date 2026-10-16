"""Real LoopX Chat service on a fresh runtime: no registry, Goal, model or credential exists.

The scan root is an empty project directory, exactly the state an installed
LoopX opens for the first time. The only Agent executable is a stub that
exits at once, so the service sees an installed Codex CLI without any model
ever running; the machine's real CLIs and API keys are never consulted.
"""
import json
import os
import signal
import socket
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
# The implicit default runtime root lives under HOME; point it inside this
# disposable root before LoopX is imported so a creation that ignored the
# service's selected root would land somewhere the scenario can observe, and
# never in the tester's real state.
home = root / "home"
home.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(home)

from loopx.chat_server import default_chat_assets_dir, serve_chat  # noqa: E402

(root / "project").mkdir(parents=True, exist_ok=True)
bins = root / "bin"
bins.mkdir(exist_ok=True)
(bins / "codex").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
(bins / "codex").chmod(0o755)
for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(variable, None)
os.environ["LOOPX_USAGE_PING"] = "0"
os.environ["DO_NOT_TRACK"] = "1"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
print(json.dumps({
    "port": port,
    "project": str(root / "project"),
    "runtime": str(root / "runtime"),
    "default_runtime": str(home / ".loopx"),
}), flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
serve_chat(
    registry_path=root / "project" / ".loopx" / "registry.json",
    runtime_root_override=str(root / "runtime"),
    scan_roots=[root / "project"],
    host="127.0.0.1",
    port=port,
    codex_bin=str(bins / "codex"),
    claude_bin=str(bins / "claude"),
    kiro_cli_bin=str(bins / "kiro-cli"),
    assets_dir=default_chat_assets_dir(),
    open_browser=False,
)
