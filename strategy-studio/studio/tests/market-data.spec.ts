import {test,expect} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';

// All browser HTTP reaches actual Runtime; only provider and Registry profiles
// are injected fixtures. No live Meritz call or real customer credentials.
const root=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON||resolve(root,'../open-api-mcp/.venv/bin/python');
let child:ChildProcessWithoutNullStreams;
let nextLine:()=>Promise<any>;
let stderr='';
async function command(value:string){child.stdin.write(value+'\n');return nextLine();}
test.beforeEach(async()=>{
 stderr='';child=spawn(python,[resolve('tests/market_data_server.py'),resolve('dist/index.html')],{env:{PATH:process.env.PATH,PYTHONPATH:root,PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1'}});
 const queue:string[]=[];const waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});
 child.stderr.on('data',chunk=>{stderr+=chunk.toString();});
 nextLine=()=>queue.length?Promise.resolve(JSON.parse(queue.shift()!)):new Promise((accept,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture response timeout: '+stderr)),15000);waiters.push(line=>{clearTimeout(timer);accept(JSON.parse(line));});});
});
test.afterEach(async()=>{child.stdin.end('stop\n');await new Promise<void>(done=>{const timer=setTimeout(()=>{child.kill();done();},5000);child.once('exit',()=>{clearTimeout(timer);done();});});});
async function connect(page:any){await page.goto((await nextLine()).url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await page.getByText('메리츠 시세 조회',{exact:true}).click();await page.getByLabel('국내 종목 코드').fill('005930');await page.getByLabel('조회 시작일').fill('2025-01-01');await page.getByLabel('조회 종료일').fill('2025-01-10');}
function response(page:any,action:string){return page.waitForResponse((r:any)=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action===action);}
async function fetchData(page:any){const pending=response(page,'fetch_data');await page.getByRole('button',{name:'시세 조회 후 저장',exact:true}).click();return pending;}

test('fixture market fetch persists adapter warnings and executes exact snapshot; refresh discovers external Runtime writes',async({page})=>{
 await connect(page);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await expect(page.getByText('국내 일봉 · 시작일과 종료일 차이 최대 365일.',{exact:false})).toBeVisible();
 const fetched=await fetchData(page);expect(fetched.request().postDataJSON().arguments).toEqual({symbol:'005930',startDate:'2025-01-01',endDate:'2025-01-10'});
 const envelope=await fetched.json();expect(envelope.ok).toBe(true);const snapshot=envelope.result;
 expect(snapshot.source.kind).toBe('meritz');expect(snapshot.barCount).toBe(8);expect(snapshot.startDate).toBe('2025-01-02');expect(snapshot.endDate).toBe('2025-01-09');
 await expect(page.getByLabel('백테스트 데이터')).toHaveValue(snapshot.id);
 const evidence=page.getByRole('region',{name:'선택 데이터'});await expect(evidence).toContainText('005930');await expect(evidence).toContainText('저장된 실제 기간: 2025-01-02 ~ 2025-01-09 · 8봉');await expect(evidence).toContainText('요청 기간: 2025-01-01 ~ 2025-01-10');
 for(const warning of snapshot.warnings)await expect(evidence).toContainText(warning);
 expect(snapshot.warnings.some((w:string)=>w.includes('중복'))).toBe(true);expect(snapshot.warnings.some((w:string)=>w.includes('응답 코드를 확인할 수 없어'))).toBe(true);
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 const runResponse=response(page,'run');await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();const run=(await(await runResponse).json()).result;expect(run.status).toBe('succeeded');expect(run.manifest.dataHash).toBe(snapshot.contentHash);expect(run.datasetId).toBe(snapshot.id);expect(run.manifest.dataWarnings).toEqual(snapshot.warnings);
 const external=await command('external');expect(external.run.status).toBe('succeeded');
 await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await expect(page.locator('.library-item').filter({hasText:'외부 Runtime 생성 전략'})).toHaveCount(0);
 const seen:string[]=[];const listener=(request:any)=>{if(request.url().endsWith('/api/platform'))seen.push(request.postDataJSON().action);};page.on('request',listener);
 await page.getByRole('button',{name:'목록 새로고침',exact:true}).click();await expect(page.getByRole('status').first()).toHaveText('전략·데이터·실행 목록을 동기화했습니다.');page.off('request',listener);
 expect(seen.sort()).toEqual(['list_data','list_runs','list_strategies']);await expect(page.locator('.library-item').filter({hasText:'외부 Runtime 생성 전략'})).toBeVisible();await expect(page.locator('.library-item').filter({hasText:external.run.id.slice(0,16)})).toBeVisible();
 await page.getByRole('button',{name:'전략 만들기',exact:true}).click();await expect(page.getByLabel('백테스트 데이터').locator(`option[value="${external.dataset.id}"]`)).toHaveCount(1);await expect(page.getByLabel('백테스트 데이터')).toHaveValue(snapshot.id);
 // Data refresh also synchronizes all three shared lists without reconnecting.
 await command('external');await page.getByRole('button',{name:'데이터 목록 새로고침'}).click();await expect(page.getByRole('status').first()).toHaveText('전략·데이터·실행 목록을 동기화했습니다.');await expect(page.getByLabel('백테스트 데이터').locator('option')).toHaveCount(4);
 const stats=await command('stats');expect(stats.calls).toHaveLength(1);expect(stats.resolutions).toEqual([{account:null,require_explicit:true}]);expect(errors).toEqual([]);
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('365-day validation and ACCOUNT_REQUIRED preserve selected data, permit stored execution and later retry',async({page})=>{
 await connect(page);await page.getByLabel('조회 시작일').fill('2023-01-01');await page.getByRole('button',{name:'시세 조회 후 저장',exact:true}).click();await expect(page.getByRole('alert')).toContainText('최대 365일');expect((await command('stats')).calls).toHaveLength(0);
 await page.getByLabel('조회 시작일').fill('2025-01-01');const snapshot=(await(await fetchData(page)).json()).result;
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();await command('multiple');
 const failed=await fetchData(page);expect((await failed.json()).ok).toBe(false);await expect(page.getByRole('alert')).toContainText('등록 계좌가 여럿입니다');await expect(page.getByLabel('백테스트 데이터')).toHaveValue(snapshot.id);await expect(page.getByRole('button',{name:'시세 조회 후 저장',exact:true})).toBeEnabled();expect((await command('stats')).calls).toHaveLength(1);
 await expect(page.getByRole('button',{name:'백테스트 실행',exact:false})).toBeDisabled();await expect(page.getByRole('status').filter({hasText:'조회 시세를 아직'})).toContainText('2025-01');await page.getByRole('button',{name:'선택한 저장 시세로 계속',exact:true}).click();const runResponse=response(page,'run');await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();const run=(await(await runResponse).json()).result;expect(run.status).toBe('succeeded');expect(run.manifest.dataHash).toBe(snapshot.contentHash);
 await command('single');await page.getByRole('button',{name:'전략 만들기',exact:true}).click();await page.getByText('메리츠 시세 조회',{exact:true}).click();expect((await(await fetchData(page)).json()).ok).toBe(true);await expect(page.getByRole('alert')).toHaveCount(0);expect((await command('stats')).calls).toHaveLength(2);
 await expect(page.getByRole('button',{name:'선택한 저장 시세로 계속',exact:true})).toHaveCount(0);
 await command('multiple');expect((await(await fetchData(page)).json()).ok).toBe(false);await expect(page.getByRole('button',{name:'백테스트 실행',exact:false})).toBeDisabled();
 const external=await command('external');await page.getByRole('button',{name:'데이터 목록 새로고침'}).click();await expect(page.getByLabel('백테스트 데이터').locator(`option[value="${external.dataset.id}"]`)).toHaveCount(1);await expect(page.getByRole('button',{name:'백테스트 실행',exact:false})).toBeDisabled();await page.getByLabel('백테스트 데이터').selectOption(external.dataset.id);await expect(page.getByRole('button',{name:'선택한 저장 시세로 계속',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'백테스트 실행',exact:false})).toBeEnabled();
});

test('invalid market dates do not block the already selected saved snapshot',async({page})=>{
 await connect(page);const snapshot=(await(await fetchData(page)).json()).result;
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 await page.getByLabel('조회 시작일').fill('2025-01-11');
 await page.getByRole('button',{name:'시세 조회 후 저장',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('시작일 이후');
 await expect(page.getByLabel('백테스트 데이터')).toHaveValue(snapshot.id);
 await expect(page.getByRole('button',{name:'백테스트 실행',exact:false})).toBeEnabled();
 expect((await command('stats')).calls).toHaveLength(1);
});

test('dataset options distinguish symbols and imports; refresh preserves unsaved edits and revision parent',async({page})=>{
 await connect(page);
 const first=(await(await fetchData(page)).json()).result;
 await page.getByLabel('국내 종목 코드').fill('000660');
 const second=(await(await fetchData(page)).json()).result;
 const selection=page.getByLabel('백테스트 데이터');
 await expect(selection.locator(`option[value="${first.id}"]`)).toContainText('005930 ·');
 await expect(selection.locator(`option[value="${second.id}"]`)).toContainText('000660 ·');
 for(const id of [first.id,second.id])await expect(selection.locator(`option[value="${id}"]`)).toContainText('2025-01-02 ~ 2025-01-09 · 8봉');
 await page.getByText('내 데이터 가져오기 · CSV / JSON',{exact:true}).click();
 await page.getByLabel('내용',{exact:true}).fill('date,open,high,low,close\n2025-02-03,100,102,99,101\n2025-02-04,101,103,100,102');
 const importedResponse=response(page,'import_csv');await page.getByRole('button',{name:'데이터 검증 후 저장',exact:true}).click();const imported=(await(await importedResponse).json()).result;
 await expect(selection.locator(`option[value="${imported.id}"]`)).toContainText('가져온 데이터 · 2025-02-03 ~ 2025-02-04 · 2봉');
 const savedResponse=response(page,'save_strategy');await page.getByRole('button',{name:'전략 저장',exact:true}).click();const saved=(await(await savedResponse).json()).result;
 await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 await page.getByLabel('전략 이름',{exact:true}).fill('아직 저장하지 않은 전략 이름');await expect(page.getByText('편집 중',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'데이터 목록 새로고침',exact:true}).click();await expect(page.getByRole('status').first()).toHaveText('전략·데이터·실행 목록을 동기화했습니다.');
 await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue('아직 저장하지 않은 전략 이름');await expect(page.getByText('편집 중',{exact:true})).toBeVisible();await expect(selection).toHaveValue(imported.id);
 await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.getByRole('button',{name:'목록 새로고침',exact:true}).click();await expect(page.getByRole('status').first()).toHaveText('전략·데이터·실행 목록을 동기화했습니다.');await page.getByRole('button',{name:'전략 만들기',exact:true}).click();
 await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue('아직 저장하지 않은 전략 이름');await expect(page.getByText('편집 중',{exact:true})).toBeVisible();
 const revisedResponse=response(page,'save_strategy');await page.getByRole('button',{name:'전략 저장',exact:true}).click();const revisedHttp=await revisedResponse;expect(revisedHttp.request().postDataJSON().arguments.parentRevisionId).toBe(saved.id);
 const revised=(await revisedHttp.json()).result;expect(revised.parentRevisionId).toBe(saved.id);expect(revised.definition.name).toBe('아직 저장하지 않은 전략 이름');expect(revised.id).not.toBe(saved.id);await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
});


test('US snapshot requires explicit venue and quote mode, preserves USD and rejects overlong period without replacing data',async({page})=>{
 await connect(page);
 await page.getByRole('combobox',{name:'조회 시장',exact:true}).selectOption('US');
 await page.getByLabel('미국 종목 기호',{exact:true}).fill('SOXL');
 await expect(page.getByRole('button',{name:'시세 조회 후 저장',exact:true})).toBeDisabled();
 await page.getByRole('combobox',{name:'미국 거래소',exact:true}).selectOption('AX');
 await expect(page.getByRole('button',{name:'시세 조회 후 저장',exact:true})).toBeDisabled();
 await page.getByRole('combobox',{name:'미국 시세 구분',exact:true}).selectOption('1');
 const fetched=await fetchData(page);
 expect(fetched.request().postDataJSON().arguments).toEqual({symbol:'SOXL',startDate:'2025-01-01',endDate:'2025-01-10',market:'US',exchange:'AX',quoteMode:'1'});
 const envelope=await fetched.json();expect(envelope.ok).toBe(true);const snapshot=envelope.result;
 expect(snapshot.source).toMatchObject({currency:'USD',timezone:'America/New_York',exchange:'AX',quoteMode:'1'});
 await expect(page.getByLabel('초기 자본 (USD)',{exact:true})).toBeVisible();
 await expect(page.getByText('금액은 선택 데이터의 통화 단위이며 자동 환산하지 않습니다.',{exact:false})).toBeVisible();
 await page.getByLabel('초기 자본 (USD)',{exact:true}).fill('10000');
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 const pending=response(page,'run');await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();const run=(await(await pending).json()).result;
 expect(run.status).toBe('succeeded');expect(run.settings.initialCash).toBe(10000);expect(run.report.source.currency).toBe('USD');expect(run.manifest.dataHash).toBe(snapshot.contentHash);
 await expect(page.locator('.chart-legend')).toContainText('단위: USD');await expect(page.locator('.equity-chart')).not.toContainText('만');
 await page.getByRole('button',{name:'전략 만들기',exact:true}).click();await page.getByText('메리츠 시세 조회',{exact:true}).click();
 await page.getByLabel('조회 시작일').fill('2020-01-01');expect((await(await fetchData(page)).json()).ok).toBe(false);
 await expect(page.getByRole('alert')).toContainText('3년');await expect(page.getByLabel('백테스트 데이터')).toHaveValue(snapshot.id);
 expect((await command('stats')).calls).toHaveLength(1);
 await page.setViewportSize({width:320,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
});
