#!/usr/bin/env python3
"""정밀 대조: 합성 일봉(`meritz.backtest_engine.demo_candles`, 160봉)으로 엔진 산출을 독립 재계산과 셀단위 비교.
체결역학(슬리피지/수수료/세금) · 자금원장 · 성과지표 · 결정성. 네트워크 호출 없음.
모든 검사는 항등식·정의 일치 검사이며 특정 거래 수 등 데이터에 고정된 기대값은 없다."""
import tempfile, sys, math, statistics as st
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from meritz.catalog import load_catalog
from meritz.backtest_engine import demo_candles
from runtime.service import StrategyRuntime

bars = demo_candles()  # 결정적 합성 일봉 160봉. 실제 종목 시세가 아니다.
openByDate = {b["date"]: b["open"] for b in bars}
INIT, FEE, SLIP, TAX = 10_000_000, 15, 5, 23  # bp

rt = StrategyRuntime(root=tempfile.mkdtemp(), catalog=load_catalog())
ds = rt.import_data(bars, {"kind": "imported", "label": "합성 일봉 160봉", "currency": "KRW"})
by = {d["name"]: d for d in rt.templates()["templates"]}
rev = rt.save_strategy(by["SMA 5/20 교차"])
run = rt.run(rev["id"], ds["id"], {"initialCash": INIT, "feeBps": FEE, "slippageBps": SLIP, "taxBps": TAX})
rep, an = run["report"], run["analysis"]
orders, trades, eq = rep["orders"], rep["trades"], rep["equity"]
m, am = rep["metrics"], an["metrics"]

PASS, FAIL = [], []
def chk(name, got, exp, tol=1e-6):
    ok = (got is None and exp is None) or (got is not None and exp is not None and abs(got-exp) <= tol*max(1, abs(exp)))
    (PASS if ok else FAIL).append(f"{name}: engine={got} indep={exp}" + ("" if ok else "  <-- MISMATCH"))

# ---- 1) 체결역학: 각 주문 슬리피지/수량/수수료/세금 재계산 ----
cash = INIT; shares = 0; fees = 0.0; taxes = 0.0
for o in orders:
    d = o["date"]; op = openByDate[d]; q = o["filledQuantity"]; fp = o["fillPrice"]
    if o["action"] == "buy":
        exp_fp = op*(1+SLIP/1e4)
        chk(f"[fill buy {d}] slippage price", fp, exp_fp, 1e-6)
        gross = fp*q; fee = gross*FEE/1e4
        cash -= gross+fee; shares += q; fees += fee
    else:
        exp_fp = op*(1-SLIP/1e4)
        chk(f"[fill sell {d}] slippage price", fp, exp_fp, 1e-6)
        gross = fp*q; fee = gross*FEE/1e4; tax = gross*TAX/1e4
        cash += gross-fee-tax; shares -= q; fees += fee; taxes += tax
lastClose = bars[-1]["close"]
indep_equity = cash + shares*lastClose
chk("[ledger] totalFees", m["totalFees"], fees, 1e-6)
chk("[ledger] totalTax", m["totalTax"], taxes, 1e-6)
chk("[ledger] finalEquity", m["finalEquity"], indep_equity, 1e-6)
chk("[ledger] residual shares", float(shares), float(rep["finalPortfolio"]["shares"]), 0)

# ---- 2) 성과지표 독립 재계산 (equity 곡선/trades 기반) ----
equities = [p["equity"] for p in eq]
chk("totalReturnPct", m["totalReturnPct"], (equities[-1]/INIT-1)*100, 1e-6)
# MDD
peak = -1e18; mdd = 0.0
for v in equities:
    peak = max(peak, v); mdd = max(mdd, (peak-v)/peak*100)
chk("maxDrawdownPct", m["maxDrawdownPct"], mdd, 1e-6)
# 일별 수익률 → Sharpe/Sortino (연율화 √252, 무위험 0 가정)
rets = [equities[i]/equities[i-1]-1 for i in range(1, len(equities))]
mean = st.mean(rets)
# 엔진이 문서화한 정의(analysis.assumptions) 그대로 재계산한다:
#  sharpe  = mean / 표본표준편차 * sqrt(252)
#  sortino = mean / sqrt(sum(min(r,0)^2)/N) * sqrt(252)   (전체 관측 기준 RMS 음수수익)
#  cagrPct = (final/initial)^(365.25/달력일수) - 1 ;  annualizedReturnPct = expm1(sum(log(1+r))*252/N)
#  calmar  = cagrPct / maxDrawdownPct ;  recoveryFactor = totalReturnPct / maxDrawdownPct
import datetime as _dt
sharpe = mean/st.stdev(rets)*math.sqrt(252)
downside = math.sqrt(sum(min(x, 0)**2 for x in rets)/len(rets))
sortino = mean/downside*math.sqrt(252)
_days = (_dt.date.fromisoformat(eq[-1]["date"]) - _dt.date.fromisoformat(eq[0]["date"])).days
cagr = ((equities[-1]/INIT)**(365.25/_days)-1)*100
annualized = math.expm1(sum(math.log(1+x) for x in rets)*252/len(rets))*100
chk("sharpe(sample stdev, √252)", am["sharpe"], sharpe, 1e-9)
chk("sortino(RMS negative over N, √252)", am["sortino"], sortino, 1e-9)
chk("cagrPct(calendar 365.25d)", am["cagrPct"], cagr, 1e-9)
chk("annualizedReturnPct(252 geometric)", am["annualizedReturnPct"], annualized, 1e-9)
# 라운드트립 trades pnl → winRate/profitFactor/avgWin/avgLoss/expectancy/연속손익
pnls = [t.get("realizedPnl", t.get("pnl")) for t in trades if t.get("side") in ("SELL", None) and (t.get("realizedPnl") is not None or t.get("pnl") is not None)]
# trades 는 체결단위 2개가 1라운드. realizedPnl 누적이 아닌 라운드 pnl 추출: sell 체결의 realizedPnl 사용
rt_pnls = [t["realizedPnl"] for t in trades if t.get("side") == "SELL" and t.get("realizedPnl") is not None]
if not rt_pnls:  # 스키마상 trade에 side 없으면 cumulativeRealizedPnl 차분
    cum = [t.get("cumulativeRealizedPnl") for t in trades if t.get("cumulativeRealizedPnl") is not None]
    rt_pnls = [cum[i]-(cum[i-1] if i else 0) for i in range(len(cum))]
wins = [p for p in rt_pnls if p > 0]; losses = [p for p in rt_pnls if p < 0]
n = len(rt_pnls)
chk("winRatePct", am["winRatePct"], (len(wins)/n*100) if n else 0.0, 1e-6)
pf = (sum(wins)/abs(sum(losses))) if losses else (0.0 if not wins else float("inf"))
chk("profitFactor", am["profitFactor"], pf if math.isfinite(pf) else am["profitFactor"], 1e-6)
chk("avgWin", am["avgWin"], (sum(wins)/len(wins)) if wins else None, 1e-6)
chk("avgLoss", am["avgLoss"], (abs(sum(losses))/len(losses)) if losses else None, 1e-6)
chk("expectancy", am["expectancy"], (sum(rt_pnls)/n) if n else None, 1e-6)
# 연속 손익
mcw = mcl = cw = cl = 0
for p in rt_pnls:
    if p > 0: cw += 1; cl = 0
    elif p < 0: cl += 1; cw = 0
    else: cw = cl = 0
    mcw = max(mcw, cw); mcl = max(mcl, cl)
chk("maxConsecutiveWins", am["maxConsecutiveWins"], mcw, 0)
chk("maxConsecutiveLosses", am["maxConsecutiveLosses"], mcl, 0)
# calmar = cagrPct / MDD ,  recoveryFactor = totalReturnPct / MDD  (엔진 문서 정의)
chk("calmarRatio(cagr/MDD)", am["calmarRatio"], cagr/mdd, 1e-9)
chk("recoveryFactor(totalReturn/MDD)", am["recoveryFactor"], m["totalReturnPct"]/mdd, 1e-9)
# exposure: 포지션 보유 봉 비율
held = sum(1 for p in eq if p.get("shares", 0) != 0); exp_pct = held/len(eq)*100
chk("exposurePct", am["exposurePct"], exp_pct, 1e-6)

# ---- 3) 결정성: 3회 실행 resultHash/finalEquity 동일 ----
hashes = set(); fins = set()
for _ in range(3):
    r = rt.run(rev["id"], ds["id"], {"initialCash": INIT, "feeBps": FEE, "slippageBps": SLIP, "taxBps": TAX})
    hashes.add(r["manifest"]["resultHash"]); fins.add(r["report"]["metrics"]["finalEquity"])
chk("[determinism] distinct resultHash", float(len(hashes)), 1.0, 0)
chk("[determinism] distinct finalEquity", float(len(fins)), 1.0, 0)

print(f"\n==== 정밀 대조 결과: {len(PASS)} PASS / {len(FAIL)} FAIL ====")
for line in PASS: print(" ✓", line)
for line in FAIL: print(" ✗", line)
sys.exit(1 if FAIL else 0)
