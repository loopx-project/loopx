// Packaged search and recovery over real SQLite and the production read API.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {resolveTestPython} from '../../../../scripts/test-python.mjs';
import {launchBrowser, loadPlaywright} from '../../../../examples/dashboard-browser-smoke-support.mjs';
const repoRoot = fileURLToPath(new URL('../../../../', import.meta.url));
const outputDir = resolve(repoRoot, 'output/playwright/requirements-search');
await mkdir(outputDir, {recursive:true});
const child=spawn(resolveTestPython({repoRoot}),['-u','apps/presentation/dashboard/smoke/goal-requirements-http-fixture.py'],{cwd:repoRoot,stdio:['pipe','pipe','pipe']});
child.stderr.on('data',x=>process.stderr.write(x));
const iter=createInterface({input:child.stdout})[Symbol.asyncIterator]();
async function next(){const row=await iter.next(); assert.equal(row.done,false); return JSON.parse(row.value);}
async function command(value){child.stdin.write(value+'\n');return next();}
let browser;
try {
 const {origin}=await next();
 browser=await launchBrowser(loadPlaywright().chromium);
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 await page.addInitScript(()=>localStorage.setItem('loopx-pw-locale','en'));
 await page.route('**/api/**',route=>{
   const p=new URL(route.request().url()).pathname;
   if(p==='/api/chat/goal-acceptance'||p==='/api/chat/delivery-review') return route.continue();
   return route.fulfill({status:404,json:{error:'Not part of read-only fixture'}});
 });
 await page.goto(origin+'/chat/?statusUrl=/status.json');
 await page.locator('.personal-goal-link').first().click();
 await page.getByRole('button',{name:'Overview',exact:true}).click();
 const section=page.locator('.goal-outcomes');
 const search=section.getByRole('searchbox',{name:'Find requirements and evidence'});
 await search.fill('ARTIFACT:SOURCE-4-29');
 await section.getByRole('status').filter({hasText:'1 of 150 requirements match'}).waitFor();
 const last=section.locator('.goal-outcome').filter({has:page.getByRole('heading',{name:'Retained result 4',exact:true})});
 await last.locator('.goal-outcome-disclosure > summary').click();
 await last.locator('.goal-outcome-requirement:visible > summary').click();
 await last.getByText('核验修订 4-29',{exact:true}).waitFor();
 await page.setViewportSize({width:1440,height:1600});
 await section.screenshot({path:resolve(outputDir,'requirements-search.png')});
 await page.setViewportSize({width:1440,height:1000});
 await last.getByText('Add a requirement',{exact:true}).click();
 const draft=last.getByRole('textbox');
 await draft.fill('Do not lose this unsent correction');
 await search.fill('no such requirement');
 assert.equal(await draft.inputValue(),'Do not lose this unsent correction');
 assert.equal(await draft.isVisible(),true);
 await search.press('Escape');
 assert.equal(await last.locator('.goal-outcome-criteria > li:visible').count(),30);
 assert.equal((await command('inspect')).unchanged,true);
 await page.screenshot({path:resolve(outputDir,'requirements-real-sqlite.png')});
 await command('fault');
 await last.getByRole('button',{name:'Refresh result',exact:true}).click();
 await page.locator('.delivery-acceptance-contract').getByRole('alert').waitFor();
 assert.equal(await draft.inputValue(),'Do not lose this unsent correction');
 assert.equal(await last.getByRole('button',{name:'Retain requirement',exact:true}).isDisabled(),true);
 await command('recover');
 await last.getByRole('button',{name:'Refresh result',exact:true}).click();
 await search.fill('核验修订 4-29');
 await section.getByRole('status').filter({hasText:'1 of 150 requirements match'}).waitFor();
 assert.equal((await command('inspect')).unchanged,true);
 console.log('PASS: packaged App / real SQLite and production HTTP; search, draft, source failure/recovery, unchanged authority.');
} finally {await browser?.close(); child.stdin.end();}
