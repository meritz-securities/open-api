import {test,expect,type Page} from '@playwright/test';
// 윈도우 v1 게이팅: capabilities.pythonStrategy 로 Python 전략 지원 여부를 3상태(unknown/supported/unsupported)로 다룬다.
// (1) 미지원: "Python 코드" 모드 차단+안내  (2) 미지원: 저장된 Python 전략은 읽기 전용·실행 차단, unsupported_platform 날 에러 없음
// (3) 조회 지연·실패(unknown): Python 실행만 잠그고(fail-closed) 앱 기동·DSL 은 유지  (4) 맥(supported): 퇴행 없음
// 라이브/모델 호출 없음. 모든 API 는 fixture 로 대체한다.

const pythonDefinition={schemaVersion:1,kind:'python',name:'파이썬 전략 · 시험용',code:'def on_bar(ctx):\n    return {"action": "hold"}\n'};
const pythonRevision={id:'python-revision-fixture',hash:'python-hash',definition:pythonDefinition};
const dslDefinition={schemaVersion:1,kind:'dsl',name:'조건형 전략 · 시험용',entry:{op:'gt',left:{type:'field',field:'close'},right:{type:'constant',value:0}},exit:{op:'lt',left:{type:'field',field:'close'},right:{type:'constant',value:0}},quantity:1,allowPyramiding:false,risk:{stopLossPct:null,takeProfitPct:null}};
const dslRevision={id:'dsl-revision-fixture',hash:'dsl-hash',definition:dslDefinition};
const data={id:'data-fixture',source:{kind:'synthetic',label:'시험용 가격',currency:'KRW'},barCount:2,startDate:'2025-01-01',endDate:'2025-01-02'};
const run={id:'run-fixture',revisionId:pythonRevision.id,datasetId:data.id,status:'succeeded',createdAt:'2025-01-02T12:00:00Z',manifest:{engineVersion:'fixture'},settings:{initialCash:100,feeBps:0,slippageBps:0,taxBps:0},report:{source:data.source,strategy:{name:pythonDefinition.name},metrics:{totalReturnPct:0,maxDrawdownPct:0,finalEquity:100,benchmarkReturnPct:0},equity:[{date:'2025-01-01',equity:100,benchmark:100,drawdownPct:0},{date:'2025-01-02',equity:100,benchmark:100,drawdownPct:0}],trades:[],assumptions:{},warnings:[]},analysis:{metrics:{},diagnostics:[],monthlyReturns:[],assumptions:[]}};

type Mode='true'|'false'|'hold'|'fail';
async function fixture(page:Page,mode:Mode,withStrategies=false,platform='Windows'){
  const actions:string[]=[];let release:()=>void=()=>{};const held=new Promise<void>(r=>{release=r;});
  await page.route('**/api/session',r=>r.fulfill({json:{ok:true,result:{token:'fixture'}}}));
  await page.route('**/api/platform',async route=>{
    const {action}=route.request().postDataJSON();actions.push(action);
    let result:any;
    switch(action){
      case'templates':result={templates:[]};break;
      case'list_strategies':result={strategies:withStrategies?[pythonRevision,dslRevision]:[]};break;
      case'list_data':result={datasets:withStrategies?[data]:[]};break;
      case'list_runs':result={runs:[]};break;
      case'capabilities':{
        if(mode==='fail'){await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({ok:false,error:{message:'fixture capabilities unavailable'}})});return;}
        if(mode==='hold')await held;
        const pythonStrategy=mode!=='false';
        result={platform,pythonStrategy,dsl:true,aiGeneration:{configured:false,available:false}};break;}
      case'get_ai_connection':result={managementAllowed:false,configured:false,configuration:null};break;
      case'validate_strategy':result={valid:true,errors:[]};break;
      case'run':result=run;break;   // 차단돼야 하는 경로에서 도달하면 actions 로 검출한다
      default:result={};
    }
    await route.fulfill({json:{ok:true,result}});
  });
  return {actions,release};
}
async function launchWorkspace(page:Page){
  await page.goto('/#ticket=fixture');
  await expect(page.getByRole('status').first()).toContainText('내 PC 저장소 준비 완료');
  await page.getByRole('navigation',{name:'주 메뉴'}).getByRole('button',{name:'전략 만들기',exact:true}).click();
}
const runButton=(page:Page)=>page.getByLabel('전략 주요 작업').getByRole('button',{name:'백테스트 실행',exact:false});

test('윈도우(pythonStrategy=false): Python 코드 모드 비활성 + 안내, 날 에러 없음',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await fixture(page,'false');
  await launchWorkspace(page);
  await expect(page.getByRole('button',{name:'Python 코드',exact:true})).toBeDisabled();
  await expect(page.getByText('Python 전략 격리 환경이 준비되지 않았습니다',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'비주얼 빌더',exact:true})).toBeEnabled();
  expect(errors).toEqual([]);
});

test('윈도우 격리 준비 완료(pythonStrategy=true): Python 코드 모드 활성, 안내 없음',async({page})=>{
  await fixture(page,'true');
  await launchWorkspace(page);
  await expect(page.getByRole('button',{name:'Python 코드',exact:true})).toBeEnabled();
  await expect(page.getByText('Python 전략 격리 환경이 준비되지 않았습니다',{exact:false})).toHaveCount(0);
  await expect(page.locator('.python-pending-note')).toHaveCount(0);
});

test('윈도우: 저장된 Python 전략을 열면 읽기 전용 안내 + 백테스트 실행 차단, run 액션 미발생 (C-01)',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const {actions}=await fixture(page,'false',true);
  await launchWorkspace(page);
  await page.getByRole('button',{name:`저장 전략 열기: ${pythonDefinition.name}`,exact:true}).click();
  await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue(pythonDefinition.name);
  await page.getByLabel('백테스트 데이터').selectOption(data.id);
  await expect(page.locator('.python-unsupported-notice')).toBeVisible();
  await expect(runButton(page)).toBeDisabled();
  await expect(page.locator('input[type="file"][accept=".py"]')).toBeDisabled();
  expect(actions.filter(a=>a==='run')).toEqual([]);
  await expect(page.getByText('unsupported_platform',{exact:false})).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('조회 지연: capabilities 응답 전에는 Python 실행 잠김(fail-closed), 응답(true) 후 허용 (C-01 r2)',async({page})=>{
  const {actions,release}=await fixture(page,'hold',true);
  await launchWorkspace(page);
  await page.getByRole('button',{name:`저장 전략 열기: ${pythonDefinition.name}`,exact:true}).click();
  await page.getByLabel('백테스트 데이터').selectOption(data.id);
  // 아직 capabilities 미응답 → 실행 잠김 + 확인 중 안내
  await expect(page.locator('.python-unsupported-notice.unknown')).toBeVisible();
  await expect(runButton(page)).toBeDisabled();
  expect(actions.filter(a=>a==='run')).toEqual([]);
  release();   // 이제 pythonStrategy=true 응답
  await expect(runButton(page)).toBeEnabled();
  await expect(page.locator('.python-unsupported-notice')).toHaveCount(0);
});

test('조회 실패(500): 앱 기동·DSL 유지, Python 실행은 잠김 (C-01 r2)',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const {actions}=await fixture(page,'fail',true);
  await launchWorkspace(page);              // 기동은 성공해야 한다(capabilities 는 기동 의존이 아님)
  // DSL 전략은 열고 실행할 수 있다
  await page.getByRole('button',{name:`저장 전략 열기: ${dslDefinition.name}`,exact:true}).click();
  await page.getByLabel('백테스트 데이터').selectOption(data.id);
  await expect(runButton(page)).toBeEnabled();
  // Python 전략은 잠긴다
  await page.getByRole('button',{name:`저장 전략 열기: ${pythonDefinition.name}`,exact:true}).click();
  await expect(page.getByLabel('전략 이름',{exact:true})).toHaveValue(pythonDefinition.name);
  await expect(page.locator('.python-unsupported-notice.unknown')).toBeVisible();
  await expect(runButton(page)).toBeDisabled();
  await expect(page.getByRole('button',{name:'Python 코드',exact:true})).toBeDisabled();
  expect(actions.filter(a=>a==='run')).toEqual([]);
  expect(errors).toEqual([]);
});

for(const platform of ['Windows','Darwin']) test(`${platform}: 저장된 Python 전략 열기 및 실행`,async({page})=>{
  const {actions}=await fixture(page,'true',true,platform);
  await launchWorkspace(page);
  await page.getByRole('button',{name:`저장 전략 열기: ${pythonDefinition.name}`,exact:true}).click();
  await page.getByLabel('백테스트 데이터').selectOption(data.id);
  await expect(page.locator('.python-unsupported-notice')).toHaveCount(0);
  await expect(runButton(page)).toBeEnabled();
  await runButton(page).click();
  await expect(page.getByRole('heading',{name:'실행 리포트',exact:true})).toBeVisible();
  expect(actions.filter(a=>a==='run')).toHaveLength(1);
});
