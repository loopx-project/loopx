/**
 * View-model types the LoopX adapter fills. They are the workbench's own (`src/api.ts`,
 * `src/timeline.ts`); only the triage view is new in M1.
 */
export type {
  AgentInfo,
  GeneralSettings,
  HostInfo,
  ModelOption,
  Schedule,
  Section,
  Thread,
  Todo,
  WorkdirsConfig,
} from '../api';
import type { HostInfo } from '../api';
export type HostStatus = HostInfo['status'];
export type { StoredEvent } from '../timeline';

/** M1 workbench extension: the needs-you / running / error triage over LoopX projections. */
export type TriageBucket = 'needsYou' | 'running' | 'error' | 'quiet';

export interface TriageEntry {
  id: string;
  title: string;
  kind: 'goal' | 'session' | 'registry' | 'todo';
  bucket: TriageBucket;
  /** Why this landed in the bucket — verbatim fields from the projection, no inference. */
  evidence: string[];
  recommendedAction?: string | null;
}

export interface TriageView {
  needsYou: TriageEntry[];
  running: TriageEntry[];
  error: TriageEntry[];
  /** Everything active but waiting on an agent/external event with no user action required. */
  quiet: TriageEntry[];
  generatedAt: string;
}
