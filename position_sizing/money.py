"""금액·수량 계산의 공통부. 반올림 규칙을 한 곳에 모은다.

돈이 걸린 반올림은 모듈마다 제각각이면 안 된다. 수량은 언제나 내림이고,
평단은 누적 원장에서 유도한다.
"""
from __future__ import annotations

import math

# 국내 주식의 호가 가격단위 (2023-01-25 개편 기준, 유가증권·코스닥 주식).
# ETF·ETN·ELW·채권은 표가 다르다. 그쪽에 쓰지 말 것.
_KRX_STOCK_TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
                    (200_000, 100), (500_000, 500))


def krx_stock_tick(price: float) -> int:
    """국내 주식의 호가 가격단위. 경계값은 위 구간에 속한다 — 2,000원은 5원 단위다."""
    for upper, tick in _KRX_STOCK_TICKS:
        if price < upper:
            return tick
    return 1_000


def tick_floor(price: float, tick: int | None = None) -> float:
    """호가단위로 내림. 매수 지정가를 만들 때 쓴다."""
    t = tick or krx_stock_tick(price)
    return math.floor(price / t) * t


def tick_ceil(price: float, tick: int | None = None) -> float:
    """호가단위로 올림. 매도 목표가를 만들 때 쓴다.

    올림이라 목표가가 계산값보다 높아진다. 계산한 수익률에 정확히 맞추려면
    호가단위만큼 못 미칠 수 있다는 뜻이다.
    """
    t = tick or krx_stock_tick(price)
    return math.ceil(price / t) * t


def affordable_qty(amount: float, price: float, allow_fractional: bool = False) -> float:
    """금액으로 살 수 있는 수량. 국내 주식은 정수이므로 내림이 기본이다.

    금액이나 가격이 0 이하면 0 이다. 음수 수량은 어떤 경우에도 나오지 않는다.
    """
    if amount <= 0 or price <= 0:
        return 0.0
    q = amount / price
    return q if allow_fractional else float(math.floor(q))


def average_price(deployed: float, qty: float) -> float | None:
    """평단 = 누적 매입금액 / 누적 수량.

    **평단을 직접 받아 갱신하지 않는다.** 회차마다 평단을 재계산해 이어붙이면
    반올림 오차가 쌓인다. 원장(누적금액·누적수량)에서 매번 유도한다.
    수량이 0이면 평단은 없다 — 0 이 아니라 None 이다.
    """
    if qty <= 0:
        return None
    return deployed / qty
