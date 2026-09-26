from __future__ import annotations

import copy
import json

import pytest

from loopx.control_plane.testing.cli_output_budget import measure_cli_output
from loopx.control_plane.testing.cli_output_differential import (
    CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
    CLI_OUTPUT_PROBE_SCHEMA_VERSION,
    compare_cli_output_receipts,
    select_cli_output_base_ref,
)
from loopx.control_plane.testing.cli_output_semantics import (
    action_portfolio_schema_versions,
    action_signature_coverages,
    guided_todo_delta_schema_versions,
    planning_horizon_schema_versions,
    planning_inventory_detail_schema_versions,
    runtime_root_command_route_count,
    todo_work_counts_schema_versions,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "surface/status/small/json",
        "surface_id": "status",
        "variant_id": None,
        "scenario": "small",
        "format": "json",
        "qualification_policy": "absolute_hot_path",
        "chars": 40_000,
        "utf8_bytes": 40_000,
        "lines": 1_000,
        "compact_payload_chars": 20_000,
        "semantic_json_keys": ["status_contract", "attention_queue"],
        "json_shape_paths": ["$", "$.status_contract", "$.attention_queue"],
        "markdown_headings": [],
        "markdown_anchor": "# LoopX Status",
        "action_signature_sha256": "semantic-signature",
        "action_signature_coverages": ["turn_envelope_action_dimensions_v0"],
        "action_portfolio_schema_versions": [],
        "planning_horizon_schema_versions": [],
        "guided_todo_delta_schema_versions": [],
        "planning_inventory_detail_schema_versions": [],
        "todo_work_counts_schema_versions": [],
        "runtime_root_command_route_count": 0,
    }
    row.update(overrides)
    return row


def _receipt(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": CLI_OUTPUT_PROBE_SCHEMA_VERSION,
        "fixture_contract_version": CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
        "rows": list(rows),
    }


def test_thin_bilingual_byte_allowance_does_not_relax_character_or_quota_limits():
    from loopx.control_plane.testing.cli_output_differential import _compare_row

    base = _row(
        row_id="surface/heartbeat_prompt_thin/small/markdown",
        format="markdown",
        chars=100,
        utf8_bytes=100,
        lines=1,
        compact_payload_chars=100,
    )
    candidate = {**base, "chars": 120, "utf8_bytes": 260}
    assert not _compare_row(base, candidate)["failures"]
    assert _compare_row(base, {**candidate, "chars": 133})["failures"]
    assert _compare_row(base, {**candidate, "utf8_bytes": 293})["failures"]
    base["row_id"] = "surface/quota_should_run/small/markdown"
    candidate["row_id"] = base["row_id"]
    assert _compare_row(base, candidate)["failures"]


def test_sync_commit_uses_main_as_cli_output_base() -> None:
    ancestors = {
        ("origin/main", "HEAD"),
    }

    selected = select_cli_output_base_ref(
        "origin/integration",
        main_ref="origin/main",
        is_ancestor=lambda ancestor, descendant: (ancestor, descendant) in ancestors,
    )

    assert selected == "origin/main"


@pytest.mark.parametrize("row_kind", ["surface", "variant"])
@pytest.mark.parametrize("mode", ["thin", "brief", "compact"])
def test_host_safety_restoration_budget_is_one_time_bounded_and_prompt_only(
    row_kind, mode
):
    from loopx.control_plane.testing.cli_output_differential import _compare_row
    from loopx.control_plane.testing.cli_output_semantics import (
        host_prompt_static_safety_revision,
    )
    from loopx.control_plane.heartbeat.rules import HOST_LOOP_SAFETY_RULE

    assert (
        host_prompt_static_safety_revision(HOST_LOOP_SAFETY_RULE)
        == "host_prompt_static_safety_v1"
    )
    assert (
        host_prompt_static_safety_revision(
            HOST_LOOP_SAFETY_RULE.replace(
                "requires explicit authorization", "is always allowed"
            )
        )
        is None
    )
    base = _row(row_id=f"{row_kind}/heartbeat_prompt_{mode}/small/json")
    current = {
        **base,
        "chars": base["chars"] + 500,
        "host_prompt_static_safety_revision": "host_prompt_static_safety_v1",
    }
    assert not _compare_row(base, current)["failures"]
    assert _compare_row(base, {**current, "chars": base["chars"] + 513})["failures"]
    assert _compare_row(current, {**current, "chars": current["chars"] + 500})[
        "failures"
    ]
    assert _compare_row(
        {**base, "row_id": "surface/status/small/json"},
        {**current, "row_id": "surface/status/small/json"},
    )["failures"]


@pytest.mark.parametrize("row_kind", ["surface", "variant"])
@pytest.mark.parametrize("mode", ["thin", "brief", "compact", "full"])
def test_reward_memory_outcome_prompt_budget_is_one_time_bounded_and_prompt_only(
    row_kind, mode
):
    from loopx.control_plane.heartbeat.rules import REWARD_MEMORY_OUTCOME_COMPACT_RULE
    from loopx.control_plane.testing.cli_output_differential import _compare_row
    from loopx.control_plane.testing.cli_output_semantics import (
        reward_memory_outcome_prompt_revision,
    )

    full_contract = (
        REWARD_MEMORY_OUTCOME_COMPACT_RULE
        + " Todo validator exact digest evidence zero provider calls raw private"
    )
    assert (
        reward_memory_outcome_prompt_revision(full_contract)
        == "reward_memory_outcome_prompt_v1"
    )
    assert (
        reward_memory_outcome_prompt_revision(
            full_contract.replace("zero provider calls", "best effort")
        )
        is None
    )
    base = _row(row_id=f"{row_kind}/heartbeat_prompt_{mode}/small/json")
    current = {
        **base,
        "chars": base["chars"] + 640,
        "reward_memory_outcome_prompt_revision": ("reward_memory_outcome_prompt_v1"),
    }
    assert not _compare_row(base, current)["failures"]
    assert _compare_row(base, {**current, "chars": base["chars"] + 641})["failures"]
    assert _compare_row(current, {**current, "chars": current["chars"] + 205})[
        "failures"
    ]
    other = {**base, "row_id": "surface/status/small/json"}
    assert _compare_row(other, {**current, "row_id": other["row_id"]})["failures"]


@pytest.mark.parametrize(
    ("mode", "row_kind", "limit"),
    [
        ("thin", "surface", 400),
        ("brief", "variant", 720),
        ("compact", "variant", 288),
        ("full", "variant", 288),
    ],
)
def test_user_language_prompt_budget_is_one_time_and_mode_scoped(
    mode: str, row_kind: str, limit: int
) -> None:
    from loopx.control_plane.testing.cli_output_differential import _compare_row
    from loopx.control_plane.testing.cli_output_semantics import (
        heartbeat_user_language_prompt_revision,
    )

    rendered_rule = (
        "Lang=user; default=en; mix=asked/scoped."
        if mode in {"thin", "brief"}
        else "Language=user; fallback=English; mix only if asked/scoped-bilingual."
    )
    assert heartbeat_user_language_prompt_revision(rendered_rule) == (
        "heartbeat_user_language_v1"
    )
    assert heartbeat_user_language_prompt_revision(rendered_rule.replace("mix", "omit")) is None

    base = _row(
        row_id=f"{row_kind}/heartbeat_prompt_{mode}/small/json",
        qualification_policy=(
            "absolute_hot_path" if mode == "thin" else "explicit_opt_in_cold_path"
        ),
        chars=1_000,
        utf8_bytes=1_000,
        lines=20,
        compact_payload_chars=1_000,
    )
    candidate = {
        **base,
        "chars": 1_000 + limit,
        "compact_payload_chars": 1_000 + limit,
        "heartbeat_user_language_prompt_revision": "heartbeat_user_language_v1",
    }
    assert not _compare_row(base, candidate)["failures"]
    assert _compare_row(base, {**candidate, "chars": 1_001 + limit})["failures"]
    assert _compare_row(candidate, {**candidate, "chars": 1_000 + 2 * limit})[
        "failures"
    ]
    assert _compare_row(base, {**candidate, "heartbeat_user_language_prompt_revision": None})[
        "failures"
    ]
    other = {**base, "row_id": "surface/status/small/json"}
    assert _compare_row(other, {**candidate, "row_id": other["row_id"]})["failures"]
    if mode == "brief":
        assert not _compare_row(base, {**candidate, "lines": 26})["failures"]
        assert _compare_row(base, {**candidate, "lines": 27})["failures"]


def test_managed_executor_binding_budget_is_one_time_bounded_and_turn_only() -> None:
    from loopx.control_plane.testing.cli_output_differential import (
        _TURN_HOST_AND_MANAGED_EXECUTOR_BINDING_V0_GROWTH_ALLOWANCE as ALLOWANCE,
        _compare_row,
    )
    from loopx.control_plane.testing.cli_output_semantics import (
        managed_executor_binding_revision,
    )

    projection = (
        '{\n  "managed_executor": {\n'
        '    "executor_kind": "managed",\n'
        '    "available": true,\n'
        '    "unavailable_reason": null\n  }\n}'
    )
    assert (
        managed_executor_binding_revision(projection) == "managed_executor_binding_v0"
    )
    assert (
        managed_executor_binding_revision(
            projection.replace('"unavailable_reason"', '"reason"')
        )
        is None
    )

    base = _row(row_id="variant/loopx_turn_run_once_preview/small/json")
    current = {
        **base,
        "chars": base["chars"] + 254,
        "utf8_bytes": base["utf8_bytes"] + 254,
        "lines": base["lines"] + 9,
        "compact_payload_chars": base["compact_payload_chars"] + 205,
        "managed_executor_binding_revision": "managed_executor_binding_v0",
    }
    # The reviewed allowance covers the binding block plus the host-selection
    # consequences on the Turn surfaces, and nothing beyond it.
    assert not _compare_row(base, current)["failures"]
    assert _compare_row(
        base, {**current, "chars": base["chars"] + ALLOWANCE["chars"] + 1}
    )["failures"]
    # One time: the same growth against a baseline that already carries v0 is a
    # regression, not a migration.
    assert _compare_row(current, {**current, "chars": current["chars"] + 254})[
        "failures"
    ]
    # Surface scoped: the allowance never reaches a non-Turn surface.
    other = {**base, "row_id": "surface/status/small/json"}
    assert _compare_row(other, {**current, "row_id": other["row_id"]})["failures"]


def test_regular_integration_pr_keeps_requested_cli_output_base() -> None:
    ancestors = {
        ("origin/main", "HEAD"),
        ("origin/main", "origin/integration"),
    }

    selected = select_cli_output_base_ref(
        "origin/integration",
        main_ref="origin/main",
        is_ancestor=lambda ancestor, descendant: (ancestor, descendant) in ancestors,
    )

    assert selected == "origin/integration"


def test_measurement_records_semantic_shape_without_runtime_hash_noise() -> None:
    def payload(runtime_hash: str, source_hash: str) -> str:
        return json.dumps(
            {
                "action": {"todo_id": "todo_fixture"},
                "action_signature": {
                    "schema_version": "loopx_action_signature_v0",
                    "coverage": "turn_envelope_action_dimensions_v0",
                    "source_hash": runtime_hash,
                    "envelope_hash": runtime_hash,
                    "source_decision_hash": source_hash,
                    "matches": True,
                },
            }
        )

    first = measure_cli_output(
        payload("first-runtime", "first-source"), output_format="json"
    )
    second = measure_cli_output(
        payload("second-runtime", "second-source"),
        output_format="json",
    )
    assert "$.action.todo_id" in first["json_shape_paths"]
    assert first["action_signature_sha256"] == second["action_signature_sha256"]
    assert action_signature_coverages(json.loads(payload("first", "source"))) == [
        "turn_envelope_action_dimensions_v0",
    ]
    assert action_portfolio_schema_versions(
        {
            "action_portfolio": {"schema_version": "quota_action_portfolio_v0"},
            "nested": {
                "action_portfolio": {"schema_version": "quota_action_portfolio_v0"}
            },
        }
    ) == ["quota_action_portfolio_v0"]
    assert planning_horizon_schema_versions(
        {
            "planning_horizon": {"schema_version": "quota_planning_horizon_v0"},
            "nested": {
                "planning_horizon": {"schema_version": "quota_planning_horizon_v0"}
            },
        }
    ) == ["quota_planning_horizon_v0"]
    assert planning_inventory_detail_schema_versions(
        {
            "agent_todo_planning_inventory": {
                "schema_version": "todo_planning_inventory_detail_v0"
            },
            "nested": {
                "agent_todo_planning_inventory": {
                    "schema_version": "todo_planning_inventory_detail_v0"
                }
            },
        }
    ) == ["todo_planning_inventory_detail_v0"]
    assert guided_todo_delta_schema_versions(
        {"steps": [{"todo_delta": {"schema_version": "loopx_guided_todo_delta_v0"}}]}
    ) == ["loopx_guided_todo_delta_v0"]

    with_observability_field = json.loads(payload("third-runtime", "third-source"))
    with_observability_field["action_signature"]["diagnostic_note"] = "new"
    third = measure_cli_output(
        json.dumps(with_observability_field),
        output_format="json",
    )
    assert first["action_signature_sha256"] == third["action_signature_sha256"]

    without_hash_pair = json.loads(payload("fourth-runtime", "fourth-source"))
    del without_hash_pair["action_signature"]["source_hash"]
    del without_hash_pair["action_signature"]["envelope_hash"]
    fourth = measure_cli_output(json.dumps(without_hash_pair), output_format="json")
    assert first["action_signature_sha256"] != fourth["action_signature_sha256"]

    markdown = measure_cli_output(
        "# LoopX Status\n\n## Attention Queue\n",
        output_format="markdown",
    )
    assert markdown["markdown_headings"] == ["# LoopX Status", "## Attention Queue"]


def test_unchanged_large_inherited_baseline_passes() -> None:
    base = _receipt(_row())
    result = compare_cli_output_receipts(base, copy.deepcopy(base))
    assert result["ok"] is True
    assert result["failed_row_count"] == 0


def test_growth_above_policy_allowance_fails() -> None:
    base = _receipt(_row())
    candidate = _receipt(_row(chars=41_000))
    result = compare_cli_output_receipts(base, candidate)
    assert result["ok"] is False
    assert "chars grew" in result["rows"][0]["failures"][0]


def test_todo_work_count_schema_migration_has_one_time_bounded_budget() -> None:
    payload = {"agent_todos": {"work_counts": {"schema_version": "todo_work_counts_v0"}}}
    assert todo_work_counts_schema_versions(payload) == ["todo_work_counts_v0"]
    candidate = _row(
        chars=40_300,
        utf8_bytes=40_300,
        lines=1_010,
        compact_payload_chars=20_180,
        todo_work_counts_schema_versions=["todo_work_counts_v0"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["allowances"] == {
        "chars": 320,
        "utf8_bytes": 320,
        "lines": 10,
        "compact_payload_chars": 192,
    }
    assert result["rows"][0]["review_signals"] == [
        "Todo work-count schema migrated: none -> todo_work_counts_v0"
    ]


def test_todo_work_count_schema_migration_still_fails_above_bounded_growth() -> None:
    candidate = _row(
        chars=40_321,
        todo_work_counts_schema_versions=["todo_work_counts_v0"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert "chars grew by 321; allowance is 320" in result["rows"][0]["failures"]


def test_shrink_with_semantic_shape_retained_passes() -> None:
    base = _receipt(_row())
    candidate = _receipt(
        _row(chars=20_000, utf8_bytes=20_000, lines=500, compact_payload_chars=10_000)
    )
    assert compare_cli_output_receipts(base, candidate)["ok"] is True


def test_heartbeat_agent_input_contract_migration_is_explicit_and_reviewable() -> None:
    base = _row(
        row_id="surface/heartbeat_prompt_thin/small/json",
        surface_id="heartbeat_prompt_thin",
        semantic_json_keys=["task_body", "quota_guard_command"],
    )
    candidate = _row(
        row_id="surface/heartbeat_prompt_thin/small/json",
        surface_id="heartbeat_prompt_thin",
        chars=20_000,
        utf8_bytes=20_000,
        lines=500,
        compact_payload_chars=10_000,
        semantic_json_keys=["schema_version", "task_body", "interface_budget"],
        output_contract_version="heartbeat_agent_input_v1",
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "semantic_json_keys removed: quota_guard_command",
        "output contract migrated: generator payload -> heartbeat_agent_input_v1",
    ]


@pytest.mark.parametrize(
    ("row_id", "base_contract", "candidate_contract"),
    [
        ("surface/status/small/json", None, "heartbeat_agent_input_v1"),
        (
            "surface/heartbeat_prompt_thin/small/json",
            "heartbeat_agent_input_v0",
            "heartbeat_agent_input_v1",
        ),
        (
            "surface/heartbeat_prompt_thin/small/json",
            None,
            "heartbeat_agent_input_v2",
        ),
    ],
)
def test_output_contract_change_outside_declared_migration_fails_closed(
    row_id: str,
    base_contract: str | None,
    candidate_contract: str,
) -> None:
    base = _row(
        row_id=row_id,
        output_contract_version=base_contract,
    )
    candidate = _row(
        row_id=row_id,
        chars=20_000,
        utf8_bytes=20_000,
        lines=500,
        compact_payload_chars=10_000,
        semantic_json_keys=["status_contract"],
        output_contract_version=candidate_contract,
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is False
    assert "output_contract_version changed" in result["rows"][0]["failures"]
    assert any(
        failure.startswith("semantic_json_keys removed")
        for failure in result["rows"][0]["failures"]
    )


@pytest.mark.parametrize(
    ("candidate", "failure_fragment"),
    [
        (_row(semantic_json_keys=["status_contract"]), "semantic_json_keys removed"),
        (
            _row(action_signature_sha256="changed"),
            "action_signature semantic digest changed",
        ),
    ],
)
def test_smaller_candidate_still_fails_when_semantics_are_removed(
    candidate: dict[str, object],
    failure_fragment: str,
) -> None:
    candidate.update(chars=20_000, utf8_bytes=20_000, lines=500)
    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))
    assert result["ok"] is False
    assert any(failure_fragment in failure for failure in result["rows"][0]["failures"])


def test_declared_action_signature_coverage_migration_requires_review() -> None:
    candidate = _row(
        action_signature_sha256="versioned-semantic-signature",
        action_signature_coverages=["turn_envelope_action_dimensions_v1"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "action_signature coverage migrated: "
        "turn_envelope_action_dimensions_v0 -> turn_envelope_action_dimensions_v1"
    ]


def test_action_portfolio_coverage_migration_requires_review() -> None:
    candidate = _row(
        action_signature_sha256="portfolio-semantic-signature",
        action_signature_coverages=["turn_envelope_action_dimensions_v2"],
        chars=41_000,
        utf8_bytes=41_000,
        lines=1_030,
        compact_payload_chars=20_750,
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["allowances"] == {
        "chars": 1_600,
        "utf8_bytes": 1_600,
        "lines": 42,
        "compact_payload_chars": 1_280,
    }
    assert result["rows"][0]["review_signals"] == [
        "action_signature coverage migrated: "
        "turn_envelope_action_dimensions_v0 -> turn_envelope_action_dimensions_v2"
    ]


def test_action_portfolio_migration_still_fails_above_bounded_growth() -> None:
    candidate = _row(
        action_signature_sha256="oversized-portfolio-semantic-signature",
        action_signature_coverages=["turn_envelope_action_dimensions_v2"],
        chars=41_601,
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert "chars grew by 1601; allowance is 1600" in (result["rows"][0]["failures"])


def test_quota_action_portfolio_schema_migration_has_same_bounded_budget() -> None:
    candidate = _row(
        action_signature_sha256=None,
        action_signature_coverages=[],
        action_portfolio_schema_versions=["quota_action_portfolio_v0"],
        chars=41_000,
        utf8_bytes=41_000,
        lines=1_030,
        compact_payload_chars=20_750,
    )
    base = _row(
        action_signature_sha256=None,
        action_signature_coverages=[],
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "action_portfolio schema migrated: none -> quota_action_portfolio_v0"
    ]


def test_quota_action_portfolio_v1_schema_migration_is_declared() -> None:
    candidate = _row(
        action_portfolio_schema_versions=["quota_action_portfolio_v1"],
    )
    base = _row(
        action_portfolio_schema_versions=["quota_action_portfolio_v0"],
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "action_portfolio schema migrated: quota_action_portfolio_v0 -> "
        "quota_action_portfolio_v1"
    ]


def test_quota_action_portfolio_v2_context_migration_is_declared() -> None:
    candidate = _row(
        action_portfolio_schema_versions=["quota_action_portfolio_v2"],
        compact_payload_chars=21_280,
    )
    base = _row(
        action_portfolio_schema_versions=["quota_action_portfolio_v1"],
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "action_portfolio schema migrated: quota_action_portfolio_v1 -> "
        "quota_action_portfolio_v2"
    ]


def test_guided_todo_delta_schema_migration_requires_review() -> None:
    candidate = _row(
        guided_todo_delta_schema_versions=["loopx_guided_todo_delta_v0"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "guided todo delta schema migrated: none -> loopx_guided_todo_delta_v0"
    ]


def test_guided_todo_delta_migration_still_fails_above_bounded_growth() -> None:
    candidate = _row(
        guided_todo_delta_schema_versions=["loopx_guided_todo_delta_v0"],
        chars=40_513,
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert "chars grew by 513; allowance is 512" in (result["rows"][0]["failures"])


def test_unknown_guided_todo_delta_schema_migration_fails_closed() -> None:
    candidate = _row(
        guided_todo_delta_schema_versions=["loopx_guided_todo_delta_v1"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert result["rows"][0]["failures"] == [
        "guided todo delta schema coverage changed"
    ]


def test_unknown_action_portfolio_schema_migration_fails_closed() -> None:
    candidate = _row(
        action_portfolio_schema_versions=["quota_action_portfolio_v3"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert result["rows"][0]["failures"] == ["action_portfolio schema coverage changed"]


def test_unknown_action_signature_coverage_migration_fails_closed() -> None:
    candidate = _row(
        action_signature_sha256="unknown-semantic-signature",
        action_signature_coverages=["turn_envelope_action_dimensions_v5"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert result["rows"][0]["failures"] == ["action_signature semantic digest changed"]


def test_planning_horizon_v0_migration_has_one_bounded_growth_budget() -> None:
    candidate = _row(
        action_signature_sha256="planning-horizon-semantic-signature",
        action_signature_coverages=["turn_envelope_action_dimensions_v3"],
        planning_horizon_schema_versions=["quota_planning_horizon_v0"],
        chars=43_200,
        utf8_bytes=43_200,
        lines=1_084,
        compact_payload_chars=22_800,
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "action_signature coverage migrated: "
        "turn_envelope_action_dimensions_v0 -> turn_envelope_action_dimensions_v3",
        "planning_horizon schema migrated: none -> quota_planning_horizon_v0",
    ]


def test_planning_horizon_v0_migration_fails_above_its_bounded_growth() -> None:
    candidate = _row(
        action_signature_sha256="oversized-planning-horizon-signature",
        action_signature_coverages=["turn_envelope_action_dimensions_v3"],
        planning_horizon_schema_versions=["quota_planning_horizon_v0"],
        chars=43_201,
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert "chars grew by 3201; allowance is 3200" in (result["rows"][0]["failures"])


def test_unknown_planning_horizon_schema_migration_fails_closed() -> None:
    candidate = _row(
        planning_horizon_schema_versions=["quota_planning_horizon_v1"],
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is False
    assert result["rows"][0]["failures"] == ["planning_horizon schema coverage changed"]


def test_planning_inventory_detail_v0_has_one_bounded_growth_budget() -> None:
    candidate = _row(
        planning_inventory_detail_schema_versions=["todo_planning_inventory_detail_v0"],
        chars=41_280,
        utf8_bytes=41_280,
        lines=1_036,
        compact_payload_chars=21_024,
    )

    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["review_signals"] == [
        "planning inventory detail schema migrated: "
        "none -> todo_planning_inventory_detail_v0"
    ]


def test_planning_inventory_detail_migration_is_bounded_and_fail_closed() -> None:
    oversized = _row(
        planning_inventory_detail_schema_versions=["todo_planning_inventory_detail_v0"],
        chars=41_281,
    )
    unknown = _row(
        planning_inventory_detail_schema_versions=["todo_planning_inventory_detail_v1"]
    )

    oversized_result = compare_cli_output_receipts(
        _receipt(_row()),
        _receipt(oversized),
    )
    unknown_result = compare_cli_output_receipts(
        _receipt(_row()),
        _receipt(unknown),
    )

    assert oversized_result["ok"] is False
    assert (
        "chars grew by 1281; allowance is 1280"
        in (oversized_result["rows"][0]["failures"])
    )
    assert unknown_result["rows"][0]["failures"] == [
        "planning inventory detail schema coverage changed"
    ]


def test_runtime_root_route_growth_has_per_route_budget() -> None:
    base = _row(
        chars=1_000,
        utf8_bytes=1_000,
        lines=10,
        compact_payload_chars=1_000,
        action_signature_sha256=None,
        action_signature_coverages=[],
    )
    candidate = _row(
        chars=1_320,
        utf8_bytes=1_320,
        lines=10,
        compact_payload_chars=1_320,
        action_signature_sha256=None,
        action_signature_coverages=[],
        runtime_root_command_route_count=2,
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is True
    assert result["rows"][0]["allowances"] == {
        "chars": 320,
        "utf8_bytes": 320,
        "lines": 2,
        "compact_payload_chars": 320,
    }
    assert result["rows"][0]["review_signals"] == [
        "runtime-root command route coverage added: 2 executable route(s)"
    ]


def test_runtime_root_route_growth_still_fails_above_per_route_budget() -> None:
    base = _row(
        chars=1_000,
        utf8_bytes=1_000,
        lines=10,
        compact_payload_chars=1_000,
        action_signature_sha256=None,
        action_signature_coverages=[],
    )
    candidate = _row(
        chars=1_321,
        utf8_bytes=1_321,
        lines=10,
        compact_payload_chars=1_321,
        action_signature_sha256=None,
        action_signature_coverages=[],
        runtime_root_command_route_count=2,
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is False
    assert "chars grew by 321; allowance is 320" in result["rows"][0]["failures"]


def test_invalid_runtime_root_route_count_does_not_grant_budget() -> None:
    base = _row(
        chars=1_000,
        utf8_bytes=1_000,
        lines=10,
        compact_payload_chars=1_000,
    )
    candidate = _row(
        chars=1_097,
        utf8_bytes=1_097,
        lines=10,
        compact_payload_chars=1_097,
        runtime_root_command_route_count=True,
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is False
    assert result["rows"][0]["allowances"] == {
        "chars": 64,
        "utf8_bytes": 128,
        "lines": 2,
        "compact_payload_chars": 64,
    }


def test_runtime_root_route_count_only_matches_executable_command_prefixes() -> None:
    text = (
        "  loopx --runtime-root /tmp/indented refresh-state\n"
        "loopx --runtime-root /tmp/runtime refresh-state\n"
        '{"command": "loopx --runtime-root \'/tmp/runtime root\' quota spend-slot"}\n'
        "- expanded: `loopx --runtime-root /tmp/runtime heartbeat-prompt`\n"
        "Use --runtime-root PATH to select a runtime.\n"
        "The command is loopx --runtime-root /tmp/runtime.\n"
        "loopx --runtime-root"
    )

    assert runtime_root_command_route_count(text) == 4


def test_runtime_root_route_allowance_is_fail_closed_for_invalid_counts() -> None:
    base = _row(
        chars=1_000,
        utf8_bytes=1_000,
        lines=10,
        compact_payload_chars=1_000,
        runtime_root_command_route_count=0,
    )
    candidate = _row(
        chars=1_000,
        utf8_bytes=1_000,
        lines=10,
        compact_payload_chars=1_000,
        runtime_root_command_route_count="2",
    )

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["rows"][0]["allowances"] == {
        "chars": 64,
        "utf8_bytes": 128,
        "lines": 2,
        "compact_payload_chars": 64,
    }


def test_observed_shape_removal_is_a_review_signal_not_a_permanent_red_light() -> None:
    candidate = _row(json_shape_paths=["$", "$.status_contract"])
    candidate.update(chars=20_000, utf8_bytes=20_000, lines=500)
    result = compare_cli_output_receipts(_receipt(_row()), _receipt(candidate))
    assert result["ok"] is True
    assert result["review_required"] is True
    assert "json_shape_paths removed" in result["rows"][0]["review_signals"][0]


def test_markdown_heading_removal_requires_review() -> None:
    base_row = _row(
        row_id="surface/status/small/markdown",
        format="markdown",
        chars=2_000,
        utf8_bytes=2_000,
        lines=30,
        compact_payload_chars=None,
        semantic_json_keys=[],
        json_shape_paths=[],
        markdown_headings=["# LoopX Status", "## Attention Queue"],
        action_signature_sha256=None,
    )
    candidate = copy.deepcopy(base_row)
    candidate["markdown_headings"] = ["# LoopX Status"]
    result = compare_cli_output_receipts(_receipt(base_row), _receipt(candidate))
    assert result["ok"] is True
    assert result["review_required"] is True
    assert "markdown_headings removed" in result["rows"][0]["review_signals"][0]


def test_markdown_rows_ignore_json_only_semantic_metadata() -> None:
    base = _row(
        row_id="surface/status/small/markdown",
        format="markdown",
        semantic_json_keys=["legacy_json_key"],
    )
    candidate = copy.deepcopy(base)
    candidate["semantic_json_keys"] = []

    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))

    assert result["ok"] is True
    assert result["review_required"] is False


def test_candidate_only_row_is_allowed_but_base_row_removal_fails() -> None:
    extra = _row(row_id="surface/new/small/json", surface_id="new")
    candidate_only = compare_cli_output_receipts(_receipt(), _receipt(extra))
    assert candidate_only["ok"] is True
    assert candidate_only["candidate_only_row_count"] == 1

    removed = compare_cli_output_receipts(_receipt(_row()), _receipt())
    assert removed["ok"] is False
    assert "missing from candidate" in removed["rows"][0]["failures"][0]


def test_fixture_contract_mismatch_fails_closed() -> None:
    candidate = _receipt(_row())
    candidate["fixture_contract_version"] = "different"
    with pytest.raises(ValueError, match="fixture_contract_version"):
        compare_cli_output_receipts(_receipt(_row()), candidate)


@pytest.mark.parametrize("previous", range(4))
def test_agent_context_v4_migration_is_bounded_and_one_time(previous):
    base = _row(
        action_signature_coverages=[f"turn_envelope_action_dimensions_v{previous}"]
    )
    candidate = {
        **base,
        "action_signature_sha256": "agent-context-signature",
        "action_signature_coverages": ["turn_envelope_action_dimensions_v4"],
        "chars": 42_048,
        "utf8_bytes": 42_048,
        "lines": 1_048,
        "compact_payload_chars": 21_664,
    }
    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))
    assert result["ok"] and result["review_required"]
    assert result["rows"][0]["review_signals"] == [
        f"action_signature coverage migrated: turn_envelope_action_dimensions_v{previous}"
        " -> turn_envelope_action_dimensions_v4"
    ]
    for metric in ("chars", "utf8_bytes", "lines", "compact_payload_chars"):
        too_large = {**candidate, metric: candidate[metric] + 1}
        assert not compare_cli_output_receipts(_receipt(base), _receipt(too_large))[
            "ok"
        ]
    # After migration, neither growing again nor changing semantics is excused.
    grown = {**candidate, "chars": candidate["chars"] + 2_048}
    assert not compare_cli_output_receipts(_receipt(candidate), _receipt(grown))["ok"]
    changed = {**candidate, "action_signature_sha256": "unexpected-semantic-change"}
    assert not compare_cli_output_receipts(_receipt(candidate), _receipt(changed))["ok"]
    reverse = {
        **candidate,
        "action_signature_coverages": base["action_signature_coverages"],
        "action_signature_sha256": "reverse-signature",
    }
    assert not compare_cli_output_receipts(_receipt(candidate), _receipt(reverse))["ok"]


def test_public_multi_subagent_probe_reaches_v4_producer(tmp_path):
    import runpy
    from pathlib import Path
    from tests.control_plane import test_cli_output_budget as probe
    from loopx.control_plane.testing import cli_output_semantics as semantics

    runner = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "examples/control_plane/cli-output-probe-runner.py"
        )
    )
    with probe._stable_budget_fixture_root(tmp_path) as root:
        rows = runner["_multi_subagent_rows"](probe, semantics, root)
    assert len(rows) == 1
    assert rows[0]["action_signature_coverages"] == [
        "turn_envelope_action_dimensions_v4"
    ]
    assert any("agent_context" in path for path in rows[0]["json_shape_paths"])


def test_measurement_only_probe_skips_ceiling_but_keeps_semantic_shape() -> None:
    import runpy
    from pathlib import Path

    runner = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "examples/control_plane/cli-output-probe-runner.py"
        )
    )
    validate = runner["_assert_output_contract"]
    validate(
        output_format="json",
        text='{"required": true}',
        measurement={"payload": {"required": True}},
        semantic_json_keys=("required",),
        markdown_anchor=None,
    )
    with pytest.raises(AssertionError, match="lost semantic key"):
        validate(
            output_format="json",
            text="{}",
            measurement={"payload": {}},
            semantic_json_keys=("required",),
            markdown_anchor=None,
        )


@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_projection_envelope_migration_is_status_only_bounded_and_one_time(output_format):
    from loopx.control_plane.testing.cli_output_semantics import projection_envelope_schema_versions

    assert projection_envelope_schema_versions({"projection_envelope": {
        "schema_version": "loopx_projection_envelope_v0"}}) == ["loopx_projection_envelope_v0"]
    assert projection_envelope_schema_versions("- projection: envelope=`loopx_projection_envelope_v0` observed_at=`today`") == ["loopx_projection_envelope_v0"]
    limits = ({"chars": 3000, "utf8_bytes": 3000, "lines": 110, "compact_payload_chars": 2048}
              if output_format == "json" else {"chars": 192, "utf8_bytes": 224, "lines": 3, "compact_payload_chars": 0})
    base = _row(format=output_format, row_id=f"surface/status/small/{output_format}")
    candidate = {**base, "projection_envelope_schema_versions": ["loopx_projection_envelope_v0"],
                 **{metric: base[metric] + limit for metric, limit in limits.items()}}
    result = compare_cli_output_receipts(_receipt(base), _receipt(candidate))
    assert result["ok"] and result["review_required"]
    # JSON fixtures are large enough for ordinary ratio allowances; use their
    # smaller real scale when checking bounded Markdown growth.
    if output_format == "markdown":
        base.update(chars=1000, utf8_bytes=1000, lines=30)
        candidate.update(**{metric: base[metric] + limit for metric, limit in limits.items()})
    for metric in ("chars", "utf8_bytes", "lines"):
        too_large = {**candidate, metric: candidate[metric] + 1}
        assert not compare_cli_output_receipts(_receipt(base), _receipt(too_large))["ok"]
    grown = {**candidate, "chars": candidate["chars"] + limits["chars"]}
    assert not compare_cli_output_receipts(_receipt(candidate), _receipt(grown))["ok"]
    for versions in ([], ["loopx_projection_envelope_v1"]):
        unknown = {**candidate, "projection_envelope_schema_versions": versions}
        assert not compare_cli_output_receipts(_receipt(candidate), _receipt(unknown))["ok"]
    for surface in ("quota_should_run", "todo_list", "status_unrelated"):
        outside_base = {**base, "row_id": f"surface/{surface}/small/{output_format}"}
        outside = {**candidate, "row_id": outside_base["row_id"]}
        assert not compare_cli_output_receipts(_receipt(outside_base), _receipt(outside))["ok"]
