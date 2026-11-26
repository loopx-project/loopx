"""Managed JSON and JSONC configuration I/O helpers for slash-command hosts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import tempfile
from typing import cast

JsonEntryFactory = Callable[[], object | None]


def _copy_json_string(content: str, index: int, output: list[str]) -> int:
    """Copy a JSON string literal starting at ``index`` into ``output``."""
    output.append(content[index])
    index += 1
    escaped = False
    while index < len(content):
        char = content[index]
        output.append(char)
        index += 1
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            break
    return index


def strip_jsonc_comments(content: str) -> str:
    """Replace JSONC ``//`` and ``/* ... */`` comments outside strings with spaces."""
    output: list[str] = []
    index = 0
    length = len(content)
    while index < length:
        char = content[index]
        if char == '"':
            index = _copy_json_string(content, index, output)
            continue
        next_char = content[index + 1] if index + 1 < length else ""
        if char == "/" and next_char == "/":
            output.extend((" ", " "))
            index += 2
            while index < length and content[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and next_char == "*":
            output.extend((" ", " "))
            index += 2
            while index < length:
                if index + 1 < length and content[index : index + 2] == "*/":
                    output.extend((" ", " "))
                    index += 2
                    break
                output.append("\n" if content[index] == "\n" else " ")
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def strip_jsonc_trailing_commas(content: str) -> str:
    """Remove trailing commas before ``]`` or ``}`` outside JSON string literals."""
    output: list[str] = []
    index = 0
    length = len(content)
    while index < length:
        char = content[index]
        if char == '"':
            index = _copy_json_string(content, index, output)
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < length and content[lookahead].isspace():
                lookahead += 1
            if lookahead < length and content[lookahead] in "]}":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def opencode_plugin_name(plugin: object) -> str | None:
    """Return the package name encoded by an OpenCode plugin entry, if valid."""
    if isinstance(plugin, str):
        return plugin
    if isinstance(plugin, list) and plugin and isinstance(plugin[0], str):
        return plugin[0]
    return None


def opencode_direct_goal_plugin_conflicts(
    root: Path,
    goal_plugins: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Find direct goal-plugin registrations and invalid OpenCode JSON/JSONC configs."""
    conflicts: list[str] = []
    invalid: list[str] = []
    for name in ("opencode.json", "opencode.jsonc"):
        path = root / name
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
            if path.suffix == ".jsonc":
                content = strip_jsonc_trailing_commas(strip_jsonc_comments(content))
            payload_obj: object = json.loads(content)
        except (json.JSONDecodeError, OSError):
            invalid.append(str(path))
            continue
        if not isinstance(payload_obj, dict):
            invalid.append(str(path))
            continue
        payload = cast(dict[str, object], payload_obj)
        plugins_obj = payload.get("plugin") or []
        if isinstance(plugins_obj, str):
            plugins: list[object] = [plugins_obj]
        elif isinstance(plugins_obj, list):
            plugins = cast(list[object], plugins_obj)
        else:
            invalid.append(str(path))
            continue
        plugin_names = [
            plugin_name
            for plugin in plugins
            if (plugin_name := opencode_plugin_name(plugin)) is not None
        ]
        if any(
            plugin == package or plugin.startswith(f"{package}@")
            for plugin in plugin_names
            for package in goal_plugins
        ):
            conflicts.append(str(path))
    return conflicts, invalid


def target_package_dependencies(
    path: Path,
    dependencies: Mapping[str, str],
    *,
    execute: bool,
) -> str:
    """Preview or upsert pinned npm dependencies in a host ``package.json`` file."""
    if path.exists():
        try:
            payload_obj: object = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return "blocked_invalid_user_package_json"
        if not isinstance(payload_obj, dict):
            return "blocked_invalid_user_package_json"
        payload = cast(dict[str, object], payload_obj)
        current_obj = payload.get("dependencies")
        if current_obj is None:
            current: dict[str, object] = {}
        elif isinstance(current_obj, dict):
            current = cast(dict[str, object], current_obj)
        else:
            return "blocked_invalid_user_package_json"
        wanted: dict[str, object] = {**current, **dependencies}
        if wanted == current:
            return "unchanged"
        payload["dependencies"] = wanted
        status = "updated" if execute else "would_update"
    else:
        payload = {"private": True, "dependencies": dict(dependencies)}
        status = "created" if execute else "would_create"
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    return status


def write_json_atomic(path: Path, payload: object) -> None:
    """Replace ``path`` in one atomic step, preserving existing file permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2) + "\n")
        if mode is not None:
            os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_json_marker_section(marker_path: Path, section: str) -> dict[str, object]:
    """Read one mapping section from a LoopX ownership marker, failing closed to ``{}``."""
    try:
        payload_obj: object = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload_obj, dict):
        return {}
    entries_obj = cast(dict[str, object], payload_obj).get(section)
    return cast(dict[str, object], entries_obj) if isinstance(entries_obj, dict) else {}


def read_managed_json_entry(
    *,
    config_path: Path,
    marker_path: Path,
    marker_section: str,
    marker_schema_version: str,
    entry_key: str,
    container_key: str | None,
) -> dict[str, object] | None:
    """Return the existing entry when proven LoopX-managed by its sidecar, else ``None``."""
    try:
        marker_obj: object = json.loads(marker_path.read_text(encoding="utf-8"))
        config_obj: object = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(marker_obj, dict) or not isinstance(config_obj, dict):
        return None
    marker = cast(dict[str, object], marker_obj)
    if marker.get("schema_version") != marker_schema_version:
        return None
    section = marker.get(marker_section)
    if not isinstance(section, dict):
        return None
    recorded = cast(dict[str, object], section).get(entry_key)
    config = cast(dict[str, object], config_obj)
    container_obj: object = config if container_key is None else config.get(container_key)
    if not isinstance(container_obj, dict):
        return None
    existing = cast(dict[str, object], container_obj).get(entry_key)
    if (
        not isinstance(recorded, dict)
        or not isinstance(existing, dict)
        or existing != recorded
    ):
        return None
    return cast(dict[str, object], existing)


def merge_managed_json_entry(
    *,
    config_path: Path,
    marker_path: Path,
    marker_section: str,
    marker_schema_version: str,
    entry_key: str,
    container_key: str | None,
    legacy_container_key: str | None,
    invalid_status: str,
    user_owned_status: str,
    missing_dependency_status: str,
    entry_factory: JsonEntryFactory,
    resolve_before_dry_run: bool,
    uninstall: bool,
    execute: bool,
) -> str:
    """Fail-closed merge or uninstall of one LoopX-owned entry in a host JSON file."""
    try:
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        raw_obj: object = json.loads(text) if text.strip() else {}
    except (OSError, json.JSONDecodeError):
        return invalid_status
    if not isinstance(raw_obj, dict):
        return invalid_status
    raw = cast(dict[str, object], raw_obj)
    if container_key is None:
        container = raw
    else:
        raw_container = raw.get(container_key)
        if raw_container is None:
            raw_container = {}
        if not isinstance(raw_container, dict):
            return invalid_status
        container = cast(dict[str, object], raw_container)

    recorded = read_json_marker_section(marker_path, marker_section).get(entry_key)
    existing = container.get(entry_key)
    legacy_container: dict[str, object] | None = None
    legacy_owned = False
    if legacy_container_key is not None:
        raw_legacy = raw.get(legacy_container_key)
        if isinstance(raw_legacy, dict):
            legacy_container = cast(dict[str, object], raw_legacy)
            legacy_entry = legacy_container.get(entry_key)
            legacy_owned = (
                existing is None
                and legacy_entry is not None
                and legacy_entry == recorded
            )
            if legacy_owned:
                existing = legacy_entry
            elif existing is None and legacy_entry is not None:
                return user_owned_status

    loopx_owned = existing is not None and existing == recorded
    # Clear stale markers only during execution so dry runs stay strictly read-only.
    if execute and recorded is not None and not loopx_owned:
        marker_path.unlink(missing_ok=True)

    if uninstall:
        if existing is None:
            return "absent"
        if not loopx_owned:
            return user_owned_status
        if not execute:
            return "would_retire"
        if legacy_owned and legacy_container is not None:
            legacy_container.pop(entry_key, None)
            if not legacy_container and legacy_container_key is not None:
                raw.pop(legacy_container_key, None)
        else:
            container.pop(entry_key, None)
            if container_key is not None:
                raw[container_key] = container
        write_json_atomic(config_path, raw)
        marker_path.unlink(missing_ok=True)
        return "retired"

    if existing is not None and not loopx_owned:
        return user_owned_status
    if resolve_before_dry_run:
        entry = entry_factory()
        if entry is None:
            return missing_dependency_status
        if not execute:
            return "unchanged" if existing == entry and not legacy_owned else "would_write"
    else:
        if not execute:
            return "unchanged" if existing is not None else "would_write"
        entry = entry_factory()
        if entry is None:
            return missing_dependency_status
    if existing == entry and not legacy_owned:
        return "unchanged"
    if legacy_owned and legacy_container is not None:
        legacy_container.pop(entry_key, None)
        if not legacy_container and legacy_container_key is not None:
            raw.pop(legacy_container_key, None)
    container[entry_key] = entry
    if container_key is not None:
        raw[container_key] = container
    write_json_atomic(config_path, raw)
    write_json_atomic(
        marker_path,
        {
            "schema_version": marker_schema_version,
            marker_section: {entry_key: entry},
        },
    )
    return "written"
