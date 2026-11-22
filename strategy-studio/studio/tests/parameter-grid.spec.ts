import {test,expect,type Page} from '@playwright/test';

const definition={schemaVersion:1,kind:'dsl',name:'조건 비교 테스트',quantity:1,allowPyramiding:false,risk:{stopLossPct:null,takeProfitPct:10},entry:{op:'all',conditions:[{op:'gt',left:{type:'sma',period:5},right:{type:'constant',value:100}}]},exit:{op:'lt',left:{type:'field',field:'close'},right:{type:'constant',value:50}}};
const run={id:'grid-base',revisionId:'grid-revision',datasetId:'grid-data',status:'succeeded',settings:{initialCash:100,feeBps:0,taxBps:0,slippageBps:0},manifest:{},report:{metrics:{totalReturnPct:2,maxDrawdownPct:1,benchmarkReturnPct:1,finalEquity:102},source:{kind:'synthetic',label:'UI FIXTURE'},strategy:{},equity:[{date:'2025-01-01',equity:100,benchmark:100,drawdownPct:0},{date:'2025-01-02',equity:102,benchmark:101,drawdownPct:1}],trades:[],assumptions:{},warnings:[]},analysis:{metrics:{},diagnostics:[],monthlyReturns:[],assumptions:[]}};
const record={id:'grid-validation',runId:run.id,method:'parameter_grid',options:{axes:[{path:'quantity',values:[1,2,3]}],maxEvaluations:12},result:{method:'parameter-grid',status:'completed_with_failures',candidateCount:3,bestCandidateIndex:null,bestCandidateImproved:null,baselineComparable:false,baselineReturnPct:2,deadlineSeconds:120,assumptions:['Same data and cost settings.'],holdoutContaminated:true,results:[{index:1,parameterValues:{quantity:1},revisionId:'r1',status:'succeeded',result:{runId:'child-1',metrics:{totalReturnPct:1,maxDrawdownPct:2}}},{index:2,parameterValues:{quantity:2},revisionId:'r2',status:'succeeded',result:{runId:'child-2',metrics:{totalReturnPct:-1,maxDrawdownPct:3}}},{index:3,parameterValues:{quantity:3},revisionId:'r3',status:'failed',runId:'child-3',error:'candidate execution failed'}]}};

async function setup(page:Page,{python=false,failedLoad=false,partial=false,strategyDefinition=definition}:{python?:boolean;failedLoad?:boolean;partial?:boolean;strategyDefinition?:any}={}){
 const calls:{action:string;arguments:any}[]=[];let loadCount=0;
 await page.route('**/api/session',route=>route.fulfill({json:{ok:true,result:{token:'fixture-only'}}}));
 await page.route('**/api/platform',async route=>{
  const {action,arguments:a}=route.request().postDataJSON();calls.push({action,arguments:a});let result:any;
  switch(action){
   case'templates':result={templates:[]};break;
   case'list_strategies':result={strategies:[]};break;
   case'list_data':result={datasets:[]};break;
   case'list_runs':result={runs:[run,{...run,id:'grid-other',revisionId:'other-revision'}],validationJobs:[{id:'grid-job',runId:run.id,method:'parameter_grid',status:partial?'cancelled':'succeeded',...(partial?{}:{validationId:record.id})}]};break;
   case'get_run':result={...run,id:a.runId,revisionId:a.runId===run.id?run.revisionId:'other-revision'};break;
   case'get_strategy':loadCount++;if(failedLoad&&loadCount===1){await route.fulfill({json:{ok:false,error:{message:'fixture load failed'}}});return;}result={id:a.revisionId,definition:python?{schemaVersion:1,name:'Python fixture',kind:'python',code:'def on_bar(ctx): return None'}:strategyDefinition,hash:'fixture'};break;
   case'validate_robustness':expect(a.method).toBe('parameter_grid');result={...record,options:a.options};break;
   case'get_validation':result=record;break;
   case'get_validation_job':result={id:'grid-job',runId:run.id,method:'parameter_grid',status:'cancelled',options:record.options,candidateCount:3,baselineReturnPct:2,error:'검증이 취소되었습니다.',candidateResults:[record.result.results[0],{index:2,parameterValues:{quantity:2},status:'cancelled',runId:'cancelled-child'}, {index:3,parameterValues:{quantity:3},status:'not_run'}]};break;
   case'get_ai_connection':result={configured:false,managementAllowed:false,configuration:null};break;
   case'capabilities':result={aiStudio:true,aiGeneration:{configured:false,available:false}};break;
   default:throw Error(action);
  }
  await route.fulfill({json:{ok:true,result}});
 });
 await page.goto('/#ticket=grid-fixture');await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');
 await page.getByRole('button',{name:`실행 열기: ${run.id}`,exact:true}).click();
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByLabel('검증 방법').selectOption('parameter_grid');
 return calls;
}

test('extended indicator parameter ranges match the runtime schema',async({page})=>{
 const extended={...definition,entry:{op:'gt',left:{type:'historical_volatility',period:20,annualize:252},right:{type:'constant',value:10}}};
 await setup(page,{strategyDefinition:extended});
 const selector=page.getByRole('combobox',{name:'비교 항목 1',exact:true}),input=page.getByLabel('비교할 값 1',{exact:true});
 await selector.selectOption('entry.left.annualize');await input.fill('50000');await expect(page.getByRole('button',{name:'조건 값 비교 실행'})).toBeEnabled();
 await input.fill('100001');await expect(page.getByRole('button',{name:'조건 값 비교 실행'})).toBeDisabled();
});

test('bounded explicit values and human parameter names prevent invalid grid calls',async({page})=>{
 const calls=await setup(page),button=page.getByRole('button',{name:'조건 값 비교 실행'}),input=page.getByLabel('비교할 값 1',{exact:true});
 await expect(button).toBeEnabled();
 for(const text of ['', '1,', 'NaN', 'Infinity', 'true', '0x10', '1, 1', '1.5','0','1000000001']){await input.fill(text);await expect(button).toBeDisabled();}
 await input.fill('1,2,3,4');await page.getByRole('button',{name:'비교 항목 추가'}).click();await page.getByLabel('비교할 값 2',{exact:true}).fill('5,10,20,30');
 await expect(page.getByText('총 16개 조합 · 최대 12개 — 값을 줄여주세요.',{exact:true})).toBeVisible();await expect(button).toBeDisabled();
 await page.getByLabel('비교할 값 2',{exact:true}).fill('5,10,20');await expect(button).toBeEnabled();await expect(page.getByText('총 12개 조합 · 최대 12개',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'비교 항목 추가'}).click();await expect(page.getByRole('button',{name:'비교 항목 추가'})).toBeDisabled();
 await page.getByRole('combobox',{name:'비교 항목 3',exact:true}).selectOption('entry.conditions.0.left.period');await page.getByLabel('비교할 값 3',{exact:true}).fill('1');await expect(button).toBeDisabled();
 await page.getByLabel('비교할 값 3',{exact:true}).fill('2');await expect(button).toBeEnabled();
 await expect(page.getByRole('combobox',{name:'비교 항목 3',exact:true})).toContainText('매수 조건 1 · 왼쪽 단순 이동평균 기간 (봉)');
 await expect(page.getByRole('combobox',{name:'비교 항목 2',exact:true}).locator('option[value="quantity"]')).toHaveCount(0);
 expect(calls.filter(call=>call.action==='validate_robustness')).toHaveLength(0);
 await page.setViewportSize({width:320,height:780});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);await page.screenshot({path:'test-results/parameter-grid-mobile.png',fullPage:true});
});

test('all trials including worse and failed remain visible and restore without rerun',async({page})=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));const calls=await setup(page);
 await page.getByLabel('비교할 값 1',{exact:true}).fill('1, 2, 3');await page.getByRole('button',{name:'조건 값 비교 실행'}).click();
 const result=page.getByRole('region',{name:'조건 값 비교 결과'});await expect(result.locator('tbody tr')).toHaveCount(4);await expect(result).toContainText('원본 (참고)');await expect(result).toContainText('-1.00%');await expect(result.locator('tbody')).toContainText('이 조합을 실행하지 못했습니다. 실행 기록에서 확인하세요.');await expect(result.locator('tbody')).not.toContainText('candidate execution failed');await expect(result).not.toContainText('완료 · 후보 중 최고');
 await expect(result.getByRole('heading',{name:'조건 값 비교 결과'})).toBeFocused();await expect(result.getByRole('status').first()).toContainText('조건 값 비교 실패 포함 · 3개 조합');await expect(result.locator('tbody th[scope="row"]')).toHaveCount(4);await page.setViewportSize({width:320,height:780});expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);await page.screenshot({path:'test-results/parameter-grid-result-mobile.png',fullPage:true});await page.setViewportSize({width:1280,height:900});
 const request=calls.find(call=>call.action==='validate_robustness')!;expect(request.arguments).toEqual({runId:run.id,method:'parameter_grid',options:{axes:[{path:'quantity',values:[1,2,3]}],maxEvaluations:12}});
 await page.getByRole('button',{name:'검증 이력 새로고침'}).click();await page.getByRole('region',{name:'저장된 검증 이력'}).getByRole('button',{name:'저장된 결과 열기'}).click();
 await expect(result.getByText('저장된 결과 · '+record.id.slice(0,12))).toBeVisible();await expect(page.getByLabel('비교할 값 1',{exact:true})).toHaveValue('1, 2, 3');
 expect(calls.filter(call=>call.action==='validate_robustness')).toHaveLength(1);await page.screenshot({path:'test-results/parameter-grid-desktop.png',fullPage:true});
 await result.getByText('개별 실행과 계산 조건',{exact:true}).click();await expect(result).toContainText('child-3');await expect(result.locator('ul')).toContainText('원본에 저장된 시세와 초기 자본·비용');await expect(result.locator('ul')).not.toContainText('Same data');expect(JSON.parse((await result.locator('pre').textContent())!)).toEqual(record);
 await page.getByRole('button',{name:'실행 열기: grid-other',exact:true}).click();await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByLabel('검증 방법').selectOption('parameter_grid');await expect(result).toHaveCount(0);await expect(page.getByLabel('비교할 값 1',{exact:true})).toHaveValue('1');expect(errors).toEqual([]);
});

test('failed baseline lookup can retry and Python does not expose a fake grid editor',async({page})=>{
 const calls=await setup(page,{failedLoad:true});await expect(page.getByRole('button',{name:'조건 값 비교 실행'})).toHaveCount(0);await page.getByRole('button',{name:'원본 다시 불러오기'}).click();await expect(page.getByRole('button',{name:'조건 값 비교 실행'})).toBeEnabled();expect(calls.filter(call=>call.action==='get_strategy')).toHaveLength(2);
 await page.goto('about:blank');await page.unroute('**/api/platform');await setup(page,{python:true});await expect(page.getByText('조건 값 비교는 비주얼 빌더 전략에서 사용할 수 있습니다.',{exact:false})).toBeVisible();await expect(page.getByRole('button',{name:'조건 값 비교 실행'})).toHaveCount(0);
});


test('cancelled comparison restores completed and unexecuted candidates without claiming a winner',async({page})=>{
 const calls=await setup(page,{partial:true});await page.getByRole('button',{name:'검증 이력 새로고침'}).click();await page.getByRole('button',{name:'진행 내역 열기'}).click();
 const result=page.getByRole('region',{name:'조건 값 비교 결과'});await expect(result.locator('tbody tr')).toHaveCount(4);await expect(result).toContainText('검증이 취소되었습니다.');await expect(result.locator('tbody tr').nth(1)).toContainText('완료');await expect(result.locator('tbody tr').nth(2)).toContainText('취소');await expect(result.locator('tbody tr').nth(3)).toContainText('미실행');
 await expect(result.locator('tbody')).not.toContainText('후보 중 최고');await expect(result).not.toContainText('저장된 결과');expect(calls.filter(call=>call.action==='validate_robustness')).toHaveLength(0);await page.getByLabel('비교할 값 1',{exact:true}).fill('4,5');await expect(result).toHaveCount(0);
});


test('candidate winner below baseline stays explicit and small condition values retain precision',async({page})=>{
 await setup(page);await page.route('**/api/platform',async route=>{const {action,arguments:a}=route.request().postDataJSON();if(action!=='validate_robustness'){await route.fallback();return;}await route.fulfill({json:{ok:true,result:{...record,options:a.options,result:{...record.result,status:'completed',candidateCount:1,bestCandidateIndex:1,bestCandidateImproved:false,baselineComparable:true,results:[{index:1,parameterValues:{'entry.conditions.0.right.value':0.0000001},status:'succeeded',result:{runId:'small-value-child',metrics:{totalReturnPct:1,maxDrawdownPct:2}}}]}}}});});
 await page.getByRole('combobox',{name:'비교 항목 1',exact:true}).selectOption('entry.conditions.0.right.value');await page.getByLabel('비교할 값 1',{exact:true}).fill('0.0000001');await page.getByRole('button',{name:'조건 값 비교 실행'}).click();const result=page.getByRole('region',{name:'조건 값 비교 결과'});await expect(result).toContainText('원본보다 높지 않음');await expect(result).toContainText('매수 조건 1 · 오른쪽 비교값: 0.0000001');await expect(result.locator('tbody tr')).toHaveCount(2);
});


test('incomparable or older comparison records never claim baseline improvement',async({page})=>{
 await setup(page);let comparable:boolean|undefined=false;
 await page.route('**/api/platform',async route=>{const {action,arguments:a}=route.request().postDataJSON();if(action!=='validate_robustness'){await route.fallback();return;}await route.fulfill({json:{ok:true,result:{...record,options:a.options,result:{...record.result,status:'completed',candidateCount:1,bestCandidateIndex:1,bestCandidateImproved:null,baselineComparable:comparable,comparisonWarning:'Engine version differs',results:[record.result.results[0]]}}}});});
 const result=page.getByRole('region',{name:'조건 값 비교 결과'});
 for(const value of [false,undefined]){comparable=value;await page.getByRole('button',{name:'조건 값 비교 실행'}).click();await expect(result).toContainText('원본 (참고)');await expect(result).toContainText(value===false?'원본 대비 개선 여부를 판단할 수 없습니다.':'원본과 계산 조건이 같은지 기록되지 않아');await expect(result).not.toContainText('원본보다 높지 않음');await expect(result.locator('tbody tr').first()).toContainText('+2.00%');await expect(result.locator('tbody tr').nth(1)).toContainText('후보 중 최고');}
});


test('keyboard entry and submit moves focus to announced result',async({page})=>{
 await setup(page);const input=page.getByRole('textbox',{name:'비교할 값 1',exact:true});await input.focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.type('1,2,3');await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'비교 항목 추가'})).toBeFocused();await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'조건 값 비교 실행'})).toBeFocused();await page.keyboard.press('Enter');const result=page.getByRole('region',{name:'조건 값 비교 결과'});await expect(result.getByRole('heading')).toBeFocused();await page.keyboard.press('Tab');await expect(result.locator('summary')).toBeFocused();await page.keyboard.press('Enter');await expect(result.locator('pre')).toBeVisible();
});

test('decimal parameter display expands exponent without losing digits',async({page})=>{
 await setup(page);const cases=[{value:1e-7,text:'0.0000001'},{value:-1.2345678901234567e-10,text:'-0.00000000012345678901234568'},{value:5e-324,text:'0.'+'0'.repeat(323)+'5'}];
 await page.route('**/api/platform',async route=>{const {action,arguments:a}=route.request().postDataJSON();if(action!=='validate_robustness'){await route.fallback();return;}await route.fulfill({json:{ok:true,result:{...record,options:a.options,result:{...record.result,results:cases.map((item,i)=>({index:i+1,parameterValues:{'entry.conditions.0.right.value':item.value},status:'succeeded',result:{runId:'decimal-'+i,metrics:{totalReturnPct:1,maxDrawdownPct:2}}}))}}}});});
 await page.getByRole('button',{name:'조건 값 비교 실행'}).click();const rows=page.getByRole('region',{name:'조건 값 비교 결과'}).locator('tbody tr');for(let i=0;i<cases.length;i++){await expect(rows.nth(i+1).getByRole('rowheader')).toHaveText('매수 조건 1 · 오른쪽 비교값: '+cases[i].text);expect(Number(cases[i].text)).toBe(cases[i].value);}
});

test('leaving the report while comparison is pending does not reopen its stale result',async({page})=>{
 await setup(page);let release!:()=>void;const gate=new Promise<void>(resolve=>release=resolve);let entered!:()=>void;const pending=new Promise<void>(resolve=>entered=resolve);
 await page.route('**/api/platform',async route=>{if(route.request().postDataJSON().action!=='validate_robustness'){await route.fallback();return;}entered();await gate;await route.fulfill({json:{ok:true,result:{...record,id:'stale-grid-result'}}});});
 await page.getByRole('button',{name:'조건 값 비교 실행'}).click();await pending;await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'홈',exact:true}).click();await expect(page.getByRole('button',{name:'실행 열기: grid-other',exact:true})).toBeDisabled();release();await expect(page.getByRole('button',{name:'실행 열기: grid-other',exact:true})).toBeEnabled();await page.getByRole('button',{name:'실행 열기: grid-other',exact:true}).click();await page.getByRole('button',{name:'검증 결과',exact:true}).click();await page.getByLabel('검증 방법').selectOption('parameter_grid');await expect(page.getByRole('region',{name:'조건 값 비교 결과'})).toHaveCount(0);await expect(page.getByRole('textbox',{name:'비교할 값 1',exact:true})).toHaveValue('1');await expect(page.locator('body')).not.toContainText('stale-grid-result');
});

test('switching the component run while callbacks are pending retains only the current result',async({page})=>{
 await page.route('**/grid-harness',route=>route.fulfill({contentType:'text/html',body:'<div id="root"></div><script type="module" src="/tests/parameter-grid-harness.tsx"></script>'}));await page.goto('/grid-harness');
 await page.getByRole('button',{name:'조건 값 비교 실행'}).click();await expect(page.getByRole('status',{name:'대기 요청'})).toHaveText('runA');await page.getByRole('button',{name:'다른 실행으로 전환'}).click();await expect(page.getByRole('textbox',{name:'비교할 값 1',exact:true})).toHaveValue('1');await page.getByRole('button',{name:'조건 값 비교 실행'}).click();await page.getByRole('button',{name:'runA 응답 완료'}).click();await expect(page.getByRole('region',{name:'조건 값 비교 결과'})).toHaveCount(0);await expect(page.getByRole('button',{name:'조건 값 비교 실행'})).toBeDisabled();await page.getByRole('button',{name:'runB 응답 완료'}).click();const result=page.getByRole('region',{name:'조건 값 비교 결과'});await expect(result.getByRole('heading')).toBeFocused();await result.locator('summary').click();await expect(result.locator('pre')).toContainText('result-runB');await expect(result.locator('pre')).not.toContainText('result-runA');
});
