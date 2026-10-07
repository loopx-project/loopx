"""Public writer regressions; fixtures never touch an installed Goal."""
from pathlib import Path

import pytest

from loopx.control_plane.todos.monitor_metadata import MonitorPollObservation
from loopx.todos import list_goal_todos, update_goal_todo
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from loopx.control_plane.runtime.time import parse_timestamp
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID, GOAL_ID, _add_monitor, _write_fixture,
)


def _poll(registry: Path, todo_id: str, at: str, result: str, **kwargs):
    return update_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, todo_id=todo_id,
        role="agent", agent_id=AGENT_ID,
        monitor_metadata=MonitorPollObservation(
            generated_at=at, result_hash=result, material_change=True, **kwargs,
        ),
    )


@pytest.mark.parametrize("effect_id", [None, "effect-two"])
def test_older_monitor_observation_cannot_rewind_state(tmp_path, effect_id):
    registry, _, state = _write_fixture(tmp_path)
    todo = _add_monitor(registry, text="Observe public fixture", target_key="fixture")
    _poll(registry, todo["todo_id"], "2030-01-01T02:00:00Z", "new")
    before = state.read_bytes()
    with pytest.raises(ValueError, match="older"):
        _poll(registry, todo["todo_id"], "2030-01-01T01:00:00Z", "old",
              monitor_effect_id=effect_id)
    assert state.read_bytes() == before


def test_invalid_observation_time_is_rejected_even_with_explicit_schedule(tmp_path):
    registry, _, state = _write_fixture(tmp_path)
    todo = _add_monitor(registry, text="Observe public fixture", target_key="fixture")
    before = state.read_bytes()
    with pytest.raises(ValueError, match="timestamp"):
        _poll(registry, todo["todo_id"], "not-a-time", "new",
              next_due_at="2030-01-01T03:00:00Z")
    assert state.read_bytes() == before


def test_authority_rejection_precedes_invalid_poll_diagnostics(tmp_path):
    registry, _, state = _write_fixture(tmp_path)
    todo = _add_monitor(registry, text="Observe public fixture", target_key="fixture")
    before = state.read_bytes()
    with pytest.raises(ValueError, match="claimed_by"):
        update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
                        agent_id="codex-main-control",
                        monitor_metadata=MonitorPollObservation(
                            generated_at="invalid", result_hash="", material_change=False))
    assert state.read_bytes() == before


def test_monitor_effect_replay_and_generation_are_locked_public_writer_semantics(tmp_path):
    registry, _, state = _write_fixture(tmp_path)
    todo = _add_monitor(registry, text="Observe public fixture", target_key="fixture")
    first = _poll(registry, todo["todo_id"], "2030-01-01T02:00:00Z", "new",
                  monitor_effect_id="effect-one")
    assert first["monitor_poll_transition"]["material_change_generation"] == 1
    before = state.read_bytes()
    replay = _poll(registry, todo["todo_id"], "2030-01-01T02:00:00Z", "new",
                   monitor_effect_id="effect-one")
    assert replay["monitor_poll_transition"]["provider_replayed"] is True
    assert state.read_bytes() == before
    with pytest.raises(ValueError, match="different observation"):
        _poll(registry, todo["todo_id"], "2030-01-01T02:00:00Z", "other",
              monitor_effect_id="effect-one")
    assert state.read_bytes() == before
    readback = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, role="agent")
    assert readback["todos"][0]["material_change_generation"] == 1


@pytest.mark.parametrize("value", [
    "1970-01-01", "19700101", "1970-W01-4", "1970W014", "1970-W01", "1970W01",
    "19700101T00", "1970-01-01X0000", "1970-01-01 00:00:00", "1970-01-01T00:00z",
    "2030-01-01T12:34:56.123456+08:00", "20300101T123456,123456+0800",
    "1970-01-01T01:00:00+00:59:59.999999", "1970-01-01T00.1",
    "1970-02-30", "2021-W53", "1970-01-01T24:00:00", "1970-01-01T01:00+24:00",
    "9999-12-31T24:00:00", "9999-12-31T24:00:00Z", "9999-12-31T24:00:00.000000",
    "1970-01-01T00:0000", "1970-01-01T0000:00", "tomorrow", "2030",
])
def test_monitor_timestamp_input_matches_retained_python_iso_codec(value):
    request = {
        "schema_version": "loopx_todo_monitor_metadata_request_v0",
        "role": "agent", "task_class": "continuous_monitor", "enforce_boundedness": True,
        "metadata": {"expires_at": value},
    }
    # The retained Python reader defines the stable cross-version wire grammar.
    if parse_timestamp(value) is None:
        with pytest.raises(EffectRuntimeRejected, match="timestamp"):
            effect_runtime_result("todo.monitor_metadata.plan", request)
    else:
        result = effect_runtime_result("todo.monitor_metadata.plan", request)
        assert result["metadata"]["expires_at"] == value


@pytest.mark.parametrize(("value", "expected_utc"), [
    ("2030-01-01T00.1", "2030-01-01T00:00:00.100000+00:00"),
    ("2030-01-01T00:00.1", "2030-01-01T00:00:00.100000+00:00"),
    ("2030-01-01T00:00:00+01.1", "2029-12-31T22:59:59.900000+00:00"),
    ("2030-01-01T00:00:00+00.1", "2030-01-01T00:00:00+00:00"),
    ("2030-01-01T00:00:00-00.1", "2030-01-01T00:00:00+00:00"),
    ("2030-01-01T00:00:00+00:00:00.1", "2030-01-01T00:00:00+00:00"),
    ("2030-01-01T00.1+00.5", "2030-01-01T00:00:00.100000+00:00"),
    ("2030-01-01T00:00:00.1+00:00:00.5", "2030-01-01T00:00:00.100000+00:00"),
])
def test_monitor_timestamp_codec_is_stable_across_supported_python_versions(value, expected_utc):
    parsed = parse_timestamp(value)
    assert parsed is not None
    assert parsed.isoformat() == expected_utc
    result = effect_runtime_result("todo.monitor_metadata.plan", {
        "schema_version": "loopx_todo_monitor_metadata_request_v0",
        "role": "agent", "task_class": "continuous_monitor", "enforce_boundedness": True,
        "metadata": {"expires_at": value},
    })
    assert result["metadata"]["expires_at"] == value


def test_monitor_timestamp_codec_rejects_version_specific_end_of_day():
    value = "2030-01-01T24:00:00"
    assert parse_timestamp(value) is None
    with pytest.raises(EffectRuntimeRejected, match="timestamp"):
        effect_runtime_result("todo.monitor_metadata.plan", {
            "schema_version": "loopx_todo_monitor_metadata_request_v0",
            "role": "agent", "task_class": "continuous_monitor", "enforce_boundedness": True,
            "metadata": {"expires_at": value},
        })


@pytest.mark.parametrize("date", ["1970-01-01", "19700101", "1970-W01-4"])
@pytest.mark.parametrize("separator", ["Z", "z"])
def test_public_writer_rejects_timezone_letter_as_date_separator(tmp_path, date, separator):
    registry, _, state = _write_fixture(tmp_path)
    todo = _add_monitor(registry, text="Observe public fixture", target_key="fixture")
    value = f"{date}{separator}00:00"
    assert parse_timestamp(value) is None  # Independent legacy input contract.
    before = state.read_bytes()
    with pytest.raises(ValueError, match="expires-at must be an ISO timestamp"):
        update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
                         role="agent", agent_id=AGENT_ID, monitor_metadata={"expires_at": value})
    assert state.read_bytes() == before


@pytest.mark.parametrize("suffix", ["Z", "z"])
def test_public_writer_keeps_terminal_timezone_letter(tmp_path, suffix):
    registry, _, _state = _write_fixture(tmp_path)
    todo = _add_monitor(registry, text="Observe public fixture", target_key="fixture")
    value = f"2030-01-01T00:00{suffix}"
    assert parse_timestamp(value) is not None
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
                     role="agent", agent_id=AGENT_ID, monitor_metadata={"expires_at": value})
    readback = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, role="agent")
    assert readback["todos"][0]["expires_at"] == value
