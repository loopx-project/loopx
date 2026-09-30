"""Size diagnostics must not acquire authority over valid Turn execution."""

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from loopx.cli_commands.turn_rendering import render_loopx_turn_plan_markdown
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope,
    quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from loopx.control_plane.turn_driver import build_loopx_turn_plan
from loopx.presentation.renderers.turn_envelope_markdown import (
    render_turn_envelope_markdown,
)
from loopx.workflow_skill_install import workflow_skill_install
from tests.control_plane.test_agent_context import POLICY, SCOPE, context
from tests.test_loopx_turn_driver import _write_live_fixture
from tests.test_turn_envelope import _full_decision


def _wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("text", ["x", "界", "🚀"])
def test_oversized_envelope_measures_all_sections_without_losing_authority(
    enabled, text
):
    source = _full_decision()
    source["goal_boundary"]["write_scope"] = [
        f"public/{i}/" + text * (80 if text == "🚀" else 170) for i in range(16)
    ]
    source["goal_boundary"]["guards"] = [f"guard-{i}-" + "x" * 200 for i in range(8)]
    if enabled:
        source["interaction_contract"]["agent_context"] = context()
    envelope = build_turn_envelope(source)
    metric = envelope["compaction"]
    assert metric["within_budget"] is False
    assert metric["envelope_utf8_bytes"] == len(_wire(envelope))
    assert metric["envelope_json_bytes"] == len(_wire(envelope).decode("utf-8"))
    warning = metric["warning"]
    assert warning["severity"] == "warning"
    assert warning["excess_bytes"] == len(_wire(envelope)) - 8192
    assert sum(warning["section_bytes"].values()) == len(_wire(envelope))
    assert "boundary" in warning["over_target_sections"]
    assert quota_action_signature_document(
        source
    ) == turn_envelope_action_signature_document(envelope)
    plan = build_loopx_turn_plan(
        envelope, host="codex-cli", execution_mode="interactive-visible"
    )
    assert plan["ok"] is True
    assert plan["route"]["would_invoke_host"] is True
    assert "WARNING" in render_loopx_turn_plan_markdown(plan)
    assert "section_bytes" in render_turn_envelope_markdown(envelope)
    invalid = deepcopy(envelope)
    invalid["action_signature"]["matches"] = False
    assert (
        build_loopx_turn_plan(
            invalid, host="codex-cli", execution_mode="interactive-visible"
        )["ok"]
        is False
    )


def test_normal_envelope_retires_redundant_cold_path_inventory():
    envelope = build_turn_envelope(_full_decision())
    assert "contains" not in envelope["detail_ref"]
    assert {"full_decision", "todo_detail", "status_detail"} == set(
        envelope["detail_ref"]
    )
    assert "warning" not in envelope["compaction"]
    assert envelope["compaction"]["envelope_utf8_bytes"] == len(_wire(envelope))


def test_context_high_water_keeps_legal_scope_and_recovers_headroom():
    source = _full_decision()
    scopes = [f"public/{i:02}/" + "x" * 160 for i in range(16)]
    source["goal_boundary"]["write_scope"] = scopes
    baseline = build_turn_envelope(source)
    assert baseline["compaction"]["within_budget"] is True
    source["interaction_contract"]["agent_context"] = context()
    envelope = build_turn_envelope(source)
    assert envelope["agent_context"]["detail_ref"]
    assert envelope["boundary"]["write_scope"] == scopes
    assert len(_wire(envelope)) <= 8192


@pytest.mark.parametrize("enabled", [False, True])
def test_real_turn_plan_cli_warns_without_state_writes(tmp_path, enabled):
    project, runtime, registry = _write_live_fixture(tmp_path)
    config = json.loads(registry.read_text())
    goal = config["goals"][0]
    goal["coordination"]["write_scope"] = [
        f"public/{i}/" + "界" * 160 for i in range(16)
    ]
    goal["spawn_policy"] = {**POLICY, "spawn_allowed": enabled}
    registry.write_text(json.dumps(config))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "plan",
        "--goal-id",
        "loopx-turn-fixture",
        "--agent-id",
        "codex-fixture",
        "--scan-root",
        str(project),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["ok"] is True
    assert plan["route"]["would_invoke_host"] is True
    envelope = plan["turn_envelope"]
    assert bool(envelope.get("agent_context")) is enabled
    assert envelope["compaction"]["warning"]["code"] == "turn_envelope_budget_exceeded"
    assert envelope["compaction"]["envelope_utf8_bytes"] == len(_wire(envelope))
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_installed_skill_defers_delegation_policy_to_enabled_provider(tmp_path):
    result = workflow_skill_install(skills_dir=tmp_path / "skills", execute=True)
    assert result["ok"] is True
    installed = (tmp_path / "skills/loopx-project/SKILL.md").read_text()
    # This artifact is executable guidance: characterize its activation boundary,
    # not a dated heading or incidental editorial wording.
    assert "If context is absent, disabled, or the read fails" in installed
    assert "do not seek delegation splits or invoke child tools" in installed
    assert (
        "Consider useful\nread-heavy delegation within a single Todo" not in installed
    )
    from loopx.control_plane.agent_context import project_agent_context

    assert (
        project_agent_context(
            phase="before_plan",
            scope=SCOPE,
            orchestration={**POLICY, "spawn_allowed": False},
        )
        is None
    )
    enabled = project_agent_context(
        phase="before_plan", scope=SCOPE, orchestration=POLICY
    )
    contribution = enabled["contributions"][0]
    assert any(
        "max_children is a configured ceiling" in item
        for item in contribution["guidance"]
    )
    assert contribution["facts"]["capacity_contract"] == {
        "schema_version": "multi_subagent_capacity_v0",
        "configured_limit_kind": "upper_bound",
        "live_availability": "not_observed",
    }


def test_interaction_and_native_envelope_preserve_all_required_commands():
    from loopx.control_plane.work_items.interaction_contract import build_interaction_contract

    source = _full_decision()
    reads = [{"kind": "agent_preferences" if i == 5 else f"fixture_{i}",
              "source": "turn_start_capability_hook", "reason": "Read before work",
              "command": "loopx --registry '/" + "workspace  dir/" * 45 + f"registry.json' inspect --item {i}"}
             for i in range(6)]
    source["required_reads"] = reads
    source["interaction_contract"] = build_interaction_contract(source)
    envelope = build_turn_envelope(source)
    assert [x["command"] for x in source["interaction_contract"]["agent_channel"]["required_reads"]] == [x["command"] for x in reads]
    assert [x["command"] for x in envelope["required_reads"]] == [x["command"] for x in reads]
    assert envelope["action_signature"]["matches"] is True
