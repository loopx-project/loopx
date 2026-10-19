import { resolveFrontstageOpsStatusUrl } from "./local-status-query";
import { isConfiguredSshHostAlias } from "./ssh-host-catalog";

export const defaultLocalStatusSourceUrl = "/status.json";
export const statusSourceCatalogStorageKey = "loopx-status-source-catalog-v1";

export type StatusSource = {
  hostAlias?: string;
  id: string;
  kind: "local" | "ssh_tunnel" | "runtime";
  label: string;
  readOnly: boolean;
  statusUrl: string;
};

export type StatusSourceCatalog = {
  schemaVersion: 1;
  sources: StatusSource[];
};

export type StatusSourceStorage = Pick<Storage, "getItem" | "setItem">;

function matchesRuntimeUrl(candidate: string, requested: string, baseHref: string): boolean {
  const expected = new URL(candidate, baseHref), actual = new URL(requested, baseHref);
  if (expected.toString() === actual.toString()) return true;
  // An unbound entry may adopt this origin's first identity. A previously
  // pinned identity must never be replaced, even on the same port.
  if (actual.searchParams.has("source_namespace")) return false;
  expected.searchParams.delete("source_namespace");
  return expected.toString() === actual.toString();
}

/** Runtime choices describe physical readers, not new execution Goals. */
export function runtimeStatusSourceCatalog(
  entries: {id: string; label: string; status_url: string}[] | undefined,
  currentUrl: string,
  baseHref: string,
): StatusSourceCatalog | null {
  if (!entries?.length || entries.length > 12) return null;
  const sources: StatusSource[] = [];
  for (const entry of entries) {
    const resolved = normalizedTunnelUrl(entry.status_url, baseHref);
    if (!("url" in resolved) || sources.some((s) => s.id === entry.id || s.statusUrl === resolved.url)) return null;
    sources.push({id: entry.id, label: entry.label, kind: "runtime", readOnly: true, statusUrl: resolved.url});
  }
  const current = new URL(currentUrl, baseHref).toString();
  // A source cannot replace the selected origin with an unrelated catalog.
  return sources.some((s) => matchesRuntimeUrl(s.statusUrl, current, baseHref)) ? {schemaVersion: 1, sources} : null;
}

export const localStatusSource: StatusSource = {
  id: "local",
  kind: "local",
  label: "本机",
  readOnly: false,
  statusUrl: defaultLocalStatusSourceUrl,
};

type AddStatusSourceResult =
  | { catalog: StatusSourceCatalog; source: StatusSource }
  | { error: string };

type NormalizedTunnelUrl = { url: string } | { error: string };

function normalizedTunnelUrl(value: string, baseHref: string): NormalizedTunnelUrl {
  const resolved = resolveFrontstageOpsStatusUrl(value, baseHref);
  const source = resolved.source;
  if (!source || !source.isLoopback || source.isRelative) {
    return { error: "SSH 隧道来源必须使用显式的 localhost、127.0.0.1 或 ::1 URL。" };
  }
  const parsed = new URL(source.url, baseHref);
  if (!(["http:", "https:"] as string[]).includes(parsed.protocol)) {
    return { error: "状态来源只支持 HTTP 或 HTTPS。" };
  }
  return { url: parsed.toString() };
}

function sourceId(statusUrl: string) {
  let hash = 2166136261;
  for (const character of statusUrl) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return `ssh-${(hash >>> 0).toString(36)}`;
}

function parseStoredSource(value: unknown, baseHref: string): StatusSource | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (candidate.kind !== "ssh_tunnel" || typeof candidate.label !== "string" || typeof candidate.statusUrl !== "string") {
    return null;
  }
  const label = candidate.label.trim();
  const resolved = normalizedTunnelUrl(candidate.statusUrl, baseHref);
  if (!label || label.length > 48 || !("url" in resolved)) return null;
  const hostAlias = isConfiguredSshHostAlias(candidate.hostAlias)
    ? candidate.hostAlias.trim()
    : undefined;
  return {
    ...(hostAlias ? { hostAlias } : {}),
    id: sourceId(resolved.url),
    kind: "ssh_tunnel",
    label,
    readOnly: true,
    statusUrl: resolved.url,
  };
}

export function emptyStatusSourceCatalog(): StatusSourceCatalog {
  return { schemaVersion: 1, sources: [localStatusSource] };
}

export function loadStatusSourceCatalog(storage: StatusSourceStorage, baseHref: string): StatusSourceCatalog {
  try {
    const raw = storage.getItem(statusSourceCatalogStorageKey);
    if (!raw) return emptyStatusSourceCatalog();
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (parsed.schemaVersion !== 1 || !Array.isArray(parsed.sources)) return emptyStatusSourceCatalog();
    const seenUrls = new Set([localStatusSource.statusUrl]);
    const sources = parsed.sources.flatMap((value) => {
      const source = parseStoredSource(value, baseHref);
      if (!source || seenUrls.has(source.statusUrl)) return [];
      seenUrls.add(source.statusUrl);
      return [source];
    });
    return { schemaVersion: 1, sources: [localStatusSource, ...sources] };
  } catch {
    return emptyStatusSourceCatalog();
  }
}

export function saveStatusSourceCatalog(storage: StatusSourceStorage, catalog: StatusSourceCatalog) {
  storage.setItem(statusSourceCatalogStorageKey, JSON.stringify({
    schemaVersion: 1,
    sources: catalog.sources.filter((source) => source.kind === "ssh_tunnel"),
  }));
}

export function bindConfiguredSshHostAliases(
  catalog: StatusSourceCatalog,
  hostAliases: string[],
): StatusSourceCatalog {
  const configuredAliases = new Set(
    hostAliases.filter(isConfiguredSshHostAlias).map((alias) => alias.trim()),
  );
  let changed = false;
  const sources = catalog.sources.map((source) => {
    if (source.kind !== "ssh_tunnel" || source.hostAlias || !configuredAliases.has(source.label)) {
      return source;
    }
    changed = true;
    return { ...source, hostAlias: source.label };
  });
  return changed ? { ...catalog, sources } : catalog;
}

export function addSshTunnelStatusSource(
  catalog: StatusSourceCatalog,
  input: { hostAlias?: string; label: string; statusUrl: string },
  baseHref: string,
): AddStatusSourceResult {
  const label = input.label.trim();
  if (!label) return { error: "请填写来源名称。" };
  if (label.length > 48) return { error: "来源名称不能超过 48 个字符。" };
  const resolved = normalizedTunnelUrl(input.statusUrl, baseHref);
  if (!("url" in resolved)) return resolved;
  if (catalog.sources.some((source) => source.statusUrl === resolved.url)) {
    return { error: "这个状态 URL 已经在来源目录中。" };
  }
  if (input.hostAlias !== undefined && !isConfiguredSshHostAlias(input.hostAlias)) {
    return { error: "请选择有效的 SSH Host。" };
  }
  const hostAlias = input.hostAlias?.trim();
  const source: StatusSource = {
    ...(hostAlias ? { hostAlias } : {}),
    id: sourceId(resolved.url),
    kind: "ssh_tunnel",
    label,
    readOnly: true,
    statusUrl: resolved.url,
  };
  return {
    catalog: { ...catalog, sources: [...catalog.sources, source] },
    source,
  };
}

export function removeStatusSource(catalog: StatusSourceCatalog, sourceIdToRemove: string): StatusSourceCatalog {
  return {
    ...catalog,
    sources: catalog.sources.filter((source) => source.kind === "local" || source.id !== sourceIdToRemove),
  };
}

export function statusSourceForUrl(catalog: StatusSourceCatalog, statusUrl: string, baseHref: string) {
  if (!statusUrl.trim()) return localStatusSource;
  const resolved = resolveFrontstageOpsStatusUrl(statusUrl, baseHref);
  let normalized = statusUrl.trim();
  try {
    normalized = new URL(normalized, baseHref).toString();
  } catch {
    return null;
  }
  const runtime = catalog.sources.find((source) => source.kind === "runtime"
    && matchesRuntimeUrl(source.statusUrl, normalized, baseHref));
  if (runtime) return runtime;
  if (resolved.source?.isRelative) return localStatusSource;
  return catalog.sources.find((source) => source.statusUrl === normalized) ?? null;
}

export function projectedStatusSourceForUrl(catalog: StatusSourceCatalog, statusUrl: string, baseHref: string): StatusSource {
  const registered = statusSourceForUrl(catalog, statusUrl, baseHref);
  if (registered) return registered;
  const resolved = resolveFrontstageOpsStatusUrl(statusUrl, baseHref);
  if (resolved.source?.isRelative) return localStatusSource;
  return {
    id: "temporary",
    kind: "ssh_tunnel",
    label: "临时来源",
    readOnly: true,
    statusUrl: statusUrl.trim(),
  };
}

export function activeStatusSourceForUrl(
  catalog: StatusSourceCatalog,
  requestedStatusUrl: string | null,
  loadedStatusUrl: string,
  baseHref: string,
): StatusSource {
  // The active source follows the user's in-flight selection so a slow or
  // failed switch does not make the switcher appear to bounce back to the
  // previously loaded source.
  return projectedStatusSourceForUrl(catalog, requestedStatusUrl ?? loadedStatusUrl, baseHref);
}
