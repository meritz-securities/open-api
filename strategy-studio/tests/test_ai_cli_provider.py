"""Actual subprocess fixtures, NOT actual Claude/model invocation evidence."""
import dataclasses
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from runtime.ai_cli_provider import ProviderConfig, ProviderError, generate_candidates

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX-only initial provider')


def definition():
    return {'schemaVersion': 1, 'kind': 'dsl', 'name': 'candidate',
            'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'},
                      'right': {'type': 'constant', 'value': 0}},
            'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'},
                     'right': {'type': 'constant', 'value': 0}}}


def output(candidates=None):
    return json.dumps({'is_error': False, 'structured_output': {
        'candidates': [definition()] if candidates is None else candidates}})


def fake(tmp_path, body, version='2.1.322 (Claude Code)'):
    executable = tmp_path / 'fake claude; no shell'
    executable.write_text(f'#!{sys.executable}\nimport sys, os, json, time\n'
                          f'if sys.argv[1:] == ["--version"]:\n print({version!r}); sys.exit(0)\n' + body)
    executable.chmod(0o700)
    return ProviderConfig(str(executable), 'MODEL_SECRET', timeout_seconds=3)


def test_success_isolated_env_stdin_flags_and_no_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv('MERITZ_APP_KEY', 'MERITZ_SECRET')
    monkeypatch.setenv('HOST_TOKEN', 'HOST_SECRET')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://attacker.invalid')
    proof = tmp_path / 'proof.json'
    body = f'''from pathlib import Path
assert sys.stdin.read() == 'approved context'
assert os.environ['ANTHROPIC_API_KEY'] == 'MODEL_SECRET'
assert not any(k in os.environ for k in ('MERITZ_APP_KEY', 'HOST_TOKEN', 'ANTHROPIC_BASE_URL', 'PATH'))
assert Path(os.environ['HOME']).resolve() == Path.cwd().resolve()
assert list(Path.cwd().iterdir()) == []
a = sys.argv
for flag in ('--bare', '-p', '--strict-mcp-config', '--disable-slash-commands', '--no-session-persistence'):
 assert flag in a
assert a[a.index('--tools')+1] == ''
assert json.loads(a[a.index('--mcp-config')+1]) == {{'mcpServers':{{}}}}
schema=json.loads(a[a.index('--json-schema')+1])
assert schema['properties']['candidates']['maxItems'] == 3
assert schema['properties']['candidates']['items']['properties']['kind']['const'] == 'dsl'
Path({str(proof)!r}).write_text(json.dumps({{'cwd':os.getcwd(),'args':a}}))
print({output()!r})
'''
    config = fake(tmp_path, body)
    candidates = generate_candidates(config, 'approved context')
    assert candidates[0]['name'] == 'candidate'
    record = json.loads(proof.read_text())
    assert not Path(record['cwd']).exists()
    assert 'MODEL_SECRET' not in str(record['args'])
    assert 'MODEL_SECRET' not in repr(config)


@pytest.mark.parametrize('raw', ['not json', '{}', '{"is_error":true}',
    '{"is_error":false,"structured_output":{"candidates":[]}}',
    '{"is_error":false,"is_error":false}',
    output([{'schemaVersion':1, 'kind':'python','name':'bad','code':'raise Exception()'}]),
    output([definition()] * 4),
    output([{**definition(), 'unknown': 'x'}]),
    output([{**definition(), 'quantity': float('nan')}]),
    output([{**definition(), 'name': 'x' * 100_000}] * 3)])
def test_invalid_outputs_fixed_error(tmp_path, raw):
    config = fake(tmp_path, f'print({raw!r})\n')
    with pytest.raises(ProviderError, match='^PROVIDER_INVALID_OUTPUT$'):
        generate_candidates(config, 'goal')


@pytest.mark.parametrize('channel', ['stdout', 'stderr'])
def test_infinite_output_bounded(tmp_path, channel):
    config = fake(tmp_path, f'while True: os.write({1 if channel == "stdout" else 2}, b"x" * 4096)\n')
    config = dataclasses.replace(config, stdout_limit=8192, stderr_limit=8192)
    with pytest.raises(ProviderError, match='^PROVIDER_OUTPUT_LIMIT$'):
        generate_candidates(config, 'goal')


def test_timeout_and_no_child_error_disclosure(tmp_path):
    config = fake(tmp_path, 'sys.stderr.write("MODEL_SECRET MERITZ_SECRET"); sys.stderr.flush(); time.sleep(60)\n')
    config = dataclasses.replace(config, timeout_seconds=.3)
    started = time.monotonic()
    with pytest.raises(ProviderError, match='^PROVIDER_TIMEOUT$'):
        generate_candidates(config, 'goal')
    assert time.monotonic() - started < 3
    config = fake(tmp_path, 'print("MODEL_SECRET"); sys.exit(7)\n')
    with pytest.raises(ProviderError, match='^PROVIDER_FAILED$'):
        generate_candidates(config, 'goal')


def test_cancel_and_pre_cancel(tmp_path):
    config = fake(tmp_path, 'time.sleep(60)\n')
    event = threading.Event()
    timer = threading.Timer(.2, event.set)
    timer.start()
    try:
        with pytest.raises(ProviderError, match='^PROVIDER_CANCELLED$'):
            generate_candidates(config, 'goal', event)
    finally:
        timer.join()
    config = fake(tmp_path, 'raise AssertionError("must not start")\n')
    marker = tmp_path / 'pre-cancel-started'
    executable = Path(config.executable)
    lines = executable.read_text().splitlines(keepends=True)
    lines.insert(1, f'open({str(marker)!r}, "w").close()\n')
    executable.write_text(''.join(lines))
    with pytest.raises(ProviderError, match='^PROVIDER_CANCELLED$'):
        generate_candidates(config, 'goal', event)
    assert not marker.exists()


def test_process_group_kills_descendant_inheriting_pipes(tmp_path):
    marker = tmp_path / 'descendant-survived'
    body = f'''pid = os.fork()
if pid == 0:
 time.sleep(1)
 open({str(marker)!r}, 'w').write('bad')
 os._exit(0)
os._exit(0)
'''
    config = dataclasses.replace(fake(tmp_path, body), timeout_seconds=.3)
    with pytest.raises(ProviderError, match='^PROVIDER_TIMEOUT$'):
        generate_candidates(config, 'goal')
    time.sleep(1)
    assert not marker.exists()


@pytest.mark.parametrize('change,code', [
    ({'executable':'relative'}, 'PROVIDER_NOT_CONFIGURED'),
    ({'api_key':''}, 'PROVIDER_NOT_CONFIGURED'),
    ({'budget_usd':float('inf')}, 'PROVIDER_CONFIG_INVALID'),
    ({'budget_usd':11}, 'PROVIDER_CONFIG_INVALID'),
    ({'timeout_seconds':True}, 'PROVIDER_CONFIG_INVALID'),
    ({'stdout_limit':1_000_001}, 'PROVIDER_CONFIG_INVALID')])
def test_config_rejected(tmp_path, change, code):
    config = dataclasses.replace(fake(tmp_path, 'raise AssertionError()\n'), **change)
    with pytest.raises(ProviderError, match=f'^{code}$'):
        generate_candidates(config, 'goal')


def test_unapproved_version_does_not_generate(tmp_path):
    config = fake(tmp_path, 'raise AssertionError()\n', version='2.1.999 (Claude Code)')
    with pytest.raises(ProviderError, match='^PROVIDER_VERSION_UNSUPPORTED$'):
        generate_candidates(config, 'goal')


def test_prompt_size_and_unicode_rejected(tmp_path):
    config = fake(tmp_path, 'raise AssertionError()\n')
    for prompt in ('', '\ud800', 'x' * 120_001, None):
        with pytest.raises(ProviderError, match='^PROVIDER_INVALID_PROMPT$'):
            generate_candidates(config, prompt)


def test_three_candidates_normalized_without_execution(tmp_path):
    config = fake(tmp_path, f'print({output([definition()] * 3)!r})\n')
    result = generate_candidates(config, 'goal')
    assert len(result) == 3
    assert all(candidate['quantity'] is None for candidate in result)
    assert all(candidate['kind'] == 'dsl' for candidate in result)


def test_invalid_utf8_and_duplicate_nested_keys(tmp_path):
    for body in ('os.write(1, b"\\xff")\n',
                 'print(\'{"is_error":false,"structured_output":{"candidates":[],"candidates":[]}}\')\n'):
        with pytest.raises(ProviderError, match='^PROVIDER_INVALID_OUTPUT$'):
            generate_candidates(fake(tmp_path, body), 'goal')


def test_windows_refused_before_process_creation(tmp_path, monkeypatch):
    from runtime import ai_cli_provider
    config = fake(tmp_path, 'raise AssertionError()\n')
    monkeypatch.setattr(ai_cli_provider.os, 'name', 'nt')
    with pytest.raises(ProviderError, match='^PROVIDER_PLATFORM_UNSUPPORTED$'):
        generate_candidates(config, 'goal')


@pytest.mark.parametrize('model', ['--dangerously-skip-permissions', '-x', '.model'])
def test_model_cannot_be_option(tmp_path, model):
    config = dataclasses.replace(fake(tmp_path, 'raise AssertionError()\n'), model=model)
    with pytest.raises(ProviderError, match='^PROVIDER_CONFIG_INVALID$'):
        generate_candidates(config, 'goal')


def test_invalid_output_exception_has_no_child_context(tmp_path):
    config = fake(tmp_path, 'print("CHILD_SECRET_OUTPUT")\n')
    with pytest.raises(ProviderError) as error:
        generate_candidates(config, 'goal')
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert 'CHILD_SECRET_OUTPUT' not in repr(error.value)


@pytest.mark.parametrize('cancelled', [False, True])
def test_escaped_group_pipes_cannot_block_cleanup(tmp_path, cancelled):
    import signal
    pid_file = tmp_path / 'escaped-pid'
    body = f'''pid = os.fork()
if pid == 0:
 os.setsid()
 with open({str(pid_file)+'.tmp'!r}, 'w') as proof:
  proof.write(str(os.getpid()))
 os.replace({str(pid_file)+'.tmp'!r}, {str(pid_file)!r})
 time.sleep(30)
 os._exit(0)
time.sleep(30)
'''
    # Timeout includes the version probe and process launch: allow startup margin.
    config = dataclasses.replace(fake(tmp_path, body), timeout_seconds=8 if cancelled else 3)
    event, ready, stop = threading.Event(), threading.Event(), threading.Event()
    child = []
    def await_ready():
        deadline = time.monotonic() + 5
        while not stop.is_set() and time.monotonic() < deadline:
            if pid_file.exists():
                # Atomic rename prevents a half-written PID. Verify setsid has
                # actually completed before signalling cancellation or success.
                pid = int(pid_file.read_text())
                if pid > 0:
                    try:
                        if os.getpgid(pid) == pid:
                            child.append(pid)
                            ready.set()
                            if cancelled:
                                event.set()
                            return
                    except ProcessLookupError:
                        return
            stop.wait(.01)
    watcher = threading.Thread(target=await_ready)
    watcher.start()
    started = time.monotonic()
    try:
        with pytest.raises(ProviderError, match='^PROVIDER_CANCELLED$' if cancelled else '^PROVIDER_TIMEOUT$'):
            generate_candidates(config, 'goal', event)
        elapsed = time.monotonic() - started
        assert elapsed < config.timeout_seconds + 1.5
        assert ready.is_set(), 'the regression must exercise a ready escaped session'
        assert child and int(pid_file.read_text()) == child[0]
    finally:
        # Let readiness finish if timeout raced with a delayed child publishing
        # its atomic file. Join before cleanup so the watcher cannot act later.
        watcher.join(timeout=5)
        stop.set()
        watcher.join(timeout=1)
        pid = child[0] if child else int(pid_file.read_text()) if pid_file.exists() else None
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        assert not watcher.is_alive()


def test_invalid_unicode_prompt_exception_has_no_sensitive_context(tmp_path, monkeypatch):
    from runtime import ai_cli_provider
    config = fake(tmp_path, 'raise AssertionError("must not start")\n')
    monkeypatch.setattr(ai_cli_provider, '_run', lambda *a, **k: pytest.fail('invalid prompt started a subprocess'))
    prompt = 'APPROVED_PROMPT_TEXT \ud800'
    with pytest.raises(ProviderError, match='^PROVIDER_INVALID_PROMPT$') as error:
        generate_candidates(config, prompt)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert 'APPROVED_PROMPT_TEXT' not in repr(error.value)
