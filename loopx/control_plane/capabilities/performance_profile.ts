/** Browser-safe profile rules shared by CLI and local frontend inspection. */
type JsonObject = Record<string, unknown>;
const object = (value: unknown, label: string): JsonObject => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value as JsonObject;
};
const text = (value: unknown, label: string): string => {
  if (typeof value !== "string" || !value.trim() || value.includes("\0")) throw new Error(`${label} must be nonempty text without NUL`);
  return value;
};
const number = (value: unknown, label: string): number => {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new Error(`${label} must be finite and nonnegative`);
  return value;
};
const array = (value: unknown, label: string): unknown[] => {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
};

interface Frame {name: string; file?: string; line?: number}
interface Observation {frame: Frame; self: number; inclusive: number}
const frame = (value: unknown): Frame => {
  const raw = object(value, "frame");
  const result: Frame = {name: text(raw.name, "frame.name")};
  if (raw.file !== undefined && raw.file !== "") result.file = text(raw.file, "frame.file");
  if (raw.line !== undefined) result.line = number(raw.line, "frame.line");
  return result;
};

/** Read each thread/profile independently. Never add overlapping inclusive times. */
export function summarizePerformanceProfile(input: unknown) {
  const request = object(input, "request");
  const raw = object(request.profile, "profile");
  const top = number(request.top ?? 15, "top");
  if (!Number.isInteger(top) || top < 1 || top > 50) throw new Error("top must be an integer from 1 to 50");
  const summarize = (name: string, frames: Frame[], unit: string, build: (add: (stack: number[], weight: number) => void) => void) => {
    const scale = ({nanoseconds: 1e-6, microseconds: 1e-3, milliseconds: 1, seconds: 1000} as Record<string, number>)[unit];
    if (scale === undefined) throw new Error(`unsupported time unit: ${unit}`);
    if (!frames.length || frames.length > 100_000) throw new Error("profile requires 1..100000 frames");
    const observations: Observation[] = frames.map(frame => ({frame, self: 0, inclusive: 0}));
    let weightTotal = 0, observationsCount = 0, stackWeight = 0;
    build((stack, weight) => {
      if (stack.length > 1024) throw new Error("profile stack exceeds 1024 frames");
      for (const id of stack) if (!Number.isInteger(id) || id < 0 || id >= frames.length) throw new Error("unknown frame reference");
      const ms = number(weight, "sample weight") * scale;
      weightTotal += ms; observationsCount++;
      if (!stack.length) return;
      stackWeight += ms;
      observations[stack.at(-1)!]!.self += ms;
      // Recursion does not make one stack sample multiple independent samples.
      for (const id of new Set(stack)) observations[id]!.inclusive += ms;
    });
    if (!observationsCount || !weightTotal) throw new Error("profile contains no positive-duration observations");
    if (!Number.isFinite(weightTotal)) throw new Error("profile time overflows finite milliseconds");
    const rows = (kind: "self" | "inclusive") => observations.filter(row => row[kind] > 0)
      .sort((a, b) => b[kind] - a[kind]).slice(0, top).map(row => ({...row.frame,
        self_ms: row.self, inclusive_ms: row.inclusive}));
    return {name, observations: observationsCount, observed_weight_ms: weightTotal,
      stack_weight_ms: stackWeight, self_hotspots: rows("self"), inclusive_hotspots: rows("inclusive")};
  };
  let profiles: ReturnType<typeof summarize>[], format: string;
  if (raw.$schema === "https://www.speedscope.app/file-format-schema.json") {
    format = "speedscope";
    const frames = array(object(raw.shared, "shared").frames, "frames").map(frame);
    const records = array(raw.profiles, "profiles");
    if (!records.length || records.length > 256) throw new Error("profile requires 1..256 independent profiles");
    profiles = records.map((record, index) => {
      const item = object(record, "profile record");
      const start = number(item.startValue, "startValue"), end = number(item.endValue, "endValue");
      if (end < start) throw new Error("profile end precedes start");
      return summarize(typeof item.name === "string" ? item.name : `profile-${index}`, frames, text(item.unit, "unit"), add => {
        if (item.type === "sampled") {
          const samples = array(item.samples, "samples"), weights = array(item.weights, "weights");
          if (samples.length !== weights.length) throw new Error("sample/weight counts differ");
          let total = 0;
          samples.forEach((sample, i) => {const weight = number(weights[i], "weight"); total += weight; add(array(sample, "stack") as number[], weight);});
          if (total > end - start + Math.max(1e-9, (end - start) * 1e-6)) throw new Error("sample weights exceed declared duration");
        } else if (item.type === "evented") {
          const stack: number[] = []; let previous = start;
          for (const value of array(item.events, "events")) {
            const event = object(value, "event"), at = number(event.at, "event.at");
            if (at < previous || at > end) throw new Error("events must be ordered within the declared interval");
            if (at > previous) add([...stack], at - previous);
            const id = number(event.frame, "event.frame");
            if (!Number.isInteger(id) || id >= frames.length) throw new Error("unknown event frame");
            if (event.type === "O") stack.push(id);
            else if (event.type === "C" && stack.at(-1) === id) stack.pop();
            else throw new Error("unbalanced or unsupported profile event");
            previous = at;
          }
          if (stack.length) throw new Error("profile ends with an unclosed stack");
          if (end > previous) add([], end - previous);
        } else throw new Error("unsupported speedscope profile type");
      });
    });
  } else if (Array.isArray(raw.nodes) && Array.isArray(raw.samples)) {
    format = "v8-cpu";
    const nodes = raw.nodes.map(value => object(value, "node"));
    const ids = new Map<number, number>();
    nodes.forEach((node, i) => {const id = number(node.id, "node.id"); if (!Number.isInteger(id) || ids.has(id)) throw new Error("invalid or duplicate node id"); ids.set(id, i);});
    const parents = new Map<number, number>();
    nodes.forEach((node, parent) => {
      for (const childId of array(node.children ?? [], "children")) {
        const child = ids.get(childId as number);
        if (child === undefined || parents.has(child)) throw new Error("unknown or multiply-parented V8 node");
        parents.set(child, parent);
      }
    });
    const frames = nodes.map(node => {
      const call = object(node.callFrame, "callFrame");
      return frame({name: call.functionName || "(anonymous)", file: call.url || undefined,
        line: typeof call.lineNumber === "number" && call.lineNumber >= 0 ? call.lineNumber + 1 : undefined});
    });
    const samples = raw.samples, weights = array(raw.timeDeltas, "timeDeltas");
    if (samples.length !== weights.length) throw new Error("sample/timeDelta counts differ");
    const start = number(raw.startTime, "startTime"), end = number(raw.endTime, "endTime");
    if (end < start) throw new Error("profile end precedes start");
    profiles = [summarize("V8 CPU", frames, "microseconds", add => {
      let total = 0;
      samples.forEach((id, i) => {
        let current = ids.get(id as number); const stack: number[] = [], visited = new Set<number>();
        if (current === undefined) throw new Error("unknown V8 sample node");
        while (current !== undefined) {
          if (visited.has(current) || stack.length >= 1024) throw new Error("cyclic or overdeep V8 stack");
          visited.add(current); stack.push(current); current = parents.get(current);
        }
        const weight = number(weights[i], "timeDelta"); total += weight;
        add(stack.reverse(), weight);
      });
      if (total > end - start + Math.max(1, (end - start) * 1e-6)) throw new Error("V8 samples exceed declared duration");
    })];
  } else throw new Error("unsupported profile: expected Speedscope or V8 CPU JSON");
  return {schema_version: "performance_diagnosis_observation_v0", status: "observed", format,
    profiles, raw_evidence_boundary: "local_private",
    interpretation: "Weights describe recorded stack observations, not benchmark latency or proven root cause. Profiles may overlap; inclusive rows must not be summed.",
    benchmark_qualified: false, root_cause_proven: false};
}
