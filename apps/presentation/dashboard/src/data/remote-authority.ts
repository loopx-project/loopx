import {z} from "zod";
import {requestJson} from "./chat";

// Validate the existing owner's safe projection, never interpret a wire reply
// or accept execution authority in the browser.
const inspectionSchema = z.object({
  schema_version: z.literal("loopx_remote_authority_inspection_v1"),
  ok: z.boolean(), status: z.enum(["verified", "unavailable"]),
  stage: z.string(), reason_code: z.string(), message: z.string(), recommended_action: z.string(),
  execution_admitted: z.literal(false), http_status: z.number().int().optional(),
}).strict().refine(value => value.ok === (value.status === "verified"));
export type RemoteAuthorityInspection = z.infer<typeof inspectionSchema>;

export async function inspectGoalRemoteAuthority(goalId: string): Promise<RemoteAuthorityInspection> {
  const response = z.object({goal_id: z.string(), inspection: inspectionSchema}).strict().parse(
    await requestJson<unknown>("/api/chat/goal-configuration/remote-authority/inspect", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({goal_id: goalId}),
    }),
  );
  if (response.goal_id !== goalId) throw new Error("Inspection returned a different Goal");
  return response.inspection;
}
