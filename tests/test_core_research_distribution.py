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
assert all(module not in sys.modules for module in modules)
'''
    probe = subprocess.run(
        [core_python, "-I", "-c", script, str(RESEARCH_ENABLED)],
        cwd=tmp_path, env=_environment(core_python, {"PATH": str(tmp_path)}),
        capture_output=True, text=True, timeout=45,
    )
    assert probe.returncode == 0, probe.stderr
    entries = {row["id"]: row for row in json.loads(probe.stdout)["capabilities"]}
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
    assert not list(tmp_path.iterdir())


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
