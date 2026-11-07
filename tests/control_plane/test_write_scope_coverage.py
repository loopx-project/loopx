"""Boundary decisions use the real typed handler over an isolated test transport.

The subprocess adapter tests the Python/TypeScript boundary without a daemon or
socket. Real CLI cases also qualify managed File and SQLite admission.
"""

import copy
import json
import os
from pathlib import Path
import runpy
import subprocess

import pytest

from loopx.control_plane.quota import projection_repair
from loopx.control_plane.effect_runtime import (
    EffectRuntimeInternalError, EffectRuntimeStartupError,
)


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
    (["src/b.ts"], ["src/[z-a!b].ts"], True),
    (["src/c.ts"], ["src/[z-a!b].ts"], False),
    (["src/[b].ts"], ["src/[z-a!b].ts"], True),
    (["src/[ac].ts"], ["src/[z-a!b].ts"], False),
    (["src/[!b].ts"], ["src/[z-a!b].ts"], False),
    (["src/a.ts"], ["src/[z-a!-b].ts"], False),
    (["src/b.ts"], ["src/[z-a!-b].ts"], True),
    (["src/-.ts"], ["src/[z-a!-b].ts"], True),
    (["src/[ac].ts"], ["src/[z-a!-b].ts"], False),
    (["src/[ab].ts"], ["src/[z-a!-b].ts"], True),
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
    assert len(scope_transport) == 2
    assert all(check["required"] == "src/[ab].ts" for check in scope_transport[1][1]["checks"])


@pytest.mark.parametrize("active_covers", [True, False])
@pytest.mark.parametrize("failure", ["internal", "startup", "malformed"])
def test_optional_inactive_diagnostics_cannot_override_active_decision(monkeypatch, active_covers, failure):
    calls = []

    def request(method, params):
        calls.append(params)
        if all(check["allowed"] == ["docs/**"] for check in params["checks"]):
            return {"covered": [active_covers]}
        if failure == "malformed":
            return {"covered": [1]}
        error = EffectRuntimeInternalError if failure == "internal" else EffectRuntimeStartupError
        raise error("synthetic diagnostic failure", diagnostic_code="synthetic")

    monkeypatch.setattr(projection_repair, "effect_runtime_result", request)
    repair = projection_repair.build_boundary_projection_repair_hint(
        {"write_scope": ["docs/**"], "checkpointed_boundary_authority": {
            "entries": [{"active": False, "write_scope": ["src/**"]}],
        }}, {}, candidate_should_run=True,
        selected_todo=selected(["docs/file.md" if active_covers else "src/file.ts"]),
    )
    assert len(calls) == (1 if active_covers else 2)
    if active_covers:
        assert repair is None
    else:
        assert repair["effective_action"] == "boundary_projection_repair"
        assert repair["missing_write_scopes"] == ["src/file.ts"]
        assert "inactive_authority_candidates" not in repair


def test_existing_projection_smoke_uses_typed_boundary_entrypoint(scope_transport):
    smoke = Path(__file__).resolve().parents[2] / "examples/control_plane/quota-projection-repair-state-machine-smoke.py"
    # Reuse every retained smoke assertion over the real typed handler. The
    # standalone smoke separately qualifies the managed runtime transport.
    assert runpy.run_path(str(smoke))["main"]() == 0
    assert scope_transport
    assert all(method == "quota.write_scope_coverage" for method, _ in scope_transport)


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


@pytest.mark.parametrize("error_type", [EffectRuntimeInternalError, EffectRuntimeStartupError])
def test_active_coverage_errors_are_not_optional(monkeypatch, error_type):
    def request(*args):
        raise error_type("synthetic active failure", diagnostic_code="synthetic")

    monkeypatch.setattr(projection_repair, "effect_runtime_result", request)
    with pytest.raises(error_type):
        projection_repair.build_boundary_projection_repair_hint(
            {"write_scope": ["src/**"]}, {}, candidate_should_run=True,
            selected_todo=selected(["src/a.ts"]),
        )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["active", "expired_covered", "expired_missing"])
@pytest.mark.parametrize("required", ["src/a.ts", "src/b.ts"])
def test_real_cli_preserves_class_authority_and_expired_hints(tmp_path, monkeypatch, provider, mode, required):
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos,
    )
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count, _write_fixture,
    )
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    goal = config["goals"][0]
    pattern = "src/[z-a!-b].ts"
    goal["coordination"]["write_scope"] = (
        [pattern] if mode == "active" else [required] if mode == "expired_covered" else []
    )
    if mode != "active":
        goal["coordination"]["checkpointed_boundary_authority"] = [{
            "decision_id": "expired-scope-fixture", "write_scope": [pattern],
            "source": "synthetic approval", "recorded_at": "2000-01-01T00:00:00Z",
            "expires_at": "2000-01-02T00:00:00Z",
        }]
    registry.write_text(json.dumps(config))
    state = project / goal["state_file"]
    todos = read_fixture_todos(state)
    todos[0]["required_write_scopes"] = [required]
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    rc, packet = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--codex-app",
        "--turn-instance-id", "scope-compatibility", cwd=project)
    covered = mode == "expired_covered" or (mode == "active" and required == "src/a.ts")
    assert rc == 0, json.dumps(packet, ensure_ascii=False)
    assert packet["normal_delivery_allowed"] is covered, packet
    assert packet["selected_todo"]["todo_id"] == TODO_ID
    if not covered:
        assert packet["effective_action"] == "boundary_projection_repair", packet
        repair = packet["boundary_projection_gap"]
        assert repair["missing_write_scopes"] == [required]
        candidates = repair.get("inactive_authority_candidates", [])
        assert [entry["decision_id"] for entry in candidates] == (
            ["expired-scope-fixture"] if mode == "expired_missing" and required == "src/a.ts" else []
        )
    assert _spend_run_count(runtime) == 0
