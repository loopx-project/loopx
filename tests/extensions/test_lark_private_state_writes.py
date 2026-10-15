from __future__ import annotations

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from loopx.extensions.lark.event_inbox import (
    ingest_lark_event_inbox,
    inspect_lark_event_inbox,
)
from loopx.extensions.lark.group_history_cursor import write_group_history_cursor
from loopx.extensions.lark.presentation.explore_results import (
    read_lark_explore_local_config,
    write_lark_explore_local_config,
)


def test_concurrent_inbox_retries_commit_complete_events(tmp_path, monkeypatch):
    config = tmp_path / "inbox-config.json"
    config.write_text(json.dumps({
        "schema_version": "lark_event_inbox_config_v0",
        "enabled": True,
        "inbox_dir": ".loopx/inbox/fixture",
        "capture_scope": "configured_chat_all",
    }), encoding="utf-8")
    event = {
        "schema_version": "lark_event_inbox_event_v0",
        "event_id": "evt_retry",
        "message_id": "om_retry",
        "create_time": "2026-08-26T00:00:00Z",
        "content": "可重试的材料",
    }
    replace = os.replace
    ready = Barrier(2)

    def concurrent_replace(source, destination):
        # Both collectors have accepted the same event before either commits.
        ready.wait(timeout=10)
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", concurrent_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(
            ingest_lark_event_inbox,
            project=tmp_path,
            config_path=config,
            events=[event],
            execute=True,
        ) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert all(result["write_performed"] for result in results)
    projection = inspect_lark_event_inbox(project=tmp_path, config_path=config)
    assert projection["pending_count"] == 1
    assert projection["items"][0]["content"] == event["content"]
    inbox = tmp_path / ".loopx/inbox/fixture"
    assert [path.name for path in inbox.iterdir()] == ["om_retry.json"]


@pytest.mark.parametrize("writer", [
    write_group_history_cursor,
    write_lark_explore_local_config,
])
def test_interrupted_private_state_write_preserves_previous_file(
    tmp_path, monkeypatch, writer,
):
    path = tmp_path / "state.json"
    writer(path, {"revision": "previous", "description": "已有材料"})
    previous = path.read_bytes()
    original_open = io.open

    class InterruptedWriter:
        def __init__(self, handle):
            self.handle = handle

        def write(self, text):
            self.handle.write(text[:7])
            self.handle.flush()
            raise OSError("synthetic partial write")

    @contextmanager
    def interrupted_open(file, mode="r", *args, **kwargs):
        with original_open(file, mode, *args, **kwargs) as handle:
            yield InterruptedWriter(handle) if "w" in mode else handle

    # Fail actual filesystem IO after bytes were written, independent of the
    # caller's serialization or temporary-file implementation.
    with monkeypatch.context() as patch:
        patch.setattr(io, "open", interrupted_open)
        with pytest.raises(OSError, match="synthetic partial write"):
            writer(path, {"revision": "next", "description": "后续材料"})
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX private-file mode")
def test_explore_config_replacement_is_private_and_readable(tmp_path):
    path = tmp_path / "explore.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    write_lark_explore_local_config(path, {
        "board": {"base_token": "fixture-base", "title": "研究结果"},
        "ok": True,
        "exists": True,
        "path": "display-only",
    })
    assert path.stat().st_mode & 0o777 == 0o600
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert not {"ok", "exists", "path"}.intersection(stored)
    readback = read_lark_explore_local_config(path)
    assert readback["ok"] is True
    assert readback["board"] == {"base_token": "fixture-base", "title": "研究结果"}
