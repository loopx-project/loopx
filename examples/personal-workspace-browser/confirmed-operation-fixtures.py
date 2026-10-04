"""Isolated canonical-store fixtures; never contact a real channel or executor."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operation_action_fixtures as fixtures  # noqa: E402


def main() -> None:
    goal_id = "product-release"
    with TemporaryDirectory(prefix="loopx-operation-ui-") as root:
        service, store = fixtures.service(Path(root), goal_id=goal_id)
        handler = fixtures.managed_handler(service, store, goal_id=goal_id)
        native = {
            "thread_id": "owned-managed-thread",
            "host_turn_id": "fixture-native-turn",
        }
        request = fixtures.request(goal_id=goal_id)
        terms = request["normalized_parameters"]
        terms.pop("executor")
        terms["projection"].update(
            title="Synthetic managed operation",
            subtitle="Isolated backend fixture; no real account",
            warning="Engineering fixture only. No real channel, financial effect or automatic wakeup.",
            simulated=False,
        )
        proposal = handler(
            "loopx_operation", {"action": "prepare", "request": request}, native
        )["proposal"]
        delivered = store.record_operation_delivery(
            proposal["proposal_id"], delivery=fixtures.delivery(proposal)
        )
        confirmed = store.decide_operation(
            proposal["proposal_id"],
            decision="confirm",
            confirmation=fixtures.confirmation(delivered),
        )
        consumed = handler(
            "loopx_operation",
            {
                "action": "consume",
                "proposal_id": proposal["proposal_id"],
                "consumption_id": "fixture-attempt",
            },
            native,
        )
        assert consumed["execution_allowed"] is True
        waiting = store.load(proposal["proposal_id"])
        unknown = fixtures.agent_result(
            confirmed, "fixture-attempt", result="submission_unknown"
        )
        reported = handler(
            "loopx_operation",
            {
                "action": "report",
                "proposal_id": proposal["proposal_id"],
                "outcome": unknown,
            },
            native,
        )
        assert reported["ok"] is True
        ambiguous = store.load(proposal["proposal_id"])
        final = {
            **unknown,
            "outcome": "not_executed",
            "external_write_performed": False,
            "reconciles_outcome_digest": reported["outcome_digest"],
        }
        recovered = handler(
            "loopx_operation",
            {
                "action": "report",
                "proposal_id": proposal["proposal_id"],
                "outcome": final,
            },
            native,
        )
        assert recovered["ok"] is True
        reconciled = store.load(proposal["proposal_id"])
        print(
            json.dumps(
                {
                    "prepared": proposal,
                    "delivered": delivered,
                    "confirmed": confirmed,
                    "waiting": waiting,
                    "unknown": ambiguous,
                    "reconciled": reconciled,
                }
            )
        )


if __name__ == "__main__":
    main()
