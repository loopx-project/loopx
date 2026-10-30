#!/usr/bin/env python3
"""Offline structural validation and fixed-session decision scores."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import sys
import base64
from collections import defaultdict
from pathlib import Path

BASE = "04c5799b88f6c4cf12a224b7fa679f272e1e794e"
HEAD = "e42c536e609ba383217a3e83b8cf07c72542944c"
ROLES = ("aa_base", "aa_compare", "ab_base", "ab_compare")
ROLE_ARM = {"aa_base": "base", "aa_compare": "base", "ab_base": "base", "ab_compare": "head"}
ROLE_LABEL = {"aa_base": "base", "aa_compare": "compare", "ab_base": "base", "ab_compare": "compare"}
PHASES = ("aa_primary", "aa_conditioning", "ab")
CASES = {(p, h, m) for p in ("file", "sqlite") for h in (0, 500)
         for m in ("cold-daemon", "warm")}
TRIALS = 5000


class Invalid(ValueError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def quantile(values: list[float], q: float) -> float:
    if not values:
        raise Invalid("cannot calculate quantile without raw primary rows")
    ordered = sorted(values)
    i = (len(ordered) - 1) * q
    lo = int(i)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (i - lo)


def resource_paths(script: Path) -> tuple[Path, Path]:
    if script.parent.name == "analysis":
        root = script.parent.parent
        return root / "harness", root / "driver" / "session_driver.py"
    return script.parent, script.parent / "session_driver.py"


def frozen_schedule() -> dict:
    return frozen_schedule_for_script(Path(__file__).resolve())


def frozen_schedule_for_script(script: Path) -> dict:
    _, driver_path = resource_paths(script)
    if not driver_path.is_file():
        raise Invalid(f"frozen schedule driver is missing: {driver_path}")
    spec = importlib.util.spec_from_file_location("qualification_frozen_driver", driver_path)
    if spec is None or spec.loader is None:
        raise Invalid("cannot load the frozen qualification driver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.source_schedule()


def expected_rows(schedule: dict) -> list[dict]:
    rows = []
    for role in schedule["startup_order"]:
        for ordinal in range(1, 11):
            rows.append({"phase": "warmup", "role": role, "invocation_ordinal": ordinal - 1,
                         "warmup_ordinal": ordinal, "block": None, "round": None,
                         "position": None, "pair_order": [role], "workload_age": None})
    ordinal = {role: 9 for role in ROLES}
    for block in schedule["source_schedule"]["blocks"]:
        for rd in block["rounds"]:
            for position, pair in enumerate(rd["pairs"]):
                for role in pair["order"]:
                    ordinal[role] += 1
                    rows.append({"phase": pair["phase"], "role": role,
                                 "invocation_ordinal": ordinal[role], "warmup_ordinal": None,
                                 "block": block["block"], "round": rd["round"],
                                 "position": position, "pair_order": pair["order"],
                                 "workload_age": rd["age"]})
    return rows


def validate_config(c: dict, s: dict, manifest_slot: dict) -> None:
    key = (s["provider"], s["history"], s["mode"], s["replica"])
    if (c.get("experiment_kind") != "fixed_session_candidate_qualification"
            or (c.get("provider"), c.get("history"), c.get("mode"), c.get("replica")) != key
            or c.get("providers") != [s["provider"]] or c.get("history_counts") != [s["history"]]
            or c.get("modes") != [s["mode"]] or c.get("qualification_job_cap_seconds") != 5400
            or c.get("aa_pairs") != 160 or c.get("ab_pairs") != 320
            or c.get("warmups_per_arm_per_phase") != 10):
        raise Invalid("evaluation config case or workload mismatch")
    if (c.get("baseline_source_sha") != BASE or c.get("candidate_source_sha") != HEAD
            or c.get("baseline") != BASE or c.get("candidate_head") != HEAD):
        raise Invalid("evaluation config source pins mismatch")
    mapping = s.get("source_slot_by_arm")
    if (mapping not in ({"base": "base", "head": "head"}, {"base": "head", "head": "base"})
            or mapping != manifest_slot.get("source_slot_by_arm")
            or c.get("source_slot_by_arm") != mapping):
        raise Invalid("semantic source arms do not match the preregistered physical slots")
    if s.get("startup_order") != [*ROLES[s["replica"]:], *ROLES[:s["replica"]]]:
        raise Invalid("startup order is not the frozen Latin rotation")
    sources = s.get("actual_sources")
    if sources != {"base": BASE, "head": HEAD}:
        raise Invalid("slot actual source pins mismatch")
    paths = {"base": c.get("baseline_source"), "head": c.get("candidate_source")}
    if any(not isinstance(paths[arm], str) or not paths[arm].endswith("/" + mapping[arm]) for arm in mapping):
        raise Invalid("source paths do not agree with semantic-to-physical slot map")


def validate_schedule(schedule: dict, c: dict, slot: dict, manifest_sha: str) -> None:
    core = {k: v for k, v in schedule.items() if k != "schedule_sha256"}
    src = schedule.get("source_schedule", {})
    if (digest(core) != schedule.get("schedule_sha256")
            or schedule.get("experiment_kind") != "fixed_session_candidate_qualification"
            or any(schedule.get(k) != v for k, v in {
                "provider": slot["provider"], "history": slot["history"], "mode": slot["mode"],
                "replica": slot["replica"], "actual_sources": {"base": BASE, "head": HEAD},
                "role_source_arms": ROLE_ARM, "startup_order": slot["startup_order"],
                "source_slot_by_arm": slot["source_slot_by_arm"],
                "calibration_manifest_sha256": manifest_sha,
                "accounting": {"blocks": 160, "rounds_per_block": 2, "primary_cli": 960,
                    "conditioning_cli": 320, "warmup_cli": 40, "factory_setup_cli": 17,
                    "session_rows": 1320, "domain_cli_per_session": 1421},
                "workload_age": "10 + 2*block + round; all roles 10..329"}.items())
            or sorted(slot.get("startup_order", [])) != sorted(ROLES)
            or src != frozen_schedule()):
        raise Invalid("session schedule hash or frozen controls mismatch")


def validate_rows(rows: list[dict], schedule: dict, slot: dict, c: dict) -> dict:
    wanted = expected_rows(schedule)
    if len(rows) != 1320 or len(rows) != len(wanted):
        raise Invalid(f"row count {len(rows)} != 1320")
    counts = defaultdict(int)
    warm_ids, warm_pids, measured_ids, out_hashes = {}, {}, set(), {}
    prev_outer = 0
    mapping = slot["source_slot_by_arm"]
    for i, (row, expect) in enumerate(zip(rows, wanted)):
        if any(row.get(k) != v for k, v in expect.items()):
            raise Invalid(f"row {i} schedule/age/order mismatch")
        role, arm = expect["role"], ROLE_ARM[expect["role"]]
        physical = mapping[arm]
        actual_sha = BASE if arm == "base" else HEAD
        source_path = c.get("baseline_source" if arm == "base" else "candidate_source")
        if (row.get("record_type") != "sample" or row.get("experiment_kind") != c["experiment_kind"]
                or any(row.get(k) != slot[k] for k in ("provider", "history", "mode", "replica"))
                or row.get("logical_label") != ROLE_LABEL[role] or row.get("source_arm") != arm
                or row.get("source_slot") != physical or row.get("actual_source_sha") != actual_sha
                or row.get("actual_source") != source_path
                or row.get("namespace") != f"c2-session-{slot['mode']}-r{slot['replica']}-{role}"
                or row.get("cli_returncode") != 0 or row.get("error") not in (None, "")):
            raise Invalid(f"row {i} case/source/role identity mismatch")
        identity = row.get("pid_identity", {})
        benchmark = c.get("benchmark_identity", {})
        if (type(identity.get("pid")) is not int or type(identity.get("uid")) is not int
                or type(identity.get("starttime_ticks")) is not int or identity["pid"] <= 0
                or identity["starttime_ticks"] <= 0 or identity.get("uid") != benchmark.get("uid")
                or identity.get("affinity") != c.get("cpu_affinity")):
            raise Invalid(f"row {i} process identity mismatch")
        ident = (identity["pid"], identity["starttime_ticks"], identity["uid"], tuple(identity["affinity"]))
        if expect["phase"] == "warmup":
            if role in warm_ids and warm_ids[role] != ident:
                raise Invalid(f"row {i} warmup identity changed")
            warm_ids[role] = ident
            warm_pids[role] = identity["pid"]
        elif slot["mode"] == "warm" and ident != warm_ids.get(role):
            raise Invalid(f"row {i} warm process identity changed")
        elif slot["mode"] == "cold-daemon":
            k = (identity["pid"], identity["starttime_ticks"])
            if k in measured_ids or any(k[:2] == old[:2] for old in warm_ids.values()):
                raise Invalid(f"row {i} cold invocation reused a process")
            measured_ids.add(k)
        ms = row.get("cli_ms")
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not math.isfinite(ms) or ms <= 0:
            raise Invalid(f"row {i} invalid cli_ms")
        stdout, sha = row.get("raw_stdout"), row.get("stdout_sha256")
        try:
            stdout_bytes = base64.b64decode(row.get("raw_stdout_base64", ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise Invalid(f"row {i} stdout base64 is invalid") from exc
        if (not isinstance(stdout, str) or hashlib.sha256(stdout_bytes).hexdigest() != sha
                or stdout_bytes.decode("utf-8", "replace") != stdout):
            raise Invalid(f"row {i} stdout hash mismatch")
        group = "warmup" if expect["phase"] == "warmup" else "measured"
        if group in out_hashes and out_hashes[group] != sha:
            raise Invalid(f"row {i} stdout differs within phase group")
        out_hashes[group] = sha
        st, en = row.get("cli_start_monotonic_ns"), row.get("cli_end_monotonic_ns")
        os_, oe = row.get("invocation_outer_start_monotonic_ns"), row.get("invocation_outer_end_monotonic_ns")
        if (any(type(v) is not int or v <= 0 for v in (st, en, os_, oe))
                or not prev_outer <= os_ <= st < en <= oe or abs(ms - (en - st) / 1e6) > .001):
            raise Invalid(f"row {i} monotonic bounds mismatch")
        prev_outer = oe
        if expect["phase"] != "warmup":
            counts[expect["phase"]] += 1
    if len(warm_ids) != 4 or len(set(warm_pids.values())) != 4:
        raise Invalid("warmup lacks four distinct role process identities")
    if dict(counts) != {"aa_primary": 320, "aa_conditioning": 320, "ab": 640}:
        raise Invalid(f"phase counts mismatch: {dict(counts)}")
    if slot["mode"] == "cold-daemon" and len(measured_ids) != 1280:
        raise Invalid("cold measured invocations did not use 1280 fresh processes")
    return {**counts, "stdout_sha256_by_group": out_hashes}


def validate_events(events: list[dict], rows: list[dict], slot: dict) -> None:
    if any("error" in str(e.get("event", "")).lower() or e.get("error") not in (None, "") for e in events):
        raise Invalid("session events contain an error")
    required_names = {f"c2-session-{slot['mode']}-r{slot['replica']}-{r}" for r in ROLES} | {"base", "fixture-setup"}
    finals = [e for e in events if e.get("event") == "final_cleanup"]
    final_names = {e.get("namespace") for e in finals}
    if (not required_names.issubset(final_names) or len(finals) != len(final_names)
            or any(e.get("confirmed_stopped") is not True for e in finals)):
        raise Invalid("final cleanup namespace or confirmation mismatch")
    if any(e.get("namespace") is not None and e["namespace"] not in final_names for e in events):
        raise Invalid("session event references an unconfirmed namespace")
    if slot["mode"] == "warm":
        if len(events) != len(finals):
            raise Invalid("warm session has unexpected lifecycle events")
        boundary = rows[-1]["invocation_outer_end_monotonic_ns"]
        for e in finals:
            if e.get("end_monotonic_ns", 0) <= boundary:
                raise Invalid("warm final cleanup is early or out of order")
            boundary = e["end_monotonic_ns"]
        return
    measured = [r for r in rows if r["phase"] != "warmup"]
    cleanup = [e for e in events if e.get("event") == "cold_post_call_cleanup"]
    stops = [e for e in events if e.get("event") == "cold_warmup_stop_all"]
    if (len(events) != len(finals) + 1281 or len(cleanup) != 1280 or len(stops) != 1
            or stops[0].get("confirmed_zero") is not True):
        raise Invalid("cold cleanup event counts mismatch")
    if [e.get("event") for e in events] != ["cold_warmup_stop_all"] + ["cold_post_call_cleanup"] * 1280 + ["final_cleanup"] * len(finals):
        raise Invalid("cold cleanup events are out of order")
    if not rows[39]["invocation_outer_end_monotonic_ns"] <= stops[0].get("end_monotonic_ns", 0) <= measured[0]["invocation_outer_start_monotonic_ns"]:
        raise Invalid("cold warmup cleanup is outside its boundary")
    for i, (r, e) in enumerate(zip(measured, cleanup)):
        if (e.get("namespace") != r["namespace"] or e.get("confirmed_zero") is not True
                or e.get("start_monotonic_ns", 0) < r["invocation_outer_end_monotonic_ns"]
                or e.get("end_monotonic_ns", 0) <= e.get("start_monotonic_ns", 0)
                or (i + 1 < len(measured) and e["end_monotonic_ns"] > measured[i + 1]["invocation_outer_start_monotonic_ns"])):
            raise Invalid("cold post-call cleanup does not match row/order")
    boundary = cleanup[-1]["end_monotonic_ns"]
    for e in finals:
        if e.get("end_monotonic_ns", 0) <= boundary:
            raise Invalid("cold final cleanup is out of order")
        boundary = e["end_monotonic_ns"]


def validate_cache(report: dict, config: dict) -> None:
    required = ("command_started", "residency_held_before_command", "residency_held_after_command",
                "command_group_gone", "normal_user_runtime_cleanup", "memlock_limit_restored", "all_memory_released")
    needed = report.get("required_page_bytes", 0)
    if (report.get("command_returncode") != 0 or report.get("command_timed_out") is True
            or report.get("evaluation_config_sha256") != digest(config) or report.get("cleanup_errors") != []
            or any(report.get(k) is not True for k in required) or report.get("actual_vm_locked_bytes_after") != 0
            or report.get("actual_vm_locked_bytes") != needed or report.get("max_pinned_bytes") != config.get("cache_max_bytes")
            or report.get("setup_cap_seconds") != config.get("cache_max_seconds") or needed <= 0
            or needed > report.get("max_pinned_bytes", 0) or report.get("available_bytes", 0) < needed + 1024**3
            or report.get("setup_seconds", math.inf) > config.get("cache_max_seconds", 0)
            or report.get("command_timeout_seconds", math.inf) <= 0 or report.get("command_timeout_seconds", math.inf) > 4800
            or report.get("elapsed_seconds", math.inf) > 5400
            or type(report.get("benchmark_pid")) is not int or report["benchmark_pid"] <= 0
            or report.get("residency_permissions_verified") != report.get("unique_files")
            or type(report.get("memlock_limit_raised")) is not bool or report.get("error") not in (None, "")):
        raise Invalid("cache-control residency, cleanup, or budget failed")
    b, i = config.get("benchmark_identity", {}), report.get("benchmark_process_identity", {})
    if (report.get("benchmark_uid") != b.get("uid") or report.get("benchmark_gid") != b.get("gid")
            or report.get("benchmark_groups") != b.get("extra_groups") or i.get("pid") != report["benchmark_pid"]
            or type(i.get("starttime_ticks")) is not int or i["starttime_ticks"] <= 0
            or i.get("uid") != b.get("uid") or i.get("gid") != b.get("gid")
            or i.get("groups") != sorted(b.get("extra_groups", []))
            or i.get("uid_slots") != [b.get("uid")] * 4 or i.get("gid_slots") != [b.get("gid")] * 4):
        raise Invalid("cache-control process UID/GID identity mismatch")


def _product_manifests(folder: Path, expected_root: Path | None = None) -> None:
    expected_root = expected_root or resource_paths(Path(__file__).resolve())[0]
    base_expected = expected_root / "expected-base-product-manifest.json"
    head_expected = expected_root / "expected-head-product-manifest.json"
    if not base_expected.is_file() or not head_expected.is_file():
        raise Invalid("expected base/head product manifests are missing beside session artifacts")
    expected = {"base": json.loads(base_expected.read_text()), "head": json.loads(head_expected.read_text())}
    for arm in ("base", "head"):
        actual = json.loads((folder / f"{arm}-product-manifest.json").read_text())
        if not isinstance(actual, dict) or not actual or actual != expected[arm]:
            raise Invalid(f"{arm} product manifest differs from its Git-pinned expected manifest")
        if any(not p.startswith("loopx/") or not isinstance(h, str) or len(h) != 64
               or any(c not in "0123456789abcdef" for c in h) for p, h in actual.items()):
            raise Invalid(f"{arm} product manifest is malformed")


def validate_session(folder: Path, slot: dict, manifest_sha: str, expected_root: Path | None = None) -> dict:
    suffix = f"{slot['provider']}-{slot['history']}-{slot['mode']}-r{slot['replica']}"
    config = json.loads((folder / "evaluation_config.json").read_text())
    schedule = json.loads((folder / f"session-schedule-{suffix}.json").read_text())
    result = json.loads((folder / f"result-{suffix}.json").read_text())
    validate_config(config, slot, slot)
    _product_manifests(folder, expected_root)
    cache = json.loads((folder / "cache-control-report.json").read_text())
    validate_cache(cache, config)
    driver = result.get("driver_identity", {})
    ci = cache["benchmark_process_identity"]
    if (result.get("evaluation_config_sha256") != digest(config)
            or any(driver.get(k) != ci.get(k) for k in ("pid", "starttime_ticks", "uid"))
            or driver.get("affinity") != config.get("cpu_affinity")):
        raise Invalid("cache report is not bound to this config and driver process")
    validate_schedule(schedule, config, slot, manifest_sha)
    if (result.get("status") != "completed" or result.get("experiment_kind") != config["experiment_kind"]
            or result.get("actual_sources") != {"base": BASE, "head": HEAD}
            or result.get("candidate_executed") is not True
            or result.get("schedule_sha256") != schedule.get("schedule_sha256")
            or result.get("cleanup_confirmed") is not True or result.get("failures") != []
            or result.get("rows_written") != 1320 or result.get("measured_rows") != 1280
            or result.get("conditioning_rows_retained") != 320):
        raise Invalid("result status, source, schedule, cleanup, or accounting mismatch")
    row_path = folder / f"session-rows-{suffix}.jsonl"
    rows = [json.loads(x) for x in row_path.read_text().splitlines()]
    counts = validate_rows(rows, schedule, slot, config)
    events = [json.loads(x) for x in (folder / f"session-events-{suffix}.jsonl").read_text().splitlines()]
    validate_events(events, rows, slot)
    primary = [{k: r[k] for k in ("phase", "role", "block", "round", "cli_ms")}
               for r in rows if r["phase"] in ("aa_primary", "ab")]
    return {"primary": primary, "conditioning_rows": counts["aa_conditioning"], "config": config,
            "synthetic_fixture": result.get("synthetic_fixture") is True,
            "provenance": {"schedule_sha256": schedule["schedule_sha256"],
                "manifest_sha256": manifest_sha, "actual_sources": {"base": BASE, "head": HEAD},
                "source_slot_by_arm": slot["source_slot_by_arm"],
                "semantic_sources": {"base": {"sha": BASE, "path": config["baseline_source"],
                    "physical_slot": slot["source_slot_by_arm"]["base"]},
                    "head": {"sha": HEAD, "path": config["candidate_source"],
                    "physical_slot": slot["source_slot_by_arm"]["head"]}},
                "stdout_sha256_by_group": counts["stdout_sha256_by_group"]}}


def marginal(rows: list[dict], phase: str) -> dict:
    base_role, compare_role = (("aa_base", "aa_compare") if phase == "aa_primary" else ("ab_base", "ab_compare"))
    base = [r["cli_ms"] for r in rows if r["phase"] == phase and r["role"] == base_role]
    compare = [r["cli_ms"] for r in rows if r["phase"] == phase and r["role"] == compare_role]
    if len(base) != len(compare) or not base:
        raise Invalid(f"unmatched/missing {phase} primary rows")
    return {"n_per_role": len(base), "base": {"p50_ms": quantile(base, .5), "p95_ms": quantile(base, .95)},
            "compare": {"p50_ms": quantile(compare, .5), "p95_ms": quantile(compare, .95)},
            "compare_minus_base_ms": {q: quantile(compare, p) - quantile(base, p)
                                      for q, p in (("p50", .5), ("p95", .95))}}


def bootstrap(case_sessions: list[dict], phase: str, seed: int) -> dict:
    """Resample matched pairs inside each fixed session, retaining equal session weights."""
    rng = random.Random(seed)
    base_role, compare_role = (("aa_base", "aa_compare") if phase == "aa_primary" else ("ab_base", "ab_compare"))
    n = 160 if phase == "aa_primary" else 320
    draws = {"p50": [], "p95": []}
    by_session = []
    for session in case_sessions:
        pairs = {}
        for r in session["primary"]:
            if r["phase"] == phase:
                pairs.setdefault(r["block"] * 2 + r["round"], {})[r["role"]] = r["cli_ms"]
        ordered = [(p[base_role], p[compare_role]) for p in pairs.values()]
        if len(ordered) != n:
            raise Invalid(f"session has {len(ordered)} {phase} pairs; expected {n}")
        by_session.append(ordered)
    for _ in range(TRIALS):
        bvals, cvals = [], []
        for pairs in by_session:
            for ix in (rng.randrange(n) for _ in range(n)):
                bvals.append(pairs[ix][0]); cvals.append(pairs[ix][1])
        draws["p50"].append(quantile(cvals, .5) - quantile(bvals, .5))
        draws["p95"].append(quantile(cvals, .95) - quantile(bvals, .95))
    return {metric: {"q0_025_ms": quantile(v, .025), "q0_95_ms": quantile(v, .95),
                     "absolute_q0_95_ms": quantile([abs(x) for x in v], .95)}
            for metric, v in draws.items()}


def validate_auxiliary_probe(value: dict, slot: dict, config: dict) -> dict:
    if (value.get("schema") != "c2_auxiliary_probe_v1"
            or any(value.get(k) != slot.get(k) for k in ("provider", "history", "mode", "replica"))
            or value.get("source_slot_by_arm") != slot["source_slot_by_arm"]
            or value.get("source_sha_by_arm") != {"base": BASE, "head": HEAD}
            or set(value.get("arms", {})) != {"base", "head"}
            or set(value.get("daemon_rss", {})) != {"base", "head"}):
        raise Invalid("auxiliary probe schema, case, or source provenance mismatch")
    for arm in ("base", "head"):
        report = value["arms"][arm]
        if report.get("cli_count") != 42:
            raise Invalid(f"auxiliary {arm} CLI count is not 42")
        for field, expected_n in (("cold_start", 16), ("typed_rpc", None),
                                  ("wire_attempt", None), ("context_transaction", 42)):
            stats_value = report.get(field, {})
            n = stats_value.get("n")
            if (type(n) is not int or n <= 0 or (expected_n is not None and n != expected_n)
                    or ((stats_value.get("p50_ms") is None or stats_value.get("p95_ms") is None) != (n == 0))):
                raise Invalid(f"auxiliary {arm} {field} statistics are malformed")
            for quantile_name in ("p50_ms", "p95_ms"):
                sample = stats_value.get(quantile_name)
                if sample is not None and (not isinstance(sample, (int, float)) or not math.isfinite(sample) or sample < 0):
                    raise Invalid(f"auxiliary {arm} {field} has an invalid {quantile_name}")
        for key in ("typed_rpc_count", "wire_attempt_count", "context_rpc_count", "context_wire_attempt_count"):
            if type(report.get(key)) is not int or report[key] < 0:
                raise Invalid(f"auxiliary {arm} {key} is malformed")
        operation_counts = report.get("context_operation_counts")
        if (report["typed_rpc_count"] != report["typed_rpc"]["n"]
                or report["wire_attempt_count"] != report["wire_attempt"]["n"]
                or not isinstance(operation_counts, dict)
                or set(operation_counts) != {"progressive_context_plan", "context_delivery", "context_recovery"}
                or any(type(n) is not int or n < 0 for n in operation_counts.values())
                or report["context_rpc_count"] != sum(operation_counts.values())
                or report["context_rpc_count"] > report["typed_rpc_count"]
                or report["context_wire_attempt_count"] > report["wire_attempt_count"]):
            raise Invalid(f"auxiliary {arm} RPC count totals are inconsistent")
        if (operation_counts["context_delivery"] != 42
                or operation_counts["progressive_context_plan"] != (42 if arm == "base" else 0)
                or report["context_wire_attempt_count"] < report["context_rpc_count"]):
            raise Invalid(f"auxiliary {arm} context transaction coverage is incomplete")
        rss = value["daemon_rss"].get(arm, {})
        stages = [rss.get(stage) for stage in ("after_warmup", "after_burst", "after_idle_5s")]
        if any(not isinstance(stage, dict) for stage in stages):
            raise Invalid(f"auxiliary {arm} RSS stages are incomplete")
        first = stages[0]
        identity = ("pid", "uid", "affinity", "starttime_ticks")
        if (any(type(first.get(k)) is not int for k in ("pid", "uid", "starttime_ticks", "rss_bytes"))
                or first["pid"] <= 0 or first["starttime_ticks"] <= 0 or first["rss_bytes"] < 0
                or first["uid"] != config.get("benchmark_identity", {}).get("uid")
                or first["affinity"] != config.get("cpu_affinity")
                or any(any(stage.get(k) != first.get(k) for k in identity) for stage in stages[1:])
                or any(type(stage.get("rss_bytes")) is not int or stage["rss_bytes"] < 0 for stage in stages)):
            raise Invalid(f"auxiliary {arm} RSS process identity changed or is malformed")
    return {"status": "success", "evidence": value}


def analyze_auxiliary_probe(path: Path, slot: dict, config: dict) -> dict:
    suffix = path.name.removeprefix("result-")
    cleanup = path.with_name("cleanup-error-" + suffix)
    partial = path.with_name("partial-" + suffix)
    evidence = None
    try:
        if cleanup.is_file():
            return {"status": "failed", "path": str(cleanup), "evidence": json.loads(cleanup.read_text())}
        if not path.is_file():
            if partial.is_file():
                return {"status": "failed", "path": str(partial), "evidence": json.loads(partial.read_text())}
            return {"status": "missing", "path": str(path)}
        evidence = json.loads(path.read_text())
        return validate_auxiliary_probe(evidence, slot, config)
    except (OSError, UnicodeError, json.JSONDecodeError, Invalid, TypeError, AttributeError, KeyError) as exc:
        return {"status": "invalid", "path": str(path), "error": f"{type(exc).__name__}: {exc}",
                "evidence": evidence}


def _write_synthetic_session(folder: Path, slot: dict, manifest_sha: str, expected_root: Path) -> None:
    suffix = f"{slot['provider']}-{slot['history']}-{slot['mode']}-r{slot['replica']}"
    mapping = slot["source_slot_by_arm"]
    config = {"experiment_kind": "fixed_session_candidate_qualification", "provider": slot["provider"],
        "history": slot["history"], "mode": slot["mode"], "replica": slot["replica"],
        "providers": [slot["provider"]], "history_counts": [slot["history"]], "modes": [slot["mode"]],
        "qualification_job_cap_seconds": 5400, "aa_pairs": 160, "ab_pairs": 320,
        "warmups_per_arm_per_phase": 10, "baseline_source_sha": BASE, "candidate_source_sha": HEAD,
        "baseline": BASE, "candidate_head": HEAD, "source_slot_by_arm": mapping,
        "baseline_source": f"/checkout/{mapping['base']}", "candidate_source": f"/checkout/{mapping['head']}",
        "cpu_affinity": [0], "cache_max_bytes": 2 * 1024**3, "cache_max_seconds": 60,
        "benchmark_identity": {"uid": 1000, "gid": 1000, "extra_groups": [1000]}}
    src = frozen_schedule()
    core = {"experiment_kind": config["experiment_kind"], "provider": slot["provider"],
        "history": slot["history"], "mode": slot["mode"], "replica": slot["replica"],
        "actual_sources": {"base": BASE, "head": HEAD}, "role_source_arms": ROLE_ARM,
        "source_slot_by_arm": mapping, "startup_order": slot["startup_order"], "source_schedule": src,
        "calibration_manifest_sha256": manifest_sha,
        "accounting": {"blocks": 160, "rounds_per_block": 2, "primary_cli": 960,
            "conditioning_cli": 320, "warmup_cli": 40, "factory_setup_cli": 17,
            "session_rows": 1320, "domain_cli_per_session": 1421},
        "workload_age": "10 + 2*block + round; all roles 10..329"}
    schedule = {**core, "schedule_sha256": digest(core)}
    rows, phase_stdout = [], {}
    for index, want in enumerate(expected_rows(schedule)):
        role, arm = want["role"], ROLE_ARM[want["role"]]
        physical = mapping[arm]
        actual_sha = BASE if arm == "base" else HEAD
        path = config["baseline_source"] if arm == "base" else config["candidate_source"]
        group = "warmup" if want["phase"] == "warmup" else "measured"
        stdout = phase_stdout.setdefault(group, "synthetic output\n")
        start = 100_000_000 + index * 6_000_000_000
        latency = (1.0, 2.0, 3.0, 100.0)[slot["replica"]]
        delta = .01 if want["phase"] == "aa_primary" and role.endswith("compare") else (
            -.01 if want["phase"] == "ab" and role.endswith("compare") else 0.0)
        ms = 5000.0 if want["phase"] == "aa_conditioning" else latency + delta
        end = start + int(ms * 1_000_000)
        rows.append({**want, "record_type": "sample", "experiment_kind": config["experiment_kind"],
            "provider": slot["provider"], "history": slot["history"], "mode": slot["mode"], "replica": slot["replica"],
            "logical_label": ROLE_LABEL[role], "source_arm": arm, "source_slot": physical,
            "actual_source_sha": actual_sha, "actual_source": path,
            "namespace": f"c2-session-{slot['mode']}-r{slot['replica']}-{role}",
            "cli_returncode": 0, "error": None,
            "pid_identity": {"pid": ROLES.index(role) + 10 if slot["mode"] == "warm" or want["phase"] == "warmup" else index + 1000,
                "uid": 1000, "starttime_ticks": ROLES.index(role) + 100 if slot["mode"] == "warm" or want["phase"] == "warmup" else index + 2000,
                "affinity": [0]},
            "cli_ms": ms, "raw_stdout": stdout,
            "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
            "raw_stdout_base64": base64.b64encode(stdout.encode()).decode(),
            "cli_start_monotonic_ns": start + 1, "cli_end_monotonic_ns": end,
            "invocation_outer_start_monotonic_ns": start,
            "invocation_outer_end_monotonic_ns": end + 1})
    identity = {"pid": 1234, "starttime_ticks": 5678, "uid": 1000, "gid": 1000,
        "groups": [1000], "uid_slots": [1000] * 4, "gid_slots": [1000] * 4}
    cache = {"command_returncode": 0, "command_timed_out": False,
        "evaluation_config_sha256": digest(config), "cleanup_errors": [],
        "command_started": True, "residency_held_before_command": True,
        "residency_held_after_command": True, "command_group_gone": True,
        "normal_user_runtime_cleanup": True, "memlock_limit_restored": True,
        "all_memory_released": True, "actual_vm_locked_bytes_after": 0,
        "required_page_bytes": 4096, "actual_vm_locked_bytes": 4096,
        "max_pinned_bytes": config["cache_max_bytes"], "setup_cap_seconds": 60,
        "available_bytes": 2 * 1024**3, "setup_seconds": 1, "command_timeout_seconds": 4800,
        "elapsed_seconds": 10, "benchmark_pid": 1234, "residency_permissions_verified": 1,
        "unique_files": 1, "memlock_limit_raised": False, "error": None,
        "benchmark_uid": 1000, "benchmark_gid": 1000, "benchmark_groups": [1000],
        "benchmark_process_identity": {"pid": 1234, **identity}}
    result = {"status": "completed", "experiment_kind": config["experiment_kind"],
        "candidate_executed": True,
        "synthetic_fixture": True,
        "actual_sources": {"base": BASE, "head": HEAD}, "schedule_sha256": schedule["schedule_sha256"],
        "cleanup_confirmed": True, "failures": [], "rows_written": 1320, "measured_rows": 1280,
        "conditioning_rows_retained": 320,
        "driver_identity": {"pid": 1234, "starttime_ticks": 5678, "uid": 1000, "affinity": [0]},
        "evaluation_config_sha256": digest(config)}
    names = ("base", "fixture-setup", *(f"c2-session-{slot['mode']}-r{slot['replica']}-{r}" for r in ROLES))
    events = []
    measured = [r for r in rows if r["phase"] != "warmup"]
    if slot["mode"] == "cold-daemon":
        events.append({"event": "cold_warmup_stop_all", "confirmed_zero": True,
            "end_monotonic_ns": rows[39]["invocation_outer_end_monotonic_ns"] + 1})
        events.extend({"event": "cold_post_call_cleanup", "namespace": r["namespace"],
            "confirmed_zero": True, "start_monotonic_ns": r["invocation_outer_end_monotonic_ns"] + 1,
            "end_monotonic_ns": r["invocation_outer_end_monotonic_ns"] + 2} for r in measured)
    events.extend({"event": "final_cleanup", "namespace": name, "confirmed_stopped": True,
        "error": None, "end_monotonic_ns": rows[-1]["invocation_outer_end_monotonic_ns"] + 10 + ix}
        for ix, name in enumerate(names))
    product = {"loopx/example.py": "a" * 64}
    expected_root.mkdir(parents=True, exist_ok=True)
    for arm in ("base", "head"):
        (expected_root / f"expected-{arm}-product-manifest.json").write_text(json.dumps(product))
        (folder / f"{arm}-product-manifest.json").write_text(json.dumps(product))
    (folder / "evaluation_config.json").write_text(json.dumps(config))
    (folder / f"session-schedule-{suffix}.json").write_text(json.dumps(schedule))
    (folder / f"result-{suffix}.json").write_text(json.dumps(result))
    (folder / "cache-control-report.json").write_text(json.dumps(cache))
    (folder / f"session-rows-{suffix}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (folder / f"session-events-{suffix}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    _write_synthetic_auxiliary(folder / "aux-probe-rows" / f"result-{suffix}.json", slot, config)


def _write_synthetic_auxiliary(path: Path, slot: dict, config: dict) -> None:
    stats = {"n": 42, "p50_ms": 1.0, "p95_ms": 1.0}
    rss = {"pid": 9000, "uid": 1000, "affinity": [0], "starttime_ticks": 9001, "rss_bytes": 10000}
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "c2_auxiliary_probe_v1", "provider": slot["provider"],
        "history": slot["history"], "mode": slot["mode"], "replica": slot["replica"],
        "source_slot_by_arm": slot["source_slot_by_arm"], "source_sha_by_arm": {"base": BASE, "head": HEAD},
        "arms": {arm: {"cold_start": {"n": 16, "p50_ms": 1.0, "p95_ms": 1.0},
            "typed_rpc": {**stats, "n": 84 if arm == "base" else 42},
            "wire_attempt": {**stats, "n": 84 if arm == "base" else 42},
            "typed_rpc_count": 84 if arm == "base" else 42, "wire_attempt_count": 84 if arm == "base" else 42,
            "context_transaction": stats, "context_rpc_count": 84 if arm == "base" else 42,
            "context_wire_attempt_count": 84 if arm == "base" else 42,
            "context_operation_counts": {"progressive_context_plan": 42 if arm == "base" else 0,
                                         "context_delivery": 42, "context_recovery": 0},
            "cli_count": 42} for arm in ("base", "head")},
        "daemon_rss": {arm: {stage: rss for stage in ("after_warmup", "after_burst", "after_idle_5s")}
                       for arm in ("base", "head")}}
    path.write_text(json.dumps(payload))


def analyze(root: Path, manifest_path: Path, expected_root: Path | None = None) -> dict:
    raw = manifest_path.read_bytes(); manifest = json.loads(raw); slots_manifest = manifest.get("slots", [])
    if (manifest.get("kind") != "fixed_session_candidate_qualification"
            or manifest.get("status") != "ready_for_candidate_measurement" or len(slots_manifest) != 32):
        raise Invalid("manifest does not match the frozen 32-slot qualification protocol")
    expected_keys = {(*case, rep) for case in CASES for rep in range(4)}
    got = {(s.get("provider"), s.get("history"), s.get("mode"), s.get("replica")) for s in slots_manifest}
    if got != expected_keys or len(got) != 32:
        raise Invalid("manifest slots are duplicated or incomplete")
    if any(s.get("status") != "planned" or s.get("actual_sources") != {"base": BASE, "head": HEAD}
           or s.get("startup_order") != [*ROLES[s["replica"]:], *ROLES[:s["replica"]]]
           for s in slots_manifest):
        raise Invalid("manifest source pins or role startup order mismatch")
    balance = defaultdict(list)
    for s in slots_manifest:
        balance[(s["provider"], s["history"], s["mode"])].append(
            s.get("source_slot_by_arm", {}).get("base"))
    if any(sorted(places.count(x) for x in ("base", "head")) != [2, 2] for places in balance.values()):
        raise Invalid("physical source allocation is not balanced 2/2 per fixed case")
    slots, valid, auxiliary = [], {}, []
    manifest_sha = hashlib.sha256(raw).hexdigest()
    for s in slots_manifest:
        aux_report = {"status": "unavailable", "error": "main session did not validate"}
        key = (s["provider"], s["history"], s["mode"], s["replica"])
        name = f"result-{key[0]}-{key[1]}-{key[2]}-r{key[3]}.json"
        expected_folder = root / f"c2-candidate-{key[0]}-{key[1]}-{key[2]}-r{key[3]}"
        folder = expected_folder
        state, errors = "success", []
        if s.get("status") not in ("planned", "missing", "failed", "incomplete", "invalid", "success"):
            state, errors = "invalid", ["unknown manifest slot status"]
        elif not folder.exists():
            state = "missing"
        else:
            result = folder / name
            if result.exists():
                try:
                    if json.loads(result.read_text()).get("status") == "failed":
                        state, errors = "failed", ["result reports failed"]
                except Exception:
                    state, errors = "invalid", ["result unreadable"]
            if state == "success":
                suffix = f"{key[0]}-{key[1]}-{key[2]}-r{key[3]}"
                required = ["evaluation_config.json", "cache-control-report.json", "base-product-manifest.json",
                            "head-product-manifest.json", f"session-schedule-{suffix}.json",
                            f"session-rows-{suffix}.jsonl", f"session-events-{suffix}.jsonl", name]
                missing = [n for n in required if not (folder / n).is_file()]
                if missing:
                    state, errors = "incomplete", ["missing artifacts: " + ", ".join(missing)]
                else:
                    try:
                        valid[key] = validate_session(folder, s, manifest_sha, expected_root)
                    except Exception as e:
                        state, errors = "invalid", [f"{type(e).__name__}: {e}"]
                    if state == "success":
                        suffix = f"{key[0]}-{key[1]}-{key[2]}-r{key[3]}"
                        aux_path = folder / "aux-probe-rows" / f"result-{suffix}.json"
                        aux_report = analyze_auxiliary_probe(aux_path, s, valid[key]["config"])
        if not folder.exists():
            aux_report = {"status": "missing", "path": None}
        auxiliary.append({"provider": key[0], "history": key[1], "mode": key[2],
                          "replica": key[3], **aux_report})
        slots.append({"provider": key[0], "history": key[1], "mode": key[2], "replica": key[3],
                      "status": state, "errors": errors, "auxiliary_probe": aux_report,
                      "provenance": valid[key]["provenance"] if key in valid else None})
    cases, rng_seed = [], 20261030
    for p, h, m in sorted(CASES):
        keys = [(p, h, m, r) for r in range(4)]
        sessions = [valid[k] for k in keys if k in valid]
        combined = [row for sess in sessions for row in sess["primary"]]
        item = {"provider": p, "history": h, "mode": m, "valid_sessions": len(sessions), "expected_sessions": 4,
                "status": "complete" if len(sessions) == 4 else "partial", "primary_raw_rows": len(combined),
                "conditioning_rows_retained": sum(sess["conditioning_rows"] for sess in sessions),
                "point": {phase: marginal(combined, phase) for phase in ("aa_primary", "ab")} if combined else None,
                "leave_one_session_out": [], "bootstrap": None, "engineering_score_pass": False}
        if len(sessions) == 4:
            item["bootstrap"] = {phase: bootstrap(sessions, phase, rng_seed + i)
                                 for i, phase in enumerate(("aa_primary", "ab"))}
            for omit in range(4):
                remain = [r for i, sess in enumerate(sessions) if i != omit for r in sess["primary"]]
                item["leave_one_session_out"].append({"omitted_replica": keys[omit][3],
                    "point": {phase: marginal(remain, phase) for phase in ("aa_primary", "ab")}})
            aa_ok = all(item["bootstrap"]["aa_primary"][q]["absolute_q0_95_ms"] <= min(25.0,
                .05 * item["point"]["aa_primary"]["base"][metric])
                for q, metric in (("p50", "p50_ms"), ("p95", "p95_ms")))
            aa_resolution = max(item["bootstrap"]["aa_primary"][q]["absolute_q0_95_ms"] for q in ("p50", "p95"))
            ab_ok = all(item["bootstrap"]["ab"][q]["q0_025_ms"] <= 0
                        and item["bootstrap"]["ab"][q]["q0_95_ms"] <= aa_resolution for q in ("p50", "p95"))
            material_point_pass = all(item["point"][phase]["compare_minus_base_ms"]["p95"]
                <= min(25.0, .05 * item["point"][phase]["base"]["p95_ms"])
                for phase in ("aa_primary", "ab"))
            item["aa_resolution_ms"] = aa_resolution
            item["material_point_pass"] = material_point_pass
            item["engineering_score_pass"] = aa_ok and ab_ok and material_point_pass
        cases.append(item)
    counts = {s: sum(x["status"] == s for x in slots) for s in ("success", "missing", "failed", "incomplete", "invalid")}
    complete = counts["success"] == 32
    auxiliary_complete = len(auxiliary) == 32 and all(item["status"] == "success" for item in auxiliary)
    synthetic_data = any(session["synthetic_fixture"] for session in valid.values())
    score_pass = complete and auxiliary_complete and all(c["engineering_score_pass"] for c in cases)
    return {"kind": "fixed_session_candidate_qualification_report", "manifest_sha256": manifest_sha,
            "slot_counts": counts, "slots": slots, "cases": cases, "auxiliary_slots": auxiliary,
            "structurally_complete": complete,
            "auxiliary_complete": auxiliary_complete,
            "engineering_score_pass": score_pass and not synthetic_data,
            "synthetic_data": synthetic_data,
            "qualification_eligible": score_pass and not synthetic_data,
            "formal_coverage_qualified": False,
            "interpretation": "Conditional fixed-session engineering decision-score quantiles; not calibrated population 95% coverage or a universal-host guarantee. Historical 4/8 results are not reclassified.",
            "auxiliary_probe_interpretation": "Validated separately from main uninstrumented CLI latency; auxiliary wall times are not qualification latency."}


def selfcheck() -> None:
    # Run every case through the production file tree and decision scorer.
    assert quantile([0, 10], .5) == 5
    assert resource_paths(Path("/tmp/candidate-qualification/analysis/analyze_qualification.py")) == (
        Path("/tmp/candidate-qualification/harness"), Path("/tmp/candidate-qualification/driver/session_driver.py"))
    assert resource_paths(Path("/tmp/.github/c2-context-qualification/analyze_qualification.py")) == (
        Path("/tmp/.github/c2-context-qualification"), Path("/tmp/.github/c2-context-qualification/session_driver.py"))
    old_trials = TRIALS
    try:
        globals()["TRIALS"] = 20
        import tempfile
        with tempfile.TemporaryDirectory(prefix="c2-qualification-selfcheck-") as tmp:
            root = Path(tmp)
            driver_source = resource_paths(Path(__file__).resolve())[1].read_bytes()
            for layout in (root / "analysis-layout", root / "flat-layout"):
                if layout.name == "analysis-layout":
                    script = layout / "analysis" / "analyze_qualification.py"
                    driver = layout / "driver" / "session_driver.py"
                else:
                    script = layout / "analyze_qualification.py"
                    driver = layout / "session_driver.py"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text("")
                driver.parent.mkdir(parents=True, exist_ok=True)
                driver.write_bytes(driver_source)
                assert frozen_schedule_for_script(script) == frozen_schedule()
            slots = []
            for p, h, m in sorted(CASES):
                for replica in range(4):
                    physical = "base" if replica < 2 else "head"
                    slots.append({"provider": p, "history": h, "mode": m, "replica": replica,
                        "status": "planned", "actual_sources": {"base": BASE, "head": HEAD},
                        "startup_order": [*ROLES[replica:], *ROLES[:replica]],
                        "source_slot_by_arm": {"base": physical, "head": "head" if physical == "base" else "base"}})
            manifest = root / "qualification-manifest.json"
            manifest.write_text(json.dumps({"kind": "fixed_session_candidate_qualification",
                "status": "ready_for_candidate_measurement", "slots": slots}))
            expected_root = root / "harness"
            for index, slot in enumerate(slots):
                folder = root / f"c2-candidate-{slot['provider']}-{slot['history']}-{slot['mode']}-r{slot['replica']}"
                folder.mkdir()
                _write_synthetic_session(folder, slot, hashlib.sha256(manifest.read_bytes()).hexdigest(), expected_root)
            quality = root / "c2-candidate-quality"
            quality.mkdir()
            (quality / "result-file-0-cold-daemon-r0.json").write_text(json.dumps({"status": "completed"}))
            report = analyze(root, manifest, expected_root)
            assert report["slot_counts"] == {"success": 32, "missing": 0, "failed": 0, "incomplete": 0, "invalid": 0}
            assert report["structurally_complete"] and report["auxiliary_complete"]
            for case_report in report["cases"]:
                assert case_report["primary_raw_rows"] == 3840
                assert case_report["conditioning_rows_retained"] == 1280
                assert case_report["point"]["aa_primary"]["base"]["p95_ms"] == 100.0
                assert case_report["point"]["ab"]["base"]["p95_ms"] == 100.0
                assert abs(case_report["point"]["aa_primary"]["compare"]["p95_ms"] - 100.01) < 1e-9
                assert case_report["engineering_score_pass"] is True
                assert abs(case_report["bootstrap"]["aa_primary"]["p95"]["q0_95_ms"] - .01) < 1e-9
                assert case_report["leave_one_session_out"][3]["point"]["aa_primary"]["base"]["p95_ms"] == 3.0
            assert report["engineering_score_pass"] is False and report["synthetic_data"] is True
            assert report["qualification_eligible"] is False
            assert report["formal_coverage_qualified"] is False

            auxiliary_folder = root / "c2-candidate-file-0-cold-daemon-r0"
            auxiliary = json.loads((auxiliary_folder / "aux-probe-rows/result-file-0-cold-daemon-r0.json").read_text())
            auxiliary_config = json.loads((auxiliary_folder / "evaluation_config.json").read_text())
            auxiliary_slot = slots[0]
            for arm in ("base", "head"):
                corrupted = json.loads(json.dumps(auxiliary))
                arm_report = corrupted["arms"][arm]
                for field in ("typed_rpc", "wire_attempt", "context_transaction"):
                    arm_report[field] = {"n": 0, "p50_ms": None, "p95_ms": None}
                for field in ("typed_rpc_count", "wire_attempt_count", "context_rpc_count", "context_wire_attempt_count"):
                    arm_report[field] = 0
                arm_report["context_operation_counts"] = dict.fromkeys(arm_report["context_operation_counts"], 0)
                try:
                    validate_auxiliary_probe(corrupted, auxiliary_slot, auxiliary_config)
                except Invalid:
                    pass
                else:
                    raise AssertionError("missing RPC measurements were accepted")

            slot = slots[0]
            folder = root / "c2-candidate-file-0-cold-daemon-r0"
            suffix = f"{slot['provider']}-{slot['history']}-{slot['mode']}-r{slot['replica']}"
            rows_path = folder / f"session-rows-{suffix}.jsonl"
            original_rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
            cache_path = folder / "cache-control-report.json"
            original_cache = json.loads(cache_path.read_text())
            schedule_path = folder / f"session-schedule-{suffix}.json"
            original_schedule = json.loads(schedule_path.read_text())
            head_manifest_path = folder / "head-product-manifest.json"
            original_head_manifest = head_manifest_path.read_text()
            corruptions = (
                (rows_path, lambda: _write_jsonl(rows_path, original_rows, 40, "workload_age", 999)),
                (rows_path, lambda: _write_jsonl(rows_path, original_rows, 40, "actual_source_sha", "f" * 40)),
                (rows_path, lambda: _write_jsonl_nested(rows_path, original_rows, 40, "pid_identity", "uid", -1)),
                (cache_path, lambda: cache_path.write_text(json.dumps({**original_cache, "evaluation_config_sha256": "0" * 64}))),
                (schedule_path, lambda: _rewrite_provenance(schedule_path, original_schedule)),
                (head_manifest_path, lambda: head_manifest_path.write_text(json.dumps({"loopx/example.py": "f" * 64}))),
            )
            for changed_path, corrupt in corruptions:
                corrupt()
                try:
                    validate_session(folder, slot, hashlib.sha256(manifest.read_bytes()).hexdigest(), expected_root)
                except Invalid:
                    pass
                else:
                    raise AssertionError("corrupted synthetic session was accepted")
                if changed_path == rows_path:
                    _write_jsonl(rows_path, original_rows)
                elif changed_path == cache_path:
                    cache_path.write_text(json.dumps(original_cache))
                elif changed_path == schedule_path:
                    schedule_path.write_text(json.dumps(original_schedule))
                else:
                    head_manifest_path.write_text(original_head_manifest)

            result_path = folder / f"result-{suffix}.json"
            result_doc = json.loads(result_path.read_text())
            result_path.write_text(json.dumps({**result_doc, "status": "failed"}))
            missing_slot = slots[1]
            missing_folder = root / f"c2-candidate-{missing_slot['provider']}-{missing_slot['history']}-{missing_slot['mode']}-r{missing_slot['replica']}"
            import shutil
            shutil.rmtree(missing_folder)
            report = analyze(root, manifest, expected_root)
            assert report["slot_counts"] == {"success": 30, "missing": 1, "failed": 1, "incomplete": 0, "invalid": 0}
            assert report["structurally_complete"] is False
    finally:
        globals()["TRIALS"] = old_trials
    print("selfcheck passed: 32 synthetic sessions, pooled raw quantiles, equal session weights, 5,000-trial production default, missing/failed retention, aux schema, both layouts, provenance gates")


def _write_jsonl(path: Path, rows: list[dict], index: int | None = None,
                 field: str | None = None, value: object = None) -> None:
    rows = json.loads(json.dumps(rows))
    if index is not None and field is not None:
        rows[index][field] = value
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _write_jsonl_nested(path: Path, rows: list[dict], index: int,
                        parent: str, field: str, value: object) -> None:
    rows = json.loads(json.dumps(rows))
    rows[index][parent][field] = value
    _write_jsonl(path, rows)


def _rewrite_provenance(path: Path, schedule: dict) -> None:
    altered = json.loads(json.dumps(schedule))
    altered["calibration_manifest_sha256"] = "0" * 64
    core = {k: v for k, v in altered.items() if k != "schedule_sha256"}
    altered["schedule_sha256"] = digest(core)
    path.write_text(json.dumps(altered))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_root", nargs="?", type=Path)
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args()
    if args.selfcheck:
        selfcheck(); return 0
    if args.artifact_root is None or args.manifest is None:
        parser.error("artifact_root and manifest are required unless --selfcheck is used")
    try:
        report = analyze(args.artifact_root, args.manifest)
    except Exception as exc:
        print(json.dumps({"status": "invalid_manifest", "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
