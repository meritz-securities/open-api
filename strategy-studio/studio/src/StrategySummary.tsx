import type {Condition, Definition, Operand} from './api';
import {describeExtendedOperand, type ExtendedOperand} from './indicatorNames';

const prices:Record<string,string>={open:'시가',high:'고가',low:'저가',close:'종가'};
function operand(value:Operand):string {
 if(value.type==='field')return prices[value.field]||'선택한 가격';
 if(value.type==='constant')return String(value.value);
 if(value.type==='sma')return `${value.period}개 봉 평균 가격`;
 if(value.type==='ema')return `최근 가격을 더 반영한 ${value.period}개 봉 평균 가격`;
 if(value.type==='rsi')return `가격 상승·하락 강도(${value.period}개 봉 RSI)`;
 // 빌더 외 지표(볼린저·MACD 등): 지표 이름·구성선·기간을 그대로 읽어 준다. "RSI" 로 오표기하지 않는다.
 return describeExtendedOperand(value as unknown as ExtendedOperand);
}
export function explainCondition(condition:Condition):string {
 let count=0;
 function visit(value:Condition,depth:number):string {
  if(++count>64||depth>8)throw Error('조건이 너무 복잡합니다. 먼저 조건을 정리하세요.');
  if('conditions' in value){
   const parts=value.conditions.map(child=>visit(child,depth+1));
   return `${value.op==='all'?'모두 만족':'하나 이상 만족'}: (${parts.join(value.op==='all'?' 그리고 ':' 또는 ')})`;
  }
  if(value.left.type==='constant'&&value.right.type==='constant'){
   const left=value.left.value,right=value.right.value;
   const matches=value.op==='gt'?left>right:value.op==='gte'?left>=right:value.op==='lt'?left<right:value.op==='lte'?left<=right:false;
   return matches?'고정된 숫자를 비교하므로 항상 만족하는 조건입니다.':'고정된 숫자를 비교하므로 성립하지 않는 조건입니다.';
  }
  const left=operand(value.left),right=operand(value.right);
  switch(value.op){
   case 'gt':return `${left} 값이 ${right}보다 높을 때`;
   case 'gte':return `${left} 값이 ${right} 이상일 때`;
   case 'lt':return `${left} 값이 ${right}보다 낮을 때`;
   case 'lte':return `${left} 값이 ${right} 이하일 때`;
   case 'cross_above':return `${left} 값이 ${right} 기준을 아래에서 위로 넘어설 때`;
   case 'cross_below':return `${left} 값이 ${right} 기준을 위에서 아래로 내려갈 때`;
  }
 }
 try{return visit(condition,1);}catch{return '조건을 읽을 수 없습니다. 편집 화면에서 조건을 확인하세요.';}
}
export function StrategySummary({definition,incomplete=false}:{definition:Definition;incomplete?:boolean}){
 if(definition.kind!=='dsl')return <p className="micro">코드로 작성한 전략은 코드의 매수·매도 규칙을 확인하세요.</p>;
 if(incomplete)return <p role="status" className="notice">현재 조건에 수정할 항목이 있습니다. 아래 편집 화면의 안내를 확인하세요.</p>;
 const fixed=definition.quantity!=null;
 return <section className="strategy-summary" aria-label="현재 전략 설명">
  <h3>이 전략은 이렇게 거래합니다</h3>
  <dl className="detail-list">
   <div><dt>살 때</dt><dd>{explainCondition(definition.entry)}</dd></div>
   <div><dt>팔 때</dt><dd>{explainCondition(definition.exit)}</dd></div>
   <div><dt>거래 수량</dt><dd>{fixed?`조건을 만족할 때마다 ${definition.quantity}주씩 매수·매도합니다.`:'살 때는 사용 가능한 현금으로, 팔 때는 보유한 주식 전부를 거래합니다.'} {definition.allowPyramiding?'보유 중에도 매수 조건이 계속 맞으면 각 봉마다 추가로 살 수 있습니다.':'주식을 보유 중이면 추가로 사지 않습니다.'}</dd></div>
  </dl>
  {(definition.risk.stopLossPct!=null||definition.risk.takeProfitPct!=null)&&<p className="micro">수수료와 슬리피지를 포함한 주당 평균 매입원가 대비 {definition.risk.stopLossPct!=null?`${definition.risk.stopLossPct}% 하락`:''}{definition.risk.stopLossPct!=null&&definition.risk.takeProfitPct!=null?' 또는 ':''}{definition.risk.takeProfitPct!=null?`${definition.risk.takeProfitPct}% 상승`:''}하면 매도 조건으로 판단합니다. {fixed?`이때도 ${definition.quantity}주씩 매도합니다.`:'이때는 전량 매도합니다.'}</p>}
  <p className="micro">각 봉(데이터 한 구간)의 종가로 판단하고 다음 봉 시가에 가상 체결합니다. 주식을 보유한 상태에서 매수·매도 조건이 함께 맞으면 매도가 우선입니다. 보유 주식이 없으면 매수 조건에 따라 삽니다. 수수료·잔고에 따라 주문이 거절될 수 있습니다.</p>
 </section>;
}
