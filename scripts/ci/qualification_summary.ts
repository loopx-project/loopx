/** Read-only GitHub evidence projection. The existing Python gate owns the verdict. */
export interface JobEvidence {
  id: number;
  run_id: number;
  run_attempt: number;
  name: string;
  status: string;
  conclusion: string | null;
  steps?: {name: string; conclusion: string | null}[];
}

export interface QualificationSummary {
  repository: string;
  serverUrl: string;
  runId: number;
  runAttempt: number;
  sourceSha: string;
  checkoutSha: string;
  baseSha: string;
  gateResult: string;
  needs: Record<string, {result: string; outputs?: Record<string, string>}>;
  jobs: JobEvidence[] | null;
}

// Display grouping only: no exemption or failure waiver is derived from these names.
const AGGREGATES = new Set([
  "checks", "pytest", "merge-gate", "stage2c-correctness-e2e",
  "typescript-coverage", "dashboard-acceptance",
]);

function text(value: string): string {
  return value.replace(/[\x00-\x1f\x7f]/g, " ").slice(0, 200)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replace(/[\\`*_[\]{}()|#!]/g, "\\$&");
}

export function renderQualificationSummary(input: QualificationSummary): string {
  const origin = new URL(input.serverUrl);
  if (origin.protocol !== "https:" || !/^[\w.-]+\/[\w.-]+$/.test(input.repository)
      || !Number.isSafeInteger(input.runId) || input.runId <= 0
      || !Number.isSafeInteger(input.runAttempt) || input.runAttempt <= 0) {
    throw new Error("Invalid workflow run identity");
  }
  const runUrl = `${origin.origin}/${input.repository}/actions/runs/${input.runId}`;
  const lines = [
    "## Qualification evidence", "",
    `Gate step: ${text(input.gateResult)}. This report does not grant or waive qualification.`, "",
    `Source head: ${text(input.sourceSha) || "unavailable"}`,
    `Workflow checkout: ${text(input.checkoutSha) || "unavailable"}`,
    `Event base: ${text(input.baseSha) || "not recorded for this event"}`,
    `Run: [${input.runId}, attempt ${input.runAttempt}](${runUrl}/attempts/${input.runAttempt})`,
    `Change kind: ${text(input.needs.changes?.outputs?.change_kind ?? "unavailable")}`, "",
    "### Gate dependencies", "", "| Dependency | Result |", "| --- | --- |",
  ];
  for (const [name, need] of Object.entries(input.needs)) {
    lines.push(`| ${text(name)} | ${text(need.result)} |`);
  }
  lines.push("", "Skipped jobs are not passing test evidence; the gate verifies whether an exemption is allowed.");
  if (input.jobs === null) {
    lines.push("", `Job evidence unavailable. Inspect the [workflow run](${runUrl}); retain the gate result above.`);
    return lines.join("\n") + "\n";
  }

  lines.push("", "Job evidence includes advisory checks; the gate result and dependency policy determine qualification.");
  const jobs = input.jobs.filter(job => job.run_id === input.runId && job.run_attempt === input.runAttempt);
  if (jobs.length !== input.jobs.length) {
    lines.push("", `Excluded ${input.jobs.length - jobs.length} jobs from another run or attempt.`);
  }
  const failed = jobs.filter(job => job.conclusion === "failure" || job.conclusion === "timed_out"
    || job.conclusion === "action_required" || job.conclusion === "startup_failure");
  const incomplete = jobs.filter(job => job.conclusion === "cancelled" || job.status !== "completed")
    // This summary executes inside the gate; its own running state is expected.
    .filter(job => job.name !== "merge-gate");
  const table = (title: string, rows: JobEvidence[]) => {
    if (!rows.length) return;
    lines.push("", `### ${title}`, "", "| Job | Result | Failed steps |", "| --- | --- | --- |");
    for (const job of rows) {
      const label = text(job.name);
      const link = Number.isSafeInteger(job.id) && job.id > 0 ? `[${label}](${runUrl}/job/${job.id})` : label;
      const steps = job.steps?.filter(step => step.conclusion === "failure" || step.conclusion === "timed_out") ?? [];
      const detail = steps.length ? steps.map(step => text(step.name)).join("; ") : "No failed step reported";
      lines.push(`| ${link} | ${text(job.conclusion ?? job.status)} | ${detail} |`);
    }
  };
  table("Failing jobs (inspect steps for test or setup errors)", failed.filter(job => !AGGREGATES.has(job.name)));
  table("Aggregate failures (may follow upstream failures or missing evidence)", failed.filter(job => AGGREGATES.has(job.name)));
  table("Incomplete jobs", incomplete);
  if (!jobs.length) lines.push("", "No jobs returned for this attempt; absence is not proof of success.");
  return lines.join("\n") + "\n";
}
