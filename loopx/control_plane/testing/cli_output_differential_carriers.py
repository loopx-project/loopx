from __future__ import annotations

from ..quota.turn_envelope import (
    ACTION_SIGNATURE_COVERAGE_V0,
    ACTION_SIGNATURE_COVERAGE_V2,
    ACTION_SIGNATURE_COVERAGE_V4,
)
from .cli_output_differential_allowances import (
    JsonObject,
    MetricAllowance,
    mapping_field,
    shape_paths,
    surface_from_row_id,
)


def _task_step_read_fence_migration(
    base: JsonObject, candidate: JsonObject
) -> tuple[MetricAllowance, list[str]]:
    """Bound the first read-fence projection, not later growth or other views."""
    before = base.get("next_action_basis_count", 0)
    after = candidate.get("next_action_basis_count", 0)
    row_id = str(base["row_id"])
    if not (
        base["format"] == "json"
        and row_id.startswith(
            ("surface/status/", "surface/quota_should_run/", "variant/status_task_graph_detail/")
        )
        and type(before) is int
        and before == 0
        and type(after) is int
        and 0 < after <= 4
    ):
        return {}, []
    # Each ordinary fence is 100 pretty / 94 compact chars. The unchanged
    # two-peer detail fixture adds 457 / 346 chars including task/actor rows.
    per_fence: MetricAllowance = (
        {"chars": 256, "utf8_bytes": 256, "lines": 8, "compact_payload_chars": 192}
        if row_id.startswith("variant/status_task_graph_detail/")
        else {"chars": 192, "utf8_bytes": 192, "lines": 6, "compact_payload_chars": 144}
    )
    return {metric: value * after for metric, value in per_fence.items()}, [
        "task-bound recommendation read fences added; bounded one-time JSON growth",
    ]


def _authoring_input_migration(
    base: JsonObject, candidate: JsonObject
) -> tuple[MetricAllowance, list[str]]:
    """Bound only the observed addition; an already upgraded base gets none."""
    surface = surface_from_row_id(str(base["row_id"]))
    before = mapping_field(base, "authoring_inputs")
    after = mapping_field(candidate, "authoring_inputs")
    limits: MetricAllowance = {}
    reason = ""
    if (
        surface in {"start_goal_guided", "start_goal_guided_command_pack_detail"}
        and before.get("complete_todo_readback") is False
        and after.get("complete_todo_readback") is True
    ):
        # Same fixture: JSON +948 chars/+894 compact/+6 lines; Markdown
        # +436 chars/+2 lines. Preserve full-body and authority guidance.
        limits = (
            {"chars": 1_000, "utf8_bytes": 1_000, "lines": 8, "compact_payload_chars": 960}
            if base.get("format") == "json"
            else {"chars": 480, "utf8_bytes": 480, "lines": 3, "compact_payload_chars": 0}
        )
        reason = "complete Todo readback step added"
    elif (
        surface in {"quota_should_run", "loopx_turn_plan"}
        and base.get("format") == "json"
        and before.get("vision_cli_authoring") is False
        and after.get("vision_cli_authoring") is True
    ):
        # Default required-replan quota gains the full authoring input:
        # +3,049 chars/+1,807 compact/+94 lines. Turn already had the schema
        # and gains only +237 chars of first-write/recovery guidance.
        limits = (
            {"chars": 3_200, "utf8_bytes": 3_200, "lines": 96, "compact_payload_chars": 1_920}
            if surface == "quota_should_run"
            else {"chars": 256, "utf8_bytes": 256, "lines": 0, "compact_payload_chars": 256}
        )
        reason = "Vision CLI authoring input added"
    elif (
        surface
        in {
            "start_goal_guided",
            "start_goal_guided_command_pack_detail",
            "bootstrap_command_pack",
            "bootstrap_command_pack_message_only",
        }
        and before.get("first_delivery_guidance") is False
        and after.get("first_delivery_guidance") is True
    ):
        # Frozen 96-row fixture: +2,340 JSON chars, +3,496 for explicit
        # command-pack detail, +578 Markdown. Keep all existing consumers and
        # the full guidance; no additional lines or absolute budget increase.
        chars = (
            (3_600 if surface == "start_goal_guided_command_pack_detail" else 2_400)
            if base.get("format") == "json"
            else 600
        )
        limits = {
            "chars": chars,
            "utf8_bytes": chars,
            "lines": 0,
            "compact_payload_chars": chars if base.get("format") == "json" else 0,
        }
        reason = "complete initial-delivery quality guidance added"
    return limits, [reason + "; bounded one-time growth"] if limits else []


def _continuation_instruction_migration(
    base: JsonObject, candidate: JsonObject
) -> tuple[MetricAllowance, list[str]]:
    """Qualify only reviewed instruction transitions on their actual carriers."""
    row_id = str(base["row_id"])
    surface = row_id.split("/")[1]
    before = mapping_field(base, "continuation_instructions")
    after = mapping_field(candidate, "continuation_instructions")

    def migrated(clause: str) -> bool:
        return (
            before.get(f"{clause}_before") is True
            and before.get(f"{clause}_after") is False
            and after.get(f"{clause}_before") is False
            and after.get(f"{clause}_after") is True
        )

    chars, lines = 0, 0
    if (
        surface in {"quota_should_run", "loopx_turn_plan"}
        and base.get("format") == candidate.get("format") == "json"
        and migrated("objective_replan")
    ):
        # Matched crowded fixture: +729 after moving the ordinary read hint
        # into the complete replan review. Once delivered, ordinary growth
        # budgets resume. No line, shape, action or envelope-budget exception.
        chars += 768
    if surface in {
        "quota_should_run",
        "loopx_turn_plan",
        "quota_should_run_all_detail",
        "quota_should_run_goal_boundary_detail",
        "quota_should_run_scheduler_detail",
        "quota_should_run_todo_summary_detail",
        "quota_should_run_turn_envelope",
        "quota_should_run_user_todo_summary_detail",
        "quota_should_run_vision_detail",
    }:
        # Same-fixture context +208, with one character of receipt metadata
        # variation; optional replan guidance +363. Do not loosen line budgets.
        if migrated("context"):
            chars += 224
        if migrated("replan"):
            chars += 384
        if migrated("objective_maintenance"):
            # Matched fixture: maintenance +208 chars (including separator).
            # The complete reviewed transition also carries platform-normalized
            # projection metadata; preserve that prose with a bounded allowance.
            chars += 224
            lines = 4
        if migrated("open_work_review"):
            # Same fixture: +212 text bytes, +213 quota / +216 turn-plan
            # JSON chars after accounting metadata. Preserve all prior prose;
            # only this complete transition receives the one-time allowance.
            chars += 224
            if migrated("objective_maintenance"):
                # Combined objective + work-review migration measured 497
                # characters on Windows. Do not drop either reviewed clause to
                # satisfy the legacy 224-character growth floor.
                chars = max(chars, 512)
    elif surface in {
        "heartbeat_prompt_thin",
        "heartbeat_prompt_brief",
        "heartbeat_prompt_compact",
    }:
        # Complete stop/correction rollout: thin +154, brief +163, compact +215.
        if migrated("stop"):
            chars = 224
        if migrated("authorized_delivery"):
            # Same public fixture: thin +427 body chars plus generator policy
            # text in brief; retain authority/recovery clauses. Once present,
            # ordinary growth limits resume; no new line or shape allowance.
            chars += 512
    if row_id.startswith(
        (
            "surface/heartbeat_prompt_thin/",
            "variant/heartbeat_prompt_brief/",
            "variant/heartbeat_prompt_compact/",
            "variant/heartbeat_prompt_full/",
        )
    ) and migrated("outcome_refinement"):
        # The complete outcome paragraph adds 523 chars/bytes in all four
        # matched heartbeat modes. Bound that one-time addition, including
        # a boolean budget diagnostic's single-character variation; later
        # growth, other surfaces, lines and shapes receive no new allowance.
        chars += 544
    before_basis = mapping_field(base, "turn_envelope_cost_basis")
    after_basis = mapping_field(candidate, "turn_envelope_cost_basis")
    if (
        surface in {"loopx_turn_plan", "quota_should_run_turn_envelope"}
        and base.get("format") == candidate.get("format") == "markdown"
        and base.get("turn_budget_warning") is False
        and candidate.get("turn_budget_warning") is True
        and before_basis.get("objective_maintenance_before") is True
        and after_basis.get("objective_maintenance_after") is True
    ):
        # Measured warning appearance: +417/+419 chars and four lines. Preserve
        # the warning and its target; paired JSON proves the reviewed cause.
        chars, lines = max(chars, 448), 4
    if not chars:
        return {}, []
    return {
        "chars": chars,
        "utf8_bytes": chars,
        "lines": lines,
        "compact_payload_chars": chars if base.get("format") == "json" else 0,
    }, ["reviewed continuation instructions migrated; bounded one-time growth"]


def _turn_projection_migration(
    base: JsonObject, candidate: JsonObject
) -> tuple[MetricAllowance, list[str]]:
    old_paths = shape_paths(base)
    new_paths = shape_paths(candidate)
    # Inherited Stride now supplies the existing bounded decision contract on
    # the required-review route. Only absence -> complete projection receives
    # this one-time allowance; later growth, removed facts and other paths do not.
    stride_routes: dict[str, tuple[str, int]] = {
        "surface/quota_should_run/crowded/json": ("$", 6_800),
        "surface/loopx_turn_plan/crowded/json": (
            "$.work_contract" if "$.work_contract" in new_paths else "$.turn_envelope",
            6_500,
        ),
    }
    stride_route = stride_routes.get(str(base.get("row_id")))
    if stride_route and candidate.get("row_id") == base.get("row_id"):
        root, bound = stride_route
        control = f"{root}.replan_action_packet.stride_control"
        strategy = f"{root}.autonomous_replan_obligation.replan_context.stride_strategy"
        required = {
            f"{control}.{key}"
            for key in (
                "schema_version",
                "strategy_version",
                "reference_context_id",
                "reference_obligation_id",
                "required_fields",
                "rule",
                "minimal_example.schema_version",
                "minimal_example.strategy_version",
                "minimal_example.reference_context_id",
                "minimal_example.reference_obligation_id",
                "minimal_example.basis",
                "minimal_example.action",
                *[
                    f"minimal_example.axes.{axis}.{field}"
                    for axis in ("work_slice", "route_distance", "validation_scope")
                    for field in ("plan", "reason")
                ],
                *[
                    f"minimal_example.next_probe.{field}"
                    for field in (
                        "scope",
                        "prediction",
                        "adoption_criterion",
                        "cost_bound",
                        "minimum_evidence",
                    )
                ],
            )
        }
        # Optional review blocks are required in full once projected. Presence
        # of representative leaves cannot qualify a truncated decision example.
        for review, fields in (
            (
                "previous_review",
                (
                    "reference_decision_ref",
                    "adoption",
                    "information_gain",
                    "goal_gain",
                    "evidence_refs",
                    "lesson",
                ),
            ),
            ("explore_review", ("revision", "disposition", "considered_refs", "reason")),
        ):
            prefix = f"{control}.minimal_example.{review}"
            if any(path == prefix or path.startswith(prefix + ".") for path in new_paths):
                required.update(f"{prefix}.{field}" for field in fields)
        added = new_paths - old_paths
        if (
            base.get("format") == candidate.get("format") == "json"
            and control not in old_paths
            and required <= new_paths
            and old_paths <= new_paths
            and all(
                path == control or path.startswith(control + ".") or path == strategy
                for path in added
            )
        ):
            return {
                "chars": bound,
                "utf8_bytes": bound,
                "compact_payload_chars": bound,
            }, [
                "inherited Stride decision context enabled; bounded one-time JSON growth"
            ]
    profile = "$.turn_envelope.contract_capsule.execution_profile"
    restored = {
        f"{profile}.{field}"
        for field in (
            "small_scale_streak_threshold",
            "outcome_floor",
            "outcome_floor.configured",
            "outcome_floor.surface_streak_threshold",
            "outcome_floor.must_advance",
            "outcome_floor.must_advance[]",
        )
    }
    if (
        str(base.get("row_id", "")).startswith("surface/loopx_turn_plan/")
        and candidate.get("row_id") == base["row_id"]
        and base.get("format") == candidate.get("format") == "json"
        and {
            f"{profile}.{field}"
            for field in (
                "cadence",
                "minimum_scale",
                "spend_rule",
                "must_include",
                "must_include[]",
            )
        }
        <= old_paths
        and old_paths.isdisjoint(restored)
        and old_paths <= new_paths
        and new_paths - old_paths == restored
        and base.get("action_signature_coverages")
        == candidate.get("action_signature_coverages")
        and base.get("action_signature_coverages")
        in ([ACTION_SIGNATURE_COVERAGE_V0], [ACTION_SIGNATURE_COVERAGE_V2])
    ):
        # Same 96-row fixture: restoring the native outcome/degradation rules
        # adds 138–142 JSON chars/bytes, zero lines. Keep 18 chars of headroom
        # only for this first complete projection; later/partial/unrelated
        # growth and absolute ceilings retain their existing guards.
        return {"chars": 160, "utf8_bytes": 160, "compact_payload_chars": 160}, [
            "complete execution profile restored; bounded one-time JSON growth"
        ]
    # The unchanged public multi-subagent fixture grows 1,147 JSON characters
    # when its bounded contribution replaces a historical reference. Qualify
    # only that transition (133 chars headroom), never later inline growth.
    if (
        base.get("row_id")
        == "variant/quota_should_run_turn_envelope_multi_subagent/small/json"
        and candidate.get("row_id") == base["row_id"]
        and base.get("format") == candidate.get("format") == "json"
        and base.get("action_signature_coverages")
        == candidate.get("action_signature_coverages")
        == [ACTION_SIGNATURE_COVERAGE_V4]
        and {"$.agent_context.detail_ref", "$.agent_context.content_hash"} <= old_paths
        and "$.agent_context.contributions[]" not in old_paths
        and {"$.agent_context.contributions[]", "$.agent_context.failures"} <= new_paths
        and "$.agent_context.detail_ref" not in new_paths
    ):
        return {"chars": 1_280, "utf8_bytes": 1_280, "compact_payload_chars": 1_280}, [
            "bounded planning context migrated from reference to inline; one-time growth"
        ]
    return {}, []
