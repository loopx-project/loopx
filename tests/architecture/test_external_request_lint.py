"""New consumers use checked entry points even if they rename a raw import."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("policy", "source", "rejected"), [
    ("external-request", "from ...extensions.runtime import _execute_extension_runtime_binding as send  # noqa: TID251", True),
    ("external-request", "from loopx.extensions import runtime as r\nr._execute_extension_runtime_binding(b, request=q)", True),
    ("external-request", "from loopx.extensions.capability_admission import prepare_external_capability_invocation", True),
    ("external-request", "from loopx.extensions.governed_capability_execution import start_governed_external_capability", False),
    ("external-transport", "from urllib.request import urlopen as send", True),
    ("external-transport", "import httpx as client", True),
    ("external-transport", "from loopx.extensions.runtime import execute_extension_runtime_binding as send", True),
    ("external-transport", "from loopx.extensions.capability_admission import invoke_external_capability", False),
])
def test_external_request_source_boundary(policy, source, rejected):
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--ignore-noqa",
         "--config", f"scripts/ci/{policy}-lint.toml", "--output-format", "json",
         "--stdin-filename", "loopx/capabilities/new_service/adapter.py", "-"],
        cwd=ROOT, input=source, capture_output=True, text=True, check=False,
    )
    assert result.returncode == int(rejected), result.stderr or result.stdout
    assert [item["code"] for item in json.loads(result.stdout)] == (["TID251"] if rejected else [])
