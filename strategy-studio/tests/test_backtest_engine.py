import copy

import pytest

from meritz.backtest_engine import demo_candles, run_backtest, run_demo

SETTINGS = {"fast": 2, "slow": 3, "initialCash": 100, "feeBps": 100, "slippageBps": 100}
CLOSES = [3, 2, 1, 2, 3, 2, 1, 2]
BARS = [{"date": f"2025-01-{i + 1:02d}", "open": 4 if i == 5 else c,
         "close": c, "high": max(4 if i == 5 else c, c), "low": c} for i, c in enumerate(CLOSES)]


def test_independent_trade_arithmetic():
    r = run_backtest(BARS, SETTINGS)
    assert len(r["trades"]) == 2
    first = r["trades"][0]
    assert first["signalDate"] == "2025-01-05"
    assert first["date"] == "2025-01-06"
    assert first["quantity"] == 24
    assert first["price"] == pytest.approx(4.04)
    assert first["fee"] == pytest.approx(.9696)
    assert r["trades"][1]["price"] == pytest.approx(1.98)
    assert r["metrics"]["totalFees"] == pytest.approx(1.4448)
    assert r["metrics"]["finalEquity"] == pytest.approx(49.1152)
    assert r["metrics"]["totalReturnPct"] == pytest.approx(-50.8848)
    assert r["metrics"]["maxDrawdownPct"] == pytest.approx(73.9296)
    assert r["equity"][-1]["benchmark"] == pytest.approx(66.0704)


def test_no_future_prices_affect_past():
    a = run_backtest(BARS, SETTINGS)
    changed = copy.deepcopy(BARS)
    changed[-1].update(open=100, high=100, low=100, close=100)
    b = run_backtest(changed, SETTINGS)
    assert a["equity"][:-1] == b["equity"][:-1]
    assert a["trades"][0] == b["trades"][0]
    assert a["trades"][1]["signalDate"] == b["trades"][1]["signalDate"]


def test_last_signal_and_mark_to_market():
    r = run_backtest(BARS[:-1], SETTINGS)
    assert len(r["trades"]) == 1
    assert r["metrics"]["finalEquity"] == pytest.approx(26.0704)
    assert any("다음 봉이 없어" in w for w in r["warnings"])


def test_flat_and_unaffordable():
    flat = [{**bar, "open": 5, "high": 5, "low": 5, "close": 5} for bar in BARS]
    assert run_backtest(flat, SETTINGS)["trades"] == []
    assert run_backtest(flat, SETTINGS)["metrics"]["finalEquity"] == 100
    assert run_backtest(BARS, {**SETTINGS, "initialCash": 1})["trades"] == []


def test_reproducibility_and_source():
    assert run_demo() == run_demo()
    assert run_demo()["runId"] != run_demo(7, 25)["runId"]
    assert len(demo_candles()) == 160
    source = {"kind": "meritz", "label": "fixture", "symbol": "005930", "market": "TJ",
              "startDate": "2025-01-01", "endDate": "2025-01-08", "fetchedAt": "2025-01-09",
              "adjustment": "requested-adjusted"}
    a = run_backtest(BARS, SETTINGS, source)
    b = run_backtest(BARS, SETTINGS, {**source, "fetchedAt": "2025-01-10"})
    assert a["runId"] == b["runId"]
    assert not any("합성 데이터" in w for w in a["warnings"])
    assert any("수정주가를 요청" in w for w in a["warnings"])
    assert a["runId"] != run_backtest(BARS, SETTINGS, {**source, "adjustment": "unadjusted"})["runId"]


@pytest.mark.parametrize("update", [{"fast": 3}, {"fast": True}, {"fast": 2.5}, {"slow": 121},
                                    {"initialCash": float("nan")}, {"feeBps": -1}, {"slippageBps": float("inf")}])
def test_bad_settings(update):
    with pytest.raises(ValueError):
        run_backtest(BARS, {**SETTINGS, **update})


@pytest.mark.parametrize("update", [{"date": "2025-02-30"}, {"date": "2025-01-02"}, {"open": 0},
                                    {"high": 1}, {"close": float("nan")}, {"open": True}])
def test_bad_candles(update):
    with pytest.raises(ValueError):
        run_backtest([{**BARS[0], **update}, *BARS[1:]], SETTINGS)


def test_resource_bounds():
    with pytest.raises(ValueError):
        run_backtest(BARS[:3], SETTINGS)
    with pytest.raises(ValueError):
        run_backtest([BARS[0]] * 5001, SETTINGS)
