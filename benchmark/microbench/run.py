"""CLI entrypoint for Tier-0 deterministic differential & Tier-1 dual-model microbench."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

from .metrics import assert_public_safe_payload
from .models import (
    DEFAULT_ARK_FLASH_MODEL,
    DEFAULT_CODEX_MODEL,
    CodexGptMicrobenchRunner,
    DeepSeekArkMicrobenchRunner,
    MicrobenchModelRunner,
    require_positive_finite_float,
)
from .scenarios import MICROBENCH_SCENARIO_IDS
from .tier0 import run_tier0_differential
from .tier1 import run_tier1_dual_model_suite


def render_microbench_markdown(payload: dict[str, Any]) -> str:
    """Render concise Behavior Constraint x Efficiency table without leaking private details."""
    lines = ["## Dual-Model Microbenchmark (M1/M2/M3)", ""]
    tier0 = payload.get("tier0")
    if isinstance(tier0, dict):
        raw_base = str(tier0["base_ref"])
        short_base = raw_base[:12] if len(raw_base) == 40 else raw_base
        lines.append(
            f"- **Tier-0 Deterministic Differential:** `passed={tier0['passed']}` "
            f"(`wall={tier0['wall_seconds']}s`, `base={short_base}`)"
        )
        lines.extend(
            [
                "",
                "| Scenario | Scope | Behavior Preserved | Wire Bytes Delta | Prompt Tokens Delta |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for row in tier0.get("scenarios", []):
            cmp = row["comparison"]
            d = cmp["deltas"]
            attr = row.get("source_attribution") if isinstance(row.get("source_attribution"), dict) else {}
            scope_lbl = str(attr.get("comparison_kind") or "unspecified")
            lines.append(
                f"| `{row['scenario_id']}` | `{scope_lbl}` | `{cmp['behavior_preserved']}` | "
                f"`{d.get('wire_bytes')}` | `{d.get('prompt_tokens')}` |"
            )
        lines.append("")

    tier1 = payload.get("tier1")
    if isinstance(tier1, dict):
        lines.append(
            f"- **Tier-1 Dual-Model Suite:** `passed={tier1['passed']}` "
            f"(`wall={tier1['wall_seconds']}s`)"
        )
        lines.extend(
            [
                "",
                "| Scenario | Model | Scope | Base Pass | Head Pass | Wire Delta | Prompt Tok Delta | Comp Tok Delta | Wall Delta(s) |",
                "|---|---|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in tier1.get("results", []):
            cmp = row["comparison"]
            d = cmp["deltas"]
            attr = row.get("source_attribution") if isinstance(row.get("source_attribution"), dict) else {}
            scope_lbl = str(attr.get("comparison_kind") or "unspecified")
            lines.append(
                f"| `{row['scenario_id']}` | `{row['model_id']}` | `{scope_lbl}` | "
                f"`{row['base']['behavior_constraint']['passed']} ({row['base']['status']})` | "
                f"`{row['head']['behavior_constraint']['passed']} ({row['head']['status']})` | "
                f"`{d.get('wire_bytes')}` | `{d.get('prompt_tokens')}` | "
                f"`{d.get('completion_tokens')}` | `{d.get('wall_seconds')}` |"
            )
        lines.append("")
    return "\n".join(lines)


def _positive_finite_float(raw: str) -> float:
    try:
        return require_positive_finite_float(raw, label="timeout")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run LoopX Dual-Model Behavior Constraint x Efficiency Microbenchmark (M1/M2/M3)."
    )
    parser.add_argument(
        "--tier",
        choices=("0", "1", "both"),
        default="0",
        help="0 = <15s deterministic differential, 1 = bounded 1-3m dual-model, both = Tier-0 + Tier-1.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=MICROBENCH_SCENARIO_IDS,
        default=None,
        help="Filter specific scenario(s). Defaults to all M1/M2/M3 scenarios.",
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        help="Optional Git base ref for Base checkout materialization (defaults to matched local contrast).",
    )
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument(
        "--include-cli-output-differential",
        action="store_true",
        help="Also execute examples/control_plane/cli-output-base-head-differential-smoke.py in Tier-0.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required to invoke live external models in Tier-1.",
    )
    parser.add_argument(
        "--models",
        default="codex,ark",
        help="Comma-separated Tier-1 models to include: codex,ark.",
    )
    parser.add_argument("--codex-model", default=DEFAULT_CODEX_MODEL)
    parser.add_argument("--ark-model", default=DEFAULT_ARK_FLASH_MODEL)
    parser.add_argument("--codex-auth-json", type=Path, default=None)
    parser.add_argument("--max-total-seconds", type=_positive_finite_float, default=180.0)
    parser.add_argument("--per-call-timeout-seconds", type=_positive_finite_float, default=45.0)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "schema_version": "loopx_microbench_suite_run_v0",
        "passed": True,
    }
    if args.tier in {"0", "both"}:
        tier0 = run_tier0_differential(
            scenarios=args.scenario,
            base_ref=args.base_ref,
            main_ref=args.main_ref,
            include_cli_output_differential=args.include_cli_output_differential,
        )
        report["tier0"] = tier0
        report["passed"] = report["passed"] and bool(tier0["passed"])

    if args.tier in {"1", "both"}:
        if not args.live:
            report["tier1"] = {
                "schema_version": "loopx_microbench_receipt_v0",
                "tier": "tier1_dual_model",
                "skipped": True,
                "reason": "live_opt_in_required",
                "passed": True,
            }
        else:
            selected_models = {item.strip().lower() for item in args.models.split(",") if item.strip()}
            unknown_models = sorted(selected_models - {"codex", "ark"})
            if unknown_models:
                raise SystemExit(f"Unsupported --models entries: {', '.join(unknown_models)}")
            runners: list[MicrobenchModelRunner] = []
            if "codex" in selected_models:
                runners.append(
                    CodexGptMicrobenchRunner(
                        model=args.codex_model,
                        auth_json_path=args.codex_auth_json,
                    )
                )
            if "ark" in selected_models:
                runners.append(DeepSeekArkMicrobenchRunner(model=args.ark_model))
            tier1 = run_tier1_dual_model_suite(
                runners=runners,
                scenarios=args.scenario,
                base_ref=args.base_ref,
                main_ref=args.main_ref,
                codex_model=args.codex_model,
                ark_model=args.ark_model,
                codex_auth_json_path=args.codex_auth_json,
                max_total_seconds=args.max_total_seconds,
                per_call_timeout_seconds=args.per_call_timeout_seconds,
            )
            report["tier1"] = tier1
            report["passed"] = report["passed"] and bool(tier1["passed"])

    assert_public_safe_payload(report, label="microbench_suite_report")
    rendered = (
        render_microbench_markdown(report)
        if args.format == "markdown"
        else json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    )
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
