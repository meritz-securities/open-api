"""Timezone packaging checks, including fail-closed missing/corrupt data gates."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('timezone_support', ROOT / 'packaging/timezone_support.py')
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def test_pins_lock_and_native_snapshot_agree():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    lock = tomllib.loads((ROOT / 'uv.lock').read_text())
    version = support.TZDATA_VERSION
    assert f'tzdata=={version}' in project['project']['dependencies']
    assert f'tzdata=={version}' in (ROOT / 'packaging/macos-arm64-build-requirements.txt').read_text().splitlines()
    locked = next(p for p in lock['package'] if p['name'] == 'tzdata')
    assert locked['version'] == version
    assert locked['wheels'][0]['hash'].removeprefix('sha256:') in (ROOT / 'packaging/TZDATA-NOTICES.txt').read_text()


def test_empty_os_tzpath_uses_packaged_timezone_and_dst():
    result = subprocess.run([sys.executable, str(ROOT / 'packaging/timezone_support.py')],
                            env={**os.environ, 'PYTHONTZPATH': ''}, text=True, capture_output=True, check=True)
    evidence = json.loads(result.stdout)
    assert evidence['osTzdbDisabled'] is True
    assert evidence['incomingTZPATH'] == []
    assert evidence['incomingPYTHONTZPATH'] == ''
    assert evidence['incomingOsTzdbDisabled'] is True
    assert evidence['resetTZPATH'] == []
    assert evidence['offsetCases'] == 6
    assert evidence['version'] == support.TZDATA_VERSION
    assert len(evidence['files']) == 3


@pytest.mark.parametrize('optimized', [False, True])
@pytest.mark.parametrize('case', ['valid', 'missing_zone', 'changed_zone', 'missing_license', 'changed_license'])
def test_bundle_timezone_gate(tmp_path, optimized, case):
    files = {support.RESOURCE: b'TZif independent fixture',
             f'tzdata-{support.TZDATA_VERSION}.dist-info/licenses/LICENSE': b'independent license fixture'}
    for relative, value in files.items():
        path = tmp_path / '_internal' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    evidence = {'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    if case != 'valid':
        target = tmp_path / '_internal' / (support.RESOURCE if case.endswith('zone') else list(files)[1])
        if case.startswith('missing'): target.unlink()
        else: target.write_bytes(b'changed')
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'packaging')!r}); from timezone_support import validate_bundle; validate_bundle({str(tmp_path)!r}, {evidence!r})"
    result = subprocess.run([sys.executable] + (['-O'] if optimized else []) + ['-c', code], capture_output=True, text=True)
    assert (result.returncode == 0) == (case == 'valid')
    if case != 'valid': assert 'Bundled timezone data/license missing or changed' in result.stderr


def test_probe_distinguishes_incoming_search_path_from_its_reset(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'packaging/timezone_support.py')],
                            env={**os.environ, 'PYTHONTZPATH': str(tmp_path)}, text=True, capture_output=True, check=True)
    evidence = json.loads(result.stdout)
    assert evidence['incomingTZPATH'] == [str(tmp_path)]
    assert evidence['incomingPYTHONTZPATH'] == str(tmp_path)
    assert evidence['incomingOsTzdbDisabled'] is False
    assert evidence['osTzdbDisabled'] is True
    assert evidence['resetTZPATH'] == []
