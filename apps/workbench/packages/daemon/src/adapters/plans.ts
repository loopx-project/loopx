/** A plan name an adapter has no wording for, shown as reported but capitalised: `gold` → `Gold`. */
export function titlePlan(id: string): string {
  return id.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
