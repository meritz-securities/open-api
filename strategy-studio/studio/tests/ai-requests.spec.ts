import {test,expect,type Page} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
const root=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON||resolve(root,'../open-api-mcp/.venv/bin/python');
let child:ChildProcessWithoutNullStreams,nextLine:()=>Promise<any>,stderr='';
async function command(value:string){child.stdin.write(value+'\n');return nextLine();}
test.beforeEach(()=>{
 stderr='';child=spawn(python,[resolve('tests/ai_requests_server.py'),resolve('dist/index.html')],{env:{PATH:process.env.PATH,PYTHONPATH:root,PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1'}});
 const queue:string[]=[],waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});child.stderr.on('data',data=>{stderr+=data.toString();});
 nextLine=()=>queue.length?Promise.resolve(JSON.parse(queue.shift()!)):new Promise((accept,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout: '+stderr)),20000);waiters.push(line=>{clearTimeout(timer);accept(JSON.parse(line));});});
});
test.afterEach(async()=>{child.stdin.end('stop\n');await new Promise<void>(done=>{const timer=setTimeout(()=>{child.kill();done();},5000);child.once('exit',()=>{clearTimeout(timer);done();});});});
async function openRun(page:Page,id:string){await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.getByRole('button').filter({has:page.locator('small',{hasText:id.slice(0,16)})}).click();await page.getByRole('button',{name:'개선 실험',exact:true}).click();}
async function save(page:Page,purpose:string){await page.getByRole('textbox',{name:'개선 목적',exact:true}).fill(purpose);const response=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='create_ai_request');await page.getByRole('button',{name:'개선 요청 저장 / 재시도'}).click();return (await(await response).json()).result;}

test('external test-authored submissions restore with failure, compare and no rerun',async({page})=>{
 const first=await nextLine();await page.goto(first.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openRun(page,first.baseline);
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await command('lose_response');const lost=await save(page,'비용과 낙폭을 줄여줘');expect(lost).toBeUndefined();await expect(page.getByRole('alert').first()).toContainText('저장 후 응답 유실');
 const retry=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='create_ai_request');await page.getByRole('button',{name:'개선 요청 저장 / 재시도'}).click();const saved=(await(await retry).json()).result;const id=saved.request.id;
 await expect(page.getByRole('region',{name:'AI 요청 상세'})).toContainText(id);
 await page.context().grantPermissions(['clipboard-read','clipboard-write'],{origin:new URL(first.url).origin});await page.getByRole('button',{name:'Claude 요청문 복사',exact:true}).click();expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('meritz_ai_request_get(requestId="'+id+'")');await expect(page.getByRole('status').filter({hasText:'모델을 호출하지 않았습니다'})).toBeVisible();
 await page.getByRole('button',{name:'개선 요청 저장 / 재시도'}).click();await expect(page.getByRole('button',{name:'개선 요청 저장 / 재시도'})).toBeEnabled();expect((await command('inspect')).requests).toHaveLength(1);
 await page.getByText('외부 Claude 요청문',{exact:true}).click();await expect(page.getByRole('region',{name:'AI 요청 상세'}).locator('pre').first()).toContainText('meritz_ai_candidates_submit');
 // Fixture-authored definitions, not an AI generation claim.
 const submitted=await command('external '+id);expect(submitted.attempts.map((a:any)=>a.status)).toEqual(['succeeded','failed']);
 const before=await command('inspect');expect(before.requests).toHaveLength(1);await page.getByRole('button',{name:'AI 요청 상태 새로고침'}).click();await expect(page.getByRole('region',{name:'AI 후보 비교'})).toContainText('동일 데이터: 예');await expect(page.getByRole('region',{name:'AI 요청 상세'})).toContainText('후보 실행에 실패했습니다.');
 await page.goto('about:blank');await page.goto((await command('ticket')).url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openRun(page,first.baseline);
 await page.getByRole('button',{name:'AI 요청 목록 새로고침'}).click();await page.getByRole('region',{name:'외부 AI 개선 요청'}).getByRole('button').filter({hasText:id.slice(0,12)}).click();await expect(page.getByRole('region',{name:'AI 후보 비교'})).toBeVisible();
 await page.setViewportSize({width:320,height:800});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.setViewportSize({width:1280,height:800});
 const after=await command('inspect');expect(after.runs).toEqual(before.runs);expect(after.baselineUnchanged).toBe(true);expect(after.requests).toHaveLength(1);
 await expect(page.getByRole('region',{name:'외부 AI 개선 요청'}).getByText('후보 실패 포함',{exact:true})).toBeVisible();
 await openRun(page,first.other);await page.getByRole('button',{name:'AI 요청 목록 새로고침'}).click();await expect(page.getByRole('region',{name:'AI 요청 상세'})).toHaveCount(0);await expect(page.getByRole('region',{name:'외부 AI 개선 요청'})).not.toContainText(id.slice(0,12));expect(errors).toEqual([]);
});

test('pending cancellation and delayed response preserve a newer draft',async({page})=>{
 const first=await nextLine();await page.goto(first.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openRun(page,first.baseline);
 const pending=await save(page,'취소할 요청');await page.getByRole('button',{name:'AI 요청 취소'}).click();await expect(page.getByRole('region',{name:'AI 요청 상세'}).getByRole('heading')).toContainText('취소');
 await page.getByRole('button',{name:'새 요청 작성'}).click();await page.getByRole('textbox',{name:'개선 목적',exact:true}).fill('지연 응답 요청');await command('delay');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='create_ai_request');await page.getByRole('button',{name:'개선 요청 저장 / 재시도'}).click();expect((await command('started')).started).toBe(true);
 await page.getByRole('textbox',{name:'개선 목적',exact:true}).fill('나중에 작성한 초안');await command('release');await response;await expect(page.getByRole('textbox',{name:'개선 목적',exact:true})).toHaveValue('나중에 작성한 초안');await expect(page.getByRole('region',{name:'AI 요청 상세'})).toHaveCount(0);
 expect((await command('inspect')).requests.find((r:any)=>r.request.id===pending.request.id).status).toBe('cancelled');
 const running=await save(page,'다른 Runtime에서 실행');expect((await command('start_external '+running.request.id)).started).toBe(true);await page.getByRole('button',{name:'AI 요청 상태 새로고침'}).click();await expect(page.getByRole('region',{name:'AI 요청 상세'}).getByRole('heading')).toContainText('실행 중');
 await page.getByRole('button',{name:'AI 요청 취소'}).click();await expect(page.getByRole('alert').first()).toContainText('이 실행 프로세스');await expect(page.getByRole('region',{name:'AI 요청 상세'}).getByRole('heading')).toContainText('실행 중');expect((await command('finish_external')).done).toBe(true);
 await page.getByRole('button',{name:'AI 요청 취소'}).click();await expect(page.getByRole('region',{name:'외부 AI 개선 요청'}).getByRole('status')).toContainText('작업이 이미 종료되었습니다.');
});

test('2000 unbroken characters and request ID fit at 320px',async({page})=>{
 const first=await nextLine();await page.goto(first.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openRun(page,first.baseline);await page.setViewportSize({width:320,height:800});
 const result=await save(page,'A'.repeat(2000));const section=page.getByRole('region',{name:'외부 AI 개선 요청'});await expect(section.getByRole('region',{name:'AI 요청 상세'})).toContainText(result.request.id);
 expect(await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))).toEqual({width:320,scroll:320});
});

test('same Runtime cancellation acknowledges delivery before final state',async({page})=>{
 const first=await nextLine();await page.goto(first.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openRun(page,first.baseline);
 const saved=await save(page,'현재 Runtime 취소');expect((await command('start_local '+saved.request.id)).started).toBe(true);await page.getByRole('button',{name:'AI 요청 상태 새로고침'}).click();
 await page.getByRole('button',{name:'AI 요청 취소'}).click();await expect(page.getByRole('region',{name:'외부 AI 개선 요청'}).getByRole('status')).toContainText('취소를 요청했습니다.');await expect(page.getByRole('region',{name:'AI 요청 상세'}).getByRole('heading')).toContainText('실행 중');
 expect((await command('finish_external')).done).toBe(true);await page.getByRole('button',{name:'AI 요청 상태 새로고침'}).click();await expect(page.getByRole('region',{name:'AI 요청 상세'}).getByRole('heading')).toContainText('취소');
 const state=await command('inspect');expect(state.requests.find((row:any)=>row.request.id===saved.request.id).status).toBe('cancelled');
});

test('actual profitable and losing candidates use signed colors in both tables',async({page})=>{
 const first=await nextLine();const baseline=await command('signed_baseline');await page.goto(first.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openRun(page,baseline.id);
 const saved=await save(page,'수익률 색상 검증');const submitted=await command('signed_submit '+saved.request.id);expect(submitted.attempts[0].metrics.totalReturnPct).toBeGreaterThan(0);expect(submitted.attempts[1].metrics.totalReturnPct).toBeLessThan(0);
 await page.getByRole('button',{name:'AI 요청 상태 새로고침'}).click();const detail=page.getByRole('region',{name:'AI 요청 상세'});const rows=detail.locator('table').first().locator('tbody tr');await expect(rows.nth(0).locator('td').nth(2)).toHaveClass('up');await expect(rows.nth(1).locator('td').nth(2)).toHaveClass('down');
 const comparison=page.getByRole('region',{name:'AI 후보 비교'}).locator('tbody tr');await expect(comparison.nth(1).locator('td').nth(1)).toHaveClass('up');await expect(comparison.nth(2).locator('td').nth(1)).toHaveClass('down');
 expect(submitted.result.comparison.runs[2].deltaDrawdownPct).toBeGreaterThan(0);await expect(comparison.nth(2).locator('td').nth(2)).toHaveClass('down');await expect(comparison.nth(0).locator('td').nth(2)).toHaveClass('');
 for(const [index,channel] of [[0,0],[1,2]] as const){const color=await rows.nth(index).locator('td').nth(2).evaluate(element=>getComputedStyle(element).color);const rgb=color.match(/\d+/g)!.map(Number);expect(rgb[channel]).toBeGreaterThan(rgb[channel===0?2:0]);}
 await page.goto('about:blank');await page.goto((await command('ticket')).url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openRun(page,submitted.attempts[1].runId);
 const request=await save(page,'낙폭 감소 색상 검증');const improved=await command('signed_submit '+request.request.id);expect(improved.result.comparison.runs[1].deltaDrawdownPct).toBeLessThan(0);
 await page.getByRole('button',{name:'AI 요청 상태 새로고침'}).click();await expect(page.getByRole('region',{name:'AI 후보 비교'}).locator('tbody tr').nth(1).locator('td').nth(2)).toHaveClass('up');

});
