import {test,expect} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
import {existsSync,readFileSync} from 'node:fs';

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
test('actual Runtime: DSL, synthetic bars, Doctor, 3 reruns, exports and persisted reopen',async({page})=>{
 test.setTimeout(90000);await page.emulateMedia({reducedMotion:'reduce'});
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const first=JSON.parse(await nextLine());await page.goto(first.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();expect(new URL(page.url()).hash).toBe('');
 await page.getByRole('button',{name:'전략 검증',exact:true}).click();await expect(page.getByText('지원 규칙 검증 통과')).toBeVisible();
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 await page.getByText('연습용 시세',{exact:true}).click();await page.getByRole('button',{name:'예시 시세로 연습'}).click();await expect(page.getByLabel('백테스트 데이터')).not.toHaveValue('');
 const execution=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='run');
 await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();const envelope=await(await execution).json();expect(envelope.ok).toBe(true);const baseline=envelope.result;expect(baseline.report.equity.length).toBeGreaterThan(30);
 await expect(page.getByRole('heading',{name:'실행 리포트'})).toBeVisible();expect(baseline.report.metrics.totalReturnPct).toBeCloseTo(0.00024726038789779636,12);await expect(page.locator('.metric').first().locator('strong')).toHaveText('+0.000247%');await expect(page.locator('.metric').first().locator('strong')).toHaveClass('up');await page.getByRole('button',{name:'위험 진단',exact:true}).click();await expect(page.locator('.diagnostic')).not.toHaveCount(0);
 await page.getByRole('button',{name:'개선 실험',exact:true}).click();await page.getByRole('button',{name:'후보 만들기'}).click();await expect(page.getByText('비교할 후보 3개')).toBeVisible();
 const compare=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='compare');await page.getByRole('button',{name:'전체 후보 재백테스트'}).click();const compared=await compare;expect(compared.request().postDataJSON().arguments.runIds).toHaveLength(4);expect((await compared.json()).ok).toBe(true);await expect(page.getByText('조건 동등성과 상세 비교')).toBeVisible();
 for(const format of ['JSON','CSV','HTML']){const downloadEvent=page.waitForEvent('download');await page.getByRole('button',{name:format,exact:true}).click();const download=await downloadEvent;expect(download.suggestedFilename()).toMatch(new RegExp('\\.'+format.toLowerCase()+'$'));expect(await download.failure()).toBeNull();const content=readFileSync((await download.path())!,'utf8');expect(content.length).toBeGreaterThan(20);if(format==='JSON')expect(JSON.parse(content)).toBeTruthy();}
 await page.getByRole('button',{name:'성과 분석',exact:true}).click();await page.setViewportSize({width:320,height:780});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);await page.screenshot({path:'test-results/runtime-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:'test-results/runtime-desktop.png',fullPage:true});
 child.stdin.write('ticket\n');const reopened=JSON.parse(await nextLine());await page.goto('about:blank');await page.goto(reopened.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await page.getByRole('button',{name:'전략과 실행 기록',exact:false}).click();await expect(page.locator('.library-item')).toHaveCount(8);await page.locator('.library-item').last().click();await expect(page.getByRole('heading',{name:'실행 리포트'})).toBeVisible();expect(errors).toEqual([]);
});

test('offline Monaco: authenticated syntax diagnostics, correction, SDK help and keyboard exit',async({page})=>{
 await page.emulateMedia({reducedMotion:'reduce'});
 const errors:string[]=[];const external:string[]=[];const workers:string[]=[];
 page.on('worker',worker=>workers.push(worker.url()));
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error'||message.type()==='warning')errors.push(message.text());});
 page.on('request',request=>{if(!['127.0.0.1','localhost'].includes(new URL(request.url()).hostname))external.push(request.url());});
 child.stdin.write('ticket\n');const ticket=JSON.parse(await nextLine());
 const response=await page.goto(ticket.url);
 const csp=response!.headers()['content-security-policy'];
 expect(csp).toContain("script-src 'sha256-");expect(csp).not.toContain("'unsafe-eval'");
 await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await page.getByRole('button',{name:'Python 코드',exact:true}).click();
 const input=page.getByRole('textbox',{name:'Python 전략 코드',exact:false});
 await expect(input).toBeVisible();await input.focus();
 await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText('def on_bar(ctx)\n    return {"action": "hold"}\n');
 const invalidResponse=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='validate_strategy');
 await page.getByRole('button',{name:'전략 검증',exact:true}).click();
 const invalid=(await(await invalidResponse).json()).result;
 expect(invalid.valid).toBe(false);expect(invalid.errors[0].line).toBe(1);
 await expect(page.locator('.validation-errors li')).toContainText(invalid.errors[0].message);await expect(page.locator('.validation-errors code')).toContainText('1:');
 await expect(page.locator('.squiggly-error')).not.toHaveCount(0);
 await input.focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText('from meritz_strategy import Context\n\ndef on_bar(ctx):\n    context = Context(ctx)\n    return context.buy(1)\n');
 await expect(page.locator('.squiggly-error')).toHaveCount(0);
 await expect(page.getByText('전략 검증 실패',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'전략 검증',exact:true}).click();await expect(page.getByText('지원 규칙 검증 통과')).toBeVisible();
 await input.focus();await page.keyboard.press('ControlOrMeta+End');await page.keyboard.insertText('    context.');
 await expect(page.locator('.suggest-widget.visible')).toBeVisible();
 await expect(page.locator('.suggest-widget')).toContainText('get_indicator');
 await page.keyboard.press('Escape');await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'편집기 밖으로 이동'})).toBeFocused();
 await page.setViewportSize({width:320,height:780});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
 await page.getByRole('button',{name:'화면 테마 변경'}).last().click();
 await expect(page.locator('.monaco-editor.vs')).toBeVisible();
 // Remount after DSL switching exercises disposal of models and providers.
 await page.getByRole('button',{name:'비주얼 빌더',exact:true}).click();await page.getByRole('button',{name:'Python 코드',exact:true}).click();
 await expect(page.locator('.monaco-editor')).toHaveCount(1);
 const shippedCode=JSON.parse((await page.locator('.json-view').textContent())!).code;
 const wrapper='from meritz_strategy import Context as SDKContext\n\ndef on_bar(ctx):\n    context = SDKContext(ctx)\n';
 await input.focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(wrapper+'    context.');await page.keyboard.press('Control+Space');
 await expect(page.locator('.suggest-widget.visible .monaco-list-row')).toHaveCount(7);
 await page.keyboard.press('Escape');
 for(const source of [shippedCode+'    context.',wrapper+'    intent = context.buy(1, reason="test")\n    context.',wrapper+'    for bar in []:\n        pass\n    context.',wrapper+'    with resource as other:\n        pass\n    context.',wrapper+'    if 1 == 1 and 1 <= 2 and 2 >= 1 and 1 != 2:\n        pass\n    context.']) {
  await input.focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(source);await page.keyboard.press('Control+Space');
  await expect(page.locator('.suggest-widget.visible .monaco-list-row')).toHaveCount(7);await page.keyboard.press('Escape');
 }
 for(const ending of ['ctx.','math.','1.','"context.','# context.','"""\ncontext.','cash = SDKContext(ctx).get_cash()\n    cash.','other = SDKContext(ctx) if True else {}\n    other.','context += 1\n    context.','context, other = ({}, {})\n    context.','for context in []:\n        context.','with thing as context:\n        context.','del context\n    context.']) {
  await input.focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(wrapper+'    '+ending);
  await page.keyboard.press('Control+Space');await expect(page.locator('.suggest-widget.visible')).toHaveCount(0);
  await page.keyboard.press('Escape');
 }
 await input.focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(wrapper+'    context.');await page.keyboard.press('Control+Space');
 await expect(page.locator('.suggest-widget.visible .monaco-list-row')).toHaveCount(7);
 await expect.poll(async()=>JSON.parse((await page.locator('.json-view').textContent())!).code).toBe(wrapper+'    context.');
 await page.keyboard.insertText('buy');await page.keyboard.press('Enter');
 await expect(page.locator('.view-lines')).toContainText('context.buy(quantity)');
 await page.keyboard.press('Escape');
 await page.screenshot({path:'test-results/editor-mobile.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'화면 테마 변경'}).click();
 await page.screenshot({path:'test-results/editor-desktop.png',fullPage:true});
 expect(external).toEqual([]);expect(workers).toEqual([]);expect(errors).toEqual([]);
});

test('delayed Python file read does not replace a newer DSL draft',async({page})=>{
 await page.addInitScript(()=>{
  const original=File.prototype.text;
  File.prototype.text=function(){
   if(this.name!=='delayed.py')return original.call(this);
   return new Promise<string>(resolve=>{(window as any).releaseStrategyFile=async()=>resolve(await original.call(this));});
  };
 });
 child.stdin.write('ticket\n');const ticket=JSON.parse(await nextLine());await page.goto(ticket.url);
 await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await page.getByRole('button',{name:'Python 코드',exact:true}).click();
 await page.getByLabel('.py 파일').setInputFiles({name:'delayed.py',mimeType:'text/plain',buffer:Buffer.from('def on_bar(ctx):\n    return {"action":"hold"}\n')});
 await page.waitForFunction(()=>typeof (window as any).releaseStrategyFile==='function');
 await page.getByRole('button',{name:'비주얼 빌더',exact:true}).click();
 await page.evaluate(()=>(window as any).releaseStrategyFile());
 await expect(page.locator('.error[role=alert]')).toContainText('파일을 읽는 동안 전략이 변경되어');
 await expect(page.getByRole('button',{name:'비주얼 빌더',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(page.locator('.monaco-editor')).toHaveCount(0);
});

test('actual walk-forward: two saved candidates, rolling folds and persisted full runs',async({page})=>{
 test.setTimeout(150000);await page.emulateMedia({reducedMotion:'reduce'});
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 child.stdin.write('ticket\n');const ticket=JSON.parse(await nextLine());await page.goto(ticket.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await page.getByLabel('전략 이름',{exact:true}).fill('Walk 후보');await page.getByLabel('주문 수량',{exact:false}).fill('2');
 let saved=page.waitForResponse(response=>response.url().endsWith('/api/platform')&&response.request().postDataJSON()?.action==='save_strategy');
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();const candidate=(await(await saved).json()).result;
 await expect(page.getByText('저장됨',{exact:true})).toBeVisible();await page.getByLabel('전략 이름',{exact:true}).fill('Walk 기준');await page.getByLabel('주문 수량',{exact:false}).fill('1');
 saved=page.waitForResponse(response=>response.url().endsWith('/api/platform')&&response.request().postDataJSON()?.action==='save_strategy');await page.getByRole('button',{name:'전략 저장',exact:true}).click();const baseline=(await(await saved).json()).result;
 await page.getByText('연습용 시세',{exact:true}).click();await page.getByRole('button',{name:'예시 시세로 연습'}).click();await expect(page.getByLabel('백테스트 데이터')).not.toHaveValue('');
 await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();await expect(page.getByRole('heading',{name:'실행 리포트',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByLabel('검증 방법').selectOption('walk_forward');
 await page.getByText('추가 후보 선택 (0/3)',{exact:true}).click();const checkbox=page.getByRole('checkbox',{name:/Walk 후보/});await expect(checkbox).toBeEnabled();await checkbox.check();
 await page.getByLabel('학습 봉 수').fill('80');await page.getByLabel('검증 봉 수').fill('2');await expect(page.getByRole('button',{name:'Walk-forward 실행'})).toBeDisabled();
 await page.getByLabel('검증 봉 수').fill('20');await expect(page.getByText('160봉 · 4개 구간 · 마지막 0봉 제외',{exact:true})).toBeVisible();
 const validationResponse=page.waitForResponse(response=>response.url().endsWith('/api/platform')&&response.request().postDataJSON()?.action==='validate_robustness',{timeout:130000});
 await page.getByRole('button',{name:'Walk-forward 실행'}).click();const response=await validationResponse;const request=response.request();
 expect(request.postDataJSON().arguments.options).toEqual({candidateRevisionIds:[candidate.id],trainBars:80,testBars:20});
 const envelope=await response.json();expect(envelope.ok).toBe(true);const record=envelope.result;expect(record.result.status).toBe('completed');expect(record.result.folds).toHaveLength(4);expect(record.result.candidateRevisionIds).toEqual([baseline.id,candidate.id]);expect(record.result.holdoutContaminated).toBe(true);
 const result=page.getByRole('region',{name:'Walk-forward 결과'});await expect(result.locator('tbody tr')).toHaveCount(4);await expect(result).toContainText('사후');
 const ids=record.result.folds.flatMap((fold:any)=>[...fold.training.map((trial:any)=>trial.result.runId),fold.test.result.runId]);expect(new Set(ids).size).toBe(12);
 const headers={'X-Platform-Token':request.headers()['x-platform-token'],Origin:new URL(page.url()).origin};
 const listed=await page.request.post(new URL('/api/platform',page.url()).href,{headers,data:{action:'list_runs',arguments:{}}});const listedEnvelope=await listed.json();expect(listedEnvelope.ok).toBe(true);const runs=listedEnvelope.result.runs;expect(ids.every((id:string)=>runs.some((run:any)=>run.id===id))).toBe(true);
 const stored=await page.request.post(new URL('/api/platform',page.url()).href,{headers,data:{action:'get_validation',arguments:{validationId:record.id}}});expect((await stored.json()).result).toEqual(record);
 await result.getByText('구간별 실행 ID와 실패 근거',{exact:true}).click();await expect(result).toContainText(ids[0]);
 await result.getByText('구간별 실행 ID와 실패 근거',{exact:true}).click();await page.getByText('추가 후보 선택 (1/3)',{exact:true}).click();
 await page.setViewportSize({width:1440,height:1100});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:'test-results/walk-forward-desktop.png',fullPage:false});
 await page.setViewportSize({width:320,height:780});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);await page.screenshot({path:'test-results/walk-forward-mobile.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1000});
 // A real failing strategy verifies failure rendering without synthetic response fixtures.
 const failureSaved=await page.request.post(new URL('/api/platform',page.url()).href,{headers,data:{action:'save_strategy',arguments:{definition:{schemaVersion:1,kind:'python',name:'Walk 실패 후보',code:'def on_bar(ctx):\n    return {"action": "unsupported"}\n'}}}});expect((await failureSaved.json()).ok).toBe(true);
 await page.getByLabel('검증 방법').selectOption('cost_sensitivity');await page.getByLabel('검증 방법').selectOption('walk_forward');
 await page.getByText('추가 후보 선택 (0/3)',{exact:true}).click();const failedChoice=page.getByRole('checkbox',{name:/Walk 실패 후보/});await expect(failedChoice).toBeEnabled();await failedChoice.check();
 await page.getByLabel('학습 봉 수').fill('80');await page.getByLabel('검증 봉 수').fill('40');
 const failedResponse=page.waitForResponse(value=>value.url().endsWith('/api/platform')&&value.request().postDataJSON()?.action==='validate_robustness',{timeout:130000});await page.getByRole('button',{name:'Walk-forward 실행'}).click();
 const failed=(await(await failedResponse).json()).result;expect(failed.result.status).toBe('completed_with_failures');expect(failed.result.folds).toHaveLength(2);expect(failed.result.folds.every((fold:any)=>fold.selectedRevisionId===null&&fold.test===null)).toBe(true);
 await expect(result).toContainText('실패 포함');await expect(result.locator('tbody tr')).toHaveCount(2);await expect(result.locator('tbody')).toContainText('선택 없음');
 await result.getByText('구간별 실행 ID와 실패 근거',{exact:true}).click();await expect(result).toContainText('candidate execution or metric validation failed');expect(errors).toEqual([]);
});
