from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import effect_runtime_result
from ..connector_registry.core import load_connector_registry
from ...extensions.public_github_research import execute_public_github, inspect_provider
from .projection import readback, render_readback, validate_receipt


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]], None
]
AddFormat = Callable[[argparse.ArgumentParser], None]
FormatSelector = Callable[..., str]
MAX_INPUT_BYTES = 1_000_000


def _load_object(path_text: str, *, label: str) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError(f"{label} exceeds the {MAX_INPUT_BYTES}-byte limit")
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be a readable JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_receipt(path_text: str) -> dict:
    value = _load_object(path_text, label="external evidence receipt")
    receipt = value.get("receipt", value)
    if not isinstance(receipt, dict):
        raise ValueError("external evidence receipt must be an object")
    return receipt


def _provider_inventory(value: Mapping[str, Any]) -> list[dict[str, object]]:
    providers = value.get("providers")
    if not isinstance(providers, list):
        raise ValueError("provider inventory requires a providers array")
    if not all(isinstance(provider, Mapping) for provider in providers):
        raise ValueError("provider inventory entries must be objects")
    return [dict(provider) for provider in providers]


def _connector_inventory(path_text: str | None) -> list[dict[str, object]]:
    if path_text is None:
        return []
    registry_path = None if path_text == "" else Path(path_text).expanduser()
    state = load_connector_registry(registry_path)
    return [
        {
            "provider_id": f"connector:{connector['id']}",
            "provider_kind": "connector",
            "protocol": "external_evidence_research_v0",
            "declared": True,
            "installed": False,
            "enabled": False,
            "ready": False,
            "unavailable_reason": "connector_registry_is_inventory_not_readiness",
        }
        for connector in state.get("connectors", [])
        if isinstance(connector, Mapping) and connector.get("id")
    ]


def _merge_providers(
    registry_providers: list[dict[str, object]],
    observed_providers: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged = {
        str(provider["provider_id"]): provider
        for provider in registry_providers
        if provider.get("provider_id")
    }
    for provider in observed_providers:
        provider_id = provider.get("provider_id")
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("provider inventory entries require provider_id")
        merged[provider_id] = provider
    return list(merged.values())


def _render(payload: dict[str, object]) -> str:
    if payload.get("schema_version") == "loopx_external_evidence_readback_v0":
        return render_readback(payload)
    lines = ["# LoopX External Evidence", ""]
    for field in (
        "status",
        "plan_id",
        "request_id",
        "provider_id",
        "provider_kind",
        "disposition",
        "retire_ready",
        "blocker",
        "reason",
    ):
        if field in payload:
            lines.append(f"- {field}: `{payload.get(field)}`")
    selected = payload.get("selected_provider")
    if isinstance(selected, Mapping):
        lines.append(f"- selected_provider: `{selected.get('provider_id')}`")
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        for field in (
            "provider_count",
            "method_count",
            "connector_count",
            "ready_count",
            "unavailable_count",
        ):
            if field in summary:
                lines.append(f"- {field}: `{summary.get(field)}`")
    return "\n".join(lines) + "\n"


def register_external_evidence_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "external-evidence",
        help="Discover, plan, receipt, admit, and retire auditable external evidence.",
    )
    actions = parser.add_subparsers(dest="external_evidence_action", required=True)

    discover = actions.add_parser(
        "discover",
        help="Project method and connector inventory without claiming execution readiness.",
    )
    discover.add_argument("--provider-inventory-json")
    discover.add_argument("--connector-registry", nargs="?", const="")
    add_subcommand_format(discover)

    plan = actions.add_parser(
        "plan", help="Select one currently ready evidence provider."
    )
    plan.add_argument("--objective", required=True)
    plan.add_argument("--user-activity", required=True)
    plan.add_argument("--decision", required=True)
    plan.add_argument("--evidence-kind", action="append", required=True)
    plan.add_argument("--constraint", action="append", default=[])
    plan.add_argument("--provider-inventory-json")
    plan.add_argument("--public-github", action="store_true", help="Opt in to a fresh anonymous public GitHub readiness probe.")
    plan.add_argument("--source", action="append", default=[])
    plan.add_argument("--search-term", action="append", default=[])
    plan.add_argument("--connector-registry", nargs="?", const="")
    plan.add_argument("--preferred-provider-id")
    add_subcommand_format(plan)

    execute = actions.add_parser("execute", help="Execute the exact public GitHub plan without admitting evidence.")
    execute.add_argument("--plan-json", required=True)
    execute.add_argument("--execute", action="store_true", help="Authorize bounded anonymous public-source reads.")
    add_subcommand_format(execute)

    project = actions.add_parser("readback", help="Show receipt, parent decision and actual research-ledger coverage.")
    project.add_argument("--plan-json", required=True)
    project.add_argument("--receipt-json", required=True)
    project.add_argument("--admission-json")
    project.add_argument("--project", help="Existing deepresearch project; no implicit run is created.")
    project.add_argument("--execute", action="store_true", help="Write explicitly admitted sources to the existing ledger.")
    add_subcommand_format(project)

    receipt = actions.add_parser(
        "receipt",
        help="Validate and bind a caller-presented provider receipt to its exact plan.",
    )
    receipt.add_argument("--plan-json", required=True)
    receipt.add_argument("--receipt-json", required=True)
    add_subcommand_format(receipt)

    admit = actions.add_parser(
        "admit", help="Validate a provider receipt and parent decision."
    )
    admit.add_argument("--plan-json", required=True)
    admit.add_argument("--receipt-json", required=True)
    admit.add_argument("--decision", choices=["admit", "reject"], required=True)
    admit.add_argument("--reason", required=True)
    admit.add_argument("--admit-source", action="append", default=[])
    add_subcommand_format(admit)

    retire = actions.add_parser(
        "retire", help="Check downstream projection coverage before retirement."
    )
    retire.add_argument("--admission-json", required=True)
    retire.add_argument("--downstream-source", action="append", default=[])
    add_subcommand_format(retire)


def handle_external_evidence_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "external-evidence":
        return None
    try:
        if args.external_evidence_action == "discover":
            providers = _merge_providers(
                _connector_inventory(args.connector_registry),
                []
                if args.provider_inventory_json is None
                else _provider_inventory(
                    _load_object(
                        args.provider_inventory_json,
                        label="external evidence provider inventory",
                    )
                ),
            )
            payload = effect_runtime_result(
                "external_evidence.discover",
                {"providers": providers},
            )
        elif args.external_evidence_action == "plan":
            if not args.provider_inventory_json and not getattr(args, "public_github", False):
                raise ValueError("plan requires provider inventory or explicit --public-github")
            inventory = _load_object(args.provider_inventory_json,
                label="external evidence provider inventory") if args.provider_inventory_json else {"providers": []}
            if getattr(args, "public_github", False):
                if len(getattr(args, "source", [])) > 8:
                    raise ValueError("public GitHub plan allows at most eight sources")
                inventory["providers"] = _merge_providers(_provider_inventory(inventory), [inspect_provider(getattr(args, "source", []))])
            providers = _merge_providers(
                _connector_inventory(args.connector_registry),
                _provider_inventory(inventory),
            )
            payload = effect_runtime_result(
                "external_evidence.plan",
                {
                    "request": {
                        "objective": args.objective,
                        "user_activity": args.user_activity,
                        "decision": args.decision,
                        "evidence_kinds": args.evidence_kind,
                        "constraints": args.constraint,
                        **({"source_refs": getattr(args, "source", [])} if getattr(args, "source", []) else {}),
                        **({"search_terms": getattr(args, "search_term", [])} if getattr(args, "search_term", []) else {}),
                    },
                    "providers": providers,
                    "preferred_provider_id": args.preferred_provider_id,
                },
            )
        elif args.external_evidence_action == "execute":
            if not args.execute:
                raise ValueError("--execute is required for real public-source reads")
            plan = _load_object(args.plan_json, label="external evidence plan")
            # Canonical identity must pass the typed owner before any HTTP call.
            request = plan.get("request")
            selected = plan.get("selected_provider")
            probe_receipt = {"schema_version": "loopx_external_evidence_receipt_v0",
                "plan_id": plan.get("plan_id"),
                "request_id": request.get("request_id") if isinstance(request, Mapping) else None,
                "provider_id": selected.get("provider_id") if isinstance(selected, Mapping) else None,
                "provider_kind": selected.get("provider_kind") if isinstance(selected, Mapping) else None,
                "status": "failed", "sources": [], "summary": "Validation only", "completed_at": "not-executed"}
            validate_receipt(plan, probe_receipt)
            payload = execute_public_github(plan)
            payload["observation"] = validate_receipt(plan, payload["receipt"])
        elif args.external_evidence_action == "readback":
            payload = readback(_load_object(args.plan_json, label="external evidence plan"),
                _load_receipt(args.receipt_json),
                _load_object(args.admission_json, label="external evidence admission") if args.admission_json else None,
                project=Path(args.project).expanduser() if args.project else None, execute=args.execute)
        elif args.external_evidence_action == "receipt":
            payload = effect_runtime_result(
                "external_evidence.receipt",
                {
                    "plan": _load_object(
                        args.plan_json, label="external evidence plan"
                    ),
                    "receipt": _load_receipt(args.receipt_json),
                },
            )
        elif args.external_evidence_action == "admit":
            payload = effect_runtime_result(
                "external_evidence.admit",
                {
                    "plan": _load_object(
                        args.plan_json, label="external evidence plan"
                    ),
                    "receipt": _load_receipt(args.receipt_json),
                    "decision": {
                        "disposition": args.decision,
                        "reason": args.reason,
                        "admitted_source_refs": args.admit_source,
                    },
                },
            )
        elif args.external_evidence_action == "retire":
            payload = effect_runtime_result(
                "external_evidence.retire",
                {
                    "admission": _load_object(
                        args.admission_json,
                        label="external evidence admission",
                    ),
                    "downstream_source_refs": args.downstream_source,
                },
            )
        else:
            raise ValueError(
                "external-evidence requires discover, plan, receipt, admit, or retire"
            )
    except (RuntimeError, ValueError) as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_external_evidence_error_v0",
            "status": "invalid_request",
            "error": str(exc),
        }
        print_payload(payload, output_format(args), _render)
        return 1
    print_payload(payload, output_format(args), _render)
    return 0
