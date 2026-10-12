from __future__ import annotations

import json
import platform
import shlex
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .control_plane.projects.identity import default_goal_id


DEFAULT_CODEX_BIN = "codex"
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_EXECUTOR_TIMEOUT_SECONDS = 30.0
DEFAULT_MIN_HUMAN_INPUT_IDLE_SECONDS = 5.0


HELP_COMMANDS = {
    "root": ("--help",),
    "exec": ("exec", "--help"),
    "resume": ("resume", "--help"),
}


def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().replace("_", "-").split())


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _visible_session_injection_detected(text: str) -> bool:
    has_session = _has_any(
        text,
        (
            "session",
            "conversation",
            "thread",
            "--session",
            "--conversation",
            "session-id",
            "session id",
        ),
    )
    has_attach = _has_any(
        text,
        (
            "attach to existing tui",
            "attach to active tui",
            "attach to an idle tui",
            "attach to existing session",
            "attach to active session",
            "inject into session",
            "inject into active session",
            "inject prompt into session",
            "send prompt to session",
            "send message to session",
            "send-message",
            "send message",
        ),
    )
    has_visible_turn = _has_any(text, ("prompt", "message", "stdin", "turn", "tui", "visible"))
    return has_session and has_attach and has_visible_turn


def _remote_control_surface_detected(text: str) -> bool:
    return _has_any(text, ("remote-control", "remote control")) and _has_any(
        text,
        ("--remote", "app server", "app-server"),
    )


def _visible_resume_supported(resume_help: str) -> bool:
    return "usage: codex resume" in resume_help and "[prompt]" in resume_help


def classify_codex_cli_session_surface(
    *,
    command_outputs: Mapping[str, str],
    command_errors: Mapping[str, str] | None = None,
    codex_cli_available: bool = True,
) -> dict[str, Any]:
    """Classify public Codex CLI help text without reading local sessions."""

    command_errors = command_errors or {}
    normalized_outputs = {name: _normalize(text) for name, text in command_outputs.items()}
    all_help = " ".join(normalized_outputs.values())
    root_help = normalized_outputs.get("root", "")
    exec_help = normalized_outputs.get("exec", "")
    resume_help = normalized_outputs.get("resume", "")

    exec_supported = " exec" in f" {root_help} " or bool(exec_help.strip())
    resume_supported = " resume" in f" {root_help} " or bool(resume_help.strip())
    session_handle_detected = resume_supported or _has_any(
        all_help,
        (
            "--session",
            "--conversation",
            "session-id",
            "session id",
            "conversation id",
            "resume",
        ),
    )
    same_tui_injection_detected = _visible_session_injection_detected(all_help)
    remote_control_surface_detected = _remote_control_surface_detected(all_help)
    visible_resume_supported = _visible_resume_supported(resume_help)
    safe_injection_supported = same_tui_injection_detected

    if safe_injection_supported:
        recommended_mode = "session_attached_visible_turn"
        automation_action = "try_visible_session_attach_with_idle_guard"
    elif remote_control_surface_detected or visible_resume_supported:
        recommended_mode = "visible_resume_or_remote_control_spike"
        automation_action = "prototype_visible_resume_or_remote_control_with_idle_guard"
    elif exec_supported:
        recommended_mode = "tui_bootstrap_only"
        automation_action = "ask_user_to_start_inside_codex_cli_tui"
    else:
        recommended_mode = "tui_bootstrap_only"
        automation_action = "ask_user_to_start_inside_codex_cli_tui"

    warnings: list[str] = []
    if session_handle_detected and not same_tui_injection_detected:
        warnings.append(
            "Resume/session help is not enough to claim same-open-TUI injection; require an explicit visible attach/inject primitive."
        )
    if (remote_control_surface_detected or visible_resume_supported) and not same_tui_injection_detected:
        warnings.append(
            "A visible resume or remote-control surface exists; prototype it behind an idle guard before calling it session-attached automation."
        )
    if exec_supported and not (safe_injection_supported or remote_control_surface_detected or visible_resume_supported):
        warnings.append(
            "Codex exec is available, but headless fallback is disabled for the default LoopX setup-then-goal bootstrap path."
        )
    if not codex_cli_available:
        warnings.append("Codex CLI was not available on PATH; classification used missing-command evidence.")
    if command_errors:
        warnings.append("Some probe commands returned errors; inspect command_errors before enabling automation.")

    return {
        "ok": True,
        "schema_version": "codex_cli_session_probe_v0",
        "codex_cli_available": codex_cli_available,
        "capabilities": {
            "exec_supported": exec_supported,
            "resume_supported": resume_supported,
            "session_handle_detected": session_handle_detected,
            "visible_resume_supported": visible_resume_supported,
            "remote_control_surface_detected": remote_control_surface_detected,
            "same_tui_injection_detected": same_tui_injection_detected,
            "safe_injection_supported": safe_injection_supported,
        },
        "recommended_mode": recommended_mode,
        "automation_action": automation_action,
        "boundary": {
            "help_only_probe": True,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
        },
        "command_errors": dict(command_errors),
        "warnings": warnings,
    }


def load_codex_cli_probe_fixture(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "command_outputs" in data:
        outputs = data["command_outputs"]
    else:
        outputs = data
    if not isinstance(outputs, dict):
        raise ValueError("Codex CLI probe fixture must be a JSON object")
    return {str(key): str(value) for key, value in outputs.items()}


def load_codex_cli_visible_session_proof_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Codex CLI visible session proof fixture must be a JSON object")
    return data


def load_codex_cli_runtime_idle_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Codex CLI runtime idle fixture must be a JSON object")
    return data


def load_codex_cli_first_response_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Codex CLI first-response fixture must be a JSON object")
    return data


def probe_human_input_idle_seconds(*, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Read a coarse local human-input idle metric without touching Codex state.

    On macOS this reads IOHIDSystem's HIDIdleTime counter. The value says only
    how long the machine has been idle since the last keyboard/mouse event; it
    does not read typed text, terminal buffers, Codex transcripts, session
    files, stdout/stderr, or credentials.
    """

    system = platform.system().lower()
    if system != "darwin":
        return {
            "ok": False,
            "source": "unsupported_platform",
            "platform": system or "unknown",
            "error": "human_input_idle_probe_only_implemented_for_macos",
        }
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {"ok": False, "source": "macos_hid_idle_time", "error": "ioreg_not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "source": "macos_hid_idle_time", "error": "timeout"}
    if result.returncode != 0:
        return {"ok": False, "source": "macos_hid_idle_time", "error": f"exit_{result.returncode}"}
    for line in result.stdout.splitlines():
        if "HIDIdleTime" not in line:
            continue
        raw_value = line.split("=", 1)[-1].strip()
        try:
            return {
                "ok": True,
                "source": "macos_hid_idle_time",
                "platform": "darwin",
                "human_input_idle_seconds": int(raw_value) / 1_000_000_000,
            }
        except ValueError:
            return {
                "ok": False,
                "source": "macos_hid_idle_time",
                "error": "unparseable_hid_idle_time",
            }
    return {"ok": False, "source": "macos_hid_idle_time", "error": "hid_idle_time_not_found"}


def run_codex_cli_session_probe(
    *,
    codex_bin: str = DEFAULT_CODEX_BIN,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    fixture: Path | None = None,
) -> dict[str, Any]:
    if fixture:
        outputs = load_codex_cli_probe_fixture(fixture)
        payload = classify_codex_cli_session_surface(
            command_outputs=outputs,
            codex_cli_available=True,
        )
        payload["source"] = "fixture"
        return payload

    outputs: dict[str, str] = {}
    errors: dict[str, str] = {}
    available = True
    for name, extra_args in HELP_COMMANDS.items():
        try:
            result = subprocess.run(
                [codex_bin, *extra_args],
                check=False,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            available = False
            errors[name] = "codex_cli_not_found"
            break
        except subprocess.TimeoutExpired:
            errors[name] = "timeout"
            continue
        text = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if result.returncode != 0:
            errors[name] = f"exit_{result.returncode}"
        outputs[name] = text

    payload = classify_codex_cli_session_surface(
        command_outputs=outputs,
        command_errors=errors,
        codex_cli_available=available,
    )
    payload["source"] = "real_help"
    payload["codex_bin"] = codex_bin
    payload["timeout_seconds"] = timeout_seconds
    return payload


def _shell_arg(value: str) -> str:
    return shlex.quote(value)


def _nested_bool(payload: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return False
        current = current.get(key)
    return current is True


def _nested_false(payload: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return False
        current = current.get(key)
    return current is False


VISIBLE_SESSION_PROOF_REQUIRED_TRUE_CHECKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("user_opt_in", ("user_opt_in",), "user explicitly opted into this proof"),
    ("quota_guard_passed", ("quota_guard", "passed"), "quota should-run allowed this proof path"),
    ("idle_no_human_typing", ("idle_guard", "no_active_human_typing"), "idle guard saw no active human typing"),
    ("idle_no_running_turn", ("idle_guard", "no_running_turn"), "idle guard saw no running Codex turn"),
    ("idle_checked_before_prompt", ("idle_guard", "checked_before_prompt"), "idle guard ran before the visible prompt"),
    ("visible_to_user", ("turn_visibility", "visible_to_user"), "the steering turn was visible to the user"),
    ("visible_prompt_public_safe", ("turn_visibility", "prompt_public_safe"), "the visible prompt was public-safe"),
    ("user_can_interrupt", ("interruptibility", "user_can_interrupt"), "the user can interrupt the turn"),
    ("manual_takeover_available", ("interruptibility", "manual_takeover_available"), "manual takeover remains available"),
    ("compact_writeback_planned", ("writeback", "compact_evidence_planned"), "compact evidence writeback is planned before quota spend"),
)


VISIBLE_SESSION_PROOF_REQUIRED_FALSE_CHECKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("no_raw_transcript_read", ("boundary", "reads_raw_transcripts"), "raw transcripts were not read"),
    ("no_session_files_read", ("boundary", "reads_session_files"), "session files were not read"),
    ("no_credentials_read", ("boundary", "reads_credentials"), "credentials were not read"),
    ("no_hidden_session_mutation", ("boundary", "mutates_hidden_session_state"), "hidden session state was not mutated"),
    ("no_quota_spend_before_writeback", ("boundary", "spends_quota_before_writeback"), "quota was not spent before writeback"),
)


RUNTIME_IDLE_REQUIRED_TRUE_CHECKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("idle_no_human_typing", ("idle_guard", "no_active_human_typing"), "no active human typing was observed"),
    ("idle_no_running_turn", ("idle_guard", "no_running_turn"), "no running Codex turn was observed"),
    ("idle_checked_before_prompt", ("idle_guard", "checked_before_prompt"), "idle check ran before any visible prompt"),
    ("visible_to_user", ("turn_visibility", "visible_to_user"), "the target turn remains visible to the user"),
    ("user_can_interrupt", ("interruptibility", "user_can_interrupt"), "the user can interrupt the turn"),
    ("manual_takeover_available", ("interruptibility", "manual_takeover_available"), "manual takeover remains available"),
)


RUNTIME_IDLE_REQUIRED_FALSE_CHECKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("no_raw_transcript_read", ("boundary", "reads_raw_transcripts"), "raw transcripts were not read"),
    ("no_session_files_read", ("boundary", "reads_session_files"), "session files were not read"),
    ("no_stdout_stderr_read", ("boundary", "reads_stdout_stderr"), "stdout/stderr streams were not read"),
    ("no_credentials_read", ("boundary", "reads_credentials"), "credentials were not read"),
    ("no_hidden_session_mutation", ("boundary", "mutates_hidden_session_state"), "hidden session state was not mutated"),
)


FIRST_RESPONSE_REQUIRED_TRUE_CHECKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("manual_or_visible_delivery", ("prompt_delivery", "manual_or_visible_delivery"), "the start message was delivered through a visible TUI path"),
    ("prompt_public_safe", ("prompt_delivery", "prompt_public_safe"), "the delivered prompt was public-safe"),
    ("goal_id_visible", ("first_response", "goal_id_visible"), "the first response showed the current goal id"),
    ("user_gate_or_none_visible", ("first_response", "user_gate_or_none_visible"), "the first response showed the concrete user gate or that none blocks the path"),
    ("top_user_todo_or_none_visible", ("first_response", "top_user_todo_or_none_visible"), "the first response showed the top user todo or that none exists"),
    ("top_agent_todo_visible", ("first_response", "top_agent_todo_visible"), "the first response showed the selected agent todo"),
    ("next_safe_action_visible", ("first_response", "next_safe_action_visible"), "the first response showed the next safe action"),
    ("bounded_segment_started_or_blocker_written", ("first_response", "bounded_segment_started_or_blocker_written"), "the first response either started one bounded segment or wrote a precise blocker"),
    ("user_can_interrupt", ("interruptibility", "user_can_interrupt"), "the user can interrupt the visible TUI path"),
    ("manual_takeover_available", ("interruptibility", "manual_takeover_available"), "manual takeover remains available"),
    ("compact_evidence_planned", ("writeback", "compact_evidence_planned"), "compact evidence writeback is planned before quota spend"),
    ("quota_spend_after_writeback_only", ("writeback", "quota_spend_after_writeback_only"), "quota spend happens only after validated writeback"),
)


FIRST_RESPONSE_REQUIRED_FALSE_CHECKS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("no_argv_prompt", ("prompt_delivery", "argv_prompt_used"), "the start prompt was not passed as a process argv prompt"),
    ("no_raw_transcript_read", ("boundary", "reads_raw_transcripts"), "raw transcripts were not read"),
    ("no_session_files_read", ("boundary", "reads_session_files"), "session files were not read"),
    ("no_stdout_stderr_read", ("boundary", "reads_stdout_stderr"), "stdout/stderr streams were not read"),
    ("no_credentials_read", ("boundary", "reads_credentials"), "credentials were not read"),
    ("no_hidden_session_mutation", ("boundary", "mutates_hidden_session_state"), "hidden session state was not mutated"),
    ("no_quota_spend_before_writeback", ("boundary", "spends_quota_before_writeback"), "quota was not spent before writeback"),
)


def build_codex_cli_runtime_idle_observation_payload(
    *,
    observed_surface: str,
    turn_state: str,
    human_input_idle_seconds: float | None,
    min_human_input_idle_seconds: float,
    checked_before_prompt: bool,
    visible_to_user: bool,
    user_can_interrupt: bool,
    manual_takeover_available: bool,
    probe_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert public-safe local idle observations into detector input."""

    idle_seconds_known = human_input_idle_seconds is not None
    no_active_human_typing = bool(
        idle_seconds_known and human_input_idle_seconds >= min_human_input_idle_seconds
    )
    no_running_turn = turn_state == "idle"
    return {
        "observed_surface": observed_surface,
        "source": "local_runtime_observation",
        "runtime_observation": {
            "schema_version": "codex_cli_runtime_idle_observation_v0",
            "human_input_idle_seconds": human_input_idle_seconds,
            "min_human_input_idle_seconds": min_human_input_idle_seconds,
            "human_input_idle_source": (probe_result or {}).get("source") or "provided",
            "human_input_idle_probe_ok": (probe_result or {}).get("ok"),
            "turn_state": turn_state,
            "turn_state_source": "public_safe_local_observation",
            "cannot_prove_unknown_turn_state": turn_state == "unknown",
        },
        "idle_guard": {
            "no_active_human_typing": no_active_human_typing,
            "no_running_turn": no_running_turn,
            "checked_before_prompt": checked_before_prompt,
        },
        "turn_visibility": {"visible_to_user": visible_to_user},
        "interruptibility": {
            "user_can_interrupt": user_can_interrupt,
            "manual_takeover_available": manual_takeover_available,
        },
        "boundary": {
            "reads_raw_transcripts": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "reads_credentials": False,
            "mutates_hidden_session_state": False,
        },
    }


def build_codex_cli_runtime_idle_detector(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    idle_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate public-safe runtime idle evidence before a visible later turn.

    This is not a Codex session inspector. It accepts either a reproducible
    public-safe fixture or a narrow local observation payload. Both paths prove
    the two product-critical facts for later visible turns: the user is not
    typing, and Codex is not already running a turn. The detector intentionally
    does not read transcripts, session files, stdout/stderr, credentials, or
    hidden runtime state.
    """

    resolved_project = str(project.expanduser())
    resolved_goal_id = goal_id or default_goal_id(project)
    required_fixture_shape = {
        "observed_surface": "visible_resume_prompt | remote_control_visible_prompt | same_tui_visible_attach | codex_cli_tui_visible_window",
        "idle_guard": {
            "no_active_human_typing": True,
            "no_running_turn": True,
            "checked_before_prompt": True,
        },
        "turn_visibility": {"visible_to_user": True},
        "interruptibility": {
            "user_can_interrupt": True,
            "manual_takeover_available": True,
        },
        "boundary": {
            "reads_raw_transcripts": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "reads_credentials": False,
            "mutates_hidden_session_state": False,
        },
    }
    if idle_payload is None:
        return {
            "ok": False,
            "schema_version": "codex_cli_runtime_idle_detector_v0",
            "project": resolved_project,
            "goal_id": resolved_goal_id,
            "agent_id": agent_id,
            "cli_bin": cli_bin,
            "source": "idle_evidence_required",
            "decision": "runtime_idle_evidence_required",
            "approved_for_visible_later_turn": False,
            "recommended_action": "capture public-safe runtime idle evidence before steering a later visible Codex CLI turn",
            "required_fixture_shape": required_fixture_shape,
            "checks": [],
            "failures": ["missing_runtime_idle_evidence"],
            "boundary": {
                "fixture_only": False,
                "public_safe_fixture_supported": True,
                "local_observation_adapter_supported": True,
                "runs_codex": False,
                "reads_raw_transcripts": False,
                "reads_session_files": False,
                "reads_stdout_stderr": False,
                "reads_credentials": False,
                "mutates_codex_session": False,
                "spends_loopx_quota": False,
            },
        }

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for key, path, description in RUNTIME_IDLE_REQUIRED_TRUE_CHECKS:
        passed = _nested_bool(idle_payload, path)
        checks.append({"key": key, "required": True, "passed": passed, "description": description})
        if not passed:
            failures.append(key)
    for key, path, description in RUNTIME_IDLE_REQUIRED_FALSE_CHECKS:
        passed = _nested_false(idle_payload, path)
        checks.append({"key": key, "required": False, "passed": passed, "description": description})
        if not passed:
            failures.append(key)

    observed_surface = str(idle_payload.get("observed_surface") or "unknown")
    supported_surface = observed_surface in {
        "visible_resume_prompt",
        "remote_control_visible_prompt",
        "same_tui_visible_attach",
        "codex_cli_tui_visible_window",
    }
    checks.append(
        {
            "key": "supported_observed_surface",
            "required": sorted(
                [
                    "codex_cli_tui_visible_window",
                    "remote_control_visible_prompt",
                    "same_tui_visible_attach",
                    "visible_resume_prompt",
                ]
            ),
            "actual": observed_surface,
            "passed": supported_surface,
            "description": "idle evidence was captured from a visible Codex CLI surface",
        }
    )
    if not supported_surface:
        failures.append("unsupported_observed_surface")

    approved = not failures
    if approved:
        decision = "runtime_idle_detector_passed"
        recommended_action = "allow a later visible Codex CLI turn only after a fresh quota guard"
    else:
        decision = "runtime_idle_detector_incomplete"
        recommended_action = "keep the TUI bootstrap path visible and do not steer a later turn yet"

    source = str(idle_payload.get("source") or "idle_fixture")
    local_observation = source == "local_runtime_observation"
    return {
        "ok": True,
        "schema_version": "codex_cli_runtime_idle_detector_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "source": source,
        "observed_surface": observed_surface,
        "runtime_observation": idle_payload.get("runtime_observation"),
        "decision": decision,
        "approved_for_visible_later_turn": approved,
        "recommended_action": recommended_action,
        "checks": checks,
        "failures": failures,
        "boundary": {
            "fixture_only": not local_observation,
            "public_safe_fixture_supported": True,
            "local_observation_adapter_supported": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "reads_credentials": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
        },
    }


def build_codex_cli_visible_session_proof(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    proof_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate a public-safe proof packet for visible Codex CLI steering.

    This command intentionally does not run Codex or inspect local session
    state. It validates whether a separately captured public-safe observation
    is strong enough to treat resume/remote-control as a candidate for future
    same-session automation.
    """

    resolved_project = str(project.expanduser())
    resolved_goal_id = goal_id or default_goal_id(project)
    if proof_payload is None:
        return {
            "ok": False,
            "schema_version": "codex_cli_visible_session_proof_v0",
            "project": resolved_project,
            "goal_id": resolved_goal_id,
            "agent_id": agent_id,
            "decision": "proof_fixture_required",
            "approved_for_same_session_automation": False,
            "recommended_action": "capture a public-safe proof fixture; do not run same-session automation yet",
            "required_fixture_shape": {
                "user_opt_in": True,
                "quota_guard": {"passed": True},
                "idle_guard": {
                    "no_active_human_typing": True,
                    "no_running_turn": True,
                    "checked_before_prompt": True,
                },
                "turn_visibility": {
                    "visible_to_user": True,
                    "prompt_public_safe": True,
                },
                "interruptibility": {
                    "user_can_interrupt": True,
                    "manual_takeover_available": True,
                },
                "boundary": {
                    "reads_raw_transcripts": False,
                    "reads_session_files": False,
                    "reads_credentials": False,
                    "mutates_hidden_session_state": False,
                    "spends_quota_before_writeback": False,
                },
                "writeback": {"compact_evidence_planned": True},
            },
            "boundary": {
                "fixture_only": True,
                "runs_codex": False,
                "reads_raw_transcripts": False,
                "reads_credentials": False,
                "reads_session_files": False,
                "mutates_codex_session": False,
                "spends_loopx_quota": False,
            },
            "checks": [],
            "failures": ["missing_proof_fixture"],
        }

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for key, path, description in VISIBLE_SESSION_PROOF_REQUIRED_TRUE_CHECKS:
        passed = _nested_bool(proof_payload, path)
        checks.append({"key": key, "required": True, "passed": passed, "description": description})
        if not passed:
            failures.append(key)
    for key, path, description in VISIBLE_SESSION_PROOF_REQUIRED_FALSE_CHECKS:
        passed = _nested_false(proof_payload, path)
        checks.append({"key": key, "required": False, "passed": passed, "description": description})
        if not passed:
            failures.append(key)

    observed_surface = str(proof_payload.get("observed_surface") or "unknown")
    supported_surface = observed_surface in {
        "visible_resume_prompt",
        "remote_control_visible_prompt",
        "same_tui_visible_attach",
    }
    if not supported_surface:
        failures.append("unsupported_observed_surface")
    checks.append(
        {
            "key": "supported_observed_surface",
            "required": sorted(
                [
                    "remote_control_visible_prompt",
                    "same_tui_visible_attach",
                    "visible_resume_prompt",
                ]
            ),
            "actual": observed_surface,
            "passed": supported_surface,
            "description": "observed surface is a visible Codex CLI steering path",
        }
    )

    approved = not failures
    if approved:
        decision = "visible_session_proof_passed"
        recommended_action = (
            "allow a future opt-in driver spike to use this visible surface behind quota and idle guards"
        )
    else:
        decision = "visible_session_proof_incomplete"
        recommended_action = (
            "keep TUI bootstrap primary; do not treat this as same-session automation"
        )

    return {
        "ok": True,
        "schema_version": "codex_cli_visible_session_proof_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "source": "proof_fixture",
        "observed_surface": observed_surface,
        "decision": decision,
        "approved_for_same_session_automation": approved,
        "recommended_action": recommended_action,
        "checks": checks,
        "failures": failures,
        "boundary": {
            "fixture_only": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
        },
    }
