from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .event_inbox import (
    ROUTE_KEY_PATTERN,
    SAFE_PROFILE_PATTERN,
    inspect_lark_event_inbox,
    load_lark_event_inbox_config,
)

CONFIG_SCHEMA_VERSION_V0 = "lark_event_collector_config_v0"
CONFIG_SCHEMA_VERSION = "lark_event_collector_config_v1"
SUPPORTED_CONFIG_SCHEMA_VERSIONS = {
    CONFIG_SCHEMA_VERSION_V0,
    CONFIG_SCHEMA_VERSION,
}
PLAN_SCHEMA_VERSION = "lark_event_collector_plan_v0"
STATUS_SCHEMA_VERSION = "lark_event_collector_status_v0"
INSTALL_SCHEMA_VERSION = "lark_event_collector_install_v0"
SERVICE_RE = re.compile(r"^loopx-[a-z0-9][a-z0-9._-]{1,73}$")
CHAT_RE = re.compile(r"^oc_[A-Za-z0-9_-]+$")
TIMEOUT_RE = re.compile(r"^[1-9][0-9]*(?:s|m|h)$")
SUPPORTED_SUPERVISORS = {"launchd", "systemd"}
SUPPORTED_EVENT_KEY = "im.message.receive_v1"
MAX_ROUTE_COUNT = 50
TURN_START_SYNC_MAX_LOOKBACK_SECONDS = 7 * 24 * 60 * 60
TURN_START_SYNC_MAX_OVERLAP_SECONDS = 5 * 60
OPERATION_CALLBACK_EVENT_KEY = "card.action.trigger"

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _project_config_path(project: str | Path, raw: str | Path) -> Path:
    root = Path(project).expanduser().resolve()
    path = Path(raw).expanduser()
    path = path if path.is_absolute() else root / path
    path = path.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("lark collector config must stay inside the project") from exc
    git_probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
    )
    if git_probe.returncode != 0:
        has_git_boundary = any(
            (candidate / ".git").exists() for candidate in (root, *root.parents)
        )
        if has_git_boundary or relative.parts[:2] != (".loopx", "config"):
            raise ValueError(
                "lark collector config must be ignored and untracked, or stay "
                "under .loopx/config in a non-Git project"
            )
        return path
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", str(relative)],
        capture_output=True,
        check=False,
    )
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--quiet", "--", str(relative)],
        capture_output=True,
        check=False,
    )
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise ValueError("lark collector config must be ignored and untracked")
    return path


def _relative_project_path(root: Path, raw: object, label: str) -> tuple[str, Path]:
    value = str(raw or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in Path(value).parts:
        raise ValueError(f"{label} must be a project-relative path")
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the project") from exc
    return value, path


def load_lark_event_collector_config(
    *, project: str | Path, config_path: str | Path
) -> dict[str, Any]:
    root = Path(project).expanduser().resolve()
    path = _project_config_path(root, config_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "lark collector config must be a readable JSON object"
        ) from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") not in SUPPORTED_CONFIG_SCHEMA_VERSIONS
    ):
        raise ValueError(
            "lark collector config schema_version must be "
            f"{CONFIG_SCHEMA_VERSION_V0} or {CONFIG_SCHEMA_VERSION}"
        )
    schema_version = str(payload["schema_version"])
    enabled = payload.get("enabled") is True
    service_name = str(payload.get("service_name") or "loopx-lark-collector").strip()
    event_key = str(payload.get("event_key") or "im.message.receive_v1").strip()
    identity = str(payload.get("identity") or "bot").strip()
    supervisor = str(payload.get("supervisor") or "").strip()
    timeout = str(payload.get("consume_timeout") or "30m").strip()
    lark_cli_bin = str(payload.get("lark_cli_bin") or "lark-cli").strip()
    raw_turn_start_sync = payload.get("turn_start_sync")
    if raw_turn_start_sync is not None and not isinstance(raw_turn_start_sync, Mapping):
        raise TypeError("collector turn_start_sync must be an object")
    raw_turn_start_sync = (
        raw_turn_start_sync if isinstance(raw_turn_start_sync, Mapping) else {}
    )
    turn_start_sync_enabled = raw_turn_start_sync.get("enabled") is True
    turn_start_sync_lookback = raw_turn_start_sync.get(
        "initial_lookback_seconds", 15 * 60
    )
    turn_start_sync_overlap = raw_turn_start_sync.get("overlap_seconds", 5)
    turn_start_sync_page_size = raw_turn_start_sync.get("page_size", 50)
    raw_operation_callbacks = payload.get("operation_callbacks")
    if raw_operation_callbacks is not None and not isinstance(
        raw_operation_callbacks, Mapping
    ):
        raise TypeError("collector operation_callbacks must be an object")
    raw_operation_callbacks = (
        raw_operation_callbacks if isinstance(raw_operation_callbacks, Mapping) else {}
    )
    unknown_operation_callback_fields = set(raw_operation_callbacks) - {"enabled", "managed_turn_wake"}
    if unknown_operation_callback_fields:
        raise ValueError("collector operation_callbacks contains unsupported fields")
    operation_callbacks_enabled = raw_operation_callbacks.get("enabled") is True
    if operation_callbacks_enabled and schema_version == CONFIG_SCHEMA_VERSION_V0:
        raise ValueError("collector operation_callbacks requires config v1")
    managed_turn_wake = None
    raw_wake = raw_operation_callbacks.get("managed_turn_wake")
    if raw_wake is not None:
        fields = {"registry_path", "goal_id", "requester_agent_id", "execution_config", "binding_id"}
        if not isinstance(raw_wake, Mapping) or set(raw_wake) != fields:
            raise ValueError("collector managed_turn_wake requires one explicit operator binding")
        if not operation_callbacks_enabled:
            raise ValueError("collector managed_turn_wake requires enabled operation callbacks")
        if any(not isinstance(raw_wake[key], str) or not raw_wake[key]
               or len(raw_wake[key]) > 4096 or any(ord(c) < 32 for c in raw_wake[key]) for key in fields):
            raise ValueError("collector managed_turn_wake requires bounded string fields")
        registry_path = Path(raw_wake["registry_path"]).expanduser()
        if not registry_path.is_absolute():
            raise ValueError("collector managed_turn_wake registry_path must be absolute")
        config_ref, _ = _relative_project_path(root, raw_wake["execution_config"], "wake execution_config")
        managed_turn_wake = {**dict(raw_wake), "registry_path": str(registry_path),
                             "project": str(root), "execution_config": config_ref}
    for label, value, lower, upper in (
        (
            "initial_lookback_seconds",
            turn_start_sync_lookback,
            60,
            TURN_START_SYNC_MAX_LOOKBACK_SECONDS,
        ),
        (
            "overlap_seconds",
            turn_start_sync_overlap,
            0,
            TURN_START_SYNC_MAX_OVERLAP_SECONDS,
        ),
        ("page_size", turn_start_sync_page_size, 1, 50),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not lower <= value <= upper
        ):
            raise ValueError(f"collector turn_start_sync {label} is invalid")
    if not SERVICE_RE.fullmatch(service_name):
        raise ValueError("service_name must be a lowercase loopx- service token")
    if event_key != SUPPORTED_EVENT_KEY:
        raise ValueError(f"collector event_key must be {SUPPORTED_EVENT_KEY}")
    if identity != "bot":
        raise ValueError("collector identity must be bot")
    if supervisor not in SUPPORTED_SUPERVISORS:
        raise ValueError("supervisor must be launchd or systemd")
    if not TIMEOUT_RE.fullmatch(timeout):
        raise ValueError("consume_timeout must use a bounded duration such as 30m")
    if Path(lark_cli_bin).name != lark_cli_bin or not lark_cli_bin:
        raise ValueError("lark_cli_bin must be a command name, not a path")
    if schema_version == CONFIG_SCHEMA_VERSION_V0:
        raw_routes: object = [
            {
                "route_key": "default",
                "chat_id": payload.get("chat_id"),
                "event_inbox_config": payload.get("event_inbox_config"),
            }
        ]
    else:
        if "chat_id" in payload or "event_inbox_config" in payload:
            raise ValueError(
                "collector v1 uses routes instead of top-level chat_id or "
                "event_inbox_config"
            )
        raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list) or not (
        1 <= len(raw_routes) <= MAX_ROUTE_COUNT
    ):
        raise ValueError(
            f"collector routes must contain 1-{MAX_ROUTE_COUNT} route objects"
        )
    routes: list[dict[str, Any]] = []
    seen_route_keys: set[str] = set()
    seen_chat_ids: set[str] = set()
    seen_inbox_refs: set[str] = set()
    seen_inbox_paths: set[Path] = set()
    for index, raw_route in enumerate(raw_routes):
        if not isinstance(raw_route, Mapping):
            raise TypeError(f"collector route {index + 1} must be an object")
        route_key = str(raw_route.get("route_key") or "").strip()
        if not ROUTE_KEY_PATTERN.fullmatch(route_key):
            raise ValueError(
                f"collector route {index + 1} route_key must be a lowercase "
                "public-safe token"
            )
        chat_id = str(raw_route.get("chat_id") or "").strip()
        if not CHAT_RE.fullmatch(chat_id):
            raise ValueError(
                f"collector route {index + 1} chat_id must be a Lark oc_ chat id"
            )
        inbox_config_ref, inbox_config_path = _relative_project_path(
            root,
            raw_route.get("event_inbox_config"),
            f"collector route {index + 1} event_inbox_config",
        )
        if route_key in seen_route_keys:
            raise ValueError("collector routes must use unique route keys")
        if chat_id in seen_chat_ids:
            raise ValueError("collector routes must use unique chat ids")
        if inbox_config_ref in seen_inbox_refs:
            raise ValueError(
                "collector routes must use independent event inbox configs and paths"
            )
        inbox = load_lark_event_inbox_config(
            project=root,
            config_path=inbox_config_path,
        )
        if enabled and not inbox["enabled"]:
            raise ValueError("enabled collector requires enabled event inboxes")
        if enabled and not inbox["thread_complete"]:
            raise ValueError(
                "collector lifecycle currently requires configured_chat_all capture"
            )
        reply_chat_id = str(inbox["reply"].get("chat_id") or "").strip()
        if inbox["reply"].get("enabled") is True and reply_chat_id != chat_id:
            raise ValueError(
                "collector route chat_id must match the inbox reply chat_id"
            )
        inbox_path = inbox["inbox_path"]
        if inbox_path is not None and inbox_path in seen_inbox_paths:
            raise ValueError(
                "collector routes must use independent event inbox configs and paths"
            )
        seen_route_keys.add(route_key)
        seen_chat_ids.add(chat_id)
        seen_inbox_refs.add(inbox_config_ref)
        if inbox_path is not None:
            seen_inbox_paths.add(inbox_path)
        routes.append(
            {
                "route_key": route_key,
                "chat_id": chat_id,
                "event_inbox_config_ref": inbox_config_ref,
                "inbox": inbox,
            }
        )
    configured_profile = str(payload.get("profile") or "").strip()
    reply_profiles = {
        str(route["inbox"]["reply"].get("sender_profile") or "").strip()
        for route in routes
        if route["inbox"]["reply"].get("enabled") is True
    }
    reply_profiles.discard("")
    if not configured_profile and len(reply_profiles) > 1:
        raise ValueError(
            "all collector route reply profiles must match one profile-bound "
            "event stream"
        )
    reply_profile = next(iter(reply_profiles), "")
    profile = configured_profile or reply_profile
    profile_source = None
    if configured_profile:
        profile_source = "collector_config"
    elif reply_profile:
        profile_source = "event_inbox_reply"
    if enabled and (
        not SAFE_PROFILE_PATTERN.fullmatch(profile) or profile.lower() == "default"
    ):
        raise ValueError(
            "enabled lark collector requires an explicit non-default profile "
            "or an enabled inbox reply sender_profile"
        )
    if configured_profile and any(
        configured_profile != candidate for candidate in reply_profiles
    ):
        raise ValueError(
            "lark collector profile must match every inbox reply sender_profile"
        )
    if turn_start_sync_enabled and any(
        route["inbox"]["material_review"].get("enabled") is not True for route in routes
    ):
        raise ValueError(
            "collector turn_start_sync requires material_review on every route so "
            "new messages enter an Agent triage lane"
        )
    return {
        "schema_version": schema_version,
        "enabled": enabled,
        "project": root,
        "config_path": path,
        "service_name": service_name,
        "event_key": event_key,
        "identity": identity,
        "profile": profile,
        "profile_source": profile_source,
        "supervisor": supervisor,
        "consume_timeout": timeout,
        "lark_cli_bin": lark_cli_bin,
        "turn_start_sync": {
            "enabled": turn_start_sync_enabled,
            "initial_lookback_seconds": turn_start_sync_lookback,
            "overlap_seconds": turn_start_sync_overlap,
            "page_size": turn_start_sync_page_size,
        },
        "operation_callbacks": {
            "enabled": operation_callbacks_enabled,
            "event_key": OPERATION_CALLBACK_EVENT_KEY,
            "managed_turn_wake": managed_turn_wake,
        },
        "routes": routes,
    }


def _jq_projection(chat_ids: str | Sequence[str]) -> str:
    values = [chat_ids] if isinstance(chat_ids, str) else list(chat_ids)
    if not values:
        raise ValueError("collector jq projection requires at least one chat id")
    chat_filter = " or ".join(
        f".chat_id == {json.dumps(chat_id, ensure_ascii=False)}" for chat_id in values
    )
    return (
        f"select({chat_filter}) | "
        '{schema_version:"lark_event_inbox_event_v0",'
        "event_id:(.event_id // .message_id // .id),"
        "message_id:(.message_id // .id),"
        "create_time:.create_time,content:.content,"
        "attachment_count:(.attachment_count // 0),"
        "sender_type:(.sender_type // .sender.sender_type),"
        "sender_id:(.sender_id // .sender.id // .sender.sender_id),"
        "chat_id:.chat_id}"
    )


def _executable_prefix(executable: str) -> list[str]:
    path = Path(executable)
    try:
        first_line = path.open(encoding="utf-8").readline().strip()
    except (OSError, UnicodeDecodeError):
        return [executable]
    if first_line == "#!/usr/bin/env node":
        node = shutil.which("node")
        if node is None:
            raise ValueError(
                "lark-cli uses a Node wrapper but node is not available on PATH"
            )
        return [node, executable]
    return [executable]


def _collector_argv(
    config: Mapping[str, Any],
    executable: str,
    *,
    runtime_root: str | Path | None = None,
) -> list[str]:
    prefix = _executable_prefix(executable)
    repo_root = Path(__file__).resolve().parents[3]
    discovered = shutil.which("loopx")
    loopx_executable = (
        Path(discovered) if discovered else repo_root / "scripts" / "loopx"
    )
    if not loopx_executable.is_file():
        raise ValueError("loopx executable is unavailable for collector runtime")
    argv = [str(loopx_executable)]
    if runtime_root is not None:
        argv.extend(["--runtime-root", str(Path(runtime_root).expanduser().resolve())])
    argv.extend(
        [
            "lark-inbox",
            "collector-run",
            "--project",
            str(config["project"]),
            "--config",
            str(config["config_path"]),
            "--lark-cli-executable",
            executable,
        ]
    )
    if len(prefix) == 2:
        argv.extend(["--node-executable", prefix[0]])
    return argv


def _service_file(config: Mapping[str, Any]) -> Path:
    name = str(config["service_name"])
    if config["supervisor"] == "launchd":
        return Path.home() / "Library" / "LaunchAgents" / f"{name}.plist"
    return Path.home() / ".config" / "systemd" / "user" / f"{name}.service"


def _service_payload(config: Mapping[str, Any], argv: Sequence[str]) -> bytes:
    root = Path(config["project"])
    runtime = root / ".loopx" / "runtime" / "lark-collector"
    if config["supervisor"] == "launchd":
        return plistlib.dumps(
            {
                "Label": str(config["service_name"]),
                "ProgramArguments": list(argv),
                "WorkingDirectory": str(root),
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 5,
                "StandardOutPath": str(runtime / "collector.stdout.log"),
                "StandardErrorPath": str(runtime / "collector.stderr.log"),
            },
            sort_keys=True,
        )
    command = " ".join(shlex.quote(value) for value in argv)
    return (
        "[Unit]\nDescription=LoopX Lark event collector\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={root}\nExecStart={command}\n"
        "Restart=always\nRestartSec=5\n\n"
        "[Install]\nWantedBy=default.target\n"
    ).encode()


def _plan(
    config: Mapping[str, Any],
    *,
    runtime_root: str | Path | None = None,
) -> tuple[dict[str, Any], list[str], bytes]:
    executable = shutil.which(str(config["lark_cli_bin"]))
    operation_runtime_missing = bool(
        config["operation_callbacks"]["enabled"] and runtime_root is None
    )
    argv = _collector_argv(
        config,
        executable or str(config["lark_cli_bin"]),
        runtime_root=runtime_root,
    )
    service_payload = _service_payload(config, argv)
    return (
        {
            "ok": not operation_runtime_missing,
            "schema_version": PLAN_SCHEMA_VERSION,
            "enabled": config["enabled"],
            "status": (
                "pinned_runtime_required"
                if operation_runtime_missing
                else "install_ready"
                if executable
                else "dependency_missing"
            ),
            "service_name": config["service_name"],
            "supervisor": config["supervisor"],
            "event_key": config["event_key"],
            "identity": config["identity"],
            "profile_bound": bool(config.get("profile")),
            "profile_source": config["profile_source"],
            "profile_returned": False,
            "capture_scope": "configured_chat_all",
            "thread_complete": all(
                route["inbox"]["thread_complete"] for route in config["routes"]
            ),
            "route_count": len(config["routes"]),
            "multi_chat_routing": len(config["routes"]) > 1,
            "operation_callbacks_enabled": config["operation_callbacks"]["enabled"],
            "operation_callback_managed_wake_configured": config["operation_callbacks"]["managed_turn_wake"] is not None,
            "operation_callback_event_key": (
                config["operation_callbacks"]["event_key"]
                if config["operation_callbacks"]["enabled"]
                else None
            ),
            "operation_callback_console_configuration_preflighted": False,
            "lark_cli_available": executable is not None,
            "install_hint": (
                "Pass the pinned LoopX runtime root for operation callbacks."
                if operation_runtime_missing
                else None
                if executable
                else "Install and configure lark-cli, then rerun the collector plan."
            ),
            "service_digest": hashlib.sha256(service_payload).hexdigest()[:16],
            "local_paths_returned": False,
            "chat_id_returned": False,
            "credentials_returned": False,
            "external_writes_performed": False,
        },
        argv,
        service_payload,
    )


def plan_lark_event_collector(
    *,
    project: str | Path,
    config_path: str | Path,
    runtime_root: str | Path | None = None,
) -> dict[str, Any]:
    config = load_lark_event_collector_config(project=project, config_path=config_path)
    plan, _, _ = _plan(config, runtime_root=runtime_root)
    return plan


def _run(runner: Runner, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return runner(list(argv), capture_output=True, text=True, check=False)


def _event_consume_available(runner: Runner, executable: str) -> bool:
    support = _run(
        runner,
        [*_executable_prefix(executable), "event", "consume", "--help"],
    )
    return support.returncode == 0


def install_lark_event_collector(
    *,
    project: str | Path,
    config_path: str | Path,
    runtime_root: str | Path | None = None,
    execute: bool = False,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    config = load_lark_event_collector_config(project=project, config_path=config_path)
    plan, _, _ = _plan(config, runtime_root=runtime_root)
    if not config["enabled"]:
        raise ValueError("cannot install a disabled lark collector")
    if plan["ok"] is not True:
        return {**plan, "schema_version": INSTALL_SCHEMA_VERSION, "execute": execute}
    if not plan["lark_cli_available"]:
        return {**plan, "schema_version": INSTALL_SCHEMA_VERSION, "execute": execute}
    executable = shutil.which(str(config["lark_cli_bin"]))
    if not _event_consume_available(runner, str(executable)):
        return {
            **plan,
            "schema_version": INSTALL_SCHEMA_VERSION,
            "ok": False,
            "status": "dependency_incompatible",
            "execute": execute,
            "install_hint": "Upgrade lark-cli to a version with event consume support.",
        }
    argv = _collector_argv(config, str(executable), runtime_root=runtime_root)
    service_payload = _service_payload(config, argv)
    if not execute:
        return {
            **plan,
            "schema_version": INSTALL_SCHEMA_VERSION,
            "status": "preview_ready",
            "execute": False,
            "would_write_service": True,
        }
    service_path = _service_file(config)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    (Path(config["project"]) / ".loopx" / "runtime" / "lark-collector").mkdir(
        parents=True, exist_ok=True
    )
    temporary = service_path.with_suffix(service_path.suffix + ".tmp")
    temporary.write_bytes(service_payload)
    temporary.replace(service_path)
    if config["supervisor"] == "launchd":
        domain = f"gui/{os.getuid()}"
        _run(runner, ["launchctl", "bootout", f"{domain}/{config['service_name']}"])
        started = _run(runner, ["launchctl", "bootstrap", domain, str(service_path)])
        if started.returncode == 0:
            started = _run(
                runner,
                ["launchctl", "kickstart", "-k", f"{domain}/{config['service_name']}"],
            )
    else:
        reloaded = _run(runner, ["systemctl", "--user", "daemon-reload"])
        started = (
            _run(
                runner,
                ["systemctl", "--user", "enable", "--now", str(config["service_name"])],
            )
            if reloaded.returncode == 0
            else reloaded
        )
    return {
        **plan,
        "schema_version": INSTALL_SCHEMA_VERSION,
        "status": "installed" if started.returncode == 0 else "supervisor_start_failed",
        "ok": started.returncode == 0,
        "execute": True,
        "write_performed": True,
        "supervisor_start_performed": True,
        "supervisor_start_succeeded": started.returncode == 0,
    }


def inspect_lark_event_collector(
    *,
    project: str | Path,
    config_path: str | Path,
    runtime_root: str | Path | None = None,
    probe_event_bus: bool = False,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    config = load_lark_event_collector_config(project=project, config_path=config_path)
    plan, _, _ = _plan(config, runtime_root=runtime_root)
    service_path = _service_file(config)
    if config["supervisor"] == "launchd":
        observed = _run(
            runner,
            ["launchctl", "print", f"gui/{os.getuid()}/{config['service_name']}"],
        )
    else:
        observed = _run(
            runner,
            ["systemctl", "--user", "is-active", str(config["service_name"])],
        )
    bus_healthy = None
    if probe_event_bus and plan["lark_cli_available"]:
        executable = shutil.which(str(config["lark_cli_bin"]))
        profile_args = (
            ["--profile", str(config["profile"])] if config.get("profile") else []
        )
        bus = _run(
            runner,
            [
                str(executable),
                *profile_args,
                "event",
                "status",
                "--json",
                "--fail-on-orphan",
            ],
        )
        bus_healthy = bus.returncode == 0
    inbox_statuses = [
        inspect_lark_event_inbox(
            project=config["project"],
            config_path=route["event_inbox_config_ref"],
            limit=1,
        )
        for route in config["routes"]
    ]
    route_event_counts = [
        int(inbox_status.get("captured_count") or 0) for inbox_status in inbox_statuses
    ]
    event_count = sum(route_event_counts)
    routes_with_event_evidence = sum(count > 0 for count in route_event_counts)
    active = observed.returncode == 0 and (
        config["supervisor"] != "launchd" or "state = running" in observed.stdout
    )
    installed = service_path.is_file()
    callback_status_path = (
        Path(config["project"])
        / ".loopx"
        / "runtime"
        / "lark-collector"
        / "operation-callback-status.json"
    )
    callback_status: Mapping[str, Any] = {}
    try:
        raw_callback_status = json.loads(
            callback_status_path.read_text(encoding="utf-8")
        )
        if isinstance(raw_callback_status, Mapping):
            callback_status = raw_callback_status
    except (OSError, json.JSONDecodeError):
        pass
    callbacks_enabled = config["operation_callbacks"]["enabled"] is True
    callback_listener_active = bool(
        callbacks_enabled
        and active
        and callback_status.get("listener_active") is True
    )
    callback_listener_ready = bool(
        callback_listener_active
        and callback_status.get("listener_ready") is True
    )
    callback_delivery_verified = bool(
        callbacks_enabled
        and callback_status.get("callback_delivery_verified") is True
    )
    callback_qualification_state = (
        "disabled"
        if not callbacks_enabled
        else "callback_qualified"
        if callback_delivery_verified
        else "listener_ready_unqualified"
        if callback_listener_ready
        else "listener_unready"
    )
    healthy = bool(
        config["enabled"]
        and plan["lark_cli_available"]
        and installed
        and active
        and (bus_healthy is not False)
    )
    return {
        "ok": True,
        "schema_version": STATUS_SCHEMA_VERSION,
        "enabled": config["enabled"],
        "status": "healthy" if healthy else "attention_required",
        "healthy": healthy,
        "service_name": config["service_name"],
        "supervisor": config["supervisor"],
        "profile_bound": bool(config.get("profile")),
        "profile_source": config["profile_source"],
        "profile_returned": False,
        "installed": installed,
        "active": active,
        "lark_cli_available": plan["lark_cli_available"],
        "event_bus_probe_performed": probe_event_bus,
        "event_bus_healthy": bus_healthy,
        "captured_event_count": event_count,
        "real_event_evidence_present": event_count > 0,
        "route_count": len(config["routes"]),
        "multi_chat_routing": len(config["routes"]) > 1,
        "routes_with_event_evidence_count": routes_with_event_evidence,
        "all_routes_real_event_evidence_present": (
            routes_with_event_evidence == len(config["routes"])
        ),
        "operation_callbacks_enabled": callbacks_enabled,
        "operation_callback_managed_wake_configured": config["operation_callbacks"]["managed_turn_wake"] is not None,
        "operation_callback_listener_active": callback_listener_active,
        "operation_callback_listener_ready": callback_listener_ready,
        "operation_callback_delivery_verified": callback_delivery_verified,
        "operation_callback_qualification_state": callback_qualification_state,
        "operation_callback_verified_count": int(
            callback_status.get("verified_callback_count") or 0
        ),
        "operation_callback_last_evidence_at": callback_status.get(
            "last_verified_callback_at"
        ),
        "operation_callback_last_failure_code": callback_status.get(
            "last_failure_code"
        ),
        "operation_callback_last_failure_stage": callback_status.get(
            "last_failure_stage"
        ),
        "operation_callback_last_failure_event_shape": callback_status.get(
            "last_failure_event_shape"
        ),
        "operation_callback_console_configuration_preflighted": False,
        "thread_complete": all(
            route["inbox"]["thread_complete"] for route in config["routes"]
        ),
        "local_paths_returned": False,
        "chat_id_returned": False,
        "credentials_returned": False,
        "external_writes_performed": False,
    }
