import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdtempSync, mkdirSync, existsSync, readFileSync, writeFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { ZcodeAdapter, ZCODE_BASIC_TOOLS } from '../src/adapters/zcode.js';
import type { AgentSession, TurnHooks } from '../src/adapters/types.js';

// Opt-in: an unmodified installed CLI, isolated storage, and a local model endpoint. No real API keys.
test('real zcode: images, memory isolation, goals and native plan approval', { skip: !process.env.CALLI_ZCODE_REAL_BIN, timeout: 60_000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-zcode-real-'));
  const requests: any[] = [];
  let goalMode = false;
  let goalReplies = 0;
  let usageProbe = false;
  const toolCalls: Array<{ name: string; args: Record<string, unknown> }> = [];
  const nativeModes: string[] = [];
  const server = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    requests.push(JSON.parse(body));
    if (usageProbe && goalMode && goalReplies === 1) await new Promise(r => setTimeout(r, 2600));
    const streaming = JSON.parse(body).stream === true;
    const verification = goalMode && body.includes('Verify whether the active session goal is actually complete.');
    const content = verification ? JSON.stringify({ passed: goalReplies >= 2, reason: 'Local test verdict', nextAction: goalReplies >= 2 ? '' : 'Reply once more.' }) : '本地验证完成';
    if (goalMode && !verification && streaming) goalReplies++;
    if (!streaming) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ id: `fixture-${requests.length}`, object: 'chat.completion', model: 'fixture', choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } }));
      return;
    }
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    const tool = toolCalls.shift();
    if (tool) {
      for (const chunk of [
        { choices: [{ index: 0, delta: { role: 'assistant', tool_calls: [{ index: 0, id: `call-${requests.length}`, type: 'function', function: { name: tool.name, arguments: JSON.stringify(tool.args) } }] }, finish_reason: null }] },
        { choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } },
      ]) res.write(`data: ${JSON.stringify({ id: `fixture-${requests.length}`, object: 'chat.completion.chunk', created: 1, model: 'fixture', ...chunk })}\n\n`);
      res.end('data: [DONE]\n\n'); return;
    }
    for (const chunk of [
      { choices: [{ index: 0, delta: { role: 'assistant', content }, finish_reason: null }] },
      { choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } },
    ]) res.write(`data: ${JSON.stringify({ id: `fixture-${requests.length}`, object: 'chat.completion.chunk', created: 1, model: 'fixture', ...chunk })}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  let session: AgentSession | undefined;
  try {
    await new Promise<void>((resolve) => server.listen(0, 'localhost', resolve));
    const port = (server.address() as { port: number }).port;
    const modelConfig = {
      enabled: true,
      properties: { requiresMfjsToolSchema: false, contextWindow: 128000, inputFormat: { supportsText: true, supportsImage: true, supportsVideo: false, supportsAudio: false, supportsPdf: false }, outputFormat: { supportsText: true }, supportsToolCall: true, supportsJsonSchemaOutput: false, supportsNativeWebSearch: false, supportsMidConversationSystem: false },
      optionSpecs: { reasoningLevel: { values: ['off'], map: '{}' }, maxOutputTokens: { max: 4096, map: '{}' } },
    };
    writeFileSync(join(dir, 'builtin.json'), JSON.stringify({ schemaVersion: 1, revision: 1, config: {
      providerConfigRules: { providerRules: [], templateRules: [] },
      modelConfigRules: { modelRules: [{ modelMatch: '.*', config: modelConfig }], modelApiRules: [], providerSiteRules: [], templateModelRules: [], builtinProviderModelRules: [] },
    } }));
    writeFileSync(join(dir, 'personal.json'), JSON.stringify({ schemaVersion: 1, config: {
      providerConfigRules: { providerRules: [{ providerId: 'fixture', providerName: 'Fixture', enabled: true, config: { group: 'standard-personal', access: { type: 'api-key', apiKey: 'local-fixture-key' }, api: { type: 'openai-chat-completions', baseUrl: `http://localhost:${port}/v1` }, personalModelIds: ['fixture'] } }] },
      modelConfigRules: { providerModelRules: [], manualProviderModelRules: [] },
      defaultModelSelection: { providerId: 'fixture', modelId: 'fixture', options: { reasoningLevel: 'off' } },
    } }));
    mkdirSync(join(dir, '.zcode'));
    const projectConfig = JSON.stringify({ features: { memory: true }, memory: { use: true } });
    writeFileSync(join(dir, '.zcode/config.json'), projectConfig);
    const memory = join(dir, 'cli/memories/projects', `${basename(dir).toLowerCase()}-${createHash('sha256').update(dir).digest('hex').slice(0, 16)}`, 'memory');
    mkdirSync(memory, { recursive: true });
    const sentinel = 'MEMORY_SENTINEL_MUST_NOT_BE_INJECTED';
    writeFileSync(join(memory, 'MEMORY.md'), sentinel);
    const adapter = new ZcodeAdapter([process.env.CALLI_ZCODE_REAL_BIN!, 'app-server'], {
      ZCODE_DATA_BASE_DIR: dir, ZCODE_STORAGE_DIR: dir, ZCODE_SESSION_DB_PATH: join(dir, 'sessions.db'),
      ZCODE_BUILTIN_PROVIDER_CONFIG_FILE: join(dir, 'builtin.json'), ZCODE_PERSONAL_PROVIDER_CONFIG_FILE: join(dir, 'personal.json'),
    });
    const models = await adapter.models();
    assert.equal(models.length, 1);
    assert.equal(models[0].images, true);
    const contextReadings: Array<{ used: number | null; size: number }> = [];
    const consumption: Array<Record<string, unknown>> = [];
    const hooks: TurnHooks = { emit: event => { if (event.type === 'usage.sample') consumption.push(event.data); if (event.type === 'usage.updated') contextReadings.push(event.data as { used: number | null; size: number }); }, requestApproval: async () => ({ cancelled: true }) };
    session = await adapter.start({ cwd: dir, model: models[0].id });
    const first = await session.prompt({ text: 'Reply hello.', images: [] }, hooks);
    assert.equal(first.stopReason, 'end_turn', JSON.stringify(first));
    assert.ok((contextReadings.at(-1)?.size ?? 0) > 0, 'Use the native effective window rather than guessing from model configuration');
    assert.ok((contextReadings.at(-1)?.used ?? 0) > 0, 'The native runtime must report measured context usage');
    assert.ok(consumption.length > 0, 'The native CLI reports cumulative consumption');
    assert.equal(consumption.at(-1)?.total, 15, 'Only this request is counted, not the current context');
    const id = session.agentSessionId;
    await session.close();
    session = await adapter.start({ cwd: dir, resumeId: id, onWorkMode: mode => nativeModes.push(mode) });
    assert.equal(session.agentSessionId, id);
    assert.equal((await session.prompt({ text: 'Reply hello again.', images: [] }, hooks)).stopReason, 'end_turn');
    assert.equal(consumption.at(-1)?.total, 15, 'Repeated input is still charged on the next request');
    const readingsBeforeCompact = contextReadings.length;
    assert.equal((await session.prompt({ text: '/compact', images: [] }, hooks)).stopReason, 'end_turn');
    assert.ok(contextReadings.length >= readingsBeforeCompact + 2);
    assert.ok((contextReadings.at(-1)?.size ?? 0) > 0);
    assert.ok((contextReadings.at(-1)?.used ?? 0) > 0, 'Compaction must publish the native post-compaction measurement');
    for (const text of ['/help', '/session', '/usage']) {
      let response = '';
      assert.equal((await session.prompt({ text, images: [] }, { ...hooks, emit: (event) => { if (event.type === 'message.delta') response += event.data.text; } })).stopReason, 'end_turn');
      assert.ok(response.length > 0);
      if (text === '/usage') assert.match(response, /总计：[0-9,]+ tokens/);
    }
    assert.equal(requests.length, 3, 'No extra title or memory extraction calls');
    const imageData = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
    const imagePath = join(dir, 'picture.png');
    writeFileSync(imagePath, Buffer.from(imageData, 'base64'));
    for (const text of ['Describe both images.', '']) {
      assert.equal((await session.prompt({ text, images: [
        { data: imageData, mimeType: 'image/png', path: imagePath },
        { data: imageData, mimeType: 'image/png' },
      ] }, hooks)).stopReason, 'end_turn');
      const user = requests.at(-1).messages.filter((message: any) => message.role === 'user').at(-1);
      const images = user.content.filter((block: any) => block.type === 'image_url');
      assert.equal(images.length, 2, 'Both local-path and inline images reach the upstream model');
      assert.ok(images.every((block: any) => block.image_url.url === `data:image/png;base64,${imageData}`));
    }

    goalMode = true; usageProbe = true;
    const liveReadings: Array<{ at: number; total: number }> = [];
    let goalText = '';
    const goalHooks: TurnHooks = { ...hooks, emit: (event) => { if (event.type === 'message.delta') goalText += event.data.text; if (event.type === 'usage.sample' && Number(event.data.total) > 0) liveReadings.push({ at: Date.now(), total: Number(event.data.total) }); } };
    assert.equal((await session.prompt({ text: '/goal Reply twice for the local protocol test.', images: [] }, goalHooks)).stopReason, 'end_turn');
    assert.ok(liveReadings.length >= 2, `Usage must arrive mid-goal: ${JSON.stringify({ liveReadings, goalReplies, requests: requests.length })}`);
    assert.ok(Date.now() - liveReadings[0].at > 100, 'The first usage report arrives before the native goal finishes');
    assert.ok(goalReplies >= 2, 'The native runtime must continue through multiple turns');
    assert.match(goalText, /报告已完成/);
    assert.equal((await session.prompt({ text: '/goal clear', images: [] }, goalHooks)).stopReason, 'end_turn');
    goalMode = false;
    await session.setWorkMode!('plan');
    const changedFile = join(dir, 'must-not-write.txt');
    toolCalls.push({ name: 'Write', args: { file_path: changedFile, content: 'unexpected mutation' } });
    assert.equal((await session.prompt({ text: 'Check native plan restrictions.', images: [] }, hooks)).stopReason, 'end_turn');
    assert.equal(existsSync(changedFile), false, 'Native Plan must prevent writes even with the tool allowlist');
    let questionsAsked = 0;
    toolCalls.push({ name: 'AskUserQuestion', args: { questions: [{ header: '范围', question: '先做哪一端？', options: [{ label: '手机', description: '先适配手机' }, { label: '桌面', description: '先适配桌面' }], multiSelect: false }] } });
    const planHooks: TurnHooks = { ...hooks, requestInput: async request => {
      questionsAsked++;
      return { answers: Object.fromEntries(request.questions.map(q => [q.id, [q.options[0].value]])) };
    } };
    assert.equal((await session.prompt({ text: 'Ask for a planning preference.', images: [] }, planHooks)).stopReason, 'end_turn');
    assert.equal(questionsAsked, 1, 'The real runtime must route AskUserQuestion to the client');
    toolCalls.push({ name: 'ExitPlanMode', args: { plan: '# 实施计划\n\n完成本地协议验证。' } });
    assert.equal((await session.prompt({ text: 'Present the plan for approval.', images: [] }, planHooks)).stopReason, 'end_turn');
    assert.equal(questionsAsked, 2, 'Plan approval must require an explicit client answer');
    assert.ok(nativeModes.includes('execute'), 'Native plan approval must publish the new mode');
    assert.equal(session.agentSessionId, id);
    await session.close(); session = undefined;
    assert.equal(JSON.stringify(requests).includes(sentinel), false);
    assert.equal(readFileSync(join(memory, 'MEMORY.md'), 'utf8'), sentinel);
    assert.deepEqual(readdirSync(memory), ['MEMORY.md']);
    assert.equal(readFileSync(join(dir, '.zcode/config.json'), 'utf8'), projectConfig);
    for (const request of requests) {
      if (!request.tools?.length) continue; // The native goal verifier cannot call tools.
      const tools = request.tools.map((tool: any) => tool.function.name);
      assert.ok(tools.length > 0);
      assert.ok(tools.every((tool: string) => ZCODE_BASIC_TOOLS.includes(tool)));
    }
  } finally {
    await session?.close();
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    rmSync(dir, { recursive: true, force: true });
  }
});
