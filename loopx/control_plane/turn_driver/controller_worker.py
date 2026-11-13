"""Codex Controller and Worker sessions serving one admitted Turn over the v1 Host ABI."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.quota.turn_envelope import COMPLETE_TURN_HOST_REQUEST_SCHEMA
from loopx.control_plane.runtime.document_io import atomic_write_private_json
from loopx.control_plane.turn_driver.codex_cli import (
    CodexFailureObservation, _codex_command, codex_cli_event_session_id, codex_cli_result_schema,
    codex_receipt_instructions, normalize_codex_result, prepare_codex_controller_input,
)
from loopx.control_plane.turn_driver.host_process_transport import HostOutputLines, run_host_process
from loopx.control_plane.turn_driver.decision_capture import read_turn_observation_text
from loopx.control_plane.turn_driver.executor import MATERIAL_HOST_RESULT_KINDS
from loopx.control_plane.turn_driver.host_candidate import extract_turn_work_identity
from loopx.control_plane.turn_driver.journal_store import turn_journal_path
from loopx.file_lock import try_exclusive_file_lock


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def object_schema(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


WORK_REPORT = object_schema({"finding": {"type": "string"},
                             "artifacts": {"type": "array", "items": {"type": "string"}}})


@dataclass(frozen=True)
class RoleHost:
    project: Path
    runtime: Path
    registry: Path
    codex_home: Path
    codex_bin: str
    model: str | None
    effort: str | None
    sandbox: str
    timeout: float
    material: Path | None = None
    material_sha256: str | None = None
    goal_state: Path | None = None
    dispatch_task_material: bool = False
    controller_writable: bool = False

    @property
    def worker_writable(self) -> bool:
        return self.sandbox != "read-only"

    @property
    def protected(self) -> tuple[Path, ...]:
        return (self.runtime, self.registry, self.codex_home, self.project / ".codex",
                self.project / ".loopx", self.project / ".git", self.project / "ACTIVE_GOAL_STATE.md",
                *((self.goal_state,) if self.goal_state is not None else ()))


def read_material(path: Path) -> bytes:
    # The caller selects the audience and pins these exact UTF-8 bytes at admission.
    with path.open("rb") as stream:
        data = stream.read(128 * 1024 + 1)
    if len(data) > 128 * 1024:
        raise ValueError("Role material exceeds 128 KiB")
    data.decode("utf-8")
    return data


def role_policy(role: str, host: RoleHost) -> tuple[str, dict[str, Any]]:
    project = host.project
    writable = host.worker_writable and (role == "worker" or host.controller_writable)
    context = {
        "kind": "project_workspace", "workspace_path": str(project), "project_ref": digest(str(project))[:24],
        "audience": "local_owner", "grant": "workspace_write" if writable else "workspace_read",
        "filesystem_scope": "workspace_only",
    }
    identity = effect_runtime_result("collaboration.project.session_identity", {"context": context})
    profile = identity["permissions_profile"]
    config = identity["host_config"]
    filesystem = config["permissions"][profile]["filesystem"]
    fs = filesystem[":workspace_roots"]
    for path in host.protected:
        if project.is_relative_to(path):
            raise ValueError("worker workspace overlaps private Host state")
        if path.is_relative_to(project):
            fs[str(path.relative_to(project))] = "deny"
        else:
            filesystem[str(path)] = "deny"
    return profile, config


def toml_value(value: Any) -> str:
    # Codex -c takes TOML, including quoted filesystem keys, never shell text.
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(key) + " = " + toml_value(item) for key, item in value.items()) + "}"
    return json.dumps(value)


def role_cli_options(role: str, host: RoleHost, timeout: float) -> tuple[str, ...]:
    _, config = role_policy(role, host)
    if host.worker_writable and (role == "worker" or host.controller_writable):
        # Shell here-documents, compilers and tempfile users need scratch space.
        # Use the existing write grant; the shared system temp directory stays denied.
        shell = config["shell_environment_policy"]
        scratch = {"TMPDIR": str(host.project), "TMP": str(host.project), "TEMP": str(host.project),
                   "TMPPREFIX": str(host.project / ".zsh-tmp")}
        shell.setdefault("set", {}).update(scratch)
        shell["include_only"] = [*shell["include_only"], *scratch]
    config.update(approval_policy="never", include_permissions_instructions=False, features={
        "apps": False, "multi_agent": False, "multi_agent_v2": False, "hooks": False, "plugins": False,
        "remote_plugin": False, "browser_use": False, "computer_use": False, "goals": False, "js_repl": False,
    })
    # Native configuration can include project and managed MCP sources. Only
    # names leave the discovery response; never persist its commands or secrets.
    listing = subprocess.run([host.codex_bin, "mcp", "list", "--json"], cwd=host.project,
                             capture_output=True, timeout=min(15, timeout))
    if listing.returncode:
        raise RuntimeError("cannot inspect configured Codex CLI tools")
    servers = json.loads(listing.stdout)
    if not isinstance(servers, list) or not all(isinstance(row, dict) and isinstance(row.get("name"), str)
                                               and row["name"] for row in servers):
        raise ValueError("invalid Codex CLI tool inventory")
    # Codex validates each override before merging native configuration.
    # Mask only enabled, existing entries. Match their discriminator with
    # an empty address so there is no executable, endpoint or credential.
    masks = {}
    for server in servers:
        if server.get("enabled") is False:
            continue
        kind = server["transport"]["type"]
        if kind not in {"stdio", "streamable_http"}:
            raise ValueError("unsupported native MCP transport")
        address = "command" if kind == "stdio" else "url"
        masks[server["name"]] = {"enabled": False, address: ""}
    if masks:
        config["mcp_servers"] = masks
    overrides = [key + "=" + toml_value(value) for key, value in config.items()]
    return ("--ignore-rules", *(arg for override in overrides for arg in ("-c", override)))


def cli_model_binding(host: RoleHost, thread: str) -> dict[str, str]:
    # The exec JSONL stream identifies the thread. Read only the native model
    # and provider chosen for that exact id; neither transcripts nor auth enter Core.
    for db in sorted(host.codex_home.glob("state_*.sqlite"), reverse=True):
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            row = conn.execute("SELECT model, model_provider, cwd, approval_mode FROM threads WHERE id=?",
                               (thread,)).fetchone()
        if row and row[0] and row[1] and Path(row[2]).resolve() == host.project and row[3] == "never":
            return {"model": row[0], "modelProvider": row[1]}
    raise RuntimeError("native role model binding unconfirmed")


class RoleTransport:
    def __init__(self, host: RoleHost, deadline: float, admission: FirstPartyHostGoalAdmission,
                 evidence: dict[str, Any], save: Callable[[], None]) -> None:
        self.host, self.deadline, self.admission = host, deadline, admission
        self.evidence, self.save = evidence, save

    def remaining(self) -> float:
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("controller-worker Turn deadline")
        return seconds

    def check_running(self) -> None:
        self.remaining()
        with self.admission.launch_lifetime(operation="turn_role_continue"):
            pass

    def send(self, role: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        threads = self.evidence["threads"]
        previous = threads.get(role)
        binding = self.evidence.get("host_models", {}).get(role)
        options = role_cli_options(role, self.host, self.remaining())
        if previous:
            if (self.evidence.get("transport") != "codex-exec" or not isinstance(binding, dict)
                    or binding != cli_model_binding(self.host, previous)):
                raise RuntimeError("original native role binding unavailable")
            options += ("-c", "model_provider=" + toml_value(binding["modelProvider"]))
        step: dict[str, Any] = {"role": role, "command": "exec resume" if previous else "exec", "input_sha256": digest(prompt),
                "thread_token_usage_total": None}
        self.evidence["steps"].append(step)
        self.save()
        seen: str | None = None
        completed = False
        failures = CodexFailureObservation()

        def observe(line: str) -> None:
            nonlocal seen, completed
            event = json.loads(line)
            candidate = codex_cli_event_session_id(event)
            if candidate:
                if (seen is not None or previous and candidate != previous
                        or any(candidate == value for key, value in threads.items() if key != role)):
                    raise RuntimeError("native role identity changed")
                seen = threads[role] = candidate
                self.save()
            failures.observe_event(event)
            if event.get("type") == "turn.completed":
                if not seen or completed:
                    raise RuntimeError("ambiguous native role completion")
                completed = True
                usage = event.get("usage") or {}
                keys = ("input_tokens", "output_tokens")
                if all(type(usage.get(k)) is int and usage[k] >= 0 for k in keys):
                    step["thread_token_usage_total"] = {
                        "totalTokens": sum(usage[k] for k in keys), "inputTokens": usage["input_tokens"],
                        "outputTokens": usage["output_tokens"],
                        **{dest: usage[src] for src, dest in (("cached_input_tokens", "cachedInputTokens"),
                            ("reasoning_output_tokens", "reasoningOutputTokens")) if type(usage.get(src)) is int},
                    }

        began = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as temp:
                schema_path, output = Path(temp) / "schema.json", Path(temp) / "result.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                command = _codex_command(codex_bin=self.host.codex_bin, project=self.host.project,
                    schema_path=schema_path, output_path=output, sandbox=None, model=binding["model"] if binding else self.host.model, reasoning_effort=self.host.effort, session_id=previous,
                    mcp_server=None, config_args=options)
                events = HostOutputLines(observe)
                diagnostics = HostOutputLines(failures.observe_stderr)
                run = run_host_process(command, project=self.host.project, input_text=prompt,
                    timeout_seconds=self.remaining(), on_stdout=events.feed, on_stderr=diagnostics.feed,
                    launch_guard=lambda: self.admission.launch_lifetime(operation="turn_role_turn_start"),
                    check_running=self.check_running, interrupt_before_terminate=True)
                events.finish()
                diagnostics.finish()
                if (run["outcome"] != "exited" or run["returncode"] != 0 or not run["output_complete"]
                        or not events.complete or not completed or seen is None):
                    step["native_status"] = run["outcome"] if run["outcome"] != "exited" else "failed"
                    step["codex_error_info"] = failures.category(complete=diagnostics.complete)
                    raise RuntimeError("native role Turn did not complete")
                observed = cli_model_binding(self.host, seen)
                if previous and binding != observed:
                    raise RuntimeError("native role model binding changed")
                self.evidence.setdefault("host_models", {})[role] = observed
                with output.open("rb") as stream:
                    data = stream.read(128 * 1024 + 1)
                if len(data) > 128 * 1024:
                    raise ValueError("oversized native role result")
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError("invalid native role result")
                step.update(native_status="completed", output_sha256=digest(result))
                return result
        finally:
            step["elapsed_seconds"] = round(time.monotonic() - began, 3)
            self.save()


def run_roles(native, request, material=None, *, dispatch_task_material=False):
    prompt, result_schema = prepare_codex_controller_input(request)
    decision_schema = object_schema({"instruction": {"type": ["string", "null"]},
                                     "result": {"anyOf": [result_schema, {"type": "null"}]}})
    if dispatch_task_material and not material:
        raise ValueError("original Worker task material is missing")
    original_task = material if dispatch_task_material else None
    if original_task is not None:
        prompt += '\nThe caller supplied the Worker task below. Use instruction="dispatch" to send it unchanged.'
    if material is not None:
        prompt += "\nOperator-supplied task material:\n" + material
    worker_returned = False
    while True:
        schema = decision_schema
        if original_task is not None and not worker_returned:
            schema = object_schema({**decision_schema["properties"], "instruction": {"enum": ["dispatch", None],
                                    "type": ["string", "null"]}})
        decision = native.send("controller", prompt, schema)
        instruction, result = decision.get("instruction"), decision.get("result")
        if result is not None:
            if instruction is not None or not isinstance(result, dict):
                raise ValueError("controller returned ambiguous decision")
            if not worker_returned and result.get("result_kind") in MATERIAL_HOST_RESULT_KINDS:
                raise ValueError("controller returned a material result without a Worker return")
            return normalize_codex_result(request, result)
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 16000:
            raise ValueError("controller returned an invalid task")
        if original_task is not None and not worker_returned:
            if instruction != "dispatch":
                raise ValueError("expected dispatch of the original Worker task")
            instruction = original_task
        returned = native.send("worker", instruction, WORK_REPORT)
        if (not isinstance(returned.get("finding"), str) or not returned["finding"].strip()
                or not isinstance(returned.get("artifacts"), list)
                or not all(isinstance(path, str) for path in returned["artifacts"])):
            raise ValueError("worker returned an invalid report")
        prompt = "Worker return:\n" + json.dumps(returned, ensure_ascii=False)
        if not worker_returned:
            prompt += "\nFor your Core receipt:\n" + "\n".join(codex_receipt_instructions())
        worker_returned = True


@contextmanager
def interrupt_on_stop():
    def stop(*_):
        raise InterruptedError("controller-worker Host stopped")
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def prepare_role_attempt(path, journal, request):
    """Select the original result or the one Core-authorized receipt repair under the role lock."""
    request_hash = digest(request)
    if not path.exists():
        if "correction" in request["result_contract"]:
            raise RuntimeError("original role attempt requires reconciliation")
        evidence = {"transport": "codex-exec", "turn_key": request["turn_key"], "input_sha256": request_hash, "threads": {}, "steps": []}
        return evidence, evidence

    evidence = json.loads(path.read_text())
    if not isinstance(evidence.get("result"), dict):
        raise RuntimeError("original role attempt requires reconciliation")
    if evidence["input_sha256"] == request_hash:
        return evidence, evidence

    # Only the result-contract feedback may differ from the completed attempt.
    contract = dict(request["result_contract"])
    feedback = contract.pop("correction", None)
    if (not isinstance(feedback, str) or not feedback
            or digest({**request, "result_contract": contract}) != evidence["input_sha256"]):
        raise RuntimeError("original role attempt requires reconciliation")
    correction = evidence.get("correction")
    if correction is not None:
        if correction.get("input_sha256") == request_hash and isinstance(correction.get("result"), dict):
            return evidence, correction
        raise RuntimeError("original receipt correction requires reconciliation")

    audit = json.loads(journal.read_text()).get("recovery_audit") or {}
    planned = audit.get("planned") or {}
    if (planned.get("retry_failed") is not True or planned.get("reinvoke_host") is not True
            or planned.get("resume_from") != "host_execute" or (audit.get("actual") or {}).get("status") != "started"):
        raise RuntimeError("original Turn has no authorized Host retry")
    if evidence.get("transport") != "codex-exec":
        raise RuntimeError("original role transport requires reconciliation")
    correction = {"input_sha256": request_hash}
    evidence["correction"] = correction
    return evidence, correction


def correct_original_receipt(native, request, evidence):
    controller = evidence["threads"].get("controller")
    if not isinstance(controller, str) or not controller or controller == evidence["threads"].get("worker"):
        raise RuntimeError("original Controller identity unavailable")
    feedback = request["result_contract"]["correction"]
    reply = native.send("controller", feedback + " Return the corrected typed Turn result.",
                        codex_cli_result_schema(request))
    if reply.get("result_kind") != evidence["result"].get("result_kind"):
        raise ValueError("receipt correction changed the original result kind")
    return normalize_codex_result(request, reply)


def serve(request, host):
    if request.get("schema_version") != COMPLETE_TURN_HOST_REQUEST_SCHEMA:
        raise ValueError("controller-worker requires a complete C1 Turn request")
    identity = extract_turn_work_identity(request)
    project, runtime = host.project, host.runtime
    key = request["turn_key"]
    journal = turn_journal_path(runtime, goal_id=identity["goal_id"], turn_key=key)
    capture = journal.parent / "decisions" / journal.name
    reference = request.get("detail_ref", {}).get("captured_decision", {})
    if reference.get("path") != str(capture) or capture.resolve(strict=True) != capture:
        raise ValueError("original Turn decision capture unavailable")
    # The Codex input preparation validates the complete contract and its source hash.
    # Compare the actual journal bytes as JSON before attesting that the inline
    # work_contract is the original. Neither role needs access to private Core files.
    if digest(json.loads(read_turn_observation_text(capture))) != digest(request["work_contract"]):
        raise ValueError("original decision differs from the delivered Turn work contract")
    from loopx.registry import find_registry_goal, load_registry, resolve_state_file

    goal = find_registry_goal(load_registry(host.registry), identity["goal_id"])
    if not goal:
        raise ValueError("Controller-worker requires its original registered Goal")
    state = resolve_state_file(Path(goal["repo"]), goal.get("state_file"))
    host = replace(host, goal_state=state.resolve() if state else None)
    material = None
    if host.material is not None:
        source = host.material.resolve(strict=True)
        if not source.is_file() or any(source.is_relative_to(path) for path in host.protected):
            raise ValueError("Role material overlaps protected state or is not a file")
        data = read_material(source)
        if hashlib.sha256(data).hexdigest() != host.material_sha256:
            raise ValueError("Role material changed after admission")
        material = data.decode("utf-8")
    path = journal.parent / "controller-worker" / journal.name
    timeout = host.timeout
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("controller-worker requires a finite execution budget")
    deadline = time.monotonic() + timeout
    admission = FirstPartyHostGoalAdmission.for_plan(registry_path=host.registry,
        goal_id=identity["goal_id"], planned_goal_ref=request.get("goal_ref"))
    with try_exclusive_file_lock(path) as lock:
        if lock is None:
            raise RuntimeError("original role attempt is still active")
        evidence, attempt = prepare_role_attempt(path, journal, request)
        if isinstance(attempt.get("result"), dict):
            return attempt["result"]
        # Test the selected executable and workspace sandbox before paid inference.
        # Turn supervision also enforces the outer deadline over this preflight.
        probe = subprocess.run([host.codex_bin, "sandbox", "-P", ":workspace", "--",
                                sys.executable, "-I", "-c", "pass"], cwd=project,
                               capture_output=True, timeout=min(15, timeout))
        if probe.returncode:
            raise RuntimeError("Native Codex workspace sandbox is unavailable")
        def save():
            atomic_write_private_json(path, evidence)
        save()
        # Role execution is serial. Both roles share the caller's workspace
        # grant during work. A receipt-only retry remains read-only.
        host = replace(host, controller_writable=attempt is evidence)
        native = RoleTransport(host, deadline, admission, evidence, save)
        if attempt is evidence:
            result = run_roles(native, request, material, dispatch_task_material=host.dispatch_task_material)
        else:
            result = correct_original_receipt(native, request, evidence)
        attempt["result"] = result
        save()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "runtime", "registry"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--sandbox", choices=("read-only", "workspace-write", "danger-full-access"), required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--material", type=Path)
    parser.add_argument("--material-sha256")
    parser.add_argument("--dispatch-task-material", action="store_true")
    args = parser.parse_args()
    home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    host = RoleHost(args.project.resolve(), args.runtime.resolve(), args.registry.resolve(), home,
                    args.codex_bin, args.model, args.effort, args.sandbox, args.timeout, args.material, args.material_sha256,
                    dispatch_task_material=args.dispatch_task_material)
    with interrupt_on_stop():
        print(json.dumps(serve(json.load(sys.stdin), host), ensure_ascii=False))


if __name__ == "__main__":
    main()
