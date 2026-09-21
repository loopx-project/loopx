export type DelegationPreflight = {
  state: "authority_unavailable" | "turn_blocked" | "acceptance_unavailable" | "runtime_unavailable" | "runtime_unverified" | "launchable";
  turn_eligible: boolean;
  acceptance_ready: boolean;
  turn_route: string | null;
  authority_ready: boolean;
  authority_reason: string | null;
  authority_state: "promotion_required" | "unavailable" | "promoted";
  authority_next_action: "preview_reviewed_goal_authority_promotion" | "repair_canonical_authority" | "none";
  promotion_from_surface_allowed: false;
  executor: {host: string; available: boolean | null; reason: string | null; profile: string | null} | null;
  effects: {host_invoked: boolean; state_written: boolean; quota_spent: boolean; scheduler_acknowledged: boolean};
};
