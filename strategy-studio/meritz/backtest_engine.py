"""Bounded long-only daily SMA simulation. No code execution or trading API calls."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

ENGINE_VERSION = "sma-shared-2"
DEFAULTS = {"fast": 5, "slow": 20, "initialCash": 1_000_000, "feeBps": 15, "slippageBps": 10}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate(candles, settings):
    if not isinstance(settings, dict) or set(settings) != set(DEFAULTS):
        raise ValueError("전략 설정 항목을 확인하십시오.")
    fast, slow = settings["fast"], settings["slow"]
    if type(fast) is not int or type(slow) is not int or not 2 <= fast < slow <= 120:
        raise ValueError("이동평균 기간은 정수이며 2 ≤ fast < slow ≤ 120이어야 합니다.")
    if not isinstance(candles, list) or not slow + 2 <= len(candles) <= 5000:
        raise ValueError("시세는 slow + 2개 이상, 5,000개 이하여야 합니다.")
    if not all(_number(settings[key]) for key in ("initialCash", "feeBps", "slippageBps")):
        raise ValueError("설정값은 유한한 숫자여야 합니다.")
    if not 1 <= settings["initialCash"] <= 1e12 or not 0 <= settings["feeBps"] <= 1000 or not 0 <= settings["slippageBps"] <= 1000:
        raise ValueError("초기 자금 또는 비용 설정이 지원 범위를 벗어났습니다.")
    previous = ""
    for bar in candles:
        try:
            date = bar["date"]
            if not isinstance(date, str) or dt.date.fromisoformat(date).isoformat() != date or date <= previous:
                raise ValueError
            for key in ("open", "high", "low", "close"):
                if not _number(bar[key]) or not 0.01 <= bar[key] <= 1e9:
                    raise ValueError
            if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("일봉 날짜·가격·순서를 확인하십시오.") from None
        previous = date


def demo_candles():
    candles = []
    day = dt.date(2025, 1, 2)
    while len(candles) < 160:
        if day.weekday() < 5:
            i = len(candles)
            close = round(100 + i * .15 + 12 * math.sin(i / 9) + 4 * math.cos(i / 3), 2)
            opening = round((candles[-1]["close"] if candles else 100) + math.sin(i * 1.7) * 1.5, 2)
            candles.append({"date": day.isoformat(), "open": opening, "close": close,
                            "high": round(max(opening, close) + 1.25, 2), "low": round(min(opening, close) - 1.25, 2)})
        day += dt.timedelta(days=1)
    return candles


def run_backtest(candles, settings, source=None):
    """Legacy SMA API adapted to the shared broker; no second accounting loop.

    Public SMA settings and strategy shape remain. Engine/run hashes intentionally
    change because the broker implementation is versioned shared-broker-2.
    """
    from .strategy_engine import run_strategy
    _validate(candles, settings)
    fast, slow = settings['fast'], settings['slow']
    previous_difference = None
    def decide(observation):
        nonlocal previous_difference
        history = observation['history']
        if len(history) < slow:
            return {'action':'hold'}
        fast_mean = sum(b['close'] for b in history[-fast:]) / fast
        slow_mean = sum(b['close'] for b in history[-slow:]) / slow
        difference = fast_mean - slow_mean
        shares = observation['portfolio']['shares']
        side = None
        if previous_difference is not None:
            side = 'buy' if previous_difference <= 0 < difference and shares == 0 else (
                'sell' if previous_difference >= 0 > difference and shares > 0 else None)
        previous_difference = difference
        if side is None:
            return {'action':'hold'}
        direction = '상향' if side == 'buy' else '하향'
        reason = (f'{observation["date"]} 종가 기준 SMA({fast})={fast_mean:.4f}, SMA({slow})={slow_mean:.4f}: '
                  f'{direction} 교차. 다음 봉 시가에 비용을 반영한 가상 체결.')
        return {'action':side, 'reason':reason, 'evidence':{'fastSma':fast_mean,'slowSma':slow_mean}}
    code_hash = hashlib.sha256(json.dumps({'adapter':'sma-shared-2','fast':fast,'slow':slow},sort_keys=True).encode()).hexdigest()
    report = run_strategy(candles, {k:settings[k] for k in ('initialCash','feeBps','slippageBps')}, decide,
                          source, {'name':f'SMA {fast}/{slow}','codeHash':code_hash}, evidence_origin='host-computed')
    report['strategy'] = {'fast':fast,'slow':slow}
    # These explanations are computed by the trusted built-in adapter.
    for trade in report['trades']:
        trade['reason'] = trade['reason'].removeprefix('사용자 코드가 제공한 설명(독립 검증 안 됨): ')
    report['warnings'] = [w for w in report['warnings'] if not w.startswith('거래 설명과 evidence')]
    report['assumptions']['fillRule'] = '종가 교차 신호 → 다음 봉 시가 ± 슬리피지 · 정수 수량 · 매수/매도 수수료'
    return report


def run_demo(fast=5, slow=20):
    return run_backtest(demo_candles(), {**DEFAULTS, "fast": fast, "slow": slow})
