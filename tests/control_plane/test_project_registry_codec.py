from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from loopx.control_plane.projects import registry_codec
from loopx.control_plane.projects.registry_codec import (
    ProjectRegistryError,
    ProjectRegistryMutationError,
    ProjectRegistryProtocolError,
    decode_project_registry,
    load_project_registry,
    mutate_project_registry,
    source_session_registry_transaction,
)
from loopx.global_registry import (
    GlobalRegistryReduction,
    mutate_global_registry,
    sync_project_registry_to_global,
)
from loopx.history import load_registry


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _strict(
    payload: dict[str, object],
    *,
    protocol: str = "goal_instance_v1",
) -> list[object]:
    return [
        {
            "schema_version": "loopx_project_registry_envelope_v1",
            "minimum_writer_protocol": protocol,
            "payload_sha256": _digest(payload),
        },
        payload,
    ]


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_legacy_object_decode_keeps_permissive_json_behavior(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text('{"value": 1, "value": NaN}\n', encoding="utf-8")

    loaded = load_project_registry(path)

    assert math.isnan(loaded["value"])


@pytest.mark.parametrize(
    "text, match",
    [
        (
            '[{"schema_version":"loopx_project_registry_envelope_v1",'
            '"minimum_writer_protocol":"goal_instance_v1",'
            '"payload_sha256":"sha256:'
            + "0" * 64
            + '"},{"value":1,"value":2}]',
            "duplicate",
        ),
        (
            '[{"schema_version":"loopx_project_registry_envelope_v1",'
            '"minimum_writer_protocol":"goal_instance_v1",'
            '"payload_sha256":"sha256:'
            + "0" * 64
            + '"},{"value":NaN}]',
            "non-finite",
        ),
    ],
)
def test_strict_decode_rejects_noncanonical_json(
    tmp_path: Path,
    text: str,
    match: str,
) -> None:
    path = tmp_path / "registry.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        load_project_registry(path)


def test_strict_decode_validates_canonical_payload_digest(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload: dict[str, object] = {"unicode": "目标", "nested": {"b": 2, "a": 1}}
    envelope = _strict(payload)
    _write(path, envelope)

    assert load_project_registry(path) == payload

    envelope[1] = {"unicode": "changed"}
    _write(path, envelope)
    with pytest.raises(ValueError, match="digest"):
        load_project_registry(path)


def test_dsh_strict_registry_fixture_uses_the_python_wire_contract() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "packages/dsh-loopx-plugin/tests/fixtures/project-registry-strict-v1.json"
    )

    payload = decode_project_registry(fixture.read_bytes())

    assert payload["goals"][0]["state_file"] == (
        ".codex/goals/goal-fixture/ACTIVE_GOAL_STATE.md"
    )
    assert payload["meta"]["fraction"] == 1.0
    assert payload["meta"]["label"] == "目标"


def test_future_protocol_is_readable_but_not_mutable(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload: dict[str, object] = {"schema_version": "0.1", "goals": []}
    _write(path, _strict(payload, protocol="goal_instance_v2"))
    before = path.read_bytes()

    assert load_project_registry(path) == payload

    with pytest.raises(ProjectRegistryProtocolError, match="goal_instance_v2"):
        mutate_project_registry(
            path,
            operation="test_future_protocol",
            reducer=lambda registry: registry.update({"updated": True}),
        )
    assert path.read_bytes() == before


def test_source_session_transaction_creates_and_preserves_v2(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.json"
    initial: dict[str, object] = {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": "source_session_v1",
        "goals": [],
    }

    with source_session_registry_transaction(
        path,
        operation="test_source_session_create",
        create=lambda: initial,
    ) as transaction:
        payload = transaction.payload_copy()
        payload["created"] = True
        assert transaction.commit(payload) is True

    created = json.loads(path.read_text(encoding="utf-8"))
    assert created[0]["schema_version"] == "loopx_project_registry_envelope_v2"
    assert created[0]["minimum_writer_protocol"] == "goal_instance_v2"
    assert created[0]["payload_sha256"] == _digest(created[1])

    with source_session_registry_transaction(
        path,
        operation="test_source_session_update",
    ) as transaction:
        payload = transaction.payload_copy()
        payload["updated"] = True
        assert transaction.commit(payload) is True

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated[0]["schema_version"] == "loopx_project_registry_envelope_v2"
    assert updated[0]["minimum_writer_protocol"] == "goal_instance_v2"
    assert updated[0]["payload_sha256"] == _digest(updated[1])
    assert updated[1] == {**initial, "created": True, "updated": True}

    before = path.read_bytes()
    with pytest.raises(ProjectRegistryProtocolError, match="goal_instance_v2"):
        mutate_project_registry(
            path,
            operation="test_legacy_writer_rejected",
            reducer=lambda registry: registry.update({"legacy_write": True}),
        )
    assert path.read_bytes() == before


def test_source_session_profile_is_not_a_generic_runtime_registry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.json"
    payload: dict[str, object] = {
        "schema_version": "0.2",
        "profile_id": "source_session_v1",
        "goals": [],
    }
    _write(
        path,
        [
            {
                "schema_version": "loopx_project_registry_envelope_v2",
                "minimum_writer_protocol": "goal_instance_v2",
                "payload_sha256": _digest(payload),
            },
            payload,
        ],
    )

    assert load_project_registry(path) == payload
    with pytest.raises(
        ProjectRegistryProtocolError,
        match="lifecycle-only",
    ):
        load_registry(path)


def test_v2_envelope_rejects_an_unknown_profile(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload: dict[str, object] = {
        "schema_version": "0.2",
        "profile_id": "unqualified_profile_v1",
        "goals": [],
    }
    _write(
        path,
        [
            {
                "schema_version": "loopx_project_registry_envelope_v2",
                "minimum_writer_protocol": "goal_instance_v2",
                "payload_sha256": _digest(payload),
            },
            payload,
        ],
    )

    with pytest.raises(ProjectRegistryError, match="profile_id"):
        load_project_registry(path)


def test_global_registry_mutation_remains_object_only(tmp_path: Path) -> None:
    path = tmp_path / "registry.global.json"
    _write(path, _strict({"schema_version": "0.1", "goals": []}))

    with pytest.raises(ValueError, match="JSON object"):
        load_registry(path)

    with pytest.raises(ValueError, match="JSON object"):
        sync_project_registry_to_global(
            registry_path=path,
            runtime_root_override=str(tmp_path),
            dry_run=True,
        )

    with pytest.raises(ValueError, match="JSON object"):
        mutate_global_registry(
            path,
            "test_global_object_only",
            lambda payload: GlobalRegistryReduction(
                payload=payload,
                receipt={},
            ),
        )


def test_strict_mutation_preserves_format_digest_and_mode(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload: dict[str, object] = {"schema_version": "0.1", "goals": []}
    _write(path, _strict(payload))
    path.chmod(0o640)

    result = mutate_project_registry(
        path,
        operation="test_strict_mutation",
        reducer=lambda registry: registry.update({"updated": True}) or "receipt",
    )

    root = json.loads(path.read_text(encoding="utf-8"))
    assert result == "receipt"
    assert root[1] == {**payload, "updated": True}
    assert root[0]["payload_sha256"] == _digest(root[1])
    assert path.stat().st_mode & 0o777 == 0o640


def test_noop_mutation_keeps_exact_bytes_and_mtime(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload: dict[str, object] = {"schema_version": "0.1", "goals": []}
    _write(path, _strict(payload))
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    result = mutate_project_registry(
        path,
        operation="test_noop",
        reducer=lambda registry: "unchanged",
    )

    assert result == "unchanged"
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime


def test_missing_registry_requires_explicit_legacy_initializer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.json"

    with pytest.raises(FileNotFoundError):
        mutate_project_registry(
            path,
            operation="test_missing",
            reducer=lambda registry: None,
        )

    mutate_project_registry(
        path,
        operation="test_create",
        create=lambda: {"schema_version": "0.1", "goals": []},
        reducer=lambda registry: registry.update({"created": True}),
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": "0.1",
        "goals": [],
        "created": True,
    }


def test_failed_write_readback_restores_exact_preimage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "registry.json"
    path.write_bytes(b'{\n  "schema_version": "0.1",\n  "goals": []\n}\n')
    before = path.read_bytes()
    real_read = registry_codec._read_document
    reads = 0

    def fail_first_readback(candidate: Path) -> registry_codec._ProjectRegistryDocument:
        nonlocal reads
        reads += 1
        if reads == 2:
            raise OSError("injected readback failure")
        return real_read(candidate)

    monkeypatch.setattr(registry_codec, "_read_document", fail_first_readback)

    with pytest.raises(ProjectRegistryMutationError, match="readback"):
        mutate_project_registry(
            path,
            operation="test_readback_restore",
            reducer=lambda registry: registry.update({"changed": True}),
        )

    assert path.read_bytes() == before
