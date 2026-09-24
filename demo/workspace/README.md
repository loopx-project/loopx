# Workspace stories

Explore three connected projects in the real LoopX Personal Workspace: a 120-person community event, a home-energy buying guide, and a neighborhood website release. Each has four work roles, 18 delivery tasks across multiple phases, two owner decisions and two scheduled watches.

From a source checkout with Python 3.11+ and Node 22.22.3+:

```bash
python -m demo.workspace serve
```

Open the printed loopback URL. The command creates a disposable directory and serves the existing packaged frontend through the standard Chat backend. For a repeatable location:

```bash
python -m demo.workspace prepare --root /tmp/workspace-stories
python -m demo.workspace serve --root /tmp/workspace-stories --port 8791
```

Only a new empty directory or this demo's matching manifest is accepted. Prepare preserves an existing replay; choose a new directory to start again. Older manifest versions require a new directory.

## Projects and decisions

| Project | Work and constraints | Decisions |
| --- | --- | --- |
| Riverside Community Day | Three-venue comparison, $6000 limit, $5400 plan, $600 contingency, revised catering quote, access review, 18 volunteer shifts and two first-aid gaps | Venue reservation; invitation approval |
| Home Energy Buying Guide | 12 source cards, three household profiles, 27 tariff/efficiency combinations, conflicting assumptions, five-section editorial plan | Cost assumptions; publication approval |
| Riverside Neighborhood Website | Six pages, 18-route inventory, 24 accessibility criteria, navigation/form review findings, content permissions, rollback and handoff | Content freeze; deployment approval |

Each project has seven replayed completion checkpoints, five ready tasks, four blocked tasks and two deferred follow-ups. Dependency notes retain predecessor IDs; deferred tasks use real `todo_done` resume conditions. Two watch-only monitors have cadence and next-due metadata. No scheduler or live Agent is started by the demo.

Switch Board/List, filter by Agent, expand completed history, inspect the owner decisions and scheduled watches. `BRIEF.md`, `working-table.csv`, `calculations.json` and `DELIVERY-PLAN.md` preserve the planning inputs and dependencies. The energy sensitivity table and event contingency are calculated when preparing the workspace.

Replay one decision:

```bash
python -m demo.workspace advance --root /tmp/workspace-stories \
  --story research-brief --decision assumptions
```

Refresh the UI afterward. Only that decision and its direct blocked successors are advanced. Other owner decisions and downstream blockers remain intact. The command does not purchase, publish, deploy or start an Agent.

## Data and isolation

These are authored scenario replays using real LoopX APIs and state transitions, not customer case studies or receipts of live Agent execution. Natural project titles keep the interface readable; the manifest and completion evidence retain provenance. Source-card inventories and website checklists are planning inputs, not claims of external research or executed website tests. Event money is a project budget, not model spending.

The demo does not import personal registries, session history or credentials, and does not sync into the global registry. The loopback server uses a separate HOME/CODEX_HOME, minimal environment and unavailable Agent/Lark binaries. Chat and Lark connection errors are intentional isolation and do not qualify live IM behavior. Stop with Ctrl-C.

This remains a source-checkout demo under `demo/`, outside the installed wheel and capability catalog. Screenshots and recordings belong in ignored `output/playwright/`. Keep real operating statistics separately timestamped with their counting scope.

Validation: `python -m pytest tests/test_workspace_story_demo.py -q` exercises real state, directory isolation, repeatable prepare, computed artifacts and decision-scoped transitions. The normal Workspace browser smoke covers the shared Board/List and completed-history behavior, including #3961.
