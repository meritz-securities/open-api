import http.client
import json
import re
import threading

from meritz.backtest_local import create_server


def test_loopback_origin_token_body_and_tool_boundary():
    class Bridge:
        calls=[]
        def call(self,name,arguments):
            self.calls.append((name,arguments));return {'structuredContent':{'valid':True},'content':[],'isError':False}
    bridge=Bridge();server=create_server(bridge)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    def request(method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        c.request(method,path,body,headers or {});r=c.getresponse();data=r.read();response=(r.status,dict(r.getheaders()),data);c.close();return response
    try:
        status,headers,page=request('GET','/?local=1')
        assert status==200 and headers['X-Frame-Options']=='DENY' and headers['Cache-Control']=='no-store'
        assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
        token=re.search(rb'name="backtest-local-token" content="([^"]+)"',page).group(1).decode()
        payload=json.dumps({'name':'meritz_backtest_validate','arguments':{'code':'def on_bar(ctx): pass'}})
        good={'Origin':origin,'X-Backtest-Token':token,'Content-Type':'application/json'}
        assert request('POST','/api/backtest',payload,good)[0]==200
        for altered in [dict(good,Origin='https://evil.example'),dict(good,Host='evil.example'),dict(good,**{'X-Backtest-Token':'wrong'})]:
            assert request('POST','/api/backtest',payload,altered)[0]==403
        assert request('POST','/api/backtest',payload,{'Content-Type':'application/json'})[0]==403
        assert request('POST','/api/backtest',json.dumps({'name':'meritz_trading','arguments':{}}),good)[0]==400
        assert request('POST','/api/backtest','x'*200001,good)[0]==413
        assert request('GET','/../../.env')[0]==404
        assert request('GET','/',headers={'Host':'rebind.example'})[0]==403
        assert len(bridge.calls)==1
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
