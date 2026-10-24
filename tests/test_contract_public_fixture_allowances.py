from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from loopx.contract import scan_public_boundary

PASSWORD_ASSIGN = "pass" + "word="
PRIVATE_ADDRESS = "192." + "168.3.4"


def test_reviewed_public_fixture_is_bound_to_content_path_and_rule(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture.py"
    original = (
        'header = "Author'
        + 'ization: Basic QWxhZGRpbjpvcGVu"\naddress = "'
        + PRIVATE_ADDRESS
        + '"\n'
    )
    fixture.write_text(original, encoding="utf-8")
    allowance = {
        "content_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "rule_ids": ["credential", "private_ip"],
    }
    registry = {
        "public_boundary": {"public_fixture_allowances": {"fixture.py": allowance}}
    }

    # Existing private content stays blocked unless the owner explicitly binds it.
    assert len(scan_public_boundary([tmp_path])["hits"]) == 2
    reviewed = scan_public_boundary([tmp_path], registry=registry)
    assert reviewed["ok"] is True
    assert reviewed["files"] == 1
    assert len(reviewed["allowed_hits"]) == 2

    allowance["rule_ids"] = ["private_ip"]
    scoped = scan_public_boundary([tmp_path], registry=registry)
    assert scoped["hits"] == ["fixture.py:1: credential"]

    allowance["rule_ids"] = ["credential", "private_ip"]
    copy = tmp_path / "other.py"
    copy.write_text(original, encoding="utf-8")
    assert len(scan_public_boundary([tmp_path], registry=registry)["hits"]) == 2
    copy.unlink()

    fixture.write_text(original + PASSWORD_ASSIGN + '"hunter2"\n', encoding="utf-8")
    changed = scan_public_boundary([tmp_path], registry=registry)
    assert len(changed["hits"]) == 3
    assert changed["allowed_hits"] == []


@pytest.mark.parametrize(
    "allowance",
    [
        None,
        "fixture.py",
        {},
        {"content_sha256": "bad"},
        {"content_sha256": "bad", "rule_ids": "credential"},
    ],
)
def test_malformed_fixture_allowance_grants_nothing(
    tmp_path: Path, allowance: object
) -> None:
    (tmp_path / "fixture.py").write_text(
        PASSWORD_ASSIGN + '"hunter2"\n', encoding="utf-8"
    )
    registry = {
        "public_boundary": {"public_fixture_allowances": {"fixture.py": allowance}}
    }

    result = scan_public_boundary([tmp_path], registry=registry)

    assert result["hits"] == ["fixture.py:1: credential"]
    assert result["allowed_hits"] == []


TOKEN_ASSIGN = "tok" + "en="


@pytest.mark.parametrize("value", ["secret", '"secret"', "'secret'", "secret&other=value"])
def test_explicit_placeholder_survives_new_and_edited_public_files(tmp_path: Path, value: str) -> None:
    registry = {"public_boundary": {"public_credential_placeholders": ["secret"]}}
    fixture = tmp_path / "new.py"
    fixture.write_text(TOKEN_ASSIGN + value + "\n", encoding="utf-8")
    assert scan_public_boundary([tmp_path])["hits"] == ["new.py:1: credential"]
    result = scan_public_boundary([tmp_path], registry=registry)
    assert result["ok"] and result["allowed_hits"] == ["new.py:1: credential"]
    # Editing a file requires no growing list of content-hash exemptions.
    fixture.write_text("# edited\n" + TOKEN_ASSIGN + value + "\n", encoding="utf-8")
    assert scan_public_boundary([tmp_path], registry=registry)["ok"]


@pytest.mark.parametrize("value", [
    "secret-real", "secret123", "Secret", "secret/real", "secret%32", "secret+real",
    '"secret real"', '"secret&other"', '"secret\\nreal"', '"secret" + payload',
    '"secret".suffix', '"secret"payload', "hunter2",
    "secret&" + TOKEN_ASSIGN + "real-value",
    "secret " + PASSWORD_ASSIGN + "hunter2",
    "secret&key=AK" + "IA1234567890ABCDEF",
])
def test_placeholder_never_grants_prefix_or_mixed_credential_permission(tmp_path: Path, value: str) -> None:
    registry = {"public_boundary": {"public_credential_placeholders": ["secret"]}}
    (tmp_path / "new.py").write_text(TOKEN_ASSIGN + value + "\n", encoding="utf-8")
    result = scan_public_boundary([tmp_path], registry=registry)
    assert result["hits"] == ["new.py:1: credential"]
    assert result["allowed_hits"] == []


@pytest.mark.parametrize("policy", [None, "secret", {}, [], [None], ["secret", None], ["*"]])
def test_missing_or_malformed_placeholder_policy_preserves_default(tmp_path: Path, policy: object) -> None:
    (tmp_path / "fixture.py").write_text(TOKEN_ASSIGN + "secret\n", encoding="utf-8")
    baseline = scan_public_boundary([tmp_path])
    result = scan_public_boundary([tmp_path], registry={
        "public_boundary": {"public_credential_placeholders": policy},
    })
    assert result == baseline


def test_placeholder_does_not_exempt_unrelated_findings_or_files(tmp_path: Path) -> None:
    registry = {"public_boundary": {"public_credential_placeholders": ["secret"]}}
    (tmp_path / "new.py").write_text(TOKEN_ASSIGN + "secret\n" + PRIVATE_ADDRESS + "\n", encoding="utf-8")
    (tmp_path / "other.md").write_text(PASSWORD_ASSIGN + "hunter2\n", encoding="utf-8")
    result = scan_public_boundary([tmp_path], registry=registry)
    assert result["files"] == result["scanned_files"] == 2
    assert result["hits"] == ["new.py:2: private_ip", "other.md:1: credential"]


def test_reviewed_line_survives_unrelated_edits_without_exempting_new_values(tmp_path: Path) -> None:
    line = 'address = "' + PRIVATE_ADDRESS + '"'
    fixture = tmp_path / "fixture.py"
    fixture.write_text(line + "\n", encoding="utf-8")
    allowance = {
        "line_sha256s": [hashlib.sha256(line.encode()).hexdigest()],
        "rule_ids": ["private_ip"],
    }
    registry = {"public_boundary": {"public_fixture_allowances": {"fixture.py": allowance}}}
    assert scan_public_boundary([tmp_path])["hits"] == ["fixture.py:1: private_ip"]
    fixture.write_text("# unrelated edit\n" + line + "\nassert True\n", encoding="utf-8")
    result = scan_public_boundary([tmp_path], registry=registry)
    assert result["ok"] and result["allowed_hits"] == ["fixture.py:2: private_ip"]
    # A line copied to an unreviewed path has no allowance.
    (tmp_path / "other.py").write_text(line + "\n", encoding="utf-8")
    assert scan_public_boundary([tmp_path], registry=registry)["hits"] == ["other.py:1: private_ip"]
    (tmp_path / "other.py").unlink()
    # New lines, changed values and appended credentials stay blocked.
    fixture.write_text(line + "\n" + PRIVATE_ADDRESS.replace("3.4", "3.5") + "\n", encoding="utf-8")
    assert scan_public_boundary([tmp_path], registry=registry)["hits"] == ["fixture.py:2: private_ip"]
    fixture.write_text(line + " " + PASSWORD_ASSIGN + '"hunter2"\n', encoding="utf-8")
    result = scan_public_boundary([tmp_path], registry=registry)
    assert len(result["hits"]) == 2 and result["allowed_hits"] == []
    allowance["rule_ids"] = ["credential"]
    fixture.write_text(line + "\n", encoding="utf-8")
    assert scan_public_boundary([tmp_path], registry=registry)["hits"] == ["fixture.py:1: private_ip"]


@pytest.mark.parametrize("hashes", [None, "*", [], [None], ["bad"], ["0" * 64, None]])
def test_invalid_reviewed_line_digests_preserve_default(tmp_path: Path, hashes: object) -> None:
    (tmp_path / "fixture.py").write_text(PRIVATE_ADDRESS + "\n", encoding="utf-8")
    registry = {"public_boundary": {"public_fixture_allowances": {"fixture.py": {
        "line_sha256s": hashes, "rule_ids": ["private_ip"],
    }}}}
    result = scan_public_boundary([tmp_path], registry=registry)
    assert result["hits"] == ["fixture.py:1: private_ip"]
    assert result["allowed_hits"] == []
