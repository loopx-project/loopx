# Public / Private Boundary

LoopX is designed to be public, but most useful goal evidence is not.

## Public

These are safe to keep in the public repository:

- schemas,
- runtime directory conventions,
- generic CLI code,
- adapter lifecycle rules,
- peer task and ephemeral worker lifecycle states,
- generic coordination rules,
- validation commands,
- sanitized examples,
- high-level design notes.

## Private

These should stay in project-local ignored files:

- local absolute paths,
- internal repository names,
- raw logs and metrics,
- task ids,
- document links,
- credentials and tokens,
- person or team names from private work,
- active goal state that reveals current user context,
- raw sub-agent prompts and traces,
- child run evidence that contains local paths or private artifacts.

## Example Boundaries

Use examples that describe the shape of private material without copying the
material itself. A good public fixture should let a contributor understand the
contract, rerun the validation, and inspect the failure mode without learning
anything about the original private run.

### Benchmark Traces

Safe public summary:

```json
{
  "case_id": "synthetic-benchmark-case",
  "adapter": "benchmark-native",
  "attempts": 2,
  "terminal_status": "blocked",
  "blocker_class": "missing-public-fixture",
  "validation": ["python3 examples/benchmark-candidate-source-boundary-smoke.py"]
}
```

Unsafe public trace:

```text
task text copied from a private benchmark, verifier tail, raw model transcript,
upload URL, host log path, or unreleased scoring artifact
```

### Active State

Safe public fixture:

```markdown
# ACTIVE_GOAL_STATE example

- Goal: Improve a synthetic fixture.
- User gate: Choose whether to publish the sanitized example.
- Agent todo: Run the public smoke and report the result.
- Evidence: `examples/example-smoke.py` passed.
```

Unsafe public state:

```markdown
- Goal: Finish the user's current private project.
- Evidence: copied owner notes, private document URL, local runtime file, raw
  child-agent prompt, or private repository branch.
```

### Local Paths

Safe path shape:

```text
<project-root>/examples/example-smoke.py
<runtime-root>/archived-goals/<goal-id>/
```

Unsafe path:

```text
a real workstation home directory, private mounted volume, local registry
database, or host-specific benchmark output directory
```

### Credentials

Safe credential boundary:

```json
{
  "provider": "example-provider",
  "credential_source": "environment",
  "credential_values_recorded": false,
  "missing_credential_blocker": "configure provider credentials locally"
}
```

Unsafe credential material:

```text
token value, cookie, authorization header, private SSH key, session dump, or
redaction that still preserves enough characters to reconstruct the secret
```

The rule that decides whether control-plane text looks private has one owner:
`loopx/public_safe_text.py`, mirrored by the TypeScript Vision checkpoint.
It rejects credential shapes, including the header, assignment, and
quoted-JSON key forms, while ordinary governance prose such as "needs owner
authorization" stays public-safe. Both runtimes are pinned to the shared corpus
in `tests/fixtures/public_safe_text_corpus.json`; extend that corpus rather
than adding a per-file exception.

Detection and permission are separate. The owner names a category for every
recognized shape, and a surface picks the policy (the category set) its
destination needs:

- Repository publication -- a PR-time scan of development code -- uses the full
  set (`ALL_CATEGORIES`). A bare credential word is rejected there.
- LoopX's own operational state -- `feedback`, `authority`,
  `boundary_authority`, and the Vision checkpoint -- uses
  `TEXT_OWNER_CATEGORIES`, which recognizes a bare credential word without
  rejecting it. "the Bearer token expired" describes a credential; it does not
  carry one.

The value arms stay in every policy, so the narrower tier releases prose only. A
label carries a value when one of four independent signals is present: an
assignment operator (`:` or `=`), including after a quoted object key, which
carries whatever follows with no length condition; a connector (whitespace,
comma, semicolon, dash, or a copula such as "is" or "set to") followed by a token
containing a digit or one of `+ / =`; the same connector followed by a quoted
run; or the same connector followed by an unbroken letter-only run of
`OPAQUE_VALUE_MIN_LENGTH` (16) characters or more. A raw credential token with no
label at all -- a GitHub token, a private key block -- is rejected by both tiers
too, which the word-only rule never caught.

No signal reads the length of a word to decide whether a credential *word* is
present, so a scheme name beside an ordinary English word stays prose while the
same word with one digit appended is a value. One residual is stated rather than
argued: a letter-only run of fifteen characters or fewer, written beside the label
with no quotes and no assignment operator, is prose to this owner. The publication
tier still rejects the mention, and the internal-state tier is not a
credential-storage exemption; the corpus carries that row as
`disclosed_residual_short_letter_value` so the limit stays a decision with a test.

A URL is in neither tier's internal-state policy yet. The rule these four owners
enforced before never rejected an ordinary link, so this split does not start to;
whether an internal-state field may carry one is decided per face, together with
the remaining caller migration in #5136.

### Compact Artifacts

Safe compact artifact:

```json
{
  "artifact_kind": "status_projection",
  "public_safe": true,
  "source_refs": ["synthetic-fixture"],
  "next_action": "rerun the public smoke after changing the fixture"
}
```

Unsafe artifact:

```text
raw uploaded files, screenshots with private data, unredacted logs, hidden
provider payloads, or a public artifact that points back to private storage
```

Structured public-output mappings use string field names, as JSON does.
`validate_public_safe_value` rejects a non-string key before classifying the
field or inspecting its value. String keys are classified after case, separator,
and camelCase normalization; converting a non-string key with `str()` is not a
safe substitute for a field name.

The typed public-export gate also rejects home-relative references, explicit
`path:` local references, historical colon/equal-prefixed local references and
`file://` URLs (including host-qualified and case-insensitive forms), in both
keys and recursively nested values. It consumes
`find_public_safe_local_path` from the shared text owner. Ordinary HTTP(S)
links, other remote URIs and repository-relative references remain subject to
their destination's existing rules; they are not generically private.

This policy applies to the existing typed-export callers:

| Destination | Behavior at the boundary |
| --- | --- |
| Goal lifecycle and acceptance projections | Omit unsafe labels and evidence references before compaction; preserve private source records. |
| Goal notices, attention and delivery review | Reject unsafe facts or synthesized output, or use the caller's existing fallback. |
| Research observations and diagnostic envelopes | Reject unsafe structured observations. |
| Peer routing, child task packets and native-child receipts | Reject unsafe candidates, references and packet fields. |
| Team plans, governed transition receipts, lane settlements and previews | Reject unsafe public payloads before admission/publication. |
| Extension capability requests and results | Reject unsafe provider payloads at the existing admission boundary. |

Decision-context packets and material-lifecycle fields already rejected file
URLs. They now report their field-specific local-path error instead of a raw-URL
error; their length limits and rejection verdicts remain unchanged. Historical
absolute-path diagnostics at the typed export gate also remain unchanged.

This is a destination-specific migration under #5136. The internal text-owner
category policies, TypeScript Vision checkpoint, generic
`public_safe_compact_text` helper and presentation redactor are unchanged;
using that compactor alone is not proof that a payload passed the typed export
gate. Other caller migrations remain separate work.

## Sub-Agent Data

Sub-agent orchestration increases leakage risk because child prompts often
contain more context than the final report needs. Public artifacts should keep:

- schema names,
- role names,
- sanitized work-scope examples,
- lifecycle states,
- generic merge rules.

Private project state should keep:

- raw child prompts,
- raw trajectories,
- local task evidence,
- non-public repo names,
- exact command output when it contains project-specific context.

Run summaries are publishable only after sanitization.

## Practical Rule

The public repo should answer: "How does a loopx work?"

The project repo should answer: "What is this specific goal currently doing?"

The runtime root should answer: "What happened in recent goal ticks?"

Real controller state belongs in ignored local files such as
`.loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md`,
legacy `.codex/goals/<goal-id>/ACTIVE_GOAL_STATE.md`,
`.local/goals/<goal-id>/ACTIVE_GOAL_STATE.md`, or the shared runtime root. A
public repository may track sanitized templates, fixtures, and compact
projections, but not the live file that a controller updates on every turn.

`loopx check` treats that as a file-state boundary, not just a path-name
boundary. Local private state that is not tracked by git may contain private
document links because it is not a publishable artifact. If that same file is
tracked by git, it enters the public boundary and is scanned like any other
publishable file.

Projects that intentionally publish tracked files with private document links
can opt in through the project registry:

```json
{
  "public_boundary": {
    "tracked_private_doc_urls": "allow"
  }
}
```

This policy only allows `private_doc_url` findings in tracked files. It does
not allow credentials, tokens, passwords, private IPs, internal task ids, or
local private paths.

If a runtime-only goal is obsolete, archive its directory rather than copying
private run payloads into public notes:

```bash
loopx archive-runtime --goal-id old-experiment-goal
loopx archive-runtime --goal-id old-experiment-goal --execute
```

The first command is a dry-run. The second moves the local runtime directory
under `<runtime-root>/archived-goals/`; it does not sanitize or publish the
payload.

## Private-Safe Pilot Checklist

Before a private project becomes a LoopX pilot, define this boundary in
the project-local active state or registry. Do this before reading private
evidence, launching adapters, or publishing a public fixture.

- Goal identity: stable `goal_id`, public-safe objective, owner mode, and the
  exact question the pilot should answer.
- Evidence classes: list source roles such as design authority, owner review,
  source repository, target repository, validation dashboard, and historical
  notes without naming private URLs, repos, people, teams, or product configs.
- Public projection: decide which fields may leave the project, such as role,
  freshness, missing gate, next action, validation surface, quota state, and
  stop condition.
- Private retention: keep raw links, paths, metrics, logs, task ids, review
  text, generated config, and implementation diffs in project-local ignored
  state or the runtime root.
- Write scope: state whether the first pilot pass is read-only, local-state
  only, public fixture only, or allowed to edit project files.
- Gate order: require health and boundary scan, then owner or controller gate,
  then evidence readiness, then compute quota, then Codex execution.
- Validation surface: name the smallest public-safe check that proves the
  pilot's projection is useful, such as `read-only-map`, `status`, review
  packet, dashboard render, or a fixture smoke.
- Handoff rule: if a missing owner action appears, write it as a user todo; if
  a safe project-agent follow-up appears, write it as an agent todo. Do not
  hide either in `Next Action`.
- Publication stop: do not commit or push a pilot artifact unless the artifact
  itself passes the public/private scan and the private evidence remains in
  project-local state.

The first public artifact from a private pilot should usually be a sanitized
fixture or status schema. The first private artifact should be a compact
project-local state update that says which private sources were considered and
why they were or were not safe to project.
