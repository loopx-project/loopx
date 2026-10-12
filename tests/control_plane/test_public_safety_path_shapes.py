"""Boundary shapes for the shared public-safety surface rules.

The local-path rule recognizes every drive-qualified path, UNC shares and
``/data`` roots; the secret rule tolerates quoted keys and values. Neither
widening may start matching URLs, clock times or ratios.
"""

import pytest

from loopx.control_plane.runtime.public_safety import (
    LOCAL_PATH_SURFACE_PATTERN,
    SECRET_LIKE_SURFACE_PATTERN,
    validate_public_safe_value,
)

REJECTED_PATHS = [
    "C:" + chr(92) + "build" + chr(92) + "evidence.txt",
    "C:/workspace/private/worker.json",
    "D:" + chr(92) + "Projects" + chr(92) + "loopx" + chr(92) + "state.json",
    chr(92) * 2 + "server" + chr(92) + "share" + chr(92) + "evidence.txt",
    "/data/reports/evidence.txt",
    "C:" + chr(92) + "Users" + chr(92) + "fixture" + chr(92) + "evidence.txt",
]

ACCEPTED_TEXT = [
    "https://example.org/data/report",
    "s3://bucket/key",
    "notion://page/1",
    "12:30/45",
    "ratio 3:4/5 done",
    "id x:y/z",
    "docs/evidence.md",
    "access key rotation guide",
    "path: is fine without a path",
    "profile://example.org/report",
]

EXPORT_LOCAL_REFERENCES = [
    "~/evidence.txt",
    "~" + chr(92) + "evidence.txt",
    "path:/srv/evidence.txt",
    "PATH:" + chr(92) + "evidence.txt",
    "file:///tmp/evidence.txt",
    "FILE://localhost/share/evidence.txt",
    "file://server/share/evidence.txt",
    "file://./evidence.txt",
    "lookup :/Users/fixture/evidence.txt",
]


@pytest.mark.parametrize("value", EXPORT_LOCAL_REFERENCES)
@pytest.mark.parametrize("position", ["scalar", "nested_value", "nested_key"])
def test_public_export_rejects_local_references_at_every_depth(value, position):
    payload = {
        "scalar": value,
        "nested_value": {"items": ({"summary": value},)},
        "nested_key": {"items": [{value: "safe text"}]},
    }[position]
    with pytest.raises(ValueError, match="local path|unsafe field name") as matched:
        validate_public_safe_value(payload)
    # Diagnostics name the field, without echoing the private locator.
    assert value not in str(matched.value)

def _secret(key: str, separator: str, quote: str = "", key_quote: str = "") -> str:
    """Build a credential-shaped probe without a literal secret assignment."""

    return f"{key_quote}{key}{key_quote}{separator}{quote}{'synthetic' * 4}{quote}"


REJECTED_SECRETS = [
    _secret("token", ": ", quote='"'),
    _secret("token", "="),
    _secret("access_key", "=", quote="'"),
    _secret("access_key", ": ", quote='"', key_quote='"'),
    _secret("sk", " = ", quote="'"),
]


@pytest.mark.parametrize("value", REJECTED_PATHS)
def test_drive_unc_and_data_paths_are_local_paths(value):
    assert LOCAL_PATH_SURFACE_PATTERN.search(value)
    with pytest.raises(ValueError, match="absolute local path"):
        validate_public_safe_value(value)


@pytest.mark.parametrize("value", ACCEPTED_TEXT)
def test_urls_times_and_ratios_are_not_local_paths(value):
    assert not LOCAL_PATH_SURFACE_PATTERN.search(value)
    assert not SECRET_LIKE_SURFACE_PATTERN.search(value)
    validate_public_safe_value(value)


@pytest.mark.parametrize("value", REJECTED_SECRETS)
def test_quoted_secret_keys_and_values_are_credential_like(value):
    assert SECRET_LIKE_SURFACE_PATTERN.search(value)
    with pytest.raises(ValueError, match="credential-like"):
        validate_public_safe_value(value)


def test_path_shaped_value_reports_local_path_before_opaque_shape():
    # The turn-executor contract records this ordering: a drive-qualified
    # path is a local path first, so that diagnostic wins over the
    # opaque-reference shape check that runs afterwards.
    with pytest.raises(ValueError, match="absolute local path"):
        validate_public_safe_value({"worker_ref": "C:/workspace/private/worker.json"})
