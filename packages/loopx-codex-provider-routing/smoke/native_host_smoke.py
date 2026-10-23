"""Offline smoke for native App shutdown during an exhausted-account handoff."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))

native_host = importlib.import_module("loopx_codex_provider_routing.native_host")

EXE = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"


class Apps:
    def __init__(self):
        self.pid = 4242

    def executable(self, _bundle):
        return EXE

    def app_bundle(self, _spec, _role, _home):
        return Path("/Applications/ChatGPT.app")

    def running(self, _exe, _spec):
        return self.pid


def adapter():
    value = native_host.NativeHost.__new__(native_host.NativeHost)
    value.apps = Apps()
    value.spec = {}
    value.home = Path("/tmp/native-host-smoke")
    value.expected = "revision"
    value.not_after = None
    value.revision = lambda: "revision"
    return value


def close_with(launch_time, *, force, refuse=False):
    calls = []
    host = adapter()

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs.get("input", "")))
        if kwargs["input"] == native_host.FORCE_QUIT or (kwargs["input"] == native_host.QUIT and not refuse):
            host.apps.pid = None

        class Result:
            stdout = json.dumps({"launch_time": launch_time, "accepted": not refuse})
            returncode = 0

        return Result()

    original = native_host.subprocess.run
    native_host.subprocess.run = fake_run
    try:
        closed = host.close_client("revision", force=force)
    finally:
        native_host.subprocess.run = original
    return closed, calls


# An App started outside Launch Services reports a nil launchDate. The force
# path must still terminate it instead of failing the handoff as changed.
closed, calls = close_with(None, force=True, refuse=True)
assert closed is True
assert len(calls) == 3, calls
assert "launch_time" in calls[0][1] and "terminate" not in calls[0][1], calls[0]
assert "app.terminate" in calls[1][1] and "forceTerminate" not in calls[1][1], calls[1]
assert "forceTerminate" in calls[2][1], calls[2]
assert calls[2][0][-1] == "unknown", calls[2][0]

# A registered App keeps the launch-time fence.
closed, calls = close_with(123.0, force=True, refuse=True)
assert closed is True
assert len(calls) == 3, calls
assert "forceTerminate" in calls[2][1], calls[2]
assert calls[2][0][-1] == "123.0", calls[2][0]

# Force permission does not bypass a successful normal quit.
closed, calls = close_with(123.0, force=True)
assert closed is True
assert len(calls) == 2, calls
assert "app.terminate" in calls[1][1] and "forceTerminate" not in calls[1][1], calls[1]

# The default path still asks the App to quit normally.
closed, calls = close_with(None, force=False)
assert closed is True
assert len(calls) == 1, calls
assert "terminate" in calls[0][1] and "forceTerminate" not in calls[0][1], calls[0]

print("native host force handoff smoke passed")
