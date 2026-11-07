"""Boundary decisions use the real typed handler over an isolated test transport.

The subprocess adapter tests the Python/TypeScript boundary without a daemon or
socket. Existing should-run CLI tests retain the managed-runtime integration.
"""

import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from loopx.control_plane.quota import projection_repair


@pytest.fixture
def scope_transport(monkeypatch):
    calls = []

    def request(method, params):
        calls.append((method, params))
        result = subprocess.run(
            ["node", "--input-type=module", "-e", """
import {readFileSync} from 'node:fs';
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from './loopx/control_plane/effect_runtime_handlers.ts';
const {method, params} = JSON.parse(readFileSync(0, 'utf8'));
const handlers = createEffectRuntimeHandlers({fingerprint: 'synthetic', requestShutdown() {}});
console.log(JSON.stringify(await dispatchEffectRuntimeMethod(handlers, method, params)));
"""],
            input=json.dumps({"method": method, "params": params}), text=True,
            capture_output=True, check=True, timeout=10,
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "LOOPX_USAGE_PING": "0"},
        )
        return json.loads(result.stdout)

    monkeypatch.setattr(projection_repair, "effect_runtime_result", request, raising=False)
    return calls


def selected(scopes):
    return {"todo_id": "todo_scope_fixture", "task_class": "advancement_task",
            "status": "open", "done": False, "action_kind": "implement",
            "text": "Implement synthetic change", "required_write_scopes": scopes}


@pytest.mark.parametrize(("required", "allowed", "needs_repair"), [
    (["src/*.ts"], ["src/?.ts"], True),
    (["src/[ab].ts"], ["src/?.ts"], False),
    (["src/[ab].ts"], ["src/a.ts", "src/b.ts"], False),
    (["src/a.ts", "src/long.ts"], ["src/?.ts"], True),
    (["materials/run/**", "reports/result.md"], ["material*/**", "reports/*.md"], False),
    (["src/[A-Z].ts"], ["src/[a-z].ts"], True),
    (["src/a.ts"], [], True),
])
def test_boundary_checks_entire_declared_language(scope_transport, required, allowed, needs_repair):
    todo = selected(required)
    boundary = {"write_scope": allowed}
    before = copy.deepcopy((todo, boundary))
    repair = projection_repair.build_boundary_projection_repair_hint(
        boundary, {"first_executable_items": [todo]},
        candidate_should_run=True, selected_todo=todo,
    )
    assert (repair is not None) is needs_repair
    if repair:
        assert repair["effective_action"] == "boundary_projection_repair"
        assert repair["missing_write_scopes"] == (required[-1:] if len(required) > 1 else required)
    assert (todo, boundary) == before


def test_inactive_grants_use_the_same_coverage_without_granting_authority(scope_transport):
    todo = selected(["src/[ab].ts", "docs/*.md"])
    boundary = {"write_scope": ["docs/**"], "checkpointed_boundary_authority": {
        "entries": [
            {"decision_id": "active", "active": True, "write_scope": ["src/**"]},
            {"decision_id": "too_narrow", "active": False, "write_scope": ["src/a.ts"]},
            {"decision_id": "covers_missing", "active": False, "write_scope": ["src/?.ts"]},
            {"decision_id": "covers_other", "active": False, "write_scope": ["docs/**"]},
        ],
    }}
    repair = projection_repair.build_boundary_projection_repair_hint(
        boundary, {}, candidate_should_run=True, selected_todo=todo,
    )
    assert repair["missing_write_scopes"] == ["src/[ab].ts"]
    assert [entry["decision_id"] for entry in repair["inactive_authority_candidates"]] == ["covers_missing"]
    assert len(scope_transport) == 1


@pytest.mark.parametrize(("run", "scopes"), [(False, ["src/**"]), (True, [])])
def test_inactive_or_read_only_route_needs_no_scope_request(scope_transport, run, scopes):
    assert projection_repair.build_boundary_projection_repair_hint(
        {}, {}, candidate_should_run=run, selected_todo=selected(scopes),
    ) is None
    assert scope_transport == []


@pytest.mark.parametrize("response", [None, {}, {"covered": []}, {"covered": [1]}, {"covered": [True, True]}])
def test_malformed_coverage_does_not_silently_authorize(monkeypatch, response):
    monkeypatch.setattr(projection_repair, "effect_runtime_result", lambda *args: response)
    with pytest.raises(TypeError, match="invalid typed write-scope coverage result"):
        projection_repair.build_boundary_projection_repair_hint(
            {"write_scope": ["src/**"]}, {}, candidate_should_run=True,
            selected_todo=selected(["src/*.ts"]),
        )
