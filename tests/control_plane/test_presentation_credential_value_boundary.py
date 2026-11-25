"""The presentation boundary rejects raw credential values, not just key labels.

`scan_public_boundary_text` is the single gate used by the session dash, the
`loopx dash` HTML path, manager-channel detail returns and non-private peer
result publication. It carried labeled assignments for three key names and
issuer-prefixed provider tokens, but a live value could still arrive as an
`Authorization: Bearer` header, a password/secret assignment, a URL with
embedded userinfo, or a refresh/bare token. The owning classifier already named
those shapes as `credential`; the presentation policy now consumes the same
source, so the same bytes cannot be private to the owner and public here.
"""

from __future__ import annotations

import pytest

from loopx.chat.manager_details import _text as manager_text
from loopx.presentation import public_safety
from loopx.public_safe_text import classify_private_text


CREDENTIAL_VALUES = {
    "authorization bearer header": (
        "GET /v1/items\nAuthorization: Bearer 8f3a1c9e7b2d4f6a0c5e8b1d3f7a9c2e\n"
    ),
    "password assignment": 'service password = "correct-horse-battery-staple"\n',
    "secret access key assignment": (
        "secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
    ),
    "url userinfo": "https://deploy:sup3rsecret@internal.example.com/repo\n",
    "refresh token assignment": "refresh_token=abcdefghijklmnop\n",
    "bare token assignment": "token=abcdefghijklmnop\n",
}


@pytest.mark.parametrize("value", CREDENTIAL_VALUES.values(), ids=CREDENTIAL_VALUES.keys())
def test_a_raw_credential_value_fails_the_presentation_boundary(value: str) -> None:
    assert public_safety.scan_public_boundary_text(value) == {
        "ok": False,
        "warnings": ["credential value"],
    }
    match = classify_private_text(value)
    assert match is not None
    assert match.category == "credential"


@pytest.mark.parametrize(
    "text",
    [
        "Send a Bearer token in the Authorization header; never paste the value.",
        "The password rotation policy applies to every service account.",
        "Authorization: Bearer <redacted>",
        "https://example.com/a",
        "<h2>Password:</h2><p>Sign in with the password you use for your email.</p>",
        "<h2>Token:</h2><p>Ask your administrator to issue a token for this device.</p>",
        "Password: sign in with the account you use for email.\n",
        "Token: contact your administrator for a new token.\n",
        "Password: 12 characters minimum.\n",
        "Password: 8+ characters\n",
        "Password: 8 to 64 characters.\n",
        "Token: 24 hours.\n",
        "Token: 24-hour expiry\n",
        "Token: 2 business days.\n",
    ],
    ids=(
        "bearer prose",
        "password prose",
        "redacted bearer",
        "plain url",
        "password heading",
        "token heading",
        "password sentence",
        "token sentence",
        "password length rule",
        "password length plus",
        "password length range",
        "token expiry rule",
        "token expiry hyphen",
        "token business days",
    ),
)
def test_credential_prose_stays_public(text: str) -> None:
    assert public_safety.scan_public_boundary_text(text) == {
        "ok": True,
        "warnings": [],
    }


SHORT_ASSIGNMENT_VALUES = [
    "password=hunter2",
    "token=abc123",
    "secret: hunter2",
    "token: abc123def456ghi",
    "Password: 12345678",
    '{"password": "hunter2"}',
    '{"token": "abc123"}',
    '{"secret": "abcd1234"}',
    "refresh_token: abcdefghijklmnop",
]


@pytest.mark.parametrize("value", SHORT_ASSIGNMENT_VALUES)
def test_short_and_json_key_assignments_fail_the_boundary(value: str) -> None:
    assert public_safety.scan_public_boundary_text(value) == {
        "ok": False,
        "warnings": ["credential value"],
    }
    match = classify_private_text(value)
    assert match is not None
    assert match.category == "credential"


def test_connector_and_quoted_credential_values_fail_the_boundary() -> None:
    for value in (
        "password is hunter2hunter2",
        'client secret is "abcd1234"',
    ):
        assert public_safety.scan_public_boundary_text(value) == {
            "ok": False,
            "warnings": ["credential value"],
        }
        match = classify_private_text(value)
        assert match is not None
        assert match.category == "credential"


@pytest.mark.parametrize(
    "value",
    [
        ":root { --color-token: #3b82f6; --space-md: 16px; }\n",
        ":root{--color-token:#3b82f6;--space-md:16px}\n",
        ":root { --color-token: var(--palette-blue-500); }\n",
        "body { color: var(--color-token); }\n",
        "Pass --client-secret=<value> to configure the connector.\n",
        "Pass --access-key=EXAMPLEKEYVALUE to configure the provider.\n",
    ],
    ids=("css token", "minified css", "css var value", "css var use", "flag secret", "flag access key"),
)
def test_hyphenated_design_names_are_not_credential_assignments(value: str) -> None:
    # A hyphen joins one identifier in a stylesheet or CLI help. The suffix
    # (`--color-token`) is a name, not a credential label beside an operator.
    assert public_safety.scan_public_boundary_text(value) == {
        "ok": True,
        "warnings": [],
    }


def test_basic_authorization_header_uses_the_owner_shape() -> None:
    value = "GET /v1/items\nAuthorization: Basic dXNlcjpwYXNzd29yZA==\n"

    assert public_safety.scan_public_boundary_text(value) == {
        "ok": False,
        "warnings": ["basic-auth credential value"],
    }
    match = classify_private_text(value)
    assert match is not None
    assert match.category == "credential"


def test_existing_labels_keep_one_warning_per_value() -> None:
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"

    # The provider and labeled-assignment arms still own their warnings; the
    # shared value source must not add a second label for the same bytes.
    assert public_safety.scan_public_boundary_text(token) == {
        "ok": False,
        "warnings": ["provider token"],
    }
    assert public_safety.scan_public_boundary_text(f"deploy used access_token={token}") == {
        "ok": False,
        "warnings": ["credential assignment", "provider token"],
    }


def test_manager_detail_return_omits_a_raw_bearer_value() -> None:
    text = "next step: call the endpoint with Authorization: Bearer " + "8f3a" * 8

    assert manager_text(text) == "[sensitive text omitted]"
    assert manager_text("next step: rotate the expired Bearer credential") == (
        "next step: rotate the expired Bearer credential"
    )
