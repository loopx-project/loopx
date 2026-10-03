/** Shared wire vocabulary; dependency-free for the CLI owner and browser decoder. */
export const HANDOFF_MODES = ["legacy", "soft_claim", "hard_lease"] as const;
/** New configuration targets. legacy remains readable for upgrade and receipts. */
export const EXECUTION_HANDOFF_MODES = ["soft_claim", "hard_lease"] as const;
export type HandoffMode = typeof HANDOFF_MODES[number];
