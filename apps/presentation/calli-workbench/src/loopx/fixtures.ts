/**
 * Offline demo fixture: the bundle the workbench's `?fixture=1` mode reads.
 *
 * `src/loopx/fixtures/status.demo.json` is a public-safe copy of LoopX
 * examples/status.example.json (see its `_provenance`) plus:
 *  - one synthetic attention item waiting on the user (needs-you group),
 *  - `_demo_sessions`: two synthetic chat sessions so the running and failed
 *    triage groups are populated offline.
 *
 * The JSON stays a status payload (zod passthrough ignores `_demo_sessions`);
 * this module is the reader that splits it into status + sessions. It takes
 * the parsed/copied JSON object, so it works in Node (tests) and in a bundler
 * (Vite imports JSON natively) without fs or network access.
 *
 * Real mode never touches this module.
 */
import { z } from 'zod';
import { chatSessionSummarySchema, chatVisibleMessageSchema, type ChatAdapter, type ChatCapabilities, type ChatGoalContext, type ChatSessionSummary, type ChatVisibleMessage } from './contracts/chat-schema.ts';

/** Everything fixture mode needs; no endpoint is contacted. */
export interface FixtureData {
  status: unknown;
  sessions: ChatSessionSummary[];
  messagesBySession?: Record<string, ChatVisibleMessage[]>;
  endpoints?: ChatAdapter[];
  capabilities?: ChatCapabilities | null;
  goalContexts?: ChatGoalContext[];
  /** ssh aliases; deliberately empty by default — nothing is probed offline. */
  sshHosts?: string[];
}

const demoSessionsSchema = z.array(chatSessionSummarySchema).default([]);
const demoMessagesSchema = z.record(z.string(), z.array(chatVisibleMessageSchema)).default({});

/** Sessions are validated on read so a broken fixture fails loudly, not silently. */
export function demoSessionsFromStatus(status: unknown): ChatSessionSummary[] {
  const raw = (status as { _demo_sessions?: unknown } | null)?._demo_sessions;
  return demoSessionsSchema.parse(raw ?? []);
}

/** Minimal adapter catalog for the offline agent picker, derived from the demo sessions. */
export function demoEndpoints(sessions: ChatSessionSummary[]): ChatAdapter[] {
  const byAgent = new Map<string, ChatAdapter>();
  for (const s of sessions) {
    if (byAgent.has(s.agent_id)) continue;
    byAgent.set(s.agent_id, {
      agent_id: s.agent_id,
      display_name: s.agent_id,
      adapter_kind: s.adapter_kind ?? 'demo',
      available: true,
      streaming: false,
      resume: s.resumable,
      interrupt: false,
      tool_calls: false,
      source: 'fixture',
    });
  }
  return [...byAgent.values()];
}

/** Synthetic conversations keyed by session id, validated like the sessions. */
export function demoMessagesFromStatus(status: unknown): Record<string, ChatVisibleMessage[]> {
  const raw = (status as { _demo_messages?: unknown } | null)?._demo_messages;
  return demoMessagesSchema.parse(raw ?? {});
}

export function createDemoFixture(status: unknown): FixtureData {
  const sessions = demoSessionsFromStatus(status);
  return { status, sessions, messagesBySession: demoMessagesFromStatus(status), endpoints: demoEndpoints(sessions), sshHosts: [] };
}
