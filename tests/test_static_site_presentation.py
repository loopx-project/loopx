from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.presentation.static_site import (
    MANIFEST_FILE,
    RECEIPT_FILE,
    RECEIPT_LOG_FILE,
    REVISION_DIR,
    REVISION_FILE,
    StaticSiteContractError,
    _utc_now,
    package_static_site,
    rollback_static_site,
    verify_static_site_readback,
)

# A real 16x16 AVIF written by Pillow's libavif encoder and read back at that size,
# so the media-compatibility case exercises bytes a browser decodes rather than a
# synthetic payload under a media suffix.
_AVIF_16X16 = base64.b64decode(
    "AAAAIGZ0eXBhdmlmAAAAAGF2aWZtaWYxbWlhZk1BMUIAAADrbWV0YQAAAAAAAAAhaGRscgAA"
    "AAAAAAAAcGljdAAAAAAAAAAAAAAAAAAAAAAOcGl0bQAAAAAAAQAAAB5pbG9jAAAAAEQAAAEAAQ"
    "AAAAEAAAETAAAAOAAAAChpaW5mAAAAAAABAAAAGmluZmUCAAAAAAEAAGF2MDFDb2xvcgAAAABq"
    "aXBycAAAAEtpcGNvAAAAFGlzcGUAAAAAAAAAEAAAABAAAAAQcGl4aQAAAAADCAgIAAAADGF2MU"
    "OBAAwAAAAAE2NvbHJuY2x4AAEADQAGgAAAABdpcG1hAAAAAAAAAAEAAQQBAoMEAAAAQG1kYXQS"
    "AAoJGAz/2iAhoNCAMikXh4eGIYSSSSZQAAAAOtUs5tqInBL0Pog/t6d/UvOhu9plDueWx8dawA=="
)


# A real 16x16 two-frame APNG written by Pillow and read back as two frames, so the
# animation container is exercised at its own `.apng` suffix rather than as a
# renamed still image.
_APNG_16X16 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAACGFjVEwAAAACAAAAAPON"
    "k3AAAAAaZmNUTAAAAAAAAAAQAAAAEAAAAAAAAAAAAAEACgAAY0bPJQAAACNJREFUeJxj"
    "PCEnx0AKYCJJNcOoBuIAE5Hq4GBUAzGA5FACAE8fASTP+Ap+AAAAGmZjVEwAAAABAAAA"
    "EAAAABAAAAAAAAAAAAABAAoAAPg1JfEAAAAnZmRBVAAAAAJ4nGOUkzvBQApgIkk1w6gG"
    "4gATkergYFQDMYDkUAIATcsBJE6yAzwAAAAASUVORK5CYII="
)

# The media containers that must survive the text scan. Each entry is real encoder
# output, which is why the parametrised cases cannot pass on synthetic bytes.
_REAL_MEDIA_ASSETS = (
    pytest.param("assets/hero.avif", _AVIF_16X16, id="avif"),
    pytest.param("assets/spinner.apng", _APNG_16X16, id="apng"),
)


def test_utc_timestamp_uses_the_legacy_compatible_timezone_alias() -> None:
    timestamp = _utc_now()

    assert timestamp.endswith("Z")
    assert "+00:00" not in timestamp


def _site(root: Path, *, body: str = "first") -> Path:
    site = root / "site"
    site.mkdir()
    (site / "index.html").write_text(
        f"<html><body>{body}</body></html>\n", encoding="utf-8"
    )
    (site / "assets").mkdir()
    (site / "assets" / "app.js").write_text(
        f"window.payload = {body!r};\n", encoding="utf-8"
    )
    return site


def _package(
    site: Path, output: Path, state: Path, **overrides: object
) -> dict[str, object]:
    options: dict[str, object] = {
        "site_dir": site,
        "output_dir": output,
        "state_dir": state,
        "site_id": "public-demo",
        "entry_path": "index.html",
        "revision": "rev-a",
        "publisher_kind": "local",
        "base_url": None,
        "desktop_visual_check": "passed",
        "mobile_visual_check": "passed",
        "link_check": "passed",
        "execute": True,
    }
    options.update(overrides)
    return package_static_site(**options)  # type: ignore[arg-type]


def test_package_is_semantically_idempotent_and_rollback_restores_previous_revision(
    tmp_path: Path,
) -> None:
    site = _site(tmp_path)
    output = tmp_path / "publish"
    state = tmp_path / "state"

    first = _package(site, output, state)
    assert first["write_performed"] is True
    assert first["active_revision"] == "rev-a"
    assert (output / "index.html").read_text(encoding="utf-8").find("first") >= 0
    assert (output / REVISION_DIR / "rev-a" / REVISION_FILE).is_file()
    assert (output / RECEIPT_FILE).is_file()

    repeated = _package(site, output, state, revision="rev-alias")
    assert repeated["semantic_noop"] is True
    assert repeated["write_performed"] is False
    assert repeated["active_revision"] == "rev-a"
    assert not (output / REVISION_DIR / "rev-alias").exists()
    assert len((state / RECEIPT_LOG_FILE).read_text(encoding="utf-8").splitlines()) == 1

    (site / "index.html").write_text(
        "<html><body>second</body></html>\n", encoding="utf-8"
    )
    second = _package(site, output, state, revision="rev-b")
    assert second["active_revision"] == "rev-b"
    assert second["previous_revision"] == "rev-a"
    assert "second" in (output / "index.html").read_text(encoding="utf-8")
    assert "first" in (output / REVISION_DIR / "rev-a" / "index.html").read_text(
        encoding="utf-8"
    )

    rolled_back = rollback_static_site(output_dir=output, state_dir=state, execute=True)
    assert rolled_back["active_revision"] == "rev-a"
    assert rolled_back["from_revision"] == "rev-b"
    assert "first" in (output / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((output / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["active_revision"] == "rev-a"
    assert manifest["previous_revision"] == "rev-b"
    assert len((state / RECEIPT_LOG_FILE).read_text(encoding="utf-8").splitlines()) == 3


def test_rollback_rejects_revision_paths_outside_the_retained_revision(
    tmp_path: Path,
) -> None:
    site = _site(tmp_path)
    output = tmp_path / "publish"
    state = tmp_path / "state"
    _package(site, output, state)
    (site / "index.html").write_text("second\n", encoding="utf-8")
    _package(site, output, state, revision="rev-b")

    escaped_content = b"copied from output\n"
    escaped_source = output / "escaped.txt"
    escaped_source.write_bytes(escaped_content)
    victim = tmp_path / "escaped.txt"
    victim.write_text("must remain unchanged\n", encoding="utf-8")
    metadata_path = output / REVISION_DIR / "rev-a" / REVISION_FILE
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["files"] = [
        {
            "path": "../../escaped.txt",
            "sha256": hashlib.sha256(escaped_content).hexdigest(),
            "size": len(escaped_content),
        }
    ]
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(StaticSiteContractError, match="revision file path"):
        rollback_static_site(
            output_dir=output,
            revision="rev-a",
            state_dir=state,
            execute=True,
        )

    assert victim.read_text(encoding="utf-8") == "must remain unchanged\n"


def test_rollback_rejects_retained_revision_content_drift(tmp_path: Path) -> None:
    site = _site(tmp_path)
    output = tmp_path / "publish"
    state = tmp_path / "state"
    _package(site, output, state)
    (site / "index.html").write_text("second\n", encoding="utf-8")
    _package(site, output, state, revision="rev-b")
    (output / REVISION_DIR / "rev-a" / "index.html").write_text(
        "tampered\n", encoding="utf-8"
    )

    with pytest.raises(StaticSiteContractError, match="integrity"):
        rollback_static_site(
            output_dir=output,
            revision="rev-a",
            state_dir=state,
            execute=True,
        )

    assert (output / MANIFEST_FILE).read_text(encoding="utf-8").find(
        '"active_revision": "rev-b"'
    ) >= 0


def test_github_pages_adapter_projects_stable_latest_and_revision_urls(
    tmp_path: Path,
) -> None:
    site = _site(tmp_path)
    payload = _package(
        site,
        tmp_path / "publish",
        tmp_path / "state",
        revision="abc123",
        publisher_kind="github-pages",
        base_url="https://example.github.io/project",
        execute=False,
    )

    publisher = payload["publisher"]
    assert isinstance(publisher, dict)
    assert publisher["latest_url"] == "https://example.github.io/project/"
    assert (
        publisher["revision_url"]
        == "https://example.github.io/project/revisions/abc123/"
    )
    assert publisher["http_readback_required"] is True
    assert payload["write_required"] is True
    assert not (tmp_path / "publish").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("desktop_visual_check", "not-run"),
        ("mobile_visual_check", "failed"),
        ("link_check", "unknown"),
    ],
)
def test_package_requires_visual_and_link_validation_receipts(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    site = _site(tmp_path)
    with pytest.raises(
        StaticSiteContractError, match="requires passed validation receipts"
    ):
        _package(site, tmp_path / "publish", tmp_path / "state", **{field: value})


@pytest.mark.parametrize("prefix", ["sk-proj-", "sk-svcacct-", "sk-admin-", "sk-ant-api03-"])
def test_package_rejects_unlabeled_ai_provider_tokens(tmp_path: Path, prefix: str) -> None:
    site = _site(tmp_path)
    token = prefix + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
    (site / "notes.txt").write_text(f"provider output: {token}\n", encoding="utf-8")
    destination = tmp_path / "publish"

    with pytest.raises(StaticSiteContractError, match="public boundary scan failed"):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


def test_package_scans_large_text_assets_before_certifying_them_public_safe(
    tmp_path: Path,
) -> None:
    site = _site(tmp_path)
    token = "ghp_" + "a" * 36
    large_report = site / "large-report.txt"
    large_report.write_text(
        "A" * 2_000_001 + "\ntoken=" + token + "\n", encoding="utf-8"
    )
    destination = tmp_path / "publish"

    with pytest.raises(
        StaticSiteContractError,
        match="public boundary scan failed for large-report.txt: provider token",
    ):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


@pytest.mark.parametrize(
    ("asset", "payload"),
    [
        ("data.csv", "name,value\napi,{token}\n"),
        ("assets/export.csv", "name,value\napi,{token}\n"),
        ("build.log", "export GITHUB_TOKEN={token}\n"),
        ("config.yaml", "token: {token}\n"),
        (".env", "OPENAI_API_KEY={token}\n"),
        ("Dockerfile", "ENV GITHUB_TOKEN={token}\n"),
        ("app.js.map", '{"sourcesContent": ["const token = \\"{token}\\";"]}'),
        ("tsconfig.tsbuildinfo", '{"token": "{token}"}'),
    ],
)
def test_package_scans_text_assets_outside_the_former_suffix_allow_list(
    tmp_path: Path, asset: str, payload: str
) -> None:
    site = _site(tmp_path)
    token = "ghp_" + "a" * 36
    (site / asset).write_text(payload.replace("{token}", token), encoding="utf-8")
    destination = tmp_path / "publish"

    with pytest.raises(StaticSiteContractError, match="public boundary scan failed"):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


def test_package_scans_a_bom_marked_utf16_capture_before_publication(
    tmp_path: Path,
) -> None:
    site = _site(tmp_path)
    token = "ghp_" + "a" * 36
    (site / "capture.txt").write_text(
        "GITHUB_TOKEN=" + token + "\n", encoding="utf-16"
    )
    destination = tmp_path / "publish"

    with pytest.raises(StaticSiteContractError, match="public boundary scan failed"):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


def test_package_fails_closed_on_bomless_utf16_text(tmp_path: Path) -> None:
    site = _site(tmp_path)
    token = "ghp_" + "a" * 36
    (site / "capture.txt").write_bytes(
        ("GITHUB_TOKEN=" + token + "\n").encode("utf-16-le")
    )
    destination = tmp_path / "publish"

    with pytest.raises(
        StaticSiteContractError, match="content is not reviewable text"
    ):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


def test_package_fails_closed_on_nul_bearing_text(tmp_path: Path) -> None:
    site = _site(tmp_path)
    token = "ghp_" + "a" * 36
    (site / "notes.txt").write_bytes(
        ("GITHUB_TOKEN=" + token + "\x00tail\n").encode("utf-8")
    )
    destination = tmp_path / "publish"

    with pytest.raises(
        StaticSiteContractError, match="content is not reviewable text"
    ):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


def test_package_fails_closed_on_undecodable_text(tmp_path: Path) -> None:
    site = _site(tmp_path)
    token = "ghp_" + "a" * 36
    (site / "notes.txt").write_bytes(
        b"GITHUB_TOKEN=" + token.encode("utf-8") + b"\xff\n"
    )
    destination = tmp_path / "publish"

    with pytest.raises(
        StaticSiteContractError, match="content is not decodable as utf-8"
    ):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


def test_package_still_copies_binary_assets_without_text_scanning(
    tmp_path: Path,
) -> None:
    site = _site(tmp_path)
    (site / "render.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(32)))
    result = _package(site, tmp_path / "publish", tmp_path / "state")

    assert result["write_performed"] is True
    assert (tmp_path / "publish" / "render.png").is_file()


@pytest.mark.parametrize(("asset", "content"), _REAL_MEDIA_ASSETS)
def test_package_copies_a_real_media_asset_byte_for_byte(
    tmp_path: Path, asset: str, content: bytes
) -> None:
    """A browser-decodable container is media, not unreviewable text."""
    site = _site(tmp_path)
    (site / asset).write_bytes(content)
    result = _package(site, tmp_path / "publish", tmp_path / "state")

    assert result["write_performed"] is True
    assert (tmp_path / "publish" / asset).read_bytes() == content
    manifest = json.loads(
        (tmp_path / "publish" / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    files = {str(item["path"]): item for item in manifest["files"]}
    assert files[asset]["sha256"] == hashlib.sha256(content).hexdigest()
    assert files[asset]["size"] == len(content)


@pytest.mark.parametrize(("asset", "content"), _REAL_MEDIA_ASSETS)
def test_package_rejects_a_text_asset_next_to_real_media(
    tmp_path: Path, asset: str, content: bytes
) -> None:
    """The media exemption must not disarm the text boundary in the same bundle."""
    site = _site(tmp_path)
    (site / asset).write_bytes(content)
    token = "ghp_" + "a" * 36
    (site / "assets" / "export.csv").write_text(
        f"name,value\napi,{token}\n", encoding="utf-8"
    )
    destination = tmp_path / "publish"

    with pytest.raises(
        StaticSiteContractError,
        match="public boundary scan failed for assets/export.csv",
    ):
        _package(site, destination, tmp_path / "state")
    assert not destination.exists()


def test_package_rejects_reserved_paths_symlinks_and_public_boundary_leaks(
    tmp_path: Path,
) -> None:
    site = _site(tmp_path)
    (site / "notes.txt").write_text(
        "developer path: /Users/example/private.txt\n", encoding="utf-8"
    )
    with pytest.raises(StaticSiteContractError, match="public boundary scan failed"):
        _package(site, tmp_path / "publish-a", tmp_path / "state-a")

    (site / "notes.txt").unlink()
    (site / MANIFEST_FILE).write_text("{}\n", encoding="utf-8")
    with pytest.raises(StaticSiteContractError, match="reserved path"):
        _package(site, tmp_path / "publish-b", tmp_path / "state-b")

    (site / MANIFEST_FILE).unlink()
    (site / "linked.html").symlink_to(site / "index.html")
    with pytest.raises(StaticSiteContractError, match="must not contain symlinks"):
        _package(site, tmp_path / "publish-c", tmp_path / "state-c")


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


def test_http_readback_retries_then_persists_verified_receipt(tmp_path: Path) -> None:
    site = _site(tmp_path)
    output = tmp_path / "publish"
    state = tmp_path / "state"
    _package(
        site,
        output,
        state,
        revision="remote-a",
        publisher_kind="github-pages",
        base_url="https://example.github.io/project/",
    )
    expected = json.loads((output / RECEIPT_FILE).read_text(encoding="utf-8"))
    wrong = {**expected, "semantic_digest": "sha256:wrong"}
    responses = iter([_Response(wrong), _Response(expected)])

    def opener(_url: str, *, timeout: int) -> _Response:
        assert timeout == 10
        return next(responses)

    verified = verify_static_site_readback(
        output_dir=output,
        state_dir=state,
        retries=2,
        retry_delay_seconds=0,
        execute=True,
        opener=opener,
    )
    assert verified["verified"] is True
    assert verified["attempts"] == 2
    events = [
        json.loads(line)
        for line in (state / RECEIPT_LOG_FILE).read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "http_readback_verified"
    assert (
        events[-1]["receipt_url"]
        == "https://example.github.io/project/.loopx-static-site-receipt.json"
    )


def test_cli_package_preview_is_read_only_and_exposes_publisher_as_first_class_parameter(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = _site(tmp_path)
    output = tmp_path / "publish"
    result = main(
        [
            "--format",
            "json",
            "presentation",
            "package",
            "--site-dir",
            str(site),
            "--output-dir",
            str(output),
            "--site-id",
            "cli-demo",
            "--publisher",
            "github-pages",
            "--base-url",
            "https://example.github.io/cli-demo/",
            "--desktop-visual-check",
            "passed",
            "--mobile-visual-check",
            "passed",
            "--link-check",
            "passed",
        ]
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["publisher"]["kind"] == "github-pages"
    assert output.exists() is False


@pytest.mark.parametrize(("asset", "content"), _REAL_MEDIA_ASSETS)
def test_cli_package_copies_real_media_and_rejects_text_beside_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    asset: str,
    content: bytes,
) -> None:
    """The shipped entrypoint must keep both halves of the boundary: media copies
    byte for byte, and a text asset carrying a credential still fails the bundle."""
    site = _site(tmp_path)
    (site / asset).write_bytes(content)

    def run(output: Path) -> int:
        return main(
            [
                "--format",
                "json",
                "presentation",
                "package",
                "--site-dir",
                str(site),
                "--output-dir",
                str(output),
                "--site-id",
                "media-cli-demo",
                "--desktop-visual-check",
                "passed",
                "--mobile-visual-check",
                "passed",
                "--link-check",
                "passed",
                "--execute",
            ]
        )

    accepted_output = tmp_path / "publish"
    assert run(accepted_output) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["write_performed"] is True
    assert (accepted_output / asset).read_bytes() == content

    token = "ghp_" + "a" * 36
    (site / "assets" / "export.csv").write_text(
        f"name,value\napi,{token}\n", encoding="utf-8"
    )
    rejected_output = tmp_path / "publish-rejected"
    assert run(rejected_output) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["ok"] is False
    assert "public boundary scan failed for assets/export.csv" in rejected["error"]
    assert not rejected_output.exists()
