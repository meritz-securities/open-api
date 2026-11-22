"""Isolated real HTTP/MCP grid fixture. Generated bars; no account registry."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading

from fastmcp import Client, FastMCP
from runtime.mcp import register_platform
from runtime.service import StrategyRuntime
from runtime.server import create_server

page = Path(sys.argv[1]).read_bytes()
with tempfile.TemporaryDirectory(prefix='meritz-grid-browser-') as directory:
    runtime = StrategyRuntime(directory)
    definition = {'schemaVersion': 1, 'kind': 'dsl', 'name': '생성 시세 · 조건 비교 시험',
                  'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'},
                            'right': {'type': 'constant', 'value': 0}},
                  'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'},
                           'right': {'type': 'constant', 'value': 0}},
                  'quantity': 1, 'allowPyramiding': False}
    revision = runtime.save_strategy(definition)
    dataset = runtime.import_data([
        {'date': f'2025-01-0{index+1}', 'open': price, 'high': price, 'low': price, 'close': price}
        for index, price in enumerate([100, 110, 120])
    ], {'kind': 'imported', 'label': 'GENERATED TEST ONLY · 100, 110, 120'})
    baseline = runtime.run(revision['id'], dataset['id'],
                           {'initialCash': 1000, 'feeBps': 0, 'taxBps': 0, 'slippageBps': 0})
    assert baseline['report']['metrics']['finalEquity'] == 1010
    original_revision = runtime.get_strategy(revision['id'])
    original_data = runtime.store.get('dataset', dataset['id'])

    def start():
        server = create_server(runtime, page=page)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def unchanged():
        return (runtime.get_run(baseline['id']) == baseline and
                runtime.get_strategy(revision['id']) == original_revision and
                runtime.store.get('dataset', dataset['id']) == original_data)

    async def mcp_replay():
        original_record = runtime.store.list('validation')[0]
        original_hashes = [runtime.get_run(item['result']['runId'])['manifest']['resultHash']
                           for item in original_record['result']['results']]
        server = FastMCP('grid-browser-parity')
        register_platform(server, None, None, runtime)
        async with Client(server) as client:
            response = await client.call_tool('meritz_strategy_robustness', {
                'runId': baseline['id'], 'method': 'parameter_grid',
                'options': {'axes': [{'path': 'quantity', 'values': [2, 3]}]},
            })
            record = response.data
            hashes = [runtime.get_run(item['result']['runId'])['manifest']['resultHash']
                      for item in record['result']['results']]
            return {'record': record, 'hashes': hashes, 'originalHashes': original_hashes, 'originalUnchanged': unchanged()}

    server, thread = start()
    print(json.dumps({'url': server.issue_ticket(), 'baselineId': baseline['id'],
                      'baselineEquity': 1010}), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == 'reopen':
                server.shutdown(); server.server_close(); thread.join(timeout=5)
                runtime = StrategyRuntime(directory)
                server, thread = start()
                print(json.dumps({'url': server.issue_ticket(), 'originalUnchanged': unchanged()}), flush=True)
            elif line.strip() == 'mcp':
                print(json.dumps(asyncio.run(mcp_replay())), flush=True)
            elif line.strip() == 'stop':
                break
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
