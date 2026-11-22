import {test,expect,type Page} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
const root=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON||resolve(root,'../open-api-mcp/.venv/bin/python');
let child:ChildProcessWithoutNullStreams,nextLine:()=>Promise<any>,stderr='';
async function command(value:string){child.stdin.write(value+'\n');return nextLine();}
test.beforeEach(()=>{
 stderr='';child=spawn(python,[resolve('tests/candidate_history_server.py'),resolve('dist/index.html')],{env:{PATH:process.env.PATH,PYTHONPATH:root,PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1'}});
 const queue:string[]=[],waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});child.stderr.on('data',data=>{stderr+=data.toString();});
 nextLine=()=>queue.length?Promise.resolve(JSON.parse(queue.shift()!)):new Promise((accept,reject)=>{const timeout=setTimeout(()=>reject(Error('Fixture timeout: '+stderr)),30000);waiters.push(line=>{clearTimeout(timeout);accept(JSON.parse(line));});});
});
test.afterEach(async()=>{child.stdin.end('stop\n');await new Promise<void>(done=>{const timer=setTimeout(()=>{child.kill();done();},5000);child.once('exit',()=>{clearTimeout(timer);done();});});});
async function openOrigin(page:Page,id:string){await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.getByRole('button').filter({has:page.locator('small',{hasText:id.slice(0,16)})}).click();await page.getByRole('button',{name:'개선 실험',exact:true}).click();}
async function backToCandidates(page:Page){await page.getByRole('button',{name:'실행 리포트',exact:true}).click();await page.getByRole('button',{name:'개선 실험',exact:true}).click();await page.getByRole('button',{name:'후보 이력 새로고침'}).click();}
function response(page:Page,action:string){return page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action===action);}

test('external candidates compare without rerun; explicit adoption restores old revision/data/settings and preserves source',async({page})=>{
 test.setTimeout(90000);const initial=await nextLine();const external=await command('external');expect(external.derived.length).toBeGreaterThan(0);expect(external.revisionIds).not.toContain(external.candidate.revisionId);expect(external.datasetIds).not.toContain(external.candidate.datasetId);
 await page.goto(initial.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await page.getByLabel('전략 이름',{exact:true}).fill('보존할 미저장 초안');await openOrigin(page,initial.baseline.id);
 const history=page.getByRole('region',{name:'저장된 개선 후보'});await history.getByRole('button',{name:'후보 이력 새로고침'}).click();await expect(history.locator('[data-candidate-id]')).toHaveCount(4);await expect(history).toContainText('최신 100건');
 for(const id of external.derived)await expect(history.locator(`[data-candidate-id="${id}"]`)).toHaveCount(0);
 const successful=history.locator(`[data-candidate-id="${external.candidate.id}"]`),failed=history.locator(`[data-candidate-id="${external.failed.id}"]`),cancelled=history.locator(`[data-candidate-id="${external.cancelled.id}"]`);
 await expect(history.locator(`[data-candidate-id="${external.interrupted.id}"]`)).toContainText('중단');
 await expect(failed).toContainText('실패');await expect(cancelled).toContainText('취소');await expect(failed.getByRole('button',{name:'원본과 저장 결과 비교'})).toBeDisabled();
 const before=await command('stats');const actions:string[]=[];page.on('request',r=>{if(r.url().endsWith('/api/platform'))actions.push(r.postDataJSON().action);});
 const comparedResponse=response(page,'compare');await successful.getByRole('button',{name:'원본과 저장 결과 비교'}).click();const comparedHttp=await comparedResponse;expect(comparedHttp.request().postDataJSON().arguments.runIds).toEqual([initial.baseline.id,external.candidate.id]);const compared=(await comparedHttp.json()).result;expect(compared.sameDataset).toBe(true);expect(compared.sameCosts).toBe(false);expect(compared.sameSettings).toBe(false);
 const comparison=page.getByRole('region',{name:'저장 후보 비교 결과'});await expect(comparison).toContainText('동일 비용: 아니오');await page.getByText('비교 근거와 비용 설정',{exact:true}).click();expect(JSON.parse((await comparison.locator('pre').textContent())!)).toEqual(compared);
 await successful.getByRole('button',{name:'후보 편집 준비'}).click();const confirmation=page.getByRole('dialog',{name:'후보 편집으로 전환'});await expect(confirmation).toContainText('외부 성공 후보');await confirmation.getByRole('button',{name:'현재 편집 유지'}).click();await page.getByRole('button',{name:'전략 만들기',exact:true}).click();await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue('보존할 미저장 초안');await expect(page.getByText('편집 중',{exact:true})).toBeVisible();
 // Switching the original report cancels prepared adoption and scopes history to the new source.
 await backToCandidates(page);await successful.getByRole('button',{name:'후보 편집 준비'}).click();await expect(confirmation).toBeVisible();
 await openOrigin(page,external.candidate.id);await expect(confirmation).toHaveCount(0);await page.getByRole('button',{name:'후보 이력 새로고침'}).click();await expect(history.locator('[data-candidate-id]')).toHaveCount(0);
 await openOrigin(page,initial.baseline.id);await page.getByRole('button',{name:'후보 이력 새로고침'}).click();await expect(history.locator('[data-candidate-id]')).toHaveCount(4);
 // A later edit invalidates prepared adoption rather than being silently replaced.
 await backToCandidates(page);await successful.getByRole('button',{name:'후보 편집 준비'}).click();await expect(confirmation).toBeVisible();await page.getByRole('button',{name:'전략 만들기',exact:true}).click();await page.getByLabel('전략 이름',{exact:true}).fill('확인 뒤 작성한 새 초안');await confirmation.getByRole('button',{name:'현재 초안 대신 후보 편집'}).click();await expect(page.getByRole('alert')).toContainText('편집 내용이 변경');await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue('확인 뒤 작성한 새 초안');
 await backToCandidates(page);const revisionResponse=response(page,'get_strategy');await successful.getByRole('button',{name:'후보 편집 준비'}).click();expect((await revisionResponse).request().postDataJSON().arguments.revisionId).toBe(external.candidate.revisionId);await expect(confirmation).toBeVisible();await confirmation.getByRole('button',{name:'현재 초안 대신 후보 편집'}).click();
 await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue('외부 성공 후보');await expect(page.getByLabel('백테스트 데이터')).toHaveValue(external.candidate.datasetId);await expect(page.getByLabel('백테스트 데이터').locator('option:checked')).toContainText('목록 범위 밖');
 // A bounded recent list is not proof the selected immutable snapshot vanished.
 await page.getByRole('button',{name:'데이터 목록 새로고침',exact:true}).click();await expect(page.getByRole('status').first()).toHaveText('전략·데이터·실행 목록을 동기화했습니다.');
 await expect(page.getByLabel('백테스트 데이터')).toHaveValue(external.candidate.datasetId);await expect(page.getByLabel('백테스트 데이터').locator(`option[value="${external.candidate.datasetId}"]`)).toHaveCount(1);
 await page.getByRole('button',{name:'데이터 목록 새로고침',exact:true}).click();await expect(page.getByRole('status').first()).toHaveText('전략·데이터·실행 목록을 동기화했습니다.');await expect(page.getByLabel('백테스트 데이터').locator('option')).toHaveCount(102);
 for(const [label,value] of [['초기 자본','2345678'],['수수료 (bp)','7'],['슬리피지 (bp)','4'],['매도 거래세 (bp)','3']])await expect(page.getByLabel(label==='초기 자본'?/^초기 자본 \(/:label,{exact:true})).toHaveValue(value);
 const after=await command('stats');expect(after.runs.map((r:any)=>r.id)).toEqual(before.runs.map((r:any)=>r.id));expect(after.baselineHash).toBe(after.initialBaselineHash);expect(actions).not.toContain('experiment');expect(actions).not.toContain('run');
 const savedResponse=response(page,'save_strategy');await page.getByRole('button',{name:'전략 저장',exact:true}).click();const savedHttp=await savedResponse;expect(savedHttp.request().postDataJSON().arguments.parentRevisionId).toBe(external.candidate.revisionId);expect((await savedHttp.json()).result.id).not.toBe(external.candidate.revisionId);
 const runResponse=response(page,'run');await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();const rerun=(await(await runResponse).json()).result;expect(rerun.status).toBe('succeeded');expect(rerun.settings).toEqual(external.candidate.settings);expect(rerun.manifest.dataHash).toBe(external.candidate.manifest.dataHash);expect((await command('stats')).baselineHash).toBe(before.initialBaselineHash);
});

for(const kind of ['failed','interrupted'] as const)test(`${kind} candidate opens persisted code for editing without experiment execution`,async({page})=>{
 test.setTimeout(90000);const initial=await nextLine();const external=await command('external');await page.goto(initial.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await openOrigin(page,initial.baseline.id);await page.getByRole('button',{name:'후보 이력 새로고침'}).click();
 const failed=page.locator(`[data-candidate-id="${external[kind].id}"]`);await expect(failed).toContainText(kind==='failed'?'실패':'중단');if(kind==='interrupted'){await expect(failed).toContainText('앱이 종료되기 전에 실행이 완료되지 않았습니다.');await expect(failed).not.toContainText('Previous execution stopped');}await expect(failed.getByRole('button',{name:'원본과 저장 결과 비교'})).toBeDisabled();const before=await command('stats');
 await failed.getByRole('button',{name:'후보 편집 준비'}).click();await expect(page.getByRole('dialog',{name:'후보 편집으로 전환'})).toContainText(kind==='failed'?'외부 실패 코드 후보':'외부 중단 코드 후보');await page.getByRole('button',{name:'현재 초안 대신 후보 편집'}).click();await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue(kind==='failed'?'외부 실패 코드 후보':'외부 중단 코드 후보');await expect(page.getByRole('button',{name:'Python 코드',exact:true})).toHaveAttribute('aria-pressed','true');await expect(page.getByText('편집 중',{exact:true})).toBeVisible();
 await page.getByLabel('전략 이름',{exact:true}).fill('실패 후보 수정');const savedResponse=response(page,'save_strategy');await page.getByRole('button',{name:'전략 저장',exact:true}).click();const http=await savedResponse;expect(http.request().postDataJSON().arguments.parentRevisionId).toBe(external[kind].revisionId);const saved=(await http.json()).result;expect(saved.definition.kind).toBe('python');expect(saved.definition.code).toContain('def on_bar(ctx)');
 const after=await command('stats');expect(after.runs.map((r:any)=>r.id)).toEqual(before.runs.map((r:any)=>r.id));expect(after.baselineHash).toBe(before.initialBaselineHash);
});

test('delayed actual revision lookup cannot replace a draft completed by an earlier file read',async({page})=>{
 test.setTimeout(90000);
 await page.addInitScript(()=>{const original=File.prototype.text;File.prototype.text=function(){if(this.name!=='late.py')return original.call(this);return new Promise<string>(accept=>{(window as any).releaseLateDraft=async()=>accept(await original.call(this));});};});
 const initial=await nextLine();const external=await command('external');await page.goto(initial.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await page.getByRole('button',{name:'Python 코드',exact:true}).click();await page.getByLabel('전략 이름',{exact:true}).fill('지연 초안');
 await page.getByLabel('.py 파일').setInputFiles({name:'late.py',mimeType:'text/plain',buffer:Buffer.from('def on_bar(ctx):\n    return {"action":"hold", "reason":"late draft"}\n')});await page.waitForFunction(()=>typeof (window as any).releaseLateDraft==='function');
 await openOrigin(page,initial.baseline.id);await page.getByRole('button',{name:'후보 이력 새로고침'}).click();await command('delay_lookup');
 await page.locator(`[data-candidate-id="${external.candidate.id}"]`).getByRole('button',{name:'후보 편집 준비'}).click();expect((await command('wait_lookup')).started).toBe(true);
 // Release an already-started UI file read while the real get_strategy handler waits.
 await page.evaluate(()=>(window as any).releaseLateDraft());await page.getByRole('button',{name:'전략 만들기',exact:true}).click();await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue('지연 초안');
 await command('release_lookup');await expect(page.getByRole('status').first()).toHaveText('원본 또는 편집 내용이 바뀌어 후보 불러오기를 취소했습니다.');await expect(page.getByRole('dialog',{name:'후보 편집으로 전환'})).toHaveCount(0);
 await page.getByText('고급: 전략 JSON 가져오기 / 현재 조건 확인',{exact:true}).click();await expect(page.locator('.json-import pre')).toContainText('late draft');await expect(page.getByText('편집 중',{exact:true})).toBeVisible();
});
