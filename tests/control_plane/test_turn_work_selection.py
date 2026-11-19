"""Historical Turn selection retains original JSON and Python text identity."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from loopx.control_plane.turn_driver.driver import selected_turn_todo


_SELECTED = {"todo_id": "fallback", "text_ref": "action.recommended_action",
             "unknown": [None, False, 1.0, -0.0, 9007199254740993]}


@pytest.mark.parametrize(("primary", "expected"), [
    (" primary ", "primary"),
    ("\u001cprimary\u001f", "primary"),
    ("\ufeffprimary\ufeff", "\ufeffprimary\ufeff"),
    ("", None), ("  ", None), (None, None), (False, None), (0, None),
    (True, "True"), (7, "7"), (1.0, "1.0"),
    ([], None), ({}, None), (["x"], "['x']"), ({"x": 1}, "{'x': 1}"),
])
def test_historical_primary_text_identity(primary: Any, expected: str | None) -> None:
    envelope = {"action": {"selected_todo": deepcopy(_SELECTED)},
                "task_orchestration_contract": {
                    "schema_version": "task_orchestration_contract_v2",
                    "mode": "adaptive", "primary_todo_id": primary}}
    before = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    wanted = (_SELECTED if expected is None else {
        "todo_id": expected, "source": "task_orchestration_contract.primary_todo_id"})
    assert json.dumps(selected_turn_todo(envelope), sort_keys=True) == json.dumps(wanted, sort_keys=True)
    assert json.dumps(envelope, ensure_ascii=False, sort_keys=True) == before


@pytest.mark.parametrize("orchestration", [
    None, [], {}, {"schema_version": "task_orchestration_contract_v1", "mode": "adaptive", "primary_todo_id": "other"},
    {"schema_version": "task_orchestration_contract_v2", "mode": "serial", "primary_todo_id": "other"},
])
def test_only_registered_adaptive_contract_overrides_selected_record(orchestration: Any) -> None:
    envelope = {"action": {"selected_todo": deepcopy(_SELECTED)},
                "task_orchestration_contract": orchestration}
    assert json.dumps(selected_turn_todo(envelope), sort_keys=True) == json.dumps(_SELECTED, sort_keys=True)


@pytest.mark.parametrize("action", [None, [], {}, {"selected_todo": None}, {"selected_todo": []}])
def test_absent_or_nonobject_selected_work_stays_empty(action: Any) -> None:
    assert selected_turn_todo({"action": action}) == {}


def test_selected_result_is_detached_from_original_record() -> None:
    envelope = {"action": {"selected_todo": deepcopy(_SELECTED)}}
    selected = selected_turn_todo(envelope)
    selected["todo_id"] = "changed"
    assert envelope["action"]["selected_todo"]["todo_id"] == "fallback"


def test_large_selected_record_retains_original_number_tokens_through_snapshot_transport() -> None:
    selected = {**deepcopy(_SELECTED), "text": '"\\' * 1_100_000}
    envelope = {"action": {"selected_todo": selected}}
    assert json.dumps(selected_turn_todo(envelope), sort_keys=True) == json.dumps(selected, sort_keys=True)
