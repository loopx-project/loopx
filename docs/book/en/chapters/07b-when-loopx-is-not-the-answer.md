# When LoopX is not the answer

After understanding the architecture, decide which tasks benefit from a control plane. Consider state coordination and failure cost: session changes, external waits, handoffs, and the consequences of repeating an operation.

## Decide whether onboarding earns its cost

Suppose you want to add type annotations to one function, validate it in the current session, and can cheaply redo it. Connecting a project and maintaining a Goal, Todos, and receipts may cost more than the task. A plain session is often enough.

If the same fix requires waiting for CI, several review rounds, and another Agent taking over, external state becomes useful. The next executor can identify verified results and outstanding decisions.

A short task can still contain high-risk effects. Duration alone does not determine whether governance is useful.

## Four responsibility boundaries

### One: the control plane manages acceptance; the domain defines success

LoopX records Goals, acceptance, queues, authority, and evidence. Capabilities can support domain-specific judgments. A done record alone cannot establish code correctness, statistical significance, or accepted writing.

Before onboarding, identify the observation that would support completion. Exploratory tasks can use intermediate acceptance, such as a testable hypothesis and next experiment. Human judgment can remain behind a corresponding Gate.

**Usage question:** who accepts the result, from which evidence, and what happens if it fails acceptance?

### Two: governance costs work; measure performance through the real path

Reading state, admission, validation, and writeback add work. Avoiding repeated execution and unnecessary wakes can reduce total cost. Measure single-turn latency and end-to-end completion separately; neither must necessarily get worse.

A control plane does not directly make a model response faster. It may improve a journey whose delays come from rebuilding context, unattended waits, or repeated work.

**Usage question:** for the same task, compare completion time, actual resource consumption, and human intervention. Check whether recovery benefits offset maintenance cost.

### Three: durable state and scoped memory have separate jobs

The [state substrate](state-substrate.md) owns work-lifecycle facts. LoopX also supports source-bound, scoped, revocable Agent preferences, alongside decision context, reward memory, and turn-recall capabilities or integration boundaries.

This does not make arbitrary chats a permanent knowledge base. Preferences grant no action authority. Recalled content needs source, freshness, and scope checks; availability depends on release, configuration, and provider.

**Usage question:** are you retaining a Todo or receipt, an explicit user preference, or external knowledge material? Select its existing owner before deciding how to store and recall it, rather than putting every conversation in active state.

### Four: the Host executes; the control plane governs work lifecycle

LoopX determines eligible work, settlement, and recovery boundaries. The Host provides model sessions, tools, and the concrete execution environment. Adapters connect them; visibility, background wakes, and recovery differ by Host.

The control plane can improve planning, collaboration, and validation. It cannot guarantee every model inference or grant permissions the Host lacks. Switching Hosts still requires capability and identity checks.

**Usage question:** is the gap execution capability, or missing state, coordination, and recovery? Choose a combination that addresses that gap.

## When a plain session is enough

| Task characteristic | Decision basis |
| --- | --- |
| Closed scope, sufficient current context | Additional state management may offer little value |
| No external waits or handoffs | No need to restore waiting and ownership across sessions |
| Cheap retries, no irreversible effects | Simple retry may suffice |
| You can accept the result immediately | A complete long-term acceptance workflow may be unnecessary |

When these hold together, a plain session is reasonable. For longer work, use the [qualification card](02-session-goal-loopx.md) to identify what needs persistence and governance, then start with one verifiable task.

## How to test the choice

Complete onboarding and one delivery, then exercise a wait, session change, or recovery relevant to your risk. Check readable state, retained results, and an actionable failure path. Count added maintenance and human effort as costs.

| Boundary | Evidence entry | What it does not promise |
| --- | --- | --- |
| Goal and domain acceptance | [Goal Vision/replan contract](/loopx/docs/reference/protocols/goal-vision-replan-contract-v0/) | Every done record proves the business objective |
| Preferences and recall | [Semantic Preference](https://github.com/loopx-project/loopx/blob/main/loopx/capabilities/semantic_preference/README.md) | Automatic chat persistence or authority grants |
| Host integration | [Runtime Connector Catalog](/loopx/docs/integrations/runtime-connector-catalog/) | Equal wake and recovery capabilities across Hosts |
| Long-running cost | S7/S10 of the [overall roadmap](/loopx/docs/architecture/rfcs/loopx-overall-roadmap-v0/) | Proven performance at arbitrary scale, duration, or failure conditions |

Apply the architecture choice to the task: which repetition and coordination costs does it remove, which responsibilities does it add, and do its current boundaries cover the failures you care about?