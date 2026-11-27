"""Host-specific slash-command and runtime bridge surface installers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from .opencode import plugin_source, runtime_source
from ..skill_install_readback import retire_duplicate_managed_skills
from ..slash_command_files import (
    CommandFacadeSpec,
    install_skill_facade as _install_skill_facade,
    retire_managed_file as _retire_managed_file,
    retire_status as _retire_status,
    target_status as _target_status,
)
from .slash_command_config_io import (
    opencode_direct_goal_plugin_conflicts,
    target_package_dependencies,
)

CODEX_HOST_SURFACES = [
    "codex-cli",
    "codex-ide-plugin",
    "codex-app",
    "codex-app-ssh",
]


class MetadataRenderer(Protocol):
    """Render host skill metadata YAML for one command facade."""

    def __call__(
        self, *, command: str, display_name: str, short_description: str
    ) -> str: ...


def installed_entry(
    *,
    surface: str,
    mechanism: str,
    command: str,
    path: Path | str | None,
    status: str,
    host_surfaces: list[str] | None = None,
    invoke_as: list[str] | None = None,
    **extra: object,
) -> dict[str, object]:
    """Build one canonical status row for ``install_slash_commands()['installed']``."""
    entry: dict[str, object] = {"surface": surface}
    if host_surfaces is not None:
        entry["host_surfaces"] = list(host_surfaces)
    entry.update(
        {
            "mechanism": mechanism,
            "command": command,
            "path": str(path) if path is not None else None,
            "status": status,
            "invoke_as": list(invoke_as) if invoke_as is not None else [],
        }
    )
    entry.update(extra)
    return entry


def _user_owned_target_paths(pairs: tuple[tuple[Path, str], ...]) -> list[str]:
    return [
        str(path)
        for path, content in pairs
        if _target_status(path, content, execute=False) == "skipped_user_file"
    ]


def _append_unit_entries(
    *,
    installed: list[dict[str, object]],
    surface: str,
    command: str,
    invoke_as: list[str],
    entries: tuple[tuple[str, Path, str], ...],
    uninstall: bool,
    execute: bool,
) -> None:
    for mechanism, path, content in entries:
        status = (
            _retire_status(path, execute=execute)
            if uninstall
            else _target_status(path, content, execute=execute)
        )
        installed.append(
            installed_entry(
                surface=surface,
                host_surfaces=[surface],
                mechanism=mechanism,
                command=command,
                path=path,
                status=status,
                invoke_as=invoke_as,
            )
        )


def install_codex_surface(
    *,
    installed: list[dict[str, object]],
    codex_root: Path,
    canonical_specs: list[CommandFacadeSpec],
    legacy_alias_specs: list[CommandFacadeSpec],
    execute: bool,
    uninstall: bool,
    render_skill_content: Callable[[CommandFacadeSpec], str],
    render_openai_metadata: MetadataRenderer,
) -> object:
    """Install/retire Codex skills, metadata, legacy prompts, and native-registry status."""
    # Codex exposes canonical skills only; retire its older managed facade,
    # metadata and custom-prompt paths without creating replacements.
    for spec in legacy_alias_specs:
        for path in (
            codex_root / "skills" / spec["name"] / "SKILL.md",
            codex_root / "skills" / spec["name"] / "agents" / "openai.yaml",
            codex_root / "prompts" / f"{spec['name']}.md",
        ):
            status = _retire_managed_file(path, execute=execute)
            if status:
                installed.append(
                    installed_entry(
                        surface="codex",
                        mechanism="retired_codex_legacy_alias",
                        command=spec["command"],
                        path=path,
                        status=status,
                    )
                )
    prompt_dir = codex_root / "prompts"
    for spec in canonical_specs:
        prompt_path = prompt_dir / f"{spec['name']}.md"
        retire_result = (
            _retire_status(prompt_path, execute=execute)
            if uninstall
            else _retire_managed_file(prompt_path, execute=execute)
        )
        if retire_result:
            installed.append(
                installed_entry(
                    surface="codex",
                    host_surfaces=CODEX_HOST_SURFACES,
                    mechanism="retired_codex_custom_prompt",
                    command=spec["command"],
                    path=prompt_path,
                    status=retire_result,
                )
            )

    skill_dir = codex_root / "skills"
    for spec in canonical_specs:
        skill_path = skill_dir / str(spec["name"]) / "SKILL.md"
        metadata_path = skill_path.parent / "agents" / "openai.yaml"
        invoke_as = [f"${spec['name']}", "/skills"]
        if uninstall:
            for mechanism, path in (
                ("codex_explicit_skills", skill_path),
                ("codex_skill_openai_metadata", metadata_path),
            ):
                installed.append(
                    installed_entry(
                        surface="codex",
                        host_surfaces=CODEX_HOST_SURFACES,
                        mechanism=mechanism,
                        command=spec["command"],
                        path=path,
                        status=_retire_status(path, execute=execute),
                        invoke_as=invoke_as,
                    )
                )
            continue
        skill_status = _target_status(
            skill_path, render_skill_content(spec), execute=execute
        )
        installed.append(
            installed_entry(
                surface="codex",
                host_surfaces=CODEX_HOST_SURFACES,
                mechanism="codex_explicit_skills",
                command=spec["command"],
                path=skill_path,
                status=skill_status,
                invoke_as=invoke_as,
            )
        )
        if skill_status not in {"skipped_user_file", "preserved_existing_loopx_skill"}:
            display_name = (
                "LoopX"
                if spec["name"] == "loopx"
                else "LoopX "
                + str(spec["name"])[6:].replace("-", " ").title().replace("Pr ", "PR ")
            )
            metadata = render_openai_metadata(
                command=str(spec["command"]),
                display_name=display_name,
                short_description=str(spec["description"]),
            )
            installed.append(
                installed_entry(
                    surface="codex",
                    host_surfaces=CODEX_HOST_SURFACES,
                    mechanism="codex_skill_openai_metadata",
                    command=spec["command"],
                    path=metadata_path,
                    status=_target_status(metadata_path, metadata, execute=execute),
                    invoke_as=invoke_as,
                )
            )
        else:
            retired_meta = _retire_managed_file(metadata_path, execute=execute)
            if retired_meta:
                installed.append(
                    installed_entry(
                        surface="codex",
                        host_surfaces=CODEX_HOST_SURFACES,
                        mechanism="retired_codex_command_metadata",
                        command=spec["command"],
                        path=metadata_path,
                        status=retired_meta,
                    )
                )
    for spec in canonical_specs:
        installed.append(
            installed_entry(
                surface="codex",
                host_surfaces=["codex-cli"],
                mechanism="unsupported_native_slash_registry",
                command=spec["command"],
                path=None,
                status="unsupported_host_surface",
                reason=(
                    "Current Codex does not support user-defined native top-level slash "
                    "commands. Use explicit skills instead."
                ),
                native_registry_supported=False,
                failure_policy="fail_closed_to_explicit_skill",
                fallback=(
                    f"Use `${spec['name']}` or `/skills` to explicitly invoke the LoopX "
                    "command skill; for the visible TUI loop, run "
                    "`loopx codex-cli-bootstrap-message --project .`, paste the setup "
                    "message, then set `/goal <thin task_body>`."
                ),
            )
        )

    if uninstall:
        return None
    return retire_duplicate_managed_skills(
        skill_dir, execute=execute, retire_legacy_aliases=True
    )


def _opencode_bridge_preflight(
    *,
    installed: list[dict[str, object]],
    opencode_root: Path,
    plugin_path: Path,
    runtime_path: Path,
    package_path: Path,
    plugin_content: str,
    runtime_content: str,
    dependencies: Mapping[str, str],
    direct_goal_plugins: frozenset[str],
) -> bool:
    conflicts, invalid_configs = opencode_direct_goal_plugin_conflicts(
        opencode_root, direct_goal_plugins
    )
    if invalid_configs:
        installed.append(
            installed_entry(
                surface="opencode",
                host_surfaces=["opencode"],
                mechanism="opencode_goal_bridge",
                command="/goal",
                path=plugin_path,
                status="blocked_invalid_opencode_config",
                reason=(
                    "Repair the listed OpenCode JSON/JSONC config before installing "
                    "the bridge so direct plugin conflicts can be checked safely."
                ),
                invalid_configs=invalid_configs,
            )
        )
        return True
    if conflicts:
        installed.append(
            installed_entry(
                surface="opencode",
                host_surfaces=["opencode"],
                mechanism="opencode_goal_bridge",
                command="/goal",
                path=plugin_path,
                status="blocked_conflicting_direct_plugin",
                reason=(
                    "Remove direct goal-plugin registration from the listed "
                    "OpenCode config, then rerun installation. The bridge imports "
                    "the pinned plugin and both must not be loaded independently."
                ),
                conflicts=conflicts,
            )
        )
        return True
    user_owned_bridge_paths = _user_owned_target_paths(
        ((plugin_path, plugin_content), (runtime_path, runtime_content))
    )
    if user_owned_bridge_paths:
        installed.append(
            installed_entry(
                surface="opencode",
                host_surfaces=["opencode"],
                mechanism="opencode_goal_bridge",
                command="/goal",
                path=plugin_path,
                status="blocked_user_owned_bridge_file",
                reason=(
                    "Move or rename the listed user-owned OpenCode bridge "
                    "files before installing LoopX so no partial bridge or "
                    "dependency update is applied."
                ),
                conflicts=user_owned_bridge_paths,
            )
        )
        return True
    package_status = target_package_dependencies(
        package_path, dependencies, execute=False
    )
    if package_status == "blocked_invalid_user_package_json":
        installed.append(
            installed_entry(
                surface="opencode",
                host_surfaces=["opencode"],
                mechanism="opencode_goal_dependencies",
                command="/goal",
                path=package_path,
                status=package_status,
            )
        )
        return True
    return False


def install_opencode_surface(
    *,
    installed: list[dict[str, object]],
    opencode_root: Path,
    facade_specs: list[CommandFacadeSpec],
    specs: list[CommandFacadeSpec],
    with_goal_bridge: bool,
    execute: bool,
    uninstall: bool,
    dependencies: Mapping[str, str],
    direct_goal_plugins: frozenset[str],
    render_command_body: Callable[[CommandFacadeSpec], str],
) -> None:
    """Install/retire OpenCode skills, optional goal bridge, and command facades."""
    # OpenCode reads global skills from OPENCODE_CONFIG_DIR/skills. The
    # static command facade stays as it is — a command is something the user
    # types, a skill is something the model can reach for itself.
    _install_skill_facade(
        specs=facade_specs,
        installed=installed,
        skills_dir=opencode_root / "skills",
        surface="opencode",
        host_surfaces=["opencode"],
        mechanism="opencode_skills",
        execute=execute,
        uninstall=uninstall,
    )
    commands_dir = opencode_root / "commands"
    plugin_path = opencode_root / "plugins" / "loopx-goal.js"
    runtime_path = opencode_root / "loopx" / "goal-bridge-runtime.mjs"
    package_path = opencode_root / "package.json"
    plugin_content = plugin_source()
    runtime_content = runtime_source()

    bridge_preflight_blocked = False
    if with_goal_bridge and not uninstall:
        bridge_preflight_blocked = _opencode_bridge_preflight(
            installed=installed,
            opencode_root=opencode_root,
            plugin_path=plugin_path,
            runtime_path=runtime_path,
            package_path=package_path,
            plugin_content=plugin_content,
            runtime_content=runtime_content,
            dependencies=dependencies,
            direct_goal_plugins=direct_goal_plugins,
        )

    if with_goal_bridge and not bridge_preflight_blocked:
        if uninstall:
            _append_unit_entries(
                installed=installed,
                surface="opencode",
                command="/goal",
                invoke_as=["/goal", "loopx_goal_activate"],
                entries=(
                    ("opencode_goal_bridge", plugin_path, plugin_content),
                    ("opencode_goal_bridge_runtime", runtime_path, runtime_content),
                ),
                uninstall=True,
                execute=execute,
            )
            installed.append(
                installed_entry(
                    surface="opencode",
                    host_surfaces=["opencode"],
                    mechanism="opencode_goal_dependencies",
                    command="/goal",
                    path=package_path,
                    status="preserved_shared_dependencies",
                )
            )
        else:
            package_status = target_package_dependencies(
                package_path, dependencies, execute=execute
            )
            installed.append(
                installed_entry(
                    surface="opencode",
                    host_surfaces=["opencode"],
                    mechanism="opencode_goal_dependencies",
                    command="/goal",
                    path=package_path,
                    status=package_status,
                )
            )
            if package_status == "blocked_invalid_user_package_json":
                bridge_preflight_blocked = True
            else:
                _append_unit_entries(
                    installed=installed,
                    surface="opencode",
                    command="/goal",
                    invoke_as=["/goal", "loopx_goal_activate"],
                    entries=(
                        ("opencode_goal_bridge_runtime", runtime_path, runtime_content),
                        ("opencode_goal_bridge", plugin_path, plugin_content),
                    ),
                    uninstall=False,
                    execute=execute,
                )

    if not bridge_preflight_blocked:
        for spec in specs:
            path = commands_dir / f"{spec['name']}.md"
            status = (
                _retire_status(path, execute=execute)
                if uninstall
                else _target_status(path, render_command_body(spec), execute=execute)
            )
            installed.append(
                installed_entry(
                    surface="opencode",
                    host_surfaces=["opencode"],
                    mechanism="opencode_commands",
                    command=spec["command"],
                    path=path,
                    status=status,
                    invoke_as=[str(spec["command"])],
                )
            )


def install_pi_surface(
    *,
    installed: list[dict[str, object]],
    extension_path: Path,
    runtime_path: Path,
    extension_content: str,
    runtime_content: str,
    execute: bool,
    uninstall: bool,
) -> None:
    """Install/retire Pi goal extension and loop runtime as an atomic unit."""
    # Uninstall retires managed files per target; install preflights both targets
    # so an unmanaged user file cannot leave an incompatible partial runtime.
    if not uninstall:
        user_owned_pi_paths = _user_owned_target_paths(
            ((extension_path, extension_content), (runtime_path, runtime_content))
        )
        if user_owned_pi_paths:
            installed.append(
                installed_entry(
                    surface="pi",
                    host_surfaces=["pi"],
                    mechanism="pi_goal_extension",
                    command="/loopx",
                    path=extension_path,
                    status="blocked_user_owned_pi_file",
                    invoke_as=["/loopx", "loopx_goal_activate"],
                    reason=(
                        "Move or rename the listed user-owned Pi files before "
                        "installing LoopX so the adapter and its loop runtime "
                        "are installed as one atomic unit; no Pi file was written."
                    ),
                    conflicts=user_owned_pi_paths,
                )
            )
            return
    _append_unit_entries(
        installed=installed,
        surface="pi",
        command="/loopx",
        invoke_as=["/loopx", "loopx_goal_activate"],
        entries=(
            ("pi_goal_extension", extension_path, extension_content),
            ("pi_goal_extension_runtime", runtime_path, runtime_content),
        ),
        uninstall=uninstall,
        execute=execute,
    )
