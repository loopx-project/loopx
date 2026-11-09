"""Run a caller-selected local probe and emit an Explore execution attachment.

No evaluator access or submission is performed. Candidate file pins cover only
the explicitly selected files at the probe boundaries, not transitive imports.
The optional solver diagnostic is reported separately from host measurements.
"""
from __future__ import annotations

from _thread import LockType
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def resolve_execution_observation(producer):
    """Select measurements without prescribing task structure or strategy."""
    if producer not in (None, "local_probe"):
        raise ValueError("execution_observation must be omitted or 'local_probe'")
    return producer


def execution_observation_from_env(env: dict[str, str]):
    if "LOOPX_RESEARCH_LOOP" in env:
        raise ValueError(
            "LOOPX_RESEARCH_LOOP has been removed; remove it from the worker environment. "
            "Use ordinary Goal/Todo planning and select LOOPX_EXECUTION_OBSERVATION=local_probe "
            "only when needed."
        )
    return resolve_execution_observation(env.get("LOOPX_EXECUTION_OBSERVATION"))


def runtime_execution_guidance(env: dict[str, str]) -> str:
    return execution_observation_guidance() if execution_observation_from_env(env) else ""


def execution_observation_guidance() -> str:
    return (
        "\nLocal execution observations:\nReuse applicable evidence from an execution that already "
        "matches the candidate and input revision. When decision-critical measurements are missing, "
        "use `loopx-research-probe --help` to wrap the relevant local execution within its existing "
        "budget. Do not repeat an experiment merely to produce an attachment. Retain actual host "
        "elapsed time, exit or timeout, and selected-file hashes; keep unmeasured values unknown. "
        "When aggregate memory could change the choice, reuse or estimate the complete workload with "
        "the applicable reference using --memory-limit-bytes. If the estimate is decision-critical, "
        "verify real overlap and compare a lower-footprint method on the same workload and accepted outcome. "
        "Method ids are producer declarations and optional solver counters are reports, not "
        "independently verified execution. Attach available execution_observation through ordinary "
        "Explore writeback with matching candidate, input revision and evidence refs. This tool "
        "does not create work or decide completion: use the original Goal, current Todo acceptance "
        "and ordinary continuation or replan, respecting Stop, budgets and genuine blockers.\n"
    )


def candidate_snapshot(paths: list[Path], workspace: Path) -> tuple[str, list[dict[str, str]]]:
    # Lazy import keeps feature-off runners compatible with older product pins.
    from loopx.capabilities.explore.candidate_content import candidate_snapshot as capture

    return capture(paths, workspace)


def candidate_manifest_sha256(files: list[dict[str, str]]) -> str:
    from loopx.capabilities.explore.candidate_content import candidate_manifest_sha256 as digest

    return digest(files)


def _group_members(group_id: int) -> dict[int, bytes]:
    members = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            stat = (entry / "stat").read_bytes().rsplit(b")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        # Fields after comm start with state, ppid and pgrp. Use starttime to
        # distinguish PID reuse, and skip zombies which no longer own memory.
        if int(stat[2]) == group_id and int(stat[3]) == group_id and stat[0] != b"Z":
            members[int(entry.name)] = stat[19]
    return members


def _group_pss_sample(group_id: int) -> tuple[int, int] | None:
    try:
        members = _group_members(group_id)
        if not members:
            return None
        footprint = 0
        for pid in members:
            rollup = Path(f"/proc/{pid}/smaps_rollup").read_bytes()
            pss = next(line.split() for line in rollup.splitlines() if line.startswith(b"Pss:"))
            if len(pss) != 3 or pss[2] != b"kB" or not pss[1].isdigit():
                return None
            footprint += int(pss[1]) * 1024
        # This checks membership only. PSS reads happen one PID at a time,
        # so a stable group can still move memory between those reads.
        if members == _group_members(group_id):
            return footprint, len(members)
    except (OSError, IndexError, ValueError, StopIteration):
        pass  # Racing/denied reads are unknown, not a smaller valid sample.
    return None


def _observe_group_pss(group_id: int, stopped: threading.Event, samples: dict, lock: LockType) -> None:
    while not stopped.is_set():
        sample = _group_pss_sample(group_id)
        with lock:
            if stopped.is_set():
                return
            if sample is None:
                samples["incomplete_samples"] += 1
            else:
                peak, members = sample
                samples["complete_samples"] += 1
                if samples["peak_sampled_pss_bytes"] is None or peak > samples["peak_sampled_pss_bytes"]:
                    samples.update(peak_sampled_pss_bytes=peak, members_at_peak=members)
        stopped.wait(0.25)


def _memory_observation(samples: dict, source: dict, limit_bytes: int, evidence_ref: str) -> dict:
    return {
        "measurement_basis": "linux-sequential-process-group-pss-v1",
        "candidate_sha256": source["candidate_sha256"],
        "input_revision": source["input_revision"],
        "executed_method_id": source["executed_method_id"],
        "probe_evidence_ref": evidence_ref.strip(),
        "reference_memory_bytes": limit_bytes,
        **samples,
    }


def run_probe(*, command: list[str], candidate_files: list[Path], output: Path,
              input_revision: str, planned_method_id: str, executed_method_id: str,
              evidence_ref: str, limit_seconds: float, change_reason: str | None = None,
              diagnostics: Path | None = None, memory_limit_bytes: int | None = None) -> dict:
    from loopx.capabilities.explore.execution_observation import normalize_execution_observation

    if not command or not math.isfinite(limit_seconds) or limit_seconds <= 0:
        raise ValueError("a command and finite positive local probe limit are required")
    if memory_limit_bytes is not None and (type(memory_limit_bytes) is not int or memory_limit_bytes <= 0):
        raise ValueError("memory_limit_bytes requires a positive integer")
    workspace = Path.cwd().resolve()
    before, files = candidate_snapshot(candidate_files, workspace)
    observation = dict(schema_version="explore_execution_observation_v0",
        candidate_sha256=before, input_revision=input_revision, planned_method_id=planned_method_id,
        executed_method_id=executed_method_id, change_reason=change_reason,
        elapsed_seconds=None, limit_seconds=limit_seconds, search_iterations=None,
        stop_reason=None, exit_code=None, evidence_refs=[evidence_ref, "source:" + before])
    normalize_execution_observation(observation)  # Reject bad attribution before execution.
    if diagnostics is not None and diagnostics.exists():
        raise FileExistsError("use a fresh diagnostic path; previous observations cannot describe this probe")
    # Reserve the primary receipt before any process effect. A stale sidecar
    # cannot be adopted as a new probe, including a dangling symbolic link.
    memory_output = output.with_suffix(output.suffix + ".memory.json")
    if memory_limit_bytes is not None and os.path.lexists(memory_output):
        raise FileExistsError("use a fresh memory observation path")
    with output.open("x", encoding="utf-8") as record:
        with output.with_suffix(output.suffix + ".candidate.json").open("x", encoding="utf-8") as source:
            source.write(json.dumps({"candidate_sha256": before, "files": files}, sort_keys=True) + "\n")
        env = dict(os.environ)
        if diagnostics is not None:
            env["LOOPX_RESEARCH_DIAGNOSTICS"] = str(diagnostics.resolve())
        started = time.monotonic()
        timed_out = False
        samples = (dict(complete_samples=0, incomplete_samples=0, peak_sampled_pss_bytes=None, members_at_peak=None)
                   if memory_limit_bytes is not None else None)
        with output.with_suffix(output.suffix + ".stdout").open("xb") as stdout, \
             output.with_suffix(output.suffix + ".stderr").open("xb") as stderr:
            process = subprocess.Popen(command, cwd=workspace, env=env, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            stopped = None
            sampler = None
            wait_seconds = limit_seconds
            try:
                if samples is not None and sys.platform == "linux":
                    sampling_start = time.monotonic()
                    stopped, lock = threading.Event(), threading.Lock()
                    sampler = threading.Thread(target=_observe_group_pss,
                        args=(process.pid, stopped, samples, lock), daemon=True)
                    try:
                        sampler.start()
                    except RuntimeError:
                        sampler = None  # Unavailable diagnostics must not cancel the probe.
                    wait_seconds = max(0, limit_seconds - (time.monotonic() - sampling_start))
                process.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                if stopped is not None:
                    with lock:
                        stopped.set()
                # Stop the owned group, including descendants that outlive a
                # successful leader; they must not continue changing a candidate.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                if sampler is not None:
                    sampler.join(timeout=0.25)
        observation["elapsed_seconds"] = time.monotonic() - started
        observation["exit_code"] = process.returncode
        observation["stop_reason"] = "host_timeout" if timed_out else f"process_exit:{process.returncode}"
        after, _ = candidate_snapshot(candidate_files, workspace)
        if after != before:
            record.write(json.dumps({"ok": False, "reason": "candidate_changed_during_probe",
                                     "before_sha256": before, "after_sha256": after}))
            raise ValueError("candidate changed during the probe; preserve this attempt and rerun a fixed candidate")
        # Host measurements stand independently of the optional solver report.
        # Validate them first, so a malformed/unavailable report cannot erase a
        # completed execution or change its real exit/timeout status.
        result = normalize_execution_observation(observation)
        try:
            if samples is not None and memory_limit_bytes is not None:
                measurement = _memory_observation(samples, result, memory_limit_bytes, evidence_ref)
                encoded = json.dumps(measurement, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                try:
                    with memory_output.open("xb", buffering=0) as memory_record:
                        if memory_record.write(encoded) != len(encoded):
                            raise OSError("incomplete optional memory observation")
                        os.fsync(memory_record.fileno())
                except OSError:
                    print("Optional process-group memory observation could not be retained.", file=sys.stderr)
                else:
                    result["evidence_refs"].append("resource:" + hashlib.sha256(encoded).hexdigest())
                    result = normalize_execution_observation(result)
            if diagnostics is not None:
                try:
                    if diagnostics.exists():
                        if not diagnostics.is_file():
                            raise ValueError("solver diagnostics must be a regular file")
                        with diagnostics.open("rb") as stream:
                            raw = stream.read(4097)
                        if len(raw) > 4096:
                            raise ValueError("solver diagnostics exceed the bounded observation size")
                        reported = json.loads(raw)
                        if not isinstance(reported, dict) or set(reported) - {"search_iterations", "stop_reason"}:
                            raise ValueError("solver diagnostics accept only search_iterations and stop_reason")
                        proposed = dict(result, search_iterations=reported.get("search_iterations"))
                        reason = reported.get("stop_reason")
                        if reason is not None:
                            if not isinstance(reason, str):
                                raise ValueError("solver stop_reason must be text or null")
                            proposed["stop_reason"] += "; solver_report:" + reason
                        checked = normalize_execution_observation(proposed)
                        if timed_out:
                            checked["stop_reason"] = "host_timeout"
                        result = checked
                except Exception as error:
                    # Confined to optional diagnostics, never candidate checks
                    # or process execution. Retain a bounded local explanation
                    # without copying raw solver text or exception paths.
                    detail = dict(source="solver_diagnostics", error_kind=type(error).__name__,
                        candidate_sha256=before, probe_evidence_ref=evidence_ref,
                        message="Optional solver diagnostics are invalid or unavailable; solver measurements remain unknown.")
                    encoded = json.dumps(detail, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                    with output.with_suffix(output.suffix + ".diagnostics-error.json").open("xb") as error_record:
                        error_record.write(encoded)
                    result["evidence_refs"].append("diagnostics-error:" + hashlib.sha256(encoded).hexdigest())
                    result = normalize_execution_observation(result)
        finally:
            # Even failure to retain the diagnostic explanation leaves the
            # independently valid host observation available to its caller.
            record.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
            record.flush()
            os.fsync(record.fileno())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-revision", required=True)
    parser.add_argument("--planned-method-id", required=True)
    parser.add_argument("--executed-method-id", required=True)
    parser.add_argument("--change-reason")
    parser.add_argument("--evidence-ref", required=True)
    parser.add_argument("--limit-seconds", type=float, required=True)
    parser.add_argument("--memory-limit-bytes", type=int,
        help="Write a .memory.json diagnostic with sequential Linux PSS samples and this reference (bytes)")
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = vars(parser.parse_args())
    args["candidate_files"] = args.pop("candidate_file")
    if args["command"][:1] == ["--"]:
        args["command"] = args["command"][1:]
    result = run_probe(**args)
    print(json.dumps(result, allow_nan=False))
    if result["stop_reason"] == "host_timeout":
        return 124
    code = result["exit_code"]
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    raise SystemExit(main())
