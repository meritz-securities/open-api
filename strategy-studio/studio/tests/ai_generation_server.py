"""Fake CLI outputs are test-authored. Actual Store/engine/HTTP; no model calls."""
import dataclasses
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from runtime.ai_cli_provider import ProviderConfig
from runtime.service import StrategyRuntime
from runtime.server import create_server

socket.socket.connect=lambda *args,**kwargs: (_ for _ in ()).throw(RuntimeError('No outbound test access'))
with tempfile.TemporaryDirectory(prefix='studio-generation-') as temp:
    root=Path(temp); marker=root/'starts'; mode=root/'mode'; mode.write_text('success')
    definition={'schemaVersion':1,'kind':'dsl','name':'기준 전략','entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'quantity':1,'allowPyramiding':False,'risk':{'stopLossPct':None,'takeProfitPct':None}}
    candidate={**definition,'name':'시험 코드가 작성한 후보'}
    exe=root/'fake-cli'
    exe.write_text(f'''#!{sys.executable}
import sys,json,time
from pathlib import Path
if sys.argv[1:]==['--version']:
 print('2.1.322 (Claude Code)');sys.exit(0)
with open({str(marker)!r},'a') as output:output.write('start\\n')
sys.stdin.read()
mode=Path({str(mode)!r}).read_text()
if mode=='slow':
 while Path({str(mode)!r}).read_text()=='slow':time.sleep(.02)
if mode=='failure':sys.exit(7)
print({json.dumps({'is_error':False,'structured_output':{'candidates':[candidate]}},ensure_ascii=False)!r})
''')
    exe.chmod(0o700)
    provider=ProviderConfig(str(exe),'FAKE_TEST_ONLY',timeout_seconds=20,model='fixture-model',budget_usd=.25)
    service=StrategyRuntime(root/'store',ai_provider=provider,ai_studio=True)
    revision=service.save_strategy(definition);data=service.demo_data();baseline=service.run(revision['id'],data['id']);baseline_original=service.get_run(baseline['id'])
    other=service.run(revision['id'],data['id'])
    delayed_preview=threading.Event();release_preview=threading.Event();release_preview.set();original_preview=service.preview_ai_generation
    def preview(requestId):
        result=original_preview(requestId)
        if not release_preview.is_set():delayed_preview.set();release_preview.wait(10)
        return result
    service.preview_ai_generation=preview
    original_generate=service.generate_ai_candidates;lose_response=False
    def generate(*args,**kwargs):
        result=original_generate(*args,**kwargs)
        if lose_response:raise ValueError('TEST: generation response lost after persistence')
        return result
    service.generate_ai_candidates=generate
    # Inject control-read/engine interruption faults only in this test process.
    from runtime import ai_generation
    control_failure='';control_once=threading.Event();original_cancelled=ai_generation.cancelled
    def cancelled(service_,request_id):
        if control_failure=='watcher' and marker.exists() and threading.current_thread().name.endswith('(watch)') and not control_once.is_set():
            control_once.set();raise ValueError('PRIVATE_TEST_CONTROL_READ_FAILURE')
        return original_cancelled(service_,request_id)
    ai_generation.cancelled=cancelled
    original_run_locked=service._run_locked
    def run_locked(*args,**kwargs):
        if control_failure=='execution' and kwargs.get('cancel') is not None:
            kwargs['cancel'].control_error='CANCEL_STATE_UNAVAILABLE';kwargs['cancel'].set()
        return original_run_locked(*args,**kwargs)
    service._run_locked=run_locked
    server=create_server(service,page=Path(sys.argv[1]).read_bytes());threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':server.issue_ticket(),'baseline':baseline['id'],'other':other['id']}),flush=True)
    lock=None
    try:
        for line in sys.stdin:
            message=json.loads(line);action=message['action'];value={}
            if action=='stop':break
            if action=='ticket':value={'url':server.issue_ticket()}
            elif action=='mode':mode.write_text(message['value'])
            elif action=='control_failure':control_failure=message['value']
            elif action=='configured':service._ai_provider=provider if message['value'] else None
            elif action=='budget':service._ai_provider=dataclasses.replace(provider,budget_usd=message['value'])
            elif action=='lock':lock=service.store.execution_lock();lock.__enter__()
            elif action=='unlock':lock.__exit__(None,None,None);lock=None
            elif action=='delay_preview':delayed_preview.clear();release_preview.clear()
            elif action=='preview_waiting':value={'waiting':delayed_preview.wait(5)}
            elif action=='release_preview':release_preview.set()
            elif action=='lose_response':lose_response=message['value']
            elif action=='inspect':
                rows=service.list_ai_requests()['requests'];runs=[service.get_run(row['id']) for row in service.list_runs()['runs']]
                value={'starts':len(marker.read_text().splitlines()) if marker.exists() else 0,'requests':rows,'runs':runs,'baselineUnchanged':service.get_run(baseline['id'])==baseline_original}
                if message.get('requestId'):value['detail']=service.get_ai_request(message['requestId'])
            else:raise ValueError(action)
            print(json.dumps(value,ensure_ascii=False),flush=True)
    finally:
        mode.write_text('success');release_preview.set()
        if lock:lock.__exit__(None,None,None)
        server.shutdown();server.server_close()
