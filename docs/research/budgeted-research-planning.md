# Budgeted Research Planning: Goals, Theory and Design Rationale

[中文](budgeted-research-planning.zh-CN.md)

This companion explains **why** LoopX should compare conditional research plans
by their final accepted outcomes within a resource budget. It supports the
[joint Target/Yardstick/Stride RFC](../architecture/rfcs/target-yardstick-stride-decisions-v0.md#budgeted-planning)
and the [EdgeBench pilot](../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#edgebench-budgeted-planning).
Those RFCs own requirements and acceptance; this is an explanatory design basis,
not a new runtime policy, empirical result or mandatory per-Turn prompt.

Read §§1–3 for the objective and planning model, §§4–6 for feedback and evidence,
and §§7–9 for the existing implementation, first experiment and learning path.
All numerical examples below are illustrative, not benchmark measurements.

<a id="objective"></a>

## 1. The goal: better use of a long budget

LoopX keeps goals, state, evidence and decisions available across work sessions.
That infrastructure is useful only if it helps an agent deliver better outcomes
at comparable total cost, or comparable outcomes at lower cost and attention.
More durable records, longer activity and more replans are not that outcome.

There are two research loops:

- **Within a task:** improve candidate artifacts, learn which routes work and
  choose measurements while the budget runs down.
- **Across tasks/runs:** improve the research policy itself, eventually learning
  forecasts or allocation rules from qualified experience.

Success in the first loop does not establish recursive improvement of the second.
Near-term work uses the current model and existing decision owners to improve
EdgeBench continuation. A learned Meta Model is a later research stage, not a
prerequisite for testing whether better planning instructions help.

Let x0 be the starting candidate, X_B^π the candidate selected by the task's
declared terminal rule after policy π uses budget B, and F the accepted outcome
in a higher-is-better orientation:

$$
J_\pi(B\mid s_0)=\mathbb E[F(X_B^\pi)\mid s_0].
$$

For a common starting state, maximizing J also maximizes expected improvement
over F(x0): the common baseline cancels. Precise knowledge of the initial
absolute score is not required to compare policies, but starting evidence,
headroom, candidates and evaluator state still matter.

The practical target is incremental benefit over a strong continuation:

$$
\Delta J(B\mid s_0)
=\mathbb E[F(X_B^\pi)-F(X_B^{\pi_0})\mid s_0].
$$

If both start at 60, LoopX reaches 70 and the comparator reaches 68, within-run
progress is 10 while incremental benefit is 2. The comparator keeps working;
it is not a frozen starting artifact. The full experiment therefore retains
strong Official continuation and current LoopX, not only a weakened ablation.

Budget B can constrain wall time, tokens and evaluator resources separately.
Equal wall time does not imply equal compute. Planning, context reads, failed
experiments, yardstick repair and confirmation consume those resources. Parallel
durations cannot simply be summed into wall time. Under a fixed-budget outcome
objective, the opportunity cost is the other work displaced; do not also subtract
an invented score penalty for the same expense. A monetary objective requires an
explicit conversion to utility.

<a id="planning-model"></a>

## 2. From selecting fixed candidates to creating better ones

[Selecting Computations](https://aima.cs.berkeley.edu/~russell/papers/uai12-meta.pdf)
studies computations that reveal information about fixed candidates. Program
research also changes candidates and the instruments used to evaluate them:

| Activity | What changes | Why it can matter at the horizon |
| --- | --- | --- |
| Test an unchanged candidate | Belief about its quality | Select the right candidate or abandon a misleading route |
| Change an implementation or algorithm | The candidate itself | Create an outcome that was not available before |
| Build/calibrate a yardstick | Future measurement quality/cost | Make many subsequent decisions more informative |

These benefits can overlap; they are not independently additive reward terms.
Repeated measurements of different code versions are not iid samples of one
fixed success probability. A low score may be useful evidence, and a high proxy
score may contribute nothing to final accepted quality.

For explanation, suppose action a uses known positive resource k(a). Let b be
remaining budget and G(s) the expected outcome if research ends now under the
declared candidate-selection rule:

$$
V^*(s,b)=\max\left\{G(s),\
\max_{a:k(a)\le b}\mathbb E[V^*(S',b-k(a))\mid s,a]\right\}.
$$

This is a **budgeted** Bellman model, not the paper's utility-minus-cN objective.
The expectation includes all later choices, not just the next score delta.
Real actions have uncertain duration, overlapping resource occupancy, pending
results and evaluation availability; implementations must preserve those facts
rather than pretend every command is one equal-cost step.

A useful state distinguishes candidate versions and retained valid results,
unresolved prerequisites, evidence scope/uncertainty, pending experiments,
remaining resources and time for final confirmation. A posterior mean alone
cannot distinguish little evidence from strong evidence with the same mean.
The simple Beta model admits two sufficient counts; real research does not
therefore admit a universal two-number state or require rereading every log.

Stopping a route can free resources for another. Ending the whole run has no
automatic budget-saving benefit when unused budget has no other value. Existing
completion, exhaustion, permission and resource limits remain legitimate stops;
the objective does not require useless activity until a timer expires.

## 3. Why compare plans, not just immediate gain/cost

Information is valuable when it can change a useful later decision. A first
probe may not change today's winner but may enable a second probe that does.
Likewise, a correctness harness or algorithm skeleton can be necessary before
any scored improvement becomes possible.

A deterministic example exposes the limit of greedy ratios. With budget 8,
action A costs 1 and gains 2 but is available only twice; plan B costs 8 and
gains 10 only on completion. A has the higher immediate gain/cost, but taking
it first prevents completing B and yields at most 4. Ratios can screen routes;
they do not solve dependencies, indivisible investments or deadlines.

Priority also depends on competing routes and the remaining horizon. Even
independent candidate uncertainties do not imply independent decision values:
changing the best alternative can change which candidate is worth measuring.
The paper's non-indexability example rules out a generally optimal ranking
based only on each candidate's own state.

Use a short conditional continuation: necessary setup, an observation that
distinguishes outcomes, and the next response to each material outcome.
Execute its first coherent slice and reconsider when useful evidence arrives.
Long-term planning means allowing justified delayed payoff, not protecting a
plan from counterevidence or demanding a method switch at every review.

### Where the expectations come from

Bellman notation does not provide a transition model. In the paper, a posterior
can explicitly predict the next observation; program research usually has only
mechanism arguments, comparable experience and uncertain estimates.

For a synthetic example, a safe route has expected gain 2.8, while a new route
adds 20 with probability p and otherwise retains the same valid incumbent.
Both routes include all work through the same deadline. The new route wins when:

$$
20p>2.8\quad\Longleftrightarrow\quad p>0.14.
$$

The useful question is whether evidence puts p above that threshold, not whether
an LLM can confidently invent “25%.” Use mechanism/headroom analysis, cheap
discriminating probes, comparable past episodes, ranges and sensitivity checks.
Record predictions before results, then calibrate success, gain and duration
against what happened. Missing evidence remains unknown.

### What the finite dynamic program teaches

The paper's VPI/c bound limits **expected** computation count; it is not a safe
fixed truncation depth. For one Beta(1,1) candidate versus known λ, a stronger
condition guarantees stopping once c ≥ λ(1−λ)/(n+3). This gives a depth
H=O(1/c). Success/failure histories with the same counts (a,b) merge, leaving
only 1+2+…+(H+1)=O(1/c²) states. Backward induction compares stopping with
−c+pV(a+1,b)+(1−p)V(a,b+1), using complete continuation values.

The paper precomputes 129 λ grid points and interpolates. Its blinkered policy
evaluates one direction at a time against the best other current mean, then
reconsiders after an observation. Precomputation is O(D/c²), while scanning k
candidates online is O(k). Grid interpolation and single-direction planning
remain approximations to the general multi-candidate problem.

The transferable idea is to reuse valid sufficient state and amortize repeated
decision work. It is not a claim that LoopX has this small state space. Learned
or cached estimates must still match the candidate, measurement assumptions and
remaining horizon; c=0 cannot be substituted into these bounds for a hard-budget
task. A full dynamic-programming solver is not required for the first pilot.

<a id="feedback"></a>

## 4. Milestones are state before they are reward

“Executable → correct → within the tail-latency limit → improved native score”
describes dependent conditions. They can be reversible, shared across routes or
replaced by an alternative route. If all are necessary, their joint probability
uses conditional factors, not a product of independent marginal success rates.

Keep three feedback meanings distinguishable:

| Feedback | Example | What it does not establish |
| --- | --- | --- |
| Accepted outcome change | A valid candidate improves the task outcome | Which previous action caused the whole gain |
| Progress state | A prerequisite now holds for this version | Guaranteed final improvement |
| Information change | A counterexample defeats a local assumption | A better artifact already exists |

This state helps choose the next work without awarding one point per milestone.
Counting easy steps, repeatedly repairing the same defect or ignoring regressions
can produce reward without improving the intended result.

For a fixed scalar outcome rule, signed differences telescope:

$$
\sum_t[F(x_{t+1})-F(x_t)]=F(x_T)-F(x_0).
$$

Do not add the terminal outcome again or discard negative changes. If the task
selects a best valid submission, use that declared quantity consistently rather
than mixing it with current-workspace quality. Repeated selection on noisy
measurements still needs evaluation safeguards.

For a future learner, potential-based shaping offers a more general accounting:

$$
r'_t=r_t+\Phi(s_{t+1})-\Phi(s_t),\qquad
\sum_t r'_t=\sum_t r_t-\Phi(s_0).
$$

in the undiscounted finite task when every true terminal potential is zero and
time/budget is represented as needed. Discounted tasks use γΦ(s')−Φ(s);
truncation is not termination, and changing the potential online needs consistent
accounting. Policy invariance does not guarantee easier finite-budget learning.
The potential difference is a trajectory change, not a causal action label.
These are design constraints for a later learner, not a new runtime reward API.

<a id="measurement"></a>

## 5. Yardstick: information quality is part of the plan

A useful local measurement can be modeled as:

$$
Z_{\mathrm{local}}(x)=F(x)+b_{\mathrm{local}}(x)+\epsilon.
$$

More local samples can reduce random noise ε without removing systematic bias b.
A local holdout still uses that measurement system. Mean improvement can also
hide a correctness or timeout violation that invalidates the whole submission.
[Multi-fidelity optimization](https://proceedings.mlr.press/v70/kandasamy17a.html)
provides this problem frame; its particular surrogate assumptions are not
automatically valid for program search.

Check fidelity at actual selection decisions: matched parent/child or sibling
candidates, exact workload/evaluator versions, and relevant validity boundaries.
High correlation across a rising historical score curve can merely track time;
it need not distinguish the next useful change. When affordable and informative,
audit uncertain or proxy-rejected candidates too, so false negatives are visible.
A reversal is scoped counterevidence, not proof that a whole evaluator family
must be permanently blacklisted.

Native confirmation can qualify a candidate, redirect research or calibrate the
proxy. Its value includes how it changes later choices. A pending result is not
failure or measured gain. Cooldown and queue availability are separate from
compute cost; waiting may overlap independent work. Plan backward from the
deadline so a candidate can actually receive the confirmation required by the
frozen terminal rule. Do not manufacture extra evaluation opportunities.

## 6. Why action logs are not causal labels

The observed fraction of gains after grow, verify or switch mixes different
starting states. Grow may be selected after promising evidence; switch after a
route stalls. Their raw frequencies cannot rank the actions at one shared state.
Nor does a flat incumbent prove a particular check prevented a regression.

A local comparison can instead target:

$$
\tau_B(s;a,a_0,\pi)
=\mathbb E[Y_B^{a,\pi}-Y_B^{a_0,\pi}\mid S_0=s].
$$

This compares intervening with a or a0 at the same start and then using the same
continuation **rule** π within B. Later actions can differ in response to new
evidence; forcing identical later actions would remove the information benefit.
Already spent resources and the cost of the first action must be accounted for
consistently.

Credible experiments need well-defined treatment versions (consistency),
comparable or randomized assignment (exchangeability), and coverage of feasible
alternatives in target states (positivity). Shared mutable memory, evaluator
sessions or caches can create interference. Repeated seeds improve precision
but do not fix missing state or unobserved alternatives.

Two generalization questions remain separate:

1. Does a candidate improve beyond checks used to develop it?
2. Does the controller improve outcomes on tasks not used to tune/select it?

Freeze the controller and evaluation protocol before a new-task test. This
freezes how feedback is used, not the evolving candidate or the agent's ability
to react to permitted Native feedback during that test. Repeatedly tuning the
controller on test outcomes consumes that test set's independence. A checkpoint
fork estimates continuation from that state; it does not establish the
from-start effect of the full policy.

<a id="worked-loop"></a>

## 7. A complete decision-to-outcome example

The following is a synthetic interpretation of the existing decision path,
not a schema or prescribed tool order. The run has a valid incumbent, a local
speed gain with untested tail behavior, another feasible route, a pending
evaluation window and a fixed deadline.

| Boundary | Reasoning and work | Durable evidence / next decision |
| --- | --- | --- |
| Restore state | Check candidate/evaluator versions, incumbent, pending work and remaining resources | Unknown or omitted feedback remains a retrieval need |
| Compare continuations | Continue tuning versus establish a discriminating stress check, then confirm if useful | Explain the prerequisite, expected range and comparator |
| Forecast before work | State which result would justify more investment, which would refute it, and whether confirmation can finish | Prediction and source basis in existing reasoning/finding |
| Execute a bounded slice | Run the selected check; independently useful work may continue while waiting | Actual method, artifact, result and resource use |
| Interpret feedback | Suppose the fast candidate violates a tail constraint | Revise that route/yardstick scope; preserve the valid incumbent |
| Continue and confirm | Repair or pursue the alternative, using the remaining authorized opportunity | Bind accepted result to the exact candidate and frozen rule |
| Assess policy benefit | Compare matched continuation or complete runs | Good local reasoning alone is not evidence of aggregate gain |

The useful loop is: **decision-time state → prediction and choice → actual work
and cost → observation → revised continuation → accepted terminal outcome**.
Keep failed/cancelled work, delayed results and unknowns. Attribute dependencies
across the sequence instead of giving all terminal gain to its last action.

<a id="current-and-next"></a>

## 8. Existing owners and the smallest useful next experiment

Source snapshot:
[bd2af5e](https://github.com/loopx-project/loopx/tree/bd2af5e0ec5acc9eafb0b0eb6b3d4661206a2eef).
This audit describes source, not installed adoption or measured benefit.

| Owner / boundary | Available basis | What remains to establish |
| --- | --- | --- |
| [Stride](https://github.com/loopx-project/loopx/blob/bd2af5e0ec5acc9eafb0b0eb6b3d4661206a2eef/loopx/control_plane/work_items/stride_policy.ts) | Decision axes, next probe, previous review; bounded Explore measurement/yardstick feedback | Whether the model uses it to improve the next continuation |
| [Yardstick](https://github.com/loopx-project/loopx/blob/bd2af5e0ec5acc9eafb0b0eb6b3d4661206a2eef/loopx/control_plane/work_items/yardstick_decision.ts) | Profiles, cited measurements, ordering counterevidence, proposed method/cost boundary | Calibrated fidelity, transition/value prediction and useful confirmation choices |
| [Followthrough](https://github.com/loopx-project/loopx/blob/bd2af5e0ec5acc9eafb0b0eb6b3d4661206a2eef/loopx/control_plane/work_items/replan_context.ts) | Previous decision and subsequent evidence | Actual adoption, causal contribution and total outcome benefit |
| [EdgeBench adapter](https://github.com/loopx-project/loopx/blob/bd2af5e0ec5acc9eafb0b0eb6b3d4661206a2eef/benchmark/edgebench/README.md) | Frozen feedback/selection, submission constraints and result lineage | A qualified same-state restore for each proposed checkpoint comparison |

PRs [#8382](https://github.com/loopx-project/loopx/pull/8382) and
[#8385](https://github.com/loopx-project/loopx/pull/8385) are merged at this snapshot.
They make evidence reachable; they do not supply numerical fidelity, an optimal
planner or a learned policy. The current single-agent path is sufficient for
the first treatment; a second controller session is not a prerequisite.

The [existing pilot contract](../architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md#edgebench-budgeted-planning)
remains the experiment owner:

1. Pin a bounded planning instruction on the existing Stride/Yardstick/Explore
   path, keeping defaults and wire fields unchanged. Demonstrate actual
   decision-to-work readback before expensive comparisons.
2. Compare current LoopX with that treatment at prospectively selected, genuinely
   restorable checkpoints. The proposed 3 tasks × 2 checkpoints × 2 arms ×
   3 hours is 36 additional worker-hours, not the total study cost.
3. Account separately for creating those states, control and evaluation. Preserve
   Native history/cooldown; if equivalent restoration is unavailable, use a
   correctly labeled from-start comparison. An uninformative short horizon does
   not justify relaxing the protocol.
4. Follow promising evidence with the proposed matched 18-hour from-start
   comparison against strong Official continuation and current LoopX. Freeze
   repeats, margins, resource caps, selection and analysis before confirmation.

A successful first result is a better accepted outcome after paying all overhead,
supported by trajectories showing what decision changed and why. More complete
forms, extra Native calls or a plausibly intelligent explanation are insufficient.
Detailed task choice and run authorization remain in the pilot, not this essay.

## 9. From useful feedback to a learned research policy

Use the existing decision/evidence owners through three stages:

| Stage | Learn or demonstrate | Evidence that can support it |
| --- | --- | --- |
| Current-model planning | Better choices at useful evidence boundaries | Adopted work and matched outcome/cost comparisons |
| Calibrated prediction | More reliable gain, duration and failure forecasts | Predictions recorded before outcomes, evaluated on held-out episodes |
| Learned allocation | Better policies for state and remaining budget | Prospective policy comparisons and frozen new-task tests |

Each training example should connect what was visible at decision time,
considered alternatives and predictions, the selected action and actual work,
resource use, successor evidence and later terminal outcome. These are semantic
requirements on useful data, not additional unsupported fields on current v0.
Keep task/source permissions and candidate/version lineage.

Selected-action logs can train predictors but do not reveal every unchosen
outcome. Proxy/Native pairs can calibrate measurement relationships, and paired
forks can estimate local treatment differences; neither automatically supplies
all action values. Offline policy evaluation needs its own coverage and
identification assumptions. Future peaks, later test answers and post-hoc
interpretations must not leak into decision-time features.

For an implementation or document handoff, preserve the chain:
**objective and comparator → theoretical reason → existing owner → proposed
change → evidence that would show benefit**. Keep source implementation,
forecast, hypothesis and measured result distinct. A later proposal should
change a design because a specified assumption or observation warrants it,
not silently promote this explanatory model to a solved optimizer.

## Sources and scope

- [Selecting Computations, §§2–6](https://aima.cs.berkeley.edu/~russell/papers/uai12-meta.pdf):
  fixed-candidate metareasoning, complementary information, stopping,
  context-dependent priorities and blinkered/VOI approximations.
- [BOCA, §§1–3](https://proceedings.mlr.press/v70/kandasamy17a.html):
  costly target evaluations and cheaper fidelities; its surrogate assumptions
  are not established for arbitrary code modifications.
- [Potential-based shaping, §3](https://people.eecs.berkeley.edu/~russell/papers/icml99-shaping.pdf):
  reward-transformation conditions, not evidence that every milestone reward
  accelerates learning.
- [What If, Ch. 1–3](https://miguelhernan.org/s/hernanrobins_WhatIf_19aug26.pdf):
  potential outcomes, randomized comparisons and identification assumptions.
- [DuoDiscover](https://arxiv.org/html/2610.17184v1) and
  [LoopX feedback analysis](https://loopx-project.github.io/loopx/blog/zh/edgebench-feedback-and-memory/):
  motivation for target/yardstick choice and separating startup, continuation
  and retained outcomes; neither proves this planning treatment works.

The budgeted candidate-creation model and the links between these theories are
design synthesis. No new benchmark run, training or policy-benefit measurement
was performed to produce this document.
