"""일봉 캔들 로더 + 데모용 샘플 데이터.

- 실데이터: market_candles_days(국내 일봉)로 받아 표준 캔들 dict 리스트로 변환.
- 데모: 네트워크 없이 지표를 바로 돌려볼 수 있는 합성 캔들(sample_candles).

캔들 dict 스키마 (과거→현재 순 정렬):
    {"date","open","high","low","close","volume","frgn_ntby","orgn_ntby"}

한 번 호출에 오는 봉 수가 상한이다. 더 과거는 받을 수 없다.
200일선처럼 긴 지표는 캔들이 모자라면 계산되지 않는다.
"""
from __future__ import annotations

import math


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def parse_candles(data) -> list[dict]:
    """market_candles_days 의 data 를 표준 캔들 리스트로 바꾼다.

    응답 필드는 2026-08-28 실호출로 확인했다 —
    date · oprc · hprc · lprc · stck_clpr · acml_vol · frgn_ntby_qty · orgn_ntby_vol
    """
    rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    candles = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        candles.append({
            "date": str(r.get("date", "")),
            "open": _to_float(r.get("oprc")),
            "high": _to_float(r.get("hprc")),
            "low": _to_float(r.get("lprc")),
            "close": _to_float(r.get("stck_clpr")),
            "volume": _to_float(r.get("acml_vol")),
            "frgn_ntby": _to_float(r.get("frgn_ntby_qty")),   # 외국인 순매수수량
            "orgn_ntby": _to_float(r.get("orgn_ntby_vol")),   # 기관 순매수거래량
        })
    candles.sort(key=lambda c: c["date"])   # 과거→현재
    return candles


def load_candles(iscd: str, mkt: str = "J") -> list[dict]:
    """실서버에서 일봉을 받아 캔들 리스트로 반환한다. (앱키·네트워크 필요)

    **한 번만 호출한다.** 이 API 는 연속조회를 지원하지 않는다 —
    카탈로그에 tr_cont·tr_cont_key 를 선언하지 않고, 응답 rsp_cd 도
    손상돼 오므로 페이지 끝을 코드로 판정할 수 없다(corrections.json).
    받을 수 있는 봉 수가 상한이며 더 과거는 받을 수 없다.

    당일 봉은 장중에도 장 종료 후에도 생성되지 않는다. 목록의 마지막은
    언제나 직전 영업일이다. 당일 값이 필요하면 현재가 조회를 쓴다.
    """
    from meritz import client, load_catalog  # 지연 임포트(데모는 네트워크 불필요)

    cat, c = load_catalog(), client()
    api = cat.get("market_candles_days")
    params = {"mrkt_div_code": mkt, "iscd": iscd}
    by_date: dict[str, dict] = {}

    res = c.call(api, params)
    if not res["ok"]:
        raise RuntimeError(f"{res.get('error')}: {res.get('message')}")
    body = res["body"]
    if isinstance(body, dict):
        for candle in parse_candles(body.get("data")):
            by_date[candle["date"]] = candle

    return [by_date[d] for d in sorted(by_date)]


def sample_candles(n: int = 140) -> list[dict]:
    """네트워크 없이 지표를 시연하기 위한 합성 일봉(결정적, 재현 가능).

    하락 후 반등하는 파형이라 골든크로스·RSI 반전·밴드 이탈이 실제로 발생한다.
    수급(외국인·기관 순매수)은 당일 등락 방향을 따르게 만들어 수급 관측도 시연된다.
    """
    candles: list[dict] = []
    prev_close = 50000.0
    for i in range(n):
        close = 50000 + 5000 * math.sin(i / 15.0) - 3000 * math.cos(i / 9.0) + i * 25
        change = close - prev_close
        candles.append({
            "date": f"2026{(i // 30 + 1):02d}{(i % 30 + 1):02d}",
            "open": round(prev_close, 1),
            "high": round(max(prev_close, close) * 1.008, 1),
            "low": round(min(prev_close, close) * 0.992, 1),
            "close": round(close, 1),
            "volume": 1_000_000 + (i * 37 % 500) * 1000,
            "frgn_ntby": round(change * 12, 0),     # 등락 방향을 따르는 합성 수급
            "orgn_ntby": round(change * 8, 0),
        })
        prev_close = close
    return candles


def closes(candles: list[dict]) -> list[float]:
    return [c["close"] for c in candles]
