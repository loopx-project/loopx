#!/usr/bin/env python3
"""Run the fixed auxiliary runtime probe after primary session timings."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load_evaluator() -> Any:
    paths = [
        Path(os.environ["C2_EVALUATOR_PATH"]) if os.environ.get("C2_EVALUATOR_PATH") else None,
        HERE / "evaluate.py",
        HERE.parents[1] / "session-calibration" / "harness" / "evaluate.py",
    ]
    path = next((p for p in paths if p is not None and p.is_file()), None)
    if path is None:
        raise RuntimeError("cannot locate the original session-calibration evaluator")
    spec = importlib.util.spec_from_file_location("c2_original_evaluator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _driver() -> Any:
    import session_driver
    for name in ("pid_identity", "runtime_identities", "stop_and_verify", "write_frozen"):
        if not callable(getattr(session_driver, name, None)):
            raise RuntimeError(f"session_driver missing shared helper {name}")
    return session_driver


def _trace_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    assert payload.get("schema") == "c2_rpc_trace_v1"
    rows = payload["rows"]
    assert all(set(row) == {"kind", "start_offset_ns", "duration_ns", "method", "operation", "status", "exception_kind"} for row in rows)
    context = [row for row in rows if row["operation"] in {
        "progressive_context_plan", "context_delivery", "context_recovery"
    }]
    logical_context = [row for row in context if row["kind"] == "typed_rpc"]
    deliveries = [row for row in logical_context if row["operation"] == "context_delivery"]
    txn = None
    if context and deliveries:
        first = min(row["start_offset_ns"] for row in logical_context)
        last_end = max(row["start_offset_ns"] + row["duration_ns"] for row in deliveries)
        if last_end >= first:
            txn = last_end - first
    return {
        "managed_start_ns": [r["duration_ns"] for r in rows if r["kind"] == "managed_start"],
        "typed_rpc_ns": [r["duration_ns"] for r in rows if r["kind"] == "typed_rpc"],
        "wire_attempt_ns": [r["duration_ns"] for r in rows if r["kind"] == "wire_attempt"],
        "context_rpc_count": sum(r["kind"] == "typed_rpc" for r in context),
        "context_wire_attempt_count": sum(r["kind"] == "wire_attempt" for r in context),
        "context_operation_counts": {
            operation: sum(r["kind"] == "typed_rpc" and r["operation"] == operation for r in context)
            for operation in ("progressive_context_plan", "context_delivery", "context_recovery")
        },
        "context_transaction_ns": txn,
    }


def _stats(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None}
    ordered = sorted(v / 1e6 for v in values)
    index = (len(ordered) - 1) * .95
    lo = int(index)
    return {"n": len(ordered), "p50_ms": statistics.median(ordered),
            "p95_ms": ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (index - lo)}


def _rss(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError(f"VmRSS unavailable for runtime pid {pid}")


def _process_snapshot(driver: Any, pid: int) -> dict[str, Any]:
    identity = driver.pid_identity(pid)
    if not isinstance(identity, dict) or identity.get("pid") != pid:
        raise RuntimeError(f"runtime identity changed for pid {pid}")
    if identity.get("uid") != os.getuid():
        raise RuntimeError(f"runtime uid mismatch for pid {pid}")
    affinity = identity.get("affinity")
    if affinity is None or sorted(affinity) != sorted(os.sched_getaffinity(0)):
        raise RuntimeError(f"runtime affinity mismatch for pid {pid}")
    if type(identity.get("starttime_ticks")) is not int:
        raise RuntimeError(f"runtime starttime_ticks missing for pid {pid}")
    return {"pid": pid, "uid": identity["uid"], "affinity": affinity,
            "starttime_ticks": identity["starttime_ticks"], "rss_bytes": _rss(pid)}


def _same_process(before: dict[str, Any], after: dict[str, Any]) -> None:
    for field in ("pid", "uid", "affinity", "starttime_ticks"):
        if before[field] != after[field]:
            raise RuntimeError(f"warm runtime {field} changed during RSS sampling")


def _assert_fresh_cold_pids(pids: list[int]) -> None:
    if len(pids) != 16 or len(set(pids)) != 16:
        raise RuntimeError("cold CLI runs must each start a fresh daemon")


def _assert_stable_warm_pids(pids: list[int]) -> int:
    if len(pids) != 10 or len(set(pids)) != 1:
        raise RuntimeError("warmups must establish one stable daemon")
    return pids[-1]


def _reported_pids(value: Any) -> set[int]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError("runtime_identities must return a list of identity records")
    if any(type(item.get("starttime_ticks")) is not int for item in value):
        raise RuntimeError("runtime identity lacks starttime_ticks")
    return {item["pid"] for item in value if type(item.get("pid")) is int}


def _intercept(e: Any, trace: Path):
    """Swap only the evaluator's exact entrypoint subprocess boundary."""
    original = e.subprocess.run
    script = Path(__file__).resolve().with_name("rpc_probe.py")
    def run(command: Any, *args: Any, **kwargs: Any) -> Any:
        if (isinstance(command, (list, tuple)) and len(command) >= 3
                and command[1:3] == ["-m", "loopx.entrypoint"]):
            command = [command[0], str(script), *command[3:]]
            env = dict(kwargs.get("env") or os.environ)
            env["C2_RPC_TRACE_PATH"] = str(trace)
            kwargs["env"] = env
        return original(command, *args, **kwargs)
    e.subprocess.run = run
    return original


def _stop(driver: Any, e: Any, namespace: str) -> None:
    driver.stop_and_verify(e, namespace)


def _run_cli(e: Any, driver: Any, manifest: dict[str, Any], arm: str, mode: str,
             namespace: str, trace: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    original = _intercept(e, trace)
    try:
        row, _ = e.run(manifest, arm, mode, namespace)
    except BaseException as exc:
        rows.append({"arm": arm, "mode": mode, "error_kind": type(exc).__name__})
        raise
    finally:
        e.subprocess.run = original
    trace_row = _trace_summary(trace)
    rows.append({"arm": arm, "mode": mode, "runtime_pid": row["runtime_pid"],
                 **trace_row})
    return {**row, **trace_row}


def _source_mapping(e: Any) -> dict[str, str]:
    config = e.CONFIG
    mapping = config.get("source_slot_by_arm")
    if not isinstance(mapping, dict) or set(mapping) != {"base", "head"}:
        raise RuntimeError("evaluation_config must declare source_slot_by_arm for both semantic arms")
    if set(mapping.values()) != {"base", "head"}:
        raise RuntimeError("source_slot_by_arm must map semantic arms to distinct physical slots")
    for arm, slot in mapping.items():
        if Path(e.SOURCES[arm]).name != slot:
            raise RuntimeError(f"semantic arm {arm} does not point at declared physical source slot {slot}")
    return mapping


def run() -> int:
    e, driver = _load_evaluator(), _driver()
    slot_map = _source_mapping(e)
    results_root = Path(os.environ.get("C2_AUX_OUTPUT_ROOT", str(e.ROOT)))
    row_dir, trace_dir = results_root / "aux-probe-rows", results_root / "aux-probes"
    row_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, Any]] = []
    case_paths = sorted((e.ROOT / "evaluation-fixtures").glob("*/manifest.json"))
    if not case_paths:
        raise RuntimeError("no prepared evaluation fixture manifests found")
    for manifest_path in case_paths:
        manifest = json.loads(manifest_path.read_text())
        provider, history = manifest["provider"], manifest["history"]
        for mode in e.CONFIG["modes"]:
            traces: dict[str, list[dict[str, Any]]] = {"base": [], "head": []}
            rss: dict[str, Any] = {}
            cleanup_errors: list[dict[str, Any]] = []
            owner_ns = {arm: f"aux-{provider}-{history}-{mode}-{arm}" for arm in ("base", "head")}
            try:
                for arm in ("base", "head"):
                    _stop(driver, e, owner_ns[arm])
                for arm in ("base", "head"):
                    cold_pids = []
                    for index in range(16):
                        for owner in ("base", "head"):
                            _stop(driver, e, owner_ns[owner])
                        trace = trace_dir / f"{provider}-{history}-{mode}-{arm}-cold-{index}.json"
                        row = _run_cli(e, driver, manifest, arm, "cold-daemon", owner_ns[arm], trace, traces[arm])
                        cold_pid = row["runtime_pid"]
                        if len(row["managed_start_ns"]) != 1:
                            raise RuntimeError(f"cold CLI did not measure one managed startup for {arm}")
                        if cold_pid not in _reported_pids(driver.runtime_identities(e, owner_ns[arm])):
                            raise RuntimeError(f"cold runtime identity missing for {arm}")
                        cold_pids.append(cold_pid)
                    _assert_fresh_cold_pids(cold_pids)
                for arm in ("base", "head"):
                    _stop(driver, e, owner_ns[arm])
                for arm in ("base", "head"):
                    for index in range(10):
                        trace = trace_dir / f"{provider}-{history}-{mode}-{arm}-warmup-{index}.json"
                        _run_cli(e, driver, manifest, arm, "warm", owner_ns[arm], trace, traces[arm])
                    warm_pids = [row["runtime_pid"] for row in traces[arm][-10:]]
                    warm_pid = _assert_stable_warm_pids(warm_pids)
                    if warm_pid not in _reported_pids(driver.runtime_identities(e, owner_ns[arm])):
                        raise RuntimeError(f"warm runtime identity missing for {arm}")
                    rss[arm] = {"after_warmup": _process_snapshot(driver, warm_pid)}
                    for index in range(16):
                        trace = trace_dir / f"{provider}-{history}-{mode}-{arm}-burst-{index}.json"
                        row = _run_cli(e, driver, manifest, arm, "warm", owner_ns[arm], trace, traces[arm])
                        if row["runtime_pid"] != warm_pid:
                            raise RuntimeError(f"bounded burst changed warm daemon for {arm}")
                    if len(traces[arm]) != 42:
                        raise RuntimeError(f"auxiliary CLI count mismatch for {arm}")
                    rss[arm]["after_burst"] = _process_snapshot(driver, warm_pid)
                    _same_process(rss[arm]["after_warmup"], rss[arm]["after_burst"])
                time.sleep(5)
                for arm in ("base", "head"):
                    pid = rss[arm]["after_warmup"]["pid"]
                    rss[arm]["after_idle_5s"] = _process_snapshot(driver, pid)
                    _same_process(rss[arm]["after_warmup"], rss[arm]["after_idle_5s"])
                result = {
                    "schema": "c2_auxiliary_probe_v1", "provider": provider,
                    "history": history, "mode": mode, "replica": e.CONFIG["replica"],
                    "source_slot_by_arm": slot_map,
                    "source_sha_by_arm": {arm: e.CONFIG["baseline" if arm == "base" else "candidate_head"]
                                           for arm in ("base", "head")},
                    "arms": {}, "daemon_rss": rss,
                }
                for arm in ("base", "head"):
                    rs = traces[arm]
                    cold = [r for r in rs if r["mode"] == "cold-daemon"]
                    typed = [n for r in rs for n in r["typed_rpc_ns"]]
                    wire = [n for r in rs for n in r["wire_attempt_ns"]]
                    txns = [r["context_transaction_ns"] for r in rs if r["context_transaction_ns"] is not None]
                    result["arms"][arm] = {
                        "cold_start": _stats([n for r in cold for n in r["managed_start_ns"]]),
                        "typed_rpc": _stats(typed), "wire_attempt": _stats(wire),
                        "typed_rpc_count": len(typed), "wire_attempt_count": len(wire),
                        "context_transaction": _stats(txns),
                        "context_rpc_count": sum(r["context_rpc_count"] for r in rs),
                        "context_wire_attempt_count": sum(r["context_wire_attempt_count"] for r in rs),
                        "context_operation_counts": {
                            operation: sum(r.get("context_operation_counts", {}).get(operation, 0) for r in rs)
                            for operation in ("progressive_context_plan", "context_delivery", "context_recovery")
                        },
                        "cli_count": len(rs),
                    }
                target = row_dir / f"result-{provider}-{history}-{mode}-r{e.CONFIG['replica']}.json"
                _write_frozen(driver, target, result)
            except BaseException as exc:
                errors.append({"provider": provider, "history": history, "mode": mode,
                               "error_kind": type(exc).__name__})
                _write_frozen(driver, row_dir / f"partial-{provider}-{history}-{mode}-r{e.CONFIG.get('replica', 'unknown')}.json",
                              {"partial": traces, "error": errors[-1]})
                raise
            finally:
                for arm in ("base", "head"):
                    try:
                        _stop(driver, e, owner_ns[arm])
                    except BaseException as exc:
                        failure = {"arm": arm, "error_kind": type(exc).__name__}
                        errors.append(failure)
                        cleanup_errors.append(failure)
            if cleanup_errors:
                target = row_dir / f"cleanup-error-{provider}-{history}-{mode}-r{e.CONFIG.get('replica', 'unknown')}.json"
                _write_frozen(driver, target, {"errors": cleanup_errors})
                raise RuntimeError(f"auxiliary daemon cleanup failed for {provider}-{history}/{mode}")
    print(json.dumps({"status": "complete", "cases": len(case_paths), "errors": errors}))
    return 0


def _write_frozen(driver: Any, path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    driver.write_frozen(path, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true")
    group.add_argument("--self-check", action="store_true")
    group.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.cleanup_only:
        e, driver = _load_evaluator(), _driver()
        for manifest_path in sorted((e.ROOT / "evaluation-fixtures").glob("*/manifest.json")):
            manifest = json.loads(manifest_path.read_text())
            for mode in e.CONFIG["modes"]:
                for arm in ("base", "head"):
                    _stop(driver, e, f"aux-{manifest['provider']}-{manifest['history']}-{mode}-{arm}")
        return 0
    return run()


def self_check() -> int:
    """Check privacy, timing counts, and real external-process identity cleanup."""
    import importlib.util
    import tempfile
    import types
    from rpc_probe import _operation
    driver_paths = (HERE / "session_driver.py", HERE.parent / "driver" / "session_driver.py")
    driver_path = next((path for path in driver_paths if path.is_file()), None)
    if driver_path is None:
        raise RuntimeError("probe self-check requires the staged session_driver adapter")
    spec = importlib.util.spec_from_file_location("session_driver", driver_path)
    assert spec and spec.loader
    shared_driver = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = shared_driver
    spec.loader.exec_module(shared_driver)
    with tempfile.TemporaryDirectory() as temp:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            first = process.pid
            assert first > 0
            identity = shared_driver.pid_identity(first)
            assert set(identity) == {"pid", "uid", "affinity", "starttime_ticks"}
            assert _process_snapshot(shared_driver, first)["starttime_ticks"] == identity["starttime_ticks"]
            assert _reported_pids([identity]) == {first}
            assert _operation("quota.turn_envelope.evaluate", {"operation": "context_delivery"}) == "context_delivery"
            fake_trace = Path(temp) / "trace.json"
            fake_trace.write_text(json.dumps({"schema": "c2_rpc_trace_v1", "rows": [
                {"kind": "wire_attempt", "start_offset_ns": 31, "duration_ns": 2, "method": "m", "operation": "context_delivery", "status": "ok", "exception_kind": None},
                {"kind": "typed_rpc", "start_offset_ns": 10, "duration_ns": 7, "method": "m", "operation": "progressive_context_plan", "status": "ok", "exception_kind": None},
                {"kind": "typed_rpc", "start_offset_ns": 20, "duration_ns": 5, "method": "m", "operation": "context_recovery", "status": "ok", "exception_kind": None},
                {"kind": "typed_rpc", "start_offset_ns": 40, "duration_ns": 3, "method": "m", "operation": "context_delivery", "status": "ok", "exception_kind": None},
            ]}))
            summary = _trace_summary(fake_trace)
            assert summary["context_rpc_count"] == 3 and summary["context_wire_attempt_count"] == 1
            assert summary["context_operation_counts"]["context_delivery"] == 1
            assert summary["context_transaction_ns"] == 33
            assert "params" not in fake_trace.read_text()
            for mapping in ({"base": "base", "head": "head"},
                            {"base": "head", "head": "base"}):
                mapped = types.SimpleNamespace(
                    CONFIG={"source_slot_by_arm": mapping},
                    SOURCES={arm: f"/tmp/sources/{slot}" for arm, slot in mapping.items()},
                )
                assert _source_mapping(mapped) == mapping
                mapped.TMP = Path(temp)
                stopped_sources = []
                mapped.stop_runtime = lambda arm, namespace=None: stopped_sources.append(mapped.SOURCES[arm])
                shared_driver.stop_and_verify(mapped, f"selfcheck-{mapping['base']}-{mapping['head']}")
                assert stopped_sources == [mapped.SOURCES["base"], mapped.SOURCES["head"]]
            _assert_fresh_cold_pids(list(range(1, 17)))
            _assert_stable_warm_pids([42] * 10)
            for invalid, check in (([1] * 16, _assert_fresh_cold_pids), ([1, 2] * 5, _assert_stable_warm_pids)):
                try:
                    check(invalid)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("runtime lifecycle assertion accepted an invalid PID sequence")

            seen: list[tuple[str, str]] = []
            evaluator = types.SimpleNamespace(
                SOURCES={"base": "baseline-source", "head": "candidate-source"},
                subprocess=subprocess,
                TMP=Path(temp),
                stop_runtime=lambda arm, namespace=None: seen.append((namespace, arm)),
            )
            def failing_cli(manifest: Any, arm: str, mode: str, namespace: str) -> Any:
                evaluator.stop_runtime(arm, namespace)
                evaluator.subprocess.run([sys.executable, "-c", "pass"], check=True)
                raise ValueError("private CLI details")
            evaluator.run = failing_cli
            original_run, original_stop = evaluator.subprocess.run, evaluator.stop_runtime
            for arm in ("base", "head"):
                try:
                    _run_cli(evaluator, shared_driver, {}, arm, "cold-daemon", f"aux-{arm}", Path(temp) / f"{arm}.json", [])
                except ValueError:
                    pass
                else:
                    raise AssertionError("probe swallowed a CLI error")
                assert evaluator.subprocess.run is original_run and evaluator.stop_runtime is original_stop
                _stop(shared_driver, evaluator, f"aux-{arm}")
            assert seen == [("aux-base", "base"), ("aux-base", "base"), ("aux-base", "head"),
                            ("aux-head", "head"), ("aux-head", "base"), ("aux-head", "head")]
            evaluator.run = lambda *_: ({"runtime_pid": first}, b"{}")
            success_rows = []
            result = _run_cli(evaluator, shared_driver, {}, "head", "warm", "aux-head", fake_trace, success_rows)
            assert result["managed_start_ns"] == summary["managed_start_ns"]
            assert result["context_rpc_count"] == summary["context_rpc_count"]
            assert success_rows[0]["runtime_pid"] == first
            process.terminate()
            process.wait(timeout=3)
            assert process.poll() is not None
            print("AUXILIARY_PROBE_SELFCHECK ok disposable-process=stopped counts=1/1 trace=private")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
