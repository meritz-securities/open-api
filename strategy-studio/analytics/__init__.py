"""Deterministic descriptive analytics; no strategy reasons are trusted evidence."""
from __future__ import annotations
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import math
import statistics


def _verify_fill_model(report, bars, settings):
    """Independent next-bar execution oracle using immutable input candles/settings."""
    result={'status':'unsupported','mismatches':[],'missing':[],'expectedFills':{},'lastClose':None,'initialCash':None}
    def numeric(v):
        if type(v) not in (int,float):raise ValueError
        d=Decimal(str(v))
        if not d.is_finite():raise ValueError
        return d
    def mismatch(tid,field,expected,actual):
        result['mismatches'].append({'tradeId':tid,'field':field,'expected':float(expected) if isinstance(expected,Decimal) else expected,'actual':actual})
    def compare(tid,field,expected,actual):
        try:
            d=numeric(actual)
            if abs(d-expected)>max(Decimal('0.000001'),abs(expected)*Decimal('1e-10')):mismatch(tid,field,expected,actual)
        except (ValueError,InvalidOperation):mismatch(tid,field,expected,None)
    try:
        if not isinstance(bars,list) or len(bars)<2 or not isinstance(settings,dict):raise ValueError
        with localcontext() as context:
            context.prec=50
            initial=numeric(settings['initialCash']);fee=numeric(settings['feeBps'])/10000
            slip=numeric(settings['slippageBps'])/10000;tax=numeric(settings['taxBps'])/10000
            if initial<=0 or any(not Decimal(0)<=v<=Decimal('.1') for v in (fee,slip,tax)):raise ValueError
            dates=[b['date'] for b in bars]
            if dates!=sorted(set(dates)):raise ValueError
            for bar in bars:
                o,h,l,c=[numeric(bar[k]) for k in ('open','high','low','close')]
                if min(o,h,l,c)<=0 or l>min(o,c) or h<max(o,c):raise ValueError
            result['lastClose']=bars[-1]['close'];result['initialCash']=settings['initialCash']
            for key in ('initialCash','feeBps','slippageBps','taxBps'):
                compare(None,'assumptions.'+key,numeric(settings[key]),report.get('assumptions',{}).get(key))
            by_date={d:i for i,d in enumerate(dates)}
            orders=report.get('orders',[])
            if not isinstance(orders,list):raise ValueError
            trades=report.get('trades',[])
            for order in orders:
                if order.get('status')=='filled' and sum(t.get('id')==order.get('tradeId') and t.get('orderId')==order.get('id') for t in trades)!=1:
                    mismatch(order.get('tradeId'),'filled order linkage','exactly one matching trade','missing or duplicate')
            for trade in trades:
                tid=trade.get('id');matches=[o for o in orders if o.get('id')==trade.get('orderId') and o.get('tradeId')==tid]
                if len(matches)!=1:
                    result['missing'].append({'tradeId':tid,'field':'matched filled order'});continue
                order=matches[0];signal=order.get('signalDate');index=by_date.get(signal)
                if index is None or index+1>=len(bars):
                    mismatch(tid,'signalDate','raw candle with following bar',signal);continue
                bar=bars[index+1];side=trade.get('side');buy=side=='BUY'
                if side not in ('BUY','SELL'):
                    mismatch(tid,'side','BUY or SELL',side);continue
                for field,actual,expected in [('signalDate',trade.get('signalDate'),signal),('date',trade.get('date'),bar['date']),('order.date',order.get('date'),bar['date']),('order.status',order.get('status'),'filled'),('order.action',order.get('action'),'buy' if buy else 'sell')]:
                    if actual!=expected:mismatch(tid,field,expected,actual)
                o=numeric(bar['open']);order_type=order.get('orderType','market')
                base=o
                if order_type=='limit':
                    limit=numeric(order.get('limitPrice'))
                    if limit<=0:raise ValueError
                    if (buy and numeric(bar['low'])>limit) or (not buy and numeric(bar['high'])<limit):
                        mismatch(tid,'eligibility','limit reached in next raw bar','not reached')
                    base=min(o,limit) if buy else max(o,limit)
                elif order_type!='market':
                    result['missing'].append({'tradeId':tid,'field':'supported orderType'});continue
                price=base*(1+slip if buy else 1-slip)
                if order_type=='limit':price=min(price,limit) if buy else max(price,limit)
                qty=numeric(trade.get('quantity'))
                if qty<=0 or qty!=qty.to_integral_value():raise ValueError
                gross=qty*price;expected_fee=gross*fee;expected_tax=Decimal(0) if buy else gross*tax
                for field,expected in [('price',price),('fee',expected_fee),('tax',expected_tax)]:compare(tid,field,expected,trade.get(field))
                if 'gross' in trade:compare(tid,'gross',gross,trade['gross'])
                if 'fillPrice' in order:compare(tid,'order.fillPrice',price,order['fillPrice'])
                if 'filledQuantity' in order:compare(tid,'order.filledQuantity',qty,order['filledQuantity'])
                if 'quantity' in order:compare(tid,'order.quantity',qty,order['quantity'])
                result['expectedFills'][tid]={'price':price,'fee':expected_fee,'tax':expected_tax}
            result['status']='triggered' if result['mismatches'] else ('unsupported' if result['missing'] else 'clear')
            return result
    except (ValueError,InvalidOperation,KeyError,TypeError):
        result['missing'].append({'field':'raw bars/settings/order provenance','reason':'absent or invalid independent execution inputs'})
        result['status']='triggered' if result['mismatches'] else 'unsupported'
        return result


def _reconcile(report, fill_model=None):
    """Independent Decimal ledger from fill primitives; never trust post-fill totals."""
    mismatches=[]; missing=[]; sell_results=[]; post_cash=[]; checked=0
    def number(value):
        if type(value) not in (int,float): raise ValueError('numeric field required')
        result=Decimal(str(value))
        if not result.is_finite():raise ValueError('finite number required')
        return result
    def compare(obj,key,expected,trade_id=None):
        nonlocal checked
        if key not in obj:
            missing.append({'tradeId':trade_id,'field':key});return
        try:actual=number(obj[key])
        except (ValueError,InvalidOperation):
            mismatches.append({'tradeId':trade_id,'field':key,'reason':'invalid numeric value'});return
        checked+=1
        tolerance=Decimal(0) if key in ('shares','sharesAfter') else max(Decimal('0.000001'),abs(expected)*Decimal('0.0000000001'))
        if abs(actual-expected)>tolerance:
            mismatches.append({'tradeId':trade_id,'field':key,'expected':float(expected),'actual':float(actual)})
    try:
        with localcontext() as context:
            context.prec=50
            cash=number(fill_model['initialCash'] if fill_model and fill_model.get('initialCash') is not None else report.get('assumptions',{}).get('initialCash'))
            if cash<=0:raise ValueError('initial cash must be positive')
            initial=cash;shares=Decimal(0);basis=Decimal(0);realized=Decimal(0)
            for trade in report.get('trades',[]):
                tid=trade.get('id');side=trade.get('side')
                try:
                    qty=number(trade.get('quantity'));price=number(trade.get('price'))
                    fee=number(trade.get('fee'));tax=number(trade.get('tax'))
                    if qty<=0 or qty!=qty.to_integral_value() or price<=0 or fee<0 or tax<0 or side not in ('BUY','SELL'):
                        raise ValueError('invalid fill primitive')
                except (ValueError,InvalidOperation):
                    missing.append({'tradeId':tid,'field':'fill primitives','reason':'quantity/price/fee/tax/side missing or invalid'})
                    return {'status':'triggered' if mismatches else 'insufficient_data','mismatches':mismatches,'missing':missing,'checkedFields':checked,'sellResults':[],'postCash':[]}
                expected=fill_model.get('expectedFills',{}).get(tid) if fill_model else None
                if expected:price,fee,tax=expected['price'],expected['fee'],expected['tax']
                gross=qty*price;pnl=Decimal(0)
                if side=='BUY':
                    cash-=gross+fee+tax;basis+=gross+fee+tax;shares+=qty
                else:
                    if qty>shares:
                        mismatches.append({'tradeId':tid,'field':'quantity','reason':'sell exceeds independently reconstructed holdings'})
                        return {'status':'triggered','mismatches':mismatches,'missing':missing,'checkedFields':checked,'sellResults':[],'postCash':[]}
                    removed=basis*qty/shares;pnl=gross-fee-tax-removed
                    cash+=gross-fee-tax;basis-=removed;shares-=qty;realized+=pnl
                    if not shares:basis=Decimal(0)
                    sell_results.append({'tradeId':tid,'realizedPnl':float(pnl)})
                if cash < -max(Decimal('0.000001'),initial*Decimal('0.0000000001')):
                    mismatches.append({'tradeId':tid,'field':'cash','reason':'negative independently reconstructed cash','expected':float(cash)})
                for key,value in [('cashAfter',cash),('sharesAfter',shares),('averageCostAfter',basis/shares if shares else Decimal(0)),('realizedPnl',pnl)]:
                    compare(trade,key,value,tid)
                post_cash.append({'tradeId':tid,'cash':float(cash)})
            final=report.get('finalPortfolio',{})
            for key,value in [('cash',cash),('shares',shares),('averageCost',basis/shares if shares else Decimal(0)),('realizedPnl',realized)]:compare(final,key,value)
            # Never derive a closing price from reported equity/unrealized PnL:
            # doing so would make the reconciliation circular.
            close=fill_model['lastClose'] if fill_model and fill_model.get('lastClose') is not None else final.get('lastClose')
            if fill_model and fill_model.get('lastClose') is not None and 'lastClose' in final:compare(final,'lastClose',number(close))
            if not shares or close is not None:
                marked=cash+shares*number(close) if shares else cash
                compare(final,'equity',marked)
                curve=report.get('equity',[])
                if curve:compare(curve[-1],'equity',marked,'final-equity')
                else:missing.append({'tradeId':None,'field':'final equity curve'})
            else:missing.append({'tradeId':None,'field':'lastClose','reason':'independent final valuation requires actual closing price'})
            return {'status':'triggered' if mismatches else ('insufficient_data' if missing else 'clear'),'mismatches':mismatches,'missing':missing,'checkedFields':checked,'sellResults':sell_results,'postCash':post_cash}
    except (ValueError,InvalidOperation):
        return {'status':'triggered' if mismatches else 'insufficient_data','mismatches':mismatches,'missing':missing+[{'field':'initial cash or closing price','reason':'missing or invalid'}],'checkedFields':checked,'sellResults':[],'postCash':[]}


def analyze(report, manifest=None, bars=None, settings=None):
    equity=report.get('equity',[]); trades=report.get('trades',[])
    initial=report.get('assumptions',{}).get('initialCash')
    metrics={}; reasons={}
    def put(key,value,reason='undefined denominator or insufficient observations'):
        if value is None or not math.isfinite(value): metrics[key]=None; reasons[key]=reason
        else: metrics[key]=value
    values=[x['equity'] for x in equity]
    returns=[b/a-1 for a,b in zip(values,values[1:]) if a>0]
    days=(date.fromisoformat(equity[-1]['date'])-date.fromisoformat(equity[0]['date'])).days if len(equity)>1 else 0
    # CAGR uses first observed valuation, matching the elapsed interval exactly.
    growth=None
    if days>0 and values[0]>0 and values[-1]>0:
        try: growth=math.expm1(math.log(values[-1]/values[0])*365.25/days)*100
        except OverflowError: pass
    put('cagrPct',growth,'requires positive first/final equity and positive calendar duration')
    mean=statistics.mean(returns) if returns else 0
    std=statistics.stdev(returns) if len(returns)>1 else 0
    put('sharpe',mean/std*math.sqrt(252) if std else None,'requires at least two returns with nonzero sample volatility')
    downside=math.sqrt(sum(min(x,0)**2 for x in returns)/len(returns)) if returns else 0
    put('sortino',mean/downside*math.sqrt(252) if len(returns)>1 and downside else None,'requires at least two returns and nonzero downside deviation')
    peak=initial if isinstance(initial,(int,float)) and initial>0 else (values[0] if values else 0)
    mdd=0
    for value in values:
        peak=max(peak,value)
        if peak>0:mdd=max(mdd,(peak-value)/peak*100)
    put('maxDrawdownPct',mdd if values else None,'no equity observations')
    sells=[t for t in trades if t.get('side')=='SELL']
    pnl=[t['realizedPnl'] for t in sells if type(t.get('realizedPnl')) in (int,float)]
    complete=len(pnl)==len(sells) and bool(sells)
    gains=sum(max(x,0) for x in pnl); losses=-sum(min(x,0) for x in pnl)
    put('winRatePct',sum(x>0 for x in pnl)/len(pnl)*100 if complete else None,'requires realized PnL for all sell fills; unit is sell fill, not round trip')
    put('profitFactor',gains/losses if complete and losses else None,'no realized losses or incomplete realized PnL; Infinity is not emitted')
    final=report.get('finalPortfolio',{})
    put('realizedPnl',final.get('realizedPnl',sum(pnl) if complete else None),'host realized PnL unavailable')
    put('unrealizedPnl',final.get('unrealizedPnl'),'host unrealized PnL unavailable')
    monthly=[]; previous=initial
    for row in equity:
        month=row['date'][:7]
        if not monthly or monthly[-1]['month']!=month:
            monthly.append({'month':month,'startEquity':previous,'endEquity':row['equity']})
        else:monthly[-1]['endEquity']=row['equity']
        previous=row['equity']
    for row in monthly:
        base=row.pop('startEquity'); end=row.pop('endEquity')
        row['returnPct']=(end/base-1)*100 if base and base>0 else None
        if row['returnPct'] is None:row['reason']='initial equity unavailable or nonpositive'
    # --- Additional descriptive metrics (doctor-v1, additive). All unit-per-sell-fill
    # metrics reuse the same reported realized-PnL list and `complete` gate as
    # winRatePct/profitFactor so their semantics stay consistent. Breakeven (0) is
    # neither a win nor a loss, matching the existing winRateUnit assumption. ---
    wins=[x for x in pnl if x>0]; loss_vals=[x for x in pnl if x<0]
    avg_win=gains/len(wins) if complete and wins else None
    avg_loss=losses/len(loss_vals) if complete and loss_vals else None
    put('avgWin',avg_win,'requires at least one winning sell fill with complete realized PnL; currency per winning sell fill')
    put('avgLoss',avg_loss,'requires at least one losing sell fill with complete realized PnL; positive magnitude per losing sell fill')
    put('winLossRatio',avg_win/avg_loss if avg_win is not None and avg_loss else None,'requires at least one winning and one losing sell fill; average win over average loss magnitude')
    put('expectancy',sum(pnl)/len(pnl) if complete else None,'requires complete realized PnL for all sell fills; mean realized PnL per sell fill in currency')
    def _streak(seq,predicate):
        best=current=0
        for item in seq:
            current=current+1 if predicate(item) else 0
            if current>best:best=current
        return best
    put('maxConsecutiveWins',_streak(pnl,lambda x:x>0) if complete else None,'requires complete realized PnL for all sell fills; longest run of winning sell fills, breakeven breaks the run')
    put('maxConsecutiveLosses',_streak(pnl,lambda x:x<0) if complete else None,'requires complete realized PnL for all sell fills; longest run of losing sell fills, breakeven breaks the run')
    cagr_val=metrics['cagrPct']; mdd_val=metrics['maxDrawdownPct']
    put('calmarRatio',cagr_val/mdd_val if cagr_val is not None and mdd_val else None,'requires defined CAGR and nonzero max drawdown; CAGR percent divided by max drawdown percent')
    net_base=initial if isinstance(initial,(int,float)) and initial>0 else (values[0] if values else None)
    net_return_pct=(values[-1]/net_base-1)*100 if net_base and net_base>0 and values else None
    put('recoveryFactor',net_return_pct/mdd_val if net_return_pct is not None and mdd_val else None,'requires equity observations and nonzero max drawdown; net return percent divided by max drawdown percent')
    annualized=None
    if returns and all(1+r>0 for r in returns):
        try: annualized=math.expm1(sum(math.log(1+r) for r in returns)*252/len(returns))*100
        except (ValueError,OverflowError): annualized=None
    put('annualizedReturnPct',annualized,'geometric per-bar return compounded at 252 bars/year; distinct from cagrPct which annualizes by calendar days')
    exposure=None
    if equity:
        try:
            net_by_date={}
            for trade in trades:
                day=trade['date']; qty=trade['quantity']; side=trade['side']
                if side not in ('BUY','SELL') or type(qty) not in (int,float): raise ValueError
                net_by_date[day]=net_by_date.get(day,0)+(qty if side=='BUY' else -qty)
            held_shares=0; in_market=0
            for row in equity:
                held_shares+=net_by_date.get(row['date'],0)
                if held_shares>1e-9: in_market+=1
            exposure=in_market/len(equity)*100
        except (KeyError,ValueError,TypeError): exposure=None
    put('exposurePct',exposure,'requires equity observations and every trade to carry date/side/numeric quantity; percent of equity bars holding a nonzero reconstructed position')
    priced_months=[row for row in monthly if row.get('returnPct') is not None]
    best_month=max(priced_months,key=lambda row:row['returnPct']) if priced_months else None
    worst_month=min(priced_months,key=lambda row:row['returnPct']) if priced_months else None
    put('bestMonthReturnPct',best_month['returnPct'] if best_month else None,'requires at least one month with computable return; largest monthly return percent (label in monthlyExtremes)')
    put('worstMonthReturnPct',worst_month['returnPct'] if worst_month else None,'requires at least one month with computable return; smallest monthly return percent (label in monthlyExtremes)')
    monthly_extremes={'best':{'month':best_month['month'],'returnPct':best_month['returnPct']} if best_month else None,'worst':{'month':worst_month['month'],'returnPct':worst_month['returnPct']} if worst_month else None}
    diagnostics=[]
    def diagnostic(key,status,title,message,evidence,threshold=None,severity='warning'):
        diagnostics.append({'id':key,'severity':severity,'status':status,'title':title,'message':message,'evidence':evidence,'threshold':threshold})
    diagnostic('sample-size','triggered' if len(sells)<20 else 'clear','실현 표본 수','매도 체결 수는 독립 전략 표본 수를 보장하지 않습니다.',{'sellFillCount':len(sells),'tradeIds':[t['id'] for t in sells]},20)
    diagnostic('drawdown','triggered' if mdd>=20 else ('clear' if values else 'insufficient_data'),'최대 낙폭','초기 자금과 관측 종가 평가액으로 계산합니다.',{'maxDrawdownPct':metrics['maxDrawdownPct']},20)
    fee=sum(t.get('fee',0)+t.get('tax',0) for t in trades)
    drag=fee/initial*100 if initial and initial>0 else None
    diagnostic('cost-burden','insufficient_data' if drag is None else ('triggered' if drag>=2 else 'clear'),'거래 비용 부담','초기 자금 대비 실제 모의 체결 비용입니다. 비용이 없는 전략의 성과 차이를 뜻하지 않습니다.',{'feeAndTax':fee,'initialCapitalPct':drag,'tradeIds':[t['id'] for t in trades]},2)
    fill_model=_verify_fill_model(report,bars,settings)
    ledger=_reconcile(report,fill_model)
    if fill_model['status']=='triggered':ledger['status']='triggered'
    elif ledger['status']=='clear' and fill_model['status']!='clear':ledger['status']='unsupported'
    ledger['mismatches'].extend(fill_model['mismatches'])
    diagnostic('fill-model-reconciliation',fill_model['status'],'원본 시세 기반 체결 대조','실제로 체결된 주문의 가격·비용·체결일을 불변 원본 OHLC·설정과 대조합니다. 미체결·거부 판단이나 주문 수량의 최적성은 검증하지 않습니다.',{k:v for k,v in fill_model.items() if k not in ('status','expectedFills')},severity='error' if fill_model['status']=='triggered' else 'info')
    diagnostic('accounting-reconciliation',ledger['status'],'독립 회계 대조','원본 시세·설정에서 검증한 체결값으로 Decimal 원장을 재계산합니다. 원본 입력이 없으면 전체 회계 검증을 통과로 표시하지 않습니다.',{k:v for k,v in ledger.items() if k!='status'},{'absoluteTolerance':1e-6,'relativeTolerance':1e-10},severity='error' if ledger['status']=='triggered' else 'info')
    tax_bps=report.get('assumptions',{}).get('taxBps')
    tax_status='insufficient_data' if tax_bps is None else ('triggered' if sells and tax_bps==0 else 'clear')
    diagnostic('zero-sell-tax',tax_status,'매도 세금 0 설정','매도 체결이 있는데 사용자 세금 설정이 0이면 비용 가정을 확인하세요. 특정 시장의 법정 세율을 판단한 결과가 아닙니다.',{'taxBps':tax_bps,'sellFillCount':len(sells),'tradeIds':[t['id'] for t in sells]},0)
    independent_sells=ledger['sellResults']
    streak=longest=0;streak_ids=[];longest_ids=[]
    for item in independent_sells:
        if item['realizedPnl']<0:
            streak+=1;streak_ids.append(item['tradeId'])
            if streak>longest:longest=streak;longest_ids=list(streak_ids)
        else:streak=0;streak_ids=[]
    usable=len(independent_sells)==len(sells) and bool(sells)
    diagnostic('consecutive-losses','insufficient_data' if not usable else ('triggered' if longest>=3 else 'clear'),'연속 손실 매도','독립 재계산한 매도 체결 손익 기준이며 왕복 거래 수와 다릅니다.',{'maxConsecutiveLosses':longest if usable else None,'tradeIds':longest_ids},3)
    positive=[x for x in independent_sells if x['realizedPnl']>0]
    largest=max(positive,key=lambda x:x['realizedPnl']) if positive else None
    concentration=largest['realizedPnl']/sum(x['realizedPnl'] for x in positive)*100 if largest and usable else None
    diagnostic('profitable-sell-concentration','insufficient_data' if concentration is None else ('triggered' if concentration>=50 else 'clear'),'수익 매도 집중','총 양의 매도 실현손익에서 최대 한 건의 비중입니다. 포트폴리오 분산 위험을 뜻하지 않습니다.',{'largestProfitSharePct':concentration,'tradeIds':[largest['tradeId']] if largest else []},50)
    balances=ledger['postCash'];min_cash=min(balances,key=lambda x:x['cash']) if balances else None
    cash_pct=min_cash['cash']/initial*100 if min_cash and initial and initial>0 else None
    diagnostic('cash-depletion','insufficient_data' if cash_pct is None else ('triggered' if cash_pct<=1 else 'clear'),'체결 후 현금 여유','체결 원장으로 재계산한 최소 현금입니다. 현금이 적다는 이유만으로 전략 실패를 뜻하지 않습니다.',{'minimumCashPct':cash_pct,'tradeIds':[min_cash['tradeId']] if min_cash else []},1)
    diagnostic('corporate-actions','unsupported','기업행동','배당·분할 현금 및 보유 수량 조정은 모델링하지 않습니다.',{'adjustment':report.get('source',{}).get('adjustment')},severity='info')
    diagnostic('portfolio-concentration','unsupported','분산 위험','단일종목 결과에서 다종목 상관관계와 포트폴리오 분산 효과를 추정하지 않습니다.',{},severity='info')
    diagnostic('code-causality','unsupported','전략 설명의 검증 범위','사용자 reason 문자열은 계산 증거가 아닙니다. 호스트 체결과 DSL 노드 기록만 별도 확인할 수 있습니다.',{},severity='info')
    result={'metrics':metrics,'metricReasons':reasons,'monthlyReturns':monthly,'monthlyExtremes':monthly_extremes,'diagnostics':diagnostics,'assumptions':{'tradingDaysPerYear':252,'riskFreeRate':0,'cagr':'calendar 365.25 days; first observed equity to final observed equity','volatility':'sample standard deviation','downside':'root mean squared negative returns over all observations','winRateUnit':'sell fill; breakeven is not win','avgWinLossUnit':'currency per winning/losing sell fill; avgLoss reported as positive magnitude','winLossRatio':'avgWin divided by avgLoss magnitude','expectancyUnit':'currency; mean realized PnL per sell fill','consecutiveStreaksUnit':'reported sell-fill realized PnL; breakeven breaks a streak','calmar':'cagrPct divided by maxDrawdownPct','recoveryFactor':'net return percent (final vs initial equity) divided by maxDrawdownPct','annualizedReturn':'geometric per-bar return compounded to 252 bars/year; distinct from calendar cagrPct','exposure':'percent of equity bars with a nonzero reconstructed position','monthlyExtremes':'best/worst monthlyReturns entry by returnPct; labels alongside bestMonthReturnPct/worstMonthReturnPct','ruleVersion':'doctor-v1'}}
    return result
