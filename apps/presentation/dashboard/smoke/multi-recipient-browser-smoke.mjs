// One request, independent recipient states and returns over the packaged real backend.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {testPythonCommand} from '../../../../scripts/test-python.mjs';
import {launchBrowser, loadPlaywright} from '../../../../examples/dashboard-browser-smoke-support.mjs';
const repo = resolve(import.meta.dirname, '../../../..');
const output = resolve(repo, 'output/playwright/multi-recipient');
await mkdir(output, {recursive:true});
const fixturePath = resolve(repo, 'examples/personal-workspace-browser/steward_requests_fixture.py');
const command = testPythonCommand(`import runpy; runpy.run_path(${JSON.stringify(fixturePath)}, run_name="__main__")`, {repoRoot:repo});
const child = spawn(command.command, [...command.args, '--status-file', resolve(repo, 'examples/status.example.json'),
  '--multi-recipient'], {cwd:repo, env:{...process.env, LOOPX_USAGE_PING:'0'}, stdio:['pipe','pipe','pipe']});
const exited = once(child, 'exit');
let stderr = '';
child.stderr.on('data', value => stderr += value);
const queue = [], waiting = [];
createInterface({input:child.stdout}).on('line', line => {
  try {const value=JSON.parse(line); if(waiting.length) waiting.shift()(value); else queue.push(value);} catch { /* CLI banner */ }
});
const next = () => new Promise((resolve, reject) => {
  const timer=setTimeout(()=>reject(Error(`Fixture timeout: ${stderr}`)),90000);
  const receive=value=>{clearTimeout(timer);resolve(value);};
  if(queue.length) receive(queue.shift()); else waiting.push(receive);
});
async function fixture(action) {child.stdin.write(action+'\n');return next();}
let browser;
try {
  const ready=await next();assert.equal(ready.event,'ready');
  browser=await launchBrowser(loadPlaywright().chromium);
  const page=await browser.newPage({viewport:{width:1200,height:888}});
  await page.addInitScript(()=>localStorage.setItem('loopx-pw-locale','zh-CN'));
  await page.goto(ready.origin+'/chat/');
  await page.getByRole('navigation',{name:'管家视图'}).getByRole('button',{name:/^(Chat|对话)$/}).click();
  const cards=page.locator('.personal-collaboration').filter({hasText:'核对迁移方案 ·'});
  await cards.first().waitFor(); assert.equal(await cards.count(),2);
  const builder=cards.filter({hasText:'核对迁移方案 · builder'});
  const reviewer=cards.filter({hasText:'核对迁移方案 · reviewer'});
  assert.match(await builder.innerText(),/尚未记录/);
  assert.match(await reviewer.innerText(),/已采纳/);
  await page.screenshot({path:resolve(output,'conversation.png')});
  const summary=builder.locator('summary');
  await summary.focus();await page.keyboard.press('Enter');
  await builder.getByText('背景与补充',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:resolve(output,'recipient-detail.png')});
  await summary.press('Enter');
  const before=await fixture('inspect');
  await fixture('report-multi');
  // The real pending-return observer must discover the reply without a reload.
  await page.waitForFunction(()=>[...document.querySelectorAll('.personal-collaboration')].some(
    row=>row.textContent.includes('核对迁移方案 · reviewer')&&row.textContent.includes('回复已送达')),null,{timeout:45000});
  assert.doesNotMatch(await builder.innerText(),/回复已送达/);
  await page.getByText('reviewer 已完成独立核查；builder 的结论仍待返回。',{exact:true}).waitFor();
  const after=await fixture('inspect');
  assert.equal(after.turn_count,before.turn_count);
  assert.equal(after.posts,before.posts,'Observation and result refresh create no additional writes');
  console.log(JSON.stringify({ok:true,recipient_count:2,automatic_return:true,remaining_recipient:'builder',
    additional_browser_writes:after.posts-before.posts,output}));
} finally {
  await browser?.close();
  if(child.exitCode===null){child.stdin.write('stop\n');await exited;}
}
