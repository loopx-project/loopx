import { AcpAdapter, type AcpSession } from './acp.js';
import { DEEPSEEK_ICON } from './icons.js';
import type { ModelOption, PermissionMode } from './types.js';

/**
 * DeepSeek Harness (`dsh`), through the ACP server its own CLI ships: `dsh --profile acp` speaks
 * standard ACP v1 over stdio and persists every session on the agent side. The protocol handling
 * lives in acp.ts; what is dsh-specific:
 *
 * - bringing a conversation back goes through `session/resume`, not `session/load`: the agent
 *   keeps its log to itself and never replays it, so the hub's own event log is the thread's
 *   history;
 * - there are no session modes at all: no permission levels and no plan mode. Risky actions still
 *   arrive as `session/request_permission`, with one-shot allow/reject options only;
 * - the model list is grouped by provider, and every option's value is an opaque route id
 *   (`["provider","model"]`) the agent validates itself, so it is stored and sent back as-is;
 * - reasoning effort is a `reasoning_effort` option that only models with a reasoning setting
 *   declare; models without one have no effort picker.
 */

/** The agent has no session modes; the hub shows no permission picker and never calls set_mode. */
const DSH_MODE_IDS: Record<PermissionMode, readonly string[]> = {
  ask: [],
  auto_edit: [],
  full_access: [],
};

/** Fallback while the model list is unread; a probed model carries its own, narrower list. */
const DSH_EFFORTS = ['off', 'low', 'medium', 'high', 'max'] as const;

export const DSH_METADATA = {
  adapter: 'dsh' as const,
  label: 'DeepSeek Harness',
  icon: DEEPSEEK_ICON,
  installGuide: 'npm install -g @deepseek-ai/dsh',
};

export class DshAcpAdapter extends AcpAdapter {
  readonly id = 'dsh';
  readonly label = DSH_METADATA.label;
  readonly icon = DSH_METADATA.icon;
  readonly installGuide = DSH_METADATA.installGuide;
  readonly capabilities = {
    resume: true,
    approvals: true,
    coordination: true,
    permissionModes: [] as const,
    effortLevels: DSH_EFFORTS,
    images: true,
    deleteSession: false,
    history: false,
    quota: false,
    fork: false,
    forkAt: false,
    forkInto: false,
    multiAccount: false,
    modelSwitch: true,
  };

  protected readonly modeIds = DSH_MODE_IDS;
  protected override readonly resumeVia = 'session/resume' as const;

  /**
   * Asks each model for its reasoning efforts. A model that cannot be selected right now (its
   * provider's catalog is briefly unreachable) stays listed without effort details: hiding a
   * model the user expects would be worse than showing an incomplete note.
   */
  protected override async refineModels(models: ModelOption[], session: AcpSession): Promise<ModelOption[]> {
    const out: ModelOption[] = [];
    for (const model of models) {
      const probed = await session.probeModel(model.id);
      out.push(probed ? { ...model, ...probed } : model);
    }
    return out;
  }

  resumeCommand(agentSessionId: string): string {
    // The shipped profiles have no per-session terminal resume; the Web UI lists persisted
    // sessions and this session keeps the thread's directory as its workspace.
    void agentSessionId;
    return 'dsh web';
  }

  /** The API key's balance is out; the wording comes from the DeepSeek API error chain. */
  planExhausted(message: string): boolean {
    return /insufficient balance/i.test(message);
  }
}
