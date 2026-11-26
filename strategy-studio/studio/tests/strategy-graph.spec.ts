import {test,expect,type Page} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
const root=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON||resolve(root,'../open-api-mcp/.venv/bin/python');
let child:ChildProcessWithoutNullStreams,nextLine:()=>Promise<any>,stderr='';
async function command(action:string,extra:Record<string,unknown>={}){child.stdin.write(JSON.stringify({action,...extra})+'\n');return nextLine();}
let browserErrors:string[]=[];
test.beforeEach(({page})=>{
 browserErrors=[];const watch=(target:Page)=>{target.on('pageerror',error=>browserErrors.push(error.message));target.on('console',message=>{if(message.type()==='error')browserErrors.push(message.text());});};watch(page);page.context().on('page',watch);
 child=spawn(python,[resolve('tests/strategy_graph_server.py'),resolve('dist/index.html')],{env:{PATH:process.env.PATH,PYTHONPATH:root,PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1'}});stderr='';const queue:string[]=[],waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});child.stderr.on('data',chunk=>{stderr+=chunk.toString();});
 nextLine=()=>queue.length?Promise.resolve(JSON.parse(queue.shift()!)):new Promise((accept,reject)=>{const timer=setTimeout(()=>reject(Error('Runtime timeout: '+stderr)),15000);waiters.push(line=>{clearTimeout(timer);accept(JSON.parse(line));});});
});
test.afterEach(async()=>{child.stdin.end('{"action":"stop"}\n');await new Promise<void>(done=>{const timer=setTimeout(()=>{child.kill();done();},5000);child.once('exit',()=>{clearTimeout(timer);done();});});expect(browserErrors).toEqual([]);});
async function launch(page:Page){const first=await nextLine();await page.goto(first.url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();return first;}
async function connect(page:Page,from:string,to:string,port:string){await page.getByRole('button',{name:from+' 출력',exact:true}).click();await page.getByRole('button',{name:`${to} ${port} 입력`,exact:true}).click();}
async function save(page:Page){const response=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='save_strategy');await page.getByRole('button',{name:'전략 저장',exact:true}).click();const envelope=await(await response).json();expect(envelope.ok).toBe(true);return envelope.result;}

test('graph adds and connects real AND/RSI rules, persists layout and matches Runtime JSON execution',async({page})=>{
 const first=await launch(page);const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));await page.getByLabel('전략 이름',{exact:true}).fill('그래프 실제 전략');await page.getByRole('button',{name:'노드 연결 편집'}).click();
 await page.getByRole('button',{name:'buy condition 연결 삭제',exact:true}).click();await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'전략 검증',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Python 코드',exact:true})).toBeDisabled();await page.getByText(/^고급: 전략 JSON 가져오기/).click();await expect(page.getByRole('button',{name:'검증 후 가져오기'})).toBeDisabled();
 await page.getByRole('button',{name:'AND 추가',exact:true}).click();await page.getByRole('button',{name:'상대강도 RSI 추가',exact:true}).click();await page.getByLabel('노드 기간',{exact:true}).fill('14');await page.getByRole('button',{name:'숫자 추가',exact:true}).click();await page.getByLabel('노드 숫자',{exact:true}).fill('70');await page.getByRole('button',{name:'비교 추가',exact:true}).click();await page.getByRole('combobox',{name:'노드 비교 방식',exact:true}).selectOption('lt');
 await connect(page,'entry','node-1','condition.0');await connect(page,'node-2','node-4','left');await connect(page,'node-3','node-4','right');await connect(page,'node-4','node-1','condition.1');await connect(page,'node-1','buy','condition');
 await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();
 const handle=page.getByRole('button',{name:'node-1 노드 이동',exact:true});await handle.focus();await handle.press('ArrowRight');await handle.press('ArrowDown');
 const position=await page.locator('[data-node-id="node-1"]').evaluate(element=>({x:parseFloat((element as HTMLElement).style.left),y:parseFloat((element as HTMLElement).style.top)}));
 await page.getByRole('button',{name:'직접 조건 편집'}).click();await expect(page.getByLabel('조건 결합 0')).toHaveValue('all');await page.getByRole('button',{name:'노드 연결 편집'}).click();
 const saved=await save(page);expect(saved.definition.entry).toEqual({op:'all',conditions:[{op:'cross_above',left:{type:'sma',period:5},right:{type:'sma',period:20}},{op:'lt',left:{type:'rsi',period:14},right:{type:'constant',value:70}}]});expect(saved.editorLayout.entry).toEqual(position);
 await page.getByLabel('백테스트 데이터',{exact:true}).selectOption(first.krw);const response=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='run');await page.getByRole('button',{name:/^백테스트 실행/}).click();const run=(await(await response).json()).result;const proof=await command('equivalence',{runId:run.id});expect(proof.sameDefinitionHash).toBe(true);expect(proof.sameResultHash).toBe(true);
 await page.goto('about:blank');await page.goto((await command('ticket')).url);await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.getByRole('button',{name:/그래프 실제 전략/}).last().click();await page.getByRole('button',{name:'노드 연결 편집'}).click();const restored=await page.locator('[data-node-id="entry"]').evaluate(element=>({x:parseFloat((element as HTMLElement).style.left),y:parseFloat((element as HTMLElement).style.top)}));expect(restored).toEqual(position);
 await page.setViewportSize({width:320,height:800});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.getByRole('button',{name:'entry.conditions.1.left 속성 편집',exact:true}).click();await expect(page.getByLabel('노드 기간',{exact:true})).toHaveValue('14');await expect(page.locator('.tree-handle small')).toHaveCount(0);await expect(page.locator('.tree-inspector h4')).toHaveText('상대강도 RSI · 14봉');for(const [label,width,height] of [['mobile',320,800],['desktop',1440,1000]] as const){
  const visual=await page.context().newPage();await visual.setViewportSize({width,height});await visual.goto((await command('ticket')).url);await expect(visual.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await visual.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await visual.getByRole('button',{name:/그래프 실제 전략/}).last().click();await visual.getByRole('button',{name:'노드 연결 편집'}).click();await visual.getByRole('button',{name:'기본 배치',exact:true}).click();await expect(visual.locator('.studio')).toHaveCount(1);await visual.evaluate(()=>{window.scrollTo(0,0);document.querySelector('.tree-viewport')?.scrollTo(0,0);});await visual.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));await visual.screenshot({path:`test-results/strategy-graph-${label}.png`,fullPage:true});await visual.close();
 }expect(errors).toEqual([]);
});

test('palette button, pointer movement, keyboard connections and incomplete draft survive navigation',async({page})=>{
 await launch(page);await save(page);await page.getByRole('button',{name:'노드 연결 편집'}).click();const palette=page.getByRole('button',{name:'숫자 추가',exact:true});await palette.click();await expect(page.locator('[data-node-id="node-1"]')).toBeVisible();await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeDisabled();
 const handle=page.getByRole('button',{name:'node-1 노드 이동',exact:true});await handle.scrollIntoViewIfNeeded();const before=await handle.boundingBox();await page.mouse.move(before!.x+10,before!.y+10);await page.mouse.down();await page.mouse.move(before!.x+55,before!.y+55,{steps:5});await page.mouse.up();expect(await page.locator('[data-node-id="node-1"]').evaluate(node=>parseFloat((node as HTMLElement).style.left))).toBeGreaterThan(40);
 await page.getByRole('navigation').getByRole('button',{name:'홈',exact:true}).click();await page.locator('.home').getByRole('button',{name:/나의 이동평균 전략/}).click();await expect(page.getByRole('alert')).toContainText('미완성 그래프');await expect(page.getByRole('dialog',{name:'저장 전략 열기 확인'})).toHaveCount(0);await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.locator('.library-item').filter({hasText:'나의 이동평균 전략'}).click();await expect(page.getByRole('alert')).toContainText('미완성 그래프');await page.getByRole('button',{name:'전략 만들기',exact:true}).click();await expect(page.locator('[data-node-id="node-1"]')).toHaveCount(1);await page.getByRole('button',{name:'node-1 노드 이동',exact:true}).focus();await page.keyboard.press('Delete');await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'buy condition 연결 삭제',exact:true}).click();await page.getByRole('button',{name:'entry 출력',exact:true}).focus();await page.keyboard.press('Enter');await page.getByRole('button',{name:'buy condition 입력',exact:true}).focus();await page.keyboard.press('Enter');await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'buy condition 연결 삭제',exact:true}).click();
 const output=page.getByRole('button',{name:'entry 출력',exact:true}),input=page.getByRole('button',{name:'buy condition 입력',exact:true});
 await output.scrollIntoViewIfNeeded();await input.scrollIntoViewIfNeeded();const a=await output.boundingBox(),b=await input.boundingBox();await page.mouse.move(a!.x+a!.width/2,a!.y+a!.height/2);await page.mouse.down();await page.mouse.move(b!.x+b!.width/2,b!.y+b!.height/2,{steps:8});await page.mouse.up();await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();
});

test('palette drop uses the visible point after vertical canvas scrolling',async({page})=>{
 await launch(page);await save(page);await page.getByRole('button',{name:'노드 연결 편집'}).click();
 for(let i=0;i<6;i++)await page.getByRole('button',{name:'AND 추가',exact:true}).click();
 const viewport=page.locator('.tree-viewport'),canvas=page.locator('.tree-canvas');
 const before=await viewport.evaluate(e=>{e.scrollTop=e.scrollHeight;const r=e.getBoundingClientRect();return {scrollTop:e.scrollTop,clientHeight:e.clientHeight,x:r.left+150,y:r.top+120};});
 expect(before.scrollTop).toBeGreaterThan(0);
 await page.evaluate(({x,y})=>{const source=document.querySelector('.tree-palette button[draggable="true"]') as HTMLButtonElement;const canvas=document.querySelector('.tree-canvas')!;const transfer=new DataTransfer();source.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));canvas.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,clientX:x,clientY:y,dataTransfer:transfer}));canvas.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,clientX:x,clientY:y,dataTransfer:transfer}));},{x:before.x,y:before.y});
 const dropped=page.locator('[data-node-id^="node-"]').last();await expect(dropped).toBeVisible();const top=await dropped.evaluate(e=>parseFloat((e as HTMLElement).style.top));expect(top).toBeGreaterThan(before.scrollTop);expect(Math.abs(top-(before.scrollTop+120))).toBeLessThanOrEqual(2);
});

test('currency change gates only execution until amount is explicitly confirmed',async({page})=>{
 const first=await launch(page);await save(page);const dataset=page.getByLabel('백테스트 데이터',{exact:true}),execute=page.getByRole('button',{name:/^백테스트 실행/});await dataset.selectOption(first.krw);await expect(execute).toBeEnabled();await dataset.selectOption(first.usd);await expect(execute).toBeDisabled();await expect(page.getByText('초기 자본 확인 필요',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();await expect(page.getByRole('button',{name:'전략 검증',exact:true})).toBeEnabled();await page.getByRole('button',{name:'금액 확인',exact:true}).click();await expect(execute).toBeEnabled();
 await dataset.selectOption(first.krw);await expect(execute).toBeDisabled();await page.getByLabel('초기 자본 (KRW)',{exact:true}).fill('50000');await expect(execute).toBeEnabled();await dataset.selectOption(first.unknown);await expect(execute).toBeDisabled();await page.getByRole('button',{name:'금액 확인',exact:true}).click();await expect(execute).toBeEnabled();await dataset.selectOption(first.unknown2);await expect(execute).toBeDisabled();
});

test('direct form depth errors block API saves and validation while remaining editable',async({page})=>{
 await launch(page);const actions:string[]=[];page.on('request',r=>{if(r.url().endsWith('/api/platform'))actions.push(r.postDataJSON().action);});
 for(let depth=0;depth<=6;depth++)await page.getByLabel(`조건 결합 ${depth}`,{exact:true}).first().selectOption('all');
 await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'전략 검증',exact:true})).toBeDisabled();await expect(page.getByText(/저장·검증·실행 불가:/)).toContainText('깊이 8');
 await page.getByRole('button',{name:'노드 연결 편집',exact:true}).click();await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeDisabled();await page.getByRole('button',{name:'직접 조건 편집',exact:true}).click();
 expect(actions.filter(action=>['save_strategy','validate_strategy'].includes(action))).toEqual([]);
 await page.getByLabel('조건 결합 6',{exact:true}).first().selectOption('single');await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();await expect(page.getByRole('button',{name:'전략 검증',exact:true})).toBeEnabled();await save(page);
});

test('USD candidate adoption explicitly confirms capital amount and currency',async({page})=>{
 const first=await launch(page),saved=await save(page);await page.getByLabel('백테스트 데이터',{exact:true}).selectOption(first.krw);
 const baseline=await command('run',{arguments:{revisionId:saved.id,datasetId:first.usd,settings:{initialCash:50000,feeBps:15,slippageBps:10,taxBps:0}}});const candidate=await command('experiment',{arguments:{runId:baseline.id,definition:{...saved.definition,name:'USD 후보 확인'}}});
 await page.getByRole('button',{name:'전략과 실행 기록',exact:true}).click();await page.getByRole('button',{name:'목록 새로고침',exact:true}).click();await page.getByRole('button').filter({has:page.locator('small',{hasText:baseline.id.slice(0,16)})}).click();await page.getByRole('button',{name:'개선 실험',exact:true}).click();await page.getByRole('button',{name:'후보 이력 새로고침',exact:true}).click();await page.locator(`[data-candidate-id="${candidate.id}"]`).getByRole('button',{name:'후보 편집 준비',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'후보 편집으로 전환'});await expect(dialog).toContainText('초기 자본 50000 USD');await expect(dialog).toContainText('초기 자본 금액과 통화 단위를 함께 확인');await dialog.getByRole('button',{name:'현재 초안 대신 후보 편집',exact:true}).click();await expect(page.getByLabel('초기 자본 (USD)',{exact:true})).toHaveValue('50000');await expect(page.getByText('초기 자본 확인 필요',{exact:true})).toHaveCount(0);await save(page);await expect(page.getByRole('button',{name:/^백테스트 실행/})).toBeEnabled();
});

test('plain-language explanation follows edited conditions and never describes a stale valid graph',async({page})=>{
 await launch(page);const summary=page.getByRole('region',{name:'현재 전략 설명'});
 await expect(summary).toContainText('5개 봉 평균 가격');await expect(summary).toContainText('20개 봉 평균 가격');
 await page.getByLabel('기간 (봉)',{exact:true}).first().fill('10');await expect(summary).toContainText('10개 봉 평균 가격');await expect(summary.locator('dl > div').filter({has:page.locator('dt',{hasText:'살 때'})})).not.toContainText('5개 봉 평균 가격');
 await page.getByLabel('손절 (%)',{exact:true}).fill('5');await expect(summary).toContainText('5% 하락');await expect(summary).toContainText('이때도 1주씩 매도');await expect(summary).toContainText('수수료와 슬리피지를 포함한 주당 평균 매입원가');await expect(summary).toContainText('보유 주식이 없으면 매수 조건에 따라 삽니다');await expect(summary).toContainText('다음 봉 시가');
 await page.getByRole('button',{name:'노드 연결 편집',exact:true}).click();await page.getByRole('button',{name:'buy condition 연결 삭제',exact:true}).click();
 await expect(page.locator('.json-import summary')).toContainText('마지막 완성 조건');await page.locator('.json-import summary').click();await expect(page.locator('.json-import')).toContainText('지금 편집 중인 미완성 연결은 포함되지 않습니다');await expect(summary).toHaveCount(0);await expect(page.getByText('현재 조건에 수정할 항목이 있습니다.',{exact:false})).toBeVisible();
 await page.setViewportSize({width:320,height:800});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('eight examples expose beginner guidance while edits update the actual strategy explanation',async({page})=>{
 await launch(page);const selector=page.getByRole('combobox',{name:'예제 전략',exact:true});await expect(selector.locator('option')).toHaveCount(36);
 const expected:Record<string,{guide:string;buy:RegExp;sell:RegExp;current:string}>={
 'SMA 5/20 교차':{guide:'짧은 흐름과 긴 흐름의 교차',buy:/5.*20.*위로/,sell:/5.*20.*아래로/,current:'5개 봉 평균 가격'},
 'EMA 12/26 교차':{guide:'최근 종가에 더 큰 비중',buy:/12.*26.*위로/,sell:/12.*26.*아래로/,current:'최근 가격을 더 반영한 12개 봉'},
 'RSI 30 회복 · 70 이탈':{guide:'기준선 통과',buy:/30 이하.*30보다 높/,sell:/70 이상.*70보다 낮/,current:'가격 상승·하락 강도(14개 봉 RSI)'},
 '종가와 20일 평균 교차':{guide:'현재 종가와 SMA',buy:/종가.*평균 위로/,sell:/종가.*평균 아래로/,current:'종가 값이 20개 봉 평균 가격'},
 'SMA 20/60 교차':{guide:'SMA(최근 종가의 단순 평균)',buy:/20.*60.*위로/,sell:/20.*60.*아래로/,current:'20개 봉 평균 가격'},
 'SMA 교차 + RSI 확인':{guide:'조건을 함께 확인',buy:/같은 봉.*50보다 높/,sell:/RSI와 관계없이 전량/,current:'모두 만족:'},
 '낮은 RSI에서 매 봉 1주':{guide:'낮게 유지되는 동안 조금씩',buy:/30보다 낮은 모든 봉.*1주/,sell:/70보다 높은 모든 봉.*1주/,current:'각 봉마다 추가로'},
 '한 번 매수하고 보유':{guide:'매도하지 않고 보유',buy:/첫 봉.*다음 봉/,sell:/자동 매도 조건이 없습니다/,current:'성립하지 않는 조건입니다.'}
 };
 for(const templateName of Object.keys(expected)){
  await selector.selectOption({label:templateName});const guide=page.locator('.template-guide');await guide.locator('summary').click();
  const name=await page.getByLabel('전략 이름',{exact:true}).inputValue(),text=expected[name];expect(text).toBeTruthy();await expect(guide).toContainText(text.guide);await expect(guide.locator('dl > div').filter({has:page.locator('dt',{hasText:'매수 규칙'})})).toContainText(text.buy);await expect(guide.locator('dl > div').filter({has:page.locator('dt',{hasText:'매도 규칙'})})).toContainText(text.sell);await expect(page.getByRole('region',{name:'현재 전략 설명'})).toContainText(text.current);
  await expect(guide).toContainText('매수 규칙');await expect(guide).toContainText('매도 규칙');await expect(guide).toContainText('거래 수량');await expect(guide).toContainText('예제 기준');
  await expect(page.getByRole('region',{name:'현재 전략 설명'})).toBeVisible();await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();if(templateName==='SMA 5/20 교차')await page.addStyleTag({content:'.status-row{display:none!important}'});await page.screenshot({path:'test-results/guide-template-picker.png',fullPage:true});
  await guide.locator('summary').click();
 }
 await selector.selectOption('0');await expect(page.getByRole('region',{name:'현재 전략 설명'})).toContainText('5개 봉 평균 가격');
 await page.getByLabel('기간 (봉)',{exact:true}).first().fill('8');await expect(page.getByRole('region',{name:'현재 전략 설명'})).toContainText('8개 봉 평균 가격');
 const saved=await save(page);expect(saved.definition.entry.left.period).toBe(8);
 await selector.selectOption('0');await expect(page.getByLabel('기간 (봉)',{exact:true}).first()).toHaveValue('5');
 await page.setViewportSize({width:320,height:800});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('form edits discard obsolete layout, Python hides template guide and invalid periods give editable guidance',async({page})=>{
 await launch(page);await page.getByRole('combobox',{name:'예제 전략',exact:true}).selectOption('0');await page.getByRole('button',{name:'노드 연결 편집',exact:true}).click();await page.getByRole('button',{name:'entry 노드 이동',exact:true}).press('ArrowRight');await page.getByRole('button',{name:'직접 조건 편집',exact:true}).click();await page.getByLabel('조건 결합 0',{exact:true}).selectOption('all');await expect(page.getByText(/저장된 배치 일부가 유효하지/)).toHaveCount(0);
 await page.getByLabel('기간 (봉)',{exact:true}).first().fill('1');await expect(page.getByText('현재 조건에 수정할 항목이 있습니다.',{exact:false})).toBeVisible();await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeDisabled();await expect(page.getByText(/저장·검증·실행 불가:/)).not.toContainText('entry.');await page.getByLabel('기간 (봉)',{exact:true}).first().fill('5');await expect(page.getByRole('button',{name:'전략 저장',exact:true})).toBeEnabled();
 await expect(page.locator('.template-guide')).toHaveCount(1);await page.getByRole('button',{name:'Python 코드',exact:true}).click();await expect(page.locator('.template-guide')).toHaveCount(0);
});

test('practice prices and costs have plain-language units without changing bp inputs',async({page})=>{
 await launch(page);await expect(page.getByRole('region',{name:'메리츠 시세 조회',exact:true})).toBeVisible();await expect(page.getByLabel('국내 종목 코드')).toBeVisible();await expect(page.getByRole('button',{name:'예시 시세로 연습',exact:true})).toBeHidden();await page.getByText('연습용 시세',{exact:true}).click();await expect(page.getByRole('button',{name:'예시 시세로 연습',exact:true})).toBeVisible();await expect(page.getByText('만들어진 예시 가격입니다. 실제 종목의 투자 성과가 아닙니다.')).toBeVisible();await page.getByLabel('수수료 (bp)',{exact:true}).fill('12.5');await expect(page.getByLabel('수수료 (bp) 환산',{exact:true})).toHaveText('0.125%');await page.getByLabel('슬리피지 (bp)',{exact:true}).fill('20');await expect(page.getByLabel('슬리피지 (bp) 환산',{exact:true})).toHaveText('0.2%');await expect(page.getByText(/슬리피지는 예상 가격보다 불리하게/)).toBeVisible();await expect(page.getByText('Claude 요청 문구',{exact:true})).toHaveCount(0);
});
