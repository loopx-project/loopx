/** Read-only generated memory. IDs are scoped to the connected machine and source. */
export type MemoryKind = 'summary' | 'durable' | 'raw' | 'evidence' | 'skill' | 'other';
export type MemoryState = 'available' | 'missing' | 'empty' | 'unreadable' | 'unsupported';
export interface MemorySource {
  id: string;
  agents: { id: string; label: string }[];
}
export interface MemoryFile {
  id: string;
  path: string;
  title: string;
  excerpt: string;
  kind: MemoryKind;
  bytes: number;
  modifiedAt: string;
}
export interface MemoryIndex {
  state: MemoryState;
  files: MemoryFile[];
  checkedAt: string;
  /** The bounded scan omitted files or encountered unreadable entries. */
  partial: boolean;
}
export interface MemoryDocument {
  id: string;
  text: string;
  modifiedAt: string;
  truncated: boolean;
}
export interface MemoryMessages {
  title: string; subtitle: string; source: string; shared: string;
  refresh: string; loading: string; search: string; all: string;
  summary: string; durable: string; raw: string; evidence: string; skill: string; other: string;
  missing: string; empty: string; unreadable: string; unsupported: string;
  noSources: string; noResults: string; choose: string; stale: string;
  checked: string; modified: string; partial: string; truncated: string;
  back: string; preview: string; original: string; readFailed: string;
  readOnly: string; scope: string; documents: (count: number) => string;
}
export interface WebHostMemory {
  sources(): Promise<MemorySource[]>;
  list(sourceId: string): Promise<MemoryIndex>;
  read(sourceId: string, fileId: string): Promise<MemoryDocument>;
  /** Refresh hint while visible, including reconnects. It does not imply new memories exist. */
  subscribe(listener: () => void): () => void;
  messages(): MemoryMessages;
}
