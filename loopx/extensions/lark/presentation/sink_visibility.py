"""The one owner of the visibility a Lark projection sink row may carry.

``owner-only`` keeps the synced rows private to the owner; ``shared`` is the
switch that turns on the public-safe redaction before any row is written.  The
pair is one decision -- which accept set gates redaction -- and it used to be
restated by both Lark projection sinks and spelled out a third and fourth time
inside the two CLI parsers that hand the value to them.

The values carry a hyphen, so this owner is deliberately *not* a registered
vocabulary: the semantic registry's value shape is ``[a-z][a-z0-9_]*``, and
widening it is a maintainer decision, not a side effect of a deduplication.

``loopx.extensions.manifest._PRESENTATION_SURFACE_VISIBILITIES``
(``public-safe`` / ``owner-only``) answers a different question about advertised
extension surfaces.  It shares the word ``owner-only`` and must stay separate.
"""

from __future__ import annotations

SINK_VISIBILITY_OWNER_ONLY = "owner-only"
SINK_VISIBILITY_SHARED = "shared"

SINK_VISIBILITIES = {SINK_VISIBILITY_OWNER_ONLY, SINK_VISIBILITY_SHARED}
