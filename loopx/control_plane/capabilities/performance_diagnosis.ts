/** Local profiling plans and observations, never execution or admission authority. */
import {createHash} from "node:crypto";
import {join} from "node:path";
import {PerformanceProfileInputError, summarizePerformanceProfile as summarizeProfile} from "./performance_profile.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

type JsonObject = Record<string, unknown>;
const object = (value: unknown, label: string): JsonObject => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an object`);
  return value as JsonObject;
};
const text = (value: unknown, label: string): string => {
  if (typeof value !== "string" || !value.trim() || value.includes("\0")) throw new EffectRuntimeRequestError(`${label} must be nonempty text without NUL`);
  return value;
};
const array = (value: unknown, label: string): unknown[] => {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value;
};

/** This vocabulary is capability-local: tools are recipes, not extension readiness. */
const TOOLS = ["pyinstrument", "py-spy", "memray", "node-cpu", "node-heap"] as const;
export function planPerformanceDiagnosis(input: unknown) {
  const request = object(input, "request");
  const tool = text(request.tool, "tool");
  if (!(TOOLS as readonly string[]).includes(tool)) throw new EffectRuntimeRequestError(`tool must be one of ${TOOLS.join(", ")}`);
  const command = array(request.command, "command").map((arg, index) => {
    if (typeof arg !== "string" || arg.includes("\0")) throw new EffectRuntimeRequestError(`command[${index}] must be a string without NUL`);
    return arg;
  });
  if (!command.length) throw new EffectRuntimeRequestError("command requires an executable");
  text(command[0], "command executable");
  const output = text(request.output_directory, "output_directory");
  const platform = text(request.platform, "platform");
  const executable = command[0]!;
  const target = command.slice(1);
  if ((tool === "pyinstrument" || tool === "memray") && (!target.length || (target[0]!.startsWith("-") && target[0] !== "-m"))) {
    throw new EffectRuntimeRequestError("this recipe requires a Python script or -m module target, without interpreter flags");
  }
  let argv: string[], artifact: string, observes: string, limits: string[];
  switch (tool) {
    case "pyinstrument":
      artifact = join(output, "profile.speedscope.json");
      argv = [executable, "-m", "pyinstrument", "-r", "speedscope", "-o", artifact, ...target];
      observes = "sampled Python wall time, including waits on the observed thread";
      limits = ["Other threads and subprocesses are not included; profile the owning process separately.", "Does not distinguish native CPU from blocked time."];
      break;
    case "py-spy":
      artifact = join(output, "profile.speedscope.json");
      argv = [text(request.profiler_executable ?? "py-spy", "profiler_executable"), "record", "--format", "speedscope", "--threads", "--idle", "-o", artifact, "--", ...command];
      observes = "sampled Python thread stacks, including threads considered idle";
      limits = ["Idle-inclusive weights are thread observations, not process wall time or CPU utilization.", "Native stacks and child processes require a separately scoped capture.", "Profiler progress shares stdout with the target; capture structured target output and its exit status separately through the Host.", platform === "darwin" ? "macOS requires process-inspection privileges; do not add sudo or weaken SIP automatically." : "Launching the target as a child avoids the usual Linux attach restriction; OS policy may still deny sampling."];
      break;
    case "memray":
      artifact = join(output, "allocations.bin");
      argv = [executable, "-m", "memray", "run", "--native", "-o", artifact, ...target];
      observes = "Python and native allocation stacks";
      limits = ["Allocation instrumentation changes workload cost; use Memray's reporter for this binary, not the profile summarizer.", "Platform and allocator support must be checked locally."];
      break;
    default:
      if (target.some(arg => /^--(cpu|heap)-prof(?:$|[=-])/.test(arg))) throw new EffectRuntimeRequestError("target already contains profiling flags; use an uninstrumented baseline command");
      artifact = join(output, tool === "node-cpu" ? "profile.cpuprofile" : "profile.heapprofile");
      const prefix = tool === "node-cpu" ? "cpu" : "heap";
      argv = [executable, `--${prefix}-prof`, `--${prefix}-prof-dir=${output}`, `--${prefix}-prof-name=${tool === "node-cpu" ? "profile.cpuprofile" : "profile.heapprofile"}`, ...target];
      observes = tool === "node-cpu" ? "V8 CPU stack samples in this Node process" : "sampled V8 heap allocation stacks";
      limits = ["Does not profile Python, other processes, kernel IO, or all native allocations.", "Exit normally so Node can finish the profile; use the runtime's heap viewer for heap profiles."];
  }
  return {schema_version: "performance_diagnosis_plan_v0", status: "planned", tool,
    baseline_argv: command, profile_argv: argv, artifact, observes, limits,
    execution_performed: false, tool_readiness_verified: false, shell: false,
    raw_evidence_boundary: "local_private", benchmark_qualified: false,
    next_steps: ["Verify tool/runtime version and target ownership; create a fresh ignored output directory.",
      "Measure the unchanged baseline separately; run profile_argv through the Host's authorized executor.",
      "Check target exit, profile readback and coverage; test the inferred cause with a controlled intervention.",
      "Rerun the original uninstrumented workload and semantic checks before claiming improvement."]};
}

/** The transport digest hashes normalized JSON, not original capture bytes. */
export function summarizePerformanceProfile(input: unknown) {
  let result: ReturnType<typeof summarizeProfile>;
  try {result = summarizeProfile(input);}
  catch (error) {
    if (error instanceof PerformanceProfileInputError) throw new EffectRuntimeRequestError(error.message);
    throw error;
  }
  const raw = object(input, "request").profile;
  return {...result, normalized_profile_sha256: createHash("sha256").update(JSON.stringify(raw)).digest("hex")};
}
