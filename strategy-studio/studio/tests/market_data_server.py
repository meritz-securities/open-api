"""Browser-only fixture: actual Runtime/adapter/Registry selection, fake provider.

No profiles, keys or user store are read. Every outbound socket connection fails.
Fixture OHLC is invented for tests, never evidence of a live Meritz response.
"""
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from meritz.accounts import Registry
from meritz.catalog import load_catalog
from runtime.service import StrategyRuntime
from runtime.server import create_server


def forbid_outbound(*args, **kwargs):
    raise RuntimeError('Outbound connections forbidden in market-data browser fixture')


socket.socket.connect = forbid_outbound


class FixtureClient:
    def __init__(self, profile):
        self.s = profile
        self.calls = []

    def call(self, api, params):
        if api.key == 'ovs_market_candles_days':
            assert api.method == 'GET'
            assert params['mrkt_div_code'] == 'OV'
            assert params['mrkt_cls_code'] == 'AX'
            assert params['dely_rltm_cls_code'] == '1'
            self.calls.append({'key': api.key, 'params': params})
            rows = [{'date': f'202501{day:02d}', 'oprc': '20.25', 'hprc': '22.75',
                     'lprc': '19.50', 'prpr': '21.125', 'dcpn_cnt': '3'}
                    for day in range(2, 10)]
            return {'ok': True, 'verified': False, 'body': {'data': rows}}
        assert api.key == 'market_candles_days' and api.method == 'GET'
        assert params['mrkt_div_code'] == 'TJ' and params['exch_use_yn'] == 'Y'
        self.calls.append({'key': api.key, 'params': params})
        rows = []
        for day in range(2, 10):
            rows.append({'date': f'202501{day:02d}', 'oprc': 100 + day,
                         'hprc': 104 + day, 'lprc': 98 + day, 'stck_clpr': 102 + day})
        rows += [dict(rows[0]), {'date': '20241231', 'oprc': 100, 'hprc': 101, 'lprc': 99, 'stck_clpr': 100}]
        return {'ok': True, 'verified': False, 'body': {'data': rows}}


class FixtureRegistry(Registry):
    def __init__(self):
        # Bypass Registry.__init__/load_profiles/ApiClient entirely. Exercise the
        # real inherited resolve() policy with explicit fake profiles/client.
        self.default = SimpleNamespace(label='fixture-default', has_credentials=True)
        self.profiles = [self.default]
        self.client = FixtureClient(self.default)
        self._clients = {self.default.label: self.client}
        self.resolutions = []

    def resolve(self, account, *, require_explicit):
        self.resolutions.append({'account': account, 'require_explicit': require_explicit})
        return super().resolve(account, require_explicit=require_explicit)


with tempfile.TemporaryDirectory(prefix='meritz-market-browser-fixture-') as directory:
    registry = FixtureRegistry()
    service = StrategyRuntime(directory, catalog=load_catalog(), registry=registry)
    server = create_server(service, page=Path(sys.argv[1]).read_bytes())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({'url': server.issue_ticket(), 'fixture': True}), flush=True)
    try:
        for line in sys.stdin:
            command = line.strip()
            if command == 'stop':
                break
            if command == 'external':
                # A separate Runtime instance writes the same shared store, as an
                # external MCP process would; no UI request creates these items.
                external = StrategyRuntime(directory)
                definition = external.templates()['templates'][0]
                definition['name'] = '외부 Runtime 생성 전략'
                revision = external.save_strategy(definition)
                data = external.demo_data()
                run = external.run(revision['id'], data['id'])
                response = {'revision': revision, 'dataset': data, 'run': run}
            elif command == 'multiple':
                registry.profiles = [registry.default, SimpleNamespace(label='fixture-second')]
                response = {'mode': 'multiple'}
            elif command == 'single':
                registry.profiles = [registry.default]
                response = {'mode': 'single'}
            elif command == 'stats':
                response = {'calls': registry.client.calls, 'resolutions': registry.resolutions}
            else:
                raise ValueError('Unsupported fixture command')
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        server.shutdown()
        server.server_close()
