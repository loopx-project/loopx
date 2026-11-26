"""Research wheel ownership (with Explore as primary workflow) and installed journeys."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from tests.test_core_distribution import _environment, _run, core_python as core_python


DISTRIBUTION = "loopx-capability-research"
RESEARCH_ENABLED = os.environ.get("LOOPX_RESEARCH_TEST_ENABLED") == "1"
REPOSITORY = Path(__file__).resolve().parents[1]


def _load_prepare():
    spec = importlib.util.spec_from_file_location(
        "loopx_core_prepare_test", REPOSITORY / "packages/loopx-core/prepare.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_core_and_research_bidirectional_staging_ownership(tmp_path):
    prepare = _load_prepare()
    tracked = prepare._tracked_loopx_sources(REPOSITORY)
    research_sources = {
        path.as_posix()
        for path in prepare._optional_capability_sources(tracked)[DISTRIBUTION]
    }
    expected_explore_moved = {
        "loopx/cli_commands/explore.py",
        "loopx/cli_commands/explore_planning_commands.py",
        "loopx/cli_commands/explore_feishu_commands.py",
        "loopx/capabilities/explore/harness_runtime.py",
        "loopx/capabilities/explore/harness_frontier.py",
        "loopx/capabilities/explore/todo_branch_plan.py",
        "loopx/capabilities/explore/worker_branch_plan.py",
        "loopx/capabilities/explore/replay_runtime.py",
        "loopx/capabilities/explore/research_bridge.py",
    }
    expected_research = {
        "loopx/capabilities/deep_research/runtime.py",
        "loopx/capabilities/external_research/cli.py",
        "loopx/capabilities/external_research/projection.py",
        "loopx/capabilities/external_research/provider_probe.py",
        "loopx/cli_commands/deepresearch.py",
        "loopx/control_plane/capabilities/external_evidence.ts",
        "loopx/extensions/public_github_research.py",
    }
    assert expected_explore_moved <= research_sources
    assert expected_research <= research_sources

    core_staged = prepare.prepare_core_source(tmp_path / "core", repository=REPOSITORY)
    research_staged = prepare.prepare_capability_source(
        tmp_path / "research", DISTRIBUTION, repository=REPOSITORY,
    )
    core_files = {p.relative_to(core_staged).as_posix() for p in (core_staged / "loopx").rglob("*") if p.is_file()}
    res_files = {p.relative_to(research_staged).as_posix() for p in (research_staged / "loopx").rglob("*") if p.is_file()}
    assert res_files == research_sources
    assert res_files.isdisjoint(core_files)
    for retained in (
        "loopx/capabilities/explore/result_log.py",
        "loopx/capabilities/explore/turn_context.py",
        "loopx/capabilities/explore/result_writeback.py",
        "loopx/capabilities/explore/machine_defaults.py",
        "loopx/capabilities/explore/harness_gate.py",
        "loopx/capabilities/explore/composition_frontier.py",
        "loopx/capabilities/explore/research_evidence.py",
        "loopx/capabilities/explore/research_frontier.py",
    ):
        assert retained in core_files and retained not in res_files
    for moved in expected_explore_moved | expected_research:
        assert moved in res_files and moved not in core_files


def test_core_staging_has_zero_unconditional_research_wheel_imports(tmp_path):
    prepare = _load_prepare()
    staged = prepare.prepare_core_source(tmp_path / "source", repository=REPOSITORY)
    research_sources = prepare._optional_capability_sources(
        prepare._tracked_loopx_sources(REPOSITORY)
    )[DISTRIBUTION]
    research_modules = {
        ".".join(list(p.with_suffix("").parts)[:-1] if p.stem == "__init__" else p.with_suffix("").parts)
        for p in research_sources
        if p.suffix == ".py"
    }

    def _imported(node, mod_parts):
        if isinstance(node, ast.Import):
            return [a.name for a in node.names]
        if isinstance(node, ast.ImportFrom):
            base = mod_parts[:-node.level] if node.level else []
            mod = ".".join([*base, *([node.module] if node.module else [])])
            return [mod, *(f"{mod}.{a.name}" for a in node.names if mod)]
        return []

    def _scan(stmts, mod_parts, offenders, rel):
        for stmt in stmts:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for target in _imported(stmt, mod_parts):
                    if target in research_modules or any(target.startswith(r + ".") for r in research_modules):
                        offenders.append(f"{rel}:{stmt.lineno}->{target}")
            elif isinstance(stmt, ast.ClassDef):
                _scan(stmt.body, mod_parts, offenders, rel)
            elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.While)):
                if isinstance(stmt, ast.If) and ast.unparse(stmt.test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
                    continue
                _scan(stmt.body, mod_parts, offenders, rel)
                if not isinstance(stmt, (ast.With, ast.AsyncWith)):
                    _scan(stmt.orelse, mod_parts, offenders, rel)
            elif isinstance(stmt, ast.Try):
                handlers = [ast.unparse(h.type) if h.type else "" for h in stmt.handlers]
                if any("ModuleNotFoundError" in h or "ImportError" in h for h in handlers):
                    continue
                _scan(stmt.body, mod_parts, offenders, rel)
                _scan(stmt.orelse, mod_parts, offenders, rel)
                _scan(stmt.finalbody, mod_parts, offenders, rel)

    offenders = []
    for py_file in sorted((staged / "loopx").rglob("*.py")):
        rel = py_file.relative_to(staged)
        parts = list(rel.with_suffix("").parts)
        mod_parts = parts[:-1] if parts[-1] == "__init__" else parts
        _scan(ast.parse(py_file.read_text(encoding="utf-8")).body, mod_parts, offenders, rel.as_posix())
    assert offenders == []


def test_installed_research_inventory_and_help_are_passive(core_python, tmp_path):
    prepare = _load_prepare()
    expected_files = sorted(
        p.as_posix()
        for p in prepare._optional_capability_sources(prepare._tracked_loopx_sources(REPOSITORY))[DISTRIBUTION]
    )
    script = '''
import json, sys
from importlib.metadata import distribution
from importlib.util import find_spec
from loopx.capabilities.deep_research.catalog_entry import RESEARCH_MODULES
from loopx.entrypoint import main
from loopx.release_manifest import _distribution_runtime_fingerprint

installed = sys.argv[1] == "True"
expected_files = set(json.loads(sys.argv[2]))
assert all((find_spec(module) is not None) is installed for module in RESEARCH_MODULES)
core = distribution("loopx-core")
core_files = {entry.as_posix() for entry in core.files if entry.parts[0] == "loopx"}
assert expected_files.isdisjoint(core_files)
if installed:
    research = distribution("loopx-capability-research")
    assert research.version == core.version
    assert research.requires == ["loopx-core==" + core.version]
    members = {entry.as_posix() for entry in research.files if entry.parts[0] == "loopx"}
    assert expected_files <= members and members.isdisjoint(core_files)
    assert "loopx/capabilities/explore/harness_runtime.py" in members
    assert "loopx/cli_commands/explore.py" in members
assert _distribution_runtime_fingerprint() is not None
assert main(["capability", "list", "--format", "json"]) == 0
assert main(["commands", "--format", "json"]) == 0
assert all(module not in sys.modules for module in RESEARCH_MODULES)
'''
    probe = subprocess.run(
        [core_python, "-I", "-c", script, str(RESEARCH_ENABLED), json.dumps(expected_files)],
        cwd=tmp_path, env=_environment(core_python, {"PATH": str(tmp_path)}),
        capture_output=True, text=True, timeout=45,
    )
    assert probe.returncode == 0, probe.stderr
    decoder = json.JSONDecoder()
    cap_payload, offset = decoder.raw_decode(probe.stdout.lstrip())
    cmd_payload, _ = decoder.raw_decode(probe.stdout[len(probe.stdout) - len(probe.stdout.lstrip()) + offset:].lstrip())
    listed_commands = {
        entry["command"].split()[1]
        for group in cmd_payload["groups"]
        for entry in group["commands"]
        if len(entry["command"].split()) >= 2
    }
    for command in ("explore", "deepresearch", "external-evidence"):
        assert (command in listed_commands) is RESEARCH_ENABLED
    entries = {row["id"]: row for row in cap_payload["capabilities"]}
    for name, command in (
        ("explore", "explore"),
        ("deep-research", "deepresearch"),
        ("external-evidence-research", "external-evidence"),
    ):
        entry = entries[name]
        assert entry["provider_id"] == DISTRIBUTION
        assert entry["provider_state"]["installed"] is RESEARCH_ENABLED
        assert entry["provider_state"]["ready"] is RESEARCH_ENABLED
        result = _run(core_python, [command, "--help"], tmp_path,
                      expected=0 if RESEARCH_ENABLED else 2, environment={"PATH": str(tmp_path)})
        if RESEARCH_ENABLED:
            assert f" {command} " in result.stdout
        else:
            assert DISTRIBUTION in entry["next_real_step"] and DISTRIBUTION in result.stderr
    doctor = json.loads(_run(core_python, ["doctor", "--installation-only", "--format", "json"], tmp_path).stdout)
    assert doctor["ok"] is True
    assert doctor["service_runtime_identity"]["package_fingerprint"].startswith("sha256:")
    if RESEARCH_ENABLED:
        companions = {c["distribution"]: c for c in doctor["companion_distributions"]}
        assert companions[DISTRIBUTION]["ready"] is True
        assert companions[DISTRIBUTION]["version"] == doctor["service_runtime_identity"]["package_version"]
    else:
        assert "companion_distributions" not in doctor
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(not RESEARCH_ENABLED, reason="requires the matching Research wheel")
def test_installed_research_doctor_diagnoses_mismatch_and_missing_resource(core_python, tmp_path):
    probe = subprocess.run(
        [core_python, "-I", "-c",
         "from importlib.metadata import distribution; from pathlib import Path;"
         "d=distribution('loopx-capability-research');"
         "dist_info=next(p.locate().parent for p in d.files if 'METADATA' in str(p));"
         "res=next(p.locate() for p in d.files if str(p).endswith('external_evidence.ts'));"
         "explore_mod=next(p.locate() for p in d.files if str(p).endswith('capabilities/explore/harness_runtime.py'));"
         "print(dist_info); print(res); print(explore_mod)"],
        capture_output=True, text=True, check=True,
    )
    dist_info_str, res_str, explore_str = probe.stdout.strip().splitlines()
    metadata = Path(dist_info_str) / "METADATA"
    resource = Path(res_str)
    explore_mod = Path(explore_str)
    original_meta = metadata.read_bytes()
    backup_res = resource.with_suffix(".ts.bak")
    backup_explore = explore_mod.with_suffix(".py.bak")
    try:
        # 1. Version mismatch must fail doctor --installation-only with exact reason & version.
        metadata.write_text(
            re.sub(r"(?m)^Version: .*$", "Version: 0.0.1-mismatch", original_meta.decode("utf-8")),
            encoding="utf-8",
        )
        mismatched = _run(core_python, ["doctor", "--installation-only", "--format", "json"], tmp_path, expected=1)
        m_payload = json.loads(mismatched.stdout)
        assert m_payload["ok"] is False
        assert "package_fingerprint" not in m_payload["service_runtime_identity"]
        comp = {c["distribution"]: c for c in m_payload["companion_distributions"]}[DISTRIBUTION]
        assert comp["installed"] is True and comp["ready"] is False
        assert comp["version"] == "0.0.1-mismatch"
        assert "version_mismatch" in comp["reasons"]
        for cap_id in ("explore", "external-evidence-research"):
            cap = json.loads(_run(core_python, ["capability", "show", cap_id, "--format", "json"], tmp_path).stdout)
            assert cap["capability"]["provider_state"]["installed"] is True
            assert cap["capability"]["provider_state"]["ready"] is False
    finally:
        metadata.write_bytes(original_meta)

    for target, backup, expected_field, expected_entry in (
        (resource, backup_res, "missing_resources", "loopx/control_plane/capabilities/external_evidence.ts"),
        (explore_mod, backup_explore, "missing_modules", "loopx.capabilities.explore.harness_runtime"),
    ):
        try:
            target.rename(backup)
            missing = _run(core_python, ["doctor", "--installation-only", "--format", "json"], tmp_path, expected=1)
            r_payload = json.loads(missing.stdout)
            assert r_payload["ok"] is False
            assert "package_fingerprint" not in r_payload["service_runtime_identity"]
            comp = {c["distribution"]: c for c in r_payload["companion_distributions"]}[DISTRIBUTION]
            assert comp["installed"] is True and comp["ready"] is False
            assert "missing_files" in comp["reasons"]
            assert expected_entry in comp[expected_field]
            cmds = json.loads(_run(core_python, ["commands", "--format", "json"], tmp_path).stdout)
            listed = {e["command"].split()[1] for g in cmds["groups"] for e in g["commands"] if len(e["command"].split()) >= 2}
            assert not ({"explore", "external-evidence", "deepresearch"} & listed)
        finally:
            if backup.exists():
                backup.rename(target)

    restored = json.loads(_run(core_python, ["doctor", "--installation-only", "--format", "json"], tmp_path).stdout)
    assert restored["ok"] is True
    assert restored["service_runtime_identity"]["package_fingerprint"].startswith("sha256:")


@pytest.mark.skipif(not RESEARCH_ENABLED, reason="requires the matching Research wheel")
def test_installed_research_explore_primary_journey_and_bridges(core_python, tmp_path, monkeypatch):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _run_explore_and_bridge_journey(core_python, tmp_path)


def test_source_explore_bridge_isolation_sanitization_idempotence_and_non_backwrite(tmp_path, monkeypatch):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _run_explore_and_bridge_journey(sys.executable, tmp_path, check_module_laziness=False)


def _run_explore_and_bridge_journey(python_bin: str, tmp_path: Path, *, check_module_laziness: bool = True) -> None:
    project_a = tmp_path / "proj-a"
    project_b = tmp_path / "proj-b"
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    project_a.mkdir()
    project_b.mkdir()
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    def cli(*args: str, expected: int = 0) -> dict:
        interpreter = [python_bin, "-I"] if check_module_laziness else [python_bin]
        result = subprocess.run(
            [*interpreter, "-m", "loopx.entrypoint", *prefix, *args],
            cwd=tmp_path if check_module_laziness else REPOSITORY,
            env=_environment(python_bin), capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == expected, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    # 1. Bootstrap Goal A (Explore enabled) and Goal B (Explore enabled separately + disabled Goal C).
    cli("bootstrap", "--project", str(project_a), "--goal-id", "goal-a",
        "--objective", "Explore research workflows safely.", "--write-scope", "docs/**")
    cli("configure-goal", "--goal-id", "goal-a", "--explore-mode", "planning", "--execute")
    cli("register-agent", "--goal-id", "goal-a", "--agent-id", "researcher", "--require-new", "--execute")

    cli("bootstrap", "--project", str(project_b), "--goal-id", "goal-b",
        "--objective", "Separate goal isolation boundary.", "--write-scope", "docs/**")
    cli("configure-goal", "--goal-id", "goal-b", "--explore-mode", "evidence", "--execute")

    disabled_dir = tmp_path / "proj-disabled"
    disabled_dir.mkdir()
    cli("bootstrap", "--project", str(disabled_dir), "--goal-id", "goal-off",
        "--objective", "Disabled Explore goal.", "--write-scope", "docs/**")

    # 2. Primary Explore write, readback, and continuation on enabled Goal A.
    root_node = cli(
        "explore", "node", "--goal-id", "goal-a", "--node-id", "q_primary",
        "--kind", "question", "--status", "exploring",
        "--title", "Does package separation retain Goal continuity?",
        "--agent-id", "researcher",
    )
    assert root_node["ok"] is True
    first_finding = cli(
        "explore", "finding", "--goal-id", "goal-a", "--finding-id", "f_initial",
        "--node", "q_primary", "--status", "confirmed",
        "--title", "Core retains result log and Goal boundary checks.",
        "--evidence-ref", "docs/architecture/rfcs/monorepo-distribution-split-v0.md",
        "--agent-id", "researcher",
    )
    assert first_finding["ok"] is True
    readback = cli("explore", "summary", "--goal-id", "goal-a")
    assert readback["ok"] is True and readback["counts"]["node_count"] == 1 and readback["counts"]["finding_count"] == 1
    follow_node = cli(
        "explore", "node", "--goal-id", "goal-a", "--node-id", "h_bridge",
        "--kind", "hypothesis", "--parent", "q_primary", "--status", "open",
        "--title", "Continuation child node", "--agent-id", "researcher",
    )
    assert follow_node["ok"] is True
    branch_plan = cli("explore", "worker-branch-plan", "--goal-id", "goal-a", "--agent-id", "researcher")
    assert branch_plan["ok"] is True
    turn_ctx = cli("explore", "turn-context", "--goal-id", "goal-a", "--agent-id", "researcher")
    assert turn_ctx["goal_id"] == "goal-a" and turn_ctx["graph_enabled"] is True

    # 3. Parent-admitted external-evidence -> Explore (no auto-execution, cross-goal rejection, sanitization, replay).
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    providers = ext_dir / "providers.json"
    providers.write_text(json.dumps({"providers": [{
        "provider_id": "host:external-research", "provider_kind": "method",
        "protocol": "external_evidence_research_v0", "declared": True,
        "installed": True, "enabled": True, "ready": True,
    }]}), encoding="utf-8")
    plan = cli(
        "external-evidence", "plan", "--objective", "Verify external citation intake",
        "--user-activity", "Review capture", "--decision", "Admit sanitized evidence",
        "--evidence-kind", "original_source", "--provider-inventory-json", str(providers),
    )
    assert plan["execution_envelope"]["authority"] == "read_external_sources_only"
    assert plan["execution_envelope"]["raw_content_persistence"] == "provider_private"
    plan_file = ext_dir / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")

    local_root = "/" + "Users/alice/" + "private/"
    local_leak_ref = local_root + "secret_brief.md"
    public_ref = "https://example.com/public-evidence"
    opaque_ext_ref = "urn:private-connector:doc-42"
    receipt = {
        "schema_version": "loopx_external_evidence_receipt_v0",
        "plan_id": plan["plan_id"],
        "request_id": plan["request"]["request_id"],
        "provider_id": "host:external-research",
        "provider_kind": "method",
        "status": "succeeded",
        "summary": "Captured fixture receipt without network execution.",
        "completed_at": "2026-09-22T00:00:00Z",
        "sources": [
            {
                "source_ref": public_ref,
                "source_family": "public-fixture",
                "basis": "observed",
                "finding": f"Fixture confirms contract semantics from {local_root}local.txt cleanly.",
                "accessed_at": "2026-09-21T10:00:00Z",
                "publication_date": None,
                "content_digest": "sha256:" + "a" * 64,
            },
            {
                "source_ref": opaque_ext_ref,
                "source_family": "connector-fixture",
                "basis": "observed",
                "finding": f"Sanitized non-HTTP locator with {local_root}nested.md ref.",
                "accessed_at": "2026-09-21T10:05:00Z",
                "publication_date": None,
                "content_digest": "sha256:" + "b" * 64,
            },
        ],
    }
    receipt_file = ext_dir / "receipt.json"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

    # External evidence admission itself rejects bare local file paths as provenance URIs.
    file_leak_receipt = {**receipt, "sources": [{**receipt["sources"][0], "source_ref": local_leak_ref}]}
    file_leak_path = ext_dir / "file_leak_receipt.json"
    file_leak_path.write_text(json.dumps(file_leak_receipt), encoding="utf-8")
    file_leak_err = cli(
        "external-evidence", "admit", "--plan-json", str(plan_file),
        "--receipt-json", str(file_leak_path), "--decision", "admit",
        "--reason", "Reject local path URI", "--admit-source", local_leak_ref, expected=1,
    )
    assert file_leak_err["ok"] is False and "non-file provenance URI" in file_leak_err["error"]

    admitted = cli(
        "external-evidence", "admit", "--plan-json", str(plan_file),
        "--receipt-json", str(receipt_file), "--decision", "admit",
        "--reason", "Validated capture",
        "--admit-source", public_ref, "--admit-source", opaque_ext_ref,
    )
    admit_file = ext_dir / "admit.json"
    admit_file.write_text(json.dumps(admitted), encoding="utf-8")

    rejected_adm = cli(
        "external-evidence", "admit", "--plan-json", str(plan_file),
        "--receipt-json", str(receipt_file), "--decision", "reject",
        "--reason", "Declined",
    )
    reject_file = ext_dir / "reject.json"
    reject_file.write_text(json.dumps(rejected_adm), encoding="utf-8")

    # Goal C has Explore disabled -> refused (no extra Goal write authority).
    off_err = cli(
        "explore", "observe-external", "--goal-id", "goal-off", "--node", "q_primary",
        "--plan-json", str(plan_file), "--receipt-json", str(receipt_file),
        "--admission-json", str(admit_file), "--execute", expected=1,
    )
    assert off_err["ok"] is False and "enabled" in off_err["error"]

    # Non-admitted disposition refused.
    rej_err = cli(
        "explore", "observe-external", "--goal-id", "goal-a", "--node", "q_primary",
        "--plan-json", str(plan_file), "--receipt-json", str(receipt_file),
        "--admission-json", str(reject_file), "--execute", expected=1,
    )
    assert rej_err["ok"] is False

    # Cross-goal rejection: Goal B node missing or explicit expected-goal mismatch fails closed.
    cli("explore", "node", "--goal-id", "goal-b", "--node-id", "q_b", "--kind", "question", "--title", "Goal B node")
    cross_err = cli(
        "explore", "observe-external", "--goal-id", "goal-b", "--node", "q_b",
        "--expected-goal-id", "goal-a",
        "--plan-json", str(plan_file), "--receipt-json", str(receipt_file),
        "--admission-json", str(admit_file), "--execute", expected=1,
    )
    assert cross_err["ok"] is False and "different goal" in cross_err["error"]
    goal_b_before = cli("explore", "summary", "--goal-id", "goal-b")
    assert goal_b_before["counts"]["finding_count"] == 0

    # Dry-run readback & observe-external must not append.
    dry = cli(
        "external-evidence", "readback", "--goal-id", "goal-a", "--explore-node", "q_primary",
        "--plan-json", str(plan_file), "--receipt-json", str(receipt_file),
        "--admission-json", str(admit_file),
    )
    assert dry["explore_projection"]["dry_run"] is True and dry["explore_projection"]["written"] is False

    # Apply with --execute: sanitizes local paths, rejects credentials, and replays idempotently.
    applied = cli(
        "explore", "observe-external", "--goal-id", "goal-a", "--node", "q_primary",
        "--plan-json", str(plan_file), "--receipt-json", str(receipt_file),
        "--admission-json", str(admit_file), "--execute",
    )
    assert applied["ok"] is True and applied["written"] is True and applied["appended_count"] == 2
    assert applied["redacted_source_count"] >= 2 and applied["automatic_execution"] is False

    log_file = Path(applied["log_path"])
    after_apply_bytes = log_file.read_bytes()
    log_text = after_apply_bytes.decode("utf-8")
    assert ("/" + "Users/") not in log_text and "secret_brief.md" not in log_text and "local.txt" not in log_text
    assert "nested.md" not in log_text and opaque_ext_ref not in log_text and "[redacted-local-path]" in log_text

    replay = cli(
        "explore", "observe-external", "--goal-id", "goal-a", "--node", "q_primary",
        "--plan-json", str(plan_file), "--receipt-json", str(receipt_file),
        "--admission-json", str(admit_file), "--execute",
    )
    assert replay["ok"] is True and replay["written"] is False and replay["replayed"] is True and replay["appended_count"] == 0
    assert log_file.read_bytes() == after_apply_bytes

    # Credential-bearing evidence fails closed cleanly.
    secret_marker = "tok" + "en=synthetic_credential_fixture_000"
    secret_receipt = {
        **receipt,
        "sources": [{
            **receipt["sources"][0],
            "finding": f"Captured {secret_marker} from private endpoint",
            "content_digest": "sha256:" + "c" * 64,
        }],
    }
    sec_rec_path = ext_dir / "sec_receipt.json"
    sec_rec_path.write_text(json.dumps(secret_receipt), encoding="utf-8")
    sec_admit = cli(
        "external-evidence", "admit", "--plan-json", str(plan_file),
        "--receipt-json", str(sec_rec_path), "--decision", "admit",
        "--reason", "Secret test", "--admit-source", public_ref,
    )
    sec_admit_path = ext_dir / "sec_admit.json"
    sec_admit_path.write_text(json.dumps(sec_admit), encoding="utf-8")
    sec_err = cli(
        "explore", "observe-external", "--goal-id", "goal-a", "--node", "q_primary",
        "--plan-json", str(plan_file), "--receipt-json", str(sec_rec_path),
        "--admission-json", str(sec_admit_path), "--execute", expected=1,
    )
    assert sec_err["ok"] is False and log_file.read_bytes() == after_apply_bytes

    # 4. One-way read-only deepresearch citation export (non-backwrite, sanitization, replay idempotence).
    dr_proj = tmp_path / "dr-proj"
    dr_proj.mkdir()
    cli("deepresearch", "start", "--project", str(dr_proj), "--question", "Audit private citations cleanly.")
    cli("deepresearch", "add-source", "--project", str(dr_proj),
        "--url-or-path", local_root + "confidential_report.md",
        "--tool", "local_read",
        "--claims-json", json.dumps([{"text": "Private report prose should remain in session ledger.", "stance": "supports"}]))
    unanswered_err = cli(
        "explore", "import-citations", "--goal-id", "goal-a", "--node", "h_bridge",
        "--project", str(dr_proj), "--execute", expected=1,
    )
    assert unanswered_err["ok"] is False and "no authorized resolved citations" in unanswered_err["error"]
    cli("deepresearch", "resolve-question", "--project", str(dr_proj),
        "--question-id", "q1", "--answer", "CONFIDENTIAL BODY DO NOT LEAK", "--evidence-claims", "c1")
    cli("deepresearch", "report", "--project", str(dr_proj))

    ledger_path = dr_proj / ".loopx/deepresearch/research.json"
    report_path = dr_proj / ".loopx/deepresearch/report.md"
    ledger_before = ledger_path.read_bytes()
    report_before = report_path.read_bytes()

    dr_cross = cli(
        "explore", "import-citations", "--goal-id", "goal-b", "--node", "q_b",
        "--project", str(dr_proj), "--expected-goal-id", "goal-a", "--execute", expected=1,
    )
    assert dr_cross["ok"] is False

    dr_dry = cli(
        "explore", "import-citations", "--goal-id", "goal-a", "--node", "h_bridge",
        "--project", str(dr_proj),
    )
    assert dr_dry["ok"] is True and dr_dry["written"] is False and dr_dry["deepresearch_ledger_mutated"] is False

    dr_exec = cli(
        "explore", "import-citations", "--goal-id", "goal-a", "--node", "h_bridge",
        "--project", str(dr_proj), "--agent-id", "researcher", "--execute",
    )
    assert dr_exec["ok"] is True and dr_exec["written"] is True and dr_exec["appended_count"] == 1
    assert dr_exec["deepresearch_ledger_mutated"] is False
    assert ledger_path.read_bytes() == ledger_before and report_path.read_bytes() == report_before

    after_dr_bytes = log_file.read_bytes()
    after_dr_text = after_dr_bytes.decode("utf-8")
    assert ("/" + "Users/") not in after_dr_text
    assert "confidential_report.md" not in after_dr_text
    assert "CONFIDENTIAL BODY DO NOT LEAK" not in after_dr_text
    assert "deepresearch:claim:c1:" in after_dr_text

    dr_replay = cli(
        "explore", "import-citations", "--goal-id", "goal-a", "--node", "h_bridge",
        "--project", str(dr_proj), "--agent-id", "researcher", "--execute",
    )
    assert dr_replay["ok"] is True and dr_replay["written"] is False and dr_replay["replayed"] is True and dr_replay["appended_count"] == 0
    assert log_file.read_bytes() == after_dr_bytes
    assert ledger_path.read_bytes() == ledger_before


@pytest.mark.skipif(not RESEARCH_ENABLED, reason="requires the matching Research wheel")
def test_installed_research_continues_original_ledger(core_python, tmp_path):
    question = "Where does the sample inventory go?"
    source = "inventory.txt"
    (tmp_path / source).write_text("The sample is held in bin A.\n", encoding="utf-8")
    for args in (
        ["start", "--question", question],
        ["add-source", "--url-or-path", source, "--tool", "local_read",
         "--claims-json", '[{"text":"The sample is held in bin A.","stance":"supports"}]'],
    ):
        _run(sys.executable, ["--format", "json", "deepresearch", *args], tmp_path)
    ledger = tmp_path / ".loopx/deepresearch/research.json"
    before = ledger.read_bytes()
    status = json.loads(_run(core_python, ["--format", "json", "deepresearch", "status"], tmp_path).stdout)
    assert status["packet"]["question"] == question
    assert status["packet"]["ledger_summary"]["sources"] == 1
    assert ledger.read_bytes() == before
    _run(core_python, ["deepresearch", "resolve-question", "--question-id", "q1",
                       "--answer", "The sample is held in bin A.", "--evidence-claims", "c1"], tmp_path)
    report = json.loads(_run(core_python, ["--format", "json", "deepresearch", "report"], tmp_path).stdout)
    assert "inventory.txt" in Path(report["report_path"]).read_text(encoding="utf-8")
    assert len(json.loads(ledger.read_bytes())["sources"]) == 1
