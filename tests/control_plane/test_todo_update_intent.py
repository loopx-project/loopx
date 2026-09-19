from loopx.control_plane.todos.monitor_metadata import MonitorPollObservation
from loopx.control_plane.todos.update_intent import (
    build_canonical_update_intent,
    canonical_update_is_supported,
)


def test_update_intent_keeps_explicit_clears_and_empty_scalars() -> None:
    intent = build_canonical_update_intent(
        reason="",
        required_capabilities=[],
        clear_claim=True,
        clear_global_gate=True,
    )

    assert intent == {
        "reason": "",
        "required_capabilities": [],
        "clear_global_gate": True,
        "clear_claim": True,
    }


def test_update_route_promotes_declarative_decision_metadata() -> None:
    supported = build_canonical_update_intent(
        action_kind="publish",
        task_domain="delivery",
        task_repository="git:github.com/example/project",
        required_write_scopes=["src/**"],
    )
    assert canonical_update_is_supported(
        text=None,
        note=None,
        intent=supported,
        monitor_metadata=None,
    )

    # Declarative scope metadata now crosses the same typed planning
    # transaction; terminal outcomes remain on their effect-owned path.
    governance = build_canonical_update_intent(
        decision_scope={"kind": "write_scope", "granularity": "action", "scope_key": "release"},
    )
    assert canonical_update_is_supported(
        text=None,
        note=None,
        intent=governance,
        monitor_metadata=None,
    )


def test_completion_and_observation_intents_route_to_their_canonical_owners() -> None:
    intent = build_canonical_update_intent(reason="ordinary")
    assert canonical_update_is_supported(
        text=None,
        note=None,
        intent=intent,
        monitor_metadata=MonitorPollObservation(generated_at="2030-01-01T00:00:00Z", result_hash="observed", material_change=True),
    )
    assert canonical_update_is_supported(
        text=None, note=None, intent=build_canonical_update_intent(status="done"), monitor_metadata=None,
    )
    assert not canonical_update_is_supported(
        text=None,
        note="   ",
        intent={},
        monitor_metadata=None,
    )


def test_configuration_field_admission_belongs_to_the_typed_transaction() -> None:
    # Even malformed/unknown fields must reach the rejecting TS decoder;
    # they cannot divert a promoted request into a Markdown business writer.
    assert canonical_update_is_supported(text=None, note=None, intent={},
        monitor_metadata={"material_change_generation": 99})
