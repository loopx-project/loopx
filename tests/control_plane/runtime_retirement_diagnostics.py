"""Failure-only observations for copied runtime drain and retirement fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from loopx.control_plane import effect_runtime

PREFIX = "loopx_fixture_retirement:"
EVENTS = frozenset({"shutdown_requested", "close", "flush_enter", "flush_done", "locator_read_done",
    "locator_read_error", "locator_unlink_enter", "locator_unlink_done", "locator_unlink_error",
    "cleanup_finally", "lock_attempt", "lock_acquired", "lock_acquire_error", "lock_release_error",
    "lock_release_result", "request_error", "kill_enter", "kill_returned"})
CODES = frozenset({"EACCES", "EPERM", "ENOENT", "EEXIST", "EIO", "EBUSY", "ENOTEMPTY", "EBADF",
    "ENOSPC", "EMFILE", "ENFILE", "ETIMEDOUT", "EINTR", "ENOTDIR", "EROFS", "EDQUOT", "EINVAL",
    "EISDIR", "ENAMETOOLONG", "mutation_lock_timeout"})
SYSCALLS = frozenset({"access", "chmod", "close", "fstat", "fsync", "mkdir", "open", "read",
    "readdir", "rename", "rmdir", "stat", "unlink", "write"})
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
        ('})().finally(() => process.exit(0));',
         '})().finally(() => { fixtureTrace("cleanup_finally"); process.exit(0); });'),
    ):
        text = _replace(text, before, after)
    server.write_text(text)
    cache = root / "effect_runtime_compile_cache.ts"
    text = _replace(cache.read_text(), 'const FLUSHING =', TRACE + '\nconst FLUSHING =')
    for before, after in (
        ('flushCompileCache();', 'fixtureTrace("flush_enter"); flushCompileCache(); fixtureTrace("flush_done");'),
        ('process.kill(process.pid, "SIGKILL");',
         'fixtureTrace("kill_enter", {main_thread: isMainThread}); process.kill(process.pid, "SIGKILL"); fixtureTrace("kill_returned");'),
        ('published = JSON.parse(await readFile(locator.infoPath, "utf8"));',
         'published = JSON.parse(await readFile(locator.infoPath, "utf8")); fixtureTrace("locator_read_done");'),
        ('} catch {\n      return;\n    }\n    // Both retirement paths',
         '} catch (error) {\n      fixtureTrace("locator_read_error", {code: fixtureCode(error)});\n      return;\n    }\n    // Both retirement paths'),
        ('await rm(locator.infoPath, { force: true });',
         'fixtureTrace("locator_unlink_enter");\n      try { await rm(locator.infoPath, { force: true }); fixtureTrace("locator_unlink_done"); }\n'
         '      catch (error) { fixtureTrace("locator_unlink_error", {code: fixtureCode(error)}); throw error; }'),
    ):
        text = _replace(text, before, after)
    cache.write_text(text)
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
    errors = root / "effect_runtime_errors.ts"
    text = _replace(errors.read_text(), 'export const EFFECT_RUNTIME_ERROR_KINDS =',
                    TRACE + '\nexport const EFFECT_RUNTIME_ERROR_KINDS =')
    text = _replace(text, '  if (error instanceof EffectRuntimeBoundaryError) {',
        '  try {\n'
        '    const system = error as {syscall?: unknown; errno?: unknown};\n'
        '    fixtureTrace("request_error", {code: fixtureCode(error), syscall: system?.syscall, errno: system?.errno});\n'
        '  } catch {}\n'
        '  if (error instanceof EffectRuntimeBoundaryError) {')
    errors.write_text(text)


def observe_copied_runtime(tmp_path: Path, monkeypatch, request) -> tuple[Path, list[int]]:
    """Reuse the isolated source copy and retain stderr until failure readback."""
    source_root = effect_runtime._control_plane_root()
    root = tmp_path / "control_plane"
    for relative in effect_runtime._runtime_source_files(source_root):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, target)
    instrument_copied_runtime(root)
    captures: list[int] = []
    popen = effect_runtime.subprocess.Popen

    def close_captures():
        for descriptor in captures:
            try:
                os.close(descriptor)
            except OSError:
                pass

    request.addfinalizer(close_captures)

    def capture_stderr(args, *positional, **options):
        retained_stderr(args, options, root, captures)
        return popen(args, *positional, **options)

    monkeypatch.setattr(effect_runtime.subprocess, "Popen", capture_stderr)
    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: root)
    return root, captures


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
        if type(value.get("main_thread")) is bool:
            item["main_thread"] = value["main_thread"]
        if "code" in value:
            item["code"] = value["code"] if isinstance(value["code"], str) and value["code"] in CODES else None
        if "syscall" in value:
            item["syscall"] = value["syscall"] if isinstance(value["syscall"], str) and value["syscall"] in SYSCALLS else None
        if type(value.get("errno")) is int:
            item["errno"] = value["errno"]
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
