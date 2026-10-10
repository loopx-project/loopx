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
    "installed_python_ts_json_resources", "real_baseline_bootstrap",
    "three_mutations_one_receipt_each", "default_qualification_and_exact_candidate_read",
    "rollback_inactive_write_new_lineage",
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

    def process(self, stage: str, argv: list[str], *, timeout: int = 60, parse_json: bool = True) -> Any:
        started = time.monotonic()
        result = subprocess.run(argv, cwd=self.cwd, env=self.environment,
            text=True, capture_output=True, timeout=timeout, check=False)
        self.report["processes"].append({"stage": stage, "argv": argv, "returncode": result.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, f"{stage} exited {result.returncode}: {result.stdout}\n{result.stderr}")
        return json.loads(result.stdout) if parse_json else result.stdout

    def checked(self, stage: str, **evidence: Any) -> None:
        self.report["checks"].append({"stage": stage, "status": "passed", **evidence})

    def cli(self, stage: str, *args: str) -> dict[str, Any]:
        result = self.process(stage, [str(self.console), "--registry", str(self.registry),
            "--runtime-root", str(self.runtime), "--format", "json", *args])
        require(isinstance(result, dict) and result.get("ok") is True, f"{stage} did not return ok: {result}")
        return result

    def sdk_add(self, stage: str, text: str) -> dict[str, Any]:
        code = """
import json, sys
from pathlib import Path
from loopx.todos import add_goal_todo
value = add_goal_todo(registry_path=Path(sys.argv[1]), runtime_root_arg=sys.argv[2],
    goal_id=sys.argv[3], role='agent', text=sys.argv[4], task_class='advancement_task', action_kind='analyze')
print(json.dumps(value))
"""
        value = self.process(stage, [str(self.python), "-I", "-c", code,
            str(self.registry), str(self.runtime), GOAL, text])
        require(value.get("ok") is True and value.get("added") is True, f"{stage} did not add one Todo")
        return value

    def read_store(self, stage: str, package: Path) -> dict[str, Any]:
        module = package / "control_plane/coordination/file_authority_store.ts"
        code = """
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const [modulePath, root, goal] = process.argv.slice(1);
const {FileAuthorityStore} = await import(pathToFileURL(modulePath).href);
const store = new FileAuthorityStore(join(root, 'authority-shadow', 'file-v0'), goal, {existingOnly: true});
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

        # This lifecycle qualifies pre-promotion capture. Explicitly retain the
        # supported File target-only source instead of inheriting new-Goal
        # canonical SQLite creation, which correctly fences legacy capture.
        source_storage = {"schema_version": "loopx_goal_storage_defaults_v1",
            "new_goal_provider": "file", "canonical_creation": False,
            "new_goal_handoff_mode": "hard_lease"}
        source_storage_path = self.cwd / "shadow-source-storage.json"
        source_storage_path.write_text(json.dumps(source_storage), encoding="utf-8")
        preview = self.cli("console_source_storage_preview", "machine-config", "preview",
            "--namespace", "goal_storage", "--config-json", str(source_storage_path))
        revision = preview.get("plan_revision")
        require(isinstance(revision, str) and bool(revision), "source storage preview has no revision")
        applied = self.cli("console_source_storage_apply", "machine-config", "apply",
            "--namespace", "goal_storage", "--config-json", str(source_storage_path),
            "--expected-plan-revision", revision, "--execute")
        require(applied.get("readback_verified") is True
            and applied["machine_configuration"]["namespaces"]["goal_storage"] == source_storage,
            "target-only source configuration did not read back exactly")
        created = self.cli("console_project_bootstrap", "bootstrap", "--project", str(self.project),
            "--goal-id", GOAL, "--objective", "Qualify installed authority transactions.",
            "--no-global-sync")
        require(created.get("storage_target") == {
            "schema_version": "loopx_new_goal_storage_target_v0", "provider": "file"},
            "shadow source was not created with the target-only File contract")
        selection = created["storage_selection"]
        require(selection.get("provider") == "file" and selection.get("promotion_performed") is False
            and "authority_initialized" not in selection,
            "shadow source unexpectedly initialized canonical authority")
        # Set configuration only, before shadow bootstrap creates the real binding.
        registry = json.loads(self.registry.read_text())
        goal = next(item for item in registry["goals"] if item["id"] == GOAL)
        goal["coordination"].update({"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"],
            "runtime_shadow": {"schema_version": "loopx_coordination_runtime_shadow_config_v0",
                "enabled": True, "provider": "file_v0"}})
        self.registry.write_text(json.dumps(registry))
        boot = self.cli("console_shadow_bootstrap", "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")["bootstrap"]
        require(boot.get("status") == "applied" and bool(boot.get("capture_lineage_id")), f"bootstrap not applied: {boot}")
        self.checked("real_baseline_bootstrap", capture_lineage_id=boot["capture_lineage_id"])

        first = self.sdk_add("installed_python_todo_add", "First installed package mutation.")
        require(first["coordination_runtime_shadow"]["outcome"] == "delivered", f"first Todo capture failed: {first}")
        lease = self.cli("console_native_lease_acquire", "task-lease", "acquire", "--goal-id", GOAL,
            "--todo-id", first["todo_id"], "--owner", "agent-a", "--idempotency-key", "installed-lease-a", "--ttl-seconds", "600")
        require(lease["coordination_runtime_shadow"]["outcome"] == "delivered", f"native lease capture failed: {lease}")
        second = self.sdk_add("installed_python_second_todo_add", "Third installed package mutation.")
        require(second["coordination_runtime_shadow"]["outcome"] == "delivered", f"second Todo capture failed: {second}")
        drained = self.cli("console_drain", "authority-shadow", "drain", "--goal-id", GOAL)
        require(drained.get("pending_after") == 0 and drained.get("prepared_only_after") == 0,
            f"drain left unverified work: {drained}")
        qualified = self.cli("console_qualify_default_policy", "coordination-shadow", "qualify", "--goal-id", GOAL)["qualification"]
        require(qualified.get("status") == "qualified" and qualified.get("qualified") is True,
            f"default policy was not qualified: {qualified}")
        require(qualified["policy"]["minimum_operations"] == 3 and qualified["evidence"]["operation_count"] == 3,
            "qualification did not prove exactly three real primary mutations under the default threshold")
        candidate = self.cli("console_read_candidate", "coordination-shadow", "read-candidate", "--goal-id", GOAL,
            "--todo-id", first["todo_id"])["read_candidate"]
        require(candidate.get("status") == "matched" and candidate.get("read_candidate_qualified") is True,
            f"candidate read was not qualified: {candidate}")
        independent = self.read_store("independent_installed_native_readback", package)
        require(independent["head"]["status"] == "loaded" and independent["history"]["status"] == "page", "independent provider read failed")
        transactions = independent["history"]["transactions"]
        require(len(transactions) == 4 and independent["head"]["cursor"] == "4", "primary writes did not map one-to-one to receipts")
        require(all(len(tx["receipts"]) == 1 for tx in transactions[1:]), "a primary mutation has missing or duplicate receipts")
        old_head = independent["head"]["head"]
        require({first["todo_id"], second["todo_id"]}.issubset({item["todo_id"] for item in old_head["todos"]}), "native readback lost a Todo")
        require(any(item["todo_id"] == first["todo_id"] and item["owner"] == "agent-a" for item in old_head["leases"]), "native readback lost the lease")
        self.checked("three_mutations_one_receipt_each", mutation_count=3, cursor="4")
        self.checked("default_qualification_and_exact_candidate_read", minimum_operations=3, todo_id=first["todo_id"])

        rolled = self.cli("console_rollback", "coordination-shadow", "rollback", "--goal-id", GOAL,
            "--provider-revision", independent["head"]["provider_revision"], "--execute")["rollback"]
        require(rolled.get("status") == "applied", f"rollback not applied: {rolled}")
        absent = self.read_store("independent_read_after_rollback", package)
        require(absent["head"]["status"] == "missing", "rollback left an active candidate")
        inactive = self.sdk_add("installed_python_inactive_primary_write", "Primary remains writable after rollback.")
        require(inactive["coordination_runtime_shadow"]["reason_code"] == "bootstrap_required", "inactive write fabricated capture qualification")
        require(not (self.runtime / "authority-shadow" / "outbox" / GOAL).exists(), "inactive write created outbox without a lineage")
        again = self.cli("console_new_bootstrap", "coordination-shadow", "bootstrap", "--goal-id", GOAL, "--execute")["bootstrap"]
        require(again.get("status") == "applied" and again.get("capture_lineage_id") != boot["capture_lineage_id"], "new baseline reused the retired lineage")
        final = self.read_store("independent_new_baseline_readback", package)
        require(final["head"]["status"] == "loaded" and final["head"]["cursor"] == "1", "new baseline has an invalid initial cursor")
        require(inactive["todo_id"] in {item["todo_id"] for item in final["head"]["head"]["todos"]}, "new baseline omitted the inactive primary write")
        require(len(final["history"]["transactions"]) == 1, "new baseline pretended to retain old mutation coverage")
        self.checked("rollback_inactive_write_new_lineage", previous_lineage=boot["capture_lineage_id"], new_lineage=again["capture_lineage_id"])


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
