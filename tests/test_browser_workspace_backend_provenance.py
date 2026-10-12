"""Browser launchers select a package source without installing or borrowing one."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required by browser smokes")
LAUNCH = """
import {spawnSync} from 'node:child_process';
import {testPythonCommand} from './scripts/test-python.mjs';
const plan = testPythonCommand(process.argv[1]);
const result = spawnSync(plan.command, plan.args, {encoding:'utf8', timeout:30000});
process.stdout.write(result.stdout ?? '');
process.stderr.write(result.stderr ?? '');
process.exit(result.status ?? 1);
"""


@pytest.fixture
def bare_python(tmp_path: Path) -> Path:
    target = tmp_path / "python environment"
    venv.EnvBuilder(with_pip=False).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    probe = subprocess.run(
        [str(python), "-I", "-c", "import importlib.util; assert importlib.util.find_spec('loopx') is None"],
        check=True, capture_output=True, text=True,
    )
    assert probe.stdout == ""
    return python


def environment(python: Path, tmp_path: Path, *, installed: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "LOOPX_TEST_PYTHON", "LOOPX_PYTHON_BIN", "LOOPX_PYTHON", "VIRTUAL_ENV"):
        env.pop(key, None)
    env["LOOPX_PYTHON_BIN" if installed else "LOOPX_TEST_PYTHON"] = str(python)
    env.update(TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    return env


def launch(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    return subprocess.run(
        [NODE, "--input-type=module", "-e", LAUNCH, code],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=40,
    )


def test_source_backend_uses_selected_bare_interpreter(bare_python: Path, tmp_path: Path) -> None:
    result = launch(
        "import json; from loopx.chat_server import ChatHTTPServer; "
        "print(json.dumps({'python':sys.executable,'package':loopx.__file__}))",
        environment(bare_python, tmp_path),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert Path(observed["python"]) == bare_python
    assert Path(observed["package"]).resolve() == ROOT / "loopx/__init__.py"


def test_installed_profile_missing_package_cannot_borrow_checkout(bare_python: Path, tmp_path: Path) -> None:
    env = environment(bare_python, tmp_path, installed=True)
    env["PYTHONPATH"] = str(ROOT)
    result = launch("print('unexpected backend startup')", env)
    assert result.returncode != 0
    assert "ModuleNotFoundError: No module named 'loopx'" in result.stderr
    assert "unexpected backend startup" not in result.stdout


@pytest.mark.parametrize("editable", [False, True])
def test_installed_profile_package_origin(bare_python: Path, tmp_path: Path, editable: bool) -> None:
    # Minimal import fixture tests the loader, not installed product acceptance.
    probe = subprocess.run(
        [str(bare_python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True, capture_output=True, text=True,
    )
    site = Path(probe.stdout.strip())
    if editable:
        (site / "checkout.pth").write_text(str(ROOT) + "\n", encoding="utf-8")
    else:
        package = site / "loopx"
        package.mkdir()
        (package / "__init__.py").write_text("marker = 'installed-only'\n", encoding="utf-8")
    result = launch("print(loopx.marker)", environment(bare_python, tmp_path, installed=True))
    if editable:
        assert result.returncode != 0
        assert "wrong LoopX package source" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "installed-only"


def test_source_profile_ignores_foreign_pythonpath(bare_python: Path, tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    (foreign / "loopx").mkdir(parents=True)
    (foreign / "loopx/__init__.py").write_text("raise RuntimeError('foreign package imported')\n", encoding="utf-8")
    env = environment(bare_python, tmp_path)
    env["PYTHONPATH"] = str(foreign)
    result = launch("print(loopx.__file__)", env)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == ROOT / "loopx/__init__.py"


def test_real_workspace_backend_http_from_bare_python(bare_python: Path, tmp_path: Path) -> None:
    assert NODE is not None
    code = f"""
import runpy
sys.argv = [{str(ROOT / 'examples/personal-workspace-browser/goal-workspace-backend.py')!r}, {str(tmp_path)!r}]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    runner = """
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {testPythonCommand} from './scripts/test-python.mjs';
const plan = testPythonCommand(process.argv[1]);
const child = spawn(plan.command, plan.args, {stdio:['ignore','pipe','pipe']});
const exited = once(child, 'exit');
const lines = createInterface({input:child.stdout});
let diagnostic = '';
child.stderr.on('data', chunk => {diagnostic += chunk;});
let timer;
try {
  const ready = await Promise.race([
    once(lines, 'line').then(([line]) => JSON.parse(line)),
    exited.then(() => {throw new Error(diagnostic || 'Backend exited before readiness');}),
    new Promise((_, reject) => {timer = setTimeout(() => reject(new Error('Backend readiness timeout')), 30000);}),
  ]);
  const response = await fetch(`http://127.0.0.1:${ready.port}/api/actions`);
  console.log(JSON.stringify({status:response.status, body:await response.json(), pid:child.pid}));
} finally {
  clearTimeout(timer);
  lines.close();
  child.kill('SIGTERM');
  const stopTimer = setTimeout(() => child.kill('SIGKILL'), 5000);
  try {await exited;} finally {clearTimeout(stopTimer);}
}
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", runner, code], cwd=ROOT,
        env=environment(bare_python, tmp_path), capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["status"] == 200
    assert observed["body"]["ok"] is True
    assert observed["pid"] != os.getpid()
