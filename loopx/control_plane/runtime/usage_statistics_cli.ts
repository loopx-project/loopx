import { configure, inspect, observe } from "./usage_statistics.ts";
import { durationBucket, FEATURES, object } from "./usage_statistics_contract.ts";
import type { Context } from "./usage_statistics.ts";
import type { Counter } from "./usage_statistics_contract.ts";

import type { GoalObservation } from "./usage_statistics_goal_contract.ts";
import { validGoalObservation, hostCategory } from "./usage_statistics_goal_contract.ts";

import { validCycle } from "./usage_statistics_cycles.ts";

try {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
    if (input.length > 4096) throw new Error("usage_request_too_large");
  }
  const request: unknown = JSON.parse(input);
  if (!object(request) || typeof request.path !== "string" || !object(request.facts)) throw new Error("usage_request_invalid");
  const ctx: Context = { env: process.env, version: String(request.facts.version), python: String(request.facts.python), channel: String(request.facts.channel) };
  let result: unknown;
  if (request.action === "status") result = await inspect(request.path, ctx);
  else if (request.action === "enable" || request.action === "disable" || request.action === "acknowledge") {
    result = await configure(request.path, ctx, request.action, request.notice);
  } else if (request.action === "start") {
    result = await observe(request.path, ctx, String(request.generation), null);
  } else if (request.action === "cycle" && validCycle(request.observation, Date.now())) {
    result = await observe(request.path, ctx, String(request.generation), null, undefined, undefined, request.observation);
  } else if (request.action === "goal" && object(request.observation) && validGoalObservation({ ...request.observation, host: hostCategory(request.observation.host) }, Date.now())) {
    result = await observe(request.path, ctx, String(request.generation), null, undefined, { ...request.observation, host: hostCategory(request.observation.host) } as GoalObservation);
  } else if (request.action === "observe" && typeof request.feature === "string"
    && typeof request.elapsed_ms === "number" && Number.isFinite(request.elapsed_ms) && request.elapsed_ms >= 0) {
    result = await observe(request.path, ctx, String(request.generation), {
      feature: (FEATURES as readonly unknown[]).includes(request.feature) ? request.feature : "other", outcome: request.outcome, error: request.error,
      duration: durationBucket(request.elapsed_ms), count: 1,
    } as Counter);
  } else throw new Error("usage_request_invalid");
  process.stdout.write(JSON.stringify(result) + "\n");
} catch {
  // No environment, source paths or exception text in the diagnostic contract.
  process.stdout.write(JSON.stringify({ error: "usage_statistics_unavailable" }) + "\n");
  process.exitCode = 1;
}
