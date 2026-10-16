#!/usr/bin/env python3
"""Qualify one built wheel or sdist through an isolated installed-package E2E.

This runner never imports the checkout. It installs the supplied artifact into
an empty temporary venv, executes the installed console command from a separate
working directory, and retains all process results in the requested JSON report.
Run it once per distribution format; every stage is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


GOAL = "installed-authority-e2e"
REQUIRED_STAGES = {
    "installed_python_ts_json_resources", "canonical_file_creation_and_binding",
    "direct_canonical_mutations_and_replay", "independent_provider_history_and_receipts",
    "legacy_shadow_bootstrap_rejected", "installed_fallback_dispatch",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class InstalledQualification:
    def __init__(self, artifact: Path, workspace: Path, report: dict[str, Any]) -> None:
        self.artifact = artifact
        workspace = workspace.resolve()
        self.workspace = workspace
        self.report = report
        self.venv = workspace / "venv"
        self.cwd = workspace / "outside-checkout"
        self.cwd.mkdir()
        self.runtime = self.cwd / "runtime"
        self.registry = self.cwd / "registry.json"
        self.project = self.cwd / "project"
        self.project.mkdir()
        self.python = self.venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.console = self.python.parent / ("loopx.exe" if os.name == "nt" else "loopx")
        node = shutil.which("node")
        require(node is not None, "Node.js is required; this qualification cannot skip native execution")
        self.node = Path(str(node)).absolute()
        removed = [key for key in os.environ if key.startswith(("PYTHON", "LOOPX", "NODE", "PIP_")) or key in {"VIRTUAL_ENV", "CONDA_PREFIX"}]
        self.environment = {key: value for key, value in os.environ.items() if key not in removed}
        self.environment["PATH"] = os.pathsep.join([str(self.python.parent), str(self.node.parent), os.defpath])
        self.environment["PYTHONNOUSERSITE"] = "1"
        self.report["isolation"] = {"cwd": str(self.cwd), "venv": str(self.venv),
            "removed_environment_keys": sorted(removed), "node": str(self.node),
            "checkout_added_to_import_path": False}

    def process(self, stage: str, argv: list[str], *, timeout: int = 60,
                parse_json: bool = True, succeeds: bool = True) -> Any:
        started = time.monotonic()
        result = subprocess.run(argv, cwd=self.cwd, env=self.environment,
            text=True, capture_output=True, timeout=timeout, check=False)
        self.report["processes"].append({"stage": stage, "argv": argv, "returncode": result.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stdout": result.stdout, "stderr": result.stderr})
        require((result.returncode == 0) is succeeds,
            f"{stage} exited {result.returncode}: {result.stdout}\n{result.stderr}")
        return json.loads(result.stdout) if parse_json else result.stdout

    def checked(self, stage: str, **evidence: Any) -> None:
        self.report["checks"].append({"stage": stage, "status": "passed", **evidence})

    def cli(self, stage: str, *args: str, succeeds: bool = True) -> dict[str, Any]:
        result = self.process(stage, [str(self.console), "--registry", str(self.registry),
            "--runtime-root", str(self.runtime), "--format", "json", *args],
            succeeds=succeeds)
        require(isinstance(result, dict) and (result.get("ok") is True) is succeeds,
            f"{stage} returned an unexpected result: {result}")
        return result

    def canonical_result(self, stage: str, value: dict[str, Any]) -> dict[str, Any]:
        require(value.get("source_authority") == "file_v0", f"{stage} used another authority: {value}")
        require(value.get("decision_read_from_provider") is True, f"{stage} did not read the provider")
        require(value.get("legacy_fallback_used") is False, f"{stage} used a legacy fallback")
        require("coordination_runtime_shadow" not in value, f"{stage} emitted retired shadow capture")
        return value

    def sdk_add(self, stage: str, text: str, operation_id: str,
                *, added: bool) -> dict[str, Any]:
        code = """
import json, sys
from pathlib import Path
from loopx.todos import add_goal_todo
value = add_goal_todo(registry_path=Path(sys.argv[1]), runtime_root_arg=sys.argv[2],
    goal_id=sys.argv[3], role='agent', text=sys.argv[4], task_class='advancement_task',
    action_kind='analyze', operation_id=sys.argv[5])
print(json.dumps(value))
"""
        value = self.process(stage, [str(self.python), "-I", "-c", code,
            str(self.registry), str(self.runtime), GOAL, text, operation_id])
        require(value.get("ok") is True and value.get("added") is added,
            f"{stage} returned an unexpected add result: {value}")
        return self.canonical_result(stage, value)

    def read_store(self, stage: str, package: Path) -> dict[str, Any]:
        module = package / "control_plane/coordination/file_authority_store.ts"
        code = """
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const [modulePath, root, goal] = process.argv.slice(1);
const {FileAuthorityStore} = await import(pathToFileURL(modulePath).href);
const store = new FileAuthorityStore(join(root, 'authority', 'file-v0'), goal, {existingOnly: true});
const head = await store.loadAuthority();
const history = await store.scanCommitted(null, 100);
console.log(JSON.stringify({module_path: modulePath, head, history}));
"""
        return self.process(stage, [str(self.node), "--no-warnings", "--experimental-strip-types",
            "--input-type=module", "-e", code, str(module), str(self.runtime), GOAL])

    def run(self) -> None:
        self.process("create_empty_venv", [sys.executable, "-I", "-m", "venv", str(self.venv)],
            timeout=120, parse_json=False)
        self.process("install_artifact", [str(self.python), "-I", "-m", "pip", "--disable-pip-version-check",
            "install", "--no-input", "--no-cache-dir", "--no-deps", str(self.artifact)],
            timeout=240, parse_json=False)
        provenance_code = """
import hashlib, importlib.metadata, importlib.resources, json, pathlib, sys
import loopx, loopx.todos, loopx.control_plane.coordination.shadow_management
package = pathlib.Path(loopx.__file__).resolve().parent
resources = {}
for relative in ['control_plane/coordination/runtime_shadow.ts', 'control_plane/coordination/shadow_management.ts',
    'control_plane/coordination/file_authority_store.ts', 'control_plane/coordination/local_authority_shadow_identity.ts',
    'control_plane/coordination/legacy_writer_lock_paths.ts',
    'control_plane/runtime/receipt_log_snapshot.ts',
    'control_plane/goals/goal_frontier/fallback_disposition.ts',
    'control_plane/work_items/task_lease_acquire.ts',
    'control_plane/coordination/coordination_state_contract_v0.json',
    'control_plane/coordination/coordination_state_contract.generated.ts']:
    resource = importlib.resources.files('loopx').joinpath(relative)
    raw = resource.read_bytes()
    if relative.endswith('.json'): json.loads(raw)
    resources[relative] = {'path': str(resource), 'sha256': hashlib.sha256(raw).hexdigest()}
print(json.dumps({'executable': sys.executable, 'package': str(package),
    'version': importlib.metadata.version('loopx'), 'resources': resources,
    'python_modules': [loopx.__file__, loopx.todos.__file__, loopx.control_plane.coordination.shadow_management.__file__]}))
"""
        provenance = self.process("installed_resource_provenance", [str(self.python), "-I", "-c", provenance_code])
        package = Path(provenance["package"])
        require(package.is_relative_to(self.venv), "Python imported outside the isolated venv")
        for path in provenance["python_modules"] + [item["path"] for item in provenance["resources"].values()]:
            require(Path(path).resolve().is_relative_to(package), f"resource escaped installed package: {path}")
        self.report["provenance"] = provenance
        self.checked("installed_python_ts_json_resources", resource_count=len(provenance["resources"]))

        fallback_code = """
import json
from loopx.control_plane.effect_runtime import effect_runtime_result
print(json.dumps(effect_runtime_result('goal.fallback_disposition.project', {
    'schema_version': 'goal_fallback_disposition_request_v0',
    'terminal': False, 'agent_id': 'agent-a', 'blocker_present': False,
    'resume_planning': {}, 'source_state': 'complete', 'items': [],
    'resume_evaluation': {}, 'legacy_selectable_ids': [], 'declarations': [],
    'created_or_reopened_ids': [],
})))
"""
        fallback = self.process("installed_fallback_dispatch", [str(self.python), "-I", "-c", fallback_code])
        require(fallback.get("kind") == "resolved", "empty fallback declarations must resolve")
        self.checked("installed_fallback_dispatch")

        storage_defaults = {"schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": "file",
            "canonical_creation": True, "new_goal_handoff_mode": "hard_lease"}
        storage_defaults_path = self.cwd / "goal-storage-defaults.json"
        storage_defaults_path.write_text(json.dumps(storage_defaults), encoding="utf-8")
        storage_preview = self.cli("canonical_goal_storage_preview", "machine-config", "preview", "--namespace",
            "goal_storage", "--config-json", str(storage_defaults_path))
        self.cli("canonical_goal_storage_apply", "machine-config", "apply", "--namespace", "goal_storage",
            "--config-json", str(storage_defaults_path), "--expected-plan-revision",
            str(storage_preview["plan_revision"]), "--execute")
        created = self.cli("console_project_bootstrap", "bootstrap", "--project", str(self.project),
            "--goal-id", GOAL, "--objective", "Qualify installed authority transactions.",
            "--no-global-sync")
        selection = created.get("storage_selection")
        require(isinstance(selection, dict), f"bootstrap omitted storage selection: {created}")
        require(selection.get("provider") == "file"
            and selection.get("handoff_mode") == "hard_lease"
            and selection.get("authority_initialized") is True
            and selection.get("legacy_fallback_used") is False,
            f"bootstrap did not initialize canonical File authority: {selection}")
        require((self.runtime / "authority" / "file-v0").is_dir(),
            "bootstrap did not create the canonical File provider directory")
        self.cli("console_configure_agents_and_shadow_rejection_probe", "configure-goal",
            "--goal-id", GOAL, "--registered-agent", "agent-a", "--registered-agent", "agent-b",
            "--coordination-runtime-shadow-file", "--execute")
        self.checked("canonical_file_creation_and_binding",
            provider=selection["provider"], provider_revision=selection["provider_revision"])

        first_operation = "installed-todo-first"
        first = self.sdk_add("installed_python_todo_add",
            "First installed package mutation.", first_operation, added=True)
        lease = self.cli("console_native_lease_acquire", "task-lease", "acquire", "--goal-id", GOAL,
            "--todo-id", first["todo_id"], "--owner", "agent-a",
            "--idempotency-key", "installed-lease-a", "--expected-version", "0",
            "--ttl-seconds", "600")
        self.canonical_result("console_native_lease_acquire", lease)
        require(lease.get("acquired") is True and lease.get("idempotent") is False,
            f"native lease was not acquired: {lease}")
        second_operation = "installed-todo-second"
        second = self.sdk_add("installed_python_second_todo_add",
            "Second installed package mutation.", second_operation, added=True)
        before_replay = self.cli("console_canonical_todo_list_before_replay",
            "todo", "list", "--goal-id", GOAL)
        self.canonical_result("console_canonical_todo_list_before_replay",
            before_replay["authority_read"])
        receipt_before = self.cli("console_exact_receipt_before_replay", "todo", "receipt",
            "--goal-id", GOAL, "--operation-id", first_operation)
        self.canonical_result("console_exact_receipt_before_replay", receipt_before)
        require(receipt_before.get("status") == "found", f"Todo receipt missing: {receipt_before}")

        replayed = self.sdk_add("installed_python_todo_add_exact_replay",
            "First installed package mutation.", first_operation, added=False)
        require(replayed.get("status") in {"replayed", "recovered"},
            f"Todo retry did not replay its original operation: {replayed}")
        lease_replay = self.cli("console_native_lease_acquire_exact_replay",
            "task-lease", "acquire", "--goal-id", GOAL, "--todo-id", first["todo_id"],
            "--owner", "agent-a", "--idempotency-key", "installed-lease-a",
            "--expected-version", "0", "--ttl-seconds", "600")
        self.canonical_result("console_native_lease_acquire_exact_replay", lease_replay)
        require(lease_replay.get("idempotent") is True and lease_replay.get("acquired") is False,
            f"lease retry did not replay its original operation: {lease_replay}")
        after_replay = self.cli("console_canonical_todo_list_after_replay",
            "todo", "list", "--goal-id", GOAL)
        self.canonical_result("console_canonical_todo_list_after_replay",
            after_replay["authority_read"])
        require(after_replay["todos"] == before_replay["todos"]
            and after_replay["authority_read"]["provider_revision"]
            == before_replay["authority_read"]["provider_revision"],
            "exact retries advanced or changed canonical Todo authority")
        receipt_after = self.cli("console_exact_receipt_after_replay", "todo", "receipt",
            "--goal-id", GOAL, "--operation-id", first_operation)
        require(receipt_after == receipt_before, "exact retry changed its historical receipt")
        self.checked("direct_canonical_mutations_and_replay",
            todo_ids=[first["todo_id"], second["todo_id"]],
            lease_version=lease["lease"]["version"],
            provider_revision=after_replay["authority_read"]["provider_revision"])

        independent = self.read_store("independent_installed_native_readback", package)
        require(independent["head"]["status"] == "loaded"
            and independent["history"]["status"] == "page",
            "independent provider read failed")
        transactions = independent["history"]["transactions"]
        require(len(transactions) == 4 and independent["head"]["cursor"] == "4",
            "canonical writes did not map one-to-one to provider transactions")
        require(all(len(tx["receipts"]) == 1 for tx in transactions[1:]),
            "a canonical business mutation has missing or duplicate receipts")
        require({first_operation, second_operation}.issubset(
            {tx["operation_id"] for tx in transactions}),
            "provider history omitted an installed SDK operation")
        head = independent["head"]["head"]
        require({first["todo_id"], second["todo_id"]}.issubset(
            {item["todo_id"] for item in head["todos"]}),
            "native readback lost a Todo")
        require(any(item["todo_id"] == first["todo_id"] and item["owner"] == "agent-a"
            for item in head["leases"]), "native readback lost the lease")
        self.checked("independent_provider_history_and_receipts",
            transaction_count=len(transactions), cursor=independent["head"]["cursor"],
            operation_ids=[tx["operation_id"] for tx in transactions])

        rejected = self.cli("console_legacy_shadow_bootstrap_rejected",
            "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute",
            succeeds=False)["bootstrap"]
        require(rejected.get("status") == "failed"
            and rejected.get("reason_code") == "legacy_authority_already_promoted",
            f"legacy shadow bootstrap did not reject canonical authority: {rejected}")
        final = self.read_store("independent_read_after_legacy_rejection", package)
        require(final["head"]["provider_revision"] == independent["head"]["provider_revision"]
            and final["history"] == independent["history"],
            "rejected legacy bootstrap changed canonical authority")
        self.checked("legacy_shadow_bootstrap_rejected",
            reason_code=rejected["reason_code"],
            provider_revision=final["head"]["provider_revision"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path, help="Exact built wheel or sdist to install")
    parser.add_argument("--report-json", required=True, type=Path, help="Complete qualification report and raw process results")
    args = parser.parse_args(argv)
    artifact = args.artifact.expanduser().resolve()
    report: dict[str, Any] = {"schema_version": "loopx_installed_authority_e2e_v1", "status": "failed",
        "artifact": str(artifact), "artifact_sha256": None, "checks": [], "processes": [],
        "fail": 0, "pending": 0, "unverified": 0}
    try:
        require(artifact.is_file() and (artifact.name.endswith(".whl") or artifact.name.endswith(".tar.gz")), "artifact must be an existing wheel or sdist")
        report["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="loopx-installed-authority-") as directory:
            InstalledQualification(artifact, Path(directory), report).run()
        require({row["stage"] for row in report["checks"]} == REQUIRED_STAGES, "a required qualification stage did not execute")
        report["status"] = "passed"
    except Exception as error:
        report["fail"] = 1
        report["unverified"] = len(REQUIRED_STAGES - {row["stage"] for row in report["checks"]})
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        destination = args.report_json.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report_json": str(destination),
        "checks_passed": len(report["checks"]), "fail": report["fail"], "pending": report["pending"], "unverified": report["unverified"]}))
    return 0 if report["status"] == "passed" and report["fail"] == report["pending"] == report["unverified"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
