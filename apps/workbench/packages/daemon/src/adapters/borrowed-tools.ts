import type { EmittedEvent } from '../events.js';
import type { PermissionMode } from './types.js';

/** Provider-owned descriptions and execution rules. The hub never branches on an agent brand. */
export interface BorrowedTool {
  name: string;
  description: string;
  keywords: string[];
  permissionMode: PermissionMode;
  resultKind: 'image' | 'text';
  /** Known integration limits are visible before a caller spends a native turn. */
  unavailableReason?: string;
  inputSchema: Record<string, unknown>;
  prepare(input: unknown): { input: Record<string, unknown>; text: string };
  /** Collect results only when native execution evidence supports this capability. */
  collect(events: EmittedEvent[], answer: string, input: Record<string, unknown>): {
    images?: string[];
    data?: Record<string, unknown>;
  };
}
