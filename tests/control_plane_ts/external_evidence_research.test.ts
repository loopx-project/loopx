import assert from "node:assert/strict";
import test from "node:test";

import {
  evaluateExternalEvidenceAdmission,
  planExternalEvidenceRequest,
  projectExternalEvidenceDiscovery,
  projectExternalEvidenceRetirement,
  recordExternalEvidenceReceiptObservation,
} from "../../loopx/control_plane/capabilities/external_evidence.ts";

const request = {
  objective: "Compare current provider behavior",
  user_activity: "Choose a research provider for a decision",
  decision: "Whether the observed evidence is strong enough to adopt",
  evidence_kinds: ["current_behavior", "counterexample"],
  constraints: ["public sources only"],
};

const methodProvider = {
  provider_id: "host:external-research",
  provider_kind: "method",
  protocol: "external_evidence_research_v0",
  declared: true,
  installed: true,
  enabled: true,
  ready: true,
  unavailable_reason: null,
};

const registryOnlyConnector = {
  provider_id: "connector:official-docs",
  provider_kind: "connector",
  protocol: "external_evidence_research_v0",
  declared: true,
  installed: false,
  enabled: false,
  ready: false,
  unavailable_reason: "connector_registry_is_inventory_not_readiness",
};

function plan() {
  return planExternalEvidenceRequest({
    request,
    providers: [methodProvider, registryOnlyConnector],
    preferred_provider_id: "host:external-research",
  });
}

function receipt(currentPlan: Record<string, unknown>) {
  const currentRequest = currentPlan.request as Record<string, unknown>;
  return {
    schema_version: "loopx_external_evidence_receipt_v0",
    plan_id: currentPlan.plan_id,
    request_id: currentRequest.request_id,
    provider_id: methodProvider.provider_id,
    provider_kind: methodProvider.provider_kind,
    status: "succeeded",
    sources: [
      {
        source_ref: "https://example.com/original",
        source_family: "example-release",
        basis: "observed",
        finding: "The current release exposes the required interaction.",
        limitation: "The page does not prove backend durability.",
        publication_date: "2026-09-19",
        accessed_at: "2026-09-20T10:00:00Z",
        content_digest: `sha256:${"a".repeat(64)}`,
      },
    ],
    summary: "One direct source supports the interaction claim.",
    limitations: ["No durability test was performed."],
    completed_at: "2026-09-20T10:01:00Z",
  };
}

test("discovers method and connector inventory without claiming execution", () => {
  const result = projectExternalEvidenceDiscovery({
    providers: [methodProvider, registryOnlyConnector],
  });
  assert.equal(result.status, "ready");
  assert.deepEqual(result.ready_provider_ids, ["host:external-research"]);
  assert.deepEqual(result.summary, {
    provider_count: 2,
    method_count: 1,
    connector_count: 1,
    ready_count: 1,
    unavailable_count: 1,
  });
  assert.deepEqual(result.truth_contract, {
    registry_presence_is_readiness: false,
    supported_status_is_readiness: false,
    execution_observed: false,
    evidence_coverage_observed: false,
  });
});

test("reports connector-only discovery as inventory-only", () => {
  const result = projectExternalEvidenceDiscovery({
    providers: [registryOnlyConnector],
  });
  assert.equal(result.status, "inventory_only");
  assert.deepEqual(result.ready_provider_ids, []);
});

test("plans one ready provider without treating registry presence as readiness", () => {
  const result = plan();
  assert.equal(result.status, "ready");
  assert.equal(
    (result.selected_provider as Record<string, unknown>).provider_id,
    "host:external-research",
  );
  assert.equal(
    (result.execution_envelope as Record<string, unknown>).authority,
    "read_external_sources_only",
  );
  assert.match(
    String((result.request as Record<string, unknown>).request_id),
    /^sha256:[0-9a-f]{64}$/,
  );
  assert.match(String(result.plan_id), /^sha256:[0-9a-f]{64}$/);
  assert.equal(
    (result.execution_envelope as Record<string, unknown>).plan_id,
    result.plan_id,
  );
});
test("blocks when every provider is inventory-only", () => {
  const result = planExternalEvidenceRequest({
    request,
    providers: [registryOnlyConnector],
  });
  assert.equal(result.status, "blocked");
  assert.equal(result.selected_provider, null);
  assert.equal(result.blocker, "no_ready_provider");
});

test("rejects a provider that claims ready without lifecycle readiness", () => {
  assert.throws(
    () => planExternalEvidenceRequest({
      request,
      providers: [{ ...registryOnlyConnector, ready: true }],
    }),
    /cannot be ready/,
  );
});

test("records a provider receipt without attesting execution or claiming coverage", () => {
  const currentPlan = plan();
  const result = recordExternalEvidenceReceiptObservation({
    plan: currentPlan,
    receipt: receipt(currentPlan),
  });
  assert.equal(result.status, "succeeded");
  assert.equal(result.plan_id, currentPlan.plan_id);
  assert.match(String(result.receipt_observation_id), /^sha256:[0-9a-f]{64}$/);
  assert.deepEqual(result.truth_contract, {
    provider_receipt_observed: true,
    provider_execution_attested: false,
    evidence_produced_reported: true,
    evidence_coverage_observed: false,
    automatic_admission: false,
    automatic_promotion: false,
    raw_source_fallback_allowed: true,
  });
});

test("execution receipt fails closed on stale provider identity", () => {
  const currentPlan = plan();
  assert.throws(
    () => recordExternalEvidenceReceiptObservation({
      plan: currentPlan,
      receipt: { ...receipt(currentPlan), provider_id: "connector:stale" },
    }),
    /provider_id does not match/,
  );
});

test("execution receipt fails closed on stale plan identity", () => {
  const currentPlan = plan();
  assert.throws(
    () => recordExternalEvidenceReceiptObservation({
      plan: currentPlan,
      receipt: { ...receipt(currentPlan), plan_id: `sha256:${"b".repeat(64)}` },
    }),
    /plan_id does not match/,
  );
});

test("canonical ready-plan verification rejects semantic mutations", () => {
  const mutations: Array<[
    string,
    (value: Record<string, unknown>) => void,
  ]> = [
    ["objective", (value) => {
      (value.request as Record<string, unknown>).objective = "Use a different objective";
    }],
    ["decision", (value) => {
      (value.request as Record<string, unknown>).decision = "Make a different decision";
    }],
    ["constraints", (value) => {
      (value.request as Record<string, unknown>).constraints = ["private sources allowed"];
    }],
    ["provider readiness", (value) => {
      const candidates = value.provider_candidates as Array<Record<string, unknown>>;
      candidates[0].ready = false;
      candidates[0].unavailable_reason = "became unavailable";
    }],
    ["execution envelope", (value) => {
      (value.execution_envelope as Record<string, unknown>).authority = "write_external_sources";
    }],
  ];

  for (const [label, mutate] of mutations) {
    const currentPlan = plan();
    const mutatedPlan = structuredClone(currentPlan) as Record<string, unknown>;
    mutate(mutatedPlan);
    assert.throws(
      () => recordExternalEvidenceReceiptObservation({
        plan: mutatedPlan,
        receipt: receipt(currentPlan),
      }),
      undefined,
      label,
    );
  }
});

test("admits exact source refs and exposes only compact provenance", () => {
  const currentPlan = plan();
  const result = evaluateExternalEvidenceAdmission({
    plan: currentPlan,
    receipt: receipt(currentPlan),
    decision: {
      disposition: "admit",
      reason: "The source directly answers the interaction question.",
      admitted_source_refs: ["https://example.com/original"],
    },
  });
  assert.equal(result.disposition, "admit");
  assert.equal(result.plan_id, currentPlan.plan_id);
  assert.match(String(result.admission_id), /^sha256:[0-9a-f]{64}$/);
  assert.match(String(result.receipt_digest), /^sha256:[0-9a-f]{64}$/);
  const projection = result.downstream_projection as Record<string, unknown>;
  assert.equal((projection.sources as unknown[]).length, 1);
  assert.equal(Object.hasOwn(projection, "raw_content"), false);
});

for (const duplicate of [
  "https://example.com/original",
  "  https://example.com/original  ",
]) {
  test(`admission rejects duplicate source refs after normalization: ${JSON.stringify(duplicate)}`, () => {
    const currentPlan = plan();
    assert.throws(
      () => evaluateExternalEvidenceAdmission({
        plan: currentPlan,
        receipt: receipt(currentPlan),
        decision: {
          disposition: "admit",
          reason: "Direct evidence must yield a readable admission.",
          admitted_source_refs: ["https://example.com/original", duplicate],
        },
      }),
      /decision\.admitted_source_refs must be unique/,
    );
  });
}

test("distinct source selections remain readable through retirement", () => {
  const currentPlan = plan();
  const currentReceipt = receipt(currentPlan);
  const first = "https://example.com/original";
  const second = "https://example.com/second";
  currentReceipt.sources.push({ ...currentReceipt.sources[0], source_ref: second });
  for (const refs of [[second], [second, first]]) {
    const admission = evaluateExternalEvidenceAdmission({
      plan: currentPlan,
      receipt: currentReceipt,
      decision: {
        disposition: "admit",
        reason: "Selected sources answer the question.",
        admitted_source_refs: refs,
      },
    });
    assert.deepEqual(admission.admitted_source_refs, refs);
    assert.equal(
      projectExternalEvidenceRetirement({ admission, downstream_source_refs: [] }).status,
      "retained",
    );
    assert.equal(
      projectExternalEvidenceRetirement({ admission, downstream_source_refs: refs }).status,
      "retire_ready",
    );
  }
});

test("admission fails closed on stale plan identity and local file provenance", () => {
  const currentPlan = plan();
  assert.throws(
    () => evaluateExternalEvidenceAdmission({
      plan: currentPlan,
      receipt: {
        ...receipt(currentPlan),
        request_id: `sha256:${"b".repeat(64)}`,
      },
      decision: {
        disposition: "admit",
        reason: "stale",
        admitted_source_refs: ["https://example.com/original"],
      },
    }),
    /request_id does not match/,
  );
  const localReceipt = receipt(currentPlan);
  localReceipt.sources[0].source_ref = "file:///tmp/raw-transcript";
  assert.throws(
    () => evaluateExternalEvidenceAdmission({
      plan: currentPlan,
      receipt: localReceipt,
      decision: {
        disposition: "admit",
        reason: "local path",
        admitted_source_refs: ["file:///tmp/raw-transcript"],
      },
    }),
    /non-file provenance URI/,
  );
});

test("retirement waits for downstream use of every admitted source", () => {
  const currentPlan = plan();
  const admission = evaluateExternalEvidenceAdmission({
    plan: currentPlan,
    receipt: receipt(currentPlan),
    decision: {
      disposition: "admit",
      reason: "direct evidence",
      admitted_source_refs: ["https://example.com/original"],
    },
  });
  assert.equal(
    projectExternalEvidenceRetirement({ admission, downstream_source_refs: [] }).status,
    "retained",
  );
  assert.equal(
    projectExternalEvidenceRetirement({ admission, downstream_source_refs: [] }).plan_id,
    currentPlan.plan_id,
  );
  assert.equal(
    projectExternalEvidenceRetirement({
      admission,
      downstream_source_refs: ["https://example.com/original"],
    }).status,
    "retire_ready",
  );
});

test("retirement fails closed on mutated admission semantics", () => {
  const currentPlan = plan();
  const admission = evaluateExternalEvidenceAdmission({
    plan: currentPlan,
    receipt: receipt(currentPlan),
    decision: {
      disposition: "admit",
      reason: "direct evidence",
      admitted_source_refs: ["https://example.com/original"],
    },
  });
  const forgedReject = structuredClone(admission) as Record<string, unknown>;
  forgedReject.disposition = "reject";
  forgedReject.admitted_source_refs = [];
  const forgedProjection = forgedReject.downstream_projection as Record<string, unknown>;
  forgedProjection.disposition = "reject";
  forgedProjection.sources = [];
  assert.throws(
    () => projectExternalEvidenceRetirement({
      admission: forgedReject,
      downstream_source_refs: [],
    }),
    /admission_id does not match/,
  );

  const mutatedFinding = structuredClone(admission) as Record<string, unknown>;
  const mutatedProjection = mutatedFinding.downstream_projection as Record<string, unknown>;
  const mutatedSources = mutatedProjection.sources as Array<Record<string, unknown>>;
  mutatedSources[0].finding = "A different finding";
  assert.throws(
    () => projectExternalEvidenceRetirement({
      admission: mutatedFinding,
      downstream_source_refs: ["https://example.com/original"],
    }),
    /admission_id does not match/,
  );
});
