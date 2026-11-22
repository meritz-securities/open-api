"""Loopback Studio with one-time launch ticket; GET never discloses a session token."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import hashlib
import base64
import re
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit
import webbrowser
from .service import StrategyRuntime
from .ai_studio import HTTP_ACTIONS as AI_HTTP_ACTIONS


# 내부 ValueError 문구 → 고객 안내. 영문 내부 문자열은 그대로 내보내지 않는다.
KNOWN_ERRORS = {
    'Another local run is active': ('RUN_ACTIVE', '다른 백테스트가 실행 중입니다. 완료 후 다시 시도하세요.'),
    'Another AI generation is active': ('AI_GENERATION_ACTIVE', '다른 AI 생성 작업이 실행 중입니다. 완료 후 다시 시도하세요.'),
    'Snapshot exceeds local storage limit': ('STORAGE_LIMIT', '저장 한도를 초과했습니다. 데이터 범위를 줄이거나 결과를 나눠 저장하세요.'),
    'Snapshot exceeds decoded storage limit': ('STORAGE_LIMIT', '저장 한도를 초과했습니다. 데이터 범위를 줄이거나 결과를 나눠 저장하세요.'),
    'unknown strategy kind or fields': ('INVALID_STRATEGY', '전략 정의 형식이 올바르지 않습니다.'),
    'Snapshot not found': ('NOT_FOUND', '요청한 항목을 찾을 수 없습니다. 목록을 새로 고친 뒤 다시 선택하세요.'),
    'Invalid snapshot identifier': ('INVALID_ID', '항목 식별자 형식이 올바르지 않습니다.'),
    'Immutable snapshot cannot be overwritten': ('IMMUTABLE', '저장된 항목은 덮어쓸 수 없습니다. 새 항목으로 저장하세요.'),
    'Finalized run cannot be overwritten': ('IMMUTABLE', '완료된 실행 결과는 변경할 수 없습니다.'),
    'Unsupported database version; do not downgrade': ('STORAGE_VERSION', '저장 데이터가 이 앱보다 새 버전입니다. 최신 앱으로 실행하세요.'),
}
GENERIC_ERROR_MESSAGE = '요청을 처리하지 못했습니다. 입력값을 확인하세요.'
_HANGUL = re.compile(r'[\uac00-\ud7a3\u3131-\u318e]')
_CODE_TOKEN = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
AI_PROVIDER_ACTIONS = frozenset({'preview_ai_generation', 'generate_ai_candidates'})


def public_error(error):
    """ValueError → (code, message). 한국어 문구와 대문자 코드 토큰(UI 가 매핑)은 그대로, 영문 내부 문구는 일반 안내로 바꾸고
    내부 문구를 코드로만 남긴다. 서버 로그는 남기지 않는다."""
    text = str(error)[:500]
    if text in KNOWN_ERRORS:
        return KNOWN_ERRORS[text]
    if text.startswith('unsupported_platform:'):
        return 'UNSUPPORTED_PLATFORM', 'Python 전략 격리 환경이 준비되지 않았습니다.'
    if _CODE_TOKEN.match(text) or _HANGUL.search(text):
        return 'INVALID_REQUEST', text
    code = re.sub(r'[^A-Z0-9]+', '_', text.upper()).strip('_')[:64]
    return (code or 'INVALID_REQUEST'), GENERIC_ERROR_MESSAGE


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    def __init__(self, service, page, port=0):
        self.service = service; self.page = page
        scripts = re.findall(rb'<script\b[^>]*>(.*?)</script>', page, re.IGNORECASE | re.DOTALL)
        self.script_policy = ' '.join("'sha256-" + base64.b64encode(hashlib.sha256(code).digest()).decode() + "'" for code in scripts) or "'none'"
        self.auth_lock = threading.Lock(); self.tickets = {}; self.sessions = {}
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(('127.0.0.1', port), Handler)
    def issue_ticket(self):
        ticket = secrets.token_urlsafe(32)
        with self.auth_lock:
            self.tickets = {k:v for k,v in self.tickets.items() if v > time.monotonic()}
            if len(self.tickets) >= 8: self.tickets.pop(next(iter(self.tickets)))
            self.tickets[ticket] = time.monotonic() + 60
        return f'http://127.0.0.1:{self.server_port}/#ticket={ticket}'
    def redeem(self, ticket):
        if not isinstance(ticket,str) or len(ticket)>100: return None
        with self.auth_lock:
            expiry = self.tickets.pop(ticket, 0)
            if expiry < time.monotonic(): return None
            self.sessions={k:v for k,v in self.sessions.items() if v>time.monotonic()}
            if len(self.sessions)>=8:self.sessions.pop(next(iter(self.sessions)))
            token=secrets.token_urlsafe(32);self.sessions[token]=time.monotonic()+12*3600
        return token
    def authorized(self, token):
        if len(token)>100:return False
        with self.auth_lock:
            return any(expiry>time.monotonic() and hmac.compare_digest(token,key) for key,expiry in self.sessions.items())
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):request.close();return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version='MeritzStrategyLocal'
    def log_message(self,*args):pass
    def setup(self):
        super().setup();self.connection.settimeout(5)
    def host_ok(self):
        return self.client_address[0]=='127.0.0.1' and self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
    def send(self,status,value,kind='application/json;charset=utf-8'):
        body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status)
        for key,val in {'Content-Type':kind,'Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':f"default-src 'none'; script-src {self.server.script_policy}; style-src 'unsafe-inline'; connect-src 'self'; img-src data: blob:; font-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():self.send_header(key,val)
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if not self.host_ok():return self.send(403,{'ok':False,'error':{'code':'HOST','message':'Invalid host'}})
        if urlsplit(self.path).path!='/':return self.send(404,{'ok':False,'error':{'code':'NOT_FOUND','message':'Not found'}})
        return self.send(200,self.server.page,'text/html;charset=utf-8')
    def do_POST(self):
        origin=f'http://127.0.0.1:{self.server.server_port}'
        if not self.host_ok() or self.headers.get('Origin')!=origin:return self.send(403,{'ok':False,'error':{'code':'ORIGIN','message':'Invalid origin'}})
        if self.path not in ('/api/platform','/api/session'):return self.send(404,{'ok':False,'error':{'code':'NOT_FOUND','message':'Not found'}})
        if self.path=='/api/platform' and not self.server.authorized(self.headers.get('X-Platform-Token','')):return self.send(403,{'ok':False,'error':{'code':'AUTH','message':'앱 실행 링크로 다시 연결하세요.'}})
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,{'ok':False,'error':{'code':'TYPE','message':'JSON required'}})
        try:
            size=int(self.headers.get('Content-Length','0'));limit=1024 if self.path=='/api/session' else 4_000_000
            if not 0<size<=limit:return self.send(413,{'ok':False,'error':{'code':'LIMIT','message':'Request too large'}})
            body=self.rfile.read(size)
            if len(body)!=size:raise ValueError('Incomplete body')
            value=json.loads(body,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            if not isinstance(value,dict):raise ValueError('Object required')
        except (ValueError,RecursionError,TimeoutError):return self.send(400,{'ok':False,'error':{'code':'JSON','message':'Invalid JSON'}})
        if self.path=='/api/session':
            if set(value)!={'ticket'}:return self.send(400,{'ok':False,'error':{'code':'CONTRACT','message':'Ticket required'}})
            token=self.server.redeem(value['ticket'])
            if token is None:return self.send(403,{'ok':False,'error':{'code':'TICKET','message':'실행 링크가 만료되었거나 이미 사용되었습니다. 앱에서 다시 여세요.'}})
            return self.send(200,{'ok':True,'result':{'token':token}})
        if set(value)!={'action','arguments'} or not isinstance(value['action'],str) or not isinstance(value['arguments'],dict):return self.send(400,{'ok':False,'error':{'code':'CONTRACT','message':'Action and arguments required'}})
        try:
            # Credential setup is deliberately HTTP-only, never CLI/MCP dispatch.
            if value['action'] in AI_HTTP_ACTIONS:
                if not getattr(self.server.service,'ai_studio_enabled',False):
                    # 고객용 앱: Studio 내 AI 연결·생성 기능을 서버에서 차단한다(UI 숨김과 별개의 방어선).
                    return self.send(403,{'ok':False,'error':{'code':'AI_STUDIO_DISABLED','message':'이 앱은 AI 연결 기능을 제공하지 않습니다. AI 활용은 메리츠 MCP 경로를 사용하세요.'}})
                result=self.server.service._ai_studio.invoke(value['action'],value['arguments'])
            elif value['action'] in AI_PROVIDER_ACTIONS and not getattr(self.server.service,'ai_studio_enabled',False):
                return self.send(403,{'ok':False,'error':{'code':'AI_STUDIO_DISABLED','message':'이 앱은 AI 연결 기능을 제공하지 않습니다. AI 기능은 연결된 메리츠 MCP에서 사용하세요.'}})
            elif value['action'] in ('get_market_connection','connect_market_session','disconnect_market_session'):
                result=getattr(self.server.service,value['action'])(**value['arguments'])
            else:
                result=self.server.service.dispatch(value['action'],value['arguments'])
            return self.send(200,{'ok':True,'result':result})
        except ValueError as error:
            code,message=public_error(error)
            return self.send(400,{'ok':False,'error':{'code':code,'message':message}})
        except TypeError:
            return self.send(400,{'ok':False,'error':{'code':'INVALID_REQUEST','message':'인자 형식을 확인하세요.'}})
        except Exception:
            return self.send(500,{'ok':False,'error':{'code':'LOCAL_ERROR','message':'로컬 요청을 처리하지 못했습니다. 저장 공간·실행 환경을 확인하세요.'}})
    def do_OPTIONS(self):self.send(405,{'ok':False,'error':{'code':'METHOD','message':'Method not allowed'}})


def create_server(service=None,port=0,page=None,*,allow_market_connection=False,allow_ai_connection=False,ai_studio=False):
    if service is not None and (allow_market_connection or allow_ai_connection):
        raise ValueError('기존 서비스와 앱 전용 연결 옵션을 동시에 지정할 수 없습니다.')
    if service is None:
        from meritz import load_catalog
        from meritz.accounts import Registry
        service=StrategyRuntime(catalog=load_catalog(), registry=None if allow_market_connection else Registry(),
                                allow_market_connection=allow_market_connection, allow_ai_connection=allow_ai_connection, ai_studio=ai_studio)
    if page is None:
        import sys
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
        page=(root/'studio/dist/index.html').read_bytes()
    return LocalServer(service,page,port)


def configure_webview_downloads(webview_module):
    """Enable report downloads in the app window; pywebview defaults this off."""
    webview_module.settings['ALLOW_DOWNLOADS'] = True


def _native_webview_available():
    """자체 창에 쓸 OS 웹뷰가 실제로 있는지. macOS/Linux 는 True(WKWebView/GTK 는 OS 구성요소).
    Windows 는 pywebview 와 같은 기준으로 WebView2 런타임(정식/베타/개발/카나리, 86.0.622.0 이상)을 레지스트리에서 확인한다
    — pywebview 는 없을 때 예외 대신 MSHTML 로 폴백하므로 여기서 선검사한다."""
    import platform
    if platform.system()!='Windows':return True
    # 1순위: pywebview 가 실제로 고른 renderer 를 그대로 본다(판정 불일치 원천 차단). winforms 모듈은 import 시점에
    #        .NET 버전·WebView2 레지스트리(pywebview 자체 기준)를 평가해 renderer 를 'edgechromium' | 'mshtml' | 'cef' 로 정한다.
    try:
        import webview.platforms.winforms as winforms  # Windows 에서만 import 가능(pythonnet 필요)
        return getattr(winforms,'renderer',None)=='edgechromium'
    except Exception:
        pass
    # 2순위(폴백 미러): pywebview winforms._is_chromium() 과 같은 조건 — .NET Framework 4.6.2+(Release>=394802),
    #        WebView2 정식/베타/개발/카나리 GUID, pv >= 86.0.622.0, 레지스트리 경로 선택(x86·HKCU 는 일반 경로, 그 외 HKLM 은 WOW6432Node).
    try:
        import webview
        if (getattr(webview,'settings',{}) or {}).get('WEBVIEW2_RUNTIME_PATH'):return True
    except Exception:pass
    try:
        import winreg
    except Exception:return False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as net_key:
            release,_=winreg.QueryValueEx(net_key,'Release')
        if int(release)<394802:return False
    except Exception:
        return False
    def version(s):
        try:return tuple(int(p) for p in str(s).split('.'))
        except Exception:return (0,)
    x86=platform.machine().lower() in ('x86','i386','i686')
    guids=('{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}','{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}',
           '{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}','{65C35B14-6C1D-4122-AC46-7148CC9D6497}')
    for guid in guids:
        for hive,is_hkcu in ((winreg.HKEY_CURRENT_USER,True),(winreg.HKEY_LOCAL_MACHINE,False)):
            sub=(rf'SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}' if (x86 or is_hkcu)
                 else rf'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}')
            try:
                with winreg.OpenKey(hive,sub) as key:
                    pv,_=winreg.QueryValueEx(key,'pv')
                    if version(pv)>=(86,0,622,0):return True
            except Exception:
                continue
    return False


def _console_utf8():
    # Windows 에서 stdout 이 파이프(설치 검사·다른 프로그램)이면 기본 인코딩이 cp1252 같은 코드페이지라
    # 한국어 안내문에서 UnicodeEncodeError 로 서버가 죽는다. 콘솔(tty)은 그대로 두고 파이프만 UTF-8 로 쓴다.
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def main(open_browser=True,port=0,window=False):
    _console_utf8()
    # 고객용 앱: 시세 연결은 앱 안에서 설정, Studio 내 상용 AI 연결/생성 기능은 끈다(ai_studio=False). AI 활용은 메리츠 MCP(--mcp) 경로.
    server=create_server(allow_market_connection=True,allow_ai_connection=False,ai_studio=False);url=server.issue_ticket()
    print(f'Meritz Strategy Studio: http://127.0.0.1:{server.server_port}/',flush=True)
    if window:
        try:
            import webview
        except Exception as error:
            print(f'웹뷰 모듈을 불러오지 못해 브라우저로 전환합니다: {error}',flush=True);window=False
    if window and not _native_webview_available():
        # Windows 에서 WebView2 런타임이 없으면 pywebview 는 예외 없이 MSHTML(IE) 렌더러로 내려간다(webview/platforms/winforms.py:131~155).
        # Studio 는 type="module" 스크립트라 MSHTML 에서 동작하지 않으므로, 창을 만들기 전에 가용성을 확인하고 브라우저로 전환한다.
        print('자체 창을 띄울 웹뷰 런타임(WebView2)이 없어 브라우저로 전환합니다. https://developer.microsoft.com/microsoft-edge/webview2/ 에서 설치하면 다음 실행부터 자체 창을 씁니다.',flush=True)
        window=False
    if window:
        # pywebview disables anchor[download] by default; report exports must work
        # inside the customer desktop window on supported native renderers.
        configure_webview_downloads(webview)
        import threading
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.2},daemon=True);thread.start()
        try:
            try:
                webview.create_window('메리츠 전략 백테스트',url,width=1280,height=840,min_size=(1024,700))
                webview.start()  # 메인 스레드 네이티브 창 GUI 루프. 창을 닫으면 반환한다.
            except Exception as error:
                # 창 생성/표시 실패(예: Windows WebView2 런타임 없음, 디스플레이 없음) → 브라우저로 폴백. 서버는 계속 동작한다.
                print(f'자체 창을 열 수 없어 브라우저로 전환합니다: {error}',flush=True)
                webbrowser.open(server.issue_ticket())  # 기존 티켓은 소모됐을 수 있으므로 새 일회용 링크를 발급한다.
                thread.join()
        except KeyboardInterrupt:pass
        finally:
            server.shutdown();server.server_close()
        return
    if open_browser:webbrowser.open(url)
    else:print('일회용 로컬 실행 링크 (60초 후 만료): '+url,flush=True)
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:server.server_close()
