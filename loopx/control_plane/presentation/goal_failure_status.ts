/** Preserve the existing conservative display signal for extensible legacy
 * labels. This never proves success or authorizes execution. Replace it with
 * normalized runtime failure facts under the TS migration contract; until then
 * even an error-recovery label may stay flagged rather than hide a failure. */
export function hasGoalFailureStatus(values: readonly unknown[]): boolean {
  return values.some(value => typeof value === "string"
    && /(^|[_\s-])(failure|failed|error|broken|unhealthy|blocked[_\s-]?health|health[_\s-]?blocked)([_\s-]|$)/i.test(value));
}
