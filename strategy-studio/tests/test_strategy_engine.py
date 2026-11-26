import copy

import pytest

from meritz.strategy_engine import run_strategy

SETTINGS = {"initialCash": 100, "feeBps": 100, "slippageBps": 100}
INFO = {"name": "고객 전략", "codeHash": "a" * 64}
BARS = [{"date": "2025-01-01", "open": 3, "high": 3, "low": 3, "close": 3},
        {"date": "2025-01-02", "open": 4, "high": 4, "low": 1, "close": 1},
        {"date": "2025-01-03", "open": 2, "high": 2, "low": 2, "close": 2}]


def decide_round_trip(observation):
    return {"action": ("buy", "sell", "hold")[observation["index"]], "reason": "고객의 판단"}


def test_independent_round_trip_arithmetic_and_next_open():
    report = run_strategy(BARS, SETTINGS, decide_round_trip, None, INFO)
    buy, sell = report["trades"]
    assert buy["signalDate"] == "2025-01-01" and buy["date"] == "2025-01-02"
    assert buy["quantity"] == 24 and buy["price"] == pytest.approx(4.04)
    assert buy["fee"] == pytest.approx(.9696)
    assert sell["price"] == pytest.approx(1.98)
    assert report["metrics"]["totalFees"] == pytest.approx(1.4448)
    assert report["metrics"]["finalEquity"] == pytest.approx(49.1152)
    assert report["metrics"]["maxDrawdownPct"] == pytest.approx(73.9296)
    assert report["equity"][-1]["benchmark"] == pytest.approx(66.0704)
    assert report["strategy"] == {**INFO, "interface": "on_bar_v1"}
    assert "독립 검증 안 됨" in buy["reason"]


def test_only_past_history_and_detached_state():
    original = copy.deepcopy(BARS)
    observations = []

    def mutate(observation):
        observations.append(copy.deepcopy(observation))
        assert len(observation["history"]) == observation["index"] + 1
        assert observation["history"][-1]["date"] == observation["date"]
        observation["bar"]["close"] = 9999
        observation["history"][0]["open"] = 9999
        observation["portfolio"]["cash"] = 9999
        return decide_round_trip(observation)

    result = run_strategy(BARS, SETTINGS, mutate, None, INFO)
    assert BARS == original
    assert result["metrics"]["finalEquity"] == pytest.approx(49.1152)
    assert observations[1]["portfolio"]["shares"] == 24
    assert observations[2]["history"][0]["open"] == 3


def test_future_changes_do_not_change_prior_observations():
    seen_a, seen_b = [], []
    changed = copy.deepcopy(BARS)
    changed[-1].update(open=100, high=100, low=100, close=100)
    for bars, seen in ((BARS, seen_a), (changed, seen_b)):
        def decide(observation):
            seen.append(observation)
            return decide_round_trip(observation)
        run_strategy(bars, SETTINGS, decide, None, INFO)
    assert seen_a[:2] == seen_b[:2]


def test_distinct_user_strategies_produce_distinct_behavior_and_hash():
    hold = run_strategy(BARS, SETTINGS, lambda _: {"action": "hold"}, None, INFO)
    traded = run_strategy(BARS, SETTINGS, decide_round_trip, None, INFO)
    assert hold["metrics"]["finalEquity"] == 100
    assert hold["trades"] == []
    assert hold["runId"] != traded["runId"]
    assert traded == run_strategy(BARS, SETTINGS, decide_round_trip, None, INFO)
    assert traded["runId"] != run_strategy(BARS, SETTINGS, decide_round_trip, None, {**INFO, "codeHash": "b" * 64})["runId"]


def test_explicit_quantity_and_end_holdings():
    report = run_strategy(BARS[:2], SETTINGS, lambda _: {"action": "buy", "quantity": 2}, None, INFO)
    assert report["trades"][0]["quantity"] == 2
    assert report["metrics"]["finalEquity"] == pytest.approx(93.8392)
    assert any("다음 봉이 없어" in warning for warning in report["warnings"])


@pytest.mark.parametrize("bad", [None, [], {"action": "BUY"}, {"action": "buy", "quantity": 0},
    {"action": "buy", "quantity": True}, {"action": "buy", "quantity": 1.5}, {"action": "buy", "quantity": 1_000_000_001},
    {"action": "hold", "quantity": 1}, {"action": "hold", "metrics": {}}, {"action": "hold", "reason": "x" * 501},
    {"action": "hold", "reason": None}, {"action": "hold", "reason": "\x00"}, {"action": []}])
def test_malformed_worker_decisions_rejected(bad):
    with pytest.raises(ValueError):
        run_strategy(BARS, SETTINGS, lambda _: bad, None, INFO)


@pytest.mark.parametrize("action,quantity", [("buy", 25), ("sell", 1)])
def test_oversized_explicit_quantity_rejected_at_fill(action, quantity):
    with pytest.raises(ValueError, match="초과"):
        run_strategy(BARS, SETTINGS, lambda _: {"action": action, "quantity": quantity}, None, INFO)


def test_omitted_quantity_without_capacity_is_no_fill():
    report = run_strategy(BARS, SETTINGS, lambda _: {"action": "sell"}, None, INFO)
    assert report["trades"] == []
    assert report['orders'][0]['rejectionCode'] == 'ZERO_CAPACITY'
    assert all(order['action'] == 'sell' for order in report['orders'])
    assert any("2건" in warning for warning in report["warnings"])


def test_explicit_sell_without_position_is_rejected_by_default_policy():
    with pytest.raises(ValueError, match='보유 수량'):
        run_strategy(BARS, SETTINGS,
            lambda observation: {'action': 'sell', 'quantity': 1} if observation['index'] == 0 else {'action': 'hold'},
            None, INFO)


@pytest.mark.parametrize("update", [{"initialCash": True}, {"initialCash": float("nan")}, {"feeBps": -1},
                                    {"slippageBps": 1001}, {"fast": 2}])
def test_bad_settings(update):
    with pytest.raises(ValueError):
        run_strategy(BARS, {**SETTINGS, **update}, lambda _: {"action": "hold"}, None, INFO)


@pytest.mark.parametrize("update", [{"date": "2025-02-30"}, {"date": "2025-01-02"}, {"open": 0},
                                    {"close": float("inf")}, {"high": 1}, {"low": True}])
def test_bad_prices(update):
    with pytest.raises(ValueError):
        run_strategy([{**BARS[0], **update}, *BARS[1:]], SETTINGS, lambda _: {"action": "hold"}, None, INFO)


def test_bounds_and_metadata():
    for bars in (BARS[:1], [BARS[0]] * 5001):
        with pytest.raises(ValueError):
            run_strategy(bars, SETTINGS, lambda _: {"action": "hold"}, None, INFO)
    for info in ({**INFO, "codeHash": "bad"}, {**INFO, "name": ""}, {**INFO, "interface": "unknown"}):
        with pytest.raises(ValueError):
            run_strategy(BARS, SETTINGS, lambda _: {"action": "hold"}, None, info)


def test_source_provenance_and_code_hash_participate_in_result_id():
    source = {"kind": "meritz", "label": "fixture", "symbol": "005930", "market": "TJ",
              "startDate": "2025-01-01", "endDate": "2025-01-03", "fetchedAt": "2025-01-04", "adjustment": "requested-adjusted"}
    a = run_strategy(BARS, SETTINGS, decide_round_trip, source, INFO)
    b = run_strategy(BARS, SETTINGS, decide_round_trip, {**source, "fetchedAt": "2025-01-05"}, INFO)
    assert a["runId"] == b["runId"]
    assert a["source"]["kind"] == "meritz"
    assert any("수정주가를 요청" in warning for warning in a["warnings"])
    assert not any("합성 데이터" in warning for warning in a["warnings"])
