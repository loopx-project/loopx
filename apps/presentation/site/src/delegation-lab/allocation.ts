import scenario from "../../../../../examples/collaboration-delivery/scenario.json" with { type: "json" };

// Browser-only teaching state. These are not LoopX protocol/authority owners.
export const orders = [...scenario.orders].sort((a, b) => a.id.localeCompare(b.id));
export const stock = scenario.stock;
export const budget = scenario.budget;
export const regionCapacity = scenario.region_cap;
export const vectorCount = orders.reduce((total, row) => total * (row.demand + 1), 1);
export type Policy = { reserve: number; minimumEast: number };
export type Request = { revision: number; policy: Policy };
export type Plan = { quantities: number[]; value: number; cost: number };
export type Violation = { subject: string; used: number; limit: number; minimum?: boolean };
export type Inspection = { quantities: number[]; invalid: string[]; violations: Violation[]; value: number; cost: number };
export type Review = { requestRevision: number; draft: string[]; accepted: boolean; inspection: Inspection; best: Plan | null };

export const policies: Policy[] = [
  { reserve: 0, minimumEast: 0 },
  { reserve: 1, minimumEast: 4 },
  { reserve: 1, minimumEast: 99 },
];
// Published fixture outputs, deliberately separate from the exhaustive checker.
export const fixturePlans = [[1, 1, 3, 2, 0, 2], [2, 1, 3, 1, 0, 2], null];

export function inspectPlan(draft: readonly string[], policy: Policy): Inspection {
  const quantities = draft.map((value) => value.trim() === "" ? NaN : Number(value));
  const invalid = orders.filter((row, i) => !Number.isSafeInteger(quantities[i]) || quantities[i] < 0 || quantities[i] > row.demand).map((row) => row.id);
  if (draft.length !== orders.length || invalid.length) return { quantities, invalid: invalid.length ? invalid : ["a–f"], violations: [], value: 0, cost: 0 };
  const sum = (field: "stock_group" | "region", key: string) => orders.reduce((total, row, i) => total + (row[field] === key ? quantities[i] : 0), 0);
  const cost = orders.reduce((total, row, i) => total + quantities[i] * row.cost, 0);
  const value = orders.reduce((total, row, i) => total + quantities[i] * row.value, 0);
  const violations: Violation[] = [];
  for (const [key, amount] of Object.entries(stock)) {
    const used = sum("stock_group", key), limit = amount - policy.reserve;
    if (used > limit) violations.push({ subject: key, used, limit });
  }
  for (const [key, limit] of Object.entries(scenario.region_cap)) {
    const used = sum("region", key);
    if (used > limit) violations.push({ subject: key, used, limit });
  }
  if (cost > budget) violations.push({ subject: "budget", used: cost, limit: budget });
  const east = sum("region", "east");
  if (east < policy.minimumEast) violations.push({ subject: "east", used: east, limit: policy.minimumEast, minimum: true });
  return { quantities, invalid, violations, value, cost };
}

// All 2,400 integer vectors for this six-order fixture; no generated solver,
// model call, server or native Inbox is involved. Ascending enumeration keeps
// the lexicographically smallest vector when values tie.
export function enumerateBest(policy: Policy): Plan | null {
  let best: Plan | null = null;
  function visit(q: number[]) {
    if (q.length < orders.length) {
      for (let n = 0; n <= orders[q.length].demand; n++) visit([...q, n]);
      return;
    }
    const cost = q.reduce((total, n, i) => total + n * orders[i].cost, 0);
    if (cost > budget) return;
    for (const [key, capacity] of Object.entries(stock)) {
      if (q.reduce((total, n, i) => total + (orders[i].stock_group === key ? n : 0), 0) > capacity - policy.reserve) return;
    }
    for (const [key, capacity] of Object.entries(scenario.region_cap)) {
      if (q.reduce((total, n, i) => total + (orders[i].region === key ? n : 0), 0) > capacity) return;
    }
    if (q.reduce((total, n, i) => total + (orders[i].region === "east" ? n : 0), 0) < policy.minimumEast) return;
    const value = q.reduce((total, n, i) => total + n * orders[i].value, 0);
    if (!best || value > best.value) best = { quantities: q, value, cost };
  }
  visit([]);
  return best;
}

export function reviewPlan(request: Request, draft: readonly string[]): Review {
  const inspection = inspectPlan(draft, request.policy), best = enumerateBest(request.policy);
  const accepted = !inspection.invalid.length && !inspection.violations.length && best !== null && best.quantities.every((n, i) => n === inspection.quantities[i]);
  return { requestRevision: request.revision, draft: [...draft], accepted, inspection, best };
}

export function reviewIsCurrent(request: Request, draft: readonly string[], review: Review | null) {
  return review !== null && review.requestRevision === request.revision && review.draft.length === draft.length && review.draft.every((n, i) => n === draft[i]);
}

export function canReturn(request: Request, planRevision: number, receiptRevision: number | null, draft: readonly string[], review: Review | null) {
  return receiptRevision === request.revision && planRevision === request.revision && reviewIsCurrent(request, draft, review) && review?.accepted === true;
}
