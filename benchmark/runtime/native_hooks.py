"""Stage and trust only the benchmark's generated native Codex hooks."""

from __future__ import annotations

import json
import shlex
from functools import partial
from pathlib import Path


OFFICIAL_STOP_HOOK_REASON = "Do not stop. Continue working on the implementation."


def prepare_official_stop_hook(env: dict[str, str], home: Path, *, deadline: float) -> list[str]:
    # Put the entire handler in the declaration so native trust covers the
    # command body as well as the path to the hook declaration.
    response = shlex.quote(json.dumps({"decision": "block", "reason": OFFICIAL_STOP_HOOK_REASON}))
    command = rf"cat >/dev/null && printf '%s\n' {response}"
    return prepare_native_hooks(env, home, {
        "Stop": [{"hooks": [{"type": "command", "command": command, "timeout": 30}]}],
    }, purpose="official-stop", deadline=deadline)


def prepare_native_hooks(env: dict[str, str], home: Path, configuration: dict, *,
                         purpose: str, deadline: float | None = None) -> list[str]:
    from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport

    content = json.dumps({"hooks": configuration})
    path = home / "hooks.json"
    if path.is_symlink() or (path.exists() and path.read_text() != content):
        raise ValueError(f"native {purpose} hooks changed within a trial; use a new trial")
    if not path.exists():
        path.write_text(content)
        path.chmod(0o600)
    # The native host owns keys and hashes. Discovery launches no model, and
    # discovery-only plugin overrides leave the solver configuration intact.
    with (home / f"{purpose}-hooks-stderr.log").open("a") as stderr:
        with StdioNativeGoalTransport.spawn(
            [env["CODEX_BIN"], "-c", "features.plugins=false",
             "-c", "features.remote_plugin=false", "-c", "features.plugin_sharing=false",
             "app-server", "--listen", "stdio://"],
            cwd=env["LOOPX_PROJECT"], env=env, stderr=stderr, response_timeout_sec=10,
        ) as native:
            request = native.request if deadline is None else partial(native.request, deadline=deadline)
            request("initialize", {"clientInfo": {"name": "loopx_context_transport", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
            native.notify("initialized", {})
            listed = request("hooks/list", {"cwds": [env["LOOPX_PROJECT"]]})
    hooks = [hook for entry in listed["data"] for hook in entry["hooks"]
             if hook.get("sourcePath") == str(path.resolve())]
    # Discovery can reorder handlers. Counts alone cannot rule out
    # duplicates, disabled handlers or substitutions.
    expected = [{"eventName": event[0].lower() + event[1:],
                 "handlerType": handler["type"], "command": handler["command"],
                 "matcher": group.get("matcher"), "timeoutSec": handler.get("timeout"),
                 "async": handler.get("async", False)}
                for event, groups in configuration.items()
                for group in groups for handler in group["hooks"]]
    mismatch = f"native host did not discover the exact trial {purpose} hooks"
    if not expected or len(hooks) != len(expected) or path.read_text() != content:
        raise ValueError(mismatch)
    entries, keys = [], set()
    for hook in hooks:
        actual = {field: hook.get(field) for field in expected[0]}
        if hook.get("enabled") is not True or type(hook.get("async")) is not bool or actual not in expected:
            raise ValueError(mismatch)
        expected.remove(actual)
        key, digest = hook.get("key"), hook.get("currentHash")
        if not isinstance(key, str) or not key or key in keys or not isinstance(digest, str) or not digest:
            raise ValueError(f"native host returned no unique {purpose}-hook trust identity")
        keys.add(key)
        entries.append(f"{json.dumps(key)} = {{ trusted_hash = {json.dumps(digest)} }}")
    # Native keys contain dots and paths. Pass the whole table with literal
    # keys instead of nesting keys via CLI dot notation.
    return ["-c", "hooks.state={" + ",".join(entries) + "}"]
