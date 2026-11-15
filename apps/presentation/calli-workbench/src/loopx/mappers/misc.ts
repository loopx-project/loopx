/**
 * LoopX chat adapters/endpoints → Calli AgentInfo[]; ssh-host catalog → HostInfo[];
 * capabilities + status → health/workdirs/general-settings/models; and the
 * Goal-derived sidebar grouping for sections()/threads().
 */
import { placeholder } from '../placeholders.ts';
import type { ChatAdapter, ChatCapabilities, ChatGoalContext, SshHostCatalog } from '../contracts/chat-schema.ts';
import type { RunGoal, StatusPayload } from '../contracts/status-schema.ts';
import type { AgentInfo, GeneralSettings, HostInfo, HostStatus, ModelOption, Section, WorkdirsConfig } from '../types.ts';

/** Fixed id of the sidebar group holding threads whose session has no goal_id. */
export const UNASSIGNED_SECTION_ID = 'loopx:unassigned';

export function mapAdaptersToAgents(adapters: ChatAdapter[]): AgentInfo[] {
  return adapters.map((a) => ({
    id: a.agent_id,
    label: a.display_name || a.agent_id,
    adapter: a.adapter_kind || undefined,
    available: a.available,
    capabilities: {
      resume: a.resume,
      approvals: placeholder<boolean>('AgentInfo.capabilities.approvals'),
      permissionModes: placeholder<string[]>('AgentInfo.capabilities.permissionModes / defaultPermissionMode'),
      defaultPermissionMode: placeholder<string>('Thread.permissionMode'),
      effortLevels: placeholder<never[]>('AgentInfo.capabilities.effortLevels / images / history / quota / deleteSession / fork'),
      images: false,
      deleteSession: false,
      history: false,
      quota: false,
      fork: false,
    },
  }));
}

export function mapSshHostsToHosts(catalog: SshHostCatalog): HostInfo[] {
  return catalog.hosts.map((h) => ({
    id: h.alias,
    label: h.alias,
    target: h.alias,
    status: placeholder<HostStatus>('HostInfo.status'),
  }));
}

function goalGroupName(goal: RunGoal): string {
  return goal.display_name ?? goal.goal_objective ?? goal.id;
}

/**
 * Sidebar groups = one per Goal (run_history.goals, in projection order), plus
 * the trailing unassigned group. goal_ids that only sessions reference (e.g.
 * the manager session's "loopx-manager") get NO group of their own: raw ids
 * would leak into the UI and are unstable — their sessions land in the
 * unassigned group. The unassigned title is localized by the UI.
 */
export function mapSections(status: StatusPayload): Section[] {
  const goals = status.run_history?.goals ?? [];
  const sections: Section[] = goals.map((goal, i) => ({ id: goal.id, name: goalGroupName(goal), position: i + 1 }));
  sections.push({ id: UNASSIGNED_SECTION_ID, name: placeholder<string>('Section.name (unassigned group)'), position: sections.length + 1 });
  return sections;
}

export function mapHealth(status: StatusPayload, capabilities: ChatCapabilities | null): { ok: boolean; version: string } {
  const version =
    capabilities?.runtime_identity?.package_version ??
    status.local_dashboard_api?.runtime_identity?.package_version ??
    'unknown';
  return { ok: status.ok, version };
}

export function mapWorkdirs(status: StatusPayload, contexts: ChatGoalContext[]): WorkdirsConfig {
  void status; // the raw runtime root is deliberately unused: no local absolute paths in the view model
  const repos = contexts
    .map((g) => g.repository?.label)
    .filter((label): label is string => Boolean(label));
  const workdirs = Array.from(new Set(repos));
  return {
    workdirs,
    roots: workdirs.map((path) => ({ path, removable: false })),
    scratch: false,
  };
}

export function mapGeneralSettings(): GeneralSettings {
  return {
    startupPage: placeholder<GeneralSettings['startupPage']>('GeneralSettings.startupPage'),
    autoArchiveDays: placeholder<GeneralSettings['autoArchiveDays']>('GeneralSettings.autoArchiveDays'),
  };
}

export function mapModels(agentId: string, sessions: { agent_id: string; agent_model_id?: string | null; agent_reasoning_effort?: string | null }[], capabilities: ChatCapabilities | null): ModelOption[] {
  const fromSession = sessions.find((s) => s.agent_id === agentId && s.agent_model_id);
  const id = fromSession?.agent_model_id ?? (capabilities?.manager && agentId === 'codex' ? capabilities.manager.model : null);
  if (!id) return [];
  return [
    {
      id,
      label: id,
      description: 'LoopX chat runtime effective model (read-only)',
      isDefault: true,
    },
  ];
}
