/** Bounded request-control wire vocabulary, shared with its frontend decoder.
 * These facts do not classify the overall Goal or grant execution authority.
 */
export const REQUEST_CONTROL_SCHEMA = "collaboration_request_control_v0";
export const REQUEST_AMENDMENT_RESULT_SCHEMA = "collaboration_request_amendment_result_v0";
export const REQUEST_AMENDMENT_OUTCOMES = ["committed", "aborted", "rejected"] as const;
export type RequestAmendmentOutcome = typeof REQUEST_AMENDMENT_OUTCOMES[number];
export const REQUEST_CONTROL_STATES = ["open", "effect_pending", "cancellation_pending", "cancelled"] as const;
export const REQUEST_OBSERVATION_STATES = ["recorded", "unavailable", "not_recorded"] as const;
