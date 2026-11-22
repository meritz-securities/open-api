import {useEffect,useRef,useState} from 'react';
import {localTime} from './uiLabels';
import {api} from './api';
type Connection={managementAllowed:boolean;source:'session'|'inherited';configured:boolean;authenticated:null;busy:boolean;lastRequest:null|{status:'succeeded'|'failed';finishedAt:string;errorCode:string|null};notice:string};
export function MarketConnection({busy,refreshKey,onSource}:{busy:boolean;refreshKey:string;onSource?:(source:'session'|'inherited')=>void}){
 const [state,setState]=useState<Connection>(),[appKey,setAppKey]=useState(''),[appSecret,setAppSecret]=useState(''),[pending,setPending]=useState(false),[error,setError]=useState('');
 const sequence=useRef(0);
 async function request(action:string,args:Record<string,unknown>={}){
  const version=++sequence.current;setPending(true);setError('');
  try{const result=await api<Connection>(action,args);if(!result||typeof result.managementAllowed!=='boolean'||typeof result.configured!=='boolean')throw Error();if(version===sequence.current){setState(result);onSource?.(result.source);}}
  catch(error){if(version===sequence.current){const safeMessages=['키와 시크릿은 공백 없는 1~4096자여야 합니다.','시세 조회 중에는 연결을 변경할 수 없습니다.','시세 조회 중에는 연결을 해제할 수 없습니다.'];const message=error instanceof Error?error.message:'';setError(safeMessages.includes(message)?message:'연결 설정을 확인하지 못했습니다. 상태를 새로고침하고 다시 시도하세요.');}}
  finally{if(version===sequence.current)setPending(false);}
 }
 useEffect(()=>{void request('get_market_connection');return()=>{sequence.current++;};},[refreshKey]);
 const disabled=busy||pending||Boolean(state?.busy);
 return <section className="market-connection" aria-label="메리츠 연결 설정"><div className="section-title"><h2>메리츠 연결</h2><button disabled={disabled} onClick={()=>void request('get_market_connection')}>연결 상태 새로고침</button></div><p>{pending?'연결 설정 확인 중':state?state.configured?'연결 정보 설정됨 · 인증 확인 전':'연결 정보 없음':'연결 상태 미확인'}</p>{state?.lastRequest&&<p className="micro">최근 조회 {state.lastRequest.status==='succeeded'?'성공':'실패'} · {localTime(state.lastRequest.finishedAt)}</p>}{state?.lastRequest?.status==='failed'&&<p className="micro">입력 확인을 포함한 조회 처리 결과입니다. 이 표시만으로 인증 실패 여부를 판단하지 않습니다.</p>}{error&&<p role="alert" className="error">{error}</p>}{state?.notice&&<p className="micro">{state.notice}</p>}{state?.managementAllowed?<form onSubmit={event=>{event.preventDefault();if(disabled)return;const args={appKey,appSecret};setAppKey('');setAppSecret('');void request('connect_market_session',args);}}><p className="micro">이 앱 실행 중에만 보관합니다. 앱 프로세스를 종료하면 다시 입력하세요. 탭을 닫는 것만으로 해제되지 않습니다. 설정만으로 인증이나 시세 조회가 실행되지 않습니다.</p><div className="field-grid"><label>AppKey<input type="password" autoComplete="off" spellCheck={false} maxLength={4096} value={appKey} disabled={disabled} onChange={event=>setAppKey(event.target.value)}/></label><label>AppSecret<input type="password" autoComplete="off" spellCheck={false} maxLength={4096} value={appSecret} disabled={disabled} onChange={event=>setAppSecret(event.target.value)}/></label></div><div className="home-actions"><button type="submit" disabled={disabled||!appKey||!appSecret}>연결 정보 설정</button><button type="button" disabled={disabled||!state.configured} onClick={()=>{setAppKey('');setAppSecret('');void request('disconnect_market_session');}}>연결 정보 해제</button></div></form>:state&&<p className="micro">이 화면에서는 공유 연결을 변경할 수 없습니다.</p>}</section>;
}
