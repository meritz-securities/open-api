import type {TemplateDetail} from './api';

export function TemplateGuide({detail}:{detail?:TemplateDetail}){
 if(!detail)return null;
 return <details className="template-guide">
  <summary>선택한 예제 설명</summary>
  <p>{detail.summary}</p>
  <dl className="detail-list">
   <div><dt>매수 규칙</dt><dd>{detail.buyRule}</dd></div>
   <div><dt>매도 규칙</dt><dd>{detail.sellRule}</dd></div>
   <div><dt>거래 수량</dt><dd>{detail.sizing}</dd></div>
  </dl>
  {detail.parameters.length>0&&<p className="micro">예제 기본값: {detail.parameters.map(p=>`${p.label} ${p.value}`).join(' · ')}</p>}
  <ul>{detail.cautions.map((text,index)=><li key={index}>{text}</li>)}</ul>
  <p className="micro">이 설명은 처음 제공한 예제 기준입니다. 수정한 매수·매도 조건은 아래의 현재 전략 설명에서 확인하세요.</p>
 </details>;
}
