# Experimental CLI prerequisite; M1 remains open

[Implementation PR #5385](https://github.com/loopx-project/loopx/pull/5385),
head `ad478f4753b5cfeaa5ee5248fceb8a06c8c28c2b`, adds an opt-in TypeScript/Node
source-checkout CLI for selected Lark text -> semantic proposals -> individual
digest review -> canonical User Todo create/amend -> private CLI readback.
It follows the proposed RFC in [#5384](https://github.com/loopx-project/loopx/pull/5384).
Both remain unmerged proposals at this observation.

The implementation reuses canonical writer/provider/receipt and registry witness
owners. It admits an existing private, explicitly active exact-instance Goal in a
project-local object registry with no registered Agents. Unsupported profiles fail
closed. Source revisions, profile and registry changes invalidate old reviews.
A later batch item can be refreshed without another model request. Deadlines are
review/Todo-note metadata; no reminder or completion effect is installed.

Validation on the implementation head: 91 focused Node tests pass, including nine
feature cases and a real CLI/HTTP/disposable file-store journey with Python absent
from PATH. Lark and model responses are scripted. The full suite had 3,530 passes,
21 failures and 31 skips; the same 21 failures reproduced on unmodified `f49b4a0`.
Final changes received focused revalidation. Typecheck retains the identical 53
baseline diagnostics. These results do not establish a green repository-wide run.

M1 remains **partial and unqualified**. Desktop settings/return, the durable
proposal lifecycle, owner identity verification, live Lark/model compatibility,
semantic evaluation and packaged readback are untested or unimplemented. This
CLI's private files are transient review handoffs, not a replacement proposal
service. The next owner is personal-workspace/work-items with the Lark extension,
which must integrate the existing typed conversation contract and finish the M1
journey. M2/M3 acceptance is unchanged. No account data was used or message sent.

No operational Todo registry was available in the checkout; this entry records
code evidence and the existing RFC successor, not an operational task claim.
