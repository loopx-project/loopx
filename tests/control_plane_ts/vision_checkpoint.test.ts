import assert from "node:assert/strict";
import test from "node:test";

import {
  buildVisionCheckpoint,
  VISION_REFRESH_PREPARED_SCHEMA_VERSION,
  VISION_REFRESH_REQUEST_SCHEMA,
} from "../../loopx/control_plane/goals/vision_checkpoint.ts";

function finalizeRequest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: VISION_REFRESH_REQUEST_SCHEMA,
    phase: "finalize",
    agent_id: "codex-main",
    agent_vision: null,
    existing_agent_vision: null,
    vision_unchanged_reason: null,
    delivery_outcome: "outcome_progress",
    active_state_next_action_would_update: false,
    delivery_boundary: "semantic_closeout",
    todo_id: "todo_current001",
    completion_todo_id: null,
    autonomous_replan_recorded: false,
    ...overrides,
  };
}

function prepareRequest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: VISION_REFRESH_REQUEST_SCHEMA,
    phase: "prepare",
    goal_id: "goal-main",
    agent_id: "codex-main",
    agent_vision_packet: {
      state: "vision_patch_proposed",
      vision_patch: {
        vision_summary: "Ship one bounded route.",
      },
    },
    existing_agent_vision: null,
    merge_patch: false,
    require_path_delta_for_durable_change: false,
    ...overrides,
  };
}

test("prepare owns packet normalization, budgets, and path delta", () => {
  const result = buildVisionCheckpoint(prepareRequest({
    agent_vision_packet: {
      goal_id: "goal-main",
      agent_id: "codex-main",
      state: "closed",
      vision_patch: {
        vision_summary: "  Ship   one bounded route.  ",
        role_scope: "Own the route.",
        acceptance_summary: "The route produces evidence.",
        advancement_policy: "repeat-until-closed",
      },
      path_delta: {
        outcome: "replan",
        prior_assumption: "The old route remained viable.",
        observed_reality: "Fresh evidence invalidated it.",
        changed: ["Use the bounded successor."],
      },
      todo_delta: [" create_successor "],
      validation: { write_correctness_checked: true },
    },
  }));

  assert.equal(result.schema_version, VISION_REFRESH_PREPARED_SCHEMA_VERSION);
  const vision = result.agent_vision as Record<string, unknown>;
  assert.equal(vision.state, "vision_closed");
  assert.deepEqual(vision.vision_patch, {
    vision_summary: "Ship one bounded route.",
    role_scope: "Own the route.",
    acceptance_summary: "The route produces evidence.",
    advancement_policy: "repeat_until_closed",
  });
  assert.deepEqual(vision.todo_delta, ["create_successor"]);
  assert.deepEqual(vision.path_delta, {
    schema_version: "goal_path_delta_v0",
    outcome: "replan",
    prior_assumption: "The old route remained viable.",
    observed_reality: "Fresh evidence invalidated it.",
    changed: ["Use the bounded successor."],
  });
  assert.deepEqual(vision.validation, {
    write_correctness_checked: true,
    budget_checked: true,
    budget_status: "ok",
  });
});

test("authoring rejects misplaced declared deltas without classifying telemetry", () => {
  const delta = {schema_version: "goal_path_delta_v0", outcome: "replan",
    prior_assumption: "Keep the route.", observed_reality: "A dependency changed.",
    changed: ["Use the successor."]};
  const patch = {vision_summary: "Deliver the successor."};
  for (const extra of [
    {goal_path_delta_v0: delta}, {comparison: delta},
    {path_delta: delta, comparison: delta},
    {vision_patch: {...patch, path_delta: delta}},
  ]) {
    assert.throws(() => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {vision_patch: patch, ...extra},
    })), /must be supplied as agent_vision.path_delta/);
  }
  const telemetry = {outcome: "ok", evidence_refs: ["evidence:probe"]};
  const baseline = buildVisionCheckpoint(prepareRequest({agent_vision_packet: {vision_patch: patch}}));
  assert.deepEqual(buildVisionCheckpoint(prepareRequest({
    agent_vision_packet: {vision_patch: patch, telemetry},
  })), baseline);
  assert.throws(() => buildVisionCheckpoint(prepareRequest({agent_vision_packet: {
    vision_patch: patch, path_delta: {...delta, schema_version: "unsupported"},
  }})), /path_delta.schema_version must be goal_path_delta_v0/);
  const accepted = buildVisionCheckpoint(prepareRequest({agent_vision_packet: {
    vision_patch: patch, path_delta: delta, telemetry,
  }}));
  assert.deepEqual((accepted.agent_vision as Record<string, unknown>).path_delta, delta);
});

test("structured replans have a bounded 1800-character budget including path evidence", () => {
  for (const character of ["x", "界"]) {
    // Independent boundary oracle: 420 + 420 + 280 + 320 + 320 + 6 + 34.
    const packet = {
      vision_patch: {vision_summary: character.repeat(420), acceptance_summary: character.repeat(420),
        role_scope: character.repeat(280)},
      path_delta: {outcome: "replan", prior_assumption: character.repeat(320),
        observed_reality: character.repeat(320), changed: [character.repeat(34)]},
    };
    const accepted = buildVisionCheckpoint(prepareRequest({agent_vision_packet: packet}));
    const vision = accepted.agent_vision as Record<string, unknown>;
    const budget = vision.vision_budget as Record<string, unknown>;
    assert.equal(budget.total_usage, 1800);
    assert.equal(budget.total_limit, 1800);
    assert.equal((vision.path_delta as Record<string, unknown>).observed_reality, character.repeat(320));
    assert.throws(() => buildVisionCheckpoint(prepareRequest({agent_vision_packet: {
      ...packet, path_delta: {...packet.path_delta, observed_reality: character.repeat(321)},
    }})), /path_delta.observed_reality uses 321 chars; limit is 320/);
    assert.throws(() => buildVisionCheckpoint(prepareRequest({agent_vision_packet: {
      ...packet, path_delta: {...packet.path_delta, changed: [character.repeat(35)]},
    }})), /total_agent_vision uses 1801 chars; limit is 1800/);
  }
});

test("prepare preserves v0 JSON-to-text compatibility", () => {
  const result = buildVisionCheckpoint(prepareRequest({
    agent_vision_packet: {
      vision_summary: true,
      todo_delta: [["one", "two"], { route: "bounded" }, {}],
      path_delta: {
        outcome: "replan",
        prior_assumption: { route: "old" },
        observed_reality: "Fresh evidence invalidated it.",
        changed: ["Use the bounded successor."],
      },
    },
  }));

  const vision = result.agent_vision as Record<string, unknown>;
  assert.deepEqual(vision.vision_patch, { vision_summary: "True" });
  assert.deepEqual(vision.todo_delta, [
    "['one', 'two']",
    "{'route': 'bounded'}",
  ]);
  assert.equal(
    (vision.path_delta as Record<string, unknown>).prior_assumption,
    "{'route': 'old'}",
  );
});

test("prepare merges a patch and requires an explicit durable replan", () => {
  const existing = {
    state: "vision_active",
    vision_patch: {
      vision_summary: "Old route.",
      role_scope: "Own framing.",
      acceptance_summary: "Produce evidence.",
      advancement_policy: "as_needed",
    },
  };
  const agentVisionPacket = {
    vision_patch: { vision_summary: "New route." },
    path_delta: {
      outcome: "replan",
      prior_assumption: "The old route would hold.",
      observed_reality: "New evidence changed the route.",
      changed: ["Use the new route."],
    },
  };
  const result = buildVisionCheckpoint(prepareRequest({
    agent_vision_packet: agentVisionPacket,
    existing_agent_vision: existing,
    merge_patch: true,
    require_path_delta_for_durable_change: true,
  }));
  assert.deepEqual(
    (result.agent_vision as Record<string, unknown>).vision_patch,
    {
      vision_summary: "New route.",
      role_scope: "Own framing.",
      acceptance_summary: "Produce evidence.",
      advancement_policy: "as_needed",
    },
  );

  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: { vision_patch: { vision_summary: "New route." } },
      existing_agent_vision: existing,
      merge_patch: true,
      require_path_delta_for_durable_change: true,
    })),
    /provide path_delta with schema_version=goal_path_delta_v0 and outcome=replan/,
  );
});

test("prepare rejects identity drift, private text, and typed budget overflow", () => {
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        goal_id: "other-goal",
        vision_summary: "Bounded route.",
      },
    })),
    /does not match/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Read \/Users\/owner\/private.txt",
      },
    })),
    /contains a private-looking value/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: { vision_summary: "x".repeat(421) },
    })),
    (error: unknown) => {
      const rejection = error as { code?: string; message?: string };
      assert.equal(
        rejection.code,
        "vision_budget_exceeded",
      );
      assert.match(rejection.message ?? "", /suggested compact value/);
      assert.ok((rejection.message ?? "").length <= 240);
      return true;
    },
  );
});

test("prepare rejects incomplete and over-wide path deltas", () => {
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Bounded route.",
        path_delta: { outcome: "replan" },
      },
    })),
    /requires prior_assumption and observed_reality/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Bounded route.",
        path_delta: {
          outcome: "replan",
          prior_assumption: "Old assumption.",
          observed_reality: "New reality.",
          retained: ["one", "two", "three", "four"],
        },
      },
    })),
    /path_delta.retained has 4 items; limit is 3/,
  );
});

test("prepare validates and carries bounded unique fallback declarations", () => {
  const result = buildVisionCheckpoint(prepareRequest({
    agent_vision_packet: {
      vision_summary: "Ship one bounded route.",
      fallback_declarations: [
        {
          declaration_id: " declared_fallback_direction ",
          target_todo_id: "todo_declared_fallback",
          successor_todo_id: null,
        },
        { declaration_id: "typed_successor", successor_todo_id: "todo_successor" },
      ],
    },
  }));

  const vision = result.agent_vision as Record<string, unknown>;
  assert.deepEqual(vision.fallback_declarations, [
    {
      declaration_id: "declared_fallback_direction",
      target_todo_id: "todo_declared_fallback",
    },
    { declaration_id: "typed_successor", successor_todo_id: "todo_successor" },
  ]);
  const budget = vision.vision_budget as Record<string, unknown>;
  const fieldLimits = budget.field_limits as Record<string, number>;
  const fieldUsage = budget.field_usage as Record<string, number>;
  assert.equal(fieldLimits["fallback_declarations"], 4);
  assert.equal(fieldLimits["fallback_declarations[]"], 120);
  assert.equal(
    fieldUsage["fallback_declarations[0].declaration_id"],
    "declared_fallback_direction".length,
  );

  const empty = buildVisionCheckpoint(prepareRequest({
    agent_vision_packet: {
      vision_summary: "Ship one bounded route.",
      fallback_declarations: [],
    },
  })) as Record<string, unknown>;
  assert.equal(
    (empty.agent_vision as Record<string, unknown>).fallback_declarations,
    undefined,
  );
});

test("prepare rejects unbounded, duplicated, and unsafe fallback declarations", () => {
  const declaration = (id: string) => ({ declaration_id: id });
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Ship one bounded route.",
        fallback_declarations: "declared_fallback_direction",
      },
    })),
    /fallback_declarations must be a JSON array/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Ship one bounded route.",
        fallback_declarations: [1, 2, 3, 4, 5].map(() => declaration("one")),
      },
    })),
    /fallback_declarations has 5 items; limit is 4/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Ship one bounded route.",
        fallback_declarations: [
          declaration("declared_fallback_direction"),
          declaration("declared_fallback_direction"),
        ],
      },
    })),
    /repeats declaration_id "declared_fallback_direction"/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Ship one bounded route.",
        fallback_declarations: [{ target_todo_id: "todo_declared_fallback" }],
      },
    })),
    /requires a non-empty declaration_id/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Ship one bounded route.",
        fallback_declarations: [
          { declaration_id: "route", target_todo_id: "todo_with_fallback" },
          { declaration_id: "token = leaked", successor_todo_id: "todo_x" },
        ],
      },
    })),
    /contains a private-looking value/,
  );
  assert.throws(
    () => buildVisionCheckpoint(prepareRequest({
      agent_vision_packet: {
        vision_summary: "Ship one bounded route.",
        fallback_declarations: [
          { declaration_id: "x".repeat(121) },
        ],
      },
    })),
    (error: unknown) => {
      const rejection = error as { code?: string; message?: string };
      assert.equal(rejection.code, "vision_budget_exceeded");
      return true;
    },
  );
});

test("pure prepare and finalize reductions replay deterministically", () => {
  const prepare = prepareRequest();
  assert.deepEqual(
    buildVisionCheckpoint(prepare),
    buildVisionCheckpoint(prepare),
  );
  const finalize = finalizeRequest({
    agent_vision: (buildVisionCheckpoint(prepare) as Record<string, unknown>)
      .agent_vision,
  });
  assert.deepEqual(
    buildVisionCheckpoint(finalize),
    buildVisionCheckpoint(finalize),
  );
});

test("semantic closeout retains the strict material vision checkpoint", () => {
  const result = buildVisionCheckpoint(finalizeRequest());
  assert.equal(result.required, true);
  assert.equal(result.satisfied, false);
  assert.equal(result.decision, "missing_required");
  assert.deepEqual(result.triggers, [
    { kind: "material_delivery_outcome", delivery_outcome: "outcome_progress" },
  ]);
});

test("a bounded blocked retry does not invent a vision change", () => {
  const blockedRetry = {
    schema_version: "quota_blocked_retry_v0",
    source: "turn_settlement",
    todo_id: "todo_current001",
    observed_at: "2026-09-24T14:00:00Z",
    due_at: "2026-09-24T14:05:00Z",
    resume_when: "resume_at:2026-09-24T14:05:00Z",
  };
  const blocked = buildVisionCheckpoint(finalizeRequest({
    delivery_outcome: "outcome_gap", blocked_retry: blockedRetry,
  }));
  assert.equal(blocked.required, false);
  assert.equal(blocked.satisfied, true);
  assert.equal(blocked.decision, "not_required");
  assert.deepEqual(blocked.triggers, []);

  const changedAction = buildVisionCheckpoint(finalizeRequest({
    delivery_outcome: "outcome_gap", blocked_retry: blockedRetry,
    active_state_next_action_would_update: true,
  }));
  assert.equal(changedAction.required, true);
  assert.deepEqual(changedAction.triggers, [{kind: "durable_next_action_update"}]);
  assert.throws(() => buildVisionCheckpoint(finalizeRequest({
    delivery_outcome: "outcome_gap",
    blocked_retry: {...blockedRetry, todo_id: "todo_other"},
  })), /blocked retry does not bind/);
});

test("explicit in-flight progress records continuity without vision repetition", () => {
  const result = buildVisionCheckpoint(finalizeRequest({
    delivery_boundary: "in_flight_continuation",
  }));
  assert.equal(result.required, false);
  assert.equal(result.satisfied, true);
  assert.equal(result.decision, "not_required");
  assert.deepEqual(result.triggers, [
    { kind: "in_flight_continuation", todo_id: "todo_current001" },
  ]);
});

test("non-material delivery remains valid without opening a vision checkpoint", () => {
  const result = buildVisionCheckpoint(finalizeRequest({
    delivery_outcome: "surface_only",
  }));
  assert.equal(result.required, false);
  assert.equal(result.satisfied, true);
  assert.equal(result.decision, "not_required");
  assert.deepEqual(result.triggers, []);
});

test("unchanged closeout binds the exact persisted vision revision", () => {
  const result = buildVisionCheckpoint(finalizeRequest({
    existing_agent_vision: {
      state: "vision_active",
      generated_at: "2026-08-22T18:30:17+08:00",
    },
    vision_unchanged_reason: "Validated evidence keeps the current route intact.",
  }));
  assert.equal(result.required, true);
  assert.equal(result.satisfied, true);
  assert.equal(result.decision, "unchanged_with_reason");
  assert.deepEqual(result.continuity_basis, {
    kind: "existing_vision_unchanged",
    vision_generated_at: "2026-08-22T18:30:17+08:00",
  });
});

test("legacy vision without a revision cannot claim outcome continuity", () => {
  const result = buildVisionCheckpoint(finalizeRequest({
    existing_agent_vision: { state: "vision_active" },
    vision_unchanged_reason: "The legacy route is still current.",
  }));
  assert.equal(result.decision, "unchanged_with_reason");
  assert.equal("continuity_basis" in result, false);
});

test("completion, replan, and durable route changes reject in-flight claims", () => {
  assert.throws(
    () => buildVisionCheckpoint(finalizeRequest({
      delivery_boundary: "in_flight_continuation",
      completion_todo_id: "todo_current001",
    })),
    /conflicts with Todo completion/,
  );
  assert.throws(
    () => buildVisionCheckpoint(finalizeRequest({
      delivery_boundary: "in_flight_continuation",
      autonomous_replan_recorded: true,
    })),
    /conflicts with autonomous replan/,
  );
  assert.throws(
    () => buildVisionCheckpoint(finalizeRequest({
      delivery_boundary: "in_flight_continuation",
      active_state_next_action_would_update: true,
    })),
    /conflicts with a durable Next Action update/,
  );
});

test("only accountable agent-bound progress may be in flight", () => {
  assert.throws(
    () => buildVisionCheckpoint(finalizeRequest({
      delivery_boundary: "in_flight_continuation",
      delivery_outcome: "outcome_gap",
    })),
    /requires delivery_outcome=outcome_progress/,
  );
  assert.throws(
    () => buildVisionCheckpoint(finalizeRequest({
      delivery_boundary: "in_flight_continuation",
      todo_id: null,
    })),
    /requires an agent-bound Todo settlement/,
  );
});

test("finalize owns unchanged-reason public safety and budget", () => {
  assert.throws(
    () => buildVisionCheckpoint(finalizeRequest({
      vision_unchanged_reason: "Read \/Users\/owner\/private.txt",
    })),
    /contains a private-looking value/,
  );
  assert.throws(
    () => buildVisionCheckpoint(finalizeRequest({
      vision_unchanged_reason: "x".repeat(241),
    })),
    /vision_unchanged_reason exceeds 240 chars/,
  );
});
