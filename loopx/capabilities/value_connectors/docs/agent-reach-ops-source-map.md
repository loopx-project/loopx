# Agent-Reach Ops Source Map

Status: public-safe field pattern for connector-backed content operations.

This note turns the Agent-Reach value-explorer experiments into one reusable
LoopX source profile inside the broader connector source map. It
describes how a LoopX agent can use Agent-Reach as a source router, then let
LoopX own evidence, gates, drafts, monitors, and handoff state.

Agents do not need to read this document before acting. The executable
entrypoint is:

```bash
loopx value-connectors source-map --connector agent_reach_ops_source_map --format json
```

For all currently surfaced connector profiles, use:

```bash
loopx value-connectors source-map --format json
```

## When To Use It

Use this pattern when a LoopX agent needs to:

- find public signals for a post, reply, launch note, or contributor-facing
  update;
- compare whether an idea is a mature category, an emerging phrase, or weak
  noise;
- build a source-backed draft without copying raw timelines, private chats,
  credentials, or local logs into LoopX state;
- keep publishing auditable even when the owner grants broad posting
  discretion.

Do not use it for credential collection, account setup, private timeline dumps,
mass outreach, captcha bypass, trading, paid data, or production actions.

## Operating Loop

Every connector-backed content run should use this sequence:

```text
doctor -> route selection -> read-only source map -> maturity scoring
       -> ops brief -> draft packet -> publish/audit gate -> compact monitor
```

The important rule is that Agent-Reach is the source router, not the source of
truth for action. LoopX keeps the compact action contract:

- what source was read;
- whether the boundary is public, logged-in read-only, private-needs-review, or
  forbidden;
- what claim the source supports;
- what draft was produced;
- what publish authority exists;
- what monitor or stop condition follows.

## Route Selection

Start with:

```bash
agent-reach doctor --json
```

Treat the doctor output as capability evidence. A route is usable only when the
active backend is available and the access boundary is clear.

Suggested route mapping:

| Route | Typical backend | Boundary | Safe use |
| --- | --- | --- | --- |
| GitHub | `gh CLI` | logged-in read | repo/search metadata, public stars, descriptions, issues/PR metadata when explicitly routed |
| Web | Jina Reader | public no-login | public docs and articles, with light quoting only |
| RSS | feedparser | public no-login | feed titles, links, timestamps, summaries |
| V2EX | public API | public no-login | hot topics and public replies as community signal, usually monitor-grade |
| Bilibili | public search API or `bili-cli` | public no-login | video title, author, play count, public URL |
| X/Reddit/Xiaohongshu/Facebook/Instagram | platform CLI or OpenCLI | logged-in read | read-only public or account-visible metadata only; no posting without a separate publish action record |

If a route needs browser cookies, platform login, private groups, DMs, account
setup, or raw body expansion, stop at metadata-only and project a gate.

## Evidence Card Shape

Agents should emit compact cards before drafting:

```yaml
agent_reach_ops_signal_v0:
  channel: github | web | rss | v2ex | bilibili | x | reddit | xiaohongshu | other
  backend: gh CLI | Jina Reader | feedparser | V2EX API | Bilibili public search API | ...
  query: string
  title: string
  url: string | null
  summary: string
  boundary: public_no_login | logged_in_read | private_needs_review | forbidden
  operation: read
  observed_at: ISO-8601 timestamp
  confidence: doctor_status | source_metadata | source_body_reviewed
  maturity_score: 0 | 1 | 2 | 3
  maturity_reason: string
```

`operation` must be `read`. A connector run that creates `external_write`
cards is invalid for this source-map stage.

## Maturity Scoring

Keep the scoring deliberately simple so the next agent can reuse it:

| Score | Meaning | Example signal |
| --- | --- | --- |
| 0 | Noise or unavailable route | unrelated hot topic, route missing, or stale source |
| 1 | Weak exploratory signal | exact phrase appears but with little adoption |
| 2 | Emerging signal | repeated usage, modest stars, replies, or public attention |
| 3 | Mature signal | strong adoption, many stars/views/replies, or multiple independent sources |

For public GitHub search, stars can be a first-pass proxy:

- `>= 1000`: mature category signal;
- `>= 100`: emerging category signal;
- `>= 10`: weak but visible;
- `< 10`: exploratory unless other sources corroborate it.

For public video/community sources, use attention only as a clue. Do not copy
unverified claims from drama, rumor, or account-risk videos into LoopX claims.

## Ops Brief

Before drafting, produce a short brief:

```yaml
ops_brief:
  source_batch: evidence-card file or packet id
  mature_signals:
    - claim:
      supporting_cards:
      why_it_matters_for_loopx:
  weak_signals:
    - claim:
      reason_to_monitor_instead_of_draft:
  recommended_angles:
    - audience:
      body_angle:
      evidence_refs:
  stop_conditions:
    - account boundary unclear
    - source only supports metadata, not body quote
    - post would repeat unverified platform drama
```

The brief is the handoff point. A different agent should be able to draft or
review from it without reading raw connector output.

## Content-Ops Drafting

A draft is valid only when it has:

- a named angle and target reader;
- a source map with public/private status;
- exact body text;
- media plan;
- repo or docs link when the post is about LoopX;
- account/channel/timing record when publishing is allowed;
- stop condition and first monitor plan.

Broad owner permission may allow an agent to publish according to judgment, but
it does not remove the audit requirement. The publish record still needs the
final body, active account or channel, source refs, timestamp, and follow-up
monitor boundary.

## Reusable Agent Prompt

When onboarding a new LoopX agent for creator/operator work, the preferred
instruction is to call the CLI packet:

```text
Run `loopx value-connectors source-map --format json` before drafting from
external signals. Choose a read-only source profile, emit compact evidence
cards, score maturity, and write an ops brief. Use `loopx value-connectors plan`
before any signup, send, post, reply, upload, production action, credentialed
read, or private-source expansion.
```

The longer fallback prompt is:

```text
Before drafting social content, run connector-first source mapping.
Use Agent-Reach routes only for read-only signal collection unless a separate
LoopX gate authorizes a publish action. Emit compact evidence cards, score
maturity, write an ops brief, and draft from the brief. If publishing is
authorized, record the exact body/account/time/source-map/stop-condition before
posting. If the active account or source boundary is unclear, stop with a
no-send packet.
```

## Example Finding

A value-explorer run using Agent-Reach routes found:

- mature GitHub signals for `long-running AI agent`, `AI agent control plane`,
  and `agent loop engineering`;
- high Bilibili attention around Claude Code and AI Agent setup/tutorial
  content;
- weak V2EX hot-topic relevance for this exact LoopX angle at that moment.

The reusable conclusion is:

```text
Connector-backed agents can find the trend. LoopX should make the action
reviewable: evidence cards, gates, draft packets, and monitors.
```

## Productization Boundary

The first stable part is now productized as a packet:

- `loopx value-connectors source-map --connector agent_reach_ops_source_map ...`;

Keep live collection and publish tooling local until at least two successful
batches prove the card shape and source boundaries. Productize only the stable
parts:

- `loopx content-ops draft --from-source-map ...`;
- `loopx content-ops publish-record --from-draft ...`;
- `loopx content-ops monitor --published-url ...`.

Do not productize raw provider payload retention, platform-specific cookies, or
publish shortcuts.
