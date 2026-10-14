"""Private source rendezvous never substitutes a late or foreign point."""
import hashlib
import json
import threading
import time

import pytest

from benchmark.runtime.checkpoint_handoff import await_controller_capture


@pytest.mark.parametrize("response", ["captured", "failed", "foreign", "timeout"])
def test_original_request_only_and_cleanup_after_missed_point(tmp_path, response):
    mailbox = tmp_path / "checkpoint-controller"
    ready = mailbox / "ready.json"
    observed = []
    def controller():
        until = time.time() + 2
        while not ready.exists() and time.time() < until:
            time.sleep(.005)
        raw = ready.read_bytes()
        request = json.loads(raw)  # Publication exposes a complete request.
        observed.append(request)
        if response == "timeout":
            return
        result = {"request_sha256": hashlib.sha256(raw).hexdigest(),
                  "ok": response == "captured", "error_kind": "SyntheticFailure",
                  "capture": {"research_point_qualified": False, "sha256": "retained"}}
        if response == "foreign":
            result["request_sha256"] = "another-point"
        pending = mailbox / "reply.tmp"
        pending.write_text(json.dumps(result))
        pending.rename(mailbox / (request["capture_id"] + ".json"))
    thread = threading.Thread(target=controller)
    thread.start()
    try:
        args = (tmp_path / "checkpoints", [{"id": "code", "source_path": "/task/code"}], {},
                {"source_sha256": "prepared", "source_witness": {}}, time.time() + .5)
        if response == "captured":
            assert await_controller_capture(*args)["sha256"] == "retained"
        else:
            with pytest.raises(TimeoutError if response == "timeout" else ValueError):
                await_controller_capture(*args)
    finally:
        thread.join(3)
    assert not thread.is_alive() and not ready.exists() and len(observed) == 1


def test_surviving_request_is_not_overwritten(tmp_path):
    mailbox = tmp_path / "checkpoint-controller"
    mailbox.mkdir()
    ready = mailbox / "ready.json"
    ready.write_bytes(b"original uncertain request")
    with pytest.raises(FileExistsError):
        await_controller_capture(tmp_path / "checkpoints", [], {},
            {"source_sha256": "later", "source_witness": {}}, time.time() + 1)
    assert ready.read_bytes() == b"original uncertain request"
