from loopx.control_plane.todos.update_intent import build_canonical_update_intent


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
