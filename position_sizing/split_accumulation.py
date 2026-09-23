"""분할매수 계산기 — 총액을 N회로 나눠 나눠 사는 방식의 산술.

널리 쓰이는 기계적 규칙만 계산한다. 예산을 회차 수로 나눠 1회 한도를 정하고,
그 한도로 살 수 있는 수량과 갱신될 평단, 평단 대비 목표가를 낸다.

    1회 한도 = 예산 / 회차수
    수량     = floor(1회 한도 / 현재가)
    평단     = 누적 매입금액 / 누적 수량
    목표가   = 평단 * (1 + 목표수익률)

**손절 규칙이 없다.** 하락이 이어지면 예산 전액이 투입되고, 그 뒤에도 하락하면
회수 수단이 남지 않는다. 최대 손실은 투입 원금 전액이다(`detail["worst_case"]`).
**평단이 내려가는 것은 손실이 주는 것이 아니다** — 평단이 내려가는 동안
평가손실 금액은 계속 커진다. 그래서 이 계산기는 평단과 평가손실을 언제나
나란히 출력한다.

**회차 수·목표수익률·예산에 기본값을 두지 않는다.** 함수 안에 상수로 박으면
그 숫자가 적정하다는 진술이 된다. 전부 호출자가 정하고, 이 코드는 정해진 값을
계산만 한다.

수익률·승률·최대낙폭을 산출하지 않는다. 내는 것은 배분금액·누적투입·누적수량·
평단, 그리고 그 평단과 짝이 되는 평가손익과 최대 손실 가능액뿐이다.

실행:  python -m position_sizing.split_accumulation                 # 데모
       python -m position_sizing.split_accumulation 005930          # 현재가 조회

⚠️ 산출값은 계획이지 주문이 아니다. 투자자문·매매권유가 아니다.
"""
from __future__ import annotations

import sys

from position_sizing.money import affordable_qty, tick_ceil
from position_sizing.notice import NOTICE, PLAN_NOTICE
from position_sizing.position import Position

# 계산에 넣지 않은 것들. detail 에 실어 보내 잊지 않게 한다.
ASSUMPTIONS = ("수수료 미반영", "세금 미반영", "슬리피지 미반영",
               "직전 회차 전량 체결 가정", "목표가는 호가단위 올림")


def plan(position: Position, price: float, target_pct: float) -> dict:
    """이번 회차에 무엇이 되는지 계산한다. 주문하지 않는다."""
    if price <= 0 or position.budget <= 0 or position.splits <= 0 or position.qty < 0:
        return {"state": "NO_SIGNAL", "reason": "예산·회차·현재가가 유효하지 않습니다",
                "detail": {}}

    avg = position.average
    split_amount = position.budget / position.splits
    # 잔여 예산을 넘겨 살 수는 없다. 마지막 회차가 한도보다 적게 남는 경우다.
    spend_cap = min(split_amount, position.remaining)
    qty = affordable_qty(spend_cap, price)
    cost = qty * price
    after = position.after_buy(qty, cost) if qty else position
    new_avg = after.average
    target = tick_ceil(new_avg * (1 + target_pct)) if new_avg else None

    # 평단과 평가손익은 언제나 함께 낸다. 평단만 보면 "내려갔으니 나아졌다"고 읽는다.
    valuation = position.qty * price
    unrealized = valuation - position.deployed

    if avg is not None and target_pct is not None and price >= tick_ceil(avg * (1 + target_pct)):
        state = "TARGET_REACHED"
        reason = f"현재가 {price:,.0f} 가 평단 {avg:,.0f} 기준 목표가에 도달"
    elif position.rounds >= position.splits:
        state = "ROUNDS_COMPLETE"
        reason = f"{position.splits}회차를 모두 진행했습니다"
    elif position.remaining < price:
        state = "BUDGET_EXHAUSTED"
        reason = f"잔여 예산 {position.remaining:,.0f} 으로 1주({price:,.0f})를 살 수 없습니다"
    elif qty == 0:
        state = "SPLIT_TOO_SMALL"
        reason = (f"1회 한도 {split_amount:,.0f} 으로 1주({price:,.0f})를 살 수 없습니다. "
                  f"회차를 {int(position.budget // price)} 이하로 줄여야 합니다")
    elif position.qty == 0:
        state = "NOT_STARTED"
        reason = f"첫 회차. {qty:,.0f}주 매수 시 평단 {new_avg:,.0f} 가 됩니다"
    else:
        state = "ACCUMULATING"
        reason = (f"{position.rounds + 1}/{position.splits} 회차. {qty:,.0f}주 추가 시 "
                  f"평단 {avg:,.0f} → {new_avg:,.0f}")

    return {
        "state": state, "reason": reason,
        "detail": {
            "price": price,
            "round": position.rounds, "splits": position.splits,
            "split_amount": round(split_amount),
            "plan_qty": qty, "plan_cost": round(cost),
            "avg": round(avg) if avg is not None else None,
            "avg_after": round(new_avg) if new_avg is not None else None,
            "target_price": target,
            # 평단과 짝이다. 따로 내지 않는다 — 평단만 보면 "내려갔으니
            # 나아졌다"로 읽힌다. 비율(%)이 아니라 금액으로만 낸다.
            "unrealized": round(unrealized),
            "deployed": round(position.deployed), "remaining": round(position.remaining),
            "progress_pct": round(position.deployed / position.budget * 100, 1),
            "worst_case": round(position.deployed + cost),   # 주가 0 가정 시 손실
            "source": position.source,
            "assumptions": list(ASSUMPTIONS),
        },
    }


def _price(iscd: str) -> float:
    """현재가 조회. 일봉의 마지막 봉은 언제나 직전 영업일이라 당일 계획에 못 쓴다."""
    from indicators.server_levels import load_quote  # 지연 임포트(데모는 망 불필요)

    quote = load_quote(iscd)
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    return float(str(quote.get("stck_prpr") or 0).replace(",", ""))


def main(argv: list[str]) -> None:
    print(NOTICE)
    print(PLAN_NOTICE)
    # 데모가 쓰는 값. 계산을 보여 주기 위한 예시이지 권장값이 아니다.
    budget, splits, target_pct = 4_000_000.0, 40, 0.10
    if len(argv) > 1:
        price = _price(argv[1])
        src = f"실데이터 {argv[1]} 현재가 {price:,.0f}"
    else:
        price = 100_000.0
        src = f"데모 가정 현재가 {price:,.0f}"

    # 1회 한도로 1주도 못 사면 표를 만들 수 없다. 0 으로 채운 표를 내는 대신
    # 왜 안 되는지와 얼마면 되는지를 말한다.
    probe = plan(Position(budget=budget, splits=splits, source="데모"), price, target_pct)
    if probe["state"] == "SPLIT_TOO_SMALL":
        need = price * splits
        print(f"\n[분할매수 계산] {src}")
        print(f"  {probe['reason']}")
        print(f"  {splits}분할을 유지하려면 예산이 {need:,.0f} 이상이어야 합니다.")
        print("  예산은 전부 잃어도 되는 금액이어야 합니다. 맞출 수 없으면 회차를 줄이십시오.")
        return

    pos = Position(budget=budget, splits=splits, source="데모")
    print(f"\n[분할매수 계산] 예산 {budget:,.0f} / {splits}분할 · {src}")
    # 한 회차만 보여 주면 "그래서 얼마 버나"로 읽힌다. 하락이 이어질 때 무엇이
    # 벌어지는지 함께 보여 준다.
    print(f"  {'회차':>4} {'가격':>10} {'수량':>5} {'평단':>10} {'평가손익':>12} {'투입누적':>12}")
    p = price
    for _ in range(5):
        r = plan(pos, p, target_pct)
        d = r["detail"]
        print(f"  {d['round'] + 1:>4} {p:>10,.0f} {d['plan_qty']:>5,.0f} "
              f"{(d['avg_after'] or 0):>10,.0f} {d['unrealized']:>12,.0f} {d['worst_case']:>12,.0f}")
        pos = pos.after_buy(d["plan_qty"], d["plan_cost"])
        p *= 0.95      # 5%씩 하락하는 구간을 가정한다
    last = plan(pos, p, target_pct)
    d = last["detail"]
    print(f"\n  지표: {last['state']}  —  {last['reason']}")
    print(f"  평단 {d['avg']:,.0f} · 평가손익 {d['unrealized']:,.0f} "
          f"· 최대손실가능 {d['worst_case']:,.0f}")
    print("\n  * 평단이 내려가는 동안 평가손실은 커집니다. 둘은 반대 방향이 아닙니다.")
    print("  * 손절 규칙이 없습니다. 하락이 이어지면 투입 원금 전액이 손실될 수 있습니다.")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
