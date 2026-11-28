from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest

from loopx.capabilities.native_chat import (
    report_failure_ledger,
    report_failure_probe,
    report_failure_proof,
)
from loopx.capabilities.native_chat.report_failure_proof import (
    CONTRADICTED_FAILURES_SCHEMA,
    CONTRADICTED_REPORT_FAILURES,
    REPORT_FAILURE_PROOF,
    build_report_quota_request,
    build_report_timeout_request,
    contradict_report_failure,
    is_report_failure_contradicted,
    probe_report_failure,
    proof_targets_original_turn,
    read_retained_report_failure,
    report_failure_binding,
    retain_report_failure,
    valid_report_failure_proof,
    verify_report_window_failure,
)
from loopx.capabilities.periodic_report.cadence_journal import (
    admit_cadence_window,
    cadence_journal_path,
)
from loopx.capabilities.periodic_report.private_generation import attempt_directory

REPO_ROOT = Path(__file__).resolve().parents[2]
_IGNORE_MARKER = "type" + ": ignore"
_PINNED_EXPORTS = (
    "REPORT_FAILURE_PROOF",
    "CONTRADICTED_REPORT_FAILURES",
    "CONTRADICTED_FAILURES_SCHEMA",
    "report_failure_binding",
    "_read_report_failure_record",
    "_is_antigravity_turn_id",
    "_is_valid_retained_proof",
    "_is_contradicted_proof",
    "_failure_proof_lock",
    "_contradiction_ledger_path",
    "_binding_key",
    "_read_contradiction_ledger",
    "_durable_contradiction_status",
    "read_retained_report_failure",
    "is_report_failure_contradicted",
    "retain_report_failure",
    "_invalidate_attempt_proof",
    "contradict_report_failure",
    "valid_report_failure_proof",
    "proof_targets_original_turn",
    "probe_report_failure",
    "_extract_antigravity_turn_cascade",
    "_antigravity_trajectory_steps",
    "_antigravity_has_running_step",
    "_antigravity_has_final_response",
    "_probe_antigravity_report_failure",
    "observe_antigravity_report_failure",
    "observe_report_failure",
    "_try_retain_report_failure",
    "handle_contradicted_report_observation",
    "verify_report_window_failure",
    "resolve_report_quota_observation",
    "build_report_quota_request",
    "build_report_timeout_request",
)


def _any_ast_refs(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            count += 1
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            count += 1
        elif isinstance(node, ast.alias) and node.name == "Any":
            count += 1
    return count


_SAMPLE_SUBSCRIPTION: dict[str, object] = {
    "enabled": True,
    "profile_preset": "portfolio-progress",
    "effective_revision": "sha256:" + "1" * 64,
    "schedule": {
        "schema_version": "periodic_report_schedule_v0",
        "schedule_id": "daily-report",
        "timezone": "UTC",
        "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "start_date": "2026-01-01",
    },
}


def _sample_window(runtime_root: Path) -> dict[str, object]:
    admitted = admit_cadence_window(
        runtime_root=runtime_root,
        goal_id="goal-1",
        agent_id="agent-1",
        subscription=_SAMPLE_SUBSCRIPTION,
        now=datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
    )
    return dict(cast(dict[str, object], admitted["window"]))


def _sample_original(
    window: dict[str, object],
    *,
    upstream_turn_id: str = "codex-turn-1",
    retryable: bool = False,
) -> dict[str, object]:
    return {
        "origin": "native_loopx",
        "loopx_execution": True,
        "status": "completed",
        "session_id": "sess-1",
        "turn_id": "chat-turn-1",
        "upstream_turn_id": upstream_turn_id,
        "loopx_request": {
            "actor": {"session_id": "sess-1", "goal_id": "goal-1", "agent_id": "agent-1"},
            "work_kind": "report",
            "report_window_id": window["window_id"],
            "report_subscription_revision": window["subscription_revision"],
            "turn_instance_id": "report." + str(window["window_id"]),
        },
        "response": {
            "native_loopx": {
                "status": "failed",
                "result_kind": "host_failure",
                "host_failure": {"retryable": retryable, "kind": "unknown"},
            }
        },
    }


class _MemoryStore:
    def __init__(self, turns: dict[tuple[str, str], dict[str, object]]) -> None:
        self._turns = turns

    def load_turn(self, session_id: str, turn_id: str) -> dict[str, object] | None:
        return self._turns.get((session_id, turn_id))

    def load_session(self, session_id: str) -> dict[str, object] | None:
        return None

    def events_after(
        self, session_id: str, turn_id: str, event_id: str | None
    ) -> list[dict[str, object]]:
        return []


class _FakeController:
    def __init__(self, root: Path) -> None:
        self.coordination_runtime_root = root
        self.codex_bin = "codex"
        self.codex_home = root


def test_report_failure_split_zero_any_ceilings_and_exports() -> None:
    paths = [
        REPO_ROOT / "loopx/capabilities/native_chat/report_failure_probe.py",
        REPO_ROOT / "loopx/capabilities/native_chat/report_failure_ledger.py",
        REPO_ROOT / "loopx/capabilities/native_chat/report_failure_proof.py",
        Path(__file__).resolve(),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert _any_ast_refs(path) == 0, path
        assert _IGNORE_MARKER not in text, path
        assert len(text.splitlines()) < 750, path

    main_lines = (
        REPO_ROOT / "loopx/capabilities/native_chat/report_failure_proof.py"
    ).read_text(encoding="utf-8").splitlines()
    assert len(main_lines) <= 550

    for symbol in _PINNED_EXPORTS:
        assert hasattr(report_failure_proof, symbol), symbol

    assert report_failure_proof.probe_report_failure is report_failure_probe.probe_report_failure
    assert (
        report_failure_proof.report_failure_binding
        is report_failure_ledger.report_failure_binding
    )


def test_binding_proof_shapes_malformed_inputs_and_key_presence(tmp_path: Path) -> None:
    window = _sample_window(tmp_path)
    original = _sample_original(window)
    binding = report_failure_binding(original, window)
    assert binding is not None
    assert binding["window_id"] == window["window_id"]

    assert report_failure_binding(_sample_original(window, retryable=True), window) is None
    assert report_failure_binding({"origin": "native_loopx", "loopx_request": "bad", "response": 123}, window) is None

    assert (
        valid_report_failure_proof(
            original,
            ["thread-1"],
            {
                "thread_id": "thread-1",
                "turn_id": "codex-turn-1",
                "only_original_user_item": True,
                "codex_error_info": "usageLimitExceeded",
            },
        )
        is True
    )
    assert (
        valid_report_failure_proof(
            original,
            ["thread-1"],
            {
                "thread_id": "thread-1",
                "turn_id": "codex-turn-1",
                "only_original_user_item": True,
                "codex_error_info": "usageLimitExceeded",
                "contradicted": True,
            },
        )
        is False
    )
    assert (
        valid_report_failure_proof(
            _sample_original(window, upstream_turn_id="agy_turn_1"),
            ["cascade-1"],
            {
                "adapter_kind": "antigravity_ls",
                "thread_id": "cascade-1",
                "turn_id": "agy_turn_1",
                "cascade_idle": True,
                "no_final_response": True,
            },
        )
        is True
    )
    assert proof_targets_original_turn(original, ["thread-1"], cast(dict[str, object], "bad")) is False
    assert proof_targets_original_turn(original, ["thread-1"], {"thread_id": 123, "turn_id": "codex-turn-1"}) is False

    # Contradiction ledger rejects corrupt schema/JSON fail-closed and distinguishes explicit null vs deleted key.
    ledger_path = cadence_journal_path(tmp_path, "goal-1").parent / CONTRADICTED_REPORT_FAILURES
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("{corrupt", encoding="utf-8")
    assert is_report_failure_contradicted(tmp_path, original, window) is True
    assert read_retained_report_failure(tmp_path, original, window) is False

    explicit_null_entry = {
        "binding": binding,
        "provider_thread_id": None,
        "only_original_user_item": False,
        "contradicted": True,
    }
    assert report_failure_ledger._is_contradicted_proof(explicit_null_entry, binding) is True
    missing_key_entry = dict(explicit_null_entry)
    del missing_key_entry["provider_thread_id"]
    assert report_failure_ledger._is_contradicted_proof(missing_key_entry, binding) is False

    ledger_path.write_text(
        json.dumps({"schema_version": CONTRADICTED_FAILURES_SCHEMA, "entries": {}}),
        encoding="utf-8",
    )
    assert is_report_failure_contradicted(tmp_path, original, window) is False
    assert probe_report_failure(_FakeController(tmp_path), _MemoryStore({}), _sample_original(window, upstream_turn_id="agy_turn_1")) is None


def test_quota_timeout_request_builders_and_single_pass_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _sample_window(tmp_path)
    codex_original = _sample_original(window)
    store = _MemoryStore({("sess-1", "chat-turn-1"): codex_original})
    controller = _FakeController(tmp_path)
    folder = attempt_directory(tmp_path, window)
    folder.mkdir(parents=True, exist_ok=True)

    quota_req = build_report_quota_request(
        controller,
        store,
        codex_original,
        report_window=window,
        fallback_observe_fn=lambda _orig: {
            "turn_id": "codex-turn-1",
            "thread_id": "thread-1",
            "only_original_user_item": True,
            "codex_error_info": "usageLimitExceeded",
            "quota_available": True,
        },
    )
    assert quota_req is not None
    assert quota_req["turn_instance_id"] == f"report.{window['window_id']}.quota-recovery"
    assert build_report_quota_request(
        controller,
        store,
        {
            **codex_original,
            "loopx_request": {**cast(dict[str, object], codex_original["loopx_request"]), "report_quota_recovery": {}},
        },
        fallback_observe_fn=lambda _orig: None,
    ) is None

    ag_original = _sample_original(window, upstream_turn_id="agy_turn_9")
    timeout_req = build_report_timeout_request(
        ag_original,
        observe_fn=lambda _orig: {
            "adapter_kind": "antigravity_ls",
            "cascade_idle": True,
            "no_final_response": True,
            "native_timeout": True,
            "quota_available": True,
        },
    )
    assert timeout_req is not None
    assert timeout_req["turn_instance_id"] == f"report.{window['window_id']}.timeout-recovery"

    # Verify fallback path (proof=None) performs only 1 post-probe ledger+record read instead of 2+2.
    ledger_reads = 0
    record_reads = 0
    orig_read_ledger = report_failure_ledger._read_contradiction_ledger
    orig_read_record = report_failure_ledger._read_report_failure_record

    def count_ledger(path: Path) -> tuple[str, dict[str, object]]:
        nonlocal ledger_reads
        ledger_reads += 1
        return orig_read_ledger(path)

    def count_record(root: Path, win: dict[str, object]) -> dict[str, object] | None:
        nonlocal record_reads
        record_reads += 1
        return orig_read_record(root, win)

    monkeypatch.setattr(report_failure_ledger, "_read_contradiction_ledger", count_ledger)
    monkeypatch.setattr(report_failure_proof, "_read_contradiction_ledger", count_ledger)
    monkeypatch.setattr(report_failure_ledger, "_read_report_failure_record", count_record)
    monkeypatch.setattr(report_failure_proof, "_read_report_failure_record", count_record)
    ledger_path = cadence_journal_path(tmp_path, "goal-1").parent / CONTRADICTED_REPORT_FAILURES
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({"schema_version": CONTRADICTED_FAILURES_SCHEMA, "entries": {}}),
        encoding="utf-8",
    )
    verified = verify_report_window_failure(
        controller,
        store,
        codex_original,
        window,
        probe_fn=lambda _orig, **_kw: (["thread-1"], None),
    )
    assert verified is True
    assert (folder / REPORT_FAILURE_PROOF).is_file()
    assert ledger_reads == 2  # 1 pre-probe + 1 single post-probe check
    assert record_reads == 1  # single post-probe retained record check

    # Verify Antigravity retain_report_failure rejects non-idle proofs and retains valid agy_turn_ proofs.
    ag_root = tmp_path / "ag-retain"
    ag_win = _sample_window(ag_root)
    ag_orig = _sample_original(ag_win, upstream_turn_id="agy_turn_retain_1")
    ag_store = _MemoryStore({("sess-1", "chat-turn-1"): ag_orig})
    attempt_directory(ag_root, ag_win).mkdir(parents=True, exist_ok=True)
    retain_report_failure(
        ag_root,
        ag_store,
        ag_orig,
        ag_win,
        {"turn_id": "agy_turn_retain_1", "thread_id": "cascade-1", "adapter_kind": "antigravity_ls", "cascade_idle": False, "no_final_response": True},
    )
    assert read_retained_report_failure(ag_root, ag_orig, ag_win) is False
    retain_report_failure(
        ag_root,
        ag_store,
        ag_orig,
        ag_win,
        {"turn_id": "agy_turn_retain_1", "thread_id": "cascade-1", "adapter_kind": "antigravity_ls", "cascade_idle": True, "no_final_response": True},
    )
    assert read_retained_report_failure(ag_root, ag_orig, ag_win) is True

    # Verify contradict_report_failure skips pre-read of attempt file when proof supplies thread_id.
    record_reads = 0
    contradict_report_failure(
        tmp_path,
        store,
        codex_original,
        window,
        {"contradicted": True, "turn_id": "codex-turn-1", "thread_id": "thread-1"},
    )
    # 2 reads come strictly from _invalidate_attempt_proof (existing + readback), 0 pre-ledger read.
    assert record_reads == 2
    assert is_report_failure_contradicted(tmp_path, codex_original, window) is True


def test_paired_origin_main_behavioral_parity_report_failure_proof(
    tmp_path: Path,
) -> None:
    rev = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "origin/main"],
        text=True,
        capture_output=True,
    )
    if rev.returncode != 0:
        pytest.skip("origin/main ref unavailable in shallow checkout")

    baseline_code = subprocess.check_output(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "show",
            "origin/main:loopx/capabilities/native_chat/report_failure_proof.py",
        ],
        text=True,
    )
    script = """
import sys, types
from datetime import datetime, timezone
from pathlib import Path
from loopx.capabilities.native_chat import report_failure_proof as head_mod
from loopx.capabilities.periodic_report.cadence_journal import admit_cadence_window
from loopx.capabilities.periodic_report.private_generation import attempt_directory

spec_mod = types.ModuleType("loopx.capabilities.native_chat.baseline_report_failure_proof")
spec_mod.__package__ = "loopx.capabilities.native_chat"
exec(compile(open(sys.argv[1], encoding="utf-8").read(), "origin_main_rfp.py", "exec"), spec_mod.__dict__)

root_base = Path(sys.argv[2]) / "base"
root_head = Path(sys.argv[2]) / "head"
subscription = {
    "enabled": True,
    "profile_preset": "portfolio-progress",
    "effective_revision": "sha256:" + "1" * 64,
    "schedule": {
        "schema_version": "periodic_report_schedule_v0",
        "schedule_id": "daily-report",
        "timezone": "UTC",
        "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "start_date": "2026-01-01",
    },
}
window = admit_cadence_window(
    runtime_root=root_base, goal_id="goal-1", agent_id="agent-1",
    subscription=subscription, now=datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
)["window"]
admit_cadence_window(
    runtime_root=root_head, goal_id="goal-1", agent_id="agent-1",
    subscription=subscription, now=datetime(2026, 1, 2, 10, tzinfo=timezone.utc),
)
original = {
    "origin": "native_loopx",
    "loopx_execution": True,
    "status": "completed",
    "session_id": "sess-1",
    "turn_id": "chat-turn-1",
    "upstream_turn_id": "codex-turn-1",
    "loopx_request": {
        "actor": {"session_id": "sess-1", "goal_id": "goal-1", "agent_id": "agent-1"},
        "work_kind": "report",
        "report_window_id": window["window_id"],
        "report_subscription_revision": window["subscription_revision"],
        "turn_instance_id": "report." + window["window_id"],
    },
    "response": {
        "native_loopx": {
            "status": "failed",
            "result_kind": "host_failure",
            "host_failure": {"retryable": False, "kind": "unknown"},
        }
    },
}
class Store:
    def load_turn(self, sid, tid): return original if (sid, tid) == ("sess-1", "chat-turn-1") else None
    def load_session(self, sid): return None
    def events_after(self, sid, tid, ev): return []
class Ctrl:
    def __init__(self, root): self.coordination_runtime_root = root; self.codex_bin = "codex"; self.codex_home = root

for root in (root_base, root_head):
    attempt_directory(root, window).mkdir(parents=True, exist_ok=True)

proof = {"turn_id": "codex-turn-1", "thread_id": "t1", "only_original_user_item": True, "codex_error_info": "usageLimitExceeded", "quota_available": True}
assert head_mod.report_failure_binding(original, window) == spec_mod.report_failure_binding(original, window)
assert head_mod.valid_report_failure_proof(original, ["t1"], proof) == spec_mod.valid_report_failure_proof(original, ["t1"], proof)
assert head_mod.verify_report_window_failure(Ctrl(root_head), Store(), original, window, probe_fn=lambda *_a, **_kw: (["t1"], proof)) == spec_mod.verify_report_window_failure(Ctrl(root_base), Store(), original, window, probe_fn=lambda *_a, **_kw: (["t1"], proof))
assert head_mod.verify_report_window_failure(Ctrl(root_head), Store(), original, window, probe_fn=lambda *_a, **_kw: (["t1"], None)) == spec_mod.verify_report_window_failure(Ctrl(root_base), Store(), original, window, probe_fn=lambda *_a, **_kw: (["t1"], None))
assert head_mod.build_report_quota_request(Ctrl(root_head), Store(), original, report_window=window, fallback_observe_fn=lambda _o: proof) == spec_mod.build_report_quota_request(Ctrl(root_base), Store(), original, report_window=window, fallback_observe_fn=lambda _o: proof)
bad_proof = {"turn_id": "codex-turn-1", "thread_id": "t1", "contradicted": True, "explicit_contradiction": True}
assert head_mod.verify_report_window_failure(Ctrl(root_head), Store(), original, window, probe_fn=lambda *_a, **_kw: (["t1"], bad_proof)) == spec_mod.verify_report_window_failure(Ctrl(root_base), Store(), original, window, probe_fn=lambda *_a, **_kw: (["t1"], bad_proof))
assert head_mod.is_report_failure_contradicted(root_head, original, window) == spec_mod.is_report_failure_contradicted(root_base, original, window) is True
assert head_mod.read_retained_report_failure(root_head, original, window) == spec_mod.read_retained_report_failure(root_base, original, window) is False
print("PARITY_OK")
"""
    baseline_file = tmp_path / "origin_main_rfp.py"
    baseline_file.write_text(baseline_code, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", script, str(baseline_file), str(tmp_path)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "PARITY_OK"


def test_interleaving_durable_contradiction_prevents_window_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _sample_window(tmp_path)
    original = _sample_original(window)
    store = _MemoryStore({("sess-1", "chat-turn-1"): original})
    controller = _FakeController(tmp_path)
    folder = attempt_directory(tmp_path, window)
    folder.mkdir(parents=True, exist_ok=True)
    retain_report_failure(
        tmp_path, store, original, window,
        {"turn_id": "codex-turn-1", "thread_id": "thread-1", "only_original_user_item": True},
    )
    assert (folder / REPORT_FAILURE_PROOF).is_file()

    orig_read_record = report_failure_ledger._read_report_failure_record
    triggered = False
    record_reads = 0

    def interleaved_read_record(root: Path, win: dict[str, object]) -> dict[str, object] | None:
        nonlocal triggered, record_reads
        record_reads += 1
        rec = orig_read_record(root, win)
        if not triggered:
            triggered = True
            # Commit durable contradiction while generation.lock holds off attempt overwrite.
            contradict_report_failure(
                root, store, original, win,
                {"contradicted": True, "turn_id": "codex-turn-1", "thread_id": "thread-1"},
            )
        return rec

    monkeypatch.setattr(report_failure_ledger, "_read_report_failure_record", interleaved_read_record)
    monkeypatch.setattr(report_failure_proof, "_read_report_failure_record", interleaved_read_record)

    admitted = admit_cadence_window(
        runtime_root=tmp_path,
        goal_id="goal-1",
        agent_id="agent-1",
        subscription=_SAMPLE_SUBSCRIPTION,
        now=datetime(2026, 1, 4, 10, tzinfo=timezone.utc),
        can_replace_failed_window=lambda w: verify_report_window_failure(
            controller, store, original, w,
            probe_fn=lambda *_a, **_kw: (["thread-1"], None),
            generation_locked=True,
        ),
    )
    assert triggered is True
    assert record_reads == 1
    assert admitted["mutated"] is False
    assert admitted["window"]["window_id"] == window["window_id"]
    assert is_report_failure_contradicted(tmp_path, original, window) is True
    assert read_retained_report_failure(tmp_path, original, window) is False
