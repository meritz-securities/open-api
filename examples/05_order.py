"""주문 — 확인 없이는 전송되지 않는 형태.

  python examples/05_order.py

**그대로 실행해도 주문이 나가지 않는다.** 미리보기까지만 한다.
send() 를 부르려면 호출하는 쪽이 사용자 동의를 받아 confirm_token 을 넘겨야 한다.

주문·환전은 MERITZ_READ_ONLY=0 으로 열어야 나간다. 기본값은 조회 전용이다.

주문 함수를 "인자만 받고 바로 전송" 하는 모양으로 만들지 않는 것이 요점이다.
실수 한 번으로 주문이 나가기 때문이다.
"""
from meritz import client, load_catalog, settings
from meritz.core import safety

cat = load_catalog()
api = cat.get("orders_buy")


def preview(body):
    """보낼 내용과 confirm_token 을 돌려준다. 전송하지 않는다."""
    # 앱키까지 넘긴다 — 계좌를 정하는 것이 앱키다. 확인한 계좌와
    # 다른 계좌로 전송되는 일을 지문이 막는다.
    return safety.preview(api, body, settings().base_url, settings().app_key)


def send(body, confirm_token):
    """사용자 동의를 받은 뒤에만 부른다.

    확인 게이트는 ApiClient.call() 안에 있다. 여기서 따로 검사하지 않는다 —
    토큰은 1회용이라 두 번 보면 두 번째가 실패한다. 토큰이 없거나 맞지 않으면
    call() 이 MeritzError(code="NEEDS_CONFIRMATION") 를 올린다.
    """
    res = client().call(api, {**body, "confirm_token": confirm_token})

    # 접수되지 않은 주문을 성공으로 넘기지 않는다.
    # rsp_cd 가 "0001"("주문이 완료되었습니다")여도 warn_cls_code 가 경고면
    # 시장에는 아무것도 나가 있지 않다. 판정은 core 가 한다.
    if res.get("error") == "ORDER_NOT_ACCEPTED":
        raise RuntimeError(res["message"])
    if not res["ok"]:
        raise RuntimeError(f"{res.get('error')}: {res.get('message')}")
    return res


if __name__ == "__main__":
    # 주문의 종목코드는 "A" + 6자리다. 시세(005930)와 다르다.
    body = {"iscd": "A005930", "odqt": "1", "oder_unpr": "50000",
            "oder_cls_code": "01",       # 01 지정가 / 05 시장가
            "oder_cond_cls_code": "0",   # 0 일반 / 3 IOC / 4 FOK
            "orgl_oder_no": "0",         # 신규에는 원주문이 없다
            "whol_rctf_cncl_yn": "N",    # 신규에는 해당 없음
            "warn_cnfr_yn": "N",         # 경고가 오면 "Y" 로 재전송
            "exch_kind_code": "01"}      # 01 KRX / 02 NXT

    pv = preview(body)
    print(f"보낼 곳  {pv['will_send']['method']} {pv['will_send']['url']}")
    for k, v in body.items():
        print(f"  {k:<20} {v}")
    print(f"\n{pv['note']}")
    print(f"\n전송하지 않았습니다. 동의를 받은 뒤 {pv['expires_in_sec']}초 안에")
    print(f"  send(body, {pv['confirm_token']!r})")
