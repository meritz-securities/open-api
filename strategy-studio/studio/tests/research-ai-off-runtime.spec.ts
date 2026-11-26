import {test,expect} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';

// 고객 구성(AI 미연결, 라이브러리 기본값 ai_studio=False) 실제 런타임에서 "기간 나눠 개선·검증" 이 끝까지 간다:
// 개선 기간 계산 → (후보 0건) 원본으로 확정 → 검증 기간 계산. AI UI 는 어디에도 없어야 한다. 합성 시세만 사용.
const runtimeRoot=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON;
let child:ChildProcessWithoutNullStreams;let nextLine:()=>Promise<any>;let errors='';
test.beforeAll(async()=>{
 if(!python)throw Error('Set MERITZ_TEST_PYTHON to the pinned test interpreter.');
 child=spawn(python,[resolve('tests/runtime_server.py'),resolve('dist/index.html')],{env:{PATH:process.env.PATH,PYTHONPATH:runtimeRoot,PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1'}});
 const queue:string[]=[];const waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});
 child.stderr.on('data',chunk=>{errors+=chunk.toString();});
 nextLine=async()=>JSON.parse(await(queue.length?Promise.resolve(queue.shift()!):new Promise<string>((accept,reject)=>{const timer=setTimeout(()=>reject(Error('Runtime launch timeout: '+errors)),20000);waiters.push(line=>{clearTimeout(timer);accept(line);});})));
});
test.afterAll(async()=>{if(child){child.stdin.end('stop\n');await new Promise<void>(done=>{const timeout=setTimeout(()=>{child.kill();done();},5000);child.once('exit',()=>{clearTimeout(timeout);done();});});}});

test('customer runtime (AI off): separated improvement completes with original-only freeze; no AI UI; local data reset works',async({page})=>{
 test.setTimeout(180000);await page.emulateMedia({reducedMotion:'reduce'});
 const pageErrors:string[]=[];page.on('pageerror',e=>pageErrors.push(e.message));
 const first=await nextLine();await page.goto(first.url);
 await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');
 await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await expect(page.getByText('Claude')).toHaveCount(0);await expect(page.getByText('AI 요청 문구')).toHaveCount(0);
 await page.getByText('연습용 시세',{exact:true}).click();await page.getByRole('button',{name:'예시 시세로 연습'}).click();await expect(page.getByLabel('백테스트 데이터')).not.toHaveValue('');
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 const execution=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='run');
 await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();expect((await(await execution).json()).ok).toBe(true);
 await expect(page.getByRole('heading',{name:'실행 리포트'})).toBeVisible();
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();
 await page.getByRole('button',{name:'기간 나눠 개선·검증'}).click();
 // 연구 화면: AI 패널 없음 · 기간 저장(세션 생성)
 await expect(page.getByText('Claude')).toHaveCount(0);
 await page.getByRole('textbox',{name:'기간 분리 개선 목적'}).fill('AI 없이 원본 전략의 기간 분리 검증');
 const create=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='create_research');
 await page.getByRole('button',{name:'기간 저장',exact:true}).click();expect((await(await create).json()).ok).toBe(true);
 const prepare=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='prepare_research_training');
 await page.getByRole('button',{name:'개선 기간 계산',exact:true}).click();expect((await(await prepare).json()).ok).toBe(true);
 await expect(page.getByText('원본 전략만으로 확정')).toBeVisible({timeout:60000});
 const freezeButton=page.getByRole('button',{name:'원본으로 확정',exact:true});await expect(freezeButton).toBeEnabled();
 const freeze=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='freeze_research');
 await freezeButton.click();const frozen=(await(await freeze).json());expect(frozen.ok).toBe(true);
 await expect(page.getByText('후보 없이 원본 전략으로 확정했습니다.')).toBeVisible();
 const evaluate=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='evaluate_research');
 await page.getByRole('button',{name:'검증 기간 계산',exact:true}).click();expect((await(await evaluate).json()).ok).toBe(true);
 await expect(page.getByRole('heading',{name:'3. 검증 기간 결과'})).toBeVisible();
 await expect(page.getByText('후보 제출 대기')).toHaveCount(0);
 // 로컬 데이터 초기화(두 단계 확인) → 목록이 빈다
 await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략과 실행 기록',exact:true}).click();
 await expect(page.getByText(/저장 위치: /)).toBeVisible();
 await page.getByRole('button',{name:'로컬 데이터 초기화',exact:true}).click();
 await expect(page.getByRole('group',{name:'초기화 확인'})).toContainText('모두 삭제합니다');
 const reset=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='reset_local_data');
 await page.getByRole('button',{name:'모두 삭제',exact:true}).click();expect((await(await reset).json()).ok).toBe(true);
 await expect(page.getByText('아직 저장된 전략이 없습니다.')).toBeVisible();await expect(page.getByText('아직 실행 기록이 없습니다.')).toBeVisible();
 expect(pageErrors).toEqual([]);
});
