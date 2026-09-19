#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
repo_root_text = str(REPO_ROOT)
if sys.path[0] != repo_root_text:
    sys.path.insert(0, repo_root_text)

# The candidate checkout must win over any installed LoopX package.
from loopx.control_plane.testing.actual_default_model_behavior_portfolio import (  # noqa: E402
    build_actual_default_model_behavior_scenario_inputs,
    run_actual_default_model_behavior_portfolio,
)
from loopx.control_plane.testing.capability_monitor_repair_tool_behavior import (  # noqa: E402
    DoubaoCapabilityMonitorRepairToolBehaviorActor,
)
from loopx.control_plane.testing.doubao_model_behavior_actor import (  # noqa: E402
    DoubaoModelBehaviorActor,
    DoubaoOnboardingModelBehaviorActor,
    _direct_ark_transport,
)
from loopx.control_plane.testing.release_commit_qualification import (  # noqa: E402
    collect_release_source_identity,
)
from loopx.control_plane.testing.replan_semantic_action_behavior import (  # noqa: E402
    DoubaoReplanSemanticActionBehaviorActor,
)
from loopx.control_plane.testing.scoped_gate_successor_tool_behavior import (  # noqa: E402
    DoubaoScopedGateSuccessorToolBehaviorActor,
)
from loopx.control_plane.testing.selected_todo_tool_behavior import (  # noqa: E402
    DoubaoSelectedTodoToolBehaviorActor,
)
from loopx.control_plane.testing.terminal_settlement_tool_behavior import (  # noqa: E402
    DoubaoTerminalSettlementToolBehaviorActor,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the actual-default behavior portfolio against the real Ark "
            "Doubao API and print only its bounded receipt."
        )
    )
    parser.add_argument("--qualification-id", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = _parser().parse_args()
    source = collect_release_source_identity(args.repo_root)
    if source["git_dirty"]:
        raise RuntimeError(
            "live Doubao qualification requires a clean candidate checkout"
        )
    provider_call_count = 0
    provider_models: set[str] = set()

    def counted_transport(**kwargs):
        nonlocal provider_call_count
        provider_call_count += 1
        provider_models.add(json.loads(kwargs["body"])["model"])
        return _direct_ark_transport(**kwargs)

    turn_actor = DoubaoModelBehaviorActor.from_environment(
        timeout_seconds=args.timeout_seconds, transport=counted_transport
    )
    onboarding_actor = DoubaoOnboardingModelBehaviorActor.from_environment(
        timeout_seconds=args.timeout_seconds, transport=counted_transport
    )
    selected_todo_actor = DoubaoSelectedTodoToolBehaviorActor.from_environment(
        timeout_seconds=args.timeout_seconds, transport=counted_transport
    )
    replan_semantic_action_actor = DoubaoReplanSemanticActionBehaviorActor.from_environment(
        timeout_seconds=args.timeout_seconds, transport=counted_transport
    )
    scoped_gate_successor_actor = (
        DoubaoScopedGateSuccessorToolBehaviorActor.from_environment(
            timeout_seconds=args.timeout_seconds, transport=counted_transport
        )
    )
    capability_monitor_repair_actor = (
        DoubaoCapabilityMonitorRepairToolBehaviorActor.from_environment(
            timeout_seconds=args.timeout_seconds, transport=counted_transport
        )
    )
    terminal_settlement_actor = (
        DoubaoTerminalSettlementToolBehaviorActor.from_environment(
            timeout_seconds=args.timeout_seconds, transport=counted_transport
        )
    )
    with TemporaryDirectory(prefix="loopx-doubao-live-") as temp_dir:
        temp_root = Path(temp_dir)

        def qualify_selected_todo(run_id: str) -> dict[str, object]:
            run_digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
            return selected_todo_actor.qualify(
                qualification_id=run_id,
                fixture_root=temp_root / "selected-todo" / run_digest,
            )

        def qualify_replan_semantic_action(run_id: str) -> dict[str, object]:
            run_digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
            return replan_semantic_action_actor.qualify(
                qualification_id=run_id,
                fixture_root=temp_root / "replan-semantic-action" / run_digest,
                required_vision=True,
            )

        def qualify_scoped_gate_successor(run_id: str) -> dict[str, object]:
            run_digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
            return scoped_gate_successor_actor.qualify(
                qualification_id=run_id,
                fixture_root=temp_root / "scoped-gate-successor" / run_digest,
            )

        def qualify_capability_monitor_repair(run_id: str) -> dict[str, object]:
            run_digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
            return capability_monitor_repair_actor.qualify(
                qualification_id=run_id,
                fixture_root=temp_root / "capability-monitor-repair" / run_digest,
            )

        def qualify_terminal_settlement(run_id: str) -> dict[str, object]:
            run_digest = sha256(run_id.encode("utf-8")).hexdigest()[:16]
            return terminal_settlement_actor.qualify(
                qualification_id=run_id,
                fixture_root=temp_root / "terminal-settlement" / run_digest,
            )

        sources, packets = build_actual_default_model_behavior_scenario_inputs(
            temp_root / "portfolio"
        )
        result = run_actual_default_model_behavior_portfolio(
            packets,
            scenario_sources=sources,
            qualification_id=args.qualification_id,
            turn_actor=turn_actor,
            onboarding_actor=onboarding_actor,
            selected_todo_actor=qualify_selected_todo,
            replan_semantic_action_actor=qualify_replan_semantic_action,
            scoped_gate_successor_actor=qualify_scoped_gate_successor,
            capability_monitor_repair_actor=qualify_capability_monitor_repair,
            terminal_settlement_actor=qualify_terminal_settlement,
        )
    result["source"] = source
    # An actor invocation can make several HTTP calls while using tools.
    # Persist aggregate provenance, never headers or request/response bodies.
    result["provider_call_count"] = provider_call_count
    result["provider_models"] = sorted(provider_models)
    result["model_id"] = next(iter(provider_models)) if len(provider_models) == 1 else None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["qualification_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
