from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from itertools import count
from pathlib import Path
from threading import Barrier, Event, Lock
from time import sleep

import pytest

import loopx.cli_runtime as cli_runtime_module
import loopx.status as status_module
from loopx.control_plane import effect_runtime


def _install_fingerprint_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    fingerprints: list[str] = []
    counter_lock = Lock()

    def fingerprint() -> str:
        with counter_lock:
            value = f"revision-{len(fingerprints) + 1}"
            fingerprints.append(value)
            return value

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", fingerprint)
    return fingerprints


def test_scope_reuses_one_revision_and_nested_scopes_join_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprints = _install_fingerprint_counter(monkeypatch)

    assert effect_runtime._runtime_fingerprint_for_request() == "revision-1"
    assert effect_runtime._runtime_fingerprint_for_request() == "revision-2"
    with effect_runtime.effect_runtime_request_scope():
        assert effect_runtime._runtime_fingerprint_for_request() == "revision-3"
        with effect_runtime.effect_runtime_request_scope():
            assert effect_runtime._runtime_fingerprint_for_request() == "revision-3"
        assert effect_runtime._runtime_fingerprint_for_request() == "revision-3"
    assert effect_runtime._runtime_fingerprint_for_request() == "revision-4"

    assert fingerprints == [
        "revision-1",
        "revision-2",
        "revision-3",
        "revision-4",
    ]


def test_scope_retries_resolution_after_a_fingerprint_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = count(1)

    def fingerprint() -> str:
        attempt = next(attempts)
        if attempt == 1:
            raise FileNotFoundError("source changed during the scan")
        return "stable-revision"

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", fingerprint)

    with effect_runtime.effect_runtime_request_scope():
        with pytest.raises(FileNotFoundError):
            effect_runtime._runtime_fingerprint_for_request()
        assert (
            effect_runtime._runtime_fingerprint_for_request()
            == "stable-revision"
        )

    assert next(attempts) == 3


def test_copied_context_cannot_reuse_a_closed_request_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprints = _install_fingerprint_counter(monkeypatch)

    with effect_runtime.effect_runtime_request_scope():
        assert effect_runtime._runtime_fingerprint_for_request() == "revision-1"
        inherited = copy_context()

    assert inherited.run(
        effect_runtime._runtime_fingerprint_for_request
    ) == "revision-2"
    assert inherited.run(
        effect_runtime._runtime_fingerprint_for_request
    ) == "revision-3"
    assert fingerprints == ["revision-1", "revision-2", "revision-3"]


def test_source_changes_become_visible_on_the_next_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "runtime.ts"
    source.write_text("export const revision = 1;\n", encoding="utf-8")
    monkeypatch.setattr(
        effect_runtime,
        "_control_plane_root",
        lambda: tmp_path,
    )

    with effect_runtime.effect_runtime_request_scope():
        first = effect_runtime._runtime_fingerprint_for_request()
        source.write_text("export const revision = 2;\n", encoding="utf-8")
        assert effect_runtime._runtime_fingerprint_for_request() == first

    with effect_runtime.effect_runtime_request_scope():
        assert effect_runtime._runtime_fingerprint_for_request() != first


def test_concurrent_requests_resolve_independent_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprints = _install_fingerprint_counter(monkeypatch)
    ready = Barrier(2)

    def resolve_request() -> tuple[str, str]:
        with effect_runtime.effect_runtime_request_scope():
            ready.wait()
            first = effect_runtime._runtime_fingerprint_for_request()
            second = effect_runtime._runtime_fingerprint_for_request()
            return first, second

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: resolve_request(), range(2)))

    assert all(first == second for first, second in results)
    assert len({first for first, _second in results}) == 2
    assert fingerprints == ["revision-1", "revision-2"]


def test_one_request_serializes_concurrent_first_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    calls_lock = Lock()

    def fingerprint() -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.01)
        return "shared-revision"

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", fingerprint)
    state = effect_runtime._RequestRuntimeRevision()

    with ThreadPoolExecutor(max_workers=4) as executor:
        revisions = list(executor.map(lambda _index: state.resolve(), range(8)))

    assert {revision.fingerprint for revision in revisions} == {
        "shared-revision"
    }
    assert calls == 1


def test_copied_contexts_share_one_concurrent_first_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    calls_lock = Lock()
    ready = Barrier(2)

    def fingerprint() -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.01)
        return "shared-revision"

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", fingerprint)

    with effect_runtime.effect_runtime_request_scope():
        inherited = [copy_context(), copy_context()]

        def resolve(context_index: int) -> str:
            ready.wait()
            return inherited[context_index].run(
                effect_runtime._runtime_fingerprint_for_request
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            revisions = list(executor.map(resolve, range(2)))

    assert revisions == ["shared-revision", "shared-revision"]
    assert calls == 1


def test_joined_copied_context_keeps_revision_after_parent_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprints = _install_fingerprint_counter(monkeypatch)
    joined = Event()
    parent_exited = Event()

    def resolve_after_parent_exit() -> tuple[str, str]:
        with effect_runtime.effect_runtime_request_scope():
            joined.set()
            assert parent_exited.wait(timeout=2)
            return (
                effect_runtime._runtime_fingerprint_for_request(),
                effect_runtime._runtime_fingerprint_for_request(),
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with effect_runtime.effect_runtime_request_scope():
            inherited = copy_context()
            future = executor.submit(inherited.run, resolve_after_parent_exit)
            assert joined.wait(timeout=2)
        parent_exited.set()
        first, second = future.result(timeout=2)

    assert first == second == "revision-1"
    assert fingerprints == ["revision-1"]


def test_common_command_dispatch_defines_one_effect_runtime_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprints = _install_fingerprint_counter(monkeypatch)
    observed: list[str] = []

    def run_command(
        _args: object,
        *,
        registry_path: Path,
        allow_missing_registry: bool,
    ) -> int:
        assert registry_path == Path("registry.json")
        assert allow_missing_registry is False
        observed.append(effect_runtime._runtime_fingerprint_for_request())
        observed.append(effect_runtime._runtime_fingerprint_for_request())
        return 17

    monkeypatch.setattr(
        cli_runtime_module,
        "_dispatch_common_command",
        run_command,
    )

    assert cli_runtime_module.dispatch_common_command(
        object(),
        registry_path=Path("registry.json"),
        allow_missing_registry=False,
    ) == 17
    assert observed == ["revision-1", "revision-1"]
    assert fingerprints == ["revision-1"]


def test_long_running_full_cli_does_not_pin_a_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprints = _install_fingerprint_counter(monkeypatch)

    def run_command(_argv: list[str]) -> int:
        effect_runtime._runtime_fingerprint_for_request()
        effect_runtime._runtime_fingerprint_for_request()
        return 0

    monkeypatch.setattr(cli_runtime_module, "_run_full_cli", run_command)

    assert cli_runtime_module.main(["serve-status"]) == 0
    assert fingerprints == ["revision-1", "revision-2"]


def test_collect_status_defines_one_programmatic_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fingerprints = _install_fingerprint_counter(monkeypatch)

    def collect(**_kwargs: object) -> dict[str, object]:
        return {
            "first": effect_runtime._runtime_fingerprint_for_request(),
            "second": effect_runtime._runtime_fingerprint_for_request(),
        }

    monkeypatch.setattr(status_module, "_collect_status_read_model", collect)

    payload = status_module.collect_status(
        registry_path=Path("registry.json"),
        runtime_root_override=None,
        limit=1,
    )

    assert payload == {"first": "revision-1", "second": "revision-1"}
    assert fingerprints == ["revision-1"]
