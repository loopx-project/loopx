"""Contract and boundary tests for the modular Zero-Any ``loopx.chat.ingress`` slice."""
from __future__ import annotations

import ast
import base64
from collections.abc import Mapping, Sequence
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings
from loopx.chat.ingress import (
    ChatIngressStore,
    chat_runtime_activation_state,
)
from loopx.chat.ingress_common import (
    AdmissionStoreProtocol,
    ConversationBindingsProtocol,
    IngressStoreHostProtocol,
)
from loopx.chat_store import ChatSessionStore

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MYPY_SUCCESS_RE = re.compile(r"^Success: no issues found in (\d+) source files?\s*$", re.MULTILINE)
_MYPY_FOUND_RE = re.compile(
    r"^Found (\d+) errors? in (\d+) files? \(checked (\d+) source files?\)\s*$",
    re.MULTILINE,
)

_INGRESS_MODULES = (
    "loopx/chat/ingress.py",
    "loopx/chat/ingress_common.py",
    "loopx/chat/ingress_admission.py",
    "loopx/chat/ingress_queue.py",
    "loopx/chat/ingress_receipts.py",
)


def _collect_mypy_contract_errors(
    proc: subprocess.CompletedProcess[str],
    *,
    probe: Path,
    callers: Sequence[str],
) -> tuple[list[str], dict[str, list[str]]]:
    expected_files = 1 + len(callers)
    details = f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout.strip()}\nSTDERR:\n{proc.stderr.strip()}"
    if proc.returncode not in (0, 1):
        raise AssertionError(f"mypy exited unexpectedly:\n{details}")
    if proc.stderr.strip():
        raise AssertionError(f"mypy reported errors on stderr:\n{details}")
    if proc.returncode == 0:
        match = _MYPY_SUCCESS_RE.search(proc.stdout)
        if match is None or int(match.group(1)) != expected_files:
            raise AssertionError(f"mypy did not inspect all {expected_files} files:\n{details}")
    else:
        found = _MYPY_FOUND_RE.search(proc.stdout)
        if found is None or int(found.group(3)) != expected_files or int(found.group(1)) <= 0:
            raise AssertionError(f"mypy failed or aborted before checking {expected_files} files:\n{details}")

    probe_errors: list[str] = []
    caller_errors: dict[str, list[str]] = {path: [] for path in callers}
    probe_prefix = f"{probe}:"
    for line in proc.stdout.splitlines():
        if ": error:" not in line:
            continue
        if line.startswith(probe_prefix):
            probe_errors.append(line)
            continue
        for caller in callers:
            if line.startswith(f"{caller}:"):
                caller_errors[caller].append(line)
                break
    return probe_errors, caller_errors


def _run_mypy_contract_probe(
    tmp_path: Path,
    *,
    probe_lines: Sequence[str],
    callers: Sequence[str],
    extra_env: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    if importlib.util.find_spec("mypy") is None:
        pytest.skip("mypy optional test dependency is unavailable")
    probe = tmp_path / "ingress_contract_probe.py"
    probe.write_text("\n".join([*probe_lines, ""]), encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(_REPO_ROOT / "pyproject.toml"),
        "--no-incremental",
        "--follow-imports=silent",
        "--cache-dir",
        str(tmp_path / ".mypy_cache"),
        "--show-error-codes",
        str(probe),
        *callers,
    ]
    env = {**os.environ, **extra_env} if extra_env is not None else None
    proc = subprocess.run(cmd, cwd=_REPO_ROOT, env=env, capture_output=True, text=True, check=False)
    return _collect_mypy_contract_errors(proc, probe=probe, callers=callers)


def test_chat_ingress_modules_meet_loc_zero_any_and_layout_pins() -> None:
    assert ChatIngressStore.__module__ == "loopx.chat.ingress"
    assert importlib.util.find_spec("loopx.chat_ingress") is None

    for rel in (*_INGRESS_MODULES, "tests/test_chat_ingress_contract.py"):
        src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        any_hits = sum(isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(tree))
        ignore_marker = "# " + "type: ignore"
        noqa_marker = "# " + "noqa"
        ignores = sum(ignore_marker in line for line in src.splitlines())
        noqas = sum(noqa_marker in line for line in src.splitlines())
        loc = len(src.splitlines())
        assert any_hits == 0, f"{rel} leaked AST Any"
        assert ignores == 0, f"{rel} leaked type-ignore comment"
        assert noqas == 0, f"{rel} leaked noqa comment"
        if rel == "loopx/chat/ingress.py":
            assert loc <= 550, f"{rel} exceeded 550 LOC ({loc})"
        elif rel.startswith("loopx/chat/"):
            assert loc < 750, f"{rel} exceeded 750 LOC ({loc})"


def test_real_store_and_bindings_match_ingress_protocols(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "runtime", startup_maintenance=False)
    bindings = ChatConversationBindings(
        root=tmp_path / "runtime" / "chat",
        project_contexts=object(),
        observe=lambda _ref: {},
    )
    assert isinstance(store, ChatIngressStore)
    assert bindings.controller is None

    method_checks: tuple[tuple[type[object], type[object], str], ...] = (
        (IngressStoreHostProtocol, ChatSessionStore, "load_session"),
        (IngressStoreHostProtocol, ChatSessionStore, "load_turn"),
        (IngressStoreHostProtocol, ChatSessionStore, "events_after"),
        (IngressStoreHostProtocol, ChatSessionStore, "messages"),
        (IngressStoreHostProtocol, ChatSessionStore, "append_event"),
        (IngressStoreHostProtocol, ChatSessionStore, "append_message"),
        (IngressStoreHostProtocol, ChatSessionStore, "release_active_turn"),
        (IngressStoreHostProtocol, ChatSessionStore, "flush_events"),
        (AdmissionStoreProtocol, ChatSessionStore, "resumable_session_candidates"),
        (ConversationBindingsProtocol, ChatConversationBindings, "read"),
        (ConversationBindingsProtocol, ChatConversationBindings, "resolve"),
        (ConversationBindingsProtocol, ChatConversationBindings, "ensure_delivery_scope"),
        (ConversationBindingsProtocol, ChatConversationBindings, "_ensure_delivery_scope"),
    )
    for proto_cls, real_cls, name in method_checks:
        proto_params = list(inspect.signature(getattr(proto_cls, name)).parameters.items())[1:]
        real_params = inspect.signature(getattr(real_cls, name)).parameters
        for param_name, proto_param in proto_params:
            if proto_param.kind is inspect.Parameter.VAR_KEYWORD:
                assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in real_params.values())
                continue
            assert param_name in real_params, f"{real_cls.__name__}.{name} missing {param_name!r}"


def test_real_store_and_callers_mypy_contract(tmp_path: Path) -> None:
    callers = [
        "loopx/chat_store.py",
        "loopx/runtime_activation.py",
        "loopx/capabilities/native_chat/external_conversations.py",
        "loopx/capabilities/native_chat/external_mention_recovery.py",
        "loopx/extensions/lark/goal_topic_runtime.py",
        "loopx/extensions/lark/goal_topic_runtime_service.py",
        "loopx/extensions/lark/private_topic_lanes.py",
    ]
    probe_errors, caller_errors = _run_mypy_contract_probe(
        tmp_path,
        probe_lines=[
            "from loopx.capabilities.native_chat.conversation_bindings import ChatConversationBindings",
            "from loopx.chat.ingress_common import ConversationBindingsProtocol, IngressStoreHostProtocol",
            "from loopx.chat_store import ChatSessionStore",
            "",
            "def bind_store(store: ChatSessionStore) -> IngressStoreHostProtocol:",
            "    return store",
            "",
            "def bind_bindings(bindings: ChatConversationBindings) -> ConversationBindingsProtocol:",
            "    return bindings",
        ],
        callers=callers,
    )
    assert not probe_errors, f"Ingress protocol probe errors: {probe_errors}"
    assert len(caller_errors["loopx/chat_store.py"]) <= 4
    assert len(caller_errors["loopx/runtime_activation.py"]) <= 2
    assert len(caller_errors["loopx/capabilities/native_chat/external_conversations.py"]) <= 33
    assert caller_errors["loopx/capabilities/native_chat/external_mention_recovery.py"] == []
    assert caller_errors["loopx/extensions/lark/goal_topic_runtime.py"] == []
    assert caller_errors["loopx/extensions/lark/goal_topic_runtime_service.py"] == []
    assert caller_errors["loopx/extensions/lark/private_topic_lanes.py"] == []


def test_ingress_receipts_queue_and_strict_activation_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    store = ChatSessionStore(runtime, startup_maintenance=False)
    created_session = store.create_session(
        goal_id="g-ingress",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-1",
    )
    sid = str(created_session["session_id"])

    monkeypatch.setattr("loopx.chat.ingress.SESSION_QUEUE_MAX_PENDING", 1)
    queued_one, created_flag = store.create_queued_turn(
        sid,
        client_turn_id="q-1",
        message="first item",
        origin="external",
    )
    assert created_flag is True
    replay_one, replay_flag = store.create_queued_turn(
        sid,
        client_turn_id="q-1",
        message="first item",
        origin="external",
    )
    assert replay_flag is False
    assert replay_one["turn_id"] == queued_one["turn_id"]
    with pytest.raises(ValueError, match="client_turn_id already belongs to a different request"):
        store.create_queued_turn(sid, client_turn_id="q-1", message="mutated", origin="external")
    with pytest.raises(RuntimeError, match="session_queue_full"):
        store.create_queued_turn(sid, client_turn_id="q-2", message="overflow", origin="external")

    active_ids: list[str] = []
    counts = chat_runtime_activation_state(runtime, active_session_ids=active_ids)
    assert counts == {"sessions": 1, "nonterminal_turns": 1}
    assert active_ids == []

    receipt, receipt_created = store.create_ingress_receipt(
        sid,
        client_ingress_id="ing-1",
        mode="loopx_queue",
        message="hello",
    )
    assert receipt_created is True and receipt["status"] == "pending"
    updated = store.update_ingress_receipt(sid, "ing-1", status="delivered")
    assert updated["status"] == "delivered"
    with pytest.raises(ValueError, match="unsupported chat ingress fields"):
        store.update_ingress_receipt(sid, "ing-1", lock_fn="unexpected")
    assert [row["client_ingress_id"] for row in store.loopx_ingress(sid)] == ["ing-1"]

    dangling = store._ingress_path(sid, "ing-dangling")
    dangling.parent.mkdir(parents=True, exist_ok=True)
    dangling.symlink_to(tmp_path / "missing-target.json")
    with pytest.raises(OSError, match="Ingress receipt is unreadable"):
        store.steering_receipt(sid, "ing-dangling")

    turn_path = store._turn_path(sid, str(queued_one["turn_id"]))
    bad_turn = dict(queued_one)
    bad_turn["status"] = "corrupt_status"
    turn_path.write_text(json.dumps(bad_turn), encoding="utf-8")
    with pytest.raises(OSError, match="Queued Turn state is invalid"):
        store.queued_turns(sid, strict=True)
    with pytest.raises(ValueError, match="unreadable Chat Turn state"):
        chat_runtime_activation_state(runtime)


def test_recover_queued_turn_input_preserves_valid_and_rejects_malformed_attachments(
    tmp_path: Path,
) -> None:
    store = ChatSessionStore(tmp_path / "runtime", startup_maintenance=False)
    created_session = store.create_session(
        goal_id="g-recover",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-recover",
    )
    sid = str(created_session["session_id"])

    # 1) Interrupted write with valid list attachments preserves exact list and recovers claim.
    spec_bytes = b"# Spec\n"
    valid_att = [{
        "id": "att-1",
        "mime_type": "text/markdown",
        "name": "spec.md",
        "size": len(spec_bytes),
        "data_url": f"data:text/markdown;base64,{base64.b64encode(spec_bytes).decode('ascii')}",
    }]
    valid_turn, created = store.create_queued_turn(
        sid,
        client_turn_id="q-valid-att",
        message="with attachment",
        attachments=valid_att,
        origin="web",
    )
    assert created is True
    valid_tid = str(valid_turn["turn_id"])
    (store._session_dir(sid) / "messages.jsonl").unlink()
    store._event_path(sid, valid_tid).unlink()
    claimed = store.claim_next_queued_turn(sid, host_claim_id="claim-valid")
    assert claimed is not None and str(claimed["turn_id"]) == valid_tid
    msgs = [m for m in store.messages(sid) if m.get("turn_id") == valid_tid]
    assert len(msgs) == 1 and msgs[0].get("attachments") == valid_att
    assert any(e.get("kind") == "turn.queued" for e in store.events_after(sid, valid_tid, None))
    assert store.release_active_turn(sid, valid_tid)
    store.update_turn(sid, valid_tid, status="completed")

    # 2) Interrupted write with non-empty non-list or non-dict attachments fails closed BEFORE
    # writing messages.jsonl or turn.queued, keeping the turn queued/pending recovery.
    for idx, bad_att in enumerate(({"name": "legacy-att"}, "persisted.txt", ["bad-item"])):
        queued, _ = store.create_queued_turn(
            sid,
            client_turn_id=f"q-bad-{idx}",
            message=f"bad {idx}",
            origin="web",
        )
        tid = str(queued["turn_id"])
        turn_path = store._turn_path(sid, tid)
        raw_turn = json.loads(turn_path.read_text(encoding="utf-8"))
        raw_turn["attachments"] = bad_att
        turn_path.write_text(json.dumps(raw_turn), encoding="utf-8")
        messages_path = store._session_dir(sid) / "messages.jsonl"
        prior_msgs = messages_path.read_text(encoding="utf-8")
        store._event_path(sid, tid).unlink()
        with pytest.raises(ValueError, match="attachments"):
            store.claim_next_queued_turn(sid, host_claim_id=f"claim-bad-{idx}")
        assert messages_path.read_text(encoding="utf-8") == prior_msgs
        assert not store._event_path(sid, tid).exists()
        reloaded = store.load_turn(sid, tid)
        assert reloaded is not None and reloaded["status"] == "queued"
        # Clean up this bad turn so subsequent iterations inspect the next fixture cleanly.
        store.update_turn(sid, tid, status="failed", error_code="test_cleanup")

    # 3) Interrupted event flush when messages.jsonl already recorded the input replays cleanly.
    legacy_att: object = {"name": "legacy-att"}
    replayed, _ = store.create_queued_turn(
        sid,
        client_turn_id="q-legacy-replay",
        message="legacy replay",
        origin="web",
    )
    replay_tid = str(replayed["turn_id"])
    turn_path = store._turn_path(sid, replay_tid)
    raw_replay = json.loads(turn_path.read_text(encoding="utf-8"))
    raw_replay["attachments"] = legacy_att
    turn_path.write_text(json.dumps(raw_replay), encoding="utf-8")
    store._event_path(sid, replay_tid).unlink()
    messages_path = store._session_dir(sid) / "messages.jsonl"
    msg_rows = [json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in msg_rows:
        if row.get("turn_id") == replay_tid and row.get("role") == "user":
            row["attachments"] = legacy_att
    messages_path.write_text(
        "".join(json.dumps(row) + "\n" for row in msg_rows),
        encoding="utf-8",
    )
    claimed_replay = store.claim_next_queued_turn(sid, host_claim_id="claim-legacy-replay")
    assert claimed_replay is not None and str(claimed_replay["turn_id"]) == replay_tid
    assert any(e.get("kind") == "turn.queued" for e in store.events_after(sid, replay_tid, None))
    assert store.release_active_turn(sid, replay_tid)
    store.update_turn(sid, replay_tid, status="completed")

    # 4) Turn discovery read error populates host._last_read_error_paths[session_id].
    corrupt_path = store._turn_path(sid, "corruptturn001")
    corrupt_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(OSError, match="Queued Turn state is unreadable"):
        store.queued_turns(sid, strict=True)
    failed_map = getattr(store, "_last_read_error_paths", None)
    assert isinstance(failed_map, dict) and failed_map.get(sid) == corrupt_path
