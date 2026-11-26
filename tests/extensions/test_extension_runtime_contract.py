"""Contract, budget, efficiency, and negative-path tests for extension runtime modules."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from types import MappingProxyType
import pytest

from loopx.extensions import (
    manifest as ext_manifest,
    process_runtime as ext_process,
    runtime as ext_runtime,
    runtime_bindings as ext_bindings,
    runtime_lifecycle as ext_lifecycle,
    runtime_state as ext_state,
)
from loopx.extensions.process_runtime import CappedProcessResult
from loopx.extensions.readiness import ResolvedRuntimeEntrypoint


def _write_provider_script(path: Path, *, doctor_exit: int = 0) -> Path:
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"if '--doctor' in sys.argv:\n    raise SystemExit({doctor_exit})\n"
        "req = json.load(sys.stdin)\n"
        "json.dump({'ok': True, 'echo': req.get('msg')}, sys.stdout)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_manifest(
    path: Path,
    *,
    entrypoint: Path,
    extension_id: str = "ext-contract",
    version: str = "1.0.0",
    requires_api: str = ">=1,<2",
    permissions: list[str] | None = None,
    protocol: str = "cap_v0",
    capability_id: str | None = None,
    implements_count: int = 1,
) -> Path:
    perms = list(permissions or [])
    lines = [
        'schema_version = "loopx_extension_manifest_v0"',
        f"id = {json.dumps(extension_id)}",
        f"version = {json.dumps(version)}",
        f"requires_loopx_api = {json.dumps(requires_api)}",
        f"permissions = {json.dumps(perms)}",
        "",
        "[runtime]",
        f"protocol = {json.dumps(protocol)}",
        f"entrypoint = {json.dumps(str(entrypoint))}",
        'doctor_args = ["--doctor"]',
        f"required_permissions = {json.dumps(perms)}",
        "timeout_seconds = 5",
    ]
    if capability_id is not None:
        lines += [
            "",
            "[[provides]]",
            f"id = {json.dumps(capability_id)}",
            'kind = "advisory"',
        ]
        for _ in range(implements_count):
            lines += [
                "",
                "[[implements]]",
                f"capability_id = {json.dumps(capability_id)}",
                f"protocol = {json.dumps(protocol)}",
            ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_extension_runtime_module_budgets_and_reexports() -> None:
    root = Path(ext_runtime.__file__).resolve().parents[2]
    targets = [
        ("loopx/extensions/runtime.py", 650),
        ("loopx/extensions/runtime_state.py", 750),
        ("loopx/extensions/runtime_lifecycle.py", 750),
        ("loopx/extensions/runtime_bindings.py", 750),
        ("tests/extensions/test_extension_runtime_contract.py", 700),
    ]
    internal_edges: dict[str, set[str]] = {}
    for rel, limit in targets:
        path = root / rel
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        assert len(lines) <= limit, f"{rel} exceeded {limit} LOC ({len(lines)})"
        assert not re.search(r"#\s*type:\s*ignore", src), f"type ignore in {rel}"
        assert not re.search(r"#\s*noqa\b", src), f"noqa in {rel}"
        tree = ast.parse(src, filename=str(path))
        assert not any(
            isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(tree)
        ), f"AST Any present in {rel}"
        internal_edges[path.stem] = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("runtime")
        }

    assert not (
        internal_edges["runtime_state"] & {"runtime", "runtime_lifecycle", "runtime_bindings"}
    )
    assert "runtime" not in internal_edges["runtime_lifecycle"]
    assert "runtime" not in internal_edges["runtime_bindings"]

    assert ext_runtime.ExtensionCapabilityResolution is ext_state.ExtensionCapabilityResolution
    assert ext_runtime.ExtensionCatalogEntry is ext_state.ExtensionCatalogEntry
    assert ext_runtime.ExtensionHookAdapterEntry is ext_state.ExtensionHookAdapterEntry
    assert ext_runtime.ExtensionRuntimeBinding is ext_state.ExtensionRuntimeBinding
    assert ext_runtime.default_extension_state_file is ext_state.default_extension_state_file
    assert ext_runtime._resolved_active_extension is ext_state._resolved_active_extension
    assert ext_runtime.run_capped_process is ext_process.run_capped_process
    for name in (
        "install_extension",
        "enable_extension",
        "disable_extension",
        "rollback_extension",
        "extension_status",
        "doctor_installed_extension",
        "doctor_enabled_extensions",
        "extension_catalog_entries",
    ):
        assert getattr(ext_runtime, name) is getattr(ext_lifecycle, name)
    for name in (
        "resolve_capability_extension_id",
        "resolve_extension_activation",
        "resolve_extension_binding",
        "resolve_optional_capability_binding",
        "resolve_extension_runtime_binding",
        "resolve_capability_binding",
    ):
        assert getattr(ext_runtime, name) is getattr(ext_bindings, name)


def test_extension_capability_resolution_and_state_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires exactly one binding"):
        ext_runtime.ExtensionCapabilityResolution(
            status="ready",
            extension_id="ext-1",
            installed=True,
            enabled=True,
            doctor_verified=True,
            next_action=None,
            binding=None,
        )
    with pytest.raises(ValueError, match="requires exactly one binding"):
        ext_runtime.ExtensionCapabilityResolution(
            status="extension_disabled",
            extension_id="ext-1",
            installed=True,
            enabled=False,
            doctor_verified=False,
            next_action="enable_extension",
            binding={"schema_version": ext_runtime.EXTENSION_BINDING_SCHEMA_VERSION},
        )
    res = ext_runtime.ExtensionCapabilityResolution(
        status="ready",
        extension_id="ext-1",
        installed=True,
        enabled=True,
        doctor_verified=True,
        next_action=None,
        binding=MappingProxyType({"extension_id": "ext-1"}),
    )
    assert res.public_readiness()["status"] == "ready"

    retained = ext_state._retain_revisions(
        [{"revision": f"r{idx}"} for idx in range(7)],
        {"revision": "r7"},
        required_revision="r0",
    )
    assert [row["revision"] for row in retained][0] == "r0"
    assert len(retained) == ext_state.MAX_REVISIONS

    legacy_entry = {
        "doctor_verified_revision": "r1",
        "doctor_verified_entrypoint_identity": "id-legacy",
    }
    assert ext_state._verified_identities(legacy_entry, "r1") == ["id-legacy"]

    bad_state = tmp_path / "bad-state.json"
    bad_state.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        ext_state._read_state(bad_state)
    bad_state.write_text(json.dumps({"schema_version": "wrong", "extensions": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match=ext_state.EXTENSION_STATE_SCHEMA_VERSION):
        ext_state._read_state(bad_state)


def test_efficiency_single_read_and_single_revision_scan_per_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _write_provider_script(tmp_path / "provider.py")
    manifest = _write_manifest(
        tmp_path / "ext.toml",
        entrypoint=provider,
        extension_id="ext-eff",
        permissions=["cap.read"],
        capability_id="cap-eff",
    )
    state_file = tmp_path / "state.json"
    installed = ext_runtime.install_extension(manifest, state_file=state_file, execute=True)
    assert installed["changed"] is True

    read_calls = 0
    rev_calls = 0
    deepcopy_calls = 0
    verify_calls = 0
    orig_read = ext_state._read_state
    orig_rev = ext_state._entry_for_revision
    orig_deepcopy = deepcopy
    orig_verify = ext_state._verified_entrypoint

    def count_read(path: Path) -> dict[str, object]:
        nonlocal read_calls
        read_calls += 1
        return orig_read(path)

    def count_rev(entry: Mapping[str, object], revision: str) -> dict[str, object]:
        nonlocal rev_calls
        rev_calls += 1
        return orig_rev(entry, revision)

    def count_deepcopy(obj: object) -> object:
        nonlocal deepcopy_calls
        deepcopy_calls += 1
        return orig_deepcopy(obj)

    def count_verify(
        entry: Mapping[str, object],
        *,
        active_revision: str | None = None,
        snapshot: Mapping[str, object] | None = None,
        manifest: Mapping[str, object] | None = None,
    ) -> ResolvedRuntimeEntrypoint | None:
        nonlocal verify_calls
        verify_calls += 1
        return orig_verify(
            entry,
            active_revision=active_revision,
            snapshot=snapshot,
            manifest=manifest,
        )

    monkeypatch.setattr(ext_bindings, "_read_state", count_read)
    monkeypatch.setattr(ext_lifecycle, "_read_state", count_read)
    monkeypatch.setattr(ext_state, "_entry_for_revision", count_rev)
    monkeypatch.setattr(ext_lifecycle, "_entry_for_revision", count_rev)
    monkeypatch.setattr(ext_lifecycle, "deepcopy", count_deepcopy)
    monkeypatch.setattr(ext_bindings, "_verified_entrypoint", count_verify)
    monkeypatch.setattr(ext_lifecycle, "_verified_entrypoint", count_verify)

    # resolve_capability_binding: 1 state read, 1 revision lookup, 0 deepcopies.
    binding = ext_runtime.resolve_capability_binding(
        state_file=state_file,
        capability_id="cap-eff",
        protocol="cap_v0",
        permission="cap.read",
    )
    assert binding["extension_id"] == "ext-eff"
    assert read_calls == 1 and rev_calls == 1 and deepcopy_calls == 0

    # resolve_optional_capability_binding (auto-discovery): 1 state read, 1 revision lookup, 1 verify.
    read_calls = rev_calls = verify_calls = 0
    opt = ext_runtime.resolve_optional_capability_binding(
        state_file=state_file,
        capability_id="cap-eff",
        protocol="cap_v0",
        permission="cap.read",
    )
    assert opt.status == "ready"
    assert read_calls == 1 and rev_calls == 1 and verify_calls == 1

    # extension_catalog_entries: 1 revision scan & 1 deepcopy per entry, caller mutation isolated.
    rev_calls = deepcopy_calls = verify_calls = 0
    catalog = ext_runtime.extension_catalog_entries(state_file=state_file)
    assert len(catalog) == 1 and rev_calls == 1 and deepcopy_calls == 1 and verify_calls == 1
    perms = catalog[0]["provider"]["permissions"]
    assert isinstance(perms, list)
    perms.append("mutated-by-caller")
    assert ext_runtime.extension_catalog_entries(state_file=state_file)[0]["provider"]["permissions"] == ["cap.read"]

    # doctor_installed_extension (dry-run): 1 revision scan before probe.
    rev_calls = 0
    doc_dry = ext_runtime.doctor_installed_extension("ext-eff", state_file=state_file, execute=False)
    assert doc_dry["available"] is True and doc_dry["status"] == "probe_required" and rev_calls == 1
    doc_exec = ext_runtime.doctor_installed_extension("ext-eff", state_file=state_file, execute=True)
    assert doc_exec["verified"] is True and rev_calls == 3


def test_lifecycle_rollback_api_guard_and_runtime_failure_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _write_provider_script(tmp_path / "provider.py")
    v1 = _write_manifest(
        tmp_path / "v1.toml",
        entrypoint=provider,
        extension_id="ext-solo",
        version="1.0.0",
        protocol="solo_v0",
    )
    v2 = _write_manifest(
        tmp_path / "v2.toml",
        entrypoint=provider,
        extension_id="ext-solo",
        version="2.0.0",
        protocol="solo_v0",
    )
    state_file = tmp_path / "state.json"

    with pytest.raises(ValueError, match="install or upgrade"):
        ext_runtime.install_extension(v1, state_file=state_file, operation="reinstall")
    ext_runtime.install_extension(v1, state_file=state_file, execute=True)
    with pytest.raises(ValueError, match="has no rollback revision"):
        ext_runtime.rollback_extension("ext-solo", state_file=state_file, execute=True)

    ext_runtime.install_extension(v2, state_file=state_file, operation="upgrade", execute=True)
    # Force rollback manifest incompatible API and confirm rollback fails closed before switching.
    raw = ext_state._read_state(state_file)
    entry = ext_state._extensions_dict(raw)["ext-solo"]
    assert isinstance(entry, dict)
    target = ext_state._entry_for_revision(entry, str(entry["rollback_revision"]))
    manifest_obj = target["manifest"]
    assert isinstance(manifest_obj, dict) and isinstance(manifest_obj["provider"], dict)
    manifest_obj["provider"]["requires_loopx_api"] = ">=99,<100"
    ext_state._write_state(state_file, raw)
    with pytest.raises(ValueError, match="roll forward"):
        ext_runtime.rollback_extension("ext-solo", state_file=state_file, execute=True)

    # Valid standalone dry-run/execute + capped process failure modes via run_capped_process seam.
    dry = ext_runtime.run_standalone_extension(
        "ext-solo",
        state_file=state_file,
        request=MappingProxyType({"schema_version": "req_v0"}),
        execute=False,
    )
    assert dry["status"] == "ready" and dry["dry_run"] is True

    monkeypatch.setattr(
        ext_runtime,
        "run_capped_process",
        lambda *_a, **_kw: CappedProcessResult(stdout=b"", stderr=b"", returncode=-1, failure_kind="timeout"),
    )
    timed_out = ext_runtime.run_standalone_extension(
        "ext-solo", state_file=state_file, request={"msg": "slow"}, execute=True
    )
    assert timed_out["ok"] is False and timed_out["failure_kind"] == "timeout" and timed_out["exit_code"] is None

    monkeypatch.setattr(
        ext_runtime,
        "run_capped_process",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    failed_spawn = ext_runtime.run_standalone_extension(
        "ext-solo", state_file=state_file, request={"msg": "err"}, execute=True
    )
    assert failed_spawn["failure_kind"] == "execution_failed"

    monkeypatch.setattr(
        ext_runtime,
        "run_capped_process",
        lambda *_a, **_kw: CappedProcessResult(stdout=b"{bad", stderr=b"", returncode=0, failure_kind=None),
    )
    bad_json = ext_runtime.run_standalone_extension(
        "ext-solo", state_file=state_file, request={"msg": "badjson"}, execute=True
    )
    assert bad_json["failure_kind"] == "response_not_json_object"

    # Binding execution negative validations (fail closed).
    valid_binding: ext_runtime.ExtensionRuntimeBinding = {
        "schema_version": ext_runtime.EXTENSION_BINDING_SCHEMA_VERSION,
        "extension_id": "ext-solo",
        "provider_version": "2.0.0",
        "revision": "rev",
        "protocol": "solo_v0",
        "argv": [str(provider)],
        "environment_path_prefix": None,
        "doctor_argv": [str(provider), "--doctor"],
        "timeout_seconds": 5,
    }
    with pytest.raises(ValueError, match="argv is invalid"):
        ext_runtime.execute_extension_runtime_binding({**valid_binding, "argv": []}, request={})
    with pytest.raises(ValueError, match="timeout is invalid"):
        ext_runtime.execute_extension_runtime_binding({**valid_binding, "timeout_seconds": True}, request={})
    with pytest.raises(ValueError, match="timeout is invalid"):
        ext_runtime.execute_extension_runtime_binding({**valid_binding, "timeout_seconds": float("nan")}, request={})
    with pytest.raises(ValueError, match="search directory must be absolute"):
        ext_runtime.execute_extension_runtime_binding({**valid_binding, "environment_path_prefix": 123}, request={})
    with pytest.raises(RuntimeError, match="invalid JSON"):
        ext_runtime.execute_extension_runtime_binding(valid_binding, request={})

    # Silence unused import check while anchoring manifest version coupling.
    assert ext_manifest.EXTENSION_MANIFEST_SCHEMA_VERSION == "loopx_extension_manifest_v0"


def test_duplicate_implements_rejects_auto_explicit_and_optional_consistently(
    tmp_path: Path,
) -> None:
    provider = _write_provider_script(tmp_path / "dup_provider.py")
    manifest = _write_manifest(
        tmp_path / "dup_manifest.toml",
        entrypoint=provider,
        extension_id="ext-dup",
        permissions=["read_state"],
        protocol="dup_v0",
        capability_id="cap.dup",
        implements_count=2,
    )
    state_file = tmp_path / "ext_state.json"
    install = ext_runtime.install_extension(manifest, state_file=state_file, execute=True)
    assert install["changed"] is True
    assert install["enabled"] is True
    assert (
        ext_runtime.resolve_capability_extension_id(
            state_file=state_file,
            capability_id="cap.dup",
            protocol="dup_v0",
        )
        == "ext-dup"
    )
    with pytest.raises(ValueError, match="does not implement"):
        ext_runtime.resolve_capability_binding(
            state_file=state_file,
            capability_id="cap.dup",
            protocol="dup_v0",
            permission="read_state",
        )
    with pytest.raises(ValueError, match="does not implement"):
        ext_runtime.resolve_extension_binding(
            "ext-dup",
            state_file=state_file,
            capability_id="cap.dup",
            protocol="dup_v0",
            permission="read_state",
        )
    for explicit_id in (None, "ext-dup"):
        opt = ext_runtime.resolve_optional_capability_binding(
            state_file=state_file,
            capability_id="cap.dup",
            protocol="dup_v0",
            permission="read_state",
            extension_id=explicit_id,
        )
        assert opt.status == "extension_incompatible"
        assert opt.extension_id == "ext-dup"
        assert opt.installed is True
        assert opt.enabled is True
        assert opt.doctor_verified is True
        assert opt.next_action == "install_compatible_extension"
        assert opt.binding is None
