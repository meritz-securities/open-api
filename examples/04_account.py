"""계좌 조회 — 예수금·잔고·평가.

  python examples/04_account.py

**계좌번호를 넣지 않는다.** 앱키에 계좌가 묶여 있어 게이트웨이가 채운다.
acno·asno·acnt_pwd 를 보내면 오히려 실패한다.
"""
from meritz import MeritzError, client, load_catalog

cat, c = load_catalog(), client()


def show(key, title):
    try:
        res = c.call(cat.get(key), {})
    except MeritzError as e:
        print(f"{title}: 호출 실패 — {e}")
        return
    if not res["ok"]:
        print(f"{title}: {res.get('error')} — {res.get('message')}")
        return
    data = (res["body"].get("data") if isinstance(res["body"], dict) else None)
    if isinstance(data, list):
        print(f"{title}: {len(data)}건")
        for row in data[:5]:
            print(f"   {row.get('isnm') or row.get('iscd') or row}")
    elif isinstance(data, dict):
        print(f"{title}")
        for k, v in list(data.items())[:6]:
            print(f"   {k:<24} {v}")
    else:
        print(f"{title}: 자료 없음")


show("deposit", "예수금")
show("valuation", "계좌 평가")
show("holdings", "보유 종목")
