"""Disposable real HTTP research fixture; synthetic bars and no account registry."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from runtime.service import StrategyRuntime
from runtime.server import create_server

page = Path(sys.argv[1]).read_bytes()
with tempfile.TemporaryDirectory(prefix='meritz-research-browser-') as directory:
    runtime = StrategyRuntime(directory)
    definition = {'schemaVersion': 1, 'kind': 'dsl', 'name': '기간 분리 독립 계산 시험',
                  'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'},
                            'right': {'type': 'constant', 'value': 0}},
                  'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'},
                           'right': {'type': 'constant', 'value': 0}},
                  'quantity': 1, 'allowPyramiding': False,
                  'risk': {'stopLossPct': None, 'takeProfitPct': None}}
    revision = runtime.save_strategy(definition)
    dates = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09']
    dataset = runtime.import_data([
        {'date': date, 'open': p, 'high': p, 'low': p, 'close': p}
        for date, p in zip(dates, [10, 20, 30, 30, 20, 10])
    ], {'kind': 'imported', 'label': 'GENERATED TEST ONLY', 'currency': 'KRW'})
    original_revision = runtime.get_strategy(revision['id'])
    original_data = runtime.store.get('dataset', dataset['id'])
    def start():
        server = create_server(runtime, page=page)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread
    def info():
        return {'url': server.issue_ticket(), 'revisionId': revision['id'],
                'datasetId': dataset['id'], 'definition': definition,
                'runs': runtime.list_runs()['runs'],
                'sourceUnchanged': runtime.get_strategy(revision['id']) == original_revision
                and runtime.store.get('dataset', dataset['id']) == original_data}
    server, thread = start()
    print(json.dumps(info()), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == 'reopen':
                server.shutdown(); server.server_close(); thread.join(timeout=5)
                runtime = StrategyRuntime(directory)
                server, thread = start()
                print(json.dumps(info()), flush=True)
            elif line.strip() == 'snapshot':
                print(json.dumps(info()), flush=True)
            elif line.strip() == 'stop':
                break
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
