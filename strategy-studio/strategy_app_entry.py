"""One-folder native entrypoint; worker dispatch precedes server startup and credential loading."""
import sys

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--strategy-worker':
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
        from meritz.backtest_local import main
        main(open_browser='--no-browser' not in sys.argv)
