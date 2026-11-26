"""표준 기술적 지표 (순수 파이썬, 외부 의존성 없음).

공개된 표준 지표만 구현한다: SMA · EMA · RSI · 볼린저밴드 · MACD.
입력은 종가 리스트(과거→현재 순). 반환은 각 시점 값 리스트이며, 계산 불가 구간은 None.

이 지표들은 특정 회사 소유가 아닌 공용(퍼블릭) 기술적 분석 정의를 따른다.
"""
from __future__ import annotations


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
