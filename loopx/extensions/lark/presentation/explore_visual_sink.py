"""Whiteboard visual-sink configuration, redaction, and publishing for Explore."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ....capabilities.explore.result_log import build_explore_graph_view
from ....presentation.explore_views import (
    PRESENTATION_MODE_DUAL_VIEW,
    build_explore_presentation_bundle,
    build_vertical_explore_mermaid,
    explore_source_digest,
    explore_source_revision,
    validate_explore_view_freshness,
)
from ..cli_transport import DEFAULT_CLI_BIN, CommandRunner, command_error, default_subprocess_runner
from .explore_stage_document import ensure_stage_whiteboards
from .explore_visual_integrity import (
    cross_role_stage_token_conflict_delivery,
    finalize_visual_role_results,
)
from .explore_visual_readback import (
    defer_visual_stage_after_host_timeout,
    publish_visual_with_readback,
    settle_visual_stage_readbacks,
)
from .explore_visual_styles import (
    BOARD_STYLE_AUTO_FLOW,
    ExploreBoardStyle,
    board_source_with_delivery_marker,
    explore_board_style,
    resolve_explore_board_style,
    resolve_explore_board_style_update,
    summarize_explore_visual_sync,
)
from .kanban import _redact_credential_shapes, _redact_public_safe_shapes
from .sink_visibility import SINK_VISIBILITIES, SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITY_SHARED

if TYPE_CHECKING:
    from .explore_results import LarkExploreConfig

LARK_EXPLORE_VISUAL_SYNC_VERSION = "loopx_lark_explore_visual_sync_v0"
LARK_EXPLORE_VISUALS_SYNC_VERSION = "loopx_lark_explore_visuals_sync_v0"

AUTO_DOC_TITLE = "LoopX Explore 实时探索拓扑"
AUTO_DOC_APPEND_XML = (
    "<h1>实时探索拓扑</h1>"
    "<p>Executive 总览画板；由 feishu-sync 自动更新。</p>"
    '<whiteboard type="blank"></whiteboard>'
    "<h2>LoopX Explore · Canonical 全局画板</h2>"
    "<p>Canonical 全局画板；由 feishu-sync 自动更新。</p>"
    '<whiteboard type="blank"></whiteboard>'
)
AUTO_DOC_CREATE_XML = f"<title>{AUTO_DOC_TITLE}</title>{AUTO_DOC_APPEND_XML}"

_EXPLORE_NODE_DISPLAY_FIELDS = ("title", "summary", "blocked_reason")
_EXPLORE_EDGE_DISPLAY_FIELDS = ("edge_type", "summary")
_SVG_TEXT_SPAN_PATTERN = re.compile(
    r"(?P<open><text\b[^>]*>)(?P<content>[^<]*)(?P<close></text>)"
)
_MERMAID_LABEL_PATTERN = re.compile(r'"(?P<content>[^"\n]*)"')


def editable_local_explore_config(local: Mapping[str, Any]) -> dict[str, Any]:
    """Return persisted config fields without derived status metadata."""

    return {
        key: value
        for key, value in local.items()
        if key not in {"ok", "exists", "path", "updated_at"}
    }


def _created_overview_boards(command: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return newly created whiteboard blocks in document order."""

    payload = command.get("json")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    document = data.get("document") if isinstance(data, Mapping) else None
    blocks = document.get("new_blocks") if isinstance(document, Mapping) else None
    boards = []
    for block in blocks or []:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("block_type") or "")
        block_token = str(block.get("block_token") or "").strip()
        if block_type == "whiteboard" and block_token:
            boards.append(
                {
                    "block_id": str(block.get("block_id") or "").strip() or None,
                    "block_token": block_token,
                }
            )
    return boards


def _build_visual_sink_entry(
    *,
    whiteboard_token: str,
    docx_token: str,
    statuses: list[str] | None,
    tags: list[str] | None,
    projection_mode: str,
    include_ancestors: bool,
    mermaid_node_limit: int,
    stage_capacity: int,
    style: ExploreBoardStyle,
    tokens: list[str],
    stage_enabled: bool = True,
    view_role: str | None = None,
    overview_token: str | None = None,
) -> dict[str, Any]:
    sink: dict[str, Any] = {
        "schema_version": "loopx_lark_explore_visual_sink_config_v0",
        "whiteboard_token": whiteboard_token or None,
        "docx_token": docx_token or None,
        "statuses": [str(item) for item in statuses or [] if str(item).strip()],
        "tags": [str(item) for item in tags or [] if str(item).strip()],
        "projection_mode": projection_mode,
        "include_ancestors": bool(include_ancestors),
        "mermaid_node_limit": max(1, int(mermaid_node_limit)),
        "stage_capacity": int(stage_capacity),
        "board_style": style.name,
        "renderer": style.renderer,
        "presentation_mode": "stage_document",
        "stage_boards_enabled": stage_enabled,
        "stage_whiteboards": [
            {"stage_index": index, "whiteboard_token": stage_token}
            for index, stage_token in enumerate(tokens, start=1)
        ],
    }
    if view_role:
        sink["view_role"] = view_role
    if overview_token:
        sink["overview_whiteboard_token"] = overview_token
    return sink


def configure_lark_explore_visual_sink(
    *,
    config_path: Path,
    whiteboard_token: str | None = None,
    docx_token: str | None = None,
    statuses: list[str] | None = None,
    tags: list[str] | None = None,
    projection_mode: str = "canonical_filtered",
    include_ancestors: bool = True,
    mermaid_node_limit: int = 100,
    stage_capacity: int = 14,
    stage_whiteboard_tokens: list[str] | None = None,
    board_style: str | None = None,
    view_role: str | None = None,
    auto_create: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Configure an optional owner-facing whiteboard over canonical Explore data.

    ``auto_create=True`` bootstraps the hosting document and the two overview
    boards (executive topology + canonical global) without manual token
    plumbing. Canonical Evidence Stage boards are created lazily by
    ``feishu-sync`` unless explicit stage tokens are supplied.
    """

    from . import explore_results as _explore_results

    token = str(whiteboard_token or "").strip()
    document_token = str(docx_token or "").strip()
    if not token and not document_token and not auto_create:
        raise ValueError("whiteboard_token or docx_token is required")
    valid_modes = {
        "canonical_filtered",
        "issue_fix_two_lane",
        "canonical_full",
        "executive_auto",
    }
    if projection_mode not in valid_modes:
        raise ValueError(f"projection_mode must be one of {sorted(valid_modes)}")
    role = str(view_role or "").strip() or None
    if role not in {None, "canonical", "executive"}:
        raise ValueError("view_role must be canonical or executive")
    expected_mode = {"canonical": "canonical_full", "executive": "executive_auto"}.get(role)
    if expected_mode and projection_mode != expected_mode:
        raise ValueError(f"view_role {role} requires projection_mode {expected_mode}")
    if not 10 <= int(stage_capacity) <= 20:
        raise ValueError("stage_capacity must be between 10 and 20")
    local = _explore_results.read_lark_explore_local_config(config_path)
    if not local.get("ok") or not local.get("exists"):
        raise ValueError("run `loopx explore feishu-setup` before configuring a visual sink")
    visual_sinks = local.get("visual_sinks")
    existing_sink = (
        visual_sinks.get(role)
        if role and isinstance(visual_sinks, Mapping)
        else local.get("visual_sink") if not role else None
    )
    style = resolve_explore_board_style_update(
        board_style,
        existing_sink if isinstance(existing_sink, Mapping) else {},
    )
    tokens = [
        str(item).strip()
        for item in stage_whiteboard_tokens or []
        if str(item).strip()
    ]
    if not tokens and token:
        tokens = [token]
    elif token and token not in tokens:
        tokens.insert(0, token)

    def _auto_failure(error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "schema_version": "loopx_lark_explore_visual_sink_configure_v0",
            "execute": execute,
            "status": "auto_create_failed",
            "config_path": str(config_path),
            "view_role": role,
            "error": error,
        }

    overview_boards: list[dict[str, Any]] = []
    auto_created_document: dict[str, Any] | None = None
    if auto_create:
        if execute:
            board_meta = local.get("board") if isinstance(local.get("board"), dict) else {}
            cli_bin = str(board_meta.get("cli_bin") or DEFAULT_CLI_BIN)
            identity = str(board_meta.get("identity") or "user")
            stored_sinks = local.get("visual_sinks") if isinstance(local.get("visual_sinks"), Mapping) else {}
            stored_canonical = stored_sinks.get("canonical") if isinstance(stored_sinks.get("canonical"), Mapping) else {}
            stored_executive = stored_sinks.get("executive") if isinstance(stored_sinks.get("executive"), Mapping) else {}
            if not document_token:
                document_token = str(stored_canonical.get("docx_token") or stored_executive.get("docx_token") or "").strip()
            if (
                document_token
                and str(stored_canonical.get("overview_whiteboard_token") or "").strip()
                and str(stored_executive.get("overview_whiteboard_token") or "").strip()
            ):
                overview_boards = [
                    {"block_token": str(stored_executive["overview_whiteboard_token"])},
                    {"block_token": str(stored_canonical["overview_whiteboard_token"])},
                ]
            else:
                doc_args = (
                    [cli_bin, "docs", "+update", "--as", identity, "--doc", document_token,
                     "--command", "append", "--content", AUTO_DOC_APPEND_XML, "--format", "json"]
                    if document_token
                    else [cli_bin, "docs", "+create", "--as", identity, "--title", AUTO_DOC_TITLE,
                          "--content", AUTO_DOC_CREATE_XML, "--format", "json"]
                )
                doc_command = _explore_results.run_command(
                    doc_args, execute=True, runner=_explore_results.default_subprocess_runner
                )
                if not doc_command.get("ok"):
                    return _auto_failure(command_error(doc_command))
                if not document_token:
                    document_token = str(
                        ((doc_command.get("json") or {}).get("data", {}).get("document", {}).get("document_id") or "")
                    ).strip()
                    if not document_token:
                        return _auto_failure("created document did not return a document_id")
                overview_boards = _created_overview_boards(doc_command)
            if len(overview_boards) < 2:
                return _auto_failure(
                    "auto-created document did not return both overview whiteboards"
                )
            auto_created_document = {
                "docx_token": document_token,
                "overview_boards": overview_boards,
            }
        else:
            overview_boards = [
                {"block_token": "planned-executive-overview"},
                {"block_token": "planned-canonical-overview"},
            ]

    if auto_create:
        if board_style is None:
            style = explore_board_style(BOARD_STYLE_AUTO_FLOW)
        executive_board = overview_boards[0] if overview_boards else {}
        canonical_board = overview_boards[1] if len(overview_boards) > 1 else {}
        explicit_stage_tokens = bool(tokens)
        planned_sinks: dict[str, dict[str, Any]] = {}
        role_configs = (
            ("canonical", canonical_board, "canonical_full", True),
            ("executive", executive_board, "executive_auto", explicit_stage_tokens and role == "executive"),
        )
        for role_name, board_info, role_mode, stage_enabled in role_configs:
            if role not in {None, role_name}:
                continue
            role_tokens = (
                tokens
                if (role_name == "canonical" and explicit_stage_tokens)
                or (role_name == "executive" and stage_enabled)
                else []
            )
            planned_sinks[role_name] = _build_visual_sink_entry(
                whiteboard_token=token,
                docx_token=document_token,
                statuses=statuses,
                tags=tags,
                projection_mode=role_mode,
                include_ancestors=include_ancestors,
                mermaid_node_limit=mermaid_node_limit,
                stage_capacity=stage_capacity,
                style=style,
                tokens=role_tokens,
                stage_enabled=stage_enabled,
                view_role=role_name,
                overview_token=str(board_info.get("block_token") or "").strip() or None,
            )
        if execute:
            updated = editable_local_explore_config(local)
            sinks = dict(updated.get("visual_sinks") or {})
            sinks.update(planned_sinks)
            updated["visual_sinks"] = sinks
            _explore_results.write_lark_explore_local_config(config_path, updated)
        return {
            "ok": True,
            "schema_version": "loopx_lark_explore_visual_sink_configure_v0",
            "execute": execute,
            "status": "auto_configured" if execute else "would_auto_create",
            "config_path": str(config_path),
            "view_role": role,
            "auto_create": True,
            "document": auto_created_document
            or {
                "docx_token": document_token or "planned-docx",
                "overview_boards": overview_boards,
            },
            "visual_sinks": planned_sinks,
        }

    visual_sink = _build_visual_sink_entry(
        whiteboard_token=token,
        docx_token=document_token,
        statuses=statuses,
        tags=tags,
        projection_mode=projection_mode,
        include_ancestors=include_ancestors,
        mermaid_node_limit=mermaid_node_limit,
        stage_capacity=stage_capacity,
        style=style,
        tokens=tokens,
        view_role=role,
    )
    if execute:
        updated = editable_local_explore_config(local)
        if role:
            visual_sinks = dict(updated.get("visual_sinks") or {})
            visual_sinks[role] = visual_sink
            updated["visual_sinks"] = visual_sinks
        else:
            updated["visual_sink"] = visual_sink
        _explore_results.write_lark_explore_local_config(config_path, updated)
    return {
        "ok": True,
        "schema_version": "loopx_lark_explore_visual_sink_configure_v0",
        "execute": execute,
        "status": "configured" if execute else "would_configure",
        "config_path": str(config_path),
        "view_role": role,
        "visual_sink": visual_sink,
    }


def _redact_visual_display_text(value: Any, *, public_safe: bool) -> str:
    return (
        _redact_public_safe_shapes(value)
        if public_safe
        else _redact_credential_shapes(value)
    )


def _redact_explore_display_records(
    records: Any,
    *,
    fields: tuple[str, ...],
    public_safe: bool,
) -> tuple[list[dict[str, Any]], bool]:
    sanitized: list[dict[str, Any]] = []
    changed = False
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        view = dict(record)
        for display_field in fields:
            value = view.get(display_field)
            if not isinstance(value, str) or not value:
                continue
            redacted = _redact_visual_display_text(value, public_safe=public_safe)
            if redacted != value:
                view[display_field] = redacted
                changed = True
        sanitized.append(view)
    return sanitized, changed


def _redact_explore_display_values(
    nodes: Any,
    edges: Any,
    *,
    public_safe: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Redact display text before any SVG or Mermaid encoding.

    IDs, parent links, status, tags and timestamps stay untouched so the
    topology, lane assignment and freshness keep their structural meaning.
    """

    redacted_nodes, nodes_changed = _redact_explore_display_records(
        nodes, fields=_EXPLORE_NODE_DISPLAY_FIELDS, public_safe=public_safe
    )
    redacted_edges, edges_changed = _redact_explore_display_records(
        edges, fields=_EXPLORE_EDGE_DISPLAY_FIELDS, public_safe=public_safe
    )
    return redacted_nodes, redacted_edges, nodes_changed or edges_changed


def _redact_explore_projection_display(
    projection: Mapping[str, Any],
    *,
    public_safe: bool,
) -> dict[str, Any]:
    nodes, edges, _ = _redact_explore_display_values(
        projection.get("nodes") or [],
        projection.get("edges") or [],
        public_safe=public_safe,
    )
    return {**projection, "nodes": nodes, "edges": edges}


def _redact_rendered_display_spans(
    source: str,
    *,
    input_format: str,
    public_safe: bool,
) -> str:
    """Rewrite only decoded display spans of a caller-supplied rendered view.

    The semantic paths redact values before encoding. This guard keeps the
    visibility boundary for pre-rendered projections while never matching
    enclosing markup, so ``</text>`` and Mermaid delimiters stay intact.
    """

    if input_format == "svg":

        def redact_svg_text(match: re.Match[str]) -> str:
            content = html.unescape(match.group("content"))
            return (
                match.group("open")
                + html.escape(
                    _redact_visual_display_text(content, public_safe=public_safe)
                )
                + match.group("close")
            )

        return _SVG_TEXT_SPAN_PATTERN.sub(redact_svg_text, source)
    if input_format == "mermaid":

        def redact_mermaid_label(match: re.Match[str]) -> str:
            return (
                '"'
                + _redact_visual_display_text(
                    match.group("content"), public_safe=public_safe
                )
                + '"'
            )

        return _MERMAID_LABEL_PATTERN.sub(redact_mermaid_label, source)
    return source


def sync_explore_visual_to_lark(
    config: LarkExploreConfig,
    *,
    projection: Mapping[str, Any],
    visual_sink: Mapping[str, Any] | None,
    config_path: Path,
    semantic_digest: str,
    display_projection: Mapping[str, Any] | None = None,
    view_key: str | None = None,
    sink_visibility: str = SINK_VISIBILITY_OWNER_ONLY,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
    defer_readback: bool = False,
) -> dict[str, Any]:
    """Publish a configured whiteboard without conflating it with Base rows."""

    if sink_visibility not in SINK_VISIBILITIES:
        raise ValueError(f"sink_visibility must be one of {sorted(SINK_VISIBILITIES)}")
    public_safe = sink_visibility == SINK_VISIBILITY_SHARED
    def _visual_result(
        status: str, *, ok: bool = False, published: bool = False, **extra: Any
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "schema_version": LARK_EXPLORE_VISUAL_SYNC_VERSION,
            "status": status,
            "execute": execute,
            "published": published,
            **extra,
        }

    if not isinstance(visual_sink, Mapping):
        return _visual_result("not_configured", ok=True)
    whiteboard_token = str(visual_sink.get("whiteboard_token") or "").strip()
    if not whiteboard_token:
        return _visual_result("invalid_config", error="visual_sink.whiteboard_token is required")
    source_digest = explore_source_digest(projection)
    source_revision = explore_source_revision(projection, digest=source_digest)
    if isinstance(display_projection, Mapping) and (
        display_projection.get("source_digest") or display_projection.get("source_revision")
    ):
        freshness = validate_explore_view_freshness(projection, display_projection)
        if not freshness["fresh"]:
            return _visual_result(
                "stale_projection",
                source_digest=source_digest,
                source_revision=source_revision,
                freshness=freshness,
                error=freshness["reason"],
            )
    if isinstance(display_projection, Mapping):
        graph = dict(display_projection)
    else:
        render_nodes, render_edges, _ = _redact_explore_display_values(
            projection.get("nodes") or [], projection.get("edges") or [], public_safe=public_safe
        )
        graph = build_explore_graph_view(
            render_nodes, render_edges,
            statuses=visual_sink.get("statuses") or [],
            tags=visual_sink.get("tags") or [],
            include_ancestors=bool(visual_sink.get("include_ancestors", True)),
            node_limit=max(1, int(visual_sink.get("mermaid_node_limit") or 100)),
        )
    sink_key = str(view_key or visual_sink.get("view_role") or "visual").strip() or "visual"
    try:
        style = resolve_explore_board_style(visual_sink)
    except ValueError as exc:
        return _visual_result("invalid_config", error=str(exc))
    rendered_source = str(graph.get(style.source_key) or "")
    style_fallback = False
    if not rendered_source.strip() and style.source_key == "svg":
        mermaid_source = str(graph.get("mermaid") or "")
        if mermaid_source.strip():
            style = explore_board_style(BOARD_STYLE_AUTO_FLOW)
            rendered_source = mermaid_source
            style_fallback = True
    if not rendered_source.strip():
        return _visual_result(
            "invalid_projection",
            view_role=str(graph.get("view_role") or sink_key),
            renderer=style.renderer,
            error=f"display projection does not contain {style.source_key} source",
        )
    rendered_source = _redact_rendered_display_spans(
        rendered_source, input_format=style.input_format, public_safe=public_safe
    )
    delivery_material = json.dumps(
        {
            "source_digest": semantic_digest, "source_revision": source_revision,
            "view_role": sink_key, "whiteboard_token": whiteboard_token,
            "board_style": style.name, "renderer": style.renderer, "rendered_source": rendered_source,
        },
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    delivery_digest = hashlib.sha256(delivery_material).hexdigest()
    delivery_marker = f"LoopX delivery {delivery_digest[:20]}"
    published_source = board_source_with_delivery_marker(rendered_source, delivery_marker, style=style)
    source_name = f".loopx-explore-{sink_key}-{delivery_digest[:12]}.{style.extension}"
    command = [
        config.cli_bin, "whiteboard", "+update", "--as", config.identity,
        "--whiteboard-token", whiteboard_token, "--input_format", style.input_format,
        "--source", f"@{source_name}", "--overwrite",
        "--idempotent-token", f"loopx-explore-{sink_key}-{delivery_digest[:20]}",
        "--format", "json",
    ]
    delivery = publish_visual_with_readback(
        cli_bin=config.cli_bin, identity=config.identity, whiteboard_token=whiteboard_token,
        marker=delivery_marker, command=command, published_source=published_source,
        source_path=config_path.parent / source_name, execute=execute, runner=runner,
        defer_readback=defer_readback,
    )
    result = delivery["command"]
    readback = delivery["readback"]
    delivery_ok = bool(delivery["ok"])
    retryable = bool(delivery["retryable"])
    publish_attempts = delivery["publish_attempts"]
    return _visual_result(
        "published" if execute and delivery_ok
        else "would_publish" if not execute
        else "publish_unverified" if result.get("ok") and delivery_marker
        else "publish_failed",
        ok=delivery_ok,
        published=bool(execute and delivery_ok),
        external_write_performed=delivery["external_write_performed"],
        reconciled_existing_delivery=delivery["reconciled_existing_delivery"],
        prepublish_readback=delivery["prepublish_readback"],
        semantic_digest=semantic_digest,
        delivery_digest=delivery_digest,
        source_digest=source_digest,
        source_revision=source_revision,
        view_role=str(graph.get("view_role") or sink_key),
        board_style=style.name,
        style_fallback=style_fallback,
        renderer=style.renderer,
        input_format=style.input_format,
        sink_visibility=sink_visibility,
        public_safe_redaction=public_safe,
        docx_token=str(visual_sink.get("docx_token") or "") or None,
        graph_counts=graph.get("graph_counts"),
        filter=graph.get("filter"),
        command=result,
        publish_attempt_count=len(publish_attempts),
        publish_attempts=publish_attempts,
        readback=readback,
        retryable=retryable,
        required_action="retry Explore visual sync; post-publish marker readback did not settle" if retryable else None,
        error=None if delivery_ok else str(readback.get("error") or "") if result.get("ok") else command_error(result),
    )


def explore_visual_semantic_digest(projection: Mapping[str, Any]) -> str:
    """Return a timestamp-free digest for an explicit visual projection sync."""

    return explore_source_digest(projection)


def sync_explore_visuals_to_lark(
    config: LarkExploreConfig,
    *,
    projection: Mapping[str, Any],
    visual_sinks: Mapping[str, Any],
    config_path: Path,
    sink_visibility: str = SINK_VISIBILITY_OWNER_ONLY,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    """Generate and publish configured same-source Explore views."""

    from . import explore_results as _explore_results

    if sink_visibility not in SINK_VISIBILITIES:
        raise ValueError(f"sink_visibility must be one of {sorted(SINK_VISIBILITIES)}")
    public_safe = sink_visibility == SINK_VISIBILITY_SHARED
    bundle = build_explore_presentation_bundle(projection)
    requested_roles = [role for role in ("canonical", "executive") if isinstance(visual_sinks.get(role), Mapping)]
    active_roles = ["canonical"]
    if bundle["presentation_mode"] == PRESENTATION_MODE_DUAL_VIEW:
        active_roles.append("executive")
    conflict = cross_role_stage_token_conflict_delivery(
        bundle=bundle, visual_sinks=visual_sinks, requested_roles=requested_roles,
        active_roles=active_roles, execute=execute, schema_version=LARK_EXPLORE_VISUALS_SYNC_VERSION,
    )
    if conflict:
        return conflict
    results: dict[str, Any] = {}
    stage_readback_targets: list[tuple[dict[str, Any], str]] = []
    readback_host_wait_exhausted = False

    def _publish_target(
        *,
        role_sink: Mapping[str, Any],
        whiteboard_token: str,
        display_projection: Mapping[str, Any],
        view_key: str,
        semantic_digest: str,
        stage_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal readback_host_wait_exhausted
        target_sink = dict(role_sink, whiteboard_token=whiteboard_token)
        target_result = sync_explore_visual_to_lark(
            config, projection=projection, visual_sink=target_sink, config_path=config_path,
            semantic_digest=semantic_digest, display_projection=display_projection,
            view_key=view_key, sink_visibility=sink_visibility,
            execute=bool(execute and not readback_host_wait_exhausted),
            runner=runner, defer_readback=bool(execute and not readback_host_wait_exhausted),
        )
        if execute and readback_host_wait_exhausted:
            defer_visual_stage_after_host_timeout(target_result)
        if stage_info is not None:
            target_result = dict(target_result, stage=dict(stage_info))
        prepublish_readback = target_result.get("prepublish_readback")
        if isinstance(prepublish_readback, Mapping) and prepublish_readback.get("host_wait_exhausted"):
            readback_host_wait_exhausted = True
        if execute and target_result.get("retryable") and target_result.get("external_write_performed") and whiteboard_token:
            stage_readback_targets.append((target_result, whiteboard_token))
        return target_result

    for role in requested_roles:
        if role not in active_roles:
            results[role] = {
                "ok": True, "status": "not_recommended", "execute": execute, "published": False,
                "source_digest": bundle["source_digest"], "source_revision": bundle["source_revision"], "view_role": role,
            }
            continue
        role_sink = visual_sinks[role]
        stage_capacity = int(role_sink.get("stage_capacity") or 14)
        role_bundle = build_explore_presentation_bundle(projection, policy={"stage_node_capacity": stage_capacity})
        role_view = role_bundle[role]
        render_nodes, render_edges, display_redacted = _redact_explore_display_values(
            role_view.get("nodes") or [], role_view.get("edges") or [], public_safe=public_safe
        )
        if display_redacted:
            render_layout = build_vertical_explore_mermaid(
                render_nodes, render_edges, view_role=role, group_node_limit=stage_capacity
            )
            role_view = dict(
                role_view, nodes=render_nodes, edges=render_edges,
                mermaid=render_layout["mermaid"], stage_views=render_layout["stage_views"],
            )
        stage_views = [item for item in role_view.get("stage_views") or [] if isinstance(item, Mapping)]
        stage_boards_enabled = bool(role_sink.get("stage_boards_enabled", True))
        configured_stages: dict[int, dict[str, Any]] = {}
        section_commands: list[dict[str, Any]] = []
        reconciliation: dict[str, Any] = {}
        if stage_boards_enabled:
            configured_stages, section_commands, _, reconciliation, stage_config_error = ensure_stage_whiteboards(
                config, role=role, role_sink=role_sink, stage_views=stage_views, config_path=config_path,
                sink_visibility=sink_visibility, execute=execute, runner=runner,
                read_local_config=_explore_results.read_lark_explore_local_config,
                write_local_config=_explore_results.write_lark_explore_local_config,
            )
            missing_stage_indexes = [
                int(stage["stage_index"]) for stage in stage_views if int(stage["stage_index"]) not in configured_stages
            ]
            if stage_config_error or missing_stage_indexes:
                results[role] = {
                    "ok": False, "status": "stage_whiteboards_missing", "execute": execute, "published": False,
                    "view_role": role, "source_digest": role_bundle["source_digest"],
                    "source_revision": role_bundle["source_revision"], "required_stage_count": len(stage_views),
                    "configured_stage_count": len(configured_stages), "missing_stage_indexes": missing_stage_indexes,
                    "section_commands": section_commands, "reconciliation": reconciliation,
                    "error": stage_config_error or "configure one document section and whiteboard token for each Evidence Stage",
                }
                continue
        stage_results = []
        role_nodes = {
            str(node.get("node_id") or ""): node for node in role_view.get("nodes") or [] if isinstance(node, Mapping)
        }
        role_edges = [edge for edge in role_view.get("edges") or [] if isinstance(edge, Mapping)]
        if stage_boards_enabled:
            for stage in stage_views:
                stage_index = int(stage["stage_index"])
                stage_node_ids = {str(item) for item in stage.get("node_ids") or []}
                stage_projection = dict(role_view)
                stage_projection["mermaid"] = str(stage.get("mermaid") or "")
                stage_projection["svg"] = str(stage.get("svg") or "")
                stage_projection["nodes"] = [
                    role_nodes[node_id] for node_id in stage.get("node_ids") or [] if node_id in role_nodes
                ]
                stage_projection["edges"] = [
                    edge for edge in role_edges
                    if str(edge.get("from_node") or "") in stage_node_ids and str(edge.get("to_node") or "") in stage_node_ids
                ]
                stage_projection["stage"] = dict(stage)
                stage_token = str(configured_stages[stage_index].get("whiteboard_token") or "").strip()
                stage_results.append(
                    _publish_target(
                        role_sink=role_sink, whiteboard_token=stage_token, display_projection=stage_projection,
                        view_key=f"{role}_stage_{stage_index:02d}", semantic_digest=role_bundle["source_digest"],
                        stage_info=stage,
                    )
                )
        role_ok = all(bool(item.get("ok")) for item in stage_results)
        role_retryable = any(bool(item.get("retryable")) for item in stage_results)
        overview_result = None
        overview_token = str(role_sink.get("overview_whiteboard_token") or "").strip()
        if overview_token:
            overview_projection = dict(role_view)
            overview_projection.pop("stage_views", None)
            overview_result = _publish_target(
                role_sink=role_sink, whiteboard_token=overview_token, display_projection=overview_projection,
                view_key=f"{role}_overview", semantic_digest=role_bundle["source_digest"],
            )
            role_ok = role_ok and bool(overview_result.get("ok"))
            role_retryable = role_retryable or bool(overview_result.get("retryable"))
        role_delivery_material = "|".join(
            str(item.get("delivery_digest") or "")
            for item in stage_results + ([overview_result] if overview_result else [])
        ).encode("utf-8")
        results[role] = {
            "ok": role_ok,
            "status": (
                "published" if execute and role_ok else "would_publish" if role_ok else "publish_failed"
            ),
            "execute": execute,
            "published": bool(execute and role_ok),
            "retryable": role_retryable,
            "required_action": (
                f"retry Explore visual sync for the {role} marker readback"
                if role_retryable
                else None
            ),
            "view_role": role,
            "board_style": (
                str(stage_results[0].get("board_style") or "")
                if stage_results
                else ""
            ),
            "renderer": (
                str(stage_results[0].get("renderer") or "")
                if stage_results
                else ""
            ),
            "presentation_mode": "stage_document",
            "source_digest": role_bundle["source_digest"],
            "source_revision": role_bundle["source_revision"],
            "delivery_digest": hashlib.sha256(role_delivery_material).hexdigest(),
            "stage_count": len(stage_results),
            "stage_capacity": stage_capacity,
            "stages": stage_results,
            "overview": overview_result,
            "section_commands": section_commands,
            "reconciliation": reconciliation,
            "reconciled_stage_count": sum(
                bool(item.get("reconciled_existing_delivery"))
                for item in stage_results
            ),
            "updated_stage_count": sum(
                bool(item.get("external_write_performed")) for item in stage_results
            ),
            "readback_host_wait_exhausted": any(
                bool((item.get("readback") or {}).get("host_wait_exhausted"))
                for item in stage_results
                if isinstance(item.get("readback"), Mapping)
            ),
        }
    if stage_readback_targets:
        settle_visual_stage_readbacks(
            cli_bin=config.cli_bin, identity=config.identity,
            stage_targets=stage_readback_targets, runner=runner,
        )
    finalize_visual_role_results(results, execute=execute)
    delivery = summarize_explore_visual_sync(
        views=results,
        configured_roles=requested_roles,
        recommended_roles=active_roles,
        execute=execute,
    )
    return {
        **delivery,
        "schema_version": LARK_EXPLORE_VISUALS_SYNC_VERSION,
        "execute": execute,
        "presentation_mode": bundle["presentation_mode"],
        "reason_codes": bundle["reason_codes"],
        "source_digest": bundle["source_digest"],
        "source_revision": bundle["source_revision"],
        "recommended_roles": active_roles,
        "views": results,
        "readback_host_wait_exhausted": any(
            bool(view.get("readback_host_wait_exhausted"))
            for view in results.values()
            if isinstance(view, Mapping)
        ),
    }


def _visual_delivery_digests(sync_payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Return the configured role digests that make a visual write idempotent."""

    if not isinstance(sync_payload, Mapping):
        return {}
    views = sync_payload.get("views")
    if not isinstance(views, Mapping):
        return {}
    digests = {}
    for role, view in views.items():
        if not isinstance(view, Mapping):
            continue
        role_digest = str(view.get("delivery_digest") or "").strip()
        if role_digest:
            digests[str(role)] = role_digest
        overview = view.get("overview")
        if isinstance(overview, Mapping):
            overview_digest = str(overview.get("delivery_digest") or "").strip()
            if overview_digest:
                digests[f"{role}_overview"] = overview_digest
        for stage in view.get("stages") or []:
            if not isinstance(stage, Mapping):
                continue
            stage_digest = str(stage.get("delivery_digest") or "").strip()
            stage_index = int(stage.get("stage", {}).get("stage_index") or 0)
            if stage_digest and stage_index:
                digests[f"{role}_stage_{stage_index:02d}"] = stage_digest
    return digests
