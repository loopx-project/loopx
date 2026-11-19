from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
from typing import cast

from .agent_registry import (
    agent_profile_for_goal,
    registered_agent_ids_for_goal,
)
from .control_plane.agents.legacy_migration import (
    completed_peer_agent_runtime_migration,
    legacy_agent_hierarchy_present,
    peer_agent_runtime_migration_completed,
    peer_agent_runtime_migration_id,
)
from .control_plane.heartbeat.builder import build_heartbeat_prompt
from .control_plane.todos.contract import normalize_required_capabilities
from .execution_profile import execution_profile_turn_granularity
from .history import load_registry
from .paths import global_registry_path, resolve_runtime_root, select_default_runtime_root
from .registry import registry_goals, resolve_state_file

Payload = dict[str, object]
PayloadView = Mapping[str, object]

DEFAULT_UPGRADE_MODES = ("thin",)
SHOULD_RUN_FALSE_RE = re.compile(
    r"should[_\s-]*run`?\s*(?:=|is|:)?\s*`?false",
    re.IGNORECASE,
)
SAFE_BYPASS_RE = re.compile(
    r"safe[_\s-]*bypass|outcome[_\s-]*floor[_\s-]*recovery|recovery[_\s-]*delivery[_\s-]*allowed",
    re.IGNORECASE,
)
SHOULD_RUN_FALSE_HARD_STOP_RE = re.compile(
    r"no\s+(?:implementation|work|delivery|spend)|"
    r"do\s+not\s+(?:run|do|execute|append|spend|work)|"
    r"quiet(?:ly)?\s+(?:skip|no-op)|"
    r"skip\s+(?:delivery|work|compute)",
    re.IGNORECASE,
)
PROJECT_POLICY_MARKERS = (
    "Current controller policy:",
    "Primary stability objective:",
    "Current controller policy",
)
AVAILABLE_CAPABILITY_PATTERN = re.compile(
    r"--available-capability(?:=|\s+)([A-Za-z][A-Za-z0-9_:-]{0,63})"
)
STAGE_DEFERRED_ATTENTION_STATUSES = {
    "stage_deferred_not_installed",
}
STAGE_DEFERRED_ADAPTER_STATUSES = {
    "planned",
}
HOST_LOOP_UPDATE_STATUSES = {"missing", "partial", "stale", "unknown", "error", "unavailable"}
_AUTOMATION_PARSE_ERROR_LIMIT = 20
_SUMMARY_MARKDOWN_FIELDS = (
    "ready_for_default_promotion", "managed_goal_count", "current_prompt_count",
    "stale_prompt_count", "unknown_prompt_count", "not_installed_prompt_count",
    "stage_deferred_goal_count", "installed_manifest_source", "installed_manifest_entry_count",
    "installed_manifest_parse_error_count", "installed_manifest_has_task_body",
    "installed_prompt_policy_warning_count", "installed_prompt_policy_warning_prompt_count",
    "host_loop_activated_goal_count", "host_loop_missing_goal_count",
    "peer_runtime_automation_migration_count",
)
_MANIFEST_MARKDOWN_FIELDS = ("available", "path", "reason", "parse_error_count", "parse_errors_complete")
_PROPAGATION_MARKDOWN_FIELDS = (
    "schema_version", "ready_for_default_promotion", "managed_target_count",
    "deferred_target_count", "update_count", "deferred_install_count", "recommended_action",
)
_MIGRATION_MARKDOWN_FIELDS = (
    "migration_id", "delivery_semantics", "host_update_required_once", "completion_command",
)


def _dict_view(value: object) -> PayloadView:
    return cast(PayloadView, value) if isinstance(value, dict) else {}


def _dict_list(value: object) -> list[Payload]:
    return [cast(Payload, item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _str_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def _int_value(value: object) -> int:
    if not value:
        return 0
    return int(value) if isinstance(value, (int, float, str, bytes, bytearray)) else int(str(value))


def _unavailable_policy_audit(reason: str, *, status: str = "unavailable") -> Payload:
    return {
        "available": False,
        "status": status,
        "warning_count": 0,
        "warnings": [],
        "reason": reason,
    }


def _first_match(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _bullet_fields(mapping: PayloadView, keys: tuple[str, ...]) -> list[str]:
    return [f"- {key}: `{mapping.get(key)}`" for key in keys]


def prompt_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def goal_heartbeat_prompt(
    goal: Payload,
    *,
    cli_bin: str,
    mode: str,
    agent_id: str | None,
    available_capabilities: Sequence[str] | None = None,
) -> Payload:
    """Build the prompt one Goal's ``mode`` heartbeat target installs.

    The prompt inputs come from the Goal's own registration (state file, agent
    profile, registered agents, turn granularity, Reward Memory policy), so the
    upgrade plan and every release check that must agree with it byte-for-byte
    have to build the prompt through this single owner. Rebuilding the argument
    list at a second call site is how a digest check drifts the moment the
    product gains one more input.
    """
    goal_id = str(goal.get("id") or "")
    repo = Path(str(goal.get("repo") or ".")).expanduser()
    raw_state_file = goal.get("state_file")
    state_file = resolve_state_file(repo, str(raw_state_file) if raw_state_file else None)
    registered_agents = registered_agent_ids_for_goal(goal)
    reward_memory_enabled = False
    if agent_id is not None:
        from .capabilities.reward_memory.configuration import (
            reward_memory_automatic_ingest_for_agent,
        )

        reward_memory_enabled = reward_memory_automatic_ingest_for_agent(
            goal, agent_id
        )
    normalized_capabilities = (
        available_capabilities
        if isinstance(available_capabilities, (list, tuple))
        else None
        if available_capabilities is None
        else list(available_capabilities)
    )
    raw_profile = goal.get("execution_profile")
    return cast(
        Payload,
        build_heartbeat_prompt(
            goal_id=goal_id,
            active_state=None,
            active_state_source="registry",
            resolved_active_state=state_file,
            compact=mode == "compact",
            brief=mode == "brief",
            thin=mode == "thin",
            cli_bin=cli_bin,
            agent_id=agent_id,
            agent_profile=agent_profile_for_goal(goal, agent_id),
            registered_agents=registered_agents or None,
            available_capabilities=normalized_capabilities,
            runtime_profile="codex_app_heartbeat",
            turn_granularity=execution_profile_turn_granularity(
                cast(Payload, raw_profile) if isinstance(raw_profile, dict) else None
            ),
            reward_memory_enabled=reward_memory_enabled,
        ),
    )


def prompt_summary(prompt: PayloadView, mode: str) -> Payload:
    task_body = str(prompt.get("task_body") or "")
    command = prompt.get("expanded_prompt_command") if mode == "full" else prompt.get(f"{mode}_prompt_command")
    interface_budget = _dict_view(prompt.get("interface_budget"))
    return {
        "mode": mode,
        "sha256": prompt_digest(task_body),
        "char_count": len(task_body),
        "line_count": len(task_body.splitlines()),
        "interface_budget": dict(interface_budget) if interface_budget else {},
        "within_interface_budget": interface_budget.get("within_budget"),
        "interface_budget_char_count": interface_budget.get("budget_char_count"),
        "interface_budget_max_chars": interface_budget.get("max_chars"),
        "command": command,
    }


def prompt_policy_audit(prompt: str | None) -> Payload:
    if not isinstance(prompt, str) or not prompt.strip():
        return _unavailable_policy_audit("installed prompt body is unavailable")

    warnings: list[dict[str, str]] = []
    should_run_match = SHOULD_RUN_FALSE_RE.search(prompt)
    safe_bypass_match = SAFE_BYPASS_RE.search(prompt)
    if should_run_match and (safe_bypass_match is None or should_run_match.start() < safe_bypass_match.start()):
        window_end = safe_bypass_match.start() if safe_bypass_match else min(len(prompt), should_run_match.start() + 900)
        window = prompt[should_run_match.start():window_end]
        if SHOULD_RUN_FALSE_HARD_STOP_RE.search(window):
            warnings.append(
                {
                    "kind": "should_run_false_before_safe_bypass",
                    "severity": "warning",
                    "recommended_action": "regenerate the installed heartbeat prompt so recovery/safe-bypass handling precedes generic should_run=false skip handling",
                }
            )

    if any(marker in prompt for marker in PROJECT_POLICY_MARKERS):
        warnings.append(
            {
                "kind": "embedded_project_policy",
                "severity": "warning",
                "recommended_action": "move project-specific policy into registry, active state, status, or review-packet payloads and reinstall a thin generated heartbeat prompt",
            }
        )

    if "--active-state" in prompt:
        warnings.append(
            {
                "kind": "pinned_active_state_argument",
                "severity": "warning",
                "recommended_action": "omit --active-state for connected goals so the installed heartbeat resolves the active state from the registry",
            }
        )

    return {
        "available": True,
        "status": "warning" if warnings else "clean",
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def installed_prompt_policy_audit(entry: PayloadView | None) -> Payload:
    if not entry:
        return _unavailable_policy_audit("installed prompt entry is unavailable")
    audit = entry.get("prompt_policy_audit")
    if isinstance(audit, dict):
        return cast(Payload, audit)
    task_body = entry.get("task_body")
    return prompt_policy_audit(task_body if isinstance(task_body, str) else None)


def load_installed_manifest(path: Path | None) -> Payload:
    if path is None:
        return load_codex_app_automation_manifest()
    if not path.exists():
        return {
            "available": False,
            "path": str(path),
            "entries": [],
            "reason": "installed automation manifest does not exist",
        }
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw_entries: object = raw
    elif isinstance(raw, dict):
        raw_entries = raw.get("automations") or raw.get("entries") or []
    else:
        raw_entries = []
    return {
        "available": True,
        "path": str(path),
        "entries": _dict_list(raw_entries),
    }


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def parse_automation_toml(path: Path) -> Payload:
    return cast(Payload, tomllib.loads(path.read_text(encoding="utf-8")))


def infer_goal_id_from_prompt(prompt: str) -> str | None:
    return _first_match(
        (
            r"Advance\s+`([^`]+)`\s+from\b",
            r"Advance\s+`([^`]+)`\s+using\b",
            r"--goal-id\s+([A-Za-z0-9_.:-]+)",
            r"goal_id:\s*`([^`]+)`",
        ),
        prompt,
    )


def infer_agent_id_from_prompt(prompt: str) -> str | None:
    return _first_match(
        (
            r"Agent:\s*`([^`]+)`",
            r"--agent-id\s+([A-Za-z0-9_.:-]+)",
        ),
        prompt,
    )


def infer_prompt_mode(prompt: str) -> str:
    if "compact LoopX heartbeat body" in prompt:
        return "compact"
    if "Brief installed LoopX heartbeat" in prompt:
        return "brief"
    if "Keep the heartbeat thin." in prompt or "from the registry-declared active state" in prompt:
        return "thin"
    return DEFAULT_UPGRADE_MODES[0]


def infer_available_capabilities_from_prompt(prompt: str) -> list[str]:
    return normalize_required_capabilities(AVAILABLE_CAPABILITY_PATTERN.findall(prompt))


def load_codex_app_automation_manifest(root: Path | None = None) -> Payload:
    from .control_plane.heartbeat.automation_upgrade import bootstrap_binding

    home = root or codex_home()
    automations_root = home / "automations"
    if not automations_root.exists():
        return {
            "available": False,
            "path": str(automations_root),
            "entries": [],
            "reason": "no installed automation manifest provided and Codex App automations directory does not exist",
            "source": "codex_app_automations",
            "parse_error_count": 0,
            "parse_errors": [],
            "parse_errors_complete": True,
        }

    entries: list[Payload] = []
    parse_errors: list[dict[str, str]] = []
    parse_error_count = 0
    for path in sorted(automations_root.glob("*/automation.toml")):
        parse_error_reason: str | None = None
        try:
            automation = parse_automation_toml(path)
        except OSError:
            parse_error_reason = "unreadable"
        except UnicodeError:
            parse_error_reason = "invalid_utf8"
        except tomllib.TOMLDecodeError:
            parse_error_reason = "invalid_toml"
        if parse_error_reason is not None:
            parse_error_count += 1
            if len(parse_errors) < _AUTOMATION_PARSE_ERROR_LIMIT:
                parse_errors.append({"automation_id": path.parent.name, "reason": parse_error_reason})
            continue
        if automation.get("kind") != "heartbeat":
            continue
        prompt = automation.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            continue
        goal_id = infer_goal_id_from_prompt(prompt)
        if not goal_id:
            continue
        agent_id = infer_agent_id_from_prompt(prompt)
        status = str(automation.get("status") or "ACTIVE")
        binding = bootstrap_binding(prompt)
        runtime_thin_bootstrap: Payload | None = (
            {**{str(key): value for key, value in binding.items()}, "registry": str(binding["registry"])}
            if binding
            else None
        )
        entries.append(
            {
                "automation_id": str(automation.get("id") or path.parent.name),
                "goal_id": goal_id,
                "agent_id": agent_id,
                "mode": infer_prompt_mode(prompt),
                "prompt_sha256": prompt_digest(prompt),
                "char_count": len(prompt),
                "line_count": len(prompt.splitlines()),
                "prompt_policy_audit": prompt_policy_audit(prompt),
                "available_capabilities": infer_available_capabilities_from_prompt(prompt),
                "rrule": str(automation.get("rrule") or "").strip(),
                "target_thread_id": str(automation.get("target_thread_id") or "").strip(),
                "status": status,
                "installed": status.upper() != "DELETED",
                "source": "codex_app_automation_toml",
                "runtime_thin_bootstrap": runtime_thin_bootstrap,
                "path": str(path),
            }
        )

    return {
        "available": True,
        "path": str(automations_root),
        "entries": entries,
        "source": "codex_app_automations",
        "reason": (
            None
            if entries
            else (
                "no readable LoopX heartbeat automations discovered"
                if parse_error_count
                else "no LoopX heartbeat automations discovered"
            )
        ),
        "parse_error_count": parse_error_count,
        "parse_errors": parse_errors,
        "parse_errors_complete": parse_error_count == len(parse_errors),
    }


def resolve_codex_app_automation_rrule(
    *,
    goal_id: str,
    agent_id: str | None = None,
    thread_id: str | None = None,
    root: Path | None = None,
    automation_id: str | None = None,
) -> Payload:
    """Resolve one active Codex App heartbeat RRULE without exposing its body."""

    manifest = load_codex_app_automation_manifest(root)
    if not manifest.get("available"):
        return {"available": False, "reason": manifest.get("reason")}
    safe_goal_id = str(goal_id or "").strip()
    safe_agent_id = str(agent_id or "").strip()
    safe_thread_id = str(thread_id or os.environ.get("CODEX_THREAD_ID") or "").strip()
    candidates = [
        entry
        for entry in _dict_list(manifest.get("entries"))
        if str(entry.get("goal_id") or "") == safe_goal_id
        and (not safe_agent_id or str(entry.get("agent_id") or "") == safe_agent_id)
        and (automation_id or entry.get("installed") is True)
        and (not automation_id or entry.get("automation_id") == automation_id)
        and (
            not safe_thread_id
            or str(entry.get("target_thread_id") or "") == safe_thread_id
        )
    ]
    parse_error_count = _int_value(manifest.get("parse_error_count"))
    # An unreadable file can hide another matching heartbeat.
    if len(candidates) != 1 or parse_error_count:
        reason = "no matching active Codex App heartbeat RRULE"
        if len(candidates) > 1:
            reason = "Codex App heartbeat RRULE is ambiguous"
        elif parse_error_count:
            reason = "Codex App heartbeat manifest could not be fully read"
        result: Payload = {
            "available": False,
            "reason": reason,
            "candidate_count": len(candidates),
        }
        if parse_error_count:
            result["manifest_parse_error_count"] = parse_error_count
            result["manifest_parse_errors"] = manifest.get("parse_errors") or []
            result["manifest_parse_errors_complete"] = manifest.get("parse_errors_complete") is True
        return result
    entry = candidates[0]
    status = str(entry.get("status") or "").upper()
    if status in ("PAUSED", "DELETED"):
        return {"available": False, "reason": f"Codex App heartbeat is {status.lower()}"}
    if status != "ACTIVE" or not str(entry.get("rrule") or "").strip():
        return {"available": False, "reason": "Codex App heartbeat has no readable active schedule"}
    return {
        "available": True,
        "rrule": entry["rrule"],
        "automation_id": str(entry.get("automation_id") or "").strip(),
        "source": "codex_app_automation_manifest",
    }


def installed_entry_digest(entry: PayloadView) -> str | None:
    for key in ("prompt_sha256", "task_body_sha256", "sha256"):
        value = entry.get(key)
        if value:
            return str(value)
    task_body = entry.get("task_body")
    return prompt_digest(task_body) if isinstance(task_body, str) else None


def installed_entry_available_capabilities(entry: PayloadView | None) -> list[str]:
    if not entry:
        return []
    explicit = normalize_required_capabilities(entry.get("available_capabilities"))
    if explicit:
        return explicit
    task_body = entry.get("task_body")
    return infer_available_capabilities_from_prompt(task_body) if isinstance(task_body, str) else []


def entry_declares_not_installed(entry: PayloadView | None) -> bool:
    if not entry:
        return False
    status = str(entry.get("status") or "").replace("-", "_").lower()
    if status in {"not_installed", "no_automation", "uninstalled"}:
        return True
    return entry.get("installed") is False


def entry_key(entry: PayloadView) -> tuple[str, str, str]:
    return (
        str(entry.get("goal_id") or ""),
        str(entry.get("mode") or DEFAULT_UPGRADE_MODES[0]),
        str(entry.get("agent_id") or ""),
    )


def index_installed_entries(entries: Sequence[PayloadView]) -> dict[tuple[str, str, str], PayloadView]:
    indexed: dict[tuple[str, str, str], PayloadView] = {}
    for entry in entries:
        goal_id, mode, agent_id = entry_key(entry)
        if goal_id:
            indexed[(goal_id, mode, agent_id)] = entry
    return indexed


def prompt_target_key(mode: str, agent_id: str | None) -> str:
    return f"{mode}:{agent_id}" if agent_id else mode


def peer_runtime_upgrade_migration(
    goal: PayloadView,
    *,
    goal_id: str,
    installed: Mapping[str, PayloadView],
    generated_prompts: Mapping[str, PayloadView],
) -> Payload:
    if peer_agent_runtime_migration_completed(goal):
        completed = completed_peer_agent_runtime_migration(goal) or {}
        return {
            "schema_version": "peer_runtime_automation_migration_v1",
            "required": False,
            "status": "completed",
            "migration_id": completed.get("migration_id"),
        }
    if not legacy_agent_hierarchy_present(goal):
        return {
            "schema_version": "peer_runtime_automation_migration_v1",
            "required": False,
            "status": "not_required",
        }
    migration_id = peer_agent_runtime_migration_id(goal_id, goal)
    host_updates: list[Payload] = []
    for target, prompt_status in installed.items():
        if not (prompt_status.get("installed") is True and prompt_status.get("requires_update") is True):
            continue
        generated = generated_prompts.get(target) or {}
        host_updates.append(
            {
                "prompt_target": target,
                "agent_id": prompt_status.get("agent_id"),
                "automation_id": prompt_status.get("automation_id"),
                "prompt_command": generated.get("command"),
                "idempotency_key": migration_id,
            }
        )
    completion_command = (
        "loopx configure-goal "
        f"--goal-id {goal_id} "
        f"--ack-automation-prompt-migration {migration_id} --execute"
    )
    return {
        "schema_version": "peer_runtime_automation_migration_v1",
        "required": True,
        "status": "pending",
        "migration_id": migration_id,
        "delivery_semantics": "stable_idempotent_until_ack",
        "host_update_required_once": bool(host_updates),
        "host_updates": host_updates,
        "completion_command": completion_command,
        "ordered_steps": [
            "update each discovered host automation whose prompt is stale, using the "
            "stable migration_id as the idempotency key",
            "run completion_command once; it atomically records completion and removes "
            "legacy hierarchy fields",
            "rerun quota should-run with the same registered agent id",
        ],
    }


def build_loop_activation_summary(*, installed: Mapping[str, PayloadView]) -> Payload:
    targets_by_status: dict[str, list[str]] = {
        "current": [],
        "stale": [],
        "unknown": [],
        "not_installed": [],
    }
    for key, value in installed.items():
        bucket = targets_by_status.get(str(value.get("status") or ""))
        if bucket is not None:
            bucket.append(key)
    current_targets = targets_by_status["current"]
    stale_targets = targets_by_status["stale"]
    missing_targets = targets_by_status["unknown"]
    not_installed_targets = targets_by_status["not_installed"]
    target_count = len(installed)
    if target_count and len(current_targets) == target_count:
        status = "current"
    elif target_count and len(not_installed_targets) == target_count:
        status = "explicitly_not_installed"
    elif current_targets and (stale_targets or missing_targets or not_installed_targets):
        status = "partial"
    elif stale_targets:
        status = "stale"
    elif missing_targets:
        status = "missing"
    elif not_installed_targets:
        status = "explicitly_not_installed"
    else:
        status = "unknown"
    activated = status == "current"
    return {
        "schema_version": "loopx_host_loop_activation_v0",
        "host_surface": "codex_app_heartbeat",
        "status": status,
        "activated": activated,
        "target_count": target_count,
        "current_count": len(current_targets),
        "stale_count": len(stale_targets),
        "missing_count": len(missing_targets),
        "not_installed_count": len(not_installed_targets),
        "current_targets": current_targets,
        "stale_targets": stale_targets,
        "missing_targets": missing_targets,
        "not_installed_targets": not_installed_targets,
        "recommended_action": (
            "loop surface is active"
            if activated
            else "for Codex App, create or update the heartbeat automation from the generated scoped heartbeat-prompt; "
            "for Codex CLI TUI or Claude Code, verify their own host loop surface instead; "
            "do not claim LoopX setup complete until the active host surface is proven or a concrete host-tool gate is reported"
        ),
    }


def goal_adapter(goal: PayloadView) -> PayloadView:
    return _dict_view(goal.get("adapter"))


def goal_adapter_status(goal: PayloadView) -> str:
    return str(goal_adapter(goal).get("status") or "")


def goal_adapter_kind(goal: PayloadView) -> str | None:
    kind = goal_adapter(goal).get("kind")
    return str(kind) if kind else None


def goal_is_stage_deferred(goal: PayloadView) -> bool:
    attention_status = str(goal.get("attention_status") or "").replace("-", "_").lower()
    if attention_status in STAGE_DEFERRED_ATTENTION_STATUSES:
        return True
    adapter_status = goal_adapter_status(goal).replace("-", "_").lower()
    return adapter_status in STAGE_DEFERRED_ADAPTER_STATUSES


def stage_deferred_goal_summary(goal: PayloadView, state_file: Path | None) -> Payload:
    return {
        "goal_id": str(goal.get("id") or ""),
        "adapter_kind": goal_adapter_kind(goal),
        "adapter_status": goal_adapter_status(goal) or None,
        "status": goal.get("status"),
        "attention_status": goal.get("attention_status"),
        "repo": str(Path(str(goal.get("repo") or ".")).expanduser()),
        "state_file": str(state_file) if state_file else None,
        "state_file_exists": bool(state_file and state_file.exists()),
        "requires_update": False,
        "deferred_reason": "stage_deferred_until_operator_authorizes_heartbeat",
        "recommended_action": goal.get("recommended_action")
        or "do not install or update this heartbeat until the operator authorizes the stage",
    }


def managed_default_upgrade_target(goal: PayloadView) -> Payload:
    installed = _dict_view(goal.get("installed_prompts"))
    mode_summaries: list[Payload] = []
    statuses: set[str] = set()
    policy_warning_count = 0
    for mode, prompt_status in installed.items():
        if not isinstance(prompt_status, dict):
            continue
        prompt_view = cast(PayloadView, prompt_status)
        status = str(prompt_view.get("status") or "unknown")
        statuses.add(status)
        audit = _dict_view(prompt_view.get("prompt_policy_audit"))
        warning_count = _int_value(audit.get("warning_count"))
        policy_warning_count += warning_count
        mode_summaries.append(
            {
                "mode": mode,
                "status": status,
                "requires_update": bool(prompt_view.get("requires_update")),
                "automation_id": prompt_view.get("automation_id"),
                "policy_warning_count": warning_count,
            }
        )

    requires_update = bool(goal.get("requires_update"))
    if requires_update:
        action = "regenerate_installed_prompt"
        if policy_warning_count:
            reason = "installed prompt policy warnings must be cleared before default promotion"
        elif "unknown" in statuses:
            reason = "installed prompt is missing from the manifest"
        elif "stale" in statuses:
            reason = "installed prompt digest differs from the generated default"
        else:
            reason = "installed prompt requires refresh before default promotion"
    elif "not_installed" in statuses:
        action = "not_installed_noop"
        reason = "heartbeat is explicitly not installed; default promotion must not install it"
    elif "current" in statuses:
        action = "current"
        reason = "installed heartbeat prompt is already current"
    else:
        action = "noop"
        reason = "no installed heartbeat work is required"

    return {
        "goal_id": goal.get("goal_id"),
        "action": action,
        "requires_update": requires_update,
        "reason": reason,
        "prompt_modes": mode_summaries,
    }


def build_default_upgrade_propagation(
    *,
    summary: PayloadView,
    managed: Sequence[PayloadView],
    deferred: Sequence[PayloadView],
    recommended_action: str,
) -> Payload:
    managed_targets = [managed_default_upgrade_target(goal) for goal in managed]
    stage_deferred_targets = [
        {
            "goal_id": goal.get("goal_id"),
            "action": "skip_stage_deferred",
            "requires_update": False,
            "deferred_reason": goal.get("deferred_reason"),
            "recommended_action": goal.get("recommended_action"),
        }
        for goal in deferred
    ]
    return {
        "schema_version": "default_upgrade_propagation_v0",
        "ready_for_default_promotion": summary.get("ready_for_default_promotion"),
        "managed_target_count": len(managed_targets),
        "deferred_target_count": len(stage_deferred_targets),
        "update_count": sum(1 for target in managed_targets if target["requires_update"]),
        "current_count": summary.get("current_prompt_count"),
        "not_installed_noop_count": sum(1 for target in managed_targets if target["action"] == "not_installed_noop"),
        "stale_count": summary.get("stale_prompt_count"),
        "unknown_count": summary.get("unknown_prompt_count"),
        "policy_warning_count": summary.get("installed_prompt_policy_warning_count"),
        "deferred_install_count": 0,
        "managed_targets": managed_targets,
        "stage_deferred_targets": stage_deferred_targets,
        "recommended_action": recommended_action,
    }


def _summarize_managed_heartbeats(managed: Sequence[PayloadView]) -> dict[str, int]:
    counts = {
        "unknown": 0, "stale": 0, "current": 0, "not_installed": 0,
        "policy_warning_count": 0, "policy_warning_prompt_count": 0,
        "host_loop_activated": 0, "host_loop_missing": 0, "peer_runtime_migration_count": 0,
    }
    for goal in managed:
        for installed in _dict_view(goal.get("installed_prompts")).values():
            if not isinstance(installed, dict):
                continue
            status_value = installed.get("status")
            if status_value in {"unknown", "stale", "current", "not_installed"}:
                counts[str(status_value)] += 1
            warning_count = _int_value(_dict_view(installed.get("prompt_policy_audit")).get("warning_count"))
            counts["policy_warning_count"] += warning_count
            if warning_count > 0:
                counts["policy_warning_prompt_count"] += 1
        activation = _dict_view(goal.get("host_loop_activation"))
        if activation.get("activated") is True:
            counts["host_loop_activated"] += 1
        if activation.get("status") in HOST_LOOP_UPDATE_STATUSES:
            counts["host_loop_missing"] += 1
        if _dict_view(goal.get("peer_runtime_automation_migration")).get("required") is True:
            counts["peer_runtime_migration_count"] += 1
    return counts


def build_upgrade_plan(
    *,
    registry_path: Path,
    runtime_root_override: str | None = None,
    installed_manifest: Path | None = None,
    cli_bin: str = "loopx",
    modes: list[str] | tuple[str, ...] | None = None,
    goal_ids: list[str] | tuple[str, ...] | None = None,
) -> Payload:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_override)
    if not registry_path.exists():
        fallback = global_registry_path(runtime_root or select_default_runtime_root())
        if fallback.exists():
            registry_path = fallback
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(registry, runtime_root_override)

    selected_modes = tuple(modes or DEFAULT_UPGRADE_MODES)
    selected_goal_ids = set(goal_ids or [])
    manifest = load_installed_manifest(installed_manifest)
    manifest_entries = _dict_list(manifest.get("entries"))
    manifest_task_body_count = sum(1 for entry in manifest_entries if "task_body" in entry)
    installed_by_key = index_installed_entries(manifest_entries)
    managed: list[Payload] = []
    deferred: list[Payload] = []

    for raw_goal in registry_goals(registry):
        goal = cast(Payload, raw_goal)
        goal_id = str(goal.get("id") or "")
        if selected_goal_ids and goal_id not in selected_goal_ids:
            continue
        repo = Path(str(goal.get("repo") or ".")).expanduser()
        raw_state_file = goal.get("state_file")
        state_file = resolve_state_file(repo, str(raw_state_file) if raw_state_file else None)
        if goal_is_stage_deferred(goal):
            deferred.append(stage_deferred_goal_summary(goal, state_file))
            continue
        registered_agents = registered_agent_ids_for_goal(goal)
        prompt_summaries: dict[str, Payload] = {}
        installed: dict[str, Payload] = {}
        target_agents: Sequence[str | None] = registered_agents or (None,)
        prompt_targets = [(mode, agent_id) for mode in selected_modes for agent_id in target_agents]
        for mode, agent_id in prompt_targets:
            key = prompt_target_key(mode, agent_id)
            entry = installed_by_key.get((goal_id, mode, agent_id or ""))
            legacy_unscoped = False
            if entry is None and agent_id:
                entry = installed_by_key.get((goal_id, mode, ""))
                legacy_unscoped = entry is not None
            available_capabilities = installed_entry_available_capabilities(entry)
            prompt = goal_heartbeat_prompt(
                goal, cli_bin=cli_bin, mode=mode, agent_id=agent_id,
                available_capabilities=available_capabilities,
            )
            prompt_meta = prompt_summary(prompt, mode)
            prompt_meta["agent_id"] = agent_id
            prompt_meta["prompt_target"] = key
            prompt_summaries[key] = prompt_meta
            expected_digest = str(prompt_meta.get("sha256") or "")
            not_installed = entry_declares_not_installed(entry)
            actual_digest = None if not_installed else installed_entry_digest(entry) if entry else None
            bootstrap = _dict_view(entry.get("runtime_thin_bootstrap")) if entry else {}
            live_thin = bool(
                bootstrap
                and mode == "thin"
                and bootstrap.get("goal_id") == goal_id
                and bootstrap.get("agent_id") == agent_id
                and bootstrap.get("cli_bin", "loopx") == cli_bin
                and bootstrap.get("runtime_root") == (str(Path(runtime_root_override).expanduser().resolve()) if runtime_root_override else None)
                and Path(str(bootstrap.get("registry"))).resolve() == Path(registry_path).resolve()
            )
            prompt_status = "not_installed" if not_installed else ("current" if live_thin or actual_digest == expected_digest else "stale") if entry else "unknown"
            policy_audit = (
                _unavailable_policy_audit("heartbeat is explicitly not installed", status="not_applicable")
                if not_installed
                else installed_prompt_policy_audit(entry)
            )
            warnings = _dict_list(policy_audit.get("warnings"))
            if legacy_unscoped:
                warnings = [
                    *warnings,
                    {
                        "kind": "legacy_unscoped_prompt_for_registered_agent",
                        "severity": "warning",
                        "recommended_action": f"replace this heartbeat with a scoped prompt generated with --agent-id {agent_id}",
                    },
                ]
                policy_audit = {**policy_audit, "status": "warning", "warning_count": len(warnings), "warnings": warnings}
            policy_warning_count = _int_value(policy_audit.get("warning_count"))
            installed[key] = {
                "status": prompt_status,
                "requires_update": prompt_status in {"unknown", "stale"} or policy_warning_count > 0,
                "automation_id": entry.get("automation_id") if entry else None,
                "installed": False if not_installed else bool(entry),
                "agent_id": agent_id,
                "prompt_target": key,
                "legacy_unscoped_match": legacy_unscoped,
                "prompt_sha256": actual_digest,
                "expected_sha256": expected_digest,
                "available_capabilities": available_capabilities,
                "prompt_policy_audit": policy_audit,
            }
        loop_activation = build_loop_activation_summary(installed=installed)
        runtime_migration = peer_runtime_upgrade_migration(
            goal, goal_id=goal_id, installed=installed, generated_prompts=prompt_summaries,
        )

        managed.append(
            {
                "goal_id": goal_id,
                "adapter_kind": goal_adapter_kind(goal),
                "adapter_status": goal_adapter_status(goal) or None,
                "registered_agents": registered_agents,
                "agent_model": "peer_v1",
                "repo": str(repo),
                "state_file": str(state_file) if state_file else None,
                "state_file_exists": bool(state_file and state_file.exists()),
                "generated_prompts": prompt_summaries,
                "installed_prompts": installed,
                "host_loop_activation": loop_activation,
                "peer_runtime_automation_migration": runtime_migration,
                "requires_update": any(bool(item["requires_update"]) for item in installed.values())
                or loop_activation.get("status") in HOST_LOOP_UPDATE_STATUSES
                or runtime_migration.get("required") is True,
            }
        )

    counts = _summarize_managed_heartbeats(managed)
    ready = (
        bool(managed)
        and counts["unknown"] == 0
        and counts["stale"] == 0
        and counts["policy_warning_count"] == 0
        and counts["host_loop_missing"] == 0
        and counts["peer_runtime_migration_count"] == 0
    )
    if ready:
        recommended_action = "promotion propagation is complete"
    elif counts["peer_runtime_migration_count"] > 0:
        recommended_action = (
            "complete each peer runtime automation migration in the projected order; "
            "stable migration ids make host updates idempotent and each completion ack "
            "removes the prompt exactly once"
        )
    elif counts["host_loop_missing"] > 0:
        recommended_action = "activate missing host loops from generated scoped heartbeat prompts before claiming autonomous setup"
    elif counts["policy_warning_count"] > 0:
        recommended_action = "regenerate installed heartbeat automations with prompt policy warnings before default promotion"
    elif managed:
        recommended_action = "refresh installed heartbeat automations/controller clients before default promotion"
    elif deferred:
        recommended_action = "selected heartbeats are stage-deferred; do not install until the operator authorizes that stage"
    else:
        recommended_action = "no registry-managed heartbeats matched the upgrade plan selection"
    summary: Payload = {
        "managed_goal_count": len(managed),
        "current_prompt_count": counts["current"],
        "stale_prompt_count": counts["stale"],
        "unknown_prompt_count": counts["unknown"],
        "not_installed_prompt_count": counts["not_installed"],
        "stage_deferred_goal_count": len(deferred),
        "ready_for_default_promotion": ready,
        "installed_manifest_available": manifest.get("available"),
        "installed_manifest_source": manifest.get("source"),
        "installed_manifest_entry_count": len(manifest_entries),
        "installed_manifest_parse_error_count": _int_value(manifest.get("parse_error_count")),
        "installed_manifest_task_body_count": manifest_task_body_count,
        "installed_manifest_has_task_body": manifest_task_body_count > 0,
        "installed_prompt_policy_warning_count": counts["policy_warning_count"],
        "installed_prompt_policy_warning_prompt_count": counts["policy_warning_prompt_count"],
        "host_loop_activated_goal_count": counts["host_loop_activated"],
        "host_loop_missing_goal_count": counts["host_loop_missing"],
        "peer_runtime_automation_migration_count": counts["peer_runtime_migration_count"],
    }
    default_upgrade_propagation = build_default_upgrade_propagation(
        summary=summary, managed=managed, deferred=deferred, recommended_action=recommended_action,
    )
    return {
        "ok": True,
        "mode": "upgrade-plan",
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "cli_bin": cli_bin,
        "prompt_modes": list(selected_modes),
        "installed_manifest": manifest,
        "summary": summary,
        "managed_heartbeats": managed,
        "stage_deferred_heartbeats": deferred,
        "default_upgrade_propagation": default_upgrade_propagation,
        "recommended_action": recommended_action,
    }


def render_upgrade_plan_markdown(payload: PayloadView) -> str:
    summary = _dict_view(payload.get("summary"))
    lines = [
        "# LoopX Upgrade Plan",
        "",
        *_bullet_fields(payload, ("ok", "registry", "runtime_root", "cli_bin")),
        f"- prompt_modes: `{', '.join(_str_list(payload.get('prompt_modes')))}`",
        *_bullet_fields(summary, _SUMMARY_MARKDOWN_FIELDS),
        f"- recommended_action: `{payload.get('recommended_action')}`",
    ]
    manifest = _dict_view(payload.get("installed_manifest"))
    lines.extend(["", "## Installed Manifest", "", *_bullet_fields(manifest, _MANIFEST_MARKDOWN_FIELDS)])
    for error in _dict_list(manifest.get("parse_errors")):
        lines.append(
            f"- parse_error automation_id=`{error.get('automation_id')}` "
            f"reason=`{error.get('reason')}`"
        )
    propagation = _dict_view(payload.get("default_upgrade_propagation"))
    lines.extend(["", "## Default Upgrade Propagation", "", *_bullet_fields(propagation, _PROPAGATION_MARKDOWN_FIELDS)])
    for target in _dict_list(propagation.get("managed_targets")):
        lines.append(
            f"- managed `{target.get('goal_id')}` action=`{target.get('action')}` "
            f"requires_update=`{target.get('requires_update')}` reason=`{target.get('reason')}`"
        )
    managed_heartbeats = _dict_list(payload.get("managed_heartbeats"))
    for goal in managed_heartbeats:
        migration = _dict_view(goal.get("peer_runtime_automation_migration"))
        if migration.get("required") is not True:
            continue
        lines.extend(["", f"## Peer Runtime Migration: {goal.get('goal_id')}", "", *_bullet_fields(migration, _MIGRATION_MARKDOWN_FIELDS)])
    for target in _dict_list(propagation.get("stage_deferred_targets")):
        lines.append(
            f"- deferred `{target.get('goal_id')}` action=`{target.get('action')}` "
            f"requires_update=`{target.get('requires_update')}`"
        )
    lines.extend(["", "## Managed Heartbeats", ""])
    for goal in managed_heartbeats:
        installed = _dict_view(goal.get("installed_prompts"))
        generated = _dict_view(goal.get("generated_prompts"))
        activation = _dict_view(goal.get("host_loop_activation"))
        status_parts: list[str] = []
        warning_lines: list[str] = []
        for mode, status_entry in installed.items():
            if not isinstance(status_entry, dict):
                continue
            audit = _dict_view(status_entry.get("prompt_policy_audit"))
            audit_warning_count = _int_value(audit.get("warning_count"))
            audit_suffix = f",audit_warnings={audit_warning_count}" if audit_warning_count else ""
            status_parts.append(f"{mode}={status_entry.get('status')}{audit_suffix}")
            warnings = _dict_list(audit.get("warnings"))
            if warnings:
                kinds = ", ".join(str(warning.get("kind")) for warning in warnings if warning.get("kind"))
                warning_lines.append(f"  prompt_policy_warnings[{mode}]: `{kinds}`")
        prompt_parts = [
            f"{mode}:sha={str(prompt.get('sha256') or '')[:12]} chars={prompt.get('char_count')}"
            for mode, prompt in generated.items()
            if isinstance(prompt, dict)
        ]
        lines.extend(
            [
                f"- `{goal.get('goal_id')}` state_file_exists=`{goal.get('state_file_exists')}` "
                f"requires_update=`{goal.get('requires_update')}` installed=`{', '.join(status_parts)}`",
                f"  host_loop_activation: surface=`{activation.get('host_surface')}` "
                f"status=`{activation.get('status')}` "
                f"activated=`{activation.get('activated')}` current=`{activation.get('current_count')}` "
                f"missing=`{activation.get('missing_count')}` stale=`{activation.get('stale_count')}`",
                f"  prompts: `{'; '.join(prompt_parts)}`",
                *warning_lines,
            ]
        )
    deferred_goals = _dict_list(payload.get("stage_deferred_heartbeats"))
    if deferred_goals:
        lines.extend(["", "## Stage Deferred Heartbeats", ""])
        for goal in deferred_goals:
            lines.append(
                f"- `{goal.get('goal_id')}` adapter=`{goal.get('adapter_kind')}:{goal.get('adapter_status')}` "
                f"attention_status=`{goal.get('attention_status')}` requires_update=`{goal.get('requires_update')}`"
            )
    return "\n".join(lines) + "\n"
