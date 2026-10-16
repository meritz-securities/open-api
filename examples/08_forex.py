"""환율 조회.

  python examples/08_forex.py

환전 신청(fx_exchanges)은 **자금 이동**이라 이 예제에 넣지 않았다.
필요하면 05_order.py 와 같은 확인 절차를 거친다.
"""
import datetime

from meritz import MeritzError, client, load_catalog

cat, c = load_catalog(), client()

try:
    res = c.call(cat.get("fx_rates"),
                 {"stnd_date": datetime.date.today().strftime("%Y%m%d")})
except MeritzError as e:
    raise SystemExit(f"호출 실패: {e}") from None
if not res["ok"]:
    raise SystemExit(f"{res.get('error')} — {res.get('message')}")

data = (res["body"].get("data") if isinstance(res["body"], dict) else None) or []
# stnd_exrt 기준환율 · tltr_slrt 전신환 매도 · tltr_byrt 전신환 매수
for row in (data if isinstance(data, list) else [data])[:8]:
    print(f"  {row.get('crcd'):<5} {row.get('crcd_kor_name'):<10} "
          f"기준 {row.get('stnd_exrt')}  매도 {row.get('tltr_slrt')}  "
          f"매수 {row.get('tltr_byrt')}")
