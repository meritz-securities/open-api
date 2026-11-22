"""CLI 는 기본적으로 원인을 숨기고, MERITZ_CLI_DIAGNOSTICS=1 일 때만 stderr 로 예외 문구를 낸다."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, extra_env):
    env = {k: v for k, v in os.environ.items() if not k.startswith('MERITZ_')}
    env.update(MERITZ_NO_ENV_FILE='1', **extra_env)
    # 존재하지 않는 revision 으로 run 을 부르면 ValueError 경로를 탄다.
    return subprocess.run([sys.executable, '-m', 'runtime.cli', 'run', '--root', str(tmp_path),
                           '--arguments', json.dumps({'revisionId': 'missing', 'datasetId': 'missing'})],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


def test_cli_hides_cause_by_default(tmp_path):
    result = _run(tmp_path, {})
    assert result.returncode == 1
    assert json.loads(result.stdout)['error']['code'] == 'INVALID_REQUEST'
    assert result.stderr.strip() == ''


def test_cli_diagnostics_env_writes_cause_to_stderr_only(tmp_path):
    result = _run(tmp_path, {'MERITZ_CLI_DIAGNOSTICS': '1'})
    assert result.returncode == 1
    assert json.loads(result.stdout)['error']['code'] == 'INVALID_REQUEST'
    assert result.stderr.strip() == 'ValueError: Snapshot not found'
