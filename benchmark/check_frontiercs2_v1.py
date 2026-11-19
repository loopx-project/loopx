#!/usr/bin/env python3
"""Verify frozen compatibility sources without importing or executing them."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


ARCHIVE = Path(__file__).resolve().with_name("frontiercs2-single-host-v1")
EXPECTED = {
    "docker_environment.py": "d6e22a5f894e801d3736657a69b700f16b83f008240235121f5ff40361914b64",
    "frontier_submit.py": "f4cbc551acd10a729e5cade5cc3a70b653d9aee1dee4b1cbfeb20b3208df6f00",
    "local_runner.py": "5f407c8c9d800eb110cc453a9815b571861d0d2fca6412ffe1c83e9918938cad",
    "source-manifest.json": "d0c096b7e1b67630174990f9813e16f7904d27b11be9edf7892c9fe8ae579cbc",
    "submission_bridge.py": "cd1be73411bbb94f1a7ee0df9e49f0d26aa5d6e23412be233c5e483a1728b639",
}


def check_archive(archive: Path) -> dict:
    observed = set()
    python_count = embedded_count = 0
    for path in archive.iterdir():
        if path.name == "__pycache__" and path.is_dir() and not path.is_symlink():
            if any(p.is_symlink() or not p.is_file() or p.suffix != ".pyc" for p in path.iterdir()):
                raise ValueError("unexpected non-bytecode cache entry")
            continue
        if path.is_symlink() or not path.is_file() or path.name not in EXPECTED:
            raise ValueError(f"unexpected archive entry: {path.name}")
        if path.stat().st_mode & 0o111:
            raise ValueError(f"executable mode changed: {path.name}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != EXPECTED[path.name]:
            raise ValueError(f"archive bytes changed: {path.name}")
        observed.add(path.name)
        if path.suffix == ".py":
            compile(raw, path.name, "exec")
            python_count += 1
            for node in ast.parse(raw).body:
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and any(isinstance(t, ast.Name) and t.id == "NNP_BOOTSTRAP" for t in node.targets)):
                    compile(node.value.value, f"{path.name}:NNP_BOOTSTRAP", "exec")
                    embedded_count += 1
        else:
            json.loads(raw)
    if observed != set(EXPECTED):
        raise ValueError("archive source files are missing")
    return {
        "original_snapshot_preserved": True,
        "files_checked": len(observed),
        "python_syntax_checks": python_count,
        "embedded_python_checks": embedded_count,
        "benchmark_executed": False,
        "standalone_runnable": False,
        "runtime_validation": "not_performed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    args = parser.parse_args()
    try:
        result = check_archive(args.archive)
    except (OSError, ValueError, SyntaxError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
