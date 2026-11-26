export type Operand = {type:'field';field:'open'|'high'|'low'|'close'} | {type:'constant';value:number} | {type:'sma'|'ema'|'rsi';period:number};
export type Condition = {op:'all'|'any';conditions:Condition[]} | {op:'gt'|'gte'|'lt'|'lte'|'cross_above'|'cross_below';left:Operand;right:Operand};
export type Definition = {schemaVersion:1;name:string;kind:'dsl';entry:Condition;exit:Condition;quantity:number|null;allowPyramiding:boolean;risk:{stopLossPct:number|null;takeProfitPct:number|null}} | {schemaVersion:1;name:string;kind:'python';code:string};
export type Settings = {initialCash:number;feeBps:number;slippageBps:number;taxBps:number};
export type EditorLayout=Record<string,{x:number;y:number}>;
export type TemplateDetail={name:string;summary:string;buyRule:string;sellRule:string;sizing:string;cautions:string[];parameters:{path:string;label:string;value:number}[]};
export type Revision = {editorLayout?:EditorLayout;id:string;strategyId?:string;definition:Definition;hash:string;parentRevisionId?:string};
export type Dataset = {id:string;contentHash?:string;source:Record<string,unknown>;barCount?:number;count?:number;startDate?:string;endDate?:string};
export type Trade = {id:string;date:string;signalDate:string;side:'BUY'|'SELL';quantity:number;price:number;fee:number;reason:string;tax?:number;cashAfter?:number;sharesAfter?:number;realizedPnl?:number;evidence?:unknown};
export type Run = {researchId?:string;researchPhase?:string;settings:Settings;id:string;revisionId:string;datasetId:string;parentRunId?:string;status:string;manifest:Record<string,unknown>;createdAt?:string;report:{metrics:Record<string,number>;source:Record<string,unknown>;strategy:Record<string,unknown>;equity:{date:string;equity:number;benchmark:number;drawdownPct:number}[];trades:Trade[];assumptions:Record<string,unknown>;warnings:string[];orders?:unknown[];finalPortfolio?:Record<string,unknown>};analysis:{metrics:Record<string,number|null>;metricReasons?:Record<string,string>;monthlyReturns:unknown[];diagnostics:{id:string;severity:string;status:string;title:string;message:string;evidence:unknown;threshold?:unknown}[];assumptions:unknown}};
export type Candidate = {name:string;definition?:Definition;settings?:Partial<Settings>;reason:string;diff:unknown};
export type ResearchSource={runId:string}|{revisionId:string;datasetId:string;settings:Settings};
export type ResearchStart={source:ResearchSource;revisionId:string;strategyName:string;kind?:'dsl'|'python';barCount?:number;startDate?:string;endDate?:string};
export type ResearchDraft={researchId:string;purpose:string;trainFraction:number;attempted:boolean};
export type ResearchPeriod={startDate:string;endDate:string;barCount:number;datasetId?:string|null;source?:Record<string,unknown>};
export type ResearchDisclosures={strictBlindEvaluation:false;sourceWasCompletedRun:boolean;fullPeriodObserved:boolean;priorFullDatasetRunCount:number;priorResearchSessionCount:number;humanExposureUnknown:boolean;limitations?:string[]};
export type ResearchSummary={id:string;createdAt:string;purpose:string;strategyName:string;sourceKind:string;revisionId:string;datasetId:string;split:{train:ResearchPeriod;holdout:ResearchPeriod};disclosures:ResearchDisclosures};
export type ResearchCandidate={index:number;role:string;name:string;revisionId:string;trainingRunId:string;metrics:Record<string,number|null>;selected:boolean;evaluationEligible:boolean};
export type ResearchAttempt={index:number;status:string;stage:string;revisionId?:string;runId?:string;metrics?:Record<string,number|null>;error?:string};
export type ResearchView={
 session:ResearchSummary&{runId?:string;binding:{sourceKind:string;source:ResearchSource;settings:Settings};split:{trainFraction:number;train:ResearchPeriod;holdout:ResearchPeriod};aiRequestId:string};
 training:{jobId:string;jobStatus:string|null;baselineRunId:string;baselineStatus:string;baselineMetrics:Record<string,number|null>|null;aiRequestId:string;aiRequestStatus:string|null;submittedCandidateCount?:number;attempts:ResearchAttempt[];cancellationRequested:boolean;bindingStatus:string;bindingError?:string|null};
 frozen:null|{selectedRevisionId:string;candidateCount?:number;selection?:'original'|'candidate';freezeMode?:string;candidates:ResearchCandidate[];evaluationSlots:{index:number;role:string;revisionId:string;runId:string;trainingRunId:string}[]};
 evaluation:{jobId:string;jobStatus:string|null;status:string;datasetId:string|null;slots:{index:number;role:string;revisionId:string;runId:string;status:string;metrics:Record<string,number|null>|null;error?:string|null}[];result:unknown;cancellationRequested:boolean};
 eligibility:{freezeMode?:'original'|'candidates'|null;canPrepareTraining:boolean;canFreeze:boolean;canEvaluate:boolean;canCancelTraining:boolean;canCancelEvaluation:boolean;reasons:Record<string,string[]>};
 warnings:string[];
};
let sessionToken:string|undefined;
let authentication:Promise<void>|undefined;
export function token(){return sessionToken;}
export function authenticate(){
 if(authentication)return authentication;
 authentication=(async()=>{
 const ticket=new URLSearchParams(location.hash.slice(1)).get('ticket');
 history.replaceState(null,'',location.pathname+location.search);
 if(!ticket)throw new Error('MCP의 Studio 열기 또는 데스크톱 실행기로 다시 열어주세요. 일반 주소에는 인증 정보가 없습니다.');
 const response=await fetch('/api/session',{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket})});
 const data=await response.json();
 if(!response.ok||!data.ok||!data.result?.token)throw new Error(data.error?.message||'실행 티켓이 만료되었습니다. Studio를 다시 열어주세요.');
 sessionToken=data.result.token;
 })();return authentication;
}
export class ApiError extends Error {constructor(message:string,public status:number,public code?:string){super(message);this.name="ApiError";}}
export async function api<T>(action:string,args:Record<string,unknown>={}):Promise<T>{
 const secret=token();if(!secret)throw new Error('인증된 로컬 Studio 주소로 열어주세요. API 토큰을 수동 입력하지 않습니다.');
 const response=await fetch('/api/platform',{method:'POST',credentials:'omit',headers:{'Content-Type':'application/json','X-Platform-Token':secret},body:JSON.stringify({action,arguments:args}),signal:AbortSignal.timeout(180000)});
 let data;try{data=await response.json();}catch{throw new Error(`서버 응답을 읽을 수 없습니다 (${response.status}).`);}
 if(!response.ok||!data.ok)throw new ApiError(data.error?.message||`요청 실패 (${response.status})`,response.status,data.error?.code);
 return data.result as T;
}
export const baseCondition=():Condition=>({op:'cross_above',left:{type:'sma',period:5},right:{type:'sma',period:20}});
export const defaultStrategy=():Definition=>({schemaVersion:1,kind:'dsl',name:'나의 이동평균 전략',entry:baseCondition(),exit:{op:'cross_below',left:{type:'sma',period:5},right:{type:'sma',period:20}},quantity:1,allowPyramiding:false,risk:{stopLossPct:null,takeProfitPct:null}});
export const percent=(v:number|null|undefined)=>{
 if(v==null||!Number.isFinite(v))return '—';
 if(v===0)return '0.00%';
 return `${v>0?'+':''}${Math.abs(v)<.005?v.toPrecision(3):v.toFixed(2)}%`;
};
export const numeric=(v:number|null|undefined)=>v==null?'—':new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2}).format(v);
export const tone=(v:number|null|undefined)=>v==null||!Number.isFinite(v)||v===0?'':v>0?'up':'down';
