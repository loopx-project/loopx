"""The operator provider credential: one secret file, one redacted readback."""

from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    read_machine_configuration,
)
from loopx.control_plane.operator_provider import (
    PROVIDER_KEY_FIELD,
    BASE_URL_FIELD,
    OPERATOR_PROVIDER_STORE_REF,
    SOURCE_MACHINE_STORE,
    SOURCE_SERVICE_ENVIRONMENT,
    SOURCE_UNSET,
    STATUS_ABSENT,
    STATUS_CONFIGURED,
    STATUS_INVALID,
    operator_provider_environ,
    operator_provider_projection,
    operator_provider_store_path,
    write_operator_provider,
)
from loopx.control_plane import operator_provider as operator_provider_module
from loopx.control_plane.turn_driver.host_binding import selected_turn_host

KEY = "sk-operator-provider-fixture-key"
OTHER_KEY = "sk-operator-provider-fixture-key-2"
BASE_URL = "https://endpoint.example.invalid/v1"
ENV = {
    "DEEPSEEK_API_KEY": "sk-service-environment-key",
    "DEEPSEEK_BASE_URL": "https://env.example.invalid/v1",
}


def test_a_stored_credential_resolves_ahead_of_the_service_environment(tmp_path):
    write_operator_provider(
        runtime_root=tmp_path, api_key=KEY, base_url=BASE_URL
    )

    resolved = operator_provider_environ(tmp_path, environ=ENV)

    assert resolved["DEEPSEEK_API_KEY"] == KEY
    assert resolved["DEEPSEEK_BASE_URL"] == BASE_URL
    # The caller's mapping is an input, not a scratchpad.
    assert ENV["DEEPSEEK_API_KEY"] == "sk-service-environment-key"


def test_a_partly_stored_credential_keeps_the_environment_for_the_other_field(
    tmp_path,
):
    write_operator_provider(runtime_root=tmp_path, base_url=BASE_URL)

    resolved = operator_provider_environ(tmp_path, environ=ENV)
    projection = operator_provider_projection(tmp_path, environ=ENV)

    assert resolved["DEEPSEEK_API_KEY"] == "sk-service-environment-key"
    assert resolved["DEEPSEEK_BASE_URL"] == BASE_URL
    assert projection[PROVIDER_KEY_FIELD]["source"] == SOURCE_SERVICE_ENVIRONMENT
    assert projection[PROVIDER_KEY_FIELD]["env_var"] == "DEEPSEEK_API_KEY"
    assert projection[BASE_URL_FIELD]["source"] == SOURCE_MACHINE_STORE


def test_an_unconfigured_machine_reports_absent_without_inventing_a_source(
    tmp_path,
):
    projection = operator_provider_projection(tmp_path, environ={})

    assert projection["status"] == STATUS_ABSENT
    assert projection["record_present"] is False
    assert projection[PROVIDER_KEY_FIELD]["source"] == SOURCE_UNSET
    assert projection[BASE_URL_FIELD]["source"] == SOURCE_UNSET


def test_the_store_is_readable_only_by_its_owner(tmp_path):
    write_operator_provider(runtime_root=tmp_path, api_key=KEY)

    path = operator_provider_store_path(tmp_path)
    directory_mode = stat.S_IMODE(path.parent.stat().st_mode)
    file_mode = stat.S_IMODE(path.stat().st_mode)

    assert path.relative_to(tmp_path).as_posix() == OPERATOR_PROVIDER_STORE_REF
    assert directory_mode == 0o700
    assert file_mode == 0o600


def test_no_readback_returns_the_stored_key(tmp_path):
    """The key is write-only: every surface reads the fingerprint instead."""

    write_operator_provider(runtime_root=tmp_path, api_key=KEY)

    projection = operator_provider_projection(tmp_path, environ={})
    serialized = json.dumps(projection)

    assert KEY not in serialized
    assert projection[PROVIDER_KEY_FIELD]["configured"] is True
    assert projection[PROVIDER_KEY_FIELD]["fingerprint"]
    assert projection[BASE_URL_FIELD]["value"] is None


def test_two_keys_are_distinguishable_without_either_being_readable(tmp_path):
    first = write_operator_provider(runtime_root=tmp_path, api_key=KEY)
    second = write_operator_provider(runtime_root=tmp_path, api_key=OTHER_KEY)

    assert (
        first[PROVIDER_KEY_FIELD]["fingerprint"]
        != second[PROVIDER_KEY_FIELD]["fingerprint"]
    )
    assert first["store_revision"] != second["store_revision"]
    assert KEY not in json.dumps(second)
    assert OTHER_KEY not in json.dumps(second)


def test_an_update_merges_one_field_without_clearing_the_other(tmp_path):
    write_operator_provider(runtime_root=tmp_path, api_key=KEY, base_url=BASE_URL)

    updated = write_operator_provider(runtime_root=tmp_path, api_key=OTHER_KEY)

    assert updated["status"] == STATUS_CONFIGURED
    assert updated[BASE_URL_FIELD]["value"] == BASE_URL
    assert updated[PROVIDER_KEY_FIELD]["configured"] is True

    cleared = write_operator_provider(
        runtime_root=tmp_path, clear_api_key=True, environ={}
    )

    assert cleared[PROVIDER_KEY_FIELD]["configured"] is False
    assert cleared[BASE_URL_FIELD]["value"] == BASE_URL


def test_concurrent_partial_updates_preserve_both_credential_fields(
    tmp_path, monkeypatch
):
    write_operator_provider(
        runtime_root=tmp_path,
        api_key=KEY,
        base_url=BASE_URL,
        environ={},
    )
    real_read = operator_provider_module.read_operator_provider
    arrivals_lock = Lock()
    release_reads = Event()
    arrivals = 0

    def synchronized_read(runtime_root=None):
        nonlocal arrivals
        state = real_read(runtime_root)
        with arrivals_lock:
            arrivals += 1
            if arrivals == 2:
                release_reads.set()
        # On the fixed path, the first writer waits here while holding the
        # file lock; the second then reads the first writer's saved state.
        # Without the lock, both callers load the same old record.
        release_reads.wait(timeout=0.5)
        return state

    monkeypatch.setattr(
        operator_provider_module, "read_operator_provider", synchronized_read
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        key_update = executor.submit(
            write_operator_provider,
            runtime_root=tmp_path,
            api_key=OTHER_KEY,
            environ={},
        )
        endpoint_update = executor.submit(
            write_operator_provider,
            runtime_root=tmp_path,
            base_url="https://updated.example.invalid/v1",
            environ={},
        )
        assert key_update.result()[PROVIDER_KEY_FIELD]["configured"] is True
        assert endpoint_update.result()[BASE_URL_FIELD]["value"] == (
            "https://updated.example.invalid/v1"
        )

    final = real_read(tmp_path)
    assert final[PROVIDER_KEY_FIELD] == OTHER_KEY
    assert final[BASE_URL_FIELD] == "https://updated.example.invalid/v1"


def test_clearing_the_last_field_removes_the_record(tmp_path):
    write_operator_provider(runtime_root=tmp_path, api_key=KEY)

    projection = write_operator_provider(
        runtime_root=tmp_path, clear_api_key=True, environ={}
    )

    assert projection["status"] == STATUS_ABSENT
    assert projection["record_present"] is False
    assert not operator_provider_store_path(tmp_path).exists()


def test_an_invalid_record_refuses_instead_of_authenticating_from_the_environment(
    tmp_path,
):
    """A credential the product surface cannot read must not run anything."""

    path = operator_provider_store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": "operator_provider_credential_v0", "unknown": 1}
        ),
        encoding="utf-8",
    )

    resolved = operator_provider_environ(tmp_path, environ=ENV)
    projection = operator_provider_projection(tmp_path, environ=ENV)

    assert "DEEPSEEK_API_KEY" not in resolved
    assert "DEEPSEEK_BASE_URL" not in resolved
    assert projection["status"] == STATUS_INVALID
    assert projection[PROVIDER_KEY_FIELD]["configured"] is False
    assert projection[PROVIDER_KEY_FIELD]["blocked_by"] == STATUS_INVALID
    assert projection["repair"]


def test_a_base_url_must_be_an_endpoint(tmp_path):
    with pytest.raises(ValueError):
        write_operator_provider(
            runtime_root=tmp_path, base_url="endpoint.example.invalid"
        )

    with pytest.raises(ValueError):
        write_operator_provider(runtime_root=tmp_path, api_key="has whitespace")


def test_the_machine_configuration_document_never_carries_the_credential(tmp_path):
    """The secret stays out of the document that is projected and backed up."""

    write_operator_provider(runtime_root=tmp_path, api_key=KEY, base_url=BASE_URL)
    document = tmp_path / "machine" / "configuration.json"

    assert not document.exists()
    assert KEY not in json.dumps(
        operator_provider_projection(tmp_path, environ={})
    )
    assert KEY not in json.dumps(
        read_machine_configuration(
            tmp_path, registry=build_builtin_machine_configuration_registry()
        )
        or {}
    )


def test_a_stored_credential_selects_the_managed_default_turn_host(tmp_path):
    """The stored key reaches the existing resolution instead of a second one."""

    without = selected_turn_host(operator_provider_environ(tmp_path, environ={}))
    write_operator_provider(runtime_root=tmp_path, api_key=KEY)
    with_stored = selected_turn_host(
        operator_provider_environ(tmp_path, environ={})
    )

    assert without == ("codex-cli", "no_operator_credential")
    assert with_stored == ("dsh", "operator_credential")
