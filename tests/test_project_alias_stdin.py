"""Git alias probes must preserve the caller's open protocol input."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("missing_origin", [False, True])
def test_git_probe_preserves_protocol_input(missing_origin: bool) -> None:
    source = Path(__file__).resolve().parents[1]
    # Replace only the Git executable with a portable process that reads stdin.
    # All pipe, timeout and return-code behavior comes from the real probe.
    fake_git = (
        "import sys; head=sys.stdin.buffer.read(1); "
        f"print('consumed RPC' if head else 'missing-origin' if {missing_origin!r} "
        "else 'alias-root',flush=True); "
        f"raise SystemExit({128 if missing_origin else 0})"
    )
    code = (
        f"import sys,json; sys.path.insert(0,{str(source)!r}); "
        "from pathlib import Path; import loopx.project_alias as alias; "
        "run=alias.subprocess.run; "
        f"alias.subprocess.run=lambda argv,**kwargs: "
        f"run([sys.executable,'-I','-c',{fake_git!r}],**kwargs); "
        "result=alias._run_git(Path.cwd(),'rev-parse','--show-toplevel'); "
        "print(json.dumps({'code':result.returncode,'stdout':result.stdout,"
        "'remaining':sys.stdin.readline()}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        input="RPC packet\n",
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    row = json.loads(result.stdout)
    assert row["remaining"] == "RPC packet\n"
    assert row["code"] == (128 if missing_origin else 0)
    assert row["stdout"].strip() == ("missing-origin" if missing_origin else "alias-root")
