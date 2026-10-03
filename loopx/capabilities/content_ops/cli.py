from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..issue_fix.content_ops_cli import (
    handle_content_ops_issue_fix_command,
    register_content_ops_issue_fix_commands,
)
from .computer_use_provider import (
    build_content_ops_browser_action_request_packet,
    render_content_ops_browser_action_request_markdown,
)
from .computer_use_reducer import (
    apply_content_ops_browser_receipt,
    render_content_ops_browser_receipt_markdown,
)
from .item_lifecycle import (
    apply_content_ops_item_event,
    build_content_ops_item_packet,
    build_content_ops_queue_status_packet,
    render_content_ops_item_packet_markdown,
    render_content_ops_queue_status_markdown,
)
from .layout import (
    build_layout_plan_packet,
    build_layout_template_catalog_packet,
    build_layout_template_packet,
    check_layout_packet,
    render_layout_packet_markdown,
)
from .surface import (
    build_content_ops_chatview_report_packet,
    build_content_ops_exploration_plan_packet,
    build_content_ops_packet_aggregation_packet,
    build_content_ops_preview_packet,
    build_content_ops_private_connector_gate_packet,
    build_content_ops_public_handle_observation_packet,
    build_content_ops_walkthrough_artifact_packet,
    render_content_ops_chatview_report_markdown,
    render_content_ops_exploration_plan_markdown,
    render_content_ops_packet_aggregation_markdown,
    render_content_ops_preview_markdown,
    render_content_ops_private_connector_gate_markdown,
    render_content_ops_public_handle_observation_markdown,
    render_content_ops_walkthrough_artifact_markdown,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def _load_json_object(path_text: str) -> dict[str, Any]:
    if path_text == "-":
        payload = json.loads(sys.stdin.read())
    else:
        payload = json.loads(Path(path_text).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path_text} must contain a JSON object")
    return payload


def _parse_path_counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--api-path-count must use PATH=COUNT")
        path, raw_count = value.split("=", 1)
        counts[path.strip()] = int(raw_count.strip())
    return counts


def _parse_layout_pages(page_values: list[str]) -> list[dict[str, object]]:
    pages: list[dict[str, object]] = []
    for value in page_values:
        parts = value.split(":", 2)
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise ValueError("--page must use PAGE_ID:ROLE:SUBJECT_ID")
        page_id, role, subject_id = (part.strip() for part in parts)
        pages.append(
            {"page_id": page_id, "role": role, "subject_id": subject_id}
        )
    return pages


def register_content_ops_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    content_ops_parser = subparsers.add_parser(
        "content-ops",
        help="Render public-safe creator/content operations preview packets.",
    )
    content_ops_sub = content_ops_parser.add_subparsers(
        dest="content_ops_command",
        required=True,
    )
    preview_parser = content_ops_sub.add_parser(
        "preview",
        help="Preview metadata-only connector trials and content-ops projection.",
    )
    add_subcommand_format(preview_parser)
    preview_parser.add_argument(
        "--generated-at",
        default="2026-06-23T00:00:00Z",
        help="Public-safe generated_at timestamp for the synthetic preview fixture.",
    )
    exploration_plan_parser = content_ops_sub.add_parser(
        "exploration-plan",
        help=(
            "Build a fixture-only exploration_plan_v0 packet before connector "
            "source reads."
        ),
    )
    add_subcommand_format(exploration_plan_parser)
    exploration_plan_parser.add_argument(
        "--scenario",
        default="mixed_connector_product_workflow",
        help="Public-safe scenario label for the exploration plan fixture.",
    )
    exploration_plan_parser.add_argument(
        "--generated-at",
        default="2026-06-23T00:00:00Z",
        help="Public-safe generated_at timestamp for the exploration plan.",
    )
    register_content_ops_issue_fix_commands(content_ops_sub, add_subcommand_format)
    observe_parser = content_ops_sub.add_parser(
        "observe-public-handle",
        help="Observe a public platform handle as metadata-only source_item_v0.",
    )
    add_subcommand_format(observe_parser)
    observe_parser.add_argument(
        "--url",
        required=True,
        help="Public https handle URL to observe with a HEAD-only metadata check.",
    )
    observe_parser.add_argument(
        "--source-item-id",
        required=True,
        help="Stable source_item_v0 id to assign to the compact observation.",
    )
    observe_parser.add_argument(
        "--surface",
        default="x_public_feed",
        help="Content-ops surface name for this observation.",
    )
    observe_parser.add_argument(
        "--source-kind",
        default="x_public_profile_handle",
        help="source_item_v0 source_kind to write into the compact record.",
    )
    observe_parser.add_argument(
        "--freshness",
        default="fresh",
        choices=("fresh", "stale", "unknown"),
        help="Freshness value for the generated source_item_v0.",
    )
    observe_parser.add_argument(
        "--terms-note",
        default=None,
        help="Optional public-safe terms/source-boundary note.",
    )
    observe_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="Timeout for the HEAD-only metadata check.",
    )
    observe_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Build the metadata-only packet without any external read.",
    )
    private_gate_parser = content_ops_sub.add_parser(
        "project-private-connector-gate",
        help="Project an owner gate before private connector metadata intake.",
    )
    add_subcommand_format(private_gate_parser)
    private_gate_parser.add_argument(
        "--connector-id",
        default="chatlog_alpha_chatview",
        help="Stable connector id for the private metadata-only gate.",
    )
    private_gate_parser.add_argument(
        "--connector-name",
        default="chatlog-alpha/chatview",
        help="Human-readable connector name for the owner gate.",
    )
    private_gate_parser.add_argument(
        "--surface",
        default="wechat_private_archive",
        help="Content-ops surface name for this private connector.",
    )
    private_gate_parser.add_argument(
        "--proposed-source-item-id",
        default="source_wechat_metadata_signal_001",
        help="source_item_v0 id to reserve after owner approval.",
    )
    private_gate_parser.add_argument(
        "--source-kind",
        default="wechat_private_connector_metadata",
        help="source_item_v0 source_kind for the metadata-only placeholder.",
    )
    private_gate_parser.add_argument(
        "--owner-label",
        default="WeChat archive owner",
        help="Public-safe owner label to show in the gate packet.",
    )
    private_gate_parser.add_argument(
        "--freshness",
        default="unknown",
        choices=("fresh", "stale", "unknown"),
        help="Freshness value for the metadata-only placeholder.",
    )
    aggregate_parser = content_ops_sub.add_parser(
        "aggregate-packets",
        help=(
            "Aggregate public source_item packets and private owner_gate packets "
            "into a compact content_ops_surface_v0 projection."
        ),
    )
    add_subcommand_format(aggregate_parser)
    aggregate_parser.add_argument(
        "--public-packet-json",
        action="append",
        default=[],
        help=(
            "Path to a content_ops_public_handle_observation_packet_v0 JSON object. "
            "Repeat for multiple public source packets. Use '-' to read stdin."
        ),
    )
    aggregate_parser.add_argument(
        "--private-gate-packet-json",
        action="append",
        default=[],
        help=(
            "Path to a content_ops_private_connector_gate_packet_v0 JSON object. "
            "Repeat for multiple private connector gates."
        ),
    )
    aggregate_parser.add_argument(
        "--surface-id",
        default="content_ops_connector_packet_aggregation",
        help="Stable surface_id for the generated content_ops_surface_v0.",
    )
    aggregate_parser.add_argument(
        "--generated-at",
        default="2026-06-23T00:00:00Z",
        help="Public-safe generated_at timestamp for the aggregate surface.",
    )
    chatview_parser = content_ops_sub.add_parser(
        "project-chatview-report",
        help=(
            "Project a public-safe ChatView operator card as an "
            "aggregation-compatible private connector gate packet."
        ),
    )
    add_subcommand_format(chatview_parser)
    chatview_parser.add_argument(
        "--channel-count",
        type=int,
        required=True,
        help="Compact channel collection count from the approved ChatView trial.",
    )
    chatview_parser.add_argument(
        "--recent-record-count",
        type=int,
        required=True,
        help="Compact recent record count from the approved ChatView trial.",
    )
    chatview_parser.add_argument(
        "--report-count",
        type=int,
        required=True,
        help="Compact report collection count from the approved ChatView trial.",
    )
    chatview_parser.add_argument(
        "--api-request-count",
        type=int,
        required=True,
        help="Total API request count observed during the approved ChatView trial.",
    )
    chatview_parser.add_argument(
        "--api-path-count",
        action="append",
        default=[],
        help="Normalized ChatView API path class count as PATH=COUNT. Repeatable.",
    )
    chatview_parser.add_argument(
        "--connector-url",
        default="https://chatview.zaynjarvis.com/",
        help="Approved ChatView connector URL; must be public https.",
    )
    chatview_parser.add_argument(
        "--source-item-id",
        default="source_chatview_metadata_signal_001",
        help="source_item_v0 id to reserve for the compact ChatView signal.",
    )
    chatview_parser.add_argument(
        "--owner-label",
        default="ChatView owner",
        help="Public-safe owner label to show in the gate packet.",
    )
    chatview_parser.add_argument(
        "--generated-at",
        default="2026-06-23T00:00:00Z",
        help="Public-safe generated_at timestamp for the ChatView report.",
    )
    walkthrough_parser = content_ops_sub.add_parser(
        "walkthrough-artifact",
        help=(
            "Build a public-safe X/ChatView connector walkthrough artifact "
            "from compact metadata packets and owner gates."
        ),
    )
    add_subcommand_format(walkthrough_parser)
    walkthrough_parser.add_argument(
        "--public-handle-url",
        default="https://x.com/OpenAI",
        help="Public https handle URL to use as the public metadata signal.",
    )
    walkthrough_parser.add_argument(
        "--public-source-item-id",
        default="source_x_public_handle_walkthrough",
        help="source_item_v0 id for the public handle signal.",
    )
    walkthrough_parser.add_argument(
        "--chatview-source-item-id",
        default="source_chatview_metadata_signal_walkthrough",
        help="source_item_v0 id for the compact ChatView signal.",
    )
    walkthrough_parser.add_argument(
        "--channel-count",
        type=int,
        required=True,
        help="Compact ChatView channel count from an approved private preview.",
    )
    walkthrough_parser.add_argument(
        "--recent-record-count",
        type=int,
        required=True,
        help="Compact recent record count from an approved private preview.",
    )
    walkthrough_parser.add_argument(
        "--report-count",
        type=int,
        required=True,
        help="Compact report count from an approved private preview.",
    )
    walkthrough_parser.add_argument(
        "--api-request-count",
        type=int,
        required=True,
        help="Compact API request count from an approved private preview.",
    )
    walkthrough_parser.add_argument(
        "--api-path-count",
        action="append",
        default=[],
        help="Normalized ChatView API path class count as PATH=COUNT. Repeatable.",
    )
    walkthrough_parser.add_argument(
        "--private-preview-item-count",
        type=int,
        default=0,
        help="Number of private preview records the operator inspected locally.",
    )
    walkthrough_parser.add_argument(
        "--theme-signal",
        action="append",
        default=[],
        help="Public-safe operator-curated theme label. Repeatable.",
    )
    walkthrough_parser.add_argument(
        "--generated-at",
        default="2026-06-23T00:00:00Z",
        help="Public-safe generated_at timestamp for the walkthrough artifact.",
    )
    item_create_parser = content_ops_sub.add_parser(
        "item-create",
        help="Create one provider-neutral content_ops_item_v0 without draft bodies.",
    )
    add_subcommand_format(item_create_parser)
    item_create_parser.add_argument("--item-id", required=True)
    item_create_parser.add_argument(
        "--item-kind",
        required=True,
        choices=("article", "post", "profile_update", "reply", "repost"),
    )
    item_create_parser.add_argument("--channel", required=True)
    item_create_parser.add_argument("--content-digest", required=True)
    item_create_parser.add_argument("--content-ref", required=True)
    item_create_parser.add_argument("--source-ref", action="append", default=[])
    item_create_parser.add_argument("--created-at", required=True)
    item_transition_parser = content_ops_sub.add_parser(
        "item-transition",
        help=(
            "Apply one provider-neutral content item event and emit the updated "
            "item plus a compact transition receipt."
        ),
    )
    add_subcommand_format(item_transition_parser)
    item_transition_parser.add_argument(
        "--item-json",
        required=True,
        help="Path to content_ops_item_v0 JSON, or '-' for stdin.",
    )
    item_transition_parser.add_argument(
        "--event-json",
        required=True,
        help="Path to a content item event JSON object.",
    )
    item_browser_request_parser = content_ops_sub.add_parser(
        "item-browser-request",
        help=(
            "Generate a bounded computer_use_action_request_v0 for an item's "
            "current state, for a host browser/CUA tool to attempt. For an "
            "'approved' item this durably declares delivery intent first "
            "(approved -> delivery_ready) -- WRITES to the item -- before "
            "returning a request; you MUST persist the packet's `item` field "
            "before invoking a provider, and calling this again on an "
            "already-delivery_ready item fails closed rather than retrying."
        ),
    )
    add_subcommand_format(item_browser_request_parser)
    item_browser_request_parser.add_argument(
        "--item-json",
        required=True,
        help="Path to content_ops_item_v0 JSON, or '-' for stdin.",
    )
    item_browser_request_parser.add_argument("--goal-id", required=True)
    item_browser_request_parser.add_argument("--todo-id", required=True)
    item_browser_request_parser.add_argument(
        "--provider-id", default="computer_use_runtime"
    )
    item_browser_request_parser.add_argument(
        "--occurred-at",
        required=True,
        help=(
            "Used only if this call durably declares delivery intent "
            "(approved item); ignored otherwise."
        ),
    )
    item_browser_receipt_parser = content_ops_sub.add_parser(
        "item-browser-receipt",
        help=(
            "Reduce one computer_use_receipt_v0 (answering a prior "
            "item-browser-request) and apply the resulting item-lifecycle "
            "transition, if any."
        ),
    )
    add_subcommand_format(item_browser_receipt_parser)
    item_browser_receipt_parser.add_argument(
        "--item-json",
        required=True,
        help="Path to content_ops_item_v0 JSON, or '-' for stdin.",
    )
    item_browser_receipt_parser.add_argument(
        "--action-request-json",
        required=True,
        help=(
            "Path to the item-browser-request output this receipt answers "
            "(preferred -- keeps a receipt replay safe even if the item has "
            "since moved on), or a bare computer_use_action_request_v0 (only "
            "safe to retry with an unrefreshed --item-json)."
        ),
    )
    item_browser_receipt_parser.add_argument(
        "--receipt-json",
        required=True,
        help="Path to the computer_use_receipt_v0 to reduce.",
    )
    item_browser_receipt_parser.add_argument("--occurred-at", required=True)
    queue_parser = content_ops_sub.add_parser(
        "queue-status",
        help=(
            "Project caller-owned content_ops_item_v0 files into one read-only "
            "managed queue surface."
        ),
    )
    add_subcommand_format(queue_parser)
    queue_parser.add_argument(
        "--item-json",
        action="append",
        required=True,
        help=(
            "Path to content_ops_item_v0 JSON; repeat in priority order. "
            "Use '-' to read one item from stdin."
        ),
    )
    queue_parser.add_argument(
        "--queue-id",
        default="content_ops_managed_queue",
        help="Stable queue id for the projection.",
    )
    queue_parser.add_argument(
        "--generated-at",
        default="2026-08-10T00:00:00Z",
        help="Public-safe generated_at timestamp for the queue projection.",
    )
    template_list_parser = content_ops_sub.add_parser(
        "template-list",
        help="List the built-in public-safe content layout template library.",
    )
    add_subcommand_format(template_list_parser)
    template_show_parser = content_ops_sub.add_parser(
        "template-show",
        help="Show one built-in content layout template and its acceptance rules.",
    )
    add_subcommand_format(template_show_parser)
    template_show_parser.add_argument("--template-id", required=True)
    layout_plan_parser = content_ops_sub.add_parser(
        "layout-plan",
        help="Build a typed page-role plan before rendering content assets.",
    )
    add_subcommand_format(layout_plan_parser)
    layout_plan_parser.add_argument("--item-id", required=True)
    layout_plan_parser.add_argument("--template-id", required=True)
    layout_plan_parser.add_argument(
        "--page",
        action="append",
        required=True,
        help="Planned page as PAGE_ID:ROLE:SUBJECT_ID; repeat in reading order.",
    )
    layout_plan_parser.add_argument(
        "--required-role",
        action="append",
        default=[],
        help="Required typed role; repeat to override the template defaults.",
    )
    layout_plan_parser.add_argument(
        "--closing-role",
        default=None,
        help="Required role for the final page; defaults to the template rule.",
    )
    layout_plan_parser.add_argument("--generated-at", required=True)
    layout_check_parser = content_ops_sub.add_parser(
        "layout-check",
        help="Check rendered-page measurements against a typed layout plan.",
    )
    add_subcommand_format(layout_check_parser)
    layout_check_parser.add_argument(
        "--plan-json",
        required=True,
        help="Path to content_ops_layout_plan_v0 or its packet wrapper.",
    )
    layout_check_parser.add_argument(
        "--measurement-json",
        required=True,
        help="Path to provider-produced content_ops_layout_measurement_v0.",
    )


def handle_content_ops_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int:
    try:
        issue_fix_result = handle_content_ops_issue_fix_command(args)
        if issue_fix_result is not None:
            payload, renderer = issue_fix_result
        elif args.content_ops_command == "preview":
            payload = build_content_ops_preview_packet(generated_at=args.generated_at)
            renderer = render_content_ops_preview_markdown
        elif args.content_ops_command == "exploration-plan":
            payload = build_content_ops_exploration_plan_packet(
                scenario=args.scenario,
                generated_at=args.generated_at,
            )
            renderer = render_content_ops_exploration_plan_markdown
        elif args.content_ops_command == "observe-public-handle":
            payload = build_content_ops_public_handle_observation_packet(
                url=args.url,
                source_item_id=args.source_item_id,
                surface=args.surface,
                source_kind=args.source_kind,
                freshness=args.freshness,
                terms_note=args.terms_note,
                timeout_seconds=args.timeout_seconds,
                fetch=not args.no_fetch,
            )
            renderer = render_content_ops_public_handle_observation_markdown
        elif args.content_ops_command == "project-private-connector-gate":
            payload = build_content_ops_private_connector_gate_packet(
                connector_id=args.connector_id,
                connector_name=args.connector_name,
                surface=args.surface,
                proposed_source_item_id=args.proposed_source_item_id,
                source_kind=args.source_kind,
                owner_label=args.owner_label,
                freshness=args.freshness,
            )
            renderer = render_content_ops_private_connector_gate_markdown
        elif args.content_ops_command == "aggregate-packets":
            payload = build_content_ops_packet_aggregation_packet(
                public_handle_packets=[
                    _load_json_object(path) for path in args.public_packet_json
                ],
                private_connector_gate_packets=[
                    _load_json_object(path) for path in args.private_gate_packet_json
                ],
                surface_id=args.surface_id,
                generated_at=args.generated_at,
            )
            renderer = render_content_ops_packet_aggregation_markdown
        elif args.content_ops_command == "project-chatview-report":
            payload = build_content_ops_chatview_report_packet(
                channel_count=args.channel_count,
                recent_record_count=args.recent_record_count,
                report_count=args.report_count,
                api_request_count=args.api_request_count,
                api_path_counts=_parse_path_counts(args.api_path_count),
                connector_url=args.connector_url,
                source_item_id=args.source_item_id,
                owner_label=args.owner_label,
                generated_at=args.generated_at,
            )
            renderer = render_content_ops_chatview_report_markdown
        elif args.content_ops_command == "walkthrough-artifact":
            payload = build_content_ops_walkthrough_artifact_packet(
                public_handle_url=args.public_handle_url,
                public_source_item_id=args.public_source_item_id,
                chatview_source_item_id=args.chatview_source_item_id,
                channel_count=args.channel_count,
                recent_record_count=args.recent_record_count,
                report_count=args.report_count,
                api_request_count=args.api_request_count,
                api_path_counts=_parse_path_counts(args.api_path_count),
                private_preview_item_count=args.private_preview_item_count,
                theme_signals=args.theme_signal,
                generated_at=args.generated_at,
            )
            renderer = render_content_ops_walkthrough_artifact_markdown
        elif args.content_ops_command == "item-create":
            payload = build_content_ops_item_packet(
                item_id=args.item_id,
                item_kind=args.item_kind,
                channel=args.channel,
                content_digest=args.content_digest,
                content_ref=args.content_ref,
                source_refs=args.source_ref,
                created_at=args.created_at,
            )
            renderer = render_content_ops_item_packet_markdown
        elif args.content_ops_command == "item-transition":
            payload = apply_content_ops_item_event(
                _load_json_object(args.item_json),
                _load_json_object(args.event_json),
            )
            renderer = render_content_ops_item_packet_markdown
        elif args.content_ops_command == "item-browser-request":
            payload = build_content_ops_browser_action_request_packet(
                item=_load_json_object(args.item_json),
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                provider_id=args.provider_id,
                occurred_at=args.occurred_at,
            )
            renderer = render_content_ops_browser_action_request_markdown
        elif args.content_ops_command == "item-browser-receipt":
            action_request_payload = _load_json_object(args.action_request_json)
            if "action_request" in action_request_payload:
                # The full item-browser-request packet -- preferred. Carries
                # expected_transition (safe replay even if the item has moved
                # on) and item_id, which is *always* enforced on this path --
                # a caller using the packet gets item-binding protection
                # automatically, not as something they could forget to opt into.
                action_request = action_request_payload["action_request"]
                expected_transition = action_request_payload.get("expected_transition")
                expected_item_id = action_request_payload.get("item_id")
            else:
                # A bare computer_use_action_request_v0 (e.g. hand-built by a
                # caller that never went through item-browser-request). Retries
                # are only safe here if the caller keeps resubmitting the same,
                # unrefreshed --item-json; see apply_content_ops_browser_receipt.
                # This path does NOT get the expected_item_id check (there is no
                # packet to source it from) -- only the unconditional
                # gate_id-derivation check inside the reducer still protects an
                # external_write/credential_use request taken this way.
                action_request = action_request_payload
                expected_transition = None
                expected_item_id = None
            payload = apply_content_ops_browser_receipt(
                item=_load_json_object(args.item_json),
                action_request=action_request,
                receipt=_load_json_object(args.receipt_json),
                occurred_at=args.occurred_at,
                expected_transition=expected_transition,
                expected_item_id=expected_item_id,
            )
            renderer = render_content_ops_browser_receipt_markdown
        elif args.content_ops_command == "queue-status":
            payload = build_content_ops_queue_status_packet(
                items=[
                    _load_json_object(path) for path in args.item_json
                ],
                queue_id=args.queue_id,
                generated_at=args.generated_at,
            )
            renderer = render_content_ops_queue_status_markdown
        elif args.content_ops_command == "template-list":
            payload = build_layout_template_catalog_packet()
            renderer = render_layout_packet_markdown
        elif args.content_ops_command == "template-show":
            payload = build_layout_template_packet(args.template_id)
            renderer = render_layout_packet_markdown
        elif args.content_ops_command == "layout-plan":
            payload = build_layout_plan_packet(
                item_id=args.item_id,
                template_id=args.template_id,
                pages=_parse_layout_pages(args.page),
                required_roles=args.required_role,
                closing_role=args.closing_role,
                generated_at=args.generated_at,
            )
            renderer = render_layout_packet_markdown
        elif args.content_ops_command == "layout-check":
            payload = check_layout_packet(
                _load_json_object(args.plan_json),
                _load_json_object(args.measurement_json),
            )
            renderer = render_layout_packet_markdown
        else:
            raise ValueError(
                "content-ops requires `preview`, `exploration-plan`, "
                "`issue-fix-intake`, `issue-fix-metadata-preview`, "
                "`observe-public-handle`, `project-private-connector-gate`, "
                "`aggregate-packets`, `project-chatview-report`, or "
                "`walkthrough-artifact`, `item-create`, `item-transition`, "
                "`item-browser-request`, `item-browser-receipt`, "
                "`queue-status`, `template-list`, `template-show`, "
                "`layout-plan`, or `layout-check`"
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "mode": "content-ops",
            "error": str(exc),
        }
        if args.content_ops_command in {
            "template-list",
            "template-show",
            "layout-plan",
            "layout-check",
        }:
            renderer = render_layout_packet_markdown
        else:
            renderer = render_content_ops_private_connector_gate_markdown
    print_payload(payload, output_format(args), renderer)
    return 0 if payload.get("ok") else 1
