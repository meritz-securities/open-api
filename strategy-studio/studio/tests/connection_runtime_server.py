"""Real HTTP/session/Settings/Registry/adapter; only ApiClient.call is simulated.

All values are generated fixtures. No profiles, keys, or live market are read.
Outbound sockets and HTTP requests fail closed, including token issuance/revoke.
"""
import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

import requests
from meritz.accounts import Registry
from meritz.client import ApiClient
from meritz.config import PROD, Settings
from meritz import load_catalog
from runtime.server import create_server
from runtime.service import StrategyRuntime

stats={'providerCalls':0,'outboundAttempts':0,'credentialMatch':False,'readOnly':False,'productionEndpoint':False}

def forbidden(*args,**kwargs):
    stats['outboundAttempts']+=1
    raise RuntimeError('Network forbidden in generated connection fixture')

socket.socket.connect=forbidden
requests.Session.request=forbidden

def fake_call(client,api,params,*args,**kwargs):
    assert api.key=='market_candles_days' and api.method=='GET'
    assert params=={'iscd':'005930','mrkt_div_code':'TJ','exch_use_yn':'Y','mod_stpr_cls_code':'0','from':'20250101','to':'20250104'}
    stats['providerCalls']+=1
    stats['credentialMatch']=client.s.app_key=='fixture-app-key' and client.s.app_secret=='fixture-app-secret'
    stats['readOnly']=client.s.read_only is True
    stats['productionEndpoint']=client.s.base_url==PROD
    rows=[{'date':f'2025010{i+1}','oprc':str(p),'hprc':str(p),'lprc':str(p),'stck_clpr':str(p),'acml_vol':'1'} for i,p in enumerate([10,20,30,40])]
    return {'ok':True,'verified':False,'body':{'data':rows}}

ApiClient.call=fake_call
with tempfile.TemporaryDirectory(prefix='connection-ui-generated-') as folder:
    os.environ['MERITZ_STRATEGY_HOME']=folder
    os.environ['MERITZ_NO_ENV_FILE']='1'
    if len(sys.argv)>2 and sys.argv[2]=='shared':
        service=StrategyRuntime(folder,catalog=load_catalog(),registry=Registry([Settings(label='generated',app_key='fixture-key',app_secret='fixture-secret')]))
        server=create_server(service,page=Path(sys.argv[1]).read_bytes())
    else:
        server=create_server(page=Path(sys.argv[1]).read_bytes(),allow_market_connection=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    print(json.dumps({'url':server.issue_ticket(),'fixture':True}),flush=True)
    try:
        for line in sys.stdin:
            command=line.strip()
            if command=='stop':break
            if command=='stats':
                clean=all(b'fixture-app-key' not in p.read_bytes() and b'fixture-app-secret' not in p.read_bytes() for p in Path(folder).rglob('*') if p.is_file())
                result={**stats,'storeContainsNoCredentials':clean,'connection':server.service.get_market_connection()}
            elif command.startswith('dataset '):result=server.service.store.get('dataset',command.split(' ',1)[1])
            else:raise ValueError('Unknown generated fixture command')
            print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        server.shutdown();server.server_close();thread.join(5)
