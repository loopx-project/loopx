"""Contract, redaction, malformed-input, and origin/main parity tests for goal_channel_projection."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import types

import pytest

from loopx.control_plane.goals import (
    goal_channel_projection as gcp,
    goal_channel_projection_contracts as gcp_contracts,
)


def _load_baseline_goal_channel_projection() -> types.ModuleType | None:
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        ["git", "show", "origin/main:loopx/control_plane/goals/goal_channel_projection.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    mod = types.ModuleType("_baseline_goal_channel_projection")
    mod.__package__ = "loopx.control_plane.goals"
    mod.__file__ = str(repo_root / "loopx/control_plane/goals/goal_channel_projection.py")
    exec(compile(proc.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod


def test_goal_channel_projection_modules_zero_any_and_size_bounds() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        (root / "loopx/control_plane/goals/goal_channel_projection.py", 550),
        (root / "loopx/control_plane/goals/goal_channel_projection_contracts.py", 550),
        (Path(__file__).resolve(), 550),
    ]
    for path, max_loc in paths:
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= max_loc, f"{path} exceeded {max_loc} LOC"
        assert re.search(r"#\s*type:\s*ignore\b", source) is None, f"{path} contains type ignore"
        tree = ast.parse(source)
        any_refs = sum(
            1
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
            or (isinstance(node, ast.alias) and node.name == "Any")
        )
        assert any_refs == 0, f"{path} has {any_refs} Any references"


def test_goal_channel_projection_preserves_exports() -> None:
    expected = (
        "GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION",
        "RAW_MATERIAL_KEY_HINTS",
        "PRIVATE_VALUE_HINTS",
        "_as_mapping",
        "_as_mappings",
        "_private_value",
        "compact_goal_channel_text",
        "_text",
        "_raw_material_keys",
        "_first_text",
        "_project_asset",
        "_source_refs",
        "_compact_quota",
        "_todo_candidates",
        "_compact_todo",
        "_compact_todos",
        "_user_gate_todos",
        "_open_gates",
        "_compact_coordination_entry",
        "_compact_artifacts",
        "_recent_events",
        "_source_warnings",
        "build_goal_channel_projection",
    )
    for name in expected:
        assert hasattr(gcp, name), f"missing export {name}"
    assert gcp.compact_goal_channel_text is gcp_contracts.compact_goal_channel_text


def test_redaction_and_truncation_boundaries() -> None:
    sample_user_path = "/" + "Users" + "/example/secret"
    private_host = ".".join(("byte" + "dance", "lark" + "office"))
    assert gcp.compact_goal_channel_text(None) is None
    assert gcp.compact_goal_channel_text("   \n\t  ") is None
    assert gcp.compact_goal_channel_text(sample_user_path) is None
    assert gcp.compact_goal_channel_text("/private/var/log") is None
    assert gcp.compact_goal_channel_text(f"https://a.{private_host}.com/doc") is None
    assert gcp.compact_goal_channel_text("secret-value-123") is None
    assert gcp.compact_goal_channel_text("hello   world", limit=11) == "hello world"
    assert gcp.compact_goal_channel_text("hello world!", limit=11) == "hello worl..."
    assert gcp.compact_goal_channel_text("hello", limit=0) == "..."

    raw = gcp._raw_material_keys(
        {"Credential_Blob": "x", "nested": {"safe": f"path {sample_user_path}"}},
        [{"trace_id": "t1", "ok": 1}],
    )
    assert raw == ["Credential_Blob", "nested", "safe", "trace_id"]


def test_key_presence_bool_vs_int_and_legacy_containers() -> None:
    sample_user_path = "/" + "Users" + "/private"
    entry = gcp._compact_coordination_entry(
        {
            "todo_id": "t1",
            "lease_version": 2,
            "lease_epoch": True,
            "write_scope": ["a.py", None, sample_user_path, "b.py"],
        }
    )
    assert entry == {
        "todo_id": "t1",
        "lease_version": 2,
        "write_scope": ["a.py", "b.py"],
    }

    todo = gcp._compact_todo({"title": "Check", "goal_bound": False, "priority": ""})
    assert todo == {"title": "Check", "status": "open", "goal_bound": "False"}

    # Empty first_open_items falls through to first non-empty candidate list.
    candidates = gcp._todo_candidates(
        {"first_open_items": [], "open_items": [{"title": "Next step"}], "items": [{"title": "Ignored"}]}
    )
    assert candidates == [{"title": "Next step"}]

    # Non-True action_required keeps decision_frame truthy while open_gates requires is True.
    gates = gcp._open_gates(
        quota_payload={"interaction_contract": {"user_channel": {"action_required": "yes"}}},
        project_asset={},
        user_todos=[{"todo_id": "u1", "title": "Pending"}],
    )
    assert gates == []


def test_malformed_inputs_and_unavailable_coordination_fail_closed() -> None:
    blocked_user_path = "/" + "Users" + "/p"
    projection = gcp.build_goal_channel_projection(
        goal_id="goal-malformed",
        status_item={
            "status": "blocked",
            "project_asset": {
                "quota": {"state": "held", "spent_slots": 2},
                "user_todos": {
                    "items": [
                        {"title": "   ", "status": "open"},
                        {"title": "Approve PR", "task_class": "user_gate", "status": "open", "todo_id": "g-1"},
                    ]
                },
                "open_gates": [
                    "not-a-mapping",
                    {"gate_id": "done-gate", "status": "closed"},
                    {"gate_id": "live-gate", "status": "open", "blocks": ["x", None, blocked_user_path]},
                ],
            },
        },
        quota_payload={"interaction_contract": "invalid"},
        run_history_goal={"latest_runs": ["bad", {"generated_at": "2026-11-26T00:00:00Z", "recommended_action": "Review"}]},
        artifacts=[{"kind": "", "path": "/private/secret"}],
        generated_at=None,
        runtime_root=12345,
    )
    assert "generated_at" not in projection
    assert projection["artifacts"] == []
    assert projection["open_gates"] == [{"gate_id": "live-gate", "kind": "operator_gate", "status": "open", "blocks": ["x"]}]
    assert projection["recent_events"] == [{"generated_at": "2026-11-26T00:00:00Z", "summary": "Review"}]
    raw_warnings = projection["source_warnings"]
    assert isinstance(raw_warnings, list)
    warning_kinds = [w["kind"] for w in raw_warnings if isinstance(w, dict)]
    assert "raw_or_private_material_omitted" in warning_kinds
    assert "coordination_unavailable" in warning_kinds
    assert projection["active_leases"] == []


def test_paired_origin_main_behavioral_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = _load_baseline_goal_channel_projection()
    if baseline is None:
        pytest.skip("git origin/main unavailable in this checkout")

    from loopx.control_plane.goals import coordination_observation

    for obs_status, truncated, extra_auth in [
        ("unavailable", False, False),
        ("loaded", True, True),
    ]:
        def _fake_observe(*, runtime_root: object, goal_id: str) -> dict[str, object]:
            obs: dict[str, object] = {
                "status": obs_status,
                "entries": [
                    {
                        "todo_id": "t-1",
                        "owner_agent": "lark-conv-slim",
                        "claimed_by": "lark-conv-slim",
                        "lease_version": 3,
                        "lease_epoch": False,
                        "status": "active",
                        "write_scope": ["loopx/control_plane/goals/goal_channel_projection.py"],
                    }
                ],
                "total_count": 105 if truncated else 1,
                "truncated": truncated,
                "display_limit": 100,
                "todo_count": 1,
                "lease_count": 1,
            }
            if extra_auth:
                obs.update(
                    source_authority="canonical_local_authority",
                    provider_revision="rev-1",
                    observed_at="2026-11-26T00:00:00+00:00",
                    legacy_fallback_used=False,
                )
            return obs

        monkeypatch.setattr(coordination_observation, "observe_goal_coordination", _fake_observe)
        monkeypatch.setattr(
            coordination_observation,
            "coordination_authority_transition",
            lambda o: {"state": o.get("source_authority") or "unavailable", "can_promote": o.get("status") == "loaded"},
        )

        status_item: dict[str, object] = {
            "status": "running",
            "display_name": "Parity Goal",
            "waiting_on": "none",
            "project_asset": {
                "state_updated_at": "2026-11-26T00:01:00+00:00",
                "user_todos": {"first_open_items": [{"title": "Approve release", "task_class": "user_gate", "todo_id": "u1"}]},
                "agent_todos": {"items": [{"title": "Ship slice", "todo_id": "a1", "priority": "P1"}]},
            },
        }
        status_payload = {"generated_at": "2026-11-26T00:02:00+00:00"}
        quota_payload: dict[str, object] = {
            "recommended_action": "Verify checks",
            "quota": {"state": "allow", "spent_slots": 1, "allowed_slots": 4},
            "interaction_contract": {
                "user_channel": {"action_required": True},
                "agent_channel": {"must_attempt": False, "quiet_noop_allowed": True},
            },
        }
        run_history_goal: dict[str, object] = {
            "display_name": "Renamed Parity Goal",
            "latest_runs": [{"generated_at": "2026-11-26T00:00:30+00:00", "classification": "ok", "health_check": "green"}],
        }
        review_packet = {"generated_at": "2026-11-26T00:03:00+00:00", "token": "redact-me"}
        artifacts = [{"kind": "report", "label": "Summary", "path": "docs/summary.md", "run_id": "run-1"}]
        active_leases = [{"todo_id": "t-1", "raw_blob": "omit-key"}]
        generated_at = "2026-11-26T00:04:00+00:00"
        head_out = gcp.build_goal_channel_projection(
            goal_id="goal-parity",
            status_item=status_item,
            status_payload=status_payload,
            quota_payload=quota_payload,
            run_history_goal=run_history_goal,
            review_packet=review_packet,
            artifacts=artifacts,
            active_leases=active_leases,
            generated_at=generated_at,
            runtime_root=tmp_path,
        )
        base_out = baseline.build_goal_channel_projection(
            goal_id="goal-parity",
            status_item=status_item,
            status_payload=status_payload,
            quota_payload=quota_payload,
            run_history_goal=run_history_goal,
            review_packet=review_packet,
            artifacts=artifacts,
            active_leases=active_leases,
            generated_at=generated_at,
            runtime_root=tmp_path,
        )
        assert head_out == base_out
