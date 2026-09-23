"""전략 계산기 검증.

지표와 달리 금액과 수량이 나온다. 그래서 검사하는 것도 다르다 —
예산을 넘지 않는가, 손실 상한이 늘 함께 나오는가, 주문을 내지 않는가.

네트워크와 앱키를 쓰지 않는다.
"""
from __future__ import annotations

import pathlib

import pytest

from indicators import atr as atr_mod
from indicators import bollinger as bb_mod
from indicators import golden_cross as gc_mod
from indicators import macd as macd_mod
from indicators import obv as obv_mod
from indicators import orderbook_imbalance as ob_mod
from indicators import price_channel as pc_mod
from indicators import rsi_reversal as rsi_mod
from indicators import server_levels as sl_mod
from indicators import stochastic as stoch_mod
from indicators import supply_demand as sd_mod
from position_sizing import split_accumulation as split
from position_sizing import volatility_sizing as vsize
from position_sizing.money import affordable_qty, average_price, krx_stock_tick, tick_ceil
from position_sizing.position import Position

SIZING_DIR = pathlib.Path(split.__file__).parent

# 테스트가 쓰는 값. 계산기에는 기본값이 없으므로 호출마다 명시한다.
TARGET = 0.10


def _candles(closes, highs=None, lows=None):
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    return [{"date": f"2026{i:04d}", "open": c, "high": highs[i], "low": lows[i],
             "close": c, "volume": 1000.0, "frgn_ntby": 0.0, "orgn_ntby": 0.0}
            for i, c in enumerate(closes)]


# ------------------------------------------------------------- money
def test_quantity_is_floored_and_never_negative():
    assert affordable_qty(100_000, 30_000) == 3.0
    assert affordable_qty(100_000, 30_000, allow_fractional=True) == pytest.approx(10 / 3)
    assert affordable_qty(-1, 100) == 0.0, "음수 예산에 음수 수량이 나오면 안 된다"
    assert affordable_qty(100, 0) == 0.0, "가격 0 으로 나누지 않는다"


def test_average_is_none_when_nothing_is_held():
    """평단 미형성은 0 이 아니라 없음이다. 0 이면 수익률이 무한대가 된다."""
    assert average_price(0, 0) is None
    assert average_price(100, 0) is None
    assert average_price(300_000, 3) == 100_000


def test_krx_tick_boundaries_belong_to_the_upper_band():
    assert krx_stock_tick(1_999) == 1
    assert krx_stock_tick(2_000) == 5
    assert krx_stock_tick(4_999) == 5
    assert krx_stock_tick(5_000) == 10
    assert krx_stock_tick(50_000) == 100
    assert krx_stock_tick(500_000) == 1_000


def test_tick_ceil_rounds_the_target_up():
    assert tick_ceil(279_500) == 279_500
    assert tick_ceil(279_501) == 280_000


# --------------------------------------------------------- 분할매수
def test_split_never_spends_more_than_the_budget():
    """전 회차를 끝까지 돌려도 투입 누적이 예산을 넘지 않는다."""
    pos = Position(budget=4_000_000, splits=40)
    price = 100_000.0
    for _ in range(80):                      # 회차 수보다 많이 돌려 본다
        d = split.plan(pos, price, TARGET)["detail"]
        if not d:
            break
        pos = pos.after_buy(d["plan_qty"], d["plan_cost"])
        assert pos.deployed <= pos.budget, "예산을 초과했습니다"
        price *= 0.97


def test_split_is_idempotent_once_the_budget_is_gone():
    """예산이 소진된 뒤 반복 호출해도 수량이 더 늘지 않는다."""
    pos = Position(budget=100_000, splits=1, deployed=100_000, qty=1)
    for _ in range(5):
        r = split.plan(pos, 100_000, TARGET)
        assert r["detail"]["plan_qty"] == 0
        pos = pos.after_buy(r["detail"]["plan_qty"], r["detail"]["plan_cost"])
    assert pos.deployed == 100_000


def test_split_average_matches_the_ledger():
    """평단은 누적 원장에서 유도한다. 회차마다 재계산해 이어붙이지 않는다."""
    pos = Position(budget=1_000_000, splits=10)
    price = 50_000.0
    for _ in range(5):
        d = split.plan(pos, price, TARGET)["detail"]
        pos = pos.after_buy(d["plan_qty"], d["plan_cost"])
        price *= 0.9
    assert pos.average == pytest.approx(pos.deployed / pos.qty)


def test_falling_price_lowers_the_average_but_grows_the_loss():
    """이 계산기에서 가장 중요한 검사.

    평단이 내려가는 것을 "손실이 줄었다"로 읽는 오해를 코드로 반박한다.
    하락이 이어지면 평단은 단조 감소하지만 평가손실은 단조 증가한다.
    """
    pos = Position(budget=10_000_000, splits=40)
    price = 100_000.0
    averages, losses = [], []
    for _ in range(10):
        d = split.plan(pos, price, TARGET)["detail"]
        pos = pos.after_buy(d["plan_qty"], d["plan_cost"])
        averages.append(pos.average)
        losses.append(pos.qty * price - pos.deployed)
        price *= 0.9

    assert all(b < a for a, b in zip(averages, averages[1:])), "평단이 단조 감소해야 합니다"
    assert all(b < a for a, b in zip(losses, losses[1:])), "평가손실은 단조 증가해야 합니다"


def test_split_reports_the_worst_case_every_time():
    """손실 상한은 선택 항목이 아니다. 금액을 내는 계산은 늘 함께 낸다."""
    pos = Position(budget=4_000_000, splits=40)
    d = split.plan(pos, 50_000, TARGET)["detail"]
    assert "worst_case" in d and d["worst_case"] > 0
    assert d["worst_case"] == d["deployed"] + d["plan_cost"]


def test_split_always_reports_average_and_loss_together():
    """평단만 내면 반쪽짜리다. 둘은 언제나 같이 나온다."""
    held = Position(budget=1_000_000, splits=10, deployed=200_000, qty=2)
    d = split.plan(held, 80_000, TARGET)["detail"]
    assert d["avg"] == 100_000
    assert d["unrealized"] == pytest.approx(2 * 80_000 - 200_000)
    assert "unrealized_pct" not in d, "비율은 내지 않는다 — 금액으로만 낸다"


def test_split_states_cover_the_boundaries():
    assert split.plan(Position(budget=0, splits=40), 100, TARGET)["state"] == "NO_SIGNAL"
    assert split.plan(Position(budget=1_000_000, splits=40), 0, TARGET)["state"] == "NO_SIGNAL"
    assert split.plan(Position(budget=1_000_000, splits=40), 100_000, TARGET)["state"] == "SPLIT_TOO_SMALL"
    assert split.plan(Position(budget=4_000_000, splits=40), 50_000, TARGET)["state"] == "NOT_STARTED"
    reached = Position(budget=4_000_000, splits=40, deployed=100_000, qty=1, rounds=1)
    assert split.plan(reached, 120_000, TARGET)["state"] == "TARGET_REACHED"
    done = Position(budget=4_000_000, splits=2, deployed=4_000_000, qty=40, rounds=2)
    assert split.plan(done, 100_000, TARGET)["state"] == "ROUNDS_COMPLETE"


def test_split_clamps_an_inconsistent_position():
    """외부에서 예산보다 많이 투입된 상태가 들어와도 음수 잔여가 나오지 않는다."""
    d = split.plan(Position(budget=100_000, splits=10, deployed=500_000, qty=5), 100_000, TARGET)["detail"]
    assert d["remaining"] == 0
    assert d["plan_qty"] == 0


# ------------------------------------------------- 변동성 기준 수량
def test_sizing_never_risks_more_than_the_allowance():
    """계산상 위험이 감수 금액을 넘지 않는다. 이 방식의 존재 이유다."""
    candles = _candles([100_000 + (i % 7) * 1_000 for i in range(40)])
    equity = 100_000_000
    d = vsize.plan(candles, equity, risk_pct=0.01)["detail"]
    assert d["actual_risk"] <= equity * 0.01


def test_sizing_shrinks_as_volatility_grows():
    calm = _candles([100_000] * 40, highs=[100_500] * 40, lows=[99_500] * 40)
    wild = _candles([100_000] * 40, highs=[110_000] * 40, lows=[90_000] * 40)
    q_calm = vsize.plan(calm, 100_000_000)["detail"]["qty"]
    q_wild = vsize.plan(wild, 100_000_000)["detail"]["qty"]
    assert q_wild < q_calm, "변동성이 커지면 수량이 줄어야 합니다"


def test_sizing_refuses_to_divide_by_zero_volatility():
    """보합·거래정지로 ATR 이 0 이면 수량이 무한대가 된다. 상태로 막는다."""
    flat = _candles([100_000] * 40, highs=[100_000] * 40, lows=[100_000] * 40)
    r = vsize.plan(flat, 100_000_000)
    assert r["state"] == "NO_SIGNAL"
    assert r["detail"] == {}


def test_sizing_respects_the_exposure_cap():
    calm = _candles([100_000] * 40, highs=[100_100] * 40, lows=[99_900] * 40)
    equity = 100_000_000
    r = vsize.plan(calm, equity, max_exposure_pct=0.05)
    assert r["state"] == "SIZE_CAPPED"
    assert r["detail"]["exposure"] <= equity * 0.05


def test_sizing_rejects_invalid_inputs():
    candles = _candles([100_000 + i * 100 for i in range(40)])
    for kwargs in ({"equity": 0}, {"equity": -1}, {"equity": 1e8, "risk_pct": 0},
                   {"equity": 1e8, "atr_k": 0}):
        assert vsize.plan(candles, **kwargs)["state"] == "NO_SIGNAL"
    assert vsize.plan(_candles([100.0] * 3), 1e8)["state"] == "NO_SIGNAL"


# ------------------------------------------------------- 공통 규약
# 상태값에 넣지 않는 어휘. 부분 문자열로 검사한다.
#
# SIGNAL 은 뺐다 — NO_SIGNAL 은 "데이터가 모자라 판정하지 못했다"는 뜻이고
# 이 저장소 전체가 그렇게 쓴다. 매매 신호를 뜻하지 않는다.
# ADD 도 뺐다 — 세 글자라 무관한 단어에 걸린다.
FORBIDDEN = ("BUY", "SELL", "HOLD", "LONG", "SHORT", "ENTRY", "EXIT",
             "TAKE_PROFIT", "STOP_LOSS", "RECOMMEND",
             "매수", "매도", "진입", "청산", "익절", "손절", "추천")


def _states() -> set:
    up = _candles([100_000 + i * 500 for i in range(60)])
    seen = {vsize.plan(up, 100_000_000)["state"]}
    for pos, price in ((Position(budget=0, splits=40), 100),
                       (Position(budget=1_000_000, splits=40), 100_000),
                       (Position(budget=4_000_000, splits=40), 50_000),
                       (Position(budget=4_000_000, splits=40, deployed=100_000, qty=1,
                                 rounds=1), 120_000),
                       (Position(budget=4_000_000, splits=2, deployed=4_000_000, qty=40,
                                 rounds=2), 100_000)):
        seen.add(split.plan(pos, price, TARGET)["state"])
    for mod in (gc_mod, rsi_mod, macd_mod, bb_mod, sd_mod, atr_mod, stoch_mod, obv_mod, pc_mod):
        seen.add(mod.evaluate(up)["state"])
    seen.add(ob_mod.evaluate(ob_mod.sample_orderbook())["state"])
    seen.add(sl_mod.evaluate(sl_mod.sample_quote())["state"])
    return seen


def test_no_state_contains_a_trading_verb():
    """부분 문자열까지 막는다. 집합 교집합으로는 HOLDING·SHORTFALL 이 통과한다."""
    bad = {s: w for s in _states() for w in FORBIDDEN if w in s}
    assert not bad, f"매매 판단 어휘가 상태값에 있습니다: {bad}"


def test_strategy_modules_do_not_reach_for_order_apis():
    """계산기는 주문 API 이름을 문자열로도 갖지 않는다."""
    hits = {f.name: w
            for f in SIZING_DIR.glob("*.py")
            for w in ("order_buy", "order_sell", "order_cancel", "order_modify")
            if w in f.read_text(encoding="utf-8")}
    assert not hits, f"주문 API 가 전략 모듈에 있습니다: {hits}"


def test_every_money_result_carries_its_assumptions():
    """계산에 넣지 않은 것을 결과에 싣는다. 문서에만 적으면 읽히지 않는다."""
    for d in (split.plan(Position(budget=4_000_000, splits=40), 50_000, TARGET)["detail"],
              vsize.plan(_candles([100_000 + i * 100 for i in range(40)]), 1e8)["detail"]):
        assert d["assumptions"], "assumptions 가 비어 있습니다"
        assert "worst_case" in d, "worst_case 가 없습니다"


def test_output_never_reports_a_return_rate():
    """평가손익은 금액으로만 낸다. 수익률·승률·낙폭은 산출하지 않는다.

    금액은 지금 얼마나 물려 있는지를 알리는 위험 고지이지만, 비율로 바꾸는
    순간 성과 지표로 읽힌다. 계산기가 낼 것이 아니다.
    """
    banned = ("수익률", "승률", "CAGR", "MDD", "낙폭", "연환산", "return_pct", "win_rate")
    results = [split.plan(Position(budget=4_000_000, splits=40, deployed=100_000, qty=2,
                                   rounds=1), 45_000, TARGET),
               vsize.plan(_candles([100_000 + i * 100 for i in range(40)]), 1e8)]
    for r in results:
        blob = repr(r)
        assert not [w for w in banned if w in blob], f"성과 표기가 있습니다: {r['state']}"
        assert not [k for k in r["detail"] if k.endswith("_pct") and "exposure" not in k
                    and "atr" not in k and "progress" not in k], \
            f"비율 필드가 있습니다: {list(r['detail'])}"


def test_demo_output_reports_no_return_rate():
    """실행 출력에도 성과 표기가 없어야 한다. detail 만 막으면 print 로 샌다.

    고지는 검사 대상이 아니다 — "수익률을 산출하지 않습니다" 는 산출이 아니라
    산출하지 않는다는 말이다. 고지 다음의 계산 결과만 본다.
    """
    import contextlib
    import io

    for mod in (split, vsize):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main(["demo"])
        body = buf.getvalue().split("\n\n", 1)[1]        # 고지 블록을 뺀 나머지
        assert "[고지]" not in body, "고지와 본문이 분리되지 않았습니다"
        found = [w for w in ("수익률", "승률", "CAGR", "MDD", "낙폭") if w in body]
        assert not found, f"{mod.__name__} 출력에 성과 표기: {found}"


# ------------------------------------------------------------- 고지
def test_every_runnable_module_prints_the_notice():
    """실행하면 첫 줄에 고지가 나와야 한다. 새 모듈이 빠뜨리면 여기서 걸린다."""
    import contextlib
    import importlib
    import io

    root = SIZING_DIR.parent
    missing = []
    for pkg in ("indicators", "position_sizing"):
        for f in sorted((root / pkg).glob("*.py")):
            if f.stem == "__init__" or "__main__" not in f.read_text(encoding="utf-8"):
                continue
            mod = importlib.import_module(f"{pkg}.{f.stem}")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.main(["demo"])
            if not buf.getvalue().startswith("[고지]"):
                missing.append(f"{pkg}.{f.stem}")
    assert not missing, f"실행 고지가 없는 모듈: {missing}"


def test_every_runnable_module_carries_the_docstring_notice():
    """모듈 docstring 에도 한 줄 고지를 둔다. 소스를 열어 보는 사람을 위한 것이다."""
    root = SIZING_DIR.parent
    missing = [f"{pkg}/{f.name}"
               for pkg in ("indicators", "position_sizing")
               for f in sorted((root / pkg).glob("*.py"))
               if ("__main__" in (t := f.read_text(encoding="utf-8")) or f.stem == "__init__")
               and "⚠️" not in t]
    assert not missing, f"docstring 고지가 없는 모듈: {missing}"


def test_disclaimer_covers_the_calculation_examples():
    """DISCLAIMER.md 가 두 패키지를 실제로 다루는지 확인한다."""
    text = (SIZING_DIR.parent / "DISCLAIMER.md").read_text(encoding="utf-8")
    for term in ("indicators/", "position_sizing/", "손절 규칙이 없습니다",
                 "권장값이 아니며", "수익률·승률·최대낙폭을 산출하지 않습니다"):
        assert term in text, f"DISCLAIMER.md 에 '{term}' 가 없습니다"
