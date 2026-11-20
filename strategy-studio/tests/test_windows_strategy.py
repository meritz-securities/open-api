"""Exercise Studio's Windows transport protocol with a protocol fixture."""
import sys

import pytest

from meritz import windows_strategy
from meritz.strategy_runner import StrategyError
from runtime.service import StrategyRuntime


@pytest.fixture
def guest(tmp_path, monkeypatch):
    # This child simulates CLI framing. It never evaluates the bootstrap or user code.
    script = tmp_path / 'fake_cli.py'
    script.write_text("""
import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
mode = (root / 'mode').read_text()
def emit(data):
    print(json.dumps(data), flush=True)
bootstrap = json.loads(input())
assert 'def main()' in bootstrap
checks = dict.fromkeys(['wasi', 'readonly', 'environment'], True)
checks['readonly'] = mode != 'failed-probe'
emit({'checks': checks})
init = json.loads(input())
assert 'def on_bar(ctx)' in init['code'] and init['sdk'] and init['indicators']
(root / 'code-sent').touch()
emit({'ready':True})
for line in sys.stdin:
    msg = json.loads(line)
    if mode == 'timeout':
        time.sleep(20)
    buy = msg['observation']['index'] == 0
    emit({'seq':msg['seq'], 'decision':{'action':'buy','quantity':1} if buy else {'action':'hold'}})
""", encoding='utf-8')
    (tmp_path / 'mode').write_text('ok')
    monkeypatch.setattr(windows_strategy, '_command', lambda memory_mib: [sys.executable, str(script)])
    monkeypatch.setattr(windows_strategy, 'available', lambda: True)
    return tmp_path


def test_windows_studio_run_reaches_shared_ledger(guest, monkeypatch):
    service = StrategyRuntime(guest / 'store')
    monkeypatch.setattr('runtime.service.platform.system', lambda: 'Windows')
    monkeypatch.setattr('meritz.strategy_runner.sys.platform', 'win32')
    # The store uses its native locking primitive; test the shared calculation
    # directly here. verify_native.py covers the Windows file lock and HTTP API.
    assert service.capabilities()['pythonStrategy']
    definition = {'schemaVersion': 1, 'name': '사용자 전략', 'kind': 'python',
                  'code': 'def on_bar(ctx): return {"action":"buy","quantity":1} if ctx["index"] == 0 else {"action":"hold"}'}
    bars = [dict(date=f'2025-01-0{i}', open=p, high=p, low=p, close=p)
            for i, p in enumerate([10, 20, 30, 40], 1)]
    settings = {'initialCash': 100, 'feeBps': 0, 'taxBps': 0, 'slippageBps': 0}
    report = service._calculate(definition, {'bars': bars, 'source': {'kind':'synthetic', 'label':'fixture'}}, settings)
    assert report['metrics']['finalEquity'] == 120
    assert report['runtime']['isolation'] == 'Windows WebAssembly (WASI)'


def test_failed_probe_precedes_strategy_transfer(guest):
    (guest / 'mode').write_text('failed-probe')
    with pytest.raises(StrategyError, match='자체 검사'):
        windows_strategy.WindowsWorker(2, 1, 256)
    assert not (guest / 'code-sent').exists()


def test_unresponsive_worker_is_removed(guest):
    (guest / 'mode').write_text('timeout')
    worker = windows_strategy.WindowsWorker(4, .2, 256)
    try:
        worker.send({'code': 'def on_bar(ctx): pass', 'sdk': 'sdk', 'indicators': 'indicators'})
        assert worker.receive() == {'ready': True}
        worker.send({'seq': 0, 'observation': {'index': 0}})
        with pytest.raises(StrategyError, match='시간 제한'):
            worker.receive()
    finally:
        worker.close()
    assert worker.process.poll() is not None
