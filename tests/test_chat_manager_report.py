import json
from datetime import datetime, timezone

from loopx.chat.manager_history import read_manager_delivery_history
from loopx.capabilities.manager_context import evidence_goal_scope, POLICY_SCHEMA


def test_filter_delivery_window_before_cap_and_exclude_accounting(tmp_path):
    p = tmp_path / "goals" / "alpha" / "runs" / "index.jsonl"
    p.parent.mkdir(parents=True)
    rows = [
        {
            "generated_at": "2026-01-01T12:00:00+00:00",
            "goal_id": "alpha",
            "agent_id": "worker",
            "todo_id": "todo_ship",
            "classification": "released",
            "delivery_outcome": "outcome_progress",
        }
    ]
    rows += [
        {
            "generated_at": f"2026-01-02T01:{i:02}:00+00:00",
            "classification": "quota_slot_spent",
            "delivery_outcome": "surface_only",
        }
        for i in range(50)
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    x = read_manager_delivery_history(
        tmp_path, "alpha", now=datetime(2026, 1, 2, 2, tzinfo=timezone.utc), limit=1
    )
    assert x["coverage"]["matched"] == 1
    assert x["deliveries"][0]["todo_id"] == "todo_ship"
    assert x["deliveries"][0]["verification"] == "agent_reported_outcome"


def test_missing_history_and_invalid_time_are_not_zero_progress(tmp_path):
    assert read_manager_delivery_history(tmp_path, "alpha")["status"] == "unavailable"
    p = tmp_path / "goals" / "alpha" / "runs" / "index.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        json.dumps(
            {"generated_at": "yesterday", "delivery_outcome": "outcome_progress"}
        )
    )
    x = read_manager_delivery_history(tmp_path, "alpha")
    assert x["coverage"]["invalid_delivery_records"] == 1


def test_audience_read_grant_is_distinct_from_delegation_and_revocable(tmp_path):
    assert evidence_goal_scope(tmp_path, "manager.external.test") is None
    p = tmp_path / ".local" / "manager-context" / "policy.json"
    p.parent.mkdir(parents=True)
    source = {
        "sender_ids": ["owner"],
        "targets": [{"goal_id": "private", "agent_id": "worker"}],
    }

    def write():
        p.write_text(
            json.dumps(
                {
                    "schema_version": POLICY_SCHEMA,
                    "sources": {"manager.external.test": source},
                }
            )
        )

    write()
    assert evidence_goal_scope(tmp_path, "manager.external.test") is None
    source["evidence_goal_ids"] = ["beta", "alpha"]
    write()
    assert evidence_goal_scope(tmp_path, "manager.external.test") == ["alpha", "beta"]
    source["evidence_goal_ids"] = []
    write()
    assert evidence_goal_scope(tmp_path, "manager.external.test") == []
    source["evidence_goal_ids"] = ["*"]
    write()
    assert evidence_goal_scope(tmp_path, "manager.external.test") == []
    assert evidence_goal_scope(tmp_path, "manager.external.other") is None


def test_new_day_does_not_evict_previous_day(tmp_path):
    p = tmp_path / "goals" / "alpha" / "runs" / "index.jsonl"
    p.parent.mkdir(parents=True)
    rows = [
        {
            "generated_at": at,
            "delivery_outcome": "outcome_progress",
            "agent_id": "worker",
        }
        for at in [
            "2026-01-01T12:00:00+00:00",
            "2026-01-02T01:00:00+00:00",
            "2026-01-02T02:00:00+00:00",
        ]
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    x = read_manager_delivery_history(
        tmp_path, "alpha", now=datetime(2026, 1, 2, 3, tzinfo=timezone.utc), limit=1
    )
    assert x["coverage"]["included"] == 2
    assert x["coverage"]["omitted"] == 1
    assert any(r["recorded_at"].startswith("2026-01-01") for r in x["deliveries"])


def test_report_retains_concrete_findings_without_fetching_artifacts(tmp_path):
    p = tmp_path / "goals" / "alpha" / "runs" / "index.jsonl"
    p.parent.mkdir(parents=True)
    row = {
        "generated_at": "2026-01-01T12:00:00+00:00",
        "goal_id": "alpha", "agent_id": "worker", "todo_id": "todo_check",
        "classification": "comparison_validated",
        "delivery_outcome": "outcome_progress",
        "json_path": str(tmp_path / "must-not-read.json"),
        "vision_checkpoint": {"unchanged_reason": "The new result disproves the initial assumption."},
        "agent_vision": {"path_delta": {
            "outcome": "replan", "observed_reality": "Two controls passed; the third remained inconclusive."
        }},
        "progress_observation": {
            "result_class": "advanced", "probe_kind": "paired-control",
            "surface_id": "comparison",
            "evidence_ids": [str(tmp_path / "private-artifact")] * 10,
        },
    }
    p.write_text(json.dumps(row))
    result = read_manager_delivery_history(
        tmp_path, "alpha", now=datetime(2026, 1, 2, 3, tzinfo=timezone.utc)
    )
    detail = result["deliveries"][0]["recorded_details"]
    assert detail["checkpoint_reason"] == row["vision_checkpoint"]["unchanged_reason"]
    assert detail["observed_reality"].startswith("Two controls passed")
    assert detail["probe_kind"] == "paired-control"
    assert detail["verification"] == "recorded_claim_not_independent_verification"
    assert detail["artifact_read_status"] == "not_read"
    assert detail["evidence_coverage"] == {"status": "read", "known": 10, "included": 8, "omitted": 2}
    assert all(ref.startswith("sha256:") for ref in detail["evidence_refs"])
    assert "private-artifact" not in json.dumps(result)
    assert "must-not-read" not in json.dumps(result)


def test_detail_missing_invalid_truncated_and_redacted_are_explicit():
    from loopx.chat.manager_history import _recorded_details

    detail = _recorded_details({
        "vision_checkpoint": {"unchanged_reason": "x" * 1000},
        "agent_vision": {"path_delta": {"observed_reality": {"unexpected": "payload"}}},
        "progress_observation": {"evidence_ids": "not-a-list", "probe_kind": "api_key=private-value"},
    })
    assert detail["field_coverage"]["truncated"] == ["checkpoint_reason"]
    assert detail["field_coverage"]["invalid"] == ["observed_reality"]
    assert "result_class" in detail["field_coverage"]["missing"]
    assert "private-value" not in json.dumps(detail)
    assert "unexpected" not in json.dumps(detail)
    assert detail["evidence_coverage"]["known"] is None
    assert detail["evidence_refs"] == []
    assert _recorded_details({})["field_coverage"]["missing"]


def test_local_scope_configuration_validates_goals_and_preserves_targets(tmp_path):
    import pytest
    from loopx.capabilities.manager_context import configure_evidence_scope

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "alpha"}]}))
    channel = "manager.external." + "a" * 24
    path = tmp_path / ".local" / "manager-context" / "policy.json"
    preview = configure_evidence_scope(
        tmp_path, registry, channel=channel, goal_ids=["alpha"]
    )
    assert preview["executed"] is False and not path.exists()
    with pytest.raises(ValueError):
        configure_evidence_scope(
            tmp_path, registry, channel=channel, goal_ids=["unknown"], execute=True
        )
    configure_evidence_scope(
        tmp_path, registry, channel=channel, goal_ids=["alpha"], execute=True
    )
    assert evidence_goal_scope(tmp_path, channel) == ["alpha"]
    config = json.loads(path.read_text())
    config["sources"][channel]["targets"] = [{"goal_id": "alpha", "agent_id": "worker"}]
    path.write_text(json.dumps(config))
    configure_evidence_scope(
        tmp_path, registry, channel=channel, goal_ids=[], execute=True
    )
    assert evidence_goal_scope(tmp_path, channel) == []
    assert (
        json.loads(path.read_text())["sources"][channel]["targets"]
        == config["sources"][channel]["targets"]
    )
