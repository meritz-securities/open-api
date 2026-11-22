import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from meritz import strategy_runner as runner
from meritz.backtest_engine import demo_candles
from meritz.strategy_engine import run_strategy, DEFAULTS


def test_validation_does_not_execute_and_reports_line():
    result=runner.validate_code('def on_bar(ctx)\n    pass')
    assert not result['valid'] and result['errors'][0]['line']==1
    assert not runner.validate_code('print("hello")')['valid']
    assert not runner.validate_code('async def on_bar(ctx): pass')['valid']
    assert not runner.validate_code('def on_bar(a,b): pass')['valid']
    assert runner.validate_code('raise Exception("must not execute")\ndef on_bar(ctx):pass')['valid']
    assert not runner.validate_code('def on_bar(ctx):\n #\ud800\n pass')['valid']


def test_unsupported_platform_fails_closed():
    with patch.object(runner.sys,'platform','linux'), patch.object(runner.subprocess,'Popen') as spawn:
        with pytest.raises(runner.StrategyError):runner.StrategySession('def on_bar(ctx): return {"action":"hold"}')
        spawn.assert_not_called()


def test_service_rejects_python_before_persisting_run_on_unsupported_platform(tmp_path, monkeypatch):
    import runtime.service as service_module
    from runtime.service import StrategyRuntime

    service=StrategyRuntime(tmp_path,catalog=None)
    revision=service.save_strategy({'schemaVersion':1,'kind':'python','name':'플랫폼 차단 확인',
                                    'code':'def on_bar(ctx):\n return {"action":"hold"}'})
    dataset=service.demo_data()
    monkeypatch.setattr(service_module.platform,'system',lambda:'Linux')
    with pytest.raises(ValueError,match='unsupported_platform'):
        service.run(revision['id'],dataset['id'])
    assert service.store.list('run') == []


def test_onefile_bundle_fails_closed():
    # SANDBOX(/usr/bin/sandbox-exec)는 macOS 에만 있다. 이 테스트는 단일 파일 번들 차단만 보므로 존재하는 파일로 바꿔 Linux CI 에서도 같은 분기를 탄다.
    with patch.object(runner.sys,'platform','darwin'), patch.object(runner,'SANDBOX',__file__), patch.object(runner.sys,'frozen',True,create=True), patch.object(runner.sys,'_MEIPASS','/private/tmp/_MEIunsafe',create=True):
        with pytest.raises(runner.StrategyError,match='폴더형'):runner.ensure_supported()


def test_worker_detects_closed_streams_even_with_partial_stdout(monkeypatch):
    worker = runner._Worker.__new__(runner._Worker)
    worker.bar_seconds = 1
    worker.buffer = bytearray(b'{"action":')
    worker.selector = type('ClosedSelector', (), {'select': lambda self, timeout: [], 'get_map': lambda self: {}})()
    monkeypatch.setattr(worker, '_check', lambda deadline: None)
    with pytest.raises(runner.StrategyError, match='정상 응답 없이 종료'):
        worker.receive()


mac=pytest.mark.skipif(sys.platform!='darwin',reason='Native macOS execution acceptance; no unsandboxed fallback')


@mac
def test_native_sandbox_probes_and_clean_environment(monkeypatch):
    monkeypatch.setenv('MERITZ_APP_KEY','HOST_CANARY_NEVER_INHERIT')
    assert all(runner.sandbox_self_test()['checks'].values())
    code='import os\ndef on_bar(ctx):\n    assert "MERITZ_APP_KEY" not in os.environ\n    return {"action":"hold"}'
    with runner.StrategySession(code) as session:
        assert session.decide({})=={'action':'hold'}


@mac
def test_customer_code_runs_and_changes_results():
    reports=[]
    for code in ['def on_bar(ctx):\n return {"action":"buy","quantity":1} if ctx["index"] == 0 else {"action":"hold"}',
                 'def on_bar(ctx):\n return {"action":"hold"}']:
        with runner.StrategySession(code) as session:
            report=run_strategy(demo_candles(),DEFAULTS,session.decide,None,
                {'name':'고객 파일','codeHash':session.validation['codeHash'],'interface':'on_bar_v1'})
            reports.append(report)
    assert reports[0]['metrics']['tradeCount']==1
    assert reports[1]['metrics']['tradeCount']==0
    assert reports[0]['metrics']['finalEquity']!=reports[1]['metrics']['finalEquity']


@mac
def test_history_is_only_past_and_current():
    code='def on_bar(ctx):\n assert len(ctx["history"]) == ctx["index"]+1\n assert ctx["history"][-1]["date"] == ctx["bar"]["date"]\n return {"action":"hold"}'
    with runner.StrategySession(code) as session:
        report=run_strategy(demo_candles(),DEFAULTS,session.decide,None,
            {'name':'과거만','codeHash':session.validation['codeHash'],'interface':'on_bar_v1'})
        assert report['metrics']['tradeCount']==0


@mac
@pytest.mark.parametrize('attack',[
    'while True: pass',
    'import time; time.sleep(30)',
    'while True: print("x"*1000)',
    'import os; os.write(1,b"x"*20000)',
])
def test_bounded_attacks_terminate_and_next_run_works(attack):
    started=time.monotonic()
    with pytest.raises(runner.StrategyError):
        with runner.StrategySession('def on_bar(ctx):\n '+attack,wall_seconds=3,bar_seconds=.6) as session:
            session.decide({})
    assert time.monotonic()-started<6
    with runner.StrategySession('def on_bar(ctx):return {"action":"hold"}') as session:
        assert session.decide({})['action']=='hold'


@mac
def test_host_file_and_child_process_denied(tmp_path):
    secret=tmp_path/'canary'; secret.write_text('private')
    code=f'def on_bar(ctx):\n open({str(secret)!r}).read()\n return {{"action":"hold"}}'
    with runner.StrategySession(code) as session:
        with pytest.raises(runner.StrategyError,match='2행'):session.decide({})
    code='import subprocess\ndef on_bar(ctx):\n subprocess.run(["/bin/echo","escape"])\n return {"action":"hold"}'
    with runner.StrategySession(code) as session:
        with pytest.raises(runner.StrategyError):session.decide({})


@mac
def test_inheritable_fd_is_not_visible(tmp_path):
    secret=tmp_path/'secret';secret.write_text('FD_CANARY')
    fd=os.open(secret,os.O_RDONLY);os.set_inheritable(fd,True)
    try:
        code=f'import os\ndef on_bar(ctx):\n try:\n  data=os.read({fd},32)\n except OSError:\n  return {{"action":"hold"}}\n raise RuntimeError("fd inherited")'
        with runner.StrategySession(code) as session:
            assert session.decide({})=={'action':'hold'}
    finally:os.close(fd)


@mac
def test_fake_metrics_protocol_rejected():
    code='import os,json\ndef on_bar(ctx):\n os.write(1,(json.dumps({"seq":0,"equity":999999999})+"\\n").encode())\n return {"action":"hold"}'
    with runner.StrategySession(code) as session:
        with pytest.raises(runner.StrategyError,match='순서'):session.decide({})


@mac
def test_memory_growth_is_terminated():
    code='import time\ndef on_bar(ctx):\n data=[]\n while True:\n  data.append(bytearray(8*1024*1024))\n  time.sleep(.02)'
    started=time.monotonic()
    with runner.StrategySession(code,memory_mib=96,bar_seconds=3) as session:
        with pytest.raises(runner.StrategyError,match='메모리'):
            session.decide({})
    assert time.monotonic()-started<6


@mac
def test_memory_burst_is_terminated():
    with runner.StrategySession('def on_bar(ctx):\n data=b"x"*(1<<30)\n while True: pass',bar_seconds=3) as session:
        with pytest.raises(runner.StrategyError,match='메모리'):session.decide({})


@mac
def test_memory_monitor_failure_fails_closed():
    with runner.StrategySession('def on_bar(ctx): return {"action":"hold"}') as session:
        session.worker.last_rss=0
        with patch.object(runner,'_memory_footprint',side_effect=OSError('monitor unavailable')):
            with pytest.raises(runner.StrategyError,match='자원 감시'):session.decide({})


@mac
def test_parent_exec_environment_is_not_readable_through_procargs():
    import subprocess
    env=dict(os.environ,MERITZ_APP_KEY='EXEC_START_CANARY')
    result=subprocess.run([sys.executable,'-c',
        'from meritz.strategy_runner import sandbox_self_test; '
        'assert sandbox_self_test()["checks"]["procargs"]'],env=env,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()
