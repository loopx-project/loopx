"""Completed-command discovery uses fresh native ownership in one thread scan."""
import queue
import time
from types import SimpleNamespace

import pytest

from loopx.chat_agent import CodexChatAgentError, CodexChatAgentSession


def inventory(tmp_path, monkeypatch, *, fault=None):
    session = CodexChatAgentSession(process=SimpleNamespace(poll=lambda: None),
        messages=queue.Queue(), thread_id="original-thread", work_dir=tmp_path,
        response_timeout_sec=.5)
    turns = [f"completed-{i}" for i in range(23)]
    calls = []
    entries = [{"turnId": turn, "item": {"type": "agentMessage", "id": f"answer-{i}"}}
               for i, turn in enumerate(turns)]
    entries += [
        {"turnId": turns[-1], "item": {"type": "commandExecution", "id": "owned-command"}},
        {"turnId": "newer-turn", "item": {"type": "commandExecution", "id": "newer-command"}},
    ]

    def write(packet):
        method, params = packet["method"], packet["params"]
        calls.append((method, params))
        assert params["threadId"] == "original-thread"
        if fault == "rpc" and method == "thread/items/list":
            session.messages.put({"id": packet["id"], "error": {"message": "unavailable"}})
            return
        if method == "thread/backgroundTerminals/list":
            result = {"data": [] if fault == "idle" else [
                {"itemId": "owned-command", "processId": "owned-pty"},
                {"itemId": "newer-command", "processId": "newer-pty"},
                {"itemId": "wire-command", "processId": "wire-pty"},
            ]}
        else:
            assert method == "thread/items/list"
            # Preserve the former per-Turn protocol for the counterfactual.
            if params.get("turnId"):
                result = {"data": [entry for entry in entries if entry["turnId"] == params["turnId"]]}
            else:
                page = 1 if params.get("cursor") else 0
                result = {"data": entries[20:] if page else entries[:20],
                          "nextCursor": "second" if not page or fault == "cursor-cycle" else None}
                if page and fault == "missing-data":
                    result.pop("data")
                elif page and fault == "missing-turn":
                    result["data"] = [{"item": {"type": "commandExecution", "id": "owned-command"}}]
                elif page and fault == "missing-item":
                    result["data"] = [{"turnId": turns[-1]}]
                elif page and fault == "unbound-command":
                    result["data"] = [{"turnId": turns[-1], "item": {"type": "commandExecution", "id": ""}}]
                elif page and fault == "ambiguous-item":
                    result["data"].append({"turnId": "newer-turn", "item": {
                        "type": "commandExecution", "id": "owned-command"}})
        session.messages.put({"id": packet["id"], "result": result})

    monkeypatch.setattr(session, "_write", write)
    # Native command events can outlive their history projection, but only
    # this exact thread/Turn association may supplement the fresh inventory.
    session.messages.put({"method": "item/completed", "params": {
        "threadId": "original-thread", "turnId": turns[0],
        "item": {"type": "commandExecution", "id": "wire-command"}}})
    if fault == "wire-conflict":
        session.messages.put({"method": "item/completed", "params": {
            "threadId": "original-thread", "turnId": turns[1],
            "item": {"type": "commandExecution", "id": "wire-command"}}})
    return session, turns, calls


def test_completed_history_discovery_scans_all_pages_once_and_keeps_frozen_order(tmp_path, monkeypatch):
    session, turns, calls = inventory(tmp_path, monkeypatch)
    assert session.background_turn_ids(turns + [turns[0]]) == [turns[0], turns[-1]]
    item_reads = [params for method, params in calls if method == "thread/items/list"]
    assert len(item_reads) == 2
    assert all("turnId" not in params for params in item_reads)
    assert item_reads[-1]["cursor"] == "second"
    assert all(method in {"thread/items/list", "thread/backgroundTerminals/list"} for method, _ in calls)


@pytest.mark.parametrize("fault", ["rpc", "missing-data", "missing-turn", "missing-item",
                                  "unbound-command", "cursor-cycle", "ambiguous-item", "wire-conflict"])
def test_incomplete_or_ambiguous_thread_inventory_never_supplies_stop_targets(tmp_path, monkeypatch, fault):
    session, turns, calls = inventory(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(CodexChatAgentError):
        session.background_turn_ids(turns)
    assert all(not method.endswith("/terminate") for method, _ in calls)


def test_idle_background_inventory_never_reads_old_command_history(tmp_path, monkeypatch):
    session, turns, calls = inventory(tmp_path, monkeypatch, fault="idle")
    assert session.background_turn_ids(turns) == []
    assert [method for method, _ in calls] == ["thread/backgroundTerminals/list"]


def test_empty_frozen_candidate_set_does_not_query_native_thread(tmp_path, monkeypatch):
    session, _, calls = inventory(tmp_path, monkeypatch)
    started = time.monotonic()
    assert session.background_turn_ids([]) == []
    assert calls == [] and time.monotonic() - started < .5
