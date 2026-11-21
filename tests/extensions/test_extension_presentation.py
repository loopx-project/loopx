from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

from loopx.cli import main
from loopx.extensions.presentation import (
    collect_active_extension_presentation_surfaces,
    default_extension_projection_root,
    publish_extension_projection,
    read_extension_projection,
    validate_opaque_presentation_view,
)
from loopx.extensions.runtime import (
    disable_extension,
    doctor_installed_extension,
    enable_extension,
    install_extension,
)


def _valid_view() -> dict[str, object]:
    # Core keeps the `view` opaque unless an extension registers a validator for
    # the declared view_schema. These lifecycle tests use a synthetic,
    # structure-only view so they exercise generic Core behavior without
    # depending on any one domain's schema. Finance owns and tests its own
    # decision-research view in tests/extensions/test_finance_presentation_view.py.
    return {
        "headline": "Synthetic lifecycle view",
        "generated_label": "unchanged",
        "sections": [
            {
                "id": "summary",
                "title": "Summary",
                "body": "Opaque passthrough content for the lifecycle proof.",
            }
        ],
    }


def _provider_projection() -> dict[str, object]:
    return {
        "schema_version": "synthetic_research_packet_v0",
        "presentation_projection": {
            "schema_version": "extension_presentation_projection_v0",
            "surface_id": "investment-research",
            "goal_id": "synthetic-research-goal",
            "generated_at": "2026-01-15T12:00:00+00:00",
            "review_due_at": "2026-02-15T12:00:00+00:00",
            "lineage": {
                "source_id": "synthetic-research-2026-01-15",
                "version": 1,
                "row_lifecycle": "active",
                "supersedes": [],
                "superseded_by": None,
            },
            "view_schema": "synthetic_dashboard_v0",
            "view": _valid_view(),
        },
    }


def _projection_provider(
    path: Path,
    *,
    projection: dict[str, object] | None = None,
    invocation_marker: Path | None = None,
    interpreter: str | Path = sys.executable,
) -> Path:
    response = projection if projection is not None else _provider_projection()
    marker_statement = (
        ""
        if invocation_marker is None
        else f"Path({str(invocation_marker)!r}).write_text('called', encoding='utf-8')\n"
    )
    path.write_text(
        f"""#!{interpreter}
import json
from pathlib import Path
import sys

if "--doctor" in sys.argv:
    raise SystemExit(0)

json.load(sys.stdin)
{marker_statement}json.dump({response!r}, sys.stdout)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _projection_manifest(
    path: Path,
    *,
    entrypoint: Path,
    version: str = "1.0.0",
    visibility: str = "public-safe",
) -> Path:
    path.write_text(
        f"""\
schema_version = "loopx_extension_manifest_v0"
id = "test-research-extension"
version = "{version}"
requires_loopx_api = ">=1,<2"
permissions = []

[runtime]
protocol = "test_research_extension_v0"
entrypoint = {json.dumps(str(entrypoint))}
doctor_args = ["--doctor"]
required_permissions = []
timeout_seconds = 5

[[presentation_surfaces]]
id = "investment-research"
kind = "synthetic_dashboard"
title = "Investment Research"
view_schema = "synthetic_dashboard_v0"
view_validator = "loopx.extensions.presentation:validate_opaque_presentation_view"
visibility = "{visibility}"
empty_state_title = "No validated research yet"
empty_state_detail = "Publish a validated projection."
""",
        encoding="utf-8",
    )
    return path


def _installed_projection_extension(
    tmp_path: Path,
    *,
    projection: dict[str, object] | None = None,
    invocation_marker: Path | None = None,
    visibility: str = "public-safe",
) -> tuple[Path, dict[str, object]]:
    provider = _projection_provider(
        tmp_path / "provider",
        projection=projection,
        invocation_marker=invocation_marker,
    )
    manifest = _projection_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
        visibility=visibility,
    )
    state_file = tmp_path / "runtime" / "extensions" / "state.json"
    installed = install_extension(manifest, state_file=state_file, execute=True)
    return state_file, installed


def _set_view_validator(state_file: Path, reference: str) -> None:
    """Point the installed surface at another declared validator."""

    state = json.loads(state_file.read_text(encoding="utf-8"))
    surface = state["extensions"]["test-research-extension"]["revisions"][0][
        "manifest"
    ]["presentation_surfaces"][0]
    surface["view_validator"] = reference
    state_file.write_text(json.dumps(state), encoding="utf-8")


def _verify_installed_extension(state_file: Path) -> None:
    doctor = doctor_installed_extension(
        "test-research-extension",
        state_file=state_file,
        execute=True,
    )
    assert doctor["verified"]


def test_projection_publication_dry_run_does_not_invoke_or_write(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "provider-called"
    state_file, installed = _installed_projection_extension(
        tmp_path,
        invocation_marker=marker,
    )

    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
    )

    assert receipt == {
        "ok": True,
        "schema_version": "extension_projection_publish_receipt_v0",
        "operation": "publish_projection",
        "extension_id": "test-research-extension",
        "surface_id": "investment-research",
        "revision": installed["revision"],
        "dry_run": True,
        "executed": False,
        "status": "ready",
    }
    assert not marker.exists()
    assert not default_extension_projection_root(state_file).exists()


def test_projection_publication_binds_revision_hash_and_exact_readback(
    tmp_path: Path,
) -> None:
    state_file, installed = _installed_projection_extension(tmp_path)

    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )

    projection_file = (
        default_extension_projection_root(state_file)
        / "test-research-extension"
        / "investment-research.json"
    )
    persisted = json.loads(projection_file.read_text(encoding="utf-8"))
    assert receipt["ok"] is True
    assert receipt["status"] == "published"
    assert receipt["revision"] == installed["revision"]
    assert receipt["payload_sha256"] == persisted["payload_sha256"]
    assert receipt["readback_verified"] is True
    assert persisted["schema_version"] == "extension_projection_surface_v0"
    assert persisted["extension_id"] == "test-research-extension"
    assert persisted["extension_revision"] == installed["revision"]
    assert persisted["surface_id"] == "investment-research"
    assert persisted["visibility"] == "public-safe"
    assert persisted["view"]["headline"] == "Synthetic lifecycle view"
    assert projection_file.stat().st_mode & 0o777 == 0o600
    assert not list(projection_file.parent.glob(".*.tmp.*"))


def test_failed_projection_publication_preserves_previous_file(
    tmp_path: Path,
) -> None:
    state_file, _ = _installed_projection_extension(tmp_path)
    first = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    projection_file = (
        default_extension_projection_root(state_file)
        / "test-research-extension"
        / "investment-research.json"
    )
    previous_bytes = projection_file.read_bytes()

    invalid = _provider_projection()
    invalid["presentation_projection"]["view"]["sections"][0][
        "api_key"
    ] = "forbidden-secret"
    _projection_provider(tmp_path / "provider", projection=invalid)
    doctor_installed_extension(
        "test-research-extension",
        state_file=state_file,
        execute=True,
    )

    with pytest.raises(ValueError, match="forbidden key"):
        publish_extension_projection(
            "test-research-extension",
            "investment-research",
            state_file=state_file,
            request={"schema_version": "synthetic_request_v0"},
            execute=True,
        )

    assert projection_file.read_bytes() == previous_bytes
    assert json.loads(previous_bytes)["payload_sha256"] == first["payload_sha256"]


def test_projection_publication_rejects_canonical_root_escape(
    tmp_path: Path,
) -> None:
    state_file, _ = _installed_projection_extension(tmp_path)
    projection_root = default_extension_projection_root(state_file)
    projection_root.mkdir(parents=True)
    escaped_root = tmp_path / "escaped-projections"
    escaped_root.mkdir()
    (projection_root / "test-research-extension").symlink_to(
        escaped_root,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="canonical projection root"):
        publish_extension_projection(
            "test-research-extension",
            "investment-research",
            state_file=state_file,
            request={"schema_version": "synthetic_request_v0"},
            execute=True,
        )

    assert not (escaped_root / "investment-research.json").exists()


def test_projection_publication_rejects_undeclared_surface(
    tmp_path: Path,
) -> None:
    state_file, _ = _installed_projection_extension(tmp_path)

    with pytest.raises(ValueError, match="does not declare presentation surface"):
        publish_extension_projection(
            "test-research-extension",
            "other-surface",
            state_file=state_file,
            request={"schema_version": "synthetic_request_v0"},
            execute=True,
        )


def test_projection_publication_dry_run_skips_validator_loading(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "provider-called"
    state_file, _ = _installed_projection_extension(
        tmp_path,
        invocation_marker=marker,
    )
    _set_view_validator(state_file, "missing_validator_module:validate_view")
    # The declaration is part of the verified runtime identity, so re-running the
    # doctor is what lets this surface reach its own validator error.
    _verify_installed_extension(state_file)

    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=False,
    )
    assert receipt["ok"]
    assert receipt["dry_run"]
    assert not receipt["executed"]
    assert not marker.exists()


def test_projection_publication_fails_when_validator_unavailable_on_execute(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "provider-called"
    state_file, _ = _installed_projection_extension(
        tmp_path,
        invocation_marker=marker,
    )
    _set_view_validator(state_file, "missing_validator_module:validate_view")
    # The declaration is part of the verified runtime identity, so re-running the
    # doctor is what lets this surface reach its own validator error.
    _verify_installed_extension(state_file)

    with pytest.raises(ValueError, match="view_validator .* is unavailable"):
        publish_extension_projection(
            "test-research-extension",
            "investment-research",
            state_file=state_file,
            request={"schema_version": "synthetic_request_v0"},
            execute=True,
        )

    assert not marker.exists()


def test_projection_publication_requires_fresh_doctor_after_declaration_change(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "provider-called"
    state_file, _ = _installed_projection_extension(
        tmp_path,
        invocation_marker=marker,
    )
    _set_view_validator(state_file, "missing_validator_module:validate_view")

    # The runtime's verified executable contract changed: the declaration now
    # names a different implementation, so the old doctor proof cannot be reused
    # and nothing may execute against the unverified declaration.
    with pytest.raises(ValueError, match="doctor readiness is stale"):
        publish_extension_projection(
            "test-research-extension",
            "investment-research",
            state_file=state_file,
            request={"schema_version": "synthetic_request_v0"},
            execute=True,
        )

    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX console-script shebang fixture")
def test_projection_publication_loads_validator_from_isolated_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "provider-runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(runtime_root)
    runtime_python = runtime_root / "bin" / "python"
    purelib = subprocess.run(
        [
            str(runtime_python),
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    validator_module = Path(purelib) / "isolated_presentation_validator.py"
    validator_module.write_text(
        "def validate_view(value):\n"
        "    if value.get('runtime_marker') != 'isolated':\n"
        "        raise ValueError('isolated validator rejected view')\n"
        "    return value\n",
        encoding="utf-8",
    )
    projection = _provider_projection()
    projection["presentation_projection"]["view"]["runtime_marker"] = "isolated"
    provider = _projection_provider(
        runtime_root / "bin" / "isolated-provider",
        projection=projection,
        interpreter=runtime_python,
    )
    manifest = _projection_manifest(
        tmp_path / "extension.toml",
        entrypoint=provider,
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "loopx.extensions.presentation:validate_opaque_presentation_view",
            "isolated_presentation_validator:validate_view",
        ),
        encoding="utf-8",
    )
    state_file = tmp_path / "runtime" / "extensions" / "state.json"
    install_extension(manifest, state_file=state_file, execute=True)
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "isolated_presentation_validator.py").write_text(
        "raise RuntimeError('ambient PYTHONPATH must not be imported')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(ambient))
    assert str(Path(purelib)) not in sys.path

    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )

    assert receipt["status"] == "published"
    envelope = read_extension_projection(
        state_file=state_file,
        extension_id="test-research-extension",
        surface_id="investment-research",
        extension_revision=str(receipt["revision"]),
        payload_sha256=str(receipt["payload_sha256"]),
    )
    assert envelope["view"]["runtime_marker"] == "isolated"


def test_active_presentation_surfaces_project_empty_ready_and_review_due(
    tmp_path: Path,
) -> None:
    state_file, installed = _installed_projection_extension(tmp_path)

    empty = collect_active_extension_presentation_surfaces(
        state_file=state_file,
        now=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )
    assert empty["count"] == 1
    assert empty["empty_count"] == 1
    assert empty["items"][0]["state"] == "empty"
    assert "view" not in empty["items"][0]
    assert "detail_ref" not in empty["items"][0]

    publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    projection_file = (
        default_extension_projection_root(state_file)
        / "test-research-extension"
        / "investment-research.json"
    )
    persisted = json.loads(projection_file.read_text(encoding="utf-8"))
    ready = collect_active_extension_presentation_surfaces(
        state_file=state_file,
        now=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )
    assert ready["ready_count"] == 1
    assert ready["items"][0]["state"] == "ready"
    # Status is a compact index: it carries a content-addressed pointer to the
    # published projection, never the full (provider-owned) view inline.
    assert "view" not in ready["items"][0]
    assert ready["items"][0]["detail_ref"] == {
        "extension_id": "test-research-extension",
        "surface_id": "investment-research",
        "extension_revision": installed["revision"],
        "payload_sha256": persisted["payload_sha256"],
    }

    review_due = collect_active_extension_presentation_surfaces(
        state_file=state_file,
        now=datetime(2026, 2, 16, tzinfo=timezone.utc),
    )
    assert review_due["review_due_count"] == 1
    assert review_due["items"][0]["state"] == "review_due"
    assert "view" not in review_due["items"][0]
    assert review_due["items"][0]["detail_ref"]["payload_sha256"] == (
        persisted["payload_sha256"]
    )


def test_active_presentation_surfaces_hide_disabled_or_stale_extension(
    tmp_path: Path,
) -> None:
    state_file, _ = _installed_projection_extension(tmp_path)
    publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )

    disable_extension(
        "test-research-extension",
        state_file=state_file,
        execute=True,
    )
    assert collect_active_extension_presentation_surfaces(
        state_file=state_file
    )["items"] == []

    enable_extension(
        "test-research-extension",
        state_file=state_file,
        execute=True,
    )
    assert collect_active_extension_presentation_surfaces(
        state_file=state_file,
        now=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )["ready_count"] == 1

    provider = tmp_path / "provider"
    provider.write_text(
        provider.read_text(encoding="utf-8") + "# changed provider content\n",
        encoding="utf-8",
    )
    assert collect_active_extension_presentation_surfaces(
        state_file=state_file
    )["items"] == []


def test_active_presentation_surfaces_fail_closed_on_current_file_corruption(
    tmp_path: Path,
) -> None:
    state_file, _ = _installed_projection_extension(tmp_path)
    publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    projection_file = (
        default_extension_projection_root(state_file)
        / "test-research-extension"
        / "investment-research.json"
    )
    persisted = json.loads(projection_file.read_text(encoding="utf-8"))
    persisted["view"]["headline"] = "forged"
    projection_file.write_text(json.dumps(persisted), encoding="utf-8")

    collection = collect_active_extension_presentation_surfaces(
        state_file=state_file
    )

    assert collection["invalid_count"] == 1
    assert collection["items"][0]["state"] == "invalid"
    assert collection["items"][0]["diagnostic"] == "projection_hash_invalid"
    assert "view" not in collection["items"][0]
    assert "detail_ref" not in collection["items"][0]


def test_active_presentation_surfaces_do_not_reuse_old_revision_projection(
    tmp_path: Path,
) -> None:
    state_file, installed = _installed_projection_extension(tmp_path)
    publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    manifest_v2 = _projection_manifest(
        tmp_path / "extension-v2.toml",
        entrypoint=tmp_path / "provider",
        version="2.0.0",
    )
    upgraded = install_extension(
        manifest_v2,
        state_file=state_file,
        operation="upgrade",
        execute=True,
    )
    assert upgraded["revision"] != installed["revision"]

    collection = collect_active_extension_presentation_surfaces(
        state_file=state_file
    )

    assert collection["empty_count"] == 1
    assert collection["items"][0]["state"] == "empty"
    assert collection["items"][0]["extension_revision"] == upgraded["revision"]


def test_publish_projection_cli_supports_dry_run_and_execute(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_file, installed = _installed_projection_extension(tmp_path)
    request_file = tmp_path / "request.json"
    request_file.write_text(
        json.dumps({"schema_version": "synthetic_request_v0"}),
        encoding="utf-8",
    )
    arguments = [
        "--format",
        "json",
        "extension",
        "publish-projection",
        "test-research-extension",
        "investment-research",
        "--state-file",
        str(state_file),
        "--input-json",
        str(request_file),
    ]

    assert main(arguments) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "ready"
    assert preview["executed"] is False

    assert main([*arguments, "--execute"]) == 0
    published = json.loads(capsys.readouterr().out)
    assert published["status"] == "published"
    assert published["revision"] == installed["revision"]
    assert published["readback_verified"] is True


def _publish_for_read(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    state_file, installed = _installed_projection_extension(tmp_path)
    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    return state_file, installed, receipt


def test_read_extension_projection_returns_full_view_by_detail_ref(
    tmp_path: Path,
) -> None:
    state_file, installed, receipt = _publish_for_read(tmp_path)

    envelope = read_extension_projection(
        state_file=state_file,
        extension_id="test-research-extension",
        surface_id="investment-research",
        extension_revision=str(installed["revision"]),
        payload_sha256=str(receipt["payload_sha256"]),
    )

    assert envelope["extension_id"] == "test-research-extension"
    assert envelope["extension_revision"] == installed["revision"]
    assert envelope["surface_id"] == "investment-research"
    assert envelope["payload_sha256"] == receipt["payload_sha256"]
    assert envelope["view"]["headline"] == "Synthetic lifecycle view"


def test_read_extension_projection_rejects_hash_mismatch(tmp_path: Path) -> None:
    state_file, installed, _ = _publish_for_read(tmp_path)

    with pytest.raises(ValueError, match="payload_sha256 does not match"):
        read_extension_projection(
            state_file=state_file,
            extension_id="test-research-extension",
            surface_id="investment-research",
            extension_revision=str(installed["revision"]),
            payload_sha256="f" * 64,
        )


def test_read_extension_projection_rejects_owner_only_surface(
    tmp_path: Path,
) -> None:
    state_file, installed = _installed_projection_extension(
        tmp_path,
        visibility="owner-only",
    )
    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )

    with pytest.raises(
        ValueError,
        match="owner-only projection reads require an authenticated audience",
    ):
        read_extension_projection(
            state_file=state_file,
            extension_id="test-research-extension",
            surface_id="investment-research",
            extension_revision=str(installed["revision"]),
            payload_sha256=str(receipt["payload_sha256"]),
        )


def test_read_extension_projection_rejects_stale_revision(tmp_path: Path) -> None:
    state_file, _installed, receipt = _publish_for_read(tmp_path)

    with pytest.raises(ValueError, match="extension_revision does not match"):
        read_extension_projection(
            state_file=state_file,
            extension_id="test-research-extension",
            surface_id="investment-research",
            extension_revision="0000000000000000",
            payload_sha256=str(receipt["payload_sha256"]),
        )


def test_read_extension_projection_rejects_undeclared_surface(
    tmp_path: Path,
) -> None:
    state_file, installed, receipt = _publish_for_read(tmp_path)

    with pytest.raises(ValueError, match="does not declare presentation surface"):
        read_extension_projection(
            state_file=state_file,
            extension_id="test-research-extension",
            surface_id="other-surface",
            extension_revision=str(installed["revision"]),
            payload_sha256=str(receipt["payload_sha256"]),
        )


def test_read_extension_projection_rejects_missing_projection(
    tmp_path: Path,
) -> None:
    state_file, installed = _installed_projection_extension(tmp_path)

    with pytest.raises(ValueError, match="no published projection"):
        read_extension_projection(
            state_file=state_file,
            extension_id="test-research-extension",
            surface_id="investment-research",
            extension_revision=str(installed["revision"]),
            payload_sha256="a" * 64,
        )


def test_read_extension_projection_rejects_path_escaping_ids(tmp_path: Path) -> None:
    state_file, installed, receipt = _publish_for_read(tmp_path)

    with pytest.raises(ValueError, match="lower-kebab path segment"):
        read_extension_projection(
            state_file=state_file,
            extension_id="../../etc",
            surface_id="investment-research",
            extension_revision=str(installed["revision"]),
            payload_sha256=str(receipt["payload_sha256"]),
        )


def test_publish_projection_dry_run_does_not_load_validator(
    tmp_path: Path,
) -> None:
    """Regression: dry-run must not import extension-owned validator module."""
    provider_marker = tmp_path / "provider_marker"
    state_file, installed = _installed_projection_extension(
        tmp_path,
        invocation_marker=provider_marker,
    )

    receipt = publish_extension_projection(
        "test-research-extension",
        surface_id="investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=False,
    )
    assert receipt["ok"] is True
    assert receipt["dry_run"] is True
    assert receipt["executed"] is False
    assert not provider_marker.exists(), (
        "dry-run must not invoke provider"
    )
    assert not default_extension_projection_root(state_file).exists(), (
        "dry-run must not write projection"
    )


LOCAL_PATH_VIEW_VALUES = (
    "see file:///Users/alice/goal.md",
    "path:/Users/alice/goal.md",
    "///tmp/a.log",
    "//Users/alex/x",
    "x=/private/a",
    "/home/",
    "///var/",
    "C:\\",
)


@pytest.mark.parametrize("value", LOCAL_PATH_VIEW_VALUES)
def test_opaque_view_rejects_every_local_path_shape(value: str) -> None:
    # The migration must not loosen presentation's historical reject side. Each
    # row was either accepted here before the migration (the first three) or
    # rejected by the old local regex (the last two); all five now come from the
    # shared owner.
    with pytest.raises(ValueError, match="must not contain a local path"):
        validate_opaque_presentation_view({"headline": value})


def test_opaque_view_keeps_ordinary_remote_urls_and_prose() -> None:
    view = {
        "headline": "https://example.com/docs",
        "body": "the token budget is 1200",
    }
    assert validate_opaque_presentation_view(view) == view


PUBLIC_URL_VIEW_VALUES = (
    "https://docs.example.test./home/overview",
    "https://[2001:db8::1]/home/overview",
    "https://docs.example.com/?next=/home/overview",
)


@pytest.mark.parametrize("value", PUBLIC_URL_VIEW_VALUES)
def test_opaque_view_keeps_http_links_with_path_shaped_url_parts(value: str) -> None:
    assert validate_opaque_presentation_view({"headline": value}) == {
        "headline": value
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX console-script shebang fixture")
def test_projection_publication_rejects_local_path_without_rewriting(
    tmp_path: Path,
) -> None:
    state_file, _ = _installed_projection_extension(tmp_path)
    first = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    projection_file = (
        default_extension_projection_root(state_file)
        / "test-research-extension"
        / "investment-research.json"
    )
    previous_bytes = projection_file.read_bytes()

    invalid = _provider_projection()
    projection = invalid["presentation_projection"]
    assert isinstance(projection, dict)
    view = projection["view"]
    assert isinstance(view, dict)
    view["headline"] = "see file:///Users/alice/goal.md"
    _projection_provider(tmp_path / "provider", projection=invalid)
    doctor_installed_extension(
        "test-research-extension",
        state_file=state_file,
        execute=True,
    )

    with pytest.raises(ValueError, match="must not contain a local path"):
        publish_extension_projection(
            "test-research-extension",
            "investment-research",
            state_file=state_file,
            request={"schema_version": "synthetic_request_v0"},
            execute=True,
        )

    assert projection_file.read_bytes() == previous_bytes
    assert json.loads(previous_bytes)["payload_sha256"] == first["payload_sha256"]


@pytest.mark.parametrize("value", ("/home/", "///var/", "C:\\"))
@pytest.mark.skipif(os.name == "nt", reason="POSIX console-script shebang fixture")
def test_projection_publication_rejects_directory_roots_without_rewriting(
    tmp_path: Path,
    value: str,
) -> None:
    state_file, _ = _installed_projection_extension(tmp_path)
    first = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    projection_file = (
        default_extension_projection_root(state_file)
        / "test-research-extension"
        / "investment-research.json"
    )
    previous_bytes = projection_file.read_bytes()

    invalid = _provider_projection()
    projection = invalid["presentation_projection"]
    assert isinstance(projection, dict)
    view = projection["view"]
    assert isinstance(view, dict)
    view["headline"] = value
    _projection_provider(tmp_path / "provider", projection=invalid)
    doctor_installed_extension(
        "test-research-extension",
        state_file=state_file,
        execute=True,
    )

    with pytest.raises(ValueError, match="must not contain a local path"):
        publish_extension_projection(
            "test-research-extension",
            "investment-research",
            state_file=state_file,
            request={"schema_version": "synthetic_request_v0"},
            execute=True,
        )

    assert projection_file.read_bytes() == previous_bytes
    assert json.loads(previous_bytes)["payload_sha256"] == first["payload_sha256"]


@pytest.mark.parametrize("value", PUBLIC_URL_VIEW_VALUES)
@pytest.mark.skipif(os.name == "nt", reason="POSIX console-script shebang fixture")
def test_projection_publication_accepts_http_link_and_reads_it_back(
    tmp_path: Path,
    value: str,
) -> None:
    state_file, installed = _installed_projection_extension(tmp_path)
    accepted = _provider_projection()
    projection = accepted["presentation_projection"]
    assert isinstance(projection, dict)
    view = projection["view"]
    assert isinstance(view, dict)
    view["headline"] = value
    _projection_provider(tmp_path / "provider", projection=accepted)
    doctor_installed_extension(
        "test-research-extension",
        state_file=state_file,
        execute=True,
    )

    receipt = publish_extension_projection(
        "test-research-extension",
        "investment-research",
        state_file=state_file,
        request={"schema_version": "synthetic_request_v0"},
        execute=True,
    )
    assert receipt["status"] == "published"
    assert receipt["readback_verified"] is True

    envelope = read_extension_projection(
        state_file=state_file,
        extension_id="test-research-extension",
        surface_id="investment-research",
        extension_revision=str(installed["revision"]),
        payload_sha256=str(receipt["payload_sha256"]),
    )
    assert envelope["view"]["headline"] == value
