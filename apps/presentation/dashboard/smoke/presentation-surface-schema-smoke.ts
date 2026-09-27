import { goalAcceptanceObservationSchema } from "../src/data/goal-acceptance-observation.js";
import {
  exampleStatusPayload,
  parseStatusPayload,
  parsePresentationSurfaceCollectionResponse,
  presentationSurfaceCollectionSchema,
  presentationSurfaceSchema,
  todoItemSchema,
  withGoalActivationState,
} from "../src/data/status.js";
import {
  fetchPresentationProjection,
  parsePresentationProjection,
  presentationProjectionResponseSchema,
} from "../src/data/decision-research.js";
import projectionFixture from "../src/data/fixtures/presentation-projection.example.json";

function assert(condition: boolean, message: string) {
  if (!condition) {
    throw new Error(message);
  }
}

const PAYLOAD_SHA256 =
  "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

const revisedTodo = todoItemSchema.parse({
  index: 0,
  text: "Verify the replacement validator",
  done: false,
  todo_id: "todo_validator_revision",
  role: "agent",
  completion_validation_required: true,
  completion_validation_sha256: PAYLOAD_SHA256,
  completion_validation_revision: 1,
  completion_validation_revision_history: [{
    revision: 1,
    previous_declaration_sha256: "1".repeat(64),
    declaration_sha256: PAYLOAD_SHA256,
    actor_agent_id: "agent-a",
    revised_at: "2026-09-20T00:00:00Z",
  }],
});
assert(
  revisedTodo.completion_validation_revision_history.at(-1)?.actor_agent_id ===
    "agent-a",
  "Todo validator revision readback must survive status parsing",
);
const firstBoundTodo = todoItemSchema.parse({
  ...revisedTodo,
  completion_validation_revision_history: [{
    schema_version: "loopx_todo_completion_validation_revision_receipt_v1",
    revision: 1,
    operation_id: "first-bind",
    previous_declaration_sha256: null,
    previous_validation_authority: {},
    declaration_sha256: PAYLOAD_SHA256,
    actor_agent_id: "agent-a",
    revised_at: "2026-09-27T00:00:00Z",
  }],
});
assert(
  firstBoundTodo.completion_validation_revision_history[0]?.previous_declaration_sha256 === null &&
    firstBoundTodo.completion_validation_revision_history[0]?.schema_version ===
      "loopx_todo_completion_validation_revision_receipt_v1",
  "First validator binding must preserve explicit absence and its versioned receipt in status readback",
);

function detailRef() {
  return {
    extension_id: "test-research-extension",
    surface_id: "investment-research",
    extension_revision: "0123456789abcdef",
    payload_sha256: PAYLOAD_SHA256,
  };
}

function baseSurface(state: "empty" | "ready" | "review_due" | "invalid") {
  return {
    extension_id: "test-research-extension",
    extension_revision: "0123456789abcdef",
    surface_id: "investment-research",
    surface_kind: "decision_research_dashboard",
    title: "Investment Research",
    view_schema: "decision_research_dashboard_v0",
    visibility: "public-safe",
    state,
    goal_id: state === "empty" || state === "invalid" ? null : "synthetic-research-goal",
    generated_at:
      state === "empty" || state === "invalid"
        ? null
        : "2026-01-15T12:00:00+00:00",
    review_due_at:
      state === "empty" || state === "invalid"
        ? null
        : "2026-02-15T12:00:00+00:00",
    diagnostic: state === "invalid" ? "projection validation failed" : null,
    empty_state_title: "No validated research yet",
    empty_state_detail: "Publish a validated projection.",
  };
}

// The Core status contract is provider-neutral: ready/review_due carry a compact
// detail_ref pointer, not an inline domain view.
for (const state of ["empty", "ready", "review_due", "invalid"] as const) {
  const candidate = baseSurface(state);
  const parsed = presentationSurfaceSchema.parse(
    state === "ready" || state === "review_due"
      ? { ...candidate, detail_ref: detailRef() }
      : candidate,
  );
  assert(parsed.state === state, `surface state drifted: ${state}`);
}

const readySurface = presentationSurfaceSchema.parse({
  ...baseSurface("ready"),
  detail_ref: detailRef(),
});
assert(
  readySurface.state === "ready" && "detail_ref" in readySurface,
  "ready surface must carry a detail_ref",
);
assert(
  !("view" in readySurface),
  "ready surface must not carry an inline view",
);

// A generic view_schema token (a different domain) must still parse: Core does
// not freeze any one domain's schema.
const genericSurface = presentationSurfaceSchema.safeParse({
  ...baseSurface("ready"),
  surface_kind: "release_dashboard",
  view_schema: "release_dashboard_v0",
  detail_ref: detailRef(),
});
assert(genericSurface.success, "generic provider view_schema must parse");

const collection = presentationSurfaceCollectionSchema.parse({
  schema_version: "extension_presentation_surfaces_v0",
  count: 2,
  ready_count: 1,
  review_due_count: 1,
  empty_count: 0,
  invalid_count: 0,
  items: [
    { ...baseSurface("ready"), detail_ref: detailRef() },
    { ...baseSurface("review_due"), detail_ref: detailRef() },
  ],
});
assert(collection.items.length === 2, "ready and review-due surfaces must parse");
assert(
  parsePresentationSurfaceCollectionResponse({
    ok: true,
    presentation_surfaces: collection,
  }).ready_count === 1,
  "cold-path presentation surface response must parse",
);

const legacyPayload = structuredClone(exampleStatusPayload) as Record<string, unknown>;
delete legacyPayload.presentation_surfaces;
const legacyParsed = parseStatusPayload(legacyPayload);
assert(
  legacyParsed.presentation_surfaces.count === 0,
  "missing presentation surfaces must default to an empty collection",
);

const activationBase = structuredClone(exampleStatusPayload);
const activationGoal = activationBase.run_history.goals[0]!;
activationGoal.activation_state = "active";
activationBase.attention_queue.items[0] = {
  ...activationBase.attention_queue.items[0]!,
  activation_state: "active",
  goal_id: activationGoal.id,
};
const stoppedProjection = withGoalActivationState(activationBase, activationGoal.id, "stopped");
assert(
  activationBase.run_history.goals[0]!.activation_state === "active",
  "optimistic activation projection must not mutate the parsed source payload",
);
assert(
  stoppedProjection.run_history.goals[0]!.activation_state === "stopped",
  "optimistic activation projection must update the goal directory source",
);
assert(
  stoppedProjection.attention_queue.items[0]!.activation_state === "stopped",
  "optimistic activation projection must keep the attention row aligned",
);

// An inline `view` on a ready surface must fail closed: status is index-only.
const readyWithInlineView = {
  ...baseSurface("ready"),
  detail_ref: detailRef(),
  view: { headline: "should not be here" },
};
assert(
  !presentationSurfaceSchema.safeParse(readyWithInlineView).success,
  "ready surface with an inline view must fail closed",
);

// A ready surface without a detail_ref must fail closed.
const readyWithoutDetailRef = baseSurface("ready");
assert(
  !presentationSurfaceSchema.safeParse(readyWithoutDetailRef).success,
  "ready surface without a detail_ref must fail closed",
);

const invalidSurface = {
  ...baseSurface("invalid"),
  detail_ref: detailRef(),
};
assert(
  !presentationSurfaceSchema.safeParse(invalidSurface).success,
  "invalid surface with a detail_ref must fail closed",
);

const malformedDetailRef = {
  ...baseSurface("ready"),
  detail_ref: { ...detailRef(), payload_sha256: "not-a-hash" },
};
assert(
  !presentationSurfaceSchema.safeParse(malformedDetailRef).success,
  "malformed detail_ref payload hash must fail closed",
);

const unknownCollection = {
  schema_version: "extension_presentation_surfaces_v99",
  count: 0,
  ready_count: 0,
  review_due_count: 0,
  empty_count: 0,
  invalid_count: 0,
  items: [],
};
assert(
  !presentationSurfaceCollectionSchema.safeParse(unknownCollection).success,
  "unknown collection schema must fail closed",
);

const projection = parsePresentationProjection(projectionFixture);
assert(
  projection.view_schema === "decision_research_dashboard_v0",
  "projection must declare the finance view schema",
);
assert(
  projection.view.entities[0]!.observations.every(
    (observation) => observation.source_ref.length > 0,
  ),
  "every entity observation must carry an evidence source_ref",
);
assert(
  projection.view.research_ledger.every((entry) => entry.evidence_refs.length >= 1),
  "every research-ledger case must carry at least one evidence ref",
);

const driftedProbability = structuredClone(projectionFixture) as {
  projection: { view: { entities: { scenario_estimates: { probability: number }[] }[] } };
};
driftedProbability.projection.view.entities[0]!.scenario_estimates[0]!.probability = 0.9;
assert(
  !presentationProjectionResponseSchema.safeParse(driftedProbability).success,
  "scenario probabilities not summing to 1 must fail closed",
);

const tamperedBoundary = structuredClone(projectionFixture) as {
  projection: { view: { boundary: { trading_allowed: boolean } } };
};
tamperedBoundary.projection.view.boundary.trading_allowed = true;
assert(
  !presentationProjectionResponseSchema.safeParse(tamperedBoundary).success,
  "a trading_allowed=true boundary must fail closed",
);

async function verifyProjectionFetchContract() {
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async (input) => {
    requestedUrl = String(input);
    return new Response(JSON.stringify(projectionFixture), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    });
  };
  const matchingSurface = presentationSurfaceSchema.parse({
    ...baseSurface("ready"),
    extension_id: projection.extension_id,
    extension_revision: projection.extension_revision,
    surface_id: projection.surface_id,
    surface_kind: projection.surface_kind,
    goal_id: projection.goal_id,
    generated_at: projection.generated_at,
    review_due_at: projection.review_due_at,
    detail_ref: {
      extension_id: projection.extension_id,
      surface_id: projection.surface_id,
      extension_revision: projection.extension_revision,
      payload_sha256: projection.payload_sha256,
    },
  });
  if (matchingSurface.state !== "ready") {
    throw new Error("matching projection fixture must produce a ready surface");
  }

  try {
    const fetched = await fetchPresentationProjection(
      "http://127.0.0.1:8765/extension-projection",
      matchingSurface,
    );
    assert(fetched.payload_sha256 === projection.payload_sha256, "matching projection must load");
    const requested = new URL(requestedUrl);
    assert(
      requested.searchParams.get("payload_sha256") === projection.payload_sha256,
      "fetch must bind the advertised payload hash",
    );

    let mismatchRejected = false;
    try {
      await fetchPresentationProjection(
        "http://127.0.0.1:8765/extension-projection",
        {
          ...matchingSurface,
          detail_ref: {
            ...matchingSurface.detail_ref,
            payload_sha256: "f".repeat(64),
          },
        },
      );
    } catch (error) {
      mismatchRejected = String(error).includes(
        "projection response does not match the requested detail_ref",
      );
    }
    assert(mismatchRejected, "projection identity mismatch must fail closed");

    let publicHostRejected = false;
    requestedUrl = "";
    try {
      await fetchPresentationProjection(
        "https://public.example/extension-projection",
        matchingSurface,
      );
    } catch (error) {
      publicHostRejected = String(error).includes(
        "projection detail URL must use a loopback host",
      );
    }
    assert(publicHostRejected, "public projection detail URLs must fail closed");
    assert(requestedUrl === "", "public projection detail URL must be rejected before fetch");

    let ownerOnlyRejected = false;
    try {
      await fetchPresentationProjection(
        "http://127.0.0.1:8765/extension-projection",
        {
          ...matchingSurface,
          visibility: "owner-only",
        },
      );
    } catch (error) {
      ownerOnlyRejected = String(error).includes(
        "rich projection rendering requires public-safe visibility",
      );
    }
    assert(ownerOnlyRejected, "owner-only projection surfaces must fail closed");
  } finally {
    globalThis.fetch = originalFetch;
  }
}

verifyProjectionFetchContract()
  .then(() => {
    console.log("presentation surface schema smoke ok");
  })
  .catch((error: unknown) => {
    console.error(error);
    throw error;
  });

// This bounded observation must not claim the distinct full lifecycle RFC id.
const acceptanceObservation = {
  schema_version: "goal_acceptance_observation_projection_v0",
  goal_id: "synthetic-goal", read_only: true, acceptance_assessed: false,
  coverage: "partial", missing_sources: [], truncated: false,
  historical_progress: [], acceptance_gaps: [], guards: [],
  next_action: null, next_action_source: null,
};
assert(goalAcceptanceObservationSchema.safeParse(acceptanceObservation).success, "acceptance observation v0 must parse");
assert(!goalAcceptanceObservationSchema.safeParse({ ...acceptanceObservation, schema_version: "goal_artifact_lifecycle_projection_v0" }).success, "full lifecycle v0 is a distinct contract, not an observation alias");
