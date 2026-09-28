import assert from "node:assert/strict";
import {
  compareProposalRecency,
  olderProposals,
  proposalRecencyKey,
} from "../../../node_modules/.cache/loopx-proposal-recency/proposal-recency.js";

// The store's contract: `ChatActionStore.list` sorts by
// (`updated_at`, `proposal_id`) with `reverse=True`, so a restored list arrives
// newest first and a draft created in this session is appended last.
const stored = (previewId, updatedAt, createdAt = updatedAt) => ({ createdAt, previewId, updatedAt });
const newest = stored("draft-b", "2026-09-14T02:00:00Z");
const older = stored("draft-a", "2026-09-14T01:00:00Z");

// The two real arrival orders must agree about which draft is newest.
assert.deepEqual([older, newest].sort(compareProposalRecency), [newest, older]);
assert.deepEqual([newest, older].sort(compareProposalRecency), [newest, older]);
assert.deepEqual(olderProposals([older, newest]), [older]);
assert.deepEqual(olderProposals([newest, older]), [older]);
assert.deepEqual(olderProposals([newest]), []);
assert.deepEqual(olderProposals([]), []);

// Ties fall back to the id, in the same direction the store sorts it, so the
// answer is stable instead of depending on which record was listed first.
const tieLow = stored("draft-a", "2026-09-14T02:00:00Z");
assert.equal(compareProposalRecency(tieLow, newest), 1);
assert.equal(compareProposalRecency(newest, tieLow), -1);
assert.equal(compareProposalRecency(newest, stored("draft-b", "2026-09-14T02:00:00Z")), 0);

// A draft that never reached the store still orders deterministically, and a
// stored draft stays ahead of it because a timestamp outranks an absent one.
const hostOnly = { previewId: "draft-z" };
assert.deepEqual(proposalRecencyKey(hostOnly), ["", "draft-z"]);
assert.equal(compareProposalRecency(newest, hostOnly), -1);
assert.equal(compareProposalRecency(hostOnly, newest), 1);

// A host preview that carries only `created_at` still compares by that time.
const createdAtOnly = { createdAt: "2026-09-14T03:00:00Z", previewId: "draft-c" };
assert.deepEqual(proposalRecencyKey(createdAtOnly), ["2026-09-14T03:00:00Z", "draft-c"]);
assert.equal(compareProposalRecency(createdAtOnly, newest), -1);

// Recency follows the record, not the position: the same records in the
// contract-violating order still name the same newest draft.
const reversed = [newest, older];
assert.equal(reversed.slice().sort(compareProposalRecency)[0].previewId, "draft-b");
assert.equal([older, newest].slice().sort(compareProposalRecency)[0].previewId, "draft-b");

console.log("Proposal recency invariants passed");
