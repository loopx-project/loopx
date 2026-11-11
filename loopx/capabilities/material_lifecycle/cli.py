from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from loopx.capabilities.project_skill_delivery import PROJECT_SKILL_SURFACES

from ._validation import check_record_keys
from .architecture import build_material_lifecycle_architecture_packet
from .project_skill import (
    install_project_material_skill,
    inspect_project_material_skill,
    uninstall_project_material_skill,
)
from .ranking import (
    MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION,
    plan_material_wave_rerank,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]

_CLI_WAVE_RERANK_FIELDS = {
    "annotations",
    "default_evidence_refs",
    "max_orthogonal_in_top_window",
    "ordered_material_refs",
    "protected_material_refs",
    "required_load_bearing_refs",
    "top_window_size",
}


def _collection_size(value: object) -> int:
    if isinstance(value, Collection) and not isinstance(value, (str, bytes)):
        return len(value)
    return 0


def _load_wave_rerank_cli_payload(input_spec: str) -> dict[str, object]:
    raw_text = (
        sys.stdin.read()
        if input_spec == "-"
        else Path(input_spec).expanduser().read_text(encoding="utf-8")
    )
    decoded: Any = json.loads(raw_text)
    if not isinstance(decoded, Mapping):
        raise ValueError("wave rerank input must be a JSON object")
    check_record_keys(
        decoded,
        field="request",
        allowed=_CLI_WAVE_RERANK_FIELDS,
        required={"ordered_material_refs"},
    )
    reordered_refs, summary = plan_material_wave_rerank(
        decoded["ordered_material_refs"],
        decoded.get("annotations"),
        top_window_size=(
            30
            if decoded.get("top_window_size") is None
            else decoded["top_window_size"]
        ),
        protected_material_refs=decoded.get("protected_material_refs"),
        required_load_bearing_refs=decoded.get("required_load_bearing_refs"),
        max_orthogonal_in_top_window=(
            0
            if decoded.get("max_orthogonal_in_top_window") is None
            else decoded["max_orthogonal_in_top_window"]
        ),
        default_evidence_refs=decoded.get("default_evidence_refs"),
    )
    return {
        "ok": True,
        "status": "planned",
        "capability": {
            "capability_id": "material_lifecycle",
            "default_enabled": False,
            "creates_authority": False,
            "mutates_core_state": False,
        },
        "apply_authorized": False,
        "reordered_material_refs": reordered_refs,
        **summary,
    }


def _render(payload: dict[str, object]) -> str:
    if payload.get("skill_id") == "loopx-material":
        lines = [
            "# LoopX Material Project Skill",
            "",
            f"- status: `{payload.get('status')}`",
            f"- mode: `{payload.get('mode') or 'inspect'}`",
            f"- project_connected: `{payload.get('project_connected')}`",
            f"- managed: `{payload.get('managed')}`",
            f"- changed: `{payload.get('changed')}`",
        ]
        surface_items = payload.get("surfaces")
        for item in surface_items if isinstance(surface_items, list) else []:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('surface')}: `{item.get('status')}` at "
                    f"`{item.get('target')}`"
                )
        if payload.get("error"):
            lines.append(f"- error: `{payload.get('error')}`")
        return "\n".join([*lines, ""])
    if payload.get("schema_version") == MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION:
        return "\n".join(
            [
                "# Material Wave Rerank Plan",
                "",
                f"- status: `{payload.get('status')}`",
                f"- no_change: `{payload.get('no_change')}`",
                f"- item_count: `{_collection_size(payload.get('reordered_material_refs'))}`",
                f"- moved_count: `{_collection_size(payload.get('moves'))}`",
                f"- top_window_size: `{payload.get('top_window_size')}`",
                f"- top_window_orthogonal_count: `{payload.get('top_window_orthogonal_count')}`",
                f"- apply_authorized: `{payload.get('apply_authorized')}`",
                "",
            ]
        )
    capability = payload.get("capability")
    capability_id = (
        capability.get("capability_id")
        if isinstance(capability, dict)
        else "material_lifecycle"
    )
    lines = [
        "# Material Lifecycle",
        "",
        f"- status: `{payload.get('status')}`",
        f"- capability_id: `{capability_id}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: `{payload.get('error')}`")
    else:
        lines.append(
            f"- contract_schemas: `{_collection_size(payload.get('contract_schemas'))}`"
        )
    return "\n".join([*lines, ""])


def register_material_lifecycle_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "material-lifecycle",
        help="Inspect the backup-safe material inventory and rerank contract.",
    )
    commands = parser.add_subparsers(
        dest="material_lifecycle_command",
        required=True,
    )
    architecture = commands.add_parser(
        "architecture",
        help="Render the default-off Stage-0 Material Lifecycle contract.",
    )
    add_subcommand_format(architecture)
    plan_wave_rerank = commands.add_parser(
        "plan-wave-rerank",
        help="Preview a deterministic wave-based material rerank from exact-read annotations.",
    )
    plan_wave_rerank.add_argument(
        "--input",
        required=True,
        help="Path to a public-safe wave rerank JSON request or '-' for stdin.",
    )
    add_subcommand_format(plan_wave_rerank)
    skill_status = commands.add_parser(
        "skill-status",
        help="Inspect the project-local managed loopx-material skill.",
    )
    skill_status.add_argument("--project", default=".")
    skill_status.add_argument(
        "--surface",
        action="append",
        choices=PROJECT_SKILL_SURFACES,
    )
    add_subcommand_format(skill_status)
    install_skill = commands.add_parser(
        "install-skill",
        help="Preview or install the managed loopx-material skill into one connected project.",
    )
    install_skill.add_argument("--project", default=".")
    install_skill.add_argument(
        "--surface",
        action="append",
        choices=PROJECT_SKILL_SURFACES,
    )
    install_skill.add_argument("--execute", action="store_true")
    add_subcommand_format(install_skill)
    uninstall_skill = commands.add_parser(
        "uninstall-skill",
        help="Preview or uninstall the managed loopx-material skill from one project.",
    )
    uninstall_skill.add_argument("--project", default=".")
    uninstall_skill.add_argument(
        "--surface",
        action="append",
        choices=PROJECT_SKILL_SURFACES,
    )
    uninstall_skill.add_argument("--execute", action="store_true")
    add_subcommand_format(uninstall_skill)


def handle_material_lifecycle_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "material-lifecycle":
        return None
    try:
        if args.material_lifecycle_command == "architecture":
            payload = build_material_lifecycle_architecture_packet()
        elif args.material_lifecycle_command == "plan-wave-rerank":
            payload = _load_wave_rerank_cli_payload(args.input)
        elif args.material_lifecycle_command == "skill-status":
            payload = inspect_project_material_skill(
                args.project,
                surfaces=tuple(args.surface or ("codex",)),
            )
        elif args.material_lifecycle_command == "install-skill":
            payload = install_project_material_skill(
                args.project,
                surfaces=tuple(args.surface or ("codex",)),
                execute=args.execute,
            )
        else:
            payload = uninstall_project_material_skill(
                args.project,
                surfaces=tuple(args.surface or ("codex",)),
                execute=args.execute,
            )
    except (OSError, ValueError, TypeError) as exc:
        if args.material_lifecycle_command == "plan-wave-rerank":
            payload = {
                "ok": False,
                "status": "blocked",
                "capability": {
                    "capability_id": "material_lifecycle",
                    "default_enabled": False,
                    "creates_authority": False,
                    "mutates_core_state": False,
                },
                "apply_authorized": False,
                "error": str(exc),
            }
        else:
            payload = {
                "ok": False,
                "status": "blocked",
                "skill_id": "loopx-material",
                "error": str(exc),
                "surfaces": [],
            }
        print_payload(payload, output_format(args), _render)
        return 2
    print_payload(payload, output_format(args), _render)
    return 0
