import {test,expect,type Page} from '@playwright/test';
// 고객용 Studio: 서버가 aiStudio=false 를 알리면(또는 알리지 않으면) 상용 AI 연결·초안 생성·개선 요청 UI 가 전혀 나타나지 않고
// AI 관련 API(get_ai_connection 등)도 호출되지 않는다. aiStudio=true 일 때만 표시(개발/내부 전용). 라이브/모델 호출 없음.

const dsl={schemaVersion:1,kind:'dsl',name:'조건형 전략 · 시험용',entry:{op:'gt',left:{type:'field',field:'close'},right:{type:'constant',value:0}},exit:{op:'lt',left:{type:'field',field:'close'},right:{type:'constant',value:0}},quantity:1,allowPyramiding:false,risk:{stopLossPct:null,takeProfitPct:null}};
const revision={id:'revision-fixture',hash:'fixture-hash',definition:dsl};
const data={id:'data-fixture',source:{kind:'synthetic',label:'시험용 가격',currency:'KRW'},barCount:2,startDate:'2025-01-01',endDate:'2025-01-02'};
const run={id:'run-fixture',revisionId:revision.id,datasetId:data.id,status:'succeeded',createdAt:'2025-01-02T12:00:00Z',manifest:{engineVersion:'fixture'},settings:{initialCash:100,feeBps:0,slippageBps:0,taxBps:0},report:{source:data.source,strategy:{name:dsl.name},metrics:{totalReturnPct:20,maxDrawdownPct:0,finalEquity:120,benchmarkReturnPct:10},equity:[{date:'2025-01-01',equity:100,benchmark:100,drawdownPct:0},{date:'2025-01-02',equity:120,benchmark:110,drawdownPct:0}],trades:[],assumptions:{},warnings:[]},analysis:{metrics:{},diagnostics:[],monthlyReturns:[],assumptions:[]}};
const AI_ACTIONS=new Set(['get_ai_connection','connect_ai_session','disconnect_ai_session','create_ai_draft','get_ai_draft','generate_ai_draft','cancel_ai_draft','preview_ai_http_improvement','generate_ai_http_improvement','list_ai_requests','get_ai_request','create_ai_request']);

async function fixture(page:Page,aiStudio:boolean|undefined){
  const actions:string[]=[];
  await page.route('**/api/session',r=>r.fulfill({json:{ok:true,result:{token:'fixture'}}}));
  await page.route('**/api/platform',async route=>{
    const {action}=route.request().postDataJSON();actions.push(action);
    let result:any;
    switch(action){
      case'templates':result={templates:[]};break;
      case'list_strategies':result={strategies:[revision]};break;
      case'list_data':result={datasets:[data]};break;
      case'list_runs':result={runs:[run]};break;
      case'get_run':result=run;break;
      case'validate_strategy':result={valid:true,errors:[]};break;
      case'capabilities':result={platform:'Darwin',pythonStrategy:true,dsl:true,...(aiStudio===undefined?{}:{aiStudio}),aiGeneration:{configured:false,available:false}};break;
      case'get_ai_connection':result={managementAllowed:true,configured:false,configuration:null};break;
      case'list_ai_requests':result={requests:[]};break;
      default:result={};
    }
    await route.fulfill({json:{ok:true,result}});
  });
  return actions;
}
async function launch(page:Page){await page.goto('/#ticket=fixture');await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');}
async function openReportExperimentTab(page:Page){
  await page.getByRole('button',{name:`실행 열기: ${run.id}`,exact:true}).click();
  await expect(page.getByRole('heading',{name:'실행 리포트',exact:true})).toBeVisible();
  // 리포트 탭은 role=tab 이 아니라 버튼(setTab('experiment'))이다. 없으면 텍스트로 찾는다(타임아웃 후 폴백 금지).
  const tabButton=page.getByRole('button',{name:'개선 실험',exact:true});
  if(await tabButton.count())await tabButton.first().click();else await page.getByText('개선 실험',{exact:true}).first().click();
}

for(const flag of [false,undefined] as const) test(`고객용(aiStudio=${String(flag)}): AI 연결·요청문·개선 요청 UI 없음, AI API 호출 0건`,async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const actions=await fixture(page,flag);
  await launch(page);
  await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
  await expect(page.getByLabel('전략 이름',{exact:true})).toBeVisible();
  await expect(page.getByText('Claude 요청 문구',{exact:true})).toHaveCount(0);
  await expect(page.getByLabel('Claude API 키',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'AI 연결 설정',exact:true})).toHaveCount(0);
  await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'홈',exact:true}).click();
  await openReportExperimentTab(page);
  await expect(page.getByRole('heading',{name:'AI 개선 요청',exact:true})).toHaveCount(0);
  await expect(page.getByText('외부 AI 개선 요청',{exact:false})).toHaveCount(0);
  expect(actions.filter(a=>AI_ACTIONS.has(a))).toEqual([]);
  expect(errors).toEqual([]);
});

test('내부/개발(aiStudio=true): AI UI 표시(대조군)',async({page})=>{
  const actions=await fixture(page,true);
  await launch(page);
  await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
  await expect(page.getByText('Claude 요청 문구',{exact:true})).toBeVisible();
  await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'홈',exact:true}).click();
  await openReportExperimentTab(page);
  await expect(page.getByRole('heading',{name:'AI 개선 요청',exact:true})).toBeVisible();
  expect(actions.filter(a=>a==='get_ai_connection').length).toBeGreaterThan(0);
});
