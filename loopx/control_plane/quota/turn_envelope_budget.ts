/** Performance diagnostics, never Turn admission or execution authority. */
import { turnStartPromptBudgetBytes } from "../capability_hooks.ts";
import type { JsonObject } from "../effect_program.ts";

export const TURN_ENVELOPE_BUDGET_BYTES = 8_192;

// Review allocations, not truncation limits. Preserve authority even on overflow.
export const TURN_ENVELOPE_SECTION_TARGETS = {
  action: 800, boundary: 2_000, writeback: 600, scheduler: 600,
  contracts: 1_800, context: 1_400, transport: 992,
} as const;
type Section = keyof typeof TURN_ENVELOPE_SECTION_TARGETS;
const SECTION_FIELDS: Record<string, Section> = {
  action: "action", user: "action", required_reads: "action",
  replan_action_packet: "action", response_plan: "action",
  boundary: "boundary", execution_policy: "boundary", writeback: "writeback",
  scheduler: "scheduler", contract_capsule: "contracts",
  agent_context: "context", work_context: "context", task_orchestration_contract: "context",
};

function sectionBytes(envelope: JsonObject): Record<Section, number> {
  const sizes = Object.fromEntries(
    Object.keys(TURN_ENVELOPE_SECTION_TARGETS).map((key) => [key, 0]),
  ) as Record<Section, number>;
  // Include property names, separators and braces; totals equal wire bytes.
  sizes.transport = 1;
  for (const [key, value] of Object.entries(envelope)) {
    sizes[SECTION_FIELDS[key] ?? "transport"] +=
      Buffer.byteLength(JSON.stringify(key) + ":" + JSON.stringify(value), "utf8") + 1;
  }
  return sizes;
}

export function turnEnvelopeBudgetBytes(envelope: JsonObject): number {
  const reads = Array.isArray(envelope.required_reads) ? envelope.required_reads : [];
  return TURN_ENVELOPE_BUDGET_BYTES + reads.reduce((total: number, value: unknown) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return total;
    const read = value as JsonObject;
    return total + (read.source === "turn_start_capability_hook"
      ? turnStartPromptBudgetBytes(read.prompt_budget_bytes) : 0);
  }, 0);
}

export function measureTurnEnvelope(envelope: JsonObject, source: JsonObject): void {
  // Keep v0 *_json_bytes code-point metrics for compatibility. New diagnostics
  // and the performance target use actual compact JSON UTF-8 bytes.
  const sourceChars = [...JSON.stringify(source)].length;
  const budgetBytes = turnEnvelopeBudgetBytes(envelope);
  const hookBudget = budgetBytes - TURN_ENVELOPE_BUDGET_BYTES;
  envelope.compaction = {
    source_json_bytes: sourceChars, envelope_json_bytes: 0,
    byte_reduction_ratio: 0, budget_bytes: budgetBytes,
    within_budget: true, envelope_utf8_bytes: 0,
  };
  // Measurements include their own serialized metadata. Recompute to a fixed
  // point (decimal widths and the four-place ratio stabilize after a few passes).
  const seen = new Set<string>();
  let ratioLocked = false;
  for (let pass = 0; pass < 16; pass += 1) {
    const before = JSON.stringify(envelope);
    // Rounding can alternate between e.g. 0.54 and 0.5401, changing its own
    // width. Freeze that approximate ratio on a cycle; byte counts stay exact.
    if (seen.has(before)) ratioLocked = true;
    seen.add(before);
    const chars = [...before].length;
    const bytes = Buffer.byteLength(before, "utf8");
    const metric: JsonObject = {
      source_json_bytes: sourceChars, envelope_json_bytes: chars,
      byte_reduction_ratio: ratioLocked
        ? (envelope.compaction as JsonObject).byte_reduction_ratio : sourceChars
        ? Math.round((1 - chars / sourceChars) * 10_000) / 10_000 : 0,
      budget_bytes: budgetBytes,
      within_budget: bytes <= budgetBytes,
      envelope_utf8_bytes: bytes,
    };
    if (hookBudget) metric.hook_prompt_budget_bytes = hookBudget;
    if (bytes > budgetBytes) {
      const sections = sectionBytes(envelope);
      metric.warning = {
        code: "turn_envelope_budget_exceeded", severity: "warning",
        excess_bytes: bytes - budgetBytes,
        section_bytes: sections,
        over_target_sections: (Object.keys(sections) as Section[])
          .filter((key) => sections[key] > TURN_ENVELOPE_SECTION_TARGETS[key] + (key === "action" ? hookBudget : 0)),
      };
    }
    envelope.compaction = metric;
    if (JSON.stringify(envelope) === before) break;
  }
}
