import {useEffect,useRef,useState} from 'react';
import {AIRequests} from './AIRequests';
import {api,ApiError,numeric,percent,tone,type ResearchDraft,type ResearchStart,type ResearchView} from './api';
import type {Call} from './Report';
import './ResearchWorkspace.css';

const statuses:Record<string,string>={not_run:'미실행',pending:'미실행',running:'진행 중',queued:'대기',succeeded:'완료',completed:'완료',completed_with_failures:'실패 포함',failed:'실패',cancelled:'취소',interrupted:'중단',awaiting_candidates:'후보 제출 대기',generating:'후보 생성 중',ready:'후보 실행 대기'};
const status=(value:string|null|undefined)=>value?statuses[value]||'상태 확인 필요':'시작 전';
const reasons:Record<string,string>={TRAINING_IN_PROGRESS:'개선 기간 계산이 진행 중입니다. 상태를 확인하며 기다려주세요.',EVALUATION_FINISHED:'검증 기간 계산이 완료되었습니다.',TRAINING_BASELINE_NOT_READY:'개선 기간의 원본 계산이 먼저 완료되어야 합니다.',TRAINING_CANCELLED:'개선 작업이 취소되어 후보를 확정할 수 없습니다.',POOL_NOT_FROZEN:'후보를 먼저 확정하세요.',TRAINING_BINDING_FAILED:'개선 요청 연결에 실패했습니다. 새 검증에서 다시 시작하세요.',CANDIDATES_NOT_READY:'제출된 후보 전체가 완료되어야 확정할 수 있습니다.',TRAINING_CANDIDATES_NOT_COMPLETE:'제출된 후보 전체가 완료되어야 확정할 수 있습니다. 실패·취소·미실행 후보를 확인하세요.',RESEARCH_VERSION_CHANGED:'저장 당시와 계산 버전이 달라 이 작업을 이어서 실행할 수 없습니다. 새 검증을 시작하세요.',TRAINING_ALREADY_PREPARED:'개선 기간 원본은 이미 계산되었습니다.',TRAINING_ATTEMPT_ALREADY_CLAIMED:'이미 시작한 개선 기간 계산은 다시 실행하지 않습니다. 저장된 상태를 확인하세요.',POOL_ALREADY_FROZEN:'후보가 이미 확정되어 있습니다.',EVALUATION_CANCELLED:'검증 작업이 취소되어 다시 실행하지 않습니다.',EVALUATION_ATTEMPT_ALREADY_CLAIMED:'이미 시작한 검증은 다시 실행하지 않습니다. 저장된 상태를 확인하세요.'};
function reasonText(values:string[]|undefined,fallback:string){return values?.map(value=>reasons[value]).find(Boolean)||fallback;}
function drawdown(value:number|null|undefined){return typeof value==='number'?percent(-Math.abs(value)):'—';}

type Props={start?:ResearchStart;researchId?:string;draft:ResearchDraft;onDraftChange:(draft:ResearchDraft)=>void;call:Call;busy:boolean;onOpened:(id:string)=>void;onBack:()=>void;onOpenRun:(id:string)=>void;onOpenAIConnection:()=>void};
export function ResearchWorkspace({start,researchId,draft,onDraftChange,call,busy,onOpened,onBack,onOpenRun,onOpenAIConnection}:Props){
 const [view,setView]=useState<ResearchView>(),[kind,setKind]=useState(start?.kind),[kindFailed,setKindFailed]=useState(false),[verified,setVerified]=useState(false),[working,setWorking]=useState(''),[cancelling,setCancelling]=useState(false),[notice,setNotice]=useState(''),[announcement,setAnnouncement]=useState(''),[pendingRequestRefresh,setPendingRequestRefresh]=useState<string>();
 const mounted=useRef(false),sequence=useRef(0),currentId=useRef(researchId),viewRef=useRef(view),inflight=useRef(false),heading=useRef<HTMLHeadingElement>(null),evaluationHeading=useRef<HTMLHeadingElement>(null),currentCall=useRef(call);
 currentCall.current=call;viewRef.current=view;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;sequence.current++;};},[]);
 function show(value:ResearchView,message:string){currentId.current=value.session.id;viewRef.current=value;setView(value);setVerified(true);setNotice('');setAnnouncement(message);}
 function matches(value:any,id:string):value is ResearchView{return value?.session?.id===id&&value?.training&&value?.evaluation&&value?.eligibility;}
 useEffect(()=>{
  if(!researchId||viewRef.current?.session.id===researchId)return;
  currentId.current=researchId;const request=++sequence.current;setVerified(false);setNotice('');
  void currentCall.current('get_research',{researchId}).then(value=>{if(!mounted.current||request!==sequence.current||currentId.current!==researchId)return;if(matches(value,researchId))show(value,'저장된 검증 기록을 불러왔습니다.');else setNotice('저장된 상태를 확인하지 못했습니다. 다시 조회하세요.');}).catch(()=>{if(mounted.current&&request===sequence.current)setNotice('저장된 상태를 확인하지 못했습니다. 다시 조회하세요.');});
 },[researchId]);
 useEffect(()=>{
  if(researchId||!start||start.kind)return;
  let active=true;setKindFailed(false);
  void currentCall.current('get_strategy',{revisionId:start.revisionId}).then(value=>{if(!active)return;if(value?.id===start.revisionId&&(value.definition?.kind==='dsl'||value.definition?.kind==='python'))setKind(value.definition.kind);else setKindFailed(true);}).catch(()=>{if(active)setKindFailed(true);});
  return()=>{active=false;};
 },[researchId,start?.revisionId]);
 useEffect(()=>{if(!announcement)return;const frame=requestAnimationFrame(()=>{const target=announcement==='검증 기간 계산 상태를 확인했습니다.'?evaluationHeading.current:heading.current;if(target?.getClientRects().length){target.focus({preventScroll:true});target.scrollIntoView({block:'nearest'});}});return()=>cancelAnimationFrame(frame);},[announcement]);
 async function refresh(automatic=false){
  const id=currentId.current||researchId;if(!id||busy||inflight.current)return;const request=++sequence.current;inflight.current=true;setWorking('refresh');setNotice('');
  try{const value=await call('get_research',{researchId:id});if(!mounted.current||request!==sequence.current||currentId.current!==id)return;if(matches(value,id)){if(automatic){currentId.current=id;viewRef.current=value;setView(value);setVerified(true);setNotice('후보 처리 결과를 확인했습니다.');}else show(value,'저장된 진행 상태를 확인했습니다.');}else{setVerified(false);setNotice('저장된 상태를 확인하지 못했습니다. 다시 조회하세요.');}}
  catch{if(mounted.current&&request===sequence.current){setVerified(false);setNotice('저장된 상태를 확인하지 못했습니다. 다시 조회하세요.');}}
  finally{inflight.current=false;if(mounted.current)setWorking('');}
 }
 useEffect(()=>{if(!pendingRequestRefresh||pendingRequestRefresh!==view?.training.aiRequestId||busy||working||cancelling||inflight.current)return;setPendingRequestRefresh(undefined);void refresh(true);},[pendingRequestRefresh,view?.training.aiRequestId,busy,working,cancelling]);
 async function create(){
  if(!start||kind!=='dsl'||busy||inflight.current||!draft.purpose.trim()||!Number.isFinite(draft.trainFraction)||draft.trainFraction<.2||draft.trainFraction>.8)return;
  if(start.barCount!==undefined&&(start.barCount<4||start.barCount>5000||Math.floor(start.barCount*draft.trainFraction)<2||start.barCount-Math.floor(start.barCount*draft.trainFraction)<2)){setNotice('기간 분리에는 시세 4~5,000봉이 필요하며, 두 기간에 각각 2봉 이상 있어야 합니다. 시세 또는 기간 비율을 확인하세요.');return;}
  const attempt={...draft,attempted:true};onDraftChange(attempt);inflight.current=true;setWorking('create');setNotice('');const request=++sequence.current;currentId.current=attempt.researchId;
  try{const value=await api<ResearchView>('create_research',{source:start.source,purpose:attempt.purpose,trainFraction:attempt.trainFraction,researchId:attempt.researchId});if(!mounted.current||request!==sequence.current)return;if(matches(value,attempt.researchId)){show(value,'개선 기간과 검증 기간을 저장했습니다. 아직 계산하지 않았습니다.');onOpened(value.session.id);}else{setVerified(false);setNotice('기간 저장 결과를 확인하지 못했습니다. 같은 요청으로 저장을 재시도하세요.');}}
  catch(error){if(mounted.current&&request===sequence.current){setVerified(false);if(error instanceof ApiError&&error.status===400&&error.code==='INVALID_REQUEST'){
    try{const saved=await api<ResearchView>('get_research',{researchId:attempt.researchId});if(!mounted.current||request!==sequence.current)return;if(matches(saved,attempt.researchId)){show(saved,'저장된 기간을 확인했습니다. 계산은 다시 실행하지 않았습니다.');onOpened(saved.session.id);}else setNotice('저장 여부를 확인하지 못했습니다. 입력값과 요청 번호를 유지합니다. 같은 요청으로 저장을 재확인하세요.');}
    catch(lookupError){if(!mounted.current||request!==sequence.current)return;if(lookupError instanceof ApiError&&lookupError.status===400&&lookupError.code==='INVALID_REQUEST'&&lookupError.message==='RESEARCH_NOT_FOUND'){currentId.current=undefined;onDraftChange({...attempt,researchId:crypto.randomUUID().replaceAll('-',''),attempted:false});setNotice('기간을 저장하지 못했습니다. 시세 4~5,000봉, 각 기간 2봉 이상인지 확인하고 목적과 비율을 수정하세요. 다른 시세는 이전 화면에서 선택할 수 있습니다.');}else setNotice('저장 여부를 확인하지 못했습니다. 입력값과 요청 번호를 유지합니다. 같은 요청으로 저장을 재확인하세요.');}
   }else setNotice('기간 저장 결과를 확인하지 못했습니다. 같은 요청으로 저장을 재시도하세요.');}}
  finally{inflight.current=false;if(mounted.current)setWorking('');}
 }
 async function perform(action:'prepare_research_training'|'freeze_research'|'evaluate_research'){
  if(!view||!verified||busy||inflight.current)return;
  const allowed=action==='prepare_research_training'?view.eligibility.canPrepareTraining:action==='freeze_research'?view.eligibility.canFreeze:view.eligibility.canEvaluate;
  if(!allowed)return;const id=view.session.id,request=++sequence.current;inflight.current=true;setWorking(action);setNotice('');
  try{const value=await call(action,{researchId:id});if(!mounted.current||request!==sequence.current||currentId.current!==id)return;if(matches(value,id))show(value,action==='prepare_research_training'?'개선 기간 계산 상태를 확인했습니다.':action==='freeze_research'?'검증할 후보를 확정했습니다.':'검증 기간 계산 상태를 확인했습니다.');else{setVerified(false);setNotice('처리 결과를 확인하지 못했습니다. 상태를 새로고침하세요. 자동으로 다시 실행하지 않습니다.');}}
  catch{if(mounted.current&&request===sequence.current){setVerified(false);setNotice('처리 결과를 확인하지 못했습니다. 상태를 새로고침하세요. 자동으로 다시 실행하지 않습니다.');}}
  finally{inflight.current=false;if(mounted.current)setWorking('');}
 }
 async function cancel(phase:'training'|'evaluation'){
  if(!view||cancelling)return;const allowed=phase==='training'?view.eligibility.canCancelTraining:view.eligibility.canCancelEvaluation;if(!allowed)return;
  const id=view.session.id,request=++sequence.current;setCancelling(true);setNotice('');
  try{const value=await api<ResearchView&{cancellation?:{requested:boolean;effective?:boolean;reason?:string}}>('cancel_research',{researchId:id,phase});if(!mounted.current||request!==sequence.current||currentId.current!==id)return;if(matches(value,id)){show(value,'취소 요청 처리 상태를 확인했습니다.');setNotice(value.cancellation?.requested?'취소 요청을 접수했습니다. 완료 여부는 최종 상태를 다시 확인하세요.':'현재 상태에서는 취소가 적용되지 않았습니다. 완료된 결과와 확정된 후보는 유지됩니다.');}else{setVerified(false);setNotice('취소 접수 여부를 확인하지 못했습니다. 상태를 새로고침하세요.');}}
  catch{if(mounted.current&&request===sequence.current){setVerified(false);setNotice('취소 접수 여부를 확인하지 못했습니다. 상태를 새로고침하세요.');}}
  finally{if(mounted.current)setCancelling(false);}
 }
 const blocked=busy||Boolean(working)||cancelling;
 const stage=!view?'기간 설정':!view.training.aiRequestStatus?'개선 기간 계산':!view.frozen?'후보 개선·확정':'검증 결과';
 const disclosures=view?.session.disclosures;
 return <div className="research-workspace">
  <div className="section-title"><div><h1 ref={heading} tabIndex={-1}>기간 나눠 개선·검증</h1><p>{view?.session.strategyName||start?.strategyName||'저장된 검증'}</p></div><button onClick={onBack}>이전 화면</button></div>
  <ol className="research-steps" aria-label="검증 단계">{['기간 설정','개선 기간 계산','후보 개선·확정','검증 결과'].map(label=><li key={label} aria-current={stage===label?'step':undefined}>{label}</li>)}</ol>
  <p className="research-status" role="status" aria-live="polite">{notice||announcement}</p>
  {!view&&researchId&&<section className="card"><p>저장된 기록을 불러와 확인합니다. 계산은 다시 실행하지 않습니다.</p><button disabled={blocked} onClick={()=>void refresh()}>저장 상태 다시 확인</button></section>}
  {!view&&!researchId&&start&&<section className="card" aria-label="기간 분리 설정">
   {'runId' in start.source&&<p className="research-notice">이 리포트는 전체 기간을 이미 계산한 결과입니다. 기간을 다시 나누더라도 처음 보는 자료로 검증한 결과는 아닙니다.</p>}
   <p className="micro">저장된 전략과 선택한 시세·자본·비용을 사용합니다. 편집 중인 전략과 설정은 바꾸지 않습니다.</p>
   {start.startDate&&start.endDate&&<p>저장 시세: {start.startDate} ~ {start.endDate}</p>}
   {kindFailed&&<p role="alert">원본 전략을 확인하지 못했습니다. 이전 화면에서 다시 열어주세요.</p>}
   {kind==='python'&&<p>기간을 나눈 개선·검증은 비주얼 빌더 전략만 지원합니다. Python 전략의 기존 백테스트는 계속 사용할 수 있습니다.</p>}
   {!kind&&!kindFailed&&<p>원본 전략 종류를 확인하고 있습니다.</p>}
   <fieldset disabled={blocked||draft.attempted||kind!=='dsl'}>
    <label>개선 목적<textarea aria-label="기간 분리 개선 목적" rows={2} maxLength={2000} value={draft.purpose} onChange={event=>onDraftChange({...draft,purpose:event.target.value})}/></label>
    <div className="field-grid"><label>전략 개선에 쓸 기간<select value={String(draft.trainFraction)} onChange={event=>onDraftChange({...draft,trainFraction:Number(event.target.value)})}>{[.2,.3,.4,.5,.6,.7,.8].map(value=><option key={value} value={value}>앞 {Math.round(value*100)}%</option>)}</select></label><p>검증할 기간: 뒤 {Math.round((1-draft.trainFraction)*100)}%</p></div>
   </fieldset>
   <p className="micro">시간 순서대로 나눕니다. 시세 4~5,000봉에서 각 기간에 2봉 이상이 필요합니다. 정확한 날짜와 봉 수는 기간 저장 후 표시하며, 이 단계에서는 전략을 실행하지 않습니다.</p>
   {draft.attempted&&<p className="micro">같은 저장 요청을 재확인합니다. 목적과 비율을 바꾸려면 이전 화면에서 새 검증을 시작하세요.</p>}
   <button className="primary" disabled={blocked||kind!=='dsl'||!draft.purpose.trim()} onClick={()=>void create()}>{draft.attempted?'기간 저장 재시도':'기간 저장'}</button>
  </section>}
  {view&&<>
   <section className="card" aria-label="분리한 기간과 실행 조건">
    <div className="section-title"><h2>저장한 기간</h2><button disabled={blocked} onClick={()=>void refresh()}>진행 상태 새로고침</button></div>
    <p>{view.session.purpose}</p>
    <div className="research-periods">{([['train','전략 개선에 쓸 기간'],['holdout','검증할 기간']] as const).map(([key,label])=><dl key={key}><dt>{label}</dt><dd>{view.session.split[key].startDate} ~ {view.session.split[key].endDate}<br/>{view.session.split[key].barCount}봉</dd></dl>)}</div>
    <p className="micro">초기 자본 {numeric(view.session.binding.settings.initialCash)} {typeof view.session.split.train.source?.currency==='string'?view.session.split.train.source.currency:'(표시 통화 확인 필요)'} · 수수료 {view.session.binding.settings.feeBps}bp · 거래세 {view.session.binding.settings.taxBps}bp · 가격 보정 {view.session.binding.settings.slippageBps}bp</p>
    <p className="research-notice">{disclosures?.sourceWasCompletedRun||disclosures?.fullPeriodObserved?'전체 기간을 이미 계산한 기록이 있습니다. 이번 결과를 처음 보는 자료의 검증으로 해석하지 마세요.':'뒤 기간은 이번 작업의 후보 개선에 사용하지 않습니다. 다른 곳에서 데이터를 이미 확인했는지는 알 수 없습니다.'}</p>
    <p className="micro">이 저장 시세의 이전 전체 기간 실행 {disclosures?.priorFullDatasetRunCount??'확인 불가'}건 · 이전 기간 분리 {disclosures?.priorResearchSessionCount??'확인 불가'}건. 다른 저장본·외부 이용까지 포함한 전체 횟수는 아닙니다.</p>
    {view.warnings?.length>0&&<ul>{view.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>}
   </section>
   <section className="card research-stage" aria-label="개선 기간 원본 계산">
    <div className="section-title"><h2>1. 개선 기간 계산</h2><span className="badge">{status(view.training.baselineStatus)}</span></div>
    <p>앞 기간에서 원본을 계산한 뒤, 이 결과에 연결된 요청으로 후보를 만듭니다.</p><p className="micro">계산 단계는 120초 제한을 확인하며 중단 지점에서 멈춥니다. 정확히 그 시각에 강제 종료되는 것은 아닙니다.</p>
    <div className="research-actions"><button className="primary" disabled={blocked||!verified||!view.eligibility.canPrepareTraining} onClick={()=>void perform('prepare_research_training')}>개선 기간 계산</button>
     {view.training.baselineStatus==='succeeded'&&<button disabled={blocked} onClick={()=>onOpenRun(view.training.baselineRunId)}>개선 기간 리포트 열기</button>}
     {view.eligibility.canCancelTraining&&(working==='prepare_research_training'||view.training.jobStatus||view.training.bindingStatus==='bound')&&<button disabled={cancelling||view.training.cancellationRequested} onClick={()=>void cancel('training')}>개선 작업 취소 요청</button>}
    </div>
    {view.training.cancellationRequested&&<p>개선 작업 취소 요청이 기록되어 있습니다. 후보를 새로 생성하거나 확정하지 않습니다.</p>}
    {view.training.bindingStatus==='training_binding_failed'&&<p role="alert">원본 계산은 보존됐지만 개선 요청 연결에 실패했습니다. 이 작업을 다시 실행하지 말고 새 검증에서 시작하세요.</p>}
    {!view.eligibility.canPrepareTraining&&view.training.baselineStatus!=='succeeded'&&<p className="micro">{reasonText(view.eligibility.reasons.prepareTraining,'저장된 실행 상태를 확인하세요. 중단된 계산은 자동으로 다시 실행하지 않습니다.')}</p>}
   </section>
   {verified&&!view.eligibility.reasons.freeze?.includes('RESEARCH_VERSION_CHANGED')&&view.training.bindingStatus==='bound'&&!view.frozen&&!view.training.cancellationRequested&&<AIRequests key={view.training.aiRequestId} runId={view.training.baselineRunId} boundRequest={{requestId:view.training.aiRequestId,purpose:view.session.purpose}} onStatusChange={value=>{if(value.requestId===view.training.aiRequestId)setPendingRequestRefresh(value.requestId);}} call={call} busy={blocked} onOpenAIConnection={onOpenAIConnection}/>}
   <section className="card research-stage" aria-label="개선 후보 확정">
    <div className="section-title"><h2>2. 후보 개선·확정</h2><span className="badge">{view.frozen?'확정됨':status(view.training.aiRequestStatus)}</span></div>
    {view.eligibility.freezeMode==='original'&&!view.frozen?<p>제출된 개선 후보가 없습니다. <strong>원본 전략만으로 확정</strong>하고 다음 단계에서 검증 기간을 계산할 수 있습니다. (후보는 메리츠 MCP 경로로 제출된 경우에만 비교합니다.)</p>:<p>제출된 후보 전체가 완료되어야 확정합니다. 개선 기간 수익률로 하나를 선택하며 동률이면 원본, 제출 순서를 따릅니다.</p>}
    {!view.frozen&&<>
     <p className="micro">표가 잘리면 좌우로 이동하세요. 키보드는 표에 초점을 둔 뒤 방향키로 이동할 수 있습니다.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="전략별 기간 수익률 비교 표"><table className="research-table"><thead><tr><th>전략</th><th>개선 기간 수익률</th><th>최대 낙폭</th><th>상태</th></tr></thead><tbody>
      <tr><th scope="row">원본</th><td className={tone(view.training.baselineMetrics?.totalReturnPct)}>{percent(view.training.baselineMetrics?.totalReturnPct)}</td><td>{drawdown(view.training.baselineMetrics?.maxDrawdownPct)}</td><td>{status(view.training.baselineStatus)}</td></tr>
      {view.training.attempts.map(attempt=><tr key={attempt.index}><th scope="row">후보 {attempt.index+1}</th><td className={tone(attempt.metrics?.totalReturnPct)}>{percent(attempt.metrics?.totalReturnPct)}</td><td>{drawdown(attempt.metrics?.maxDrawdownPct)}</td><td>{status(attempt.status)}{attempt.error&&<p>이 후보를 완료하지 못했습니다. 개선 요청에서 내역을 확인하세요.</p>}</td></tr>)}
     </tbody></table></div>
     <p className="micro">요청의 처리 결과를 확인하면 후보 상태를 함께 갱신합니다. 일부 실패·취소가 있으면 후보를 골라 빼고 확정할 수 없습니다.</p>
     <div className="research-actions"><button disabled={blocked} onClick={()=>void refresh()}>후보 결과 확인</button><button className="primary" disabled={blocked||!verified||!view.eligibility.canFreeze} onClick={()=>void perform('freeze_research')}>{view.eligibility.freezeMode==='original'?'원본으로 확정':'후보 확정'}</button></div>
     {!view.eligibility.canFreeze&&<p className="micro">{reasonText(view.eligibility.reasons.freeze,'제출된 후보 전체의 완료 상태를 확인한 뒤 확정할 수 있습니다.')}</p>}
    </>}
    {view.frozen&&<p>{view.frozen.candidateCount===0?'후보 없이 원본 전략으로 확정했습니다.':'전체 후보와 개선 기간의 선택 결과를 고정했습니다.'} 검증 결과를 본 뒤 이 작업의 후보를 바꿀 수 없습니다.</p>}
   </section>
   {view.frozen&&<section className="card research-stage" aria-label="분리 검증 결과">
    <div className="section-title"><h2 ref={evaluationHeading} tabIndex={-1}>3. 검증 기간 결과</h2><span className="badge">{status(view.evaluation.status)}</span></div>
    <p>앞 기간에서 선택된 전략과 원본만 뒤 기간에서 계산합니다. 원본이 선택됐으면 한 번만 계산합니다. 뒤 기간 결과로 새 우승 후보를 고르지 않습니다.</p>
    <div className="research-actions"><button className="primary" disabled={blocked||!verified||!view.eligibility.canEvaluate} onClick={()=>void perform('evaluate_research')}>검증 기간 계산</button>
     {view.eligibility.canCancelEvaluation&&(working==='evaluate_research'||view.evaluation.jobStatus)&&<button disabled={cancelling||view.evaluation.cancellationRequested} onClick={()=>void cancel('evaluation')}>검증 작업 취소 요청</button>}
    </div>
    {view.evaluation.cancellationRequested&&<p>검증 작업 취소 요청이 기록되어 있습니다. 최종 상태를 확인하세요.</p>}
    <p className="micro">표가 잘리면 좌우로 이동하세요. 키보드는 표에 초점을 둔 뒤 방향키로 이동할 수 있습니다.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="전략별 기간 수익률 비교 표"><table className="research-table"><thead><tr><th>전략</th><th>개선 수익률</th><th>개선 낙폭</th><th>검증 수익률</th><th>검증 낙폭</th><th>검증 상태</th></tr></thead><tbody>{view.frozen.candidates.map(candidate=>{
     const slot=candidate.evaluationEligible?view.evaluation.slots.find(item=>item.revisionId===candidate.revisionId):undefined;
     return <tr key={candidate.index}><th scope="row">{candidate.role==='baseline'?'원본':`후보 ${candidate.index} · ${candidate.name||'이름 없음'}`}{candidate.selected&&<span className="badge">개선 기간에서 선택됨</span>}</th><td className={tone(candidate.metrics.totalReturnPct)}>{percent(candidate.metrics.totalReturnPct)}</td><td>{drawdown(candidate.metrics.maxDrawdownPct)}</td><td className={tone(slot?.metrics?.totalReturnPct)}>{percent(slot?.metrics?.totalReturnPct)}</td><td>{drawdown(slot?.metrics?.maxDrawdownPct)}</td><td>{candidate.evaluationEligible?status(slot?.status||'not_run'):'검증 대상 미선정'}{slot?.error&&<p>이 계산을 완료하지 못했습니다. 저장된 실행 내역을 확인하세요.</p>}{slot?.status==='succeeded'&&<button disabled={blocked} onClick={()=>onOpenRun(slot.runId)}>검증 리포트 열기</button>}</td></tr>;
    })}</tbody></table></div>
    <p className="micro">기간마다 자본·보유량·지표 준비를 새로 시작합니다. 두 기간의 수익률을 하나의 누적 수익률로 합치지 않습니다. 미선정·실패·취소 결과도 보존합니다.</p>
    {!view.eligibility.canEvaluate&&!['succeeded','completed_with_failures'].includes(view.evaluation.status)&&<p className="micro">{reasonText(view.eligibility.reasons.evaluate,'검증 기간 계산의 저장 상태를 확인하세요.')}</p>}
    {view.evaluation.status==='succeeded'&&<p className="micro">검증 기간 계산이 완료되어 결과를 저장했습니다.</p>}{view.evaluation.status==='completed_with_failures'&&<p>일부 계산이 완료되지 않았습니다. 성공·실패 내역을 함께 보존하며 자동으로 다시 실행하지 않습니다.</p>}{['interrupted','failed','cancelled'].includes(view.evaluation.status)&&<p>검증이 중단되었거나 완료되지 않았습니다. 저장된 결과를 보존하며 자동으로 다시 실행하지 않습니다.</p>}
   </section>}
  </>}
 </div>;
}
