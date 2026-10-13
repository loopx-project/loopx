from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.catalog import build_capability_detail_packet
from loopx.capabilities.decision_context import (
    build_decision_source_provider,
    normalize_decision_context_profile,
    resolve_decision_context_activation,
)
from loopx.cli import main

OBSERVED_AT = "2026-07-25T18:30:00+00:00"
PRIVATE_CONTENT = "private authority content"


def profile_payload(
    authority_path: Path,
    *,
    enabled: bool = True,
    agent_id: str = "example-agent",
    adapter: str = "local-file",
) -> dict[str, Any]:
    return {
        "schema_version": "decision_context_profile_v0",
        "goal_id": "example-decision-goal",
        "enabled": enabled,
        "enabled_agents": [agent_id],
        "source_provider_bindings": [
            {
                "provider_id": "local-authority",
                "adapter": adapter,
                "config": {"max_bytes": 4096},
            }
        ],
        "sources": [
            {
                "source_id": "source:authority:baseline",
                "provider_id": "local-authority",
                "source_kind": "artifact",
                "priority": "p0",
                "evidence_level": "s3",
                "objectives": ["delivery-quality", "product-adoption"],
                "scan_mode": "incremental",
                "exact_read_policy": "material_change",
                "freshness_seconds": 3 * 24 * 60 * 60,
                "private_locator": str(authority_path),
                "max_changes": 8,
                "enabled": True,
            }
        ],
        "context_provider": None,
        "automation": {
            "automatic_capture": False,
            "fail_open": True,
        },
    }


def write_profile(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_profile_is_default_off_without_private_file() -> None:
    status, profile = resolve_decision_context_activation(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=None,
    )

    assert profile is None
    assert status["status"] == "disabled"
    assert status["default_enabled"] is False
    assert status["available"] is False
    assert status["external_writes_performed"] is False


def test_available_status_and_manifest_do_not_leak_private_profile(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    authority = tmp_path / "private-authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile_path = write_profile(
        tmp_path / "private-profile.json",
        profile_payload(authority),
    )

    assert (
        main(
            [
                "--format",
                "json",
                "decision-context",
                "source-manifest",
                "--goal-id",
                "example-decision-goal",
                "--agent-id",
                "example-agent",
                "--profile",
                str(profile_path),
                "--observed-at",
                OBSERVED_AT,
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "available"
    assert payload["source_manifest"]["source_count"] == 1
    assert payload["source_manifest"]["observed_at"] == OBSERVED_AT
    for private_value in (
        str(authority),
        str(profile_path),
        PRIVATE_CONTENT,
        "max_bytes",
    ):
        assert private_value not in serialized
    assert payload["private_locator_captured"] is False
    assert payload["raw_content_captured"] is False
    assert payload["credentials_captured"] is False


@pytest.mark.parametrize(
    ("goal_id", "agent_id", "adapter", "expected_status"),
    [
        (
            "another-goal",
            "example-agent",
            "local-file",
            "profile_invalid",
        ),
        (
            "example-decision-goal",
            "another-agent",
            "local-file",
            "agent_not_enabled",
        ),
        (
            "example-decision-goal",
            "example-agent",
            "missing-adapter",
            "provider_unavailable",
        ),
    ],
)
def test_scope_and_provider_routes_fail_closed_without_private_leaks(
    tmp_path: Path,
    goal_id: str,
    agent_id: str,
    adapter: str,
    expected_status: str,
) -> None:
    authority = tmp_path / "private-authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile_path = write_profile(
        tmp_path / "private-profile.json",
        profile_payload(authority, adapter=adapter),
    )

    status, _profile = resolve_decision_context_activation(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
    )
    serialized = json.dumps(status, sort_keys=True)

    assert status["status"] == expected_status
    assert status["available"] is False
    assert str(authority) not in serialized
    assert str(profile_path) not in serialized
    assert PRIVATE_CONTENT not in serialized
    if adapter == "missing-adapter":
        assert adapter not in serialized
        assert status["unavailable_adapter_count"] == 1


def test_private_host_provider_can_satisfy_activation_without_adapter_registration(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "private-authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile_path = write_profile(
        tmp_path / "private-profile.json",
        profile_payload(authority, adapter="private-host-adapter"),
    )

    status, _profile = resolve_decision_context_activation(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile_path,
        available_source_provider_ids={"local-authority"},
    )
    serialized = json.dumps(status, sort_keys=True)

    assert status["status"] == "available"
    assert status["available"] is True
    assert status["runtime_bound_provider_count"] == 1
    assert status["source_providers"] == [
        {
            "adapter": "runtime-bound",
            "adapter_registered": False,
            "runtime_bound": True,
            "private_config_captured": False,
        }
    ]
    assert "private-host-adapter" not in serialized
    assert str(authority) not in serialized


def test_runtime_provider_binding_must_be_declared_by_private_profile(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "private-authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile_path = write_profile(
        tmp_path / "private-profile.json",
        profile_payload(authority),
    )

    status, _profile = resolve_decision_context_activation(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile_path,
        available_source_provider_ids={"another-provider"},
    )

    assert status["status"] == "profile_invalid"
    assert status["reason_code"] == "runtime_provider_scope_mismatch"
    assert status["available"] is False


@pytest.mark.parametrize(
    ("field_name", "value", "expected_message"),
    [
        (
            "automatic_capture",
            True,
            "automatic capture requires an explicit source_ids allowlist",
        ),
        ("fail_open", False, "providers must fail open"),
    ],
)
def test_profile_rejects_unscoped_capture_and_fail_closed_providers(
    tmp_path: Path,
    field_name: str,
    value: bool,
    expected_message: str,
) -> None:
    payload = profile_payload(tmp_path / "authority.md")
    payload["automation"][field_name] = value

    with pytest.raises(ValueError, match=expected_message):
        normalize_decision_context_profile(payload)


def test_enabled_profile_may_configure_only_an_ephemeral_context_provider(
    tmp_path: Path,
) -> None:
    payload = profile_payload(tmp_path / "authority.md")
    payload["source_provider_bindings"] = []
    payload["sources"] = []
    payload["context_provider"] = {
        "provider": "extension",
        "namespace": "peer-session",
        "max_results": 4,
        "timeout_seconds": 10,
        "config": {"extension_id": "loopx-obelisk"},
    }

    profile = normalize_decision_context_profile(payload)

    assert profile.sources == ()
    assert profile.provider_bindings == ()
    assert profile.context_provider is not None
    assert profile.context_provider["scope_ref"] is None


def test_enabled_profile_still_requires_a_source_or_context_provider(
    tmp_path: Path,
) -> None:
    payload = profile_payload(tmp_path / "authority.md")
    payload["source_provider_bindings"] = []
    payload["sources"] = []

    with pytest.raises(ValueError, match="source or context provider"):
        normalize_decision_context_profile(payload)


def test_local_file_provider_supports_bounded_scan_exact_read_and_cursor(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = normalize_decision_context_profile(profile_payload(authority))
    provider = build_decision_source_provider(
        profile.provider_binding_map()["local-authority"]
    )
    source = profile.sources[0]

    first = provider.scan(
        source=source,
        after_cursor=None,
        before="2100-01-01T00:00:00+00:00",
        limit=8,
        timeout_seconds=2,
        observed_at=OBSERVED_AT,
    )
    exact_read = provider.exact_read(
        source=source,
        item=first.items[0],
        timeout_seconds=2,
        observed_at=OBSERVED_AT,
    )
    receipt = first.public_receipt(exact_reads=[exact_read])
    second = provider.scan(
        source=source,
        after_cursor=first.cursor_after,
        before="2100-01-01T00:00:00+00:00",
        limit=8,
        timeout_seconds=2,
        observed_at=OBSERVED_AT,
    )
    serialized = json.dumps(receipt, sort_keys=True)

    assert first.status == "completed"
    assert exact_read.content == PRIVATE_CONTENT
    assert receipt["changed_count"] == 1
    assert receipt["exact_read_count"] == 1
    assert second.status == "no_change"
    assert str(authority) not in serialized
    assert PRIVATE_CONTENT not in serialized


def test_local_file_provider_rejects_oversized_authority(tmp_path: Path) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    payload = profile_payload(authority)
    bindings = payload["source_provider_bindings"]
    assert isinstance(bindings, list)
    binding = bindings[0]
    assert isinstance(binding, dict)
    private_config = binding["config"]
    assert isinstance(private_config, dict)
    private_config["max_bytes"] = 2
    profile = normalize_decision_context_profile(payload)
    provider = build_decision_source_provider(
        profile.provider_binding_map()["local-authority"]
    )

    with pytest.raises(ValueError, match="exceeds max_bytes"):
        provider.scan(
            source=profile.sources[0],
            after_cursor=None,
            before="2100-01-01T00:00:00+00:00",
            limit=8,
            timeout_seconds=2,
            observed_at=OBSERVED_AT,
        )


def test_catalog_routes_to_default_off_profile_inspection() -> None:
    capability = build_capability_detail_packet("decision-context")["capability"]
    schema_versions = {
        protocol["schema_version"] for protocol in capability["implemented_protocols"]
    }

    assert "inspect-profile" in capability["entry_command"]
    assert {
        "decision_context_ephemeral_recall_v0",
        "decision_context_profile_v0",
        "decision_context_activation_status_v0",
        "decision_review_receipt_v0",
        "decision_review_settlement_v0",
    } <= schema_versions


def test_local_file_read_enforces_limit_after_stale_preflight(tmp_path, monkeypatch):
    authority = tmp_path / "authority.txt"
    authority.write_bytes(b"small")
    original_stat = Path.stat
    preflight = authority.stat()
    grew = False

    def stale_preflight(path, *args, **kwargs):
        nonlocal grew
        if path == authority:
            if not grew:
                # Real late growth, not a fabricated provider return value.
                authority.write_bytes(b"x" * 32)
                grew = True
            return preflight
        return original_stat(path, *args, **kwargs)

    payload = profile_payload(authority)
    payload["source_provider_bindings"][0]["config"]["max_bytes"] = 16
    profile = normalize_decision_context_profile(payload)
    provider = build_decision_source_provider(profile.provider_binding_map()["local-authority"])
    monkeypatch.setattr(Path, "stat", stale_preflight)
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        provider.scan(source=profile.sources[0], after_cursor=None,
            before="2100-01-01T00:00:00+00:00", limit=8,
            timeout_seconds=2, observed_at=OBSERVED_AT)
    assert grew


def test_local_file_growth_during_read_is_bounded(tmp_path, monkeypatch):
    import os

    authority = tmp_path / "authority.txt"
    authority.write_bytes(b"small")
    inode = authority.stat().st_ino
    payload = profile_payload(authority)
    payload["source_provider_bindings"][0]["config"]["max_bytes"] = 16
    profile = normalize_decision_context_profile(payload)
    provider = build_decision_source_provider(profile.provider_binding_map()["local-authority"])
    original_fstat, original_fdopen = os.fstat, os.fdopen
    read_sizes = []
    grew = False

    def grow_after_descriptor_check(fd):
        nonlocal grew
        observed = original_fstat(fd)
        if observed.st_ino == inode and not grew:
            authority.write_bytes(b"x" * 64)
            grew = True
        return observed

    class ObservedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            read_sizes.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(os, "fstat", grow_after_descriptor_check)
    monkeypatch.setattr(os, "fdopen", lambda *a, **kw: ObservedReader(original_fdopen(*a, **kw)))
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        provider.scan(source=profile.sources[0], after_cursor=None,
            before="2100-01-01T00:00:00+00:00", limit=8,
            timeout_seconds=2, observed_at=OBSERVED_AT)
    assert grew
    assert read_sizes == [17]


def test_local_file_replacement_after_open_has_no_verified_read(tmp_path, monkeypatch):
    import os

    authority = tmp_path / "authority.txt"
    authority.write_bytes(b"original")
    replacement = tmp_path / "replacement.txt"
    replacement.write_bytes(b"replaced")
    inode = authority.stat().st_ino
    profile = normalize_decision_context_profile(profile_payload(authority))
    provider = build_decision_source_provider(profile.provider_binding_map()["local-authority"])
    original_fstat = os.fstat
    replaced = False

    def replace_after_open(fd):
        nonlocal replaced
        observed = original_fstat(fd)
        if observed.st_ino == inode and not replaced:
            replacement.replace(authority)
            replaced = True
        return observed

    monkeypatch.setattr(os, "fstat", replace_after_open)
    with pytest.raises(ValueError, match="changed during bounded read"):
        provider.scan(source=profile.sources[0], after_cursor=None,
            before="2100-01-01T00:00:00+00:00", limit=8,
            timeout_seconds=2, observed_at=OBSERVED_AT)
    assert replaced


@pytest.mark.parametrize("size,allowed", [(16, True), (17, False)])
def test_local_file_exact_byte_limit_is_preserved(tmp_path, size, allowed):
    authority = tmp_path / "authority.txt"
    authority.write_bytes(b"x" * size)
    payload = profile_payload(authority)
    payload["source_provider_bindings"][0]["config"]["max_bytes"] = 16
    profile = normalize_decision_context_profile(payload)
    provider = build_decision_source_provider(profile.provider_binding_map()["local-authority"])
    args = dict(source=profile.sources[0], after_cursor=None,
        before="2100-01-01T00:00:00+00:00", limit=8,
        timeout_seconds=2, observed_at=OBSERVED_AT)
    if not allowed:
        with pytest.raises(ValueError, match="exceeds max_bytes"):
            provider.scan(**args)
        return
    scan = provider.scan(**args)
    exact = provider.exact_read(source=profile.sources[0], item=scan.items[0],
        timeout_seconds=2, observed_at=OBSERVED_AT)
    assert exact.content == "x" * 16


def test_local_file_in_place_edit_during_read_is_not_current(tmp_path, monkeypatch):
    import os

    authority = tmp_path / "authority.txt"
    authority.write_bytes(b"before")
    inode = authority.stat().st_ino
    profile = normalize_decision_context_profile(profile_payload(authority))
    provider = build_decision_source_provider(profile.provider_binding_map()["local-authority"])
    original_fstat = os.fstat
    changed = False

    def edit_after_open(fd):
        nonlocal changed
        observed = original_fstat(fd)
        if observed.st_ino == inode and not changed:
            authority.write_bytes(b"after!")
            os.utime(authority, ns=(observed.st_atime_ns, observed.st_mtime_ns + 1_000_000_000))
            changed = True
        return observed

    monkeypatch.setattr(os, "fstat", edit_after_open)
    with pytest.raises(ValueError, match="changed during bounded read"):
        provider.scan(source=profile.sources[0], after_cursor=None,
            before="2100-01-01T00:00:00+00:00", limit=8,
            timeout_seconds=2, observed_at=OBSERVED_AT)
    assert changed


def test_local_file_symlink_to_explicit_source_still_reads(tmp_path):
    authority = tmp_path / "authority.txt"
    authority.write_text("stable source")
    alias = tmp_path / "alias.txt"
    alias.symlink_to(authority)
    profile = normalize_decision_context_profile(profile_payload(alias))
    provider = build_decision_source_provider(profile.provider_binding_map()["local-authority"])
    scan = provider.scan(source=profile.sources[0], after_cursor=None,
        before="2100-01-01T00:00:00+00:00", limit=8,
        timeout_seconds=2, observed_at=OBSERVED_AT)
    exact = provider.exact_read(source=profile.sources[0], item=scan.items[0],
        timeout_seconds=2, observed_at=OBSERVED_AT)
    assert exact.content == "stable source"
    assert exact.resource_ref == str(authority.resolve())


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX FIFO replacement")
def test_local_file_replaced_by_fifo_after_preflight_does_not_block(tmp_path, monkeypatch):
    import os

    authority = tmp_path / "authority.txt"
    authority.write_text("stable source")
    profile = normalize_decision_context_profile(profile_payload(authority))
    provider = build_decision_source_provider(profile.provider_binding_map()["local-authority"])
    original_open = os.open
    descriptor = None

    def replace_at_open(path, flags, *a, **kw):
        nonlocal descriptor
        if Path(path) == authority:
            assert flags & os.O_NONBLOCK
            authority.unlink()
            os.mkfifo(authority)
        descriptor = original_open(path, flags, *a, **kw)
        return descriptor

    monkeypatch.setattr(os, "open", replace_at_open)
    with pytest.raises(ValueError, match="not a regular file"):
        provider.scan(source=profile.sources[0], after_cursor=None,
            before="2100-01-01T00:00:00+00:00", limit=8,
            timeout_seconds=2, observed_at=OBSERVED_AT)
    assert descriptor is not None
    with pytest.raises(OSError):
        os.fstat(descriptor)
