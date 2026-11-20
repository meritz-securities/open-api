"""표준 기술적 지표 (순수 파이썬, 외부 의존성 없음).

공개된 표준 지표만 구현한다: SMA · EMA · RSI · 볼린저밴드 · MACD.
입력은 종가 리스트(과거→현재 순). 반환은 각 시점 값 리스트이며, 계산 불가 구간은 None.

이 지표들은 특정 회사 소유가 아닌 공용(퍼블릭) 기술적 분석 정의를 따른다.
"""
from __future__ import annotations

import math


def sma(values: list[float], period: int) -> list:
    """단순이동평균(Simple Moving Average)."""
    out: list = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list:
    """지수이동평균(Exponential Moving Average). 초기값은 첫 period 구간의 SMA로 시딩."""
    out: list = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list:
    """RSI (Wilder 평활). 0~100. period+1 개 이상 종가 필요."""
    out: list = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def bollinger(values: list[float], period: int = 20, num_std: float = 2.0) -> dict:
    """볼린저밴드. mid=SMA, upper/lower=mid ± num_std·표준편차(모집단)."""
    mid = sma(values, period)
    upper: list = [None] * len(values)
    lower: list = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is None:
            continue
        window = values[i - period + 1: i + 1]
        m = mid[i]
        sd = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return {"mid": mid, "upper": upper, "lower": lower}


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD(12,26,9). macd=EMA_fast-EMA_slow, signal=EMA(macd), hist=macd-signal."""
    ema_fast, ema_slow = ema(values, fast), ema(values, slow)
    macd_line: list = [None] * len(values)
    for i in range(len(values)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    signal_line: list = [None] * len(values)
    hist: list = [None] * len(values)
    defined = [v for v in macd_line if v is not None]
    if len(defined) >= signal:
        start = next(i for i, v in enumerate(macd_line) if v is not None)
        sig_sub = ema(macd_line[start:], signal)
        for j, v in enumerate(sig_sub):
            signal_line[start + j] = v
        for i in range(len(values)):
            if macd_line[i] is not None and signal_line[i] is not None:
                hist[i] = macd_line[i] - signal_line[i]
    return {"macd": macd_line, "signal": signal_line, "hist": hist}


def crossed_up(a: list, b: list) -> bool:
    """직전 a<=b 에서 현재 a>b 로 상향 돌파(골든크로스 성격)."""
    if len(a) < 2 or len(b) < 2:
        return False
    a0, a1, b0, b1 = a[-2], a[-1], b[-2], b[-1]
    if None in (a0, a1, b0, b1):
        return False
    return a0 <= b0 and a1 > b1


def crossed_down(a: list, b: list) -> bool:
    """직전 a>=b 에서 현재 a<b 로 하향 돌파(데드크로스 성격)."""
    if len(a) < 2 or len(b) < 2:
        return False
    a0, a1, b0, b1 = a[-2], a[-1], b[-2], b[-1]
    if None in (a0, a1, b0, b1):
        return False
    return a0 >= b0 and a1 < b1

def true_range(high: list[float], low: list[float], close: list[float]) -> list:
    """True Range. max(고−저, |고−전일종가|, |저−전일종가|).

    첫 봉은 전일 종가가 없으므로 고−저로 둔다(표준 관행).
    """
    out: list = [None] * len(close)
    for i in range(len(close)):
        if high[i] is None or low[i] is None:
            continue
        if i == 0:
            out[i] = high[i] - low[i]
        else:
            pc = close[i - 1]
            out[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
    return out


def atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list:
    """ATR(Average True Range). Wilder 평활 — RSI 와 같은 방식이다.

    첫 값은 인덱스 period 에 놓이며 초기 period 구간 TR 의 단순평균으로 시딩한다.
    """
    tr = true_range(high, low, close)
    out: list = [None] * len(close)
    if period <= 0 or len(close) <= period:
        return out
    seed = sum(tr[1:period + 1]) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, len(close)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def stochastic(high: list[float], low: list[float], close: list[float],
               period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> dict:
    """스토캐스틱 슬로우. %K 는 기간 내 고저 범위에서 종가의 위치(0~100)다.

    fast %K = (종가 - 기간최저) / (기간최고 - 기간최저) * 100
    slow %K = SMA(fast %K, smooth_k),  %D = SMA(slow %K, smooth_d)

    고가와 저가가 같아 범위가 0이면(상·하한가 등) 그 봉의 %K 는 None 이다.
    """
    n = len(close)
    fast_k: list = [None] * n
    for i in range(period - 1, n):
        hh = max(high[i - period + 1:i + 1])
        ll = min(low[i - period + 1:i + 1])
        if hh > ll:
            fast_k[i] = (close[i] - ll) / (hh - ll) * 100
    k = _sma_sparse(fast_k, smooth_k)
    d = _sma_sparse(k, smooth_d)
    return {"fast_k": fast_k, "k": k, "d": d}


def _sma_sparse(values: list, period: int) -> list:
    """앞쪽이 None 으로 비어 있는 계열의 이동평균. 창 안에 None 이 하나라도 있으면 None."""
    out: list = [None] * len(values)
    if period <= 0:
        return out
    for i in range(period - 1, len(values)):
        w = values[i - period + 1:i + 1]
        if all(v is not None for v in w):
            out[i] = sum(w) / period
    return out


def obv(close: list[float], volume: list[float]) -> list:
    """OBV(On-Balance Volume). 종가가 오른 날 거래량을 더하고 내린 날 뺀 누적값.

    첫 봉은 비교 대상이 없으므로 0 에서 시작한다. 보합은 더하지도 빼지도 않는다.
    절대값 자체에는 의미가 없고 방향과 기울기만 본다.
    """
    out: list = []
    acc = 0.0
    for i, c in enumerate(close):
        if i and c > close[i - 1]:
            acc += volume[i]
        elif i and c < close[i - 1]:
            acc -= volume[i]
        out.append(acc)
    return out


def donchian(high: list[float], low: list[float], period: int = 20) -> dict:
    """돈치안 채널. 각 봉 기준 **직전** period 봉의 최고가·최저가다.

    당일을 뺀 이유는 돌파 판정 때문이다. 당일 고가를 넣으면 신고가인 날은
    언제나 상단과 같아져 돌파가 성립하지 않는다.
    """
    n = len(high)
    upper: list = [None] * n
    lower: list = [None] * n
    for i in range(period, n):
        upper[i] = max(high[i - period:i])
        lower[i] = min(low[i - period:i])
    return {"upper": upper, "lower": lower,
            "mid": [None if u is None else (u + lower[i]) / 2 for i, u in enumerate(upper)]}


# ---------------------------------------------------------------------------
# 확장 지표 (순수 파이썬, 외부 의존성 없음). 아래 지표들도 공용 기술적 분석
# 정의를 따른다. 입력·반환 규약은 위 함수들과 동일하다: 리스트는 과거→현재
# 순이며, 계산 불가 구간(워밍업·범위 0 등)은 None 이다.
# 공식 출처는 indicators/PROVENANCE.md 에 지표별로 기록한다.
# ---------------------------------------------------------------------------


def _ema_sparse(values: list, period: int) -> list:
    """앞쪽이 None 으로 비어 있는 계열의 EMA. 첫 유효 지점부터 ema() 를 적용한다."""
    out: list = [None] * len(values)
    if period <= 0:
        return out
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None:
        return out
    tail = values[start:]
    if any(v is None for v in tail) or len(tail) < period:
        # 중간에 구멍이 있으면(정상 지표 사슬에서는 발생하지 않음) 안전하게 비운다.
        return out
    sub = ema(tail, period)
    for j, v in enumerate(sub):
        out[start + j] = v
    return out


def wma(values: list[float], period: int) -> list:
    """가중이동평균(Weighted Moving Average). 가중치는 1..period, 최신 봉이 period.

    분모는 삼각수 period·(period+1)/2 다. 창 안에 None 이 있으면 그 지점은 None.
    """
    out: list = [None] * len(values)
    if period <= 0:
        return out
    denom = period * (period + 1) / 2.0
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(v * (j + 1) for j, v in enumerate(window)) / denom
    return out


def hma(values: list[float], period: int) -> list:
    """헐 이동평균(Hull Moving Average). WMA(2·WMA(n/2) − WMA(n), √n).

    반응 속도를 높이면서 지연을 줄인 가중이동평균 계열이다.
    """
    out: list = [None] * len(values)
    if period <= 1:
        return out
    half = max(1, period // 2)
    sqrt_p = max(1, int(period ** 0.5))
    wma_half = wma(values, half)
    wma_full = wma(values, period)
    raw: list = [None] * len(values)
    for i in range(len(values)):
        if wma_half[i] is not None and wma_full[i] is not None:
            raw[i] = 2 * wma_half[i] - wma_full[i]
    # raw 는 앞쪽만 None 이고 그 뒤로는 연속이므로 wma() 가 창 내 None 을 걸러준다.
    return wma(raw, sqrt_p)


def stdev(values: list[float], period: int) -> list:
    """이동 표준편차(모집단 기준). 볼린저밴드와 동일한 분모(period)를 쓴다."""
    out: list = [None] * len(values)
    if period <= 0:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        m = sum(window) / period
        out[i] = (sum((x - m) ** 2 for x in window) / period) ** 0.5
    return out


def roc(values: list[float], period: int = 12) -> list:
    """변화율(Rate of Change). (현재 − period 전) / (period 전) × 100 [%].

    기준값이 0이면 비율이 정의되지 않으므로 None.
    """
    out: list = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        if base != 0:
            out[i] = (values[i] - base) / base * 100.0
    return out


def momentum(values: list[float], period: int = 10) -> list:
    """모멘텀. 현재값 − period 전 값(절대 차이)."""
    out: list = [None] * len(values)
    for i in range(period, len(values)):
        out[i] = values[i] - values[i - period]
    return out


def cci(high: list[float], low: list[float], close: list[float], period: int = 20) -> list:
    """CCI(Commodity Channel Index). (TP − SMA(TP)) / (0.015 × 평균편차).

    TP(전형가격)=(고+저+종)/3. 평균편차가 0(완전 평탄)이면 CCI 는 0.0 으로 둔다.
    """
    n = len(close)
    tp = [(high[i] + low[i] + close[i]) / 3.0 for i in range(n)]
    tp_sma = sma(tp, period)
    out: list = [None] * n
    for i in range(period - 1, n):
        m = tp_sma[i]
        if m is None:
            continue
        window = tp[i - period + 1:i + 1]
        mean_dev = sum(abs(x - m) for x in window) / period
        out[i] = 0.0 if mean_dev == 0 else (tp[i] - m) / (0.015 * mean_dev)
    return out


def williams_r(high: list[float], low: list[float], close: list[float], period: int = 14) -> list:
    """윌리엄스 %R. (기간최고 − 종가) / (기간최고 − 기간최저) × −100. 범위 0..−100.

    고저 범위가 0이면(상·하한가 등) 그 봉은 None.
    """
    n = len(close)
    out: list = [None] * n
    for i in range(period - 1, n):
        hh = max(high[i - period + 1:i + 1])
        ll = min(low[i - period + 1:i + 1])
        if hh > ll:
            out[i] = (hh - close[i]) / (hh - ll) * -100.0
    return out


def adx(high: list[float], low: list[float], close: list[float], period: int = 14) -> dict:
    """ADX / +DI / −DI (Wilder). 추세의 방향(+DI,−DI)과 강도(ADX)를 본다.

    +DM·−DM·TR 을 Wilder 평활한 뒤 +DI=100·+DM/TR, −DI=100·−DM/TR,
    DX=100·|+DI−−DI|/(+DI+−DI), ADX=DX 의 Wilder 평균이다.
    +DI/−DI 는 인덱스 period 부터, ADX 는 인덱스 2·period−1 부터 값을 갖는다.
    """
    n = len(close)
    plus_di: list = [None] * n
    minus_di: list = [None] * n
    adx_out: list = [None] * n
    if period <= 0 or n <= period:
        return {"plus_di": plus_di, "minus_di": minus_di, "adx": adx_out}
    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        pdm[i] = up if (up > down and up > 0) else 0.0
        mdm[i] = down if (down > up and down > 0) else 0.0
        pc = close[i - 1]
        tr[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
    # Wilder 시딩: 인덱스 1..period 합. 첫 DI 는 인덱스 period.
    tr_s = sum(tr[1:period + 1])
    pdm_s = sum(pdm[1:period + 1])
    mdm_s = sum(mdm[1:period + 1])
    dx: list = [None] * n

    def _di(i):
        if tr_s == 0:
            return None, None
        pd = 100.0 * pdm_s / tr_s
        md = 100.0 * mdm_s / tr_s
        return pd, md

    pd, md = _di(period)
    plus_di[period], minus_di[period] = pd, md
    if pd is not None and (pd + md) != 0:
        dx[period] = 100.0 * abs(pd - md) / (pd + md)
    elif pd is not None:
        dx[period] = 0.0
    for i in range(period + 1, n):
        tr_s = tr_s - tr_s / period + tr[i]
        pdm_s = pdm_s - pdm_s / period + pdm[i]
        mdm_s = mdm_s - mdm_s / period + mdm[i]
        pd, md = _di(i)
        plus_di[i], minus_di[i] = pd, md
        if pd is not None and (pd + md) != 0:
            dx[i] = 100.0 * abs(pd - md) / (pd + md)
        elif pd is not None:
            dx[i] = 0.0
    # ADX: DX 의 Wilder 평균. 첫 ADX 는 인덱스 2·period−1 (DX period 개 평균).
    first_adx_idx = 2 * period - 1
    if first_adx_idx < n:
        window = dx[period:first_adx_idx + 1]
        if all(v is not None for v in window):
            prev = sum(window) / period
            adx_out[first_adx_idx] = prev
            for i in range(first_adx_idx + 1, n):
                if dx[i] is None:
                    break
                prev = (prev * (period - 1) + dx[i]) / period
                adx_out[i] = prev
    return {"plus_di": plus_di, "minus_di": minus_di, "adx": adx_out}


def aroon(high: list[float], low: list[float], period: int = 25) -> dict:
    """아룬(Aroon) Up/Down. 최근 period 구간에서 신고가·신저가가 얼마나 최근인지.

    Up = (period − 신고가 이후 경과봉) / period × 100, Down 은 신저가 기준.
    창은 현재 봉을 포함한 period+1 개 봉이다(표준).
    """
    n = len(high)
    up: list = [None] * n
    down: list = [None] * n
    for i in range(period, n):
        hw = high[i - period:i + 1]
        lw = low[i - period:i + 1]
        # 가장 최근의 최고/최저 위치(동률이면 최신 봉 우선).
        hh = max(hw)
        ll = min(lw)
        since_high = period - max(j for j, v in enumerate(hw) if v == hh)
        since_low = period - max(j for j, v in enumerate(lw) if v == ll)
        up[i] = (period - since_high) / period * 100.0
        down[i] = (period - since_low) / period * 100.0
    return {"up": up, "down": down}


def trix(values: list[float], period: int = 15) -> list:
    """TRIX. 삼중 지수평활(EMA 3회) 종가의 1봉 변화율 × 100 [%].

    e1=EMA(종가), e2=EMA(e1), e3=EMA(e2); TRIX=(e3−전일e3)/전일e3 × 100.
    """
    n = len(values)
    out: list = [None] * n
    e1 = ema(values, period)
    e2 = _ema_sparse(e1, period)
    e3 = _ema_sparse(e2, period)
    for i in range(1, n):
        if e3[i] is not None and e3[i - 1] is not None and e3[i - 1] != 0:
            out[i] = (e3[i] - e3[i - 1]) / e3[i - 1] * 100.0
    return out


def keltner(high: list[float], low: list[float], close: list[float],
            period: int = 20, atr_period: int = 10, mult: float = 2.0) -> dict:
    """켈트너 채널. mid=EMA(종가), upper/lower=mid ± mult × ATR.

    볼린저와 달리 변동성 폭을 표준편차가 아닌 ATR 로 잡는다.
    """
    mid = ema(close, period)
    atr_s = atr(high, low, close, atr_period)
    n = len(close)
    upper: list = [None] * n
    lower: list = [None] * n
    for i in range(n):
        if mid[i] is not None and atr_s[i] is not None:
            upper[i] = mid[i] + mult * atr_s[i]
            lower[i] = mid[i] - mult * atr_s[i]
    return {"mid": mid, "upper": upper, "lower": lower}


def parabolic_sar(high: list[float], low: list[float],
                  step: float = 0.02, max_step: float = 0.2) -> list:
    """파라볼릭 SAR (Wilder). 추세 추종 스톱값. 가속계수 AF 는 step 씩 증가, max_step 상한.

    첫 봉은 직전 봉이 없어 None. 초기 추세는 첫 두 봉으로 잡고, SAR 은 직전 두 봉의
    극값을 넘지 않도록 클램프한다.
    """
    n = len(high)
    out: list = [None] * n
    if n < 2:
        return out
    uptrend = high[1] >= high[0]
    af = step
    ep = high[0] if uptrend else low[0]
    sar_val = low[0] if uptrend else high[0]
    for i in range(1, n):
        sar_val = sar_val + af * (ep - sar_val)
        if uptrend:
            sar_val = min(sar_val, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < sar_val:
                uptrend = False
                sar_val = ep
                ep = low[i]
                af = step
            elif high[i] > ep:
                ep = high[i]
                af = min(af + step, max_step)
        else:
            sar_val = max(sar_val, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > sar_val:
                uptrend = True
                sar_val = ep
                ep = high[i]
                af = step
            elif low[i] < ep:
                ep = low[i]
                af = min(af + step, max_step)
        out[i] = sar_val
    return out


def ultimate_oscillator(high: list[float], low: list[float], close: list[float],
                        period1: int = 7, period2: int = 14, period3: int = 28) -> list:
    """얼티밋 오실레이터(Larry Williams). 세 기간의 매수압력을 가중 합산(0~100).

    BP=종가 − min(저가, 전일종가), TR=max(고가, 전일종가) − min(저가, 전일종가).
    UO = 100 × (4·avg1 + 2·avg2 + avg3) / 7, avgN=ΣBP/ΣTR(해당 기간).
    """
    n = len(close)
    out: list = [None] * n
    bp = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        pc = close[i - 1]
        bp[i] = close[i] - min(low[i], pc)
        tr[i] = max(high[i], pc) - min(low[i], pc)
    longest = max(period1, period2, period3)

    def _avg(i, length):
        tr_sum = sum(tr[i - length + 1:i + 1])
        if tr_sum == 0:
            return None
        return sum(bp[i - length + 1:i + 1]) / tr_sum

    for i in range(longest, n):
        a1 = _avg(i, period1)
        a2 = _avg(i, period2)
        a3 = _avg(i, period3)
        if None in (a1, a2, a3):
            continue
        out[i] = 100.0 * (4 * a1 + 2 * a2 + a3) / 7.0
    return out


def vwap(high: list[float], low: list[float], close: list[float],
         volume: list[float]) -> list:
    """VWAP(거래량가중평균가격). 누적 전형가격×거래량 ÷ 누적 거래량.

    거래량 필드가 필요하다(일봉 bar 의 선택 항목 volume). 세션 시작부터 누적한다.
    누적 거래량이 0인 구간은 None.
    """
    n = len(close)
    out: list = [None] * n
    cum_tpv = 0.0
    cum_vol = 0.0
    for i in range(n):
        tp = (high[i] + low[i] + close[i]) / 3.0
        cum_tpv += tp * volume[i]
        cum_vol += volume[i]
        if cum_vol > 0:
            out[i] = cum_tpv / cum_vol
    return out


def mfi(high: list[float], low: list[float], close: list[float],
        volume: list[float], period: int = 14) -> list:
    """MFI(Money Flow Index). 거래량을 반영한 RSI 성격의 0~100 지표.

    TP=(고+저+종)/3, 원시자금흐름=TP×거래량. TP 상승일은 양(+), 하락일은 음(−).
    MFI = 100 − 100 / (1 + 양흐름합/음흐름합). 음흐름합이 0이면 100.
    거래량 필드가 필요하다. 값은 인덱스 period 부터.
    """
    n = len(close)
    out: list = [None] * n
    if period <= 0 or n <= period:
        return out
    tp = [(high[i] + low[i] + close[i]) / 3.0 for i in range(n)]
    rmf = [tp[i] * volume[i] for i in range(n)]
    for i in range(period, n):
        pos = neg = 0.0
        for j in range(i - period + 1, i + 1):
            if tp[j] > tp[j - 1]:
                pos += rmf[j]
            elif tp[j] < tp[j - 1]:
                neg += rmf[j]
        out[i] = 100.0 if neg == 0 else 100.0 - 100.0 / (1 + pos / neg)
    return out


def cmf(high: list[float], low: list[float], close: list[float],
        volume: list[float], period: int = 20) -> list:
    """CMF(Chaikin Money Flow). 기간 내 자금흐름량 합 ÷ 거래량 합 (−1~+1 근방).

    자금흐름승수 MFM=((종−저)−(고−종))/(고−저), 자금흐름량=MFM×거래량.
    고저 범위가 0인 봉의 MFM 은 0 으로 둔다. 기간 거래량 합이 0이면 None.
    거래량 필드가 필요하다.
    """
    n = len(close)
    out: list = [None] * n
    if period <= 0:
        return out
    mfv = [0.0] * n
    for i in range(n):
        rng = high[i] - low[i]
        mfm = 0.0 if rng == 0 else ((close[i] - low[i]) - (high[i] - close[i])) / rng
        mfv[i] = mfm * volume[i]
    for i in range(period - 1, n):
        vol_sum = sum(volume[i - period + 1:i + 1])
        if vol_sum != 0:
            out[i] = sum(mfv[i - period + 1:i + 1]) / vol_sum
    return out


# ---------------------------------------------------------------------------
# 확장 지표 2차 (이동평균·모멘텀·추세강도·변동성·거래량·기타).
# 규약은 동일: 과거→현재 리스트, 워밍업/정의불가 구간은 None, 순수 stdlib.
# 공식 출처는 PROVENANCE.md 참조.
# ---------------------------------------------------------------------------

# ----- 이동평균 계열 -------------------------------------------------------

def dema(values: list[float], period: int) -> list:
    """이중지수이동평균(DEMA). 2·EMA − EMA(EMA). 지연을 줄인 평활."""
    e1 = ema(values, period)
    e2 = _ema_sparse(e1, period)
    out: list = [None] * len(values)
    for i in range(len(values)):
        if e1[i] is not None and e2[i] is not None:
            out[i] = 2 * e1[i] - e2[i]
    return out


def tema(values: list[float], period: int) -> list:
    """삼중지수이동평균(TEMA). 3·EMA − 3·EMA² + EMA³."""
    e1 = ema(values, period)
    e2 = _ema_sparse(e1, period)
    e3 = _ema_sparse(e2, period)
    out: list = [None] * len(values)
    for i in range(len(values)):
        if e1[i] is not None and e2[i] is not None and e3[i] is not None:
            out[i] = 3 * e1[i] - 3 * e2[i] + e3[i]
    return out


def trima(values: list[float], period: int) -> list:
    """삼각이동평균(TRIMA). SMA 를 두 번 적용해 가운데 봉에 가중을 몰아준다."""
    out: list = [None] * len(values)
    if period <= 0:
        return out
    if period % 2 == 0:
        n1, n2 = period // 2, period // 2 + 1
    else:
        n1 = n2 = (period + 1) // 2
    first = sma(values, n1)
    return _sma_sparse(first, n2)


def kama(values: list[float], period: int = 10, fast: int = 2, slow: int = 30) -> list:
    """카우프만 적응이동평균(KAMA). 효율비(ER)로 평활상수를 가변 적용한다.

    ER = |종가−period전| / Σ|일간변화|, SC = (ER·(2/(f+1)−2/(s+1)) + 2/(s+1))².
    KAMAᵢ = KAMAᵢ₋₁ + SC·(종가ᵢ − KAMAᵢ₋₁). 첫 값은 인덱스 period.
    """
    n = len(values)
    out: list = [None] * n
    if period <= 0 or n <= period:
        return out
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    prev = values[period - 1]
    for i in range(period, n):
        direction = abs(values[i] - values[i - period])
        volatility = sum(abs(values[j] - values[j - 1]) for j in range(i - period + 1, i + 1))
        er = 0.0 if volatility == 0 else direction / volatility
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        prev = prev + sc * (values[i] - prev)
        out[i] = prev
    return out


def zlema(values: list[float], period: int) -> list:
    """제로래그 EMA(ZLEMA). 지연 보정치(종가 + (종가 − lag전 종가))에 EMA 를 적용."""
    out: list = [None] * len(values)
    if period <= 1:
        return out
    lag = (period - 1) // 2
    adjusted: list = [None] * len(values)
    for i in range(lag, len(values)):
        adjusted[i] = values[i] + (values[i] - values[i - lag])
    return _ema_sparse(adjusted, period)


def t3(values: list[float], period: int = 5, vfactor: float = 0.7) -> list:
    """틸슨 T3. EMA 6회 사슬의 가중합으로 매우 매끄러운 평활선을 만든다."""
    e1 = ema(values, period)
    e2 = _ema_sparse(e1, period)
    e3 = _ema_sparse(e2, period)
    e4 = _ema_sparse(e3, period)
    e5 = _ema_sparse(e4, period)
    e6 = _ema_sparse(e5, period)
    v = vfactor
    c1 = -v ** 3
    c2 = 3 * v ** 2 + 3 * v ** 3
    c3 = -6 * v ** 2 - 3 * v - 3 * v ** 3
    c4 = 1 + 3 * v + v ** 3 + 3 * v ** 2
    out: list = [None] * len(values)
    for i in range(len(values)):
        if None not in (e3[i], e4[i], e5[i], e6[i]):
            out[i] = c1 * e6[i] + c2 * e5[i] + c3 * e4[i] + c4 * e3[i]
    return out


def vwma(close: list[float], volume: list[float], period: int = 20) -> list:
    """거래량가중이동평균(VWMA). Σ(종가·거래량)/Σ거래량. 거래량 필드 필요."""
    n = len(close)
    out: list = [None] * n
    if period <= 0:
        return out
    for i in range(period - 1, n):
        vol_sum = sum(volume[i - period + 1:i + 1])
        if vol_sum != 0:
            out[i] = sum(close[j] * volume[j] for j in range(i - period + 1, i + 1)) / vol_sum
    return out


# ----- 모멘텀 --------------------------------------------------------------

def stochrsi(values: list[float], rsi_period: int = 14, stoch_period: int = 14,
             smooth_k: int = 3, smooth_d: int = 3) -> dict:
    """스토캐스틱 RSI. RSI 를 그 자체의 기간 고저로 다시 스토캐스틱화(0~100)."""
    r = rsi(values, rsi_period)
    n = len(values)
    fast: list = [None] * n
    for i in range(n):
        window = [r[j] for j in range(max(0, i - stoch_period + 1), i + 1) if r[j] is not None]
        if len(window) >= stoch_period and r[i] is not None:
            lo, hi = min(window[-stoch_period:]), max(window[-stoch_period:])
            if hi > lo:
                fast[i] = (r[i] - lo) / (hi - lo) * 100
    k = _sma_sparse(fast, smooth_k)
    d = _sma_sparse(k, smooth_d)
    return {"stochrsi": fast, "k": k, "d": d}


def awesome_oscillator(high: list[float], low: list[float],
                       fast: int = 5, slow: int = 34) -> list:
    """어썸 오실레이터(AO). SMA(중간값,5) − SMA(중간값,34). 중간값=(고+저)/2."""
    median = [(high[i] + low[i]) / 2.0 for i in range(len(high))]
    f, s = sma(median, fast), sma(median, slow)
    out: list = [None] * len(high)
    for i in range(len(high)):
        if f[i] is not None and s[i] is not None:
            out[i] = f[i] - s[i]
    return out


def ppo(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """PPO(퍼센티지 가격 오실레이터). (EMA_fast−EMA_slow)/EMA_slow × 100 과 신호선."""
    ema_fast, ema_slow = ema(values, fast), ema(values, slow)
    ppo_line: list = [None] * len(values)
    for i in range(len(values)):
        if ema_fast[i] is not None and ema_slow[i] is not None and ema_slow[i] != 0:
            ppo_line[i] = (ema_fast[i] - ema_slow[i]) / ema_slow[i] * 100
    signal_line = _ema_sparse(ppo_line, signal)
    hist: list = [None] * len(values)
    for i in range(len(values)):
        if ppo_line[i] is not None and signal_line[i] is not None:
            hist[i] = ppo_line[i] - signal_line[i]
    return {"ppo": ppo_line, "signal": signal_line, "hist": hist}


def dpo(values: list[float], period: int = 20) -> list:
    """DPO(추세제거 가격 오실레이터). 종가(과거로 (period/2+1) 이동) − SMA(종가)."""
    out: list = [None] * len(values)
    shift = period // 2 + 1
    base = sma(values, period)
    for i in range(len(values)):
        if base[i] is not None and i - shift >= 0:
            out[i] = values[i - shift] - base[i]
    return out


def cmo(values: list[float], period: int = 14) -> list:
    """CMO(Chande Momentum Oscillator). 100·(상승합−하락합)/(상승합+하락합), −100..100."""
    n = len(values)
    out: list = [None] * n
    if period <= 0 or n <= period:
        return out
    for i in range(period, n):
        up = down = 0.0
        for j in range(i - period + 1, i + 1):
            ch = values[j] - values[j - 1]
            up += max(ch, 0.0)
            down += max(-ch, 0.0)
        tot = up + down
        out[i] = 0.0 if tot == 0 else 100.0 * (up - down) / tot
    return out


def fisher_transform(high: list[float], low: list[float], period: int = 10) -> list:
    """피셔 변환. 중간값을 기간 고저로 −1..1 정규화 후 피셔 변환으로 전환점을 뾰족하게."""
    n = len(high)
    out: list = [None] * n
    median = [(high[i] + low[i]) / 2.0 for i in range(n)]
    val = 0.0
    fish = 0.0
    for i in range(period - 1, n):
        hh = max(median[i - period + 1:i + 1])
        ll = min(median[i - period + 1:i + 1])
        raw = 0.0 if hh == ll else 2 * ((median[i] - ll) / (hh - ll)) - 1
        val = 0.33 * raw + 0.67 * val
        val = max(-0.999, min(0.999, val))
        fish = 0.5 * math.log((1 + val) / (1 - val)) + 0.5 * fish
        out[i] = fish
    return out


def coppock(values: list[float], roc1: int = 14, roc2: int = 11, wma_period: int = 10) -> list:
    """코폭 곡선. WMA( ROC(roc1) + ROC(roc2), wma_period ). 장기 바닥 신호용."""
    r1, r2 = roc(values, roc1), roc(values, roc2)
    combined: list = [None] * len(values)
    for i in range(len(values)):
        if r1[i] is not None and r2[i] is not None:
            combined[i] = r1[i] + r2[i]
    return wma(combined, wma_period)


def rvi(open_: list[float], high: list[float], low: list[float], close: list[float],
        period: int = 10) -> dict:
    """RVI(상대활력지수). 4봉 대칭가중한 (종−시)/(고−저) 를 기간 평균. 신호선 포함.

    시가(open) 필드가 필요하다(일봉 bar 의 필수 항목).
    """
    n = len(close)
    num_raw: list = [None] * n
    den_raw: list = [None] * n
    for i in range(3, n):
        a = close[i] - open_[i]
        b = close[i - 1] - open_[i - 1]
        c = close[i - 2] - open_[i - 2]
        d = close[i - 3] - open_[i - 3]
        num_raw[i] = (a + 2 * b + 2 * c + d) / 6.0
        ra = high[i] - low[i]
        rb = high[i - 1] - low[i - 1]
        rc = high[i - 2] - low[i - 2]
        rd = high[i - 3] - low[i - 3]
        den_raw[i] = (ra + 2 * rb + 2 * rc + rd) / 6.0
    num_s = _sma_sparse(num_raw, period)
    den_s = _sma_sparse(den_raw, period)
    rvi_line: list = [None] * n
    for i in range(n):
        if num_s[i] is not None and den_s[i] is not None and den_s[i] != 0:
            rvi_line[i] = num_s[i] / den_s[i]
    signal: list = [None] * n
    for i in range(3, n):
        w = [rvi_line[i], rvi_line[i - 1], rvi_line[i - 2], rvi_line[i - 3]]
        if all(v is not None for v in w):
            signal[i] = (w[0] + 2 * w[1] + 2 * w[2] + w[3]) / 6.0
    return {"rvi": rvi_line, "signal": signal}


# ----- 추세 강도 -----------------------------------------------------------

def aroon_oscillator(high: list[float], low: list[float], period: int = 25) -> list:
    """아룬 오실레이터. Aroon Up − Aroon Down. −100..100."""
    ar = aroon(high, low, period)
    out: list = [None] * len(high)
    for i in range(len(high)):
        if ar["up"][i] is not None and ar["down"][i] is not None:
            out[i] = ar["up"][i] - ar["down"][i]
    return out


def vortex(high: list[float], low: list[float], close: list[float], period: int = 14) -> dict:
    """보텍스 지표(VI+/VI−). 상·하 방향 움직임을 TR 로 정규화한 추세 지표."""
    n = len(close)
    vi_plus: list = [None] * n
    vi_minus: list = [None] * n
    vmp = [0.0] * n
    vmm = [0.0] * n
    tr = true_range(high, low, close)
    for i in range(1, n):
        vmp[i] = abs(high[i] - low[i - 1])
        vmm[i] = abs(low[i] - high[i - 1])
    for i in range(period, n):
        tr_sum = sum(tr[i - period + 1:i + 1])
        if tr_sum != 0:
            vi_plus[i] = sum(vmp[i - period + 1:i + 1]) / tr_sum
            vi_minus[i] = sum(vmm[i - period + 1:i + 1]) / tr_sum
    return {"vi_plus": vi_plus, "vi_minus": vi_minus}


def mass_index(high: list[float], low: list[float], period: int = 25, ema_period: int = 9) -> list:
    """매스 지수. 고저폭 EMA 와 그 EMA 의 비를 기간 합산. 반전(밴드 벌어짐) 탐지용."""
    n = len(high)
    rng = [high[i] - low[i] for i in range(n)]
    e1 = ema(rng, ema_period)
    e2 = _ema_sparse(e1, ema_period)
    ratio: list = [None] * n
    for i in range(n):
        if e1[i] is not None and e2[i] is not None and e2[i] != 0:
            ratio[i] = e1[i] / e2[i]
    out: list = [None] * n
    for i in range(n):
        window = ratio[i - period + 1:i + 1]
        if i >= period - 1 and all(v is not None for v in window):
            out[i] = sum(window)
    return out


# ----- 변동성 --------------------------------------------------------------

def ulcer_index(close: list[float], period: int = 14) -> list:
    """얼서 지수. 기간 내 고점 대비 낙폭(%)의 제곱평균제곱근. 하방 위험 측정."""
    n = len(close)
    out: list = [None] * n
    for i in range(period - 1, n):
        window = close[i - period + 1:i + 1]
        peak = window[0]
        sq = 0.0
        for c in window:
            peak = max(peak, c)
            dd = (c - peak) / peak * 100.0 if peak != 0 else 0.0
            sq += dd * dd
        out[i] = (sq / period) ** 0.5
    return out


def historical_volatility(close: list[float], period: int = 20, annualize: int = 252) -> list:
    """역사적 변동성(%). 로그수익률의 표본표준편차(ddof=1)에 √annualize 를 곱한다."""
    n = len(close)
    out: list = [None] * n
    if period < 2:
        return out
    log_ret: list = [None] * n
    for i in range(1, n):
        if close[i - 1] > 0 and close[i] > 0:
            log_ret[i] = math.log(close[i] / close[i - 1])
    for i in range(period, n):
        window = log_ret[i - period + 1:i + 1]
        if all(v is not None for v in window):
            m = sum(window) / period
            var = sum((x - m) ** 2 for x in window) / (period - 1)
            out[i] = (var ** 0.5) * (annualize ** 0.5) * 100.0
    return out


def chaikin_volatility(high: list[float], low: list[float], period: int = 10) -> list:
    """차이킨 변동성(%). 고저폭 EMA 의 period 전 대비 변화율."""
    n = len(high)
    rng = [high[i] - low[i] for i in range(n)]
    e = ema(rng, period)
    out: list = [None] * n
    for i in range(n):
        if i - period >= 0 and e[i] is not None and e[i - period] is not None and e[i - period] != 0:
            out[i] = (e[i] - e[i - period]) / e[i - period] * 100.0
    return out


# ----- 거래량 기반 (거래량 필드 필요) --------------------------------------

def force_index(close: list[float], volume: list[float], period: int = 13) -> list:
    """포스 지수. (종가변화 × 거래량) 의 EMA. 거래량 필드 필요."""
    n = len(close)
    raw: list = [None] * n
    for i in range(1, n):
        raw[i] = (close[i] - close[i - 1]) * volume[i]
    return _ema_sparse(raw, period)


def ease_of_movement(high: list[float], low: list[float], volume: list[float],
                     period: int = 14, scale: float = 1.0) -> list:
    """이즈 오브 무브먼트(EMV). 가격 이동을 거래량으로 나눈 값의 SMA. 거래량 필드 필요.

    scale 은 표시 배율일 뿐 부호·상대 크기에는 영향이 없다. 고저폭이 0인 봉은 건너뛴다.
    """
    n = len(high)
    raw: list = [None] * n
    for i in range(1, n):
        rng = high[i] - low[i]
        if rng == 0 or volume[i] == 0:
            continue
        distance = (high[i] + low[i]) / 2.0 - (high[i - 1] + low[i - 1]) / 2.0
        box_ratio = (volume[i] / scale) / rng
        raw[i] = distance / box_ratio
    out: list = [None] * n
    for i in range(period - 1, n):
        window = raw[i - period + 1:i + 1]
        if all(v is not None for v in window):
            out[i] = sum(window) / period
    return out


def ad_line(high: list[float], low: list[float], close: list[float], volume: list[float]) -> list:
    """누적/분산선(A/D Line). 자금흐름승수×거래량의 누적. 거래량 필드 필요.

    MFM=((종−저)−(고−종))/(고−저), 고저폭 0인 봉은 0. OBV 보다 봉 내 위치를 반영.
    """
    out: list = []
    acc = 0.0
    for i in range(len(close)):
        rng = high[i] - low[i]
        mfm = 0.0 if rng == 0 else ((close[i] - low[i]) - (high[i] - close[i])) / rng
        acc += mfm * volume[i]
        out.append(acc)
    return out


def chaikin_oscillator(high: list[float], low: list[float], close: list[float],
                       volume: list[float], fast: int = 3, slow: int = 10) -> list:
    """차이킨 오실레이터. EMA(A/D선,fast) − EMA(A/D선,slow). 거래량 필드 필요."""
    adl = ad_line(high, low, close, volume)
    ef, es = ema(adl, fast), ema(adl, slow)
    out: list = [None] * len(close)
    for i in range(len(close)):
        if ef[i] is not None and es[i] is not None:
            out[i] = ef[i] - es[i]
    return out


def pvt(close: list[float], volume: list[float]) -> list:
    """PVT(가격거래량추세). 누적 Σ 거래량·(종가변화율). 거래량 필드 필요. 첫 봉 0."""
    out: list = []
    acc = 0.0
    for i in range(len(close)):
        if i and close[i - 1] != 0:
            acc += volume[i] * (close[i] - close[i - 1]) / close[i - 1]
        out.append(acc)
    return out


# ----- 기타 ----------------------------------------------------------------

def ichimoku(high: list[float], low: list[float], close: list[float],
             tenkan: int = 9, kijun: int = 26, senkou_b: int = 52) -> dict:
    """일목균형표. 전환선·기준선·선행스팬A/B·후행스팬.

    선행/후행 스팬의 '이동(displacement)'은 차트 표시용 오프셋이므로 적용하지 않고,
    각 값을 계산 시점 인덱스에 둔다. chikou 는 종가 그대로(후행 표시용)다.
    """
    n = len(high)

    def _mid(span):
        out: list = [None] * n
        for i in range(span - 1, n):
            out[i] = (max(high[i - span + 1:i + 1]) + min(low[i - span + 1:i + 1])) / 2.0
        return out

    ten = _mid(tenkan)
    kij = _mid(kijun)
    span_a: list = [None] * n
    for i in range(n):
        if ten[i] is not None and kij[i] is not None:
            span_a[i] = (ten[i] + kij[i]) / 2.0
    span_b = _mid(senkou_b)
    return {"tenkan": ten, "kijun": kij, "senkou_a": span_a,
            "senkou_b": span_b, "chikou": list(close)}


def pivot_points(high: list[float], low: list[float], close: list[float]) -> dict:
    """클래식(플로어) 피벗 포인트. 각 봉의 지지·저항을 **직전** 봉으로 산출한다."""
    n = len(close)
    keys = ("p", "r1", "r2", "r3", "s1", "s2", "s3")
    res = {k: [None] * n for k in keys}
    for i in range(1, n):
        ph, pl, pc = high[i - 1], low[i - 1], close[i - 1]
        p = (ph + pl + pc) / 3.0
        res["p"][i] = p
        res["r1"][i] = 2 * p - pl
        res["s1"][i] = 2 * p - ph
        res["r2"][i] = p + (ph - pl)
        res["s2"][i] = p - (ph - pl)
        res["r3"][i] = ph + 2 * (p - pl)
        res["s3"][i] = pl - 2 * (ph - p)
    return res


def elder_ray(high: list[float], low: list[float], close: list[float], period: int = 13) -> dict:
    """엘더 레이. 황소힘=고가−EMA(종가), 곰힘=저가−EMA(종가)."""
    base = ema(close, period)
    n = len(close)
    bull: list = [None] * n
    bear: list = [None] * n
    for i in range(n):
        if base[i] is not None:
            bull[i] = high[i] - base[i]
            bear[i] = low[i] - base[i]
    return {"bull": bull, "bear": bear}
