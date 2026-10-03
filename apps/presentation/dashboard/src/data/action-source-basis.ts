import { z } from "zod";

const digest = z.string().regex(/^[a-f0-9]{64}$/);
const todoBasis = {
  provider_revision: z.string().min(1),
  source_authority: z.enum(["file_v0", "sqlite_v0"]),
  registry_sha256: digest,
};

// Decode existing backend contracts; effect authority and stale-source checks
// remain in ChatActionService and the owning Todo/Goal services.
export const actionSourceBasisSchema = z.discriminatedUnion("schema_version", [
  z.object({ schema_version: z.literal("loopx_chat_canonical_update_basis_v0"), ...todoBasis }),
  z.object({ schema_version: z.literal("loopx_chat_canonical_terminal_basis_v0"), ...todoBasis }),
  z.object({
    schema_version: z.literal("loopx_goal_lifecycle_source_basis_v1"),
    source_identity: digest,
  }),
  z.object({
    schema_version: z.literal("loopx_goal_deletion_source_basis_v1"),
    source_identity: digest,
    source_content_sha256: digest,
    route_mode: z.enum(["source_to_global", "requested_to_global", "orphaned_global_stop_fallback"]),
  }),
]);
