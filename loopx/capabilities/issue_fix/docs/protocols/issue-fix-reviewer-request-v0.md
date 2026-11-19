# issue_fix_reviewer_request_v0

`issue_fix_reviewer_request_v0` is the public-safe execution contract for
automatically notifying a reviewer after an issue-fix PR exists. It converts
the read-only reviewer recommendation into a bounded external write and proves
that either a formal request or its permission-only comment fallback became
visible on the pull request.

## Default Behavior

The default strategy is `request_top_requestable_when_authorized`:

1. read live PR metadata;
2. exclude the PR author, bots, explicitly excluded identities, reviewers
   already requested, and people who already reviewed;
3. rank remaining candidates from repository `CODEOWNERS`, caller-verified
   public maintainer maps, exact changed-path contribution history, and
   nearest-module history;
4. request the highest-ranked candidate with a resolvable GitHub handle;
5. if and only if GitHub confirms that the formal request lacks permission,
   post one concise PR comment that mentions the same reviewer, linked issue,
   and compact PR-title change summary;
6. read the PR again and require either the formal request or the fallback
   comment's semantic review request and public URL to be visible;
7. continue PR lifecycle monitoring only after that verification.

An optional `--notification-sinks-json`, or a goal-default local-private config
registered through `configure-goal`, may then deliver the same verified
reviewer through project-dedicated secondary channels. Secondary delivery is
separate evidence: it never replaces GitHub review state, never reranks the
reviewer, and cannot erase a successful canonical request when its own setup is
blocked. See
[issue_fix_reviewer_notification_sinks_v0](issue-fix-reviewer-notification-sinks-v0.md).

The default maximum is one reviewer. Existing requested or completed review
coverage and a verified fallback notification count toward that maximum, so
repeated execution is idempotent and does not keep adding people or duplicate
`@reviewer` comments. A low-confidence candidate is still eligible when it is
the best requestable, non-author repository-native candidate; confidence is
evidence quality, not an automatic skip rule.

## Authority Model

Review requests are external writes. `--execute` asserts that the host has an
active `external_review_request` authority scope; the existing broader
`publish` authority also satisfies this action in the issue-fix gate. Without
that authority, the command may prepare a request preview from compact PR
metadata but cannot write.

The same narrow authority covers one fallback comment only when the formal
request returns a confirmed permission denial such as HTTP 403/404. It does not
authorize arbitrary comments, pushes, PR creation, merge, or any other
publication action. Network failures, unknown provider errors, and identity
ambiguity never trigger the fallback. Long-running agents with standing
reviewer-request authority should call this command automatically after PR
creation instead of asking a human to perform the routine notification.

## CLI

Execute and verify the default request:

```bash
loopx issue-fix reviewer-request \
  --url https://github.com/owner/repo/pull/123 \
  --repo-path /path/to/approved/repo \
  --base-ref origin/main \
  --identity-map-json verified-identities.json \
  --reviewer-sources-json reviewer-sources.json \
  --notification-sinks-json local-private-notification-sinks.json \
  --execute \
  --format json
```

For a connected long-running goal, register the local-private pointer once and
let the normal post-PR call discover it:

```bash
loopx configure-goal \
  --goal-id example-goal \
  --issue-fix-reviewer-notification-config \
  .loopx/config/issue-fix/reviewer-notification-sinks.json \
  --execute

loopx issue-fix reviewer-request \
  --goal-id example-goal \
  --project /path/to/approved/repo \
  --url https://github.com/owner/repo/pull/123 \
  --repo-path /path/to/approved/repo \
  --base-ref origin/main \
  --execute \
  --format json
```

Goal-default execute mode loads the existing PR lifecycle row or
auto-materializes one from a fresh compact GitHub lifecycle read before any
external notification. It then persists only verified `sha256:` secondary
receipts into that row. The local config, profiles, destination, and member
mapping are not copied. Repeating the call after restart returns
`already_notified` without another secondary provider write.

Preview without an external write by supplying compact, caller-approved PR
metadata containing `author`, `comments`, `reviewRequests`, `reviews`, and
`state`:

```bash
loopx issue-fix reviewer-request \
  --url https://github.com/owner/repo/pull/123 \
  --repo-path /path/to/approved/repo \
  --base-ref origin/main \
  --reviewer-sources-json reviewer-sources.json \
  --metadata-json pr-metadata.json \
  --format json
```

A preview without complete PR metadata fails closed because author exclusion
cannot be verified. Execute mode applies the same rule if the live provider
response omits the author. The compact metadata payload is never copied into
the output packet.
`--identity-map-json` may carry a human-verified git-display-name to GitHub
handle mapping when the strongest contribution candidate could not be resolved
from public noreply identity evidence. The mapping resolves identity but does
not change the underlying ownership score.
`--reviewer-sources-json` passes the same compact, public-safe source packet
used by `reviewer-plan`. Source references explain ranking; they do not grant
write authority or bypass live author/existing-reviewer exclusion.

## Output And Transitions

The packet records:

- selected, formally requested, and otherwise notified reviewer handles;
- whether external-read and external-write actions were performed;
- whether the request was performed and fully verified;
- notification mode, fallback performance/verification, and the public comment
  URL when the fallback is used;
- the recommendation status, public-safe evidence candidates, and reviewer
  source references;
- one structured transition.
- optional secondary-sink status, verification, and hashed receipts without
  private destination, member, or bot-profile fields.
- whether the sink came from an explicit input or the goal default, and whether
  verified hashed receipts were persisted in existing PR lifecycle state.
- whether the authoritative execute path reconciled the unsent secondary queue
  and how many stale entries it cancelled, without exposing destination data.

Successful verified requests emit `issue_fix_reviewer_request_verified` with
`monitor_continuation`. Confirmed permission denial followed by a verified
comment emits `issue_fix_reviewer_comment_fallback_verified`. If review is
already covered by a request, completed review, marked fallback comment, or a
live comment that explicitly mentions the reviewer and asks for review,
execution is a quiet, no-write monitor continuation and returns the existing
comment URL. Secondary sinks remain bound to that covered reviewer; an
unresolved sink identity skips secondary delivery instead of substituting a
different candidate. Any previously queued, unsent secondary target is
atomically replaced or cancelled by the current execute reconciliation.
Semantic comment matching normalizes handle case, requires a
bounded review-request phrase near the mention, and ignores quoted text, code
blocks, ordinary discussion, and marker-only metadata. Missing requestable
identity produces a runnable identity-resolution successor. Closed PRs produce
structured no-follow-up.

Permission failure of both notification paths, network/unknown provider errors,
or post-write verification failures produce a concrete blocker while
preserving the selected reviewer for a bounded retry. The command never reports
success solely because a write command returned zero.

## Public-Safety Boundary

Every packet keeps these fields false:

- `local_paths_captured`
- `raw_provider_payload_captured`
- `raw_git_output_captured`
- `commit_emails_captured`

It stores no credential, local path, raw provider response, raw git log, issue
body, comment body, transcript, or runtime state. Public source URLs and matched
route metadata may be retained; the linked maintainer-map body is not captured.
The fallback comment contains only public PR context: the reviewer handle,
linked issue reference, compact PR-title change summary, and a pointer to the
PR description for motivation, validation, and risk. New comments rely on
semantic deduplication instead of publishing an idempotency marker; legacy
markers remain read-compatible. Repository history is read only from the
explicitly approved checkout and affects the compact ranking evidence.

## Validation

Run:

```bash
python3 examples/issue-fix-reviewer-request-smoke.py
python3 examples/issue-fix-reviewer-notification-drain-postwrite-smoke.py
```

The generic fixture verifies live-author exclusion, top-candidate selection,
successful request and readback, permission-only comment fallback, fallback
URL/semantic verification, human-readable issue/change context, idempotent retry without duplicate comments,
semantic dedupe for an older explicit review-request comment, rejection of
ordinary reviewer discussion, unclassified-provider blockers, public-safety
boundaries, and no-write CLI preview. It also proves that a public
maintainer-map candidate reaches the
request packet without weakening the external-write gate. It contains no
OpenViking-specific branch or candidate.
