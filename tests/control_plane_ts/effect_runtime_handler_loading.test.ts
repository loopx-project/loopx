import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import test from "node:test";

test("coordination claims remain available without terminal research evidence", () => {
  const entry = new URL("../../loopx/control_plane/effect_runtime_handlers.ts", import.meta.url).href;
  const fixtures = new URL("./local_promotion_fixture.ts", import.meta.url).href;
  const source = new URL("./shadow_file_fixture.ts", import.meta.url).href;
  const storeModule = new URL("../../loopx/control_plane/coordination/file_authority_store.ts", import.meta.url).href;
  const script = `
    import assert from "node:assert/strict";
    import {registerHooks} from "node:module";
    import {mkdtemp, rm, writeFile} from "node:fs/promises";
    import {createHash} from "node:crypto";
    import {tmpdir} from "node:os";
    import {join} from "node:path";
    let terminalLoads = 0;
    registerHooks({load(url, context, nextLoad) {
      if (url.endsWith("/capabilities/explore_research_terminal.ts")) {
        terminalLoads++;
        throw new Error("terminal research evidence unavailable");
      }
      return nextLoad(url, context);
    }});
    const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import(${JSON.stringify(entry)});
    const {engageCanonicalRoute} = await import(${JSON.stringify(fixtures)});
    const {projection, todo} = await import(${JSON.stringify(source)});
    const {FileAuthorityStore} = await import(${JSON.stringify(storeModule)});
    const root = await mkdtemp(join(tmpdir(), "coordination-selected-loading-"));
    try {
      await engageCanonicalRoute(root);
      const store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
      assert.equal((await store.commitAuthority({expected_provider_revision: null,
        operation_id: "seed", events: [], receipts: [],
        next_projection: projection([todo()], [], "soft_claim")})).status, "applied");
      const handlers = createEffectRuntimeHandlers({fingerprint: "loading-test", requestShutdown() {}});
      const request = {schema_version: "loopx_local_coordination_todo_claim_request_v0",
        runtime_root: root, goal_id: "goal-a", todo_id: "todo_one", role: "agent",
        claimed_by: "agent-a", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
        operation_id: "claim", observed_at: "2026-09-05T04:30:00Z", dry_run: false};
      const applied = await dispatchEffectRuntimeMethod(handlers,
        "coordination.local_authority.todo_claim", request);
      assert.equal(applied.status, "applied", JSON.stringify(applied));
      const claimed = await store.loadAuthority();
      assert.equal(claimed.status, "loaded");
      assert.equal(claimed.head.todos[0].claimed_by, "agent-a");
      const receipt = await store.readReceipt("claim");
      assert.equal(receipt.status, "found");
      assert.equal((await dispatchEffectRuntimeMethod(handlers,
        "coordination.local_authority.todo_claim", request)).status, "replayed");
      assert.deepEqual(await store.loadAuthority(), claimed);
      assert.deepEqual(await store.readReceipt("claim"), receipt);
      const invalid = await dispatchEffectRuntimeMethod(handlers,
        "coordination.local_authority.todo_claim", {...request, operation_id: "invalid", dry_run: "true"});
      assert.equal(invalid.reason_code, "invalid_local_coordination_todo_claim_request");
      assert.deepEqual(await store.readReceipt("invalid"), {status: "missing"});
      assert.deepEqual(await store.loadAuthority(), claimed);
      assert.equal(terminalLoads, 0);
      const registry = join(root, "registry.json");
      await writeFile(registry, "{}");
      const terminalRequest = {
        schema_version: "loopx_local_coordination_todo_terminal_lifecycle_request_v3",
        registry_source: {path: registry, sha256: createHash("sha256").update("{}").digest("hex")},
        runtime_root: root, goal_id: "goal-a", todo_id: "todo_one", role: "agent",
        command: "complete", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
        lifecycle_grants: [], authority_reason: null, decision_outcome: null,
        operation_identity: {kind: "explicit", operation_id: "terminal"},
        lease_idempotency_key: null, lease_expected_version: null, allow_user_gate_auto_acquire: false,
        requested_no_followup: true, requested_completion_turn_key: null,
        requested_completion_identity_source: null, linked_successor_todo_ids: [], successor_intents: [],
        note: null, evidence: null, reason: null, clear_claim: false,
        validation_declaration: null, validation_receipt: null, completion_policy_request: null,
        observed_at: "2026-09-05T04:30:00Z", dry_run: false,
      };
      for (let attempt = 0; attempt < 2; attempt++) {
        const failed = await dispatchEffectRuntimeMethod(handlers,
          "coordination.local_authority.todo_terminal", terminalRequest);
        assert.equal(failed.status, "failed");
        assert.match(failed.reason, /terminal research evidence unavailable/);
        assert.deepEqual(await store.readReceipt("terminal"), {status: "missing"});
        assert.deepEqual(await store.loadAuthority(), claimed);
      }
      assert.equal((await dispatchEffectRuntimeMethod(handlers, "runtime.ping", {})).ready, true);
    } finally {await rm(root, {recursive: true, force: true});}
  `;
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types",
    "--input-type=module", "-e", script], {encoding: "utf8", timeout: 30_000});
  assert.equal(child.status, 0, child.stderr);
});

test("ordinary and disabled-replan completion isolate research implementation availability", () => {
  const entry = new URL("../../loopx/control_plane/effect_runtime_handlers.ts", import.meta.url).href;
  const fixtures = new URL("./local_promotion_fixture.ts", import.meta.url).href;
  const source = new URL("./shadow_file_fixture.ts", import.meta.url).href;
  const storeModule = new URL("../../loopx/control_plane/coordination/file_authority_store.ts", import.meta.url).href;
  const providerModule = new URL("../../loopx/control_plane/coordination/local_authority_provider.ts", import.meta.url).href;
  for (const backend of ["file", "sqlite"]) {
    for (const scenario of [
      {disabled: false, harness: {enabled: true, composition_mode: "explicit_only", composition_scope_id: "scope-a"}},
      {disabled: false, harness: {composition_mode: "explicit_only", composition_scope_id: "scope-a"}},
      {disabled: true, harness: {}},
      {disabled: true, harness: {enabled: true, composition_mode: "disabled"}},
      {disabled: true, harness: {enabled: false, composition_mode: "explicit_only", composition_scope_id: "scope-a"}},
      {disabled: false, harness: {composition_mode: "disabled", composition_scope_id: "unsafe/path"},
        failureReason: "composition_scope_id must be an opaque public-safe identifier"},
      {disabled: false, harness: {enabled: false, composition_mode: "unknown"},
        failureReason: "composition_mode must be disabled or explicit_only"},
      {disabled: false, harness: {enabled: true, composition_mode: ["explicit_only"], composition_scope_id: "scope-a"},
        failureReason: "composition_mode must be disabled or explicit_only"},
      {disabled: false, harness: {enabled: true, composition_mode: ["disabled"]},
        failureReason: "composition_mode must be disabled or explicit_only"},
    ]) {
    const registryContent = JSON.stringify({goals: [{id: "goal-a", spawn_policy: {explore_harness: scenario.harness}}]});
    const blockedModules = scenario.failureReason ? ["explore_research_execution.ts"]
      : scenario.disabled ? ["explore_research_execution.ts", "explore_research.ts"]
      : ["explore_research_execution.ts", "explore_configuration.ts"];
    for (const blockedModule of blockedModules) {
      const script = `
        import assert from "node:assert/strict";
        import {registerHooks} from "node:module";
        import {mkdtemp, rm, writeFile} from "node:fs/promises";
        import {createHash} from "node:crypto";
        import {tmpdir} from "node:os";
        import {join} from "node:path";
        let blockedLoads = 0;
        registerHooks({load(url, context, nextLoad) {
          if (url.endsWith("/capabilities/" + ${JSON.stringify(blockedModule)})) {
            blockedLoads++;
            throw new Error("research implementation unavailable");
          }
          return nextLoad(url, context);
        }});
        const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import(${JSON.stringify(entry)});
        const {engageCanonicalRoute} = await import(${JSON.stringify(fixtures)});
        const {projection, todo} = await import(${JSON.stringify(source)});
        const {FileAuthorityStore} = await import(${JSON.stringify(storeModule)});
        const root = await mkdtemp(join(tmpdir(), "coordination-ordinary-completion-"));
        try {
          let store;
          if (${JSON.stringify(backend)} === "sqlite") {
            const {selectLocalSqliteAuthority, openLocalAuthorityStore} = await import(${JSON.stringify(providerModule)});
            await selectLocalSqliteAuthority(root, "goal-a", true);
            store = await openLocalAuthorityStore(root, "goal-a");
          } else {
            store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
          }
          await engageCanonicalRoute(root);
          const ordinary = {...todo(), claimed_by: "agent-a"};
          const research = {...todo("todo_research"), claimed_by: "agent-a",
            ...${JSON.stringify(scenario.disabled ? {replan_obligation_id: "replan-0123456789abcdef"} : {action_kind: "joint_probe"})}};
          assert.equal((await store.commitAuthority({expected_provider_revision: null,
            operation_id: "seed", events: [], receipts: [],
            next_projection: projection([ordinary, research], [], "soft_claim")})).status, "applied");
          const registry = join(root, "registry.json");
          await writeFile(registry, ${JSON.stringify(registryContent)});
          const handlers = createEffectRuntimeHandlers({fingerprint: "loading-test", requestShutdown() {}});
          const request = {
            schema_version: "loopx_local_coordination_todo_terminal_lifecycle_request_v3",
            registry_source: {path: registry, sha256: createHash("sha256").update(${JSON.stringify(registryContent)}).digest("hex")},
            runtime_root: root, goal_id: "goal-a", todo_id: "todo_one", role: "agent",
            command: "complete", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
            lifecycle_grants: [], authority_reason: null, decision_outcome: null,
            operation_identity: {kind: "explicit", operation_id: "ordinary-complete"},
            lease_idempotency_key: null, lease_expected_version: null, allow_user_gate_auto_acquire: false,
            requested_no_followup: true, requested_completion_turn_key: null,
            requested_completion_identity_source: null, linked_successor_todo_ids: [], successor_intents: [],
            note: null, evidence: "validated ordinary result", reason: null, clear_claim: false,
            validation_declaration: null, validation_receipt: null, completion_policy_request: null,
            observed_at: "2026-09-05T04:30:00Z", dry_run: false,
          };
          const completed = await dispatchEffectRuntimeMethod(handlers,
            "coordination.local_authority.todo_terminal", request);
          assert.equal(completed.status, "applied", JSON.stringify(completed));
          const accepted = await store.loadAuthority();
          assert.equal(accepted.status, "loaded");
          const result = accepted.head.todos.find(row => row.todo_id === "todo_one");
          assert.equal(result.status, "done");
          assert.equal(result.done, true);
          assert.equal(result.evidence, "validated ordinary result");
          const receipt = await store.readReceipt("ordinary-complete");
          assert.equal(receipt.status, "found");
          assert.equal((await dispatchEffectRuntimeMethod(handlers,
            "coordination.local_authority.todo_terminal", request)).status, "replayed");
          assert.deepEqual(await store.loadAuthority(), accepted);
          assert.deepEqual(await store.readReceipt("ordinary-complete"), receipt);
          assert.equal(blockedLoads, 0);
          const selected = {...request, todo_id: "todo_research",
            operation_identity: {kind: "explicit", operation_id: "research-complete"}};
          if (${scenario.disabled}) {
            const finished = await dispatchEffectRuntimeMethod(handlers,
              "coordination.local_authority.todo_terminal", selected);
            assert.equal(finished.status, "applied", JSON.stringify(finished));
            const disabledAccepted = await store.loadAuthority();
            assert.equal(disabledAccepted.head.todos.find(row => row.todo_id === "todo_research").evidence,
              "validated ordinary result");
            assert.equal(disabledAccepted.head.todos.find(row => row.todo_id === "todo_research").status, "done");
            const disabledReceipt = await store.readReceipt("research-complete");
            assert.equal(disabledReceipt.status, "found");
            assert.equal((await dispatchEffectRuntimeMethod(handlers,
              "coordination.local_authority.todo_terminal", selected)).status, "replayed");
            assert.deepEqual(await store.loadAuthority(), disabledAccepted);
            assert.deepEqual(await store.readReceipt("research-complete"), disabledReceipt);
            assert.equal(blockedLoads, 0);
          } else for (let attempt = 0; attempt < 2; attempt++) {
            const failed = await dispatchEffectRuntimeMethod(handlers,
              "coordination.local_authority.todo_terminal", selected);
            assert.equal(failed.status, "failed");
            assert.match(failed.reason, new RegExp(${JSON.stringify(scenario.failureReason ?? "research implementation unavailable")}));
            assert.deepEqual(await store.readReceipt("research-complete"), {status: "missing"});
            assert.deepEqual(await store.loadAuthority(), accepted);
          }
          if (${Boolean(scenario.failureReason)}) assert.equal(blockedLoads, 0);
          if (${!scenario.disabled && !scenario.failureReason && blockedModule === "explore_research_execution.ts"}) {
            const previousLoads = blockedLoads;
            const disabledRegistry = JSON.stringify({goals: [{id: "goal-a",
              spawn_policy: {explore_harness: {enabled: false, composition_mode: "disabled"}}}]});
            await writeFile(registry, disabledRegistry);
            const stale = await dispatchEffectRuntimeMethod(handlers,
              "coordination.local_authority.todo_terminal", selected);
            assert.notEqual(stale.status, "applied");
            assert.deepEqual(await store.loadAuthority(), accepted);
            assert.deepEqual(await store.readReceipt("research-complete"), {status: "missing"});
            const recovered = {...selected, registry_source: {path: registry,
              sha256: createHash("sha256").update(disabledRegistry).digest("hex")}};
            const finished = await dispatchEffectRuntimeMethod(handlers,
              "coordination.local_authority.todo_terminal", recovered);
            assert.equal(finished.status, "applied", JSON.stringify(finished));
            const recoveredState = await store.loadAuthority();
            const recoveredReceipt = await store.readReceipt("research-complete");
            assert.equal(recoveredReceipt.status, "found");
            assert.equal((await dispatchEffectRuntimeMethod(handlers,
              "coordination.local_authority.todo_terminal", recovered)).status, "replayed");
            assert.deepEqual(await store.loadAuthority(), recoveredState);
            assert.deepEqual(await store.readReceipt("research-complete"), recoveredReceipt);
            assert.deepEqual(await store.readReceipt("ordinary-complete"), receipt);
            assert.equal(blockedLoads, previousLoads);
          }
          assert.equal((await dispatchEffectRuntimeMethod(handlers, "runtime.ping", {})).ready, true);
        } finally {await rm(root, {recursive: true, force: true});}
      `;
      const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
        "--input-type=module", "-e", script], {encoding: "utf8", timeout: 30_000});
      assert.equal(child.status, 0, `${backend}/${JSON.stringify(scenario.harness)}/${blockedModule}: ${child.stderr}`);
    }
    }
  }
});

test("runtime loads selected owners without importing unrelated methods", () => {
  const entry = new URL("../../loopx/control_plane/effect_runtime_handlers.ts", import.meta.url).href;
  // Observe a fresh process, not this test runner's already imported modules.
  const script = `
    import assert from "node:assert/strict";
    import {registerHooks} from "node:module";
    const loaded = new Set();
    registerHooks({load(url, context, nextLoad) {
      loaded.add(url);
      return nextLoad(url, context);
    }});
    const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import(${JSON.stringify(entry)});
    let shutdowns = 0;
    const handlers = createEffectRuntimeHandlers({fingerprint: "loading-test",
      requestShutdown: () => { shutdowns++; }});
    const imported = (suffix) => [...loaded].some(url => url.endsWith(suffix));
    for (const suffix of ["/task_lease_acquire_decision.ts", "/external_evidence.ts", "/performance_diagnosis.ts",
      "/goal_agent_context.ts", "/goal_capability_organization.ts", "/peer_context.ts",
      "/source_grants.ts", "/execution_identity.ts"]) {
      assert.equal(imported(suffix), false, suffix);
    }
    const ping = await dispatchEffectRuntimeMethod(handlers, "runtime.ping", {});
    assert.deepEqual(ping, {ready: true, pid: process.pid, fingerprint: "loading-test"});
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "not-a-method", {}),
      {code: "unsupported_method"});
    assert.equal(imported("/task_lease_acquire_decision.ts"), false);
    const results = await Promise.all([
      dispatchEffectRuntimeMethod(handlers, "task_lease.write_scopes.overlap",
        {left: ["docs/**"], right: ["docs/file.md"]}),
      dispatchEffectRuntimeMethod(handlers, "task_lease.write_scopes.overlap",
        {left: ["tests/**"], right: ["docs/file.md"]}),
    ]);
    assert.equal(results[0].overlap, true);
    assert.equal(results[1].overlap, false);
    const repeated = await dispatchEffectRuntimeMethod(handlers, "task_lease.write_scopes.overlap",
      {left: ["tests/**"], right: ["docs/file.md"]});
    assert.equal(repeated.overlap, false);
    assert.equal(imported("/task_lease_acquire_decision.ts"), true);
    assert.equal(imported("/external_evidence.ts"), false);
    assert.equal(imported("/performance_diagnosis.ts"), false);
    const policy = {mode: "bounded", discovery_budget_minutes: 5, max_trials: 1};
    const inspect = await dispatchEffectRuntimeMethod(handlers, "capability.improvement.inspect", {policy});
    assert.equal(inspect.mode, "bounded");
    const configured = await dispatchEffectRuntimeMethod(handlers, "capability.improvement.configuration",
      {current: policy, patch: {max_trials: 0}});
    assert.deepEqual(configured, {configuration: {...policy, max_trials: 0}});
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "capability.improvement.configuration",
      {patch: {mode: "auto_install"}}));
    const context = await dispatchEffectRuntimeMethod(handlers, "capability_hook.agent_context.project",
      {phase: "before_plan", scope: {goal_id: "example", agent_id: "coordinator"},
        capability_improvement: policy});
    assert.equal(context.authority, "guidance_only");
    assert.equal(context.contributions[0].capability_id, "goal_capability_organization");
    const worker = {goal_id: "example", agent_id: "worker"};
    const source = {sender_ids: ["owner"], targets: [{goal_id: "example"}]};
    assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "collaboration.source.recipients",
      {sender_id: "owner", source, available: [worker]}), {targets: [worker]});
    const revoked = await dispatchEffectRuntimeMethod(handlers, "collaboration.source.configure_recipient",
      {source, goal_id: "example", agent_id: "worker", grant: false,
        available: [worker], active_goal_ids: ["example"]});
    assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "collaboration.source.recipients",
      {sender_id: "owner", source: revoked.source, available: [worker]}), {targets: []});
    const peer = {conversation: {channel_id: "goal.example", goal_id: "example", origin: "web"},
      source_id: "fixture", goal_id: "example", agent_ids: ["worker"]};
    assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "collaboration.peer.context_access",
      peer), {allowed: true});
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "collaboration.peer.context_access",
      {...peer, conversation: {channel_id: "manager.external.fixture", goal_id: "example"}}));
    assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "runtime.execution_identity.match",
      {declaration: {actor_kind: "model_agent", declaration_source: "runtime_reported"}}),
      {errors: ["runtime_observation_missing"]});
    assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "runtime.execution_identity.codex", {}),
      {status: "unavailable", reason: "session_not_bound"});
    for (const suffix of ["/goal_agent_context.ts", "/goal_capability_organization.ts", "/peer_context.ts",
      "/source_grants.ts", "/execution_identity.ts"]) assert.equal(imported(suffix), true, suffix);
    await dispatchEffectRuntimeMethod(handlers, "runtime.shutdown", {});
    assert.equal(shutdowns, 1);
  `;
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types",
    "--input-type=module", "-e", script], {encoding: "utf8", timeout: 30_000});
  assert.equal(child.status, 0, child.stderr);
});

test("a selected module load failure rejects instead of falling back", () => {
  const entry = new URL("../../loopx/control_plane/effect_runtime_handlers.ts", import.meta.url).href;
  const script = `
    import assert from "node:assert/strict";
    import {registerHooks} from "node:module";
    registerHooks({load(url, context, nextLoad) {
      if (url.endsWith("/performance_diagnosis.ts")) {
        throw new Error("selected owner unavailable");
      }
      return nextLoad(url, context);
    }});
    const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import(${JSON.stringify(entry)});
    const handlers = createEffectRuntimeHandlers({fingerprint: "loading-test", requestShutdown() {}});
    for (let attempt = 0; attempt < 2; attempt++) {
      await assert.rejects(dispatchEffectRuntimeMethod(handlers, "performance_diagnosis.plan", {}),
        /selected owner unavailable/);
    }
    assert.equal((await dispatchEffectRuntimeMethod(handlers, "runtime.ping", {})).ready, true);
  `;
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types",
    "--input-type=module", "-e", script], {encoding: "utf8", timeout: 30_000});
  assert.equal(child.status, 0, child.stderr);
});
