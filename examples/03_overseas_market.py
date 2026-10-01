"""해외 시세 조회.

  python examples/03_overseas_market.py AAPL OQ

거래소 코드 — OQ 나스닥 / NY 뉴욕 / AX 아멕스 / HK 홍콩 / SH 상하이 / SZ 심천 / JP 도쿄

mrkt_div_code 와 dely_rltm_cls_code 는 필수다. 예전에는 포털 요청 예시에서 이 둘이
빠져 있었지만 2026-09-11 자로 예시와 required 플래그가 모두 바로잡혔다
(meritz/data/corrections.json 의 resolved).
"""
import sys

from meritz import MeritzError, client, load_catalog

iscd = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
exch = sys.argv[2] if len(sys.argv) > 2 else "OQ"

cat, c = load_catalog(), client()
params = {"mrkt_div_code": "OV", "mrkt_cls_code": exch, "iscd": iscd,
          "dely_rltm_cls_code": "0"}          # "0" 실시간 / "1" 지연

try:
    res = c.call(cat.get("ovs_market_prices"), params)
except MeritzError as e:
    raise SystemExit(f"호출 실패: {e}") from None
if not res["ok"]:
    raise SystemExit(f"{res.get('error')} — {res.get('message')}")

d = (res["body"].get("data") if isinstance(res["body"], dict) else None) or {}
print(f"{d.get('kor_isnm') or iscd}  {d.get('prpr')} {d.get('crnc_code') or ''}")
