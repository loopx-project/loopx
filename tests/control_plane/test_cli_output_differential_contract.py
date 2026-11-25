from __future__ import annotations

import ast
import copy
import json
import runpy
from pathlib import Path
from typing import Callable, cast

import pytest

from loopx.control_plane.testing import (
    cli_output_differential,
    cli_output_differential_allowances,
    cli_output_differential_carriers,
    cli_output_differential_migrations,
)
from loopx.control_plane.testing.cli_output_differential import (
    ACTION_PORTFOLIO_SCHEMA_VERSION_V0,
    CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION,
    CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
    CLI_OUTPUT_PROBE_SCHEMA_VERSION,
    compare_cli_output_receipts,
    select_cli_output_base_ref,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_IGNORE_MARKER = "type" + ": ignore"


def _any_ast_refs(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            count += 1
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            count += 1
        elif isinstance(node, ast.alias) and node.name == "Any":
            count += 1
    return count


def _sample_row(*, row_id: str = "surface/status/small/json") -> dict[str, object]:
    return {
        "row_id": row_id,
        "surface_id": "status",
        "format": "json",
        "qualification_policy": "baseline_and_growth",
        "chars": 100,
        "utf8_bytes": 100,
        "lines": 5,
        "compact_payload_chars": 80,
        "semantic_json_keys": ["ok", "status"],
        "json_shape_paths": ["$", "$.ok"],
        "markdown_headings": [],
    }


def _sample_receipt(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": CLI_OUTPUT_PROBE_SCHEMA_VERSION,
        "fixture_contract_version": CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
        "rows": list(rows),
    }


def test_cli_output_differential_modules_zero_any_and_under_700_loc() -> None:
    testing_dir = REPO_ROOT / "loopx" / "control_plane" / "testing"
    for filename in (
        "cli_output_differential.py",
        "cli_output_differential_allowances.py",
        "cli_output_differential_migrations.py",
        "cli_output_differential_carriers.py",
    ):
        path = testing_dir / filename
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) < 700, f"{filename} exceeded 700 LOC ({len(lines)})"
        assert _any_ast_refs(path) == 0, f"{filename} contains Any"
        assert _IGNORE_MARKER not in text, f"{filename} contains {_IGNORE_MARKER}"


def test_cli_output_differential_preserves_owner_symbol_reexports() -> None:
    for symbol in (
        "CLI_OUTPUT_PROBE_SCHEMA_VERSION",
        "CLI_OUTPUT_FIXTURE_CONTRACT_VERSION",
        "CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION",
        "GrowthAllowance",
        "Metric",
        "_TURN_HOST_AND_MANAGED_EXECUTOR_BINDING_V0_GROWTH_ALLOWANCE",
        "_rows_by_id",
        "_removed",
    ):
        assert getattr(cli_output_differential, symbol) is getattr(
            cli_output_differential_allowances, symbol
        )
    for symbol in (
        "_action_portfolio_schema_migration",
        "_complete_turn_plan_migration",
        "_complete_quota_source_migration",
        "_projection_envelope_migration",
    ):
        assert getattr(cli_output_differential, symbol) is getattr(
            cli_output_differential_migrations, symbol
        )
    for symbol in (
        "_task_step_read_fence_migration",
        "_authoring_input_migration",
        "_continuation_instruction_migration",
        "_turn_projection_migration",
    ):
        assert getattr(cli_output_differential, symbol) is getattr(
            cli_output_differential_carriers, symbol
        )


def test_select_cli_output_base_ref_sync_branch_logic() -> None:
    assert (
        select_cli_output_base_ref(
            "origin/main", main_ref="origin/main", is_ancestor=lambda _a, _b: False
        )
        == "origin/main"
    )
    assert (
        select_cli_output_base_ref(
            "stale-base",
            main_ref="origin/main",
            is_ancestor=lambda _ancestor, desc: desc == "HEAD",
        )
        == "origin/main"
    )
    assert (
        select_cli_output_base_ref(
            "fresh-base",
            main_ref="origin/main",
            is_ancestor=lambda _ancestor, _desc: True,
        )
        == "fresh-base"
    )


def test_fail_closed_validation_rejects_malformed_receipts_and_fields() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        compare_cli_output_receipts({"schema_version": "bad"}, _sample_receipt())

    with pytest.raises(ValueError, match="fixture_contract_version"):
        compare_cli_output_receipts(
            {"schema_version": CLI_OUTPUT_PROBE_SCHEMA_VERSION, "fixture_contract_version": "bad"},
            _sample_receipt(),
        )

    dup = _sample_row()
    with pytest.raises(ValueError, match="duplicate CLI output probe row_id"):
        compare_cli_output_receipts(_sample_receipt(dup, dict(dup)), _sample_receipt())

    bad_keys_candidate = {**_sample_row(), "semantic_json_keys": 42}
    with pytest.raises(TypeError, match="semantic_json_keys must be iterable"):
        compare_cli_output_receipts(
            _sample_receipt(_sample_row()), _sample_receipt(bad_keys_candidate)
        )

    bad_paths_candidate = {**_sample_row(), "json_shape_paths": 42}
    with pytest.raises(TypeError, match="json_shape_paths must be iterable"):
        compare_cli_output_receipts(
            _sample_receipt(_sample_row()), _sample_receipt(bad_paths_candidate)
        )


def test_compare_cli_output_receipts_handles_candidate_only_and_retired_rows() -> None:
    base = _sample_row(row_id="surface/evidence_log_thin/small/json")
    replacement: dict[str, object] = {
        **_sample_row(row_id="surface/quota_should_run/small/json"),
        "json_shape_paths": [
            "$.replan_context.core_goal",
            "$.replan_context.evidence",
            "$.replan_context.coverage_ledger",
        ],
    }
    summary = compare_cli_output_receipts(
        _sample_receipt(base), _sample_receipt(replacement)
    )
    assert summary["schema_version"] == CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION
    assert summary["ok"] is True
    assert summary["candidate_only_row_count"] == 1
    assert summary["review_required"] is True
    by_id = {row["row_id"]: row for row in summary["rows"]}
    assert by_id["surface/evidence_log_thin/small/json"]["status"] == "passed"
    assert by_id["surface/quota_should_run/small/json"]["status"] == "candidate_only"


def test_action_portfolio_schema_migration_rejects_malformed_dict_and_enforces_list_allowance() -> None:
    base = {
        **_sample_row(),
        "chars": 40_000,
        "action_portfolio_schema_versions": [],
    }
    candidate_grown = {
        **base,
        "chars": 41_000,
    }

    empty_list_result = compare_cli_output_receipts(
        _sample_receipt(base),
        _sample_receipt({**candidate_grown, "action_portfolio_schema_versions": []}),
    )
    assert empty_list_result["ok"] is False
    assert empty_list_result["rows"][0]["allowances"]["chars"] == 200
    assert "chars grew by 1000; allowance is 200" in empty_list_result["rows"][0]["failures"]

    valid_list_result = compare_cli_output_receipts(
        _sample_receipt(base),
        _sample_receipt(
            {
                **candidate_grown,
                "action_portfolio_schema_versions": [ACTION_PORTFOLIO_SCHEMA_VERSION_V0],
            }
        ),
    )
    assert valid_list_result["ok"] is True
    assert valid_list_result["rows"][0]["allowances"]["chars"] == 1600

    for malformed in (
        {ACTION_PORTFOLIO_SCHEMA_VERSION_V0: True},
        {"unexpected": True},
        {},
        ACTION_PORTFOLIO_SCHEMA_VERSION_V0,
        [123],
    ):
        with pytest.raises(TypeError, match="action_portfolio_schema_versions"):
            compare_cli_output_receipts(
                _sample_receipt(base),
                _sample_receipt(
                    {
                        **candidate_grown,
                        "action_portfolio_schema_versions": malformed,
                    }
                ),
            )

    for bad_field, bad_value in (
        ("planning_horizon_schema_versions", {"quota_planning_horizon_v0": True}),
        ("projection_envelope_schema_versions", {"loopx_projection_envelope_v0": True}),
        ("json_shape_paths", {"$.ok": True}),
        ("semantic_json_keys", {"ok": True}),
    ):
        with pytest.raises(TypeError, match=bad_field):
            compare_cli_output_receipts(
                _sample_receipt(base),
                _sample_receipt({**base, bad_field: bad_value}),
            )


def test_complete_quota_source_migration_requires_explicit_source_schema_key(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    from tests.control_plane import test_cli_output_budget as probe

    runner = runpy.run_path(
        str(REPO_ROOT / "examples/control_plane/cli-output-probe-runner.py")
    )
    project, runtime, registry, state = probe._write_fixture(tmp_path, probe.SCENARIOS[0])
    import_fixture_authority(registry, probe.GOAL_ID)
    command = probe._surface_commands(
        project=project,
        runtime=runtime,
        registry_path=registry,
        state_file=state,
        output_format="json",
    )["quota_should_run"]
    code, text = probe._invoke_cli(command)
    assert code == 0, text
    source = json.loads(text)
    assert isinstance(source, dict)
    raw = {
        key: value
        for key, value in source.items()
        if key not in {"schema_version", "contract_binding"}
    }
    goal_material_fn = cast(
        Callable[[dict[str, object]], dict[str, object]],
        runner["_quota_goal_material"],
    )
    observe_fn = cast(
        Callable[..., dict[str, object]],
        runner["_quota_control_observation"],
    )
    material = goal_material_fn(source)
    before = observe_fn(raw, goal_state=material)
    after = observe_fn(source, goal_state=material)
    before_fields = before.get("field_sha256")
    assert isinstance(before_fields, dict)
    assert len(before_fields) == 19
    assert before.get("controls_sha256") == after.get("controls_sha256")
    assert "source_schema" in before and before["source_schema"] is None
    assert after.get("source_schema") == "loopx_turn_envelope_v1"

    base_row: dict[str, object] = {
        **_sample_row(row_id="surface/quota_should_run/small/json"),
        "surface_id": "quota_should_run",
        "variant_id": None,
        "scenario": "small",
        "qualification_policy": "absolute_hot_path",
        "chars": 1000,
        "utf8_bytes": 1000,
        "compact_payload_chars": 1000,
        "lines": 1,
        "semantic_json_keys": ["interaction_contract"],
        "markdown_anchor": "# LoopX Quota Should Run",
        "projection_envelope_schema_versions": ["loopx_projection_envelope_v0"],
        "quota_controls": copy.deepcopy(before),
    }
    candidate_row: dict[str, object] = {
        **copy.deepcopy(base_row),
        "chars": 2500,
        "utf8_bytes": 2500,
        "compact_payload_chars": 2500,
        "output_contract_version": "loopx_turn_envelope_v1",
        "markdown_anchor": "# LoopX Turn Envelope",
        "quota_controls": after,
    }

    explicit_null = compare_cli_output_receipts(
        _sample_receipt(base_row),
        _sample_receipt(candidate_row),
    )
    assert explicit_null["ok"] is True, explicit_null
    assert explicit_null["rows"][0]["allowances"]["chars"] == 2048

    missing_controls = copy.deepcopy(before)
    del missing_controls["source_schema"]
    missing_schema = compare_cli_output_receipts(
        _sample_receipt({**base_row, "quota_controls": missing_controls}),
        _sample_receipt(candidate_row),
    )
    assert missing_schema["ok"] is False, missing_schema
    missing_row = missing_schema["rows"][0]
    assert missing_row["allowances"]["chars"] == 64
    assert "chars grew by 1500; allowance is 64" in missing_row["failures"]
    assert "output_contract_version changed" in missing_row["failures"]
    assert "markdown_anchor changed" in missing_row["failures"]
