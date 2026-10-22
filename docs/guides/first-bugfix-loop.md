# Your first bug-fixing loop

[中文](first-bugfix-loop.zh-CN.md)

Start with a tiny checkout-pricing bug. See a test fail, let an agent repair
it through LoopX, rerun the tests, and inspect a local commit. Then reuse the
same workflow for a recurring bug hunt in your own repository.

You need Python 3.11+ for the exercise. To use LoopX, also install Node.js
22.22.3+ (24 LTS recommended), LoopX, and an agent host you already use. The
tests themselves need no third-party packages or model access.

## 1. Prepare a small, disposable project

Open the [practice project](../../examples/bugfix-loop/README.md). Download its
`cart.py` and `checks.py` into a **new directory outside the LoopX checkout**.
The example files belong to the repository revision you are reading; an older
installed LoopX release does not necessarily contain them. No LoopX source
clone is needed for ordinary use.

In a terminal, enter that directory and run:

```bash
python3 checks.py
```

Expected: **six checks, two failures, exit status 1**. This is the intended
starting point. If Python or imports fail, fix that setup error before asking
the agent to repair the pricing logic.

Prices are integer cents. An empty cart costs zero. Orders totaling at least
10,000 cents ship free; other nonempty carts add 500 cents. Negative prices
are rejected and inputs must not be modified. The two initial bugs violate
the empty-cart and exact-threshold rules.

Initialize the copied project and record the intentionally broken baseline:

```bash
git init -b main
printf '__pycache__/\n.loopx/\n' > .gitignore
git add cart.py checks.py .gitignore
git commit -s -m "demo: record intentionally failing cart exercise"
git switch -c codex/cart-bugfix
```

If Git asks for your name/email, configure your own commit identity and retry.
The baseline commit intentionally has failing tests. A repair commit must pass
the checks below. There is no remote, push, or merge in this local exercise.

## 2. Install once, then choose the actual host

Follow [Installing LoopX](installing-loopx.md) if it is not installed. A normal
Python package installation uses:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

Resolve missing Python, Node.js, or CLI readiness reported by `doctor` before
starting. Reuse the same installation owner for upgrades; do not install
several competing copies to work around a setup error.

- **Codex App:** open the copied project as the working folder. Send the
  `$loopx` task below in its project thread. The App uses a heartbeat automation.
- **Codex CLI:** run `codex` from the copied project directory. Send the same
  `$loopx` task inside Codex, rather than in your shell. CLI continuation uses
  the visible native `/goal`. OpenAI documents Goals from Codex 0.128.0 onward;
  check that your build exposes `/goal`.
- **Claude Code:** open the same folder and replace `$loopx` with `/loopx`,
  using the installed command skill. See the
  [host driver guide](newcomer-command-path.md#choose-the-loop-driver).

In Codex, `$loopx` or `/skills` → `loopx` invokes the skill explicitly.
User-installed native `/loopx` commands are not the Codex entry. The generated
LoopX facade disables implicit invocation, so plain task text alone does not
guarantee that LoopX starts. If the skill is absent, check the installation
used by that Codex process, including its `CODEX_HOME`; restart after correcting
the installation if the skill is still missing.

## 3. Give the agent one complete task

Paste this **inside your agent**, with the copied project as its working folder:

```text
$loopx Repair this cart-pricing exercise. First run python3 checks.py and
explain the failures against the requirements in cart.py. Fix the empty-cart
and free-shipping-boundary bugs. Preserve all six existing checks, negative
price rejection, and the caller's input list. Add a regression only if it
covers a missing requirement; do not weaken tests to make them pass.
Use the project's existing LoopX connection if present. Otherwise complete
guided setup for this actual host, with its registered Agent and useful Todos.
Confirm the host's continuation mechanism is active; initialization alone
is not completion. Follow LoopX's current quota and stop conditions.
Run all six checks after the change and show the actual diff and results.
Leave the patch uncommitted so I can inspect and commit it in step 5.
Do not push, create a remote repository, merge, or call additional external services.
Stop when these requirements are verified, or explain a concrete blocker.
```

You should see a failure reproduction, a small patch, and a final test result.
The exercise ends when the six checks pass and the diff preserves the
requirements. It does not need to run forever.

### Confirm that it actually started

Ask the agent to report the current LoopX goal, its Agent, the next Todo, and
the active host driver. **A generated setup packet is still a preview.**

In Codex CLI, enter `/goal` to inspect the current native Goal. If activation
is missing, ask the agent for the exact generated `/goal <task_body>` text and
paste it in the same CLI session. Keep the generated LoopX loader rather than
substituting a bare objective that skips LoopX's guards. For bootstrap recovery,
run `loopx codex-cli-bootstrap-message --project .` in the project terminal and
follow its setup message in Codex; then resume the repair task.

In Codex App, verify that the corresponding heartbeat automation was actually
saved. If automation access is unavailable, the agent must report that exact
activation gap rather than say that autonomous execution is ready.

Both paths require their host to remain available. Installing LoopX does not
keep a closed terminal or unavailable App running.

## 4. Turn the workflow into a recurring bug hunt

Use this on a real project after completing the small exercise. Replace the
scope and test command with your project's actual ones:

```text
$loopx Keep checking <owned module or directory> for reproducible bugs.
Start by reading repository instructions and identifying the relevant test
command, scope, and existing budget. Work in the repository's isolated branch
or worktree. For each real bug: reproduce it, add a failing regression, make
the smallest coherent repair, rerun that regression and the affected suite,
then prepare a signed-off commit and reviewable PR under repository rules.
Record the result and next action in the existing LoopX work.
If no new evidence justifies work, wait according to LoopX's scheduler and
record the next meaningful trigger instead of repeating the same empty scan.
Honor the current quota and spending boundary. Report environmental failures
and unresolved tests honestly. Keep merge and release for my explicit decision.
```

Here, “infinite loop” means authorized work can continue or resume. Each repair
still has a finish line. Quiet monitoring should wait for a meaningful event
or its scheduled check; a higher wake frequency does not prove useful progress.
Start with one module, a known test command, and a spending boundary you accept.

To pause, ask the agent to pause this LoopX goal and its corresponding driver,
then read back both states. In CLI you can first use `/goal pause`; in App pause
the matching automation. Pausing the host driver and pausing the LoopX goal are
separate state changes. Do not delete project state to stop a loop. To resume,
open the same project, explicitly resume the same Agent lane, read its existing
state and last evidence, and reactivate the appropriate host driver.

## 5. Test, inspect, commit, then request review

For the local exercise, run these yourself after the agent finishes:

```bash
python3 checks.py
git diff --check
git diff -- cart.py checks.py
git status --short
```

Expected: **six checks pass, exit status 0**. Confirm that the agent fixed
`cart.py` rather than deleting checks, weakening expectations, or changing the
shipping rules. A test that never ran is not a pass.

Then commit only the intended repair files; normally that is `cart.py`:

```bash
git add cart.py
git diff --cached
git commit -s -m "fix: handle empty carts and free-shipping boundary"
git show --stat --oneline HEAD
```

If you intentionally added a useful regression, inspect and stage `checks.py`
explicitly too. Keep `.loopx/`, credentials, local settings and raw agent logs
out of commits. The standalone demo is now complete; no remote is required.

For your real repository, use its required tests and review process. Confirm
the correct remote and target branch, push the work branch only within your
authorization, and create a PR with the actual cause, behavior change and test
results. Read back its head and checks. A local commit, a pushed branch, an open
PR and a merged PR are different outcomes; CI or review feedback may require
another repair. Creation of a PR does not authorize merging it.

## Setup effort, ongoing cost, and limits

The copied exercise needs two files and the Python standard library. LoopX
adds one-time CLI/skill installation and host activation. After an upgrade,
check that the intended host still finds the skill and can activate its driver;
separate Codex profiles can use different installation directories.

The six local checks make no model or paid-service calls. An agent-driven
repair consumes the host's model allowance and local test resources. Recurring
automation adds wakeup and checking work; frequency, context size, retries and
provider pricing determine the cost. This guide makes no fixed price or saving
claim. Use the current LoopX quota and scheduler rather than a shell `while true`.

The fixture's red → green result verifies its pricing behavior. It does not
prove that an unattended host will resume indefinitely or that every real bug
will be found. For broader operation, continue with
[long-running coding agents](long-running-coding-agents.md). For Codex-native
Goal behavior, see [OpenAI's Goals guide](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex).
