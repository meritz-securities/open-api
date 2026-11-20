"""네이티브 창(임베디드 웹뷰) 실행 엔트리 — 기본 브라우저 대신 '자체 창'에 Strategy Studio 를 띄운다.

기존 로컬 서버(runtime.server)·React UI(studio)·보안(티켓→세션 토큰·Origin/Host 검증)을 그대로 재사용한다.
브라우저를 열지 않고, 창을 닫으면 로컬 서버도 종료한다. OS 내장 웹뷰 사용(맥 WKWebView / 윈도우 WebView2 / 리눅스 GTK).
"""
from runtime.server import main as _main


def main():
    # 단일 구현 유지: 창 모드 로직은 runtime.server.main(window=True) 한 곳에만 둔다(폴백·종료 처리 포함).
    _main(window=True)


if __name__ == '__main__':
    main()
