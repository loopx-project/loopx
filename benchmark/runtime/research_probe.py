"""Run a caller-selected local probe and emit an Explore execution attachment.

No evaluator access or submission is performed. Candidate file pins cover only
the explicitly selected files at the probe boundaries, not transitive imports.
The optional solver diagnostic is reported separately from host measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
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
        "Method ids are producer declarations and optional solver counters are reports, not "
        "independently verified execution. Attach available execution_observation through ordinary "
        "Explore writeback with matching candidate, input revision and evidence refs. This tool "
        "does not create work or decide completion: use the original Goal, current Todo acceptance "
        "and ordinary continuation or replan, respecting Stop, budgets and genuine blockers.\n"
    )


def candidate_snapshot(paths: list[Path], workspace: Path) -> tuple[str, list[dict[str, str]]]:
    files = []
    for path in paths:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(workspace) or not resolved.is_file():
            raise ValueError("candidate files must be existing files in the workspace")
        with resolved.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files.append({"path": resolved.relative_to(workspace).as_posix(), "sha256": digest})
    if not 1 <= len(files) <= 32 or len({row["path"] for row in files}) != len(files):
        raise ValueError("select one to 32 distinct candidate files")
    files.sort(key=lambda row: row["path"])
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest(), files


def run_probe(*, command: list[str], candidate_files: list[Path], output: Path,
              input_revision: str, planned_method_id: str, executed_method_id: str,
              evidence_ref: str, limit_seconds: float, change_reason: str | None = None,
              diagnostics: Path | None = None) -> dict:
    from loopx.capabilities.explore.execution_observation import normalize_execution_observation

    if not command or not math.isfinite(limit_seconds) or limit_seconds <= 0:
        raise ValueError("a command and finite positive local probe limit are required")
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
    # Reserve before any process effect. Existing evidence is never overwritten.
    with output.open("x", encoding="utf-8") as record:
        with output.with_suffix(output.suffix + ".candidate.json").open("x", encoding="utf-8") as source:
            source.write(json.dumps({"candidate_sha256": before, "files": files}, sort_keys=True) + "\n")
        env = dict(os.environ)
        if diagnostics is not None:
            env["LOOPX_RESEARCH_DIAGNOSTICS"] = str(diagnostics.resolve())
        started = time.monotonic()
        timed_out = False
        with output.with_suffix(output.suffix + ".stdout").open("xb") as stdout, \
             output.with_suffix(output.suffix + ".stderr").open("xb") as stderr:
            process = subprocess.Popen(command, cwd=workspace, env=env, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            try:
                process.wait(timeout=limit_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Stop the owned group, including descendants that outlive a
                # successful leader; they must not continue changing a candidate.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
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
