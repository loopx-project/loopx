"""The upload acknowledgement shape is one decision, taken the same way twice.

A Lark upload returns `<resource prefix><1-240 characters of [A-Za-z0-9_-]>`,
and the prefix belongs to the resource kind (`file_` for result files, `img_`
for chat images), not to the caller.  Both use sites -- reading a saved
acknowledgement back, and accepting a fresh one -- must answer from the same
pattern, so the boundary cases are pinned here rather than inherited from
whichever value a full delivery fixture happens to return.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.capabilities.manager_context import _root, _read, _write
from loopx.extensions.lark import return_files

CACHE_DIR = "lark-result-files"


class _Runner:
    """Answers the preflight and the create call, and records what was uploaded."""

    def __init__(self, key: str | None) -> None:
        self.key = key
        self.creates = 0

    def __call__(self, args: list[str]) -> dict:
        if "--dry-run" in args:
            return {"returncode": 0, "stdout": "{}"}
        self.creates += 1
        if self.key is None:
            return {"returncode": 0, "stdout": json.dumps({"ok": True, "data": {}})}
        return {
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "data": {"file_key": self.key}}),
        }


def _upload(root: Path, *, key: str | None, pattern=return_files.RESULT_FILE_KEY_PATTERN,
            runner: _Runner | None = None) -> tuple[str, _Runner]:
    active = runner or _Runner(key)
    keys = return_files._uploaded_resources(
        root=root,
        profile="steward-app",
        provider_ref="om_provider",
        attachments=[{"sha256": "a" * 64, "name": "result.bin", "size": 4}],
        cache_dir=CACHE_DIR,
        resource="files",
        key_field="file_key",
        key_pattern=pattern,
        read_bytes=lambda _attachment: b"body",
        upload_data=lambda _attachment: {"file_type": "stream", "file_name": "result.bin"},
        runner=active,
        private_transport=None,
        before_upload=lambda: True,
        verify_only=False,
    )
    return keys, active


def _saved_identity(root: Path) -> tuple[Path, dict]:
    identity = {
        "profile": "steward-app",
        "provider_ref": "om_provider",
        "sha256": "a" * 64,
        "name": "result.bin",
        "size": 4,
    }
    from loopx.control_plane.collaboration.inbox import _hash

    cache = _root(root) / CACHE_DIR / (_hash(identity) + ".json")
    return cache, identity


@pytest.mark.parametrize("key", ["file_a", "file_" + "a" * 240, "file_A1-z_9"])
def test_a_acknowledgement_within_the_shape_is_accepted(tmp_path: Path, key: str) -> None:
    keys, runner = _upload(tmp_path, key=key)

    assert keys == (key,)
    assert runner.creates == 1


@pytest.mark.parametrize(
    "key",
    [
        "file_",               # the tail is required, an empty body is not a key
        "file_" + "a" * 241,   # one character past the documented ceiling
        "file_a.b",            # '.' and ':' are event-id characters, not key ones
        "file_a b",
        "img_a",               # the image prefix cannot answer a file upload
        "filea",               # the prefix is part of the contract
        "",
    ],
)
def test_an_acknowledgement_outside_the_shape_is_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="result file upload unavailable"):
        _upload(tmp_path, key=key)


def test_the_image_prefix_accepts_what_the_file_prefix_refuses() -> None:
    assert return_files.CHAT_IMAGE_KEY_PATTERN.fullmatch("img_a1-Z_9")
    assert return_files.RESULT_FILE_KEY_PATTERN.fullmatch("file_a1-Z_9")
    assert not return_files.CHAT_IMAGE_KEY_PATTERN.fullmatch("file_a1-Z_9")
    assert not return_files.RESULT_FILE_KEY_PATTERN.fullmatch("img_a1-Z_9")


def test_a_saved_acknowledgement_outside_the_shape_is_not_reused(
    tmp_path: Path,
) -> None:
    """The read-back branch asks the same question as the upload branch."""

    _upload(tmp_path, key="file_stored")
    cache, identity = _saved_identity(tmp_path)
    _write(cache, {"identity": identity, "file_key": "file_stored!!"})

    with pytest.raises(
        ValueError, match="identity conflict or acknowledgement unavailable"
    ):
        _upload(tmp_path, key="file_never_used")


def test_the_folded_patterns_are_the_construction_they_replace() -> None:
    """Folding must not quietly re-decide the shape.

    Each pattern is asserted against the exact string the previous inline
    concatenation built, so a changed character class or bound is a review
    event rather than a silent widening of what an acknowledgement may contain.
    """

    tail = r"[A-Za-z0-9_-]{1,240}"
    assert return_files.RESULT_FILE_KEY_PATTERN.pattern == f"file_{tail}"
    assert return_files.CHAT_IMAGE_KEY_PATTERN.pattern == f"img_{tail}"


def test_a_saved_acknowledgement_in_shape_is_reused_without_uploading(
    tmp_path: Path,
) -> None:
    keys, _ = _upload(tmp_path, key="file_stored")
    cache, _identity = _saved_identity(tmp_path)
    runner = _Runner("file_second_upload")

    reused, active = _upload(tmp_path, key="file_second_upload", runner=runner)

    assert reused == keys
    assert active.creates == 0
    assert _read(cache)["file_key"] == "file_stored"
