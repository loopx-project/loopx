from __future__ import annotations

import json
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

import loopx.extensions.governed_capability_execution as governed_execution
from loopx import paths
from loopx.control_plane.effect_program import SettlementIdentity
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.extensions.capability_admission import (
    bind_external_capability_to_goal,
    invoke_external_capability,
)
from loopx.extensions.governed_capability_execution import (
    reconcile_governed_external_capability,
    start_governed_external_capability,
    validate_governed_capability_result,
)
from loopx.extensions.runtime import install_extension
from loopx.todos import list_goal_todos


def test_default_governed_run_dir_follows_single_runtime_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_root = tmp_path / ".loopx"
    legacy_root = tmp_path / ".codex" / "loopx"
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current_root)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy_root)
    suffix = Path("extensions") / "governed-capability-runs"

    assert governed_execution.default_governed_capability_run_dir() == current_root / suffix
    legacy_root.mkdir(parents=True)
    (legacy_root / paths.GLOBAL_REGISTRY_FILENAME).write_text("{}", encoding="utf-8")
    assert governed_execution.default_governed_capability_run_dir() == legacy_root / suffix

    current_root.mkdir()
    (current_root / paths.GLOBAL_REGISTRY_FILENAME).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Both default LoopX registries exist"):
        governed_execution.default_governed_capability_run_dir()
    assert governed_execution.default_governed_capability_run_dir(current_root) == current_root / suffix


def _provider(path: Path, *, call_log: Path) -> Path:
    path.write_text(
        f"""#!{sys.executable}
import json
import sys
from pathlib import Path

if "--doctor" in sys.argv:
    raise SystemExit(0)

request = json.load(sys.stdin)
phase = request["lifecycle"]["phase"]
with Path({str(call_log)!r}).open("a", encoding="utf-8") as output:
    output.write(phase + "\\n")
result = {{
    "schema_version": "fixture_material_capability_result_v0",
    "invocation_id": request["invocation_id"],
    "status": "running" if phase == "start" else "succeeded",
    "observations": [{{
        "kind": "synthetic-progress",
        "phase": phase,
        "confidence": 1.0,
        "label": "材料",
    }}],
    "domain_state_mutations": [],
    "domain_transition_receipts": [],
    "transition_proposals": [{{
        "schema_version": "loopx_continuous_monitor_proposal_v0",
        "proposal_id": "fixture_monitor_upsert_1",
        "kind": "continuous_monitor_upsert",
        "monitor_key": "fixture:material-run",
        "text": "Poll the synthetic material run.",
        "action_kind": "poll_material_delivery",
        "target_key": "fixture-run:synthetic-run-1",
        "cadence": "5m",
        "next_due_at": "2099-01-01T00:05:00+00:00",
        "expires_at": "2099-01-02T00:00:00+00:00",
        "required_capabilities": ["network"],
    }}],
    "effect_receipt": None,
    "follow_up": {{"kind": "poll", "ref": "synthetic-run-1"}},
}}
if phase == "reconcile":
    result["domain_state_mutations"] = [{{"kind": "synthetic-update"}}]
    result["domain_transition_receipts"] = [{{"kind": "synthetic-commit"}}]
    result["effect_receipt"] = {{
        "schema_version": "loopx_external_effect_receipt_v0",
        "invocation_id": request["invocation_id"],
        "idempotency_key": request["lifecycle"]["idempotency_key"],
        "status": "committed",
        "external_ref": "synthetic-run-1",
        "evidence_digest": "sha256:synthetic-evidence",
    }}
    result["transition_proposals"] = [{{
        "schema_version": "loopx_continuous_monitor_proposal_v0",
        "proposal_id": "fixture_monitor_complete_1",
        "kind": "continuous_monitor_complete",
        "monitor_key": "fixture:material-run",
        "evidence": "Synthetic provider reached its terminal state.",
    }}]
json.dump(result, sys.stdout)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _installed_material_extension(tmp_path: Path) -> tuple[Path, Path, Path]:
    call_log = tmp_path / "provider-calls.txt"
    provider = _provider(tmp_path / "provider", call_log=call_log)
    (tmp_path / "profile.json").write_text(
        json.dumps(
            {
                "schema_version": "loopx_external_domain_capability_profile_v0",
                "capability_id": "fixture-material-delivery",
                "protocol": "fixture_material_provider_v0",
                "operations": [
                    {
                        "id": "publish",
                        "effect_class": "external_write",
                        "todo_contract": {
                            "action_kinds": ["fixture_material_delivery"],
                            "target_key_prefixes": ["fixture-material:"],
                        },
                        "transition_contract": {
                            "proposal_kinds": [
                                "continuous_monitor_upsert",
                                "continuous_monitor_complete",
                            ],
                            "monitor_key_prefixes": ["fixture:"],
                            "monitor_action_kinds": ["poll_material_delivery"],
                            "monitor_target_key_prefixes": ["fixture-run:"],
                            "monitor_required_capabilities": ["network"],
                        },
                        "required_permission": "fixture.delivery.write",
                        "request_schema": "fixture_material_capability_request_v0",
                        "result_schema": "fixture_material_capability_result_v0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "extension.toml"
    manifest.write_text(
        f"""\
schema_version = "loopx_extension_manifest_v0"
id = "fixture-material-provider"
version = "0.1.0"
requires_loopx_api = ">=1,<2"
permissions = ["fixture.delivery.write"]

[runtime]
protocol = "fixture_material_provider_v0"
entrypoint = {json.dumps(str(provider))}
doctor_args = ["--doctor"]
required_permissions = ["fixture.delivery.write"]
timeout_seconds = 5

[[provides]]
id = "fixture-material-delivery"
kind = "requirement_delivery"
title = "Fixture material delivery"
status = "active-preview"
user_value = "Publish one synthetic external change."
next_real_step = "Reconcile one governed provider run."
real_world_anchor = "A synthetic external service."
entry_command = "loopx capability invoke"
visibility = "public"
integration_profile = "profile.json"
""",
        encoding="utf-8",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    active_state = tmp_path / "ACTIVE_GOAL_STATE.md"
    active_state.write_text(
        "---\nstatus: active\n---\n\n# Active Goal State\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "loopx_registry_v1",
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "fixture-goal",
                        "title": "Fixture Goal",
                        "repo": str(tmp_path),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": ["fixture-agent"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    bind_external_capability_to_goal(
        registry_path=registry,
        state_file=state_file,
        goal_id="fixture-goal",
        capability_id="fixture-material-delivery",
        operations=["publish"],
        execute=True,
    )
    return state_file, registry, call_log


def _identity(
    *, turn_instance_id: str = "fixture-material-turn-1"
) -> SettlementIdentity:
    return SettlementIdentity(
        goal_id="fixture-goal",
        agent_id="fixture-agent",
        todo_id="todo_fixturematerial1",
        turn_instance_id=turn_instance_id,
    )


def _admission(identity: SettlementIdentity) -> dict[str, object]:
    return {
        "ok": True,
        "should_run": True,
        "selected_todo": {
            "todo_id": identity.todo_id,
            "role": "agent",
            "status": "open",
            "action_kind": "fixture_material_delivery",
            "target_key": "fixture-material:delivery-1",
        },
        "heartbeat_receipt": {"settlement_identity": identity.as_dict()},
    }


def _start_arguments(tmp_path: Path) -> dict[str, object]:
    state_file, registry, _call_log = _installed_material_extension(tmp_path)
    identity = _identity()
    return {
        "state_file": state_file,
        "run_dir": tmp_path / "runs",
        "registry_path": registry,
        "goal_id": identity.goal_id,
        "agent_id": identity.agent_id,
        "todo_id": identity.todo_id,
        "turn_instance_id": identity.turn_instance_id,
        "capability_id": "fixture-material-delivery",
        "operation": "publish",
        "provider_input": {
            "context_refs": [
                {
                    "kind": "synthetic-requirement",
                    "ref": "REQ-1",
                    "digest": "sha256:synthetic-context",
                }
            ],
            "input": {
                "requirement_key": "REQ-1",
                "confidence": 1.0,
                "label": "材料",
            },
        },
        "admission": _admission(identity),
    }


def _record_runtime_operations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    operations: list[str] = []
    runtime_result = governed_execution.effect_runtime_result

    def record(
        operation: str,
        payload: Mapping[str, object],
    ) -> object:
        operations.append(operation)
        return runtime_result(operation, payload)

    monkeypatch.setattr(governed_execution, "effect_runtime_result", record)
    return operations


def test_material_start_is_previewable_and_provider_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _start_arguments(tmp_path)
    call_log = tmp_path / "provider-calls.txt"
    runtime_operations = _record_runtime_operations(monkeypatch)

    preview = start_governed_external_capability(**arguments)
    assert runtime_operations == ["governed_capability.validate_result"]
    runtime_operations.clear()
    started = start_governed_external_capability(**arguments, execute=True)
    assert runtime_operations == [
        "governed_capability.validate_result",
        "governed_capability.validate_result",
    ]
    runtime_operations.clear()
    replay = start_governed_external_capability(**arguments, execute=True)
    assert runtime_operations == ["governed_capability.validate_result"]

    assert preview["status"] == "ready"
    assert preview["executed"] is False
    assert started["status"] == "running"
    assert started["effects"]["loopx_transitions_written"] is True
    assert started["transition_receipts"][0]["action"] == "created"
    assert replay["invocation_id"] == started["invocation_id"]
    assert replay["transition_receipts"] == started["transition_receipts"]
    assert call_log.read_text(encoding="utf-8").splitlines() == ["start"]
    monitors = list_goal_todos(
        registry_path=Path(arguments["registry_path"]),
        goal_id="fixture-goal",
        role="agent",
    )["todos"]
    assert len(monitors) == 1
    assert monitors[0]["capability_binding_ref"] == "fixture:material-run"
    assert monitors[0]["status"] == "open"
    journal = Path(arguments["run_dir"]) / f"{started['invocation_id']}.json"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    journal_value = json.loads(journal.read_text(encoding="utf-8"))
    assert started["provider_result_digest"] == governed_execution._canonical_digest(
        journal_value["provider_result"]
    )


def test_legacy_provider_result_adapter_remains_available() -> None:
    result = {
        "schema_version": "fixture_material_capability_result_v0",
        "invocation_id": "capability-legacy",
        "status": "no_change",
        "observations": [],
        "domain_state_mutations": [],
        "domain_transition_receipts": [],
        "transition_proposals": [],
        "effect_receipt": {
            "schema_version": "loopx_external_effect_receipt_v0",
            "invocation_id": "capability-legacy",
            "idempotency_key": "legacy-effect",
            "status": "no_change",
            "external_ref": "legacy-ref",
            "evidence_digest": "sha256:legacy-evidence",
        },
        "follow_up": {},
    }

    assert (
        validate_governed_capability_result(
            result,
            invocation_id="capability-legacy",
            effect_id="legacy-effect",
            operation={
                "result_schema": "fixture_material_capability_result_v0",
                "effect_class": "external_write",
            },
        )
        == result
    )


def test_material_start_recovers_monitor_after_pre_receipt_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _start_arguments(tmp_path)
    original_write_journal = governed_execution._write_journal
    write_count = 0

    def crash_before_transition_receipt(
        path: Path,
        payload: Mapping[str, object],
    ) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 3:
            raise RuntimeError("synthetic checkpoint crash")
        original_write_journal(path, payload)

    monkeypatch.setattr(
        governed_execution,
        "_write_journal",
        crash_before_transition_receipt,
    )
    with pytest.raises(RuntimeError, match="synthetic checkpoint crash"):
        start_governed_external_capability(**arguments, execute=True)

    monkeypatch.setattr(
        governed_execution,
        "_write_journal",
        original_write_journal,
    )
    replay = start_governed_external_capability(**arguments, execute=True)

    assert replay["status"] == "running"
    assert replay["transition_receipts"][0]["action"] in {
        "unchanged",
        "updated",
    }
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start"]
    monitors = list_goal_todos(
        registry_path=Path(arguments["registry_path"]),
        goal_id="fixture-goal",
        role="agent",
    )["todos"]
    assert len(monitors) == 1


def test_material_operation_remains_unavailable_through_direct_invoke(
    tmp_path: Path,
) -> None:
    arguments = _start_arguments(tmp_path)

    with pytest.raises(ValueError, match="requires a governed Turn adapter"):
        invoke_external_capability(
            state_file=arguments["state_file"],
            registry_path=arguments["registry_path"],
            goal_id="fixture-goal",
            capability_id="fixture-material-delivery",
            operation="publish",
            provider_input=arguments["provider_input"],
            execute=True,
        )


def test_material_reconcile_writes_receipt_before_spending_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    identity = _identity()
    calls: list[str] = []
    runtime_operations = _record_runtime_operations(monkeypatch)

    def writeback(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("writeback")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
            "effect_receipt_digest": context["effect_receipt_digest"],
        }

    def spend(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("spend")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
        }

    committed = reconcile_governed_external_capability(
        run_dir=arguments["run_dir"],
        invocation_id=str(started["invocation_id"]),
        writeback=writeback,
        spend=spend,
    )
    assert runtime_operations == [
        "governed_capability.validate_result",
        "governed_capability.validate_result",
        "governed_capability.validate_settlement_callback",
        "governed_capability.validate_settlement_callback",
    ]
    runtime_operations.clear()
    replay = reconcile_governed_external_capability(
        run_dir=arguments["run_dir"],
        invocation_id=str(started["invocation_id"]),
        writeback=writeback,
        spend=spend,
    )
    assert runtime_operations == ["governed_capability.validate_result"]

    assert committed["status"] == "committed"
    assert committed["effect_id"] == identity.effect_id
    assert committed["effects"] == {
        "provider_invoked": True,
        "external_write_observed": True,
        "loopx_transitions_written": True,
        "loopx_state_written": True,
        "quota_spent": True,
    }
    assert [item["kind"] for item in committed["transition_receipts"]] == [
        "continuous_monitor_upsert",
        "continuous_monitor_complete",
    ]
    assert calls == ["writeback", "spend"]
    assert replay["status"] == "committed"
    assert calls == ["writeback", "spend"]
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start", "reconcile"]
    monitors = list_goal_todos(
        registry_path=Path(arguments["registry_path"]),
        goal_id="fixture-goal",
        role="agent",
    )["todos"]
    assert len(monitors) == 1
    assert monitors[0]["status"] == "done"


def test_material_start_rejects_a_different_selected_turn(tmp_path: Path) -> None:
    arguments = _start_arguments(tmp_path)
    arguments["admission"] = _admission(
        _identity(turn_instance_id="different-material-turn")
    )

    with pytest.raises(ValueError, match="does not match the invocation"):
        start_governed_external_capability(**arguments, execute=True)


def test_material_start_rejects_an_unrelated_selected_todo_action(
    tmp_path: Path,
) -> None:
    arguments = _start_arguments(tmp_path)
    admission = _admission(_identity())
    selected_todo = admission["selected_todo"]
    assert isinstance(selected_todo, dict)
    selected_todo["action_kind"] = "different_material_action"
    arguments["admission"] = admission

    with pytest.raises(
        EffectRuntimeRejected,
        match="not authorized by selected_todo",
    ):
        start_governed_external_capability(**arguments, execute=True)

    assert not (tmp_path / "provider-calls.txt").exists()


def test_material_start_requires_a_runnable_admission(tmp_path: Path) -> None:
    arguments = _start_arguments(tmp_path)
    arguments["admission"] = {
        **arguments["admission"],
        "should_run": False,
    }

    with pytest.raises(ValueError, match="requires should_run=true"):
        start_governed_external_capability(**arguments, execute=True)

    assert not (tmp_path / "provider-calls.txt").exists()


def test_material_start_recovers_an_existing_journal_without_new_run_authority(
    tmp_path: Path,
) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    arguments["admission"] = {
        **arguments["admission"],
        "should_run": False,
    }
    arguments["admission"].pop("selected_todo")

    replay = start_governed_external_capability(**arguments, execute=True)

    assert replay["invocation_id"] == started["invocation_id"]
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start"]


@pytest.mark.parametrize(
    "admission_update, error",
    [
        ({"should_run": False}, "requires should_run=true"),
        ({"selected_todo": None}, "selected_todo must be an object"),
        (
            {
                "selected_todo": {
                    "todo_id": "todo_fixturematerial1",
                    "role": "agent",
                    "status": "open",
                    "action_kind": "different_material_action",
                    "target_key": "fixture-material:delivery-1",
                }
            },
            "not authorized by selected_todo action_kind",
        ),
    ],
)
def test_material_start_requires_current_authority_before_recovering_transitions(
    tmp_path: Path,
    admission_update: dict[str, object],
    error: str,
) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    journal_path = tmp_path / "runs" / f"{started['invocation_id']}.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["transition_receipts"] = []
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    arguments["admission"] = {
        **arguments["admission"],
        **admission_update,
    }
    before = journal_path.read_bytes()

    with pytest.raises((ValueError, EffectRuntimeRejected), match=error):
        start_governed_external_capability(**arguments, execute=True)

    assert journal_path.read_bytes() == before
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start"]


def test_material_start_recovers_unsettled_transitions_with_current_authority(
    tmp_path: Path,
) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    journal_path = tmp_path / "runs" / f"{started['invocation_id']}.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["transition_receipts"] = []
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    replay = start_governed_external_capability(**arguments, execute=True)

    assert len(replay["transition_receipts"]) == 1
    assert replay["transition_receipts"][0]["proposal_id"] == (
        "fixture_monitor_upsert_1"
    )
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start"]


def test_material_turn_rejects_a_second_distinct_invocation(tmp_path: Path) -> None:
    arguments = _start_arguments(tmp_path)
    first = start_governed_external_capability(**arguments, execute=True)
    second_arguments = {
        **arguments,
        "provider_input": {
            "context_refs": [
                {
                    "kind": "synthetic-requirement",
                    "ref": "REQ-2",
                    "digest": "sha256:synthetic-context-2",
                }
            ],
            "input": {"requirement_key": "REQ-2"},
        },
    }
    second_preview = start_governed_external_capability(**second_arguments)

    assert second_preview["invocation_id"] == first["invocation_id"]
    assert second_preview["request_digest"] != first["request_digest"]
    with pytest.raises(ValueError, match="replay does not match"):
        start_governed_external_capability(**second_arguments, execute=True)
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start"]


def test_material_settlement_fails_closed_without_effect_receipt_writeback(
    tmp_path: Path,
) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    calls: list[str] = []

    def incomplete_writeback(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("writeback")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
        }

    def spend(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("spend")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
        }

    failed = reconcile_governed_external_capability(
        run_dir=arguments["run_dir"],
        invocation_id=str(started["invocation_id"]),
        writeback=incomplete_writeback,
        spend=spend,
    )

    assert failed["ok"] is False
    assert failed["status"] == "settlement_failed"
    assert calls == ["writeback"]
    assert [item["kind"] for item in failed["transition_receipts"]] == [
        "continuous_monitor_upsert"
    ]
    monitors = list_goal_todos(
        registry_path=Path(arguments["registry_path"]),
        goal_id="fixture-goal",
        role="agent",
    )["todos"]
    assert len(monitors) == 1
    assert monitors[0]["status"] == "open"

    def complete_writeback(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("writeback")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
            "effect_receipt_digest": context["effect_receipt_digest"],
        }

    committed = reconcile_governed_external_capability(
        run_dir=arguments["run_dir"],
        invocation_id=str(started["invocation_id"]),
        writeback=complete_writeback,
        spend=spend,
    )

    assert committed["status"] == "committed"
    assert calls == ["writeback", "writeback", "spend"]
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start", "reconcile"]
    monitors = list_goal_todos(
        registry_path=Path(arguments["registry_path"]),
        goal_id="fixture-goal",
        role="agent",
    )["todos"]
    assert monitors[0]["status"] == "done"


def test_material_reconcile_rejects_tampered_journal_request(tmp_path: Path) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    journal_path = Path(arguments["run_dir"]) / f"{started['invocation_id']}.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["request"]["input"]["requirement_key"] = "tampered"
    journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="request digest is invalid"):
        reconcile_governed_external_capability(
            run_dir=arguments["run_dir"],
            invocation_id=str(started["invocation_id"]),
            writeback=lambda _context: {},
            spend=lambda _context: {},
        )


def test_material_reconcile_resumes_at_spend_without_repeating_writeback(
    tmp_path: Path,
) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    calls: list[str] = []
    spend_attempts = 0

    def writeback(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("writeback")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
            "effect_receipt_digest": context["effect_receipt_digest"],
        }

    def spend(context: Mapping[str, object]) -> dict[str, object]:
        nonlocal spend_attempts
        calls.append("spend")
        spend_attempts += 1
        return {
            "ok": spend_attempts > 1,
            "appended": spend_attempts > 1,
            "settlement_identity": context["settlement_identity"],
            "reason": "synthetic first-attempt failure",
        }

    first = reconcile_governed_external_capability(
        run_dir=arguments["run_dir"],
        invocation_id=str(started["invocation_id"]),
        writeback=writeback,
        spend=spend,
    )
    monitors_after_failure = list_goal_todos(
        registry_path=Path(arguments["registry_path"]),
        goal_id="fixture-goal",
        role="agent",
    )["todos"]
    second = reconcile_governed_external_capability(
        run_dir=arguments["run_dir"],
        invocation_id=str(started["invocation_id"]),
        writeback=writeback,
        spend=spend,
    )

    assert first["status"] == "settlement_failed"
    assert monitors_after_failure[0]["status"] == "open"
    assert second["status"] == "committed"
    assert calls == ["writeback", "spend", "spend"]
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start", "reconcile"]


def test_material_reconcile_recovers_completion_after_pre_receipt_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _start_arguments(tmp_path)
    started = start_governed_external_capability(**arguments, execute=True)
    original_write_journal = governed_execution._write_journal
    calls: list[str] = []
    crashed = False

    def writeback(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("writeback")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
            "effect_receipt_digest": context["effect_receipt_digest"],
        }

    def spend(context: Mapping[str, object]) -> dict[str, object]:
        calls.append("spend")
        return {
            "ok": True,
            "appended": True,
            "settlement_identity": context["settlement_identity"],
        }

    def crash_before_completion_receipt(
        path: Path,
        payload: Mapping[str, object],
    ) -> None:
        nonlocal crashed
        raw_receipts = payload.get("transition_receipts")
        receipts = raw_receipts if isinstance(raw_receipts, list) else []
        if not crashed and any(
            isinstance(item, Mapping)
            and item.get("kind") == "continuous_monitor_complete"
            for item in receipts
        ):
            crashed = True
            raise RuntimeError("synthetic completion checkpoint crash")
        original_write_journal(path, payload)

    monkeypatch.setattr(
        governed_execution,
        "_write_journal",
        crash_before_completion_receipt,
    )
    with pytest.raises(RuntimeError, match="synthetic completion checkpoint crash"):
        reconcile_governed_external_capability(
            run_dir=arguments["run_dir"],
            invocation_id=str(started["invocation_id"]),
            writeback=writeback,
            spend=spend,
        )

    monkeypatch.setattr(
        governed_execution,
        "_write_journal",
        original_write_journal,
    )
    replay = reconcile_governed_external_capability(
        run_dir=arguments["run_dir"],
        invocation_id=str(started["invocation_id"]),
        writeback=writeback,
        spend=spend,
    )

    assert replay["status"] == "committed"
    assert calls == ["writeback", "spend"]
    assert replay["transition_receipts"][-1]["action"] == "replayed"
    assert (tmp_path / "provider-calls.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["start", "reconcile"]
    monitors = list_goal_todos(
        registry_path=Path(arguments["registry_path"]),
        goal_id="fixture-goal",
        role="agent",
    )["todos"]
    assert len(monitors) == 1
    assert monitors[0]["status"] == "done"
