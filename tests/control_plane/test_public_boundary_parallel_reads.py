from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

from loopx import contract
from loopx.control_plane.runtime.file_text_reads import iter_utf8_file_reads


def test_reads_overlap_but_results_remain_in_input_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"{index}.md" for index in range(4)]
    for index, path in enumerate(paths):
        path.write_text(str(index), encoding="utf-8")
    original = Path.read_text
    release_first, other_completed = Event(), Event()
    calls: list[Path] = []
    lock = Lock()

    def read(path: Path, *args, **kwargs) -> str:
        with lock:
            calls.append(path)
        if path == paths[0]:
            assert release_first.wait(5), "first read was not released"
        result = original(path, *args, **kwargs)
        if path == paths[1]:
            other_completed.set()
        return result

    monkeypatch.setattr(Path, "read_text", read)
    with ThreadPoolExecutor(max_workers=1) as consumer:
        result = consumer.submit(lambda: list(iter_utf8_file_reads(paths, max_workers=2)))
        try:
            assert other_completed.wait(5), "I/O is still serial"
            assert not result.done()
        finally:
            release_first.set()
        reads = result.result(timeout=5)
    assert [item.path for item in reads] == paths
    assert [item.text for item in reads] == ["0", "1", "2", "3"]
    assert all(item.error is None for item in reads)
    assert sorted(calls) == paths  # each path opened exactly once


@pytest.mark.parametrize("workers", [1, 3, 8])
def test_input_consumption_and_pending_results_are_bounded(tmp_path: Path, workers: int) -> None:
    paths = [tmp_path / f"{index:02}.md" for index in range(20)]
    for path in paths:
        path.write_text("public", encoding="utf-8")
    submitted: list[Path] = []

    def inputs():
        for path in paths:
            submitted.append(path)
            yield path

    reads = iter_utf8_file_reads(inputs(), max_workers=workers)
    try:
        assert next(reads).path == paths[0]
        assert submitted == paths[:workers]
        assert next(reads).path == paths[1]
        assert submitted == paths[: workers + 1]
    finally:
        reads.close()
    assert submitted == paths[: workers + 1]


@pytest.mark.parametrize("workers", [1, 8])
def test_read_failures_are_ordered_observations_not_cached_success(
    tmp_path: Path, workers: int
) -> None:
    valid = tmp_path / "valid.md"
    invalid = tmp_path / "invalid.md"
    missing = tmp_path / "missing.md"
    valid.write_text("before", encoding="utf-8")
    invalid.write_bytes(b"\xff")
    paths = [valid, missing, invalid]
    first = list(iter_utf8_file_reads(paths, max_workers=workers))
    assert [item.path for item in first] == paths
    assert first[0].text == "before"
    assert isinstance(first[1].error, FileNotFoundError)
    assert isinstance(first[2].error, UnicodeDecodeError)
    assert first[1].text is None and first[2].text is None
    valid.write_text("after", encoding="utf-8")
    missing.write_text("new", encoding="utf-8")
    invalid.write_text("repaired", encoding="utf-8")
    second = list(iter_utf8_file_reads(paths, max_workers=workers))
    assert [item.text for item in second] == ["after", "new", "repaired"]
    assert all(item.error is None for item in second)


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_worker_limit_cannot_read_inputs(tmp_path: Path, workers) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        list(iter_utf8_file_reads([tmp_path / "absent.md"], max_workers=workers))


def test_unexpected_reader_failure_is_not_downgraded_to_scan_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_read(*args, **kwargs):
        raise RuntimeError("synthetic unexpected reader failure")

    monkeypatch.setattr(Path, "read_text", broken_read)
    with pytest.raises(RuntimeError, match="unexpected reader failure"):
        list(iter_utf8_file_reads([tmp_path / "a.md", tmp_path / "b.md"]))


def test_local_private_state_is_filtered_before_any_worker_opens_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public.md"
    private = tmp_path / ".codex" / "goals" / "fixture" / "ACTIVE_GOAL_STATE.md"
    private.parent.mkdir(parents=True)
    public.write_text("public", encoding="utf-8")
    private.write_text("not a public scan input", encoding="utf-8")
    original = Path.read_text
    opened: list[Path] = []

    def read(path: Path, *args, **kwargs) -> str:
        assert path != private, "private contents reached the I/O worker"
        opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(contract, "_git_probe", lambda path: {
        "tracked": False, "ignored": True, "inside_worktree": True,
    })
    payload = contract.scan_public_boundary([tmp_path])
    assert payload["ok"] is True
    assert payload["scanned_files"] == 1
    assert payload["skipped_private_state_files"] == [str(private.relative_to(tmp_path))]
    assert opened == [public]


def test_full_scan_projection_matches_serial_reads_and_observes_later_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "a.md"
    source.write_text("\n".join([
        "https://tenant.lark" + "office.com/wiki/example",
        "tok" + "en=synthetic-value",
        "/" + "Users/alice/Documents/example.md",
        "ticket t-" + "20260828123456-example",
        "host 10" + ".1.2.3",
        "Author\u0131zation: synthetic-value",
        "tok" + "en=${EXAMPLE_KEY}",
    ]), encoding="utf-8")
    binary = tmp_path / "b.md"
    binary.write_bytes(b"\xff")
    (tmp_path / "package-lock.json").write_text(
        '{"packages":{"synthetic":{"resolved":"https://example.org/package.tgz"}}}',
        encoding="utf-8",
    )
    concurrent = contract.scan_public_boundary([tmp_path, source])
    original = contract.iter_utf8_file_reads
    monkeypatch.setattr(contract, "iter_utf8_file_reads", lambda paths: original(paths, max_workers=1))
    serial = contract.scan_public_boundary([tmp_path, source])
    assert concurrent == serial
    assert concurrent["files"] == 3
    assert concurrent["hits"] == [
        "a.md:1: private_doc_url", "a.md:2: credential", "a.md:3: local_private_path",
        "a.md:4: internal_task_id", "a.md:5: private_ip", "a.md:6: credential",
        "package-lock.json: non_public_package_registry (synthetic)",
    ]
    assert concurrent["credential_reference_hits"] == ["a.md:7: credential"]
    monkeypatch.setattr(contract, "iter_utf8_file_reads", original)
    source.write_text("public now", encoding="utf-8")
    (tmp_path / "package-lock.json").unlink()
    (tmp_path / "new.md").write_text("host 10" + ".4.5.6", encoding="utf-8")
    fresh = contract.scan_public_boundary([tmp_path])
    assert fresh["hits"] == ["new.md:1: private_ip"]
    assert fresh["credential_reference_hits"] == []
    assert fresh["files"] == 3


def test_tracked_private_policy_and_warning_order_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / ".codex" / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir()
    state.write_text("https://tenant.lark" + "office.com/wiki/example", encoding="utf-8")
    untracked = tmp_path / ".codex" / "untracked.md"
    untracked.write_text("not public", encoding="utf-8")
    (tmp_path / "public.md").write_text("public", encoding="utf-8")
    monkeypatch.setattr(contract, "_git_probe", lambda path: {
        "tracked": path == state, "ignored": False, "inside_worktree": True,
    })
    payload = contract.scan_public_boundary([tmp_path], registry={
        "public_boundary": {"tracked_private_doc_urls": "allow"},
    })
    assert payload["ok"] is True
    assert payload["allowed_hits"] == [".codex/ACTIVE_GOAL_STATE.md:1: private_doc_url"]
    assert payload["skipped_private_state_files"] == [".codex/untracked.md"]
    assert payload["private_state_git_warnings"] == [
        ".codex/untracked.md: private state should be gitignored",
    ]
