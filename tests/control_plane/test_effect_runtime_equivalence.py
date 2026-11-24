"""Paired origin/main vs HEAD behavioral equivalence checks for effect_runtime."""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import subprocess
import sys
import types
from collections.abc import Callable
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import IO

import pytest

from loopx.control_plane import effect_runtime as head_runtime
from loopx.control_plane.runtime.node_probe import NodeProbe, NodeProbeOutcome

_REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _baseline_runtime() -> types.ModuleType:
    completed = subprocess.run(
        ["git", "show", "origin/main:loopx/control_plane/effect_runtime.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"origin/main unavailable for paired check: {completed.stderr.strip()}")
    module = types.ModuleType("_baseline_origin_main_effect_runtime")
    module.__package__ = "loopx.control_plane"
    module.__file__ = str(Path(head_runtime.__file__))
    sys.modules[module.__name__] = module
    guard_patch = pytest.MonkeyPatch()
    guard_patch.setattr(atexit, "register", lambda func, *_a, **_kw: func)
    if hasattr(os, "register_at_fork"):
        guard_patch.setattr(os, "register_at_fork", lambda **_kw: None)
    try:
        exec(
            compile(completed.stdout, "origin/main:loopx/control_plane/effect_runtime.py", "exec"),
            module.__dict__,
        )
    finally:
        guard_patch.undo()
    return module


def _run_paired(
    monkeypatch: pytest.MonkeyPatch,
    fn: Callable[[types.ModuleType, pytest.MonkeyPatch], object],
) -> tuple[object, object]:
    base = _baseline_runtime()
    with monkeypatch.context() as base_ctx:
        base_result = fn(base, base_ctx)
    with monkeypatch.context() as head_ctx:
        head_result = fn(head_runtime, head_ctx)
    return base_result, head_result


def test_public_exports_and_constants_match_origin_main() -> None:
    base = _baseline_runtime()
    expected_constants = (
        "EFFECT_RUNTIME_REQUEST_SCHEMA_VERSION",
        "EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION",
        "EFFECT_RUNTIME_INFO_SCHEMA_VERSION",
        "EFFECT_RUNTIME_READINESS_SCHEMA_VERSION",
        "EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION",
        "EFFECT_RUNTIME_RESTART_SCHEMA_VERSION",
        "MAX_RESPONSE_BYTES",
        "MAX_REQUEST_BYTES",
        "MAX_LOCAL_SNAPSHOT_BYTES",
        "LOCAL_SNAPSHOT_METHODS",
        "MAX_STARTUP_DIAGNOSTIC_BYTES",
        "STARTUP_LOCK_TIMEOUT_SECONDS",
        "STARTUP_POLL_SECONDS",
        "STARTUP_DIAGNOSTIC_GRACE_SECONDS",
        "RUNTIME_LOCATOR_PERMISSION_RETRIES",
        "RUNTIME_RETRY_SETTLE_SECONDS",
        "RUNTIME_LOCATOR_RECOVERY_TIMEOUT_SECONDS",
        "DEFAULT_REQUEST_TIMEOUT_SECONDS",
        "CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS",
        "CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS",
        "MINIMUM_NODE_VERSION",
        "MINIMUM_NODE_VERSION_TEXT",
        "STARTUP_READY_TIMEOUT_SECONDS",
        "HOST_PERMISSION_RECOMMENDATION",
    )
    for name in expected_constants:
        assert getattr(head_runtime, name) == getattr(base, name), name

    base_public = {
        name for name in vars(base)
        if not name.startswith("_") and name != "Any"
    }
    assert base_public <= set(vars(head_runtime))


@pytest.mark.parametrize(
    "payload",
    [
        {"safe": 2**53 - 1, "unsafe": 2**53, "neg_unsafe": -(2**53)},
        {"whole_float": 1.0, "neg_zero": -0.0, "frac": 1.25},
        {"nested": [{"tuple_item": (3.0, 2**54, "ok")}]},
    ],
)
def test_source_number_tokens_and_json_encoding_equivalence(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    def check(mod: types.ModuleType, _ctx: pytest.MonkeyPatch) -> tuple[object, bytes]:
        return mod.source_number_tokens(payload), mod._encode_rpc_json(payload)

    base_out, head_out = _run_paired(monkeypatch, check)
    assert head_out == base_out

    # Lone surrogate escaping and non-finite float rejection match.
    surrogate_payload = {"bad": "\ud800", "ok": "text"}
    assert head_runtime._encode_rpc_json(surrogate_payload) == _baseline_runtime()._encode_rpc_json(
        surrogate_payload
    )
    for bad_float in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            _baseline_runtime().source_number_tokens({"bad": bad_float})
        with pytest.raises(ValueError):
            head_runtime.source_number_tokens({"bad": bad_float})


def test_request_framing_local_snapshot_and_negative_envelope_equivalence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_cases(mod: types.ModuleType, ctx: pytest.MonkeyPatch) -> list[object]:
        ctx.setattr(mod, "MAX_REQUEST_BYTES", 700)
        ctx.setattr(mod, "MAX_LOCAL_SNAPSHOT_BYTES", 2048)
        info = {"host": "127.0.0.1", "port": 1, "token": "tok"}
        outcomes: list[object] = []

        # 1. Disallowed large_local_snapshot method
        with pytest.raises(mod.EffectRuntimeRejected) as exc1:
            mod._request_with_info(
                info, request_id="r1", method="runtime.ping", params={}, timeout=1.0,
                large_local_snapshot=True,
            )
        outcomes.append((exc1.value.diagnostic_code, str(exc1.value)))

        # 2. Inline request exceeding MAX_REQUEST_BYTES without snapshot
        with pytest.raises(mod.EffectRuntimeRejected) as exc2:
            mod._request_with_info(
                info, request_id="r2", method="runtime.ping", params={"blob": "x" * 800}, timeout=1.0,
            )
        outcomes.append((exc2.value.diagnostic_code, str(exc2.value)))

        # 3. Snapshot method exceeding MAX_LOCAL_SNAPSHOT_BYTES
        snapshot_method = next(iter(sorted(mod.LOCAL_SNAPSHOT_METHODS)))
        with pytest.raises(mod.EffectRuntimeRejected) as exc3:
            mod._request_with_info(
                info, request_id="r3", method=snapshot_method, params={"blob": "x" * 2500}, timeout=1.0,
                large_local_snapshot=True,
            )
        outcomes.append((exc3.value.diagnostic_code, str(exc3.value)))

        # 4. Snapshot params_ref + result_ref roundtrip
        observed_requests: list[dict[str, object]] = []

        def fake_tcp(_info: object, encoded: bytes, *, method: str, timeout: float) -> bytes:
            req = json.loads(encoded)
            observed_requests.append(req)
            params_ref = req["params_ref"]
            assert Path(params_ref["path"]).exists()
            response_body = json.dumps({
                "schema_version": mod.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,
                "request_id": req["request_id"],
                "ok": True,
                "result": {"echo": method, "count": 3},
            }).encode("utf-8")
            sink = Path(req["response_sink"])
            sink.write_bytes(response_body)
            return (
                json.dumps({
                    "schema_version": mod.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,
                    "request_id": req["request_id"],
                    "ok": True,
                    "result_ref": {
                        "byte_count": len(response_body),
                        "sha256": hashlib.sha256(response_body).hexdigest(),
                    },
                }).encode("utf-8")
                + b"\n"
            )

        ctx.setattr(mod, "_tcp_response_frame", fake_tcp)
        resp = mod._request_with_info(
            info, request_id="r4", method=snapshot_method, params={"blob": "x" * 800},
            timeout=1.0, large_local_snapshot=True,
        )
        outcomes.append((resp, "params_ref" in observed_requests[0], "params" not in observed_requests[0]))

        # 5. Negative result_ref cases -> EffectRuntimeResponseAmbiguous
        sink_file = tmp_path / "bad-sink.json"
        sink_file.parent.mkdir(parents=True, exist_ok=True)
        sink_file.write_bytes(b"{}")
        os.chmod(sink_file, 0o600)
        for bad_env in (
            {"schema_version": "wrong", "request_id": "r5", "ok": True, "result_ref": {"byte_count": 2, "sha256": "0" * 64}},
            {"schema_version": mod.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION, "request_id": "r5", "ok": True, "result_ref": {"byte_count": 2, "sha256": "0" * 64}},
        ):
            with pytest.raises(mod.EffectRuntimeResponseAmbiguous) as bad_exc:
                mod._read_local_snapshot_response(
                    bad_env, sink_file, method=snapshot_method, request_id="r5", timeout=2.0,
                )
            outcomes.append(bad_exc.value.diagnostic_code)
        return outcomes

    base_out, head_out = _run_paired(monkeypatch, run_cases)
    assert head_out == base_out


def test_projection_fallback_and_todo_context_validation_equivalence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_cases(mod: types.ModuleType, ctx: pytest.MonkeyPatch) -> list[object]:
        calls: list[tuple[str, bool]] = []

        def fake_request(
            method: str,
            params: dict[str, object],
            *,
            timeout: float = 10.0,
            retry_safe: bool = True,
            large_local_snapshot: bool = False,
        ) -> dict[str, object]:
            calls.append((method, large_local_snapshot))
            if method == "todo.context.page":
                return {"result": params.get("_mock_result", {"summary": [], "planning": [], "terminal": []})}
            if not large_local_snapshot:
                raise mod.EffectRuntimeRejected(
                    "too big",
                    diagnostic_code="request_too_large" if params.get("size_err") else "bad_input",
                )
            return {"result": {"projected": True}}

        ctx.setattr(mod, "effect_runtime_request", fake_request)
        ctx.setattr(mod, "_runtime_fingerprint", lambda: "rev-fixed")
        out: list[object] = []

        out.append(mod.effect_runtime_projection_result("m", {"size_err": True}))
        with pytest.raises(mod.EffectRuntimeRejected) as rej:
            mod.effect_runtime_projection_result("m", {"size_err": False})
        out.append(rej.value.diagnostic_code)

        # Scope caching + summary_display_views negative validations
        with mod.effect_runtime_request_scope():
            valid_params = {"summary_display_views": {"view_names": ["summary"]}}
            r1 = mod.effect_runtime_result("todo.context.page", valid_params)
            r2 = mod.effect_runtime_result("todo.context.page", valid_params)
            out.append((r1, r2))
        with pytest.raises(AttributeError):
            mod.effect_runtime_result("todo.context.page", {"summary_display_views": ["not-a-mapping"]})
        ctx.setattr(mod, "effect_runtime_request", lambda *_a, **_kw: {"result": "not-a-dict"})
        with pytest.raises(RuntimeError, match="invalid TypeScript Todo source views"):
            mod.effect_runtime_result("todo.context.page", {"summary_display_views": {"view_names": ["summary"]}})
        ctx.setattr(mod, "effect_runtime_request", lambda *_a, **_kw: {"result": {"summary": []}})
        with pytest.raises(TypeError):
            mod.effect_runtime_result("todo.context.page", {"summary_display_views": {"view_names": 123}})
        out.append(calls)
        return out

    base_out, head_out = _run_paired(monkeypatch, run_cases)
    assert head_out == base_out


def test_error_timeout_and_retry_circuit_breaker_equivalence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_cases(mod: types.ModuleType, ctx: pytest.MonkeyPatch) -> list[object]:
        ctx.setattr(mod, "_runtime_dir", lambda: tmp_path)
        ctx.setattr(mod, "_runtime_fingerprint_for_request", lambda: "a" * 64)
        ctx.setattr(mod, "RUNTIME_RETRY_SETTLE_SECONDS", 0.0)
        info = {"schema_version": mod.EFFECT_RUNTIME_INFO_SCHEMA_VERSION, "fingerprint": "a" * 64, "pid": os.getpid(), "host": "127.0.0.1", "port": 1, "token": "t"}
        ctx.setattr(mod, "_runtime_info_for_request", lambda *_a, **_kw: info)
        outcomes: list[object] = []

        # 1. TimeoutError -> no retry even when retry_safe=True
        attempts = 0

        def fail_with(exc: BaseException) -> Callable[..., object]:
            def handler(*_a: object, **_kw: object) -> object:
                nonlocal attempts
                attempts += 1
                raise exc
            return handler

        orig_request_with_info = mod._request_with_info
        for exc, expected_cls in (
            (TimeoutError("connect timed out"), mod.EffectRuntimeStartupError),
            (mod.EffectRuntimeResponseAmbiguous("m", timeout=1.0), mod.EffectRuntimeResponseAmbiguous),
        ):
            attempts = 0
            ctx.setattr(mod, "_request_with_info", fail_with(exc))
            with pytest.raises(expected_cls) as raised:
                mod.effect_runtime_request("todo.list", {}, retry_safe=True, timeout=2.0)
            outcomes.append((type(raised.value).__name__, raised.value.diagnostic_code, attempts))

        # Pre-connect socket PermissionError -> EffectRuntimeHostPermissionError without retry
        attempts = 0
        ctx.setattr(mod, "_request_with_info", orig_request_with_info)
        ctx.setattr(mod.socket, "create_connection", fail_with(PermissionError("denied")))
        with pytest.raises(mod.EffectRuntimeHostPermissionError) as perm_raised:
            mod.effect_runtime_request("todo.list", {}, retry_safe=True, timeout=2.0)
        outcomes.append((type(perm_raised.value).__name__, perm_raised.value.diagnostic_code, attempts))

        # 2. Pre-send ConnectionRefusedError / StartupError respects retry_safe
        for retry_safe in (True, False):
            attempts = 0
            ctx.setattr(mod, "_request_with_info", fail_with(ConnectionRefusedError("refused")))
            with pytest.raises(mod.EffectRuntimeStartupError) as raised:
                mod.effect_runtime_request("todo.list", {}, retry_safe=retry_safe, timeout=0.05)
            outcomes.append((retry_safe, raised.value.diagnostic_code, attempts))

            attempts = 0
            ctx.setattr(
                mod, "_request_with_info",
                fail_with(mod.EffectRuntimeStartupError("boot fail", diagnostic_code="boot_fail")),
            )
            with pytest.raises(mod.EffectRuntimeStartupError) as raised2:
                mod.effect_runtime_request("todo.list", {}, retry_safe=retry_safe, timeout=0.05)
            outcomes.append((retry_safe, raised2.value.diagnostic_code, attempts))

        # 3. Remote error classification parity
        for err_payload in (
            {"kind": "request_rejected", "code": "bad", "message": "oops", "details": ["d1"]},
            {"kind": "conflict", "code": "c", "message": "conflict"},
            {"kind": "io_transient", "code": "t", "message": "retry"},
            {"kind": "io_permanent", "code": "p", "message": "perm"},
            {"kind": "lock_timeout", "code": "l", "message": "lock"},
            {"kind": "internal_failure", "code": "i", "message": "int"},
            {"kind": "unknown_kind", "code": "u", "message": "unk"},
            "malformed",
        ):
            mapped = mod._remote_runtime_error(err_payload)
            outcomes.append((type(mapped).__name__, mapped.error_kind, mapped.diagnostic_code, str(mapped)))
        return outcomes

    base_out, head_out = _run_paired(monkeypatch, run_cases)
    assert head_out == base_out


def test_locator_inspection_and_recovery_equivalence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fp = "b" * 64

    def run_cases(mod: types.ModuleType, ctx: pytest.MonkeyPatch) -> list[object]:
        ctx.setattr(mod, "STARTUP_POLL_SECONDS", 0.0)
        ctx.setattr(mod, "_pid_is_alive", lambda pid: pid == 1001)
        out: list[object] = []

        missing = tmp_path / "missing.json"
        out.append(asdict(mod._inspect_info(missing, fingerprint=fp)))

        dir_path = tmp_path / "dir.json"
        dir_path.mkdir(exist_ok=True)
        # Simulate Windows directory PermissionError on read_text
        orig_read = Path.read_text
        ctx.setattr(Path, "read_text", lambda self, *a, **kw: (_ for _ in ()).throw(PermissionError("dir")) if self == dir_path else orig_read(self, *a, **kw))
        out.append(asdict(mod._inspect_info(dir_path, fingerprint=fp)))
        ctx.setattr(Path, "read_text", orig_read)

        cases: list[tuple[str, bytes]] = [
            ("bad_json.json", b"{not-json"),
            ("bad_utf8.json", b"\xff\xfe"),
            ("not_dict.json", b"[1, 2]"),
            ("schema.json", json.dumps({"schema_version": "v_bad", "pid": 1001}).encode()),
            ("fp.json", json.dumps({"schema_version": mod.EFFECT_RUNTIME_INFO_SCHEMA_VERSION, "fingerprint": "c" * 64, "pid": 1001}).encode()),
            ("fields.json", json.dumps({"schema_version": mod.EFFECT_RUNTIME_INFO_SCHEMA_VERSION, "fingerprint": fp, "host": "0.0.0.0", "port": "x", "pid": 1001}).encode()),
            ("dead.json", json.dumps({"schema_version": mod.EFFECT_RUNTIME_INFO_SCHEMA_VERSION, "fingerprint": fp, "host": "127.0.0.1", "port": 99, "token": "t", "pid": 9999}).encode()),
            ("valid.json", json.dumps({"schema_version": mod.EFFECT_RUNTIME_INFO_SCHEMA_VERSION, "fingerprint": fp, "host": "127.0.0.1", "port": 99, "token": "t", "pid": 1001}).encode()),
        ]
        for fname, raw in cases:
            p = tmp_path / fname
            p.write_bytes(raw)
            res = mod._inspect_info(p, fingerprint=fp)
            out.append((fname, res.present, res.diagnostic_code, res.recorded_pid_alive, res.info))

        # Recovery accept & reject cases
        bad_locator = mod._RuntimeLocatorRead(
            info=None,
            present=True,
            diagnostic_code="runtime_locator_schema_mismatch",
            recorded_pid_alive=True,
            raw_payload={"host": "127.0.0.1", "port": 99, "token": "tok", "pid": 1001},
        )
        valid_path = tmp_path / "valid.json"
        ctx.setattr(mod, "_request_with_info", lambda *_a, **_kw: {"result": {"ready": True, "pid": 1001, "fingerprint": fp, "info_path": str(valid_path)}})
        out.append(("recover_ok", mod._recover_runtime_info(valid_path, bad_locator, fingerprint=fp)))
        ctx.setattr(mod, "_request_with_info", lambda *_a, **_kw: {"result": {"ready": True, "pid": 2002, "fingerprint": fp, "info_path": str(valid_path)}})
        out.append(("recover_bad_pid", mod._recover_runtime_info(valid_path, bad_locator, fingerprint=fp)))
        return out

    base_out, head_out = _run_paired(monkeypatch, run_cases)
    assert head_out == base_out


def test_sidecar_startup_diagnostic_stop_and_readiness_equivalence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_cases(mod: types.ModuleType, ctx: pytest.MonkeyPatch) -> list[object]:
        out: list[object] = []

        # 1. Startup diagnostic edge cases (U+2028 inside JSON message, noisy stderr, missing code)
        for stderr_bytes in (
            b"",
            b"TypeError: unrelated stack\n  at server.ts:1\n",
            json.dumps({
                "schema_version": mod.EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION,
                "code": "invalid_idle_timeout",
                "message": "bad\u2028value\n   here",
            }).encode("utf-8") + b"\n",
            json.dumps({
                "schema_version": mod.EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION,
                "code": "runtime_host_permission_denied",
                "message": "denied",
            }).encode("utf-8"),
        ):
            out.append(mod._startup_diagnostic(stderr_bytes))

        # 2. Transport env validation
        for transport in ("tcp", "invalid-mode"):
            ctx.setenv("LOOPX_EFFECT_RUNTIME_TRANSPORT", transport)
            if transport == "tcp":
                out.append(("transport", mod._uses_stdio_runtime()))
            else:
                with pytest.raises(mod.EffectRuntimeStartupError) as err:
                    mod._uses_stdio_runtime()
                out.append(("transport_err", err.value.diagnostic_code))
        ctx.delenv("LOOPX_EFFECT_RUNTIME_TRANSPORT", raising=False)

        # 3. _start_runtime ready -> grace diagnostic -> termination
        ctx.setattr(mod, "STARTUP_READY_TIMEOUT_SECONDS", 0.0)
        ctx.setattr(mod, "STARTUP_DIAGNOSTIC_GRACE_SECONDS", 0.01)
        ctx.setattr(mod, "STARTUP_POLL_SECONDS", 0.002)
        ctx.setattr(mod, "_node_executable", lambda: "node")
        ctx.setattr(mod, "_read_info", lambda *_a, **_kw: None)
        terminated = []

        class FakeProc:
            def poll(self) -> int | None:
                return None

            def terminate(self) -> None:
                terminated.append(True)

        def fake_popen(_cmd: object, **kwargs: object) -> FakeProc:
            stderr_file = kwargs["stderr"]
            assert isinstance(stderr_file, IO) or hasattr(stderr_file, "write")
            stderr_file.write(
                json.dumps({
                    "schema_version": mod.EFFECT_RUNTIME_STARTUP_ERROR_SCHEMA_VERSION,
                    "code": "invalid_idle_timeout",
                    "message": "bad idle",
                }).encode("utf-8") + b"\n"
            )
            return FakeProc()

        ctx.setattr(mod.subprocess, "Popen", fake_popen)
        with pytest.raises(mod.EffectRuntimeStartupError) as start_err:
            mod._start_runtime(fingerprint="d" * 64, info_path=tmp_path / f"rt-{mod.__name__}.json")
        out.append(("start_grace_diag", start_err.value.diagnostic_code, len(terminated), not list(tmp_path.glob("start-*.lock"))))

        # 4. _stop_effect_runtime & stdio restart states
        out.append(mod._stop_effect_runtime(tmp_path / "none.json", None, timeout=0.01))
        live_info = {"pid": os.getpid(), "token": "orig"}
        ctx.setattr(mod, "_request_with_info", lambda *_a, **_kw: {"ok": True})
        ctx.setattr(mod, "_serving_token", lambda _p: (True, "replacement"))
        out.append(mod._stop_effect_runtime(tmp_path / "live.json", live_info, timeout=0.05))
        ctx.setattr(mod, "_pid_is_alive", lambda _pid: True)
        out.append(mod._stop_effect_runtime(tmp_path / "live.json", {"pid": os.getpid(), "token": "t"}, timeout=0.01, wait_for_process_exit=True))
        out.append(mod._restart_stdio_runtime(0.01))

        # 5. Readiness projections (ready + unqualified SQLite, missing node, deep failure & mismatch)
        ctx.setattr(mod, "_runtime_dir", lambda: tmp_path / "rdir")
        ctx.setattr(mod, "_runtime_fingerprint", lambda: "e" * 64)
        unqualified_info = {
            "schema_version": mod.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
            "fingerprint": "e" * 64,
            "pid": os.getpid(),
            "host": "127.0.0.1",
            "port": 1234,
            "token": "tok",
            "runtime_identity": {
                "schema_version": "loopx_sqlite_runtime_identity_v0",
                "node_version": "v22.0.0",
                "sqlite_available": True,
                "sqlite_version": "3.45.0",
                "sqlite_source_id": "id",
                "synchronous_statement_finalization": False,
                "sqlite_authority_qualified": False,
                "unavailable_reason": None,
            },
        }
        ctx.setattr(mod, "_probe_node", lambda **_kw: NodeProbe(NodeProbeOutcome.READY, "node", "22.22.3"))
        ctx.setattr(mod, "_inspect_runtime_locator", lambda *_a, **_kw: ("valid", True, None, unqualified_info))
        out.append(mod.collect_effect_runtime_readiness(deep=False))

        ctx.setattr(mod, "_probe_node", lambda **_kw: NodeProbe(NodeProbeOutcome.MISSING))
        out.append(mod.collect_effect_runtime_readiness(deep=False))

        ctx.setattr(mod, "_probe_node", lambda **_kw: NodeProbe(NodeProbeOutcome.READY, "node", "22.22.3"))
        ctx.setattr(mod, "_inspect_runtime_locator", lambda *_a, **_kw: ("missing", None, None, None))
        ctx.setattr(mod, "effect_runtime_result", lambda *_a, **_kw: {"ready": False})
        out.append(mod.collect_effect_runtime_readiness(deep=True))
        return out

    base_out, head_out = _run_paired(monkeypatch, run_cases)
    assert head_out == base_out
