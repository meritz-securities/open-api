import {test,expect} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';

// 고객 구성(AI 미연결, 라이브러리 기본값) 실제 런타임에서 **예제 전략 35개 전수**를 화면으로 저장→검증→실행한다.
// 빌더가 직접 편집하지 못하는 지표(볼린저·MACD 등)를 쓰는 예제는 "읽기 전용" 안내가 뜨되 저장·실행이 막히지 않고,
// 예제 선택이 계속 가능해야 한다(갇힘 없음). 라이브 시세·외부 호출 없음: 합성 예시 시세만 사용한다.
const runtimeRoot=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON;
let child:ChildProcessWithoutNullStreams;
let nextLine:()=>Promise<any>;
let errors='';
const BUILDER_EDITABLE=new Set(['SMA 5/20 교차','EMA 12/26 교차','종가와 20일 평균 교차','낮은 RSI에서 매 봉 1주','RSI 30 회복 · 70 이탈','SMA 20/60 교차','SMA 교차 + RSI 확인','한 번 매수하고 보유']);
test.beforeAll(async()=>{
 if(!python)throw Error('Set MERITZ_TEST_PYTHON to the pinned test interpreter.');
 child=spawn(python,[resolve('tests/runtime_server.py'),resolve('dist/index.html')],{env:{PATH:process.env.PATH,PYTHONPATH:runtimeRoot,PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1'}});
 const queue:string[]=[];const waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});
 child.stderr.on('data',chunk=>{errors+=chunk.toString();});
 nextLine=async()=>JSON.parse(await(queue.length?Promise.resolve(queue.shift()!):new Promise<string>((accept,reject)=>{const timer=setTimeout(()=>reject(Error('Runtime launch timeout: '+errors)),20000);waiters.push(line=>{clearTimeout(timer);accept(line);});})));
});
test.afterAll(async()=>{if(!child)return;child.stdin.end('stop\n');if(child.exitCode!==null||child.signalCode!==null)return;await new Promise<void>(done=>{const finish=()=>{clearTimeout(timeout);done();};const timeout=setTimeout(()=>{child.kill();finish();},5000);child.once('exit',finish);child.once('error',finish);});});

test('customer runtime: all 35 templates save, validate and run; unsupported-builder templates are read-only but never trap',async({page})=>{
 await page.emulateMedia({reducedMotion:'reduce'});
 const pageErrors:string[]=[];page.on('pageerror',e=>pageErrors.push(e.message));
 const first=await nextLine();await page.goto(first.url);
 await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');
 await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 // 고객 구성: AI UI 가 없다.
 await expect(page.getByText('Claude')).toHaveCount(0);
 await expect(page.getByText('AI 요청 문구')).toHaveCount(0);
 // 합성 시세 1회 준비
 await page.getByText('연습용 시세',{exact:true}).click();await page.getByRole('button',{name:'예시 시세로 연습'}).click();await expect(page.getByLabel('백테스트 데이터')).not.toHaveValue('');
 const select=page.getByLabel('예제 전략');
 const names=(await select.locator('option').allTextContents()).filter(name=>name&&name!=='예제를 선택하세요');
 expect(names.length).toBe(35);
 const summary:{name:string;readonly:boolean;returnPct:number;trades:number}[]=[];
 for(const [index,name] of names.entries()){
  await expect(select,`예제 선택이 잠기면 안 된다 (${name} 직전)`).toBeEnabled();
  await select.selectOption({index:index+1});
  await expect(page.getByLabel('전략 이름')).toHaveValue(name);
  const readonly=await page.getByRole('status',{name:'읽기 전용 전략'}).isVisible();
  expect(readonly,`${name}: 빌더 편집 가능 여부`).toBe(!BUILDER_EDITABLE.has(name));
  if(readonly)await expect(page.getByText('저장·검증·실행 불가')).toHaveCount(0);
  // 요약이 미지원 지표를 "RSI" 로 오표기하지 않는다(볼린저 예제 기준 점검).
  if(name.includes('볼린저'))await expect(page.getByLabel('현재 전략 설명')).toContainText('볼린저 밴드');
  await page.getByRole('button',{name:'전략 검증',exact:true}).click();await expect(page.getByText('지원 규칙 검증 통과')).toBeVisible();
  await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
  const execution=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='run');
  await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();
  const envelope=await(await execution).json();expect(envelope.ok,`${name}: run envelope`).toBe(true);expect(envelope.result.status,`${name}: run status`).toBe('succeeded');
  await expect(page.getByRole('heading',{name:'실행 리포트'})).toBeVisible();
  summary.push({name,readonly,returnPct:envelope.result.report.metrics.totalReturnPct,trades:envelope.result.report.trades.length});
  await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 }
 expect(summary.filter(s=>s.readonly).length).toBe(27);
 expect(summary.filter(s=>!s.readonly).length).toBe(8);
 console.log(JSON.stringify(summary));
 expect(pageErrors).toEqual([]);
});
