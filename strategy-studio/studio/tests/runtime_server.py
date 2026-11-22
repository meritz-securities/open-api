"""Test-only isolated server: no registry, credentials, user store or market calls."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from runtime.service import StrategyRuntime
from runtime.server import create_server

with tempfile.TemporaryDirectory(prefix='meritz-studio-browser-') as directory:
    server = create_server(StrategyRuntime(directory), page=Path(sys.argv[1]).read_bytes())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({'url': server.issue_ticket()}), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == 'ticket':
                print(json.dumps({'url': server.issue_ticket()}), flush=True)
            elif line.strip() == 'stop':
                break
    finally:
        server.shutdown()
        server.server_close()
