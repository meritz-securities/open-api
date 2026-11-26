"""A release gate must reject corrupt inputs even with Python assertions disabled."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'packaging/check_build_provenance.py'


def fixture(tmp_path):
    build = tmp_path / 'build'
    bundle = build / 'dist/meritz-strategy-platform'
    bundle.mkdir(parents=True)
    (bundle / 'meritz-strategy-platform').write_bytes(b'independent artifact fixture')
    work = build / 'work/meritz-strategy-platform'
    work.mkdir(parents=True)
    env = tmp_path / 'env'
    module = env / 'lib/python3.11/site-packages/example.py'
    toc = work / 'PYZ-00.toc'
    toc.write_text(repr(('unused', [('example', str(module), 'PYMODULE')])))
    manifest = {'changedDuringBuild': [], 'status': 'UNSIGNED_REVIEW_CANDIDATE',
                'dependencies': {'example': '1.0'}, 'sourceHashes': {'LICENSE': hashlib.sha256((ROOT / 'LICENSE').read_bytes()).hexdigest()},
                'command': [str(env / 'bin/python')], 'standardLibraryRoot': str(tmp_path / 'stdlib'),
                'sourceRoot': str(ROOT), 'os': 'Darwin',
                'bundleHashes': {'meritz-strategy-platform': hashlib.sha256((bundle / 'meritz-strategy-platform').read_bytes()).hexdigest()}}
    audit = {'dependencies': [{'name': 'example', 'version': '1.0', 'vulns': []}]}
    return build, bundle, toc, manifest, audit


@pytest.mark.parametrize('optimized', [False, True])
@pytest.mark.parametrize('case,error', [
    ('valid', None),
    ('version', 'Audit differs from build environment'),
    ('advisory', 'Audit contains advisories or incomplete entries'),
    ('duplicate', 'Duplicate audit packages'),
    ('source', 'Changed source: LICENSE'),
    ('foreign_module', 'Module outside permitted build roots'),
    ('foreign_package', 'Third-party module outside audited environment'),
    ('changed_during_build', 'Source changed during build'),
    ('changed_binary', 'Bundle changed after build'),
])
def test_provenance_gate(tmp_path, optimized, case, error):
    build, bundle, toc, manifest, audit = fixture(tmp_path)
    if case == 'version': audit['dependencies'][0]['version'] = '2.0'
    if case == 'advisory': audit['dependencies'][0]['vulns'] = [{'id': 'TEST-ADVISORY'}]
    if case == 'duplicate': audit['dependencies'].append(audit['dependencies'][0].copy())
    if case == 'source': manifest['sourceHashes']['LICENSE'] = '0' * 64
    if case == 'foreign_module':
        modules = [('example', str(tmp_path / 'env/lib/python3.11/site-packages/example.py'), 'PYMODULE'),
                   ('unexpected', '/outside/audited/roots/evil.py', 'PYMODULE')]
        toc.write_text(repr(('unused', modules)))
    if case == 'foreign_package': toc.write_text(repr(('unused', [('evil', '/other/site-packages/evil.py', 'PYMODULE')])))
    if case == 'changed_during_build': manifest['changedDuringBuild'] = ['LICENSE']
    if case == 'changed_binary': (bundle / 'meritz-strategy-platform').write_bytes(b'changed')
    (build / 'build-manifest.json').write_text(json.dumps(manifest))
    audit_path = tmp_path / 'audit.json'; audit_path.write_text(json.dumps(audit))
    command = [sys.executable] + (['-O'] if optimized else []) + [str(CHECKER), '--source', str(ROOT), '--build', str(build), '--audit', str(audit_path)]
    result = subprocess.run(command, text=True, capture_output=True)
    if error:
        assert result.returncode != 0
        assert result.stderr.strip() == error, (command, result.stderr)
        assert 'PASS' not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output['status'] == 'PASS_SCOPED_BUILD_PROVENANCE'
        assert output['checkerSha256'] == hashlib.sha256(CHECKER.read_bytes()).hexdigest()
