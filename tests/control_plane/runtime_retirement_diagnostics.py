"""Failure-only observations for the copied runtime retirement fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path

from loopx.control_plane import effect_runtime

PREFIX = "loopx_fixture_retirement:"
EVENTS = frozenset({"shutdown_requested", "close", "flush_enter", "flush_done", "locator_read_done",
    "locator_read_error", "locator_unlink_enter", "locator_unlink_done", "locator_unlink_error",
    "cleanup_finally", "lock_attempt", "lock_acquired", "lock_acquire_error", "lock_release_error",
    "lock_release_result"})
CODES = frozenset({"EACCES", "EPERM", "ENOENT", "EEXIST", "EIO", "EBUSY", "ENOTEMPTY", "EBADF",
    "ENOSPC", "EMFILE", "ENFILE", "ETIMEDOUT", "EINTR", "ENOTDIR", "EROFS", "mutation_lock_timeout"})
TRACE = '''
function fixtureTrace(event: string, details: Record<string, unknown> = {}): void {
  try { console.error("loopx_fixture_retirement:" + JSON.stringify({
    event, pid: process.pid, ms: performance.now(), ...details})); } catch {}
}
function fixtureCode(error: unknown): string | null {
  const code = (error as {code?: unknown})?.code;
  return typeof code === "string" ? code : null;
}
'''


def _replace(source: str, before: str, after: str) -> str:
    assert source.count(before) == 1, "copied runtime diagnostic anchor changed"
    return source.replace(before, after, 1)


def instrument_copied_runtime(root: Path) -> None:
    """Observe the private fixture copy; production modules are never edited."""
    server = root / "effect_runtime_server.ts"
    text = server.read_text()
    text = _replace(text, 'const REQUEST_SCHEMA =', TRACE + '\nconst REQUEST_SCHEMA =')
    for before, after in (
        ('shutdownRequested = true;', 'shutdownRequested = true; fixtureTrace("shutdown_requested");'),
        ('server.on("close", () => {', 'server.on("close", () => { fixtureTrace("close", {pending: pendingRequests});'),
        ('flushCompileCache();', 'fixtureTrace("flush_enter"); flushCompileCache(); fixtureTrace("flush_done");'),
        ('published = JSON.parse(await readFile(infoPath, "utf8"));',
         'published = JSON.parse(await readFile(infoPath, "utf8")); fixtureTrace("locator_read_done");'),
        ('} catch {\n        return;\n      }\n      // A timed-out client',
         '} catch (error) {\n        fixtureTrace("locator_read_error", {code: fixtureCode(error)});\n        return;\n      }\n      // A timed-out client'),
        ('await rm(infoPath, { force: true });',
         'fixtureTrace("locator_unlink_enter");\n        try { await rm(infoPath, { force: true }); fixtureTrace("locator_unlink_done"); }\n'
         '        catch (error) { fixtureTrace("locator_unlink_error", {code: fixtureCode(error)}); throw error; }'),
        ('})().finally(() => process.exit(0));',
         '})().finally(() => { fixtureTrace("cleanup_finally"); process.exit(0); });'),
    ):
        text = _replace(text, before, after)
    server.write_text(text)
    io = root / "effect_runtime_io.ts"
    text = io.read_text()
    text = _replace(text, 'const MUTATION_LOCK_TIMEOUT_MS =', TRACE + '\nconst MUTATION_LOCK_TIMEOUT_MS =')
    text = _replace(text, 'const lock = await acquireFileMutationLock(targetPath, process.pid, timeoutMs);',
        'fixtureTrace("lock_attempt");\n  let lock: FileMutationLock;\n  try { lock = await acquireFileMutationLock(targetPath, process.pid, timeoutMs); }\n'
        '  catch (error) { fixtureTrace("lock_acquire_error", {code: fixtureCode(error)}); throw error; }\n  fixtureTrace("lock_acquired");')
    text = _replace(text, 'if (suppressErrors) return false;',
        'fixtureTrace("lock_release_error", {code: fixtureCode(error)});\n    if (suppressErrors) return false;')
    text = _replace(text, 'await releaseFileMutationLock(lock.targetPath, lock.token, null, true);',
        'const released = await releaseFileMutationLock(lock.targetPath, lock.token, null, true);\n'
        '    fixtureTrace("lock_release_result", {released});')
    io.write_text(text)


def trace_events(raw: bytes) -> list[dict]:
    """Never publish raw stderr or unknown fields, event names, or error text."""
    result = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith(PREFIX):
            continue
        try:
            value = json.loads(line[len(PREFIX):])
        except ValueError:
            continue
        if not isinstance(value, dict) or not isinstance(value.get("event"), str) or value["event"] not in EVENTS:
            continue
        item = {"event": value["event"]}
        for key in ("pid", "ms", "pending"):
            number = value.get(key)
            if type(number) in (int, float):
                item[key] = number
        if type(value.get("released")) is bool:
            item["released"] = value["released"]
        if "code" in value:
            item["code"] = value["code"] if isinstance(value["code"], str) and value["code"] in CODES else None
        result.append(item)
    return result[-64:]


def file_state(path: Path, pid: int) -> dict:
    """Return only existence, identity matches, liveness and numeric errno."""
    try:
        value = json.loads(path.read_text())
    except OSError as error:
        return {"read": "missing" if isinstance(error, FileNotFoundError) else "error",
                "errno": error.errno, "winerror": getattr(error, "winerror", None)}
    except ValueError:
        return {"read": "invalid_json"}
    owner = value.get("pid") if isinstance(value, dict) else None
    return {"read": "ok", "owner_matches_runtime": owner == pid,
            "owner_alive": effect_runtime._pid_is_alive(owner)}


def retained_stderr(process_args: list, options: dict, root: Path, captures: list[int]) -> None:
    if str(root / "effect_runtime_server.ts") in process_args and "stderr" in options:
        captures.append(os.dup(options["stderr"].fileno()))


def failure_trace(captures: list[int]) -> list[dict]:
    events = []
    for descriptor in captures:
        try:
            # Observe only after the original assertion failed. dup shares the
            # child's file offset: seek/read/restore can race a concurrent write.
            # This bounded trace is evidence available now, not a complete log.
            position = os.lseek(descriptor, 0, os.SEEK_CUR)
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                events.extend(trace_events(os.read(descriptor, 16 * 1024)))
            finally:
                os.lseek(descriptor, position, os.SEEK_SET)
        except OSError as error:
            events.append({"trace_read_errno": error.errno})
    return events
