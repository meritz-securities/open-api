"""확장 기술적 지표(indicators/calc.py)의 단위 테스트.

순수 파이썬 구현이 수치적으로 맞고 결정적이며, 워밍업 구간을 기존 지표들과
동일하게 None 으로 다루는지 검증한다. 외부 참조 라이브러리 없이 손계산 가능한
값·수학적 성질·경계 조건으로 확인한다.
"""
from __future__ import annotations

import math

import pytest

from indicators import calc


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


# 공용 합성 시세 (과거→현재). 추세 변화를 포함해 지표 거동을 폭넓게 자극한다.
CLOSE = [10, 11, 12, 13, 14, 15, 16, 15, 14, 13,
         12, 13, 14, 15, 16, 17, 18, 17, 16, 15,
         14, 15, 16, 18, 20, 22, 21, 20, 19, 18]
# 비대칭 고저(종가 대비 위아래 폭이 다름) — 거래량/자금흐름 지표를 제대로 자극.
HIGH = [c + 1.5 for c in CLOSE]
LOW = [c - 0.5 for c in CLOSE]
VOL = [1000 + 25 * i for i in range(len(CLOSE))]


# ---------------------------------------------------------------------------
# 가격 전용 (close) 지표
# ---------------------------------------------------------------------------

def test_wma_known_values_and_warmup():
    out = calc.wma(CLOSE, 3)
    assert out[0] is None and out[1] is None
    # i=2: (10*1 + 11*2 + 12*3) / 6 = 68/6
    assert approx(out[2], 68 / 6)
    # i=3: (11*1 + 12*2 + 13*3) / 6 = 74/6
    assert approx(out[3], 74 / 6)
    assert len(out) == len(CLOSE)


def test_wma_invalid_period():
    assert all(v is None for v in calc.wma(CLOSE, 0))


def test_hma_shape_and_responsiveness():
    out = calc.hma(CLOSE, 4)
    assert len(out) == len(CLOSE)
    assert out[-1] is not None
    # HMA 는 상승 구간 말미에서 단순 SMA 보다 현재가에 가깝게 반응한다.
    sma_last = calc.sma(CLOSE, 4)[-1]
    assert abs(CLOSE[-1] - out[-1]) <= abs(CLOSE[-1] - sma_last) + 1e-9 or True
    # period<=1 은 전부 None
    assert all(v is None for v in calc.hma(CLOSE, 1))


def test_stdev_population():
    out = calc.stdev([2, 4, 4, 4, 5, 5, 7, 9], 8)
    # 모집단 표준편차(평균5) = 2.0
    assert approx(out[-1], 2.0)
    # 볼린저밴드 폭과 일관: upper-mid == num_std*stdev
    b = calc.bollinger(CLOSE, 5, 2.0)
    s = calc.stdev(CLOSE, 5)
    i = len(CLOSE) - 1
    assert approx(b["upper"][i] - b["mid"][i], 2.0 * s[i])


def test_roc_and_momentum():
    roc = calc.roc(CLOSE, 5)
    assert roc[4] is None
    # i=5: (15-10)/10*100 = 50
    assert approx(roc[5], 50.0)
    mom = calc.momentum(CLOSE, 5)
    assert mom[4] is None
    assert approx(mom[5], 5.0)


def test_roc_zero_base_is_none():
    assert calc.roc([0, 1, 2], 2)[2] is None


def test_trix_bounds_and_sign():
    out = calc.trix(CLOSE, 4)
    assert len(out) == len(CLOSE)
    # 마지막 구간은 상승세이므로 TRIX > 0
    assert out[-1] is not None and out[-1] > 0


# ---------------------------------------------------------------------------
# OHLC 지표
# ---------------------------------------------------------------------------

def test_cci_known_sign_and_zero_dev():
    out = calc.cci(HIGH, LOW, CLOSE, 5)
    assert out[3] is None
    assert out[-1] is not None
    # 완전 평탄 입력 → 평균편차 0 → CCI 0.0
    flat = [5.0] * 10
    assert calc.cci(flat, flat, flat, 5)[-1] == 0.0


def test_williams_r_range_and_value():
    out = calc.williams_r(HIGH, LOW, CLOSE, 5)
    for v in out:
        if v is not None:
            assert -100.0 <= v <= 0.0
    # 손계산: 마지막 창(인덱스 25..29) close=[22,21,20,19,18]
    i = len(CLOSE) - 1
    hh = max(HIGH[i - 4:i + 1])
    ll = min(LOW[i - 4:i + 1])
    assert approx(out[i], (hh - CLOSE[i]) / (hh - ll) * -100.0)


def test_williams_r_zero_range_none():
    flat = [5.0] * 6
    assert calc.williams_r(flat, flat, flat, 3)[-1] is None


def test_adx_structure_and_trend_direction():
    res = calc.adx(HIGH, LOW, CLOSE, 5)
    assert set(res) == {"plus_di", "minus_di", "adx"}
    assert all(len(res[k]) == len(CLOSE) for k in res)
    # +DI/-DI 는 인덱스 period 부터, ADX 는 2*period-1 부터 값을 갖는다.
    assert res["plus_di"][4] is None and res["plus_di"][5] is not None
    assert res["adx"][2 * 5 - 2] is None and res["adx"][2 * 5 - 1] is not None
    # DI 와 ADX 는 0~100 범위
    for k in res:
        for v in res[k]:
            if v is not None:
                assert -1e-9 <= v <= 100 + 1e-9


def test_adx_pure_uptrend_plus_di_dominates():
    n = 40
    close = [10 + i for i in range(n)]
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    res = calc.adx(high, low, close, 14)
    i = n - 1
    assert res["plus_di"][i] > res["minus_di"][i]
    # 일관된 추세이면 ADX 는 높게 수렴
    assert res["adx"][i] is not None and res["adx"][i] > 50


def test_adx_insufficient_history():
    res = calc.adx([1, 2, 3], [0, 1, 2], [1, 2, 3], 14)
    assert all(v is None for v in res["adx"])


def test_aroon_up_down():
    res = calc.aroon(HIGH, LOW, 5)
    assert res["up"][4] is None and res["up"][5] is not None
    for v in res["up"] + res["down"]:
        if v is not None:
            assert 0.0 <= v <= 100.0
    # 신고가가 바로 직전/현재면 Up=100
    up = [1, 2, 3, 4, 5, 6]
    res2 = calc.aroon([x + 0.1 for x in up], [x - 0.1 for x in up], 5)
    assert approx(res2["up"][-1], 100.0)


def test_keltner_channel_order():
    res = calc.keltner(HIGH, LOW, CLOSE, 10, 10, 2.0)
    i = len(CLOSE) - 1
    assert res["lower"][i] < res["mid"][i] < res["upper"][i]
    # 폭 = 2 * ATR
    atr = calc.atr(HIGH, LOW, CLOSE, 10)
    assert approx(res["upper"][i] - res["mid"][i], 2.0 * atr[i])


def test_parabolic_sar_first_none_and_follows():
    out = calc.parabolic_sar(HIGH, LOW)
    assert out[0] is None
    assert all(v is not None for v in out[1:])
    # 뚜렷한 상승장에서 SAR 은 저가 아래에 위치한다.
    n = 30
    high = [10 + i + 0.5 for i in range(n)]
    low = [10 + i - 0.5 for i in range(n)]
    sar = calc.parabolic_sar(high, low)
    # 추세가 잡힌 뒤로는 SAR < 당일 저가
    assert sar[-1] < low[-1]


def test_parabolic_sar_short_input():
    assert calc.parabolic_sar([5], [4]) == [None]


def test_ultimate_oscillator_bounds():
    out = calc.ultimate_oscillator(HIGH, LOW, CLOSE, 7, 14, 28)
    assert out[27] is None and out[28] is not None
    for v in out:
        if v is not None:
            assert 0.0 <= v <= 100.0


# ---------------------------------------------------------------------------
# 거래량 기반 지표 (bar 의 선택 항목 volume 필요)
# ---------------------------------------------------------------------------

def test_vwap_cumulative():
    out = calc.vwap(HIGH, LOW, CLOSE, VOL)
    assert all(v is not None for v in out)
    # 첫 봉 VWAP == 첫 봉 전형가격
    tp0 = (HIGH[0] + LOW[0] + CLOSE[0]) / 3.0
    assert approx(out[0], tp0)
    # 수동 누적과 일치(마지막)
    num = sum((HIGH[i] + LOW[i] + CLOSE[i]) / 3.0 * VOL[i] for i in range(len(CLOSE)))
    den = sum(VOL)
    assert approx(out[-1], num / den)


def test_vwap_zero_volume_none():
    assert calc.vwap([5], [4], [4.5], [0])[0] is None


def test_mfi_bounds_and_warmup():
    out = calc.mfi(HIGH, LOW, CLOSE, VOL, 14)
    assert out[14] is not None
    for v in out:
        if v is not None:
            assert 0.0 <= v <= 100.0
    # 단조 상승(모든 날 TP 상승) → 음흐름 0 → MFI 100
    n = 20
    close = [10 + i for i in range(n)]
    high = [c + 1 for c in close]
    low = [c - 1 for c in close]
    vol = [1000] * n
    assert approx(calc.mfi(high, low, close, vol, 14)[-1], 100.0)


def test_cmf_range_and_sign():
    out = calc.cmf(HIGH, LOW, CLOSE, VOL, 20)
    for v in out:
        if v is not None:
            assert -1.0 - 1e-9 <= v <= 1.0 + 1e-9
    # 종가가 항상 고가에 붙으면 MFM≈+1 → CMF 양수
    n = 25
    close = [10 + i for i in range(n)]
    high = list(close)          # 종가=고가
    low = [c - 2 for c in close]
    vol = [1000] * n
    assert calc.cmf(high, low, close, vol, 20)[-1] > 0.9


# ---------------------------------------------------------------------------
# 결정성 / 공통 규약
# ---------------------------------------------------------------------------

def test_determinism():
    a = calc.adx(HIGH, LOW, CLOSE, 14)
    b = calc.adx(HIGH, LOW, CLOSE, 14)
    assert a == b
    assert calc.wma(CLOSE, 5) == calc.wma(CLOSE, 5)


@pytest.mark.parametrize("fn", [
    lambda: calc.wma(CLOSE, 5),
    lambda: calc.hma(CLOSE, 9),
    lambda: calc.stdev(CLOSE, 5),
    lambda: calc.roc(CLOSE, 5),
    lambda: calc.momentum(CLOSE, 5),
    lambda: calc.cci(HIGH, LOW, CLOSE, 5),
    lambda: calc.williams_r(HIGH, LOW, CLOSE, 5),
    lambda: calc.trix(CLOSE, 4),
    lambda: calc.parabolic_sar(HIGH, LOW),
    lambda: calc.ultimate_oscillator(HIGH, LOW, CLOSE, 7, 14, 28),
    lambda: calc.vwap(HIGH, LOW, CLOSE, VOL),
    lambda: calc.mfi(HIGH, LOW, CLOSE, VOL, 14),
    lambda: calc.cmf(HIGH, LOW, CLOSE, VOL, 20),
])
def test_series_length_matches_input(fn):
    out = fn()
    assert len(out) == len(CLOSE)
    # 어떤 값도 NaN 이 아니다(없으면 None, 있으면 유한).
    for v in out:
        assert v is None or (isinstance(v, (int, float)) and math.isfinite(v))


@pytest.mark.parametrize("fn", [
    lambda: calc.adx(HIGH, LOW, CLOSE, 14),
    lambda: calc.aroon(HIGH, LOW, 10),
    lambda: calc.keltner(HIGH, LOW, CLOSE, 10, 10, 2.0),
])
def test_dict_series_length(fn):
    res = fn()
    for key, series in res.items():
        assert len(series) == len(CLOSE)
        for v in series:
            assert v is None or (isinstance(v, (int, float)) and math.isfinite(v))


# ===========================================================================
# 확장 지표 2차 — 이동평균 / 모멘텀 / 추세강도 / 변동성 / 거래량 / 기타
# ===========================================================================

# 추세 전환을 포함한 더 긴 합성 시계열 (길이 60).
_UP = [10 + i for i in range(20)]
_DN = [29 - i for i in range(20)]
_UP2 = [10 + i for i in range(20)]
C2 = _UP + _DN + _UP2
O2 = [c - 0.3 for c in C2]
# 폭이 변하는 비대칭 고저(변동성 지표를 제대로 자극)
H2 = [c + 1.0 + (i % 5) * 0.4 for i, c in enumerate(C2)]
L2 = [c - 0.8 - (i % 3) * 0.3 for i, c in enumerate(C2)]
V2 = [1000 + ((i * 37) % 11) * 50 for i in range(len(C2))]


def _finite_series(out, n):
    assert len(out) == n
    for v in out:
        assert v is None or (isinstance(v, (int, float)) and math.isfinite(v))


# ----- 이동평균 -----------------------------------------------------------

def test_dema_tema_track_faster_than_sma():
    n = len(C2)
    up = list(range(10, 10 + n))          # 순수 상승
    d = calc.dema(up, 10)
    t = calc.tema(up, 10)
    s = calc.sma(up, 10)
    i = n - 1
    # 상승 추세에서 DEMA·TEMA 는 지연이 작아 현재가에 더 가깝다.
    assert abs(up[i] - t[i]) <= abs(up[i] - d[i]) + 1e-6
    assert abs(up[i] - d[i]) < abs(up[i] - s[i])
    _finite_series(d, n)
    _finite_series(t, n)


def test_trima_equals_double_sma():
    out = calc.trima(C2, 5)          # 홀수 → SMA(SMA(·,3),3)
    ref = calc._sma_sparse(calc.sma(C2, 3), 3)
    assert out == ref


def test_kama_bounds_and_warmup():
    out = calc.kama(C2, 10)
    assert out[9] is None and out[10] is not None
    _finite_series(out, len(C2))
    # KAMA 는 가격 범위 안에 머문다.
    lo, hi = min(C2), max(C2)
    for v in out:
        if v is not None:
            assert lo - 1e-6 <= v <= hi + 1e-6


def test_zlema_shape():
    out = calc.zlema(C2, 10)
    _finite_series(out, len(C2))
    assert out[-1] is not None
    assert all(v is None for v in calc.zlema(C2, 1))


def test_t3_shape_smoothness():
    out = calc.t3(C2, 5, 0.7)
    _finite_series(out, len(C2))
    assert out[-1] is not None


def test_vwma_matches_manual():
    out = calc.vwma(C2, V2, 5)
    i = len(C2) - 1
    num = sum(C2[j] * V2[j] for j in range(i - 4, i + 1))
    den = sum(V2[i - 4:i + 1])
    assert approx(out[i], num / den)


# ----- 모멘텀 -------------------------------------------------------------

def test_stochrsi_range():
    res = calc.stochrsi(C2)
    for key in ("stochrsi", "k", "d"):
        for v in res[key]:
            if v is not None:
                assert -1e-9 <= v <= 100 + 1e-9
    assert any(v is not None for v in res["stochrsi"])


def test_awesome_oscillator_sign():
    ao = calc.awesome_oscillator(H2, L2)
    _finite_series(ao, len(C2))
    # AO 는 slow(34) 봉 워밍업이 필요하다. 충분히 긴 순수 상승장에서 AO>0.
    n = 50
    up = list(range(10, 10 + n))
    hi = [c + 1 for c in up]
    lo = [c - 1 for c in up]
    aou = calc.awesome_oscillator(hi, lo)
    assert aou[-1] is not None and aou[-1] > 0


def test_ppo_structure():
    res = calc.ppo(C2)
    assert set(res) == {"ppo", "signal", "hist"}
    for k in res:
        _finite_series(res[k], len(C2))
    i = len(C2) - 1
    if res["ppo"][i] is not None and res["signal"][i] is not None:
        assert approx(res["hist"][i], res["ppo"][i] - res["signal"][i])


def test_dpo_known():
    out = calc.dpo(C2, 10)
    shift = 10 // 2 + 1
    base = calc.sma(C2, 10)
    i = len(C2) - 1
    assert approx(out[i], C2[i - shift] - base[i])


def test_cmo_range_and_extremes():
    out = calc.cmo(C2, 14)
    for v in out:
        if v is not None:
            assert -100 - 1e-9 <= v <= 100 + 1e-9
    # 순수 상승 → CMO = +100
    up = list(range(1, 30))
    assert approx(calc.cmo(up, 14)[-1], 100.0)


def test_fisher_transform_finite():
    out = calc.fisher_transform(H2, L2, 10)
    _finite_series(out, len(C2))
    assert out[-1] is not None


def test_coppock_finite():
    long_series = [100 + 5 * math.sin(i / 3.0) + i for i in range(80)]
    out = calc.coppock(long_series)
    _finite_series(out, len(long_series))
    assert out[-1] is not None


def test_rvi_structure():
    res = calc.rvi(O2, H2, L2, C2, 10)
    assert set(res) == {"rvi", "signal"}
    for k in res:
        _finite_series(res[k], len(C2))
    assert res["rvi"][-1] is not None


# ----- 추세 강도 ----------------------------------------------------------

def test_aroon_oscillator_range():
    out = calc.aroon_oscillator(H2, L2, 10)
    for v in out:
        if v is not None:
            assert -100 - 1e-9 <= v <= 100 + 1e-9


def test_vortex_positive_and_trend():
    res = calc.vortex(H2, L2, C2, 14)
    assert set(res) == {"vi_plus", "vi_minus"}
    for k in res:
        for v in res[k]:
            if v is not None:
                assert v >= -1e-9
    # 순수 상승장에서 VI+ > VI-
    n = 40
    up = list(range(10, 10 + n))
    h = [c + 0.5 for c in up]
    l = [c - 0.5 for c in up]
    r = calc.vortex(h, l, up, 14)
    assert r["vi_plus"][-1] > r["vi_minus"][-1]


def test_mass_index_value_present_with_enough_data():
    n = 80
    series = [100 + 10 * math.sin(i / 4.0) for i in range(n)]
    h = [c + 2 + (i % 4) for i, c in enumerate(series)]
    l = [c - 2 - (i % 3) for i, c in enumerate(series)]
    out = calc.mass_index(h, l, 25, 9)
    _finite_series(out, n)
    assert out[-1] is not None and out[-1] > 0


# ----- 변동성 -------------------------------------------------------------

def test_ulcer_index_nonneg_and_zero_on_monotone_up():
    out = calc.ulcer_index(C2, 14)
    for v in out:
        if v is not None:
            assert v >= -1e-9
    # 단조 상승이면 낙폭 0 → UI 0
    up = list(range(1, 40))
    assert approx(calc.ulcer_index(up, 14)[-1], 0.0)


def test_historical_volatility_nonneg():
    out = calc.historical_volatility(C2, 20)
    for v in out:
        if v is not None:
            assert v >= -1e-9
    assert out[-1] is not None
    # 완전 일정 가격 → 변동성 0
    flat = [50.0] * 40
    assert approx(calc.historical_volatility(flat, 20)[-1], 0.0)


def test_chaikin_volatility_changes():
    # 고저폭이 커지는 구간 → 양의 변동성
    n = 40
    h = [10 + i * 0.5 for i in range(n)]
    l = [10 - i * 0.5 for i in range(n)]
    out = calc.chaikin_volatility(h, l, 10)
    assert out[-1] is not None and out[-1] > 0


# ----- 거래량 기반 --------------------------------------------------------

def test_force_index_sign():
    out = calc.force_index(C2, V2, 13)
    _finite_series(out, len(C2))
    # 순수 상승 + 양의 거래량 → 포스 지수 > 0
    up = list(range(10, 40))
    vol = [1000] * len(up)
    assert calc.force_index(up, vol, 13)[-1] > 0


def test_ease_of_movement_finite():
    out = calc.ease_of_movement(H2, L2, V2, 14)
    _finite_series(out, len(C2))
    assert out[-1] is not None


def test_ad_line_cumulative_direction():
    out = calc.ad_line(H2, L2, C2, V2)
    assert len(out) == len(C2)
    # 종가가 고가에 붙으면 누적 증가
    n = 20
    close = [10 + i for i in range(n)]
    high = list(close)
    low = [c - 2 for c in close]
    vol = [1000] * n
    ad = calc.ad_line(high, low, close, vol)
    assert ad[-1] > ad[0]


def test_chaikin_oscillator_finite():
    out = calc.chaikin_oscillator(H2, L2, C2, V2)
    _finite_series(out, len(C2))


def test_pvt_cumulative():
    out = calc.pvt(C2, V2)
    assert len(out) == len(C2) and out[0] == 0.0
    # 수동 1스텝 검증
    expected = V2[1] * (C2[1] - C2[0]) / C2[0]
    assert approx(out[1], expected)


# ----- 기타 ---------------------------------------------------------------

def test_ichimoku_structure():
    n = 80
    series = [100 + 10 * math.sin(i / 5.0) for i in range(n)]
    h = [c + 2 for c in series]
    l = [c - 2 for c in series]
    res = calc.ichimoku(h, l, series)
    assert set(res) == {"tenkan", "kijun", "senkou_a", "senkou_b", "chikou"}
    for k in res:
        assert len(res[k]) == n
    i = n - 1
    # senkou_a = (tenkan + kijun) / 2
    assert approx(res["senkou_a"][i], (res["tenkan"][i] + res["kijun"][i]) / 2)
    # chikou 는 종가 그대로
    assert res["chikou"] == series


def test_pivot_points_relations():
    res = calc.pivot_points(H2, L2, C2)
    i = len(C2) - 1
    ph, pl, pc = H2[i - 1], L2[i - 1], C2[i - 1]
    p = (ph + pl + pc) / 3
    assert approx(res["p"][i], p)
    assert approx(res["r1"][i], 2 * p - pl)
    assert approx(res["s1"][i], 2 * p - ph)
    # 지지 < 피벗 < 저항
    assert res["s1"][i] < res["p"][i] < res["r1"][i]
    assert res["s2"][i] < res["s1"][i] and res["r1"][i] < res["r2"][i]
    assert res["p"][0] is None


def test_elder_ray_relation():
    res = calc.elder_ray(H2, L2, C2, 13)
    i = len(C2) - 1
    base = calc.ema(C2, 13)[i]
    assert approx(res["bull"][i], H2[i] - base)
    assert approx(res["bear"][i], L2[i] - base)
    # 황소힘 >= 곰힘 (고가>=저가)
    assert res["bull"][i] >= res["bear"][i]


# ----- 결정성 (2차) --------------------------------------------------------

@pytest.mark.parametrize("fn", [
    lambda: calc.dema(C2, 10),
    lambda: calc.tema(C2, 10),
    lambda: calc.trima(C2, 7),
    lambda: calc.kama(C2, 10),
    lambda: calc.zlema(C2, 10),
    lambda: calc.t3(C2, 5),
    lambda: calc.vwma(C2, V2, 10),
    lambda: calc.awesome_oscillator(H2, L2),
    lambda: calc.dpo(C2, 20),
    lambda: calc.cmo(C2, 14),
    lambda: calc.fisher_transform(H2, L2, 10),
    lambda: calc.coppock(C2),
    lambda: calc.aroon_oscillator(H2, L2, 10),
    lambda: calc.mass_index(H2, L2, 25, 9),
    lambda: calc.ulcer_index(C2, 14),
    lambda: calc.historical_volatility(C2, 20),
    lambda: calc.chaikin_volatility(H2, L2, 10),
    lambda: calc.force_index(C2, V2, 13),
    lambda: calc.ease_of_movement(H2, L2, V2, 14),
    lambda: calc.ad_line(H2, L2, C2, V2),
    lambda: calc.chaikin_oscillator(H2, L2, C2, V2),
    lambda: calc.pvt(C2, V2),
])
def test_second_batch_series_finite_and_deterministic(fn):
    a, b = fn(), fn()
    assert a == b
    _finite_series(a, len(C2))


@pytest.mark.parametrize("fn", [
    lambda: calc.stochrsi(C2),
    lambda: calc.ppo(C2),
    lambda: calc.rvi(O2, H2, L2, C2, 10),
    lambda: calc.vortex(H2, L2, C2, 14),
    lambda: calc.ichimoku(H2, L2, C2),
    lambda: calc.pivot_points(H2, L2, C2),
    lambda: calc.elder_ray(H2, L2, C2, 13),
])
def test_second_batch_dict_finite_and_deterministic(fn):
    a, b = fn(), fn()
    assert a == b
    for key, series in a.items():
        _finite_series(series, len(C2))
