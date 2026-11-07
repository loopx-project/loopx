"""Opt-in native lifecycle IO for the existing quota context projector.

The private directory belongs to one native session/workspace. Completed reader
pages establish an explicit continuity assumption; hooks invalidate known loss.
This neither proves retained model input nor grants execution authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
import json
import os
from pathlib import Path
import stat
import uuid

from .decision_capture import (
    write_decision_capture, capture_context_projection, CONTEXT_CAPTURE_PAGE_GUIDANCE,
    capture_page_range, read_context_capture_file, CONTEXT_CAPTURE_NAVIGATION_GUIDANCE,
    CONTEXT_CAPTURE_SELECTION_GUIDANCE, CONTEXT_CAPTURE_CODE_MODE_GUIDANCE,
    context_capture_read_command,
)
from .native_context_profile import (
    NATIVE_CONTEXT_PAGE_BYTES, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
    NATIVE_CONTEXT_LARGE_OUTPUT_BYTES, NATIVE_CONTEXT_HISTORY_TOKENS, NATIVE_CONTEXT_OUTPUT_ENV,
    native_context_page_budget,
)
from ..content_digest import BARE_SHA256_PATTERN
from .codex_execution_preparation import compact_execution_preparation_request

# Native adapter activation reuses scheduler-owned host declarations. These
# settings are presentation opt-ins, never host authentication or authority.
CONTEXT_DELIVERY_SETTINGS = ("off", "on", "auto")
DELTA_DELIVERY_SETTINGS = ("off", "auto")
CONTEXT_DELIVERY_SCENARIOS = ("codex_benchmark_heartbeat", "codex_app_heartbeat", "codex_app_ssh_goal")
CONTEXT_LIFECYCLE_HOOKS = ("SessionStart", "PreCompact", "PostCompact", "PostToolUse", "Interrupt")


class NativeContextSessionUnavailable(ValueError):
    """No native identity for a session journal; explicit file reads still work."""


def native_context_event_eligible(event: Mapping[str, Any]) -> bool:
    """Reject subagent hooks before parent-session context or lease effects."""
    agent_id = event.get("agent_id")
    return not (isinstance(agent_id, str) and agent_id)


def context_delivery_for_arguments(args: Any, *, codex_machine_preference: bool = False) -> dict[str, Any]:
    """One declaration adapter for explicit CLI and inherited machine opt-ins."""
    fields = {key: getattr(args, key, None) for key in ("host_surface", "scheduler_owner", "execution_mode")}
    profiles = [value for value in (getattr(args, "runtime_profile", None),
        "codex_app_heartbeat" if getattr(args, "codex_app", False) else None,
        "trae_app" if getattr(args, "trae_app", False) else None,
        "antigravity_app" if getattr(args, "antigravity_app", False) else None) if value is not None]
    setting = getattr(args, "context_delivery", None)
    setting = ("off" if os.environ.get("LOOPX_CONTEXT_DELIVERY") == "off" else "auto") if setting is None else setting
    delta = getattr(args, "delta_delivery", None)
    if len(profiles) > 1:
        return context_delivery_activation(setting, delta_delivery=delta)
    return context_delivery_activation(setting, runtime_profile=profiles[0] if profiles else None,
        scheduler_execution_context=fields if any(fields.values()) else None,
        benchmark_mode=os.environ.get("LOOPX_EXECUTION_MODE")
            if os.environ.get("LOOPX_CONTEXT_DELIVERY") == "on" else None,
        codex_machine_preference=codex_machine_preference, delta_delivery=delta)


def context_delivery_activation(setting: str, *, runtime_profile: str | None = None,
                                scheduler_execution_context: dict[str, Any] | None = None,
                                benchmark_mode: str | None = None,
                                codex_machine_preference: bool = False,
                                delta_delivery: str | None = None) -> dict[str, Any]:
    from ..scheduler.execution_context import (
        ExecutionMode, HostSurface, SchedulerOwner, resolve_scheduler_execution_context,
        scheduler_execution_context_for_runtime_profile,
    )

    if setting not in CONTEXT_DELIVERY_SETTINGS:
        raise ValueError("context-delivery must be off, on or auto")
    if delta_delivery is not None and delta_delivery not in DELTA_DELIVERY_SETTINGS:
        raise ValueError("delta-delivery must be off or auto")
    scenario = None
    # A lone legacy off is raw. An explicit pair can qualify Delta on the
    # full document without opting into progressive presentation.
    if delta_delivery == "auto" or (delta_delivery is None and setting != "off"):
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
                HostSurface.ARK_MANAGED_AGENT, HostSurface.TRAE_APP, HostSurface.ANTIGRAVITY_APP,
                HostSurface.CLAUDE_CODE, HostSurface.KUNLUNCODE, HostSurface.KIRO_CLI,
                HostSurface.LOCAL_SCHEDULER,
            }:
                context = None
            if (context is not None and context.host_surface is HostSurface.CODEX_APP
                    and context.scheduler_owner is SchedulerOwner.HOST_AUTOMATION
                    and context.execution_mode is ExecutionMode.HOSTED_AUTOMATION):
                scenario = "codex_app_heartbeat"
            elif (context is not None and context.host_surface is HostSurface.CODEX_APP_SSH
                    and context.scheduler_owner is SchedulerOwner.AGENT_CLI_LOOP
                    and context.execution_mode is ExecutionMode.INTERACTIVE):
                scenario = "codex_app_ssh_goal"
            elif (context is not None and context.host_surface is HostSurface.GENERIC_CLI
                    and context.scheduler_owner is SchedulerOwner.AGENT_CLI_LOOP
                    and context.execution_mode is ExecutionMode.INTERACTIVE
                    and benchmark_mode == "heartbeat"):
                scenario = "codex_benchmark_heartbeat"
        if scenario is None and setting == "on":
            raise ValueError(
                "--context-delivery on requires a supported declared run mode: "
                "Codex App heartbeat (--codex-app or --runtime-profile codex_app_heartbeat), "
                "Codex App SSH Goal (--runtime-profile codex_app_ssh_goal), "
                "or the Codex benchmark heartbeat worker. Missing, contradictory or unsupported "
                "declarations are rejected; use --context-delivery off for ordinary full delivery."
            )
    return {"requested": setting, "enabled": scenario is not None, "scenario": scenario,
        "progressive_enabled": setting != "off", "delta_delivery": "auto" if scenario else "off",
        "reason": "supported_declared_run_mode" if scenario else "progressive_only" if setting != "off" else "disabled",
        "supported_scenarios": list(CONTEXT_DELIVERY_SCENARIOS)}


def _read(path: Path, *, private: bool = True) -> dict[str, Any]:
    from ..effect_runtime import MAX_REQUEST_BYTES

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or (private and info.st_mode & 0o077) or info.st_size > MAX_REQUEST_BYTES):
            raise ValueError("context transport needs a bounded private regular file")
        raw = stream.read(info.st_size + 1)
    if len(raw) != info.st_size:
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
        raise NativeContextSessionUnavailable("native session context requires CODEX_THREAD_ID or a native hook session")
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
    with os.fdopen(fd, "r+", encoding="utf-8") as lock:
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


def record_native_claim_source(
    payload: Mapping[str, Any], digest: str, *, directory: str | None, context_root: str | None,
) -> None:
    """Remember the live guard's latest eligible capture outside its JSON."""
    preparation = compact_execution_preparation_request(payload)
    session = os.environ.get("CODEX_THREAD_ID") or None
    if not preparation or not preparation.get("turn_instance_id") or (not session and directory is None):
        return
    location = codex_context_directory(directory=directory, root=context_root, session=session, create=True)
    if location is None:
        return
    with _locked(location) as root:
        active_session = _state(root).get("session_id")
        if session and active_session not in (None, session):
            return
        # A caller outside the Host can explicitly select its private directory.
        # The journal identifies that session even without an ambient thread ID.
        write_decision_capture(root / "claim-source.json",
            {"session_id": session or active_session, "sha256": digest}, publish=os.replace)


def _original_native_claim_source(
    root: Path, session: str, digest: object, *, bind_unattached: bool = False,
) -> bool:
    if not isinstance(digest, str):
        return False
    try:
        path = root / "claim-source.json"
        saved = _read(path)
    except (OSError, ValueError):
        return False
    bound = {"session_id": session, "sha256": digest}
    if bind_unattached and saved == {**bound, "session_id": None}:
        # An explicit private directory can receive a guard before startup.
        # Adopt it once from the live session while holding its journal lock.
        write_decision_capture(path, bound, publish=os.replace)
        return True
    return saved == bound


def read_codex_context_reference(directory: str, *, goal_id: str | None, agent_id: str | None) -> tuple[str, str]:
    """Select an original capture after a lost hook; never assert retention.

    The journal is atomically replaced, so this read needs no lock or write.
    Later pages use the returned explicit path/digest, not a moving selector.
    """
    state = _state(_private_root(directory))
    session = os.environ.get("CODEX_THREAD_ID")
    if (not session or state.get("session_id") != session
            or not isinstance(state.get("cwd"), str) or not state["cwd"]):
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
    """Separate transport continuity from the lifetime of the admitted work.

    Losing a delta baseline says nothing about which facts the working brief
    preserved. Do not turn every compaction into another full-input loop.
    """
    return (
        "A new work Turn needs its current guard and the facts its work depends on; never prefetch an old packet to seed a delta. "
        "Compaction or a failed hook starts a new baseline epoch for the next work Turn; it does not invalidate the existing Turn or its saved recovery files. "
        "Continue from its working brief, preserving Goal, requirements/stops and original task/capture/settlement identity. "
        "Do not replay read context or rerun admission solely because the baseline was invalidated. "
        "Read missing necessary facts from this same capture with: " + command + " --select '<pointer>'. "
        "Replace the placeholder with a required pointer; full-file traversal is not a prerequisite. "
        "Finish an interrupted selected section only if the next decision needs it; do not claim unread content was read. "
        "Never repeat completed effects or settlement. If the original reference is lost, repair it first; "
        "independently authorized work can continue. A brief or recovery grants no new authority/freshness, "
        "delta baseline or retention proof. "
    )


# Both persistent setup and post-compaction recovery must describe the nested
# output budgets. The hook may be the only instruction retained after loss.
_NATIVE_CONTEXT_OUTPUT_GUIDANCE = (
    "For 64000-byte pages request reader max_output_tokens=22000 and code-mode exec pragma max_output_tokens=24000. "
)


def codex_context_recovery_instructions(directory: str, *, goal_id: str, agent_id: str,
                                         cli_argv: list[str], session_root: bool = False,
                                         page_output_bytes: int = NATIVE_CONTEXT_PAGE_OUTPUT_BYTES) -> str:
    """Host-owned persistent instructions, independent of event-hook delivery."""
    import shlex

    command = shlex.join([*cli_argv, "--format", "json", "quota", "context-read",
        "--codex-context-root" if session_root else "--codex-context-dir", directory,
        "--goal-id", goal_id, "--agent-id", agent_id, "--page-bytes", str(max(NATIVE_CONTEXT_PAGE_BYTES, page_output_bytes)),
        "--max-page-output-bytes", str(page_output_bytes)])
    return (
        _context_continuation_instructions(command) +
        CONTEXT_CAPTURE_NAVIGATION_GUIDANCE + CONTEXT_CAPTURE_CODE_MODE_GUIDANCE +
        ("The generated native configuration supports 64000-byte context-read outputs and a 20000-token history limit. "
         + _NATIVE_CONTEXT_OUTPUT_GUIDANCE +
         "Read one continuation per tool call. "
         if page_output_bytes == NATIVE_CONTEXT_LARGE_OUTPUT_BYTES else "") +
        "For code-mode reads, size batches against both direct-result and retained-history output limits; "
        "max_output_tokens alone does not establish the latter. Read one output at a time with "
        "--max-page-output-bytes when the effective history limit is unknown; for legacy default pages, "
        "start with at most two. With a known sufficient limit, larger batches can reduce "
        "round trips; emit each stdout separately and check complete model-visible bytes. This advice "
        "grants no authority to change host limits or treat complete runtime output as retention proof."
    )


def codex_context_configuration(directory: str, *, goal_id: str, agent_id: str,
                                 cli_argv: list[str], scenario: str, session_root: bool = False,
                                 inherit_machine: bool = False, progressive_enabled: bool = True) -> dict[str, Any]:
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
    activation_args = ["--context-delivery", "on"] if progressive_enabled else [
        "--context-delivery", "off", "--delta-delivery", "auto"]
    handler = {"type": "command", "command": shlex.join([
        *cli_argv, "--format", "json", "quota", "context-hook",
        *([] if inherit_machine else activation_args),
        *(["--codex-app"] if scenario == "codex_app_heartbeat" else
          ["--runtime-profile", "codex_app_ssh_goal" if scenario == "codex_app_ssh_goal" else "generic_cli"]),
        option, directory]), "timeout": 10}
    # Native Interrupt handlers have a 3s ceiling. Declare the effective value
    # rather than letting discovery silently clamp a different configuration.
    return {"config_overrides": {
            "tool_output_token_limit": NATIVE_CONTEXT_HISTORY_TOKENS,
            "shell_environment_policy.set." + NATIVE_CONTEXT_OUTPUT_ENV: str(NATIVE_CONTEXT_LARGE_OUTPUT_BYTES)},
        "hooks": {event: [{**({"matcher": "^Bash$"} if event == "PostToolUse" else {}),
                              "hooks": [{**handler, "timeout": 3} if event == "Interrupt" else handler]}]
                      for event in CONTEXT_LIFECYCLE_HOOKS},
        "developer_instructions": codex_context_recovery_instructions(directory, goal_id=goal_id,
            agent_id=agent_id, cli_argv=cli_argv, session_root=session_root,
            page_output_bytes=NATIVE_CONTEXT_LARGE_OUTPUT_BYTES)}


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
    if not native_context_event_eligible(event):
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
                        read_state = observe_native_read(state, event,
                            [page for page in pages if "read_sections" not in page])
                updated = update_native_context_lifecycle(read_state, event,
                    new_epoch=uuid.uuid4().hex, pages=pages, pages_verified=True,
                    resume_verified=name == "SessionStart" and native_resume_verified(state, event))
                if name == "PostToolUse":
                    checkpoint_native_read(read_state, updated, event,
                        [page for page in pages if "read_sections" not in page])
                if updated != state:
                    _save(root, updated)
                saved = updated.get("recovery")
                current_file_epoch = (isinstance(saved, dict)
                    and saved.get("continuity_basis") == "captured_file"
                    and isinstance(saved.get("target"), dict)
                    and saved["target"].get("context_epoch") == updated.get("context_epoch"))
                recovery_needed = same and not updated.get("candidate") and not current_file_epoch
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
            "LoopX model-context baseline invalidated. Saved files remain available. "
            + _context_continuation_instructions(recovery["command"])
            + CONTEXT_CAPTURE_SELECTION_GUIDANCE
            + _NATIVE_CONTEXT_OUTPUT_GUIDANCE
            + "If you choose to read an entire file: " + CONTEXT_CAPTURE_PAGE_GUIDANCE}}
    return {}


def native_execution_preparation_request(
    directory: str | None, event: dict[str, Any], *, readback_for: str | None = None,
) -> dict[str, Any] | None:
    """Reserve one automatic try or surface one readback for the same source."""

    if (directory is None or not native_context_event_eligible(event)
            or event.get("hook_event_name") not in {"SessionStart", "PostToolUse"}):
        return None
    session, cwd = event.get("session_id"), event.get("cwd")
    if not isinstance(session, str) or not session or not isinstance(cwd, str) or not cwd:
        return None
    try:
        with _locked(directory) as root:
            state = _state(root)
            if state.get("session_id") != session or state.get("cwd") != cwd:
                return None
            recovery = state.get("recovery")
            if not isinstance(recovery, dict):
                return None
            request = recovery.get("execution_preparation")
            source_sha256 = recovery.get("sha256")
            if (not isinstance(request, dict) or not isinstance(source_sha256, str)
                    or BARE_SHA256_PATTERN.fullmatch(source_sha256) is None):
                return None
            if readback_for is not None and readback_for != source_sha256:
                return None
            action = {**request, "source_sha256": source_sha256}
            claim_source_sha256 = action.get("claim_source_sha256")
            if (not isinstance(claim_source_sha256, str) or BARE_SHA256_PATTERN.fullmatch(claim_source_sha256) is None
                    or not _original_native_claim_source(root, session, claim_source_sha256)):
                action.pop("claim_source_sha256", None)
                return action if readback_for is None else None
            previous = state.get("host_execution_preparation")
            if isinstance(previous, dict) and previous.get("claim_source_sha256") == claim_source_sha256:
                if previous.get("state") == "attempted":
                    if previous.get("readback_sent") is True:
                        return None
                    previous["readback_sent"] = True
                    _save(root, state)
                    return {**action, "readback_only": True}
                if previous.get("state") == "ready" and event.get("hook_event_name") != "SessionStart":
                    return None
            if readback_for is not None:
                return None
            if request.get("turn_instance_id"):
                # Commit the reservation before the ordinary claim can run.
                # An interrupted Hook may have committed the claim; only the
                # exact provider receipt and current lease answer that question.
                state["host_execution_preparation"] = {
                    "source_sha256": source_sha256,
                    "claim_source_sha256": claim_source_sha256,
                    "state": "attempted",
                    "todo_id": request.get("todo_id"),
                    "turn_instance_id": request.get("turn_instance_id"),
                }
                _save(root, state)
            return action
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def record_native_execution_preparation(
    directory: str | None,
    event: dict[str, Any],
    *,
    source_sha256: str,
    receipt: Mapping[str, Any],
) -> None:
    """Record a ready receipt only while its source/session is still current."""

    if (directory is None or not native_context_event_eligible(event)
            or receipt.get("event") != "host_execution_prepared"):
        return
    with _locked(directory) as root:
        state = _state(root)
        recovery = state.get("recovery")
        if (
            state.get("session_id") != event.get("session_id")
            or state.get("cwd") != event.get("cwd")
            or not isinstance(recovery, dict)
            or recovery.get("sha256") != source_sha256
        ):
            raise ValueError("execution-preparation source moved before receipt publication")
        preparation = recovery.get("execution_preparation")
        state["host_execution_preparation"] = {
            "source_sha256": source_sha256,
            "claim_source_sha256": preparation.get("claim_source_sha256") if isinstance(preparation, dict) else None,
            "state": "ready",
            "todo_id": receipt.get("todo_id"),
            **(
                {"turn_instance_id": receipt["turn_instance_id"]}
                if receipt.get("turn_instance_id")
                else {}
            ),
        }
        _save(root, state)


def _context_pages(value: object) -> list[dict[str, Any]]:
    """Decode native shell/MCP JSON records; never classify arbitrary prose.

    Incomplete JSON has no successful page. The pending delivery stays pending.
    Enclosing tool failures override embedded successful reader pages.
    Native output truncation that is not reported here remains an assumption gap.
    """
    if isinstance(value, str):
        source_text = value
        try:
            value = json.loads(source_text)
        except ValueError:
            # CLI stdout is one complete JSON record per line. Other commands
            # may precede/follow it in the same shell result; do not search for
            # embedded JSON substrings or accept an incomplete record. Capture
            # identity and every reported byte are still verified below.
            pages = []
            for line in source_text.splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    pages.extend(_context_pages(record))
            return pages
    if not isinstance(value, dict):
        return []
    if value.get("schema_version") in ("context_capture_page_v1", "context_capture_selection_page_v1"):
        return [value]
    if isinstance(value.get("output"), str):
        pages = _context_pages(value["output"])
        failed = "exit_code" in value and value["exit_code"] != 0
    else:
        content = value.get("content")
        pages = [page for item in content if isinstance(item, dict)
                 for page in _context_pages(item.get("text"))] if isinstance(content, list) else []
        failed = False
    if pages and (failed or value.get("isError") is True):
        return [{"schema_version": "context_capture_page_v1", "ok": False}]
    return pages


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
        # These observations originate only below, after byte/source checks.
        page = {key: value for key, value in page.items() if key not in ("read_sections", "source_sha256")}
        source = state.get("source_recovery")
        if (any(key in page for key in ("selection_sha256", "selected_pointers", "list_fields", "reference_file"))
                or isinstance(source, dict) and page.get("sha256") == source.get("sha256")
                and page.get("sha256") != (state.get("recovery") or {}).get("sha256")):
            from .native_context_selection import verified_context_selection

            try:
                observed = verified_context_selection(state, page)
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                observed = None
            if observed is not None:
                result.append(observed)
            continue  # Never promote a selected read to full-delivery proof.
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


def _native_context_request(state: dict[str, Any], scope: dict[str, Any], capture_id: str) -> dict[str, Any]:
    """Compare verified durable versions independently of model retention.

    The same session's last full capture is a file baseline after partial reads
    within one context epoch. Compaction starts a fresh full baseline on the
    next work Turn. Missing/replaced/unreadable files also fall back to full.
    The shared codec verifies scope and content; no ACK is fabricated.
    """
    request: dict[str, Any] = {"scope": scope}
    previous = state.get("recovery")
    target = previous.get("target") if isinstance(previous, dict) else None
    if isinstance(target, dict) and target.get("capture_id") == capture_id:
        # Publishing an offer does not mean a reader saw it. A retry may use
        # the original predecessor, but never this offer as its own baseline.
        previous = previous.get("comparison_base")
    if isinstance(previous, dict) and isinstance(previous.get("target"), dict):
        try:
            path, raw, payload = read_context_capture_file(previous["path"], previous["sha256"], private=True)
            baseline = dict(schema_version="context_delivery_v1", mode="full",
                target=previous["target"], ancestry=0, context=payload)
            request.update(baseline=baseline,
                baseline_reference={"path": str(path), "sha256": previous["sha256"], "total_bytes": len(raw),
                    "command": context_capture_read_command(path, previous["sha256"])},
                continuity={"basis": "captured_file", "target": previous["target"], "capture_bytes_verified": True})
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass
    return request


def register_native_context_view(directory: str | None, view: dict[str, Any], *,
                                 hook_session: str | None = None) -> dict[str, Any] | None:
    """Bind the existing pending delivery to its actual paged wire, under lock."""
    if directory is None or not isinstance(view.get("target"), dict):
        return None
    with _locked(directory) as root:
        state = _state(root)
        pending = state.get("pending")
        if (not isinstance(pending, dict) or pending.get("target") != view["target"]
                or state.get("session_id") != (hook_session or os.environ.get("CODEX_THREAD_ID"))):
            raise ValueError("native page view does not match the current pending capture")
        pending.update(path=view["path"], sha256=view["sha256"], total_bytes=view["total_bytes"],
            ancestry=view.get("ancestry", 0),
            read_sections=pending.get("full_read_sections", {}) if view.get("mode") == "full" else {})
        _save(root, state)
        observed = pending.get("observed_first_read")
        return observed if isinstance(observed, dict) else None


@dataclass(frozen=True)
class NativeContextPreparation:
    """One invocation's wire and optional read plan; never native journal state."""

    delivery: dict[str, Any]
    initial_read_plan: dict[str, Any] | None = None


def prepare_codex_context(payload: dict[str, Any], *, directory: str | None, capture_directory: Path,
                          goal_id: str, agent_id: str, surface: str, capture_id: str,
                          continuity_generation: str | None = None,
                          include_initial_read_plan: bool = True,
                          progressive_enabled: bool | None = None,
                          selection_command_args_template: str | None = None,
                          captured_recovery: dict[str, Any] | None = None,
                          captured_decision_path: str | None = None,
                          hook_session: str | None = None) -> NativeContextPreparation:
    """Commit current recovery before optional projection or candidate updates.

    Serialization prevents invalidation from moving the epoch during preparation.
    Full fallback must recover this capture or refuse lookup, never an older one.
    Saved-file comparison is independent of any complete model-read observation.
    A trusted caller may reuse the identical source it just published; this
    reference must never come from the external continuity request.
    """
    from ...command_invocation import current_loopx_cli_argv
    from ..quota.error_codes import QuotaDecisionTransportError
    from ..quota.turn_envelope import prepare_context_delivery
    from ..effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError

    if directory is None:
        if not progressive_enabled:
            return NativeContextPreparation(payload)
        from .decision_capture import capture_progressive_context

        return NativeContextPreparation(capture_progressive_context(
            payload, directory=capture_directory, surface=surface)[0])
    try:
        with _locked(directory) as root:
            recovery_committed = False
            try:
                state = _state(root)
                session, epoch = state.get("session_id"), state.get("context_epoch")
                native_session = hook_session or os.environ.get("CODEX_THREAD_ID")
                if (not isinstance(session, str) or not session
                        or (native_session and session != native_session)):
                    return NativeContextPreparation(payload)  # Do not replace another session's recovery.
                # Native lifecycle events own workspace identity and epoch.
                # A shell tool may run in another checkout without changing
                # the host session or losing its previously delivered context.
                if (not native_session or not isinstance(state.get("cwd"), str) or not state["cwd"]):
                    raise ValueError("context recovery requires the same native session and workspace")
                if not isinstance(epoch, str) or not epoch:
                    raise ValueError("context recovery requires a native context epoch")
                scope = dict(goal_id=goal_id, agent_id=agent_id, session_id=session, context_epoch=epoch)
                if continuity_generation is not None:
                    scope["continuity_generation"] = continuity_generation
                selected = payload.get("selected_todo")
                read_scope = {**scope, "surface": surface,
                    "todo_id": selected.get("todo_id") if isinstance(selected, dict) else None}
                if state.get("read_scope") != read_scope:
                    for key in ("consumed_pointers", "confirmed_read_sections", "section_reads"):
                        state.pop(key, None)
                state["read_scope"] = read_scope
                request = _native_context_request(state, scope, capture_id)
                recovery = dict(captured_recovery) if captured_recovery is not None else capture_context_projection(
                    payload, directory=capture_directory, read_guidance=CONTEXT_CAPTURE_PAGE_GUIDANCE)
                state["source_recovery"] = {key: recovery[key] for key in ("path", "sha256")}
                state.pop("section_reads", None)  # partial reads of an older source are not current coverage
                page_bytes, output_bytes = native_context_page_budget()
                preparation = compact_execution_preparation_request(payload)
                if preparation and _original_native_claim_source(root, session, capture_id, bind_unattached=True):
                    preparation = compact_execution_preparation_request(payload, claim_source_sha256=capture_id)

                def publish_recovery(reference: dict[str, Any]) -> None:
                    reference["command"] = context_capture_read_command(Path(reference["path"]), reference["sha256"],
                        page_bytes=page_bytes, max_page_output_bytes=output_bytes, reader_argv=current_loopx_cli_argv())
                    state["recovery"] = {**{key: reference[key] for key in ("path", "sha256", "command")},
                        "goal_id": goal_id, "agent_id": agent_id,
                        "total_bytes": Path(reference["path"]).stat().st_size,
                        **({"execution_preparation": preparation} if preparation else {})}
                    state["pending"] = {"path": reference["path"], "sha256": reference["sha256"],
                        "total_bytes": state["recovery"]["total_bytes"], "ranges": {}}
                    _save(root, state)

                # Publish the current complete source before optional planning
                # or encoding can be interrupted. Never recover its predecessor.
                publish_recovery(recovery)
                recovery_committed = True
                # Legacy direct native callers omitted the presentation option.
                progressive = progressive_enabled is not False
                presentation = progressive or selection_command_args_template is not None
                try:
                    transaction = prepare_context_delivery(payload, scope=scope, capture_id=capture_id,
                        surface=surface, baseline=request.get("baseline"),
                        retained=request.get("retained"), continuity=request.get("continuity"),
                        include_initial_read_plan=include_initial_read_plan and progressive, native_projection={
                            "path": recovery["path"], "sha256": recovery["sha256"],
                            "source_path": recovery["path"],
                            **({"selection_command_args_template": selection_command_args_template}
                                if selection_command_args_template is not None else {}),
                            **({"captured_decision_path": captured_decision_path}
                                if captured_decision_path is not None else {}),
                            **({"progressive_enabled": False} if not progressive else {}),
                            "command_template": context_capture_read_command(Path(recovery["path"]), recovery["sha256"],
                                page_bytes=page_bytes, max_page_output_bytes=output_bytes)
                                + " --select '<supporting_material_pointer>/sources'",
                        } if presentation else None, consumed_pointers=state.get("consumed_pointers"),
                        observe_first_read=progressive)
                except (EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
                    if captured_decision_path is not None and isinstance(payload.get("replan_read_path"), str):
                        raise QuotaDecisionTransportError(
                            "Saved context presentation could not be prepared. Read the original capture and do not rerun admission."
                        ) from exc
                    return NativeContextPreparation(payload)
                if presentation and transaction["projected"]:
                    recovery = capture_context_projection(transaction["context"], directory=capture_directory,
                        filename="required-context.json", read_guidance=CONTEXT_CAPTURE_PAGE_GUIDANCE)
                    publish_recovery(recovery)
                result: dict[str, Any] = transaction["delivery"] if presentation else transaction
                read_sections = transaction["read_sections"] if progressive else {}
                result["recovery"] = recovery
                if result.get("continuity_basis") == "captured_file" and result.get("mode") == "delta":
                    result["baseline_reference"] = request["baseline_reference"]
                if result.get("schema_version") == "context_delivery_v1":
                    state["recovery"]["target"] = result["target"]
                    state["recovery"]["continuity_basis"] = "captured_file"
                    if result.get("continuity_basis") == "captured_file" and result.get("mode") == "delta":
                        base = request["baseline_reference"]
                        state["recovery"]["comparison_base"] = {
                            "path": base["path"], "sha256": base["sha256"], "target": result["base"]}
                    state["pending"].update(target=result["target"], ancestry=result["ancestry"],
                        full_read_sections=read_sections,
                        read_sections=read_sections if result["mode"] == "full" else {})
                    if plan := transaction.get("observed_first_read"):
                        from .native_first_read import observed_first_read_reference

                        reference = observed_first_read_reference(directory, result["target"], recovery,
                            transaction["context"], plan)
                        if reference is not None:
                            state["pending"]["observed_first_read"] = reference
                    _save(root, state)
                read_plan = transaction.get("initial_read_plan")
                if read_plan is not None:
                    read_plan = {**read_plan, "capture": {key: recovery[key] for key in ("path", "sha256")}}
                return NativeContextPreparation(result, read_plan)
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                if not recovery_committed:
                    _discard_recovery(root)
                return NativeContextPreparation(payload)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        # No safe fallback if the old selector cannot be invalidated under lock.
        raise QuotaDecisionTransportError(
            "Current context recovery could not be published or safely invalidated. "
            "Read the saved original decision and receipt; do not rerun admission."
        ) from exc
