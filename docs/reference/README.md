# Reference

Reference docs are machine-facing or contract-facing documents that should be
stable enough to test, lint, or link from implementation.

Current groups:

- [Reference contracts](contracts/README.md)
- [Protocol contracts](protocols/README.md)
- [Extensions and capabilities](extensions.md): outcome contracts, provider
  registration, extension packaging, readiness, and lifecycle boundaries.
- [Project skill delivery](../../loopx/capabilities/project_skill_delivery/README.md): release-owned,
  project-local skill discovery and managed-copy lifecycle.
- [PostgreSQL authority service admission v0](postgresql-authority-service-v0.md): opt-in
  authentication, tenant authorization, and restore-incarnation rotation.
- [Usage ping](usage-ping.md): default-on basic usage statistics, disclosure/opt-out and exact
  payload, opt-out switches, and the published active-installation counts.

High-traffic read paths:

- [agent_scoped_evidence_ledger_v0](protocols/agent-scoped-evidence-ledger-v0.md):
  thin, per-agent evidence chronology used before replan or handoff.
- [Goal acceptance observations](goal-acceptance-observations.md): bounded,
  read-only Goal acceptance gaps, pending gates, and historical progress
  read from `run_history.goals[].acceptance_observation`, and the Dashboard entry
  that renders them. Partial observations never certify acceptance.
