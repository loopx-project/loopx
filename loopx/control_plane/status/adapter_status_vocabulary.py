"""The adapter statuses that count as connected, defined once.

`attention_routing` and `lifecycle` take these sets as injected arguments, so a caller
chooses what it treats as connected — but every production caller passes the same
vocabulary. It was written out three times (both status projections and the `status`
module, which read neither), and the delivery set beside it twice. Any new caller had
to invent a fourth literal to call either function.

These two sets are not registered vocabularies in `loopx/semantics/vocabulary_v0.json`,
and this module is not a registered owner of any entry there. The registry's
`value_shape` admits `^[a-z][a-z0-9_]*$`, and `connected-read-only`,
`pre-tick-runnable` and `connected-delivery` carry hyphens, which no registered
vocabulary's value does today. Registering them would need a registry policy change
that belongs to the maintainer, so the guarantee that the rest of the tree imports
these sets instead of restating them is the local ownership test in
`tests/control_plane/test_connected_adapter_status_vocabulary_owner.py` — not the
registry's `owner_exclusivity` policy.
"""

from __future__ import annotations

CONNECTED_ADAPTER_STATUSES = {
    "connected",
    "connected-read-only",
    "pre-tick-runnable",
}
CONNECTED_DELIVERY_ADAPTER_STATUSES = {
    "connected-delivery",
}
