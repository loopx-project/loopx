from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.testing.model_behavior_corpus import (
    build_model_behavior_corpus,
)
from loopx.control_plane.testing.model_behavior_retained_cases import (
    MAX_RETAINED_CASE_BYTES,
    build_model_behavior_retained_case,
    load_model_behavior_retained_cases,
    retained_cases_as_corpus_inputs,
    write_model_behavior_retained_case,
)


def _full_packet() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "should-run",
        "goal_id": "fixture-goal",
        "decision": "run",
        "should_run": True,
        "effective_action": "normal_run",
        "state": "eligible",
        "action_required": False,
        "open_count": 0,
        "recommended_action": "Implement one bounded public-safe slice.",
        "selected_todo": {
            "todo_id": "todo_fixture001",
            "status": "open",
            "task_class": "advancement_task",
            "text": "Implement one bounded public-safe slice.",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "primary_action": "Implement one bounded public-safe slice.",
            },
            "cli_channel": {
                "next_cli_actions": ["loopx refresh-state --goal-id fixture-goal"],
                "spend_allowed_now": False,
                "spend_after_validation": True,
            },
        },
        "goal_boundary": {
            "write_scope": ["loopx/**", "tests/**"],
            "guards": ["stop before external writes"],
        },
    }


def _case() -> dict[str, Any]:
    return build_model_behavior_retained_case(
        _full_packet(),
        case_id="real-shadow-001",
        source_kind="real_quota_shadow",
        recorded_at="2026-07-13T04:00:00+08:00",
    )


def test_retained_case_round_trip_is_bounded_private_and_corpus_ready(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    first = write_model_behavior_retained_case(
        _case(),
        runtime_root=runtime_root,
        goal_id="fixture-goal",
    )
    second = write_model_behavior_retained_case(
        _case(),
        runtime_root=runtime_root,
        goal_id="fixture-goal",
    )

    assert first["created"] is True
    assert second["created"] is False
    stored = runtime_root / "goals/fixture-goal/model-behavior/retained-cases"
    path = stored / "real-shadow-001.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_model_behavior_retained_cases(
        runtime_root=runtime_root,
        goal_id="fixture-goal",
    )
    assert loaded == [_case()]
    corpus = build_model_behavior_corpus(
        _full_packet(),
        retained_packets=retained_cases_as_corpus_inputs(loaded),
    )
    assert corpus["cases"][0]["source_kind"] == "retained_public_decision"
    assert "full_packet" not in first
    assert "path" not in first


def test_complete_source_round_trip_preserves_its_original_binding(tmp_path: Path) -> None:
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision

    packet = compose_turn_decision({**_full_packet(),
        "supporting_evidence": {"count": 9007199254740993, "ratio": 1.0}})
    case = build_model_behavior_retained_case(packet, case_id="complete-shadow-001",
        source_kind="owner_approved_public_fixture", recorded_at="2026-07-13T04:00:00+08:00")
    runtime = tmp_path / "runtime"
    receipt = write_model_behavior_retained_case(case, runtime_root=runtime, goal_id="fixture-goal")
    loaded = load_model_behavior_retained_cases(runtime_root=runtime, goal_id="fixture-goal")
    corpus = build_model_behavior_corpus(packet,
        retained_packets=retained_cases_as_corpus_inputs(loaded))
    assert loaded == [case]
    for arm in ("full_packet", "candidate_packet"):
        restored = corpus["cases"][0][arm]
        assert json.dumps(restored, sort_keys=True) == json.dumps(packet, sort_keys=True)
        assert restored["contract_binding"] == packet["contract_binding"]
        assert "action_signature" not in restored
    assert "packet" not in receipt and "path" not in receipt


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"api_key": "fixture-value"}, "credential-shaped field"),
        (
            {"detail": "".join(("/", "Users", "/example/private.json"))},
            "local absolute path",
        ),
    ],
)
def test_retained_case_rejects_private_or_credential_material(
    patch: dict[str, Any], message: str
) -> None:
    packet = _full_packet()
    packet.update(patch)
    with pytest.raises(ValueError, match=message):
        build_model_behavior_retained_case(
            packet,
            case_id="unsafe-shadow-001",
            source_kind="real_quota_shadow",
            recorded_at="2026-07-13T04:00:00+08:00",
        )


def test_retained_case_rejects_oversize_and_path_traversal() -> None:
    packet = _full_packet()
    packet["large_diagnostic"] = "x" * MAX_RETAINED_CASE_BYTES
    with pytest.raises(ValueError, match="size limit"):
        build_model_behavior_retained_case(
            packet,
            case_id="oversize-shadow-001",
            source_kind="real_quota_shadow",
            recorded_at="2026-07-13T04:00:00+08:00",
        )
    with pytest.raises(ValueError, match="case_id"):
        build_model_behavior_retained_case(
            _full_packet(),
            case_id="../escape",
            source_kind="real_quota_shadow",
            recorded_at="2026-07-13T04:00:00+08:00",
        )


def test_retained_case_rejects_collision_and_tampering(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    case = _case()
    write_model_behavior_retained_case(
        case,
        runtime_root=runtime_root,
        goal_id="fixture-goal",
    )
    changed = _case()
    changed["recorded_at"] = "2026-07-13T04:01:00+08:00"
    with pytest.raises(ValueError, match="different content"):
        write_model_behavior_retained_case(
            changed,
            runtime_root=runtime_root,
            goal_id="fixture-goal",
        )

    path = (
        runtime_root
        / "goals/fixture-goal/model-behavior/retained-cases/real-shadow-001.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["packet_digest"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="digest does not match"):
        load_model_behavior_retained_cases(
            runtime_root=runtime_root,
            goal_id="fixture-goal",
        )


def test_retained_case_store_must_be_outside_git_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="outside a git worktree"):
        write_model_behavior_retained_case(
            _case(),
            runtime_root=repository / ".local/runtime",
            goal_id="fixture-goal",
        )


def test_complete_source_round_trip_preserves_its_original_binding(tmp_path: Path) -> None:
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision

    packet = compose_turn_decision({**_full_packet(),
        "supporting_evidence": {"count": 9007199254740993, "ratio": 1.0}})
    case = build_model_behavior_retained_case(packet, case_id="complete-shadow-001",
        source_kind="owner_approved_public_fixture", recorded_at="2026-07-13T04:00:00+08:00")
    runtime = tmp_path / "runtime"
    receipt = write_model_behavior_retained_case(case, runtime_root=runtime, goal_id="fixture-goal")
    loaded = load_model_behavior_retained_cases(runtime_root=runtime, goal_id="fixture-goal")
    corpus = build_model_behavior_corpus(packet,
        retained_packets=retained_cases_as_corpus_inputs(loaded))
    assert loaded == [case]
    for arm in ("full_packet", "candidate_packet"):
        restored = corpus["cases"][0][arm]
        assert json.dumps(restored, sort_keys=True) == json.dumps(packet, sort_keys=True)
        assert restored["contract_binding"] == packet["contract_binding"]
        assert "action_signature" not in restored
    assert "packet" not in receipt and "path" not in receipt
