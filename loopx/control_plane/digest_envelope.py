"""One owner for building the envelope that `content_digest` recognizes.

`loopx/control_plane/content_digest.py` decides what a stored SHA-256 looks like.
It is generated from its TypeScript value owner and deliberately exports nothing
but the two whole-value patterns, so it has no counterpart for the other half of
the same decision: writing the string. Producers that concatenate the prefix by
hand are named as that missing half in the guard's own docstring, and 94 sites in
`loopx/` did exactly that before this module (Refs #5336).

This is that counterpart, kept separate from the leaf on purpose: hashing bytes
is not a cross-runtime pattern, and the generator that emits the leaf refuses
syntax it cannot translate. What the two modules share is the decision, not a
copy of it -- every value returned here is checked against the owner's own
`ENVELOPED_SHA256_PATTERN` object, so a producer cannot emit a string the reader
would refuse, and neither module states the shape twice.

Callers that canonicalize before hashing keep doing that: the bytes are each
surface's own question, and the envelope is the shared one.
"""

from __future__ import annotations

import hashlib

from .content_digest import ENVELOPED_SHA256_PATTERN

DIGEST_ENVELOPE_PREFIX = "sha256:"


def enveloped_sha256(digest_hex: str) -> str:
    """Return a lowercase hex SHA-256 in its stored envelope.

    Fails closed on a value the owner would not recognize, rather than emitting a
    digest that only the writer believes is well formed.
    """

    enveloped = f"{DIGEST_ENVELOPE_PREFIX}{digest_hex}"
    if ENVELOPED_SHA256_PATTERN.fullmatch(enveloped) is None:
        raise ValueError("content digest must be sha256:<64 lowercase hex characters>")
    return enveloped


def sha256_envelope(data: bytes) -> str:
    """Hash ``data`` and return the digest in the stored envelope."""

    return enveloped_sha256(hashlib.sha256(data).hexdigest())
