import {useEffect,useRef,useState} from 'react';
import type {ResearchSummary} from './api';
import type {Call} from './Report';
import {localTime} from './uiLabels';

export function ResearchHistory({call,busy,onOpen}:{call:Call;busy:boolean;onOpen:(id:string)=>void}){
 const [rows,setRows]=useState<ResearchSummary[]>([]),[loaded,setLoaded]=useState(false),[failed,setFailed]=useState(false);
 const mounted=useRef(false),sequence=useRef(0);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;sequence.current++;};},[]);
 async function refresh(){
  const request=++sequence.current;setFailed(false);
  const value=await call('list_research',{limit:100});
  if(!mounted.current||request!==sequence.current)return;
  if(!value||!Array.isArray(value.sessions)){setFailed(true);return;}
  setRows(value.sessions);setLoaded(true);
 }
 return <section className="card" aria-label="기간 나눈 검증 기록">
  <div className="section-title"><h2>기간 나눈 검증</h2><button disabled={busy} onClick={()=>void refresh()}>검증 기록 새로고침</button></div>
  <p className="micro">최근 100개 기록을 조회합니다. 기록 열기는 계산을 다시 실행하지 않습니다.</p>
  {!loaded&&!failed&&<p>목록을 불러오면 이전 작업을 이어서 확인할 수 있습니다.</p>}
  {failed&&<p role="status">검증 기록을 불러오지 못했습니다. 다시 조회하세요.</p>}
  {loaded&&!rows.length&&<p>저장된 기간 분리 검증이 없습니다.</p>}
  {rows.map(row=><button className="library-item research-history-item" disabled={busy} key={row.id} onClick={()=>onOpen(row.id)}>
   <strong>{row.strategyName}</strong><span>{row.purpose}</span>
   <small>개선 {row.split.train.startDate} ~ {row.split.train.endDate} · 검증 {row.split.holdout.startDate} ~ {row.split.holdout.endDate}</small>
   <small>{localTime(row.createdAt)} · {row.id.slice(0,12)}</small>
  </button>)}
 </section>;
}
