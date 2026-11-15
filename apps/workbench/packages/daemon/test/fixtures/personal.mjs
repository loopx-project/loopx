import { execFileSync } from 'node:child_process';
import { readFileSync, mkdirSync, writeFileSync, unlinkSync, existsSync } from 'node:fs';
import { join } from 'node:path';
let router = false;
/** A portable coding agent needs only shell execution, its scoped env and the prompt. */
export function personalFixture(text) {
  const context = text.match(/^<calli-context>\n([\s\S]*?)\n<\/calli-context>\n\n/);
  const command = text.slice(context?.[0].length ?? 0).split('\n')[0];
  if (text.includes('<calli-personal-router>')) router = true;
  if (command.startsWith('dispatch-worker-')) {
    if (!text.includes('不推远端、不给别人发消息、不删除、不付款')) throw new Error('missing dispatch boundary');
    if (command === 'dispatch-worker-review') return '结果已整理，等你检查。\n[进度] 待验收：请检查测试结果';
    if (command === 'dispatch-worker-answer') return '请选择 A 还是 B？\n[进度] 需要你：请选择 A 还是 B？';
    return '已查清第一部分，下一轮继续。\n[进度] 进行中：继续核对';
  }
  if (router && command === 'router-fixture-fail' && existsSync('.calli/router-retry-ready')) return JSON.stringify({ intro: text.includes('<calli-personal-router>'), profile: text.includes('<calli-personal-profile>') });
  if (router && ['router-fixture-slow', 'router-fixture-fail'].includes(command)) return null;
  if (command === '由 Calli 发起的推进分流。') {
    if (text.includes('advance-invalid')) return 'not JSON';
    if (text.includes('advance-done')) return JSON.stringify({ phase: 'done', line: '测试和实现已做好' });
    if (text.includes('advance-user')) return JSON.stringify({ phase: 'blocked', on: 'user', line: '等你选方案', question: { text: '请选择 A 还是 B？', options: ['A', 'B'] } });
    if (text.includes('advance-external')) return JSON.stringify({ phase: 'blocked', on: 'external', line: '等 CI', wait: { what: 'CI 结束', then: '检查结果', checkMinutes: 10 } });
    return JSON.stringify({ phase: 'unsure', line: '' });
  }
  if (command === '由 Calli 发起的事前核对。') {
    // Cites the session titled "precheck-evidence" when the matter asks for a done or doing verdict.
    const data = JSON.parse(text.slice(text.lastIndexOf('\n{') + 1));
    const evidence = data.sessions.find(session => session.title === 'precheck-evidence');
    if (data.matter.includes('precheck-bogus')) return JSON.stringify({ state: 'done', evidence: '编造的证据', source: 'session:missing' });
    for (const state of ['done', 'doing']) if (data.matter.includes(`precheck-${state}`) && evidence) return JSON.stringify({ state, evidence: `会话 ${evidence.title} 已处理`, source: `session:${evidence.id}` });
    return JSON.stringify({ state: 'todo', evidence: '' });
  }
  const proactive = ['由 Calli 发起的提前准备。', '由 Calli 发起的沙盒动手。'].includes(command);
  const explore = command === '由 Calli 发起的一键配置探索。';
  const clarification = process.env.FAKE_PERSONAL_DELEGATE_CLARIFY === '1' && command.startsWith('这是一件新交给你的事，不是交接');
  const review = process.env.FAKE_PERSONAL_REVIEW && (command === '由 Calli 发起的私人事项盘点。' || command.startsWith('开始 '));
  if (!router && !review && !clarification && !proactive && !explore && !['personal-ui', 'personal-start', 'personal-notify', 'personal-quiet', 'personal-wish', '由 Calli 发起的事项跟进。', '由 Calli 发起的近期总结。', '# 交接信息', '# 接手'].includes(command)) return null;
  // A handover first turn, in the old coordinate format or the clean brief, carrying open todos.
  if ((command === '# 交接信息' || command === '# 接手') && !text.includes('## 关联事项与接续说明')) return null;
  if (explore) {
    // The onboarding explore's one judgement. 'hold' never settles, so a cancellation can be observed.
    const mode = process.env.FAKE_PI_EXPLORE_MODE;
    if (mode === 'hold') return '__hold__';
    if (mode === 'error') return '__explore_error__';
    if (mode === 'invalid') return '抱歉，我没能按格式回答。';
    return process.env.FAKE_PI_EXPLORE_REPLY ?? JSON.stringify({ peopleRoles: [], why: {}, profileDraft: '' });
  }
  const cli = (args, input = '') => JSON.parse(execFileSync(process.execPath, [join(process.env.CALLI_BIN_DIR, 'calli'), ...args], { input, encoding: 'utf8' }));
  if (proactive) {
    const id = text.match(/^ID：(.+)$/m)?.[1];
    if (!id) throw new Error('missing proactive matter id');
    if (process.env.FAKE_PROACTIVE_MODE === 'error') throw new Error('fixture proactive failed');
    if (process.env.FAKE_PROACTIVE_MODE === 'quiet') return '没有值得准备的材料。';
    if (command === '由 Calli 发起的提前准备。') {
      for (const instruction of ['只写材料目录下的文件', '不发消息', '不改外部仓库', '已备好', '不要自行 git 提交']) {
        if (!text.includes(instruction)) throw new Error(`missing preparation instruction: ${instruction}`);
      }
      const dir = text.match(/^材料目录：(.+)$/m)?.[1];
      if (!dir || dir.startsWith('/') || dir.includes('..')) throw new Error('invalid artifact path');
      mkdirSync(dir, { recursive: true });
      const plan = text.includes('本轮是笼统事项拆解');
      writeFileSync(join(dir, plan ? 'plan.md' : 'outline.md'), plan ? '# 拆解与第一步方案\n\n1. 读取现状\n2. 确认方案\n3. 实现验证\n' : '# 会前提纲\n\n先核对目标，再列决策。\n');
      const todo = cli(['todo', 'show', id]);
      cli(['todo', 'update', id, '--rev', String(todo.rev), '--note-file', '-'], `${todo.note.text}\n${plan ? '已备好 拆解与第一步方案' : '已备好会前提纲。'}`);
      return plan ? '已备好 拆解与第一步方案。' : '已备好会前提纲，列出目标和待决策问题。';
    }
    for (const instruction of [`Calli-Proactive: ${id}`, '跑该仓库测试', '不动主工作区', '不推送远端']) {
      if (!text.includes(instruction)) throw new Error(`missing sandbox instruction: ${instruction}`);
    }
    writeFileSync('proactive-fixture.txt', 'sandbox change\n');
    const git = args => execFileSync('git', args, { encoding: 'utf8' });
    git(['add', '--', 'proactive-fixture.txt']);
    git(['-c', 'user.name=Calli', '-c', 'user.email=calli@localhost', '-c', 'commit.gpgsign=false', 'commit', '-qm', `Prepare fixture\n\nCalli-Proactive: ${id}`]);
    return '## 改动摘要\n补好沙盒示例。\n## 测试结果\nfixture check: passed';
  }
  if (router) {
    if (command.startsWith('router-fixture-markdown ')) return JSON.parse(command.slice('router-fixture-markdown '.length));
    if (process.env.FAKE_PERSONAL_BOARD_LINKS === '1' && text.includes('#/thread/')) return [...text.matchAll(/\[[^\]]+\]\(#\/thread\/[^)]+\)/g)].map(match => match[0]).join('\n');
    if (command === '<calli-worker-report>') { const report = JSON.parse(text.split('\n').find(line => line.startsWith('{"matterId"'))); if (report.stage !== 'progress' && !report.notified && !report.pushed) cli(['notify', '--text-file', '-'], `「${report.title}」要你看一下`); return `「${report.title}」要你看一下`; }
    if (command === 'router-fixture-context') return JSON.stringify({ context: context?.[1] ?? null });
    if (command.startsWith('router-fixture-plan')) return JSON.stringify(cli(['plan', '--range', command.split(' ')[1] || 'day']));
    if (command.startsWith('router-fixture-say ')) {
      const input = JSON.parse(command.slice('router-fixture-say '.length));
      return JSON.stringify(cli(['threads', 'say', input.id, '--text-file', '-'], input.text));
    }
    if (command.startsWith('router-fixture-dispatch ')) {
      const input = JSON.parse(command.slice('router-fixture-dispatch '.length));
      const flags = ['dispatch', '--agent', input.agent, '--cwd', input.cwd, '--task-file', '-'];
      for (const key of ['matter', 'title', 'model']) if (input[key]) flags.push(`--${key}`, input[key]);
      if (input.now) flags.push('--now');
      return JSON.stringify(cli(flags, input.task));
    }
    if (command === 'router-fixture-inspect') return JSON.stringify({ intro: text.includes('<calli-personal-router>'), profile: text.includes('<calli-personal-profile>') });
    if (command === '把早报改到八点半') {
      if (!text.includes('只有人在当前对话里开口要求，才能改')) throw new Error('missing settings permission rule');
      if (!text.includes('不主动建议改设置')) throw new Error('missing settings restraint rule');
      const reply = cli(['settings', 'set', 'brief.morning', '08:30']);
      if (reply.changed !== 'brief.morning') throw new Error('unexpected settings reply');
      return `已把早报改到 ${reply.settings.morning}。`;
    }
    if (command === '看看现在关注了谁') {
      const list = cli(['settings']);
      return `当前关注：${list.lark.people.map(person => person.name).join('、') || '无'}。`;
    }
    if (command === '<calli-lark-signals>') {
      const signals = text.split('\n').filter(line => line.startsWith('{"summary":')).map(line => JSON.parse(line));
      // Let fake-pi's normal failure protocol run until the retry fixture is released.
      if (signals.some(signal => signal.source.ref === 'router-fixture-fail') && !existsSync('.calli/router-retry-ready')) return null;
      let count = 0;
      for (const signal of signals) {
        if (cli(['todo']).some(todo => todo.source?.ref === signal.source.ref)) continue;
        mkdirSync('.calli', { recursive: true });
        const file = join('.calli', 'fixture-source.json');
        writeFileSync(file, JSON.stringify(signal.source));
        try {
          const roleReason = signal.senderRole === 'boss' ? '老板提的' : signal.senderRole === 'pm' ? 'PM 提的' : null;
          const importance = roleReason || signal.source.type === 'lark-task' ? 'high' : 'normal';
          const todo = cli(['todo', 'add', '--owner', 'me', '--lane', 'you', '--importance', importance, '--source-file', file,
            ...(signal.dueAt ? ['--due', signal.dueAt] : []), ...(signal.urgentUntil ? ['--urgent-until', signal.urgentUntil] : []), '--text-file', '-'], signal.summary);
          cli(['todo', 'update', todo.id, '--rev', String(todo.rev), '--note-file', '-'], `## 下一步\n请确认是否跟踪。\n## 轻重理由\n${roleReason ?? signal.reason ?? '分给你的任务，需要你确认。'}`);
          count++;
        } finally { unlinkSync(file); }
      }
      return `发现 ${count} 件与你有关的事，已放进新发现，等你决定。`;
    }
    if (command === 'router-fixture-long') return '鱼'.repeat(350);
    if (command.startsWith('记一件事：')) {
      const name = command.slice('记一件事：'.length).trim();
      cli(['todo', 'add', '--owner', 'me', '--lane', 'you', '--text-file', '-'], name);
      return `已记下「${name}」，在新发现里点「跟踪」就能放上桌面。`;
    }
    return `收到「${command}」。要开始时告诉我，我再交给 agent。`;
  }
  if (review) {
    if (command.startsWith('开始 ')) return JSON.stringify(cli(['todo', 'delegate', command.slice('开始 '.length)]));
    if (process.env.FAKE_PERSONAL_REVIEW === 'quiet') { cli(['quiet']); return 'Nothing needs attention.'; }
    const todo = cli(['todo']).find(item => !item.done && !item.proposed);
    cli(['notify', '--text-file', '-'], `要开始「${todo.text}」吗？`);
    cli(['review', 'next', new Date(Date.now() + 3600000).toISOString()]);
    return `等待你说「开始 ${todo.id}」。`;
  }
  if (clarification) {
    const id = text.match(/^ID：([^；]+)；/m)?.[1];
    const todo = cli(['todo', 'show', id]);
    if (todo.note.text.trim()) throw new Error('clarification fixture expects no continuation note');
    if (todo.threads.filter(thread => thread.self).length !== 1) throw new Error('current work thread must be marked self');
    for (const instruction of ['1～3 个具体问题', '默认选项', 'waiting', '等你说明目标', 'calli notify', '然后结束本轮', '不要翻本地会话文件']) {
      if (!text.includes(instruction)) throw new Error(`missing clarification instruction: ${instruction}`);
    }
    cli(['todo', 'update', id, '--rev', String(todo.rev), '--state', 'waiting', '--waiting-on', '等你说明目标', '--note-file', '-'],
      `## 欠谁什么\n帮你明确「${todo.text}」的目标，具体交付尚未确定\n## 定了什么\n先问清目标\n## 做到哪\n已提出澄清问题，尚未开展任务\n## 下一步\n等回答后明确交付形式与范围，再按确认的目标推进`);
    cli(['notify', '--text-file', '-'], `「${todo.text}」需要你回答目标和交付形式后再推进。`);
    return '1. 你更关心哪一类架构变化？建议默认：当前项目使用的 Agent 框架。\n2. 希望我交付什么？建议默认：一页变化摘要和对当前项目的影响。';
  }
  if (command === '由 Calli 发起的近期总结。') {
    // Follows the summary prompt with the generic commands only, grouping threads by directory.
    const since = text.match(/calli threads --since ([0-9T:.Z+-]+)/)[1];
    const heading = text.match(/文字是「([^」]+)」的事项/)[1];
    const recent = cli(['threads', '--since', since]).threads.filter(thread => !thread.self);
    if (recent[0]) cli(['threads', 'show', recent[0].id, '--turns', '1']);
    const parent = cli(['todo']).find(todo => todo.text === heading && !todo.parentId) ?? cli(['todo', 'add', '--owner', 'me', '--text-file', '-'], heading);
    const groups = new Map();
    for (const thread of recent.filter(thread => !thread.todos.some(todo => todo.parentId === parent.id))) {
      const name = `${thread.cwd.split('/').pop()} 的工作`;
      groups.set(name, [...(groups.get(name) ?? []), thread.id]);
    }
    const children = cli(['todo']).filter(todo => todo.parentId === parent.id);
    for (const [name, ids] of groups) {
      const existing = children.find(todo => todo.text === name);
      const links = ids.flatMap(id => ['--thread', id]);
      if (existing) cli(['todo', 'update', existing.id, '--rev', String(existing.rev), ...links]);
      else cli(['todo', 'add', '--owner', 'me', '--parent', parent.id, ...links, '--text-file', '-'], name);
    }
    const current = cli(['todo', 'show', parent.id]);
    const titles = done => recent.filter(thread => thread.done === done).map(thread => thread.title).join('、') || '无';
    cli(['todo', 'update', parent.id, '--rev', String(current.rev), '--note-file', '-'], `## 做完了什么\n${titles(true)}\n## 还没做完的\n${titles(false)}\n## 需要你决定的\n无`);
    cli(['quiet']);
    return [...groups.keys()].map(name => `- ${name}`).join('\n') || '没有新的会话。';
  }
  if (command === 'personal-ui') {
    const list = cli(['todo']);
    for (const todo of list.filter(item => item.text === '周五前交报销' || item.text === '确认合同修改')) {
      cli(['todo', 'propose-done', todo.id, '--rev', String(todo.rev), '--evidence-file', '-'], '已核对凭证，等待你确认。');
    }
    for (const text of ['预约护照续签', '查收快递']) cli(['todo', 'add', '--owner', 'me', '--text-file', '-'], text);
    const todo = cli(['todo', 'add', '--owner', 'agent', '--text-file', '-'], '核对报销凭证');
    cli(['todo', 'update', todo.id, '--rev', String(todo.rev), '--note-file', '-'], '## 欠谁什么\n给你整理好报销凭证\n## 定了什么\n只处理本月凭证\n## 做到哪\n已收齐电子发票\n## 下一步\n核对金额与日期');
    cli(['wish', '--text-file', '-'], '顺路看看周末的展览');
    cli(['notify', '--text-file', '-'], '凭证已整理好，等你确认。');
    return '私人计划已整理。';
  }
  if (command === 'personal-start') {
    const todo = cli(['todo', 'add', '--owner', 'me', '--text-file', '-'], 'A lasting commitment');
    const updated = cli(['todo', 'update', todo.id, '--rev', String(todo.rev), '--note-file', '-', '--follow-up', new Date(Date.now() + 3600000).toISOString()], '## 欠谁什么\nFinish the report\n## 定了什么\nKeep it short\n## 做到哪\nDraft ready\n## 下一步\nReview the draft');
    return JSON.stringify(updated);
  }
  if (command === 'personal-notify') { cli(['notify', '--text-file', '-'], 'Invisible notice fixture'); return 'Notice recorded.'; }
  if (command === 'personal-quiet') { cli(['quiet']); return 'Nothing needs attention.'; }
  if (command === 'personal-wish') { cli(['wish', '--text-file', '-'], 'Optional idea'); cli(['quiet']); return 'Saved an optional idea.'; }
  const list = cli(['todo']);
  const id = list.find(item => text.includes(item.id))?.id;
  const todo = cli(['todo', 'show', id]);
  if (command === '由 Calli 发起的事项跟进。' && process.env.FAKE_PERSONAL_FOLLOW_UP_MODE_FILE) {
    const mode = readFileSync(process.env.FAKE_PERSONAL_FOLLOW_UP_MODE_FILE, 'utf8').trim();
    cli(['todo', 'update', id, '--rev', String(todo.rev), '--follow-up', new Date(Date.now() + 3600000).toISOString(),
      ...(mode === 'progress' ? ['--note-file', '-'] : [])], `${todo.note.text}\nProgress made`);
    if (mode === 'error') throw new Error('fixture follow-up failed');
    if (mode !== 'done') cli(['quiet']);
    return 'Follow-up recorded.';
  }
  if (todo.proposals.some(p => p.kind === 'done' && p.state === 'open')) { cli(['quiet']); return 'Already awaiting confirmation.'; }
  if (!todo.note.text.includes('Draft ready')) throw new Error('continuation note missing');
  const next = cli(['todo', 'update', id, '--rev', String(todo.rev), '--note-file', '-'], '## 欠谁什么\nFinish the report\n## 定了什么\nKeep it short\n## 做到哪\nReviewed the draft\n## 下一步\nAwait confirmation');
  const proposal = cli(['todo', 'propose-done', id, '--rev', String(next.rev), '--evidence-file', '-'], 'The draft has been reviewed.');
  return JSON.stringify(proposal);
}
