import {requestJson} from "./chat";
import {z} from "zod";

const row = z.object({todo_id: z.string(), bundle_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  title: z.string(), status: z.string(), created_by: z.string().nullable(), bound_agent: z.string().nullable(),
  created_at: z.string().nullable(), updated_at: z.string().nullable(), completed_at: z.string().nullable(),
  superseded_by: z.string().nullable()});
export type ReportReviewRow = z.infer<typeof row>;
const page = z.object({ok: z.literal(true), available: z.boolean().default(true),
  reason_code: z.string().optional(), items: z.array(row), total: z.number().int().nonnegative(), next_cursor: z.string().nullable()});
const read = z.object({ok: z.literal(true), goal_id: z.string(), review: row,
  source_session_id: z.string().regex(/^[a-f0-9]{32}$/).nullable(),
  todo: z.record(z.string(), z.unknown()), document: z.object({title: z.string(), generated_at: z.string()}).passthrough(),
  generation_id: z.string(), artifacts: z.array(z.object({renderer_id: z.string(), content: z.string(), content_digest: z.string()}))});
export type ReportReviewRead = z.infer<typeof read>;
export async function fetchReportReviews(goalId: string, cursor?: string) {
  const query = new URLSearchParams({goal_id: goalId}); if (cursor) query.set("cursor", cursor);
  return page.parse(await requestJson<unknown>(`/api/chat/report-reviews?${query}`));
}
export async function readReportReview(goalId: string, todoId: string) {
  return read.parse(await requestJson<unknown>(`/api/chat/report-reviews/${encodeURIComponent(todoId)}?${new URLSearchParams({goal_id: goalId})}`));
}
