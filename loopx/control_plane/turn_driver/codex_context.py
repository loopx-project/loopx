"""Opt-in native lifecycle IO for the existing quota context projector.

The private directory belongs to one native session/workspace. Files are only
candidate references: actual native input must independently prove retention.
No hook grants execution authority or acknowledges model understanding.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator, Mapping
from typing import Any
import json
import os
from pathlib import Path
import stat
import uuid

from .decision_capture import (
    write_decision_capture, capture_context_projection, project_captured_context, CONTEXT_CAPTURE_READ_GUIDANCE,
)

# Native adapter activation reuses scheduler-owned host declarations. These
# settings are presentation opt-ins, never host authentication or authority.
CONTEXT_DELIVERY_SETTINGS = ("off", "on")
CONTEXT_DELIVERY_SCENARIOS = ("codex_benchmark_heartbeat", "codex_app_heartbeat")


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


def codex_context_recovery_instructions(directory: str, *, goal_id: str, agent_id: str,
                                         cli_argv: list[str], session_root: bool = False) -> str:
    """Host-owned persistent instructions, independent of event-hook delivery."""
    import shlex

    command = shlex.join([*cli_argv, "--format", "json", "quota", "context-read",
        "--codex-context-root" if session_root else "--codex-context-dir", directory,
        "--goal-id", goal_id, "--agent-id", agent_id])
    return (
        "If continuing a captured LoopX decision and its complete context or recovery reference was lost "
        "(including compaction or a failed hook), recover before dependent work with: " + command + ". "
        "Follow the returned next_command through eof; verify complete bytes and digest. "
        "If lookup fails, restore the original capture/reference; do not guess, readmit or ask the owner. "
        "Recovery never renews authority/freshness or proves retention. Independent authorized work can continue. "
        "For code-mode reads, size batches against both direct-result and retained-history output limits; "
        "max_output_tokens alone does not establish the latter. Start with at most two default pages when "
        "the effective history limit is unknown. With a known sufficient limit, larger batches can reduce "
        "round trips; emit each stdout separately and check complete model-visible bytes. This advice "
        "grants no authority to change host limits or treat complete runtime output as retention proof."
    )


def codex_context_transport_configuration(native_config: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the default OpenAI request observable without changing its login.

    Native WebSocket requests may carry only previous_response_id and new
    items. The input observer deliberately cannot certify that implicit history.
    A separate native HTTP provider retains Codex's OpenAI routing and auth;
    explicit operator providers are left alone. This config grants no authority
    and does not assert that any particular input was complete.
    """
    if native_config.get("model_provider", "openai") != "openai":
        return {}
    provider = "loopx_context_http"
    if provider in native_config.get("model_providers", {}):
        raise ValueError("context HTTP provider conflicts with existing native configuration")
    return {"model_provider": provider, "model_providers": {provider: {
        "name": "OpenAI", "wire_api": "responses", "requires_openai_auth": True,
        "supports_websockets": False}}}


def codex_context_configuration(directory: str, *, goal_id: str, agent_id: str,
                                 cli_argv: list[str], scenario: str, session_root: bool = False,
                                 inherit_machine: bool = False) -> dict[str, Any]:
    """Native configuration data shared by the worker and CLI setup journey.

    This renders no trust hashes and writes no host configuration. The native
    host remains responsible for reviewing/trusting the exact discovered hook.
    This manual template assumes the default OpenAI provider. Automatic callers
    resolve the effective provider with codex_context_transport_configuration.
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
    return {**codex_context_transport_configuration({}),
        "hooks": {event: [{"hooks": [handler]}] for event in ("SessionStart", "PreToolUse")},
        "developer_instructions": codex_context_recovery_instructions(directory, goal_id=goal_id,
            agent_id=agent_id, cli_argv=cli_argv, session_root=session_root)}


def codex_context_hook(directory: str | None, event: dict[str, Any]) -> dict[str, Any]:
    """Consume native lifecycle events; emit only same-capture recovery refs.

    Compaction/fork/restart invalidate. Same-session resume keeps only a candidate:
    the new active call's actual input must prove every byte before reuse.
    PreToolUse supplies the native call id; it is not retention proof. Failure to update the
    primary journal still returns the existing recovery ref when available.
    Trust/enablement and complete event delivery remain native host concerns.
    """
    if directory is None:
        return {}
    name, session = event.get("hook_event_name"), event.get("session_id")
    if not isinstance(session, str) or not session:
        return {}
    recovery = None
    try:
        with _locked(directory) as root:
            state = _state(root)
            same = state.get("session_id") == session and state.get("cwd") == event.get("cwd")
            if same:
                recovery = state.get("recovery")
            else:
                state = {"session_id": session, "cwd": event.get("cwd")}
            resumed = name == "SessionStart" and event.get("source") == "resume" and same
            if resumed:
                state["active_call"] = None
            elif name in ("SessionStart", "PreCompact"):
                state.update(context_epoch=uuid.uuid4().hex, active_call=None)
                state.pop("candidate", None)
            elif name == "PreToolUse" and same and state.get("context_epoch"):
                state["active_call"] = event.get("tool_use_id")
            else:
                return {}
            _save(root, state)
    except (OSError, ValueError, TypeError, RecursionError):
        # A failed primary update cannot suppress a known full-read reference.
        # It also cannot manufacture a fresh active-call observation.
        pass
    if name == "SessionStart" and isinstance(recovery, dict) and recovery.get("command"):
        if event.get("source") == "resume":
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                "LoopX resumed context is unconfirmed. Reuse prior context only when complete in current input. "
                "If missing before continuing the captured decision, read the original full capture. "
                "Recovery does not renew authority or freshness. " + CONTEXT_CAPTURE_READ_GUIDANCE + " "
                + recovery["command"]}}
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
            "LoopX context baseline invalidated. Before dependent work, read this original full capture. "
            "Capture recovery does not renew authority or source freshness. "
            + CONTEXT_CAPTURE_READ_GUIDANCE + " "
            + recovery["command"]}}
    return {}


def _trace_bundle(session: str, tool_id: str) -> str | None:
    from .codex_input_trace import read_native_trace_file

    value = os.environ.get("CODEX_ROLLOUT_TRACE_ROOT")
    if not value:
        return None  # never enable tracing implicitly
    matches = []
    # Native diagnostics are optional. Bound discovery instead of scanning a
    # long session archive. Resume can create another bundle for the same
    # session; the active-call/input verifier, never mtime, selects its source.
    for index, path in enumerate(Path(value).glob("trace-*")):
        if index >= 128:
            return None
        try:
            manifest = _read(path / "manifest.json", private=False)
        except (OSError, ValueError):
            continue
        if manifest.get("root_thread_id") == session and manifest.get("rollout_id") == session:
            try:
                raw = read_native_trace_file(path / "trace.jsonl", tail=True)
                if not raw.endswith(b"\n"):
                    return None
                events = [json.loads(line)["payload"] for line in raw.splitlines()]
                calls = [event for event in events if event.get("tool_call_id") == tool_id
                         and event.get("type") in ("tool_call_started", "tool_call_ended")]
                if calls and calls[-1]["type"] == "tool_call_started":
                    matches.append(str(path))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return None
    # Count live call identities before checking retained content. Otherwise an
    # abandoned old call could win when a resumed call with the same id lost its
    # input. The selected bundle still needs the complete independent verifier.
    return matches[0] if len(matches) == 1 else None


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
    """A saved candidate is reusable only with independent current-input proof."""
    from .codex_input_trace import codex_context_in_active_input

    request: dict[str, Any] = {"scope": scope}
    candidate = state.get("candidate")
    if isinstance(candidate, dict) and isinstance(state.get("active_call"), str):
        try:
            baseline = _read(Path(candidate["path"]))
            trace = _trace_bundle(scope["session_id"], state["active_call"])
            if trace:
                observation = dict(trace_bundle=trace, tool_call_id=state["active_call"],
                    capture_file=candidate["capture_file"], capture_sha256=candidate["capture_sha256"])
                if codex_context_in_active_input(observation, baseline=baseline, session_id=scope["session_id"]):
                    request.update(baseline=baseline, retained={"complete": True, "target": baseline["target"]},
                        native_input=observation)
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass  # Missing candidate affects reuse, never the current recovery reference.
    return request


def project_codex_context(payload: dict[str, Any], *, directory: str | None, capture_directory: Path,
                          goal_id: str, agent_id: str, surface: str, capture_id: str) -> dict[str, Any]:
    """Commit current recovery before optional projection or candidate updates.

    Serialization prevents invalidation from moving the epoch during preparation.
    Full fallback must recover this capture or refuse lookup, never an older one.
    Candidates still need independent current-input proof before reuse.
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
                recovery = capture_context_projection(payload, directory=capture_directory)
                state["recovery"] = {**{key: recovery[key] for key in ("path", "sha256", "command")},
                                     "goal_id": goal_id, "agent_id": agent_id}
                _save(root, state)
                recovery_committed = True

                scope = dict(goal_id=goal_id, agent_id=agent_id, session_id=session, context_epoch=epoch)
                result = project_captured_context(payload, directory=capture_directory,
                    request=_native_context_request(state, scope), captured_recovery=recovery,
                    goal_id=goal_id, agent_id=agent_id, surface=surface, capture_id=capture_id)
                if result.get("schema_version") == "context_delivery_v1" and result["mode"] == "full":
                    path = capture_directory / "presentation.json"
                    write_decision_capture(path, result)
                    state["candidate"] = dict(path=str(path), capture_file=recovery["path"],
                        capture_sha256=recovery["sha256"])
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
