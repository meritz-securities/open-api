"""Race two coordinator processes; send callbacks are fake, locks/engine are real."""
import multiprocessing
import os
from pathlib import Path
import sys
import time

import pytest

from runtime.service import StrategyRuntime
from runtime.storage import digest


def _compete(root, request_id, definition, barrier, queue, transport, delay):
    from runtime import ai_generation
    from runtime.ai_cli_provider import ProviderConfig
    from runtime.ai_http_provider import HttpProviderConfig
    from runtime.ai_session import AISession

    service = StrategyRuntime(root, allow_ai_connection=True)
    marker = Path(root) / 'fake-model-attempts.txt'

    def send(config, prompt, cancel):
        with marker.open('a') as out:
            out.write(transport + '\n')
        time.sleep(.05)  # Keep the winning call alive while its competitor tries.
        return [definition]

    if transport == 'cli':
        service._ai_provider = ProviderConfig(executable=sys.executable, api_key='FAKE_RACE_KEY',
                                              model='race-model')
        ai_generation.generate_candidates = send  # No CLI/model process is invoked.
    else:
        service._ai_studio.session = AISession(config_factory=HttpProviderConfig, send=send,
                                               models=['race-model'], management_allowed=True)
        connection = service._ai_studio.connect_ai_session(apiKey='FAKE_RACE_KEY', model='race-model')
    try:
        barrier.wait(timeout=15)
        time.sleep(delay)
        if transport == 'cli':
            preview = service.preview_ai_generation(request_id)
            generate = lambda: service.generate_ai_candidates(request_id, preview['promptHash'], preview['consentVersion'])
        else:
            preview = service._ai_studio.preview_ai_http_improvement(
                requestId=request_id, connectionId=connection['configuration']['connectionId'])
            generate = lambda: service._ai_studio.generate_ai_http_improvement(
                requestId=request_id, promptHash=preview['promptHash'], consentVersion=preview['consentVersion'])
        # A preview can win the request gate while the loser briefly holds the
        # process-wide generation lock. Repeating the same consent is safe.
        deadline = time.monotonic() + 10
        while True:
            try:
                result = generate()
                queue.put({'transport': transport, 'status': result['status']})
                break
            except ValueError as error:
                if str(error) != 'Another AI generation is active' or time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
    except Exception as error:
        queue.put({'transport': transport, 'error': str(error)})


@pytest.mark.skipif(os.name != 'posix', reason='Coordinator process locks are POSIX-only')
@pytest.mark.parametrize('head_start', ['cli', 'http'])
def test_cli_and_http_processes_never_double_generate_same_request(tmp_path, head_start):
    service = StrategyRuntime(tmp_path)
    definition = {'schemaVersion': 1, 'kind': 'dsl', 'name': 'Race fixture',
                  'entry': {'op': 'gt', 'left': {'type': 'constant', 'value': 1},
                            'right': {'type': 'constant', 'value': 0}},
                  'exit': {'op': 'lt', 'left': {'type': 'constant', 'value': 1},
                           'right': {'type': 'constant', 'value': 0}},
                  'quantity': 1, 'allowPyramiding': False}
    revision = service.save_strategy(definition)
    data = service.import_data([
        {'date': f'2025-01-0{i+1}', 'open': p, 'high': p, 'low': p, 'close': p}
        for i, p in enumerate([10, 20, 30, 40])], {'kind': 'imported', 'label': 'race test prices'})
    baseline = service.run(revision['id'], data['id'],
                           {'initialCash': 100, 'feeBps': 0, 'slippageBps': 0, 'taxBps': 0})
    original_hash = digest(baseline)
    request_id = service.create_ai_request(baseline['id'], 'Concurrent provider test')['request']['id']
    context = multiprocessing.get_context('spawn')
    barrier, queue = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_compete,
        args=(str(tmp_path), request_id, definition, barrier, queue, name,
              0 if name == head_start else .02)) for name in ('cli', 'http')]
    try:
        for process in processes:
            process.start()
        outcomes = [queue.get(timeout=25) for _ in processes]
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0, outcomes
        assert sum(row.get('status') == 'succeeded' for row in outcomes) == 1, outcomes
        loser = next(row for row in outcomes if 'error' in row)
        assert loser['error'] == 'AI_GENERATION_PROVIDER_CONFLICT', outcomes
        attempts = (tmp_path / 'fake-model-attempts.txt').read_text().splitlines()
        assert len(attempts) == 1, outcomes
        stored = service.get_ai_request(request_id)
        assert stored['status'] == 'succeeded' and len(stored['attempts']) == 1
        child = service.get_run(stored['attempts'][0]['runId'])
        assert child['report']['metrics']['finalEquity'] == 120
        assert child['datasetId'] == baseline['datasetId'] and child['settings'] == baseline['settings']
        assert stored['submission']['origin'] == ('local-cli-generated' if attempts[0] == 'cli' else 'local-http-generated')
        assert digest(service.get_run(baseline['id'])) == original_hash
        assert len(service.store.list('run')) == 2
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        queue.close()
        queue.join_thread()
