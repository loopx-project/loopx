"""One credential key must read as one key, however the caller spells it.

`validate_public_safe_value` classifies field names exactly, after
`normalize_public_safe_field_name` folds case, separators and camelCase
boundaries. The suffix families (`_token`, `_secret`, `_password`,
`_credential`, `_credentials`) and the raw-payload rule (`raw`, `raw_*`) only
exist once the words are split: `reviewToken` flattens to `reviewtoken`, which
belongs to no family, so without the boundary a caller that renames a field to
camelCase would walk out of the rule untouched. These tests pin the boundary and
the keys it must not sweep up.
"""

import pytest

from loopx.control_plane.runtime.public_safety import (
    normalize_public_safe_field_name,
    validate_public_safe_value,
)

ACCESS_TOKEN_SPELLINGS = [
    "access_token",
    "accessToken",
    "ACCESS_TOKEN",
    "Access Token",
    "access-token",
    "access.token",
    "  accessToken  ",
]

SUFFIXED_CREDENTIAL_NAMES = [
    "reviewToken",
    "rotateSecret",
    "viewerPassword",
    "handoffCredential",
    "noteToken",
]

CREDENTIAL_SUFFIXES = ("_token", "_secret", "_password", "_credential", "_credentials")

BENIGN_CAMEL_NAMES = [
    "tokenCount",
    "keyId",
    "secretLevel",
    "taskClass",
    "cacheHitRatio",
    "lru_cache_hits",
    "goal_id",
]


@pytest.mark.parametrize("key", ACCESS_TOKEN_SPELLINGS)
def test_every_spelling_of_a_family_member_reaches_one_verdict(key: str) -> None:
    assert normalize_public_safe_field_name(key) == "access_token"
    with pytest.raises(ValueError, match="is a credential-bearing field") as raised:
        validate_public_safe_value({key: "synthetic"}, path="p")
    assert str(raised.value) == f"p.{key} is a credential-bearing field"


@pytest.mark.parametrize("key", SUFFIXED_CREDENTIAL_NAMES)
def test_suffix_rules_fire_only_because_camel_case_is_split(key: str) -> None:
    normalized = normalize_public_safe_field_name(key)

    assert normalized.endswith(CREDENTIAL_SUFFIXES)
    assert "_" in normalized
    with pytest.raises(ValueError, match="is a credential-bearing field") as raised:
        validate_public_safe_value({key: "synthetic"}, path="p")
    assert str(raised.value) == f"p.{key} is a credential-bearing field"


def test_the_raw_payload_rule_reaches_camel_case_names_too() -> None:
    assert normalize_public_safe_field_name("rawOutput") == "raw_output"
    assert normalize_public_safe_field_name("raw") == "raw"
    for key in ("raw", "raw_body", "rawOutput"):
        with pytest.raises(ValueError, match="unbounded raw payload field"):
            validate_public_safe_value({key: "synthetic"}, path="p")


@pytest.mark.parametrize("key", BENIGN_CAMEL_NAMES)
def test_a_camel_boundary_alone_does_not_widen_the_rules(key: str) -> None:
    normalized = normalize_public_safe_field_name(key)

    assert "_" in normalized or normalized == key.casefold()
    assert not normalized.endswith(CREDENTIAL_SUFFIXES)
    validate_public_safe_value({key: "synthetic"}, path="p")


def test_nested_and_listed_payloads_keep_naming_the_callers_key() -> None:
    with pytest.raises(ValueError) as nested:
        validate_public_safe_value({"outer": {"reviewToken": "synthetic"}}, path="p")
    assert str(nested.value) == "p.outer.reviewToken is a credential-bearing field"

    with pytest.raises(ValueError) as listed:
        validate_public_safe_value([{"accessToken": "synthetic"}], path="p")
    assert str(listed.value) == "p[0].accessToken is a credential-bearing field"
