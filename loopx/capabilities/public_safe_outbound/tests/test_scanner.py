from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from loopx.capabilities.public_safe_outbound.scanner import (
    compact_json,
    scan_paths,
    scan_texts,
)


def test_detects_credentials() -> None:
    # build the sample at runtime so the source stays clean of the literal pattern
    fake_key = "ark" + "-11111111-2222-3333-4444-555555555555-00000"
    assignment = "api" + "_key = '" + fake_key + "'"
    res = scan_texts({"a": assignment})
    assert res.ok is False
    assert any(h.rule_id in {"ark_api_key", "api_key_assignment"} for h in res.hits)


def test_detects_internal_domain(tmp_path: Path) -> None:
    markers = tmp_path / "markers.json"
    markers.write_text('{"internal_domains": ["corp.example"]}', encoding="utf-8")
    os.environ["PUBLIC_SAFE_CONFIG"] = str(markers)
    try:
        res = scan_texts({"b": "ref: https://code.corp.example/internal/repo"})
    finally:
        os.environ.pop("PUBLIC_SAFE_CONFIG", None)
    assert res.ok is False
    assert any(h.rule_id == "internal_domain" for h in res.hits)


def test_detects_private_path() -> None:
    private = "/" + "home/example-user/code-reading/some_workspace"
    res = scan_texts({"c": "run from " + private})
    assert res.ok is False
    assert any(h.rule_id == "private_abs_path" for h in res.hits)


def test_detects_native_windows_private_path() -> None:
    # The same private path in the spellings a Windows host produces: a drive
    # letter and an UNC share. Assembled at runtime so the source stays clean of
    # the literal pattern.
    separator = "\\"
    drive = separator.join(["C:", "Users", "example-user", "code-reading", "some_workspace"])
    unc = (
        separator
        + separator.join(["", "fileserver", "Users", "example-user", "code-reading", "some_workspace"])
    )
    for label, private in (("drive", drive), ("unc", unc)):
        res = scan_texts({label: "run from " + private})
        assert res.ok is False, label
        assert any(h.rule_id == "private_abs_path" for h in res.hits), label


def test_detects_escaped_and_mixed_windows_private_path() -> None:
    # Serialized text doubles each backslash, and a Windows host mixes
    # separators when a POSIX-style segment meets a native one; both are the
    # same private path and must be a hit, like the boundary scan sibling
    # (#6161). Assembled at runtime so the source stays clean of the literal
    # pattern.
    slash = "/"
    sep = "\\"
    doubled = sep + sep
    parts = ["Users", "example-user", "code-reading", "some_workspace"]
    cases = {
        "escaped-drive": "C:" + doubled + doubled.join(parts),
        "escaped-unc": doubled + doubled + "fileserver" + doubled + "Users" + doubled + "example-user" + doubled,
        "mixed-drive": "C:" + sep + slash.join(parts),
        "mixed-unc": slash + slash + "fileserver" + sep + "Users" + sep + "example-user" + sep,
        "rooted-native": sep + "Users" + sep + "example-user" + sep,
    }
    for label, private in cases.items():
        res = scan_texts({label: '{"cwd": "' + private + '"}'})
        assert res.ok is False, label
        assert any(h.rule_id == "private_abs_path" for h in res.hits), label


def test_native_windows_path_outside_the_private_roots_stays_clean() -> None:
    # The rule keeps the same root keywords as the POSIX spelling carried, so
    # an ordinary drive path that is not under a private root must stay clean
    # and cannot block a submission on its own.
    separator = "\\"
    public_path = separator.join(["C:", "Projects", "public-demo", "build"])
    res = scan_texts({"d": "build output lives under " + public_path})
    assert res.ok is True


def test_clean_text_passes(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text(
        "Public-safe note: compare baseline vs treatment on the same model/task/verifier.",
        encoding="utf-8",
    )
    res = scan_paths([tmp_path / "ok.md"])
    assert res.ok is True


def test_unset_config_keeps_the_generic_rules_only(tmp_path: Path) -> None:
    """No configured markers is the documented no-op, not an error."""
    os.environ.pop("PUBLIC_SAFE_CONFIG", None)
    res = scan_texts({"a": "public-safe note about baselines"})
    assert res.ok is True
    assert res.unscanned == []


def test_unreadable_configured_markers_fail_closed(tmp_path: Path) -> None:
    """A configured file that cannot be read must not degrade to generic rules."""
    os.environ["PUBLIC_SAFE_CONFIG"] = str(tmp_path / "absent-markers.json")
    try:
        res = scan_texts({"a": "public-safe note about baselines"})
    finally:
        os.environ.pop("PUBLIC_SAFE_CONFIG", None)
    assert res.ok is False
    assert res.unscanned == ["config_unreadable"]
    assert "config_unreadable" in compact_json(res)


def test_invalid_configured_markers_fail_closed(tmp_path: Path) -> None:
    markers = tmp_path / "broken.json"
    markers.write_text("{not json", encoding="utf-8")
    os.environ["PUBLIC_SAFE_CONFIG"] = str(markers)
    try:
        res = scan_texts({"a": "public-safe note about baselines"})
    finally:
        os.environ.pop("PUBLIC_SAFE_CONFIG", None)
    assert res.ok is False
    assert res.unscanned == ["config_unreadable"]


def test_valid_configured_markers_still_scan(tmp_path: Path) -> None:
    markers = tmp_path / "markers.json"
    markers.write_text(
        json.dumps({"internal_domains": ["corp.example"]}), encoding="utf-8"
    )
    os.environ["PUBLIC_SAFE_CONFIG"] = str(markers)
    try:
        res = scan_texts({"a": "ref: https://code.corp.example/internal/repo"})
    finally:
        os.environ.pop("PUBLIC_SAFE_CONFIG", None)
    assert res.ok is False
    assert res.unscanned == []
    assert any(h.rule_id == "internal_domain" for h in res.hits)


def test_oversize_requested_file_fails_closed(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text("x" * 64, encoding="utf-8")
    res = scan_paths([big], limit_bytes=16)
    assert res.ok is False
    assert res.unscanned == ["content_too_large"]


def test_unreadable_requested_file_fails_closed(tmp_path: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses file mode checks")
    private = tmp_path / "unreadable.md"
    private.write_text("public-safe note", encoding="utf-8")
    private.chmod(0o000)
    try:
        res = scan_paths([private])
    finally:
        private.chmod(0o600)
    assert res.ok is False
    assert res.unscanned == ["content_unreadable"]
    assert str(private) not in compact_json(res)


def test_directory_entries_are_not_unscanned(tmp_path: Path) -> None:
    """A path that names no file is skipped, not reported as unchecked content."""
    res = scan_paths([tmp_path])
    assert res.ok is True
    assert res.unscanned == []


def test_absolute_scan_root_never_labels_an_absolute_path(tmp_path: Path) -> None:
    """The receipt is public-safe: a hit names a file relative to the root."""
    secret = tmp_path / "nested" / "secret.md"
    secret.parent.mkdir()
    secret.write_text(
        'token = "' + "sk" + "-abcdefghijklmnopqrstuvwxyz012345" + '"',
        encoding="utf-8",
    )
    res = scan_paths([secret], root=tmp_path)
    assert res.ok is False
    assert [hit.location for hit in res.hits] == ["nested/secret.md"]
    payload = compact_json(res)
    assert str(tmp_path) not in payload
    assert str(secret) not in payload


def test_absolute_path_without_a_root_is_reduced_to_its_name(tmp_path: Path) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text('token = "' + "sk" + "-abcdefghijklmnopqrstuvwxyz012345" + '"', encoding="utf-8")
    res = scan_paths([secret])
    assert [hit.location for hit in res.hits] == ["secret.md"]
    assert str(tmp_path) not in compact_json(res)


def test_hit_sample_is_a_redacted_window_not_the_file_head() -> None:
    """`sample_masked` must locate the match, not echo the file's opening."""
    opening = "OPENING-MARKER the very first line of the file\n"
    filler = opening + ("public line\n" * 40)
    body = "api" + "_key = '" + "sk" + "-abcdefghijklmnopqrstuvwxyz012345" + "'"
    res = scan_texts({"a": filler + body})
    assert res.ok is False
    sample = res.to_public_dict()["hits"][0]["sample_masked"]
    # the matched secret is redacted
    assert "abcdefghijklmnopqrstuvwxyz" not in sample
    assert "***" in sample
    # the window is bounded, not the whole text
    assert len(sample) < len(filler + body)
    # and it does not start from the file head
    assert "OPENING-MARKER" not in sample


def test_missing_match_falls_back_to_a_masked_prefix() -> None:
    res = scan_texts({"a": "nothing sensitive here"})
    assert res.ok is True
    from loopx.capabilities.public_safe_outbound.scanner import _redact

    assert _redact("short", "absent") == "***"
