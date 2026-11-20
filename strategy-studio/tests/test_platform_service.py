import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import pytest
from fastmcp import Client, FastMCP
from runtime.service import StrategyRuntime
from runtime.storage import Store
from runtime.mcp import register_platform
from runtime.server import create_server


def definition():
    return {'schemaVersion':1,'kind':'dsl','name':'independent buy hold','entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'quantity':1,'allowPyramiding':False,'risk':{'stopLossPct':None,'takeProfitPct':None}}


def fixture(root):
    service=StrategyRuntime(root)
    bars=[{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,40])]
    revision=service.save_strategy(definition()); data=service.import_data(bars,{'kind':'imported','label':'independent fixture'})
    args={'revisionId':revision['id'],'datasetId':data['id'],'settings':{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0}}
    return service,args


def test_same_runtime_cli_mcp_persistence_and_no_market_calls(tmp_path,monkeypatch):
    monkeypatch.setenv('MERITZ_NO_ENV_FILE','1')
    import requests
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('unexpected market request'))
    service,args=fixture(tmp_path);first=service.run(**args)
    assert first['report']['metrics']['finalEquity']==120
    assert first['report']['finalPortfolio']['cash']==80
    assert first['report']['finalPortfolio']['unrealizedPnl']==20
    assert first['report']['trades'][0]['date']=='2025-01-02'
    assert first['report']['trades'][0]['evidence']['origin']=='dsl-evaluated'
    env={**os.environ,'MERITZ_NO_ENV_FILE':'1'}
    result=subprocess.run([sys.executable,'-m','runtime.cli','run','--root',str(tmp_path),'--arguments',json.dumps(args)],capture_output=True,text=True,env=env,check=True)
    cli=json.loads(result.stdout)['result']
    async def check():
        mcp=FastMCP('platform-test');names=register_platform(mcp,None,None,StrategyRuntime(tmp_path))
        async with Client(mcp) as client:
            assert 'meritz_studio_open' in names
            result=await client.call_tool('meritz_platform_backtest',args)
            return result.structured_content
    mcp=asyncio.run(check())
    assert len({first['id'],cli['id'],mcp['id']})==3
    assert first['manifest']['resultHash']==cli['manifest']['resultHash']==mcp['manifest']['resultHash']
    restored=StrategyRuntime(tmp_path)
    assert restored.get_run(first['id'])==first
    child=restored.experiment(first['id'],settings={'feeBps':100})
    assert child['datasetId']==first['datasetId'] and child['parentRunId']==first['id']
    assert restored.get_run(first['id'])==first
    assert child['report']['metrics']['finalEquity']==pytest.approx(119.8)
    comparison=restored.compare([first['id'],child['id']])
    assert comparison['sameDataset'] and not comparison['sameCosts']
    assert comparison['runs'][1]['deltaReturnPct']<0


def test_invalid_inputs_and_immutable_snapshots(tmp_path):
    service,args=fixture(tmp_path)
    for source in [{'kind':'imported','label':'x','api_key':'SECRET'},{'kind':'meritz','label':'pretend'}]:
        with pytest.raises(ValueError):service.import_data([],source)
    for content in ['date,open,high,low,close\n2025-01-01,NaN,1,1,1','date,open,high,low,close\n2025-01-01,=cmd,1,1,1']:
        with pytest.raises(ValueError):service.import_csv(content,{'kind':'imported','label':'file'})
    with pytest.raises(ValueError):service.run(**{**args,'settings':[]})
    row=service.store.get('revision',args['revisionId']);row['definition']['name']='changed'
    with pytest.raises(ValueError):service.store.put('revision',row['id'],row)
    with pytest.raises(ValueError):service.dispatch('meritz_trading',{})


def test_research_labels_and_export_escaping(tmp_path):
    service,args=fixture(tmp_path);run=service.run(**args)
    r=service.validate_robustness(run['id'],'out_of_sample',{'fraction':.5})
    assert r['result']['holdoutContaminated'] is True
    assert r['result']['holdoutAccessed'] is True
    assert service.propose(run['id'])['method']=='rule-based'
    for fmt in ['html','csv','json']:
        export=service.export_report(run['id'],fmt);assert export['content']
    from reports import export_run
    bad=copy.deepcopy(run);bad['report']['strategy']['name']='<script>alert(1)</script>'
    bad['report']['trades'][0]['reason']='=HYPERLINK("evil")'
    assert '<script>alert' not in export_run(bad,'html')['content']
    assert "'=HYPERLINK" in export_run(bad,'csv')['content']


def test_loopback_ticket_one_use_origin_and_order_boundary(tmp_path):
    service,args=fixture(tmp_path)
    server=create_server(service,page=b'<html>no token here</html>');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    def post(path,payload,**headers):
        req=urllib.request.Request(origin+path,data=json.dumps(payload).encode(),headers={'Origin':origin,'Content-Type':'application/json',**headers})
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    try:
        response=urllib.request.urlopen(origin);page=response.read();assert b'no token' in page
        assert "script-src 'unsafe-inline'" not in response.headers['Content-Security-Policy']
        assert post('/api/session',{'ticket':'wrong'},Host='attacker.example')[0]==403
        expired=server.issue_ticket().split('ticket=')[1]
        server.tickets[expired]=0
        assert post('/api/session',{'ticket':expired})[0]==403
        assert post('/api/platform',{'action':'list_strategies','arguments':{}})[0]==403
        ticket=server.issue_ticket().split('ticket=')[1]
        assert ticket.encode() not in page
        assert post('/api/session',{'ticket':ticket},Origin='https://evil.example')[0]==403
        status,payload=post('/api/session',{'ticket':ticket});assert status==200
        token=payload['result']['token']
        assert post('/api/session',{'ticket':ticket})[0]==403
        assert post('/api/platform',{'action':'list_strategies','arguments':{}},**{'X-Platform-Token':token})[0]==200
        assert post('/api/platform',{'action':'meritz_trading','arguments':{}},**{'X-Platform-Token':token})[0]==400
    finally:server.shutdown();server.server_close();thread.join()


def test_second_process_does_not_interrupt_active_run(tmp_path):
    store=Store(tmp_path)
    code='''from runtime.storage import Store
import sys,time
s=Store(sys.argv[1])
with s.execution_lock():
 s.put('run','active',{'id':'active','status':'running'})
 print('locked',flush=True)
 sys.stdin.readline()
'''
    p=subprocess.Popen([sys.executable,'-c',code,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert p.stdout.readline().strip()=='locked'
        second=Store(tmp_path)
        assert second.get('run','active')['status']=='running'
        with pytest.raises(ValueError,match='active'):
            with second.execution_lock():pass
        assert second.get('run','active')['status']=='running'
    finally:p.communicate('\n',timeout=5)
    with store.execution_lock():assert store.get('run','active')['status']=='interrupted'


def test_provider_quality_warnings_survive_snapshot_report_and_export(tmp_path, monkeypatch):
    service,args=fixture(tmp_path)
    service.catalog=object();service.registry=object()
    import meritz.backtest_data
    bars=service.store.get('dataset',args['datasetId'])['bars']
    warning='API 응답 코드를 확인할 수 없어 성공 여부를 코드로 검증하지 못했습니다.'
    monkeypatch.setattr(meritz.backtest_data,'load_market_data',lambda *a,**k:{'candles':bars,'source':{'kind':'meritz','label':'provider fixture'},'warnings':[warning]})
    data=service.fetch_data('005930','2025-01-01','2025-01-04')
    assert data['warnings']==[warning]
    result=service.run(**{**args,'datasetId':data['id']})
    assert warning in result['report']['warnings']
    assert result['manifest']['dataWarnings']==[warning]
    assert warning in service.export_report(result['id'],'html')['content']


def test_final_write_failure_persists_small_terminal_attempt(tmp_path, monkeypatch):
    service,args=fixture(tmp_path)
    original=service.store.finish_run
    calls=[]
    def bounded_finish(ident,value):
        calls.append(value['status'])
        if value['status']=='succeeded':raise ValueError('Snapshot exceeds local storage limit')
        assert not {'report','analysis','manifest'} & value.keys()
        return original(ident,value)
    monkeypatch.setattr(service.store,'finish_run',bounded_finish)
    with pytest.raises(ValueError,match='storage limit'):service.run(**args)
    assert calls==['succeeded','failed']
    assert service.store.list('run')[0]['status']=='failed'
    assert not service._active
