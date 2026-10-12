from __future__ import annotations

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
