import {test,expect} from '@playwright/test';
test('global error banner translates transport errors for customers',async({page})=>{
 let abortListRefresh=false;
 await page.route('**/api/session',route=>route.fulfill({json:{ok:true,result:{token:'error-banner-token'}}}));
 await page.route('**/api/platform',async route=>{
  const {action}=route.request().postDataJSON();
  if(abortListRefresh&&action==='list_strategies'){abortListRefresh=false;return route.abort('failed');}
  if(action==='validate_strategy')return route.fulfill({status:413,json:{ok:false,error:{code:'LIMIT',message:'Request too large'}}});
  const result=action==='get_ai_connection'?{configured:false,managementAllowed:false,configuration:null}:action==='capabilities'?{pythonStrategy:false,aiStudio:false}:action==='templates'?{templates:[]}:action==='list_strategies'?{strategies:[]}:action==='list_data'?{datasets:[]}:action==='list_runs'?{runs:[]} :{};
  await route.fulfill({json:{ok:true,result}});
 });
 await page.goto('/#ticket=error-banner');await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');
 await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 await page.getByRole('button',{name:'전략 검증',exact:true}).click();
 const alert=page.getByRole('alert').filter({hasText:'작업을 완료하지 못했습니다'});
 await expect(alert).toContainText('요청 크기가 허용 범위를 넘었습니다. 입력 내용을 줄이고 다시 시도해 주세요.');
 await expect(alert).not.toContainText('Request too large');
 await alert.getByRole('button',{name:'닫기'}).click();
 await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략과 실행 기록',exact:true}).click();
 abortListRefresh=true;await page.getByRole('button',{name:'목록 새로고침',exact:true}).click();
 await expect(alert).toContainText('내 PC 실행기와 연결이 끊겼습니다. 실행기를 다시 열고 시도해 주세요.');
 await expect(alert).not.toContainText(/Failed to fetch|NetworkError|Load failed/);
});
test('startup connection failure shows Korean recovery guidance',async({page})=>{
 await page.route('**/api/session',route=>route.abort('failed'));
 await page.goto('/#ticket=startup-failure');
 await expect(page.getByRole('status').first()).toContainText('내 PC 연결 실패');
 const alert=page.getByRole('alert').filter({hasText:'작업을 완료하지 못했습니다'});
 await expect(alert).toContainText('내 PC 실행기와 연결이 끊겼습니다. 실행기를 다시 열고 시도해 주세요.');
 await expect(alert).not.toContainText(/Failed to fetch|NetworkError|Load failed/);
});
const run={id:'run-test-0001',revisionId:'revision1',datasetId:'data1',status:'succeeded',manifest:{engineVersion:'test-only'},report:{metrics:{totalReturnPct:2,maxDrawdownPct:1,benchmarkReturnPct:1,finalEquity:102000},source:{kind:'synthetic',label:'TEST FIXTURE'},strategy:{},equity:[{date:'2025-01-01',equity:100000,benchmark:100000,drawdownPct:0},{date:'2025-01-02',equity:102000,benchmark:101000,drawdownPct:1}],trades:[{id:'t1',date:'2025-01-02',signalDate:'2025-01-01',side:'BUY',quantity:1,price:100,fee:1,reason:'Fixture evidence',cashAfter:99899}],assumptions:{},warnings:[]},analysis:{metrics:{},metricReasons:{cagrPct:'requires positive first/final equity and positive calendar duration',sharpe:'requires at least two returns with nonzero sample volatility',sortino:'requires at least two returns and nonzero downside deviation',winRatePct:'requires realized PnL for all sell fills',profitFactor:'no realized losses or incomplete realized PnL; Infinity is not emitted',realizedPnl:'host realized PnL unavailable',unrealizedPnl:'host unrealized PnL unavailable'},monthlyReturns:[{month:'2025-01',returnPct:2}],diagnostics:[{id:'test',severity:'warning',status:'observed',title:'테스트 진단',message:'Fixture only',evidence:{count:1}}],assumptions:[]}};
test('ticket auth and complete runtime-backed UI flow, responsive report',async({page})=>{
 const actions:string[]=[];
 await page.route('**/api/session',async route=>{expect(route.request().postDataJSON()).toEqual({ticket:'fixture-ticket'});await route.fulfill({json:{ok:true,result:{token:'fixture-memory-token'}}});});
 await page.route('**/api/platform',async route=>{const {action,arguments:a}=route.request().postDataJSON();actions.push(action);expect(route.request().headers()['x-platform-token']).toBe('fixture-memory-token');let result:any;
 switch(action){case'get_ai_connection':result={configured:false,managementAllowed:false,configuration:null};break;case'capabilities':result={aiStudio:true,aiGeneration:{configured:false,available:false,reason:'PROVIDER_NOT_CONFIGURED',modelInvocationVerified:false}};break;case'templates':result={templates:[]};break;case'list_strategies':result={strategies:[]};break;case'list_data':result={datasets:[]};break;case'list_runs':result={runs:[]};break;case'validate_strategy':result={valid:true,errors:[]};break;case'save_strategy':result={id:'revision1',hash:'abc',definition:a.definition};break;case'demo_data':result={id:'data1',source:{kind:'synthetic',label:'TEST FIXTURE',currency:'KRW'},barCount:2};break;case'run':expect(a.revisionId).toBe('revision1');result=run;break;case'propose':result={method:'rule-based',candidates:[{name:'후보 1',reason:'test',settings:{feeBps:20},diff:{quantity:{before:2,after:1}}}]};break;case'experiment':result={...run,id:'run-test-0002'};break;case'compare':result={runIds:a.runIds};break;case'validate_robustness':result={method:a.method,status:'computed',limitations:['Retrospective split']};break;default:throw Error(action);}await route.fulfill({json:{ok:true,result}});});
 await page.goto('/#ticket=fixture-ticket');await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();expect(new URL(page.url()).hash).toBe('');
 await page.getByRole('button',{name:'전략 검증',exact:true}).click();await expect(page.getByText('지원 규칙 검증 통과')).toBeVisible();
 await page.getByRole('button',{name:'전략 저장',exact:true}).click();await expect(page.getByText('저장됨',{exact:true})).toBeVisible();
 await page.getByText('연습용 시세',{exact:true}).click();await page.getByRole('button',{name:'예시 시세로 연습'}).click();await page.getByRole('button',{name:'백테스트 실행',exact:false}).click();await expect(page.getByRole('heading',{name:'실행 리포트'})).toBeVisible();const metricCard=page.getByRole('region',{name:'추가 성과 지표'});await expect(metricCard.getByText('계산에 필요한 자료가 부족해 표시할 수 없습니다.',{exact:true})).toHaveCount(7);await expect(metricCard).not.toContainText('requires');await expect(metricCard).not.toContainText('Infinity is not emitted');
 await page.getByRole('button',{name:'2025-01-02 매수 근거'}).click();await expect(page.getByText('Fixture evidence')).toBeVisible();await page.addStyleTag({content:'.status-row{display:none!important}'});await page.screenshot({path:'test-results/guide-trade-explanation.png',fullPage:true});await page.getByRole('button',{name:'거래 검증',exact:true}).click();await expect(page.getByText('Fixture evidence')).toBeVisible();
 await page.getByRole('button',{name:'위험 진단',exact:true}).click();await expect(page.getByRole('heading',{name:'테스트 진단'})).toBeVisible();await page.addStyleTag({content:'.status-row{display:none!important}'});await page.screenshot({path:'test-results/guide-risk-diagnosis.png',fullPage:true});
 await page.getByRole('button',{name:'개선 실험',exact:true}).click();await page.getByRole('button',{name:'후보 만들기'}).click();const firstCandidate=page.locator('details.candidate').first();await firstCandidate.locator('summary').click();await expect(firstCandidate).toContainText('매수 수량');await expect(firstCandidate).toContainText('2 → 1');await expect(firstCandidate.locator('pre')).toHaveCount(0);await page.getByRole('button',{name:'전체 후보 재백테스트'}).click();await expect(page.getByText('조건 동등성과 상세 비교')).toBeVisible();await expect(page.getByText('완료',{exact:true})).toBeVisible();await page.addStyleTag({content:'.status-row{display:none!important}'});await page.screenshot({path:'test-results/guide-improvement-lab.png',fullPage:true});
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByRole('button',{name:'검증 실행',exact:true}).click();await expect(page.getByRole('region',{name:'검증 결과'})).toContainText('사후 기간 분리 결과');await expect(page.getByText(/기간 분리는 원본 실행에서 이미 확인한 시세/)).toContainText('독립적인 블라인드 검증이나 Walk-forward가 아닙니다');await page.addStyleTag({content:'.status-row{display:none!important}'});await page.screenshot({path:'test-results/guide-validation-lab.png',fullPage:true});
 await page.getByRole('button',{name:'성과 분석',exact:true}).click();await expect(page.getByRole('region',{name:'월별 성과'})).toContainText('2025-01');await expect(page.getByRole('region',{name:'월별 성과'})).toContainText('+2.00%');await page.addStyleTag({content:'.status-row{display:none!important}'});await page.screenshot({path:'test-results/guide-performance-report.png',fullPage:true});await page.setViewportSize({width:320,height:780});await expect(page.getByRole('heading',{name:'실행 리포트'})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
 await page.screenshot({path:'test-results/studio-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:'test-results/studio-desktop.png',fullPage:true});expect(actions).toContain('compare');
});
test('plain address is locked and never fetches demo data',async({page})=>{const calls:string[]=[];page.on('request',r=>{if(r.url().includes('/api/'))calls.push(r.url());});await page.goto('/');await expect(page.getByRole('alert')).toContainText('MCP의 Studio 열기');expect(calls).toEqual([]);});


test('failed and incomplete history stays usable and a valid report can follow',async({page})=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const attempts=['failed','cancelled','running','queued','interrupted'].map(status=>({id:`attempt-${status}`,status,createdAt:'2025-01-02',...(status==='failed'?{error:'입력 전략 실행에 실패했습니다. <script>alert(1)</script>'}:status==='interrupted'?{error:'Previous execution stopped before finalization'}:status==='cancelled'?{error:'Request too large'}:{})}));
 const results=[...attempts,run];
 await page.route('**/api/session',route=>route.fulfill({json:{ok:true,result:{token:'history-token'}}}));
 await page.route('**/api/platform',async route=>{const {action,arguments:a}=route.request().postDataJSON();let result:unknown;
 switch(action){case'get_ai_connection':result={configured:false,managementAllowed:false,configuration:null};break;case'capabilities':result={aiStudio:true,aiGeneration:{configured:false,available:false,reason:'PROVIDER_NOT_CONFIGURED',modelInvocationVerified:false}};break;case'templates':result={templates:[]};break;case'list_strategies':result={strategies:[]};break;case'list_data':result={datasets:[]};break;case'list_runs':result={runs:results,validationJobs:[{id:'interrupted-job',runId:run.id,method:'out_of_sample',status:'interrupted',createdAt:'2025-01-02',error:'Previous execution stopped before finalization'}]};break;case'get_run':result=results.find(r=>r.id===a.runId);break;default:throw Error(action);}
 await route.fulfill({json:{ok:true,result}});});
 await page.goto('/#ticket=history');await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
 for(const attempt of attempts){
  await page.getByRole('button',{name:'전략과 실행 기록',exact:false}).click();
  await page.locator('.library-item').filter({has:page.getByText(({failed:'실패',cancelled:'취소',running:'실행 중',queued:'대기',interrupted:'중단'} as Record<string,string>)[attempt.status],{exact:true})}).click();
  const detail=page.getByRole('region',{name:'실행 상태 상세'});await expect(detail).toBeVisible();await expect(detail.getByText(attempt.id,{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'실행 리포트'})).toHaveCount(0);
  if(attempt.status==='failed')await expect(detail).toContainText('입력 전략 실행에 실패했습니다. <script>alert(1)</script>');if(attempt.status==='cancelled'){await expect(detail).toContainText('요청 크기가 허용 범위를 넘었습니다. 입력 내용을 줄이고 다시 시도해 주세요.');await expect(detail).not.toContainText('Request too large');}if(attempt.status==='interrupted'){await expect(detail).toContainText('앱이 종료되기 전에 실행이 완료되지 않았습니다.');await expect(detail).not.toContainText('Previous execution stopped');}
  await page.getByRole('button',{name:'실행 상태 새로고침'}).click();await expect(detail).toBeVisible();
 }
 await page.getByRole('button',{name:'실행 기록으로 돌아가기'}).click();await page.locator('.library-item').filter({has:page.getByText('완료',{exact:true})}).click();
 await expect(page.getByRole('heading',{name:'실행 리포트'})).toBeVisible();await expect(page.getByRole('region',{name:'실행 상태 상세'})).toHaveCount(0);await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByRole('button',{name:'검증 이력 새로고침'}).click();const validationHistory=page.getByRole('region',{name:'저장된 검증 이력'});await expect(validationHistory).toContainText('중단');await expect(validationHistory).toContainText('앱이 종료되기 전에 실행이 완료되지 않았습니다.');await expect(validationHistory).not.toContainText('Previous execution stopped');expect(errors).toEqual([]);
});


test('saved cost rates stay readable and technical details remain optional',async({page})=>{
 const warning='시험용 경고: 기업행동을 반영하지 않았습니다.';
 const cases=[
  {id:'cost-fractional',assumptions:{feeBps:1.1,taxBps:-0,slippageBps:.07,fillRule:'SAVED_RULE'},expected:['0.011%','0%','0.0007%']},
  {id:'cost-missing',assumptions:{feeBps:null,taxBps:'15',slippageBps:true},expected:['기록 없음','기록 없음','기록 없음']},
  {id:'cost-small',assumptions:{feeBps:1e-7,taxBps:999.99,slippageBps:15},expected:['0.000000001%','9.9999%','0.15%']},
  {id:'cost-absent',assumptions:{},expected:['기록 없음','기록 없음','기록 없음']}
 ];
 const results=cases.map(item=>({...run,id:item.id,report:{...run.report,assumptions:item.assumptions,warnings:[warning]}}));
 await page.route('**/api/session',route=>route.fulfill({json:{ok:true,result:{token:'saved-cost-fixture'}}}));
 await page.route('**/api/platform',async route=>{
  const {action,arguments:a}=route.request().postDataJSON();let result:unknown;
  switch(action){case'templates':result={templates:[]};break;case'list_strategies':result={strategies:[]};break;case'list_data':result={datasets:[]};break;case'list_runs':result={runs:results,validationJobs:[{id:'interrupted-job',runId:run.id,method:'out_of_sample',status:'interrupted',createdAt:'2025-01-02',error:'Previous execution stopped before finalization'}]};break;case'get_run':result=results.find(r=>r.id===a.runId);break;case'get_ai_connection':result={configured:false,managementAllowed:false,configuration:null};break;case'capabilities':result={aiStudio:true,aiGeneration:{configured:false,available:false}};break;default:throw Error(action);}
  // Python JSON can preserve -0.0; JSON.stringify alone normalises it to zero.
  const body=JSON.stringify({ok:true,result}).replace('"taxBps":0,','"taxBps":-0.0,');await route.fulfill({contentType:'application/json',body});
 });
 await page.goto('/#ticket=cost-fixture');await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');
 for(const item of cases){
  await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략과 실행 기록',exact:true}).click();
  await page.locator('.library-item').filter({has:page.getByText(item.id,{exact:true})}).click();
  const info=page.getByRole('region',{name:'가상 체결 조건'}),details=info.locator('details'),summary=details.locator('summary');
  await expect(info.locator('dd')).toHaveText(item.expected);await expect(info.getByText(warning,{exact:true})).toBeVisible();
  await expect(details).not.toHaveAttribute('open','');await expect(info.locator('pre').first()).not.toBeVisible();
  await summary.focus();await page.keyboard.press('Enter');await expect(details).toHaveAttribute('open','');await expect(info.locator('pre').first()).toContainText('test-only');
  if(item.id==='cost-fractional')await expect(info.locator('pre').last()).toContainText('SAVED_RULE');
  await page.keyboard.press('Space');await expect(details).not.toHaveAttribute('open','');await expect(info.getByText(warning,{exact:true})).toBeVisible();
 }
});
