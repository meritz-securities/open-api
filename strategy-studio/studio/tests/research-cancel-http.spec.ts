import {test,expect,type Page} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const studioRoot=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let child:ChildProcessWithoutNullStreams,nextLine:()=>Promise<any>,fixture:any,token:string;
test.beforeEach(async({page})=>{
 const python=process.env.MERITZ_TEST_PYTHON;if(!python)throw Error('Set pinned MERITZ_TEST_PYTHON');
 child=spawn(python,[resolve(studioRoot,'tests/research_runtime_server.py'),resolve(studioRoot,'dist/index.html')],{
  env:{PATH:process.env.PATH,PYTHONPATH:process.env.MERITZ_RUNTIME_ROOT||resolve(studioRoot,'..'),PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1',MERITZ_READ_ONLY:'1',FASTMCP_CHECK_FOR_UPDATES:'off'}});
 const lines:string[]=[],waiters:((line:string)=>void)[]=[];let errors='';
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else lines.push(line);});
 child.stderr.on('data',chunk=>{errors+=chunk.toString();});
 nextLine=async()=>JSON.parse(await(lines.length?Promise.resolve(lines.shift()!):new Promise<string>((accept,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture timeout: '+errors)),20000);
  waiters.push(line=>{clearTimeout(timer);accept(line);});})));
 fixture=await nextLine();const auth=page.waitForResponse(r=>r.url().endsWith('/api/session'));
 await page.goto(fixture.url);token=(await(await auth).json()).result.token;
 await expect(page.getByText('내 PC 저장소 준비 완료',{exact:false})).toBeVisible();
});
test.afterEach(async()=>{if(child){child.stdin.end('stop\n');await new Promise<void>(done=>{
 if(child.exitCode!==null){done();return;}const timer=setTimeout(()=>{child.kill();done();},5000);
 child.once('exit',()=>{clearTimeout(timer);done();});});}});
async function api(page:Page,action:string,args:Record<string,unknown>={}){
 const envelope=await page.evaluate(async({token,action,args})=>{
  const response=await fetch('/api/platform',{method:'POST',headers:{'Content-Type':'application/json','X-Platform-Token':token},body:JSON.stringify({action,arguments:args})});
  return {status:response.status,value:await response.json()};
 },{token,action,args});expect(envelope.status).toBe(200);expect(envelope.value.ok).toBe(true);return envelope.value.result;
}
async function create(page:Page){return api(page,'create_research',{
 source:{revisionId:fixture.revisionId,datasetId:fixture.datasetId,settings:{initialCash:100,feeBps:0,taxBps:0,slippageBps:0}},
 purpose:'취소 이후 저장 결과 확인',trainFraction:.5});}
async function trainAndFreeze(page:Page,id:string){
 const prepared=await api(page,'prepare_research_training',{researchId:id});
 expect(prepared.training.baselineMetrics.finalEquity).toBe(110);
 const submitted=await api(page,'submit_ai_candidates',{requestId:prepared.training.aiRequestId,candidates:[{...fixture.definition,name:'수량 3 후보',quantity:3}]});
 expect(submitted.attempts[0].metrics.finalEquity).toBe(130);
 return api(page,'freeze_research',{researchId:id});
}
async function reopenHistory(page:Page,id:string){
 child.stdin.write('reopen\n');const reopened=await nextLine();expect(reopened.sourceUnchanged).toBe(true);
 const actions:string[]=[];page.on('request',request=>{if(request.url().endsWith('/api/platform'))actions.push(request.postDataJSON()?.action);});
 await page.goto('about:blank');const auth=page.waitForResponse(r=>r.url().endsWith('/api/session'));
 await page.goto(reopened.url);token=(await(await auth).json()).result.token;
 await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략과 실행 기록',exact:true}).click();
 await page.getByRole('button',{name:'검증 기록 새로고침',exact:true}).click();
 const restored=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='get_research');
 await page.getByRole('region',{name:'기간 나눈 검증 기록',exact:true}).getByRole('button').filter({hasText:fixture.definition.name}).click();
 const value=(await(await restored).json()).result;expect(value.session.id).toBe(id);
 return {actions,reopened,value};
}

test('real HTTP early evaluation cancellation does not poison a later completed evaluation',async({page})=>{
 const created=await create(page),id=created.session.id;
 const refused=await api(page,'cancel_research',{researchId:id,phase:'evaluation'});
 expect(refused.cancellation.effective).toBe(false);expect(refused.cancellation.reason).toBe('POOL_NOT_FROZEN');
 expect(refused.evaluation.cancellationRequested).toBe(false);
 await trainAndFreeze(page,id);
 const completed=await api(page,'evaluate_research',{researchId:id});
 expect(completed.evaluation.status).toBe('succeeded');expect(completed.evaluation.slots.map((slot:any)=>slot.metrics.finalEquity)).toEqual([90,70]);
 const late=await api(page,'cancel_research',{researchId:id,phase:'evaluation'});
 expect(late.cancellation.effective).toBe(false);
 const after=await api(page,'get_research',{researchId:id});expect(after).toEqual(completed);
 const restored=await reopenHistory(page,id);expect(restored.value).toEqual(completed);expect(restored.reopened.runs).toHaveLength(4);
 await expect(page.getByRole('button',{name:'검증 기간 계산',exact:true})).toBeDisabled();
 expect(restored.actions).not.toContain('evaluate_research');expect(restored.actions).not.toContain('prepare_research_training');
});

test('browser restores cancelled evaluation with preserved training outcomes and no automatic execution',async({page},testInfo)=>{
 const errors:string[]=[],external:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!['127.0.0.1','localhost'].includes(new URL(request.url()).hostname))external.push(request.url());});
 const created=await create(page),id=created.session.id;const frozen=await trainAndFreeze(page,id);
 // Deterministic cancellation before evaluation claim: no artificial sleeps or engine hooks.
 const cancelled=await api(page,'cancel_research',{researchId:id,phase:'evaluation'});
 expect(cancelled.cancellation.effective).toBe(true);expect(cancelled.evaluation.cancellationRequested).toBe(true);
 expect(cancelled.eligibility.canEvaluate).toBe(false);expect(cancelled.frozen).toEqual(frozen.frozen);
 const before=await api(page,'get_research',{researchId:id});
 const restored=await reopenHistory(page,id);expect(restored.value).toEqual(before);expect(restored.reopened.runs).toHaveLength(2);
 const region=page.getByRole('region',{name:'분리 검증 결과',exact:true});
 await expect(region.getByText('검증 작업 취소 요청이 기록되어 있습니다. 최종 상태를 확인하세요.',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'검증 기간 계산',exact:true})).toBeDisabled();
 await expect(region.locator('tbody tr')).toHaveCount(2);
 for(const [index,trainReturn] of ['+10.00%','+30.00%'].entries()){
  await expect(region.locator('tbody tr').nth(index).getByRole('cell').nth(0)).toHaveText(trainReturn);
  await expect(region.locator('tbody tr').nth(index).getByRole('cell').nth(2)).toHaveText('—');
 }
 expect(restored.actions).not.toContain('evaluate_research');expect(restored.actions).not.toContain('prepare_research_training');
 expect(await api(page,'get_research',{researchId:id})).toEqual(before);
 child.stdin.write('snapshot\n');const snapshot=await nextLine();expect(snapshot.runs).toEqual(restored.reopened.runs);
 await region.scrollIntoViewIfNeeded();await page.screenshot({path:testInfo.outputPath('research-cancelled-restored.png')});
 expect(errors).toEqual([]);expect(external).toEqual([]);
});
