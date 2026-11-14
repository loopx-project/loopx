/**
 * CENTRAL REGISTRY of every Calli field the LoopX projections cannot fill.
 *
 * Rule: mappers may NOT hardcode placeholder values inline. They call
 * `placeholder('Field.name')` (typed lookup) so this file stays the single
 * source of truth for what is missing, why, and how the M1 UI presents it.
 *
 * `kind`:
 *  - placeholder — a stand-in value the UI renders as-is (e.g. "read-only")
 *  - hidden      — the control/affordance is not shown at all in M1
 *  - derived     — value is computed from another LoopX field (reason names it)
 */

export type PlaceholderKind = 'placeholder' | 'hidden' | 'derived';

export interface PlaceholderRecord {
  /** Calli type.field the record stands for. */
  field: string;
  kind: PlaceholderKind;
  /** The stand-in/derived value mappers must use. */
  value: unknown;
  /** Why the LoopX projection does not have it. */
  reason: string;
  /** How the M1 workbench presents it. */
  ui: string;
  /** Where a real value could come from later (M2/M3 plan), if any. */
  futureSource?: string;
}

export const PLACEHOLDERS: readonly PlaceholderRecord[] = [
  // --- Thread ---
  {
    field: 'Thread.permissionMode',
    kind: 'placeholder',
    value: 'read-only',
    reason: 'LoopX chat sessions run under the manager runtime profile; there is no per-thread permission mode.',
    ui: 'Static "read-only" chip on the thread header; no mode switcher.',
    futureSource: 'M2 triage drawer if LoopX exposes per-goal write scopes.',
  },
  {
    field: 'Thread.preview',
    kind: 'hidden',
    value: null,
    reason: 'LoopX session list has no last-user-message preview field.',
    ui: 'Second line under the title is omitted.',
    futureSource: 'Could derive from latest user message once sessions hold messages.',
  },
  {
    field: 'Thread.queued',
    kind: 'hidden',
    value: [],
    reason: 'M1 adapter is read-only; no messages can be queued.',
    ui: 'Queue badge/count not rendered.',
  },
  {
    field: 'Thread.waiting',
    kind: 'hidden',
    value: false,
    reason: 'No approval/question projection in LoopX chat store (approval_policy=never in demo runtime).',
    ui: 'Waiting indicator not rendered.',
    futureSource: 'M2: /api/chat/* approval surfaces if any appear.',
  },
  {
    field: 'Thread.unread / Thread.pinned / Thread.terminalActivity',
    kind: 'hidden',
    value: false,
    reason: 'Calli-hub client state; LoopX keeps no per-session read/pin flags.',
    ui: 'Badges not rendered.',
  },
  {
    field: 'Thread.resumeCommand',
    kind: 'hidden',
    value: null,
    reason: 'LoopX sessions resume through the chat service, not a shell command string.',
    ui: 'Resume-command row omitted.',
  },
  {
    field: 'Thread.speed / Thread.workMode',
    kind: 'hidden',
    value: null,
    reason: 'No speed tiers or plan/execute work modes in the LoopX chat contract.',
    ui: 'Controls omitted from the startup bar.',
  },
  {
    field: 'Thread.agentSessionId / Thread.archivedAt / Thread.forkedFrom',
    kind: 'hidden',
    value: null,
    reason: 'LoopX sessions are not archived/forked through this projection.',
    ui: 'Controls omitted.',
  },
  {
    field: 'Thread.sectionId',
    kind: 'derived',
    value: '<session goal_id when the goal is in run_history> | loopx:unassigned',
    reason: 'Per the main-agent ruling the sidebar groups by Goal: sectionId carries the session goal_id when that goal appears in run_history; sessions with no goal_id OR with a goal only they reference (e.g. loopx-manager) use the fixed unassigned id, matching the goal-derived Section[] groups.',
    ui: 'Threads land under their goal group; the rest under the localized unassigned group.',
  },
  {
    field: 'Thread.cwd',
    kind: 'derived',
    value: '<goal repository label> | ""',
    reason: 'PRIVACY (main-agent review): the raw status payload carries a local root path that must never reach the UI. The cwd shows the goal repository label from /api/chat/goals/contexts (e.g. "loopx-project/loopx"); sessions whose goal has no repository show an empty path.',
    ui: 'Path chip shows the repository label, or nothing when unknown.',
    futureSource: 'session project_ref once LoopX populates it.',
  },

  // --- AgentInfo ---
  {
    field: 'AgentInfo.capabilities.approvals',
    kind: 'placeholder',
    value: false,
    reason: 'LoopX adapters expose no approval capability flag.',
    ui: 'No approval controls.',
  },
  {
    field: 'AgentInfo.capabilities.permissionModes / defaultPermissionMode',
    kind: 'placeholder',
    value: ['read-only'],
    reason: 'M1 is read-only by design; the adapter has zero write endpoints.',
    ui: 'Permission picker hidden; startup bar shows fixed "read-only".',
  },
  {
    field: 'AgentInfo.capabilities.effortLevels / images / history / quota / deleteSession / fork',
    kind: 'hidden',
    value: [],
    reason: 'LoopX /api/chat/endpoints exposes only streaming/resume/interrupt/tool_calls/trust_scope per adapter.',
    ui: 'Respective controls/panels omitted.',
  },
  {
    field: 'AgentInfo.login',
    kind: 'hidden',
    value: undefined,
    reason: 'No login handshake in the LoopX chat surface.',
    ui: 'Sign-in affordance omitted.',
  },

  // --- HostInfo ---
  {
    field: 'HostInfo.status',
    kind: 'placeholder',
    value: 'unknown',
    reason: 'The LoopX ssh-host catalog (/ssh-hosts) lists aliases only; no reachability probe exists, so the adapter must not claim "offline".',
    ui: 'Machine chip shows "not probed" (status=unknown) with a note that connections arrive later — never an offline dot.',
    futureSource: 'M3 ssh host catalog probe (main-agent ruling: unknown, not offline).',
  },
  {
    field: 'HostInfo.code / HostInfo.error / HostInfo.version',
    kind: 'hidden',
    value: undefined,
    reason: 'No probe result to fill them from.',
    ui: 'Omitted.',
  },

  // --- startup params (besides language and theme) ---
  {
    field: 'GeneralSettings.startupPage',
    kind: 'placeholder',
    value: 'recent-thread',
    reason: 'LoopX keeps no hub-level startup preference.',
    ui: 'Setting row hidden; the workbench always opens the newest session.',
  },
  {
    field: 'GeneralSettings.autoArchiveDays',
    kind: 'placeholder',
    value: null,
    reason: 'No archive concept in the LoopX chat store projection.',
    ui: 'Setting row hidden; sessions stay listed.',
  },
  {
    field: 'Section[] (goal groups)',
    kind: 'derived',
    value: 'one per run_history goal + unassigned',
    reason: 'Sidebar groups are Calli user state in the hub; here they are DERIVED per the main-agent ruling: one group per Goal (name: display_name → goal_objective → id), plus a fixed unassigned group (id loopx:unassigned). goal_ids that only sessions reference get NO group of their own (raw ids would leak into the UI); their sessions land in unassigned.',
    ui: 'Sidebar groups goals; the unassigned group title is localized by the UI (data layer ships a neutral default).',
  },
  {
    field: 'Section.name (unassigned group)',
    kind: 'placeholder',
    value: 'Unassigned',
    reason: 'Threads whose session has no goal_id must not be dropped; the group needs a title the data layer cannot localize.',
    ui: 'UI renders its localized "unassigned" string keyed by section id loopx:unassigned.',
  },
  {
    field: 'Schedule[]',
    kind: 'placeholder',
    value: [],
    reason: 'LoopX scheduling lives in its own control-plane CLI surface, not the chat/status projections.',
    ui: 'Schedule list shows its designed empty state.',
    futureSource: 'M3: LoopX scheduler projection if published.',
  },
  {
    field: 'discoverAgents()/discoverAccounts()',
    kind: 'placeholder',
    value: [],
    reason: 'No LoopX equivalent of PATH/account discovery.',
    ui: 'Onboarding "add agent" hints omitted.',
  },

  // --- Todo ---
  {
    field: 'Todo.lane / Todo.urgency / Todo.source / Todo.dueAt / Todo.waitingOn / Todo.followUpAt / Todo.subscriptions',
    kind: 'hidden',
    value: null,
    reason: 'Lanes/urgency/sources are Calli plan-page concepts; LoopX todos carry status/priority/role only.',
    ui: 'Lane column and urgency badges not rendered; a flat ordered list is shown.',
  },
  {
    field: 'Todo.threads',
    kind: 'placeholder',
    value: [],
    reason: 'LoopX projection does not link todos to chat sessions.',
    ui: 'Thread chips omitted.',
    futureSource: 'goal_channel_projection could gain session refs.',
  },
  {
    field: 'Todo.state blocked',
    kind: 'derived',
    value: 'status="blocked" → state="blocked"',
    reason: 'LoopX todos project status="blocked" verbatim; Calli state vocabulary has no blocked value, so the union is extended rather than silently flattening blocked → open.',
    ui: 'Blocked todos render in the triage Failed pane alongside error sessions (c-projections mapping).',
  },
  {
    field: 'Todo.importance',
    kind: 'derived',
    value: 'P0→high, P1/P2→normal, else low',
    reason: 'LoopX priorities are P0..P2 strings; Calli importance is high/normal/low. Mapping is declared here, not inferred per site.',
    ui: 'Small priority glyph beside the text.',
  },

  // --- events/messages ---
  {
    field: 'StoredEvent tool.* / reasoning.* / plan.* / approval.* / input.*',
    kind: 'hidden',
    value: null,
    reason: 'LoopX chat store messages are flat role/text rows; no tool-call or reasoning stream is projected.',
    ui: 'Conversation view renders user asks and assistant answers only.',
    futureSource: 'active_turn progress events if the chat store publishes them.',
  },
  {
    field: 'ModelOption.efforts / loadPercent / images',
    kind: 'hidden',
    value: undefined,
    reason: 'The chat capabilities surface names one effective model/effort for the manager, none per adapter.',
    ui: 'Model picker shows a single fixed entry.',
  },
] as const;

const INDEX = new Map(PLACEHOLDERS.map((p) => [p.field, p]));

/**
 * Typed lookup of a registered placeholder value. Throws on unknown fields so
 * a new placeholder cannot sneak in without being registered here.
 */
export function placeholder<T>(field: string): T {
  const record = INDEX.get(field);
  if (!record) throw new Error(`Unregistered placeholder requested: ${field} — add it to placeholders.ts`);
  return record.value as T;
}

/** Fields whose record is a group (e.g. "Thread.unread / Thread.pinned"). */
export function placeholderRecord(field: string): PlaceholderRecord | undefined {
  return INDEX.get(field);
}

export const placeholderCount = PLACEHOLDERS.length;
