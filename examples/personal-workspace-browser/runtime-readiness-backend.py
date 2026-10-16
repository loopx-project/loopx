"""Real LoopX Chat service on a disposable runtime whose CLI runtimes are absent until the scenario installs them.

The optional Goal is a disposable fixture; no model or credential exists here.
The service probes only the explicitly configured temporary executables.
"""
import json
import os
import runpy
import signal
import socket
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
home = root / "home"
home.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(home)
tempfile.tempdir = str(root)
for variable in ("TMPDIR", "TEMP", "TMP"):
    os.environ[variable] = str(root)
from loopx.chat_server import default_chat_assets_dir, serve_chat  # noqa: E402
from loopx.control_plane.effect_runtime import restart_effect_runtime  # noqa: E402

bins = root / "bin"
bins.mkdir(parents=True, exist_ok=True)
(root / "project").mkdir(exist_ok=True)
for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(variable, None)
os.environ["LOOPX_USAGE_PING"] = "0"
os.environ["DO_NOT_TRACK"] = "1"
if "--with-goal" in sys.argv[2:]:
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "loopx-chat-actions-smoke.py"))
    fixture["write_registry_fixture"](root)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
print(json.dumps({"port": port, "bins": str(bins)}), flush=True)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
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
finally:
    restart_effect_runtime()
