import {localTime,runStatus} from './uiLabels';
import type {Dataset,Revision} from './api';
import './Home.css';
type Summary={id:string;revisionId?:string;datasetId?:string;status:string;createdAt?:string};
export function Home({revisions,datasets,runs,busy,ready,onEdit,onRun,onData,onNavigate,onRefresh}:{revisions:Revision[];datasets:Dataset[];runs:Summary[];busy:boolean;ready:boolean;onEdit:(r:Revision)=>void;onRun:(id:string)=>void;onData:(id:string)=>void;onNavigate:(page:string)=>void;onRefresh:()=>void}){
 const name=(d:Dataset)=>String(d.source.symbol||d.source.label||d.id.slice(0,12));
 const unavailable=busy||!ready;
 return <div className="home home-console">
  <div className="home-commandbar"><h1>홈</h1><div role="group" aria-label="바로 시작"><button onClick={()=>onNavigate('workspace')}>전략 이어서 작성</button><button onClick={()=>onNavigate('market')}>시세 조회</button><button onClick={()=>onNavigate('library')}>전체 기록</button><button disabled={unavailable} onClick={onRefresh}>목록 새로고침</button></div></div>
  <p className="home-scope">내 PC 저장 목록 · 각 목록 최근 5건 / 조회 범위 최대 100건 · 전체 보유 수가 아닙니다.</p>
  {!ready&&<p className="notice" role="status">저장 목록을 아직 확인하지 못했습니다. 하단 연결 상태를 확인하세요. 표시된 목록이 있으면 이전에 확인한 내용입니다.</p>}
  <div className="home-registers">
   <section aria-label="내 전략" className="home-register"><h2>내 전략 <span>표시 {Math.min(revisions.length,5)}건</span></h2>
    <ul>{revisions.slice(0,5).map(r=><li key={r.id}><button className="home-record" disabled={unavailable} onClick={()=>onEdit(r)}><strong>{r.definition.name}</strong><span>{r.definition.kind==='python'?'Python 코드':'조건형 전략'}</span><small>저장 버전 {r.id.slice(0,8)}</small></button></li>)}</ul>
    {ready&&!revisions.length&&<div className="home-empty"><p>저장한 전략이 없습니다.</p><button onClick={()=>onNavigate('workspace')}>예제로 시작</button></div>}
   </section>
   <section aria-label="저장 시세" className="home-register"><h2>저장 시세 <span>표시 {Math.min(datasets.length,5)}건</span></h2>
    <ul>{datasets.slice(0,5).map(d=><li key={d.id}><button className="home-record home-data-record" disabled={unavailable} onClick={()=>onData(d.id)}><strong>{name(d)}</strong><span>{d.source.kind==='synthetic'?'연습용 합성 시세':d.source.kind==='meritz'?'메리츠 시세':'가져온 시세'} · {String(d.source.currency||'통화 미확인')}</span><small>{d.startDate||'기간 미확인'} ~ {d.endDate||'기간 미확인'} · {d.barCount??d.count??'—'}개 기록</small></button></li>)}</ul>
    {ready&&!datasets.length&&<div className="home-empty"><p>저장한 시세가 없습니다.</p><button onClick={()=>onNavigate('market')}>종목·기간 조회</button></div>}
   </section>
  </div>
  <section className="home-runs" aria-label="최근 실행"><h2>최근 실행 <span>표시 {Math.min(runs.length,5)}건</span></h2>
   {runs.length>0&&<><p className="home-scroll-hint">표가 잘리면 좌우로 이동하세요. 키보드는 표에 초점을 둔 뒤 방향키를 사용하세요.</p><div className="table-scroll" role="region" aria-label="최근 실행 목록 표" tabIndex={0}><table><thead><tr><th scope="col">전략 / 시세</th><th scope="col">실행 시각</th><th scope="col">상태</th><th scope="col">열기</th></tr></thead><tbody>{runs.slice(0,5).map(r=>{const strategy=revisions.find(s=>s.id===r.revisionId),data=datasets.find(d=>d.id===r.datasetId);return <tr key={r.id}><th scope="row"><strong>{strategy?.definition.name||`전략 ${r.revisionId?.slice(0,8)||'미확인'} · 최근 목록 밖`}</strong><small>{data?name(data):`시세 ${r.datasetId?.slice(0,8)||'미확인'} · 최근 목록 밖`}</small></th><td>{localTime(r.createdAt)}</td><td><span className={`home-run-state state-${['succeeded','failed','cancelled','interrupted','running','queued'].includes(r.status)?r.status:'unknown'}`}>{runStatus(r.status)}</span></td><td><button disabled={unavailable} onClick={()=>onRun(r.id)}>{r.status==='succeeded'?'결과 열기':'상태 확인'}</button></td></tr>;})}</tbody></table></div></>}
   {ready&&!runs.length&&<p className="home-empty">실행 기록이 없습니다. 전략을 저장하고 시세를 선택하면 백테스트를 실행할 수 있습니다.</p>}
  </section>
 </div>;
}
