#!/usr/bin/env python3
"""Emit a public-safe CLI output receipt from the shared qualification fixture."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


PROBE_SCHEMA_VERSION = "loopx_cli_output_probe_v0"
FIXTURE_CONTRACT_VERSION = "loopx_cli_output_public_fixture_v0"


def _install_pytest_import_stub() -> None:
    if importlib.util.find_spec("pytest") is not None:
        return
    stub = ModuleType("pytest")

    class Mark:
        @staticmethod
        def parametrize(*_args: Any, **_kwargs: Any) -> Callable[..., Any]:
            return lambda function: function

    stub.mark = Mark()  # type: ignore[attr-defined]
    sys.modules["pytest"] = stub


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load CLI output probe fixture: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _receipt_row(
    *,
    semantics: ModuleType,
    row_id: str,
    surface_id: str,
    scenario: str,
    output_format: str,
    qualification_policy: str,
    semantic_json_keys: tuple[str, ...],
    markdown_anchor: str,
    measurement: dict,
    text: str,
    variant_id: str | None = None,
    output_contract_version: str | None = None,
) -> dict:
    payload = measurement.get("payload")
    return {
        "row_id": row_id,
        "surface_id": surface_id,
        "variant_id": variant_id,
        "scenario": scenario,
        "format": output_format,
        "qualification_policy": qualification_policy,
        "output_contract_version": output_contract_version,
        "chars": measurement["chars"],
        "utf8_bytes": measurement["utf8_bytes"],
        "lines": measurement["lines"],
        "compact_payload_chars": measurement["compact_payload_chars"],
        "pretty_print_overhead_chars": measurement["pretty_print_overhead_chars"],
        "semantic_json_keys": list(semantic_json_keys),
        "json_shape_paths": (
            semantics.json_shape_paths(payload) if isinstance(payload, dict) else []
        ),
        "markdown_headings": semantics.markdown_headings(text),
        "markdown_anchor": markdown_anchor,
        "action_signature_sha256": (
            semantics.action_signature_semantic_sha256(payload)
            if isinstance(payload, dict)
            else None
        ),
        "action_signature_coverages": (
            semantics.action_signature_coverages(payload)
            if isinstance(payload, dict)
            else []
        ),
        "action_portfolio_schema_versions": (
            semantics.action_portfolio_schema_versions(payload)
            if isinstance(payload, dict)
            else []
        ),
        "planning_horizon_schema_versions": (
            semantics.planning_horizon_schema_versions(payload)
            if isinstance(payload, dict)
            else []
        ),
        "planning_inventory_detail_schema_versions": (
            semantics.planning_inventory_detail_schema_versions(payload)
            if isinstance(payload, dict)
            else []
        ),
        "runtime_root_command_route_count": (
            semantics.runtime_root_command_route_count(text)
        ),
        "host_prompt_static_safety_revision": semantics.host_prompt_static_safety_revision(text),
        "heartbeat_user_language_prompt_revision": (
            semantics.heartbeat_user_language_prompt_revision(text)
        ),
        "reward_memory_outcome_prompt_revision": (
            semantics.reward_memory_outcome_prompt_revision(text)
        ),
        "managed_executor_binding_revision": (
            semantics.managed_executor_binding_revision(text)
        ),
        "guided_todo_delta_schema_versions": (
            semantics.guided_todo_delta_schema_versions(payload)
            if isinstance(payload, dict)
            else []
        ),
        "projection_envelope_schema_versions": (
            semantics.projection_envelope_schema_versions(payload if isinstance(payload, dict) else text)
        ),
        "todo_work_counts_schema_versions": (
            semantics.todo_work_counts_schema_versions(payload)
            if isinstance(payload, dict)
            else []
        ),
    }


def _assert_output_contract(
    *,
    output_format: str,
    text: str,
    measurement: dict,
    semantic_json_keys: tuple[str, ...],
    markdown_anchor: str | None,
) -> None:
    """Validate shape while allowing a red base to remain measurable."""

    if output_format == "markdown":
        if markdown_anchor and markdown_anchor not in text:
            raise AssertionError(
                f"markdown output lost semantic anchor {markdown_anchor!r}"
            )
        return
    payload = measurement.get("payload")
    if not isinstance(payload, dict):
        raise AssertionError("JSON output did not emit an object")
    missing = [key for key in semantic_json_keys if key not in payload]
    if missing:
        raise AssertionError(f"JSON output lost semantic key(s): {', '.join(missing)}")


def _default_rows(
    probe: ModuleType,
    semantics: ModuleType,
    fixture_root: Path,
    *,
    enforce_budget: bool = True,
) -> list[dict]:
    rows: list[dict] = []
    for scenario in probe.SCENARIOS:
        project, runtime, registry_path, state_file = probe._write_fixture(
            fixture_root / scenario.name,
            scenario,
        )
        for output_format in ("json", "markdown"):
            commands = probe._surface_commands(
                project=project,
                runtime=runtime,
                registry_path=registry_path,
                state_file=state_file,
                output_format=output_format,
            )
            for surface_id, command in commands.items():
                exit_code, text = probe._invoke_cli(command)
                if exit_code != 0:
                    raise AssertionError(f"{surface_id}/{output_format} failed")
                measurement = probe.measure_cli_output(
                    text, output_format=output_format
                )
                surface = probe.CLI_OUTPUT_BUDGET_BY_ID[surface_id]
                if enforce_budget:
                    probe.assert_cli_output_baseline(
                        surface,
                        scenario=scenario.name,
                        output_format=output_format,
                        text=text,
                        measurement=measurement,
                    )
                else:
                    _assert_output_contract(
                        output_format=output_format,
                        text=text,
                        measurement=measurement,
                        semantic_json_keys=surface.semantic_json_keys,
                        markdown_anchor=surface.markdown_anchor,
                    )
                rows.append(
                    _receipt_row(
                        semantics=semantics,
                        row_id=f"surface/{surface_id}/{scenario.name}/{output_format}",
                        surface_id=surface_id,
                        scenario=scenario.name,
                        output_format=output_format,
                        qualification_policy=surface.qualification_policy,
                        semantic_json_keys=surface.semantic_json_keys,
                        markdown_anchor=surface.markdown_anchor,
                        measurement=measurement,
                        text=text,
                        output_contract_version=(
                            getattr(surface, "output_contract_version", None)
                            if output_format == "json"
                            else None
                        ),
                    )
                )
    return rows


def _variant_rows(
    probe: ModuleType,
    semantics: ModuleType,
    fixture_root: Path,
    *,
    enforce_budget: bool = True,
) -> list[dict]:
    project, runtime, registry_path, state_file = probe._write_fixture(
        fixture_root / "mode_variants",
        probe.SCENARIOS[0],
    )
    rows: list[dict] = []
    for output_format in ("json", "markdown"):
        commands = probe._mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format=output_format,
        )
        for variant_id, command in commands.items():
            variant = probe.CLI_OUTPUT_MODE_VARIANT_BY_ID.get(variant_id)
            if variant is None:
                # The shared candidate fixture may name a mode introduced after
                # the detached base. Candidate-only rows are qualified later.
                continue
            if output_format not in variant.output_formats:
                continue
            exit_code, text = probe._invoke_cli(command)
            if exit_code != 0:
                raise AssertionError(f"{variant_id}/{output_format} failed")
            measurement = probe.measure_cli_output(text, output_format=output_format)
            if enforce_budget:
                probe.assert_cli_output_mode_variant(
                    variant,
                    output_format=output_format,
                    text=text,
                    measurement=measurement,
                )
            else:
                _assert_output_contract(
                    output_format=output_format,
                    text=text,
                    measurement=measurement,
                    semantic_json_keys=variant.semantic_json_keys,
                    markdown_anchor=variant.markdown_anchor,
                )
            rows.append(
                _receipt_row(
                    semantics=semantics,
                    row_id=f"variant/{variant_id}/small/{output_format}",
                    surface_id=variant.parent_surface_id,
                    variant_id=variant_id,
                    scenario="small",
                    output_format=output_format,
                    qualification_policy="explicit_opt_in_cold_path",
                    semantic_json_keys=variant.semantic_json_keys,
                    markdown_anchor=variant.markdown_anchor,
                    measurement=measurement,
                    text=text,
                )
            )
    return rows


def _blocking_gate_rows(
    probe: ModuleType,
    semantics: ModuleType,
    fixture_root: Path,
    *,
    enforce_budget: bool = True,
) -> list[dict]:
    project, runtime, registry_path, state_file = probe._write_fixture(
        fixture_root / "blocking_user_gate",
        probe.SCENARIOS[0],
    )
    with state_file.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n## User Todo\n\n"
            "- [ ] [P0 user gate] Approve the blocked public fixture action.\n"
            "  <!-- loopx:todo todo_id=todo_fixture_gate status=open "
            "task_class=user_gate action_kind=fixture_0 priority=P0 "
            f"blocks_agent={probe.AGENT_IDS[0]} "
            "unblocks_todo_id=todo_fixture_000 -->\n"
        )
    command = probe._mode_variant_commands(
        project=project,
        runtime=runtime,
        registry_path=registry_path,
        state_file=state_file,
        output_format="json",
    )["quota_should_run_turn_envelope"]
    exit_code, output = probe._invoke_cli(command)
    if exit_code != 0:
        raise AssertionError("blocking user-gate turn envelope failed")
    measurement = probe.measure_cli_output(output, output_format="json")
    variant = probe.CLI_OUTPUT_MODE_VARIANT_BY_ID[
        "quota_should_run_turn_envelope"
    ]
    if enforce_budget:
        probe.assert_cli_output_mode_variant(
            variant,
            output_format="json",
            text=output,
            measurement=measurement,
        )
    else:
        _assert_output_contract(
            output_format="json",
            text=output,
            measurement=measurement,
            semantic_json_keys=variant.semantic_json_keys,
            markdown_anchor=variant.markdown_anchor,
        )
    return [
        _receipt_row(
            semantics=semantics,
            row_id=(
                "variant/quota_should_run_turn_envelope_blocking_user_gate/"
                "small/json"
            ),
            surface_id=variant.parent_surface_id,
            variant_id="quota_should_run_turn_envelope_blocking_user_gate",
            scenario="small",
            output_format="json",
            qualification_policy="explicit_opt_in_cold_path",
            semantic_json_keys=variant.semantic_json_keys,
            markdown_anchor=variant.markdown_anchor,
            measurement=measurement,
            text=output,
        )
    ]



def _multi_subagent_rows(probe, semantics, fixture_root, *, enforce_budget=True):
    """Run the same enabled public fixture on base and head, not default-off only."""
    project, runtime, registry_path, state_file = probe._write_fixture(
        fixture_root / "multi_subagent_enabled", probe.SCENARIOS[0]
    )
    registry = json.loads(registry_path.read_text())
    registry["goals"][0]["spawn_policy"] = {
        "mode": "multi_subagent", "spawn_allowed": True, "max_children": 4,
        "model_config": {"model": "example-small", "reasoning_effort": "max"},
    }
    registry_path.write_text(json.dumps(registry))
    variant_id = "quota_should_run_turn_envelope"
    command = probe._mode_variant_commands(
        project=project, runtime=runtime, registry_path=registry_path,
        state_file=state_file, output_format="json",
    )[variant_id]
    exit_code, output = probe._invoke_cli(command)
    if exit_code != 0:
        raise AssertionError("enabled multi_subagent turn envelope failed")
    measurement = probe.measure_cli_output(output, output_format="json")
    variant = probe.CLI_OUTPUT_MODE_VARIANT_BY_ID[variant_id]
    if enforce_budget:
        probe.assert_cli_output_mode_variant(
            variant, output_format="json", text=output, measurement=measurement,
        )
    else:
        _assert_output_contract(
            output_format="json", text=output, measurement=measurement,
            semantic_json_keys=variant.semantic_json_keys,
            markdown_anchor=variant.markdown_anchor,
        )
    return [_receipt_row(
        semantics=semantics,
        row_id="variant/quota_should_run_turn_envelope_multi_subagent/small/json",
        surface_id=variant.parent_surface_id,
        variant_id="quota_should_run_turn_envelope_multi_subagent", scenario="small",
        output_format="json", qualification_policy="explicit_opt_in_cold_path",
        semantic_json_keys=variant.semantic_json_keys, markdown_anchor=variant.markdown_anchor,
        measurement=measurement, text=output,
    )]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-source", type=Path, required=True)
    parser.add_argument("--semantics-source", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--measurement-only",
        action="store_true",
        help="measure a historical base without requiring its retired ceilings to pass",
    )
    args = parser.parse_args()
    _install_pytest_import_stub()
    probe = _load_module("loopx_cli_output_probe_fixture", args.test_source)
    semantics = _load_module("loopx_cli_output_probe_semantics", args.semantics_source)
    if args.fixture_root.exists():
        shutil.rmtree(args.fixture_root)
    args.fixture_root.mkdir(parents=True)
    with probe._stable_budget_fixture_root(args.fixture_root) as stable_root:
        rows = [
            *_default_rows(
                probe, semantics, stable_root,
                enforce_budget=not args.measurement_only,
            ),
            *_variant_rows(
                probe, semantics, stable_root,
                enforce_budget=not args.measurement_only,
            ),
            *_blocking_gate_rows(
                probe, semantics, stable_root,
                enforce_budget=not args.measurement_only,
            ),
            *_multi_subagent_rows(
                probe, semantics, stable_root,
                enforce_budget=not args.measurement_only,
            ),
        ]
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(
            {
                "schema_version": PROBE_SCHEMA_VERSION,
                "fixture_contract_version": FIXTURE_CONTRACT_VERSION,
                "rows": sorted(rows, key=lambda row: row["row_id"]),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"cli-output-probe-runner ok rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
