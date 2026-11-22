"""지수 시세 조회 — 현재가 · 일봉 · 분봉.

  python examples/09_index.py              # 코스피
  python examples/09_index.py NAS@IXIC     # 해외 지수는 코드에 @ 가 들어 있다

지수 코드는 종목코드와 다르다. 없는 코드를 넣어도 오류가 나지 않고, 정상 응답(200 ·
rsp_cd 0000)에 모든 필드가 0 이거나 빈 값으로 온다 — 이름이 비어 있으면 코드를 의심한다.

  국내  KGG01P 코스피 · QGG01P 코스닥 · K2G01P 코스피 200 · XGG01P KRX 100
  해외  NAS@IXIC 나스닥 종합 · DJI@DJI 다우 산업 · SPI@SPX S&P 500
        NAS@SOX 필라델피아 반도체 · NII@NI225 니케이 225 · HSI@HSI 항셍
        SHS@000001 상해종합

분봉의 봉 간격 hour_cls_code 는 **초** 단위다 (60 = 1분봉, 300 = 5분봉). 1 이나 5 를
넣으면 1초·5초 간격으로 읽혀 거의 비어 온다.

분봉 응답의 cntg_hour 가 888888 이면 장마감 행, 999999 이면 시간외 행이다. 분봉이 아니므로
봉 계산에서는 뺀다.
"""
import sys

from meritz import MeritzError, client, load_catalog

iscd = sys.argv[1] if len(sys.argv) > 1 else "KGG01P"
overseas = "@" in iscd

cat, c = load_catalog(), client()


def call(domestic, abroad, **params):
    """API 하나를 부르고 data 를 돌려준다. 실패하면 사유를 말하고 멈춘다."""
    api = cat.get(abroad if overseas else domestic)
    try:
        res = c.call(api, {"iscd": iscd, **params})
    except MeritzError as e:
        raise SystemExit(f"호출 실패: {e}") from None
    if not res["ok"]:
        raise SystemExit(f"{res.get('error')} — {res.get('message')}")
    return res["body"].get("data") if isinstance(res["body"], dict) else None


# 1) 현재가 — data 는 한 건이다
d = call("market_index_prices", "ovs_market_index_prices") or {}
if not d.get("kor_isnm"):
    raise SystemExit(f"{iscd}: 이름이 비어 있습니다. 지수 코드를 확인하십시오.")
print(f"{d.get('kor_isnm')}  {d.get('stck_prpr')}  전일대비 {d.get('prdy_vrss')} ({d.get('prdy_ctrt')}%)")

# 2) 일봉 — period_cls_code: D 일 · W 주 · M 월 · Y 연. 최신 봉이 먼저 온다.
#    from·to 는 선택이다. 기간으로 조회할 때는 둘을 함께 넣는다.
print("\n일봉")
for row in (call("market_index_candles_days", "ovs_market_index_candles_days", period_cls_code="D") or [])[:5]:
    print(f"  {row.get('date')}  시 {row.get('oprc')}  고 {row.get('hprc')}  저 {row.get('lprc')}  종 {row.get('prpr')}")

# 3) 분봉 — hour_cls_code 는 초 단위. 888888(장마감)·999999(시간외) 행은 봉이 아니다.
print("\n분봉 (5분)")
bars = [r for r in (call("market_index_candles_minutes", "ovs_market_index_candles_minutes", hour_cls_code="300") or [])
        if r.get("cntg_hour") not in ("888888", "999999")]
for row in bars[:5]:
    print(f"  {row.get('date')} {row.get('cntg_hour')}  종 {row.get('prpr')}  거래량 {row.get('cntg_vol')}")
