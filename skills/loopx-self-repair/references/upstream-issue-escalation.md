# Guarded Upstream Issue Escalation

Use this route to turn a confirmed, reusable LoopX product gap into a compact
upstream issue without leaking a user's project state or creating issue spam.
Self-repair invocation is diagnosis consent, not publication consent.

## 1. Qualify The Candidate

An issue candidate should satisfy all of these conditions:

- the responsible layer is the public LoopX product, CLI, skill, installer, or
  control plane rather than the user's project;
- the behavior reproduces on a supported clean install, a public/synthetic
  fixture, or repeated independent public-safe evidence;
- the impact is concrete for users or operators;
- durable tracking or coordination adds value beyond a direct fix or PR.

Do not publish an issue for:

- a one-off agent judgment error, stale local state, or user-project defect;
- a support question that docs or a direct answer can resolve;
- a report that requires private names, task identifiers, repository details,
  local paths, internal URLs, raw logs, trajectories, or verifier output;
- credentials, suspected vulnerabilities, or other security-sensitive
  material. Stop and use the repository's private security channel instead.

Prefer a direct, reviewable PR when the repair is already understood and an
issue would only duplicate that work.

## 2. Establish Publication Authority

Automatic submission is allowed only when one of these is true:

- the user explicitly asked to open the issue in the current task; or
- the owner has provided a durable opt-in through
  `LOOPX_SELF_REPAIR_AUTO_ISSUE=1` in the current environment.

The opt-in authorizes publication behavior, not the contents of a particular
report. Qualification, public-safety, authentication, and deduplication gates
still apply every time. Repository labels, self-repair invocation, a writable
checkout, or an authenticated `gh` session are not publication authority.

Without authority, render the exact public-safe title and body, then ask one
yes/no confirmation. Do not create a user todo or interrupt the user until a
candidate has passed qualification, scanning, and duplicate search.

## 3. Build A Minimal Public Packet

The issue body should contain only:

1. observable symptom and user impact;
2. expected and actual behavior;
3. a minimal synthetic or public reproduction;
4. LoopX version or public commit and relevant runtime version;
5. the narrow suspected product surface, clearly marked as a hypothesis;
6. public-safe validation already run.

Add a stable marker derived from normalized affected surface, symptom, and
cause class. Do not include private identifiers in the fingerprint input:

```text
<!-- loopx-self-repair:fingerprint=<12-hex-sha256> -->
```

Write the draft to a temporary file outside the repository and scan it before
any GitHub write:

```bash
loopx check --scan-path "$draft_file"
```

A passing scanner is necessary but not sufficient. Read the final title and
body and remove private project names, customer or team names, task IDs, local
paths, internal links, credentials, raw logs, trajectories, verifier output,
and private production details.

## 4. Search Before Creating

Search both open and closed issues in the canonical upstream repository using
the fingerprint marker:

```bash
gh issue list \
  --repo loopx-project/loopx \
  --state all \
  --search "$fingerprint in:body" \
  --limit 20 \
  --json number,title,state,url
```

If a matching issue exists, record its URL and stop. Do not open a duplicate or
comment on the existing issue unless comment publication is separately
authorized. If search fails or its result is ambiguous, preserve the draft and
stop before publication.

## 5. Submit Once, Then Write Back

Verify authentication and create only after every earlier gate passes:

```bash
gh auth status
gh issue create \
  --repo loopx-project/loopx \
  --title "$title" \
  --body-file "$draft_file"
```

Create at most one issue per repair turn and make at most one submission
attempt. On authentication or submission failure, keep the draft and provide a
concrete recovery step; do not retry in a loop.

After finding or creating an issue, write its URL and outcome
(`upstream_issue_deduplicated` or `upstream_issue_opened`) into the related
LoopX todo/evidence record. A draft alone is not durable delivery and should
not consume a delivery spend.
