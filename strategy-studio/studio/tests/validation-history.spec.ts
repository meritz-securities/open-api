import {test,expect} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
import {existsSync} from 'node:fs';

// No route interception: all UI operations reach the actual StrategyRuntime.
// Explicit synthetic bars exercise the engine, not Meritz live data access.
const runtimeRoot=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON||resolve(runtimeRoot,'../open-api-mcp/.venv/bin/python');
let child:ChildProcessWithoutNullStreams;
let nextLine:()=>Promise<string>;
let serverErrors='';
test.beforeAll(async()=>{
 if(!existsSync(resolve(runtimeRoot,'runtime/server.py')))throw Error('Set MERITZ_RUNTIME_ROOT to the platform source root.');
 child=spawn(python,[resolve('tests/runtime_server.py'),resolve('dist/index.html')],{env:{PATH:process.env.PATH,PYTHONPATH:runtimeRoot,PYTHONUNBUFFERED:'1'}});
 const queue:string[]=[];const waiters:((s:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});
 child.stderr.on('data',chunk=>{serverErrors+=chunk.toString();});
 nextLine=()=>queue.length?Promise.resolve(queue.shift()!):new Promise((accept,reject)=>{const timer=setTimeout(()=>reject(Error('Runtime launch response timed out. '+serverErrors)),15000);waiters.push(line=>{clearTimeout(timer);accept(line);});});
});
test.afterAll(async()=>{if(child){child.stdin.end('stop\n');await new Promise<void>(done=>{const timeout=setTimeout(()=>{child.kill();done();},5000);child.once('exit',()=>{clearTimeout(timeout);done();});});}});
test('stored validation restores after reconnect without rerunning',async({page})=>{
 test.setTimeout(90000);
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 let executions=0;page.on('request',request=>{if(request.url().endsWith('/api/platform')&&request.postDataJSON()?.action==='validate_robustness')executions++;});
 await page.goto(JSON.parse(await nextLine()).url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 await page.getByText('연습용 시세',{exact:true}).click();await page.getByRole('button',{name:'예시 시세로 연습'}).click();await expect(page.getByLabel('백테스트 데이터')).not.toHaveValue('');
 const runResponse=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='run');
 await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();const run=(await(await runResponse).json()).result;
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByLabel('검증 방법').selectOption('walk_forward');
 await expect(page.getByText('추가 후보 선택 (0/3)',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Walk-forward 실행'})).toBeEnabled();
 await page.getByLabel('학습 봉 수').fill('80');await page.getByLabel('검증 봉 수').fill('20');
 const validationResponse=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='validate_robustness');
 await page.getByRole('button',{name:'Walk-forward 실행'}).click();const response=await validationResponse;const record=(await response.json()).result;
 await expect(page.getByRole('region',{name:'Walk-forward 결과'}).locator('tbody tr')).toHaveCount(4);
 const origin=new URL(page.url()).origin;const headers={'X-Platform-Token':response.request().headers()['x-platform-token'],Origin:origin};
 const invoke=async(action:string,args:Record<string,unknown>={})=>(await(await page.request.post(origin+'/api/platform',{headers,data:{action,arguments:args}})).json()).result;
 // A second baseline's job must not appear in this baseline's history.
 const other=await invoke('run',{revisionId:run.revisionId,datasetId:run.datasetId});
 await invoke('validate_robustness',{runId:other.id,method:'monte_carlo',options:{iterations:1}});
 const generic=await invoke('validate_robustness',{runId:run.id,method:'monte_carlo',options:{iterations:2}});
 await invoke('validate_robustness',{runId:run.id,method:'out_of_sample',options:{fraction:2}});
 const before=await invoke('list_runs');
 await page.goto('about:blank');child.stdin.write('ticket\n');await page.goto(JSON.parse(await nextLine()).url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.getByRole('button').filter({has:page.locator('small',{hasText:run.id.slice(0,16)})}).click();
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByRole('button',{name:'검증 이력 새로고침'}).click();
 const history=page.getByRole('region',{name:'저장된 검증 이력'});await expect(history.getByRole('button',{name:'저장된 결과 열기'})).toHaveCount(2);await expect(history.locator('.library-item')).toHaveCount(3);await expect(history).toContainText('사후 기간 분리 · 실패');
 await history.locator('.library-item').filter({hasText:'재표본화'}).getByRole('button',{name:'저장된 결과 열기'}).click();await expect(page.getByLabel('검증 방법')).toHaveValue('monte_carlo');await expect(page.getByRole('region',{name:'검증 결과'})).toContainText('Monte Carlo 재표본화 결과');await expect(page.getByRole('region',{name:'검증 결과'})).toContainText('중앙값');await expect(page.getByText('저장된 결과 · '+generic.id.slice(0,12),{exact:true})).toBeVisible();
 await history.locator('.library-item').filter({hasText:'순차 검증'}).getByRole('button',{name:'저장된 결과 열기'}).click();
 const restored=page.getByRole('region',{name:'Walk-forward 결과'});await expect(restored.locator('tbody tr')).toHaveCount(4);await expect(page.getByLabel('학습 봉 수')).toHaveValue('80');await expect(page.getByLabel('검증 봉 수')).toHaveValue('20');
 await restored.getByText('검증 기록과 가정',{exact:true}).click();expect(JSON.parse((await restored.locator('pre').textContent())!)).toEqual(record);await expect(restored.getByText('저장된 결과 · '+record.id.slice(0,12),{exact:true})).toBeVisible();await expect(page.getByText('저장된 결과 · '+generic.id.slice(0,12),{exact:true})).toHaveCount(0);
 expect(executions).toBe(1);const after=await invoke('list_runs');expect(after.runs.map((r:any)=>r.id)).toEqual(before.runs.map((r:any)=>r.id));expect(after.validationJobs).toEqual(before.validationJobs);
 // New executions invalidate history and remove restored-result badges.
 await page.getByRole('button',{name:'Walk-forward 실행'}).click();await expect(restored.locator('tbody tr')).toHaveCount(4);await expect(history.locator('.library-item')).toHaveCount(0);await expect(history).toContainText('새로고침하세요');await expect(page.getByText('저장된 결과 · '+record.id.slice(0,12),{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'검증 이력 새로고침'}).click();await expect(history.locator('.library-item')).toHaveCount(4);
 await history.locator('.library-item').filter({hasText:'재표본화'}).getByRole('button',{name:'저장된 결과 열기'}).click();await expect(page.getByText('저장된 결과 · '+generic.id.slice(0,12),{exact:true})).toBeVisible();
 const rerun=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='validate_robustness');await page.getByRole('button',{name:'검증 실행',exact:true}).click();await rerun;await expect(history.locator('.library-item')).toHaveCount(0);await expect(page.getByText('저장된 결과 · '+generic.id.slice(0,12),{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'검증 이력 새로고침'}).click();await history.locator('.library-item').filter({hasText:'재표본화'}).last().getByRole('button',{name:'저장된 결과 열기'}).click();await expect(page.getByText('저장된 결과 · '+generic.id.slice(0,12),{exact:true})).toBeVisible();
 // Navigate with a restored result visible to a different source run.
 await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.getByRole('button').filter({has:page.locator('small',{hasText:other.id.slice(0,16)})}).click();await page.getByRole('button',{name:'검증 결과',exact:true}).click();
 await expect(page.getByRole('region',{name:'Walk-forward 결과'})).toHaveCount(0);await expect(page.locator('.json-view').filter({hasText:record.id})).toHaveCount(0);await expect(page.getByText(/저장된 결과 · /)).toHaveCount(0);await expect(history.locator('.library-item')).toHaveCount(0);
 await page.getByRole('button',{name:'검증 이력 새로고침'}).click();await expect(history.locator('.library-item')).toHaveCount(1);await expect(history).toContainText('재표본화');await expect(history).not.toContainText('순차 검증');expect(errors).toEqual([]);
});
