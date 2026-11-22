"""국내 시세 조회 — 현재가·호가·일봉.

  python examples/02_market.py 005930

국내 시세의 종목코드는 6자리다(주문은 "A" 접두가 붙는다 — 05_order.py 참고).
"""
import sys

from meritz import MeritzError, client, load_catalog

iscd = sys.argv[1] if len(sys.argv) > 1 else "005930"
cat, api = load_catalog(), None
c = client()


def call(key, **params):
    res = c.call(cat.get(key), dict(mrkt_div_code="J", iscd=iscd, **params))
    if not res["ok"]:
        raise SystemExit(f"{key}: {res.get('error')} — {res.get('message')}")
    body = res["body"]
    if not isinstance(body, dict):
        raise SystemExit(f"{key}: JSON 이 아닌 응답 {str(body)[:200]}")
    return body.get("data") or {}, res


try:
    price, res = call("market_prices")
    print(f"{price.get('kor_isnm') or iscd}  {price.get('stck_prpr')}원  "
          f"전일대비 {price.get('prdy_vrss')} ({price.get('prdy_ctrt')}%)")

    # 호가 필드는 1호가에 번호가 없고 2호가부터 번호가 붙는다 (askp / askp2 …)
    book, _ = call("market_orderbook")
    print(f"  매도1 {book.get('askp')}  매수1 {book.get('bidp')}")

    days, _ = call("market_candles_days")
    rows = days if isinstance(days, list) else [days]
    print(f"  일봉 {len(rows)}건")
except MeritzError as e:
    raise SystemExit(f"호출 실패: {e}") from None

# 이 API 들은 응답 코드로 성공을 판정할 수 없다(meritz/data/corrections.json).
# 값은 정상이므로 data 유무로 판정한다.
if res.get("verified") is False:
    print("\n※ 이 API 는 응답 코드 대신 data 로 성공을 판정합니다. 값은 정상입니다.")
