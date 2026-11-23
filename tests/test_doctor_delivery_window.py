"""Doctor reports a missed Codex App delivery window as a typed diagnosis.

The Goal's own records cannot tell "waiting" from "the host stopped delivering
the scheduled prompt". This readback measures each bound lane against the
cadence its installed automation asks for, so the operator sees the silent lane
on the next ``loopx doctor`` instead of days later.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import loopx.codex_app_thread_activity as codex_activity
from loopx import doctor
from loopx.control_plane.agents.host_delivery_diagnosis import (
    HOST_DELIVERY_CHECK_ID,
    HOST_PROMPT_DELIVERY_MISSED,
    HOST_PROMPT_DELIVERY_UNVERIFIED,
    collect_host_delivery_diagnosis,
)
from loopx.control_plane.agents.host_thread_activity import (
    HostDeliveryExpectation,
    HostDeliveryScope,
    HostThreadActivity,
    HostThreadState,
)
from loopx.codex_app_thread_activity import observe_codex_threads


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _event(timestamp: str, kind: str, **payload: object) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": kind, **payload},
    }


class _CodexHome:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "sessions").mkdir(parents=True)
        self.db = root / "state_5.sqlite"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER)"
            )

    def thread(self, thread_id: str, records: list[dict[str, object]]) -> Path:
        if not records or records[0].get("type") != "session_meta":
            records = [
                {
                    "timestamp": records[0]["timestamp"] if records else "2026-09-25T09:59:00.000Z",
                    "type": "session_meta",
                    "payload": {"id": thread_id},
                },
                *records,
            ]
        path = self.root / "sessions" / f"rollout-{thread_id}.jsonl"
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?)", (thread_id, str(path), 0)
            )
        return path

    def automation(
        self,
        automation_id: str,
        *,
        goal_id: str,
        agent_id: str,
        target_thread_id: str,
        rrule: str = "RRULE:FREQ=MINUTELY;INTERVAL=20",
        status: str = "ACTIVE",
    ) -> None:
        path = self.root / "automations" / automation_id / "automation.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'version = 1\nid = "{automation_id}"\nkind = "heartbeat"\n'
            f'status = "{status}"\nrrule = "{rrule}"\n'
            f'target_thread_id = "{target_thread_id}"\n'
            f'prompt = "Advance `{goal_id}` from active state. Agent: `{agent_id}`."\n',
            encoding="utf-8",
        )


@pytest.fixture(autouse=True)
def _fresh_rollout_cache() -> None:
    codex_activity._rollout_cache.clear()


def _goal(goal_id: str = "release-work", *, thread_id: str = "thread-lane") -> dict:
    return {
        "id": goal_id,
        "coordination": {
            "thread_agent_bindings": [
                {"agent_id": "operations", "host_surface": "codex-app", "thread_id": thread_id}
            ]
        },
    }


def _observers_and_expectations(interval_minutes: int = 20):
    def expect(scopes):
        return {
            scope: HostDeliveryExpectation(
                expected_interval_minutes=interval_minutes, source="codex_app_automation_rrule"
            )
            for scope in scopes
            if isinstance(scope, HostDeliveryScope)
        }

    return (
        {"codex-app": lambda ids: observe_codex_threads(ids, homes=[])},
        expect,
    )


def test_a_lane_past_its_window_is_reported_with_the_last_observed_tick(tmp_path: Path) -> None:
    home = _CodexHome(tmp_path / ".codex")
    observed = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    home.thread("thread-lane", [_event(_ts(observed), "task_complete")])
    now = observed + timedelta(hours=3)
    observers = {"codex-app": lambda ids: observe_codex_threads(ids, homes=[home.root])}

    diagnosis = collect_host_delivery_diagnosis(
        [_goal()],
        observers=observers,
        expectations=lambda scopes: {
            scope: HostDeliveryExpectation(20, "codex_app_automation_rrule") for scope in scopes
        },
        now=now,
    )

    assert diagnosis["status"] == "stale"
    assert diagnosis["reason"] == HOST_PROMPT_DELIVERY_MISSED
    assert diagnosis["counts"]["stale"] == 1
    [lane] = diagnosis["lanes"]
    assert lane["lane"] == "release-work/operations/codex-app"
    assert lane["window_state"] == "stale"
    assert lane["diagnosis"]["code"] == HOST_PROMPT_DELIVERY_MISSED
    assert lane["diagnosis"]["last_observed_at"] == "2026-09-25T10:00:00.000Z"
    assert lane["diagnosis"]["age_hours"] == 3.0
    assert lane["diagnosis"]["window_minutes"] == 40


def test_a_lane_inside_its_window_is_quiet(tmp_path: Path) -> None:
    home = _CodexHome(tmp_path / ".codex")
    observed = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    home.thread("thread-lane", [_event(_ts(observed), "task_complete")])
    observers = {"codex-app": lambda ids: observe_codex_threads(ids, homes=[home.root])}

    diagnosis = collect_host_delivery_diagnosis(
        [_goal()],
        observers=observers,
        expectations=lambda scopes: {
            scope: HostDeliveryExpectation(20, "codex_app_automation_rrule") for scope in scopes
        },
        now=observed + timedelta(minutes=25),
    )

    assert diagnosis["status"] == "fresh"
    assert diagnosis["reason"] is None
    assert diagnosis["stale_lanes"] == []
    assert "diagnosis" not in diagnosis["lanes"][0]


def test_a_lane_without_a_cadence_is_unverified_not_healthy(tmp_path: Path) -> None:
    home = _CodexHome(tmp_path / ".codex")
    observed = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    home.thread("thread-lane", [_event(_ts(observed), "task_complete")])
    observers = {"codex-app": lambda ids: observe_codex_threads(ids, homes=[home.root])}

    diagnosis = collect_host_delivery_diagnosis(
        [_goal()], observers=observers, expectations=lambda scopes: {}, now=observed
    )

    [lane] = diagnosis["lanes"]
    assert lane["window_state"] == "unknown"
    assert lane["reason"] == "no_expectation"
    assert lane["diagnosis"] == {
        "code": HOST_PROMPT_DELIVERY_UNVERIFIED,
        "state": "unknown",
        "reason": "no_expectation",
    }
    assert diagnosis["status"] == "unverified"
    assert diagnosis["reason"] == "no_comparable_tick"
    # Unverified is not a fault: nothing proves the host stopped delivering.
    assert diagnosis["stale_lanes"] == []


def test_a_lane_with_no_record_is_missing_rather_than_fresh() -> None:
    diagnosis = collect_host_delivery_diagnosis(
        [_goal()],
        observers={
            "codex-app": lambda ids: {
                thread: __import__(
                    "loopx.control_plane.agents.host_thread_activity",
                    fromlist=["HostThreadActivity"],
                ).HostThreadActivity.unknown(
                    __import__(
                        "loopx.control_plane.agents.host_thread_activity",
                        fromlist=["HostThreadUnknownReason"],
                    ).HostThreadUnknownReason.THREAD_NOT_FOUND
                )
                for thread in ids
            }
        },
        expectations=lambda scopes: {
            scope: HostDeliveryExpectation(20, "codex_app_automation_rrule") for scope in scopes
        },
    )

    [lane] = diagnosis["lanes"]
    assert lane["window_state"] == "missing"
    assert diagnosis["status"] == "unverified"


def test_a_stopped_goal_is_not_measured_and_unbound_goals_are_skipped() -> None:
    stopped = {**_goal("retired"), "activation": {"state": "stopped"}}
    unbound = {"id": "no-thread"}
    diagnosis = collect_host_delivery_diagnosis(
        [stopped, unbound],
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
    )

    assert diagnosis["lanes"] == []
    assert diagnosis["skipped_stopped_goals"] == 1
    assert diagnosis["status"] == "not_applicable"
    assert diagnosis["reason"] == "no_bound_host_lane"


def test_a_binding_without_a_thread_is_counted_not_silently_dropped() -> None:
    # The surface is observable, so this Goal is this check's business; a
    # binding holding no thread id cannot be measured. Dropping it would leave
    # the operator reading a healthy workspace with one lane simply missing.
    goal = {
        "id": "release-work",
        "coordination": {
            "thread_agent_bindings": [
                {"agent_id": "operations", "host_surface": "codex-app", "thread_id": ""},
                {"host_surface": "codex-app", "thread_id": "thread-lane"},
            ]
        },
    }
    diagnosis = collect_host_delivery_diagnosis(
        [goal],
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
    )

    assert diagnosis["lanes"] == []
    assert diagnosis["unmeasured_bindings"] == 2
    assert diagnosis["status"] == "not_applicable"
    assert diagnosis["reason"] == "unmeasured_lane"


def test_an_unreadable_activation_record_does_not_read_fresh() -> None:
    unreadable = {
        "id": "old-goal",
        "coordination": _goal()["coordination"],
        "activation": {"schema_version": "loopx_goal_activation_v0"},
    }

    def observe(thread_ids):
        return {
            thread_id: HostThreadActivity(
                HostThreadState.IDLE, last_event_at=_ts(datetime.now(timezone.utc))
            )
            for thread_id in thread_ids
        }

    diagnosis = collect_host_delivery_diagnosis(
        [unreadable, _goal()],
        observers={"codex-app": observe},
        expectations=_observers_and_expectations()[1],
    )

    assert diagnosis["counts"]["fresh"] == 1
    assert diagnosis["unreadable_activation_goals"] == 1
    assert diagnosis["status"] == "unverified"
    assert diagnosis["reason"] == "unmeasured_lane"


def test_a_goal_bound_only_to_an_unreadable_surface_is_not_reported() -> None:
    goal = {
        "id": "another-host",
        "coordination": {
            "thread_agent_bindings": [
                {"agent_id": "operations", "host_surface": "agy", "thread_id": "thread-1"}
            ]
        },
    }
    diagnosis = collect_host_delivery_diagnosis(
        [goal],
        observers={"codex-app": lambda ids: pytest.fail("no Codex lane is bound")},
        expectations=lambda scopes: {},
    )

    assert diagnosis["lanes"] == []
    assert diagnosis["reason"] == "no_bound_host_lane"


def test_two_bindings_of_one_lane_report_one_window() -> None:
    goal = {
        "id": "release-work",
        "coordination": {
            "thread_agent_bindings": [
                {"agent_id": "operations", "host_surface": "codex-app", "thread_id": "thread-a"},
                {"agent_id": "operations", "host_surface": "codex-app", "thread_id": "thread-b"},
            ]
        },
    }
    diagnosis = collect_host_delivery_diagnosis(
        [goal],
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
    )

    # An agent with two candidate threads has no single identity, so its window
    # stays unknown; it must still appear once, under the label an operator
    # would search for.
    assert diagnosis["lane_count"] == 1
    assert diagnosis["lanes"][0]["lane"] == "release-work/operations/codex-app"
    assert diagnosis["lanes"][0]["window_state"] == "unknown"


def test_the_lane_scan_is_bounded_and_says_so() -> None:
    goals = [
        {
            "id": f"goal-{index}",
            "coordination": {
                "thread_agent_bindings": [
                    {
                        "agent_id": "operations",
                        "host_surface": "codex-app",
                        "thread_id": f"thread-{index}",
                    }
                ]
            },
        }
        for index in range(5)
    ]
    diagnosis = collect_host_delivery_diagnosis(
        goals,
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
        max_lanes=3,
    )

    assert diagnosis["lane_count"] == 3
    assert diagnosis["truncated"] is True
    assert len(diagnosis["lanes"]) == 3


def test_a_goal_past_the_shared_binding_cap_reads_truncated() -> None:
    # The shared observer keeps only MAX_OBSERVED_THREADS_PER_GOAL bindings per
    # Goal. A silent lane can sit in the part it did not read, so a bounded scan
    # must not present the result as complete.
    cap = 32
    goal = {
        "id": "wide-goal",
        "coordination": {
            "thread_agent_bindings": [
                {
                    "agent_id": f"agent-{index}",
                    "host_surface": "codex-app",
                    "thread_id": f"thread-{index}",
                }
                for index in range(cap + 1)
            ]
        },
    }
    diagnosis = collect_host_delivery_diagnosis(
        [goal],
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
    )

    assert diagnosis["lane_count"] == cap
    assert diagnosis["incomplete_goals"] == 1
    assert diagnosis["per_goal_binding_cap"] == cap
    assert diagnosis["truncated"] is True


def test_a_bounded_scan_does_not_claim_a_fresh_verdict() -> None:
    # The lanes that were read are inside their window, but the bound left
    # others unread; calling that `fresh` would claim coverage the run did not
    # have, which is the fault this readback exists to prevent.
    goals = [
        {
            "id": f"goal-{index}",
            "coordination": {
                "thread_agent_bindings": [
                    {
                        "agent_id": "operations",
                        "host_surface": "codex-app",
                        "thread_id": f"thread-{index}",
                    }
                ]
            },
        }
        for index in range(3)
    ]

    def observe(thread_ids):
        return {
            thread_id: HostThreadActivity(
                HostThreadState.IDLE, last_event_at=_ts(datetime.now(timezone.utc))
            )
            for thread_id in thread_ids
        }

    diagnosis = collect_host_delivery_diagnosis(
        goals,
        observers={"codex-app": observe},
        expectations=_observers_and_expectations()[1],
        max_lanes=2,
    )

    assert diagnosis["counts"]["fresh"] == 2
    assert diagnosis["truncated"] is True
    assert diagnosis["status"] == "unverified"
    assert diagnosis["reason"] == "scan_truncated"


def test_a_binding_named_like_a_local_path_is_redacted_not_fatal() -> None:
    # A Goal id is free-form registry text: an operator can name it after the
    # directory it covers. Doctor output also serves `--format json`, whose
    # public-output boundary refuses absolute paths. An optional diagnostic must
    # still answer, and must still name the silent lane.
    goal = _goal("/Users/operator/private-goal")
    diagnosis = collect_host_delivery_diagnosis(
        [goal],
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
        max_lanes=4,
    )

    assert diagnosis["status"] == "unverified"
    [lane] = diagnosis["lanes"]
    assert lane["goal_id"].startswith("<redacted:")
    assert lane["goal_id"].endswith(":private-goal")
    assert "/Users/operator" not in json.dumps(diagnosis)
    assert lane["label_redacted"] is True
    # The digest is stable across runs and distinct per identifier, so two
    # path-shaped Goals stay distinguishable to a machine reader.
    again = collect_host_delivery_diagnosis(
        [_goal("/Users/operator/private-goal"), _goal("/Users/operator/other-goal")],
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
    )
    labels = [item["goal_id"] for item in again["lanes"]]
    assert labels[0] == lane["goal_id"]
    assert len(set(labels)) == 2


def test_an_agent_named_like_a_credential_is_redacted() -> None:
    goal = {
        "id": "release-work",
        "coordination": {
            "thread_agent_bindings": [
                {
                    "agent_id": "api_key=sk-live-abcdefghijklmnop",
                    "host_surface": "codex-app",
                    "thread_id": "thread-lane",
                }
            ]
        },
    }
    diagnosis = collect_host_delivery_diagnosis(
        [goal],
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
    )

    [lane] = diagnosis["lanes"]
    assert lane["agent_id"].startswith("<redacted:")
    assert "sk-live" not in json.dumps(diagnosis)


def test_lane_dedupe_uses_the_identity_parts_not_the_joined_label() -> None:
    # `a` + `b/c` and `a/b` + `c` are different lanes that once shared a joined
    # label, so one of them silently disappeared from the report.
    goals = [
        {
            "id": "a",
            "coordination": {
                "thread_agent_bindings": [
                    {"agent_id": "b/c", "host_surface": "codex-app", "thread_id": "thread-1"}
                ]
            },
        },
        {
            "id": "a/b",
            "coordination": {
                "thread_agent_bindings": [
                    {"agent_id": "c", "host_surface": "codex-app", "thread_id": "thread-2"}
                ]
            },
        },
    ]
    diagnosis = collect_host_delivery_diagnosis(
        goals,
        observers={"codex-app": lambda ids: {}},
        expectations=lambda scopes: {},
    )

    assert diagnosis["lane_count"] == 2
    assert {lane["goal_id"] for lane in diagnosis["lanes"]} == {"a", "a/b"}
    # Each lane also stays addressable by its own single label.
    assert {lane["lane"] for lane in diagnosis["lanes"]} == {
        "a/b%2Fc/codex-app",
        "a%2Fb/c/codex-app",
    }


def test_an_archived_target_thread_names_the_real_cause() -> None:
    # An archived thread holds no rollout to read, so the window layer infers an
    # unparseable observation from the empty timestamp. The host record says
    # something more useful, and naming it keeps the operator off the wrong trail.
    diagnosis = collect_host_delivery_diagnosis(
        [_goal()],
        observers={
            "codex-app": lambda ids: {
                thread_id: HostThreadActivity(HostThreadState.ARCHIVED)
                for thread_id in ids
            }
        },
        expectations=lambda scopes: {
            scope: HostDeliveryExpectation(20, "codex_app_automation_rrule") for scope in scopes
        },
    )

    [lane] = diagnosis["lanes"]
    assert lane["thread_state"] == "archived"
    assert lane["diagnosis"]["reason"] == "thread_archived"
    assert diagnosis["status"] == "unverified"


def _isolated_doctor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.delenv("LOOPX_CODEX_HOMES", raising=False)
    monkeypatch.delenv("LOOPX_RELEASE_ROOT", raising=False)
    command = tmp_path / "bin" / "loopx"
    command.parent.mkdir()
    command.write_text("synthetic command; never executed", encoding="utf-8")
    monkeypatch.setattr(
        doctor,
        "default_runtime_route",
        lambda: {
            "selected_runtime_root": str(runtime),
            "status": "fresh",
            "recommended_action": None,
            "target_runtime_root": str(runtime),
            "legacy_runtime_root": None,
            "target_registry_exists": False,
            "legacy_registry_exists": False,
        },
    )
    monkeypatch.setattr(
        doctor, "resolve_command_path", lambda name: command if name == "loopx" else None
    )
    monkeypatch.setattr(doctor, "current_script_invocation_path", lambda: None)
    monkeypatch.setattr(doctor, "python_distribution_install", lambda _: {"available": False})
    monkeypatch.setattr(
        doctor.doctor_git, "trusted_release_ref_for_root", lambda *a, **k: None
    )
    monkeypatch.setattr(
        doctor, "latest_promotion_readiness_event", lambda _: {"available": False}
    )
    monkeypatch.setattr(doctor, "probe_registry_write_path", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.collect_effect_runtime_readiness",
        lambda *, deep: {"ready": True, "status": "ready"},
    )
    monkeypatch.setattr(
        "loopx.capabilities.decision_context.freshness.collect_capture_host_diagnostics",
        lambda _: {"healthy": True, "hosts": []},
    )
    monkeypatch.setattr(
        "loopx.desktop_installation.desktop_installation_status",
        lambda _: {"apps": [], "status": "not_installed"},
    )
    runtime.mkdir(parents=True)
    return home / ".codex", runtime


def _write_registry(runtime: Path, goal: dict, *, revision: str = "rev-1") -> Path:
    path = runtime / "registry.global.json"
    path.write_text(json.dumps({"goals": [dict(goal, revision=revision)]}), encoding="utf-8")
    return path


def test_doctor_reports_the_missed_window_from_the_real_automation_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home, runtime = _isolated_doctor(monkeypatch, tmp_path)
    home = _CodexHome(codex_home)
    old_tick = _ts(datetime.now(timezone.utc) - timedelta(days=3))
    home.thread("thread-lane", [_event(old_tick, "task_complete")])
    home.automation(
        "nightly", goal_id="release-work", agent_id="operations", target_thread_id="thread-lane"
    )
    _write_registry(runtime, _goal())

    payload = doctor.collect_doctor()

    diagnosis = payload["host_prompt_delivery"]
    assert diagnosis["status"] == "stale"
    assert diagnosis["lanes"][0]["diagnosis"]["code"] == HOST_PROMPT_DELIVERY_MISSED
    assert diagnosis["lanes"][0]["last_observed_at"] == old_tick
    check = next(item for item in payload["checks"] if item["id"] == HOST_DELIVERY_CHECK_ID)
    assert check["required"] is False
    assert check["ok"] is False
    assert "missed=release-work/operations/codex-app@" in check["detail"]
    assert f"last_ticks=`{old_tick}`" in doctor.render_doctor_markdown(payload)


def test_doctor_keeps_a_quiet_workspace_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex_home, runtime = _isolated_doctor(monkeypatch, tmp_path)
    home = _CodexHome(codex_home)
    home.thread("thread-lane", [_event(_ts(datetime.now(timezone.utc)), "task_complete")])
    home.automation(
        "nightly", goal_id="release-work", agent_id="operations", target_thread_id="thread-lane"
    )
    _write_registry(runtime, _goal())

    payload = doctor.collect_doctor()

    assert payload["host_prompt_delivery"]["status"] == "fresh"
    check = next(item for item in payload["checks"] if item["id"] == HOST_DELIVERY_CHECK_ID)
    assert check["ok"] is True
    assert payload["ok"] is True


def test_a_lane_bound_to_a_non_app_surface_is_not_reported_as_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Only an installed app automation carries a cadence. A Goal bound to a CLI
    # or IDE thread would otherwise read `unverified` on every run, which is how
    # an optional check stops being read at all.
    codex_home, runtime = _isolated_doctor(monkeypatch, tmp_path)
    home = _CodexHome(codex_home)
    home.thread("thread-lane", [_event(_ts(datetime.now(timezone.utc)), "task_complete")])
    goal = {
        "id": "release-work",
        "coordination": {
            "thread_agent_bindings": [
                {
                    "agent_id": "operations",
                    "host_surface": "codex-cli-tui",
                    "thread_id": "thread-lane",
                }
            ]
        },
    }
    _write_registry(runtime, goal)

    payload = doctor.collect_doctor()

    assert payload["host_prompt_delivery"]["status"] == "not_applicable"
    assert payload["host_prompt_delivery"]["reason"] == "no_bound_host_lane"
    check = next(item for item in payload["checks"] if item["id"] == HOST_DELIVERY_CHECK_ID)
    assert check["ok"] is True


def test_doctor_reports_no_bound_lane_without_a_fault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _codex_home, runtime = _isolated_doctor(monkeypatch, tmp_path)
    _write_registry(runtime, {"id": "documentation-only"})

    payload = doctor.collect_doctor()

    assert payload["host_prompt_delivery"]["status"] == "not_applicable"
    assert payload["host_prompt_delivery"]["reason"] == "no_bound_host_lane"
    check = next(item for item in payload["checks"] if item["id"] == HOST_DELIVERY_CHECK_ID)
    assert check["ok"] is True


def test_the_publish_guard_withholds_labels_instead_of_failing_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The last-resort guard: if a label this module did not anticipate still
    # violates the boundary, the verdict survives and only the names are
    # withheld. An optional check must never abort `loopx doctor`.
    codex_home, runtime = _isolated_doctor(monkeypatch, tmp_path)
    home = _CodexHome(codex_home)
    old_tick = _ts(datetime.now(timezone.utc) - timedelta(days=3))
    home.thread("thread-lane", [_event(old_tick, "task_complete")])
    home.automation(
        "nightly", goal_id="release-work", agent_id="operations", target_thread_id="thread-lane"
    )
    _write_registry(runtime, _goal())
    monkeypatch.setattr(
        "loopx.control_plane.agents.host_delivery_diagnosis._public_label",
        lambda value: (str(value or ""), False),
    )
    monkeypatch.setattr(
        "loopx.control_plane.agents.host_delivery_diagnosis._lane_payload",
        lambda goal_id, row: {
            "lane": "/Users/operator/private-goal",
            "goal_id": goal_id,
            "agent_id": row.get("agent_id"),
            "host_surface": row.get("host_surface"),
            "window_state": (row.get("delivery_window") or {}).get("state"),
            "last_observed_at": (row.get("delivery_window") or {}).get("last_observed_at"),
        },
    )

    payload = doctor.collect_doctor()

    diagnosis = payload["host_prompt_delivery"]
    assert diagnosis["status"] == "stale"
    assert diagnosis["labels_withheld"] is True
    assert diagnosis["lanes"] == []
    assert "private-goal" not in json.dumps(payload)
    check = next(item for item in payload["checks"] if item["id"] == HOST_DELIVERY_CHECK_ID)
    assert check["ok"] is False


def test_a_corrupt_registry_reports_unavailable_instead_of_raising(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.agents.host_delivery_diagnosis import (
        collect_registry_host_delivery_diagnosis,
    )

    path = tmp_path / "registry.global.json"
    path.write_text("{not json", encoding="utf-8")

    diagnosis = collect_registry_host_delivery_diagnosis(registry_path=path)

    assert diagnosis["status"] == "not_applicable"
    assert diagnosis["reason"] == "registry_unavailable"


def test_a_registry_this_build_cannot_decode_names_its_own_reason(
    tmp_path: Path,
) -> None:
    # A readable file this build cannot decode is a different operator action
    # from a missing one, and the readback has to say which.
    from loopx.control_plane.agents.host_delivery_diagnosis import (
        collect_registry_host_delivery_diagnosis,
    )

    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_project_registry_envelope_v2",
                "minimum_writer_protocol": "goal_instance_v2",
                "payload_sha256": "0" * 64,
                "profile_id": "source_session_v1",
                "goals": [],
            }
        ),
        encoding="utf-8",
    )

    diagnosis = collect_registry_host_delivery_diagnosis(registry_path=path)

    assert diagnosis["status"] == "not_applicable"
    assert diagnosis["reason"] == "registry_requires_upgrade"


def test_a_truncated_readback_does_not_report_the_check_green() -> None:
    from loopx.control_plane.agents.host_delivery_diagnosis import (
        host_delivery_diagnosis_check,
    )

    diagnosis = {
        "status": "unverified",
        "reason": "scan_truncated",
        "counts": {"fresh": 2, "stale": 0, "missing": 0, "unknown": 0},
        "lane_count": 2,
        "truncated": True,
        "unmeasured_bindings": 0,
        "unreadable_activation_goals": 0,
        "stale_lanes": [],
        "lanes": [],
    }
    check = host_delivery_diagnosis_check(diagnosis)

    assert check["ok"] is False
    assert "truncated=true" in check["detail"]


def test_a_quiet_workspace_read_back_in_full_is_green() -> None:
    from loopx.control_plane.agents.host_delivery_diagnosis import (
        host_delivery_diagnosis_check,
    )

    diagnosis = {
        "status": "fresh",
        "reason": None,
        "counts": {"fresh": 1, "stale": 0, "missing": 0, "unknown": 0},
        "lane_count": 1,
        "truncated": False,
        "unmeasured_bindings": 0,
        "unreadable_activation_goals": 0,
        "stale_lanes": [],
        "lanes": [],
    }

    assert host_delivery_diagnosis_check(diagnosis)["ok"] is True


def test_installation_scope_still_does_not_open_host_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated_doctor(monkeypatch, tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("installation-only scope must not read host delivery")

    monkeypatch.setattr(
        "loopx.control_plane.agents.host_delivery_diagnosis.collect_host_delivery_diagnosis_route",
        forbidden,
    )
    payload = doctor.collect_doctor(installation_only=True)
    assert payload["scope"] == "installation_only"
    assert "host_prompt_delivery" not in payload
