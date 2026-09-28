"""The adapter statuses that count as connected, defined once.

`attention_routing` and `lifecycle` take these sets as injected arguments, so a caller
chooses what it treats as connected — but every production caller passes the same
vocabulary. It was written out three times (both status projections and the `status`
module, which read neither), and the delivery set beside it twice. Any new caller had
to invent a fourth literal to call either function.

`loopx/semantics/vocabulary_v0.json` registers both sets with this module as their only
owner; `owner_exclusivity` in that registry's policy is what makes every other module
import them instead of redefining the literals.
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
