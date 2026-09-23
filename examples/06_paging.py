"""연속 조회 — 마지막 페이지까지 이어 받는다.

  python examples/06_paging.py

rsp_cd 가 "5762" 면 다음 페이지가 있다는 뜻이다. **오류가 아니다.**

순회는 paginate() 가 한다. 직접 돌리면 세 가지를 틀리기 쉽다.
  · 연속조회 키의 오른쪽 공백을 잘라내면 다른 구간이 오거나 조용히 0건이 온다
  · 서버가 키를 바꾸지 않으면 같은 구간이 영원히 돌아온다
  · 명세에 tr_cont 가 있어도 실제로 연속조회가 되는 API 는 네 건뿐이다
"""
import datetime

from meritz import MeritzError, client, load_catalog, paginate

cat, c = load_catalog(), client()
api = cat.get("transactions")

today = datetime.date.today()
params = {"from": (today - datetime.timedelta(days=30)).strftime("%Y%m%d"),
          "to": today.strftime("%Y%m%d")}

try:
    res = paginate(c, api, params)
except MeritzError as e:
    raise SystemExit(f"호출 실패: {e}") from None

print(f"{res['pages']}페이지 · {len(res['rows'])}건")

# truncated 는 **끝까지 받지 못했다**는 뜻이다. 건수를 합계로 쓰기 전에 봐야 한다.
if res["truncated"]:
    print(f"※ {res['note']}")
