#!/usr/bin/env python3
"""Run the real LoopX entrypoint while recording privacy-safe RPC spans."""
from __future__ import annotations

import json
import os
import runpy
import sys
import time
from pathlib import Path
from collections.abc import Mapping
from typing import Any


def _operation(method: str, params: Any) -> str | None:
    if method != "quota.turn_envelope.evaluate" or not isinstance(params, Mapping):
        return None
    operation = params.get("operation")
    return operation if operation in {"progressive_context_plan", "context_delivery", "context_recovery"} else None


def _record(rows: list[dict[str, Any]], kind: str, started: int, origin: int, method: str | None,
            operation: str | None, error: BaseException | None = None) -> None:
    rows.append({
        "kind": kind,
        "start_offset_ns": started - origin,
        "duration_ns": time.perf_counter_ns() - started,
        "method": method,
        "operation": operation,
        "status": "exception" if error else "ok",
        "exception_kind": type(error).__name__ if error else None,
    })


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--self-check":
        return _self_check()
    trace_path = os.environ.get("C2_RPC_TRACE_PATH")
    if not trace_path:
        raise SystemExit("C2_RPC_TRACE_PATH is required for an instrumented entrypoint")
    rows: list[dict[str, Any]] = []
    origin = time.perf_counter_ns()
    import loopx.control_plane.effect_runtime as runtime

    originals = instrument(runtime, rows, origin)
    try:
        sys.argv = ["loopx.entrypoint", *argv]
        runpy.run_module("loopx.entrypoint", run_name="__main__")
    finally:
        restore(runtime, originals)
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
        Path(trace_path).write_text(json.dumps({"schema": "c2_rpc_trace_v1", "rows": rows}) + "\n")
    return 0


def instrument(runtime: Any, rows: list[dict[str, Any]], origin: int) -> tuple[Any, Any, Any]:
    original_start = runtime._start_runtime
    original_request = runtime.effect_runtime_request
    original_wire = runtime._request_with_info

    def start(*args: Any, **kwargs: Any) -> Any:
        began = time.perf_counter_ns()
        error = None
        try:
            return original_start(*args, **kwargs)
        except BaseException as exc:
            error = exc
            raise
        finally:
            _record(rows, "managed_start", began, origin, None, None, error)

    def request(method: str, params: Any, *args: Any, **kwargs: Any) -> Any:
        began = time.perf_counter_ns()
        operation = _operation(method, params)
        error = None
        try:
            return original_request(method, params, *args, **kwargs)
        except BaseException as exc:
            error = exc
            raise
        finally:
            _record(rows, "typed_rpc", began, origin, method, operation, error)

    def wire(info: Any, *, method: str, params: Any, **kwargs: Any) -> Any:
        began = time.perf_counter_ns()
        operation = _operation(method, params)
        error = None
        try:
            return original_wire(info, method=method, params=params, **kwargs)
        except BaseException as exc:
            error = exc
            raise
        finally:
            _record(rows, "wire_attempt", began, origin, method, operation, error)

    runtime._start_runtime = start
    runtime.effect_runtime_request = request
    runtime._request_with_info = wire
    return original_start, original_request, original_wire


def restore(runtime: Any, originals: tuple[Any, Any, Any]) -> None:
    runtime._start_runtime, runtime.effect_runtime_request, runtime._request_with_info = originals


def _self_check() -> int:
    """Exercise instrumentation against a fake runtime module boundary."""
    import tempfile
    import types

    rows: list[dict[str, Any]] = []
    fake = types.SimpleNamespace()
    def start(**kwargs: Any) -> dict[str, Any]:
        if kwargs.get("fail"):
            raise ValueError("private payload")
        return {"ok": True}
    def request(method: str, params: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "params": params}
    def wire(info: Any, *, method: str, params: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}
    fake._start_runtime, fake.effect_runtime_request, fake._request_with_info = start, request, wire
    origin = time.perf_counter_ns()
    originals = instrument(fake, rows, origin)
    assert _operation("quota.turn_envelope.evaluate", {"operation": "context_delivery", "native_projection": {}}) == "context_delivery"
    assert _operation("quota.turn_envelope.evaluate", {"operation": "context_recovery"}) == "context_recovery"
    payload = {"secret": "must-not-appear"}
    response = fake.effect_runtime_request("quota.turn_envelope.evaluate", payload)
    assert response["params"] is payload
    assert fake._request_with_info({}, method="m", params=payload) == {"ok": True}
    try:
        fake._start_runtime(fail=True)
    except ValueError:
        pass
    else:
        raise AssertionError("instrumented start must preserve exceptions")
    restore(fake, originals)
    assert rows[-1]["exception_kind"] == "ValueError" and "secret" not in json.dumps(rows)
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "trace.json"
        target.write_text(json.dumps({"rows": rows}))
        assert json.loads(target.read_text())["rows"][1]["kind"] == "wire_attempt"
        assert "must-not-appear" not in target.read_text()
    print("RPC_PROBE_SELFCHECK ok privacy=redacted return-preserved exception-kind-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
