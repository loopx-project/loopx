"""Check a receiver's native login route without starting a model Turn.

This startup-only transport check owns neither quota policy nor recovery of an
already dispatched request. A failed observer leaves the original worker,
session and scheduler records untouched.
"""
from __future__ import annotations

import json
import os
import time
import shutil
import tempfile
from pathlib import Path

from loopx.control_plane.turn_driver.codex_stdio import native_checkpoint_reader


def check_native_receiver(env: dict[str, str]) -> None:
    # Match the account owner's existing observation budget. This limits only
    # this disposable observer, never the solver or an existing native Turn.
    deadline = time.monotonic() + 25
    # Native app-server can install bundled skills even for account-only reads.
    # Probe the selected login/config/workspace in a disposable inspection home;
    # never create those files in the receiver before its fixed-input owner runs.
    # This checks account transport, not a future model request or session store.
    home = Path(env["LOOPX_CODEX_HOME"])
    with tempfile.TemporaryDirectory(prefix="loopx-native-inspection-") as temporary:
        observer = Path(temporary)
        for name in ("auth.json", "config.toml"):
            source = home / name
            if source.is_file():
                shutil.copyfile(source, observer / name)
                (observer / name).chmod(0o600)
        native_env = {**env, "CODEX_HOME": str(observer)}
        with native_checkpoint_reader(native_env, timeout=25) as reader:
            def request(method, params):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Native receiver observation expired")
                reader.response_timeout_sec = remaining
                return reader.request(method, params)

            # Exercise the selected native binary with the original workspace
            # and copied login/config, rather than trusting login-file presence.
            account = request("account/read", {"refreshToken": False}).get("account")
            if not isinstance(account, dict) or account.get("type") != "chatgpt":
                raise ValueError("Native receiver has no ChatGPT account")
            usage = request("account/rateLimits/read", {})
            if not isinstance(usage, dict) or not usage:
                raise ValueError("Native receiver usage route is unavailable")


def main() -> int:
    try:
        check_native_receiver(dict(os.environ))
    except Exception:
        # Native transport errors can contain credentials, account identifiers
        # or private routes. Do not copy their messages into runner output.
        print(json.dumps({"ok": False, "code": "native_receiver_unavailable",
                          "inference_invoked": False}), flush=True)
        return 1
    print(json.dumps({"ok": True, "inference_invoked": False}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
