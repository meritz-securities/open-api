"""Actual Runtime/Store fixture: synthetic bars, injected failure/cancel, no keys."""
import copy
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path
from runtime.service import StrategyRuntime
from runtime.server import create_server
from runtime.storage import digest, encode_body


def no_outbound(*args, **kwargs):
    raise RuntimeError('Outbound connection forbidden in fixture')


socket.socket.connect = no_outbound


class FixtureInterruption(BaseException):
    """Fixture-only abrupt engine exit; NOT an actual OS process crash."""


class FixtureRuntime(StrategyRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_lookup = False
        self.lookup_started = threading.Event()
        self.lookup_release = threading.Event()

    def get_strategy(self, revisionId):
        if self.delay_lookup:
            self.lookup_started.set()
            if not self.lookup_release.wait(15):
                raise ValueError('Fixture lookup timed out')
        return super().get_strategy(revisionId)

    def _calculate(self, definition, data, settings, cancel=None):
        if definition['name'] == '외부 중단 코드 후보':
            raise FixtureInterruption()
        if definition['name'] in ('외부 실패 코드 후보', '외부 취소 후보'):
            if definition['name'] == '외부 취소 후보' and cancel is not None:
                cancel.set()
            raise ValueError('Injected engine failure; no real Python execution')
        return super()._calculate(definition, data, settings, cancel)


with tempfile.TemporaryDirectory(prefix='meritz-candidate-browser-') as root:
    service = FixtureRuntime(root, ai_studio=True)
    definition = copy.deepcopy(service.templates()['templates'][0])
    definition['name'] = '원본 전략'
    revision = service.save_strategy(definition)
    dataset = service.demo_data()
    baseline = service.run(revision['id'], dataset['id'])
    baseline_hash = digest(baseline)
    server = create_server(service, page=Path(sys.argv[1]).read_bytes())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(json.dumps({'url': server.issue_ticket(), 'baseline': baseline}), flush=True)
    try:
        for line in sys.stdin:
            command = line.strip()
            if command == 'stop':
                break
            if command == 'external':
                external = FixtureRuntime(root)
                proposed = copy.deepcopy(definition)
                proposed['name'] = '외부 성공 후보'
                candidate = external.experiment(baseline['id'], proposed, {'initialCash': 2345678, 'feeBps': 7, 'slippageBps': 4, 'taxBps': 3})
                failures = []
                for name in ('외부 실패 코드 후보', '외부 취소 후보', '외부 중단 코드 후보'):
                    code = {'schemaVersion': 1, 'kind': 'python', 'name': name,
                            'code': 'def on_bar(ctx):\n    return {"action": "hold", "quantity": 0, "reason": "fixture"}\n'}
                    try:
                        external.experiment(baseline['id'], code)
                    except ValueError as error:
                        failures.append(external.get_run(error.run_id))
                    except FixtureInterruption:
                        # _run_locked wrote the real running attempt before the
                        # fixture BaseException bypassed Exception handling.
                        orphan = next(r for r in external.list_runs()['runs'] if r['status'] == 'running')
                        # Actual Store initialization/lock recovery, not a direct
                        # assignment of interrupted status in the fixture.
                        external = FixtureRuntime(root)
                        interrupted = external.get_run(orphan['id'])
                        assert interrupted['status'] == 'interrupted'
                        # Simulate an interrupted record written by a pre-localization Runtime.
                        interrupted['error'] = 'Previous execution stopped before finalization'
                        status, dataset_id, summary = external.store.metadata(interrupted)
                        with external.store.connection() as con:
                            con.execute('UPDATE objects SET body=?,status=?,dataset_id=?,summary=? WHERE kind=? AND id=?',
                                        (encode_body(interrupted), status, dataset_id, summary, 'run', interrupted['id']))
                validation = external.validate_robustness(baseline['id'], 'walk_forward', {'candidateRevisionIds': [], 'trainBars': 80, 'testBars': 20})
                derived = [r['id'] for r in external.list_runs()['runs'] if r.get('validationJobId') == validation['jobId']]
                for index in range(105):
                    filler = copy.deepcopy(definition)
                    filler['name'] = f'목록 채움 {index}'
                    external.save_strategy(filler)
                    external.import_data([{'date': '2025-01-02', 'open': 100, 'high': 102, 'low': 99, 'close': 101}, {'date': '2025-01-03', 'open': 101, 'high': 103, 'low': 100, 'close': 102}], {'kind': 'imported', 'label': f'목록 채움 {index}'})
                response = {'candidate': candidate, 'failed': failures[0], 'cancelled': failures[1], 'interrupted': interrupted, 'derived': derived,
                            'revisionIds': [r['id'] for r in external.list_strategies()['strategies']], 'datasetIds': [d['id'] for d in external.list_data()['datasets']]}
            elif command == 'delay_lookup':
                service.lookup_started.clear()
                service.lookup_release.clear()
                service.delay_lookup = True
                response = {'delayed': True}
            elif command == 'wait_lookup':
                response = {'started': service.lookup_started.wait(10)}
            elif command == 'release_lookup':
                service.delay_lookup = False
                service.lookup_release.set()
                response = {'released': True}
            elif command == 'stats':
                response = {'runs': service.list_runs()['runs'], 'baselineHash': digest(service.get_run(baseline['id'])), 'initialBaselineHash': baseline_hash}
            elif command == 'ticket':
                response = {'url': server.issue_ticket()}
            else:
                raise ValueError('Unknown fixture command')
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        server.shutdown()
        server.server_close()
