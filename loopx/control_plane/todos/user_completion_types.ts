/** Shared vocabulary for canonical completion and its effect-free presentation. */
export type DecisionOutcome = "approve" | "reject" | "cancel";
export type ResumeState = "target_not_found" | "target_or_decision_scope_not_found" | "target_not_active" |
  "target_not_blocked" | "explicit_blocker_repair_required" | "other_user_blockers_active" |
  "decision_requirements_remaining" | "resumed" | "decision_rejected" | "decision_cancelled";
