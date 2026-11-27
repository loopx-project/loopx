"""Stage and trust only the benchmark's generated native Codex hooks."""

from __future__ import annotations

import inspect
import json
import os
import shlex
import stat
import subprocess
import time
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from benchmark.runtime.codex import OFFICIAL_STOP_HOOK_MODES


OFFICIAL_STOP_HOOK_REASON = "Do not stop. Continue working on the implementation."
BASELINE_TODO_DIR_NAME = "baseline-todo"
BASELINE_TODO_FILENAME = "TODO.md"
BASELINE_TODO_TEMPLATE = (
    "# Trial Task Checklist\n\n"
    "- [ ] Understand the task requirements and inspect relevant files\n"
    "- [ ] List remaining implementation steps and edge cases below\n"
    "- [ ] Implement the complete solution without leaving workspace artifacts\n"
    "- [ ] Run relevant tests and checks to verify no regressions\n"
    "- [ ] Confirm every checklist item is marked [x] before stopping\n"
)


def baseline_todo_path(home: Path) -> Path:
    """Return the trial-local counterfactual baseline checklist path outside workspace."""
    return home / BASELINE_TODO_DIR_NAME / BASELINE_TODO_FILENAME


def prepare_baseline_todo_checklist(home: Path) -> Path:
    """Create a physical trial-home todo checklist without touching workspace files."""
    if home.is_symlink():
        raise ValueError("baseline checklist requires a physical trial home")
    todo_dir = home / BASELINE_TODO_DIR_NAME
    if todo_dir.is_symlink():
        raise ValueError("baseline checklist requires a physical trial directory")
    todo_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = todo_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("baseline checklist requires a private, physical trial directory")
    checklist = todo_dir / BASELINE_TODO_FILENAME
    if checklist.is_symlink():
        raise ValueError("baseline checklist must be a physical file")
    if not checklist.exists():
        try:
            with os.fdopen(
                os.open(checklist, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                "w",
                encoding="utf-8",
            ) as stream:
                stream.write(BASELINE_TODO_TEMPLATE)
        except FileExistsError:
            pass
    elif not checklist.is_file():
        raise ValueError("baseline checklist must be a regular file")
    return checklist.resolve()


def baseline_todo_instructions(checklist_path: Path) -> str:
    """Deterministic prompt guidance for the opt-in strong stop-hook baseline."""
    return (
        f"\n\nTrial checklist file (outside the workspace repo): `{checklist_path}`\n"
        "Maintain your task breakdown, remaining edge cases, and test/regression "
        "verification steps in this trial-local file using `- [ ]` and `- [x]` items. "
        "Do not create todo or checklist files inside the workspace repository. "
        "Run tests/checks to verify there are no regressions and complete every "
        "checklist item before concluding.\n"
    )


def _todo_checklist_stop_hook_command(checklist_path: Path) -> str:
    quoted_path = shlex.quote(str(checklist_path))
    missing_response = shlex.quote(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    f"Trial checklist is missing or invalid at {checklist_path}. "
                    "Restore and maintain this trial-local checklist outside the "
                    "workspace repository, verify tests/regressions pass, and "
                    "mark every item [x] before stopping."
                ),
            }
        )
    )
    awk_script = shlex.quote(
        r"""
function esc(s,    i, c, o, lim) {
  gsub(/[\001-\037\177]/, " ", s);
  o = "";
  lim = 160;
  for (i = 1; i <= length(s) && i <= lim; i++) {
    c = substr(s, i, 1);
    if (c == "\\") o = o "\\\\";
    else if (c == "\"") o = o "\\\"";
    else o = o c;
    if (i == lim && lim < 163 && substr(s, i + 1, 1) ~ /[\200-\277]/) lim++;
  }
  return o;
}
BEGIN { open_c = 0; done_c = 0; preview = ""; }
/^[[:space:]]*[-*][[:space:]]*\[[[:space:]]\]/ {
  open_c++;
  if (open_c <= 3) {
    t = $0;
    sub(/^[[:space:]]*[-*][[:space:]]*\[[[:space:]]\][[:space:]]*/, "", t);
    preview = (preview ? preview "; " : "") esc(t);
  }
}
/^[[:space:]]*[-*][[:space:]]*\[[xX]\]/ { done_c++; }
END {
  if (open_c == 0 && done_c > 0) {
    print "{}";
    exit 0;
  }
  msg = "Continue working: trial checklist (" esc(ARGV[1]) ") has " open_c " unchecked and " done_c " checked item(s).";
  if (open_c == 0 && done_c == 0) {
    msg = msg " Track remaining work, edge cases, and test checks with - [ ] items in the checklist.";
  } else if (preview != "") {
    msg = msg " Open items: " preview ".";
  }
  msg = msg " Keep todo tracking outside the workspace repo, run verification tests/checks for regressions, and mark every item [x] before stopping.";
  printf "{\"decision\":\"block\",\"reason\":\"%s\"}\n", msg;
}
""".strip()
    )
    return (
        f"cat >/dev/null; p={quoted_path}; "
        f"if [ -L \"$p\" ] || [ ! -f \"$p\" ]; then printf '%s\\n' {missing_response}; "
        f"else LC_ALL=C awk {awk_script} \"$p\"; fi"
    )


def prepare_official_stop_hook(
    env: dict[str, str],
    home: Path,
    *,
    deadline: float | None = None,
    mode: str | None = None,
) -> list[str]:
    # Put the entire handler in the declaration so native trust covers the
    # command body as well as the path to the hook declaration.
    if mode is not None and mode not in OFFICIAL_STOP_HOOK_MODES:
        raise ValueError(f"official_stop_hook_mode supports only {OFFICIAL_STOP_HOOK_MODES}")
    if mode == "todo_checklist":
        checklist = prepare_baseline_todo_checklist(home)
        command = _todo_checklist_stop_hook_command(checklist)
    else:
        response = shlex.quote(json.dumps({"decision": "block", "reason": OFFICIAL_STOP_HOOK_REASON}))
        command = rf"cat >/dev/null && printf '%s\n' {response}"
    return prepare_native_hooks(env, home, {
        "Stop": [{"hooks": [{"type": "command", "command": command, "timeout": 30}]}],
    }, purpose="official-stop", deadline=deadline)


def prepare_native_hooks(env: dict[str, str], home: Path, configuration: dict, *,
                         purpose: str, deadline: float | None = None) -> list[str]:
    from loopx.control_plane.turn_driver.codex_stdio import NativeGoalProtocolError, StdioNativeGoalTransport

    content = json.dumps({"hooks": configuration})
    path = home / "hooks.json"
    if path.is_symlink() or (path.exists() and path.read_text() != content):
        raise ValueError(f"native {purpose} hooks changed within a trial; use a new trial")
    if not path.exists():
        path.write_text(content)
        path.chmod(0o600)
    command = [
        env["CODEX_BIN"], "-c", "features.plugins=false",
        "-c", "features.remote_plugin=false", "-c", "features.plugin_sharing=false",
        "app-server", "--listen", "stdio://",
    ]
    if deadline is not None and deadline <= time.monotonic():
        raise subprocess.TimeoutExpired(command, 0)
    # The native host owns keys and hashes. Discovery launches no model, and
    # discovery-only plugin overrides leave the solver configuration intact.
    with (home / f"{purpose}-hooks-stderr.log").open("a") as stderr:
        with StdioNativeGoalTransport.spawn(
            command, cwd=env["LOOPX_PROJECT"], env=env, stderr=stderr, response_timeout_sec=10,
        ) as native:
            supports_deadline = "deadline" in inspect.signature(native.request).parameters
            if deadline is None:
                request = native.request
            elif supports_deadline:
                request = partial(native.request, deadline=deadline)
            else:
                def request(method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
                    started = time.monotonic()
                    remaining = deadline - started
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, 0)
                    rpc_timeout = 10.0
                    caller_limited = remaining <= rpc_timeout
                    # Split-pinned product releases expose the 2-arg transport;
                    # cap per-RPC waiting and translate caller-budget timeouts uniformly.
                    native.response_timeout_sec = remaining if caller_limited else rpc_timeout
                    try:
                        return native.request(method, params)
                    except NativeGoalProtocolError as exc:
                        if caller_limited and str(exc) == f"app_server_response_timeout:{method}":
                            raise subprocess.TimeoutExpired(command, remaining) from exc
                        raise

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
