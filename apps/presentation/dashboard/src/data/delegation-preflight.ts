export type DelegationPreflight = {
  state: "authority_unavailable" | "turn_blocked" | "acceptance_unavailable" | "runtime_unavailable" | "runtime_unverified" | "launchable";
  turn_eligible: boolean;
  acceptance_ready: boolean;
  turn_route: string | null;
  authority_ready: boolean;
  authority_reason: string | null;
  executor: {host: string; available: boolean | null; reason: string | null; profile: string | null} | null;
  effects: {host_invoked: boolean; state_written: boolean; quota_spent: boolean; scheduler_acknowledged: boolean};
};
