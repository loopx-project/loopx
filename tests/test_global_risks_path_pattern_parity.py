"""Keep the cross-runtime public path boundary tied to its canonical owner."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from loopx.presentation.public_safety import LOCAL_PATH_PATTERNS
from loopx.public_safe_text import PRESENTATION_LOCAL_PATH_PATTERNS


def test_global_risks_path_patterns_match_python_owner() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for global-risk path pattern parity")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            node, "--no-warnings", "--experimental-strip-types", "--input-type=module",
            "-e",
            "import {LOCAL_PATH_PATTERNS} from "
            "'./loopx/control_plane/presentation/global_risk_read.ts';"
            "console.log(JSON.stringify(LOCAL_PATH_PATTERNS.map("
            "pattern => [pattern.source, pattern.flags])));",
        ],
        cwd=root, check=True, capture_output=True, text=True,
    )
    assert LOCAL_PATH_PATTERNS is PRESENTATION_LOCAL_PATH_PATTERNS
    expected = []
    for pattern in PRESENTATION_LOCAL_PATH_PATTERNS:
        assert not pattern.flags & ~(re.UNICODE | re.IGNORECASE)
        source = pattern.pattern
        ignore_case = bool(pattern.flags & re.IGNORECASE)
        # JS uses whole-pattern i for Python's scoped Windows keyword folding.
        # Everything else in those patterns is punctuation or already both-case
        # ASCII classes. Only these known scoped groups are equivalent here.
        for scoped, plain in (
            (r"(?i:UNC)", "UNC"),
            (r"(?i:Volume)", "Volume"),
            (r"(?i:GLOBALROOT\\Device\\)", r"GLOBALROOT\\Device\\"),
        ):
            if scoped in source:
                source = source.replace(scoped, plain)
                ignore_case = True
        # Python raw strings escape quotes; JS regex literals escape slashes.
        source = source.replace(r'\"', '"')
        expected.append([source, "i" if ignore_case else ""])
    actual = []
    for source, flags in json.loads(result.stdout):
        # g implements repeated substitution; u is JS's Unicode parsing mode.
        actual.append([re.sub(r"(?<!\\)((?:\\\\)*)\\/", r"\1/", source), flags.replace("g", "").replace("u", "")])
    assert actual == expected
