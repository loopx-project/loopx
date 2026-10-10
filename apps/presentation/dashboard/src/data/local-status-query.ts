import {
  PeriodicReportDetailRef,
  StatusPayload,
  parseStatusPayload,
  periodicReportIndexResponseSchema,
  periodicReportProjectionResponseSchema,
} from "./status";

export const expectedStatusContractSchemaVersion = 2;
export const fallbackStatusContractReloadHint = "scripts/macos-dashboard-launchagent.sh restart";

export type ResolvedLocalStatusUrl = {
  isLoopback: boolean;
  isRelative: boolean;
  url: string;
};

export type ResolvedFrontstageStatusUrl = ResolvedLocalStatusUrl;

export type StatusContractFreshnessIssue = {
  reloadHint: string;
  schemaVersion: number;
};

export type LocalDashboardApiCapabilities = {
  controlPlaneApplyUrl: string | null;
  controlPlaneDryRunUrl: string | null;
  controlPlaneWriteEnabled: boolean;
  loopbackOnly: boolean;
  readOnlyDefault: boolean;
  rewardAppendUrl: string | null;
  rewardDryRunUrl: string | null;
  rewardWriteEnabled: boolean;
  source: string;
};

function isExplicitUrl(value: string) {
  return /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(value) || value.startsWith("//");
}

export function isLoopbackHostname(hostname: string) {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
}

function resolveLoopbackStatusUrl(value: string, baseHref: string, errorPrefix: string) {
  const trimmed = value.trim();
  if (!trimmed) {
    return { error: "status URL is empty" };
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed, baseHref);
  } catch {
    return { error: "status URL is invalid" };
  }

  const isRelative = !isExplicitUrl(trimmed);
  const isLoopback = isLoopbackHostname(parsed.hostname);
  if (!isRelative && !isLoopback) {
    return {
      error: `${errorPrefix} must be relative or loopback`,
    };
  }

  return {
    source: {
      isLoopback,
      isRelative,
      url: trimmed,
    } satisfies ResolvedLocalStatusUrl,
  };
}

export function resolveLocalStatusUrl(value: string, baseHref: string) {
  return resolveLoopbackStatusUrl(value, baseHref, "statusUrl");
}

export function resolveFrontstageOpsStatusUrl(value: string, baseHref: string) {
  const resolved = resolveLoopbackStatusUrl(value, baseHref, "Ops statusUrl");
  return resolved.error
    ? { error: `${resolved.error}; use showcase mode for public links.` }
    : resolved;
}

export async function fetchFrontstageStatusPayload(statusUrl: string) {
  const response = await fetch(statusUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while loading ${statusUrl}`);
  }
  return parseStatusPayload(await response.json());
}

export function scopedStatusUrl(
  statusUrl: string,
  scope: "active" | "stopped",
  baseHref: string,
) {
  const url = new URL(statusUrl, baseHref);
  url.searchParams.set("goal_activation", scope);
  return url.toString();
}

export function statusContractFreshnessIssue(
  payload: StatusPayload,
  source: ResolvedFrontstageStatusUrl,
): StatusContractFreshnessIssue | null {
  if (!source.isLoopback) {
    return null;
  }
  const schemaVersion = payload.status_contract.schema_version ?? 0;
  if (schemaVersion >= expectedStatusContractSchemaVersion) {
    return null;
  }
  return {
    reloadHint: payload.status_contract.reload_hint || fallbackStatusContractReloadHint,
    schemaVersion,
  };
}

function localApiUrl(source: ResolvedLocalStatusUrl, path: string | null | undefined) {
  if (!path || !source.isLoopback) {
    return null;
  }
  try {
    const sourceUrl = new URL(source.url, window.location.href);
    const targetUrl = new URL(path, sourceUrl.origin);
    return isLoopbackHostname(targetUrl.hostname) ? targetUrl.toString() : null;
  } catch {
    return null;
  }
}

export function periodicReportApiUrls(
  payload: StatusPayload,
  source: ResolvedLocalStatusUrl,
) {
  return {
    detailUrl: localApiUrl(source, payload.local_dashboard_api?.periodic_report_detail_url),
    indexUrl: localApiUrl(source, payload.local_dashboard_api?.periodic_report_index_url),
  };
}

export async function fetchPeriodicReportIndex(indexUrl: string, goalId: string) {
  const url = new URL(indexUrl);
  url.searchParams.set("goal_id", goalId);
  url.searchParams.set("limit", "100");
  url.searchParams.set("offset", "0");
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while loading published reports`);
  }
  return periodicReportIndexResponseSchema.parse(await response.json()).periodic_reports;
}

export function presentationApiUrls(payload: StatusPayload, source: ResolvedLocalStatusUrl) {
  return {
    indexUrl: localApiUrl(source, payload.local_dashboard_api?.presentation_surfaces_url),
    detailUrl: localApiUrl(source, payload.local_dashboard_api?.presentation_detail_url),
  };
}

export async function fetchPeriodicReportProjection(
  detailUrl: string,
  ref: PeriodicReportDetailRef,
) {
  const url = new URL(detailUrl);
  Object.entries(ref).forEach(([key, value]) => url.searchParams.set(key, value));
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while loading published report detail`);
  }
  return periodicReportProjectionResponseSchema.parse(await response.json()).projection;
}

export function localDashboardApiCapabilities(
  payload: StatusPayload,
  source: ResolvedFrontstageStatusUrl,
): LocalDashboardApiCapabilities {
  const localApi = payload.local_dashboard_api;
  const rewardDryRunUrl = localApiUrl(source, localApi?.reward_dry_run_url);
  const rewardAppendUrl = localApiUrl(source, localApi?.reward_append_url);
  const controlPlaneDryRunUrl = localApiUrl(source, localApi?.configure_goal_dry_run_url);
  const controlPlaneApplyUrl = localApiUrl(source, localApi?.configure_goal_apply_url);
  const rewardWriteEnabled = Boolean(localApi?.reward_write_enabled && rewardAppendUrl);
  const controlPlaneWriteEnabled = Boolean(localApi?.control_plane_write_enabled && controlPlaneApplyUrl);

  return {
    controlPlaneApplyUrl,
    controlPlaneDryRunUrl,
    controlPlaneWriteEnabled,
    loopbackOnly: source.isLoopback,
    readOnlyDefault: !rewardWriteEnabled && !controlPlaneWriteEnabled,
    rewardAppendUrl,
    rewardDryRunUrl,
    rewardWriteEnabled,
    source: localApi?.source ?? "not advertised",
  };
}
