"""Codex process/file adapter for the typed benchmark trajectory observer.

No solver state, score authority, scheduler or Explore writer is used here.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from ...control_plane.effect_runtime import effect_runtime_result
from ..native_chat.codex_context import shared_chatgpt_transport


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _result_proof(directory: Path, prepared: dict[str, Any]) -> dict[str, str]:
    return {
        "input_sha256": _digest(prepared["input"]),
        "assessment_sha256": hashlib.sha256(
            (directory / "assessment.json").read_bytes()
        ).hexdigest(),
    }


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def prepare(
    input_path: Path, *, model: str, effort: str, service_tier: str
) -> dict[str, Any]:
    return effect_runtime_result(
        "benchmark.trajectory.prepare",
        {
            "input": json.loads(input_path.read_text(encoding="utf-8")),
            "config": {"model": model, "effort": effort, "service_tier": service_tier},
        },
    )


def read_job(directory: Path) -> dict[str, Any]:
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("schema_version") != "trajectory_assessment_job_v1":
        raise ValueError("Unsupported trajectory assessment receipt")
    return receipt


def request_cancel(directory: Path) -> dict[str, Any]:
    receipt = read_job(directory)
    if receipt["status"] == "running":
        # The process owner handles its own child. Never signal a PID from a file.
        (directory / "cancel.request").touch(exist_ok=True)
    return {**receipt, "cancel_requested": receipt["status"] == "running"}


def execute(
    prepared: dict[str, Any],
    output_root: Path,
    *,
    retry: bool = False,
    timeout_seconds: int = 900,
    codex: str = "codex",
    reuse_from: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Re-derive the request at the I/O boundary; do not trust caller-supplied
    # paths, prompts or schemas hidden in an otherwise valid prepared object.
    verified = effect_runtime_result(
        "benchmark.trajectory.prepare",
        {"input": prepared["input"], "config": prepared["config"]},
    )
    if prepared["analysis_key"] != verified["analysis_key"]:
        raise ValueError("Analysis input/config key mismatch")
    prepared = verified
    if reuse_from is not None:
        comparison = effect_runtime_result(
            "benchmark.trajectory.continuation", {**prepared, "previous": reuse_from}
        )
        if not comparison["can_reuse"]:
            raise ValueError("Changed evidence requires full reassessment")
    directory = output_root.resolve() / prepared["analysis_key"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / "execution.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {
            "ok": False,
            "status": "busy",
            "directory": str(directory),
            "reason": "An execution owns this key; inspect its receipt before recovery.",
        }
    os.close(fd)
    process: subprocess.Popen[str] | None = None
    started = time.monotonic()
    try:
        if (directory / "receipt.json").exists():
            prior = read_job(directory)
            if prior["status"] == "succeeded":
                # Revalidate both source applicability and model output before reuse.
                verified = effect_runtime_result(
                    "benchmark.trajectory.qualify",
                    {
                        **prepared,
                        "output": json.loads(
                            (directory / "model-output.json").read_text(
                                encoding="utf-8"
                            )
                        ),
                    },
                )
                if not (retry and verified.get("display", {}).get("coverage", {}).get("missing", 0)):
                    _write(directory / "assessment.json", verified)
                    prior.update(_result_proof(directory, prepared))
                    _write(directory / "receipt.json", prior)
                    return {**prior, "reused": True}
            if not retry or prior["status"] == "running":
                return {
                    **prior,
                    "ok": False,
                    "reason": "Explicit retry is required for a terminal failed/cancelled attempt.",
                }
            # Keep failures and partially translated successful artifacts when
            # explicitly retrying the same complete analysis job.
            archive = directory / f"attempt-{prior['attempt']}"
            archive.mkdir(exist_ok=False)
            for name in (
                "receipt.json",
                "events.jsonl",
                "stderr.txt",
                "model-output.json",
                "assessment.json",
            ):
                path = directory / name
                if path.exists():
                    path.rename(archive / name)
            attempt = prior["attempt"] + 1
        else:
            attempt = 1
        (directory / "cancel.request").unlink(missing_ok=True)
        _write(directory / "input.json", prepared["input"])
        _write(directory / "output-schema.json", prepared["output_schema"])
        (directory / "prompt.txt").write_text(prepared["prompt"], encoding="utf-8")
        config = prepared["config"]
        # Keep native account routing while ignoring unrelated user tools and
        # rules. The shared host transport delegates credentials/refresh to
        # Codex; it supplies no endpoint, bearer token or account configuration.
        transport = shared_chatgpt_transport({}, {})
        # The desktop service pins its Chat account independently of an
        # unrelated terminal's CODEX_HOME. Use that existing host selection;
        # leave ordinary CLI environments and Codex-owned credentials intact.
        environment = dict(os.environ)
        if chat_home := environment.get("LOOPX_CHAT_CODEX_HOME"):
            environment["CODEX_HOME"] = chat_home
        provider = transport["model_provider"]
        transport_args = ["-c", f"model_provider={json.dumps(provider)}"]
        for key, value in transport["model_providers"][provider].items():
            transport_args.extend(["-c", f"model_providers.{provider}.{key}={json.dumps(value)}"])
        command = [
            codex,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            *transport_args,
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
            "-c",
            'web_search="disabled"',
            "-m",
            config["model"],
            "-c",
            f"model_reasoning_effort={json.dumps(config['effort'])}",
            "-c",
            f"service_tier={json.dumps(config['service_tier'])}",
            "--output-schema",
            str(directory / "output-schema.json"),
            "-o",
            str(directory / "model-output.json"),
            "-",
        ]
        receipt: dict[str, Any] = {
            "schema_version": "trajectory_assessment_job_v1",
            "ok": False,
            "status": "running",
            "analysis_key": prepared["analysis_key"],
            "method_version": prepared["method_version"],
            "scope": prepared["input"]["scope"],
            "config": config,
            "attempt": attempt,
            "directory": str(directory),
            "started_at_unix": time.time(),
            "analysis_wall_seconds": None,
            "usage": None,
            "served_service_tier": None,
            "reused": False,
        }
        _write(directory / "receipt.json", receipt)
        if reuse_from is not None:
            # Same declared evidence, eligibility, scope and model contract.
            # Requalify against the new exact manifest; never splice labels or
            # add old metric totals to new ones. This path launches no child.
            output = {key: reuse_from["output"][key] for key in
                      ("assessments", "limitations", "episode_questions", "display_translations")
                      if key in reuse_from["output"]}
            result = effect_runtime_result(
                "benchmark.trajectory.qualify", {**prepared, "output": output}
            )
            _write(directory / "model-output.json", output)
            _write(directory / "assessment.json", result)
            receipt.update(
                status="succeeded", ok=True, reused=True,
                reused_from_analysis_key=reuse_from["analysis_key"],
                analysis_wall_seconds=time.monotonic() - started,
                **_result_proof(directory, prepared),
            )
            _write(directory / "receipt.json", receipt)
            return receipt
        with (
            (directory / "prompt.txt").open(encoding="utf-8") as prompt,
            (directory / "events.jsonl").open("w", encoding="utf-8") as events,
            (directory / "stderr.txt").open("w", encoding="utf-8") as errors,
        ):
            process = subprocess.Popen(
                command,
                cwd=directory,
                stdin=prompt,
                stdout=events,
                stderr=errors,
                env=environment,
                text=True,
                encoding="utf-8",
            )
            receipt["process_id"] = process.pid
            _write(directory / "receipt.json", receipt)
            while process.poll() is None:
                cancelled = (directory / "cancel.request").exists()
                if cancelled or time.monotonic() - started >= timeout_seconds:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    receipt["status"] = "cancelled" if cancelled else "failed"
                    receipt["error"] = (
                        "cancelled by caller" if cancelled else "analysis timeout"
                    )
                    break
                time.sleep(0.2)
        if receipt["status"] == "running":
            if process.returncode:
                raise ValueError(
                    f"Codex exited {process.returncode}; inspect stderr.txt and events.jsonl"
                )
            output = json.loads(
                (directory / "model-output.json").read_text(encoding="utf-8")
            )
            result = effect_runtime_result(
                "benchmark.trajectory.qualify", {**prepared, "output": output}
            )
            _write(directory / "assessment.json", result)
            receipt.update(
                status="succeeded", ok=True, **_result_proof(directory, prepared)
            )
        for line in (
            (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                receipt["usage"] = event.get("usage")
        receipt["analysis_wall_seconds"] = time.monotonic() - started
        _write(directory / "receipt.json", receipt)
        return receipt
    except (Exception, KeyboardInterrupt) as exc:
        # One process-lifecycle boundary: persist failure without promoting partial output.
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if (directory / "receipt.json").exists():
            receipt = read_job(directory)
            receipt.update(
                ok=False,
                status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                error=str(exc),
                analysis_wall_seconds=time.monotonic() - started,
            )
            _write(directory / "receipt.json", receipt)
        raise
    finally:
        lock.unlink(missing_ok=True)
