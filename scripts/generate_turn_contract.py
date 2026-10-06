#!/usr/bin/env python3
"""Generate the shared Turn vocabularies, public projection and controller table.

JSON owns the data; only the existing Python controller evaluates decisions.
The TypeScript artifact exports data/types, not another decision implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from pprint import pformat
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "loopx/control_plane/turn_loop_controller_contract_v0.json"
PYTHON_PATH = ROOT / "loopx/control_plane/turn_driver/turn_contract_generated.py"
TYPESCRIPT_PATH = PYTHON_PATH.with_suffix(".ts")
SETS = (
    ("turn_result_kind", "LoopXTurnResultKind", "TURN_RESULT_KINDS"),
    ("turn_route", "LoopXTurnRoute", "TURN_ROUTES"),
    ("loop_disposition", "LoopDisposition", "LOOP_DISPOSITIONS"),
)
CHECK_NAMES = frozenset(
    {
        "envelope",
        "decision_actor",
        "receipt_binding",
        "initial_terminal",
        "initial_todo",
        "completion_terminal",
        "completion_todo",
        "receipt_todo",
        "progress_budget",
        "host_retry",
    }
)
FACT_NAMES = frozenset(
    {
        "receipt_kind",
        "route",
        "receipt_present",
        "terminal_action",
        "completion",
        "host_failure_present",
        "retry_state",
        "budget_state",
    }
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate contract key: {key}")
        result[key] = value
    return result


def read_contract():
    return json.loads(
        CONTRACT_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )


def validate_contract(contract):
    def require(condition, message):
        if not condition:
            raise ValueError("Turn contract: " + message)

    require(
        set(contract)
        == {
            "schema_version",
            "evaluation",
            "qualification",
            "partitions",
            "checks",
            "route_projection",
            "rules",
            "vocabularies",
        },
        "unsupported fields",
    )
    require(
        contract["schema_version"] == "loop_turn_controller_decision_contract_v0",
        "unsupported schema",
    )
    require(
        contract["evaluation"] == "ordered_checks_then_first_matching_return",
        "unsupported evaluation",
    )
    require(
        set(contract["vocabularies"]) == {name for name, _, _ in SETS},
        "three distinct vocabularies required",
    )
    domains = {}
    for name, _, _ in SETS:
        members = contract["vocabularies"][name]
        require(
            isinstance(members, dict) and bool(members), f"{name}: members required"
        )
        require(
            all(re.fullmatch(r"[A-Z][A-Z0-9_]*", k) for k in members),
            f"{name}: invalid member name",
        )
        require(
            all(
                isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_]*", v)
                for v in members.values()
            ),
            f"{name}: invalid value",
        )
        require(
            len(set(members.values())) == len(members),
            f"{name}: aliases are not supported",
        )
        domains[name] = set(members.values())
    projection = contract["route_projection"]
    require(
        set(projection) == domains["turn_route"], "projection must be total over routes"
    )
    require(
        {k for k, v in projection.items() if v is None} == {"contract_error"},
        "only contract_error rejects",
    )
    require(
        all(v is None or v in domains["loop_disposition"] for v in projection.values()),
        "unknown projection output",
    )
    require(set(contract["checks"]) == CHECK_NAMES, "unsupported check set")
    partitions = contract["partitions"]
    require(set(partitions) == FACT_NAMES, "unsupported fact set")
    require(
        partitions["route"] == {"owner": "LoopXTurnRoute"},
        "route partition must follow owner",
    )
    require(
        partitions["receipt_kind"]
        == {"owner": "LoopXTurnResultKind", "additional": ["absent"]},
        "receipt partition must follow owner",
    )
    facts = {
        **partitions,
        "route": domains["turn_route"],
        "receipt_kind": domains["turn_result_kind"] | {"absent"},
    }
    for name in FACT_NAMES - {"route", "receipt_kind"}:
        require(
            isinstance(facts[name], list) and facts[name],
            f"{name}: finite partition required",
        )
    seen = set()
    for row in contract["rules"]:
        require(row["id"] not in seen, "duplicate rule")
        seen.add(row["id"])
        require(
            isinstance(row["when"], dict), "rule conditions must be finite conjunctions"
        )
        for fact, values in row["when"].items():
            require(
                fact in facts and isinstance(values, list) and bool(values),
                "unknown or empty condition",
            )
            require(
                all(
                    any(type(v) is type(x) and v == x for x in facts[fact])
                    for v in values
                ),
                f"{fact}: unknown partition value",
            )
        if "check" in row:
            require(
                set(row) == {"id", "when", "check"} and row["check"] in CHECK_NAMES,
                "unsupported check rule",
            )
        else:
            require(
                {"id", "when", "disposition", "reason", "lineage"}
                <= set(row)
                <= {"id", "when", "disposition", "reason", "lineage", "extra"},
                "unsupported return rule",
            )
            require(
                row["disposition"] in domains["loop_disposition"] | {"project_route"},
                "unregistered disposition",
            )
            require(
                row["lineage"] in {"decision", "effective", "receipt"},
                "unknown lineage",
            )
            require(
                row.get("extra") in {None, "capability", "iteration_stop", "retry"},
                "unknown continuation",
            )
            reason = row["reason"]
            require(
                isinstance(reason, str)
                or (
                    isinstance(reason, dict)
                    and set(reason) <= domains["loop_disposition"] | {"default"}
                    and all(isinstance(v, str) for v in reason.values())
                ),
                "invalid reason projection",
            )
    require(
        {r["check"] for r in contract["rules"] if "check" in r} == CHECK_NAMES,
        "missing admission check",
    )
    return contract


def build_artifacts():
    # Universal-newline decoding gives LF and CRLF checkouts the same content
    # identity. Parse and hash this one snapshot so provenance matches the data.
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    contract = validate_contract(json.loads(source, object_pairs_hook=_unique_object))
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    header = f"Generated by scripts/generate_turn_contract.py; do not edit.\nSource: loopx/control_plane/turn_loop_controller_contract_v0.json\nSHA256: {digest}"
    py = [
        '"""' + header + '"""',
        "from __future__ import annotations",
        "from enum import Enum",
        "",
    ]
    ts = ["// " + line for line in header.splitlines()] + [""]
    for family, symbol, array in SETS:
        members = contract["vocabularies"][family]
        py += [
            f"class {symbol}(str, Enum):",
            *[f"    {name} = {json.dumps(value)}" for name, value in members.items()],
            "",
        ]
        ts += [
            f"export const {array} = [",
            *[f"  {json.dumps(value)}," for value in members.values()],
            "] as const;",
            f"export type {symbol}Value = (typeof {array})[number];",
            "",
        ]
    py += [
        "TURN_CONTROLLER_CONTRACT = " + pformat(contract, sort_dicts=False, width=100),
        "",
        "ROUTE_TO_DISPOSITION = TURN_CONTROLLER_CONTRACT['route_projection']",
        "",
        "def project_turn_route(route: LoopXTurnRoute) -> LoopDisposition:",
        '    """Public total route projection; contract_error explicitly rejects."""',
        "    value = ROUTE_TO_DISPOSITION[LoopXTurnRoute(route).value]",
        "    if value is None:",
        '        raise ValueError("quota decision failed the shared envelope contract (schema or signature hashes)")',
        "    return LoopDisposition(value)",
        "",
    ]
    ts += [
        "export type TurnResultKind = LoopXTurnResultKindValue;",
        "export const TURN_CONTROLLER_CONTRACT = "
        + json.dumps(contract, indent=2)
        + " as const;",
        "export const ROUTE_TO_DISPOSITION = TURN_CONTROLLER_CONTRACT.route_projection;",
        "",
    ]
    return {PYTHON_PATH: "\n".join(py), TYPESCRIPT_PATH: "\n".join(ts)}


def verified_generated_paths():
    """Return provenance-qualified artifacts, never a name-based exemption."""
    artifacts = build_artifacts()
    for path, content in artifacts.items():
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise ValueError(
                f"stale generated Turn contract: {path.relative_to(ROOT)}; run scripts/generate_turn_contract.py"
            )
    return frozenset(path.relative_to(ROOT).as_posix() for path in artifacts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    artifacts = build_artifacts()
    stale = [
        path
        for path, expected in artifacts.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != expected
    ]
    if args.check and stale:
        print(
            "stale Turn artifacts: "
            + ", ".join(str(p.relative_to(ROOT)) for p in stale),
            file=sys.stderr,
        )
        return 1
    if not args.check:
        for path in stale:
            path.write_text(artifacts[path], encoding="utf-8")
    print("Turn contract bindings: " + ("generated" if stale else "up to date"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
