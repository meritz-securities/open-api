import {useEffect,useRef,useState} from 'react';
import {percent,tone,type Revision,type Run} from './api';
import type {Call} from './Report';

type Trial={revisionId?:string;status:string;error?:string;runId?:string;result?:{runId:string;metrics:Record<string,number|null>;settings:unknown;dataHash:string}};
type Window={startDate:string;endDate:string;barCount:number};
type Fold={index:number;status:string;error?:string;train:Window;testWindow:Window;training:Trial[];selectedRevisionId:string|null;test:Trial|null};
export type Validation={id:string;runId:string;method:string;options:{trainBars?:number;testBars?:number;candidateRevisionIds?:string[]};result:{method:string;status:string;folds:Fold[];droppedTailBars:number;selectionWarning:string;assumptions:unknown;holdoutContaminated:boolean;holdoutAccessed:boolean;selectionMetric:string}};
const labels:Record<string,string>={completed:'완료',completed_with_failures:'실패 포함',succeeded:'완료',failed:'실패',training_failed:'학습 실패',test_failed:'검증 실패',skipped:'생략'};

export function WalkForward({run,call,busy,restoredRecord,onExecutionStart}:{run:Run;call:Call;busy:boolean;restoredRecord?:Validation;onExecutionStart?:()=>void}){
 const bars=run.report.equity.length;
 const initialTrain=Math.max(2,Math.floor(bars/2));
 const [train,setTrain]=useState(initialTrain),[test,setTest]=useState(Math.max(2,Math.ceil((bars-initialTrain)/3)));
 const [revisions,setRevisions]=useState<Revision[]>([]),[selected,setSelected]=useState<string[]>([]),[loaded,setLoaded]=useState(false),[loading,setLoading]=useState(true),[record,setRecord]=useState<Validation>();
 useEffect(()=>{if(restoredRecord?.runId===run.id){setRecord(restoredRecord);setTrain(restoredRecord.options.trainBars??initialTrain);setTest(restoredRecord.options.testBars??2);setSelected(restoredRecord.options.candidateRevisionIds??[]);}},[restoredRecord,run.id]);
 const currentCall=useRef(call);currentCall.current=call;
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;void currentCall.current('list_strategies',{}).then(result=>{if(mounted.current&&result){setRevisions(result.strategies);setLoaded(true);}if(mounted.current)setLoading(false);});return()=>{mounted.current=false;};},[]);
 const folds=Number.isInteger(train)&&Number.isInteger(test)&&test>0?Math.floor((bars-train)/test):0;
 const valid=Number.isInteger(train)&&Number.isInteger(test)&&train>=2&&test>=2&&train<=5000&&test<=5000&&folds>=1&&folds<=6;
 const name=(id:string)=>revisions.find(item=>item.id===id)?.definition.name||id.slice(0,12);
 const extras=revisions.filter(item=>item.id!==run.revisionId);
 const result=record?.result;
 return <div className="walk-forward">
  <fieldset disabled={busy}>
   <div className="field-grid"><label>학습 봉 수<input type="number" min={2} max={5000} step={1} value={train} onChange={e=>{setTrain(Number(e.target.value));setRecord(undefined);}}/></label><label>검증 봉 수<input type="number" min={2} max={5000} step={1} value={test} onChange={e=>{setTest(Number(e.target.value));setRecord(undefined);}}/></label></div>
   <p className="micro">{bars}봉 · {valid?`${folds}개 구간 · 마지막 ${Math.max(0,(bars-train)%test)}봉 제외`:'학습·검증은 각각 2~5,000봉, 완전한 구간은 1~6개로 설정하세요.'}</p>
   <p className="subtle">기준 전략: {name(run.revisionId)} · 자동 포함</p>
   <details className="walk-candidates"><summary>추가 후보 선택 ({selected.length}/3)</summary>{!loaded?<><p className="micro">{loading?'저장된 후보를 불러오는 중입니다.':'저장된 후보를 불러오지 못했습니다.'}</p><button type="button" onClick={async()=>{setLoading(true);const value=await call('list_strategies',{});if(value){setRevisions(value.strategies);setLoaded(true);}setLoading(false);}}>후보 다시 불러오기</button></>:extras.length?extras.map(item=><label className="walk-candidate" key={item.id}><input type="checkbox" checked={selected.includes(item.id)} disabled={!selected.includes(item.id)&&selected.length>=3} onChange={e=>{setSelected(old=>e.target.checked?[...old,item.id]:old.filter(id=>id!==item.id));setRecord(undefined);}}/><span>{item.definition.name}<small>{item.id.slice(0,12)} · {item.definition.kind}</small></span></label>):<p className="micro">추가로 저장된 버전이 없습니다. 기준 전략만 검증할 수 있습니다.</p>}</details>
   <p className="micro">고정 길이 학습 구간을 검증 봉 수만큼 이동합니다. 학습 수익률(totalReturnPct)로만 후보를 선택하며, 각 검증 구간은 독립 상태로 시작합니다. 학습 실패 구간은 선택·검증을 생략합니다.</p>
   <p className="micro">이미 관측한 전체 시세의 사후 분석입니다. 독립적인 미관측 성과가 아니며 후보 선택에 따른 오염 가능성이 있습니다. 구간 수익률을 하나의 누적 수익곡선으로 합치지 않습니다.</p>
   <button className="primary" disabled={!valid||busy} onClick={async()=>{setRecord(undefined);onExecutionStart?.();const value=await call('validate_robustness',{runId:run.id,method:'walk_forward',options:{candidateRevisionIds:selected,trainBars:train,testBars:test}});if(mounted.current&&value)setRecord(value);}}>Walk-forward 실행</button>
  </fieldset>
  {result&&<section className="walk-result" aria-label="Walk-forward 결과"><div className="section-title"><h3>Walk-forward 결과</h3>{record?.id===restoredRecord?.id&&<span className="micro">저장된 결과 · {record?.id.slice(0,12)}</span>}<span className="badge">{labels[result.status]||result.status}</span></div><p className="micro">{result.selectionWarning}</p><div className="table-scroll"><table><thead><tr><th>구간</th><th>학습 기간</th><th>검증 기간</th><th>선택 후보</th><th>학습 수익률</th><th>검증 수익률</th><th>상태</th></tr></thead><tbody>{result.folds.map((fold,index)=>{
   const winner=fold.training.find(item=>item.revisionId===fold.selectedRevisionId);
   const trainingReturn=winner?.result?.metrics.totalReturnPct,testReturn=fold.test?.result?.metrics.totalReturnPct;
   return <tr key={fold.index}><td>{index+1}</td><td>{fold.train.startDate}<br/>{fold.train.endDate} · {fold.train.barCount}봉</td><td>{fold.testWindow.startDate}<br/>{fold.testWindow.endDate} · {fold.testWindow.barCount}봉</td><td>{fold.selectedRevisionId?name(fold.selectedRevisionId):'선택 없음'}</td><td className={tone(trainingReturn)}>{percent(trainingReturn)}</td><td className={tone(testReturn)}>{percent(testReturn)}</td><td>{labels[fold.status]||fold.status}</td></tr>;
  })}</tbody></table></div><p className="micro">마지막 미완성 구간 {result.droppedTailBars}봉 제외 · 각 학습·검증 실행의 전체 리포트는 실행 기록에 보관됩니다.</p><details><summary>구간별 실행 ID와 실패 근거</summary>{result.folds.map((fold,index)=><div className="walk-evidence" key={fold.index}><strong>구간 {index+1}</strong>{fold.error&&<p className="walk-error">{fold.error}</p>}{fold.training.map((trial,i)=><p key={i}>학습 · {trial.revisionId?name(trial.revisionId):'후보'} · {labels[trial.status]||trial.status}<br/><code>{trial.result?.runId||trial.runId||'실행 ID 없음'}</code>{trial.error&&<span className="walk-error">{trial.error}</span>}</p>)}<p>검증 · {fold.test?(labels[fold.test.status]||fold.test.status):'생략'}<br/><code>{fold.test?.result?.runId||fold.test?.runId||'실행 ID 없음'}</code>{fold.test?.error&&<span className="walk-error">{fold.test.error}</span>}</p></div>)}</details><details><summary>검증 기록과 가정</summary><pre className="json-view">{JSON.stringify(record,null,2)}</pre></details></section>}
 </div>;
}
