from __future__ import annotations

import os
from pathlib import Path

from loopx.capabilities.public_safe_outbound.scanner import scan_paths, scan_texts


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
