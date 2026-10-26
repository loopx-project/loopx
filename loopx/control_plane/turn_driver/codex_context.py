"""Opt-in native lifecycle IO for the existing quota context projector.

The private directory belongs to one native session/workspace. Completed reader
pages establish an explicit continuity assumption; hooks invalidate known loss.
This neither proves retained model input nor grants execution authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any
import json
import os
from pathlib import Path
import stat
import uuid

from .decision_capture import (
    write_decision_capture, capture_context_projection, project_captured_context, CONTEXT_CAPTURE_PAGE_GUIDANCE,
    capture_page_range, read_context_capture_file,
    context_capture_read_command, NATIVE_CONTEXT_PAGE_BYTES, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
)

# Native adapter activation reuses scheduler-owned host declarations. These
# settings are presentation opt-ins, never host authentication or authority.
CONTEXT_DELIVERY_SETTINGS = ("off", "on")
CONTEXT_DELIVERY_SCENARIOS = ("codex_benchmark_heartbeat", "codex_app_heartbeat")
CONTEXT_LIFECYCLE_HOOKS = ("SessionStart", "PreCompact", "PostCompact", "PostToolUse", "Interrupt")


def context_delivery_for_arguments(args: Any, *, codex_machine_preference: bool = False) -> dict[str, Any]:
    """One declaration adapter for explicit CLI and inherited machine opt-ins."""
    fields = {key: getattr(args, key, None) for key in ("host_surface", "scheduler_owner", "execution_mode")}
    profiles = [value for value in (getattr(args, "runtime_profile", None),
        "codex_app_heartbeat" if getattr(args, "codex_app", False) else None,
        "trae_app" if getattr(args, "trae_app", False) else None) if value is not None]
    setting = getattr(args, "context_delivery", None)
    setting = "off" if setting is None else setting
    if len(profiles) > 1:
        return context_delivery_activation(setting)
    return context_delivery_activation(setting, runtime_profile=profiles[0] if profiles else None,
        scheduler_execution_context=fields if any(fields.values()) else None,
        benchmark_mode=os.environ.get("LOOPX_EXECUTION_MODE")
            if os.environ.get("LOOPX_CONTEXT_DELIVERY") == "on" else None,
        codex_machine_preference=codex_machine_preference)


def context_delivery_activation(setting: str, *, runtime_profile: str | None = None,
                                scheduler_execution_context: dict[str, Any] | None = None,
                                benchmark_mode: str | None = None,
                                codex_machine_preference: bool = False) -> dict[str, Any]:
    from ..scheduler.execution_context import (
        ExecutionMode, HostSurface, SchedulerOwner, resolve_scheduler_execution_context,
        scheduler_execution_context_for_runtime_profile,
    )

    if setting not in CONTEXT_DELIVERY_SETTINGS:
        raise ValueError("context-delivery must be off or on")
    scenario = None
    if setting == "on":
        # Conflicting declarations cannot activate transport. The scheduler
        # owner still validates execution; this adapter does not repair it.
        if runtime_profile is not None and scheduler_execution_context is not None:
            resolution = resolve_scheduler_execution_context(None)
        elif runtime_profile is not None:
            resolution = scheduler_execution_context_for_runtime_profile(runtime_profile)
        else:
            resolution = resolve_scheduler_execution_context(scheduler_execution_context)
        context = resolution.context if resolution.ok else None
        if context is not None:
            # The machine's application row selects Codex. Other explicitly
            # resolved hosts keep full delivery; an explicit CLI on still
            # rejects them. Unknown/conflicting declarations never bypass this
            # adapter, and generic_cli still needs the benchmark declaration.
            if codex_machine_preference and context.host_surface in {
                HostSurface.ARK_MANAGED_AGENT, HostSurface.TRAE_APP, HostSurface.CLAUDE_CODE,
                HostSurface.KUNLUNCODE, HostSurface.KIRO_CLI, HostSurface.LOCAL_SCHEDULER,
            }:
                return context_delivery_activation("off")
            if (context.host_surface is HostSurface.CODEX_APP
                    and context.scheduler_owner is SchedulerOwner.HOST_AUTOMATION
                    and context.execution_mode is ExecutionMode.HOSTED_AUTOMATION):
                scenario = "codex_app_heartbeat"
            elif (context.host_surface is HostSurface.GENERIC_CLI
                    and context.scheduler_owner is SchedulerOwner.AGENT_CLI_LOOP
                    and context.execution_mode is ExecutionMode.INTERACTIVE
                    and benchmark_mode == "heartbeat"):
                scenario = "codex_benchmark_heartbeat"
        if scenario is None:
            raise ValueError(
                "--context-delivery on requires a supported declared run mode: "
                "Codex App heartbeat (--codex-app or --runtime-profile codex_app_heartbeat), "
                "or the Codex benchmark heartbeat worker. Missing, contradictory or unsupported "
                "declarations are rejected; use --context-delivery off for ordinary full delivery."
            )
    return {"requested": setting, "enabled": scenario is not None, "scenario": scenario,
        "reason": "supported_declared_run_mode" if scenario else "disabled",
        "supported_scenarios": list(CONTEXT_DELIVERY_SCENARIOS)}


def _read(path: Path, *, private: bool = True) -> dict[str, Any]:
    from ..effect_runtime import MAX_REQUEST_BYTES

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or (private and info.st_mode & 0o077) or info.st_size > MAX_REQUEST_BYTES):
            raise ValueError("context transport needs a bounded private regular file")
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("context transport file is too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("context transport file must contain an object")
    return result


def _private_root(directory: str) -> Path:
    root = Path(directory).expanduser().absolute()
    root = root.parent.resolve(strict=True) / root.name
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("context directory must already exist, be private and belong to this user")
    return root


def codex_context_directory(*, directory: str | None = None, root: str | None = None,
                            session: str | None = None, create: bool = False) -> str | None:
    """Namespace shared host configuration without sharing session journals.

    A native session id selects a directory, never supplies a filesystem path
    or proves retained context. Read-only recovery must not create directories.
    """
    if directory is not None and root is not None:
        raise ValueError("choose codex-context-dir or codex-context-root")
    if root is None:
        return directory
    import hashlib

    parent = _private_root(root)
    session = session if session is not None else os.environ.get("CODEX_THREAD_ID")
    if not isinstance(session, str) or not session:
        raise ValueError("native session context requires CODEX_THREAD_ID or a native hook session")
    child = parent / hashlib.sha256(session.encode()).hexdigest()
    if create:
        child.mkdir(mode=0o700, exist_ok=True)
    return str(_private_root(str(child)))


@contextmanager
def _locked(directory: str) -> Iterator[Path]:
    if os.name != "posix":
        raise ValueError("native context continuity is unavailable on this platform")
    import fcntl

    root = _private_root(directory)
    fd = os.open(root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "r+") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError("context lock is not a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield root
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _state(root: Path) -> dict[str, Any]:
    try:
        return _read(root / "session.json")
    except FileNotFoundError:
        return {}


def _save(root: Path, state: dict[str, Any]) -> None:
    write_decision_capture(root / "session.json", state, publish=os.replace)


def read_codex_context_reference(directory: str, *, goal_id: str | None, agent_id: str | None) -> tuple[str, str]:
    """Select an original capture after a lost hook; never assert retention.

    The journal is atomically replaced, so this read needs no lock or write.
    Later pages use the returned explicit path/digest, not a moving selector.
    """
    state = _state(_private_root(directory))
    session = os.environ.get("CODEX_THREAD_ID")
    if (not session or state.get("session_id") != session
            or not isinstance(state.get("cwd"), str)
            or Path(state["cwd"]).resolve() != Path.cwd().resolve()):
        raise ValueError("original capture lookup requires the same native session and workspace")
    recovery = state.get("recovery")
    if (not goal_id or not agent_id or not isinstance(recovery, dict)
            or recovery.get("goal_id") != goal_id or recovery.get("agent_id") != agent_id):
        raise ValueError("no original capture for this Goal/Agent; use its explicit capture reference")
    path, digest = recovery.get("path"), recovery.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise ValueError("original capture reference is incomplete")
    return path, digest


def _context_continuation_instructions(command: str) -> str:
    """Distinguish fresh full delivery from recovery of a dependent decision."""
    return (
        "For a new independent decision, run its normal guarded context command first. "
        "Without a usable baseline it returns the current full capture; read it completely before dependent work. "
        "Do not read an older capture merely to seed a delta. "
        "If continuing a captured LoopX decision after context loss (including compaction or a failed hook), "
        "first recover that exact original capture with: " + command + ". "
        "A fresh guard cannot replace recovery of that decision. "
        "If recovery fails, restore the original capture/reference; do not guess or ask the owner. "
        "Recovery never renews authority/freshness or proves retention. Independent authorized work can continue. "
    )


def codex_context_recovery_instructions(directory: str, *, goal_id: str, agent_id: str,
                                         cli_argv: list[str], session_root: bool = False) -> str:
    """Host-owned persistent instructions, independent of event-hook delivery."""
    import shlex

    command = shlex.join([*cli_argv, "--format", "json", "quota", "context-read",
        "--codex-context-root" if session_root else "--codex-context-dir", directory,
        "--goal-id", goal_id, "--agent-id", agent_id, "--page-bytes", str(NATIVE_CONTEXT_PAGE_BYTES),
        "--max-page-output-bytes", str(NATIVE_CONTEXT_PAGE_OUTPUT_BYTES)])
    return (
        _context_continuation_instructions(command) +
        "Follow the returned next_command through eof; verify complete bytes and digest. "
        "For code-mode reads, size batches against both direct-result and retained-history output limits; "
        "max_output_tokens alone does not establish the latter. Read one output at a time with "
        "--max-page-output-bytes when the effective history limit is unknown; for legacy default pages, "
        "start with at most two. With a known sufficient limit, larger batches can reduce "
        "round trips; emit each stdout separately and check complete model-visible bytes. This advice "
        "grants no authority to change host limits or treat complete runtime output as retention proof."
    )


def codex_context_configuration(directory: str, *, goal_id: str, agent_id: str,
                                 cli_argv: list[str], scenario: str, session_root: bool = False,
                                 inherit_machine: bool = False) -> dict[str, Any]:
    """Native configuration data shared by the worker and CLI setup journey.

    This renders no trust hashes and writes no host configuration. The native
    host remains responsible for reviewing/trusting the exact discovered hook.
    Authentication, endpoints, providers and connection modes remain native.
    """
    import shlex

    if scenario not in CONTEXT_DELIVERY_SCENARIOS:
        raise ValueError("unsupported native context configuration scenario")
    if not goal_id or not agent_id:
        raise ValueError("context configuration requires exact goal-id and agent-id")
    directory = str(_private_root(directory))
    option = "--codex-context-root" if session_root else "--codex-context-dir"
    handler = {"type": "command", "command": shlex.join([
        *cli_argv, "--format", "json", "quota", "context-hook",
        *([] if inherit_machine else ["--context-delivery", "on"]),
        *(["--codex-app"] if scenario == "codex_app_heartbeat" else
          ["--runtime-profile", "generic_cli"]),
        option, directory]), "timeout": 10}
    return {"hooks": {event: [{**({"matcher": "^Bash$"} if event == "PostToolUse" else {}),
                              "hooks": [handler]}] for event in CONTEXT_LIFECYCLE_HOOKS},
        "developer_instructions": codex_context_recovery_instructions(directory, goal_id=goal_id,
            agent_id=agent_id, cli_argv=cli_argv, session_root=session_root)}


def codex_context_hook(directory: str | None, event: dict[str, Any]) -> dict[str, Any]:
    """Native IO around the shared typed lifecycle owner, without input tracing.

    PostToolUse supplies completed reader pages. Assuming their normal delivery
    is intentional; an unseen downstream loss or missed hook can escape detection.
    Known invalidation/IO failures cannot leave a usable stale baseline.
    """
    from ..quota.turn_envelope import update_native_context_lifecycle
    from ..effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
    from .codex_context_history import checkpoint_native_read, native_resume_verified, observe_native_read

    if directory is None:
        return {}
    name, session = event.get("hook_event_name"), event.get("session_id")
    if not isinstance(session, str) or not session:
        return {}
    if isinstance(event.get("agent_id"), str) and event["agent_id"]:
        return {}  # no parent recovery reference may escape to a subagent
    pages = _context_pages(event.get("tool_response")) if name == "PostToolUse" else []
    if name == "PostToolUse" and not pages:
        return {}  # ordinary tool use neither invalidates nor rewrites the journal
    recovery = None
    try:
        with _locked(directory) as root:
            try:
                state = _state(root)
                read_state = state
                same = state.get("session_id") == session and state.get("cwd") == event.get("cwd")
                if same:
                    recovery = state.get("recovery")
                    if name == "PostToolUse":
                        pages = _verified_context_pages(state, pages)
                        read_state = observe_native_read(state, event, pages)
                updated = update_native_context_lifecycle(read_state, event,
                    new_epoch=uuid.uuid4().hex, pages=pages, pages_verified=True,
                    resume_verified=name == "SessionStart" and native_resume_verified(state, event))
                if name == "PostToolUse":
                    checkpoint_native_read(read_state, updated, event, pages)
                if updated != state:
                    _save(root, updated)
                recovery_needed = same and not updated.get("candidate")
            except (OSError, ValueError, TypeError, RecursionError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
                # A transient read/reduction failure can miss invalidation just
                # as a failed write can. While holding the same lock, retire
                # this journal before IO recovery can revive its old candidate.
                # Immutable full captures and explicit references stay intact.
                _discard_recovery(root)
                raise
    except (OSError, ValueError, TypeError, RecursionError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        recovery_needed = True
    if (name == "SessionStart" and recovery_needed
            and isinstance(recovery, dict) and recovery.get("command")):
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
            "LoopX context baseline invalidated. "
            + _context_continuation_instructions(recovery["command"])
            + CONTEXT_CAPTURE_PAGE_GUIDANCE}}
    return {}


def _context_pages(value: object) -> list[dict[str, Any]]:
    """Decode native shell/MCP JSON output; never classify arbitrary prose.

    Incomplete JSON has no successful page. The pending delivery stays pending.
    Native output truncation that is not reported here remains an assumption gap.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, dict):
        return []
    if value.get("schema_version") == "context_capture_page_v1":
        return [value]
    if isinstance(value.get("output"), str):
        pages = _context_pages(value["output"])
        if pages and "exit_code" in value and value["exit_code"] != 0:
            return [{"schema_version": "context_capture_page_v1", "ok": False}]
        return pages
    content = value.get("content")
    if isinstance(content, list):
        return [page for item in content if isinstance(item, dict)
                for page in _context_pages(item.get("text"))]
    return []


def _verified_context_pages(state: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare observed output with trusted journal references before reduction.

    Cache each bounded immutable file only within this hook. Neither output
    paths nor its self-reported digest can introduce another capture source.
    This verifies tool-output bytes, not downstream model-input retention.
    """
    references = [value for key in ("recovery", "pending")
                  if isinstance(value := state.get(key), dict)]
    files: dict[str, bytes] = {}
    result = []
    for page in pages:
        reference = next((value for value in references if value.get("sha256") == page.get("sha256")), None)
        if reference is None or page.get("ok") is not True:
            result.append(page)  # the typed owner handles failures and unrelated reads
            continue
        try:
            digest = reference["sha256"]
            if digest not in files:
                _, files[digest], _ = read_context_capture_file(reference["path"], digest, private=True)
            if capture_page_range(page, raw=files[digest], digest=digest) is None:
                raise ValueError("observed context page differs from the immutable capture")
            result.append(page)
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return [{**page, "ok": False}]
    return result


def _discard_recovery(root: Path) -> None:
    """Invalidate only this adapter's journal while its private root is locked."""
    path = root / "session.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if info.st_uid != os.getuid() or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise ValueError("context recovery journal is not an owned file")
    # unlink never follows a rejected symlink or deletes its target.
    path.unlink()


def _native_context_request(state: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
    """Use the explicit completed-delivery assumption, never a fabricated ACK."""
    request: dict[str, Any] = {"scope": scope}
    candidate = state.get("candidate")
    if (isinstance(candidate, dict) and candidate.get("basis") == "native_hook_assumption"
            and not state.get("pending")):
        try:
            path = Path(candidate["capture_file"])
            _, _, payload = read_context_capture_file(str(path), candidate["capture_sha256"], private=True)
            # The shared codec independently validates the complete context
            # digest and scope. No second read or full native-history scan.
            baseline = dict(schema_version="context_delivery_v1", mode="full",
                target=candidate["target"], ancestry=candidate.get("ancestry", 0), context=payload)
            request.update(baseline=baseline,
                continuity={"basis": "native_hook_assumption", "target": candidate["target"],
                    "capture_bytes_verified": candidate.get("capture_bytes_verified", False)})
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass  # Missing candidate affects reuse, never the current recovery reference.
    return request


def register_native_context_view(directory: str | None, view: dict[str, Any]) -> None:
    """Bind the existing pending delivery to its actual paged wire, under lock."""
    if directory is None or not isinstance(view.get("target"), dict):
        return
    with _locked(directory) as root:
        state = _state(root)
        pending = state.get("pending")
        if (not isinstance(pending, dict) or pending.get("target") != view["target"]
                or state.get("session_id") != os.environ.get("CODEX_THREAD_ID")):
            raise ValueError("native page view does not match the current pending capture")
        pending.update(path=view["path"], sha256=view["sha256"], total_bytes=view["total_bytes"],
            ancestry=view.get("ancestry", 0))
        _save(root, state)


def project_codex_context(payload: dict[str, Any], *, directory: str | None, capture_directory: Path,
                          goal_id: str, agent_id: str, surface: str, capture_id: str,
                          continuity_generation: str | None = None) -> dict[str, Any]:
    """Commit current recovery before optional projection or candidate updates.

    Serialization prevents invalidation from moving the epoch during preparation.
    Full fallback must recover this capture or refuse lookup, never an older one.
    Completed full or delta pages establish the explicit native-hook assumption.
    """
    from ..quota.error_codes import QuotaDecisionTransportError

    if directory is None:
        return payload
    try:
        with _locked(directory) as root:
            recovery_committed = False
            try:
                state = _state(root)
                session, epoch = state.get("session_id"), state.get("context_epoch")
                native_session = os.environ.get("CODEX_THREAD_ID")
                if (not isinstance(session, str) or not session
                        or (native_session and session != native_session)):
                    return payload  # Do not replace a different native session's recovery.
                if (not native_session or not isinstance(state.get("cwd"), str)
                        or Path(state["cwd"]).resolve() != Path.cwd().resolve()):
                    # A later tool can restore cwd/identity and make an old
                    # selector eligible again, so invalidate before full fallback.
                    raise ValueError("context recovery requires the same native session and workspace")
                if not isinstance(epoch, str) or not epoch:
                    raise ValueError("context recovery requires a native context epoch")
                scope = dict(goal_id=goal_id, agent_id=agent_id, session_id=session, context_epoch=epoch)
                if continuity_generation is not None:
                    scope["continuity_generation"] = continuity_generation
                request = _native_context_request(state, scope)
                recovery = capture_context_projection(payload, directory=capture_directory,
                    read_guidance=CONTEXT_CAPTURE_PAGE_GUIDANCE)
                recovery["command"] = context_capture_read_command(Path(recovery["path"]), recovery["sha256"],
                    page_bytes=NATIVE_CONTEXT_PAGE_BYTES, max_page_output_bytes=NATIVE_CONTEXT_PAGE_OUTPUT_BYTES)
                state["recovery"] = {**{key: recovery[key] for key in ("path", "sha256", "command")},
                    "goal_id": goal_id, "agent_id": agent_id,
                    "total_bytes": Path(recovery["path"]).stat().st_size}
                # Block reuse before optional wire IO, not after a first page
                # or EOF. A codec/read interruption keeps this incomplete.
                state["pending"] = {"path": recovery["path"], "sha256": recovery["sha256"],
                    "total_bytes": state["recovery"]["total_bytes"], "ranges": {}}
                _save(root, state)
                recovery_committed = True

                result = project_captured_context(payload, directory=capture_directory,
                    request=request, captured_recovery=recovery,
                    goal_id=goal_id, agent_id=agent_id, surface=surface, capture_id=capture_id)
                if result.get("schema_version") == "context_delivery_v1":
                    state["recovery"]["target"] = result["target"]
                    state["pending"].update(target=result["target"], ancestry=result["ancestry"])
                    _save(root, state)
                return result
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                if not recovery_committed:
                    _discard_recovery(root)
                return payload
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        # No safe fallback if the old selector cannot be invalidated under lock.
        raise QuotaDecisionTransportError(
            "Current context recovery could not be published or safely invalidated. "
            "Read the saved original decision and receipt; do not rerun admission."
        ) from exc
