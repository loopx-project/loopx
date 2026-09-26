# RFC: Frontier Science Research Program v0

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal; no research treatment promoted
- **Authors / owners:** LoopX maintainers; experiment owners to be assigned separately
- **Created:** 2026-09-15
- **Last normative revision:** 2026-09-15
- **Implementation baseline:** `71dbaee69b528274438295c75d39967150e7c26e` (documentation inspection, not a full runtime audit)
- **Language mirror:** [中文版](frontier-science-research-program-v0.zh-CN.md)
- **Research tracker:** [#4391](https://github.com/huangruiteng/loopx/issues/4391)
- **Community discussion:** [#4392](https://github.com/huangruiteng/loopx/discussions/4392)

## Document map and maintenance contract

This English document and its Chinese counterpart are a semantic mirror.
Sections 1–10 define the proposed research and acceptance contract, Section 11
defines staged delivery, and Section 12 lists unresolved decisions. Appendices
record non-normative execution, decisions and evidence. A merged proposal is
not runtime promotion. Research findings, implementation maturity and
maintainer approval remain separate; progress entries do not amend the design.

## 1. Decision summary

LoopX should evaluate ten scientific directions as one research portfolio,
routing each experiment to its existing capability or state owner. The
proposed near-term sequence is experiment evidence, decision-preserving
continuation, and metareasoning shadow evaluation. All ten tracks remain
visible; their inclusion is not a commitment to implement all of them.

This RFC introduces no authoritative state, command, provider, scheduling
policy, permissions, model calls or default behavior. It proposes bounded
experiment contracts and review criteria. Any active treatment needs its own
reviewed slice, owner, budget, benchmark rights and rollback boundary.
Discussion participation does not grant execution or promotion authority.

The strategic hypothesis is that provider-neutral, outcome-linked control
can improve the cost, continuity and reliability of long-horizon work as
underlying models change. That hypothesis requires product evidence.

## 2. Problem and motivation

Long-running work repeatedly revisits evidence, compresses history, chooses
whether to continue and learns from selected outcomes. It can therefore
accumulate false discoveries, erase decision-critical distinctions, overpay
for verification or reinforce harmful experience while appearing productive.

For example, two handoff summaries may both say a fix is complete while only
one has current-revision evidence and permission for the next action.
Similarly, a frequently cited memory may correlate with easy tasks without
causing better outcomes.

### Invariants

- Current goal, gate, lease, quota and effect owners retain authority.
- Observations, predictions, measured outcomes and causal claims stay distinct.
- Comparisons account for controller, retrieval, evaluation and human costs.
- Hidden evaluation content cannot become reusable memory or selection input.
- Failed, null and invalidated experiments remain visible.
- Claims identify assumptions, versions, source-reading depth and transfer limits.
- Each proposed abstraction must earn its cost against a simpler baseline.

## 3. Scope and non-goals

The portfolio covers mathematical statistics, information theory, cognition,
control, causal inference, formal methods, neuroscience, evolutionary
computation, statistical physics and scientific experimentation.

Near term means a bounded prototype over roughly 2–8 weeks; medium term is
roughly 2–9 months; long term is roughly 6–24 months. Overlap is intentional:
a prototype can be near-term while transferable evidence remains medium-term.
These estimates assume one or two engineers familiar with LoopX and an
available evaluation environment. Sample accumulation and domain partnerships
can dominate elapsed time. They are planning estimates, not release dates.

Non-goals are a new universal supervisor, a second state store, automatic
self-modification of authority, unrestricted experiments, foundation-model
training, a blanket statistical safety claim or an autonomous production lab.
No existing capability/RFC is renamed or duplicated merely to match a paper.

## 4. Current-system contract

The named baseline was inspected through public documentation and selected
RFC sections. These are documented boundaries, not a new implementation audit.

| Existing owner / contract | Documented foundation | Increment sought here |
| --- | --- | --- |
| [Hierarchical stride](hierarchical-agent-stride-control-v0.md) | Effect/delivery/authority hierarchy; M1 observation slice | Costed, calibrated shadow choices |
| [Research exploration](research-exploration-control-plane-v0.md) | Typed frontier, composition gaps and bounded execution handoff | Decision-relevant experiment selection |
| [Memory utility](post-outcome-memory-utility-attribution-v0.md) | Outcome binding; application is not causal utility | Controlled attribution and transfer conditions |
| [Benchmark program](long-horizon-harness-benchmark-research-program-v0.md) | Matched evidence and capability-evolution sandbox | Sequential validity and diverse candidate archives |
| [Continuation](cross-session-memory-substrate-v0.md) | Explicit local continuation with revision guards | Measured decision-preserving projection |
| [Effect interpreter](agent-loop-effect-interpreter-v0.md) / [shared authority](shared-goal-authority-state-provider-v0.md) | Typed effects and scoped transaction boundaries | Formalize one stable contract at a time |
| [Dreaming roadmap](../../product/roadmaps/dreaming-exploration-lane.md) | Advisory consolidation and exploration | Curriculum and negative-transfer evaluation |

## 5. Research portfolio and proposed architecture

### 5.1 Ownership, records and lifecycle

The portfolio has no new built-in capability id or provider id. Experiment
bookkeeping belongs with benchmark_toolkit and the existing evidence owner;
reward-memory, context, host, Turn, Explore and authority retain their own
semantics. Optional evaluators or domain integrations may later be extension
packages after a real caller and contract are established.

Each experiment proposal must document its hypothesis, work class,
task/model/environment revisions, candidate and baseline, experimental unit,
allocation and replication plan, outcome verifier, cost budget, analysis
assumptions, source provenance, stop rule and rollback. These are design
obligations, not a newly registered runtime schema. Existing fields are not
removed or reinterpreted.

The research lifecycle is proposal → protocol review → offline or shadow
qualification → held-out validation → maintainer disposition. Repetition must
reuse observation identity without double-counting; a new treatment or
analysis version must not silently inherit incompatible evidence.
`insufficient`, `supported`, `refuted` and `invalidated` describe proposed
evidence outcomes, not new Goal status enums. Runtime adoption requires an
explicit separately reviewed transition. Missing evaluator data leaves work
under existing policy rather than inventing success or an owner gate.

### 5.2 Track summary

| Track | Direction | First useful evidence window | Placement |
| --- | --- | --- | --- |
| T01 | Anytime-valid experiment evidence | 2–4 weeks for a bounded prototype | benchmark_toolkit; existing long-horizon benchmark RFC; diagnostics consumes evidence. |
| T02 | Decision-preserving memory and continuation | 3–6 weeks | Existing continuation/context-provider boundary; coordinate with memory utility. |
| T03 | Metareasoning and event-triggered stride | 4–8 weeks in shadow mode | Hierarchical Agent Stride RFC; existing host, Turn and authority owners. |
| T04 | Causal memory utility and transfer conditions | 4–8 weeks offline; 2–6 months for transfer evidence | reward_memory; extend Post-Outcome Memory Utility Attribution RFC. |
| T05 | Formal authority kernel and checked certificates | 2–6 weeks for one contract; broader work is medium/long term | Existing typed Effect Interpreter and authority transaction boundaries. |
| T06 | Structured curriculum and complementary learning | 4–8 weeks prototype; 2–6 months validation | Existing dreaming/exploration roadmap and reward-memory lifecycle. |
| T07 | Diversity-preserving capability evolution | 2–6 months | Existing capability evolution sandbox in the benchmark research program. |
| T08 | Active information acquisition and experiment design | 3–9 months | Research Exploration Control Plane; existing Explore/frontier and execution owners. |
| T09 | Multiscale predictive control state | 6–18 months | Research collaboration with stride/context owners; no replacement state authority. |
| T10 | Scientific experiment campaign infrastructure | 3–6 months simulator partnership; 12–24 months vertical validation | Optional domain package with a design partner; reuse Explore, quota and evidence. |

### T01. Anytime-valid experiment evidence

**Scientific basis:** Mathematical statistics: e-processes, confidence sequences, adaptive experiments.

Repeatedly inspecting fixed-sample tests and promoting the best observed candidate can inflate false discoveries. SAVI supports optional stopping under explicit assumptions; trajectory calibration additionally exposes failures hidden by final-answer-only evaluation. This is an established mathematical foundation with emerging agent applications. Sources: [SAVI](https://arxiv.org/html/2210.01948v2); [ToolChain-CRC, 2026](https://arxiv.org/html/2606.18467v1).

**LoopX experiment:** Use fixed treatment versions and complete, comparable task episodes. Record allocation, missingness, bounded outcomes, analysis identity and multiplicity handling. Compare confidence-sequence/e-process evidence with preregistered fixed-sample and current heuristic decisions.

**Acceptance and stop boundary:** Measure false promotion under a null, detection power and sample cost. Correlated calls are not independent samples; e-values are not correctness probabilities. If assumptions cannot be supported, retain descriptive evidence and do not issue a guarantee.

### T02. Decision-preserving memory and continuation

**Scientific basis:** Information theory: decision-centric rate–distortion and state abstraction.

DeMem studies memory compression through lost decision quality. Histories that look alike may require different actions. A summary saying “fix complete” must preserve whether evidence matches the current revision and whether the next action is authorized. Sources: [DeMem, 2026, §§3–5 and Appendices D/F](https://arxiv.org/html/2605.10870v1).

**LoopX experiment:** Compare ordinary summaries, structured summaries and decision-preserving packets under the same actual token budget, including retrieval costs. Start with one explicit continuation path and contrastive cases for stale evidence, changed intent and task identity.

**Acceptance and stop boundary:** Measure continuation success, lost constraints, unauthorized decisions and total cost. Abstract regret guarantees do not automatically transfer to real agents. Stop if only summary scores improve or the projection becomes a second authority.

### T03. Metareasoning and event-triggered stride

**Scientific basis:** Cognitive science, control theory and operations research: value of computation and constrained policy optimization.

CCPO studies cost-aware orchestration under reliability constraints; DOLORES constructs reasoning structure at test time. LoopX should test when another continuation, verification, recall or replan creates enough expected value to justify its cost. Sources: [CCPO, AAAI 2026](https://ojs.aaai.org/index.php/AAAI/article/view/39739); [DOLORES, 2026](https://arxiv.org/html/2605.11388v1).

**LoopX experiment:** Record predictions before actions, then bind outcomes. Compare a shadow policy with existing rules and inexpensive thresholds; use prospective controlled trials for action effects. Passive logs alone do not establish counterfactual policy value.

**Acceptance and stop boundary:** Count controller tokens and latency. Measure accepted outcome cost, false stopping, wasted verification and human attention. Stop if controller overhead consumes the benefit. A wider stride never grants wider authority.

### T04. Causal memory utility and transfer conditions

**Scientific basis:** Causal inference and controlled intervention.

CMI compares absent, present and perturbed memory, but its experiment-time selector uses target-answer scoring and annotated memory roles. Treat this as an intervention-design lead, not evidence of label-free deployment. Its perturbation score also needs an independent robustness interpretation. Sources: [CMI, 2026, §3](https://arxiv.org/html/2605.17641v1).

**LoopX experiment:** Use matched checkpoint reruns with and without a memory set, pinned model/task/tool versions and replication. Study A/B/AB/no-memory interactions only when justified. Bind conditional utility, support and uncertainty to the existing outcome receipts.

**Acceptance and stop boundary:** Keep hidden task answers out of selectors and reusable memory. Log replay is not a rerun of an unreconstructable world. Start with set-level attribution; defer item-level credit if measurement cost or confounding dominates.

### T05. Formal authority kernel and checked certificates

**Scientific basis:** Mathematical logic, model checking, SMT and proof assistants.

HERMES demonstrates tool-integrated mathematical verification with correct/incorrect/inconclusive outcomes; AXLE addresses proof-tool isolation, versions and scale. LoopX can borrow the checked-small-kernel pattern without claiming natural-language intent or real-world effects are proven. Sources: [HERMES README](https://github.com/aziksh-ospanov/HERMES/blob/main/README.md); [AXLE, 2026](https://arxiv.org/abs/2606.26442).

**LoopX experiment:** Formalize one revision/lease/receipt state machine and compare it with the implementation. Candidate certificates bind state revision, action digest, authority scope, pre/postconditions and checker version. Invalidate certificates after relevant state changes.

**Acceptance and stop boundary:** Exercise stale owners, duplicate receipts, interruption and atomicity counterexamples. A hash proves integrity, not correctness; a proof covers its specification only. Keep scope narrow if specification maintenance exceeds demonstrated fault prevention.

### T06. Structured curriculum and complementary learning

**Scientific basis:** Neuroscience: experience structure, compositional learning and complementary timescales.

A September 3, 2026 Nature Neuroscience study combines mice, RNNs and entorhinal recordings to study how structured early experience changes later strategy flexibility. Software-agent transfer is a hypothesis, not a demonstrated consequence of this biological result. Sources: [Structured experience shapes strategy learning, 2026](https://www.nature.com/articles/s41593-026-02409-7).

**LoopX experiment:** Keep event evidence in a fast path; consolidate procedures slowly with applicability, exceptions, provenance and versions. Compare random, chronological and contrastive curricula using the same experience and budget on unseen composition tasks.

**Acceptance and stop boundary:** Measure forward transfer, retention and negative transfer. Do not claim external memory emulates biological weight learning. Stop if consolidation merely repeats successes or consumes hidden extra context.

### T07. Diversity-preserving capability evolution

**Scientific basis:** Evolutionary computation, evolutionary biology and quality diversity.

DGM branches from an archive of agents; AlphaEvolve combines program variation with evaluators; Imbue reports evolution-based code optimization. Non-winning ancestors may enable later improvements. Diversity must represent behavior or applicability, not renamed prompts. Sources: [DGM](https://arxiv.org/html/2505.22954v1); [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/); [Imbue, 2026](https://imbue.com/blog/2026-02-27-darwinian-evolver).

**LoopX experiment:** Evolve one bounded adapter, memory rule or planner proposal; compare a diverse archive against single-incumbent hill climbing under equal total budget. Retain lineage and negative results; freeze evaluators, authority and held-out tasks.

**Acceptance and stop boundary:** Require transfer, tail quality and reproducible improvement. DGM also reports objective hacking: evaluator integrity and independent validation are mandatory. Stop if archive maintenance costs exceed transferable gains.

### T08. Active information acquisition and experiment design

**Scientific basis:** Bayesian experimental design, active inference and dual control.

An action can both advance work and resolve uncertainty. Active Inference as Context Acquisition studies information gain under token budgets, but its fixed-table experiments disable tools and do not establish open-ended tool-use performance. Sources: [Active Inference as Context Acquisition, 2026, §9](https://arxiv.org/html/2608.19202v1).

**LoopX experiment:** Maintain a bounded set of competing hypotheses. For each probe specify possible observations and the decisions each would change. Compare decision-relevant information value with simple heuristics or Bayesian optimization; negative evidence can close a hypothesis.

**Acceptance and stop boundary:** Measure decisions changed, hypothesis elimination quality, outcome and cost. Pure entropy reduction can reward irrelevant curiosity. Defer full free-energy architecture unless it beats simpler local mechanisms.

### T09. Multiscale predictive control state

**Scientific basis:** Statistical physics, computational mechanics, coarse-graining and causal emergence.

Software in the natural world studies when macroscopic processes become informationally, interventionally and computationally self-contained. LoopX's effect/delivery/authority hierarchy is a testable abstraction hypothesis, not a structure already validated by physics. Sources: [Software in the natural world](https://arxiv.org/html/2402.09090v1).

**LoopX experiment:** Test whether compact state predicts delivery, blockers and recovery across providers, and whether full microhistory still adds material predictive information. Follow observational prediction with controlled interventions before claiming action equivalence.

**Acceptance and stop boundary:** Compare with simple observable features before neural world models. Preserve exact authorization and evidence regardless of learned predictions. Stop if transfer or state sufficiency cannot beat existing typed summaries.

### T10. Scientific experiment campaign infrastructure

**Scientific basis:** AI for Science: mathematical discovery, physical/material simulation and computational biology.

Co-Scientist explores hypothesis generation and iteration; a 2026 materials-lab Perspective discusses campaign management across experiments and resources. These are demand and architecture signals, not proof of LoopX adoption or commercial fit. Sources: [Co-Scientist, 2026](https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/); [Materials-lab Perspective, 2026](https://www.nature.com/articles/s43246-026-01219-5).

**LoopX experiment:** Start with a computational simulator and domain-owned evaluator. Track experiment intent, resource reservation, data/sample lineage, measurements, replication and decisions across interruption. Compare recovery, reproducibility and resource waste with the partner's baseline.

**Acceptance and stop boundary:** Require a real partner, accessible interfaces and measurable failure cost. Wet labs and hardware need domain safety controllers and explicit authority. Do not build a general autonomous-lab platform before a bounded collaboration validates demand.


## 6. Alternatives and design choices

Extend existing RFCs for stride, exploration, memory utility, dreaming and
capability evolution. Separate child contracts are justified for sequential
experiment evidence and decision-preserving continuation only when their
callers and acceptance units are explicit.

Prefer fixed-sample preregistration over invalid sequential inference; simple
thresholds over an unhelpful metacontroller; structured summaries over an
unvalidated memory learner; and model checking of one state machine over
repository-wide proof obligations.

Keep quantum computing/quantum cognition, neuromorphic hardware, programmable
biological systems and generic criticality/entropy scoring on watch. This
scan found no bounded LoopX caller and measurable near-term advantage for
them. Reopen with a domain partner, executable problem and defensible
comparison. Defer a monolithic free-energy architecture and a universal
world model until local mechanisms beat inexpensive alternatives.

## 7. Safety, privacy and compatibility

Research is opt-in and advisory until a reviewed treatment is admitted.
Feature-off parity is mandatory for any shared runtime change. Calibration
and proofs never grant permissions. Learned state cannot override current
goal intent, authority, exact evidence or tenant boundaries.

Keep sensitive trajectories and source material within their authorized
scope; public artifacts use synthetic or license-compatible evidence.
Cross-project learning requires explicit data-use authority, not merely a
shared provider. Do not use hidden verifier material, target answers or task
content to optimize the evaluated selector or reusable memory.

Conformal coverage depends on its assumptions; distribution shift can
invalidate calibration. Sequential evidence needs an appropriate conditional
construction and multiplicity treatment. Formal proofs cover specifications;
integrity hashes do not prove truth. Hardware and wet-lab actions require
domain-owned safety and authorization.

## 8. Migration and rollback

This proposal migrates no state and removes no fields. Each implementation
slice must specify opt-in admission, version pinning, preflight, outcome
readback and rollback before runtime changes.

Start with offline or shadow readers. Rollback disables the candidate policy
or provider and resumes the existing policy, retaining immutable evidence and
candidate provenance. Provider rank changes, memory edits or live state
cutovers require their owning lifecycle's reviewed migration; this umbrella
does not authorize them. A scientifically negative result is a valid
disposition, not permission to rerun until favorable.

## 9. Validation and acceptance

| Claim / track | Test or evidence | Required result | Boundary |
| --- | --- | --- | --- |
| T01: evidence remains interpretable under stopping | Null/effect simulations and matched task trials | Declared error control, power and cost reported | Assumptions and multiplicity explicit |
| T02: compression preserves decisions | Equal-budget contrastive continuation tasks | Better outcome/constraint retention at stated cost | Count retrieval and original-state reads |
| T03: control earns its overhead | Prospective comparison with cheap heuristics | Quality/cost/attention trade-off improves | No retrospective causal claim without support |
| T04: memory benefit is conditional | Replicated present/absent and justified interaction trials | Utility uncertainty and applicability measured | No hidden-answer selector |
| T05: formalized transitions match implementation | Independent state-machine oracle and counterexamples | Target invariants hold in stated model | No proof of unmodeled world effects |
| T06: curriculum aids transfer | Same-data, same-budget unseen composition tasks | Retention and transfer improve without excess harm | Biological-to-agent transfer unproven initially |
| T07: diversity yields reusable capability | Archive versus single incumbent | Held-out/tail gains exceed search cost | Frozen evaluator and permissions |
| T08: probes acquire useful information | Competing hypotheses and action-changing observations | Better decisions per total cost | Entropy reduction alone is insufficient |
| T09: macrostate transfers | Cross-provider prediction plus interventions | Advantage over simple typed features | Prediction is not causal sufficiency |
| T10: campaign support meets a real need | Partner baseline and interrupted simulator workflow | Recovery, reproducibility or resource savings | No wet-lab/production promotion |
| All active treatments | Deterministic conformance and feature-off parity | No extra authority, spend or calls while disabled | Required before runtime admission |

Research measurements are unverified until an experiment publishes its
versions, protocol, aggregate evidence and limitations. Documentation checks
only qualify this proposal. Predeclare effect or non-inferiority margins and
sample design; do not adopt an arbitrary score uplift as statistical proof.

## 10. Operational contract

This document cannot affect a running system. It adds no CLI, dashboard,
Lark, daemon or notification path. Later admitted experiments must expose
owner, budget, treatment version, evidence validity, failures and disable
instructions through their existing owning surfaces. Evidence gaps must
remain distinguishable from experiment failure and task failure.

The strategic data unit is scoped state and conditions → candidate actions →
allocation/choice → executed action → verified outcome → cost and human
attention → version and validity range. Ordinary logs alone do not create
causal advantage. Reuse requires comparable work, intervention coverage and
authorized data access.

## 11. Normative delivery plan

| Milestone | Proposed deliverable | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | Bilingual portfolio, tracker and Discussion | Public-source and overlap review | Ten tracks, ownership, source limits and next decisions visible | Withdraw proposal; retain discussion history |
| M1-A | T01 bounded evidence contract | Named owner; fixed treatments and outcome units | Null/effect tests and comparison protocol | Disable observer; preserve evidence |
| M1-B | T02 continuation projection experiment | One real continuation path and contrastive cases | Equal-budget outcome/constraint results | Restore current projection |
| M1-C | T03 stride shadow experiment | Cost data and admitted comparison design | Predictions/outcomes and full-cost baseline | Disable shadow policy |
| M2 | Selected T04–T08 experiment, not all at once | M1 evidence or explicit independent justification | Transfer, failure cases and cost accounting | Remove treatment under owning lifecycle |
| M3 | T09 research or T10 partner pilot | Named research/partner owner and bounded environment | Cross-provider or domain outcome evidence | End pilot; preserve provenance |

Proposed priority is M1-A, M1-B, M1-C. T05 may proceed independently when one
stable authority contract has a concrete verification owner. Horizons in
Section 3 do not impose deadlines or assign contributors. A merged RFC or
closed drafting PR does not complete the research tracker.

## 12. Open decisions

| ID | Decision owner | Options / recommendation | Evidence needed | Due before |
| --- | --- | --- | --- | --- |
| D1 | Maintainers + experiment owner | First workflow: continuation, benchmark or another real caller; recommend a small reproducible workflow | Outcome verifier and rights to run it | M1 protocol approval |
| D2 | Statistics reviewer + toolkit owner | Fixed-sample versus sequential construction; start fixed treatments and bounded episode outcomes | Dependence, allocation, stopping and multiplicity assumptions | T01 implementation |
| D3 | Context + stride owners | Protected distinctions and non-inferiority margin | Real failure cases and budget baseline | T02/T03 trials |
| D4 | Maintainers | Formal contract versus broader proof program; recommend one state machine | Stable specification and independent oracle | T05 |
| D5 | Research/domain owner | Macrostate study versus simulator partner; keep both visible, fund by evidence | Dataset or domain partner and success criteria | M3 |
| D6 | Maintainers + contributors | Staffing/budget allocation; prioritize three M1 slices and bounded long-term exploration | Available owners and costs | Resource commitment |

## Appendix A: Execution ledger

2026-09-15: public-source synthesis and documentation baseline inspection
produced this proposal. No new runtime, model experiment or benchmark result
was delivered. Source-reading scope appears below; documentation checks belong
in the PR. No effect on normative approval.

## Appendix B: Decision log

No implementation or promotion decision has been accepted. Publication for
review does not approve a scientific claim, resource allocation or runtime
change. Record future approvals with their public review links and affected
sections.

## Appendix C: Evidence registry and reading scope

Sources in T01–T10 are primary papers, proceedings, official research articles
or a project README. They support mechanisms and bounded external findings,
not LoopX uplift. This is a selective scan, not an exhaustive survey.

| Tracks | Reading scope | Result / limitation |
| --- | --- | --- |
| T01 | SAVI framework; ToolChain-CRC setup, method and assumptions | Mechanism reviewed; proofs and experiments not independently reproduced |
| T02 | DeMem setup/method and theory-to-practice limitations | Abstract guarantees distinguished from implementation |
| T03 | CCPO official abstract; DOLORES method and limitations | No LoopX cost/reliability result |
| T04 | CMI §3, scorer and annotation dependencies | Deployment claims require label-free evidence |
| T05 | HERMES README tool contract; AXLE abstract | No LoopX state-machine proof |
| T06 | Publication date, abstract and Main of the neuroscience study | Specific mouse/RNN task; software transfer unverified |
| T07 | DGM archive mechanism and objective-hacking discussion; official AlphaEvolve/Imbue articles | No local reproduction or capability promotion |
| T08 | Context-acquisition method scope and §9 limitations | Restricted attributes; tools disabled in cited experiments |
| T09 | Original multiscale closure framework | Theoretical inspiration, not an agent-system guarantee |
| T10 | Co-Scientist official research article and materials-lab Perspective | Perspective is a proposal, not deployment or commercial proof |

## Appendix D: Deferred alternatives

Quantum/neuromorphic/biological backends, generic entropy scoring, universal
world models and whole-kernel self-modification remain deferred for the
reasons and reopening conditions in Section 6. Retain negative results and
superseded designs with the invariant they failed.

## Appendix E: Review lessons

Scientific novelty, implementation availability and deployment evidence are
different facts. Target-answer access, annotation-assisted selection,
unmeasured controller cost, repeat testing and weak transfer can each make
an attractive paper unsuitable for immediate promotion.
