"""One owner for the field rules a periodic report is validated against.  Refs #4447 Track A.

Three decisions were each stated twice on the base revision.  ``adapters.py`` and
``project_progress.py`` both compiled the language-tag rule, and both listed the
content kinds that are supporting by nature -- the two set literals even spelled
their members in a different order, which a value-keyed duplicate scan reads as two
different values.  The identity rule that gates ``goal_id``, ``agent_id`` and ``failure_code`` was
compiled three times under two different binding names -- ``pending_intent.py`` and
``request_action.py`` called it ``_IDENTITY_RE``, ``cadence_journal.py`` called it
``_ID`` -- which is exactly what a name-keyed duplicate scan cannot see.

Every one of those sites only asks membership or ``fullmatch``, so importing the
owner here is behaviour-preserving, while a widening in one copy no longer leaves
the other behind silently.

``incremental.py`` keeps its own ``_IDENTITY_RE``.  It is a *different* rule --
case-insensitive and up to 160 characters versus lowercase-led 3..128 -- so it is
bounded-context reuse of one name, not a copy, and it is pinned as distinct here
rather than merged away by renaming.
"""

from __future__ import annotations

import re

SUPPORTING_CONTENT_KINDS = frozenset({"coverage", "delivery_receipt", "runtime"})
REPORT_IDENTITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
REPORT_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
