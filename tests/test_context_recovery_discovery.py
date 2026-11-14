"""Discovery amortizes history IO; execution still needs current authority."""
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from test_native_steward_private import steward, finish  # noqa: F401
from loopx.capabilities.native_chat import external_conversations
from loopx.extensions.lark.conversation_identity import identity_ref


@pytest.fixture
def failed_source(steward):  # noqa: F811
    store, runtime, provider, transport, binding, capture, _ = steward
    fake = Path(runtime.codex_bin)
    fake.write_text(fake.read_text().replace('"codexErrorInfo": "cyberPolicy"',
        '"codexErrorInfo": "contextWindowExceeded"').replace(
        '            print(json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "durable-thread", "turnId": active_turn, "delta": "Partial answer."}}), flush=True)\n', ''))
    event = provider.event("steward-app", "discovery-history", "typed terminal failure")
    transport.admit("steward-app", event)
    ref = identity_ref(binding["provider_ref"], event["message_id"])
    row = transport.core.read_request(ref)
    assert finish(runtime, row)["status"] == "failed"
    store.update_session(row["session_id"], manager_context_version=20)
    assert transport.reconcile() == 1
    return store, runtime, provider, transport, binding, capture, ref, row


def test_one_discovery_pass_reuses_queue_history_and_controls(failed_source, monkeypatch):
    store, _, provider, transport, _, capture, ref, row = failed_source
    # Model a retained pre-dispatch failure after its existing backoff. Discovery
    # is only a hint: the original native event sequence still owns admission.
    store.update_turn(row["session_id"], row["turn_id"], upstream_turn_id=None,
        error_code="manager_authorization_unavailable", delta_count=0, response=None)
    real_datetime = datetime
    class AfterBackoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(minutes=2)
    monkeypatch.setattr(external_conversations, "datetime", AfterBackoff)
    counts = Counter()
    for owner, name in [(store, "queued_turns"), (store, "messages"), (transport.core, "source_controls")]:
        original = getattr(owner, name)
        def counted(*args, _name=name, _read=original, **kwargs):
            counts[_name] += 1
            return _read(*args, **kwargs)
        monkeypatch.setattr(owner, name, counted)
    before_calls, before_requests = len(provider.calls), capture.read_bytes()
    observe = transport.core.context_recovery_observer()
    assert [observe(ref) for _ in range(4)] == [True] * 4
    assert counts == {"queued_turns": 1, "messages": 1, "source_controls": 1}
    # The next pass rereads rather than retaining a negative/positive cache.
    assert transport.core.context_recovery_observer()(ref)
    assert counts == {"queued_turns": 2, "messages": 2, "source_controls": 2}
    assert len(provider.calls) == before_calls and capture.read_bytes() == before_requests


@pytest.mark.parametrize("change", ["queued-input", "revoked", "owner-unavailable"])
def test_discovery_hint_never_authorizes_recovery(failed_source, monkeypatch, change):
    store, _, provider, transport, binding, capture, ref, row = failed_source
    observe = transport.core.context_recovery_observer()
    assert observe(ref)
    original = store._turn_path(row["session_id"], row["turn_id"]).read_bytes()
    before = capture.read_bytes()
    if change == "queued-input":
        store.create_queued_turn(row["session_id"], client_turn_id="new-original-input",
            message="Resolve the newer input first", origin="web")
        queued = store.queued_turns
        observed = []
        deny_after_read = False
        def current_queue(session_id):
            result = queued(session_id)
            observed.append(bool(result))
            if deny_after_read:
                provider.owner_ready = False
            return result
        monkeypatch.setattr(store, "queued_turns", current_queue)
        transport.core.context_recovery_observer()(ref)
        assert observed == [True], "The next observation reads the new queue"
        # A candidate is not an eligibility verdict. Normal admission must read
        # the new queue itself, then reject authority lost during that read.
        deny_after_read = True
        with pytest.raises(ValueError):
            transport.core.recover_failed_request(ref,
                source_observation=lambda _selected: pytest.fail("source IO after denied authority"))
        assert observed == [True, True]
    else:
        if change == "revoked":
            transport.bindings.disconnect(binding["binding_id"],
                expected_revision=transport.bindings.read()["revision"])
            assert not observe(ref), "Even the same discovery pass rereads the binding"
        else:
            provider.owner_ready = False
        with pytest.raises(ValueError):
            transport.core.recover_failed_request(ref,
                source_observation=lambda _selected: pytest.fail("source IO after denied authority"))
    assert capture.read_bytes() == before
    assert store._turn_path(row["session_id"], row["turn_id"]).read_bytes() == original


def test_unavailable_queue_is_not_cached_as_empty(failed_source, monkeypatch):
    store, _, _, transport, _, _, ref, _ = failed_source
    original = store.queued_turns
    calls = 0
    def flaky(session_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic unavailable queue")
        return original(session_id)
    monkeypatch.setattr(store, "queued_turns", flaky)
    observe = transport.core.context_recovery_observer()
    assert not observe(ref)
    assert observe(ref)
    assert calls == 2


def test_admission_rejects_discovery_observations(failed_source):
    _, _, _, transport, _, _, _, row = failed_source
    reads = external_conversations._RecoveryDiscoveryReads(transport.core)
    with pytest.raises(ValueError, match="cannot authorize recovery"):
        transport.core._context_recovery_plan(row, discovery_reads=reads)


def test_discovery_facts_do_not_cross_sessions(failed_source):
    store, _, _, transport, _, _, _, row = failed_source
    second = store.create_session(goal_id="separate-synthetic-goal", agent_id="codex", channel_id="separate-source",
        adapter_kind="codex_app_server", upstream_thread_id="separate-synthetic-thread")
    queued, created = store.create_queued_turn(second["session_id"], client_turn_id="other-source-input",
        message="Only the second source has queued input", origin="web")
    assert created
    reads = external_conversations._RecoveryDiscoveryReads(transport.core)
    assert not reads.queued(row["session_id"])
    assert reads.queued(second["session_id"])
    assert {message["turn_id"] for message in reads.user_messages(second["session_id"])} == {queued["turn_id"]}
    assert all(message["turn_id"] != queued["turn_id"] for message in reads.user_messages(row["session_id"]))


def test_lark_scanner_shares_only_one_pass_and_pins_one_code_revision(failed_source, monkeypatch):
    from loopx.control_plane import effect_runtime
    store, runtime, provider, transport, binding, _, ref, row = failed_source
    event = provider.event("steward-app", "second-discovery-history", "typed terminal failure on a second request")
    transport.admit("steward-app", event)
    second = identity_ref(binding["provider_ref"], event["message_id"])
    next_row = transport.core.read_request(second)
    assert finish(runtime, next_row)["status"] == "failed"
    assert transport.reconcile_request(transport.root / f"{second}.json") == 1
    store.update_session(row["session_id"], manager_context_version=20)
    calls = Counter()
    for owner, name in [(store, "queued_turns"), (effect_runtime, "_runtime_fingerprint")]:
        original = getattr(owner, name)
        def counted(*args, _name=name, _read=original, **kwargs):
            calls[_name] += 1
            return _read(*args, **kwargs)
        monkeypatch.setattr(owner, name, counted)
    provider_calls = len(provider.calls)
    expected = {transport.root / f"{request}.json" for request in (ref, second)}
    for number in (1, 2):
        # Advance the existing discovery clock, not a new retry mechanism.
        transport._context_recovery_probe_at.clear()
        assert set(transport.pending_delivery_paths()) == expected
        assert calls == {"queued_turns": number, "_runtime_fingerprint": number}
    assert len(provider.calls) == provider_calls
