/**
 * The made-up workspace the page shows before it is connected to a hub: a few threads in two
 * projects, a plan and a scheduled task. Events have the daemon's own shapes, so the real
 * timeline draws them. Times are relative to when the page opened.
 *
 * Nothing here is anyone's real project, path or machine.
 */
import { CLAUDE_ICON, CODEX_ICON, PI_ICON } from '../../../daemon/src/adapters/icons';
import type { ActivityDay, AgentInfo, Coordination, CoordinationMessage, FolderListing, ModelOption, Schedule, Section, SystemPrompt, TextFile, Thread, ThreadOutputs, ThreadStats, Todo, TurnTrace, WorkdirsConfig } from '../api';
import type { StoredEvent } from '../timeline';

export interface DemoData {
  agents: AgentInfo[];
  config: WorkdirsConfig;
  threads: Thread[];
  sections: Section[];
  todos: Todo[];
  schedules: Schedule[];
  models: Record<string, ModelOption[]>;
  events: Record<string, StoredEvent[]>;
  /** The teams threads belong to, as the coordination endpoint would answer them. */
  coordinations: Record<string, Coordination>;
  /** Folder listings by thread directory and path inside it ('' for the top). */
  folders: Record<string, FolderListing['entries']>;
  files: Record<string, string>;
  /** What each thread produced, as the outputs endpoint would answer it. */
  outputs: Record<string, ThreadOutputs>;
  /** The daily digest, as the activity endpoint would answer it. */
  activity: ActivityDay[];
  /** The whole prompt of a conversation, by thread, for the demo's system-prompt dialog. */
  systemPrompts: Record<string, SystemPrompt>;
  /** Turn traces by thread, as the trace endpoint would answer for its last turn. */
  traces: Record<string, TurnTrace>;
  /** Session statistics by thread, as the stats endpoint would answer. */
  stats: Record<string, ThreadStats>;
}

const STOREFRONT = '~/projects/storefront';
const NOTES_API = '~/projects/notes-api';

const caps = (extra: Partial<AgentInfo['capabilities']>): AgentInfo['capabilities'] => ({
  resume: true,
  approvals: true,
  permissionModes: ['ask', 'auto_edit', 'full_access'],
  effortLevels: [],
  images: true,
  deleteSession: true,
  history: false,
  quota: false,
  fork: true,
  ...extra,
});

const AGENTS: AgentInfo[] = [
  {
    id: 'codex',
    label: 'Codex',
    icon: CODEX_ICON,
    adapter: 'codex',
    available: true,
    capabilities: caps({ workModes: ['execute', 'plan'], speed: true, effortLevels: ['low', 'medium', 'high', 'xhigh'], compact: true, forkAt: true, systemPrompt: true }),
  },
  {
    id: 'claude',
    label: 'Claude',
    icon: CLAUDE_ICON,
    adapter: 'claude',
    available: true,
    capabilities: caps({ workModes: ['execute', 'plan'], effortLevels: ['low', 'medium', 'high'], compact: true, systemPrompt: true }),
  },
  {
    id: 'pi',
    label: 'pi',
    icon: PI_ICON,
    adapter: 'pi',
    available: true,
    capabilities: caps({ approvals: false, permissionModes: [], effortLevels: ['off', 'low', 'medium', 'high'], compact: true }),
  },
];

const MODELS: Record<string, ModelOption[]> = {
  codex: [
    {
      id: 'gpt-5.5',
      label: 'GPT-5.5',
      isDefault: true,
      efforts: ['low', 'medium', 'high', 'xhigh'],
      speeds: [
        { id: 'standard', label: '标准' },
        { id: 'fast', label: '快速', description: '更快出结果，额度消耗更多', isFast: true },
      ],
    },
    { id: 'gpt-5.5-mini', label: 'GPT-5.5 mini', efforts: ['low', 'medium', 'high'] },
  ],
  claude: [
    { id: 'opus', label: 'Opus', isDefault: true },
    { id: 'sonnet', label: 'Sonnet' },
  ],
  pi: [{ id: 'default', label: '默认模型', isDefault: true }],
};

/** Builds one thread's event log: seq counts up, turns and times are given per event. */
function log(threadId: string) {
  const events: StoredEvent[] = [];
  return {
    events,
    add(turnId: string | null, ts: string, type: string, data: Record<string, unknown> = {}) {
      events.push({ threadId, seq: events.length + 1, turnId, type, ts, data });
    },
  };
}

const THEME_BEFORE = `export const themes = {
  light: lightTokens,
};

export function applyTheme(root: HTMLElement) {
  root.dataset.theme = 'light';
}
`;

const THEME_AFTER = `export type ThemeChoice = 'system' | 'light' | 'dark';

export const themes = {
  light: lightTokens,
  dark: darkTokens,
};

/** The system decides unless the user picked one in settings. */
export function applyTheme(root: HTMLElement, choice: ThemeChoice = 'system') {
  const dark = choice === 'dark' || (choice === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  root.dataset.theme = dark ? 'dark' : 'light';
}
`;

const SETTINGS_BEFORE = `      <Row label="语言">
        <LanguageSelect />
      </Row>
`;

const SETTINGS_AFTER = `      <Row label="语言">
        <LanguageSelect />
      </Row>
      <Row label="外观" hint="默认跟随系统">
        <Segmented options={['跟随系统', '浅色', '深色']} value={theme} onChange={setTheme} />
      </Row>
`;

const LIST_BEFORE = `  const rowHeight = useMemo(() => measureRow(items[0]), [items]);
`;

const LIST_AFTER = `  // Rows are measured once their images load; until then each keeps the height it was given.
  const rowHeight = useStableHeight(measureRow, items);
`;
const FILTERS_BEFORE = `  const [category, setCategory] = useState<Category>('all');
`;
const FILTERS_AFTER = `  // The filters live in the URL, so a refresh or a shared link keeps them.
  const [category, setCategory] = useQueryParam<Category>('category', 'all');
`;

/** The two preview pictures the dark-mode thread drew of its settings page: neutral greys, no real project. */
const settingsMock = (page: string, card: string, line: string, edge: string, filled: string) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 200">
<rect width="280" height="200" rx="12" fill="${page}"/>
<rect x="20" y="18" width="132" height="12" rx="6" fill="${line}"/>
<g fill="${card}" stroke="${edge}">
<rect x="20" y="44" width="240" height="40" rx="8"/>
<rect x="20" y="92" width="240" height="40" rx="8"/>
<rect x="20" y="140" width="240" height="40" rx="8"/>
</g>
<g fill="${edge}">
<circle cx="40" cy="64" r="5"/><circle cx="40" cy="112" r="5"/><circle cx="40" cy="160" r="5"/>
</g>
<g fill="${line}">
<rect x="56" y="59" width="72" height="10" rx="5"/><rect x="56" y="107" width="56" height="10" rx="5"/><rect x="56" y="155" width="64" height="10" rx="5"/>
</g>
<rect x="178" y="55" width="74" height="18" rx="9" fill="${page}" stroke="${edge}"/>
<rect x="206" y="55" width="44" height="18" rx="9" fill="${filled}"/>
<circle cx="240" cy="112" r="9" fill="${page}" stroke="${edge}"/>
<circle cx="251" cy="112" r="4" fill="${card}"/>
<rect x="196" y="151" width="56" height="18" rx="9" fill="${page}" stroke="${edge}"/>
</svg>
`;
const PREVIEW_LIGHT = settingsMock('#f8f6f1', '#ffffff', '#1f1e1c', '#e5e2da', '#1f1e1c');
const PREVIEW_DARK = settingsMock('#171614', '#242220', '#f2f0ea', '#3a3835', '#f2f0ea');

/** A row of bars: the shape of a statistics or plan screenshot, in the same neutral greys. */
const barsMock = (page: string, card: string, line: string, edge: string, ok: string, bad: string) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 200">
<rect width="280" height="200" rx="12" fill="${page}"/>
<rect x="20" y="18" width="104" height="12" rx="6" fill="${line}"/>
<g fill="${card}" stroke="${edge}">
<rect x="20" y="44" width="240" height="136" rx="8"/>
</g>
<g fill="${ok}">
<rect x="42" y="132" width="22" height="30" rx="4"/>
<rect x="78" y="96" width="22" height="66" rx="4"/>
<rect x="114" y="72" width="22" height="90" rx="4"/>
<rect x="150" y="108" width="22" height="54" rx="4"/>
</g>
<rect x="186" y="60" width="22" height="102" rx="4" fill="${bad}"/>
<rect x="42" y="62" width="112" height="9" rx="4" fill="${line}"/>
</svg>
`;
/** Two tinted bands: a diff screenshot at a readable size. */
const diffMock = (page: string, add: string, del: string, line: string) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 200">
<rect width="280" height="200" rx="12" fill="${page}"/>
<rect x="20" y="18" width="140" height="10" rx="5" fill="${line}"/>
<g fill="${del}">
<rect x="20" y="44" width="240" height="16" rx="4"/>
<rect x="20" y="64" width="200" height="16" rx="4"/>
<rect x="20" y="84" width="220" height="16" rx="4"/>
</g>
<g fill="${add}">
<rect x="20" y="108" width="240" height="16" rx="4"/>
<rect x="20" y="128" width="176" height="16" rx="4"/>
</g>
<g fill="${line}">
<rect x="20" y="156" width="120" height="10" rx="5"/>
<rect x="20" y="174" width="86" height="10" rx="5"/>
</g>
</svg>
`;

const OVERVIEW_WIDE = settingsMock('#f8f6f1', '#ffffff', '#1f1e1c', '#e5e2da', '#1f1e1c');
const OVERVIEW_PHONE = settingsMock('#f4f2ed', '#ffffff', '#24231f', '#e2dfd6', '#24231f');
const E2E_PHONE = settingsMock('#f8f6f1', '#efece4', '#1f1e1c', '#dcd8ce', '#1f1e1c');
const PLAN_BOARD = barsMock('#f8f6f1', '#ffffff', '#1f1e1c', '#e5e2da', '#8b8a85', '#c4c1b9');
const STATS_TABLE = barsMock('#171614', '#242220', '#f2f0ea', '#3a3835', '#c9c6be', '#6d6a63');
const DIFF_COLORS = diffMock('#f8f6f1', '#e6f4ea', '#fcecec', '#1f1e1c');
const TRACE = diffMock('#171614', '#1f3528', '#3a2324', '#f2f0ea');

/** The demo's answer to a request for one of its pictures, by the path it is known by. */
export function demoPicture(path: string): string | null {
  if (path.endsWith('preview-light.svg')) return PREVIEW_LIGHT;
  if (path.endsWith('preview-dark.svg')) return PREVIEW_DARK;
  if (path.endsWith('overview-wide.svg')) return OVERVIEW_WIDE;
  if (path.endsWith('overview-390.svg')) return OVERVIEW_PHONE;
  if (path.endsWith('e2e-phone.svg')) return E2E_PHONE;
  if (path.endsWith('plan-board.svg')) return PLAN_BOARD;
  if (path.endsWith('stats-table.svg')) return STATS_TABLE;
  if (path.endsWith('diff-colors.svg')) return DIFF_COLORS;
  if (path.endsWith('trace.svg')) return TRACE;
  return null;
}

export function demoData(now = Date.now()): DemoData {
  const at = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();
  const thread = (t: Partial<Thread> & Pick<Thread, 'id' | 'agent' | 'cwd' | 'title'>, updated: number): Thread => ({
    agentSessionId: null,
    status: 'idle',
    pinned: false,
    workMode: 'execute',
    speed: null,
    permissionMode: 'auto_edit',
    model: null,
    effort: null,
    archivedAt: null,
    sectionId: null,
    forkedFrom: null,
    unread: false,
    preview: null,
    terminalActivity: false,
    waiting: false,
    queued: [],
    resumeCommand: null,
    cwdReal: t.cwd,
    createdAt: at(updated + 30),
    updatedAt: at(updated),
    ...t,
  });

  // A finished turn: thinking, a search, an edit with its diff, a test run, then the answer.
  const dark = log('demo-dark-mode');
  {
    const turn = 'turn-dark-1';
    dark.add(null, at(42), 'thread.created');
    dark.add(turn, at(41), 'turn.started', { text: '设置页加一个深色模式开关，默认跟随系统。' });
    dark.add(turn, at(41), 'reasoning.delta', { text: '先找到主题变量在哪里定义，再看设置页的布局，开关放在「语言」下面比较顺。' });
    dark.add(turn, at(41), 'tool.start', { id: 'c1', title: 'rg -n "prefers-color-scheme|themes" src', kind: 'execute', input: { command: 'rg -n "prefers-color-scheme|themes" src' } });
    dark.add(turn, at(41), 'tool.end', { id: 'c1', status: 'completed', output: 'src/theme.ts:1:export const themes = {\nsrc/main.tsx:8:applyTheme(document.documentElement);' });
    // The two edits carry the numeric reference the overview reports in `details`, so the demo's
    // history/detail can answer them from these very events.
    dark.add(turn, at(40), 'tool.start', {
      id: 2,
      title: 'src/theme.ts',
      kind: 'edit',
      content: [{ type: 'diff', path: `${STOREFRONT}/src/theme.ts`, oldText: THEME_BEFORE, newText: THEME_AFTER }],
    });
    dark.add(turn, at(40), 'tool.end', { id: 2, status: 'completed' });
    dark.add(turn, at(40), 'tool.start', {
      id: 3,
      title: 'src/pages/Settings.tsx',
      kind: 'edit',
      content: [{ type: 'diff', path: `${STOREFRONT}/src/pages/Settings.tsx`, oldText: SETTINGS_BEFORE, newText: SETTINGS_AFTER }],
    });
    dark.add(turn, at(40), 'tool.end', { id: 3, status: 'completed' });
    dark.add(turn, at(39), 'tool.start', { id: 'c4', title: 'npm test', kind: 'execute', input: { command: 'npm test' } });
    dark.add(turn, at(38), 'tool.end', { id: 'c4', status: 'completed', output: '✓ theme.test.ts (6)\n✓ settings.test.tsx (11)\n\nTest Files  2 passed\nTests  17 passed' });
    dark.add(turn, at(38), 'message.delta', {
      text: [
        '加好了。设置页「语言」下面多了一行「外观」，三个选项：**跟随系统**（默认）、浅色、深色。',
        '',
        '改动：',
        '',
        '- `src/theme.ts`：新增 `darkTokens`，`applyTheme` 接受用户的选择，没选时看系统设置。',
        '- `src/pages/Settings.tsx`：加了分段选择器，选择存在 `localStorage`。',
        '- [主题说明](docs/theme.md)：主题变量的用法，给后来的人。',
        '',
        '```ts',
        "applyTheme(document.documentElement, loadThemeChoice() ?? 'system');",
        '```',
        '',
        '测试 17 个全部通过。系统切换深浅色时页面会跟着变，不用刷新。两种外观的预览：',
        '',
        '![浅色外观](docs/preview-light.svg)',
        '',
        '![深色外观](docs/preview-dark.svg)',
      ].join('\n'),
    });
    dark.add(turn, at(38), 'usage.updated', { used: 46_000, size: 258_000 });
    // What the latest request was made of, for the context panel's composition rows.
    dark.add(turn, at(38), 'usage.sample', { scope: 'request', model: 'fixture-model', input: 22_400, cacheRead: 12_800, cacheWrite: 6_400, output: 3_200, reasoning: 1_100 });
    // The plan the answer worked from, one step still on it: a finished step, the one under way,
    // and one not started, all three where the card shows them apart.
    dark.add(turn, at(38), 'plan.updated', { entries: [
      { content: '读一遍现有的主题实现', status: 'completed' },
      { content: '设置页接入 prefers-color-scheme', status: 'in_progress' },
      { content: '补齐两种外观的预览截图', status: 'pending' },
    ] });
    dark.add(turn, at(38), 'turn.done', { stopReason: 'end_turn' });
  }

  // Stopped on an approval: the agent wants to run the browser tests.
  const scroll = log('demo-scroll-fix');
  {
    const turn = 'turn-scroll-1';
    scroll.add(null, at(9), 'thread.created');
    scroll.add(turn, at(8), 'turn.started', { text: '商品列表往下滚的时候会跳一下，图片加载出来以后更明显。查一下原因并修掉。' });
    scroll.add(turn, at(8), 'reasoning.delta', { text: '跳动多半是行高在图片加载后变了，虚拟列表用的还是旧高度。先读列表组件。' });
    scroll.add(turn, at(8), 'tool.start', { id: 's1', title: 'src/components/ProductList.tsx', kind: 'read' });
    scroll.add(turn, at(8), 'tool.end', { id: 's1', status: 'completed' });
    scroll.add(turn, at(7), 'message.delta', { text: '找到了：行高只在第一次渲染时量一次，图片加载后没有重新量。改成图片加载完再量。' });
    scroll.add(turn, at(7), 'tool.start', {
      id: 's2',
      title: 'src/components/ProductList.tsx',
      kind: 'edit',
      content: [{ type: 'diff', path: `${STOREFRONT}/src/components/ProductList.tsx`, oldText: LIST_BEFORE, newText: LIST_AFTER }],
    });
    scroll.add(turn, at(7), 'tool.end', { id: 's2', status: 'completed' });
    // The plan it is working from, still under way — the turn has not finished, so one step is on.
    scroll.add(turn, at(7), 'plan.updated', { entries: [
      { content: '找出跳动的来源', status: 'completed' },
      { content: '跑商品列表的 e2e 验证', status: 'in_progress' },
      { content: '整理成改动说明', status: 'pending' },
    ] });
    scroll.add(turn, at(6), 'approval.request', {
      approvalId: 'demo-approval',
      title: '运行 npm run e2e -- product-list',
      options: [
        { optionId: 'allow', name: '允许这一次', kind: 'allow_once' },
        { optionId: 'always', name: '本会话内不再询问', kind: 'allow_always' },
        { optionId: 'reject', name: '拒绝', kind: 'reject_once' },
      ],
      toolCall: { rawInput: { command: 'npm run e2e -- product-list' } },
    });
  }

  // Plan mode: a question answered, then the plan it led to.
  const routes = log('demo-routes');
  {
    const turn = 'turn-routes-1';
    routes.add(null, at(130), 'thread.created');
    routes.add(turn, at(128), 'turn.started', { text: '想把接口迁到新的路由写法，先出个方案，别动代码。', workMode: 'plan' });
    routes.add(turn, at(128), 'reasoning.delta', { text: '现在 32 个接口都写在一个 routes.ts 里。迁移方式有两种，要先问清楚想怎么组织。' });
    routes.add(turn, at(127), 'tool.start', { id: 'r1', title: 'rg -c "router\\." src', kind: 'execute', input: { command: 'rg -c "router\\." src' } });
    routes.add(turn, at(127), 'tool.end', { id: 'r1', status: 'completed', output: 'src/routes.ts:32' });
    routes.add(turn, at(127), 'input.request', {
      inputId: 'demo-question',
      title: '新的路由怎么组织？',
      questions: [
        {
          id: 'layout',
          header: '路由组织',
          question: '新的路由怎么组织？',
          // An answered card shows the values themselves, so they are the words.
          options: [
            { value: '按文件', label: '按文件', description: '每个资源一个文件，目录即路径' },
            { value: '集中一张表', label: '集中一张表', description: '保留一个入口，改成声明式' },
          ],
        },
      ],
    });
    routes.add(turn, at(125), 'input.resolved', { inputId: 'demo-question', answer: { answers: { layout: ['按文件'] } } });
    routes.add(turn, at(124), 'plan.completed', {
      id: 'plan-routes',
      text: [
        '## 迁移到按文件组织的路由',
        '',
        '1. 新建 `src/routes/`，每个资源一个文件：`notes.ts`、`users.ts`、`tags.ts`。',
        '2. 写一个 `loadRoutes()`，按目录注册，旧的 `routes.ts` 暂时保留。',
        '3. 一次迁一个资源，每迁完一个跑一遍接口测试。',
        '4. 全部迁完后删掉 `routes.ts`，更新 README 里的接口说明。',
        '',
        '**风险**：`/users/:id/notes` 横跨两个资源，放在 `users.ts` 里，在 `notes.ts` 留一行注释指过去。',
      ].join('\n'),
    });
    routes.add(turn, at(124), 'turn.done', { stopReason: 'end_turn' });
  }

  // A plan Claude held itself until it was told to go: the native approval, its card and its trace.
  const filters = log('demo-filters');
  {
    const turn = 'turn-filters-1';
    filters.add(null, at(75), 'thread.created');
    filters.add(turn, at(74), 'turn.started', { text: '商品列表想按类目和价格筛一下，先出个方案，先别动代码。', workMode: 'plan' });
    filters.add(turn, at(74), 'reasoning.delta', { text: '筛选状态放 URL 查询参数里最稳，刷新和分享都能保住。先看列表页和数据层现在长什么样。' });
    filters.add(turn, at(73), 'tool.start', { id: 'f1', title: 'src/pages/Products.tsx', kind: 'read' });
    filters.add(turn, at(73), 'tool.end', { id: 'f1', status: 'completed' });
    filters.add(turn, at(72), 'tool.start', { id: 'f2', title: 'rg "listProducts" src', kind: 'execute', input: { command: 'rg "listProducts" src' } });
    filters.add(turn, at(72), 'tool.end', { id: 'f2', status: 'completed', output: 'src/api/products.ts:14:export function listProducts(query) {' });
    filters.add(turn, at(71), 'message.delta', { text: '看完了，接口本身已经收查询参数，只差前端把筛选接进去。方案如下，改动都落在列表页这一侧。' });
    // The bridge titles the plan-exit tool call the way Claude Code's own UI does.
    filters.add(turn, at(70), 'tool.start', { id: 'f3', title: 'Exited Plan Mode', kind: 'other' });
    filters.add(turn, at(69), 'tool.end', { id: 'f3', status: 'completed' });
    filters.add(turn, at(69), 'input.request', {
      inputId: 'demo-plan-approval',
      title: '计划已准备好',
      kind: 'plan',
      // Full access answered this gate itself: the plan stays on record, nobody was asked.
      auto: { answers: { approval: ['exit-plan-bypass'] } },
      plan: [
        '## 商品筛选方案',
        '',
        '筛选状态放进 URL 查询参数（`?category=&max=`），刷新、后退和分享链接都能保住；数据层只做透传，不另建状态。',
        '',
        '| 文件 | 改动 |',
        '|---|---|',
        '| `src/pages/Products.tsx` | 筛选栏 UI，读写查询参数 |',
        '| `src/api/products.ts` | 类型补上筛选字段 |',
        '| `src/i18n/locales/{zh,en}.ts` | 筛选项文案 |',
        '',
        '明确不做：服务端聚合筛选（接口已支持）、价格区间滑块（先用两个输入框）。',
      ].join('\n'),
      questions: [{ id: 'approval', question: '是否按此计划开始执行？', options: [{ value: 'exit-plan-bypass', label: '开始执行' }] }],
    });
    filters.add(turn, at(66), 'tool.start', {
      id: 'f4',
      title: 'src/pages/Products.tsx',
      kind: 'edit',
      content: [{ type: 'diff', path: `${STOREFRONT}/src/pages/Products.tsx`, oldText: FILTERS_BEFORE, newText: FILTERS_AFTER }],
    });
    filters.add(turn, at(65), 'tool.end', { id: 'f4', status: 'completed' });
    filters.add(turn, at(64), 'message.delta', { text: '筛选栏接好了：类目和价格都进 URL，刷新后还在。文案中英文各补了三条，测试跑过，改动就这些。' });
    filters.add(turn, at(64), 'turn.done', { stopReason: 'end_turn' });
  }

  // Started by the weekly schedule; the answer is a table.
  const weekly = log('demo-weekly');
  {
    const turn = 'turn-weekly-1';
    weekly.add(null, at(60 * 26), 'thread.created');
    weekly.add(turn, at(60 * 26), 'turn.started', { text: '整理上周的提交记录，按模块写成周报。', scheduled: true });
    weekly.add(turn, at(60 * 26), 'tool.start', { id: 'w1', title: 'git log --since="1 week ago" --oneline', kind: 'execute', input: { command: 'git log --since="1 week ago" --oneline' } });
    weekly.add(turn, at(60 * 26), 'tool.end', { id: 'w1', status: 'completed', output: '9f2c1ab 笔记支持标签筛选\n41d07e3 修复分页越界\n…（共 23 条）' });
    weekly.add(turn, at(60 * 26 - 1), 'message.delta', {
      text: [
        '## 上周周报',
        '',
        '| 模块 | 提交 | 主要变化 |',
        '|---|---|---|',
        '| 笔记 | 11 | 标签筛选上线；导出支持 Markdown |',
        '| 用户 | 7 | 登录失败次数限制；头像裁剪 |',
        '| 基础设施 | 5 | 分页越界修复；CI 缓存命中率提高 |',
        '',
        '下周重点：路由迁移（方案已在另一个线程里定好）。',
      ].join('\n'),
    });
    weekly.add(turn, at(60 * 26 - 1), 'turn.done', { stopReason: 'end_turn' });
  }

  // Finished while nobody was looking: listed as unread.
  const tests = log('demo-tests');
  {
    const turn = 'turn-tests-1';
    tests.add(null, at(20), 'thread.created');
    tests.add(turn, at(19), 'turn.started', { text: '给 /users 接口补上缺的测试。' });
    tests.add(turn, at(19), 'tool.start', { id: 't1', title: 'test/users.test.ts', kind: 'read' });
    tests.add(turn, at(19), 'tool.end', { id: 't1', status: 'completed' });
    tests.add(turn, at(17), 'tool.start', { id: 't2', title: 'npm test -- users', kind: 'execute', input: { command: 'npm test -- users' } });
    tests.add(turn, at(16), 'tool.end', { id: 't2', status: 'completed', output: 'Tests  14 passed' });
    tests.add(turn, at(16), 'message.delta', { text: '补了 5 个测试：创建、重名、分页、删除不存在的用户、权限不足。全部通过，没有改业务代码。' });
    tests.add(turn, at(16), 'turn.done', { stopReason: 'end_turn' });
  }

  // A lead with four members: one reporting back for review, one blocked on the person, one whose
  // turn died, and one still working. Their deliveries reach the lead as the quiet lines the
  // timeline draws them into.
  const wrapup = log('demo-wrapup');
  {
    const prep = 'turn-wrapup-0';
    const turn = 'turn-wrapup-1';
    wrapup.add(null, at(52), 'thread.created');
    wrapup.add(prep, at(51), 'turn.started', { text: '上线前我先落发布页、说明和几个接口的小修，再分给三个人核对。' });
    // Each edit is a tool the overview's change row reads back, turn and id and all: the demo
    // answers history/detail from these very events.
    const edit = (id: number, path: string, before: string | null, after: string) => {
      wrapup.add(prep, at(51), 'tool.start', { id, title: path, kind: 'edit', content: [{ type: 'diff', path, oldText: before, newText: after }] });
      wrapup.add(prep, at(51), 'tool.end', { id, status: 'completed' });
    };
    edit(1, `${NOTES_API}/docs/release-notes.html`, null, '<!doctype html>\n<html lang="zh">\n<head>\n  <meta charset="utf-8" />\n  <title>2.0 发布说明</title>\n</head>\n<body>\n  <h1>2.0</h1>\n  <p>概况卡、深色模式和接口测试一起上线。</p>\n</body>\n</html>\n');
    edit(2, `${NOTES_API}/docs/overview.html`, null, '<!doctype html>\n<html lang="zh">\n<head>\n  <meta charset="utf-8" />\n  <title>概况面板</title>\n</head>\n<body>\n  <h1>概况</h1>\n  <p>顶栏一个按钮，团队、计划、变更、图片和文件都在里面。</p>\n</body>\n</html>\n');
    edit(3, `${NOTES_API}/docs/overview.svg`, null, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">\n  <rect width="320" height="200" rx="12" fill="#f8f6f1"/>\n  <rect x="16" y="16" width="120" height="10" rx="5" fill="#1f1e1c"/>\n  <g fill="#e5e2da">\n    <rect x="16" y="40" width="288" height="26" rx="6"/>\n    <rect x="16" y="72" width="288" height="26" rx="6"/>\n    <rect x="16" y="104" width="288" height="26" rx="6"/>\n  </g>\n</svg>\n');
    edit(4, `${NOTES_API}/out/stats.md`, '# 统计\n\n- 轮次：11\n- 输入：48.2M\n', '# 统计\n\n- 轮次：12\n- 输入：51.4M\n- 缓存命中：97.4%\n- 输出速度：74.2 tok/s（约）\n');
    edit(5, `${NOTES_API}/src/outputs.ts`, 'export function outputs(events: StoredEvent[]) {\n  return { files: [], images: [], seq: 0 };\n}\n', 'export function outputs(events: StoredEvent[]) {\n  const files = changedFiles(events);\n  const images = imageFiles(events);\n  return { changes: summarize(files), images, files, seq: events.at(-1)?.seq ?? 0 };\n}\n\nexport function changedFiles(events: StoredEvent[]) {\n  return events.filter(isEdit).map(toChangedFile);\n}\n');
    edit(6, `${NOTES_API}/src/history.ts`, 'export async function detail(ref: DetailRef) {\n  return read(ref);\n}\n', 'export async function detail(ref: DetailRef, cursor?: number, path?: string) {\n  const page = await read(ref, cursor);\n  return path ? pickPath(page, path) : page;\n}\n');
    edit(7, `${NOTES_API}/src/threads.ts`, 'export function thread(id: string) {\n  return store.get(id);\n}\n', 'export function thread(id: string) {\n  const found = store.get(id);\n  if (!found) throw new ApiError(`no such thread: ${id}`, {}, 404);\n  return found;\n}\n');
    edit(8, `${NOTES_API}/src/store.ts`, 'const rows = new Map();\n', 'const rows = new Map();\n// Newest first, so the overview does not sort on every read.\nexport const latest = () => [...rows.values()].sort((a, b) => b.seq - a.seq);\n');
    edit(9, `${NOTES_API}/README.md`, '# notes-api\n\n示例项目：笔记服务的接口。\n', '# notes-api\n\n示例项目：笔记服务的接口。\n\n概况与统计的读法见 `docs/overview.html`。\n');
    edit(10, `${NOTES_API}/package.json`, '  "scripts": {\n    "test": "node --test"\n  }\n', '  "scripts": {\n    "test": "node --test",\n    "e2e": "node e2e/run.mjs"\n  }\n');
    edit(11, `${NOTES_API}/test/outputs.test.ts`, null, "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\nimport { changedFiles, outputs } from '../src/outputs.js';\n\ntest('a change is grouped by path, newest turn wins', () => {\n  const events = [\n    { type: 'tool.start', turnId: 't1', data: { id: 1, kind: 'edit', content: [{ type: 'diff', path: '/a.ts', oldText: 'a', newText: 'b' }] } },\n  ];\n  assert.equal(changedFiles(events as any).length, 1);\n  assert.equal(outputs(events as any).changes.total, 1);\n});\n");
    edit(12, `${NOTES_API}/docs/old-plan.md`, '# 旧的上线计划\n\n- 先做概况卡\n- 再做统计\n- 截图最后补\n- 深色放二期\n- 发布页复用周报\n', '');
    edit(13, `${NOTES_API}/docs/theme.md`, '# 主题\n\n页面从 `src/theme.ts` 取颜色变量。\n', '# 主题\n\n页面从 `src/theme.ts` 取颜色变量。要支持新的外观，加一组 token 并在 `applyTheme` 里注册。\n\n- `light`：默认。\n- `dark`：深色，跟随系统时由 `prefers-color-scheme` 决定。\n');
    wrapup.add(prep, at(51), 'message.delta', { text: '发布页、说明和接口的小修都落好了，旧计划删掉，接着分给三个人核对。' });
    wrapup.add(prep, at(51), 'turn.done', { stopReason: 'end_turn' });
    wrapup.add(turn, at(49), 'turn.started', { text: '上线前把三块收尾分出去：接口审查、深色核对、统计补齐，收齐了汇总给我。' });
    wrapup.add(turn, at(47), 'message.delta', { text: '分好了。三块都派出去了，交回后我在这里汇总。' });
    wrapup.add(turn, at(47), 'turn.done', { stopReason: 'end_turn' });
    // A member's result, as the daemon shows it in the lead's own log: who it is from, how the
    // turn went, then the member's words — the protocol lines the row reads back.
    const relay = (key: string, minutesAgo: number, from: string, text: string) => {
      const relayTurn = `turn-wrapup-${key}`;
      wrapup.add(relayTurn, at(minutesAgo), 'turn.started', { text: `[协作消息 ${key}｜${from} → 主智能体]`, displayText: `来自 ${from} 的结果\n\n${text}`, coordination: true });
      wrapup.add(relayTurn, at(minutesAgo), 'turn.done', { stopReason: 'end_turn' });
    };
    relay('review', 41, '接口审查', [
      '状态：本轮已返回，仍需验收',
      '核对完了 `packages/daemon/` 下的接口约定：',
      '',
      '- `src/outputs.ts`：汇总、路径解析、按 seq 排序',
      '- `src/history.ts`、`src/store.ts`：复用轻量摘要',
      '- 异常处理补了重启后的恢复步骤',
      '',
      '全量测试 1084 项，1083 通过，1 跳过。',
      '',
      '[进度] 待验收：接口和测试都交回，请统一验收',
    ].join('\n'));
    relay('colors', 27, '深色核对', [
      '状态：本轮已返回，仍需验收',
      '概况卡的截图都齐了，深色下 diff 里 +12 的绿色偏亮。',
      '',
      '想改成 `--positive` 混 70% 正文色，需要你确认取值。',
      '',
      '[进度] 需要你：深色下的取值需要确认',
    ].join('\n'));
    relay('stats', 12, '统计补齐', [
      '状态：失败',
      '跑 e2e 时手机 390 尺寸第 3 步超时（30s），桌面全过。日志保留在 /tmp/overview-390.log，回头接着查。',
    ].join('\n'));
    // The lead's own list, last, so the overview is where the newest plan lives.
    wrapup.add('turn-wrapup-stats', at(12), 'plan.updated', { entries: [
      { content: '发布页与说明的收尾改动', status: 'completed' },
      { content: '接口审查交回', status: 'completed' },
      { content: '深色取值确认', status: 'in_progress' },
      { content: '统计补齐重跑', status: 'pending' },
      { content: '汇总上线清单', status: 'pending' },
    ] });
  }
  // The members' own threads: each opens on the task the lead handed it. The answer closes the
  // turn, or an error does — a member that failed never said anything of its own.
  const member = (id: string, minutesAgo: number, task: string, close?: string | { error: string }) => {
    const events = log(id);
    const turn = `turn-${id}`;
    events.add(null, at(minutesAgo + 1), 'thread.created');
    events.add(turn, at(minutesAgo), 'turn.started', { text: task, displayText: `来自 主智能体 的任务\n\n${task}`, coordination: true });
    if (typeof close === 'string') {
      events.add(turn, at(minutesAgo - 6), 'message.delta', { text: close });
      events.add(turn, at(minutesAgo - 6), 'turn.done', { stopReason: 'end_turn' });
    } else if (close) {
      events.add(turn, at(minutesAgo - 20), 'turn.error', { message: close.error });
    }
    return events.events;
  };
  const wrapupEvents = {
    'demo-wrapup-review': member('demo-wrapup-review', 48, '核对接口约定、目录分工和异常处理，结果写进 out/stats.md。', '核对完了，接口和测试都交回，请验收。\n[进度] 待验收：接口和测试都交回，请统一验收'),
    'demo-wrapup-colors': member('demo-wrapup-colors', 34, '把概况卡的截图补齐，深色的取值先别定。', '截图都齐了，深色取值等你确认。\n[进度] 需要你：深色下的取值需要确认'),
    'demo-wrapup-stats': member('demo-wrapup-stats', 20, '把统计口径补齐，结果写回 out/stats.md。', { error: 'e2e 在手机 390 尺寸第 3 步超时（30s），重试后仍失败。' }),
    // The fourth member is still working: no answer, no error, just its turn left open.
    'demo-wrapup-shots': member('demo-wrapup-shots', 15, '把发布页、概况面板和手机尺寸的截图补齐，深色也来一张。'),
  };

  const sections: Section[] = [{ id: 'demo-reports', name: '周报', position: 0 }];

  const threads: Thread[] = [
    thread({ id: 'demo-wrapup', agent: 'claude', cwd: NOTES_API, title: '上线前收尾', preview: '接口审查交回了，统计补齐失败了', coordination: { rootId: 'demo-wrapup', children: 4, paused: false, doing: '等统计补齐重跑，截图还在补' } }, 12),
    thread({ id: 'demo-wrapup-review', agent: 'codex', cwd: NOTES_API, title: '接口审查', preview: '核对完了，接口和测试都交回', coordination: { rootId: 'demo-wrapup', children: 0, paused: false, doing: '已交回，待验收' }, report: { stage: 'review', note: '接口和测试都交回', at: at(41) } }, 42),
    thread({ id: 'demo-wrapup-colors', agent: 'claude', cwd: NOTES_API, title: '深色核对', preview: '深色取值等你确认', coordination: { rootId: 'demo-wrapup', children: 0, paused: false, doing: '需要你确认取值' }, report: { stage: 'blocked', note: '深色下的取值需要确认', at: at(27) } }, 28),
    thread({ id: 'demo-wrapup-stats', agent: 'pi', cwd: NOTES_API, title: '统计补齐', status: 'error', preview: '手机尺寸超时，待重跑', coordination: { rootId: 'demo-wrapup', children: 0, paused: false, doing: '手机尺寸超时' } }, 12),
    thread({ id: 'demo-wrapup-shots', agent: 'codex', cwd: NOTES_API, title: '截图补齐', status: 'running', preview: '正在补发布页和手机尺寸的截图', coordination: { rootId: 'demo-wrapup', children: 0, paused: false, doing: '正在补截图' } }, 12),
    thread({ id: 'demo-scroll-fix', agent: 'claude', cwd: STOREFRONT, title: '修复商品列表滚动跳动', status: 'running', waiting: true, waitingReason: 'approval', preview: '商品列表往下滚的时候会跳一下', model: 'opus' }, 6),
    thread({ id: 'demo-tests', agent: 'codex', cwd: NOTES_API, title: '补齐 /users 接口测试', unread: true, preview: '给 /users 接口补上缺的测试。', report: { stage: 'review', note: '补了 12 个用例，本地全部通过', at: at(16) } }, 16),
    thread({ id: 'demo-dark-mode', agent: 'codex', cwd: STOREFRONT, title: '设置页加深色模式', preview: '设置页加一个深色模式开关', model: 'gpt-5.5', effort: 'high', doneAt: at(30) }, 38),
    thread({ id: 'demo-routes', agent: 'codex', cwd: NOTES_API, title: '迁移到新的路由写法', workMode: 'plan', preview: '想把接口迁到新的路由写法' }, 124),
    thread({ id: 'demo-filters', agent: 'claude', cwd: STOREFRONT, title: '商品列表加筛选', preview: '筛选栏接好了：类目和价格都进 URL', model: 'opus', permissionMode: 'full_access', doneAt: at(64) }, 74),
    thread({ id: 'demo-weekly', agent: 'pi', cwd: NOTES_API, title: '上周周报', sectionId: 'demo-reports', permissionMode: 'full_access', preview: '整理上周的提交记录', schedule: { id: 'demo-schedule', name: '每周一周报' } }, 60 * 26 - 1),
  ];
  const byId = (id: string) => threads.filter((t) => t.id === id);

  // The wrapup thread's team, as the coordination endpoint would answer it: what each member was
  // asked and where its last turn ended.
  const message = (id: string, to: string, minutesAgo: number, task: string, state: CoordinationMessage['state'], result: string): CoordinationMessage => ({
    id, from: 'demo-wrapup', to, text: task, kind: 'task', state, result, createdAt: at(minutesAgo), turnId: `turn-${to}`,
  });
  const teamMember = (id: string, name: string, minutesAgo: number, task: string, state: CoordinationMessage['state'], result: string) => ({
    id, name, thread: threads.find((t) => t.id === id) ?? null,
    latest: message(`demo-cm-${id}`, id, minutesAgo, task, state, result),
  });
  const coordinations: Record<string, Coordination> = {
    'demo-wrapup': {
      rootId: 'demo-wrapup',
      paused: false,
      directories: [NOTES_API],
      agents: ['claude', 'codex', 'pi'],
      members: [
        teamMember('demo-wrapup-review', '接口审查', 48, '核对接口约定、目录分工和异常处理，结果写进 out/stats.md。', 'returned', '核对完了，接口和测试都交回，请验收。\n[进度] 待验收：接口和测试都交回，请统一验收'),
        teamMember('demo-wrapup-colors', '深色核对', 34, '把概况卡的截图补齐，深色的取值先别定。', 'returned', '截图都齐了，深色取值等你确认。\n[进度] 需要你：深色下的取值需要确认'),
        teamMember('demo-wrapup-stats', '统计补齐', 20, '把统计口径补齐，结果写回 out/stats.md。', 'failed', ''),
        teamMember('demo-wrapup-shots', '截图补齐', 15, '把发布页、概况面板和手机尺寸的截图补齐，深色也来一张。', 'returned', '深色和手机 390 的截图正在出，发布页的已经好了。\n[进度] 进行中'),
      ],
      messages: [],
    },
  };

  const todo = (t: Partial<Todo> & Pick<Todo, 'id' | 'text' | 'position'>): Todo => ({
    done: false,
    parentId: null,
    createdAt: at(60 * 48),
    doneAt: null,
    threads: [],
    ...t,
  });
  const todos: Todo[] = [
    todo({ id: 'demo-todo-release', text: '发布 2.0', position: 0 }),
    todo({ id: 'demo-todo-dark', text: '深色模式', parentId: 'demo-todo-release', position: 0, done: true, doneAt: at(37), threads: byId('demo-dark-mode') }),
    todo({ id: 'demo-todo-scroll', text: '商品列表滚动跳动', parentId: 'demo-todo-release', position: 1, threads: byId('demo-scroll-fix') }),
    todo({ id: 'demo-todo-notes', text: '写发布说明', parentId: 'demo-todo-release', position: 2 }),
    todo({ id: 'demo-todo-routes', text: '路由迁移', position: 1, threads: byId('demo-routes') }),
    todo({ id: 'demo-todo-tests', text: '补齐接口测试', position: 2, threads: byId('demo-tests') }),
  ];

  const nextMonday = new Date(now);
  nextMonday.setDate(nextMonday.getDate() + ((8 - nextMonday.getDay()) % 7 || 7));
  nextMonday.setHours(9, 0, 0, 0);
  const schedules: Schedule[] = [
    {
      id: 'demo-schedule',
      name: '每周一周报',
      agent: 'pi',
      cwd: NOTES_API,
      prompt: '整理上周的提交记录，按模块写成周报。',
      cron: '0 9 * * 1',
      mode: 'new',
      threadId: null,
      permissionMode: 'full_access',
      enabled: true,
      timezone: null,
      catchUp: false,
      description: '每周一 09:00',
      nextRunAt: nextMonday.toISOString(),
      lastRun: { id: 'demo-run', scheduleId: 'demo-schedule', status: 'done', threadId: 'demo-weekly', turnId: 'turn-weekly-1', startedAt: at(60 * 26), finishedAt: at(60 * 26 - 1), message: null },
      problem: null,
      createdAt: at(60 * 24 * 20),
      updatedAt: at(60 * 24 * 20),
    },
  ];

  const folders: DemoData['folders'] = {
    [`${STOREFRONT}:`]: [
      { name: 'src', type: 'dir' },
      { name: 'node_modules', type: 'dir', ignored: true },
      { name: 'package.json', type: 'file', size: 812 },
      { name: 'README.md', type: 'file', size: 1240 },
    ],
    [`${STOREFRONT}:src`]: [
      { name: 'components', type: 'dir' },
      { name: 'pages', type: 'dir' },
      { name: 'main.tsx', type: 'file', size: 402 },
      { name: 'theme.ts', type: 'file', size: THEME_AFTER.length },
    ],
    [`${NOTES_API}:`]: [
      { name: 'src', type: 'dir' },
      { name: 'test', type: 'dir' },
      { name: 'package.json', type: 'file', size: 640 },
      { name: 'README.md', type: 'file', size: 980 },
    ],
  };
  const files: DemoData['files'] = {
    [`${STOREFRONT}:src/theme.ts`]: THEME_AFTER,
    [`${STOREFRONT}:README.md`]: '# storefront\n\n示例项目：一个网店的前端。\n\n```sh\nnpm install\nnpm run dev\n```\n',
    [`${NOTES_API}:README.md`]: '# notes-api\n\n示例项目：笔记服务的接口。\n',
    [`${STOREFRONT}:docs/theme.md`]: '# 主题\n\n页面从 `src/theme.ts` 取颜色变量。要支持新的外观，加一组 token 并在 `applyTheme` 里注册。\n\n- `light`：默认。\n- `dark`：深色，跟随系统时由 `prefers-color-scheme` 决定。\n\n选择本身存在 `localStorage` 的 `theme` 键里。\n',
  };

  // What the dark-mode thread's turn produced, as the daemon's overview would gather it: the
  // pictures and the doc its answer hands over, and the files its edits touched, newest first.
  // One file is new — the pair shows only what it added — and two carry names and folders longer
  // than the card has room for.
  const outputs: DemoData['outputs'] = {
    'demo-dark-mode': {
      changes: {
        files: [
          // Two of the four were reported without a reference the history can read back.
          { path: `${STOREFRONT}/src/components/settings/appearance-theme-provider.test.tsx`, added: 34, removed: 0, created: true, turns: 1, turnId: 'turn-dark-1', at: at(40), details: [] },
          { path: `${STOREFRONT}/src/lib/follow-system-color-scheme.ts`, added: 8, removed: 3, created: false, turns: 1, turnId: 'turn-dark-1', at: at(40), details: [] },
          { path: `${STOREFRONT}/src/pages/Settings.tsx`, added: 6, removed: 1, created: false, turns: 1, turnId: 'turn-dark-1', at: at(40), details: [{ turnId: 'turn-dark-1', id: 3, version: 1 }] },
          { path: `${STOREFRONT}/src/theme.ts`, added: 12, removed: 2, created: false, turns: 1, turnId: 'turn-dark-1', at: at(40), details: [{ turnId: 'turn-dark-1', id: 2, version: 1 }] },
        ],
        added: 60,
        removed: 6,
        total: 4,
      },
      images: [
        { path: `${STOREFRONT}/docs/preview-dark.svg`, turnId: 'turn-dark-1', at: at(38) },
        { path: `${STOREFRONT}/docs/preview-light.svg`, turnId: 'turn-dark-1', at: at(38) },
      ],
      files: [{ path: `${STOREFRONT}/docs/theme.md`, turnId: 'turn-dark-1', at: at(38) }],
      seq: dark.events.length,
    },
    // The lead's own gate before delegating: enough changes to make the panel's three-row cap and
    // its auto-fold visible, a page and a drawing, a file gone from the disk, and every row
    // pointing back at the tool event its diff dialog reads.
    'demo-wrapup': {
      changes: {
        files: [
          { path: `${NOTES_API}/docs/release-notes.html`, added: 68, removed: 0, created: true, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 1, version: 1 }] },
          { path: `${NOTES_API}/docs/overview.html`, added: 44, removed: 0, created: true, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 2, version: 1 }] },
          { path: `${NOTES_API}/docs/overview.svg`, added: 22, removed: 0, created: true, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 3, version: 1 }] },
          { path: `${NOTES_API}/out/stats.md`, added: 16, removed: 4, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 4, version: 1 }] },
          { path: `${NOTES_API}/src/outputs.ts`, added: 31, removed: 9, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 5, version: 1 }] },
          { path: `${NOTES_API}/src/history.ts`, added: 12, removed: 6, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 6, version: 1 }] },
          { path: `${NOTES_API}/src/threads.ts`, added: 8, removed: 3, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 7, version: 1 }] },
          { path: `${NOTES_API}/src/store.ts`, added: 5, removed: 2, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 8, version: 1 }] },
          { path: `${NOTES_API}/README.md`, added: 3, removed: 1, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 9, version: 1 }] },
          { path: `${NOTES_API}/package.json`, added: 2, removed: 2, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 10, version: 1 }] },
          { path: `${NOTES_API}/test/outputs.test.ts`, added: 40, removed: 0, created: true, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 11, version: 1 }] },
          { path: `${NOTES_API}/docs/old-plan.md`, added: 0, removed: 18, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), missing: true, details: [{ turnId: 'turn-wrapup-0', id: 12, version: 1 }] },
          { path: `${NOTES_API}/docs/theme.md`, added: 9, removed: 2, created: false, turns: 1, turnId: 'turn-wrapup-0', at: at(51), details: [{ turnId: 'turn-wrapup-0', id: 13, version: 1 }] },
        ],
        added: 260,
        removed: 47,
        total: 13,
      },
      images: [
        { path: `${NOTES_API}/docs/overview-wide.svg`, turnId: 'turn-wrapup-1', at: at(30) },
        { path: `${NOTES_API}/docs/overview-390.svg`, turnId: 'turn-wrapup-1', at: at(30) },
        { path: `${NOTES_API}/docs/diff-colors.svg`, turnId: 'turn-wrapup-1', at: at(30) },
        { path: `${NOTES_API}/docs/stats-table.svg`, turnId: 'turn-wrapup-1', at: at(30) },
        { path: `${NOTES_API}/docs/plan-board.svg`, turnId: 'turn-wrapup-1', at: at(30) },
        { path: `${NOTES_API}/docs/trace.svg`, turnId: 'turn-wrapup-1', at: at(30) },
        { path: `${NOTES_API}/docs/e2e-phone.svg`, turnId: 'turn-wrapup-1', at: at(30) },
        // Present but unreadable: the square stays, grey, with the reason on it.
        { path: `${NOTES_API}/shots/raw-scan.png`, turnId: 'turn-wrapup-1', at: at(30) },
        // Gone from the disk: no square, one faint line under the grid.
        { path: `${NOTES_API}/shots/deleted-390.png`, turnId: 'turn-wrapup-1', at: at(30), missing: true },
      ],
      files: [
        { path: `${NOTES_API}/docs/release-notes.html`, turnId: 'turn-wrapup-0', at: at(51) },
        { path: `${NOTES_API}/docs/theme.md`, turnId: 'turn-wrapup-0', at: at(51) },
        { path: `${NOTES_API}/out/stats.md`, turnId: 'turn-wrapup-0', at: at(51) },
        { path: `${NOTES_API}/data/notes.csv`, turnId: 'turn-wrapup-0', at: at(51) },
        { path: `${NOTES_API}/docs/old-plan.md`, turnId: 'turn-wrapup-0', at: at(51), missing: true },
      ],
      seq: wrapup.events.length,
    },
  };

  const dayKey = (minutesAgo: number) => {
    const offset = new Date(now).getTimezoneOffset() * 60_000;
    return new Date(now - minutesAgo * 60_000 - offset).toISOString().slice(0, 10);
  };
  const today = dayKey(30);
  const yesterday = dayKey(60 * 26);
  const activity: DemoData['activity'] = [
    {
      date: yesterday,
      turns: 2,
      threads: 2,
      morePrompts: 0,
      prompts: [
        { threadId: 'demo-weekly', turnId: 'turn-weekly-1', ts: at(60 * 26), text: '整理上周的提交记录，写成周报发到讨论区。', title: '上周周报', agent: 'pi', cwd: NOTES_API, archived: false, tools: 5 },
        { threadId: 'demo-routes', turnId: 'turn-routes-1', ts: at(127), text: '想把接口迁到新的路由写法，先帮我看看现在哪些地方用了旧的。', title: '迁移到新的路由写法', agent: 'codex', cwd: NOTES_API, archived: false, tools: 9 },
      ],
      tools: [
        { name: 'shell', count: 6, mcp: false },
        { name: 'apply_patch', count: 4, mcp: false },
        { name: 'read_file', count: 3, mcp: false },
        { name: 'github · search_issues', count: 2, mcp: true, server: 'github' },
      ],
      mcp: [{ server: 'github', count: 2 }],
    },
    {
      date: today,
      turns: 3,
      threads: 3,
      morePrompts: 0,
      prompts: [
        { threadId: 'demo-scroll-fix', turnId: 'turn-scroll-1', ts: at(8), text: '商品列表往下滚的时候会跳一下，帮忙看看是哪里的问题。', title: '修复商品列表滚动跳动', agent: 'claude', cwd: STOREFRONT, archived: false, tools: 6 },
        { threadId: 'demo-tests', turnId: 'turn-tests-1', ts: at(19), text: '给 /users 接口补上缺的测试。', title: '补齐 /users 接口测试', agent: 'codex', cwd: NOTES_API, archived: false, tools: 4 },
        { threadId: 'demo-dark-mode', turnId: 'turn-dark-1', ts: at(41), text: '设置页加一个深色模式开关，默认跟随系统。', title: '设置页加深色模式', agent: 'codex', cwd: STOREFRONT, archived: false, tools: 3 },
      ],
      tools: [
        { name: 'mcp__figma__get_frame', count: 3, mcp: true, server: 'figma' },
        { name: 'Bash', count: 3, mcp: false },
        { name: 'Edit', count: 3, mcp: false },
        { name: 'Read', count: 2, mcp: false },
      ],
      mcp: [{ server: 'figma', count: 3 }],
    },
  ];
  const systemPrompts: DemoData['systemPrompts'] = {
    'demo-routes': {
      text: 'You are Codex, an agent working in the user\'s workspace.\n\n# 计划模式\n这是示例的节选。真实的系统提示词有几万字符，包含身份、工具说明、沙箱规则和挂载的 MCP 说明；hub 只把它原样读出来，不改动。\n\n# Tools\n- shell, apply_patch, read_file, web_search',
      source: 'Codex 会话文件',
    },
    'demo-dark-mode': {
      text: 'You are Codex, an agent working in the user\'s workspace.\n\n# 急救\n这是一个示例提示词。真实的系统提示词由 agent 自己写进会话文件，hub 只读出来给你看：它包含身份、工具说明和这个会话挂载的 MCP 说明，通常有几千字。\n\n# Tools\n- shell: run commands\n- apply_patch: edit files',
      source: 'Codex 会话文件',
    },
    'demo-scroll-fix': {
      text: 'You are Claude Code, Anthropic\'s official CLI for Claude.\n\n# 示例\n同样是节选：真实提示词里还有环境信息、git 状态和 MCP 工具的完整说明。\n\n# Tools\n- Read, Edit, Bash, Grep…',
      source: 'Claude Code 会话文件',
    },
  };

  const sec = (minutesAgo: number, seconds: number) => new Date(now - minutesAgo * 60_000 + seconds * 1000).toISOString();
  const traces: DemoData['traces'] = {
    'demo-scroll-fix': {
      threadId: 'demo-scroll-fix',
      turnId: 'turn-scroll-1',
      title: '修复商品列表滚动跳动',
      agent: 'claude',
      cwd: STOREFRONT,
      models: ['claude-sonnet-5-5'],
      status: 'done',
      startedAt: sec(8, 0),
      endedAt: sec(8, 74),
      totals: { tools: 6, mcp: 1, requests: 4, input: 61_240, output: 2_310 },
      turns: [{ turnId: 'turn-scroll-1', ts: sec(8, 0) }],
      steps: [
        { kind: 'user', ts: sec(8, 0), text: '商品列表往下滚的时候会跳一下，帮忙看看是哪里的问题。' },
        { kind: 'request', ts: sec(8, 1), endTs: sec(8, 4), model: 'claude-sonnet-5-5', tokens: { input: 14_820, output: 310, cacheRead: 12_400 } },
        { kind: 'thinking', ts: sec(8, 2), endTs: sec(8, 4), text: '滚动跳动多半是图片加载后撑开了高度，或者列表在滚动时重新测量了行高。先看列表组件和图片有没有固定尺寸。' },
        { kind: 'tool', ts: sec(8, 4), endTs: sec(8, 5), tool: 'Grep', title: 'ProductList', status: 'completed', input: '{\n  "pattern": "ProductList",\n  "path": "src"\n}', output: 'src/components/ProductList.tsx\nsrc/pages/Home.tsx' },
        { kind: 'tool', ts: sec(8, 5), endTs: sec(8, 6), tool: 'Read', title: 'src/components/ProductList.tsx', status: 'completed', output: 'export function ProductList({ items }) {\n  return items.map((item) => (\n    <article className="card">\n      <img src={item.image} />\n      …' },
        { kind: 'tool', ts: sec(8, 7), endTs: sec(8, 11), tool: 'mcp__figma__get_frame', server: 'figma', title: '商品卡片 / 列表', status: 'completed', output: '{ "width": 343, "height": 412, "image": { "aspectRatio": "4 / 5" } }' },
        { kind: 'request', ts: sec(8, 12), endTs: sec(8, 19), model: 'claude-sonnet-5-5', tokens: { input: 16_930, output: 820, cacheRead: 14_800 } },
        { kind: 'assistant', ts: sec(8, 19), endTs: sec(8, 20), text: '图片没有固定宽高比，加载完成后卡片变高，把下面的内容往下推。按设计稿给图片加上 4:5 的宽高比。' },
        { kind: 'tool', ts: sec(8, 20), endTs: sec(8, 22), tool: 'Edit', title: 'src/components/ProductList.tsx', status: 'completed', output: '已修改 1 处' },
        { kind: 'tool', ts: sec(8, 23), endTs: sec(8, 61), tool: 'Bash', title: 'npm test -- ProductList', status: 'completed', input: '{\n  "command": "npm test -- ProductList"\n}', output: ' PASS  src/components/ProductList.test.tsx\n  ✓ keeps its height while images load (38 ms)\n\nTests: 4 passed, 4 total' },
        { kind: 'request', ts: sec(8, 62), endTs: sec(8, 74), model: 'claude-sonnet-5-5', tokens: { input: 29_490, output: 1_180, cacheRead: 27_100 } },
        { kind: 'assistant', ts: sec(8, 70), endTs: sec(8, 74), text: '已修复：给商品图加了 `aspect-ratio: 4 / 5`，图片加载前就占好位置，滚动不再跳。测试 4 个全部通过。' },
      ],
    },
  };

  // The session behind the example thread, as the stats endpoint would answer it: twelve turns of
  // real-looking work, one of them (an import) without usage data. Times are hub-read, so ≈.
  const stats: DemoData['stats'] = {
    'demo-scroll-fix': {
      threadId: 'demo-scroll-fix',
      checkedAt: new Date(now).toISOString(),
      running: false,
      turns: 12,
      steps: 86,
      timing: { source: 'hub', turns: 12, segments: 38, waitMs: 64_800, avgWaitMs: 1_800, genMs: 517_200, modelMs: 582_000, toolMs: 845_000 },
      tokens: { turnsWithout: 1, input: 51_362_118, cacheRead: 50_712_904, cacheWrite: 61_322, output: 191_864, reasoning: 40_112, total: 51_553_982 },
      tokPerSec: 74.2,
      tokPerSecApprox: true,
    },
    // The lead's session: longer, mostly cache reads, so the panel's footer has a full line.
    'demo-wrapup': {
      threadId: 'demo-wrapup',
      checkedAt: new Date(now).toISOString(),
      running: true,
      turns: 39,
      steps: 210,
      timing: { source: 'hub', turns: 39, segments: 96, waitMs: 171_600, avgWaitMs: 1_760, genMs: 1_404_000, modelMs: 1_575_600, toolMs: 2_730_000 },
      tokens: { turnsWithout: 2, input: 40_020_000, cacheRead: 38_820_000, cacheWrite: 142_000, output: 180_000, reasoning: 31_200, total: 40_200_000 },
      tokPerSec: 1740,
      tokPerSecApprox: true,
    },
  };

  return {
    agents: AGENTS,
    config: {
      workdirs: [STOREFRONT, NOTES_API],
      roots: [
        { path: STOREFRONT, removable: false },
        { path: NOTES_API, removable: false },
      ],
      scratch: true,
    },
    threads,
    sections,
    todos,
    schedules,
    coordinations,
    models: MODELS,
    events: {
      'demo-wrapup': wrapup.events,
      ...wrapupEvents,
      'demo-dark-mode': dark.events,
      'demo-scroll-fix': scroll.events,
      'demo-routes': routes.events,
      'demo-filters': filters.events,
      'demo-weekly': weekly.events,
      'demo-tests': tests.events,
    },
    folders,
    files,
    outputs,
    activity,
    systemPrompts,
    traces,
    stats,
  };
}
