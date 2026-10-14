import assert from "node:assert/strict";
import {execFile,spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {createInterface} from "node:readline";
import {promisify} from "node:util";
import {repoRoot,outputDir} from "./fixture.mjs";
import {resolveTestPython} from "../../scripts/test-python.mjs";
import {fetchWorkspaceBackend, openWorkspacePage} from "./scenario-context.mjs";

export const goalWorkspaceCreateScenario = {
  id:"goal-workspace-create",
  async run({browser,url}) {
    const root=await mkdtemp(join(tmpdir(),'loopx-workspace-create-'));
    const python=resolveTestPython({repoRoot});
    const child=spawn(python,['-u',resolve(repoRoot,'examples/personal-workspace-browser/goal-workspace-todo-backend.py'),root],{cwd:repoRoot,env:{...process.env,PYTHONPATH:repoRoot},stdio:['ignore','pipe','pipe']});
    const closed=once(child,'close'),lines=createInterface({input:child.stdout});
    let diagnostics='',context,timer;
    child.stderr.on('data',chunk=>{diagnostics=(diagnostics+chunk).slice(-4000);});
    try {
      const ready=await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),closed.then(()=>{throw new Error(diagnostics);}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(`Todo backend startup: ${diagnostics}`)),30000);})]);
      clearTimeout(timer);
      const backend=`http://127.0.0.1:${ready.port}`, requests=[];
      let refuseApply=true;
      const target=new URL(url);target.searchParams.set('goalId','progress-projection');target.searchParams.set('view','overview');
      context=await openWorkspacePage(browser,target.toString(),{gotoWaitUntil:"domcontentloaded",beforeGoto:async(_api,page)=>{
        for(const pattern of ['**/status.json*','**/api/actions{,/**,?*}','**/api/chat/todo/detail?*']) await page.route(pattern,async route=>{
          const request=route.request(),path=new URL(request.url());
          if(path.pathname==='/api/actions/preview')requests.push(request.postDataJSON());
          if(path.pathname.endsWith('/apply')&&refuseApply){await route.fulfill({status:503,json:{ok:false,error:'Synthetic temporary transport refusal'}});return;}
          const response=await fetchWorkspaceBackend(route,backend);await route.fulfill({response});
        });
      }});
      const {page}=context;
      const now=page.getByRole('region',{name:'当前工作',exact:true});await now.waitFor();
      await now.getByRole('button',{name:'添加任务',exact:true}).click();
      const task=now.getByRole('textbox',{name:'任务内容',exact:true});
      const text='Verify the packaged workspace task creation result';
      await task.fill(text);await now.getByRole('button',{name:'检查任务',exact:true}).click();
      const drawer=page.locator('.personal-context-drawer');await drawer.waitFor();
      assert.equal(requests.length,1);assert.equal(requests[0].normalized_parameters.text,text);
      await drawer.locator('.personal-primary-action').click();
      await drawer.getByText(/失败/).first().waitFor();
      await page.locator('.personal-drawer-close').click();assert.equal(await task.inputValue(),text);
      refuseApply=false;
      await now.getByRole('button',{name:'检查任务',exact:true}).click();
      // The action owner may require regenerating the preview after failure.
      await drawer.locator('.personal-primary-action').click();
      await drawer.getByRole('button',{name:'确认并应用',exact:true}).waitFor();
      await drawer.getByRole('button',{name:'确认并应用',exact:true}).click();
      await now.locator('.goal-work-row').filter({hasText:text}).waitFor();
      assert.equal(await task.inputValue(),'');
      const {stdout}=await promisify(execFile)(python,['-c','from loopx.entrypoint import main; main()','--format','json','--registry',ready.registry,'todo','list','--goal-id','progress-projection','--role','agent'],{cwd:repoRoot,env:{...process.env,PYTHONPATH:repoRoot,LOOPX_USAGE_PING:'0'},timeout:30000});
      const result=JSON.parse(stdout);assert.equal(result.todos.filter(todo=>todo.text===text).length,1,'CLI must read exactly one created task');
      await page.screenshot({path:resolve(outputDir,'goal-workspace-created.png'),animations:'disabled'});
      return {coverageEntries:[],note:'Packaged inline Add task -> real action HTTP/File Todo owner -> refreshed row and independent CLI readback. Failed apply retains draft; recovery creates once. No model execution.'};
    } catch(error){if(diagnostics)process.stderr.write(diagnostics);if(context)await context.page.screenshot({path:resolve(outputDir,'goal-workspace-create-failed.png')});throw error;}
    finally {clearTimeout(timer);await context?.close();lines.close();if(child.exitCode===null&&child.signalCode===null){child.kill('SIGTERM');const kill=setTimeout(()=>child.kill('SIGKILL'),5000);try{await closed;}finally{clearTimeout(kill);}}await rm(root,{recursive:true,force:true});}
  },
};
