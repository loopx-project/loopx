"""Public presentation paths are classified by the shared text owner."""

from __future__ import annotations

import pytest

from loopx import public_safe_text
from loopx.presentation import public_safety


def test_presentation_path_rules_are_owned_by_public_safe_text() -> None:
    assert (
        public_safety.LOCAL_PATH_PATTERNS
        is public_safe_text.PRESENTATION_LOCAL_PATH_PATTERNS
    )
    assert (
        public_safety.PUBLIC_BOUNDARY_PATTERNS
        is public_safe_text.PRESENTATION_PUBLIC_BOUNDARY_PATTERNS
    )


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\alice\goal.md",
        r"\\fileserver\share\goal.md",
        r"path:C:\Users\alice\goal.md",
        r"path:\\fileserver\share\goal.md",
        r"\\?\UNC\fileserver\share\goal.md",
        r"path:\\?\UNC\fileserver\share\goal.md",
        r"\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\Users\alice\goal.md",
        r"path:\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\Users\alice\goal.md",
        r"\\?\volume{01234567-89ab-cdef-0123-456789abcdef}\Users\alice\goal.md",
        r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\Users\alice\secret.txt",
        r"path:\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\Users\alice\secret.txt",
    ],
)
def test_windows_local_paths_are_redacted_and_rejected(path: str) -> None:
    redacted = public_safety.redact_public_text(
        f"private source: {path}", limit=200
    )

    assert path not in redacted
    assert "<local-path-redacted>" in redacted
    assert public_safety.scan_public_boundary_text(path) == {
        "ok": False,
        "warnings": ["absolute local path"],
    }


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/a",
        "12:30/45",
        "3:4/5",
        "relative/path.md",
    ],
)
def test_colon_and_slash_text_without_windows_paths_stays_public(text: str) -> None:
    assert public_safety.redact_public_text(text, limit=200) == text
    assert public_safety.scan_public_boundary_text(text) == {
        "ok": True,
        "warnings": [],
    }


def test_colon_prefixed_unix_paths_keep_the_existing_boundary() -> None:
    path = "source:/Users/alice/goal.md"

    assert "<local-path-redacted>" in public_safety.redact_public_text(
        path, limit=200
    )
    assert public_safety.scan_public_boundary_text(path) == {
        "ok": False,
        "warnings": ["absolute local path"],
    }


@pytest.mark.parametrize(
    "token",
    [
        "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
        "github_pat_11ABCDEFG0" + "a1B2c3D4e5F6",
        "AKIA" + "7Q2Z3XJ5V6N8W1PL",
        "ASIA" + "7Q2Z3XJ5V6N8W1PL",
        "xoxb-" + "123456789-abcdef",
        "AIzaSyA" + "1234567890abcdefghij",
        "sk_live_51H8" + "aBcDeFgHiJkL",
        "npm_" + "a1B2c3D4e5F6g7H8i9J0",
        "pypi-AgEIcHlwaS5vcmc" + "AAAAAAAAAA",
        "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".SflKxwRJSMeKKF2QT4f",
    ],
)
def test_unlabeled_provider_tokens_fail_the_boundary_scan(token: str) -> None:
    text = f"CI log fragment: pushed with {token} to the registry mirror"

    assert public_safety.scan_public_boundary_text(text) == {
        "ok": False,
        "warnings": ["provider token"],
    }


@pytest.mark.parametrize(
    "text",
    [
        "npm run build && npm test",
        "gh pr list --limit 5",
        "AWS key ids start AKIA and ASIA, then sixteen more characters",
        "pypi- prefix alone is not a token",
        "sk-proj-abcdefghijklmnop is an OpenAI project key shape",
    ],
)
def test_ordinary_prose_and_unclaimed_shapes_stay_public(text: str) -> None:
    assert public_safety.scan_public_boundary_text(text) == {
        "ok": True,
        "warnings": [],
    }


def test_labeled_credential_assignment_keeps_its_own_warning() -> None:
    text = "deploy used access_token=ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"

    result = public_safety.scan_public_boundary_text(text)

    assert result["ok"] is False
    assert result["warnings"] == ["credential assignment", "provider token"]
