"""A reflection instruction must not displace previously bounded private recall."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shlex

import pytest

from loopx.capabilities.agent_turn_recall.runtime import run_configured_agent_turn_recall_fail_open
from loopx.capabilities.context_providers.base import ContextProviderItem
from loopx.capabilities.reward_memory.experiment import (
    resolve_reward_memory_experiment, resolve_reward_memory_surface_config,
)
from loopx.control_plane.capability_hooks import project_turn_context
from tests.capabilities.test_agent_turn_recall import RecallProvider, active_record, raw_config
from tests.control_plane.reward_memory_host_fixture import enable_live_memory
from tests.control_plane.test_quota_memory_context import quota_fixture, run_cli
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID


class ProceduralRecallProvider(RecallProvider):
    def __init__(self, config: dict, field_size: int) -> None:
        super().__init__()
        self.records = []
        route = resolve_reward_memory_surface_config(config, "agent_workflow.turn_admission")
        self.scope_ref = route["provider_binding"]["scope_ref"]
        for index in range(8):
            record = json.loads(active_record(config, expires_at="2099-01-01T00:00:00+00:00"))
            record["candidate_ref"] = f"candidate:scoped-{index}"
            record["content_summary"] = f"Validate scoped operation {index} before continuing."
            record["experience"] = {
                "schema_version": "procedural_experience_contract_v0",
                "applicability": [f"Condition {item}: " + "a" * field_size for item in range(5)],
                "observed_outcome": "An independently checked operation completed.",
                "attribution": "Validation detected the prerequisite before execution.",
                "future_behavior": {
                    "trigger": "A similar scoped operation is proposed.",
                    "action": "Validate prerequisites before execution.",
                    "validation": "Read back the independently observed result.",
                    "stop_condition": "Stop when the declared prerequisite is unavailable.",
                },
                "limitations": [f"Boundary {item}: " + "b" * field_size for item in range(5)],
                "evidence_refs": [f"artifact:scoped-{index}"],
            }
            if index == 0:
                record["experience"]["observed_outcome"] += "界" * 129
            self.records.append(record)

    def retrieve(self, **kwargs):
        result = super().retrieve(**kwargs)
        return replace(result, items=tuple(
            ContextProviderItem(
                resource_ref=f"{self.scope_ref}/scoped-{index}.json",
                summary=record["content_summary"], content=json.dumps(record), score=0.97,
            ) for index, record in enumerate(self.records)
        ), read_performed=True)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("entry", ["quota", "thin-heartbeat"])
@pytest.mark.parametrize("field_size", [464, 480])
def test_app_guard_keeps_complete_bounded_procedural_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, entry: str, field_size: int,
) -> None:
    registry, route = quota_fixture(tmp_path, monkeypatch, provider)

    def procedural_config(**kwargs):
        config = raw_config(**kwargs)
        config["corpora"][0]["corpus"]["class_id"] = "procedural_experience"
        config["corpora"][0]["standing_policy"]["allowed_target_classes"] = ["procedural_experience"]
        config["surfaces"][0]["recall_profile"]["limit"] = 8
        return config

    # Only the disposable binding differs; activation and exact digest checks
    # remain the real owners. Provider IO is the sole production test double.
    monkeypatch.setattr("tests.capabilities.test_agent_turn_recall.raw_config", procedural_config)
    enable_live_memory(registry, recall=True, ingest=True)
    _, config = resolve_reward_memory_experiment(
        registry_path=registry, goal_id=GOAL_ID, agent_id=AGENT_ID,
    )
    memory = ProceduralRecallProvider(config, field_size)
    monkeypatch.setattr(
        "loopx.capabilities.agent_turn_recall.run_configured_agent_turn_recall_fail_open",
        lambda **kwargs: run_configured_agent_turn_recall_fail_open(**kwargs, provider=memory),
    )
    turn = "app-memory-budget"
    if entry == "thin-heartbeat":
        bootstrap = run_cli(route + ["heartbeat-prompt", "--thin", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--available-capability", "filesystem_read"])
        command = next(line for line in bootstrap["task_body"].splitlines()
            if line.startswith("loopx ") and "quota should-run" in line)
        args = shlex.split(command.replace('${LOOPX_TURN:?}', turn))[1:]
    else:
        args = route + ["quota", "should-run", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--codex-app", "--turn-instance-id", turn,
            "--available-capability", "filesystem_read"]
    guard = run_cli(args)
    assert guard["should_run"] and memory.calls == 1
    assert guard["reward_memory_recall"]["status"] == "applied"
    assert guard["reward_memory_recall"]["context"] is None
    contribution, = guard["turn_context_contributions"]
    projected = project_turn_context(guard)
    context = projected["reward_memory_recall"]["context"]
    instructions = " ".join(contribution["instructions"])
    assert ("compare independent task evidence" in instructions) is (field_size == 464)
    if field_size == 464:
        assert context is not None
        expected = [{key: deepcopy(record[key]) for key in
            ("candidate_ref", "target_class", "content_summary", "experience")}
            for record in memory.records]
        assert context["guidance"] == expected
    else:
        assert context is None
    assert contribution["result_fields"]["reward_memory_reflection_json"]["maxLength"] == 2400
    assert not guard["reward_memory_recall"]["external_writes_performed"]
    repeated = run_cli(args)
    assert repeated["ok"] and memory.calls == 1
