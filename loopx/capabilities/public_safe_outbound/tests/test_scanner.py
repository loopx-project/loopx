from __future__ import annotations

import codecs
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


def test_detects_common_private_home_path_variants() -> None:
    # Home paths are often copied from an environment variable or config value:
    # they may end at the account segment, use a non-ASCII account name, or use
    # the case-insensitive spelling a Windows host accepts. Each is the same
    # private root the rule already names, not a new policy.
    slash = "/"
    sep = "\\"
    user = "示例用户"
    cases = {
        "posix-home-value": slash + "home" + slash + "example-user",
        "windows-home-value": "C:" + sep + "Users" + sep + "example-user",
        "non-ascii-user": "C:" + sep + "Users" + sep + user + sep + "some_workspace",
        "lowercase-root": "c:" + sep + "users" + sep + "example-user" + sep + "some_workspace",
    }
    for label, private in cases.items():
        res = scan_texts({label: "value=" + private})
        assert res.ok is False, label
        assert any(h.rule_id == "private_abs_path" for h in res.hits), label


def test_private_root_without_an_account_segment_stays_clean() -> None:
    # Dropping the trailing separator must not turn the root keyword itself or
    # a relative path that merely contains it into a private absolute path.
    sep = "\\"
    public_values = (
        "/home/",
        "home/example-user",
        "C:" + sep + "Users" + sep,
        "https://example.com/home/",
    )
    for value in public_values:
        res = scan_texts({"public": "value=" + value})
        assert res.ok is True, value


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


def test_bom_marked_unicode_candidates_are_decoded_before_the_rules(tmp_path: Path) -> None:
    """Windows PowerShell 5.1 writes redirected output as UTF-16LE with a BOM.

    Reading those bytes as UTF-8 leaves NUL-interleaved mojibake that no rule
    can match, so a private path or credential would pass as clean. The BOM
    declares the codec; UTF-32 must be recognised before UTF-16 because its
    little-endian BOM starts with the UTF-16 one.
    """

    private = "\\".join(["C:", "Users", "example-user", "notes.txt"])
    body = "run from " + private + "\nkey " + SK_SECRET + "\n"
    cases = {
        "utf-16": body.encode("utf-16"),
        "utf-16-be": "\ufeff".encode("utf-16-be") + body.encode("utf-16-be"),
        "utf-32": body.encode("utf-32"),
        "utf-8-sig": body.encode("utf-8-sig"),
    }
    for label, data in cases.items():
        candidate = tmp_path / f"{label}.log"
        candidate.write_bytes(data)
        res = scan_paths([candidate], root=tmp_path)
        assert res.ok is False, label
        assert res.unscanned == [], label
        assert [hit.rule_id for hit in res.hits] == ["sk_bearer", "private_abs_path"], label
        assert [hit.line for hit in res.hits] == [2, 1], label


def test_undecodable_candidate_bytes_fail_closed_instead_of_reading_clean(tmp_path: Path) -> None:
    """BOM-less UTF-16 and other NUL-bearing bytes name no reviewable text."""
    private = "private=" + "\\".join(["C:", "Users", "example-user", "notes.txt"])
    candidate = tmp_path / "bomless.log"
    candidate.write_bytes(private.encode("utf-16-le"))
    res = scan_paths([candidate])
    assert res.ok is False
    assert res.hits == []
    assert res.unscanned == ["content_not_text"]
    assert str(candidate) not in compact_json(res)


def test_undecodable_candidate_bytes_never_read_clean(tmp_path: Path) -> None:
    """Replacement decoding would certify a mangled copy of the candidate.

    A malformed UTF-8 byte and a truncated BOM-marked UTF-16 candidate both
    carry text the scanner cannot vouch for. Decoding them with ``errors=
    "replace"`` yields U+FFFD, which no rule matches, so the scan would review
    a different document and could return a clean receipt for bytes it never
    saw. Both must be reported unscanned, not clean.
    """
    malformed_utf8 = tmp_path / "malformed-utf8.log"
    malformed_utf8.write_bytes(b"run from C:\\Users\\example-user\\notes.txt\n\xff")
    truncated_utf16 = tmp_path / "truncated-utf16.log"
    body = ("key " + SK_SECRET + "\n").encode("utf-16-le")
    truncated_utf16.write_bytes(codecs.BOM_UTF16_LE + body[:-1])
    for candidate in (malformed_utf8, truncated_utf16):
        res = scan_paths([candidate], root=tmp_path)
        assert res.ok is False, candidate.name
        assert res.hits == [], candidate.name
        assert res.unscanned == ["content_not_text"], candidate.name


def test_cr_only_unicode_candidate_keeps_the_text_read_line_numbers(tmp_path: Path) -> None:
    """The decoded read keeps universal-newline line numbering."""
    candidate = tmp_path / "cr-only.log"
    candidate.write_bytes(("first line\rkey " + SK_SECRET + "\r").encode("utf-16"))
    res = scan_paths([candidate])
    assert [(hit.rule_id, hit.line) for hit in res.hits] == [("sk_bearer", 2)]


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


# Synthetic credentials, assembled at runtime so this source carries no literal
# secret. Each body is distinctive, so a receipt that leaks any part of one is
# caught by a substring check over the whole serialized output.
SK_SECRET = "sk" + "-" + "synthalpha" + "0" * 22
AKLT_SECRET = "AK" + "LT" + "synthbravo" + "7" * 16
ASSIGNED_SECRET = "synthcharlie" + "9" * 12


def _assert_receipt_withholds(result, *fragments: str) -> None:
    """No part of the scanned content may reach the serialized receipt."""
    payload = compact_json(result)
    for fragment in fragments:
        assert fragment not in payload, fragment
    for hit in json.loads(payload)["hits"]:
        assert hit["sample_masked"] == "***"


def test_a_provider_token_with_no_label_is_not_certified_clean() -> None:
    """A token's own prefix identifies it; the scan must not need a label.

    The rule table recognized an Ark key, a `sk-`/`Bearer` value and an `AKLT`
    value, so every other provider credential arrived with no label this scan
    could match and the caller got `ok: true` for content that was about to be
    published. Each shape below is real provider syntax assembled at runtime.
    """
    shapes = {
        "github_pat_classic": "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
        "github_pat_fine_grained": "github_pat" + "_" + "11ABCDEFG0" + "x" * 40,
        "aws_access_key_id": "AKIA" + "7Q2Z3XJ5V6N8W1PL",
        "aws_session_key_id": "ASIA" + "7Q2Z3XJ5V6N8W1PL",
        "slack_bot": "xoxb" + "-" + "123456789012-AbCdEfGhIjKlMnOpQrStUvWx",
        "google_api": "AIza" + "SyA1234567890abcdefghijklmnopqrs",
        "stripe_live": "sk" + "_live_" + "51H8abcdefghijklmnopqrstuv",
        "npm_token": "npm" + "_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8",
        "pypi_token": "pypi" + "-" + "AgEIcHlwaS5vcmcCJDkwNzE5MTIzNDU2Nzg5",
        "jwt": "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "."
        + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    }
    for label, value in shapes.items():
        res = scan_texts({"candidate": value})
        assert res.ok is False, label
        assert [hit.rule_id for hit in res.hits] == ["provider_token"], label
        _assert_receipt_withholds(res, value, value[4:])


def test_provider_token_in_an_env_file_on_disk_fails_the_whole_scan(tmp_path: Path) -> None:
    """The documented `--scan-root` route must reject a credential file.

    `.env` is exactly the extension-less, credential-named file the scan's
    deny-list selection promises to review, so the end-to-end path -- not only
    the rule -- is what this pins.
    """
    candidate = tmp_path / ".env"
    candidate.write_text(
        "GITHUB_TOKEN=" + "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + "\n",
        encoding="utf-8",
    )
    res = scan_paths([candidate], root=tmp_path)
    assert res.ok is False
    assert res.unscanned == []
    assert [(hit.rule_id, hit.location, hit.line) for hit in res.hits] == [
        ("provider_token", ".env", 1)
    ]
    assert str(tmp_path) not in compact_json(res)


def test_provider_shape_does_not_flag_the_prose_and_labels_it_must_pass() -> None:
    """Ordinary documentation and config references stay clean.

    A publication gate that reports clean prose as a credential is switched off
    by its operators, so the passing edge is part of the rule's contract. The
    documented over-match is pinned in the second half rather than left implicit:
    a hyphenated package slug that happens to start with an issuer prefix is
    reported, because no rule set can tell such a slug from a token by shape
    alone. That direction is the capability's documented "prefer a false hit
    over a miss" boundary, not an accident.
    """
    clean = {
        "prose": "rotate the api key and the " + "bear" + "er credential for the npm registry",
        "placeholder": "GITHUB_TOKEN=<your-token>",
        "reference": "token = os.environ['GITHUB_TOKEN']",
        "identifier": "npm_packages and pypi_index are directory names",
        "ark_prefix_prose": "the sk learn pipeline and the ak sk handshake",
    }
    for label, text in clean.items():
        res = scan_texts({"candidate": text})
        assert res.ok is True, (label, [(h.rule_id, h.line) for h in res.hits])

    for label, text in {
        "hyphenated_package": "install " + "pypi" + "-django-rest-framework first",
        "dashed_slug": "the " + "xoxb" + "-slack-app-configuration page",
    }.items():
        res = scan_texts({"candidate": text})
        assert [hit.rule_id for hit in res.hits] == ["provider_token"], label


def test_a_labelled_provider_token_still_fails_closed() -> None:
    """A labelled credential can match two rules; the verdict must not change.

    Each rule reports at most one hit and the scan collects every matching rule,
    so a quoted credential assignment whose value is also a provider token yields
    both `provider_token` and the assignment rule. `hit_count` is therefore not a
    per-credential count, and no caller in the tree reads it as one; the contract
    this pins is the verdict and the content-free receipt.
    """
    text = "api" + "_key = '" + "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + "'"
    res = scan_texts({"candidate": text})
    assert res.ok is False
    assert {hit.rule_id for hit in res.hits} == {"provider_token", "api_key_assignment"}
    _assert_receipt_withholds(res, "ghp_", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")


def test_hit_is_located_by_line_and_carries_no_file_content() -> None:
    """The receipt locates a match by line instead of echoing any bytes."""
    opening = "OPENING-MARKER the very first line of the file\n"
    filler = opening + ("public line\n" * 40)
    body = "api" + "_key = '" + SK_SECRET + "'"
    res = scan_texts({"a": filler + body})
    assert res.ok is False
    assert {hit.line for hit in res.hits} == {42}
    _assert_receipt_withholds(res, SK_SECRET, "synthalpha", "OPENING-MARKER", "public line", "_key = ")


def test_adjacent_credentials_never_reach_the_receipt() -> None:
    """Each hit must not expose the other credential that sits beside it."""
    text = "x " + SK_SECRET + " " + AKLT_SECRET + " " + "sec" + "ret = '" + ASSIGNED_SECRET + "' y"
    res = scan_texts({"a": text})
    assert {hit.rule_id for hit in res.hits} >= {"sk_bearer", "aklt_access_key", "api_key_assignment"}
    _assert_receipt_withholds(
        res, SK_SECRET, AKLT_SECRET, ASSIGNED_SECRET, "synthalpha", "synthbravo", "synthcharlie"
    )


def test_case_mapping_prefix_cannot_shift_what_is_withheld(tmp_path: Path) -> None:
    """Characters whose case mapping changes length precede the secret.

    `ß` doubles under casefold and `İ` expands under lower, so an index taken
    from a case-mapped copy points at the wrong place in the original.
    """
    for prefix in ("ß" * 40 + " ", "İ" * 40 + " "):
        res = scan_texts({"a": prefix + SK_SECRET + " tail"})
        assert [(hit.rule_id, hit.line) for hit in res.hits] == [("sk_bearer", 1)]
        _assert_receipt_withholds(res, SK_SECRET, "synthalpha", prefix.strip())

    markers = tmp_path / "markers.json"
    markers.write_text(json.dumps({"credential_patterns": ["synthcharlie"]}), encoding="utf-8")
    os.environ["PUBLIC_SAFE_CONFIG"] = str(markers)
    try:
        res = scan_texts({"a": "İ" * 40 + "\n" + ASSIGNED_SECRET})
    finally:
        os.environ.pop("PUBLIC_SAFE_CONFIG", None)
    # lower() expanded the prefix, so no index maps back: still a hit, no line.
    assert [(hit.rule_id, hit.line) for hit in res.hits] == [("credential", None)]
    assert res.ok is False
    _assert_receipt_withholds(res, ASSIGNED_SECRET, "synthcharlie")


def test_private_key_body_after_its_header_is_not_published() -> None:
    """The rule matches the header only; the key body beside it is the secret."""
    body = "MHcCAQEEI" + "synthdeltaPRIVATESCALAR" * 3
    header = "-----BEGIN " + "EC PRIVATE" + " KEY-----"
    footer = "-----END " + "EC PRIVATE" + " KEY-----"
    res = scan_texts({"key.pem": "\n".join([header, body, footer, ""])})
    assert [(hit.rule_id, hit.line) for hit in res.hits] == [("private_key_block", 1)]
    _assert_receipt_withholds(res, "synthdelta", "MHcCAQEEI")


def test_internal_link_context_is_not_published(tmp_path: Path) -> None:
    """Only the host suffix is a marker; the rest of the URL is private too."""
    markers = tmp_path / "markers.json"
    markers.write_text(json.dumps({"internal_domains": ["corp.example"]}), encoding="utf-8")
    os.environ["PUBLIC_SAFE_CONFIG"] = str(markers)
    try:
        res = scan_texts({"a": "line one\nsee https://synthecho.corp.example/team/synthfoxtrot-plan"})
    finally:
        os.environ.pop("PUBLIC_SAFE_CONFIG", None)
    assert [(hit.rule_id, hit.line) for hit in res.hits] == [("internal_domain", 2)]
    _assert_receipt_withholds(res, "synthecho", "synthfoxtrot", "corp.example")


def test_line_counts_the_original_text_not_the_collapsed_one() -> None:
    """Rules see whitespace runs collapsed; the line is still the file's own."""
    text = "a\n\n\t  \r\nb   c\n\n" + "pad\t\t" + "x " + SK_SECRET + "\nlast"
    res = scan_texts({"a": text})
    assert [(hit.rule_id, hit.line) for hit in res.hits] == [("sk_bearer", 6)]
    assert text.splitlines()[5].endswith(SK_SECRET)


@pytest.mark.parametrize("hit_first", [True, False])
def test_same_named_inputs_without_a_root_keep_every_hit(tmp_path: Path, hit_first: bool) -> None:
    """Two `entry.md` reduce to one public label; neither may hide the other."""
    hit = tmp_path / "one" / "entry.md"
    clean = tmp_path / "two" / "entry.md"
    for path in (hit, clean):
        path.parent.mkdir()
    hit.write_text("k " + SK_SECRET, encoding="utf-8")
    clean.write_text("nothing sensitive here", encoding="utf-8")
    ordered = [hit, clean] if hit_first else [clean, hit]

    res = scan_paths(ordered)
    assert res.ok is False
    assert [(h.rule_id, h.location) for h in res.hits] == [("sk_bearer", "entry.md")]

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    res = scan_paths(ordered, root=elsewhere)
    assert res.ok is False
    assert [(h.rule_id, h.location) for h in res.hits] == [("sk_bearer", "entry.md")]
    assert str(tmp_path) not in compact_json(res)


def test_same_named_inputs_with_hits_both_report(tmp_path: Path) -> None:
    first = tmp_path / "one" / "entry.md"
    second = tmp_path / "two" / "entry.md"
    for path in (first, second):
        path.parent.mkdir()
    first.write_text("k " + SK_SECRET, encoding="utf-8")
    second.write_text("\nk " + AKLT_SECRET, encoding="utf-8")
    res = scan_paths([first, second])
    assert [(h.rule_id, h.location, h.line) for h in res.hits] == [
        ("sk_bearer", "entry.md", 1),
        ("aklt_access_key", "entry.md", 2),
    ]


def test_a_path_repeated_with_the_same_spelling_is_scanned_once(tmp_path: Path) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text("k " + SK_SECRET, encoding="utf-8")
    res = scan_paths([secret, secret])
    assert len(res.hits) == 1
