#!/usr/bin/env python3
"""Emit a public-safe CLI output receipt from the shared qualification fixture."""

from __future__ import annotations

import argparse
import importlib.util
import hashlib
import shlex
import json
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from loopx.control_plane.testing.canary_harness import import_fixture_authority


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


def _turn_plan_control_observation(value: Any, *, goal_state: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Compare current controls across the explicit plan codec migration.

    This fixture observation grants no authority and does not prove full source
    parity; complete-source and real outcome qualification remain separate.
    Required reads and authoring clauses are compared as original JSON values.
    """
    if not isinstance(value, dict) or value.get("schema_version") not in {
        "loopx_turn_plan_v0", "loopx_turn_plan_v1",
    }:
        return None
    version = value["schema_version"]
    work = value["turn_envelope"] if version == "loopx_turn_plan_v0" else value["work_contract"]
    if version == "loopx_turn_plan_v0":
        actor, channel, capsule, boundary = work["agent_id"], work["action"], work["contract_capsule"], work["boundary"]
        reads, actions = work["required_reads"], work["writeback"]["next_cli_actions"]
        selected = work["action"].get("selected_todo")
    else:
        actor, channel = work["agent_identity"]["agent_id"], work["interaction_contract"]["agent_channel"]
        capsule, boundary = work, work["goal_boundary"]
        reads, actions = channel["required_reads"], work["interaction_contract"]["cli_channel"]["next_cli_actions"]
        selected = work.get("selected_todo")
    # v0 asks for the original Goal file; v1 delivers those exact bytes inline.
    # Only this known obligation may move from a read command to captured data.
    # The frozen caller independently verifies the full-file revision first.
    if goal_state is not None and version == "loopx_turn_plan_v0":
        goal_read = {"command": goal_state["command"], "source": "goal_state", "ordering": "before_work",
            "reason": "Read full Goal intent, acceptance, stops. No summaries; failed/changed sources need a fresh guard."}
        reads = [row for row in reads if row != goal_read]
    route = value["route"]
    controls = {
        "goal_id": work["goal_id"], "agent_id": actor,
        "decision": work["decision"], "should_run": work["should_run"],
        "effective_action": work["effective_action"],
        "selected_todo_id": selected["todo_id"] if isinstance(selected, dict) else None,
        "route": {key: route[key] for key in (
            "kind", "effective_action", "would_invoke_host", "host_invocation_allowed")},
        "execution": {key: channel[key] for key in (
            "must_attempt", "delivery_allowed", "quiet_noop_allowed")},
        "boundary": {key: boundary[key] for key in ("write_scope", "stop_condition")},
        "required_reads": reads, "goal_state_material": goal_state, "next_cli_actions": actions,
        "work_lane_contract": capsule["work_lane_contract"],
        "execution_obligation": capsule["execution_obligation"],
        "replan_action_packet": work.get("replan_action_packet"),
        "effects": value["effects"],
    }
    if route["kind"] == "ready_for_host":
        controls["execution_profile"] = capsule["execution_profile"]
        controls["task_scope"] = capsule["task_scope"]
    assert value["ok"] is True and not any(value["effects"].values())
    encoded = json.dumps(controls, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"plan_version": version, "controls_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "field_sha256": {key: hashlib.sha256(json.dumps(child, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                for key, child in controls.items()}}


def _quota_control_observation(value: Any) -> dict[str, Any] | None:
    """Observe the same current controls in the two declared quota carriers.

    This is a frozen fixture oracle, not full-source parity or an authority
    decoder. Compare original values, including read/settlement prose; the
    complete-source and real recovery suites retain their separate coverage.
    """
    if not isinstance(value, dict) or value.get("mode") != "should-run":
        return None
    version = value.get("schema_version")
    if version not in {None, "loopx_turn_envelope_v0", "loopx_turn_envelope_v1"}:
        return None
    legacy = version == "loopx_turn_envelope_v0"
    channel = value["action"] if legacy else value["interaction_contract"]["agent_channel"]
    cli = value["writeback"] if legacy else value["interaction_contract"]["cli_channel"]
    capsule = value["contract_capsule"] if legacy else value
    boundary = value["boundary"] if legacy else value["goal_boundary"]
    selected = channel.get("selected_todo") if legacy else value.get("selected_todo")
    selection = None
    if isinstance(selected, dict):
        selection = {key: selected.get(key) for key in (
            "todo_id", "priority", "status", "task_class", "action_kind", "claimed_by",
        )}
        # The historical codec's exact, local text reference is not a new
        # selection. Unknown references must not be silently treated as text.
        if legacy and "text_ref" in selected:
            assert selected["text_ref"] == "action.recommended_action"
            selection["text"] = channel["recommended_action"]
        else:
            selection["text"] = selected.get("text")
    user = value["user"] if legacy else value["interaction_contract"]["user_channel"]
    controls = {
        "goal_id": value["goal_id"],
        "agent_id": value["agent_id"] if legacy else value["agent_identity"]["agent_id"],
        "decision": value["decision"], "should_run": value["should_run"],
        "effective_action": value["effective_action"], "selected_todo": selection,
        "execution": {key: channel[key] for key in ("must_attempt", "delivery_allowed", "quiet_noop_allowed")},
        "boundary": {key: boundary[key] for key in ("write_scope", "stop_condition", "rule")},
        "required_reads": value["required_reads"] if legacy else channel["required_reads"],
        "next_cli_actions": cli["next_cli_actions"],
        "spend": {key: cli[key] for key in ("spend_allowed_now", "spend_after_validation", "spend_policy")},
        "work_lane_contract": capsule["work_lane_contract"],
        "execution_obligation": capsule["execution_obligation"],
        "execution_profile": capsule["execution_profile"], "task_scope": capsule["task_scope"],
        "replan_action_packet": value.get("replan_action_packet"),
        "notify": user["notify"], "user_action_required": user["action_required"],
        "scheduler_action": value["scheduler"]["action"] if legacy else value["scheduler_hint"]["action"],
    }
    def digest(child):
        return hashlib.sha256(json.dumps(child, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"source_schema": version, "controls_sha256": digest(controls),
            "field_sha256": {key: digest(child) for key, child in controls.items()}}


def _quota_markdown_field_observations(text: str) -> dict[str, str]:
    """Read the five scalar controls printed by the historical fixture renderer.

    Legacy narrative is not a complete source. Its paired JSON supplies the
    remaining fixture controls only after these actually printed values match.
    These observations never classify a live request or operator's authority.
    """
    values: dict[str, Any] = {}
    for field in ("goal_id", "decision", "should_run", "effective_action"):
        matches = re.findall(rf"^- {field}: `([^`\n]*)`$", text, re.MULTILINE)
        if len(matches) != 1:
            return {}
        values[field] = matches[0]
    if values["should_run"] not in {"True", "False"}:
        return {}
    values["should_run"] = values["should_run"] == "True"
    actors = re.findall(r"^- agent_id: `([^`\n]*)`$", text, re.MULTILINE)
    actors += re.findall(r"^- agent_identity: agent_id=(\S+) agent_model=\S+$", text, re.MULTILINE)
    if len(actors) != 1:
        return {}
    values["agent_id"] = actors[0]
    return {key: hashlib.sha256(json.dumps(child, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        for key, child in values.items()}



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
    payload = measurement.get("resolved_payload", measurement.get("payload"))
    plan, material, detail_cost = payload, None, None
    if isinstance(plan, dict) and plan.get("schema_version") == "loopx_turn_plan_v1":
        if "detail_ref" in plan:
            from tests.control_plane.cli_output_fixture import read_pinned_cli_body
            body, calls, reader_bytes = read_pinned_cli_body(plan["detail_ref"]["command"])
            plan = {**plan, "work_contract": json.loads(body)}
            detail_cost = {"reader_calls": calls, "reader_stdout_utf8_bytes": reader_bytes,
                           "body_utf8_bytes": len(body.encode("utf-8"))}
        sources = plan["work_contract"]["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        for row in sources:
            content = row.get("content", {})
            if row.get("source") == "goal_state" and content.get("full_source") is True:
                revision = "sha256:" + hashlib.sha256(content["text"].encode("utf-8")).hexdigest()
                assert revision == content["revision"] and row["ordering"] == "before_work"
                material = {"command": row["command"], "revision": revision, "ordering": row["ordering"]}
    elif isinstance(plan, dict) and plan.get("schema_version") == "loopx_turn_plan_v0":
        for row in plan["turn_envelope"]["required_reads"]:
            command = shlex.split(row.get("command", ""))
            if row.get("source") == "goal_state" and row.get("ordering") == "before_work" and command[:2] == ["cat", "--"] and len(command) == 3:
                material = {"command": row["command"], "ordering": row["ordering"],
                    "revision": "sha256:" + hashlib.sha256(Path(command[2]).read_bytes()).hexdigest()}
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
        **({"context_delivery": measurement["context_delivery"]}
           if "context_delivery" in measurement else {}),
        **({"context_body": {key: measurement["context_body"][key]
              for key in ("chars", "utf8_bytes", "lines", "compact_payload_chars", "pretty_print_overhead_chars")}}
           if "context_body" in measurement else {}),
        "compact_payload_chars": measurement["compact_payload_chars"],
        "pretty_print_overhead_chars": measurement["pretty_print_overhead_chars"],
        "semantic_json_keys": list(semantic_json_keys),
        "json_shape_paths": (
            semantics.json_shape_paths(payload) if isinstance(payload, dict) else []
        ),
        "markdown_headings": semantics.markdown_headings(text),
        "markdown_anchor": markdown_anchor,
        "turn_plan_controls": _turn_plan_control_observation(plan, goal_state=material),
        **({"quota_controls": _quota_control_observation(payload)}
           if surface_id == "quota_should_run" and isinstance(payload, dict) else {}),
        **({"quota_markdown_fields": _quota_markdown_field_observations(text)}
           if surface_id == "quota_should_run" and output_format == "markdown" and payload is None else {}),
        **({"inspection_source_read": detail_cost} if detail_cost is not None else {}),
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
        **{f"{option}_command_route_count": count
           for option, count in semantics.command_route_counts(text).items()},
        "heartbeat_peer_admission_prompt_revision": (
            semantics.heartbeat_peer_admission_prompt_revision(text)
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
        "next_action_basis_count": semantics.next_action_basis_count(payload),
        "authoring_inputs": semantics.authoring_input_observations(text),
        "continuation_instructions": semantics.continuation_instruction_observations(text),
        "turn_budget_warning": "TurnEnvelope exceeds its performance target by " in text
            and "Turn routing is unchanged." in text and "Review over-target sections:" in text,
    }


def _link_turn_warning_basis(rows: list[dict]) -> list[dict]:
    """The terse Markdown warning describes the paired JSON envelope's cost.

    Reuse that measured semantic basis, never infer instruction presence from
    a warning or grant it an unconditional output allowance.
    """
    by_id = {row["row_id"]: row for row in rows}
    for row in rows:
        if row["format"] == "markdown":
            paired = by_id.get(row["row_id"].removesuffix("markdown") + "json")
            if paired is not None:
                row["turn_envelope_cost_basis"] = paired["continuation_instructions"]
                visible = row.get("quota_markdown_fields")
                controls = paired.get("quota_controls")
                if visible and controls:
                    assert all(controls["field_sha256"].get(key) == digest
                               for key, digest in visible.items()), "Markdown quota controls differ from paired JSON"
                    row["quota_controls"] = controls
    return rows


def _assert_output_contract(
    *,
    output_format: str,
    text: str,
    measurement: dict,
    semantic_json_keys: tuple[str, ...],
    markdown_anchor: str | None,
    output_contract_version: str | None = None,
) -> None:
    """Validate shape while allowing a red base to remain measurable."""
    from loopx.control_plane.testing import cli_output_budget

    check = getattr(cli_output_budget, "assert_cli_output_semantics", None)
    if check is not None:
        check("probe", output_format=output_format, text=text, measurement=measurement,
            semantic_json_keys=semantic_json_keys, markdown_anchor=markdown_anchor,
            output_contract_version=output_contract_version)
        return

    if output_format == "markdown":
        if markdown_anchor and markdown_anchor not in text:
            raise AssertionError(
                f"markdown output lost semantic anchor {markdown_anchor!r}"
            )
        return
    payload = measurement.get("resolved_payload", measurement.get("payload"))
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
    measurements: dict[str, dict[str, dict[str, dict]]] = {}
    for scenario in probe.SCENARIOS:
        # Match _measure_scenario: alias each scenario, not its parent. Otherwise
        # emitted command paths include the scenario suffix only in this runner.
        with probe._stable_budget_fixture_root(fixture_root / scenario.name) as root:
            project, runtime, registry_path, state_file = probe._write_fixture(
                root,
                scenario,
            )
            import_fixture_authority(registry_path, probe.GOAL_ID)
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
                    measurement = probe._measure_context_output(
                        text, output_format=output_format
                    )
                    measurements.setdefault(scenario.name, {}).setdefault(
                        surface_id, {}
                    )[output_format] = measurement
                    surface = probe.CLI_OUTPUT_BUDGET_BY_ID[surface_id]
                    output_version = getattr(surface, "output_contract_version", None)
                    if output_format == "markdown" and output_version != "loopx_turn_envelope_v1":
                        output_version = None
                    if enforce_budget:
                        if (surface_id, scenario.name, output_format) == (
                            "loopx_turn_plan", "crowded", "json"
                        ):
                            probe._assert_turn_plan_writeback_routes(
                                measurement, registry_path, runtime
                            )
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
                            output_contract_version=output_version,
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
                            output_contract_version=output_version,
                        ),
                    )
    if enforce_budget:
        probe._assert_scenario_matrix(measurements)
    return _link_turn_warning_basis(rows)


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
    import_fixture_authority(registry_path, probe.GOAL_ID)
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
            measurement = probe._measure_context_output(text, output_format=output_format)
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
                    output_contract_version=getattr(variant, "output_contract_version", None),
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
                    output_contract_version=getattr(variant, "output_contract_version", None),
                    semantic_json_keys=variant.semantic_json_keys,
                    markdown_anchor=variant.markdown_anchor,
                    measurement=measurement,
                    text=text,
                )
            )
    return _link_turn_warning_basis(rows)


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
    import_fixture_authority(registry_path, probe.GOAL_ID)
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
    measurement = probe._measure_context_output(output, output_format="json")
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
            output_contract_version=getattr(variant, "output_contract_version", None),
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
            output_contract_version=getattr(variant, "output_contract_version", None),
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
    import_fixture_authority(registry_path, probe.GOAL_ID)
    variant_id = "quota_should_run_turn_envelope"
    command = probe._mode_variant_commands(
        project=project, runtime=runtime, registry_path=registry_path,
        state_file=state_file, output_format="json",
    )[variant_id]
    exit_code, output = probe._invoke_cli(command)
    if exit_code != 0:
        raise AssertionError("enabled multi_subagent turn envelope failed")
    measurement = probe._measure_context_output(output, output_format="json")
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
            output_contract_version=getattr(variant, "output_contract_version", None),
        )
    return [_receipt_row(
        semantics=semantics,
        row_id="variant/quota_should_run_turn_envelope_multi_subagent/small/json",
        surface_id=variant.parent_surface_id,
        variant_id="quota_should_run_turn_envelope_multi_subagent", scenario="small",
        output_format="json", qualification_policy="explicit_opt_in_cold_path",
        semantic_json_keys=variant.semantic_json_keys, markdown_anchor=variant.markdown_anchor,
        measurement=measurement, text=output,
        output_contract_version=getattr(variant, "output_contract_version", None),
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
    # Bind the frozen workload's helpers before it imports them. LoopX itself
    # still comes from the product checkout selected for this subprocess.
    fixture_support = _load_module(
        "tests.control_plane.cli_output_fixture",
        Path(__file__).with_name("cli_output_fixture.py"),
    )
    probe = _load_module("loopx_cli_output_probe_fixture", args.test_source)
    # Scenario helpers also call this owner; historical test modules must not
    # re-enter their old /tmp implementation from inside the shared outer root.
    probe._stable_budget_fixture_root = fixture_support.stable_budget_fixture_root
    # One current reader/cost observer for both immutable product revisions.
    # Historical fixture modules still invoke their own CLI and budget owner.
    probe._measure_context_output = lambda text, *, output_format: fixture_support.measure_cli_context_for_oracle(
        text, output_format=output_format, measure=probe.measure_cli_output)
    semantics = _load_module("loopx_cli_output_probe_semantics", args.semantics_source)
    if args.fixture_root.exists():
        shutil.rmtree(args.fixture_root)
    args.fixture_root.mkdir(parents=True)
    # Use one current root owner for both revisions. A historical test source
    # may still assume /tmp exists on every Windows drive.
    with fixture_support.stable_budget_fixture_root(args.fixture_root) as stable_root:
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
