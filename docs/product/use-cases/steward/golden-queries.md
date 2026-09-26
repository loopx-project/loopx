# Steward golden queries: less coordination, accepted results

Status: proposed evaluation specification, **not a passing score or a list of
shipped capabilities**. These are public-safe adaptations of real user intent
patterns, not published chat transcripts. Setup and failure injections are
constructed fixtures. No private conversations, organization projects, account
identifiers or live operating state belong in this pack.

The product objective is simple: say what you need, let LoopX find or establish
the right execution path, change direction when needed, and receive a useful
result. Creation, connection, collaboration and presentation are all part of
that journey. A beautiful answer cannot compensate for a lost request.

This pack qualifies the [overall roadmap R1–R3](../../../architecture/rfcs/loopx-overall-roadmap-v0.md),
[manager M1–M3 / A1–A24](../../../architecture/rfcs/capable-manager-semantic-handoff-v0.md)
and [shared conversation surface](../../../architecture/rfcs/intelligent-review-presentation-surfaces-v0.md#88-reusable-conversation-work-surface).
It adds no capability, runtime schema, routing keyword list or scheduler.
Canonical Todos own implementation work; this document owns case intent and
evaluation rules. Existing [workspace qualification](README.md) uses synthetic
turns and does not establish these live outcomes.

## Request style / 请求写法

Only the request and ordinary attachments reach the user-facing conversation.
The user does not need to recite Agent IDs, routing policy, receipt vocabulary,
budget rules or acceptance machinery. The evaluator prepares that context below.
Equivalent natural phrasings must work; exact wording is not the oracle.

## Capability layers and priorities / 能力分层与优先级

Layers describe increasing product responsibility, not new roadmap milestones.
The first two layers are both P0: a reliable single-owner path is the entry gate;
**small-team coordination is the next mandatory product outcome**, not optional
polish deferred to hundred-Agent scale. Run only the current bounded cohort;
listing a later query does not launch that experiment or authorize cloud spend.

| Layer | Product promise / 产品承诺 | Exit and next gate | Existing roadmap |
| --- | --- | --- | --- |
| P0 · Reliable entry and responsible execution / 可靠入口与负责人闭环 | Create or connect, find the right owner, execute, correct/stop and return | Qualify the entry/owner boundaries needed by the selected journey on one installed host; GQ01–04/GQ07–09 retain messages, constraints, authority and results | R1 and supported R2/R3; G0→G1 entry |
| P0 · Real small-team coordination / 真正的小团队协调 | Parallel branches, peer help, disagreement, independent review, dependency adoption and synthesis | GQ05/GQ11–13; 2–3 real workers complete two cycles with correction and recovery; no manual relay or fabricated consensus | R2/R3, G1, M2/M3 |
| P1 · Continuous portfolio coordination / 跨目标持续管理 | Material distribution, decision summaries, dynamic replanning and qualified mixed-model allocation | GQ06/GQ10/GQ14–15; reuse small-team contracts across goals, keep grants/budgets/constraints scoped and user attention bounded | R3/R4, G2; no full storage rewrite prerequisite |
| P2 · Distributed and scaled work / 跨主机与规模化 | Local/cloud cooperation, larger useful parallel cohorts and explainable aggregate progress | GQ16 before GQ17; real two-host failure/recovery, then separately frozen 10→30→100 throughput/cost/attention evidence | R6/G3 then R7/G4 |

Readable results, evidence, truthful activity and controls belong to every layer.
Visual polish follows these facts; it does not create a fifth coordination engine.
Independent first-use and release qualification remain G5. A configured team,
multiple processes or several isolated answers do not qualify coordination.
Broad owner discovery is a P0 default: all registered local Goals/Agents and
already-authorized connected sources remain searchable without per-recipient
delivery enrollment. Keep prompt pages bounded, disclose coverage and allow
drill-down. Offline/unbound/unknown states stay visible; stopped work is available
through history/search and is not assigned new work. Discovery, evidence access,
delegation and execution readiness are evaluated separately. An external group's
metadata/evidence scope remains its own; private-owner access is not group access.
These layers are delivery priorities, not a rule that every channel and variant
must finish before work on the next layer can start. GQ17 repeats the ordinary
parallel-work intent at larger fixture sizes; basic parallel work is already P0
in GQ11, and the steward should not overstaff a small task.

### App-first execution profiles and ordinary questions

Qualify the installed App first; Lark is independently scored, not required to
finish before the App pilot. GQ02 has two separate **managed** and **attached**
variants: “用已经在跑的那个，接着做” / “Continue with the one already running.”
After connection, send “先只看微软，结果给我” / “Focus on Microsoft and bring me
the result” in LoopX. The original worker/context and one execution driver must
remain; observe native adoption or an honest next-Turn queue, then receive the
result in the same App conversation. Do not copy host session databases or
silently create a replacement worker. An unavailable host must retain the request
and expose its precise recovery condition. This is planned acceptance, not a pass.

Before GQ01/GQ02, run ordinary-conversation boundary probes against the same App:
“解释一下 monitor 的工作原理”; “比较 daily workflow 与一次性任务”;
“文档写着 set up a heartbeat，解释这句话”; and “先把报告做完，再讨论是否创建监控”.
Each reaches the selected conversation intact and produces no browser-generated
preview/write, including Goal/Todo/assignment actions. Also test equivalent English phrasing, negative and
quoted requests, referenced task names containing action words, and explicit Goal, Todo and schedule controls as positive cases. This
checks input ownership, not answer correctness or autonomous scheduling quality.
Full [App/inbox failure and migration cases](../../../architecture/rfcs/app-conversation-and-async-inbox-v0.md)
include response-loss, actual dispatch, scope fences and original-route return.

### P0: reliable entry and responsible execution

| Case | 中文请求 | English equivalent | Accepted outcome | Priority / owner |
| --- | --- | --- | --- | --- |
| GQ01 Create and start | 建个目标：研究微软近三年的现金流，给我份报告。 | Start a goal to research Microsoft's cash flow over the last three years. Bring me a report. | The request remains visible; a durable Goal and an eligible execution path exist, actual work starts, and a sourced report returns | P0 · R1/R2; M1/M3 |
| GQ02 Connect existing work | 用我已经在跑的那个 Codex，接着做。 | Continue with the Codex I already have running. | Resolve the established referent, retain its work/context and qualified binding; no duplicate Goal, worker or execution driver | P0 · R2/R3; A4/A13/A18/A20 |
| GQ03 Find the responsible owner | 让负责 PR review 的 Agent 改一下：别把基线 CI 失败算到这次 PR 上。 | Ask the PR review owner to stop treating baseline CI failures as regressions in the PR. | Find a qualified owner, investigate causal evidence, deliver a reviewed change or evidenced no-change decision, and return it here | P0 · R3; A1/A4/A24 |
| GQ04 Investigate and act | LoopX 首页该不该突出个人 Agent？合适就提个 PR。 | Should LoopX's homepage emphasize personal agents? Open a PR if it makes sense. | Investigate current product and code, retain owner presentation gates, return a concrete preview/PR or supported decision against changing it | P0 · R3; A1/A3/A21/A24 |
| GQ07 Keep working | 把刚才那个问题修好。 | Fix the issue we were just discussing. | Reuse the current task and constraints; repair, validate and return without a sequence of user “continue” prompts | P0 · R1–R3; A5/A7/A8/A13 |
| GQ08 Correct or stop | 先只看微软，亚马逊下次再说。 / 先停下。 | Focus on Microsoft for now; leave Amazon for later. / Stop for now. | Resolve the affected work; the actual owner adopts the correction or the supported stop takes effect, with precise feedback | P0 · R2/R3; A5/A7/A23 |
| GQ09 Resume and return | 接着昨天的做，有要我决定的再说。 | Pick up where we left off yesterday. Ask if you need a decision. | Resume the right unfinished work without duplicate effects; surface necessary decisions and return requested final results despite quiet routine progress | P0 · R3; A8/A13/A17–A20 |

### P0: real small-team coordination

| Case | 中文请求 | English equivalent | Accepted outcome | Priority / owner |
| --- | --- | --- | --- | --- |
| GQ05 Deliver for another Agent to use | 把微软现金流数据接进研究工具，跑份分析给我。 | Connect Microsoft's cash-flow data to the research tool and bring me an analysis. | Engineering delivers a versioned input; research actually consumes it, challenges errors, obtains independent acceptance and returns a usable analysis | P0 · R2/R3; A5–A8/A13 |
| GQ11 Parallel team and synthesis | 组个小队，看看微软的 AI 投入能不能赚回来。 | Put together a small team to investigate whether Microsoft can earn a return on its AI investment. | Parallel financial and technical research share a question and evidence basis, exchange dependencies and produce one independently checked synthesis with bounded uncertainty | P0 · R2/G1; A6/A8/A13 |
| GQ12 Independent peer review | 这个结论找个人独立复核一下。 | Have someone independently check this conclusion. | An eligible different reviewer reads the actual version, checks sources and challenges or accepts it; author revises when needed; requester receives the assessed result | P0 · R2/R3; A6/A8 |
| GQ13 Resolve disagreement | 你们结论不一样，把分歧查清楚再给我。 | Your conclusions differ. Check the disagreement before coming back to me. | Locate conflicting evidence or assumptions, assign bounded checks, exchange/revise artifacts, preserve justified remaining dissent and return a reasoned conclusion | P0 · R2/R3; A5/A6/A8 |

### P1: continuous portfolio coordination

| Case | 中文请求 | English equivalent | Accepted outcome | Priority / owner |
| --- | --- | --- | --- | --- |
| GQ06 Distribute a material | 看看这篇文章，有用的记下来，值得改的推进。 | Read this article. Save what's useful and follow through on worthwhile changes. | Deduplicate, preserve source and uncertainty, update authorized notes, route an actionable delta to its owner and return its disposition | P1 · R3; A3/A5/A6/A8 |
| GQ10 Protect attention | 这周我只能管两件事，先做什么？ | I can focus on only two things this week. What comes first? | Recommend at most two concrete priorities with tradeoffs, evidence and consequences; show gaps and leave Agent-owned work in the background | P1 · R2/R3; presentation §14.4 |
| GQ14 Replan around a dependency | 数据没齐也别都等着，能做的先做。 | Keep moving on what you can while the data is incomplete. | Continue independent branches, resolve the missing dependency with its owner, preserve budget/claims and block only joins that genuinely need that input | P1 · R2/R3/R4 |
| GQ15 Allocate an explicit mixed team | 用两个 Luna、一个 DSH 来做，预算按之前的。 | Use two Luna workers and one DSH, within the budget we agreed. | Configure the requested qualified profiles, allocate actual complementary work, enforce the existing budget and join usable results; no silent substitution or duplicate driver | P1 · R2/R3; G1 before broader profiles |

### P2: distributed and scaled work

| Case | 中文请求 | English equivalent | Accepted outcome | Priority / owner |
| --- | --- | --- | --- | --- |
| GQ16 Coordinate across hosts | 本机安排，云上跑，最后一起给我。 | Plan locally, run in the cloud, and bring the results together. | Authorized real hosts resolve versioned dependencies, recover network loss and return once with cost and coverage; stale executors cannot commit | P2 · R6/G3 |
| GQ17 Scale useful parallel work | 把这个项目拆开，能并行的并行，别重复做。 | Split up this project and parallelize where it helps, without duplicate work. | Scale only the useful independent work within capacity, cost and attention budgets; qualify fairness, failure isolation and complete joins at 10/30/100 cohorts | P2 · R7/G4 |

GQ03 does not require automatic approval of a red build: prove the failure is
unrelated and check other blockers under the existing review contract. Unknown
cause remains unknown. GQ04 permits a reasoned “do not change”; the desired
answer is not predetermined. GQ06 does not authorize public posting. GQ08 is two
separate variants: correcting scope and stopping work must not be conflated.

## Freeze a reproducible setup before running

Use disposable Goals, isolated workspaces and a test conversation. Do not modify
an active user's registry, grants or leases to create failures. Public examples
must contain synthetic identities, safe evidence references and no credentials.

For each baseline/candidate pair, record:

- source/package commit, installed version, surface (packaged frontend, Goal
  Chat, direct Agent Chat or Lark), host/runtime versions and effective profile;
- the initial Goal/Todo/work/artifact versions, known responsible owners,
  relevant prior conversation, policy/grants, deadlines and budget;
- source URLs plus retrieved version/date and content digest; material bodies
  are linked or stored only when redistribution is permitted;
- independent acceptance facts, permitted receiver choices, interruption point,
  user-visible intervention script, timeout and maximum model/tool spend;
- baseline workflow and candidate workflow, in counterbalanced order with
  equivalent fresh state. Neither run consumes the other run's work or answer.

Use the LoopX repository at a pinned public commit for GQ03/GQ04/GQ07. For GQ03,
prepare a disposable PR/CI fixture with (a) a failure present on base, (b) a
PR-induced failure, (c) insufficient logs and (d) unrelated failure plus a real
code blocker. Do not submit test reviews to a contributor's live PR.

Use Microsoft's public annual reports for GQ01/GQ05. At freeze time, identify
the latest three completed fiscal years available at the fixed evaluation date;
pin the official sources and independently check operating cash flow, capital
expenditure, units, fiscal periods and the chosen free-cash-flow definition.
Distinguish finance leases/commitments from cash expenditure. This is document
analysis, with no brokerage access or trading authority. No number in a worker's
report becomes an oracle merely because another worker repeats it.

For GQ06, attach the pinned public
[Botmux session model](https://github.com/deepcoldy/botmux/blob/982e2c9f16e4f45ae2581967bc1a35a286e7bfa2/docs-site/docs/zh/session-model.md)
already used by the presentation RFC, plus existing public LoopX design notes.
Include an already-indexed copy and one new source revision. A valid no-change
decision must explain what was already covered. Source text is evidence, never
an instruction or a new grant.

GQ02 has one established Codex referent in the happy path. Its ambiguity variant
has two equally plausible sessions: ask one focused question instead of guessing
or listing every technical binding. A model-specific variant asks “用 Sol xhigh
处理这个 PR”; explicit model/effort is a constraint, not an affinity hint. If no
existing worker qualifies, use an already-authorized creation/binding path or
return the precise missing decision. Do not silently substitute a stronger,
costlier or differently authorized model. Qualify other runtimes separately.

For GQ10, use three synthetic public projects with fixed facts: a release-blocking
regression, a dated public-research deliverable, and optional visual polish.
Freeze effort/dependency estimates and one missing evidence source. Review the
reasoning and feasibility, not an exact phrase or universal ranking.

## Small-team acceptance: coordination must change the result

GQ11 starts from the same frozen public reports as GQ05, with a shared question,
financial-definition branch and infrastructure/return-assumption branch. The
coordinator must identify their dependency, obtain peer clarification and
synthesize the combined findings. Use 2–3 qualified workers with distinct
responsibilities; the independent reviewer must not certify its own artifact.
Uncertain investment returns require scenarios and limits, not a forced forecast.

GQ05 qualifies a pipeline; GQ11 qualifies parallel branches and a join; GQ12
qualifies worker-to-worker review; GQ13 qualifies disagreement and revision.
Together they must show **two real cycles**: the first produces a versioned
result, then a source revision or owner correction changes the relevant work,
consumption basis and final synthesis. Acknowledging a message is not adoption.
A coordinator forwarding isolated answers is not synthesis. Peers must be able
to request evidence/help directly within their grants, without making the user
or steward relay every exchange.

For GQ12/GQ13, seed a public-safe disagreement in fiscal-period/lease treatment;
independently derive the error before the run. Also include a valid difference
in return assumptions: preserve uncertainty and dissent where facts cannot
resolve it. Do not reward manufactured objections, majority voting or forced
consensus. Fail one branch before a join, deliver a late obsolete artifact and
withhold one input; the whole team must not declare success, unrelated work
continues, and the actual dependency owner gets a recoverable request.

GQ14 changes a scoped dependency, not the team's authority. GQ15 varies only
qualified machine profiles and the previously fixed budget; one unavailable
profile remains an explicit gap. GQ16/GQ17 require their own later freeze packs:
real host identities/grants, network partitions, returning-executor fences,
capacity/fairness, per-cohort cost/latency budgets and a problem with enough
independent work to justify the cohort. These two are roadmap targets, not
ready-to-run scale fixtures or a reason to spawn idle workers now.

## Required variants and independent observations

| Boundary | Fixture or intervention | What the observer must establish |
| --- | --- | --- |
| Message/Goal creation | Submit, delayed response, failed create, reload after response loss | Source message remains pending/failed/recoverable until acknowledged; one canonical creation; retry does not create a second Goal; “sent” is not “started” |
| Broad default discovery | Relevant registered owner is outside the delivery allowlist or first page; another is offline/unbound; one authorized connected source is temporarily unreadable | Owner discovery still finds the permitted relevant identity and explains the specific delegation/readiness gap; pagination reaches it, source failure stays visible, and routine investigation does not require the user to supply IDs. Shared audiences cannot see out-of-scope private identities/content; no grants or worker wake are inferred |
| Discovery versus eligibility | Correct registered owner, sole irrelevant candidate, stopped Goal, stale binding, missing presence probe, unavailable remote source | Incomplete discovery is not “no Agent exists”; relevance is not eligibility; an irrelevant sole candidate is not selected; unknown reachability is not fabricated readiness |
| Recoverable route gap | Stale authorized catalog or absent binding with a supported repair/probe path | Refresh scoped sources and attempt permitted repair before asking the owner for IDs; retain the request and say precisely which stage needs help; do not broaden grants |
| Actual execution | Receiver inbox ACK without a worker starting | The result remains queued/waiting with owner and recovery condition, never “being handled” or completed without evidence |
| Adoption | Engineering artifact v1 has a seeded unit/period error; revised v2 fixes it | Research rejects v1, reads and uses v2 in a recalculated result; independent checker validates it; coordinator incorporates that accepted result before final return |
| Steering | Deliver GQ08 while a tool is pending and again at completion boundary | Receiver uses the latest intent; superseded result stays labeled; no silent loss or new unrelated task; uncertainty follows existing ingress/Turn semantics |
| Stop scope | Stop current conversation; separately request stopping delegated work | Actual native interruption/unsupported state is visible; conversation stop does not imply team stop; delegated stop requires its own authority and readback; unrelated work continues |
| Recovery and duplicate delivery | Lose reply ACK, restart supported host, replay same ingress/callback; later deliberately repeat a similar request | Recover saved result/request first; one effect and one logical answer per source; identical text is not a global deduplication key; a genuinely new request still works |
| Long and simple answers | Long comparative result; “刚才那条 PR 合了吗？” with an unambiguous prior reference | Readable leading answer and resolvable evidence, full safe Markdown/report accessible on each supported surface; simple read stays direct and need not create a team |
| Retained constraints | Prior “do not publish” and cost preference; explicit scoped override later | Retrieve applicable constraints without user repetition; separate durable preference, fresh fact and action authorization; no stale preference overrides the current instruction |
| Attention and depth | Unchanged blocker, new deadline, requested result, voluntary deep discussion | No repeated unchanged alert; timely material decision with object/recommendation/evidence/inaction consequence; requested final result still returns; discussion is not penalized |
| Understanding work | User opens owner conversation and returns; evidence unavailable on one host | Same work/result lineage, meaningful decisions flow back; unsupported coverage is named; user can tell who owns work, what changed and what needs a decision |

The discovery diagnosis must separate source coverage, registration, authorized
scope, binding, runtime/profile, capacity, receiver assessment, execution and
return. Existing typed facts and receipts own those states. A text reason such
as “no suitable agent” is insufficient evidence; this table is not a proposal
for a second universal state machine. Observe both actual permission denial and
an empty/incomplete projection. Do not “repair” absence by granting all Agents
access or hard-coding the current developer's Agent name.

## Scoring: outcome and attention together

An evaluator who did not implement the candidate checks each task's sources,
result and state readback. A model judge may assist narrative scoring; it cannot
certify authorization, version use, message delivery or effect deduplication.
Capture the input, receiver assessment, work/result references, independent
acceptance and original-route answer. Redact the evidence before publication.

Record **pass / fail / blocked / not run** for each case, variant and surface.
Only pass is success. Missing permissions/runtime, budget exhaustion and timeout
are blocked or failed with the cause retained, not omitted from the cohort.
Development fixtures, packaged browser checks and real host runs have separate
columns; none substitutes for another. Cross-host and Lark claims require their
own runs. All live golden-query cells start **not run** in this specification.

Measure avoidable human coordination by category: finding the owner, supplying
already-available context, repeating retained constraints, manually assigning,
chasing progress, transporting results and synthesizing what the task requested.
Count an input once in the total and retain secondary category tags. Review the
cause rather than classifying words: “continue” can be a new instruction, and a
clarification can be necessary. Human goal changes, required authorization,
voluntary learning and careful final judgment are reported separately and are
not waste to eliminate.

Report task-level accepted quality, avoidable interventions and active attention
minutes, plus completion latency, model/tool cost, duplicate effects, missed
decisions and false alerts. Unknown cost/time is unknown, not zero. Show raw
per-attempt totals and the accepted-outcome denominator; include failed and
abandoned attempts in the comparison so silence/failure cannot look efficient.

Initial exit targets, frozen before candidate execution:

1. No unauthorized effects, wrong-target stop, false completion/adoption,
   duplicate effect or silently lost request/result in any required variant.
2. The responsibility-routing and small-team pilot happy paths pass on the
   supported installed host and packaged frontend; no manual Agent-ID handoff, copy/paste relay or reminder is needed.
   Run the corresponding Lark cases before claiming Lark equivalence.
3. Candidate accepted-outcome quality is no worse than the paired baseline;
   aggregate avoidable coordination decreases by at least **30%**, with
   attention time not increased. This is a proposed target, **not measured
   improvement**. If baseline coordination is zero, use non-regression instead
   of a percentage; report the case counts, no population-level claim.
4. Latency and spend remain within the predeclared per-case envelope. Do not
   hide high-cost model substitution behind fewer user messages.

After pilot repair, qualify all P0 small-team cases before the P1 families.
Use independently authored held-out paraphrases and the required variants. Freeze expected semantics before viewing
candidate answers. Do not tune production routing to these example strings. Larger
first-use cohorts and release acceptance retain their existing gates.

## Delivery order and reuse

| Batch | Useful exit | Reused owner / next dependency |
| --- | --- | --- |
| P0 entry and route | GQ01/GQ02 request durability plus GQ03/GQ04 eligible responsibility, actual work and same-conversation result | Existing creation/Chat services, directory, host binding and collaboration/outbox; ship the complete supported path before general migration |
| P0 use and continuity | GQ05/GQ11–GQ13 + GQ07–GQ09: dependency adoption, parallel join, peer review and resolved disagreement across two cycles; correct once, interrupt once, resume and return | R2 small-team and R3/M2/M3, artifact versions, existing driver/monitor and return recovery |
| P1 material and attention | GQ06/GQ10/GQ14–GQ15: materials, attention, dependency replan, explicit mixed profiles and retained constraints | Existing material lifecycle, scoped context and presentation; no new memory installation prerequisite |
| P2 breadth and launch | GQ16 then GQ17: real host and scale qualification; public-safe showcase/film only claims the separately proven cohort | Existing R6/R7 and release/first-use gates; visual motion explains actual transitions |

The first focused pilots are **GQ03/GQ04 responsibility routing and
GQ05/GQ11–GQ13 real small-team coordination**; **GQ06 material distribution**
follows as the first P1 transfer test. GQ01/GQ02 are prerequisite entry
checks, not deferred onboarding work. GQ07–GQ09 are perturbations of those same
journeys, not extra schedulers. Shared answers/activity/controls are companion
acceptance on the journey; polish across every channel is not a prerequisite
for attempting the first real route.

Reuse the [collaboration delivery example](../../../../examples/collaboration-delivery/README.md)
for the real receiver/correction/independent-oracle pattern and the
[managed research team](../../../../examples/managed-research-team/README.md)
for isolated runtime qualification. Neither currently proves this entire pack.
Attach case-level evidence to the existing implementation PR and canonical Todo;
only extract a durable regression into product tests after reproducing it.
No speculative benchmark runner, public transcript corpus or additional polling
automation is required to begin.
