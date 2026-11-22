/**
 * The made-up workspace the page shows before it is connected to a hub: a few threads in two
 * projects, a plan and a scheduled task. Events have the daemon's own shapes, so the real
 * timeline draws them. Times are relative to when the page opened.
 *
 * Nothing here is anyone's real project, path or machine.
 */
import { CLAUDE_ICON, CODEX_ICON, PI_ICON } from '../daemon-shared/icons';
import type { ActivityDay, AgentInfo, FolderListing, ModelOption, Schedule, Section, SystemPrompt, TextFile, Thread, Todo, TurnTrace, WorkdirsConfig } from '../api';
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
  /** Folder listings by thread directory and path inside it ('' for the top). */
  folders: Record<string, FolderListing['entries']>;
  files: Record<string, string>;
  /** The daily digest, as the activity endpoint would answer it. */
  activity: ActivityDay[];
  /** The whole prompt of a conversation, by thread, for the demo's system-prompt dialog. */
  systemPrompts: Record<string, SystemPrompt>;
  /** Turn traces by thread, as the trace endpoint would answer for its last turn. */
  traces: Record<string, TurnTrace>;
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
    dark.add(turn, at(40), 'tool.start', {
      id: 'c2',
      title: 'src/theme.ts',
      kind: 'edit',
      content: [{ type: 'diff', path: `${STOREFRONT}/src/theme.ts`, oldText: THEME_BEFORE, newText: THEME_AFTER }],
    });
    dark.add(turn, at(40), 'tool.end', { id: 'c2', status: 'completed' });
    dark.add(turn, at(40), 'tool.start', {
      id: 'c3',
      title: 'src/pages/Settings.tsx',
      kind: 'edit',
      content: [{ type: 'diff', path: `${STOREFRONT}/src/pages/Settings.tsx`, oldText: SETTINGS_BEFORE, newText: SETTINGS_AFTER }],
    });
    dark.add(turn, at(40), 'tool.end', { id: 'c3', status: 'completed' });
    dark.add(turn, at(39), 'tool.start', { id: 'c4', title: 'npm test', kind: 'execute', input: { command: 'npm test' } });
    dark.add(turn, at(38), 'tool.end', { id: 'c4', status: 'completed', output: '✓ theme.test.ts (6)\n✓ settings.test.tsx (11)\n\nTest Files  2 passed\nTests  17 passed' });
    dark.add(turn, at(38), 'message.delta', {
      text: [
        '加好了。设置页「语言」下面多了一行「外观」，三个选项：**跟随系统**（默认）、浅色、深色。',
        '',
        '改动：',
        '',
        '- `src/theme.ts`：新增 `darkTokens`，`applyTheme` 接受用户的选择，没选时看系统设置。',
        '- `src/pages/Settings.tsx`：加了分段选择器，选择保存在本机浏览器。',
        '',
        '```ts',
        "applyTheme(document.documentElement, loadThemeChoice() ?? 'system');",
        '```',
        '',
        '测试 17 个全部通过。系统切换深浅色时页面会跟着变，不用刷新。',
      ].join('\n'),
    });
    dark.add(turn, at(38), 'usage.updated', { used: 46_000, size: 258_000 });
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

  const sections: Section[] = [{ id: 'demo-reports', name: '周报', position: 0 }];

  const threads: Thread[] = [
    thread({ id: 'demo-scroll-fix', agent: 'claude', cwd: STOREFRONT, title: '修复商品列表滚动跳动', status: 'running', waiting: true, waitingReason: 'approval', preview: '商品列表往下滚的时候会跳一下', model: 'opus' }, 6),
    thread({ id: 'demo-tests', agent: 'codex', cwd: NOTES_API, title: '补齐 /users 接口测试', unread: true, preview: '给 /users 接口补上缺的测试。', report: { stage: 'review', note: '补了 12 个用例，本地全部通过', at: at(16) } }, 16),
    thread({ id: 'demo-dark-mode', agent: 'codex', cwd: STOREFRONT, title: '设置页加深色模式', preview: '设置页加一个深色模式开关', model: 'gpt-5.5', effort: 'high', doneAt: at(30) }, 38),
    thread({ id: 'demo-routes', agent: 'codex', cwd: NOTES_API, title: '迁移到新的路由写法', workMode: 'plan', preview: '想把接口迁到新的路由写法' }, 124),
    thread({ id: 'demo-weekly', agent: 'pi', cwd: NOTES_API, title: '上周周报', sectionId: 'demo-reports', permissionMode: 'full_access', preview: '整理上周的提交记录', schedule: { id: 'demo-schedule', name: '每周一周报' } }, 60 * 26 - 1),
  ];
  const byId = (id: string) => threads.filter((t) => t.id === id);

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
    models: MODELS,
    events: {
      'demo-dark-mode': dark.events,
      'demo-scroll-fix': scroll.events,
      'demo-routes': routes.events,
      'demo-weekly': weekly.events,
      'demo-tests': tests.events,
    },
    folders,
    files,
    activity,
    systemPrompts,
    traces,
  };
}
