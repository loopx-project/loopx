/** Verbatim public capture reader, not an Explore state reducer or validator.
 * Explore owns v0 semantics. Pinned bytes prevent a different capture or edited
 * record from silently acquiring these editorial scenes. Status stays source text.
 */
export interface CapturedRecord {
  schema_version: string;
  goal_id: string;
  event_id: string;
  event_kind: string;
  result_id: string;
  recorded_at: string;
  status: string;
  title?: string;
  summary: string;
}

interface CapturePin {count: number; sha256: string; sourceHash: string; evidenceId: string}
export async function readPinnedCapture(bytes: ArrayBuffer, pin: CapturePin): Promise<CapturedRecord[]> {
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    byte => byte.toString(16).padStart(2, "0")).join("");
  if (digest !== pin.sha256) throw new Error("Capture digest mismatch");
  const capture = JSON.parse(new TextDecoder().decode(bytes));
  if (capture.source_sha256 !== pin.sourceHash || !Array.isArray(capture.events) ||
      capture.events.length !== pin.count) throw new Error("Wrong capture");
  const seen = new Set<string>();
  for (const event of capture.events) {
    if (!event || event.schema_version !== "loopx_explore_result_event_v0" ||
        event.goal_id !== capture.goal_id ||
        ["event_id", "event_kind", "result_id", "recorded_at", "status", "summary"].some(
          key => typeof event[key] !== "string") ||
        !Number.isFinite(Date.parse(event.recorded_at)) || seen.has(event.event_id) ||
        (event.title !== undefined && typeof event.title !== "string")) throw new Error("Invalid capture record");
    seen.add(event.event_id);
  }
  if (!capture.events.some((event: CapturedRecord) => event.result_id === pin.evidenceId)) {
    throw new Error("Scene evidence is missing");
  }
  // Original append order is the record reader's order, even for equal or
  // out-of-order timestamps. No latest-state, historical knowledge or causal claim.
  return capture.events;
}

export const captureRevision = "44c7af3db271ab1991428d3ff1991ca922434eff";
export const datasetUrl = "https://huggingface.co/datasets/huangrt01/loopx-edgebench-research-checkpoints-20261015/tree/c858f51021dc1c1cf08263f9c04b692a5e29b40b";
