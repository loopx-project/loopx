"""Steward expression replaces templates without replacing effect authority."""

import pytest

from loopx.extensions.lark.goal_channel_runtime import notify_lark_goal_channel_gate
from loopx.extensions.lark.goal_channel_blocked_notice import deliver_blocked_notices
from loopx.extensions.lark.goal_channel_contracts import read_goal_channel_binding, write_goal_channel_binding
from loopx.extensions.lark.goal_channel_notification import _goal_notification_row
from tests.extensions.test_lark_goal_channel import GOAL_ID, _registry, _gate_test_binding, _fake_runner


def request():
    return {"todo_id": "todo_release", "role": "user", "task_class": "user_gate",
            "status": "blocked", "done": False, "updated_at": "2026-10-02T00:00:00Z", "text": "Decide whether to publish the reviewed release.",
            "reason": "Only publication awaits approval; independent verification can continue."}


def quota():
    return {"state": "operator_gate", "notify_user_on_gate": True,
            "user_todo_summary": {"gate_open_items": [request()]},
            "request_snapshot": {"goal_id": GOAL_ID, "items": [request()]}}


def test_steward_gate_covers_same_blocker_once(tmp_path):
    path = _gate_test_binding(tmp_path)
    binding = read_goal_channel_binding(path)
    binding["bindings"][GOAL_ID]["automation"] = {"blocked_notice_auto_notify_enabled": True}
    write_goal_channel_binding(path, binding)
    calls, seen = [], []
    message = "建议先核对发布证据，再决定是否发布（todo_release）。独立验证仍可继续；批准仅覆盖发布。"
    def synthesize(facts):
        seen.append(facts)
        return message
    runner = _fake_runner(calls)
    result = notify_lark_goal_channel_gate(registry=_registry(tmp_path), goal_id=GOAL_ID,
        binding_path=path, quota_packet=quota(), execute=True, runner=runner, synthesizer=synthesize)
    assert result["status"] == "sent_verified"
    assert len(seen) == 1 and seen[0]["blockers"][0]["owner_must_act"] is True
    result = deliver_blocked_notices(goal_id=GOAL_ID, binding_path=path,
        status={"attention_queue": {"items": [{"goal_id": GOAL_ID,
                "user_todos": {"items": [request()]}}]}}, quota_packet={},
        external_sink_delivery_authorized=True, runner=runner, synthesizer=synthesize)
    assert result["delivered_count"] == 1 and result["pending_count"] == 0
    sends = [args for args in calls if "+messages-send" in args]
    assert len(sends) == 1 and len(seen) == 1
    assert sends[0][sends[0].index("--text") + 1] == message


def test_generation_failure_has_no_send_and_recovers(tmp_path):
    path = _gate_test_binding(tmp_path)
    calls = []
    def fail(_):
        raise RuntimeError("synthetic model unavailable")
    def notify(synthesizer):
        return notify_lark_goal_channel_gate(registry=_registry(tmp_path), goal_id=GOAL_ID,
            binding_path=path, quota_packet=quota(), execute=True,
            runner=_fake_runner(calls), synthesizer=synthesizer)
    failed = notify(fail)
    assert failed["blocker"] == "steward_notice_unavailable"
    assert failed["external_write_performed"] is False
    assert not any("+messages-send" in args for args in calls)
    projection = _goal_notification_row(binding_path=path,
        binding_payload=read_goal_channel_binding(path), goal_id=GOAL_ID)
    assert projection["steward_notice_delivery"] == {"pending_count": 1, "failed_count": 1}
    assert notify(lambda _: "发布仍需你的决定（todo_release）；验证可以继续。 ")["status"] == "sent_verified"
    projection = _goal_notification_row(binding_path=path,
        binding_payload=read_goal_channel_binding(path), goal_id=GOAL_ID)
    assert projection["steward_notice_delivery"] == {"pending_count": 0, "failed_count": 0}


@pytest.mark.parametrize("complete_reference", [True, False])
def test_standalone_owner_blocker_preserves_decision_without_gate_sender(tmp_path, complete_reference):
    path = _gate_test_binding(tmp_path)
    payload = read_goal_channel_binding(path)
    payload["bindings"][GOAL_ID]["automation"] = {
        "human_gate_auto_notify_enabled": False, "blocked_notice_auto_notify_enabled": True}
    write_goal_channel_binding(path, payload)
    calls, seen = [], []
    def synthesize(facts):
        seen.append(facts)
        return "Review the publication evidence before deciding" + (
            " (todo_release)." if complete_reference else ".")
    result = deliver_blocked_notices(goal_id=GOAL_ID, binding_path=path,
        status={"attention_queue": {"items": [{"goal_id": GOAL_ID,
                "user_todos": {"items": [request()]}}]}}, quota_packet={},
        external_sink_delivery_authorized=True, runner=_fake_runner(calls), synthesizer=synthesize)
    decision = seen[0]["decision_notice"]["items"][0]
    assert decision == {"request_id": "todo_release", "text": request()["text"],
                        "reason": request()["reason"], "evidence": ""}
    assert bool(result["readback_verified"]) is complete_reference
    assert any("+messages-send" in args for args in calls) is complete_reference
    if not complete_reference:
        assert result["blocker"] == "steward_notice_unavailable"


def test_prepared_body_survives_provider_retry(tmp_path):
    path = _gate_test_binding(tmp_path)
    calls, generated = [], []
    runner = _fake_runner(calls)
    def failed_send(args, cwd, timeout):
        if "+messages-send" in args:
            calls.append(args)
            return {"returncode": 1, "stdout": "rejected"}
        return runner(args, cwd, timeout)
    def synthesize(facts):
        generated.append(facts)
        return "发布（todo_release）需要你的决定；独立验证可以继续。"
    def notify(send):
        return notify_lark_goal_channel_gate(registry=_registry(tmp_path), goal_id=GOAL_ID,
            binding_path=path, quota_packet=quota(), execute=True,
            runner=send, synthesizer=synthesize)
    assert notify(failed_send)["status"] == "failed"
    assert notify(runner)["status"] == "sent_verified"
    assert len(generated) == 1
    sends = [args for args in calls if "+messages-send" in args]
    assert sends[0][sends[0].index("--text") + 1] == sends[1][sends[1].index("--text") + 1]
    assert sends[0][sends[0].index("--idempotency-key") + 1] == sends[1][sends[1].index("--idempotency-key") + 1]


def test_missing_reference_or_private_generated_content_is_rejected(tmp_path):
    path = _gate_test_binding(tmp_path)
    for text in ("Please approve this release", "todo_release /tmp/private-review.txt"):
        calls = []
        result = notify_lark_goal_channel_gate(registry=_registry(tmp_path), goal_id=GOAL_ID,
            binding_path=path, quota_packet=quota(), execute=True,
            runner=_fake_runner(calls), synthesizer=lambda _: text)
        assert result["blocker"] == "steward_notice_unavailable"
        assert not any("+messages-send" in args for args in calls)
    receipts = read_goal_channel_binding(path)["bindings"][GOAL_ID]["receipts"]
    assert len(receipts) == 1 and not next(iter(receipts.values())).get("delivery_text")


def test_preview_does_not_generate_or_send(tmp_path):
    calls = []
    def unexpected(_):
        raise AssertionError("preview must not spend model budget")
    result = notify_lark_goal_channel_gate(registry=_registry(tmp_path), goal_id=GOAL_ID,
        binding_path=_gate_test_binding(tmp_path), quota_packet=quota(),
        runner=_fake_runner(calls), synthesizer=unexpected)
    assert result["status"] == "preview_ready" and calls == []


def test_legacy_ambiguous_blocker_attempt_keeps_its_body_identity(tmp_path):
    from tests.extensions.test_lark_goal_channel_blocked_notice import (
        GOAL_ID as blocked_goal, _binding, _runner, _status,
    )
    path = tmp_path / "goal-channel.json"
    _binding(path)
    calls = []
    def send(runner, synthesizer):
        return deliver_blocked_notices(goal_id=blocked_goal, binding_path=path,
            status=_status(), quota_packet={}, external_sink_delivery_authorized=True,
            runner=runner, synthesizer=synthesizer)
    assert send(_runner(calls, fail_send=True), lambda _: "Required input is missing.")["pending_count"] == 1
    payload = read_goal_channel_binding(path)
    next(iter(payload["bindings"][blocked_goal]["receipts"].values())).pop("delivery_text")
    write_goal_channel_binding(path, payload)
    def unexpected(_):
        raise AssertionError("An old attempted provider key cannot acquire a new body")
    calls.clear()
    for _ in range(2):
        result = send(_runner(calls), unexpected)
        assert result["blocker"] == "notification_body_unavailable"
    assert not any("+messages-send" in args for args in calls)
