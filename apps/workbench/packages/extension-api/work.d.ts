import type { LocalizedText } from './manifest.js';

/** A source-owned snapshot. The host adds identity, scope and freshness. */
export interface WorkSnapshot {
  title: string;
  status: string;
  statusLabel?: LocalizedText;
  summary?: string;
  items: { id: string; text: string; status: string }[];
  complete: boolean;
  updatedAt?: string;
  evidence?: string;
}
export interface WorkBinding {
  id: string;
  externalId: string;
  threadIds: string[];
  settings: Record<string, string>;
  /** Explicit content grants to installed view extensions, e.g. board. */
  consumers: string[];
  url?: string;
}
export interface WorkProjection {
  sourceId: string;
  sourceName: LocalizedText;
  binding: WorkBinding;
  snapshot: WorkSnapshot | null;
  freshness: 'fresh' | 'stale' | 'unknown' | 'unavailable';
  observedAt: string | null;
  generation: string;
  revision: number;
  error?: string;
}
export interface ExtensionConfiguration {
  settings: Record<string, string>;
  bindings: WorkBinding[];
}
export interface ExtensionDetection {
  ok: boolean;
  version?: string;
  missing?: string;
}
export interface ExtensionPackagePreview {
  token: string;
  manifest: import('./manifest.js').ExtensionManifest;
  origin: string;
  digest: string;
}
