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
from .provider_probe import probe_local_evidence_providers


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
    except OSError as exc:
        raise ValueError(
            f"{label} requires a readable JSON file path, not inline JSON"
        ) from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(f"{label} exceeds the {MAX_INPUT_BYTES}-byte limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} file must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} file must contain a JSON object")
    return value


def _load_receipt(path_text: str) -> dict[str, Any]:
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
        "error",
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
    discover.add_argument(
        "--provider-inventory-json", metavar="FILE",
        help="Path to a JSON object with a providers array; not inline JSON.",
    )
    discover.add_argument("--connector-registry", nargs="?", const="")
    discover.add_argument(
        "--no-auto-detect",
        dest="auto_detect",
        action="store_false",
        default=True,
        help="Disable default host evidence method and connector auto-detection.",
    )
    add_subcommand_format(discover)

    plan = actions.add_parser(
        "plan", help="Select one currently ready evidence provider."
    )
    plan.add_argument("--objective", required=True)
    plan.add_argument("--user-activity", required=True)
    plan.add_argument("--decision", required=True)
    plan.add_argument("--evidence-kind", action="append", required=True)
    plan.add_argument("--constraint", action="append", default=[])
    plan.add_argument(
        "--provider-inventory-json", metavar="FILE",
        help="Path to a JSON object with a providers array; not inline JSON.",
    )
    plan.add_argument("--public-github", action="store_true", help="Opt in to a fresh anonymous public GitHub readiness probe.")
    plan.add_argument("--source", action="append", default=[])
    plan.add_argument("--search-term", action="append", default=[])
    plan.add_argument("--connector-registry", nargs="?", const="")
    plan.add_argument("--preferred-provider-id")
    plan.add_argument(
        "--no-auto-detect",
        dest="auto_detect",
        action="store_false",
        default=True,
        help="Disable default host evidence provider auto-detection when --provider-inventory-json is omitted.",
    )
    add_subcommand_format(plan)

    execute = actions.add_parser("execute", help="Execute the exact public GitHub plan without admitting evidence.")
    execute.add_argument("--plan-json", required=True, metavar="FILE", help="Path to a saved plan JSON object.")
    execute.add_argument("--execute", action="store_true", help="Authorize bounded anonymous public-source reads.")
    add_subcommand_format(execute)

    project = actions.add_parser("readback", help="Show receipt, parent decision and actual research-ledger coverage.")
    project.add_argument("--plan-json", required=True, metavar="FILE", help="Path to a saved plan JSON object.")
    project.add_argument("--receipt-json", required=True, metavar="FILE", help="Path to a receipt JSON object.")
    project.add_argument("--admission-json", metavar="FILE", help="Path to a saved admission JSON object.")
    project.add_argument("--project", help="Existing deepresearch project; no implicit run is created.")
    project.add_argument("--goal-id", help="Optional target enabled Explore Goal id for sanitized bridge projection.")
    project.add_argument("--explore-node", help="Target node id in the Explore Goal when projecting with --goal-id.")
    project.add_argument("--agent-id", help="Optional registered agent id for Explore bridge projection.")
    project.add_argument("--expected-goal-id", help="Optional expected source Goal id; fails closed on mismatch.")
    project.add_argument("--execute", action="store_true", help="Write explicitly admitted sources to the existing ledger or sanitized Explore findings.")
    add_subcommand_format(project)

    receipt = actions.add_parser(
        "receipt",
        help="Validate and bind a caller-presented provider receipt to its exact plan.",
    )
    receipt.add_argument("--plan-json", required=True, metavar="FILE", help="Path to a saved plan JSON object.")
    receipt.add_argument("--receipt-json", required=True, metavar="FILE", help="Path to a receipt JSON object.")
    add_subcommand_format(receipt)

    admit = actions.add_parser(
        "admit", help="Validate a provider receipt and parent decision."
    )
    admit.add_argument("--plan-json", required=True, metavar="FILE", help="Path to a saved plan JSON object.")
    admit.add_argument("--receipt-json", required=True, metavar="FILE", help="Path to a receipt JSON object.")
    admit.add_argument("--decision", choices=["admit", "reject"], required=True)
    admit.add_argument("--reason", required=True)
    admit.add_argument("--admit-source", action="append", default=[])
    add_subcommand_format(admit)

    retire = actions.add_parser(
        "retire", help="Check downstream projection coverage before retirement."
    )
    retire.add_argument("--admission-json", required=True, metavar="FILE", help="Path to a saved admission JSON object.")
    retire.add_argument("--downstream-source", action="append", default=[])
    add_subcommand_format(retire)


def handle_external_evidence_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
    registry_path: Path | None = None,
) -> int | None:
    if args.command != "external-evidence":
        return None
    try:
        if args.external_evidence_action == "discover":
            auto_detect = (
                getattr(args, "auto_detect", True)
                and args.provider_inventory_json is None
                and args.connector_registry is None
            )
            connector_arg = "" if auto_detect else args.connector_registry
            observed_providers = (
                probe_local_evidence_providers(for_plan=False)
                if auto_detect
                else (
                    []
                    if args.provider_inventory_json is None
                    else _provider_inventory(
                        _load_object(
                            args.provider_inventory_json,
                            label="external evidence provider inventory",
                        )
                    )
                )
            )
            providers = _merge_providers(
                _connector_inventory(connector_arg),
                observed_providers,
            )
            payload = effect_runtime_result(
                "external_evidence.discover",
                {"providers": providers},
            )
        elif args.external_evidence_action == "plan":
            sources = list(getattr(args, "source", []) or [])
            explicit_public_github = bool(getattr(args, "public_github", False))
            auto_detect = (
                getattr(args, "auto_detect", True)
                and not args.provider_inventory_json
                and not explicit_public_github
            )
            if not args.provider_inventory_json and not explicit_public_github and not auto_detect:
                raise ValueError("plan requires provider inventory or explicit --public-github")
            if explicit_public_github and len(sources) > 8:
                raise ValueError("public GitHub plan allows at most eight sources")
            if auto_detect:
                if len(sources) > 8:
                    raise ValueError("public GitHub plan allows at most eight sources")
                connector_arg = "" if args.connector_registry is None else args.connector_registry
                providers = _merge_providers(
                    _connector_inventory(connector_arg),
                    probe_local_evidence_providers(
                        source_refs=sources,
                        for_plan=True,
                    ),
                )
            else:
                inventory = (
                    _load_object(
                        args.provider_inventory_json,
                        label="external evidence provider inventory",
                    )
                    if args.provider_inventory_json
                    else {"providers": []}
                )
                if explicit_public_github:
                    inventory["providers"] = _merge_providers(
                        _provider_inventory(inventory),
                        [inspect_provider(sources)],
                    )
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
                        **({"source_refs": sources} if sources else {}),
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
            plan_obj = _load_object(args.plan_json, label="external evidence plan")
            receipt_obj = _load_receipt(args.receipt_json)
            admission_obj = (
                _load_object(args.admission_json, label="external evidence admission")
                if args.admission_json
                else None
            )
            goal_target = getattr(args, "goal_id", None)
            explore_node = getattr(args, "explore_node", None)
            if goal_target or explore_node:
                if not (goal_target and explore_node):
                    raise ValueError("Explore readback projection requires both --goal-id and --explore-node")
                if admission_obj is None:
                    raise ValueError("Explore readback projection requires --admission-json")
                from ...control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
                from ...paths import default_registry_path
                from ..explore.research_bridge import project_external_evidence_to_explore

                reg_arg = getattr(args, "registry", None)
                reg_path = (
                    registry_path
                    if registry_path is not None
                    else (Path(reg_arg).expanduser() if reg_arg else default_registry_path())
                )
                route = resolve_goal_source_runtime_route(
                    registry_path=reg_path,
                    goal_id=goal_target,
                    runtime_root_override=getattr(args, "runtime_root", None),
                )
                explore_result = project_external_evidence_to_explore(
                    registry_path=Path(str(route["source_registry"])),
                    runtime_root=Path(str(route["source_runtime_root"])),
                    goal_id=goal_target,
                    node_id=explore_node,
                    plan=plan_obj,
                    receipt=receipt_obj,
                    admission=admission_obj,
                    agent_id=getattr(args, "agent_id", None),
                    expected_goal_id=getattr(args, "expected_goal_id", None),
                    execute=bool(args.execute),
                )
                payload = readback(
                    plan_obj,
                    receipt_obj,
                    admission_obj,
                    project=Path(args.project).expanduser() if args.project else None,
                    execute=False,
                )
                payload["explore_projection"] = explore_result
            else:
                payload = readback(
                    plan_obj,
                    receipt_obj,
                    admission_obj,
                    project=Path(args.project).expanduser() if args.project else None,
                    execute=args.execute,
                )
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
