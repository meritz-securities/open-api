"""Developer acceptance: python tests/smoke_strategy_bundle.py /path/to/native/executable.

The tested application receives a PATH without Python, Node or Docker. No API calls.
"""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    executable=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='strategy-bundle-test-') as directory:
        log=Path(directory)/'server.log'
        with log.open('w') as stream:
            child=subprocess.Popen([str(executable),'--no-browser'],cwd=directory,stdout=stream,stderr=stream,
                env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8','MERITZ_NO_ENV_FILE':'1',
                     'MERITZ_APP_KEY':'HOST_TEST_CANARY','MERITZ_APP_SECRET':'HOST_TEST_SECRET'},
                start_new_session=True)
            try:
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    match=re.search(r'http://127\.0\.0\.1:\d+',log.read_text())
                    if match:break
                    if child.poll() is not None:raise RuntimeError('Native startup failed')
                    time.sleep(.1)
                else:raise RuntimeError('Native startup timeout')
                origin=match.group()
                page=urllib.request.urlopen(origin+'/?local=1',timeout=5).read().decode()
                token=re.search('name="backtest-local-token" content="([^"]+)"',page)[1]
                def call(code):
                    request=urllib.request.Request(origin+'/api/backtest',data=json.dumps({
                        'name':'meritz_backtest_code','arguments':{'code':code,'source':'demo'}}).encode(),
                        headers={'Origin':origin,'X-Backtest-Token':token,'Content-Type':'application/json'})
                    return json.load(urllib.request.urlopen(request,timeout=30))
                reports=[]
                for action in ['buy','hold']:
                    intent={'action':action,'quantity':1} if action=='buy' else {'action':'hold'}
                    code='import statistics, os\ndef on_bar(ctx):\n assert statistics.mean([1,2,3])==2\n assert "MERITZ_APP_KEY" not in os.environ\n return '+repr(intent)+' if ctx["index"]==0 else {"action":"hold"}'
                    result=call(code)
                    assert not result['isError'],result
                    report=result['structuredContent']['report'];reports.append(report)
                    assert all(report['runtime']['checks'].values())
                assert [r['metrics']['tradeCount'] for r in reports]==[1,0]
                assert reports[0]['metrics']['finalEquity']!=reports[1]['metrics']['finalEquity']
                assert reports[0]['runId']!=reports[1]['runId']
                canary=Path(directory)/'secret';canary.write_text('must-not-read')
                for attack in ['while True: pass', 'while True: print("x"*2000)',
                               'open('+repr(str(canary))+').read()',
                               'import subprocess; subprocess.run(["/bin/echo","escape"])']:
                    started=time.monotonic()
                    result=call('def on_bar(ctx):\n '+attack)
                    assert result['isError'],attack
                    assert time.monotonic()-started<25
                assert not call('def on_bar(ctx): return {"action":"hold"}')['isError']
                print(json.dumps({'status':'PASS','nativeExecutable':str(executable),
                    'manualRuntimesOnPath':False,'source':'synthetic only; no API calls',
                    'checks':['native startup','statistics import','secret env excluded','9 sandbox probes including parent procargs and metadata',
                    'code changes trade count 1 vs 0','host metrics differ','code hashes differ',
                    'CPU loop terminated','output flood terminated','host file denied','subprocess denied','next run succeeds']},ensure_ascii=False,indent=2))
            finally:
                if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
                child.wait(timeout=10)


if __name__=='__main__':main()
