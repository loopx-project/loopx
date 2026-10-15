"""Real LoopX Chat service on a disposable runtime whose CLI runtimes are absent until the scenario installs them.

No registry, Goal, model or credential exists here; the service probes the
configured executables live, exactly as an installed LoopX does.
"""
import json
import os
import signal
import socket
import sys
from pathlib import Path

from loopx.chat_server import default_chat_assets_dir, serve_chat

root = Path(sys.argv[1]).resolve()
bins = root / "bin"
bins.mkdir(parents=True, exist_ok=True)
(root / "project").mkdir(exist_ok=True)
for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(variable, None)
os.environ["LOOPX_USAGE_PING"] = "0"
os.environ["DO_NOT_TRACK"] = "1"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
print(json.dumps({"port": port, "bins": str(bins)}), flush=True)
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
