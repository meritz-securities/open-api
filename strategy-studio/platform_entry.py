"""Native dispatcher: no account/config imports before isolated worker dispatch."""
import sys
if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--wasi-worker':
        from meritz.wasi_host import main
        main(int(sys.argv[2]))
    elif len(sys.argv)>1 and sys.argv[1]=='--strategy-worker':
        from meritz.strategy_worker import main
        main()
    elif len(sys.argv)>1 and sys.argv[1]=='--mcp':
        from meritz.server import main
        main(enable_platform=True)
    elif len(sys.argv)>1 and sys.argv[1]=='--strategy-mcp':
        from runtime.strategy_mcp_server import main
        main()
    elif len(sys.argv)>1 and sys.argv[1]=='--install-claude-strategy-mcp':
        from runtime.claude_install import main
        sys.exit(main())
    elif len(sys.argv)>1 and sys.argv[1]=='--cli':
        from runtime.cli import main
        sys.argv.pop(1);sys.exit(main())
    else:
        from runtime.server import main
        if '--no-browser' in sys.argv:
            main(open_browser=False)                 # 서버만(헤드리스), 링크 출력
        elif '--window' in sys.argv:
            main(window=True)                        # 자체 네이티브 창(베타에서는 선택 기능)
        else:
            main(open_browser=True)                  # 기본(베타): 기본 브라우저로 열기. --browser 도 같다
