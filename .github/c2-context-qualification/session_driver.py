#!/usr/bin/env python3
"""Fixed-session baseline/candidate qualification driver."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "04c5799b88f6c4cf12a224b7fa679f272e1e794e"
CANDIDATE = "e42c536e609ba383217a3e83b8cf07c72542944c"
SEED = 20261030
BLOCKS = 160
ROLES = ("aa_base", "aa_compare", "ab_base", "ab_compare")
SOURCE_ARM = {"aa_base": "base", "aa_compare": "base",
              "ab_base": "base", "ab_compare": "head"}
ROLE_LABEL = {"aa_base": "base", "aa_compare": "compare",
              "ab_base": "base", "ab_compare": "compare"}
JOB_CAP = 5400
RESERVE = 300
CALL_CAP = 120


def digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def write_frozen(path: Path, payload: dict) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise FileExistsError(f"preserve existing changed artifact: {path}")
        return
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded)


def source_schedule() -> dict:
    primary_rounds = [0] * 80 + [1] * 80
    pair_positions = [0] * 160 + [1] * 160
    random.Random(SEED).shuffle(primary_rounds)
    random.Random(SEED + 1).shuffle(pair_positions)
    blocks = []
    for block in range(BLOCKS):
        primary_round = primary_rounds[block]
        rounds = []
        for round_no in range(2):
            aa_position = pair_positions[block * 2 + round_no]
            primary = round_no == primary_round
            aa_order = ["aa_base", "aa_compare"]
            if (block % 2 == 1) == primary:
                aa_order.reverse()
            ab_order = ["ab_base", "ab_compare"]
            if round_no == 1:
                ab_order.reverse()
            pairs = [None, None]
            pairs[aa_position] = {"phase": "aa_primary" if primary else "aa_conditioning",
                                  "order": aa_order}
            pairs[1 - aa_position] = {"phase": "ab", "order": ab_order}
            age = 10 + 2 * block + round_no
            rounds.append({"round": round_no, "age": age,
                           "ages": {role: age for role in ROLES},
                           "aa_position": aa_position, "pairs": pairs})
        blocks.append({"block": block, "primary_aa_round": primary_round, "rounds": rounds})
    return {"seed": SEED, "primary_round_seed": SEED,
            "aa_pair_position_seed": SEED + 1, "blocks": blocks,
            "primary_round_by_block": primary_rounds,
            "aa_pair_position_by_round": pair_positions}


def validate_case(config: dict) -> tuple[str, int, str, int]:
    provider, history, mode, replica = (config.get("provider"), config.get("history"),
                                        config.get("mode"), config.get("replica"))
    if (config.get("experiment_kind") != "fixed_session_candidate_qualification"
            or provider not in ("file", "sqlite") or history not in (0, 500)
            or mode not in ("cold-daemon", "warm")
            or type(replica) is not int or replica not in range(4)
            or config.get("qualification_job_cap_seconds") != JOB_CAP):
        raise ValueError("evaluation_config.json must declare one frozen qualification case/replica")
    if (config.get("providers") != [provider]
            or config.get("history_counts") != [history]
            or config.get("modes") != [mode]):
        raise ValueError("evaluation_config.json must contain this exact case only")
    if (config.get("baseline_source_sha") != BASE
            or config.get("candidate_source_sha") != CANDIDATE
            or config.get("baseline") != BASE or config.get("candidate_head") != CANDIDATE):
        raise ValueError("qualification config source pins differ from the protocol")
    if config.get("source_slot_by_arm") not in (
            {"base": "base", "head": "head"}, {"base": "head", "head": "base"}):
        raise ValueError("source_slot_by_arm must map both semantic arms to physical slots")
    return provider, history, mode, replica


def frozen_manifest(path: Path, config: dict, case: tuple[str, int, str, int]) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("kind") != "fixed_session_candidate_qualification"
            or manifest.get("status") != "ready_for_candidate_measurement"):
        raise ValueError("candidate qualification manifest is not execution-ready")
    slots = manifest.get("slots")
    if not isinstance(slots, list) or len(slots) != 32:
        raise ValueError("qualification manifest must contain exactly 32 frozen slots")
    expected_cases = {(provider, history, mode, replica)
                      for provider in ("file", "sqlite") for history in (0, 500)
                      for mode in ("cold-daemon", "warm") for replica in range(4)}
    observed_cases = [(item.get("provider"), item.get("history"), item.get("mode"),
                       item.get("replica")) for item in slots]
    if len(set(observed_cases)) != 32 or set(observed_cases) != expected_cases:
        raise ValueError("manifest must contain each frozen case/replica exactly once")
    for item in slots:
        mapping = item.get("source_slot_by_arm")
        startup = item.get("startup_order")
        if (item.get("status") != "planned"
                or item.get("actual_sources") != {"base": BASE, "head": CANDIDATE}
                or mapping not in ({"base": "base", "head": "head"},
                                   {"base": "head", "head": "base"})
                or not isinstance(startup, list) or sorted(startup) != sorted(ROLES)):
            raise ValueError("manifest contains an unplanned slot or changed semantic source pins")
        replica = item["replica"]
        expected_startup = [*ROLES[replica:], *ROLES[:replica]]
        if startup != expected_startup:
            raise ValueError("manifest startup order must use the frozen Latin rotation by replica")
    for expected_case in {(provider, history, mode)
                          for provider in ("file", "sqlite") for history in (0, 500)
                          for mode in ("cold-daemon", "warm")}:
        case_mappings = [item["source_slot_by_arm"] for item, observed in zip(slots, observed_cases)
                         if observed[:3] == expected_case]
        if sum(mapping["head"] == "base" for mapping in case_mappings) != 2:
            raise ValueError(f"manifest candidate physical slot is not 2/2 balanced for {expected_case}")
    provider, history, mode, replica = case
    matches = [slot for slot in manifest.get("slots", [])
               if (slot.get("provider"), slot.get("history"), slot.get("mode"),
                   slot.get("replica")) == case]
    if len(matches) != 1:
        raise ValueError("baseline calibration manifest must contain this exact case/replica once")
    slot = matches[0]
    actual = slot.get("actual_sources", {})
    if actual != {"base": BASE, "head": CANDIDATE} or slot.get("status") != "planned":
        raise ValueError("manifest semantic source pins or planned status changed")
    if slot.get("source_slot_by_arm") != config["source_slot_by_arm"]:
        raise ValueError("manifest and config physical source-slot mappings differ")
    startup = slot.get("startup_order")
    if not isinstance(startup, list) or sorted(startup) != sorted(ROLES):
        raise ValueError("manifest startup_order must contain each role once")
    return slot


def schedule_document(config: dict, manifest_path: Path) -> dict:
    case = validate_case(config)
    slot = frozen_manifest(manifest_path, config, case)
    source = source_schedule()
    core = {"experiment_kind": "fixed_session_candidate_qualification",
            "provider": case[0], "history": case[1], "mode": case[2], "replica": case[3],
            "actual_sources": {"base": BASE, "head": CANDIDATE},
            "role_source_arms": SOURCE_ARM,
            "source_slot_by_arm": config["source_slot_by_arm"],
            "startup_order": slot["startup_order"], "source_schedule": source,
            "calibration_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "accounting": {"blocks": BLOCKS, "rounds_per_block": 2,
                           "primary_cli": 960, "conditioning_cli": 320,
                           "warmup_cli": 40, "factory_setup_cli": 17,
                           "session_rows": 1320, "domain_cli_per_session": 1421},
            "workload_age": "10 + 2*block + round; all roles 10..329"}
    return {**core, "schedule_sha256": digest(core)}


def plan_from_document(document: dict) -> list[dict]:
    plan = []
    for block in document["source_schedule"]["blocks"]:
        for round_data in block["rounds"]:
            for position, pair in enumerate(round_data["pairs"]):
                plan.append({"block": block["block"], "round": round_data["round"],
                             "age": round_data["age"], "position": position,
                             "phase": pair["phase"], "order": pair["order"]})
    return plan


def self_check() -> dict:
    schedule = source_schedule()
    assert len(schedule["blocks"]) == 160
    assert sorted(schedule["primary_round_by_block"]) == [0] * 80 + [1] * 80
    assert sorted(schedule["aa_pair_position_by_round"]) == [0] * 160 + [1] * 160
    rounds = [{"block": b["block"], **r}
              for b in schedule["blocks"] for r in b["rounds"]]
    counts = {phase: sum(pair["phase"] == phase for round_data in rounds
                         for pair in round_data["pairs"])
              for phase in ("aa_primary", "aa_conditioning", "ab")}
    assert counts == {"aa_primary": 160, "aa_conditioning": 160, "ab": 320}
    assert min(item["age"] for item in rounds) == 10
    assert max(item["age"] for item in rounds) == 329
    role_calls = {role: sum(role in pair["order"] for item in rounds
                            for pair in item["pairs"]) for role in ROLES}
    assert role_calls == {role: 320 for role in ROLES}
    role_phase_calls = {
        role: {phase: sum(role in pair["order"] and pair["phase"] == phase
                          for item in rounds for pair in item["pairs"])
               for phase in ("aa_primary", "aa_conditioning", "ab")}
        for role in ROLES}
    assert role_phase_calls == {
        "aa_base": {"aa_primary": 160, "aa_conditioning": 160, "ab": 0},
        "aa_compare": {"aa_primary": 160, "aa_conditioning": 160, "ab": 0},
        "ab_base": {"aa_primary": 0, "aa_conditioning": 0, "ab": 320},
        "ab_compare": {"aa_primary": 0, "aa_conditioning": 0, "ab": 320}}
    aa_first = sum(pair["order"][0] == "aa_base" for item in rounds
                   for pair in item["pairs"] if pair["phase"] == "aa_primary")
    ab_base_first = sum(pair["order"][0] == "ab_base" for item in rounds
                        for pair in item["pairs"] if pair["phase"] == "ab")
    assert aa_first == 80 and ab_base_first == 160
    return {"status": "passed", "kind": "pure_full_schedule", "blocks": 160,
            "primary_aa_rounds": {"round_0": 80, "round_1": 80},
            "aa_pair_positions": {"position_0": 160, "position_1": 160},
            "phase_pairs": counts, "actual_calls_per_role": role_calls,
            "role_phase_calls": role_phase_calls,
            "primary_aa_base_first": aa_first, "ab_base_first": ab_base_first,
            "age_bounds": [10, 329], "measured_cli": 1280,
            "primary_cli": 960, "conditioning_cli": 320,
            "warmup_cli": 40, "factory_setup_cli": 17,
            "session_cli": 1320, "candidate_executed": True,
            "latency_qualified": False}


def proc_identity(pid: int, stat_text: str, status_text: str, affinity: list[int]) -> dict:
    close_paren = stat_text.rfind(")")
    if close_paren < 0:
        raise ValueError("/proc stat lacks closing comm delimiter")
    stat_fields = stat_text[close_paren + 1:].split()
    uidline = next(line for line in status_text.splitlines() if line.startswith("Uid:"))
    return {"pid": pid, "starttime_ticks": int(stat_fields[19]),
            "uid": int(uidline.split()[1]), "affinity": affinity}


def pid_identity(pid: int) -> dict:
    return proc_identity(pid, Path(f"/proc/{pid}/stat").read_text(),
                         Path(f"/proc/{pid}/status").read_text(),
                         sorted(os.sched_getaffinity(pid)))


def decode_runtime_record(raw: str) -> dict:
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"malformed runtime record: {exc}") from exc
    if not isinstance(record, dict) or type(record.get("pid")) is not int:
        raise RuntimeError("runtime record has no integer PID")
    return record


def runtime_identities(e, namespace: str) -> list[dict]:
    found = []
    for path in (e.TMP / namespace).glob("*/runtime-*.json"):
        try:
            pid = decode_runtime_record(path.read_text())["pid"]
            found.append(pid_identity(pid))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return list({item["pid"]: item for item in found}.values())


def require_zero_live(active: dict[str, dict]) -> None:
    if active:
        raise RuntimeError(f"unexpected trial runtimes: {sorted(active)}")


def validate_identity(identity: dict, uid: int, affinity: list[int]) -> None:
    if identity["uid"] != uid or identity["affinity"] != affinity:
        raise RuntimeError(f"runtime identity mismatch: {identity}")


def require_warm_stable(current: dict[str, dict], frozen: dict[str, dict]) -> None:
    if set(current) != set(ROLES) or current != frozen:
        raise RuntimeError("warm role PID/starttime identities changed")
    if len({item["pid"] for item in current.values()}) != len(ROLES):
        raise RuntimeError("warm roles do not have four distinct PIDs")


def verify_no_trial_runtimes(e, role_ns: dict[str, str]) -> None:
    active = {role: rows[0] for role, ns in role_ns.items()
              if (rows := runtime_identities(e, ns))}
    require_zero_live(active)


def active_role_identities(e, role_ns: dict[str, str], uid: int,
                           affinity: list[int]) -> dict[str, dict]:
    identities = {}
    for role, namespace in role_ns.items():
        active = runtime_identities(e, namespace)
        if len(active) != 1:
            raise RuntimeError(f"expected one active runtime for {role}; found {len(active)}")
        validate_identity(active[0], uid, affinity)
        identities[role] = active[0]
    require_warm_stable(identities, identities)
    return identities


def require_cleanup_success(errors: list[str]) -> None:
    if errors:
        raise RuntimeError("private runtime cleanup failed: " + "; ".join(errors))


def stop_and_verify(e, namespace: str) -> None:
    for source_arm in ("base", "head"):
        e.stop_runtime(source_arm, namespace)
    live = []
    for old in runtime_identities(e, namespace):
        try:
            if pid_identity(old["pid"])["starttime_ticks"] == old["starttime_ticks"]:
                live.append(old["pid"])
        except (FileNotFoundError, ProcessLookupError):
            pass
    if live:
        raise RuntimeError(f"owned runtime remains after official stop: {namespace}: {live}")


def append_event(path: Path, event: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def output_paths(case: tuple[str, int, str, int]) -> tuple[Path, Path, Path, Path]:
    provider, history, mode, replica = case
    suffix = f"{provider}-{history}-{mode}-r{replica}"
    return tuple(ROOT / f"{name}-{suffix}.{ext}" for name, ext in
                 (("session-schedule", "json"), ("session-rows", "jsonl"),
                  ("session-events", "jsonl"), ("result", "json")))


def cleanup_only() -> dict:
    import evaluate as e
    namespaces = sorted(path.name for path in e.TMP.iterdir() if path.is_dir())
    stopped, errors = [], []
    for namespace in namespaces:
        try:
            stop_and_verify(e, namespace)
            stopped.append(namespace)
        except Exception as exc:
            errors.append({"namespace": namespace, "error": f"{type(exc).__name__}: {exc}"})
    require_cleanup_success([row["error"] for row in errors])
    return {"status": "failed" if errors else "completed", "cleanup_only": True,
            "attempted": namespaces, "stopped": stopped, "errors": errors}


def run_session(e, config: dict, manifest: dict, mode: str, plan: list[dict],
                startup_order: list[str],
                schedule_path: Path, ledger: Path, events_path: Path, report: Path,
                deadline: float) -> dict:
    role_ns = {role: f"c2-session-{mode}-r{config['replica']}-{role}" for role in ROLES}
    uid = config["benchmark_identity"]["uid"]
    affinity = sorted(config["cpu_affinity"])
    original_run = e.subprocess.run
    state = {"cli": None}
    failures: list[str] = []
    ordinals = {role: 0 for role in ROLES}
    stable_ids: dict[str, dict] = {}
    rows: list[dict] = []
    hashes: dict[str, str] = {}

    def timed_run(args, *args2, **kwargs):
        is_cli = isinstance(args, (list, tuple)) and "-m" in args and "loopx.entrypoint" in args
        if not is_cli:
            return original_run(args, *args2, **kwargs)
        kwargs["timeout"] = min(float(kwargs.get("timeout", CALL_CAP)),
                                CALL_CAP, max(0.001, deadline - time.time()))
        before = time.monotonic_ns()
        failure = None
        try:
            result = original_run(args, *args2, **kwargs)
            return result
        except subprocess.TimeoutExpired as exc:
            failure = exc
            raise
        finally:
            after = time.monotonic_ns()
            output = (getattr(locals().get("result"), "stdout", b"") or
                      getattr(failure, "stdout", b"") or b"")
            state["cli"] = {"start_monotonic_ns": before, "end_monotonic_ns": after,
                            "elapsed_ms": (after - before) / 1e6,
                            "returncode": getattr(locals().get("result"), "returncode", None),
                            "stdout": output}

    def record(call: dict) -> dict:
        if time.time() >= deadline:
            raise TimeoutError("90-minute job cap reached with five-minute cleanup reserve")
        role = call["role"]
        warmup = call["warmup_ordinal"]
        namespace = role_ns[role]
        if mode == "cold-daemon" and warmup is None:
            verify_no_trial_runtimes(e, role_ns)
        if mode == "warm" and warmup is None:
            require_warm_stable(active_role_identities(e, role_ns, uid, affinity), stable_ids)
        ordinal = ordinals[role]
        ordinals[role] += 1
        state["cli"] = None
        e.subprocess.run = timed_run
        returned, error = None, None
        outer_start = time.monotonic_ns()
        try:
            source_arm = SOURCE_ARM[role]
            returned = e.run(manifest, source_arm,
                             "warm" if warmup is not None else mode, namespace)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            e.subprocess.run = original_run
        outer_end = time.monotonic_ns()
        observed = state["cli"]
        identity, identity_error = None, None
        try:
            runtime = runtime_identities(e, namespace)
            pid = returned[0].get("runtime_pid") if returned else (
                runtime[0]["pid"] if len(runtime) == 1 else None)
            identity = next((item for item in runtime if item["pid"] == pid), None)
            if identity:
                validate_identity(identity, uid, affinity)
            elif observed and observed["returncode"] == 0:
                raise RuntimeError("successful CLI has no verified runtime identity")
            # Warmups establish identities for both modes; this prevents cold startup drift.
            if identity and (mode == "warm" or warmup is not None):
                prior = stable_ids.setdefault(role, identity)
                if prior != identity:
                    raise RuntimeError(f"warmup PID/starttime changed for {role}: {prior} -> {identity}")
            if mode == "warm" and warmup is None:
                require_warm_stable(active_role_identities(e, role_ns, uid, affinity), stable_ids)
        except Exception as exc:
            identity_error = f"identity verification: {type(exc).__name__}: {exc}"
        error = error or identity_error
        if observed and observed["returncode"] == 0 and identity is None:
            error = error or "successful CLI has no verified runtime identity"
        source_arm = SOURCE_ARM[role]
        source_slot = config["source_slot_by_arm"][source_arm]
        source_sha = BASE if source_arm == "base" else CANDIDATE
        stdout_bytes = observed["stdout"] if observed else None
        if isinstance(stdout_bytes, str):
            stdout_bytes = stdout_bytes.encode("utf-8")
        row = {"record_type": "sample", "experiment_kind": "fixed_session_candidate_qualification",
               "provider": config["provider"], "history": config["history"], "mode": mode,
               "replica": config["replica"], "phase": "warmup" if warmup is not None else call["phase"],
               "role": role, "logical_label": ROLE_LABEL[role], "source_arm": source_arm,
               "source_slot": source_slot, "actual_source": str(e.SOURCES[source_arm]),
               "actual_source_sha": source_sha,
               "namespace": namespace, "invocation_ordinal": ordinal,
               "warmup_ordinal": warmup, "block": call.get("block"),
               "round": call.get("round"), "position": call.get("position"),
               "pair_order": call.get("order"), "workload_age": call.get("age"),
               "pid_identity": identity,
               "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest() if stdout_bytes is not None else None,
               "raw_stdout_base64": base64.b64encode(stdout_bytes).decode("ascii") if stdout_bytes is not None else None,
               "cli_start_monotonic_ns": observed["start_monotonic_ns"] if observed else None,
               "cli_end_monotonic_ns": observed["end_monotonic_ns"] if observed else None,
               "cli_ms": observed["elapsed_ms"] if observed else None,
               "invocation_outer_start_monotonic_ns": outer_start,
               "invocation_outer_end_monotonic_ns": outer_end,
               "cli_returncode": observed["returncode"] if observed else None,
               "evaluator_ms": returned[0].get("ms") if returned else None,
               "raw_stdout": stdout_bytes.decode("utf-8", "replace") if stdout_bytes is not None else None,
               "result_mechanics_only": True, "latency_qualified": False, "error": error}
        group = "warmup" if warmup is not None else "measured"
        prior_hash = hashes.setdefault(group, row["stdout_sha256"])
        if row["stdout_sha256"] != prior_hash:
            error = error or f"stdout byte hash changed within {group} calls"
            row["error"] = error
        rows.append(row)
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if error:
            append_event(events_path, {"event": "sample_error", "role": role,
                                       "invocation_ordinal": ordinal, "error": error})
            raise RuntimeError(error)
        if mode == "cold-daemon" and warmup is None:
            started = time.monotonic_ns()
            try:
                stop_and_verify(e, namespace)
                verify_no_trial_runtimes(e, role_ns)
                append_event(events_path, {"event": "cold_post_call_cleanup", "namespace": namespace,
                                           "start_monotonic_ns": started,
                                           "end_monotonic_ns": time.monotonic_ns(),
                                           "confirmed_zero": True})
            except Exception as exc:
                append_event(events_path, {"event": "cold_post_call_cleanup_error", "namespace": namespace,
                                           "start_monotonic_ns": started,
                                           "end_monotonic_ns": time.monotonic_ns(),
                                           "error": f"{type(exc).__name__}: {exc}"})
                raise
        return row

    try:
        private = [p.name for p in e.TMP.iterdir() if p.is_dir()]
        for namespace in private:
            stop_and_verify(e, namespace)
        for role in startup_order:
            for index in range(10):
                record({"role": role, "phase": "warmup", "block": None, "round": None,
                        "position": None, "age": None, "order": [role],
                        "warmup_ordinal": index + 1})
        warm_ids = dict(stable_ids)
        if set(warm_ids) != set(ROLES) or len({item["pid"] for item in warm_ids.values()}) != 4:
            raise RuntimeError(f"warmups did not establish four distinct daemon identities: {warm_ids}")
        if mode == "cold-daemon":
            for namespace in role_ns.values():
                stop_and_verify(e, namespace)
            verify_no_trial_runtimes(e, role_ns)
            append_event(events_path, {"event": "cold_warmup_stop_all", "confirmed_zero": True,
                                       "end_monotonic_ns": time.monotonic_ns()})
            stable_ids.clear()
        else:
            stable_ids = warm_ids
        for pair in plan:
            pair_rows = []
            for role in pair["order"]:
                pair_rows.append(record({**pair, "role": role, "warmup_ordinal": None}))
            if (len(pair_rows) == 2
                    and pair_rows[0]["stdout_sha256"] != pair_rows[1]["stdout_sha256"]):
                raise RuntimeError(f"byte output differs within pair at block {pair['block']} "
                                   f"round {pair['round']} position {pair['position']}")
        expected_roles = {role: 10 for role in ROLES}
        expected_phases = {phase: 0 for phase in ("aa_primary", "aa_conditioning", "ab")}
        for pair in plan:
            expected_phases[pair["phase"]] += len(pair["order"])
            for role in pair["order"]:
                expected_roles[role] += 1
        actual_roles = {role: sum(row["role"] == role for row in rows) for role in ROLES}
        actual_phases = {phase: sum(row["phase"] == phase for row in rows)
                         for phase in expected_phases}
        warmup_roles = {role: sum(row["role"] == role and row["phase"] == "warmup"
                                  for row in rows) for role in ROLES}
        if actual_roles != expected_roles or actual_phases != expected_phases:
            raise RuntimeError(f"session row counts differ from frozen schedule: "
                               f"roles={actual_roles} phases={actual_phases}")
        if warmup_roles != {role: 10 for role in ROLES}:
            raise RuntimeError(f"session warmup counts differ from protocol: {warmup_roles}")
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        e.subprocess.run = original_run
        for namespace in list(dict.fromkeys([*role_ns.values(), *locals().get("private", [])])):
            cleanup_error = None
            try:
                stop_and_verify(e, namespace)
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
            if cleanup_error:
                failures.append(f"cleanup {namespace}: {cleanup_error}")
            try:
                append_event(events_path, {"event": "final_cleanup_error" if cleanup_error else "final_cleanup",
                                           "namespace": namespace, "confirmed_stopped": not cleanup_error,
                                           "error": cleanup_error, "end_monotonic_ns": time.monotonic_ns()})
            except OSError as exc:
                failures.append(f"cleanup event write {namespace}: {type(exc).__name__}: {exc}")
    result = {"status": "failed" if failures else "completed",
              "experiment_kind": "fixed_session_candidate_qualification",
              "mechanics_only": True, "latency_qualified": False,
              "candidate_executed": True, "mode": mode, "rows_written": len(rows),
              "measured_rows": sum(row["phase"] != "warmup" for row in rows),
              "conditioning_rows_retained": sum(row["phase"] == "aa_conditioning" for row in rows),
              "failures": failures,
              "cleanup_confirmed": not any(item.startswith("cleanup ") for item in failures),
              "actual_sources": {"base": BASE, "head": CANDIDATE},
              "driver_identity": pid_identity(os.getpid()),
              "evaluation_config_sha256": digest(config),
              "schedule_sha256": json.loads(schedule_path.read_text())["schedule_sha256"]}
    write_frozen(report, result)
    if failures:
        raise RuntimeError(json.dumps(result, sort_keys=True))
    return result


def run_cell() -> dict:
    config_path = ROOT / "evaluation_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    case = validate_case(config)
    schedule_path, ledger, events_path, report = output_paths(case)
    for path in (ledger, events_path, report):
        if path.exists():
            raise FileExistsError(f"preserve existing run artifact: {path}")
    manifest_path = ROOT / "qualification-manifest.json"
    expected_schedule = schedule_document(config, manifest_path)
    frozen = json.loads(schedule_path.read_text(encoding="utf-8"))
    if frozen != expected_schedule:
        raise ValueError("frozen session schedule does not match config/manifest/hash")
    started = float(os.environ.get("C2_QUAL_JOB_STARTED_EPOCH",
                                  config.get("qualification_job_started_epoch", 0)))
    if not started:
        raise ValueError("missing C2_QUAL_JOB_STARTED_EPOCH/config qualification_job_started_epoch")
    deadline = started + JOB_CAP - RESERVE
    if time.time() >= deadline:
        raise TimeoutError("90-minute job cap reached with five-minute cleanup reserve")
    # Evaluator initialization happens only after the case and frozen schedule pass validation.
    import evaluate as e
    if e.CONFIG != config:
        raise ValueError("evaluator and calibration config differ")
    for arm, expected_sha in (("base", BASE), ("head", CANDIDATE)):
        source = Path(e.SOURCES[arm])
        expected_slot = config["source_slot_by_arm"][arm]
        if source.name != expected_slot:
            raise ValueError(f"{arm} semantic source does not map to physical slot {expected_slot}: {source}")
        if (source / ".git").exists():
            observed = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            if observed != expected_sha:
                raise ValueError(f"{arm} semantic source pin mismatch: {observed}")
    fixture_path = ROOT / "evaluation-fixtures" / f"{case[0]}-{case[1]}" / "manifest.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("provider") != case[0] or fixture.get("history") != case[1]:
        raise ValueError("fixture manifest does not match the exact configured case")
    with ledger.open("x", encoding="utf-8"):
        pass
    return run_session(e, config, fixture, case[2], plan_from_document(frozen),
                       frozen["startup_order"],
                       schedule_path, ledger, events_path, report, deadline)


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--schedule-only", action="store_true")
    actions.add_argument("--self-check", action="store_true")
    actions.add_argument("--cleanup-only", action="store_true")
    actions.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.schedule_only:
        config = json.loads((ROOT / "evaluation_config.json").read_text(encoding="utf-8"))
        case = validate_case(config)
        schedule_path, *_ = output_paths(case)
        document = schedule_document(config, ROOT / "qualification-manifest.json")
        write_frozen(schedule_path, document)
        return {"status": "frozen", "schedule": str(schedule_path),
                "schedule_sha256": document["schedule_sha256"], "cli_calls": 0,
                "evaluator_imported": False}
    if args.self_check:
        result = self_check()
        write_frozen(ROOT / "selfcheck.json", result)
        return result
    if args.cleanup_only:
        return cleanup_only()
    return run_cell()


if __name__ == "__main__":
    output = main()
    print(json.dumps(output, sort_keys=True))
