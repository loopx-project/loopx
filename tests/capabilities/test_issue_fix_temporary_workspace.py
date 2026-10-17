from __future__ import annotations

import os
from pathlib import Path
import stat

from loopx.capabilities.issue_fix import acceptance_loop


def test_temporary_git_workspace_teardown_removes_read_only_objects(
    tmp_path: Path,
) -> None:
    """The packet's own temporary repository is removable on every host.

    Git marks every loose object read-only, and Windows refuses to unlink a
    read-only file, so the teardown used to raise ``PermissionError``
    [WinError 5] from its ``finally`` block after five pointless retries.
    """
    workspace = tmp_path / "loopx-issue-fix-git-fixture"
    objects = workspace / ".git" / "objects" / "32"
    objects.mkdir(parents=True)
    object_file = objects / "667d2c685aa01495f7571c1a13fe83ad1525f3"
    object_file.write_bytes(b"blob")
    os.chmod(object_file, stat.S_IREAD)

    acceptance_loop._remove_temporary_git_workspace(workspace)

    assert not workspace.exists()
