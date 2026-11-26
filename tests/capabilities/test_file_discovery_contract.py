"""Contract, edge-case, and origin/main parity tests for native_chat file_discovery."""
from __future__ import annotations

import ast
import base64
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import types
from types import SimpleNamespace

import pytest

from loopx.capabilities.native_chat import (
    file_discovery as fd,
    file_discovery_backlog as fd_backlog,
    file_discovery_media as fd_media,
)
from loopx.chat_store import ChatSessionStore, _atomic_write_json

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
_TURN_HEX = "a" * 32
_REQ_HEX = "b" * 24


def _load_baseline_file_discovery() -> types.ModuleType | None:
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        ["git", "show", "origin/main:loopx/capabilities/native_chat/file_discovery.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    mod = types.ModuleType("_baseline_file_discovery")
    mod.__package__ = "loopx.capabilities.native_chat"
    mod.__file__ = str(repo_root / "loopx/capabilities/native_chat/file_discovery.py")
    exec(compile(proc.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod


def test_file_discovery_modules_strict_zero_any_and_size_bounds() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "loopx/capabilities/native_chat/file_discovery.py",
        root / "loopx/capabilities/native_chat/file_discovery_backlog.py",
        root / "loopx/capabilities/native_chat/file_discovery_media.py",
        Path(__file__).resolve(),
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 550, f"{path} exceeded 550 LOC"
        assert re.search(r"#\s*type:\s*ignore\b", source) is None, f"{path} contains type ignore"
        tree = ast.parse(source)
        any_refs = sum(
            1
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
            or (isinstance(node, ast.alias) and node.name == "Any")
        )
        assert any_refs == 0, f"{path} has {any_refs} Any references"


def test_facade_preserves_expected_exports() -> None:
    expected = (
        "external_source_route_path",
        "_ordinary_request_text_digest",
        "_RECOVERY_JOURNAL_FIELDS",
        "recovery_journal_fields",
        "source_control_fields",
        "later_equivalent_source_input",
        "_MISSING_FILE_FP",
        "file_change_fingerprint",
        "optional_file_fingerprint",
        "_set_bounded_cache",
        "QuotaBacklogObservationCache",
        "_summarize_turn_events",
        "observed_turn_event_summary",
        "observed_turn_interrupt",
        "_same_store_method",
        "observe_quota_backlog_batch",
        "FileChatDiscovery",
        "_REPLY_IMAGE_TYPES",
        "_REPLY_IMAGE_SIGNATURES",
        "REPLY_IMAGE_NAME_PATTERN",
        "reply_image_directory",
        "reply_image_workspace",
        "_check_reply_image",
        "reply_image_attachment",
        "read_reply_image",
        "capture_reply_image_plan",
        "_DeferredBacklogRequest",
        "_preparation_image_identity",
        "_project_source_request_digest",
        "_project_user_message_hint",
        "_RecoveryDiscoveryReads",
    )
    for name in expected:
        assert hasattr(fd, name), f"missing export {name}"
    assert fd.QuotaBacklogObservationCache is fd_backlog.QuotaBacklogObservationCache
    assert fd.capture_reply_image_plan is fd_media.capture_reply_image_plan


def test_journal_and_source_control_key_presence_vs_none(tmp_path: Path) -> None:
    req_path = tmp_path / f"{_REQ_HEX}.json"
    assert fd.recovery_journal_fields(req_path, {"request_ref": None, "status": None}) == {
        "request_ref": None,
        "status": None,
    }
    assert fd.recovery_journal_fields(req_path, {"request_ref": _REQ_HEX}) is None
    assert fd.source_control_fields(req_path, {"binding_id": "b", "source": {}, "command": None}) is None

    prepared_none_origin = fd.source_control_fields(
        req_path,
        {
            "request_ref": _REQ_HEX,
            "status": "prepared",
            "turn_id": None,
            "binding_id": "b",
            "session_id": "s1",
            "source": {"private_human_message": True},
            "command": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "message": "hello",
            "origin": None,
        },
    )
    assert prepared_none_origin is not None
    assert prepared_none_origin["origin"] is None
    assert prepared_none_origin["prepared_client_turn_id"] == f"external-{_REQ_HEX}"

    accepted_empty_turn = fd.source_control_fields(
        req_path,
        {
            "request_ref": _REQ_HEX,
            "status": "accepted",
            "turn_id": "",
            "binding_id": "b",
            "session_id": "s1",
            "source": {"private_human_message": True},
            "command": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "message": "hello",
        },
    )
    assert accepted_empty_turn is not None
    assert "text_sha256" not in accepted_empty_turn


def test_later_equivalent_source_input_and_malformed_timestamps() -> None:
    base_row: dict[str, object] = {
        "status": "accepted",
        "turn_id": "t1",
        "binding_id": "b1",
        "session_id": "s1",
        "source": {"source_ref": "sr", "private_human_message": True},
        "command": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "message": "ping",
    }
    digest = fd._ordinary_request_text_digest(base_row)
    assert digest is not None

    with pytest.raises(TypeError):
        fd.later_equivalent_source_input(
            {**base_row, "created_at": 12345},
            lambda: [],
            lambda _sid, _cid: None,
        )

    assert (
        fd.later_equivalent_source_input(
            base_row,
            lambda: [
                {
                    "binding_id": "b1",
                    "source": {"source_ref": "sr", "private_human_message": True},
                    "session_id": "s1",
                    "created_at": "2026-01-01T00:01:00+00:00",
                    "text_sha256": digest,
                    "prepared_client_turn_id": "external-next",
                    "origin": "lark",
                }
            ],
            lambda _sid, _cid: {
                "origin": "lark",
                "message": "rewritten",
                "_queue_original_message": "ping",
                "attachments": [],
                "external_agent_target": None,
                "collaboration_request": None,
            },
        )
        is True
    )


def test_summarize_turn_events_single_pass_and_isolation() -> None:
    anchor_payload = {"thread_id": "th-1", "nested": {"x": 1}}
    single, steered, interrupted = fd._summarize_turn_events(
        [
            {"kind": "turn.started", "payload": None},
            {"kind": "turn.started", "payload": {"native_input": anchor_payload}},
            {"kind": "turn.steered"},
            {"kind": "turn.interrupt_requested"},
        ]
    )
    assert single == {"thread_id": "th-1", "nested": {"x": 1}}
    assert single is not anchor_payload
    assert steered is True
    assert interrupted is True

    assert fd._summarize_turn_events([{"kind": "turn.started", "payload": {"native_input": "corrupt"}}])[0] is None
    assert fd._summarize_turn_events(
        [
            {"kind": "turn.started", "payload": {"native_input": {"thread_id": "1"}}},
            {"kind": "turn.started", "payload": {"native_input": "corrupt"}},
        ]
    ) == (None, False, False)
    assert fd._summarize_turn_events(
        [
            {"kind": "turn.started", "payload": {"native_input": ["corrupt"]}},
            {"kind": "turn.started", "payload": {"native_input": {"thread_id": "2"}}},
        ]
    ) == (None, False, False)
    assert fd._summarize_turn_events(
        [
            {"kind": "turn.started", "payload": {"native_input": {"thread_id": "1"}}},
            {"kind": "turn.started", "payload": "corrupt-payload"},
        ]
    ) == (None, False, False)
    two_anchors, steered_2, interrupted_2 = fd._summarize_turn_events(
        [
            {"kind": "turn.started", "payload": {"native_input": {"thread_id": "1"}}},
            {"kind": "turn.started", "payload": {"native_input": {"thread_id": "2"}}},
        ]
    )
    assert two_anchors is None
    assert steered_2 is False
    assert interrupted_2 is False


def test_cache_and_discovery_bounds_and_monkeypatch_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = fd.QuotaBacklogObservationCache(max_entries=2)
    fp = (1, 2, 3, 4, 5)
    for idx in ("r1", "r2", "r3"):
        cache.put_base(idx, (idx,), True, "running")
    assert cache.get_base("r1", ("r1",)) is None
    assert cache.get_base("r3", ("r3",)) == (True, "running")

    # Monkeypatching optional_file_fingerprint on facade must govern commit invalidation.
    req = tmp_path / "req.json"
    trn = tmp_path / "trn.json"
    req.write_text("{}")
    trn.write_text("{}")
    meta: fd_backlog._ObservationMetaEntry = (("k",), req, trn, fp, fp)
    monkeypatch.setattr(fd, "optional_file_fingerprint", lambda _p: fp)
    cache.commit("rx", meta, True, turn_status="running")
    assert cache.get_base("rx", ("k",)) == (True, "running")
    monkeypatch.setattr(fd, "optional_file_fingerprint", lambda _p: (9, 9))
    cache.commit("rx", meta, False, turn_status="completed")
    assert cache.get_base("rx", ("k",)) is None

    # FileChatDiscovery drops stale keys and passes raw values when project returns None.
    p1 = tmp_path / "one.json"
    p2 = tmp_path / "two.json"
    p1.write_text('{"v": 1}')
    p2.write_text('{"v": 2}')
    discovery = fd.FileChatDiscovery()
    out1 = list(
        discovery.rows(
            tmp_path,
            [p1, p2],
            read=lambda p: json.loads(p.read_text()),
            project=lambda _p, row: None if row["v"] == 2 else {"keep": row["v"]},
        )
    )
    assert out1 == [(p1, {"keep": 1}), (p2, {"v": 2})]
    assert set(discovery._directories[tmp_path]) == {p1}


def test_reply_image_validation_and_malformed_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid reply image Turn"):
        fd.reply_image_directory("not-32-hex")

    assert fd.reply_image_workspace({"project_context": "bad"}, {"origin": "lark"}) is None
    assert (
        fd.reply_image_workspace(
            {
                "session_mode": "managed_runtime",
                "agent_id": "codex",
                "project_context": {
                    "audience": "bound_owner",
                    "grant": "workspace_write",
                    "workspace_path": str(tmp_path),
                },
            },
            {"origin": "lark"},
        )
        == tmp_path.resolve()
    )

    # WebP header verification & invalid RIFF payload rejection
    valid_webp = b"RIFF1234WEBP" + b"\x00" * 10
    att = fd.reply_image_attachment(valid_webp, "image/webp", 0)
    assert att["mime_type"] == "image/webp"
    with pytest.raises(ValueError, match="unsupported reply image bytes"):
        fd.reply_image_attachment(b"RIFF1234XXXX" + b"\x00" * 10, "image/webp", 0)

    out_dir = tmp_path / fd.reply_image_directory(_TURN_HEX)
    out_dir.mkdir(parents=True)
    ok_img = out_dir / "chart.png"
    bad_img = out_dir / "corrupt.png"
    ok_img.write_bytes(_PNG_1X1)
    bad_img.write_bytes(b"not-a-png")
    outside = tmp_path / "escape.png"
    outside.write_bytes(_PNG_1X1)

    assert fd.capture_reply_image_plan(tmp_path, _TURN_HEX, []) == []
    assert fd.capture_reply_image_plan(tmp_path, _TURN_HEX, ["https://x.test/a.png"]) == []
    assert fd.capture_reply_image_plan(tmp_path, _TURN_HEX, [str(ok_img)] * 5) == [None]

    plan = fd.capture_reply_image_plan(
        tmp_path,
        _TURN_HEX,
        [str(ok_img), str(bad_img), str(outside), "https://x.test/keep.png"],
    )
    assert isinstance(plan[0], dict) and plan[0]["name"] == "chart.png"
    assert plan[1] is None
    assert plan[2] is None
    assert plan[3] == "https://x.test/keep.png"

    assert fd._preparation_image_identity([]) is None
    assert fd._preparation_image_identity("bad") is None


def test_paired_origin_main_behavioral_parity(tmp_path: Path) -> None:
    baseline = _load_baseline_file_discovery()
    if baseline is None:
        pytest.skip("git origin/main unavailable in this checkout")

    # 1. Batch quota backlog + deferred requests + real ChatSessionStore parity
    store_head = ChatSessionStore(tmp_path / "head", startup_maintenance=False)
    store_base = ChatSessionStore(tmp_path / "base", startup_maintenance=False)
    for st in (store_head, store_base):
        sess = st.create_session(goal_id="g1", agent_id="codex", adapter_kind="codex", upstream_thread_id="th-1")
        sid = sess["session_id"]
        turn, _ = st.create_turn(sid, client_turn_id=f"external-{_REQ_HEX}", message="hi")
        tid = turn["turn_id"]
        st.update_turn(sid, tid, status="failed")
        st.append_event(sid, tid, kind="turn.started", payload={"native_input": {"thread_id": "t0"}})
        st.append_event(sid, tid, kind="turn.interrupted", payload={})

    s_head = next(iter(store_head.sessions_root.iterdir())).name
    s_base = next(iter(store_base.sessions_root.iterdir())).name
    t_head = next(iter((store_head.sessions_root / s_head / "turns").glob("*.json"))).stem
    t_base = next(iter((store_base.sessions_root / s_base / "turns").glob("*.json"))).stem

    for mod, st, sid, tid, tag in [
        (fd, store_head, s_head, t_head, "head"),
        (baseline, store_base, s_base, t_base, "base"),
    ]:
        root = tmp_path / tag / "ext"
        root.mkdir(parents=True)
        req_file = root / f"{_REQ_HEX}.json"
        payload = {
            "request_ref": _REQ_HEX,
            "binding_id": "b1",
            "session_id": sid,
            "turn_id": tid,
            "status": "accepted",
            "command": None,
            "source": {"source_ref": "sr", "private_human_message": True},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "message": "hi",
        }
        _atomic_write_json(req_file, payload)
        cache_store = mod.QuotaBacklogObservationCache()
        deferred = mod._DeferredBacklogRequest(payload, req_fp=mod.optional_file_fingerprint(req_file), needs_full_read=False)

        def _build_inputs(r: Mapping[str, object], _st: ChatSessionStore = st) -> dict[str, object]:
            t = _st.load_turn(str(r["session_id"]), str(r["turn_id"]))
            return {"request": dict(r), "pending_observation": {"turn": t, "stop_observed": False}}

        def _eval(params: Sequence[Mapping[str, object]]) -> list[bool]:
            out: list[bool] = []
            for p in params:
                obs = p.get("pending_observation")
                stop_observed = obs.get("stop_observed") if isinstance(obs, Mapping) else False
                out.append(not bool(stop_observed))
            return out

        flags = mod.observe_quota_backlog_batch(
            root=root,
            store=st,
            rows=[deferred],
            build_inputs=_build_inputs,
            eval_flags=_eval,
            stop_controls=lambda: (),
            evaluator_id=1,
            cache_store=cache_store,
        )
        assert flags == [False], f"{tag} expected False after interrupt"
        # Warm cache hit must return identical result
        flags_warm = mod.observe_quota_backlog_batch(
            root=root,
            store=st,
            rows=[deferred],
            build_inputs=_build_inputs,
            eval_flags=lambda _p: (_ for _ in ()).throw(AssertionError("cache miss")),
            stop_controls=lambda: (),
            evaluator_id=1,
            cache_store=cache_store,
        )
        assert flags_warm == flags

    # 2. Recovery discovery hints + image identity + workspace checks parity
    fake_core = SimpleNamespace(
        controller=SimpleNamespace(store=store_head),
        _cached_user_messages=lambda _sid: [
            fd._project_user_message_hint({"role": "user", "turn_id": t_head, "text": "hi", "created_at": "2026-01-01T00:00:00+00:00"})
        ],
        source_controls=lambda: [],
    )
    r_head = fd._RecoveryDiscoveryReads(fake_core)
    r_base = baseline._RecoveryDiscoveryReads(fake_core)
    assert r_head.user_messages(s_head) == r_base.user_messages(s_head)
    assert r_head.matching_user_messages(s_head, "hi") == r_base.matching_user_messages(s_head, "hi")

    ws_sess = {
        "session_mode": "managed_runtime",
        "agent_id": "codex",
        "project_context": {"audience": "bound_owner", "grant": "workspace_write", "workspace_path": str(tmp_path)},
    }
    ws_turn = {"origin": "lark"}
    assert fd.reply_image_workspace(ws_sess, ws_turn) == baseline.reply_image_workspace(ws_sess, ws_turn)
    assert fd.reply_image_attachment(_PNG_1X1, "image/png", 1) == baseline.reply_image_attachment(_PNG_1X1, "image/png", 1)
    assert fd._preparation_image_identity([fd.reply_image_attachment(_PNG_1X1, "image/png", 1)]) == baseline._preparation_image_identity(
        [baseline.reply_image_attachment(_PNG_1X1, "image/png", 1)]
    )

    # 3. Journal/source projections, FileChatDiscovery, and reply-image plans parity
    ctl_path = tmp_path / f"{_REQ_HEX}.json"
    ctl_row = {
        "request_ref": _REQ_HEX,
        "binding_id": "b1",
        "session_id": s_head,
        "status": "prepared",
        "turn_id": None,
        "command": None,
        "source": {"private_human_message": True},
        "created_at": "2026-01-01T00:00:00+00:00",
        "message": "ping",
    }
    _atomic_write_json(ctl_path, ctl_row)
    assert fd.recovery_journal_fields(ctl_path, ctl_row) == baseline.recovery_journal_fields(ctl_path, ctl_row)
    assert fd.source_control_fields(ctl_path, ctl_row) == baseline.source_control_fields(ctl_path, ctl_row)
    disc_head = fd.FileChatDiscovery()
    disc_base = baseline.FileChatDiscovery()
    assert list(disc_head.rows_with_fingerprints(tmp_path, [ctl_path], read=lambda p: json.loads(p.read_text()), project=fd.source_control_fields)) == list(
        disc_base.rows_with_fingerprints(tmp_path, [ctl_path], read=lambda p: json.loads(p.read_text()), project=baseline.source_control_fields)
    )
    img_dir = tmp_path / fd.reply_image_directory(_TURN_HEX)
    img_dir.mkdir(parents=True, exist_ok=True)
    img_file = img_dir / "parity.png"
    img_file.write_bytes(_PNG_1X1)
    assert fd.read_reply_image(tmp_path, _TURN_HEX, "parity.png") == baseline.read_reply_image(tmp_path, _TURN_HEX, "parity.png")
    assert fd.capture_reply_image_plan(tmp_path, _TURN_HEX, [str(img_file), "https://x.test/ok.png"]) == baseline.capture_reply_image_plan(
        tmp_path, _TURN_HEX, [str(img_file), "https://x.test/ok.png"]
    )


def test_real_discovery_preserves_malformed_start_ambiguity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from loopx.capabilities.native_chat import external_conversations as ext_mod
    from loopx.chat_manager import MANAGER_CONTEXT_VERSION
    from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION

    baseline = _load_baseline_file_discovery()
    binding_id, prov_id, op_id, proj_id, src_id = ("b" * 24, "a" * 24, "e" * 24, "f" * 24, "c" * 24)
    binding = {
        "schema_version": "loopx_chat_conversation_binding_v0", "binding_id": binding_id, "enabled": True,
        "transport_ref": "steward-app", "context_kind": "steward", "goal_ids": [], "grant": "portfolio_read",
        "provider_ref": prov_id, "operator_ref": op_id, "project_ref": proj_id, "executor_endpoint_id": "antigravity",
    }
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    bindings = SimpleNamespace(controller=None, read=lambda: {"bindings": [binding]}, resolve=lambda **_: {"binding": binding})
    ctrl = SimpleNamespace(store=store, project_contexts=SimpleNamespace(conversation_bindings=bindings), lock=threading.Lock(), adapters={}, codex_home=tmp_path)
    core = ext_mod.ChatExternalConversations(ctrl)
    core.root.mkdir(parents=True, exist_ok=True)
    anchor: dict[str, object] = {"thread_id": "cas-1", "start_step_index": 0, "input_sha256": "a" * 64}
    cases: list[tuple[str, list[tuple[str, dict[str, object]]], str | None, bool, bool]] = [
        ("clean", [("turn.started", {"native_input": anchor})], None, True, True),
        ("malformed_second_str", [("turn.started", {"native_input": anchor}), ("turn.started", {"native_input": "corrupt"})], None, False, True),
        ("malformed_first_list", [("turn.started", {"native_input": ["corrupt"]}), ("turn.started", {"native_input": anchor})], None, False, True),
        ("single_malformed", [("turn.started", {"native_input": "corrupt-only"})], None, False, False),
        ("corrupt_second_payload", [("turn.started", {"native_input": anchor})], "corrupt-payload", False, False),
    ]
    for idx, (_label, evs, corrupt_payload, expected, check_baseline) in enumerate(cases):
        sid, tid, req = f"{3000 + idx:032x}", f"{4000 + idx:032x}", f"{5000 + idx:024x}"
        store.create_session(
            goal_id="loopx-manager", agent_id="antigravity", adapter_kind="antigravity", upstream_thread_id="cas-1",
            upstream_mode="default", session_id=sid, channel_id=f"manager.external.native.{binding_id}.{src_id}",
            steward_context={"kind": "bound_steward", "binding_id": binding_id, "audience": "bound_owner", "grant": "portfolio_read",
                             "goal_ids": [], "provider_ref": prov_id, "operator_ref": op_id, "project_ref": proj_id, "source_ref": src_id, "workspace_path": str(tmp_path)},
        )
        loaded_session = store.load_session(sid)
        assert loaded_session is not None
        _atomic_write_json(store._session_path(sid), {**loaded_session, "session_mode": "managed_runtime", "executor_endpoint_id": "antigravity", "manager_context_version": MANAGER_CONTEXT_VERSION})
        _atomic_write_json(store._turn_path(sid, tid), {"schema_version": CHAT_TURN_SCHEMA_VERSION, "session_id": sid, "turn_id": tid,
            "client_turn_id": f"external-{req}", "message": "q", "origin": "lark", "status": "failed", "error_code": "antigravity_step_error",
            "delta_count": 0, "upstream_turn_id": "up-1", "created_at": "2026-01-01T00:00:00+00:00", "completed_at": "2026-01-01T00:01:00+00:00", "response": None})
        for kind, payload in evs:
            store.append_event(sid, tid, kind=kind, payload=dict(payload), buffered=False)
        if corrupt_payload is not None:
            store.append_event(sid, tid, kind="turn.started", payload={"native_input": "placeholder"}, buffered=False)
            ev_path = store._event_path(sid, tid)
            lines = ev_path.read_text(encoding="utf-8").splitlines()
            lines[-1] = json.dumps({**json.loads(lines[-1]), "payload": corrupt_payload})
            ev_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        row: dict[str, object] = {"request_ref": req, "binding_id": binding_id, "source": {"source_ref": src_id, "sender_ref": op_id, "private_human_message": True},
                                  "session_id": sid, "turn_id": tid, "command": None, "origin": "lark", "status": "accepted", "delivery_verified": True,
                                  "message": "q", "created_at": "2026-01-01T00:00:00+00:00"}
        _atomic_write_json(core.root / f"{req}.json", row)
        assert core.context_recovery_candidate(req) is expected
        if check_baseline:
            assert (core._context_recovery_plan(row, discovery_only=False).get("recovery_candidate") is True) is expected
            if baseline is not None:
                with monkeypatch.context() as mp:
                    mp.setattr(ext_mod, "observed_turn_event_summary", baseline.observed_turn_event_summary)
                    core._quota_backlog_cache.drop_event_summary(sid, tid)
                    assert core.context_recovery_candidate(req) is expected
