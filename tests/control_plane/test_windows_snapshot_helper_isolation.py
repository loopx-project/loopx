"""The package-owned ACL verifier must not execute ambient Python startup code."""

import sys

import pytest

from loopx.control_plane.runtime.private_permissions import protect_private_path
from tests.control_plane.test_windows_snapshot_privacy import typed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL helper startup")
@pytest.mark.parametrize("operation", ["read", "sink"])
def test_snapshot_helper_ignores_ambient_sitecustomize(tmp_path, monkeypatch, operation):
    startup = tmp_path / "ambient"
    startup.mkdir()
    marker = startup / "executed"
    (startup / "sitecustomize.py").write_text(
        "from pathlib import Path\nimport os\n"
        f"Path({str(marker)!r}).write_text('ambient startup ran')\n"
        "os._exit(74)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(startup))
    parent = tmp_path / "loopx-effect-owned"
    parent.mkdir()
    protect_private_path(parent, directory=True)
    leaf = parent / "response.json"
    leaf.write_text('{"synthetic":true}', encoding="utf-8")
    protect_private_path(leaf)

    result = typed(leaf, op=operation)

    assert result == {"accepted": True, "effects": 1}, result
    assert not marker.exists()
