"""분할매수의 보유 상태.

**상태의 원천은 계좌 잔고다.** 이 계산기는 직전 회차가 전량 체결됐다고 가정한다.
미체결이나 부분체결이 있으면 다음 호출의 평단·회차가 전부 어긋난다. 실제로
운용한다면 매 회차 잔고를 다시 읽어 상태를 만들어야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from position_sizing.money import average_price


@dataclass
class Position:
    """분할매수 진행 상태. 금액은 원, 수량은 주.

    budget   총 예산. **전부 잃어도 되는 금액**이어야 한다
    splits   분할 회차 수. 기본값을 두지 않는다 — 특정 회차 수가 권장값으로
             읽히면 안 되기 때문이다. 호출자가 반드시 정해야 한다
    deployed 지금까지 투입한 누적 금액
    qty      지금까지 매수한 누적 수량
    rounds   지금까지 진행한 회차 수
    source   이 상태를 어디서 얻었나 — 오차 추적용
    """
    budget: float
    splits: int
    deployed: float = 0.0
    qty: float = 0.0
    rounds: int = 0
    source: str = "사용자 입력"

    @property
    def average(self) -> float | None:
        """평단. 수량이 0이면 None — 아직 평단이 없다."""
        return average_price(self.deployed, self.qty)

    @property
    def remaining(self) -> float:
        """잔여 예산. 외부에서 어긋난 상태가 들어와도 음수가 되지 않는다."""
        return max(0.0, self.budget - self.deployed)

    def after_buy(self, qty: float, cost: float) -> Position:
        """이번 회차를 반영한 새 상태. 원본을 바꾸지 않는다."""
        return Position(budget=self.budget, splits=self.splits,
                        deployed=self.deployed + cost, qty=self.qty + qty,
                        rounds=self.rounds + 1, source=self.source)


def sample_position(budget: float, splits: int) -> Position:
    """데모용 시작 상태 — 아직 한 주도 사지 않은 상태."""
    return Position(budget=budget, splits=splits, source="데모")
