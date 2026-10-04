from __future__ import annotations

import argparse
import ast
import io
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.capabilities.catalog import (
    build_capability_catalog_packet,
    build_capability_detail_packet,
)
from loopx.capabilities.semantic_preference.cli import (
    _register_legacy_openviking_provider_arguments,
)
from loopx.capabilities.semantic_preference.contract import provider_doctor, recall
from loopx.cli import main
from loopx.extensions.hook_adapters import discover_extension_hook_adapters
from loopx.extensions.manifest import load_extension_manifest
from loopx.extensions.openviking_semantic_preference.provider import (
    register_openviking_provider_arguments,
)
from loopx.extensions.readiness import (
    resolve_runtime_entrypoint,
    resolved_entrypoint_identity,
)
from loopx.extensions.runtime import (
    MAX_EXTENSION_REQUEST_BYTES,
    MAX_EXTENSION_RESPONSE_BYTES,
    disable_extension,
    doctor_enabled_extensions,
    doctor_installed_extension,
    enable_extension,
    execute_extension_runtime_binding,
    extension_catalog_entries,
    extension_status,
    install_extension,
    resolve_capability_binding,
    resolve_capability_extension_id,
    resolve_extension_activation,
    resolve_extension_binding,
    resolve_extension_runtime_binding,
    resolve_optional_capability_binding,
    rollback_extension,
    run_standalone_extension,
)


def _provider(path: Path, *, doctor_exit: int = 0) -> Path:
    path.write_text(
        f"""#!{sys.executable}
import json
import sys

if "--doctor" in sys.argv:
    raise SystemExit({doctor_exit})

request = json.load(sys.stdin)
json.dump({{
    "schema_version": "semantic_preference_provider_response_v0",
    "items": [{{
        "preference_ref": "provider://preference/one",
        "summary": "Prefer compact validation notes.",
    }}],
}}, sys.stdout)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _manifest(
    path: Path,
    *,
    entrypoint: Path,
    version: str,
    extension_id: str = "test-semantic-extension",
) -> Path:
    path.write_text(
        f"""\
schema_version = "loopx_extension_manifest_v0"
id = "{extension_id}"
version = "{version}"
requires_loopx_api = ">=1,<2"
permissions = ["semantic_preference.read"]

[runtime]
protocol = "semantic_preference_provider_v0"
entrypoint = {json.dumps(str(entrypoint))}
doctor_args = ["--doctor"]
required_permissions = ["semantic_preference.read"]
timeout_seconds = 5

[[implements]]
capability_id = "semantic-preference"
protocol = "semantic_preference_provider_v0"
""",
        encoding="utf-8",
    )
    return path


def _standalone_manifest(
    path: Path,
    *,
    entrypoint: Path,
    version: str = "1.0.0",
    extension_id: str = "test-standalone-extension",
    permission: str | None = None,
) -> Path:
    manifest = _manifest(
        path,
        entrypoint=entrypoint,
        version=version,
        extension_id=extension_id,
    )
    contents = manifest.read_text(encoding="utf-8").replace(
        "\n[[implements]]\n"
        'capability_id = "semantic-preference"\n'
        'protocol = "semantic_preference_provider_v0"\n',
        "\n",
    )
    if permission is None:
        contents = contents.replace(
            'permissions = ["semantic_preference.read"]',
            "permissions = []",
        ).replace(
            'required_permissions = ["semantic_preference.read"]',
            "required_permissions = []",
        )
    manifest.write_text(contents, encoding="utf-8")
    return manifest


def _presentation_surface_manifest(
    path: Path,
    *,
    entrypoint: Path,
    declaration: str = "",
    include_view_validator: bool = True,
) -> Path:
    manifest = _standalone_manifest(path, entrypoint=entrypoint)
    presentation_declaration = (
        declaration
        or """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
"""
    )
    if include_view_validator:
        presentation_declaration = presentation_declaration.replace(
            '\nvisibility = "',
            "\nview_validator = "
            '"loopx.extensions.presentation:validate_opaque_presentation_view"\n'
            'visibility = "',
        )
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + presentation_declaration,
        encoding="utf-8",
    )
    return manifest


def test_presentation_surface_manifest_is_normalized(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = load_extension_manifest(
        _presentation_surface_manifest(
            tmp_path / "extension.toml",
            entrypoint=provider,
        )
    )

    assert manifest["presentation_surfaces"] == [
        {
            "id": "investment-research",
            "kind": "decision_research_dashboard",
            "title": "Investment Research",
            "view_schema": "decision_research_dashboard_v0",
            "view_validator": (
                "loopx.extensions.presentation:validate_opaque_presentation_view"
            ),
            "visibility": "owner-only",
            "empty_state_title": "No validated research yet",
            "empty_state_detail": "Publish a validated projection.",
        }
    ]


def test_capability_action_hook_adapter_manifest_is_normalized(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    manifest_path = _standalone_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        permission="semantic_preference.read",
    )
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + """

[[hook_adapters]]
id = "sample-report-source"
capability_id = "sample-report"
target_hook_id = "sample_report.request"
phase = "capability_action"
factory = "sample_extension.hooks:build_adapter"
required_permissions = ["semantic_preference.read"]
ports = ["sample_report.request.bind_source", "sample_report.request.settle_source"]
""",
        encoding="utf-8",
    )

    manifest = load_extension_manifest(manifest_path)

    assert manifest["hook_adapters"] == [
        {
            "id": "sample-report-source",
            "capability_id": "sample-report",
            "target_hook_id": "sample_report.request",
            "phase": "capability_action",
            "factory": "sample_extension.hooks:build_adapter",
            "required_permissions": ["semantic_preference.read"],
            "ports": [
                "sample_report.request.bind_source",
                "sample_report.request.settle_source",
            ],
        }
    ]


def test_capability_action_factory_failure_is_content_free_and_kernel_independent(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest_path = _standalone_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        permission="semantic_preference.read",
    )
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + """

[[hook_adapters]]
id = "sample-report-source"
capability_id = "sample-report"
target_hook_id = "sample_report.request"
phase = "capability_action"
factory = "missing_extension.hooks:build_adapter"
required_permissions = ["semantic_preference.read"]
ports = ["sample_report.request.bind_source"]
""",
        encoding="utf-8",
    )
    state_file = tmp_path / "runtime" / "extensions" / "state.json"
    install_extension(manifest_path, state_file=state_file, execute=True)

    discovery = discover_extension_hook_adapters(
        state_file=state_file,
        phase="capability_action",
        capability_id="sample-report",
        target_hook_id="sample_report.request",
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        goal_id="sample-goal",
        agent_id="sample-agent",
    )

    assert discovery.ports == ()
    assert len(discovery.failures) == 1
    assert discovery.failures[0].adapter_id == "sample-report-source"
    assert discovery.failures[0].error_code == "extension_hook_adapter_unavailable"


@pytest.mark.parametrize(
    "extension_id",
    [
        "../escaped",
        "nested/extension",
        "Uppercase",
        "contains_underscore",
        "trailing-",
        f"a{'b' * 48}",
    ],
)
def test_manifest_rejects_extension_ids_outside_strict_path_segment_grammar(
    tmp_path: Path,
    extension_id: str,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _standalone_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        extension_id=extension_id,
    )

    with pytest.raises(
        ValueError,
        match="extension id must be a lower-kebab path segment up to 48 characters",
    ):
        load_extension_manifest(manifest)


def test_presentation_surface_manifest_requires_explicit_view_validator(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _presentation_surface_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        declaration="""
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
        include_view_validator=False,
    )

    with pytest.raises(ValueError, match="requires non-empty string `view_validator`"):
        load_extension_manifest(manifest)


def test_manifest_without_presentation_surface_keeps_empty_collection(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = load_extension_manifest(
        _standalone_manifest(tmp_path / "extension.toml", entrypoint=provider)
    )

    assert manifest["presentation_surfaces"] == []


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."

[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Duplicate"
view_schema = "decision_research_dashboard_v0"
visibility = "public-safe"
empty_state_title = "Empty"
empty_state_detail = "No projection."
""",
            "duplicate presentation surface id",
        ),
        (
            """
[[presentation_surfaces]]
id = "Investment Research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
            "lower-kebab token",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision-research-dashboard"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
            "view_schema must be",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "Decision Research Dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
            "kind must be",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "private"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
            "visibility",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "<script>alert(1)</script>"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
            "plain text",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Read https://example.com/research"
""",
            "plain text",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "/tmp/research.json"
""",
            "plain text",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "%s"
"""
            % ("x" * 241),
            "at most 240",
        ),
        (
            """
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
unexpected = "not allowed"
""",
            "unsupported keys",
        ),
    ],
)
def test_presentation_surface_manifest_rejects_invalid_declarations(
    tmp_path: Path,
    declaration: str,
    message: str,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _presentation_surface_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        declaration=declaration,
    )

    with pytest.raises(ValueError, match=message):
        load_extension_manifest(manifest)


def _overflow_provider(
    path: Path,
    *,
    stream: str,
    completion_marker: Path,
) -> Path:
    child_code = (
        "from pathlib import Path; import time; "
        "time.sleep(0.6); "
        f"Path({str(completion_marker)!r}).write_text('completed', encoding='utf-8')"
    )
    path.write_text(
        f"""#!{sys.executable}
import json
import subprocess
import sys
import time

if "--doctor" in sys.argv:
    raise SystemExit(0)

json.load(sys.stdin)
subprocess.Popen([sys.executable, "-c", {json.dumps(child_code)}])
target = sys.{stream}.buffer
target.write(b"x" * {MAX_EXTENSION_RESPONSE_BYTES + 1})
target.flush()
time.sleep(5)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _python_module_manifest(path: Path, *, module_name: str) -> Path:
    path.write_text(
        """\
schema_version = "loopx_extension_manifest_v0"
id = "test-lark-module-extension"
version = "1.0.0"
requires_loopx_api = ">=1,<2"
permissions = ["semantic_preference.read"]

[runtime]
protocol = "semantic_preference_provider_v0"
python_module = "{module_name}"
doctor_args = ["--doctor"]
required_permissions = ["semantic_preference.read"]
timeout_seconds = 5

[[implements]]
capability_id = "semantic-preference"
protocol = "semantic_preference_provider_v0"
""".format(module_name=module_name),
        encoding="utf-8",
    )
    return path


def test_python_module_runtime_uses_current_interpreter_without_console_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "test_loopx_extension_provider"
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text(
        "import sys\nraise SystemExit(0 if '--doctor' in sys.argv else 2)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    python_path = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(part for part in [str(tmp_path), python_path] if part),
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    manifest = _python_module_manifest(
        tmp_path / "extension.toml",
        module_name=module_name,
    )
    state_file = tmp_path / "extensions.json"

    installed = install_extension(manifest, state_file=state_file, execute=True)
    assert installed["doctor"]["verified"] is True
    binding = resolve_extension_binding(
        "test-lark-module-extension",
        state_file=state_file,
        capability_id="semantic-preference",
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )

    assert binding["argv"] == [
        sys.executable,
        "-m",
        module_name,
    ]
    assert binding["doctor_argv"] == [*binding["argv"], "--doctor"]

    module_path.write_text(
        module_path.read_text(encoding="utf-8") + "# replaced\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        resolve_extension_activation(
            "test-lark-module-extension",
            state_file=state_file,
        )


def test_runtime_identity_survives_unchanged_release_root_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_a = tmp_path / "release-a"
    release_b = tmp_path / "release-b"
    release_a.mkdir()
    release_b.mkdir()
    module_a = release_a / "fixture_provider.py"
    module_b = release_b / "fixture_provider.py"
    module_a.write_text("VALUE = 1\n", encoding="utf-8")
    module_b.write_text(module_a.read_text(encoding="utf-8"), encoding="utf-8")
    current_origin = module_a
    monkeypatch.setattr(
        "loopx.extensions.readiness.importlib.util.find_spec",
        lambda _module: SimpleNamespace(origin=str(current_origin)),
    )
    runtime = {"python_module": "fixture_provider"}

    identity_a = resolve_runtime_entrypoint(runtime)
    current_origin = module_b
    identity_b = resolve_runtime_entrypoint(runtime)

    assert identity_a is not None and identity_b is not None
    assert identity_a.argv_prefix == identity_b.argv_prefix
    assert identity_a.identity == identity_b.identity

    module_b.write_text("VALUE = 2\n", encoding="utf-8")
    changed = resolve_runtime_entrypoint(runtime)
    assert changed is not None
    assert changed.identity != identity_a.identity


def test_executable_identity_is_content_addressed_across_paths(tmp_path: Path) -> None:
    provider_a = _provider(tmp_path / "release-a-provider")
    provider_b = tmp_path / "release-b-provider"
    provider_b.write_bytes(provider_a.read_bytes())
    provider_b.chmod(0o755)

    identity_a = resolved_entrypoint_identity(str(provider_a))
    identity_b = resolved_entrypoint_identity(str(provider_b))

    assert identity_a is not None and identity_b is not None
    assert identity_a[1] == identity_b[1]

    provider_b.write_text(
        provider_b.read_text(encoding="utf-8") + "# changed\n",
        encoding="utf-8",
    )
    changed = resolved_entrypoint_identity(str(provider_b))
    assert changed is not None
    assert changed[1] != identity_a[1]


def test_runtime_entrypoint_resolves_sibling_python_for_opaque_launcher(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "provider.exe"
    launcher.write_bytes(b"MZ opaque console launcher")
    launcher.chmod(0o755)
    sibling_python = tmp_path / "python.exe"
    sibling_python.symlink_to(sys.executable)

    resolved = resolve_runtime_entrypoint({"entrypoint": str(launcher)})

    assert resolved is not None
    assert resolved.python_executable == str(sibling_python)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink mutation fixture")
def test_opaque_runtime_identity_binds_sibling_python_artifact(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "provider.exe"
    launcher.write_bytes(b"MZ opaque console launcher")
    launcher.chmod(0o755)
    sibling_python = tmp_path / "python.exe"
    sibling_python.symlink_to(sys.executable)

    verified = resolve_runtime_entrypoint({"entrypoint": str(launcher)})
    sibling_python.unlink()
    sibling_python.symlink_to("/bin/sh")
    changed = resolve_runtime_entrypoint({"entrypoint": str(launcher)})

    assert verified is not None and changed is not None
    assert changed.python_executable == verified.python_executable == str(sibling_python)
    assert changed.identity != verified.identity


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink mutation fixture")
def test_runtime_entrypoint_identity_binds_selected_python_artifact(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    interpreter = runtime_root / "python"
    interpreter.symlink_to(sys.executable)
    provider = runtime_root / "provider"
    provider.write_text(
        f"#!{interpreter}\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)

    verified = resolve_runtime_entrypoint({"entrypoint": str(provider)})
    interpreter.unlink()
    interpreter.symlink_to("/bin/sh")
    changed = resolve_runtime_entrypoint({"entrypoint": str(provider)})

    assert verified is not None and changed is not None
    assert changed.python_executable == verified.python_executable == str(interpreter)
    assert changed.identity != verified.identity


@pytest.mark.skipif(os.name == "nt", reason="POSIX shebang lifecycle fixture")
def test_catalog_invalidates_doctor_when_selected_python_artifact_changes(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    interpreter = runtime_root / "python"
    interpreter.symlink_to(sys.executable)
    provider = _provider(runtime_root / "provider")
    provider.write_text(
        provider.read_text(encoding="utf-8").replace(
            f"#!{sys.executable}",
            f"#!{interpreter}",
            1,
        ),
        encoding="utf-8",
    )
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    ready = extension_catalog_entries(state_file=state_file)
    assert ready[0]["provider"]["ready"] is True

    interpreter.unlink()
    interpreter.symlink_to("/bin/sh")

    stale = extension_catalog_entries(state_file=state_file)
    assert stale[0]["provider"]["ready"] is False
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        resolve_extension_binding(
            "test-semantic-extension",
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
            permission="semantic_preference.read",
        )


def test_runtime_identity_binds_declared_view_validator_reference(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = load_extension_manifest(
        _standalone_manifest(tmp_path / "extension.toml", entrypoint=provider)
    )
    runtime = manifest["runtime"]
    declaration = ("missing_validator_module:validate_view",)

    undeclared = resolve_runtime_entrypoint(runtime)
    declared = resolve_runtime_entrypoint(runtime, view_validators=declaration)
    repeated = resolve_runtime_entrypoint(runtime, view_validators=declaration)

    assert undeclared is not None and declared is not None
    assert repeated is not None
    # Declaring implementation code the runtime interpreter runs is part of what
    # the doctor verifies, and an unresolvable declaration must not drift between
    # two computations of the same declaration.
    assert declared.identity != undeclared.identity
    assert repeated.identity == declared.identity


def test_doctor_keeps_provider_ready_when_declared_validator_cannot_resolve(
    tmp_path: Path,
) -> None:
    """A validator the runtime cannot import is not a provider-runtime fault.

    The doctor owns the provider runtime: the launcher, the selected interpreter
    and the implementations that run in it. A declared validator the runtime
    interpreter cannot resolve is recorded in that identity, and the projection
    surface that uses it fails with its own actionable error instead.
    """

    provider = _provider(tmp_path / "provider")
    manifest = _presentation_surface_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        declaration="""
[[presentation_surfaces]]
id = "investment-research"
kind = "decision_research_dashboard"
title = "Investment Research"
view_schema = "decision_research_dashboard_v0"
view_validator = "missing_validator_module:validate_view"
visibility = "owner-only"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
        include_view_validator=False,
    )
    state_file = tmp_path / "extensions.json"
    installed = install_extension(manifest, state_file=state_file, execute=True)

    assert installed["doctor"]["verified"] is True
    assert extension_catalog_entries(state_file=state_file)[0]["provider"]["ready"]

    redoctored = doctor_installed_extension(
        "test-standalone-extension",
        state_file=state_file,
        execute=True,
    )
    assert redoctored["verified"] is True
    assert (
        redoctored["entrypoint_identity"]
        == installed["doctor"]["entrypoint_identity"]
    )


def test_standalone_runtime_does_not_require_a_capability_contract(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _standalone_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        permission="semantic_preference.read",
    )
    state_file = tmp_path / "extensions.json"

    install_extension(manifest, state_file=state_file, execute=True)
    binding = resolve_extension_runtime_binding(
        "test-standalone-extension",
        state_file=state_file,
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )

    assert binding["extension_id"] == "test-standalone-extension"
    assert binding["argv"] == [str(provider)]
    with pytest.raises(ValueError, match="does not expose runtime protocol"):
        resolve_extension_runtime_binding(
            "test-standalone-extension",
            state_file=state_file,
            protocol="different_protocol_v0",
            permission="semantic_preference.read",
        )


def test_extension_run_is_lifecycle_gated_and_returns_provider_packet(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _standalone_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    request = {"schema_version": "test_extension_request_v0"}

    preview = run_standalone_extension(
        "test-standalone-extension",
        state_file=state_file,
        request=request,
    )
    assert preview["status"] == "ready"
    assert preview["executed"] is False

    executed = run_standalone_extension(
        "test-standalone-extension",
        state_file=state_file,
        request=request,
        execute=True,
    )
    assert executed["status"] == "succeeded"
    assert executed["provider_result"]["schema_version"] == (
        "semantic_preference_provider_response_v0"
    )

    disable_extension(
        "test-standalone-extension",
        state_file=state_file,
        execute=True,
    )
    with pytest.raises(ValueError, match="is disabled"):
        run_standalone_extension(
            "test-standalone-extension",
            state_file=state_file,
            request=request,
            execute=True,
        )


def test_capability_executor_reuses_bounded_json_runtime(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    binding = {
        "schema_version": "loopx_extension_runtime_binding_v0",
        "argv": [str(provider)],
        "timeout_seconds": 30,
    }

    result = execute_extension_runtime_binding(
        binding,
        request={"schema_version": "test_extension_request_v0"},
    )

    assert result["schema_version"] == "semantic_preference_provider_response_v0"
    with pytest.raises(ValueError, match="request exceeds"):
        execute_extension_runtime_binding(
            binding,
            request={"payload": "x" * (MAX_EXTENSION_REQUEST_BYTES + 1)},
        )


def test_capability_executor_preserves_fractional_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, float] = {}

    def run_process(*_args: object, **kwargs: object) -> SimpleNamespace:
        captured["timeout_seconds"] = float(kwargs["timeout_seconds"])
        return SimpleNamespace(
            failure_kind=None,
            stdout=b'{"ok": true}',
            returncode=0,
        )

    monkeypatch.setattr(
        "loopx.extensions.runtime.run_capped_process",
        run_process,
    )

    result = execute_extension_runtime_binding(
        {
            "schema_version": "loopx_extension_runtime_binding_v0",
            "argv": ["provider"],
            "timeout_seconds": 1.5,
        },
        request={"schema_version": "test_extension_request_v0"},
    )

    assert result == {"ok": True}
    assert captured["timeout_seconds"] == 1.5


def test_extension_run_rejects_capability_provider_bypass(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)

    with pytest.raises(ValueError, match="through their capability or domain command"):
        run_standalone_extension(
            "test-semantic-extension",
            state_file=state_file,
            request={"schema_version": "test_extension_request_v0"},
            execute=True,
        )


@pytest.mark.parametrize("permission", ["external_write", "openviking_context_write"])
def test_extension_run_rejects_any_declared_permission_before_invocation(
    tmp_path: Path,
    permission: str,
) -> None:
    marker = tmp_path / "invoked"
    provider = tmp_path / "provider"
    provider.write_text(
        f"""#!{sys.executable}
from pathlib import Path
import sys

if "--doctor" in sys.argv:
    raise SystemExit(0)
Path({json.dumps(str(marker))}).write_text("invoked", encoding="utf-8")
""",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    manifest = _standalone_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        .replace("permissions = []", f"permissions = [{json.dumps(permission)}]")
        .replace(
            "required_permissions = []",
            f"required_permissions = [{json.dumps(permission)}]",
        ),
        encoding="utf-8",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)

    with pytest.raises(ValueError, match="standalone extension run grants no"):
        run_standalone_extension(
            "test-standalone-extension",
            state_file=state_file,
            request={"schema_version": "test_extension_request_v0"},
            execute=True,
        )

    assert not marker.exists()


@pytest.mark.parametrize(
    ("stream", "expected_status", "expected_failure"),
    [
        ("stdout", "invalid_provider_output", "response_too_large"),
        ("stderr", "provider_failed", "stderr_too_large"),
    ],
)
def test_extension_run_terminates_provider_when_output_limit_is_crossed(
    tmp_path: Path,
    stream: str,
    expected_status: str,
    expected_failure: str,
) -> None:
    marker = tmp_path / f"{stream}-completed"
    provider = _overflow_provider(
        tmp_path / f"{stream}-provider",
        stream=stream,
        completion_marker=marker,
    )
    manifest = _standalone_manifest(
        tmp_path / f"{stream}-extension.toml",
        entrypoint=provider,
    )
    state_file = tmp_path / f"{stream}-extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)

    receipt = run_standalone_extension(
        "test-standalone-extension",
        state_file=state_file,
        request={"schema_version": "test_extension_request_v0"},
        execute=True,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == expected_status
    assert receipt["failure_kind"] == expected_failure
    time.sleep(0.8)
    assert not marker.exists()


def test_extension_run_terminates_provider_on_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "timeout-completed"
    provider = tmp_path / "timeout-provider"
    child_code = (
        "from pathlib import Path; import time; "
        "time.sleep(3.5); "
        f"Path({str(marker)!r}).write_text('completed', encoding='utf-8')"
    )
    provider.write_text(
        f"""#!{sys.executable}
import subprocess
import sys
import time

if "--doctor" in sys.argv:
    raise SystemExit(0)
subprocess.Popen([sys.executable, "-c", {json.dumps(child_code)}])
time.sleep(10)
""",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    manifest = _standalone_manifest(
        tmp_path / "timeout-extension.toml",
        entrypoint=provider,
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "timeout_seconds = 5",
            # The same timeout gates the install-time doctor subprocess. Keep
            # enough startup headroom for loaded parallel test hosts.
            "timeout_seconds = 3",
        ),
        encoding="utf-8",
    )
    state_file = tmp_path / "timeout-extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)

    receipt = run_standalone_extension(
        "test-standalone-extension",
        state_file=state_file,
        request={"schema_version": "test_extension_request_v0"},
        execute=True,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "provider_failed"
    assert receipt["failure_kind"] == "timeout"
    assert receipt["exit_code"] is None
    time.sleep(0.75)
    assert not marker.exists()


def test_extension_run_cli_rejects_oversized_file_before_runtime_resolution(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = tmp_path / "oversized.json"
    request.write_bytes(b"{" + b" " * MAX_EXTENSION_REQUEST_BYTES)

    assert (
        main(
            [
                "--format",
                "json",
                "extension",
                "run",
                "not-installed",
                "--state-file",
                str(tmp_path / "extensions.json"),
                "--input-json",
                str(request),
                "--execute",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "invalid_request"
    assert "exceeds the 1000000-byte limit" in payload["error"]


def test_extension_run_cli_rejects_oversized_stdin_before_runtime_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BinaryStdin:
        buffer = io.BytesIO(b"{" + b" " * MAX_EXTENSION_REQUEST_BYTES)

    monkeypatch.setattr(sys, "stdin", BinaryStdin())

    assert (
        main(
            [
                "--format",
                "json",
                "extension",
                "run",
                "not-installed",
                "--state-file",
                str(tmp_path / "extensions.json"),
                "--input-json",
                "-",
                "--execute",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "invalid_request"
    assert "exceeds the 1000000-byte limit" in payload["error"]


def test_extension_run_fails_closed_on_invalid_provider_output(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    provider.write_bytes(
        f"#!{sys.executable}\n".encode()
        + b"import sys\n"
        + b"if '--doctor' in sys.argv:\n    raise SystemExit(0)\n"
        + b"sys.stdout.buffer.write(b'\\xff')\n"
    )
    provider.chmod(0o755)
    manifest = _standalone_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)

    receipt = run_standalone_extension(
        "test-standalone-extension",
        state_file=state_file,
        request={"schema_version": "test_extension_request_v0"},
        execute=True,
    )
    assert receipt["ok"] is False
    assert receipt["status"] == "invalid_provider_output"
    assert receipt["failure_kind"] == "response_not_json_object"


@pytest.mark.parametrize(
    "runtime_target",
    [
        "",
        'entrypoint = "provider"\npython_module = "provider.module"',
        'python_module = "not-a-module"',
    ],
)
def test_runtime_requires_one_valid_launch_target(
    tmp_path: Path,
    runtime_target: str,
) -> None:
    manifest = _python_module_manifest(
        tmp_path / "extension.toml",
        module_name="loopx.extensions.lark.provider",
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'python_module = "loopx.extensions.lark.provider"',
            runtime_target,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one|dotted Python module"):
        install_extension(manifest, state_file=tmp_path / "extensions.json")


def test_install_disable_upgrade_and_rollback_preserve_verified_binding(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    v1 = _manifest(tmp_path / "v1.toml", entrypoint=provider, version="1.0.0")
    v2 = _manifest(tmp_path / "v2.toml", entrypoint=provider, version="2.0.0")
    state_file = tmp_path / "runtime" / "extensions.json"

    preview = install_extension(v1, state_file=state_file)
    assert preview["changed"] is False
    assert preview["doctor"]["status"] == "probe_required"
    assert not state_file.exists()
    assert not state_file.with_name(f"{state_file.name}.lock").exists()

    installed = install_extension(v1, state_file=state_file, execute=True)
    assert installed["changed"] is True
    activation = resolve_extension_activation(
        "test-semantic-extension",
        state_file=state_file,
        required_permissions=["semantic_preference.read"],
    )
    assert activation == {
        "schema_version": "loopx_extension_activation_v0",
        "extension_id": "test-semantic-extension",
        "provider_version": "1.0.0",
        "revision": installed["revision"],
        "enabled": True,
        "doctor_verified": True,
        "required_permissions": ["semantic_preference.read"],
    }
    with pytest.raises(ValueError, match="does not declare permissions"):
        resolve_extension_activation(
            "test-semantic-extension",
            state_file=state_file,
            required_permissions=["semantic_preference.write"],
        )
    binding = resolve_extension_binding(
        "test-semantic-extension",
        state_file=state_file,
        capability_id="semantic-preference",
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )
    assert binding["argv"] == [str(provider)]
    assert binding["timeout_seconds"] == 5

    disabled = disable_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )
    assert disabled["changed"] is True
    with pytest.raises(ValueError, match="is disabled"):
        resolve_extension_activation(
            "test-semantic-extension",
            state_file=state_file,
        )
    with pytest.raises(ValueError, match="is disabled"):
        resolve_extension_binding(
            "test-semantic-extension",
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
            permission="semantic_preference.read",
        )

    enabled = enable_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )
    assert enabled["changed"] is True
    assert enabled["doctor"]["verified"] is True
    assert resolve_extension_binding(
        "test-semantic-extension",
        state_file=state_file,
        capability_id="semantic-preference",
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )["argv"] == [str(provider)]
    already_enabled = enable_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )
    assert already_enabled["changed"] is False
    assert already_enabled["doctor"]["verified"] is True

    upgraded = install_extension(
        v2,
        state_file=state_file,
        operation="upgrade",
        execute=True,
    )
    assert upgraded["previous_revision"] == installed["revision"]
    assert upgraded["rollback_available"] is True
    rolled_back = rollback_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )
    assert rolled_back["revision"] == installed["revision"]
    assert extension_status(state_file=state_file)["extensions"] == [
        {
            "id": "test-semantic-extension",
            "enabled": True,
            "active_revision": installed["revision"],
            "rollback_available": True,
            "doctor_verified": True,
            "revision_count": 2,
        }
    ]


def test_failed_upgrade_keeps_the_active_revision(tmp_path: Path) -> None:
    ready = _provider(tmp_path / "ready-provider")
    broken = _provider(tmp_path / "broken-provider", doctor_exit=2)
    v1 = _manifest(tmp_path / "v1.toml", entrypoint=ready, version="1.0.0")
    v2 = _manifest(tmp_path / "v2.toml", entrypoint=broken, version="2.0.0")
    state_file = tmp_path / "extensions.json"

    installed = install_extension(v1, state_file=state_file, execute=True)
    with pytest.raises(ValueError, match="doctor is not ready"):
        install_extension(
            v2,
            state_file=state_file,
            operation="upgrade",
            execute=True,
        )

    status = extension_status(state_file=state_file)["extensions"][0]
    assert status["active_revision"] == installed["revision"]
    assert status["revision_count"] == 1


def test_failed_enable_remains_disabled_and_rejects_current_artifact(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    disable_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )
    _provider(provider, doctor_exit=2)

    with pytest.raises(ValueError, match="enable doctor is not ready"):
        enable_extension(
            "test-semantic-extension",
            state_file=state_file,
            execute=True,
        )

    entry = json.loads(state_file.read_text(encoding="utf-8"))["extensions"][
        "test-semantic-extension"
    ]
    assert entry["enabled"] is False
    assert extension_status(state_file=state_file)["extensions"][0]["doctor_verified"] is False
    assert "doctor_verified_entrypoint_identity" not in entry


def test_failed_executed_doctor_rejects_current_artifact(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    _provider(provider, doctor_exit=2)

    doctor = doctor_installed_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )

    assert doctor["verified"] is False
    entry = json.loads(state_file.read_text(encoding="utf-8"))["extensions"][
        "test-semantic-extension"
    ]
    assert extension_status(state_file=state_file)["extensions"][0]["doctor_verified"] is False
    assert "doctor_verified_entrypoint_identity" not in entry
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        resolve_extension_binding(
            "test-semantic-extension",
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
            permission="semantic_preference.read",
        )


def test_enabled_extension_doctor_batch_migrates_stale_identity(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    entry = state["extensions"]["test-semantic-extension"]
    entry.pop("doctor_verified_entrypoint_identities")
    entry["doctor_verified_entrypoint_identity"] = "legacy-release-identity"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    preview = doctor_enabled_extensions(state_file=state_file)
    assert preview["ok"] is True
    assert preview["status"] == "probe_required"
    assert preview["enabled_count"] == 1
    assert preview["local_state_write_performed"] is False
    assert (
        json.loads(state_file.read_text(encoding="utf-8"))["extensions"][
            "test-semantic-extension"
        ]["doctor_verified_entrypoint_identity"]
        == "legacy-release-identity"
    )

    repaired = doctor_enabled_extensions(state_file=state_file, execute=True)
    assert repaired["ok"] is True
    assert repaired["status"] == "ready"
    assert repaired["verified_count"] == 1
    assert repaired["blocked_count"] == 0
    assert repaired["external_writes_performed"] is False
    assert (
        resolve_extension_activation(
            "test-semantic-extension",
            state_file=state_file,
        )["doctor_verified"]
        is True
    )


def test_enabled_extension_doctor_batch_keeps_failed_provider_closed(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    _provider(provider, doctor_exit=2)

    repaired = doctor_enabled_extensions(state_file=state_file, execute=True)

    assert repaired["ok"] is False
    assert repaired["status"] == "blocked"
    assert repaired["verified_count"] == 0
    assert repaired["blocked_count"] == 1
    assert repaired["extensions"][0]["failure_kind"] == "probe_nonzero_exit"
    with pytest.raises(ValueError, match="extension doctor .* --execute"):
        resolve_extension_activation(
            "test-semantic-extension",
            state_file=state_file,
        )


def test_executed_doctor_rebinds_revision_only_legacy_proof(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    entry = state["extensions"]["test-semantic-extension"]
    entry.pop("doctor_verified_entrypoint_identities")
    state_file.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match="doctor readiness is stale"):
        resolve_extension_binding(
            "test-semantic-extension",
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
            permission="semantic_preference.read",
        )
    repaired = doctor_installed_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )
    assert repaired["verified"] is True
    assert resolve_extension_binding(
        "test-semantic-extension",
        state_file=state_file,
        capability_id="semantic-preference",
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )["argv"] == [str(provider)]


@pytest.mark.parametrize("replacement", ["missing", "changed"])
def test_binding_rejects_missing_or_replaced_entrypoint(
    tmp_path: Path,
    replacement: str,
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    if replacement == "missing":
        provider.unlink()
    else:
        _provider(provider, doctor_exit=0)
        provider.write_text(
            provider.read_text(encoding="utf-8").replace(
                "Prefer compact validation notes.",
                "A replaced provider must be re-verified.",
            ),
            encoding="utf-8",
        )

    assert (
        extension_status(state_file=state_file)["extensions"][0]["doctor_verified"]
        is False
    )
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        resolve_extension_activation(
            "test-semantic-extension",
            state_file=state_file,
        )
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        resolve_extension_binding(
            "test-semantic-extension",
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
            permission="semantic_preference.read",
        )


def test_runtime_permission_must_be_declared_by_manifest(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'permissions = ["semantic_preference.read"]',
            "permissions = []",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires undeclared permissions"):
        install_extension(manifest, state_file=tmp_path / "extensions.json")


def test_semantic_preference_resolves_enabled_extension(tmp_path: Path) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    state_file = tmp_path / "extensions.json"
    install_extension(manifest, state_file=state_file, execute=True)
    assert (
        resolve_capability_extension_id(
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
        )
        == "test-semantic-extension"
    )
    capability_binding = resolve_capability_binding(
        state_file=state_file,
        capability_id="semantic-preference",
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )
    assert capability_binding["argv"] == [str(provider)]
    catalog = build_capability_catalog_packet(extension_state_file=state_file)
    catalog_provider = next(
        item for item in catalog["providers"] if item["id"] == "test-semantic-extension"
    )
    assert catalog_provider["active_revision"] == capability_binding["revision"]
    assert catalog_provider["ready"] is True
    optional = resolve_optional_capability_binding(
        state_file=state_file,
        extension_id="test-semantic-extension",
        capability_id="semantic-preference",
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )
    assert optional.public_readiness() == {
        "schema_version": "loopx_extension_provider_readiness_v0",
        "status": "ready",
        "extension_id": "test-semantic-extension",
        "installed": True,
        "enabled": True,
        "doctor_verified": True,
        "next_action": None,
    }
    assert optional.binding == capability_binding
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "semantic-preference.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "semantic_preference_hook_config_v0",
                "enabled": True,
                "provider": {
                    "id": "openviking_semantic_preference",
                    "extension_state_file": str(state_file),
                    "args": ["--project", str(project)],
                },
                "surfaces": {
                    "issue_fix.pr_description": {
                        "query": "validation preferences",
                        "limit": 3,
                        "failure_policy": "fail_open",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    doctor = provider_doctor(config, project=project, execute=True)
    assert doctor["status"] == "ready"
    assert doctor["verified"] is True
    result = recall(
        config,
        project=project,
        surface="issue_fix.pr_description",
        execute=True,
    )
    assert result["status"] == "completed"
    assert result["items"][0]["summary"] == "Prefer compact validation notes."

    disable_extension(
        "test-semantic-extension",
        state_file=state_file,
        execute=True,
    )
    unavailable = recall(
        config,
        project=project,
        surface="issue_fix.pr_description",
        execute=True,
    )
    assert unavailable["status"] == "provider_unavailable"
    assert unavailable["failure_kind"] == "extension_binding_unavailable"
    optional = resolve_optional_capability_binding(
        state_file=state_file,
        extension_id="test-semantic-extension",
        capability_id="semantic-preference",
        protocol="semantic_preference_provider_v0",
        permission="semantic_preference.read",
    )
    assert optional.status == "extension_disabled"
    assert optional.binding is None

    detail = build_capability_detail_packet(
        "semantic-preference",
        extension_state_file=state_file,
    )
    assert detail["capability"]["implementation_providers"] == [
        {
            "capability_id": "semantic-preference",
            "protocol": "semantic_preference_provider_v0",
            "provider_id": "test-semantic-extension",
            "provider_version": "1.0.0",
            "provider_state": {
                "declared": True,
                "installed": True,
                "enabled": False,
                "ready": False,
            },
        }
    ]


def test_semantic_preference_config_reuses_strict_extension_id_grammar(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "semantic-preference.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "semantic_preference_hook_config_v0",
                "enabled": True,
                "provider": {
                    "id": "configured_provider",
                    "extension_id": "bad--extension",
                    "extension_state_file": str(tmp_path / "extensions.json"),
                    "args": [],
                },
                "surfaces": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="lower-kebab path segment"):
        provider_doctor(config, project=project)


def test_capability_resolution_ignores_disabled_implementations(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path / "provider")
    state_file = tmp_path / "extensions.json"
    for extension_id in ("first-provider", "second-provider"):
        manifest = _manifest(
            tmp_path / f"{extension_id}.toml",
            entrypoint=provider,
            version="1.0.0",
            extension_id=extension_id,
        )
        install_extension(manifest, state_file=state_file, execute=True)

    with pytest.raises(ValueError, match="multiple enabled, doctor-ready"):
        resolve_capability_extension_id(
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
        )

    disable_extension("first-provider", state_file=state_file, execute=True)
    assert (
        resolve_capability_extension_id(
            state_file=state_file,
            capability_id="semantic-preference",
            protocol="semantic_preference_provider_v0",
        )
        == "second-provider"
    )


def test_extension_cli_installs_preinstalled_runtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    runtime_root = tmp_path / "runtime"

    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "install",
                "--manifest",
                str(manifest),
                "--execute",
            ]
        )
        == 0
    )
    installed = json.loads(capsys.readouterr().out)
    assert installed["changed"] is True

    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "list",
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["extensions"][0]["doctor_verified"] is True

    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "disable",
                "test-semantic-extension",
                "--execute",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "enable",
                "test-semantic-extension",
                "--execute",
            ]
        )
        == 0
    )
    enabled = json.loads(capsys.readouterr().out)
    assert enabled["enabled"] is True
    assert enabled["doctor"]["verified"] is True

    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "doctor",
                "--all-enabled",
                "--execute",
            ]
        )
        == 0
    )
    batch = json.loads(capsys.readouterr().out)
    assert batch["schema_version"] == "loopx_extension_doctor_batch_v0"
    assert batch["status"] == "ready"
    assert batch["verified_count"] == 1


def test_extension_cli_rejects_implements_provider_direct_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = _provider(tmp_path / "provider")
    manifest = _manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        version="1.0.0",
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({"schema_version": "test_extension_request_v0"}),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "install",
                "--manifest",
                str(manifest),
                "--execute",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "run",
                "test-semantic-extension",
                "--input-json",
                str(request_path),
                "--execute",
            ]
        )
        == 2
    )
    error = json.loads(capsys.readouterr().out)
    assert error["schema_version"] == "loopx_extension_error_v0"
    assert error["status"] == "invalid_request"
    assert "capability or domain command" in error["error"]


def test_extension_cli_rejects_bundled_lark_provider_direct_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({"schema_version": "lark_extension_request_v0"}),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "install",
                "--bundled",
                "loopx-lark",
                "--execute",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "extension",
                "run",
                "loopx-lark",
                "--input-json",
                str(request_path),
                "--execute",
            ]
        )
        == 2
    )
    error = json.loads(capsys.readouterr().out)
    assert error["schema_version"] == "loopx_extension_error_v0"
    assert error["status"] == "invalid_request"
    assert "capability or domain command" in error["error"]


def test_core_does_not_import_openviking_provider_implementation() -> None:
    root = Path(__file__).resolve().parents[2] / "loopx"
    forbidden: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[0] == "extensions":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = " ".join(alias.name for alias in node.names)
            if "openviking_semantic_preference" in module:
                forbidden.append(str(relative))
    assert forbidden == []


def test_ordinary_cli_import_does_not_load_openviking_provider() -> None:
    module = "loopx.extensions.openviking_semantic_preference.provider"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; import loopx.cli; assert {module!r} not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_legacy_openviking_alias_matches_provider_argument_contract() -> None:
    def options(
        register: Callable[[argparse.ArgumentParser], None],
    ) -> list[tuple[object, ...]]:
        parser = argparse.ArgumentParser(add_help=False)
        register(parser)
        return [
            (
                tuple(action.option_strings),
                action.dest,
                type(action).__name__,
                action.default,
                action.nargs,
                action.const,
            )
            for action in parser._actions
            if action.option_strings
        ]

    assert options(_register_legacy_openviking_provider_arguments) == options(
        register_openviking_provider_arguments
    )


def test_executable_location_survives_path_changes_upgrade_and_rollback(tmp_path, monkeypatch):
    first_bin, second_bin, unrelated = [tmp_path / name for name in ("first", "second", "unrelated")]
    for directory in (first_bin, second_bin, unrelated):
        directory.mkdir()
    first = _provider(first_bin / "provider")
    second = _provider(second_bin / "provider")
    _provider(unrelated / "provider", doctor_exit=42)
    helper = _provider(first_bin / "companion")
    first.write_text(first.read_text().replace(
        "import json", f"import shutil\nassert shutil.which('companion') == {str(helper)!r}\nimport json",
    ))
    one = _standalone_manifest(tmp_path / "one.toml", entrypoint=Path("provider"))
    two = _standalone_manifest(tmp_path / "two.toml", entrypoint=Path("provider"), version="2.0.0")
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("PATH", str(first_bin))
    installed = install_extension(one, state_file=state_file, execute=True)
    monkeypatch.setenv("PATH", str(unrelated))
    assert doctor_enabled_extensions(state_file=state_file, execute=True)["ok"]
    result = run_standalone_extension(
        "test-standalone-extension", state_file=state_file,
        request={"schema_version": "test_extension_request_v0"}, execute=True,
    )
    assert result["status"] == "succeeded"
    assert str(first_bin) not in json.dumps(installed)
    assert os.environ["PATH"] == str(unrelated)
    monkeypatch.setenv("PATH", str(second_bin))
    install_extension(two, state_file=state_file, operation="upgrade", execute=True)
    monkeypatch.setenv("PATH", str(unrelated))
    assert rollback_extension("test-standalone-extension", state_file=state_file, execute=True)["doctor"]["verified"]
    state = json.loads(state_file.read_text())
    entry = state["extensions"]["test-standalone-extension"]
    assert {r["entrypoint_path"] for r in entry["revisions"]} == {str(first), str(second)}
    assert all(r["manifest"]["runtime"]["entrypoint"] == "provider" for r in entry["revisions"])
    first.unlink()
    missing = doctor_installed_extension("test-standalone-extension", state_file=state_file, execute=True)
    assert missing["status"] == "entrypoint_missing"  # Never switch to the unrelated PATH copy.


def test_executable_location_preserves_launcher_directory_for_sibling_tools(
    tmp_path, monkeypatch,
):
    launcher_bin = tmp_path / "launcher-bin"
    package_bin = tmp_path / "package-bin"
    unrelated = tmp_path / "unrelated"
    for directory in (launcher_bin, package_bin, unrelated):
        directory.mkdir()
    package_provider = _provider(package_bin / "provider")
    launcher_provider = launcher_bin / "provider"
    launcher_provider.symlink_to(package_provider)
    companion = _provider(launcher_bin / "companion")
    package_provider.write_text(package_provider.read_text().replace(
        "import json",
        f"import shutil\nassert shutil.which('companion') == {str(companion)!r}\nimport json",
    ))
    manifest = _standalone_manifest(
        tmp_path / "manifest.toml", entrypoint=Path("provider"),
    )
    state_file = tmp_path / "state.json"

    monkeypatch.setenv("PATH", str(launcher_bin))
    install_extension(manifest, state_file=state_file, execute=True)
    state = json.loads(state_file.read_text())
    revision = state["extensions"]["test-standalone-extension"]["revisions"][0]
    assert revision["entrypoint_path"] == str(launcher_provider)

    monkeypatch.setenv("PATH", str(unrelated))
    assert doctor_installed_extension(
        "test-standalone-extension", state_file=state_file, execute=True,
    )["verified"]
    result = run_standalone_extension(
        "test-standalone-extension", state_file=state_file,
        request={"schema_version": "test_extension_request_v0"}, execute=True,
    )
    assert result["status"] == "succeeded"


def test_legacy_doctor_captures_location_only_after_success(tmp_path, monkeypatch):
    provider = _provider(tmp_path / "provider")
    manifest = _standalone_manifest(tmp_path / "manifest.toml", entrypoint=Path("provider"))
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("PATH", str(tmp_path))
    install_extension(manifest, state_file=state_file, execute=True)
    state = json.loads(state_file.read_text())
    state["extensions"]["test-standalone-extension"]["revisions"][0].pop("entrypoint_path")
    state_file.write_text(json.dumps(state))
    preview = doctor_installed_extension("test-standalone-extension", state_file=state_file)
    assert preview["status"] == "probe_required"
    assert json.loads(state_file.read_text()) == state
    assert doctor_installed_extension("test-standalone-extension", state_file=state_file, execute=True)["verified"]
    monkeypatch.setenv("PATH", "")
    assert doctor_installed_extension("test-standalone-extension", state_file=state_file, execute=True)["verified"]
    provider.write_text(provider.read_text() + "\n# changed artifact\n")
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        resolve_extension_activation("test-standalone-extension", state_file=state_file)


def test_bound_execution_preserves_explicit_environment_and_sibling_tools(tmp_path, monkeypatch):
    provider = _provider(tmp_path / "provider")
    helper = _provider(tmp_path / "companion")
    provider.write_text(provider.read_text().replace(
        "request = json.load(sys.stdin)",
        f"import os, shutil\nassert os.environ.get('EXPLICIT_BASE') == 'fixture'\n"
        f"assert shutil.which('companion') == {str(helper)!r}\nrequest = json.load(sys.stdin)",
    ))
    manifest = _standalone_manifest(
        tmp_path / "manifest.toml", entrypoint=Path("provider"), permission="semantic_preference.read",
    )
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("PATH", str(tmp_path))
    install_extension(manifest, state_file=state_file, execute=True)
    monkeypatch.setenv("PATH", "")
    binding = resolve_extension_runtime_binding(
        "test-standalone-extension", state_file=state_file,
        protocol="semantic_preference_provider_v0", permission="semantic_preference.read",
    )
    environment = {"EXPLICIT_BASE": "fixture", "PATH": ""}
    result = execute_extension_runtime_binding(
        binding, request={"schema_version": "test_extension_request_v0"}, environment=environment,
    )
    assert result["schema_version"] == "semantic_preference_provider_response_v0"
    assert environment == {"EXPLICIT_BASE": "fixture", "PATH": ""}
