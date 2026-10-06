"""Which runs belong to one agent's own lane, decided once.

``progress_scope`` says whose a recorded run is: ``"goal"`` is the shared goal lane,
``"agent_lane"`` is one agent's own lane. The agent-lane value was written out four
times on the base revision -- ``control_plane/agents/agent_lane_recommendation.py``,
``control_plane/status/run_projection.py``, ``history.py`` and ``state_refresh.py``
each bound the name to their own literal -- and all four use it the same way: to pick
which of ``latest_runs`` count as the lane's own when deciding what to recommend, what
to project, what to report and what to archive. Two of them pass it into the same
injected helper, so retyping it in one file left the other three filtering history by a
different string, with no test able to see the disagreement.

This module is a leaf and imports nothing, so no projection becomes a de facto
authority for another -- the same reason the sibling adapter-status vocabulary lives
beside its consumers rather than inside one of them.

The goal lane is deliberately not declared here. ``GOAL_PROGRESS_SCOPE`` and the
``PROGRESS_SCOPE_CHOICES`` accept set beside it exist once, in ``state_refresh.py``,
which validates caller-supplied scope against them; moving them would relocate a
decision that has no duplicate to collapse.
"""

from __future__ import annotations

AGENT_LANE_PROGRESS_SCOPE = "agent_lane"
