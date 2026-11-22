#!/usr/bin/env python3
"""35개 전 템플릿 불변식 전수 검증 (합성 일봉 160봉, `meritz.backtest_engine.demo_candles`). 네트워크 호출 없음.

① 회계 항등식: finalEquity == initial + realizedPnl + unrealizedPnl
② finalPortfolio 정합: finalEquity == cash + shares*lastClose
③ 체결 인과성(체결된 주문만): fillDate 가 signalDate 보다 뒤. **signalDate/date 누락·미등록 날짜는 실패**(건너뛰지 않는다)
④ 마지막 봉 '신호'는 체결되지 않는다(만료). signalDate==lastDate 인데 filled 면 실패
⑤ 결정성: 2회 실행 resultHash 동일

검사 생략을 성공으로 처리하지 않는다: 템플릿 수가 EXPECTED_TEMPLATES 와 다르거나 체결 주문이 0건이면 실패.
`--selftest` 는 결함 입력(날짜 누락 체결 / 동일봉 선취 / 마지막봉 신호 체결 / 항등식 위반 / 템플릿 0개)을 주입해
검증기가 실제로 실패하는지 확인한다(음성 검증).
"""
import copy, tempfile, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

EXPECTED_TEMPLATES = 35
INIT = 10_000_000
SETTINGS = {"initialCash": INIT, "feeBps": 15, "slippageBps": 5, "taxBps": 23}


def tol(a, b, t=1e-6):
    return abs(a - b) <= t * max(1, abs(b))


def check_run(rep, *, init, last_close, last_date, date_idx):
    """하나의 실행 report 에 대한 불변식 위반 목록과 검사한 체결 주문 수를 돌려준다."""
    errs = []
    m = rep["metrics"]; fp = rep["finalPortfolio"]; orders = rep.get("orders", [])
    if not tol(m["finalEquity"], init + m["realizedPnl"] + m["unrealizedPnl"]):
        errs.append(f"항등식 finalEquity({m['finalEquity']}) != init+realized+unrealized")
    if not tol(m["finalEquity"], fp["cash"] + fp["shares"] * last_close):
        errs.append("포트폴리오 finalEquity != cash+shares*lastClose")
    filled = [o for o in orders if o.get("status") == "filled"]
    for o in filled:
        sd, fd = o.get("signalDate"), o.get("date")
        if not sd or not fd:
            errs.append(f"체결 주문 {o.get('id')} 에 signalDate/date 누락 (sd={sd}, fd={fd})"); continue
        if sd not in date_idx or fd not in date_idx:
            errs.append(f"체결 주문 {o.get('id')} 날짜가 데이터에 없음 (sd={sd}, fd={fd})"); continue
        if date_idx[fd] <= date_idx[sd]:
            errs.append(f"인과성 위반(체결) {o.get('id')}: signal={sd} fill={fd}")
        if sd == last_date:
            errs.append(f"마지막 봉 신호가 체결됨(선취) {o.get('id')}")
    return errs, len(filled)


def check_template_count(count):
    return [] if count == EXPECTED_TEMPLATES else [f"템플릿 수 {count} != {EXPECTED_TEMPLATES} (검사 대상 불일치)"]


def main(selftest=False):
    from meritz.catalog import load_catalog
    from runtime.service import StrategyRuntime
    from meritz.backtest_engine import demo_candles
    bars = demo_candles()  # 결정적 합성 일봉 160봉. 실제 종목 시세가 아니다.
    last_close = bars[-1]["close"]; last_date = bars[-1]["date"]
    date_idx = {b["date"]: i for i, b in enumerate(bars)}
    rt = StrategyRuntime(root=tempfile.mkdtemp(), catalog=load_catalog())
    ds = rt.import_data(bars, {"kind": "imported", "label": "합성 일봉 160봉", "currency": "KRW"})
    by = {d["name"]: d for d in rt.templates()["templates"]}
    ctx = dict(init=INIT, last_close=last_close, last_date=last_date, date_idx=date_idx)

    fails = list(check_template_count(len(by))); ok = 0; filled_total = 0
    for name in sorted(by):
        rev = rt.save_strategy(by[name])
        run = rt.run(rev["id"], ds["id"], SETTINGS)
        if run["status"] != "succeeded":
            fails.append(f"{name}: status={run['status']}"); continue
        errs, n_filled = check_run(run["report"], **ctx)
        filled_total += n_filled
        run2 = rt.run(rev["id"], ds["id"], SETTINGS)
        if run["manifest"]["resultHash"] != run2["manifest"]["resultHash"]:
            errs.append("resultHash 비결정적")
        if errs: fails.append(f"{name}: " + " | ".join(errs))
        else: ok += 1
    if filled_total == 0:
        fails.append("체결 주문 0건 — 인과성 검사가 수행되지 않음")

    print(f"==== {EXPECTED_TEMPLATES}템플릿 불변식: {ok} PASS / {len(fails)} FAIL  (템플릿 {len(by)}개 검사, 체결 주문 {filled_total}건 검사) ====")
    for f in fails: print(" ✗", f)
    if not fails: print("  ① 회계항등식 ② 포트폴리오정합 ③ 체결인과성(누락 날짜는 실패) ④ 마지막봉 신호 미체결 ⑤ 결정성 — 전건 통과")

    if selftest:
        # 음성 검증: 결함을 넣으면 검증기가 실패하는지. 실제 report 를 복제해 변조한다(파일·저장소 변경 없음).
        base = rt.run(rt.save_strategy(by["SMA 5/20 교차"])["id"], ds["id"], SETTINGS)["report"]
        bad = {}
        bad["날짜누락"] = copy.deepcopy(base); bad["날짜누락"]["orders"][0].pop("signalDate", None)
        bad["동일봉선취"] = copy.deepcopy(base); bad["동일봉선취"]["orders"][0]["signalDate"] = bad["동일봉선취"]["orders"][0]["date"]
        bad["마지막봉체결"] = copy.deepcopy(base); bad["마지막봉체결"]["orders"][0]["signalDate"] = last_date; bad["마지막봉체결"]["orders"][0]["date"] = last_date
        bad["미등록날짜"] = copy.deepcopy(base); bad["미등록날짜"]["orders"][0]["date"] = "1999-01-01"
        # 항등식 허용오차는 상대 1e-6(≈ 최종자산 5.9e6 기준 5.9원, IEEE 부동소수 누적 반올림 허용). 주입은 그보다 분명히 큰 1,000원.
        bad["항등식위반"] = copy.deepcopy(base); bad["항등식위반"]["metrics"]["finalEquity"] += 1000.0
        caught = {k: bool(check_run(r, **ctx)[0]) for k, r in bad.items()}
        caught["템플릿0개"] = bool(check_template_count(0))
        caught["템플릿34개"] = bool(check_template_count(34))
        all_caught = all(caught.values())
        print("==== selftest(음성 검증): " + ("PASS" if all_caught else "FAIL") + " — " +
              ", ".join(f"{k}:{'잡음' if v else '놓침'}" for k, v in caught.items()))
        if not all_caught: fails.append("selftest 실패: 결함 입력을 통과시킴")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main(selftest="--selftest" in sys.argv)
