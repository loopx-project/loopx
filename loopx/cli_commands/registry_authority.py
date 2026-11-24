from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..authority import (
    AUTHORITY_SOURCE_BOUNDARIES,
    import_doc_registry_authority,
    register_authority_source,
    render_doc_registry_authority_import_markdown,
    render_authority_source_markdown,
)
from ..global_registry import sync_project_registry_to_global


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]

REGISTRY_AUTHORITY_COMMANDS = {
    "authority-sources",
    "register-authority-source",
    "import-doc-registry-authority",
}


def register_registry_authority_commands(subparsers: argparse._SubParsersAction) -> None:
    sources = subparsers.add_parser(
        "authority-sources", help="Find registered Goal material metadata without opening sources.",
    )
    sources.add_argument("--goal-id", required=True)
    sources.add_argument("--query", default="", help="Match all whitespace-separated terms across metadata and topics.")
    sources.add_argument("--topic", help="Exact registered topic key.")
    sources.add_argument("--offset", type=int, default=0, help="Skip this many matches in stable source-id order.")
    sources.add_argument("--limit", type=int, default=20, help="Maximum matches to return (1–100).")
    authority_parser = subparsers.add_parser(
        "register-authority-source",
        help="Register a redacted local authority/material source for a goal.",
    )
    authority_parser.add_argument("--goal-id", required=True, help="Goal id whose local registry should be updated.")
    authority_parser.add_argument("--source-id", required=True, help="Stable local source id.")
    authority_parser.add_argument(
        "--source-ref",
        help="Raw local source reference to hash and redact. The raw value is never stored.",
    )
    authority_parser.add_argument("--source-kind", required=True, help="Public-safe source kind, such as doc or repository.")
    authority_parser.add_argument("--role", required=True, help="Public-safe material role.")
    authority_parser.add_argument("--freshness", required=True, help="Public-safe freshness state.")
    authority_parser.add_argument("--owner-status", help="Optional public-safe owner/review status.")
    authority_parser.add_argument("--gate-status", help="Optional public-safe gate status.")
    authority_parser.add_argument(
        "--boundary",
        choices=sorted(AUTHORITY_SOURCE_BOUNDARIES),
        default="private_redacted",
        help="Public/private boundary for this source. Defaults to private_redacted.",
    )
    authority_parser.add_argument("--revision", help="Optional public-safe revision label.")
    authority_parser.add_argument("--conflict-rule", help="Optional public-safe conflict rule.")
    authority_parser.add_argument("--topic", help="Optional topic_authority key to map to this source id.")
    authority_parser.add_argument("--dry-run", action="store_true", help="Preview the registry update without writing.")
    authority_parser.add_argument(
        "--no-global-sync",
        action="store_true",
        help="Do not refresh the shared global registry after writing the local source registry.",
    )

    doc_registry_authority_parser = subparsers.add_parser(
        "import-doc-registry-authority",
        help="Import a redacted DOC_REGISTRY summary as a local authority/material source.",
    )
    doc_registry_authority_parser.add_argument(
        "--goal-id", required=True, help="Goal id whose local registry should be updated."
    )
    doc_registry_authority_parser.add_argument("--source-id", required=True, help="Stable local source id.")
    doc_registry_authority_parser.add_argument(
        "--doc-registry-path",
        required=True,
        help="Local DOC_REGISTRY.yaml path to read. The raw path is hashed and not stored.",
    )
    doc_registry_authority_parser.add_argument(
        "--source-kind",
        default="doc_registry",
        help="Public-safe source kind. Defaults to doc_registry.",
    )
    doc_registry_authority_parser.add_argument(
        "--role",
        default="external_doc_authority_registry",
        help="Public-safe material role. Defaults to external_doc_authority_registry.",
    )
    doc_registry_authority_parser.add_argument(
        "--freshness",
        default="current",
        help="Public-safe freshness state. Defaults to current.",
    )
    doc_registry_authority_parser.add_argument("--owner-status", help="Optional public-safe owner/review status.")
    doc_registry_authority_parser.add_argument("--gate-status", help="Optional public-safe gate status.")
    doc_registry_authority_parser.add_argument(
        "--boundary",
        choices=sorted(AUTHORITY_SOURCE_BOUNDARIES),
        default="private_redacted",
        help="Public/private boundary for this source. Defaults to private_redacted.",
    )
    doc_registry_authority_parser.add_argument("--revision", help="Optional public-safe revision label.")
    doc_registry_authority_parser.add_argument("--conflict-rule", help="Optional public-safe conflict rule.")
    doc_registry_authority_parser.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Additional local topic_authority key to map to this source id. Repeatable.",
    )
    doc_registry_authority_parser.add_argument(
        "--import-topic-prefix",
        help="Prefix imported DOC_REGISTRY topic keys with this value before mapping them to the source id.",
    )
    doc_registry_authority_parser.add_argument(
        "--max-imported-topics",
        type=int,
        default=50,
        help="Maximum DOC_REGISTRY topics to map when --import-topic-prefix is set. Defaults to 50.",
    )
    doc_registry_authority_parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    doc_registry_authority_parser.add_argument(
        "--no-global-sync",
        action="store_true",
        help="Do not refresh the shared global registry after writing the local source registry.",
    )


def handle_registry_authority_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
) -> int | None:
    if args.command == "authority-sources":
        from .registry_authority_read import list_authority_sources, render_authority_sources_markdown

        try:
            payload = list_authority_sources(
                registry_path, args.goal_id, query=args.query, topic=args.topic,
                offset=args.offset, limit=args.limit,
            )
        except (OSError, ValueError, TypeError) as exc:
            payload = {"ok": False, "goal_id": args.goal_id, "read_only": True, "error": str(exc)}
        print_payload(payload, args.format, render_authority_sources_markdown)
        return 0 if payload["ok"] else 1

    if args.command == "register-authority-source":
        try:
            payload = register_authority_source(
                registry_path=registry_path,
                goal_id=args.goal_id,
                source_id=args.source_id,
                source_ref=args.source_ref,
                source_kind=args.source_kind,
                role=args.role,
                freshness=args.freshness,
                owner_status=args.owner_status,
                gate_status=args.gate_status,
                boundary=args.boundary,
                revision=args.revision,
                conflict_rule=args.conflict_rule,
                topic=args.topic,
                dry_run=bool(args.dry_run),
            )
            if not bool(args.no_global_sync):
                if args.dry_run:
                    payload["global_sync"] = {"enabled": True, "dry_run": True, "wrote": False}
                else:
                    payload["global_sync"] = sync_project_registry_to_global(
                        registry_path=registry_path,
                        runtime_root_override=args.runtime_root,
                        goal_id=args.goal_id,
                        dry_run=False,
                    )
            else:
                payload["global_sync"] = {"enabled": False}
        except Exception as exc:
            payload = {
                "ok": False,
                "registry": str(registry_path),
                "runtime_root": args.runtime_root,
                "goal_id": args.goal_id,
                "source_id": getattr(args, "source_id", None),
                "written": False,
                "dry_run": bool(getattr(args, "dry_run", False)),
                "error": str(exc),
            }
        print_payload(payload, args.format, render_authority_source_markdown)
        return 0 if payload.get("ok") else 1

    if args.command != "import-doc-registry-authority":
        return None

    try:
        payload = import_doc_registry_authority(
            registry_path=registry_path,
            goal_id=args.goal_id,
            source_id=args.source_id,
            doc_registry_path=Path(args.doc_registry_path),
            source_kind=args.source_kind,
            role=args.role,
            freshness=args.freshness,
            owner_status=args.owner_status,
            gate_status=args.gate_status,
            boundary=args.boundary,
            revision=args.revision,
            conflict_rule=args.conflict_rule,
            topics=list(args.topic or []),
            import_topic_prefix=args.import_topic_prefix,
            max_imported_topics=int(args.max_imported_topics),
            dry_run=bool(args.dry_run),
        )
        if not bool(args.no_global_sync):
            if args.dry_run:
                payload["global_sync"] = {"enabled": True, "dry_run": True, "wrote": False}
            else:
                payload["global_sync"] = sync_project_registry_to_global(
                    registry_path=registry_path,
                    runtime_root_override=args.runtime_root,
                    goal_id=args.goal_id,
                    dry_run=False,
                )
        else:
            payload["global_sync"] = {"enabled": False}
    except Exception as exc:
        payload = {
            "ok": False,
            "registry": str(registry_path),
            "runtime_root": args.runtime_root,
            "goal_id": args.goal_id,
            "source_id": getattr(args, "source_id", None),
            "written": False,
            "dry_run": bool(getattr(args, "dry_run", False)),
            "error": str(exc),
        }
    print_payload(payload, args.format, render_doc_registry_authority_import_markdown)
    return 0 if payload.get("ok") else 1
