import json
import plistlib

import pytest

from loopx.desktop_installation import desktop_installation_status


def test_different_source_does_not_require_downgrading_the_cli(tmp_path):
    app = tmp_path / "LoopX.app"
    contents = app / "Contents"
    runtime = contents / "Resources/runtime"
    runtime.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "1.0.2"})
    )
    identity = runtime / "identity.json"
    identity.write_text(
        json.dumps(
            {"schema_version": "desktop_runtime_bundle_v1", "source_revision": "a" * 40}
        )
    )
    mismatch = desktop_installation_status("b" * 40, applications=(app,))
    assert mismatch["status"] == "mismatch"
    # Hash inequality proves neither age nor incompatibility. Keep the existing
    # observation without telling an operator to replace a possibly newer CLI.
    advice = mismatch["recommended_action"]
    assert "does not establish version order or compatibility" in advice
    assert "retain a newer CLI" in advice
    assert "running service identity" in advice
    assert "LOOPX_BIN" in advice
    assert "align the CLI" not in advice
    assert mismatch["running_app_verified"] is False
    assert str(tmp_path) not in json.dumps(mismatch)
    paired = desktop_installation_status("a" * 40, applications=(app,))
    assert paired["status"] == "paired"
    assert paired["running_app_verified"] is False
    assert paired["recommended_action"] is None
    assert desktop_installation_status(None, applications=(app,))["status"] == "unknown"
    identity.write_text("[]")
    assert (
        desktop_installation_status("a" * 40, applications=(app,))["status"]
        == "unknown"
    )


@pytest.mark.parametrize("contents", [None, "[]", "{broken", '{"schema_version":"other"}'])
def test_unreadable_identity_keeps_compatibility_unknown_without_repair(tmp_path, contents):
    app = tmp_path / "LoopX.app"
    runtime = app / "Contents/Resources/runtime"
    runtime.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "1.4.0"})
    )
    identity = runtime / "identity.json"
    if contents is not None:
        identity.write_text(contents)
    before = identity.read_bytes() if identity.exists() else None
    result = desktop_installation_status("a" * 40, applications=(app,))
    assert result["status"] == "unknown"
    assert "compatibility is unknown" in result["recommended_action"]
    assert "startup diagnostics" in result["recommended_action"]
    assert "LOOPX_BIN" in result["recommended_action"]
    assert result["running_app_verified"] is False
    assert (identity.read_bytes() if identity.exists() else None) == before


def test_multiple_app_identities_preserve_each_observation_without_modifying_bundles(tmp_path):
    apps = []
    before = {}
    for name, revision in [("System", "a" * 40), ("User", "b" * 40)]:
        app = tmp_path / name / "LoopX.app"
        runtime = app / "Contents/Resources/runtime"
        runtime.mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleShortVersionString": "1.4.0"})
        )
        identity = runtime / "identity.json"
        identity.write_text(json.dumps({
            "schema_version": "desktop_runtime_bundle_v1", "source_revision": revision,
        }))
        apps.append(app)
        for file in app.rglob("*"):
            if file.is_file():
                before[file] = file.read_bytes()
    result = desktop_installation_status("a" * 40, applications=tuple(apps))
    assert result["status"] == "mismatch"
    assert [row["pairing"] for row in result["apps"]] == ["paired", "mismatch"]
    assert result["running_app_verified"] is False
    assert "does not establish version order or compatibility" in result["recommended_action"]
    assert {file: file.read_bytes() for file in before} == before
    assert str(tmp_path) not in json.dumps(result)


def test_cli_only_host_does_not_require_desktop_installation(tmp_path):
    result = desktop_installation_status(
        "a" * 40, applications=(tmp_path / "missing.app",)
    )
    assert result["status"] == "not_detected"
    assert result["recommended_action"] is None
    assert result["apps"] == []
