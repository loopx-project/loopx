from __future__ import annotations

from loopx.control_plane.capability_hooks import (
    TURN_START_HOOK_RESULT_SCHEMA_VERSION,
    TurnStartHookRegistration,
    dispatch_turn_start_hooks,
)


def _result(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
        "hook_id": "operator_inbox.turn_start_sync",
        "capability_id": "operator-inbox",
        "phase": "turn_start",
        "status": "observed",
        "observation_count": 1,
        "agent_read_required": True,
        "external_reads_performed": True,
        "external_writes_performed": False,
        "local_private_state_mutated": True,
        "private_content_returned": False,
        "provider_payload_returned": False,
        "error_code": None,
        **overrides,
    }


def _hook(producer: object) -> TurnStartHookRegistration:
    return TurnStartHookRegistration(
        hook_id="operator_inbox.turn_start_sync",
        capability_id="operator-inbox",
        requested_read_scope=("provider_history",),
        requested_write_scope=("owner_private_inbox", "owner_private_cursor"),
        producer=producer,  # type: ignore[arg-type]
        required_read={
            "kind": "operator_inbox",
            "command": "loopx inbox drain --goal-id fixture",
            "reason": "read newly synchronized operator evidence",
            "ordering": "before_work",
        },
    )


def test_turn_start_hook_returns_only_validated_agent_read_obligation() -> None:
    dispatch = dispatch_turn_start_hooks([_hook(lambda: _result())])

    assert dispatch["failures"] == []
    assert dispatch["invoked_count"] == 1
    assert dispatch["results"][0]["agent_read_required"] is True
    assert dispatch["results"][0]["observation_count"] == 1
    assert "content" not in dispatch["results"][0]
    assert dispatch["required_reads"] == [
        {
            "kind": "operator_inbox",
            "command": "loopx inbox drain --goal-id fixture",
            "reason": "read newly synchronized operator evidence",
            "ordering": "before_work",
            "source": "turn_start_capability_hook",
            "hook_id": "operator_inbox.turn_start_sync",
            "capability_id": "operator-inbox",
        }
    ]


def test_provider_shape_mismatch_is_not_misclassified_as_empty() -> None:
    malformed = _result()
    malformed.pop("agent_read_required")

    dispatch = dispatch_turn_start_hooks([_hook(lambda: malformed)])

    assert dispatch["results"] == []
    # Validation happens after the provider was invoked. An invalid response
    # cannot certify that its declared local effects did not occur.
    assert dispatch["uncertain_local_effects"] is True
    from loopx.capabilities.turn_start import attach_turn_start_effects
    from loopx.cli_commands.turn_rendering import render_loopx_turn_plan_markdown

    payload = {"ok": True, "effects": {"state_written": False}, "boundary": {"read_only": True}}
    attach_turn_start_effects(payload, dispatch)
    assert payload["effects"]["state_written"] is None
    assert payload["boundary"]["read_only"] is False
    assert "side_effects: registered capability observation" in render_loopx_turn_plan_markdown(payload)
    assert dispatch["failures"] == [
        {
            "hook_id": "operator_inbox.turn_start_sync",
            "capability_id": "operator-inbox",
            "error_code": "contract_rejected",
        }
    ]


def test_turn_start_hooks_are_single_flight_by_identity() -> None:
    hook = _hook(lambda: _result())

    dispatch = dispatch_turn_start_hooks([hook, hook])

    assert dispatch["invoked_count"] == 1
    assert dispatch["failures"][0]["error_code"] == "duplicate_hook_id"


def test_turn_start_external_write_requires_reaction_scope() -> None:
    rejected = dispatch_turn_start_hooks(
        [_hook(lambda: _result(external_writes_performed=True))]
    )

    assert rejected["results"] == []
    assert rejected["failures"][0]["error_code"] == "contract_rejected"

    admitted = TurnStartHookRegistration(
        hook_id="operator_inbox.turn_start_sync",
        capability_id="operator-inbox",
        requested_read_scope=("provider_history",),
        requested_write_scope=(
            "owner_private_inbox",
            "owner_private_cursor",
            "provider_message_reaction",
        ),
        producer=lambda: _result(external_writes_performed=True),
        required_read={
            "kind": "operator_inbox",
            "command": "loopx inbox drain --goal-id fixture",
            "reason": "read newly synchronized operator evidence",
            "ordering": "before_work",
        },
    )
    dispatch = dispatch_turn_start_hooks([admitted])

    assert dispatch["failures"] == []
    assert dispatch["results"][0]["external_writes_performed"] is True
