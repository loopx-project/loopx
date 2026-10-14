"""Maintain one Lark document section and whiteboard per Evidence Stage."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from ..cli_transport import (
    CommandRunner,
    command_error,
    run_command,
)


class StageDocumentConfig(Protocol):
    cli_bin: str
    identity: str


LocalConfigReader = Callable[[Path], dict[str, Any]]
LocalConfigWriter = Callable[[Path, Mapping[str, Any]], None]

_MANAGED_STAGE_PARAGRAPH_SUFFIX = "完整 Nodes / Edges / Findings 仍以同一 Base 为准。"


def _stage_section_prefix(*, role: str) -> str:
    return "Executive Evidence Stage" if role == "executive" else "Evidence Stage"


def _stage_section_title(*, role: str, stage_index: int) -> str:
    return f"{_stage_section_prefix(role=role)} {stage_index:02d}"


def _stage_section_xml(stage: Mapping[str, Any], *, role: str) -> str:
    stage_index = int(stage.get("stage_index") or 0)
    lane_labels = {
        "fix_pr": "PR issue-fix",
        "capability": "LoopX capability",
        "default": "Explore work",
    }
    lanes = (
        ", ".join(
            lane_labels.get(str(item), str(item).replace("_", " ").title())
            for item in stage.get("lanes") or []
        )
        or "Explore work"
    )
    return (
        f"<h2>{_stage_section_title(role=role, stage_index=stage_index)}</h2>"
        "<p>"
        f"本阶段包含 {int(stage.get('primary_node_count') or 0)} 个主节点、"
        f"{int(stage.get('context_node_count') or 0)} 个关系上下文节点；"
        f"主线：{html.escape(lanes)}；"
        f"跨主线真实关系：{int(stage.get('cross_lane_edge_count') or 0)} 条。"
        "完整 Nodes / Edges / Findings 仍以同一 Base 为准。"
        '</p><whiteboard type="blank"></whiteboard>'
    )


def _created_stage_section(command: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = command.get("json")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    document = data.get("document") if isinstance(data, Mapping) else None
    blocks = document.get("new_blocks") if isinstance(document, Mapping) else None
    whiteboard = next(
        (
            dict(block)
            for block in blocks or []
            if isinstance(block, Mapping)
            and str(block.get("block_type") or "") == "whiteboard"
            and str(block.get("block_token") or "").strip()
        ),
        None,
    )
    if not whiteboard:
        return None
    return {
        "whiteboard_block_id": str(whiteboard.get("block_id") or "").strip() or None,
        "whiteboard_token": str(whiteboard.get("block_token") or "").strip(),
        "generated_block_ids": [
            str(block.get("block_id") or "").strip()
            for block in blocks or []
            if isinstance(block, Mapping) and str(block.get("block_id") or "").strip()
        ],
    }


def _document_content(command: Mapping[str, Any]) -> str:
    payload = command.get("json")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    document = data.get("document") if isinstance(data, Mapping) else None
    return str(document.get("content") or "") if isinstance(document, Mapping) else ""


def _document_revision(command: Mapping[str, Any]) -> int | None:
    payload = command.get("json")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    document = data.get("document") if isinstance(data, Mapping) else None
    revision = document.get("revision_id") if isinstance(document, Mapping) else None
    try:
        return int(revision) if revision is not None else None
    except (TypeError, ValueError):
        return None


def _xml_root(command: Mapping[str, Any]) -> ET.Element | None:
    content = _document_content(command).strip()
    if not content:
        return None
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def _stage_headings(
    command: Mapping[str, Any], *, role: str
) -> list[dict[str, Any]] | None:
    root = _xml_root(command)
    if root is None:
        return None
    heading_pattern = re.compile(
        rf"^{re.escape(_stage_section_prefix(role=role))} (\d+)$"
    )
    headings: list[dict[str, Any]] = []
    for element in root.iter():
        if element.tag != "h2":
            continue
        title = "".join(element.itertext()).strip()
        match = heading_pattern.fullmatch(title)
        block_id = str(element.get("id") or "").strip()
        if match and block_id:
            headings.append(
                {
                    "stage_index": int(match.group(1)),
                    "heading_block_id": block_id,
                }
            )
    return headings


def _managed_stage_section(
    command: Mapping[str, Any], *, role: str, stage_index: int
) -> dict[str, Any] | None:
    root = _xml_root(command)
    if root is None:
        return None
    blocks = list(root) if root.tag == "fragment" else [root]
    if len(blocks) != 3:
        return None
    heading, paragraph, whiteboard = blocks
    expected_title = _stage_section_title(role=role, stage_index=stage_index)
    if (
        heading.tag != "h2"
        or "".join(heading.itertext()).strip() != expected_title
        or paragraph.tag != "p"
        or not "".join(paragraph.itertext())
        .strip()
        .endswith(_MANAGED_STAGE_PARAGRAPH_SUFFIX)
        or whiteboard.tag != "whiteboard"
    ):
        return None
    block_ids = [
        str(block.get("id") or "").strip() for block in (heading, paragraph, whiteboard)
    ]
    whiteboard_token = str(whiteboard.get("token") or "").strip()
    if not all(block_ids) or not whiteboard_token:
        return None
    return {
        "stage_index": stage_index,
        "section_title": expected_title,
        "heading_block_id": block_ids[0],
        "whiteboard_block_id": block_ids[2],
        "whiteboard_token": whiteboard_token,
        "generated_block_ids": block_ids,
    }


def _docs_fetch_command(
    config: StageDocumentConfig,
    *,
    docx_token: str,
    scope: str,
    start_block_id: str | None = None,
) -> list[str]:
    command = [
        config.cli_bin,
        "docs",
        "+fetch",
        "--as",
        config.identity,
        "--doc",
        docx_token,
        "--scope",
        scope,
    ]
    if scope == "outline":
        command.extend(["--max-depth", "2"])
    if start_block_id:
        command.extend(["--start-block-id", start_block_id])
    command.extend(["--detail", "with-ids", "--format", "json"])
    return command


def ensure_stage_whiteboards(
    config: StageDocumentConfig,
    *,
    role: str,
    role_sink: Mapping[str, Any],
    stage_views: list[Mapping[str, Any]],
    config_path: Path,
    execute: bool,
    runner: CommandRunner,
    read_local_config: LocalConfigReader,
    write_local_config: LocalConfigWriter,
) -> tuple[
    dict[int, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    str | None,
]:
    """Reconcile, resolve, or create stage boards and checkpoint every mutation."""

    configured = {
        int(item.get("stage_index") or 0): dict(item)
        for item in role_sink.get("stage_whiteboards") or []
        if isinstance(item, Mapping)
        and int(item.get("stage_index") or 0) > 0
        and str(item.get("whiteboard_token") or "").strip()
    }
    if not configured and str(role_sink.get("whiteboard_token") or "").strip():
        configured[1] = {
            "stage_index": 1,
            "whiteboard_token": str(role_sink.get("whiteboard_token") or "").strip(),
        }
    configured_before = {index: dict(item) for index, item in configured.items()}
    desired_indexes = {
        int(stage.get("stage_index") or 0)
        for stage in stage_views
        if int(stage.get("stage_index") or 0) > 0
    }
    stale_configured_indexes = sorted(set(configured) - desired_indexes)
    docx_token = str(role_sink.get("docx_token") or "").strip()

    def persist_configured_stages() -> None:
        local = read_local_config(config_path)
        updated = {
            key: value
            for key, value in local.items()
            if key not in {"ok", "exists", "path", "updated_at"}
        }
        sinks = dict(updated.get("visual_sinks") or {})
        persisted_sink = dict(sinks.get(role) or role_sink)
        persisted_sink["stage_whiteboards"] = [
            configured[index] for index in sorted(configured)
        ]
        sinks[role] = persisted_sink
        updated["visual_sinks"] = sinks
        write_local_config(config_path, updated)

    reconciliation_commands: list[dict[str, Any]] = []
    reconciliation: dict[str, Any] = {
        "required": bool(stale_configured_indexes),
        "performed": False,
        "remote_checked": False,
        "stale_stage_indexes": stale_configured_indexes,
        "missing_remote_stage_indexes": [],
        "duplicate_stage_indexes": [],
        "adopted_stage_indexes": [],
        "deleted_section_count": 0,
        "deleted_block_count": 0,
    }

    def observe(command: Mapping[str, Any], *, action: str) -> None:
        reconciliation_commands.append(
            {
                "action": action,
                "ok": bool(command.get("ok")),
                "returncode": command.get("returncode"),
                "error": None if command.get("ok") else command_error(command),
            }
        )

    if execute and docx_token:
        outline_command = run_command(
            _docs_fetch_command(
                config,
                docx_token=docx_token,
                scope="outline",
            ),
            execute=True,
            runner=runner,
            cwd=config_path.parent,
        )
        observe(outline_command, action="fetch_stage_outline")
        headings = (
            _stage_headings(outline_command, role=role)
            if outline_command.get("ok")
            else None
        )
        if headings is None:
            reconciliation["required"] = True
            return (
                configured,
                [],
                reconciliation_commands,
                reconciliation,
                command_error(outline_command)
                if not outline_command.get("ok")
                else "Evidence Stage outline could not be parsed",
            )
        reconciliation["remote_checked"] = True
        headings_by_stage: dict[int, list[str]] = defaultdict(list)
        for heading in headings:
            headings_by_stage[int(heading["stage_index"])].append(
                str(heading["heading_block_id"])
            )
        stale_remote_indexes = sorted(
            index for index in headings_by_stage if index not in desired_indexes
        )
        duplicate_stage_indexes = sorted(
            index
            for index, block_ids in headings_by_stage.items()
            if index in desired_indexes and len(block_ids) > 1
        )
        managed_configured_indexes = {
            index
            for index, item in configured.items()
            if index != 1
            or str(item.get("section_title") or "").strip()
            == _stage_section_title(role=role, stage_index=1)
            or bool(item.get("whiteboard_block_id"))
            or bool(item.get("generated_block_ids"))
        }
        missing_remote_stage_indexes = sorted(
            index
            for index in desired_indexes & managed_configured_indexes
            if not headings_by_stage.get(index)
        )
        reconciliation["stale_stage_indexes"] = sorted(
            set(stale_configured_indexes) | set(stale_remote_indexes)
        )
        reconciliation["missing_remote_stage_indexes"] = missing_remote_stage_indexes
        reconciliation["duplicate_stage_indexes"] = duplicate_stage_indexes
        reconciliation["required"] = bool(
            reconciliation["stale_stage_indexes"]
            or missing_remote_stage_indexes
            or duplicate_stage_indexes
        )

        # A readable whiteboard token does not prove that the board is still
        # embedded in the configured document. Drop detached checkpoints so
        # the normal missing-stage path recreates the managed section.
        for stage_index in missing_remote_stage_indexes:
            configured.pop(stage_index, None)

        sections_by_stage: dict[int, list[dict[str, Any]]] = defaultdict(list)
        indexes_to_inspect = sorted(
            set(stale_remote_indexes)
            | set(duplicate_stage_indexes)
            | {
                index
                for index in desired_indexes
                if index not in configured and headings_by_stage.get(index)
            }
        )
        for stage_index in indexes_to_inspect:
            for heading_block_id in headings_by_stage.get(stage_index, []):
                section_command = run_command(
                    _docs_fetch_command(
                        config,
                        docx_token=docx_token,
                        scope="section",
                        start_block_id=heading_block_id,
                    ),
                    execute=True,
                    runner=runner,
                    cwd=config_path.parent,
                )
                observe(
                    section_command,
                    action=f"fetch_stage_section_{stage_index:02d}",
                )
                section = (
                    _managed_stage_section(
                        section_command,
                        role=role,
                        stage_index=stage_index,
                    )
                    if section_command.get("ok")
                    else None
                )
                if section is None:
                    reconciliation["required"] = True
                    return (
                        configured,
                        [],
                        reconciliation_commands,
                        reconciliation,
                        command_error(section_command)
                        if not section_command.get("ok")
                        else (
                            f"Evidence Stage {stage_index:02d} conflicts with an "
                            "unmanaged document section; refusing destructive cleanup"
                        ),
                    )
                sections_by_stage[stage_index].append(section)

        sections_to_delete: list[dict[str, Any]] = []
        for stage_index in stale_remote_indexes:
            sections_to_delete.extend(sections_by_stage[stage_index])
        adopted_stage_indexes: list[int] = []
        for stage_index in sorted(desired_indexes):
            sections = sections_by_stage.get(stage_index, [])
            if stage_index not in configured and sections:
                keep = sections[-1]
                configured[stage_index] = {
                    key: value
                    for key, value in keep.items()
                    if key != "heading_block_id"
                }
                adopted_stage_indexes.append(stage_index)
                sections_to_delete.extend(sections[:-1])
                continue
            if len(sections) <= 1:
                continue
            configured_token = str(
                configured[stage_index].get("whiteboard_token") or ""
            ).strip()
            matching = [
                section
                for section in sections
                if section["whiteboard_token"] == configured_token
            ]
            if len(matching) != 1:
                reconciliation["required"] = True
                return (
                    configured,
                    [],
                    reconciliation_commands,
                    reconciliation,
                    (
                        f"Evidence Stage {stage_index:02d} has duplicate managed "
                        "sections but no unique configured whiteboard match"
                    ),
                )
            keep = matching[0]
            sections_to_delete.extend(
                section for section in sections if section is not keep
            )

        reconciliation["adopted_stage_indexes"] = adopted_stage_indexes
        if sections_to_delete:
            block_ids = [
                block_id
                for section in sections_to_delete
                for block_id in section["generated_block_ids"]
            ]
            delete_args = [
                config.cli_bin,
                "docs",
                "+update",
                "--as",
                config.identity,
                "--doc",
                docx_token,
                "--command",
                "block_delete",
                "--block-id",
                ",".join(block_ids),
            ]
            revision = _document_revision(outline_command)
            if revision is not None:
                delete_args.extend(["--revision-id", str(revision)])
            delete_args.extend(["--format", "json"])
            delete_command = run_command(
                delete_args,
                execute=True,
                runner=runner,
                cwd=config_path.parent,
            )
            observe(delete_command, action="delete_stale_stage_sections")
            if not delete_command.get("ok"):
                reconciliation["required"] = True
                return (
                    configured,
                    [],
                    reconciliation_commands,
                    reconciliation,
                    command_error(delete_command),
                )
            verification_command = run_command(
                _docs_fetch_command(
                    config,
                    docx_token=docx_token,
                    scope="outline",
                ),
                execute=True,
                runner=runner,
                cwd=config_path.parent,
            )
            observe(verification_command, action="verify_stage_outline")
            remaining = (
                _stage_headings(verification_command, role=role)
                if verification_command.get("ok")
                else None
            )
            if remaining is None:
                reconciliation["required"] = True
                return (
                    configured,
                    [],
                    reconciliation_commands,
                    reconciliation,
                    command_error(verification_command)
                    if not verification_command.get("ok")
                    else "updated Evidence Stage outline could not be parsed",
                )
            remaining_by_stage: dict[int, int] = defaultdict(int)
            for heading in remaining:
                remaining_by_stage[int(heading["stage_index"])] += 1
            if any(
                index not in desired_indexes or count > 1
                for index, count in remaining_by_stage.items()
            ):
                reconciliation["required"] = True
                return (
                    configured,
                    [],
                    reconciliation_commands,
                    reconciliation,
                    "Evidence Stage cleanup did not converge on remote readback",
                )
            reconciliation["deleted_section_count"] = len(sections_to_delete)
            reconciliation["deleted_block_count"] = len(block_ids)

        configured = {
            index: item
            for index, item in configured.items()
            if index in desired_indexes
        }
        if configured != configured_before:
            persist_configured_stages()
        reconciliation["performed"] = bool(
            reconciliation["stale_stage_indexes"]
            or reconciliation["missing_remote_stage_indexes"]
            or reconciliation["duplicate_stage_indexes"]
            or adopted_stage_indexes
        )
        reconciliation["required"] = False
    elif stale_configured_indexes:
        if not docx_token:
            return (
                configured,
                [],
                reconciliation_commands,
                reconciliation,
                "docx_token is required to reconcile stale Evidence Stage sections",
            )
        configured = {
            index: item
            for index, item in configured.items()
            if index in desired_indexes
        }

    missing = [
        stage
        for stage in stage_views
        if int(stage.get("stage_index") or 0) not in configured
    ]
    if missing and not docx_token:
        return (
            configured,
            [],
            reconciliation_commands,
            reconciliation,
            "docx_token is required to create missing Evidence Stage sections",
        )

    section_commands: list[dict[str, Any]] = []
    for stage in missing:
        stage_index = int(stage.get("stage_index") or 0)
        command = run_command(
            [
                config.cli_bin,
                "docs",
                "+update",
                "--as",
                config.identity,
                "--doc",
                docx_token,
                "--command",
                "append",
                "--content",
                _stage_section_xml(stage, role=role),
                "--format",
                "json",
            ],
            execute=execute,
            runner=runner,
            cwd=config_path.parent,
        )
        section_commands.append(command)
        if not command.get("ok"):
            return (
                configured,
                section_commands,
                reconciliation_commands,
                reconciliation,
                command_error(command),
            )
        if execute:
            section = _created_stage_section(command)
            if not section:
                return (
                    configured,
                    section_commands,
                    reconciliation_commands,
                    reconciliation,
                    f"Evidence Stage {stage_index:02d} section was created "
                    "without a whiteboard token",
                )
            configured[stage_index] = {
                "stage_index": stage_index,
                "section_title": _stage_section_title(
                    role=role, stage_index=stage_index
                ),
                **section,
            }
            persist_configured_stages()
        else:
            configured[stage_index] = {
                "stage_index": stage_index,
                "section_title": _stage_section_title(
                    role=role, stage_index=stage_index
                ),
                "whiteboard_token": f"planned-stage-{stage_index:02d}",
            }

    if execute and docx_token and missing:
        verification_command = run_command(
            _docs_fetch_command(
                config,
                docx_token=docx_token,
                scope="outline",
            ),
            execute=True,
            runner=runner,
            cwd=config_path.parent,
        )
        observe(verification_command, action="verify_created_stage_membership")
        verified_headings = (
            _stage_headings(verification_command, role=role)
            if verification_command.get("ok")
            else None
        )
        if verified_headings is None:
            reconciliation["required"] = True
            return (
                configured,
                section_commands,
                reconciliation_commands,
                reconciliation,
                command_error(verification_command)
                if not verification_command.get("ok")
                else "created Evidence Stage outline could not be parsed",
            )
        verified_counts: dict[int, int] = defaultdict(int)
        for heading in verified_headings:
            verified_counts[int(heading["stage_index"])] += 1
        managed_desired_indexes = {
            index
            for index in desired_indexes
            if index != 1
            or str(configured.get(index, {}).get("section_title") or "").strip()
            == _stage_section_title(role=role, stage_index=1)
            or bool(configured.get(index, {}).get("whiteboard_block_id"))
            or bool(configured.get(index, {}).get("generated_block_ids"))
        }
        missing_after_create = sorted(
            index
            for index in managed_desired_indexes
            if verified_counts.get(index) != 1
        )
        if missing_after_create:
            reconciliation["required"] = True
            return (
                configured,
                section_commands,
                reconciliation_commands,
                reconciliation,
                "created Evidence Stage sections are absent from document readback: "
                + ", ".join(f"{index:02d}" for index in missing_after_create),
            )
    return (
        configured,
        section_commands,
        reconciliation_commands,
        reconciliation,
        None,
    )
