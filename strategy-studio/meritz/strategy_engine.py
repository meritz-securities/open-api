"""Single host-owned virtual broker. It never executes strategy source or calls APIs.

Version 2 adds DAY limits, sell tax, rejection events and weighted-average accounting.
Run hashes intentionally change with the engine version; legacy metrics remain compatible.
"""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
import math
import re

ENGINE_VERSION = "shared-broker-2"
DEFAULTS = {"initialCash": 1_000_000, "feeBps": 15, "slippageBps": 10}
_OPTIONAL = {"taxBps": 0, "rejectInsufficient": False}
_BAR_FIELDS = ("date", "open", "high", "low", "close")
_SOURCE_FIELDS = ("kind", "label", "symbol", "market", "exchange", "quoteMode", "startDate", "endDate", "fetchedAt", "adjustment", "currency", "timezone", "contentHash", "dataVersion")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _inputs(candles, settings, source, strategy_info):
    if type(settings) is not dict or not set(DEFAULTS) <= set(settings) <= set(DEFAULTS) | set(_OPTIONAL):
        raise ValueError("전략 설정은 initialCash·feeBps·slippageBps와 선택 taxBps·rejectInsufficient만 지정하십시오.")
    config = {**_OPTIONAL, **settings}
    if not all(_number(config[key]) for key in (*DEFAULTS, "taxBps")) or type(config['rejectInsufficient']) is not bool:
        raise ValueError("전략 설정은 유한한 숫자와 올바른 거절 정책이어야 합니다.")
    if not 1 <= config["initialCash"] <= 1e12 or any(not 0 <= config[k] <= 1000 for k in ('feeBps','slippageBps','taxBps')):
        raise ValueError("초기 자금 또는 비용 설정이 지원 범위를 벗어났습니다.")
    if type(candles) is not list or not 2 <= len(candles) <= 5000:
        raise ValueError("시세는 2개 이상, 5,000개 이하여야 합니다.")
    normalized, previous = [], ""
    for bar in candles:
        try:
            if type(bar) is not dict:
                raise ValueError
            if any(k in bar for k in ('corporateActions', 'corporateAction', 'split', 'dividend')):
                raise ValueError('unsupported corporate actions')
            date = bar['date']
            if type(date) is not str or dt.date.fromisoformat(date).isoformat() != date or date <= previous:
                raise ValueError
            if any(not _number(bar[k]) or not .01 <= bar[k] <= 1e9 for k in _BAR_FIELDS[1:]):
                raise ValueError
            if bar['low'] > min(bar['open'],bar['close']) or bar['high'] < max(bar['open'],bar['close']):
                raise ValueError
            if 'volume' in bar and (not _number(bar['volume']) or not 0 <= bar['volume'] <= 1e15):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("일봉 날짜·가격·순서·거래량을 확인하십시오. 기업행동 이벤트는 지원하지 않습니다.") from None
        normalized.append({k:bar[k] for k in (*_BAR_FIELDS,'volume') if k in bar})
        previous = date
    source = source if source is not None else {'kind':'synthetic','label':'합성 일봉 · 실제 종목/시세 아님'}
    if type(source) is not dict or source.get('kind') not in ('synthetic','meritz','imported'):
        raise ValueError('지원하지 않는 데이터 출처입니다.')
    if any(k in source for k in ('corporateActions','corporateAction')):
        raise ValueError('기업행동 이벤트 처리는 지원하지 않습니다.')
    safe_source = {k:source[k] for k in _SOURCE_FIELDS if k in source}
    if 'label' not in safe_source or any(type(v) is not str or len(v)>200 for v in safe_source.values()):
        raise ValueError('데이터 출처 정보를 확인하십시오.')
    if type(strategy_info) is not dict or not {'name','codeHash'} <= set(strategy_info) <= {'name','codeHash','interface'}:
        raise ValueError('전략 이름과 코드 해시를 지정하십시오.')
    name, code_hash = strategy_info['name'], strategy_info['codeHash']
    if type(name) is not str or not name.strip() or len(name)>80 or any(ord(c)<32 for c in name):
        raise ValueError('전략 이름은 제어문자 없이 1~80자로 지정하십시오.')
    if type(code_hash) is not str or not re.fullmatch(r'[a-f0-9]{64}',code_hash):
        raise ValueError('전략 코드 SHA-256 해시를 확인하십시오.')
    if strategy_info.get('interface','on_bar_v1') != 'on_bar_v1':
        raise ValueError('지원하는 전략 인터페이스는 on_bar_v1입니다.')
    return normalized, config, safe_source, {'name':name,'codeHash':code_hash,'interface':'on_bar_v1'}


def _evidence(value):
    """Closed JSON values with finite numbers, bounded depth/nodes/encoded size."""
    count = 0
    def visit(item, depth=0):
        nonlocal count
        count += 1
        if depth > 8 or count > 256:
            raise ValueError('전략 근거의 구조 제한을 초과했습니다.')
        if item is None or type(item) is bool: return
        if type(item) in (int,float):
            if not _number(item): raise ValueError('전략 근거 숫자는 유한해야 합니다.')
            return
        if type(item) is str:
            if len(item)>1000 or any(0xD800<=ord(c)<=0xDFFF for c in item): raise ValueError('전략 근거 문자열 제한을 초과했습니다.')
            return
        if type(item) is list:
            for child in item: visit(child,depth+1)
            return
        if type(item) is dict:
            for key,child in item.items():
                if type(key) is not str or len(key)>80: raise ValueError('전략 근거 키를 확인하십시오.')
                visit(child,depth+1)
            return
        raise ValueError('전략 근거는 JSON 값이어야 합니다.')
    visit(value)
    if len(json.dumps(value,ensure_ascii=True,allow_nan=False).encode())>8192:
        raise ValueError('전략 근거 크기 제한을 초과했습니다.')
    return copy.deepcopy(value)


def _decision(raw):
    allowed={'action','quantity','reason','orderType','limitPrice','evidence'}
    if type(raw) is not dict or 'action' not in raw or not set(raw)<=allowed:
        raise ValueError('전략은 허용된 주문 판단 필드만 반환해야 합니다.')
    action=raw['action']
    if type(action) is not str or action not in ('buy','sell','hold'):
        raise ValueError('전략 action은 buy·sell·hold 중 하나여야 합니다.')
    if 'quantity' in raw and (action=='hold' or type(raw['quantity']) is not int or not 1<=raw['quantity']<=1_000_000_000):
        raise ValueError('매매 수량은 1~10억의 정수여야 하며 hold에는 수량을 지정할 수 없습니다.')
    reason=raw.get('reason','설명 없음')
    if type(reason) is not str or len(reason)>500 or any(ord(c)<32 and c not in '\n\t' for c in reason):
        raise ValueError('전략 설명은 제어문자 없이 500자 이하여야 합니다.')
    order_type=raw.get('orderType','market')
    if order_type not in ('market','limit') or type(order_type) is not str:
        raise ValueError('orderType은 market 또는 limit이어야 합니다.')
    if action=='hold' and any(k in raw for k in ('orderType','limitPrice')):
        raise ValueError('hold에는 주문 조건을 지정할 수 없습니다.')
    if order_type=='limit':
        if not _number(raw.get('limitPrice')) or not .01<=raw['limitPrice']<=1e9:
            raise ValueError('지정가는 유효한 양수여야 합니다.')
    elif 'limitPrice' in raw:
        raise ValueError('시장가 주문에는 limitPrice를 지정할 수 없습니다.')
    result={'action':action,'reason':reason}
    if 'quantity' in raw: result['quantity']=raw['quantity']
    if action!='hold': result['orderType']=order_type
    if order_type=='limit': result['limitPrice']=raw['limitPrice']
    if 'evidence' in raw: result['evidence']=_evidence(raw['evidence'])
    return result


def run_strategy(candles, settings, decide, source, strategy_info, *, evidence_origin="user-asserted"):
    """One intent per close, next-bar market/DAY-limit execution, no shorting.

    Weighted average cost includes buy fees. Sell P&L deducts sell fees and tax.
    Tax is a user-specified bps assumption, not a jurisdictional tax schedule.
    """
    if type(evidence_origin) is not str or evidence_origin not in ('user-asserted', 'dsl-evaluated', 'host-computed'):
        raise ValueError('Unsupported trusted evidence origin')
    bars, config, source, strategy = _inputs(candles,settings,source,strategy_info)
    if not callable(decide): raise ValueError('전략 판단 함수가 필요합니다.')
    reason_prefix = {'user-asserted':'사용자 코드가 제공한 설명(독립 검증 안 됨): ', 'dsl-evaluated':'DSL 평가 근거: ', 'host-computed':''}[evidence_origin]
    initial=config['initialCash'];fee_rate=config['feeBps']/10000;slip=config['slippageBps']/10000;tax_rate=config['taxBps']/10000
    cash,shares,cost_basis,realized=initial,0,0.0,0.0
    peak,max_drawdown,total_fees,total_tax=initial,0.0,0.0,0.0
    trades,equity,decisions,orders=[],[],[],[]
    pending=None;no_fills=0
    bp=bars[0]['open']*(1+slip);bq=math.floor(initial/(bp*(1+fee_rate)))
    if bq*bp*(1+fee_rate)>initial: bq-=1
    bc=initial-bq*bp*(1+fee_rate)
    def portfolio(close):
        return {'cash':cash,'shares':shares,'equity':cash+shares*close,'averageCost':cost_basis/shares if shares else 0.0,
                'realizedPnl':realized,'unrealizedPnl':shares*close-cost_basis,'lastClose':close}
    for index,bar in enumerate(bars):
        if pending is not None:
            buying=pending['action']=='buy';base_price=bar['open'];eligible=True
            if pending['orderType']=='limit':
                limit=pending['limitPrice']
                eligible=bar['low']<=limit if buying else bar['high']>=limit
                base_price=min(bar['open'],limit) if buying else max(bar['open'],limit)
            price=base_price*(1+slip if buying else 1-slip)
            if pending['orderType']=='limit': price=min(price,pending['limitPrice']) if buying else max(price,pending['limitPrice'])
            event={**copy.deepcopy(pending),'date':bar['date'],'status':'expired'}
            if eligible:
                available=math.floor(cash/(price*(1+fee_rate))) if buying else shares
                if buying and available*price+available*price*fee_rate>cash: available-=1
                quantity=pending.get('quantity',available)
                if quantity>available:
                    if not config['rejectInsufficient']:
                        raise ValueError('명시한 주문 수량이 다음 봉 시가 기준 매수 가능 수량 또는 보유 수량을 초과했습니다.')
                    event.update(status='rejected',rejectionCode='INSUFFICIENT_CASH' if buying else 'INSUFFICIENT_POSITION')
                elif quantity>0:
                    gross=quantity*price;fee=gross*fee_rate;tax=0 if buying else gross*tax_rate;pnl=0.0
                    if buying:
                        cash-=gross+fee;cost_basis+=gross+fee;shares+=quantity
                    else:
                        removed_basis=cost_basis/shares*quantity
                        pnl=gross-fee-tax-removed_basis
                        cash+=gross-fee-tax;cost_basis-=removed_basis;shares-=quantity;realized+=pnl
                        if not shares: cost_basis=0.0
                    if abs(cash)<1e-9: cash=0.0
                    total_fees+=fee;total_tax+=tax
                    tid=f'trade-{len(trades)+1}'
                    event.update(status='filled',tradeId=tid,fillPrice=price,filledQuantity=quantity)
                    trades.append({'id':tid,'orderId':pending['id'],'date':bar['date'],'side':'BUY' if buying else 'SELL',
                        'quantity':quantity,'price':price,'fee':fee,'signalDate':pending['signalDate'],
                        'reason':reason_prefix+pending['reason'],
                        'orderPrice':pending.get('limitPrice',base_price),'orderType':pending['orderType'],'gross':gross,'tax':tax,
                        'cashAfter':cash,'sharesAfter':shares,'averageCostAfter':cost_basis/shares if shares else 0.0,
                        'realizedPnl':pnl,'cumulativeRealizedPnl':realized,'evidence':copy.deepcopy(pending.get('evidence'))})
                else:
                    no_fills+=1;event.update(status='rejected',rejectionCode='ZERO_CAPACITY')
            else: event['expirationReason']='DAY_LIMIT_NOT_REACHED'
            orders.append(event);pending=None
        state=portfolio(bar['close']);value=state['equity'];peak=max(peak,value)
        drawdown=(peak-value)/peak*100;max_drawdown=max(max_drawdown,drawdown)
        equity.append({'date':bar['date'],'equity':value,'benchmark':bc+bq*bar['close'],'drawdownPct':drawdown,
                       **{k:state[k] for k in ('cash','shares','averageCost','realizedPnl','unrealizedPnl')}})
        observation={'index':index,'date':bar['date'],'bar':dict(bar),'history':[dict(b) for b in bars[:index+1]],'portfolio':dict(state)}
        decision=_decision(decide(observation))
        # Only the trusted caller chooses origin. User JSON is always nested data;
        # an origin key inside it can never replace this host-authored outer tag.
        decision['evidence'] = {'origin':evidence_origin, 'details':decision.get('evidence')}
        decisions.append({'date':bar['date'],'index':index,**copy.deepcopy(decision)})
        if decision['action']!='hold': pending={**decision,'signalDate':bar['date'],'id':f'order-{len(orders)+1}'}
    if pending: orders.append({**copy.deepcopy(pending),'date':bars[-1]['date'],'status':'expired','expirationReason':'NO_NEXT_BAR'})
    stable_source={k:source[k] for k in _SOURCE_FIELDS if k in source and k not in ('fetchedAt','label')}
    canonical=json.dumps({'engine':ENGINE_VERSION,'candles':bars,'settings':config,'strategy':strategy,'source':stable_source,'decisions':decisions},sort_keys=True,separators=(',',':'),allow_nan=False)
    run_id=hashlib.sha256(canonical.encode()).hexdigest()[:24]
    warnings=[('합성 데이터 시연입니다. 고객 API 키 또는 실제 시세를 사용하지 않았습니다.' if source['kind']=='synthetic' else
               '사용자가 가져온 시세입니다. 출처·정확성·기업행동 반영은 독립 검증하지 않았습니다.' if source['kind']=='imported' else
               '고객 API 키로 수정주가를 요청한 과거 시세입니다. OHLC 수정 범위와 기업행사 반영의 완전성은 검증하지 않았습니다.' if source.get('adjustment')=='requested-adjusted' else
               '고객 API 키로 분할 보정 주가를 요청한 과거 시세입니다. OHLC 보정 범위와 기업행사 반영의 완전성은 검증하지 않았습니다.' if source.get('adjustment')=='requested-split-adjusted' else
               '고객 API 키로 조회한 과거 시세입니다. 수정주가 요청 여부가 기록되지 않아 기업행사 반영 여부를 확인할 수 없습니다.'),
              ('거래 설명과 evidence는 사용자 전략이 반환한 주장입니다. 호스트 회계와 구분하며 설명의 진위를 보증하지 않습니다.' if evidence_origin=='user-asserted' else
               '거래 근거는 DSL 평가기가 기록한 계산입니다. 투자 성과나 실제 체결을 보장하지 않습니다.' if evidence_origin=='dsl-evaluated' else
               '거래 근거는 내장 전략이 기록한 계산입니다. 투자 성과나 실제 체결을 보장하지 않습니다.'),
              '세금은 설정한 매도 bps 가정입니다. 법정 세율·세액 반올림·호가 단위·거래량 제한·휴장일·배당·분할 수량 조정은 재현하지 않습니다.',
              '지정가는 다음 한 봉만 유효합니다. OHLC 범위 도달 시 전량 체결하며 봉 안의 실제 경로·대기열을 재현하지 않습니다.',
              '비교선은 첫 봉 시가에 같은 비용으로 매수한 보유 전략입니다. 사용자 전략의 첫 주문은 다음 봉부터 체결됩니다.',
              '마지막 보유분은 종가로 평가하며 강제 청산하지 않습니다. 수익률에는 미실현 손익이 포함됩니다.']
    if pending: warnings.append('마지막 봉에서 발생한 주문은 다음 봉이 없어 체결하지 않았습니다.')
    if no_fills: warnings.append(f'수량을 생략한 주문 중 가용 현금 또는 보유 수량이 없어 {no_fills}건을 체결하지 않았습니다.')
    if len(trades)<20: warnings.append('체결 건수가 20건 미만입니다. 이 결과만으로 전략의 안정성을 판단할 수 없습니다.')
    return {'schemaVersion':1,'engineVersion':ENGINE_VERSION,'evidenceOrigin':evidence_origin,'runId':run_id,'source':source,'strategy':strategy,
            'assumptions':{**config,'fillRule':'봉 마감 판단 → 다음 봉 시가 ± 슬리피지 또는 다음 봉 DAY 지정가 · 정수 전량 체결 · 매도세금 bps'},
            'metrics':{'totalReturnPct':(equity[-1]['equity']/initial-1)*100,'maxDrawdownPct':max_drawdown,
                'benchmarkReturnPct':(equity[-1]['benchmark']/initial-1)*100,'tradeCount':len(trades),'finalEquity':equity[-1]['equity'],
                'totalFees':total_fees,'totalTax':total_tax,'realizedPnl':realized,'unrealizedPnl':state['unrealizedPnl']},
            'equity':equity,'trades':trades,'orders':orders,'decisions':decisions,'finalPortfolio':state,'warnings':warnings}
