"""Research wheel ownership and the existing installed CLI journey."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_core_distribution import _environment, _run, core_python as core_python


DISTRIBUTION = "loopx-capability-research"
RESEARCH_ENABLED = os.environ.get("LOOPX_RESEARCH_TEST_ENABLED") == "1"


def test_installed_research_inventory_and_help_are_passive(core_python, tmp_path):
    script = '''
import sys
from importlib.metadata import distribution
from importlib.util import find_spec
from loopx.entrypoint import main
from loopx.release_manifest import _distribution_runtime_fingerprint

installed = sys.argv[1] == "True"
modules = (
    "loopx.capabilities.deep_research.runtime",
    "loopx.capabilities.external_research.cli",
    "loopx.capabilities.external_research.projection",
    "loopx.capabilities.external_research.provider_probe",
    "loopx.cli_commands.deepresearch",
    "loopx.extensions.public_github_research",
)
assert all((find_spec(module) is not None) is installed for module in modules)
core = distribution("loopx-core")
core_files = {entry.as_posix() for entry in core.files}
owner_files = {module.replace(".", "/") + ".py" for module in modules}
owner_files.add("loopx/control_plane/capabilities/external_evidence.ts")
assert owner_files.isdisjoint(core_files)
if installed:
    research = distribution("loopx-capability-research")
    assert research.version == core.version
    assert research.requires == ["loopx-core==" + core.version]
    members = {entry.as_posix() for entry in research.files if entry.parts[0] == "loopx"}
    assert members == owner_files and members.isdisjoint(core_files)
assert _distribution_runtime_fingerprint() is not None
assert main(["capability", "list", "--format", "json"]) == 0
assert main(["commands", "--format", "json"]) == 0
assert all(module not in sys.modules for module in modules)
'''
    probe = subprocess.run(
        [core_python, "-I", "-c", script, str(RESEARCH_ENABLED)],
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
    for command in ("deepresearch", "external-evidence"):
        assert (command in listed_commands) is RESEARCH_ENABLED
    entries = {row["id"]: row for row in cap_payload["capabilities"]}
    for name, command in (("deep-research", "deepresearch"), ("external-evidence-research", "external-evidence")):
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


def test_core_staging_has_zero_unconditional_research_wheel_imports(tmp_path):
    import ast
    from importlib.util import module_from_spec, spec_from_file_location

    repo = Path(__file__).resolve().parents[1]
    spec = spec_from_file_location("loopx_core_prepare", repo / "packages/loopx-core/prepare.py")
    prepare = module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(prepare)
    staged = prepare.prepare_core_source(tmp_path / "source")
    research_modules = {
        ".".join(list(p.with_suffix("").parts)[:-1] if p.stem == "__init__" else p.with_suffix("").parts)
        for p in prepare.RESEARCH_FILES
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


@pytest.mark.skipif(not RESEARCH_ENABLED, reason="requires the matching Research wheel")
def test_installed_research_doctor_diagnoses_mismatch_and_missing_resource(core_python, tmp_path):
    probe = subprocess.run(
        [core_python, "-I", "-c",
         "from importlib.metadata import distribution; from pathlib import Path;"
         "d=distribution('loopx-capability-research');"
         "dist_info=next(p.locate().parent for p in d.files if 'METADATA' in str(p));"
         "res=next(p.locate() for p in d.files if str(p).endswith('external_evidence.ts'));"
         "print(dist_info); print(res)"],
        capture_output=True, text=True, check=True,
    )
    dist_info_str, res_str = probe.stdout.strip().splitlines()
    metadata = Path(dist_info_str) / "METADATA"
    resource = Path(res_str)
    original_meta = metadata.read_bytes()
    backup_res = resource.with_suffix(".ts.bak")
    try:
        import re
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
        cap = json.loads(_run(core_python, ["capability", "show", "external-evidence-research", "--format", "json"], tmp_path).stdout)
        assert cap["capability"]["provider_state"]["installed"] is True
        assert cap["capability"]["provider_state"]["ready"] is False
    finally:
        metadata.write_bytes(original_meta)

    try:
        # 2. Missing external_evidence.ts must fail doctor --installation-only with missing file detail.
        resource.rename(backup_res)
        missing = _run(core_python, ["doctor", "--installation-only", "--format", "json"], tmp_path, expected=1)
        r_payload = json.loads(missing.stdout)
        assert r_payload["ok"] is False
        assert "package_fingerprint" not in r_payload["service_runtime_identity"]
        comp = {c["distribution"]: c for c in r_payload["companion_distributions"]}[DISTRIBUTION]
        assert comp["installed"] is True and comp["ready"] is False
        assert "missing_files" in comp["reasons"]
        assert "loopx/control_plane/capabilities/external_evidence.ts" in comp["missing_resources"]
        cmds = json.loads(_run(core_python, ["commands", "--format", "json"], tmp_path).stdout)
        listed = {e["command"].split()[1] for g in cmds["groups"] for e in g["commands"] if len(e["command"].split()) >= 2}
        assert "external-evidence" not in listed and "deepresearch" not in listed
    finally:
        if backup_res.exists():
            backup_res.rename(resource)

    restored = json.loads(_run(core_python, ["doctor", "--installation-only", "--format", "json"], tmp_path).stdout)
    assert restored["ok"] is True
    assert restored["service_runtime_identity"]["package_fingerprint"].startswith("sha256:")


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
