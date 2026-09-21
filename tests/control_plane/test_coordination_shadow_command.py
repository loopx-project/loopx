from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from loopx.cli import build_parser
from loopx.cli_commands import coordination_shadow as command


def _goal() -> dict[str, object]:
    return {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": "loopx_coordination_runtime_shadow_config_v0",
                "provider": "file_v0",
            }
        },
    }


def _canonical_todo(todo_id: str, *, status: str) -> dict[str, object]:
    return {
        "schema_version": "todo_item_v0",
        "todo_id": todo_id,
        "role": "agent",
        "status": status,
        "done": status == "done",
        "text": f"{status} {todo_id}",
        "archive_state": "active",
        "source_section": "Agent Todo",
    }


def _run(
    monkeypatch,
    tmp_path: Path,
    *,
    action: str,
    execute: bool = False,
    provider_revision: str | None = None,
    minimum_operations: int = 3,
    require_event_kind: list[str] | None = None,
    todo_id: str | None = None,
) -> tuple[int, dict[str, object]]:
    monkeypatch.setattr(command, "load_registry", lambda _path: {"goals": [_goal()]})
    monkeypatch.setattr(
        command, "resolve_runtime_root", lambda *_args, **_kwargs: tmp_path
    )
    monkeypatch.setattr(command, "resolve_goal_state", lambda **kwargs: (_goal(), tmp_path, tmp_path / "ACTIVE_GOAL_STATE.md"))
    monkeypatch.setattr(command, "build_runtime_shadow_source_snapshot", lambda **kwargs: (
        command.build_todo_runtime_shadow_projection(goal_id="goal-a", todos=[
            _canonical_todo("todo_b", status="open"), _canonical_todo("todo_a", status="done")],
            leases=[{"todo_id": "todo_b", "owner": "agent-a"}]),
        {"state_path": str(tmp_path / "ACTIVE_GOAL_STATE.md")},
    ))
    captured: dict[str, object] = {}

    def print_payload(payload, *_args) -> None:
        captured.update(payload)

    args = Namespace(
        command="coordination-shadow",
        coordination_shadow_command=action,
        goal_id="goal-a",
        project=None,
        state_file=None,
        execute=execute,
        provider_revision=provider_revision,
        minimum_operations=minimum_operations,
        require_event_kind=require_event_kind or [],
        todo_id=todo_id,
        format="json",
    )
    result = command.handle_coordination_shadow_command(
        args,
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        output_format=lambda _args: "json",
        print_payload=print_payload,
    )
    assert result is not None
    return result, captured


def test_coordination_shadow_inspect_is_read_only_and_compact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        command,
        "inspect_coordination_runtime_shadow",
        lambda **_kwargs: {
            "status": "missing",
            "bootstrap_required": True,
            "decision_read_from_shadow": False,
        },
    )
    monkeypatch.setattr(
        command,
        "bootstrap_coordination_runtime_shadow",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not bootstrap")),
    )

    result, payload = _run(monkeypatch, tmp_path, action="inspect")

    assert result == 0
    assert payload["executed"] is False
    assert payload["projection_summary"] == {"todo_count": 2, "lease_count": 1}
    assert "projection" not in payload
    assert payload["decision_read_from_shadow"] is False


def test_coordination_shadow_bootstrap_requires_execute_and_reads_back_parity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    inspections = iter(
        [
            {
                "status": "missing",
                "bootstrap_required": True,
                "decision_read_from_shadow": False,
            },
            {
                "status": "matched",
                "parity_matches": True,
                "decision_read_from_shadow": False,
            },
        ]
    )
    monkeypatch.setattr(
        command,
        "inspect_coordination_runtime_shadow",
        lambda **_kwargs: next(inspections),
    )
    bootstrap_request: dict[str, object] = {}

    def bootstrap(**kwargs) -> dict[str, object]:
        bootstrap_request.update(kwargs)
        return {"status": "applied", "decision_read_from_shadow": False}

    monkeypatch.setattr(command, "bootstrap_coordination_runtime_shadow", bootstrap)

    result, payload = _run(
        monkeypatch,
        tmp_path,
        action="bootstrap",
        execute=True,
    )

    assert result == 0
    assert payload["executed"] is True
    assert payload["ok"] is True
    assert payload["inspection"]["status"] == "matched"
    assert str(bootstrap_request["operation_id"]).startswith("shadow-bootstrap:goal-a:")
    assert str(bootstrap_request["source_version"]).startswith("legacy-projection:")
    assert bootstrap_request["projection"]["todos"] == [
        _canonical_todo("todo_a", status="done"),
        _canonical_todo("todo_b", status="open"),
    ]


def test_coordination_shadow_parser_exposes_explicit_execute_gate() -> None:
    parser = build_parser()
    preview = parser.parse_args(
        ["coordination-shadow", "bootstrap", "--goal-id", "goal-a"]
    )
    execute = parser.parse_args(
        [
            "coordination-shadow",
            "bootstrap",
            "--goal-id",
            "goal-a",
            "--execute",
        ]
    )

    assert preview.execute is False
    assert execute.execute is True

    rollback = parser.parse_args(
        [
            "coordination-shadow",
            "rollback",
            "--goal-id",
            "goal-a",
            "--provider-revision",
            "file:revision-1",
            "--execute",
        ]
    )
    assert rollback.provider_revision == "file:revision-1"
    assert rollback.execute is True

    qualify = parser.parse_args(
        [
            "coordination-shadow",
            "qualify",
            "--goal-id",
            "goal-a",
            "--minimum-operations",
            "5",
            "--require-event-kind",
            "todo_claim",
            "--require-event-kind",
            "task_lease_acquire",
        ]
    )
    assert qualify.minimum_operations == 5
    assert qualify.require_event_kind == ["todo_claim", "task_lease_acquire"]

    read_candidate = parser.parse_args(
        [
            "coordination-shadow",
            "read-candidate",
            "--goal-id",
            "goal-a",
            "--todo-id",
            "todo_b",
        ]
    )
    assert read_candidate.todo_id == "todo_b"

    promote = parser.parse_args(
        [
            "coordination-shadow",
            "promote",
            "--goal-id",
            "goal-a",
            "--minimum-operations",
            "5",
            "--require-event-kind",
            "todo_claim",
            "--execute",
        ]
    )
    assert promote.minimum_operations == 5
    assert promote.require_event_kind == ["todo_claim"]
    assert promote.execute is True


def test_coordination_shadow_reads_parity_matched_todo_candidate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        command,
        "inspect_coordination_runtime_shadow",
        lambda **_kwargs: {
            "status": "matched",
            "parity_matches": True,
            "decision_read_from_shadow": False,
        },
    )
    request: dict[str, object] = {}

    def read_candidate(**kwargs) -> dict[str, object]:
        request.update(kwargs)
        return {
            "status": "matched",
            "todo_id": "todo_b",
            "todo": {"todo_id": "todo_b", "status": "open"},
            "read_candidate_qualified": True,
            "decision_read_from_shadow": False,
        }

    monkeypatch.setattr(
        command,
        "read_coordination_runtime_shadow_todo_candidate",
        read_candidate,
    )
    result, payload = _run(
        monkeypatch,
        tmp_path,
        action="read-candidate",
        todo_id="todo_b",
    )

    assert result == 0
    assert payload["ok"] is True
    assert payload["executed"] is False
    assert payload["read_candidate"]["todo_id"] == "todo_b"
    assert request["todo_id"] == "todo_b"
    assert request["projection"]["todos"] == [
        _canonical_todo("todo_a", status="done"),
        _canonical_todo("todo_b", status="open"),
    ]


def test_coordination_shadow_qualify_applies_coverage_policy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        command,
        "inspect_coordination_runtime_shadow",
        lambda **_kwargs: {
            "status": "matched",
            "parity_matches": True,
            "decision_read_from_shadow": False,
        },
    )
    request: dict[str, object] = {}

    def qualify(**kwargs) -> dict[str, object]:
        request.update(kwargs)
        return {
            "status": "qualified",
            "qualified": True,
            "decision_read_from_shadow": False,
        }

    monkeypatch.setattr(command, "qualify_coordination_runtime_shadow", qualify)
    result, payload = _run(
        monkeypatch,
        tmp_path,
        action="qualify",
        minimum_operations=5,
        require_event_kind=["todo_claim", "task_lease_acquire"],
    )

    assert result == 0
    assert payload["ok"] is True
    assert payload["executed"] is False
    assert payload["qualification"]["status"] == "qualified"
    assert request["minimum_operations"] == 5
    assert request["required_event_kinds"] == [
        "todo_claim",
        "task_lease_acquire",
    ]


def test_coordination_shadow_promote_previews_and_applies_only_through_review_owner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        command,
        "inspect_coordination_runtime_shadow",
        lambda **_kwargs: {
            "status": "matched",
            "parity_matches": True,
            "decision_read_from_shadow": False,
        },
    )
    requests: list[dict[str, object]] = []

    def promote(**kwargs) -> dict[str, object]:
        requests.append(kwargs)
        return {
            "status": "applied" if kwargs["execute"] else "preview_ready",
            "promotion_ready": True,
            "executed": kwargs["execute"],
            "legacy_writer_fenced": kwargs["execute"],
            "legacy_fallback_used": False,
        }

    monkeypatch.setattr(
        command,
        "review_local_coordination_authority_promotion",
        promote,
    )
    preview_result, preview = _run(
        monkeypatch,
        tmp_path,
        action="promote",
        minimum_operations=5,
        require_event_kind=["todo_claim"],
    )
    apply_result, applied = _run(
        monkeypatch,
        tmp_path,
        action="promote",
        execute=True,
        minimum_operations=5,
        require_event_kind=["todo_claim"],
    )

    assert preview_result == 0
    assert preview["executed"] is False
    assert preview["promotion"]["status"] == "preview_ready"
    assert apply_result == 0
    assert applied["executed"] is True
    assert applied["promotion"]["status"] == "applied"
    assert requests[0]["execute"] is False
    assert requests[1]["execute"] is True
    assert requests[0]["minimum_operations"] == 5
    assert requests[0]["required_event_kinds"] == ["todo_claim"]
    assert str(requests[0]["operation_id"]).startswith("promote:goal-a:")


def test_coordination_shadow_rollback_passes_exact_selector_to_management_owner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    inspections = iter(
        [
            {
                "status": "matched",
                "provider_revision": "file:revision-1",
                "decision_read_from_shadow": False,
            },
            {
                "status": "missing",
                "bootstrap_required": True,
                "decision_read_from_shadow": False,
            },
        ]
    )
    monkeypatch.setattr(
        command,
        "inspect_coordination_runtime_shadow",
        lambda **_kwargs: next(inspections),
    )
    rollback_request: dict[str, object] = {}

    def rollback(**kwargs) -> dict[str, object]:
        rollback_request.update(kwargs)
        return {
            "status": "applied",
            "archive_retained": True,
            "decision_read_from_shadow": False,
        }

    monkeypatch.setattr(command, "rollback_coordination_runtime_shadow", rollback)

    result, payload = _run(
        monkeypatch,
        tmp_path,
        action="rollback",
        execute=True,
        provider_revision="file:revision-1",
    )

    assert result == 0
    assert payload["ok"] is True
    assert payload["executed"] is True
    assert payload["inspection"]["status"] == "not_evaluated"
    assert rollback_request["expected_provider_revision"] == "file:revision-1"
    assert rollback_request["operation_id"] == (
        "shadow-rollback:goal-a:file:revision-1"
    )


def test_coordination_shadow_rejects_goal_without_exact_opt_in(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        command,
        "load_registry",
        lambda _path: {"goals": [{"id": "goal-a"}]},
    )
    monkeypatch.setattr(
        command,
        "build_runtime_shadow_source_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not read legacy state without opt-in")
        ),
    )
    captured: dict[str, object] = {}
    args = Namespace(
        command="coordination-shadow",
        coordination_shadow_command="inspect",
        goal_id="goal-a",
        project=None,
        state_file=None,
        execute=False,
        format="json",
    )

    result = command.handle_coordination_shadow_command(
        args,
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        output_format=lambda _args: "json",
        print_payload=lambda payload, *_args: captured.update(payload),
    )

    assert result == 1
    assert captured["error_code"] == "coordination_shadow_not_enabled"
    assert captured["configuration"] == {
        "enabled": False,
        "provider": None,
        "reason_code": "configuration_absent",
    }
    assert captured["decision_read_from_shadow"] is False
