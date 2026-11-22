import {test,expect} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const studioRoot=resolve(dirname(fileURLToPath(import.meta.url)),'..');

// Actual authenticated HTTP + SQLite + shared engine. No intercepted routes or model calls.
let child:ChildProcessWithoutNullStreams,nextLine:()=>Promise<any>,errors='';
test.beforeAll(async()=>{
 const python=process.env.MERITZ_TEST_PYTHON;if(!python)throw Error('Set pinned MERITZ_TEST_PYTHON');
 child=spawn(python,[resolve(studioRoot,'tests/research_runtime_server.py'),resolve(studioRoot,'dist/index.html')],{
  env:{PATH:process.env.PATH,PYTHONPATH:process.env.MERITZ_RUNTIME_ROOT||resolve(studioRoot,'..'),PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1',MERITZ_READ_ONLY:'1',FASTMCP_CHECK_FOR_UPDATES:'off'}});
 const queue:string[]=[];const waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});
 child.stderr.on('data',chunk=>{errors+=chunk.toString();});
 nextLine=async()=>JSON.parse(await(queue.length?Promise.resolve(queue.shift()!):new Promise<string>((accept,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture timeout: '+errors)),20000);
  waiters.push(line=>{clearTimeout(timer);accept(line);});})));
});
test.afterAll(async()=>{if(child){child.stdin.end('stop\n');await new Promise<void>(done=>{
 const timer=setTimeout(()=>{child.kill();done();},5000);child.once('exit',()=>{clearTimeout(timer);done();});});}});

test('fresh research uses real HTTP, keeps heldout losses and reopens without reruns',async({page},testInfo)=>{
 const pageErrors:string[]=[],external:string[]=[],actions:string[]=[];
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('request',request=>{if(!['127.0.0.1','localhost'].includes(new URL(request.url()).hostname))external.push(request.url());if(request.url().endsWith('/api/platform'))actions.push(request.postDataJSON()?.action);});
 const first=await nextLine();expect(first.runs).toEqual([]);
 const auth=page.waitForResponse(r=>r.url().endsWith('/api/session'));
 await page.goto(first.url);const token=(await(await auth).json()).result.token;
 await expect(page.getByText('내 PC 저장소 준비 완료',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'저장 전략 열기: '+first.definition.name,exact:true}).click();
 await page.getByRole('combobox',{name:'백테스트 데이터',exact:true}).selectOption(first.datasetId);
 await page.getByLabel('초기 자본 (KRW)',{exact:true}).fill('100');
 for(const label of ['수수료 (bp)','슬리피지 (bp)','매도 거래세 (bp)'])await page.getByLabel(label,{exact:true}).fill('0');
 await page.getByRole('button',{name:'기간 나눠 개선·검증',exact:true}).click();
 await page.getByRole('textbox',{name:'기간 분리 개선 목적'}).fill('학습 수익만으로 선택한 후보의 손실도 보존');
 await page.getByRole('combobox',{name:'전략 개선에 쓸 기간',exact:true}).selectOption('0.5');
 async function action(name:string,label:string){const pending=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action===name);await page.getByRole('button',{name:label,exact:true}).click();const envelope=await(await pending).json();expect(envelope.ok).toBe(true);return envelope.result;}
 const created=await action('create_research','기간 저장');
 expect(created.session.binding.sourceKind).toBe('saved_strategy');expect(created.session.disclosures.priorFullDatasetRunCount).toBe(0);
 expect(created.session.split.holdout.datasetId).toBeNull();
 const prepared=await action('prepare_research_training','개선 기간 계산');
 expect(prepared.training.baselineMetrics.finalEquity).toBe(110);
 // Synthetic candidate submission uses the same authenticated API as MCP-host submissions.
 const submitted=await page.evaluate(async({token,requestId,definition})=>{
  const response=await fetch('/api/platform',{method:'POST',headers:{'Content-Type':'application/json','X-Platform-Token':token},body:JSON.stringify({action:'submit_ai_candidates',arguments:{requestId,candidates:[{...definition,name:'수량 3 후보',quantity:3}]}})});return response.json();
 },{token,requestId:prepared.training.aiRequestId,definition:first.definition});
 expect(submitted.ok).toBe(true);expect(submitted.result.attempts[0].metrics.finalEquity).toBe(130);
 await action('get_research','후보 결과 확인');
 await expect(page.getByRole('button',{name:'후보 확정',exact:true})).toBeEnabled();
 const frozen=await action('freeze_research','후보 확정');
 const selected=submitted.result.attempts[0].revisionId;expect(frozen.frozen.selectedRevisionId).toBe(selected);
 const evaluated=await action('evaluate_research','검증 기간 계산');
 expect(evaluated.frozen.selectedRevisionId).toBe(selected);
 expect(evaluated.evaluation.slots.map((s:any)=>s.metrics.finalEquity)).toEqual([90,70]);
 const result=page.getByRole('region',{name:'분리 검증 결과',exact:true});
 await expect(result.locator('tbody tr')).toHaveCount(2);
 await expect(result.locator('tbody tr').nth(0).getByRole('cell').nth(2)).toHaveText('-10.00%');
 await expect(result.locator('tbody tr').nth(1).getByRole('cell').nth(2)).toHaveText('-30.00%');
 await expect(result.getByText('개선 기간에서 선택됨',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'검증 기간 계산',exact:true})).toBeDisabled();
 child.stdin.write('reopen\n');const reopened=await nextLine();expect(reopened.sourceUnchanged).toBe(true);expect(reopened.runs).toHaveLength(4);
 await page.goto('about:blank');await page.goto(reopened.url);
 await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략과 실행 기록',exact:true}).click();
 await page.getByRole('button',{name:'검증 기록 새로고침',exact:true}).click();
 const history=page.getByRole('region',{name:'기간 나눈 검증 기록',exact:true});
 await history.getByRole('button').filter({hasText:first.definition.name}).click();
 await expect(result.locator('tbody tr').nth(1).getByRole('cell').nth(2)).toHaveText('-30.00%');
 await expect(page.getByRole('button',{name:'검증 기간 계산',exact:true})).toBeDisabled();
 await action('get_research','진행 상태 새로고침');
 child.stdin.write('snapshot\n');const snapshot=await nextLine();expect(snapshot.sourceUnchanged).toBe(true);expect(snapshot.runs).toEqual(reopened.runs);
 for(const name of ['create_research','prepare_research_training','freeze_research','evaluate_research'])expect(actions.filter(a=>a===name)).toHaveLength(1);
 expect(actions.some(a=>a.startsWith('generate_ai')||a==='fetch_market')).toBe(false);
 await expect(result.getByText('후보를 먼저 확정하세요.',{exact:true})).toHaveCount(0);
 await page.evaluate(()=>window.scrollTo(0,0));
 await page.screenshot({path:testInfo.outputPath('research-restored-desktop-top.png')});
 await result.scrollIntoViewIfNeeded();
 await page.screenshot({path:testInfo.outputPath('research-restored-desktop-result.png')});
 await page.setViewportSize({width:320,height:780});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
 await result.scrollIntoViewIfNeeded();await page.screenshot({path:testInfo.outputPath('research-restored-mobile-result.png')});
 await result.locator('tbody tr').nth(1).getByRole('cell').nth(2).scrollIntoViewIfNeeded();
 await expect(result.locator('tbody tr').nth(1).getByRole('cell').nth(2)).toBeInViewport();
 await page.screenshot({path:testInfo.outputPath('research-restored-mobile-heldout.png')});
 expect(pageErrors).toEqual([]);expect(external).toEqual([]);
});
