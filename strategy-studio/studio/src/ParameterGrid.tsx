import {useEffect,useRef,useState} from 'react';
import {numeric,percent,tone,type Condition,type Definition,type Run} from './api';
import type {Call} from './Report';
import './ParameterGrid.css';
import {indicatorLabel,parameterNames,type ExtendedOperand} from './indicatorNames';

const INTEGER_PARAMETER_KEYS=new Set(['fast','slow','signal','atr_period','rsi_period','stoch_period','smooth_k','smooth_d','roc1','roc2','ema_period','period1','period2','period3','tenkan','kijun','senkou_b']);
const LOOKBACK_PARAMETER_KEYS=new Set([...INTEGER_PARAMETER_KEYS,'period','wma_period']);
function parameterRange(type:string,key:string):{min:number;max:number;integer:boolean;exclusiveMin?:boolean}{
 if(key==='num_std'||key==='mult')return {min:0,max:100,integer:false};
 if(key==='scale')return {min:1e-9,max:1e12,integer:false};
 if(key==='annualize')return {min:1,max:100_000,integer:true};
 if(key==='step'||key==='vfactor'||key==='max_step')return {min:0,max:1,integer:false};
 if(key==='period'||key==='wma_period')return {min:key==='period'&&type==='force_index'?1:2,max:250,integer:true};
 if(INTEGER_PARAMETER_KEYS.has(key))return {min:1,max:250,integer:true};
 return {min:0,max:100_000,integer:false};
}

type Parameter={path:string;label:string;value:number|null;min:number;max:number;integer:boolean;exclusiveMin?:boolean};
type Axis={path:string;text:string};
type Trial={index:number;parameterValues:Record<string,number>;revisionId?:string;status:string;runId?:string;error?:string;result?:{runId:string;metrics:Record<string,number|null>}};
export type GridJob={id:string;runId:string;method:'parameter_grid';status:string;options:{axes:{path:string;values:number[]}[]};candidateCount:number;baselineReturnPct:number;candidateResults:Trial[];error?:string};
export type GridValidation={id:string;runId:string;method:'parameter_grid';options:{axes:{path:string;values:number[]}[];maxEvaluations?:number};result:{status:string;candidateCount:number;results:Trial[];bestCandidateIndex:number|null;bestCandidateImproved?:boolean|null;baselineComparable?:boolean;comparisonWarning?:string;baselineReturnPct:number;deadlineSeconds:number;assumptions:string[]}};

// Expand the shortest round-tripping JS representation without rounding digits.
function plainNumber(value:number):string{
 const text=String(value);if(!/[eE]/.test(text))return text;
 const [coefficient,power]=text.toLowerCase().split('e'),negative=coefficient.startsWith('-');
 const [whole,fraction='']=(negative?coefficient.slice(1):coefficient).split('.');
 const digits=whole+fraction,point=whole.length+Number(power);
 const expanded=point<=0?'0.'+'0'.repeat(-point)+digits:point>=digits.length?digits+'0'.repeat(point-digits.length):digits.slice(0,point)+'.'+digits.slice(point);
 return (negative?'-':'')+expanded;
}
function parameters(definition:Definition):Parameter[]{
 if(definition.kind!=='dsl')return [];
 const result:Parameter[]=[{path:'quantity',label:'매수 수량 (주)',value:definition.quantity,min:1,max:1e9,integer:true},
  {path:'risk.stopLossPct',label:'손절 기준 (%)',value:definition.risk?.stopLossPct??null,min:0,max:100,integer:false,exclusiveMin:true},
  {path:'risk.takeProfitPct',label:'익절 기준 (%)',value:definition.risk?.takeProfitPct??null,min:0,max:100,integer:false,exclusiveMin:true}];
 const visit=(condition:Condition,path:string,label:string)=>{
  if(condition.op==='all'||condition.op==='any'){condition.conditions.forEach((child,i)=>visit(child,`${path}.conditions.${i}`,`${label} ${i+1}`));return;}
  if(!('left' in condition))return;
  for(const side of ['left','right'] as const){const operand=condition[side],part=`${label} · ${side==='left'?'왼쪽':'오른쪽'}`;
   if(operand.type==='constant')result.push({path:`${path}.${side}.value`,label:`${part} 비교값`,value:operand.value,min:-1e12,max:1e12,integer:false});
   else if(operand.type==='field')continue;
   else{
    // 모든 지표의 숫자 조건값을 축으로 제공한다(빌더 외 지표 포함). 범위는 서버 DSL 검증(runtime/dsl.py: 기간 2~250, 배수 0~100)과 같다.
    const extended=operand as unknown as ExtendedOperand,name=indicatorLabel(extended.type);
    for(const [key,value] of Object.entries(extended)){
     if(key==='type'||key==='component'||typeof value!=='number')continue;
     const range=parameterRange(extended.type,key);
     result.push({path:`${path}.${side}.${key}`,label:`${part} ${name} ${parameterNames[key]||key}${LOOKBACK_PARAMETER_KEYS.has(key)?' (봉)':''}`,value,...range});
    }
   }
  }
 };
 visit(definition.entry,'entry','매수 조건');visit(definition.exit,'exit','매도 조건');return result;
}
function valuesFor(axis:Axis,parameter?:Parameter):{values:number[];error?:string}{
 const parts=axis.text.split(',').map(value=>value.trim());
 if(!parameter||!parts.length||parts.length>12||parts.some(value=>!/^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?$/i.test(value)))return {values:[],error:'숫자 1~12개를 쉼표로 구분하세요.'};
 const values=parts.map(Number);
 if(values.some(value=>!Number.isFinite(value)||value<parameter.min||value>parameter.max||(parameter.exclusiveMin&&value===parameter.min)||(parameter.integer&&!Number.isInteger(value))))return {values:[],error:`${parameter.exclusiveMin?'0 초과':numeric(parameter.min)+' 이상'} ${numeric(parameter.max)} 이하${parameter.integer?' 정수':''}로 입력하세요.`};
 if(new Set(values).size!==values.length)return {values:[],error:'같은 값을 중복 입력하지 마세요.'};
 return {values};
}

export function ParameterGrid({run,call,busy,restoredRecord,restoredJob,onExecutionStart}:{run:Run;call:Call;busy:boolean;restoredRecord?:GridValidation;restoredJob?:GridJob;onExecutionStart:()=>void}){
 const [choices,setChoices]=useState<Parameter[]>([]),[axes,setAxes]=useState<Axis[]>([]),[loaded,setLoaded]=useState(false),[loadFailed,setLoadFailed]=useState(false),[unsupported,setUnsupported]=useState(false),[reload,setReload]=useState(0),[record,setRecord]=useState<GridValidation>(),[job,setJob]=useState<GridJob>(),[submitting,setSubmitting]=useState(false),[executionFailed,setExecutionFailed]=useState(false);
 const resultHeading=useRef<HTMLHeadingElement>(null);
 const currentCall=useRef(call);currentCall.current=call;
 const mounted=useRef(false),restored=useRef<GridValidation|GridJob|undefined>(restoredRecord||restoredJob);restored.current=restoredRecord||restoredJob;
 useEffect(()=>{mounted.current=true;let active=true;setLoaded(false);setLoadFailed(false);
  void currentCall.current('get_strategy',{revisionId:run.revisionId}).then(value=>{
   if(!active)return;
   if(!value||value.id!==run.revisionId||!value.definition){setLoadFailed(true);return;}
   const available=parameters(value.definition);setChoices(available);setUnsupported(value.definition.kind!=='dsl');setLoaded(true);
   if(!restored.current)setAxes(available.length?[{path:available[0].path,text:plainNumber(available[0].value??1)}]:[]);
  }).catch(()=>{if(active)setLoadFailed(true);});
  return()=>{active=false;mounted.current=false;};
 },[run.revisionId,reload]);
 useEffect(()=>{if(restoredRecord?.runId===run.id){setAxes(restoredRecord.options.axes.map(axis=>({path:axis.path,text:axis.values.map(plainNumber).join(', ')})));setRecord(restoredRecord);setJob(undefined);setExecutionFailed(false);}},[restoredRecord,run.id]);
 useEffect(()=>{if(restoredJob?.runId===run.id){setAxes(restoredJob.options.axes.map(axis=>({path:axis.path,text:axis.values.map(plainNumber).join(', ')})));setJob(restoredJob);setRecord(undefined);setExecutionFailed(false);}},[restoredJob,run.id]);
 useEffect(()=>{if(!record&&!job)return;const frame=requestAnimationFrame(()=>{const heading=resultHeading.current;if(heading?.getClientRects().length){heading.focus({preventScroll:true});heading.scrollIntoView({block:'nearest'});}});return()=>cancelAnimationFrame(frame);},[record,job]);
 const parsed=axes.map(axis=>valuesFor(axis,choices.find(choice=>choice.path===axis.path)));
 const count=parsed.reduce((total,axis)=>total*axis.values.length,1);
 const valid=loaded&&!unsupported&&axes.length>0&&axes.length<=3&&new Set(axes.map(axis=>axis.path)).size===axes.length&&parsed.every(axis=>!axis.error)&&count<=12;
 const label=(path:string)=>choices.find(choice=>choice.path===path)?.label||'조건';
 const change=(index:number,patch:Partial<Axis>)=>{setAxes(old=>old.map((axis,i)=>i===index?{...axis,...patch}:axis));setRecord(undefined);setJob(undefined);setExecutionFailed(false);};
 const result=record?.result||(job?{status:job.status,candidateCount:job.candidateCount,results:job.candidateResults,bestCandidateIndex:null,baselineReturnPct:job.baselineReturnPct,assumptions:[],bestCandidateImproved:null}:undefined);
 const baselineComparable=record?.result.baselineComparable===true;
 const baselineNotice=job?'작업 내역의 원본 수익률은 참고값입니다. 완료된 비교 결과에서 개선 여부를 확인하세요.':record?.result.baselineComparable===false?'원본 대비 개선 여부를 판단할 수 없습니다. 모든 후보가 완료되고 계산 버전과 시세 조건이 같아야 비교할 수 있습니다.':'원본과 계산 조건이 같은지 기록되지 않아 원본 대비 개선 여부를 판단하지 않습니다.';
 const statusLabel=(status:string)=>({completed:'완료',completed_with_failures:'실패 포함',succeeded:'완료',failed:'실패',cancelled:'취소',interrupted:'중단',pending:'미실행',not_run:'미실행',running:'진행 중'} as Record<string,string>)[status]||'상태 확인 필요';
 const originalValue=(path:string)=>{const value=choices.find(choice=>choice.path===path)?.value;return value==null?'지정 안 함':plainNumber(value);};
 const jobNotice=(status:string)=>status==='cancelled'?'검증이 취소되었습니다.':status==='interrupted'?'작업이 중단되었습니다.':status==='failed'?'작업을 완료하지 못했습니다.':status==='running'?'작업이 진행 중입니다.':'보관된 작업 내역입니다.';
 return <div className="parameter-grid">
  <p className="subtle">원본 전략의 조건 값만 바꿔 같은 시세·초기 자본·비용으로 다시 계산합니다. 원본은 그대로 보관합니다.</p>
  {!loaded&&!loadFailed&&<p role="status">원본 전략을 확인하고 있습니다.</p>}
  {loadFailed&&<p role="status">원본 전략을 불러오지 못했습니다. <button disabled={busy} onClick={()=>setReload(value=>value+1)}>원본 다시 불러오기</button></p>}
  {loaded&&unsupported&&<p>조건 값 비교는 비주얼 빌더 전략에서 사용할 수 있습니다. Python 전략은 코드를 수정한 뒤 개별 백테스트로 비교하세요.</p>}
  {loaded&&!unsupported&&<fieldset disabled={busy||submitting}>
   {axes.map((axis,index)=><div className="field-grid" key={index}>
    <label>비교 항목 {index+1}<select value={axis.path} onChange={event=>{const parameter=choices.find(choice=>choice.path===event.target.value)!;change(index,{path:parameter.path,text:plainNumber(parameter.value??(parameter.path==='quantity'?1:5))});}}>{choices.filter(choice=>choice.path===axis.path||!axes.some(other=>other.path===choice.path)).map(choice=><option key={choice.path} value={choice.path}>{choice.label}</option>)}</select></label>
    <label>비교할 값 {index+1}<input type="text" aria-label={`비교할 값 ${index+1}`} maxLength={300} value={axis.text} placeholder="예: 5, 10, 20" aria-invalid={!!parsed[index]?.error} aria-describedby={`grid-values-${index}`} onChange={event=>change(index,{text:event.target.value})}/><small id={`grid-values-${index}`}>{parsed[index]?.error||`원본: ${originalValue(axis.path)} · 쉼표로 구분`}</small></label>
    {axes.length>1&&<button type="button" aria-label={`비교 항목 ${index+1} 삭제`} onClick={()=>{setAxes(old=>old.filter((_,i)=>i!==index));setRecord(undefined);setJob(undefined);}}>삭제</button>}
   </div>)}
   <button type="button" disabled={axes.length>=3||axes.length>=choices.length} onClick={()=>{const choice=choices.find(item=>!axes.some(axis=>axis.path===item.path));if(choice){setAxes(old=>[...old,{path:choice.path,text:plainNumber(choice.value??5)}]);setRecord(undefined);setJob(undefined);}}}>비교 항목 추가</button>
   <p>{parsed.some(axis=>axis.error)?'입력값을 확인하세요.':`총 ${count}개 조합 · 최대 12개`}{count>12&&' — 값을 줄여주세요.'}</p>
   <p className="micro">최대 3개 항목을 조합합니다. 이미 확인한 과거 시세에서의 비교이며, 가장 높은 수익률이 미래 성과를 보장하지 않습니다.</p>
   <button className="primary" disabled={!valid||busy||submitting} onClick={async()=>{setSubmitting(true);setRecord(undefined);setJob(undefined);setExecutionFailed(false);onExecutionStart();try{const value=await call('validate_robustness',{runId:run.id,method:'parameter_grid',options:{axes:axes.map((axis,i)=>({path:axis.path,values:parsed[i].values})),maxEvaluations:12}});if(mounted.current){if(value?.runId===run.id&&value.method==='parameter_grid')setRecord(value);else setExecutionFailed(true);}}catch{if(mounted.current)setExecutionFailed(true);}finally{if(mounted.current)setSubmitting(false);}}}>조건 값 비교 실행</button>
  </fieldset>}
  {executionFailed&&<p role="status">비교를 완료하지 못했습니다. 상단 오류와 검증 이력을 확인하세요. 완료된 개별 실행은 실행 기록에 남습니다.</p>}
  {result&&<section aria-label="조건 값 비교 결과"><div className="section-title"><h3 ref={resultHeading} tabIndex={-1}>조건 값 비교 결과</h3><span className="badge">{statusLabel(result.status)}</span></div>
   <p className="micro" role="status" aria-live="polite">조건 값 비교 {statusLabel(result.status)} · {result.results.length}개 조합을 확인할 수 있습니다.</p>
   {record&&restoredRecord&&record.id===restoredRecord.id&&<p className="micro">저장된 결과 · {record?.id.slice(0,12)}</p>}
   {job&&<p role="status">저장된 작업 · {job.id.slice(0,12)} · {jobNotice(job.status)} 계획 {job.candidateCount}개</p>}
   <p className="micro">수익이 낮아지거나 실행에 실패한 조합도 모두 표시합니다. ‘후보 중 최고’는 이번 조합 안에서만 비교한 값입니다.</p>
   {!baselineComparable&&<p role="status">{baselineNotice}</p>}
   <div className="table-scroll"><table><thead><tr><th>조건</th><th>수익률</th><th>최대 낙폭</th><th>상태</th></tr></thead><tbody>
    <tr><th scope="row">{baselineComparable?'원본 (기준)':'원본 (참고)'}</th><td className={tone(result.baselineReturnPct)}>{percent(result.baselineReturnPct)}</td><td>{percent(-Math.abs(run.report.metrics.maxDrawdownPct))}</td><td>원본 보존</td></tr>
    {result.results.map(trial=><tr key={trial.index}><th scope="row">{Object.entries(trial.parameterValues).map(([path,value])=><div key={path}>{label(path)}: {plainNumber(value)}</div>)}</th><td className={tone(trial.result?.metrics.totalReturnPct)}>{percent(trial.result?.metrics.totalReturnPct)}</td><td>{trial.result?.metrics.maxDrawdownPct==null?'—':percent(-Math.abs(trial.result.metrics.maxDrawdownPct))}</td><td>{statusLabel(trial.status)}{trial.index===result.bestCandidateIndex&&' · 후보 중 최고'}{trial.index===result.bestCandidateIndex&&baselineComparable&&result.bestCandidateImproved===false&&' · 원본보다 높지 않음'}{trial.error&&<p>{trial.status==='cancelled'?'이 조합의 실행이 취소되었습니다.':trial.status==='interrupted'?'이 조합의 실행이 중단되었습니다.':'이 조합을 실행하지 못했습니다. 실행 기록에서 확인하세요.'}</p>}</td></tr>)}
   </tbody></table></div>
   <details><summary>개별 실행과 계산 조건</summary><p className="micro">각 결과는 실행 기록에서 전체 리포트로 확인할 수 있습니다. 동률은 입력 순서대로 표시합니다. 일부 실패가 있으면 최고 후보를 정하지 않습니다.</p>{result.results.map(trial=><p key={trial.index}>조합 {trial.index} · <code>{trial.result?.runId||trial.runId||'실행 기록 없음'}</code></p>)}<ul><li>원본에 저장된 시세와 초기 자본·비용으로 각 조합을 따로 실행합니다.</li><li>후보의 수익률이 같으면 입력 순서를 따릅니다. 실패한 조합은 숨기지 않습니다.</li><li>이미 확인한 기간을 다시 비교한 결과이며, 미래 수익이나 독립적인 미관측 성과를 뜻하지 않습니다.</li></ul><pre className="json-view">{JSON.stringify(record||job,null,2)}</pre></details>
  </section>}
 </div>;
}
