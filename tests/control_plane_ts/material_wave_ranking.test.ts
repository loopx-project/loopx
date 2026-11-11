import assert from "node:assert/strict";
import test from "node:test";

import {planMaterialWaveRerank} from "../../loopx/control_plane/capabilities/material_wave_ranking.ts";
import {
  createEffectRuntimeHandlers,
  dispatchEffectRuntimeMethod,
} from "../../loopx/control_plane/effect_runtime_handlers.ts";

const handlers = createEffectRuntimeHandlers({
  fingerprint: "wave-ranking-test",
  requestShutdown: () => undefined,
});

test("stratifies unsliced deep monographs behind quick and focused sprint slices unless scoped_slice_ready is true", async () => {
  const ordered = [
    "material-monograph-course",
    "material-focused-study",
    "material-quick-baseline",
    "material-sliced-monograph",
  ];
  const result = (await dispatchEffectRuntimeMethod(
    handlers,
    "material_lifecycle.plan_wave_rerank",
    {
      ordered_material_refs: ordered,
      top_window_size: 4,
      annotations: [
        {
          material_ref: "material-monograph-course",
          horizon_wave: "imminent_gate",
          effort_band: "deep_monograph",
          scoped_slice_ready: false,
          decision_gain_score: 95,
          evidence_role: "foundational_reference",
        },
        {
          material_ref: "material-focused-study",
          horizon_wave: "imminent_gate",
          effort_band: "focused_study",
          decision_gain_score: 70,
          evidence_role: "method_comparator",
        },
        {
          material_ref: "material-quick-baseline",
          horizon_wave: "imminent_gate",
          effort_band: "quick_slice",
          decision_gain_score: 60,
          evidence_role: "method_comparator",
        },
        {
          material_ref: "material-sliced-monograph",
          horizon_wave: "imminent_gate",
          effort_band: "deep_monograph",
          scoped_slice_ready: true,
          decision_gain_score: 90,
          evidence_role: "method_comparator",
        },
      ],
    },
  )) as Record<string, unknown>;

  // quick_slice VOI = 60/1 = 60, focused_study VOI = 70/2 = 35, sliced deep_monograph VOI = 90/6 = 15;
  // unsliced deep_monograph is stratified to foundational_track at the end.
  assert.deepEqual(result.reordered_material_refs, [
    "material-quick-baseline",
    "material-focused-study",
    "material-sliced-monograph",
    "material-monograph-course",
  ]);
  assert.deepEqual(result.demoted_monograph_refs, ["material-monograph-course"]);
});

test("enforces unranked load-bearing primary gate, topological primary-over-secondary lineage, cycles, and anchor deadlines", () => {
  assert.throws(
    () =>
      planMaterialWaveRerank({
        ordered_material_refs: ["material-secondary-review", "material-quick-baseline"],
        required_load_bearing_refs: ["material-primary-spec"],
      }),
    /missing or unranked required load-bearing primary materials.*material-primary-spec/,
  );

  // Buried primary at rank 4 is pulled immediately ahead of secondary commentary in imminent_gate.
  const promoted = planMaterialWaveRerank({
    ordered_material_refs: [
      "material-quick-baseline",
      "material-secondary-review",
      "material-backlog-item",
      "material-primary-spec",
    ],
    top_window_size: 4,
    required_load_bearing_refs: ["material-primary-spec"],
    annotations: [
      {
        material_ref: "material-quick-baseline",
        horizon_wave: "imminent_gate",
        effort_band: "quick_slice",
        decision_gain_score: 90,
        evidence_role: "load_bearing_primary",
        blocks_active_deliverable: true,
      },
      {
        material_ref: "material-secondary-review",
        horizon_wave: "imminent_gate",
        effort_band: "quick_slice",
        decision_gain_score: 70,
        evidence_role: "secondary_or_commentary",
        primary_source_refs: ["material-primary-spec"],
      },
    ],
  });
  assert.deepEqual(promoted.reordered_material_refs, [
    "material-quick-baseline",
    "material-primary-spec",
    "material-secondary-review",
    "material-backlog-item",
  ]);
  assert.deepEqual(promoted.lineage_promoted_refs, ["material-primary-spec"]);

  // Cycle detection fails closed.
  assert.throws(
    () =>
      planMaterialWaveRerank({
        ordered_material_refs: ["material-alpha", "material-beta"],
        annotations: [
          {
            material_ref: "material-alpha",
            primary_source_refs: ["material-beta"],
          },
          {
            material_ref: "material-beta",
            primary_source_refs: ["material-alpha"],
          },
        ],
      }),
    /primary_source_refs cycle detected/,
  );

  // Protected secondary at rank 3 with protected anchor at rank 1 places primary into open slot 2.
  const tightSlot = planMaterialWaveRerank({
    ordered_material_refs: [
      "material-anchor-one",
      "material-other-candidate",
      "material-protected-secondary",
      "material-buried-primary",
    ],
    protected_material_refs: ["material-anchor-one", "material-protected-secondary"],
    annotations: [
      {
        material_ref: "material-other-candidate",
        horizon_wave: "imminent_gate",
        effort_band: "quick_slice",
        decision_gain_score: 95,
      },
      {
        material_ref: "material-protected-secondary",
        primary_source_refs: ["material-buried-primary"],
      },
    ],
  });
  assert.deepEqual(tightSlot.reordered_material_refs, [
    "material-anchor-one",
    "material-buried-primary",
    "material-protected-secondary",
    "material-other-candidate",
  ]);

  // Protected anchor inversion with zero open slots before protected secondary fails closed.
  assert.throws(
    () =>
      planMaterialWaveRerank({
        ordered_material_refs: [
          "material-anchor-one",
          "material-protected-secondary",
          "material-buried-primary",
        ],
        protected_material_refs: ["material-anchor-one", "material-protected-secondary"],
        annotations: [
          {
            material_ref: "material-protected-secondary",
            primary_source_refs: ["material-buried-primary"],
          },
        ],
      }),
    /protected anchor inversion/,
  );
});

test("preserves 300-item queue ergonomics: active waves 1..16, untouched 17..30, demoted orthogonal 31..34, untouched 35..300", () => {
  const ordered = Array.from(
    {length: 300},
    (_, idx) => `material:item-${String(idx + 1).padStart(3, "0")}`,
  );
  const orthogonalRefs = [
    "material:item-003",
    "material:item-007",
    "material:item-012",
    "material:item-019",
  ];
  const activeRefs = [
    "material:item-041",
    "material:item-042",
    "material:item-043",
    "material:item-044",
    "material:item-051",
    "material:item-052",
    "material:item-053",
    "material:item-054",
    "material:item-055",
    "material:item-056",
    "material:item-071",
    "material:item-072",
    "material:item-073",
    "material:item-074",
    "material:item-075",
    "material:item-076",
  ];

  const annotations = [
    ...activeRefs.slice(0, 4).map((ref, idx) => ({
      material_ref: ref,
      horizon_wave: "imminent_gate",
      effort_band: "quick_slice",
      decision_gain_score: 90 - idx,
      evidence_role: "load_bearing_primary",
      blocks_active_deliverable: true,
    })),
    ...activeRefs.slice(4, 10).map((ref, idx) => ({
      material_ref: ref,
      horizon_wave: "active_sprint",
      effort_band: "focused_study",
      decision_gain_score: 80 - idx,
      evidence_role: "method_comparator",
    })),
    ...activeRefs.slice(10, 16).map((ref, idx) => ({
      material_ref: ref,
      horizon_wave: "foundational_track",
      effort_band: "focused_study",
      decision_gain_score: 70 - idx,
      evidence_role: "foundational_reference",
    })),
    ...orthogonalRefs.map((ref, idx) => ({
      material_ref: ref,
      horizon_wave: "orthogonal_backlog",
      effort_band: "quick_slice",
      decision_gain_score: 50 - idx,
      evidence_role: "orthogonal_signal",
    })),
  ];

  const result = planMaterialWaveRerank({
    ordered_material_refs: ordered,
    annotations,
    top_window_size: 30,
    max_orthogonal_in_top_window: 0,
  });
  const reordered = result.reordered_material_refs as string[];

  assert.equal(reordered.length, 300);
  assert.deepEqual([...reordered].sort(), [...ordered].sort());
  assert.deepEqual(reordered.slice(0, 16), activeRefs);
  assert.equal(result.top_window_orthogonal_count, 0);
  assert.deepEqual(reordered.slice(30, 34), orthogonalRefs);

  const annotatedSet = new Set([...activeRefs, ...orthogonalRefs]);
  const untouchedExpected = ordered.filter((ref) => !annotatedSet.has(ref));
  assert.deepEqual(reordered.slice(16, 30), untouchedExpected.slice(0, 14));
  assert.deepEqual(reordered.slice(34), untouchedExpected.slice(14));
});

test("fails closed when load-bearing deliverable blocker cannot fit in top_window_size", () => {
  assert.throws(
    () =>
      planMaterialWaveRerank({
        ordered_material_refs: [
          "material-anchor-1",
          "material-anchor-2",
          "material-blocking-primary",
        ],
        top_window_size: 2,
        protected_material_refs: ["material-anchor-1", "material-anchor-2"],
        annotations: [
          {
            material_ref: "material-blocking-primary",
            horizon_wave: "imminent_gate",
            evidence_role: "load_bearing_primary",
            blocks_active_deliverable: true,
          },
        ],
      }),
    /load-bearing deliverable blocker material-blocking-primary landed at rank 3 outside top_window_size=2/,
  );
});
