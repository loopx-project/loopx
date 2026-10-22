"""Acceptance receipts stay tied to native identities and expose no second result path."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from benchmark.runtime import submission_receipts
from benchmark.runtime.submission_receipts import NativeSubmissionReceipts, SCHEMA, validate_packet
from benchmark.runtime.submission_recovery import mark_dispatched, prepare_submission
from benchmark.tests.test_native_submission_recovery import LocalCopyBackend, native_http, submit_fixture


def test_publisher_only_releases_acceptance_identity_even_after_native_completion(tmp_path):
    with native_http(tmp_path, immediately_complete=True) as (state, _release, observer, root):
        # Admission preserves opaque bytes, including an archive that evaluation
        # cannot unpack. Recovery needs the exact upload, not a file manifest.
        raw = b"synthetic opaque native upload"
        accepted = submit_fixture(observer, raw)
        packet = observer.update(LocalCopyBackend(), None)
        [receipt] = packet["records"]
        assert receipt["submission_id"] == accepted["submission_id"]
        assert receipt["round_id"] == accepted["round_id"]
        assert receipt["archive_sha256"] == hashlib.sha256(raw).hexdigest()
        assert receipt["archive_bytes"] == len(raw)
        assert state["posts"] == 1 and state["result_reads"] == 0
        assert json.loads((root / "native.json").read_bytes()) == packet
        assert [path.name for path in root.iterdir()] == ["native.json"]
        assert set(receipt) == {"accepted_record", "submission_id", "round_id", "archive_sha256", "archive_bytes"}


def test_unchanged_archives_are_not_rehashed_but_changed_bytes_cannot_rebind(tmp_path, monkeypatch):
    reads = []
    original_read = submission_receipts.read_archive

    def observed_read(path, *, root):
        reads.append(path)
        return original_read(path, root=root)

    monkeypatch.setattr(submission_receipts, "read_archive", observed_read)
    with native_http(tmp_path) as (state, release, observer, root):
        accepted = submit_fixture(observer, b"accepted upload")
        release(accepted["submission_id"])
        original = observer.update(LocalCopyBackend(), None)
        assert observer.update(LocalCopyBackend(), None) == original
        assert len(reads) == 1, "status polls must not reread all accepted archive bytes"
        source = observer.trial / "submissions" / accepted["round_id"] / "submission.tar.gz"
        source.write_bytes(b"replacement upload")
        [changed] = observer.update(LocalCopyBackend(), None)["records"]
        assert len(reads) == 2
        assert changed["reason"] == "native_accepted_archive_changed"
        assert "archive_sha256" not in changed
        restarted = NativeSubmissionReceipts(trial=observer.trial, run_id=state["run_id"],
            task_id=state["task_id"], judge_url=observer.judge_url, remote_root=str(root))
        try:
            restarted.bind({"SFORGE_TOKEN": "synthetic-token"})
            assert restarted.poll()["records"][0]["reason"] == "native_accepted_archive_changed"
        finally:
            restarted.close()
        binding = json.loads((observer.directory / "bindings" / (accepted["submission_id"] + ".json")).read_bytes())
        assert binding["archive_sha256"] == original["records"][0]["archive_sha256"]
        assert state["posts"] == 1 and state["result_reads"] == 0


def test_missing_or_aliased_native_archive_cannot_supply_a_recovery_match(tmp_path):
    with native_http(tmp_path, immediately_complete=True) as (state, _release, observer, _root):
        accepted = submit_fixture(observer, b"accepted upload")
        source = observer.trial / "submissions" / accepted["round_id"] / "submission.tar.gz"
        target = source.with_name("retained.tar.gz")
        source.rename(target)
        [missing] = observer.poll()["records"]
        assert missing["accepted_record"] and "archive_sha256" not in missing
        source.symlink_to(target)
        [aliased] = observer.poll()["records"]
        assert aliased["reason"] == "native_archive_not_physical"
        assert "archive_sha256" not in aliased
        source.unlink()
        target.rename(source)
        [available] = observer.poll()["records"]
        assert available["archive_sha256"] == hashlib.sha256(b"accepted upload").hexdigest()
        assert state["posts"] == 1


@pytest.mark.parametrize("identity", ["submission_id", "round"])
def test_duplicate_native_identity_is_not_matchable_while_independent_receipts_survive(tmp_path, identity):
    with native_http(tmp_path, immediately_complete=True) as (_state, _release, observer, _root):
        first = submit_fixture(observer, b"first")
        submit_fixture(observer, b"second")
        third = submit_fixture(observer, b"third")
        # Corrupt only one identity dimension; matching must require both to be
        # unique, without suppressing the unrelated third accepted upload.
        _state["entries"][1][identity] = _state["entries"][0][identity]
        rows = observer.poll()["records"]
        assert all(row["accepted_record"] is False and "archive_sha256" not in row for row in rows[:2])
        assert rows[2]["submission_id"] == third["submission_id"]
        assert rows[2]["archive_sha256"] == hashlib.sha256(b"third").hexdigest()
        assert first["submission_id"] != third["submission_id"]


def test_a_round_cannot_change_submission_identity_after_history_is_replaced(tmp_path):
    with native_http(tmp_path, immediately_complete=True) as (state, _release, observer, _root):
        submit_fixture(observer, b"accepted upload")
        observer.poll()
        state["entries"][0]["submission_id"] = "replacement-submission"
        [changed] = observer.poll()["records"]
        assert changed["reason"] == "native_accepted_round_changed"
        assert "archive_sha256" not in changed


@pytest.mark.parametrize("terminal_status", ["completed", "error"])
def test_pending_archive_prefix_cannot_bind_before_native_writer_finishes(tmp_path, monkeypatch, terminal_status):
    reads = []
    original_read = submission_receipts.read_archive

    def observed_read(path, *, root):
        reads.append(path)
        return original_read(path, root=root)

    with native_http(tmp_path) as (state, release, observer, root):
        full = b"complete synthetic native upload"
        upload = tmp_path / "upload.tar.gz"
        upload.write_bytes(full)
        prepared = prepare_submission(root, upload,
            history_reader=lambda: dict(run_id=state["run_id"], entries=[]))
        mark_dispatched(root, prepared["operation_id"])
        accepted = submit_fixture(observer, full)
        source = observer.trial / "submissions" / accepted["round_id"] / "submission.tar.gz"
        source.write_bytes(full[:8])
        monkeypatch.setattr(submission_receipts, "read_archive", observed_read)
        for status in ("queued", "running"):
            state["entries"][0]["status"] = status
            [pending] = observer.update(LocalCopyBackend(), None)["records"]
            assert pending["accepted_record"] and pending["reason"] == "native_archive_pending"
            assert "archive_sha256" not in pending
        assert reads == [] and not (observer.directory / "bindings").exists()
        with pytest.raises(ValueError, match="native_acceptance_unknown_no_resubmit"):
            prepare_submission(root, upload)
        source.write_bytes(full)
        release(accepted["submission_id"], status=terminal_status)
        [complete] = observer.update(LocalCopyBackend(), None)["records"]
        assert reads == [source]
        assert complete["archive_sha256"] == hashlib.sha256(full).hexdigest()
        recovered = prepare_submission(root, upload)
        assert recovered["action"] == "recover" and recovered["submission_id"] == accepted["submission_id"]
        assert state["posts"] == 1 and state["result_reads"] == 0


def test_started_publisher_delivers_later_receipt_and_closes_its_thread(tmp_path):
    published = threading.Event()

    class ObservedCopyBackend(LocalCopyBackend):
        def exec_run(self, handle, command, **kwargs):
            result = super().exec_run(handle, command, **kwargs)
            if command[0] == "mv" and result.exit_code == 0:
                packet = json.loads((root / "native.json").read_bytes())
                if any(row.get("archive_sha256") for row in packet["records"]):
                    published.set()
            return result

    with native_http(tmp_path, immediately_complete=True) as (state, _release, observer, root):
        observer.poll_seconds = 0.01
        observer.start(ObservedCopyBackend(), None)
        accepted = submit_fixture(observer, b"later accepted upload")
        assert published.wait(5), "the background publisher must deliver without a manual update"
        observer.close()
        assert not observer.thread.is_alive()
        [receipt] = json.loads((root / "native.json").read_bytes())["records"]
        assert receipt["submission_id"] == accepted["submission_id"]
        assert receipt["archive_sha256"] == hashlib.sha256(b"later accepted upload").hexdigest()
        assert state["posts"] == 1 and state["result_reads"] == 0


def test_failed_remote_rename_allows_retry_of_the_same_receipt_revision(tmp_path):
    class FailFirstRename(LocalCopyBackend):
        renames = 0

        def exec_run(self, handle, command, **kwargs):
            if command[0] == "mv":
                self.renames += 1
                if self.renames == 1:
                    return SimpleNamespace(exit_code=1)
            return super().exec_run(handle, command, **kwargs)

    with native_http(tmp_path, immediately_complete=True) as (state, _release, observer, root):
        submit_fixture(observer, b"accepted upload")
        before = (root / "native.json").read_bytes()
        backend = FailFirstRename()
        with pytest.raises(RuntimeError, match="native_submission_publish_failed"):
            observer.update(backend, None)
        assert (root / "native.json").read_bytes() == before
        retried = observer.update(backend, None)
        assert backend.renames == 2
        assert json.loads((root / "native.json").read_bytes()) == retried
        assert retried["records"][0]["archive_sha256"] == hashlib.sha256(b"accepted upload").hexdigest()
        assert state["posts"] == 1 and state["result_reads"] == 0


@pytest.mark.parametrize("scope", ["run", "task", "identity", "malformed-entry"])
def test_unproven_native_history_is_not_published_as_a_valid_empty_packet(tmp_path, scope):
    with native_http(tmp_path) as (state, _release, observer, root):
        submit_fixture(observer, b"accepted upload")
        before = (root / "native.json").read_bytes()
        if scope == "run":
            state["run_id"] = "other-run"
        elif scope == "task":
            state["entries"][0]["task_id"] = "other-task"
        elif scope == "identity":
            state["entries"][0]["round"] = None
        else:
            state["entries"].append(None)
        with pytest.raises(ValueError, match="native_submission"):
            observer.update(LocalCopyBackend(), None)
        assert (root / "native.json").read_bytes() == before


@pytest.mark.parametrize("invalid", ["not-record", "truthy-acceptance", "partial-digest", "boolean-size",
                                    "unsafe-id", "ambiguous-id", "ambiguous-round", "unowned-result"])
def test_packet_validation_never_promotes_malformed_receipts_to_matches(invalid):
    record = dict(accepted_record=True, submission_id="submission-1", round_id="agent-1",
                  archive_sha256="a" * 64, archive_bytes=1)
    packet = dict(schema_version=SCHEMA, run_id="synthetic-run", task_id="synthetic-task", records=[record])
    if invalid == "not-record":
        packet["records"] = [None]
    elif invalid == "truthy-acceptance":
        record["accepted_record"] = "true"
    elif invalid == "partial-digest":
        del record["archive_bytes"]
    elif invalid == "boolean-size":
        record["archive_bytes"] = True
    elif invalid == "unsafe-id":
        record["submission_id"] = "../submission-1"
    elif invalid in {"ambiguous-id", "ambiguous-round"}:
        second = deepcopy(record)
        second["round_id" if invalid == "ambiguous-id" else "submission_id"] = (
            "agent-2" if invalid == "ambiguous-id" else "submission-2")
        packet["records"].append(second)
    else:
        record["result_ref"] = "unowned-feedback"
    with pytest.raises(ValueError, match="native_submission"):
        validate_packet(packet)
