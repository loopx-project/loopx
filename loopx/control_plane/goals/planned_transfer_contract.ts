/** Browser-safe projection vocabulary of the existing planned-transfer owner.
 * These are inspection holds, not lifecycle states or execution permissions.
 * Keep Node/provider dependencies in the owning implementation. */
export type GoalTransferHoldCode =
  | "retained_leases"
  | "turn_or_host_unsettled"
  | "quota_unsettled"
  | "destination_admission_unavailable";
