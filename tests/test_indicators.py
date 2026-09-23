"""지표 계산 검증.

계산식은 공개된 표준 정의를 따른다. 값이 맞는지 손으로 계산할 수 있는 입력으로
못박아 둔다 — 구현을 바꿀 때 여기서 걸려야 한다.

네트워크와 앱키를 쓰지 않는다.
"""
from __future__ import annotations

import statistics

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
from indicators.calc import atr, bollinger, donchian, ema, macd, obv, rsi, sma, stochastic, true_range

RAMP = [float(x) for x in range(1, 31)]          # 1 … 30


# ----------------------------------------------------------------- SMA
def test_sma_matches_hand_calculation():
    s = sma(RAMP, 5)
    assert s[:4] == [None] * 4, "앞 period-1 개는 계산할 수 없다"
    assert s[4] == pytest.approx(3.0)            # (1+2+3+4+5)/5
    assert s[29] == pytest.approx(28.0)          # (26+…+30)/5


def test_sma_rejects_nonpositive_period():
    assert sma(RAMP, 0) == [None] * len(RAMP)


# ----------------------------------------------------------------- EMA
def test_ema_seeds_with_sma_then_applies_smoothing():
    e = ema(RAMP, 5)
    assert e[3] is None
    assert e[4] == pytest.approx(3.0)            # 첫 5개의 SMA 로 시딩
    k = 2 / 6
    assert e[5] == pytest.approx(6 * k + 3 * (1 - k))


def test_ema_needs_period_values():
    assert ema([1.0, 2.0], 5) == [None, None]


# ----------------------------------------------------------------- RSI
def test_rsi_is_100_when_only_gains_and_0_when_only_losses():
    assert rsi(RAMP, 14)[-1] == pytest.approx(100.0)
    assert rsi(list(reversed(RAMP)), 14)[-1] == pytest.approx(0.0)


def test_rsi_first_value_lands_at_period_index():
    r = rsi(RAMP, 14)
    assert r[13] is None
    assert r[14] is not None


# ----------------------------------------------------------- 볼린저밴드
def test_bollinger_uses_population_standard_deviation():
    b = bollinger(RAMP, 20, 2.0)
    window = RAMP[10:30]
    mid = sum(window) / 20
    assert b["mid"][-1] == pytest.approx(mid)
    assert b["upper"][-1] == pytest.approx(mid + 2 * statistics.pstdev(window))
    # 표본 표준편차였다면 다른 값이 나온다
    assert b["upper"][-1] != pytest.approx(mid + 2 * statistics.stdev(window))


# ----------------------------------------------------------------- MACD
def test_macd_line_is_fast_ema_minus_slow_ema():
    d = macd(RAMP)
    assert d["macd"][-1] == pytest.approx(ema(RAMP, 12)[-1] - ema(RAMP, 26)[-1])


def test_macd_signal_needs_more_bars_than_macd_line():
    d = macd(RAMP)                                # 30봉 — 시그널(26+9)에 모자라다
    assert d["macd"][-1] is not None
    assert d["signal"][-1] is None
    assert d["hist"][-1] is None


# ------------------------------------------------------------------ ATR
def test_true_range_first_bar_is_high_minus_low():
    h, l, c = [10.0, 12.0], [8.0, 9.0], [9.0, 11.0]
    tr = true_range(h, l, c)
    assert tr[0] == pytest.approx(2.0)
    assert tr[1] == pytest.approx(max(12 - 9, abs(12 - 9), abs(9 - 9)))


def test_atr_seeds_with_mean_then_wilder_smoothing():
    h = [10.0, 12, 13, 12, 14, 15, 14, 16]
    l = [8.0, 9, 11, 10, 11, 13, 12, 13]
    c = [9.0, 11, 12, 11, 13, 14, 13, 15]
    p = 5
    tr = true_range(h, l, c)
    a = atr(h, l, c, p)
    seed = sum(tr[1:p + 1]) / p
    assert a[p - 1] is None
    assert a[p] == pytest.approx(seed)
    assert a[p + 1] == pytest.approx((seed * (p - 1) + tr[p + 1]) / p)


# ------------------------------------------------- 상태값 (사실 서술)
def _candles(closes, highs=None, lows=None, frgn=0, orgn=0):
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    return [{"date": f"2026{i:04d}", "open": c, "high": highs[i], "low": lows[i],
             "close": c, "volume": 1000.0, "frgn_ntby": frgn, "orgn_ntby": orgn}
            for i, c in enumerate(closes)]


def test_states_never_say_buy_or_sell():
    """반환 상태값은 관찰 사실만 서술한다. 매매 판단 어휘를 쓰지 않는다.

    부분 문자열로 검사한다 — 집합 교집합으로는 HOLDING·SHORTFALL 이 통과한다.
    """
    forbidden = ("BUY", "SELL", "HOLD", "LONG", "SHORT")
    seen = set()
    up = _candles([100.0 + i for i in range(60)])
    for mod in (gc_mod, rsi_mod, macd_mod, bb_mod, sd_mod, atr_mod,
                stoch_mod, obv_mod, pc_mod):
        seen.add(mod.evaluate(up)["state"])
    seen.add(ob_mod.evaluate(ob_mod.sample_orderbook())["state"])
    seen.add(sl_mod.evaluate(sl_mod.sample_quote())["state"])
    bad = {s: w for s in seen for w in forbidden if w in s}
    assert not bad, f"매매 판단 어휘가 상태값에 있습니다: {bad}"


def test_rsi_state_names_describe_the_band_not_a_crossing():
    down = _candles([100.0 - i for i in range(40)])
    assert rsi_mod.evaluate(down)["state"] == "OVERSOLD"
    up = _candles([100.0 + i for i in range(40)])
    assert rsi_mod.evaluate(up)["state"] == "OVERBOUGHT"


def test_short_series_returns_no_signal_not_an_error():
    tiny = _candles([100.0, 101.0, 102.0])
    for mod in (gc_mod, rsi_mod, macd_mod, bb_mod, sd_mod, atr_mod,
                stoch_mod, obv_mod, pc_mod):
        assert mod.evaluate(tiny)["state"] == "NO_SIGNAL"


def test_zero_valued_detail_is_kept_not_turned_into_none():
    """0.0 은 값이다. falsy 라고 None 으로 바꾸면 안 된다."""
    flat = _candles([100.0] * 60)
    d = macd_mod.evaluate(flat)["detail"]
    assert d["macd"] == 0.0
    assert d["macd"] is not None


# --------------------------------------------- 서버 제공 지지·저항
def test_server_levels_places_price_between_pivots():
    q = sl_mod.sample_quote()
    assert sl_mod.evaluate(q)["state"] == "BETWEEN_R1_R2"
    assert sl_mod.evaluate({**q, "stck_prpr": 300000})["state"] == "ABOVE_R2"
    assert sl_mod.evaluate({**q, "stck_prpr": 100000})["state"] == "BELOW_S2"


def test_server_levels_without_pivots_is_no_signal():
    assert sl_mod.evaluate({"stck_prpr": 1000})["state"] == "NO_SIGNAL"


# ------------------------------------------------------- 호가 불균형
def test_orderbook_imbalance_reads_the_heavier_side():
    base = ob_mod.sample_orderbook()
    assert ob_mod.evaluate(base)["state"] == "ASK_HEAVY"
    flipped = {**base, "total_askp_rsqn": 641688, "total_bidp_rsqn": 1847741}
    assert ob_mod.evaluate(flipped)["state"] == "BID_HEAVY"
    even = {**base, "total_askp_rsqn": 1000000, "total_bidp_rsqn": 1000000}
    assert ob_mod.evaluate(even)["state"] == "BALANCED"


def test_orderbook_imbalance_without_quantities_is_no_signal():
    assert ob_mod.evaluate({})["state"] == "NO_SIGNAL"


# --------------------------------------------------------- 스토캐스틱
def test_stochastic_k_is_position_within_the_high_low_range():
    """종가가 기간 최고면 100, 최저면 0 이다."""
    high = [10.0, 12.0, 14.0]
    low = [8.0, 8.0, 8.0]
    close = [9.0, 10.0, 14.0]          # 마지막 종가 = 3봉 최고가
    st = stochastic(high, low, close, period=3, smooth_k=1, smooth_d=1)
    assert st["fast_k"][2] == pytest.approx(100.0)
    assert st["k"][2] == pytest.approx(100.0), "smooth_k=1 이면 slow %K 는 fast %K 와 같다"

    close_at_low = [9.0, 10.0, 8.0]
    st2 = stochastic(high, low, close_at_low, period=3, smooth_k=1, smooth_d=1)
    assert st2["fast_k"][2] == pytest.approx(0.0)


def test_stochastic_is_none_when_the_range_is_zero():
    """상·하한가처럼 고가와 저가가 같으면 분모가 0 이다. 0 이나 50 으로 때우지 않는다."""
    flat = [100.0] * 5
    st = stochastic(flat, flat, flat, period=3, smooth_k=1, smooth_d=1)
    assert st["fast_k"][-1] is None
    assert st["k"][-1] is None


def test_stochastic_smoothing_averages_the_previous_k_values():
    st = stochastic([10.0] * 6, [0.0] * 6, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                    period=1, smooth_k=3, smooth_d=1)
    # period=1 이면 fast %K = (종가-저가)/(고가-저가)*100 = 종가*10
    assert st["fast_k"] == pytest.approx([10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    assert st["k"][-1] == pytest.approx((40.0 + 50.0 + 60.0) / 3)


def test_stochastic_state_reads_the_band():
    assert stoch_mod.evaluate(_candles([100.0 + i for i in range(40)]))["state"] == "OVERBOUGHT"
    assert stoch_mod.evaluate(_candles([100.0 - i for i in range(40)]))["state"] == "OVERSOLD"


# ---------------------------------------------------------------- OBV
def test_obv_adds_volume_on_up_days_and_subtracts_on_down_days():
    close = [10.0, 11.0, 10.0, 10.0, 12.0]
    vol = [100.0, 200.0, 300.0, 400.0, 500.0]
    #        시작0   +200    -300    보합0   +500
    assert obv(close, vol) == pytest.approx([0.0, 200.0, -100.0, -100.0, 400.0])


def test_obv_ignores_unchanged_closes():
    assert obv([5.0, 5.0, 5.0], [10.0, 20.0, 30.0]) == pytest.approx([0.0, 0.0, 0.0])


def test_obv_flags_divergence_when_price_and_volume_disagree():
    """가격은 오르는데 거래량은 하락 쪽에 쌓이면 다이버전스다."""
    # 가격은 우상향하되, 오른 날의 거래량을 내린 날보다 훨씬 작게 준다
    closes, vols = [100.0], [0.0]
    for i in range(24):
        closes.append(closes[-1] + (3.0 if i % 2 else -1.0))
        vols.append(10.0 if i % 2 else 1000.0)
    candles = _candles(closes)
    for c, v in zip(candles, vols):
        c["volume"] = v
    r = obv_mod.evaluate(candles, period=20)
    assert r["state"] == "DIVERGENCE"
    assert r["detail"]["price_change_pct"] > 0


# ------------------------------------------------------- 돈치안 채널
def test_donchian_excludes_the_current_bar():
    """당일을 넣으면 신고가인 날이 돌파로 안 잡힌다. 직전 n봉으로만 만든다."""
    high = [10.0, 11.0, 12.0, 99.0]
    low = [1.0, 2.0, 3.0, 0.0]
    ch = donchian(high, low, period=3)
    assert ch["upper"][3] == 12.0, "당일 고가 99 는 채널에 들어가면 안 된다"
    assert ch["lower"][3] == 1.0
    assert ch["upper"][2] is None, "직전 3봉이 없는 구간은 계산하지 않는다"


def test_price_channel_breaks_upper_on_a_new_high():
    rising = _candles([100.0] * 25 + [130.0])
    assert pc_mod.evaluate(rising, period=20)["state"] == "BREAK_UPPER"
    falling = _candles([100.0] * 25 + [70.0])
    assert pc_mod.evaluate(falling, period=20)["state"] == "BREAK_LOWER"
    flat = _candles([100.0] * 26)
    assert pc_mod.evaluate(flat, period=20)["state"] == "INSIDE"


# ----------------------------------------------- 사건형 / 상태형 구분
def test_backtest_keeps_event_and_state_indicators_apart():
    """단위가 다른 둘을 한 표에 섞으면 잘못 읽힌다. 분류가 겹치면 안 된다."""
    from indicators import backtest

    assert not set(backtest.EVENT) & set(backtest.STATE)
    assert set(backtest.INDICATORS) == set(backtest.EVENT) | set(backtest.STATE)


def test_event_indicators_are_true_only_on_the_crossing_bar():
    """사건형으로 분류한 지표는 같은 상태가 연속으로 이어지지 않는다."""
    from indicators import backtest
    from indicators.candles import sample_candles

    candles = sample_candles()
    for name in backtest.EVENT:
        r = backtest.run(name, candles)
        # 교차가 매 봉 일어날 수는 없다. 절반을 넘으면 사건형이 아니다.
        assert r["hits"] < r["bars"] / 2, f"{name} 은 사건형으로 보기 어렵습니다"


def test_no_signal_means_only_insufficient_data():
    """NO_SIGNAL 은 "판정하지 못했다" 는 뜻이다. 중립은 제 이름을 가진다.

    예전에 MACD 와 RSI 가 중립 구간에도 NO_SIGNAL 을 돌려줬다. README 는
    NO_SIGNAL 을 데이터 부족으로 정의하므로, state != "NO_SIGNAL" 로 유효성을
    거르는 코드가 정상 중립값을 부족으로 읽었다.
    """
    flat = _candles([100.0] * 60)          # 충분한 데이터, 아무 일도 없는 구간
    for mod in (macd_mod, rsi_mod, gc_mod, bb_mod, stoch_mod, pc_mod):
        state = mod.evaluate(flat)["state"]
        assert state != "NO_SIGNAL", f"{mod.__name__} 이 중립을 NO_SIGNAL 로 냅니다"

    tiny = _candles([100.0, 101.0, 102.0])  # 데이터 부족
    for mod in (macd_mod, rsi_mod, gc_mod, bb_mod, stoch_mod, pc_mod):
        assert mod.evaluate(tiny)["state"] == "NO_SIGNAL"


def test_readme_state_table_matches_the_code():
    """README 의 상태값 표가 코드와 어긋나면 걸린다."""
    import pathlib
    import re

    text = (pathlib.Path(__file__).resolve().parent.parent / "indicators/README.md").read_text(
        encoding="utf-8")
    documented = set(re.findall(r"`([A-Z][A-Z_]{2,})`", text))
    up = _candles([100.0 + i * 500 for i in range(60)])
    down = _candles([100_000.0 - i * 500 for i in range(60)])
    flat = _candles([100.0] * 60)
    produced = set()
    for mod in (gc_mod, rsi_mod, macd_mod, bb_mod, sd_mod, atr_mod, stoch_mod, obv_mod, pc_mod):
        for series in (up, down, flat):
            produced.add(mod.evaluate(series)["state"])
    missing = produced - documented
    assert not missing, f"README 에 없는 상태값: {sorted(missing)}"
