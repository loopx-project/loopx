from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime

import pytest

from loopx.capabilities.periodic_report.cadence_journal import (
    admit_cadence_window,
    cadence_intent,
    read_cadence_journal,
    validate_cadence_window,
)
from loopx.capabilities.periodic_report.machine_defaults import resolve_goal_periodic_report_subscription
from loopx.capabilities.periodic_report.post_writeback_hook import evaluate_periodic_report_trigger_evaluation_intent


def config():
    return resolve_goal_periodic_report_subscription({"id": "example", "control_plane": {
        "periodic_report": {"enabled": True, "profile_preset": "weekly-progress",
            "route_ref": "report-channel", "timezone": "UTC", "schedule": {
                "schema_version": "periodic_report_schedule_v0", "schedule_id": "weekly-report",
                "timezone": "UTC", "rrule": "FREQ=WEEKLY;BYDAY=FR;BYHOUR=18",
            }},
    }}, None)


def admit(root, date="2026-09-11T18:00:00+00:00", subscription=None, agent="reporter"):
    return admit_cadence_window(runtime_root=root, goal_id="example", agent_id=agent,
        subscription=subscription or config(), now=datetime.fromisoformat(date))


def test_concurrent_wakes_have_one_frozen_window(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: admit(tmp_path), range(4)))
    assert sum(result["mutated"] for result in results) == 1
    assert len({result["window"]["window_id"] for result in results}) == 1
    persisted = read_cadence_journal(runtime_root=tmp_path, goal_id="example")
    assert persisted["publication"] is None
    assert persisted["window"] == results[0]["window"]
    assert not (tmp_path / "goals/example/periodic_reports/publication-cursors").exists()


def test_pending_window_survives_restart_later_period_and_competing_peer(tmp_path):
    first = admit(tmp_path)
    later = admit(tmp_path, "2026-10-02T18:15:00+00:00", agent="another-peer")
    assert later["window"] == first["window"]
    assert later["status"] == "pending"
    assert later["mutated"] is False


def test_cadence_is_eligible_without_a_fake_stage_or_user(tmp_path):
    window = admit(tmp_path)["window"]
    intent = cadence_intent(window)
    decision = evaluate_periodic_report_trigger_evaluation_intent(intent)
    assert decision["eligible"] is True
    assert "stage_completion" not in intent["payload"]
    assert "report_request" not in intent["payload"]
    assert intent["payload"]["generation_authorized"] is False
    assert intent["payload"]["external_delivery_authorized"] is False
    invalid = deepcopy(intent)
    invalid["payload"]["stage_completion"] = {}
    with pytest.raises(ValueError, match="cannot also claim"):
        evaluate_periodic_report_trigger_evaluation_intent(invalid)


def test_configuration_change_supersedes_only_unprepared_window(tmp_path):
    first = admit(tmp_path)
    updated = {**config(), "effective_revision": "sha256:" + "b" * 64}
    second = admit(tmp_path, subscription=updated)
    assert second["status"] == "pending"
    assert second["window"]["window_id"] != first["window"]["window_id"]
    assert second["mutated"] is True
    persisted = read_cadence_journal(runtime_root=tmp_path, goal_id="example")
    assert persisted["supersedes"] == first["window"]["window_id"]
    assert persisted["publication"] is None
    assert admit(tmp_path, subscription=updated)["mutated"] is False
    from loopx.capabilities.periodic_report.pending_intent import _attempt_dir
    _attempt_dir(tmp_path, "example", cadence_intent(second["window"])).mkdir(parents=True)
    third = admit(tmp_path, subscription=config())
    assert third["status"] == "configuration_changed"
    assert third["window"] == second["window"]
    assert third["mutated"] is False


def test_configuration_change_after_preparation_keeps_exact_window(tmp_path):
    from loopx.capabilities.periodic_report.pending_intent import _attempt_dir
    first = admit(tmp_path)
    _attempt_dir(tmp_path, "example", cadence_intent(first["window"])).mkdir(parents=True)
    updated = {**config(), "effective_revision": "sha256:" + "b" * 64}
    second = admit(tmp_path, subscription=updated)
    assert second["status"] == "configuration_changed"
    assert second["window"] == first["window"]
    assert second["mutated"] is False


def test_crash_after_supersession_archive_recovers_once(tmp_path, monkeypatch):
    import loopx.capabilities.periodic_report.cadence_journal as module
    first = admit(tmp_path)
    updated = {**config(), "effective_revision": "sha256:" + "b" * 64}
    original_write = module.atomic_write_json
    def fail_active_write(path, value):
        if path.name == "cadence.json":
            raise OSError("simulated interrupted replacement")
        return original_write(path, value)
    monkeypatch.setattr(module, "atomic_write_json", fail_active_write)
    with pytest.raises(OSError, match="interrupted replacement"):
        admit(tmp_path, subscription=updated)
    assert read_cadence_journal(runtime_root=tmp_path, goal_id="example")["window"] == first["window"]
    monkeypatch.setattr(module, "atomic_write_json", original_write)
    recovered = admit(tmp_path, subscription=updated)
    assert recovered["mutated"] is True
    assert admit(tmp_path, subscription=updated)["mutated"] is False
    archives = list((tmp_path / "goals/example/periodic_reports/superseded-cadence").glob("*.json"))
    assert len(archives) == 1


def test_delivery_cursor_recovers_crash_and_coalesces_missed_periods(tmp_path, monkeypatch):
    first = admit(tmp_path)
    trigger = evaluate_periodic_report_trigger_evaluation_intent(cadence_intent(first["window"]))
    observed_cursor = {"covered_trigger_ids": [trigger["selected_trigger_id"]],
        "cursor_id": "cursor-example", "publication_id": "publication-example",
        "delivered_at": "2026-09-11T18:05:00Z"}
    monkeypatch.setattr("loopx.capabilities.periodic_report.incremental.read_periodic_report_publication_cursor",
                        lambda **kwargs: observed_cursor)
    recovered = admit(tmp_path, "2026-09-11T18:15:00+00:00")
    assert recovered["status"] == "already_published" and recovered["mutated"] is True
    assert admit(tmp_path)["mutated"] is False
    assert read_cadence_journal(runtime_root=tmp_path, goal_id="example")["publication"]["publication_id"] == "publication-example"
    later = admit(tmp_path, "2026-10-02T18:15:00+00:00")
    assert later["status"] == "pending"
    assert later["window"]["due_at"] == "2026-10-02T18:00:00Z"
    assert later["window"]["window_id"] != first["window"]["window_id"]


def test_unrelated_successful_report_does_not_acknowledge_cadence(tmp_path, monkeypatch):
    first = admit(tmp_path)
    monkeypatch.setattr("loopx.capabilities.periodic_report.incremental.read_periodic_report_publication_cursor",
                        lambda **kwargs: {"covered_trigger_ids": ["unrelated-trigger"]})
    later = admit(tmp_path, "2026-09-18T18:15:00+00:00")
    assert later["status"] == "pending" and later["window"] == first["window"]


def test_disabled_is_effect_free_and_corruption_never_restarts_journal(tmp_path):
    disabled = {**config(), "enabled": False}
    assert admit(tmp_path, subscription=disabled)["status"] == "disabled"
    assert list(tmp_path.iterdir()) == []
    admit(tmp_path)
    path = tmp_path / "goals/example/periodic_reports/cadence.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="journal contract"):
        admit(tmp_path)
    assert path.read_text() == "{}"


@pytest.mark.parametrize("key,value", [("due_at", "2026-09-12T18:00:00Z"),
    ("agent_id", "../escape"), ("window_id", "cadence_bad"), ("next_due_at", "2026-09-25T18:00:00Z")])
def test_changed_window_is_rejected(tmp_path, key, value):
    window = deepcopy(admit(tmp_path)["window"])
    window[key] = value
    with pytest.raises(ValueError):
        validate_cadence_window(window)


def starting(subscription, date):
    changed = deepcopy(subscription)
    changed["schedule"]["start_date"] = date
    changed["effective_revision"] = "sha256:" + "c" * 64
    return changed


def test_future_start_does_not_admit_or_consume_window(tmp_path):
    subscription = starting(config(), "2026-09-18")
    early = admit(tmp_path, subscription=subscription)
    assert early == {"status": "not_due", "mutated": False, "window": None,
                     "next_due_at": "2026-09-18T18:00:00Z"}
    assert read_cadence_journal(runtime_root=tmp_path, goal_id="example") is None
    assert not list(tmp_path.rglob("*.json"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        due = list(pool.map(lambda _: admit(tmp_path, "2026-09-18T18:00:00+00:00", subscription), range(4)))
    assert sum(item["mutated"] for item in due) == 1
    assert len({item["window"]["window_id"] for item in due}) == 1
    assert due[0]["window"]["start_at"] == "2026-09-11T18:00:00Z"
    assert due[0]["window"]["due_at"] == "2026-09-18T18:00:00Z"
    assert admit(tmp_path, "2026-09-18T18:15:00+00:00", subscription)["mutated"] is False


def test_future_edit_retains_pending_and_prepared_history(tmp_path):
    from loopx.capabilities.periodic_report.pending_intent import _attempt_dir
    first = admit(tmp_path)
    original = (tmp_path / "goals/example/periodic_reports/cadence.json").read_bytes()
    later = starting(config(), "2026-09-18")
    assert admit(tmp_path, subscription=later)["status"] == "not_due"
    assert (tmp_path / "goals/example/periodic_reports/cadence.json").read_bytes() == original
    _attempt_dir(tmp_path, "example", cadence_intent(first["window"])).mkdir(parents=True)
    held = admit(tmp_path, subscription=later)
    assert held["status"] == "configuration_changed" and held["window"] == first["window"]
    assert (tmp_path / "goals/example/periodic_reports/cadence.json").read_bytes() == original


def test_clock_rollback_keeps_already_admitted_identity(tmp_path):
    subscription = starting(config(), "2026-09-18")
    first = admit(tmp_path, "2026-09-18T18:00:00+00:00", subscription)
    rollback = admit(tmp_path, "2026-09-11T18:00:00+00:00", subscription)
    assert rollback["window"] == first["window"] and rollback["mutated"] is False
    assert rollback["status"] == "pending"


def test_future_start_does_not_hide_corrupt_journal_or_allow_forged_early_window(tmp_path):
    old = admit(tmp_path)["window"]
    forged = {**old, "schedule": {**old["schedule"], "start_date": "2026-09-18"}}
    with pytest.raises(ValueError, match="boundaries"):
        validate_cadence_window(forged)
    (tmp_path / "goals/example/periodic_reports/cadence.json").write_text("{}")
    with pytest.raises(ValueError, match="journal contract"):
        admit(tmp_path, subscription=starting(config(), "2026-09-18"))
