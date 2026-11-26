"""Loopback-only review app using the same registered MCP tools, not a second engine."""
from __future__ import annotations

import asyncio
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser


class Bridge:
    def __init__(self):
        from fastmcp import Client
        from .server import build_server
        self.loop=asyncio.new_event_loop()
        self.thread=threading.Thread(target=self.loop.run_forever,daemon=True)
        self.thread.start()
        self.client=Client(build_server()[0])
        asyncio.run_coroutine_threadsafe(self.client.__aenter__(),self.loop).result(timeout=20)

    def call(self,name,arguments):
        async def invoke():
            result=await self.client.call_tool(name,arguments,raise_on_error=False)
            return {'isError':result.is_error,'structuredContent':result.structured_content,
                    'content':[item.model_dump(mode='json') for item in result.content]}
        return asyncio.run_coroutine_threadsafe(invoke(),self.loop).result(timeout=90)

    def close(self):
        asyncio.run_coroutine_threadsafe(self.client.__aexit__(None,None,None),self.loop).result(timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


ALLOWED_TOOLS=frozenset({'meritz_backtest_validate','meritz_backtest_code','meritz_backtest_trade',
                        'meritz_backtest_demo','meritz_backtest_compare','meritz_info'})


def create_server(bridge,port=0):
    token=secrets.token_urlsafe(32)
    page=Path(__file__).with_name('ui').joinpath('backtest.html').read_text()
    page=page.replace('<head>','<head><meta name="backtest-local-token" content="'+token+'">',1)
    class Handler(BaseHTTPRequestHandler):
        server_version='MeritzLocal'
        def log_message(self,*_): pass
        def setup(self):
            super().setup(); self.connection.settimeout(5)
        def host_ok(self):
            return self.client_address[0]=='127.0.0.1' and self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
        def send(self,status,body,kind='application/json; charset=utf-8'):
            if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if not self.host_ok():return self.send(403,{'error':'host'})
            if urlsplit(self.path).path not in ('/','/mcp-app.html'):return self.send(404,{'error':'not_found'})
            # No API keys, strategy or data are embedded in the entry page.
            return self.send(200,page.encode(),'text/html; charset=utf-8')
        def do_POST(self):
            origin=f'http://127.0.0.1:{self.server.server_port}'
            if not self.host_ok() or self.headers.get('Origin')!=origin:
                return self.send(403,{'error':'origin'})
            if not hmac.compare_digest(self.headers.get('X-Backtest-Token',''),token):
                return self.send(403,{'error':'token'})
            if self.path!='/api/backtest':return self.send(404,{'error':'not_found'})
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.send(415,{'error':'content_type'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=200_000:return self.send(413,{'error':'body_limit'})
                body=self.rfile.read(size)
                if len(body)!=size:return self.send(400,{'error':'incomplete'})
                request=json.loads(body)
                if not isinstance(request,dict) or set(request)!={'name','arguments'} or not isinstance(request['name'],str) or request['name'] not in ALLOWED_TOOLS or not isinstance(request['arguments'],dict):
                    return self.send(400,{'error':'tool_contract'})
            except (ValueError,TypeError,RecursionError,TimeoutError):
                return self.send(400,{'error':'invalid_json'})
            try:
                self.send(200,bridge.call(request['name'],request['arguments']))
            except Exception:
                self.send(500,{'isError':True,'content':[{'type':'text','text':'로컬 실행을 완료하지 못했습니다. 코드와 실행 환경을 확인하세요.'}]})
        def do_OPTIONS(self):self.send(405,{'error':'method'})
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        allow_reuse_address=False
        slots=threading.BoundedSemaphore(8)
        def process_request(self,request,address):
            if not self.slots.acquire(blocking=False):
                request.close();return
            try:super().process_request(request,address)
            except BaseException:
                self.slots.release();raise
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:self.slots.release()
    return Server(('127.0.0.1',port),Handler)


def main(*,open_browser=True,port=0):
    # This is a local single-user review path, never the remote MCP transport.
    os.environ['MCP_TYPE']='stdio'
    bridge=Bridge();server=create_server(bridge,port)
    url=f'http://127.0.0.1:{server.server_port}/?local=1'
    print('메리츠 전략 코드 실험실: '+url,flush=True)
    print('종료하려면 이 창에서 Ctrl+C를 누르세요. 코드 실행은 macOS에서 지원합니다.',flush=True)
    if open_browser:webbrowser.open(url)
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:server.server_close();bridge.close()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args();main(open_browser=not args.no_browser,port=args.port)
