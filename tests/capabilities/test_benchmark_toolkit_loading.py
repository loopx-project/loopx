"""Discovery must not depend on optional benchmark execution implementations."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest


DISCOVERY_SCRIPT = '''import importlib.abc,json,sys
class MetadataOnly(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  prefix='loopx.capabilities.benchmark_toolkit.'
  if fullname.startswith(prefix) and fullname not in (prefix+'catalog_entry',prefix+'four_arm_contract'):
   raise AssertionError('unselected benchmark implementation was imported: '+fullname)
sys.meta_path.insert(0,MetadataOnly())
def no_execution(event,args):
 if event in ('subprocess.Popen','os.system','os.posix_spawn','socket.connect'):
  raise AssertionError('discovery executed an external operation: '+event)
sys.addaudithook(no_execution)
from loopx.entrypoint import main
raise SystemExit(main(json.loads(sys.argv[1])))
'''


@pytest.mark.parametrize("arguments", [
    ["capability", "--help"],
    ["capability", "list", "--format", "json"],
    ["capability", "show", "benchmark-toolkit", "--format", "json"],
    ["capability", "show", "issue-fix", "--format", "json"],
])
def test_capability_discovery_survives_unavailable_benchmark_execution(tmp_path, arguments):
    result = subprocess.run([sys.executable, "-I", "-c", DISCOVERY_SCRIPT,
        json.dumps(["--registry", str(tmp_path / "registry.json"),
                   "--runtime-root", str(tmp_path / "runtime"), *arguments])],
        cwd=tmp_path, env={**os.environ, "PATH": "", "LOOPX_USAGE_PING": "0"},
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    if arguments[-1] == "json":
        assert json.loads(result.stdout)["ok"] is True
    assert not list(tmp_path.iterdir())


def test_toolkit_import_and_selected_export_leave_other_implementations_unloaded(tmp_path):
    source = '''import importlib.abc,sys
class SelectedOnly(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.startswith('loopx.capabilities.benchmark_toolkit.') and not fullname.endswith('.four_arm_contract'):
   raise ModuleNotFoundError('optional implementation unavailable',name=fullname)
sys.meta_path.insert(0,SelectedOnly())
import loopx.capabilities.benchmark_toolkit as toolkit
assert not any(name.startswith(toolkit.__name__+'.') for name in sys.modules)
assert 'build_benchmark_four_arm_contract' in dir(toolkit)
from loopx.capabilities.benchmark_toolkit import build_benchmark_four_arm_contract
from loopx.capabilities.benchmark_toolkit.four_arm_contract import build_benchmark_four_arm_contract as canonical
assert build_benchmark_four_arm_contract is canonical
assert toolkit.build_benchmark_four_arm_contract is canonical
assert toolkit.four_arm_contract.build_benchmark_four_arm_contract is canonical
try:toolkit.run_native_goal_turn
except ModuleNotFoundError as exc:assert exc.name==toolkit.__name__+'.native_codex_goal'
else:raise AssertionError('a selected missing implementation must remain an error')
try:toolkit.unknown_export
except AttributeError:pass
else:raise AssertionError('unknown exports must remain unavailable')
'''
    result = subprocess.run([sys.executable, "-I", "-c", source], cwd=tmp_path,
        env={**os.environ, "LOOPX_USAGE_PING": "0"}, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_explicit_star_import_retains_public_exports():
    import loopx.capabilities.benchmark_toolkit as toolkit
    from loopx.capabilities.benchmark_toolkit import native_codex_goal

    exports = {}
    exec("from loopx.capabilities.benchmark_toolkit import *", exports)
    assert {name for name in exports if not name.startswith("_")} == set(toolkit.__all__)
    assert exports["NativeGoalConfig"] is native_codex_goal.NativeGoalConfig
    assert exports["run_native_goal_turn"] is native_codex_goal.run_native_goal_turn
