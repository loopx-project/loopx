from __future__ import annotations

from pathlib import Path
from typing import Any, NotRequired, TypedDict

MANAGED_MARKER_PREFIX = "<!-- loopx-managed-slash-command:v1"
LEGACY_UPGRADABLE_SIGNATURES = (
    "loopx goal-mode setup (NOT Claude Code's built-in /goal)",
    "The output is loopx control-plane SETUP",
    "goalmode_cmd.py",
)
EXISTING_LOOPX_CAPABILITY_SKILL_SIGNATURES = (
    "# LoopX PR Review",
    "Run `loopx pr-review` first",
)


class CommandFacadeSpec(TypedDict):
    command: str
    name: str
    description: str
    argument_hint: str
    instructions: list[str]
    title: NotRequired[str]
    alias_for: NotRequired[str]


def managed_marker(*, command: str, surface: str) -> str:
    return f"{MANAGED_MARKER_PREFIX} command={command} surface={surface} -->"


def _needs_yaml_quotes(value: str) -> bool:
    """Whether a front-matter scalar must be double-quoted to stay valid YAML.

    Hosts do not agree on how forgiving their front-matter reader is. Kiro CLI
    keeps the quote characters verbatim, so a quoted `name` turns the skill's
    own slash command into `/"loopx"`; a strict YAML reader, on the other hand,
    rejects an unquoted `[task text]` or a value containing `": "`. Quoting only
    what YAML actually requires satisfies both: identifiers stay plain and
    ambiguous prose stays quoted.
    """
    if not value or value.strip() != value:
        return True
    if value[0] in "-?:,[]{}#&*!|>'\"%@`":
        return True
    if value.lower() in {"true", "false", "null", "yes", "no", "on", "off", "~"}:
        return True
    return ": " in value or " #" in value or value.endswith(":") or "\n" in value


def front_matter(*, fields: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if _needs_yaml_quotes(value):
            escaped = value.replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def skill_body(
    *,
    command: str,
    title: str,
    description: str,
    argument_hint: str,
    instructions: list[str],
    surface: str,
    front_matter_name: str | None = None,
) -> str:
    fields = {"description": description, "argument-hint": argument_hint}
    if front_matter_name:
        fields = {"name": front_matter_name, **fields}
    surface_label = (
        "slash command"
        if surface == "claude-skills"
        else "DSH workflow skill"
        if surface == "dsh-skills"
        else "explicit LoopX command skill"
    )
    return "\n\n".join(
        [
            front_matter(fields=fields),
            managed_marker(command=command, surface=surface),
            f"# {title}",
            f"Treat this as the LoopX `{command}` {surface_label}.",
            "\n".join(instructions),
            "Keep public/private boundaries intact and do not perform external writes unless the active LoopX state or owner explicitly authorizes them.",
        ]
    ) + "\n"


def _is_legacy_upgradable_loopx_file(existing: str) -> bool:
    return any(signature in existing for signature in LEGACY_UPGRADABLE_SIGNATURES)


def _is_existing_loopx_capability_skill(existing: str) -> bool:
    return any(
        signature in existing
        for signature in EXISTING_LOOPX_CAPABILITY_SKILL_SIGNATURES
    )


def target_status(path: Path, content: str, *, execute: bool) -> str:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if MANAGED_MARKER_PREFIX not in existing:
            if _is_legacy_upgradable_loopx_file(existing):
                if execute:
                    path.write_text(content, encoding="utf-8")
                return "upgraded_legacy_managed"
            if path.name == "SKILL.md" and _is_existing_loopx_capability_skill(existing):
                return "preserved_existing_loopx_skill"
            return "skipped_user_file"
        if existing == content:
            return "unchanged"
        if execute:
            path.write_text(content, encoding="utf-8")
        return "updated"
    if execute:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return "created" if execute else "would_create"


def retire_managed_file(path: Path, *, execute: bool) -> str | None:
    if not path.exists():
        return None
    if MANAGED_MARKER_PREFIX not in path.read_text(encoding="utf-8"):
        return "skipped_user_file"
    if execute:
        path.unlink()
    return "retired_managed_file" if execute else "would_retire_managed_file"


def retire_status(path: Path, *, execute: bool) -> str:
    return retire_managed_file(path, execute=execute) or "absent"


def install_skill_facade(
    *,
    specs: list[CommandFacadeSpec],
    installed: list[dict[str, Any]],
    skills_dir: Path,
    surface: str,
    host_surfaces: list[str],
    mechanism: str,
    execute: bool,
    uninstall: bool,
    invoke_prefix: str = "",
    flat: bool = False,
) -> None:
    """Install canonical skills and retire catalog aliases in every layout."""
    for spec in specs:
        path = (
            skills_dir / f"{spec['name']}.md"
            if flat
            else skills_dir / str(spec["name"]) / "SKILL.md"
        )
        if "alias_for" in spec:
            status = retire_managed_file(path, execute=execute)
            if status:
                installed.append({
                    "surface": surface,
                    "host_surfaces": list(host_surfaces),
                    "mechanism": f"retired_{surface.replace('-', '_')}_legacy_alias",
                    "command": spec["command"],
                    "path": str(path),
                    "status": status,
                    "invoke_as": [],
                    "replacement_command": spec["alias_for"],
                })
            continue
        if uninstall:
            installed.append(
                {
                    "surface": surface,
                    "host_surfaces": list(host_surfaces),
                    "mechanism": mechanism,
                    "command": spec["command"],
                    "path": str(path),
                    "status": retire_status(path, execute=execute),
                    "invoke_as": [],
                }
            )
            continue
        content = skill_body(
            command=str(spec["command"]),
            title=f"LoopX {spec['command']}",
            description=str(spec["description"]),
            argument_hint=str(spec["argument_hint"]),
            instructions=list(spec["instructions"]),
            surface="claude-skills",
            front_matter_name=str(spec["name"]),
        )
        installed.append(
            {
                "surface": surface,
                "host_surfaces": list(host_surfaces),
                "mechanism": mechanism,
                "command": spec["command"],
                "path": str(path),
                "status": target_status(path, content, execute=execute),
                "invoke_as": [f"{invoke_prefix}{spec['name']}"],
            }
        )
