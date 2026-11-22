"""Host gating only; these tests are not Windows execution evidence."""
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'packaging/windows/verify_native.py'


def test_windows_smoke_help_is_available_without_starting_an_app():
    result=subprocess.run([sys.executable,str(SCRIPT),'--help'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and 'Windows-only synthetic smoke' in result.stdout


@pytest.mark.skipif(sys.platform=='win32',reason='This check verifies rejection on non-Windows hosts only')
def test_other_hosts_cannot_emit_windows_pass(tmp_path):
    output=tmp_path/'must-not-exist.json'
    result=subprocess.run([sys.executable,str(SCRIPT),'--manifest',str(tmp_path/'missing.json'),
                           '--bundle',str(tmp_path/'missing'),'--output',str(output)],
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==2
    assert 'native Windows' in result.stderr
    assert not output.exists() and 'PASS' not in result.stdout


def test_loopback_smoke_ignores_proxy_settings(tmp_path,monkeypatch):
    import importlib.util
    import http.server
    import threading
    import urllib.request
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    spec=importlib.util.spec_from_file_location('windows_smoke_for_test',SCRIPT)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    seen=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path,self.headers.get('X-Platform-Token')))
            self.send_response(200);self.end_headers();self.wfile.write(b'ok')
        def log_message(self,*args): pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
        monkeypatch.setenv('NO_PROXY','')
        monkeypatch.setattr(urllib.request,'proxy_bypass',lambda host:False)
        def forbidden_proxy_discovery():
            pytest.fail('Local smoke must not consult registry/environment proxies')
        monkeypatch.setattr(urllib.request,'getproxies',forbidden_proxy_discovery)
        request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/local',headers={'X-Platform-Token':'NOT_A_REAL_CREDENTIAL'})
        with module.local_opener().open(request,timeout=5) as response:
            assert response.read()==b'ok'
        assert seen==[('/local','NOT_A_REAL_CREDENTIAL')]
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_python_refusal_requires_explicit_mcp_reason(tmp_path,monkeypatch):
    """Protocol test with simulated service platform, NOT a native Windows run."""
    import asyncio
    import importlib.util
    from fastmcp import Client,FastMCP
    from runtime.service import StrategyRuntime
    from runtime.mcp import register_platform
    import runtime.service as service_module
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    spec=importlib.util.spec_from_file_location('windows_smoke_reason_test',SCRIPT)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    service=StrategyRuntime(tmp_path/'data')
    canary=tmp_path/'must-not-execute'
    code=f'open({str(canary)!r},"w").write("executed")\ndef on_bar(ctx): return {{"action":"hold"}}'
    revision=service.save_strategy({'schemaVersion':1,'kind':'python','name':'refusal test','code':code})
    data=service.import_data([{'date':f'2025-01-0{i}','open':10,'high':10,'low':10,'close':10} for i in (1,2)],{'kind':'imported','label':'synthetic protocol fixture'})
    args={'revisionId':revision['id'],'datasetId':data['id']}
    monkeypatch.setattr(service_module.platform,'system',lambda:'Windows')
    monkeypatch.setattr('meritz.windows_strategy.available', lambda: False)
    async def check():
        mcp=FastMCP('explicit refusal');register_platform(mcp,None,None,service)
        async with Client(mcp) as client:
            await module.verify_python_refusal(client,args,canary)
            with pytest.raises(ValueError,match='explicit unsupported_platform'):
                await module.verify_python_refusal(client,{**args,'revisionId':'missing'},canary)
    asyncio.run(check())
    assert not canary.exists()
