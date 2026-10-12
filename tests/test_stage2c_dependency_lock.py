"""The hash-locked CI environment must cover its declared dependencies."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def test_stage2c_lock_covers_runtime_test_and_build_requirements() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    tools = (ROOT / "tests/requirements-stage2c-tools.in").read_text(encoding="utf-8")
    declared = [
        *project["project"]["dependencies"],
        *project["project"]["optional-dependencies"]["test"],
        *(
            line.strip()
            for line in tools.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ),
    ]
    locked = (ROOT / "tests/requirements-stage2c-linux-py311.txt").read_text(
        encoding="utf-8"
    )
    pins = {
        canonicalize_name(name): version
        for name, version in re.findall(
            r"^([\w.-]+)==([^\s;\\]+)", locked, re.MULTILINE
        )
    }
    # Evaluate declarations for the lock's target, independently of the host.
    environment = {
        **default_environment(),
        "implementation_name": "cpython",
        "implementation_version": "3.11.0",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_python_implementation": "CPython",
        "platform_system": "Linux",
        "python_full_version": "3.11.0",
        "python_version": "3.11",
        "sys_platform": "linux",
        "extra": "test",
    }
    for value in declared:
        requirement = Requirement(value)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        name = canonicalize_name(requirement.name)
        assert name in pins, f"Stage 2C lock is missing declared dependency: {value}"
        assert requirement.specifier.contains(pins[name], prereleases=True), (
            f"Stage 2C pin {name}=={pins[name]} does not satisfy {value}"
        )
