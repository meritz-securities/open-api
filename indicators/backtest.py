"""지표 발생 집계 — 과거 구간에서 각 지표가 어떤 상태였는지 센다.

캔들 시계열을 하루씩 늘려가며 각 지표의 evaluate() 를 호출하고, 나온 상태를
집계한다.

    python -m indicators.backtest                 # 전체 · 데모 데이터
    python -m indicators.backtest golden_cross    # 지정 지표만
    python -m indicators.backtest all 005930      # 전체 · 실데이터

**실데이터로는 집계가 거의 안 됩니다.** 일봉 API 는 연속조회를 지원하지 않아
한 번에 받을 수 있는 봉이 전부다(삼성전자 기준 40봉). MACD 하나만 해도 35봉을
쓰므로 집계할 구간이 남지 않는다. 빈도 집계는 데모 데이터로 보고, 실데이터로는
개별 지표의 현재 상태(`python -m indicators.macd 005930`)를 보는 것이 맞다.

**사건형과 상태형을 섞어 세지 않는다.** 골든크로스와 MACD 교차는 교차가 일어난
그 봉에서만 참이므로 **몇 번 일어났나**를 센다. RSI·볼린저·스토캐스틱 등은 조건이
충족된 동안 계속 참이므로 **몇 봉 머물렀나**를 센다. 둘을 한 열에 놓으면
"수급이 골든크로스보다 20배 자주 발생"처럼 단위가 다른 것을 비교하게 된다.

**수익률을 계산하지 않는다.** 지표가 언제 얼마나 걸렸는지만 센다. 매매 성과를
추정하려면 수수료·세금·슬리피지·체결 지연·호가 단위·유동성을 모두 반영해야 하고,
그것은 이 예제의 범위가 아니다.

⚠️ 투자자문 아님. (README의 고지 참조)
"""
from __future__ import annotations

import importlib
import sys
from collections import Counter

from indicators.candles import load_candles, sample_candles
from indicators.notice import NOTICE

# 사건형 — 교차가 일어난 봉에서만 참이다. 발생 "횟수"를 센다.
EVENT = ("golden_cross", "macd")

# 상태형 — 조건이 유지되는 동안 계속 참이다. 머문 "봉 수"를 센다.
STATE = ("rsi_reversal", "bollinger", "stochastic", "price_channel", "obv", "atr", "supply_demand")

INDICATORS = EVENT + STATE

# 집계표에서 빼는 상태. 데이터가 모자라 판정을 못 한 것과, 아무 일도
# 없었다는 뜻의 중립값이다. 이걸 세면 표가 중립값으로만 가득 찬다.
_SKIP = {"NO_SIGNAL", "MIXED", "NEUTRAL", "INSIDE", "VOLATILITY_NORMAL"}


WARMUP = 40      # 지표 계산에 쓰고 버리는 앞 구간. MACD(26+9)가 가장 길다.
MIN_BARS = 10    # 이보다 짧게 남으면 집계에 의미가 없다.


def run(indicator: str, candles: list[dict], warmup: int = WARMUP) -> dict:
    """봉을 하나씩 늘려가며 evaluate() 를 호출하고 상태를 센다."""
    mod = importlib.import_module(f"indicators.{indicator}")
    counts: Counter = Counter()
    at: list[int] = []          # 중립이 아닌 상태가 나온 봉 번호 — 간격 계산용
    for i in range(warmup, len(candles)):
        state = mod.evaluate(candles[: i + 1]).get("state")
        counts[state] += 1
        if state not in _SKIP:
            at.append(i)
    gaps = [b - a for a, b in zip(at, at[1:])]
    return {"counts": counts, "bars": max(0, len(candles) - warmup),
            "hits": len(at), "mean_gap": sum(gaps) / len(gaps) if gaps else None}


def _fmt(r: dict, unit: str) -> list[str]:
    """중립이 아닌 상태를 많은 순으로 'STATE 12회' 형태로 편다."""
    return [f"{s} {n}{unit}" for s, n in r["counts"].most_common() if s not in _SKIP] or ["-"]


def main(argv: list[str]) -> None:
    print(NOTICE)
    pick = argv[1] if len(argv) > 1 and argv[1] in INDICATORS else None
    iscd = argv[2] if len(argv) > 2 else (argv[1] if len(argv) > 1 and not pick else None)
    candles = load_candles(iscd) if iscd else sample_candles()
    src = f"실데이터 {iscd} ({len(candles)}봉)" if iscd else f"데모 합성데이터 ({len(candles)}봉)"

    if len(candles) - WARMUP < MIN_BARS:
        print(f"[지표 발생 집계] {src}")
        print(f"  집계할 구간이 없습니다. 지표 계산에 앞의 {WARMUP}봉이 쓰이므로 "
              f"최소 {WARMUP + MIN_BARS}봉이 필요한데 {len(candles)}봉뿐입니다.")
        print("  일봉 API 는 연속조회가 없어 더 과거를 받을 수 없습니다. 빈도 집계는")
        print("  데모 데이터(인자 없이 실행)로 보고, 종목의 현재 상태는 개별 지표로 보세요.")
        print(f"    python -m indicators.stochastic {iscd}")
        return

    events = [pick] if pick in EVENT else ([] if pick else list(EVENT))
    states = [pick] if pick in STATE else ([] if pick else list(STATE))
    print(f"[지표 발생 집계] {src}")

    if events:
        print("\n  ■ 사건형 — 교차가 일어난 횟수 (교차한 봉에서만 참)")
        print(f"    {'지표':<16} {'대상봉':>6} {'발생':>6} {'평균간격':>8}   내역")
        for name in events:
            r = run(name, candles)
            gap = f"{r['mean_gap']:.0f}봉" if r["mean_gap"] else "-"
            print(f"    {name:<16} {r['bars']:>6} {r['hits']:>6} {gap:>8}   {', '.join(_fmt(r, '회'))}")

    if states:
        print("\n  ■ 상태형 — 조건에 머문 봉 수 (조건이 유지되는 동안 계속 참)")
        print(f"    {'지표':<16} {'대상봉':>6} {'해당':>6} {'비중':>7}   내역")
        for name in states:
            r = run(name, candles)
            pct = f"{r['hits'] / r['bars'] * 100:.1f}%" if r["bars"] else "-"
            print(f"    {name:<16} {r['bars']:>6} {r['hits']:>6} {pct:>7}   {', '.join(_fmt(r, '봉'))}")

    print("\n  * 두 표의 숫자는 단위가 다릅니다. 가로로 비교하지 마세요.")
    print("  * 수익률이 아닙니다. 지표가 걸린 빈도만 셉니다.")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
