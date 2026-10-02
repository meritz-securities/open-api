"""실시간 구독 — 여러 종목·여러 서비스를 한 접속에서 함께 구독한다.

  pip install websocket-client
  python examples/07_realtime.py 005930 000660

접속점은 하나다. 무엇을 받을지는 구독 메시지의 tr_cd 가 정한다.
API 마다 경로가 나뉘지 않는다.

**종목마다 접속을 새로 열지 않는다.** 하나의 접속을 열어 두고 그 위에서
tr_cd·tr_key 를 바꿔 가며 여러 번 구독 메시지를 보낸다. 응답도 그 접속
하나로 전부 들어오고, header 의 tr_cd·tr_key 로 어느 구독의 데이터인지
가려낸다. 종목 수만큼 접속을 새로 열면(대표적으로 "종목마다 새 연결 →
확인만 하고 바로 끊기"를 반복하면) 서버 쪽 세션 정리가 접속 종료를
못 따라가 계정의 웹소켓 세션이 쌓이고, 결국 새 구독마다
EGW00002(서버 에러)로 거절되는 상태에 빠질 수 있다.

인증은 **접속이 아니라 구독 메시지**에서 이뤄진다.
"""
import json
import sys

import websocket

from meritz import load_catalog, settings
from meritz.core.client import MeritzError, TokenManager

iscd_list = sys.argv[1:] or ["005930"]
s = settings()
api = load_catalog().get("ws_stck_cntg")     # 국내주식 체결 (KRX)

# 토큰을 먼저 받는다. 접속부터 열면, 자격증명이 없을 때 소켓이 열린 채로
# 트레이스백이 난다.
try:
    token = TokenManager(s).get()
except MeritzError as e:
    raise SystemExit(f"토큰 발급 실패: {e}") from None

try:
    conn = websocket.create_connection(s.ws_url, timeout=s.timeout)
except Exception as e:
    raise SystemExit(
        f"실시간 접속 실패: {s.ws_url} — {type(e).__name__}. "
        "MERITZ_BASE_URL 을 바꾸셨다면 MERITZ_WS_URL 도 함께 바꾸셔야 합니다."
    ) from None

# 접속 하나에 종목 수만큼 구독 메시지를 순서대로 보낸다 — 접속은 새로 열지 않는다.
for iscd in iscd_list:
    conn.send(json.dumps({
        "header": {"token": f"Bearer {token}", "tr_type": "1"},   # 1 등록 / 2 해지
        "body": {"tr_cd": api["tr_id"], "tr_key": iscd},
    }))

pending_acks = len(iscd_list)
try:
    for _ in range(20 * len(iscd_list)):
        try:
            raw = conn.recv()
        except websocket.WebSocketTimeoutException:
            print(f"{s.timeout}초 동안 수신 없음. 장 시간과 종목코드를 확인하세요.")
            break
        msg = json.loads(raw)
        head = msg.get("header") or {}
        if "rsp_cd" in head:                              # 구독 응답(ACK)
            # 관측된 성공 값은 "00000" 이다. 자릿수가 REST 의 "0000" 과 늘
            # 다른지는 확인하지 못해 둘 다 성공으로 받는다.
            if head["rsp_cd"] not in ("00000", "0000"):
                raise SystemExit(f"구독 실패 {head['rsp_cd']} {head.get('rsp_msg')}")
            pending_acks -= 1
            print(f"구독 완료 ({len(iscd_list) - pending_acks}/{len(iscd_list)})")
            continue
        b = msg.get("body") or {}
        code = (b.get("shrn_iscd") or head.get("tr_key") or "").strip()
        print(f"[{code}] {b.get('bsop_hour')}  {b.get('prpr')}  {b.get('cntg_vol')}주")
finally:
    conn.close()

# 서버는 종목코드를 검사하지 않는다. 없는 종목도 구독은 성공하고
# 데이터만 오지 않는다 — 조용하면 종목코드와 장 시간을 먼저 의심한다.
