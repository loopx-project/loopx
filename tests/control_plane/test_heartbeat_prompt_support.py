from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from loopx.control_plane.agents.profile import (
    AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS,
    normalize_agent_profile,
)
from loopx.control_plane.heartbeat.agent import (
    agent_prompt_command_args,
    agent_profile_scopes,
    normalize_agent_scopes,
)
from loopx.control_plane.heartbeat.budget import (
    REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS,
    build_interface_budget,
    heartbeat_prompt_mode,
    prompt_budget_text,
)
from loopx.control_plane.heartbeat.host import (
    uses_ark_managed_agent_goal_host,
    uses_native_goal_host_loop,
)
from loopx.control_plane.heartbeat.rules import SCOPE_BOUNDED_WORK_RULE
from loopx.control_plane.heartbeat.visible_goal import (
    build_visible_goal_initial_runtime_capability_projection,
    validate_visible_goal_policy_rule,
)
from loopx.heartbeat_prompt import (
    build_heartbeat_prompt,
    render_heartbeat_prompt_markdown,
)


def test_heartbeat_prompt_mode_defaults_to_thin() -> None:
    assert heartbeat_prompt_mode() == "thin"
    assert heartbeat_prompt_mode(full=True) == "full"
    assert heartbeat_prompt_mode(compact=True) == "compact"
    assert heartbeat_prompt_mode(brief=True) == "brief"
    assert heartbeat_prompt_mode(thin=True) == "thin"


def test_prompt_budget_text_redacts_goal_and_active_state() -> None:
    rendered = prompt_budget_text(
        "goal loopx-meta at state file",
        goal_id="loopx-meta",
        active_state="file",
    )
    assert "<GOAL_ID>" in rendered
    assert "<ACTIVE_STATE>" in rendered
    assert "loopx-meta" not in rendered
    assert "file" not in rendered


def test_interface_budget_uses_visible_goal_mode() -> None:
    budget = build_interface_budget(
        task_body="short task body",
        goal_id="loopx-meta",
        active_state="active",
        native_goal_host=True,
    )
    assert budget["mode"] == "visible_goal"
    assert budget["max_chars"] == 4000
    assert budget["within_budget"] is True


@pytest.mark.parametrize("mode,limit", [("thin", 2500), ("visible_goal", 4000)])
def test_prompt_body_limit_remains_independent_of_json_envelope(mode: str, limit: int) -> None:
    for size in (limit, limit + 1):
        budget = build_interface_budget(
            task_body="x" * size,
            goal_id="fixture-goal",
            active_state="fixture-state",
            native_goal_host=mode == "visible_goal",
        )
        assert budget["max_chars"] == limit
        assert budget["within_budget"] is (size <= limit)


def test_heartbeat_envelope_and_body_overflow_are_both_rejected() -> None:
    smoke = runpy.run_path(str(
        Path(__file__).resolve().parents[2]
        / "examples/control_plane/hot-path-interface-budget-smoke.py"
    ))
    check = smoke["assert_surface"]
    payload = build_heartbeat_prompt(
        goal_id="interface-budget-goal", thin=True,
        runtime_profile="codex_app_heartbeat", agent_id="worker-a",
        agent_scopes=["implementation", "review"],
    )
    check("heartbeat_prompt_json", payload)
    # Check the envelope boundary independently of the real prompt's remaining
    # headroom: adding a metadata key can already put a valid prompt over budget.
    envelope = {"interface_budget": payload["interface_budget"], "extra": ""}
    envelope["extra"] = "x" * (4800 - smoke["json_size"](envelope))
    check("heartbeat_prompt_json", envelope)
    envelope["extra"] += "x"
    with pytest.raises(AssertionError):
        check("heartbeat_prompt_json", envelope)
    # A small envelope cannot waive the independently evaluated body budget.
    with pytest.raises(AssertionError):
        check("heartbeat_prompt_json", {"interface_budget": {"within_budget": False}})


def test_agent_scope_normalization_dedupes_and_rejects_angle_brackets() -> None:
    assert normalize_agent_scopes(["a b", "a  b", "c"]) == ["a b", "c"]
    with pytest.raises(ValueError, match="angle brackets"):
        normalize_agent_scopes(["bad<scope>"])


def test_agent_profile_scopes_reads_common_profile_keys() -> None:
    profile = {"scope_summary": "one", "default_scopes": ["two three"]}
    assert agent_profile_scopes(profile) == ["one", "two three"]


def test_heartbeat_accepts_the_full_authored_profile_scope() -> None:
    agent_id = "codex-product-capability"
    scope = "x" * AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS
    profile = normalize_agent_profile(
        {"agent_id": agent_id, "scope_summary": scope},
        registered_agents=[agent_id],
    )
    payload = build_heartbeat_prompt(
        goal_id="profile-scope-limit",
        thin=True,
        agent_id=agent_id,
        registered_agents=[agent_id],
        agent_profile=profile,
    )
    assert payload["ok"] is True
    assert payload["agent_scopes"] == [scope]
    assert payload["agent_scope_source"] == "agent_profile_v1"
    assert scope in payload["task_body"]

    explicit = build_heartbeat_prompt(
        goal_id="profile-scope-limit",
        thin=True,
        agent_id=agent_id,
        agent_scopes=["explicit bounded task"],
        registered_agents=[agent_id],
        agent_profile=profile,
    )
    assert explicit["agent_scopes"] == ["explicit bounded task"]
    assert explicit["agent_scope_source"] == "argument"
    assert "explicit bounded task" in explicit["task_body"]

    # Explicit scope must not validate an unused profile fallback first.
    legacy_profile = {"scope_summary": "x" * (AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS + 1)}
    explicit_with_legacy_profile = build_heartbeat_prompt(
        goal_id="profile-scope-limit",
        thin=True,
        agent_id=agent_id,
        agent_scopes=["explicit bounded task"],
        registered_agents=[agent_id],
        agent_profile=legacy_profile,
    )
    assert explicit_with_legacy_profile["agent_scopes"] == ["explicit bounded task"]


def test_heartbeat_scope_over_profile_limit_is_rejected() -> None:
    over_limit = "x" * (AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS + 1)
    with pytest.raises(ValueError, match="at most 320 characters"):
        normalize_agent_scopes([over_limit])
    with pytest.raises(ValueError, match="at most 320 characters"):
        normalize_agent_profile(
            {"agent_id": "peer-a", "scope_summary": over_limit},
            registered_agents=["peer-a"],
        )


def test_agent_prompt_command_args_quotes_scopes() -> None:
    args = agent_prompt_command_args(
        agent_id="codex-main-control",
        agent_scopes=["peer task claims"],
    )
    assert "--agent-id codex-main-control" in args
    assert "--agent-scope 'peer task claims'" in args


def test_host_detection_prefers_runtime_profile() -> None:
    assert (
        uses_native_goal_host_loop(
            runtime_profile="codex_cli",
            scheduler_execution_context=None,
        )
        is True
    )
    assert (
        uses_native_goal_host_loop(
            runtime_profile="generic_cli",
            scheduler_execution_context=None,
        )
        is False
    )
    assert (
        uses_ark_managed_agent_goal_host(
            runtime_profile="ark_managed_agent_goal",
            scheduler_execution_context=None,
        )
        is True
    )


def test_visible_goal_capability_projection_filters_control_vocabulary() -> None:
    projection = build_visible_goal_initial_runtime_capability_projection(
        ["loop", "network", "external_evidence_poll"]
    )
    assert projection is not None
    assert "loop" not in projection["capabilities"]
    assert projection["capabilities"] == ["network", "external_evidence_poll"]
    assert projection["scope"] == "visible_goal_session"


def test_visible_goal_policy_rejects_heartbeat_only_vocabulary() -> None:
    with pytest.raises(ValueError, match="heartbeat-only"):
        validate_visible_goal_policy_rule(
            field="permission_rule",
            value="use rrule now",
        )


def test_public_facade_still_builds_and_renders_prompts() -> None:
    payload = build_heartbeat_prompt(goal_id="loopx-meta")
    assert payload["ok"] is True
    assert payload["goal_id"] == "loopx-meta"
    assert payload["task_body"]
    assert render_heartbeat_prompt_markdown(payload)


@pytest.mark.parametrize(
    ("mode", "base_budget"),
    [("full", 12000), ("compact", 6500), ("brief", 3500), ("thin", 2500)],
)
def test_reward_memory_prompt_headroom_is_fixed_and_feature_scoped(
    mode: str, base_budget: int
) -> None:
    enabled = build_heartbeat_prompt(
        goal_id="reward-memory-budget-fixture",
        runtime_profile="codex_app_heartbeat",
        **{mode: True},
        reward_memory_enabled=True,
    )
    disabled = build_heartbeat_prompt(
        goal_id="reward-memory-budget-fixture",
        runtime_profile="codex_app_heartbeat",
        **{mode: True},
        reward_memory_enabled=False,
    )
    assert "--reward-memory-reflection-json" in enabled["task_body"]
    assert enabled["interface_budget"]["reward_memory_headroom_chars"] == (
        REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS
    )
    assert enabled["interface_budget"]["max_chars"] == (
        base_budget + REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS
    )
    assert "--reward-memory-reflection-json" not in disabled["task_body"]
    assert disabled["interface_budget"]["reward_memory_headroom_chars"] == 0
    assert disabled["interface_budget"]["max_chars"] == base_budget


@pytest.mark.parametrize("mode", ["full", "compact", "brief", "thin"])
def test_sizing_guidance_survives_prompt_compaction(mode: str) -> None:
    payload = build_heartbeat_prompt(goal_id="sizing-fixture", **{mode: True})
    body = payload["task_body"]
    # Compaction must not discard how to size work while keeping only "one step".
    assert body.count(SCOPE_BOUNDED_WORK_RULE) == 1
    assert "bounded slice" not in body
    assert payload["interface_budget"]["within_budget"] is True
    # This is guidance, not a new execution profile, scheduler or authority field.
    assert "turn_mode" not in payload
    assert "--fine-grained" not in payload["quota_guard_command"]


@pytest.mark.parametrize("mode", ["full", "compact", "brief", "thin"])
def test_reward_memory_outcome_gate_survives_app_prompt_compaction(mode: str) -> None:
    payload = build_heartbeat_prompt(
        goal_id="reward-memory-app-fixture",
        agent_id="agent-a",
        registered_agents=["agent-a"],
        runtime_profile="codex_app_heartbeat",
        **{mode: True},
    )
    body = payload["task_body"]
    assert "--reward-memory-reflection-json" in body
    assert "Todo validator" in body
    assert "exact" in body and "digest" in body and "evidence" in body
    assert "zero provider calls" in body
    assert "raw" in body and "private" in body
    if mode != "full":
        assert "Auto-ingest Todo" in body
        assert "refresh/spend readback" in body
        assert "no raw/private content" in body
    if mode != "full":
        assert payload["interface_budget"]["within_budget"] is True


@pytest.mark.parametrize("profile", ["codex_cli", "ark_managed_agent_goal"])
def test_goal_hosts_preserve_sizing_and_terminal_boundary(profile: str) -> None:
    payload = build_heartbeat_prompt(goal_id="sizing-fixture", runtime_profile=profile)
    body = payload["task_body"]
    assert body.count(SCOPE_BOUNDED_WORK_RULE) == 1
    assert "no work/spend" in body
    assert "terminal no-follow-up" in body
    assert "settlement_plan.ordered_steps" in body
    assert payload["interface_budget"]["within_budget"] is True


@pytest.mark.parametrize("mode", ["full", "compact", "brief", "thin"])
def test_exact_turn_identity_is_preserved_across_heartbeat_modes(mode: str) -> None:
    turn_id = "dsh-turn:2026-08-21T12:34:56Z"
    payload = build_heartbeat_prompt(
        goal_id="loopx-meta",
        agent_id="codex-main-control",
        runtime_profile="generic_cli",
        turn_instance_id=turn_id,
        **{mode: True},
    )

    assert payload["turn_instance_id"] == turn_id
    assert payload["schema_version"] == "loopx_heartbeat_prompt_v0"
    assert f"LOOPX_TURN={turn_id}" in payload["task_body"]
    assert "LOOPX_TURN=<current_time_iso>" not in payload["task_body"]
    assert '--turn-instance-id "${LOOPX_TURN:?}"' in payload["quota_guard_command"]
    assert "settlement_plan.ordered_steps" in payload["task_body"]
    for command_name in (
        "expanded_prompt_command",
        "compact_prompt_command",
        "brief_prompt_command",
        "thin_prompt_command",
    ):
        command = payload.get(command_name)
        if command is not None:
            assert f"--turn-instance-id {turn_id}" in command


def test_default_heartbeat_identity_behavior_remains_time_generated() -> None:
    payload = build_heartbeat_prompt(goal_id="loopx-meta")

    assert "turn_instance_id" not in payload
    assert "schema_version" not in payload
    assert "LOOPX_TURN=<current_time_iso>" in payload["task_body"]
    assert "--turn-instance-id" not in payload["thin_prompt_command"]


@pytest.mark.parametrize("turn_id", ["", "bad id", "x" * 129])
def test_exact_heartbeat_rejects_invalid_turn_identity(turn_id: str) -> None:
    with pytest.raises(ValueError, match="turn_instance_id"):
        build_heartbeat_prompt(
            goal_id="loopx-meta",
            agent_id="codex-main-control",
            runtime_profile="generic_cli",
            turn_instance_id=turn_id,
        )


def test_exact_heartbeat_requires_agent_and_receipt_producing_profile() -> None:
    with pytest.raises(ValueError, match="exact --agent-id"):
        build_heartbeat_prompt(
            goal_id="loopx-meta",
            runtime_profile="generic_cli",
            turn_instance_id="turn-exact",
        )
    with pytest.raises(ValueError, match="requires runtime-profile generic_cli"):
        build_heartbeat_prompt(
            goal_id="loopx-meta",
            agent_id="codex-main-control",
            runtime_profile="codex_cli",
            turn_instance_id="turn-exact",
        )
