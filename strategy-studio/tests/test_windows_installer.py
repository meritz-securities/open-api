"""Structural source-generator tests. Synthetic PE bytes are NOT Windows binaries.

Nothing here compiles NSIS or establishes Windows installation/runtime behavior.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

import pytest


MODULE = Path(__file__).resolve().parents[1] / 'packaging/windows/prepare_installer.py'
spec = importlib.util.spec_from_file_location('prepare_windows_installer', MODULE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def fake_pe(machine=0x8664):
    """Header-only fixture: never delivered or executed as a real binary."""
    data = bytearray(512)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<HH', data, 68, machine, 1)
    struct.pack_into('<HH', data, 84, 112, 2)
    struct.pack_into('<H', data, 88, 0x20b)
    return bytes(data)


@pytest.fixture
def candidate(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    files = {installer.EXE: fake_pe(), 'LICENSE': b'project license fixture',
             'THIRD-PARTY-NOTICES.txt': b'python notices fixture',
             'PLATFORM-NOTICES.txt': b'studio notices fixture',
             '_internal/sub/library.dat': b'fixture data',
             '_internal/a space value.txt': b'quoted file name'}
    for name, data in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {'status': 'UNSIGNED_REVIEW_CANDIDATE', 'changedDuringBuild': [],
                'os': 'Windows', 'architecture': 'AMD64',
                'bundleHashes': {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}}
    path = tmp_path / 'build-manifest.json'
    path.write_text(json.dumps(manifest))
    return path, bundle, tmp_path / 'prepared', manifest


def rewrite(candidate):
    candidate[0].write_text(json.dumps(candidate[3]))


@pytest.mark.parametrize('name', [
    '/absolute', r'C:\absolute', r'\\host\share', '../escape', 'a/../escape',
    'a//b', './file', 'a/', 'a:stream', 'con', 'nul.txt', 'COM1.exe', 'lpt9.dll',
    'COM¹.txt', 'x/PRN.txt', 'name.', 'name ', 'a/../x', 'x\x00y', 'x\ny',
    'x\x7fy', 'x?y', 'x*y', 'x"y', 'x|y', 'x>y', 'x<y',
    '.env', '.env.local', 'x/.env.production', 'x/state.sqlite', 'state.sqlite3-wal',
    'account.db', 'account.db-shm', 'Uninstall.exe',
    'CON .txt', '${DEFINE}.txt', "single'quote.txt", 'back`tick', 'dollar$.txt',
    'ABCDEF~1.TXT', 'COM0', 'COM0.txt', 'LPT0', 'LPT0.txt',
])
def test_rejects_unsafe_paths(name):
    with pytest.raises(ValueError):
        installer.safe_path(name)


@pytest.mark.parametrize('field,value', [
    ('status', 'SOURCE_CHANGED_REBUILD_REQUIRED'), ('status', 'PASS'),
    ('changedDuringBuild', ['runtime/server.py']), ('changedDuringBuild', None),
    ('os', 'Darwin'), ('architecture', 'arm64'), ('architecture', 'x86'),
    ('bundleHashes', {}), ('bundleHashes', []),
])
def test_rejects_wrong_manifest(candidate, field, value):
    candidate[3][field] = value
    rewrite(candidate)
    with pytest.raises(ValueError):
        installer.prepare(*candidate[:3])
    assert not candidate[2].exists()


@pytest.mark.parametrize('paths', [
    ['LICENSE', 'license'], ['Dir/a', 'dir/b'], ['dir/a', r'dir\a'],
    ['item', 'item/child'],
])
def test_rejects_collisions(candidate, paths):
    candidate[3]['bundleHashes'].update({n: 'a' * 64 for n in paths})
    rewrite(candidate)
    with pytest.raises(ValueError):
        installer.load_manifest(candidate[0])


def test_duplicate_json_key_rejected(candidate):
    candidate[0].write_text('{"status":"x","status":"UNSIGNED_REVIEW_CANDIDATE"}')
    with pytest.raises(ValueError, match='Duplicate JSON'):
        installer.load_manifest(candidate[0])


@pytest.mark.parametrize('data', [b'#!/bin/sh\necho fake', b'MZ', fake_pe(0xaa64), fake_pe(0x14c)])
def test_rejects_non_windows_or_wrong_pe(candidate, data):
    (candidate[1] / installer.EXE).write_bytes(data)
    candidate[3]['bundleHashes'][installer.EXE] = installer.sha(data)
    rewrite(candidate)
    with pytest.raises(ValueError):
        installer.prepare(*candidate[:3])
    assert not candidate[2].exists()


@pytest.mark.parametrize('offset,value,fmt', [(0x3c, 0xffffffff, '<I'), (84, 0, '<H'),
                                           (86, 0x2002, '<H'), (88, 0x10b, '<H')])
def test_rejects_corrupt_pe_headers(offset, value, fmt):
    data = bytearray(fake_pe())
    struct.pack_into(fmt, data, offset, value)
    with pytest.raises(ValueError):
        installer.require_amd64_pe(data)


def test_hash_and_exact_inventory(candidate):
    (candidate[1] / 'LICENSE').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        installer.prepare(*candidate[:3])


def test_extra_payload_rejected(candidate):
    (candidate[1] / 'unlisted.txt').write_text('must not copy')
    with pytest.raises(ValueError, match='file list'):
        installer.prepare(*candidate[:3])
    assert not candidate[2].exists()


def test_missing_file_rejected(candidate):
    (candidate[1] / 'PLATFORM-NOTICES.txt').unlink()
    with pytest.raises(ValueError, match='file list'):
        installer.prepare(*candidate[:3])


def test_missing_license_in_manifest_rejected(candidate):
    candidate[3]['bundleHashes'].pop('LICENSE')
    rewrite(candidate)
    with pytest.raises(ValueError, match='license notices'):
        installer.prepare(*candidate[:3])


@pytest.mark.parametrize('kind', ['file', 'directory', 'output_parent'])
def test_symlinks_rejected(candidate, kind):
    manifest, bundle, output, _ = candidate
    if kind == 'file':
        file = bundle / 'LICENSE'
        file.unlink()
        file.symlink_to(bundle / 'THIRD-PARTY-NOTICES.txt')
    elif kind == 'directory':
        (bundle / 'linked').symlink_to(bundle / '_internal', target_is_directory=True)
    else:
        alias = bundle.parent / 'alias'
        alias.symlink_to(bundle.parent, target_is_directory=True)
        output = alias / 'prepared'
    with pytest.raises(ValueError, match='Symlink/reparse'):
        installer.prepare(manifest, bundle, output)


def test_reparse_attribute_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'fake'
    path.write_text('x')
    original = Path.lstat

    class ReparseInfo:
        st_mode = 0o100644
        st_file_attributes = 0x400

    monkeypatch.setattr(Path, 'lstat', lambda p: ReparseInfo() if p == path else original(p))
    with pytest.raises(ValueError, match='Symlink/reparse'):
        installer.plain(path)


def test_existing_output_is_never_overwritten(candidate):
    candidate[2].mkdir()
    (candidate[2] / 'keep').write_text('customer')
    with pytest.raises(ValueError, match='new path'):
        installer.prepare(*candidate[:3])
    assert (candidate[2] / 'keep').read_text() == 'customer'


def test_snapshot_is_independent_and_prepared_only(candidate):
    result = installer.prepare(*candidate[:3])
    manifest, bundle, output, original = candidate
    assert result['status'] == 'PREPARED_UNTESTED'
    assert result['installerCompiled'] is result['windowsTested'] is result['signed'] is False
    expected_id = installer.sha(json.dumps(original['bundleHashes'], sort_keys=True,
                                           separators=(',', ':')).encode())[:12]
    assert result['identity'] == expected_id
    assert {str(p.relative_to(output / 'payload')) for p in (output / 'payload').rglob('*') if p.is_file()} == set(original['bundleHashes'])
    assert set(p.name for p in output.iterdir()) == {'installer.nsi', 'payload', 'preparation-manifest.json'}
    (bundle / 'LICENSE').write_bytes(b'after snapshot mutation')
    assert (output / 'payload/LICENSE').read_bytes() == b'project license fixture'
    assert result['installerSourceSha256'] == installer.sha((output / 'installer.nsi').read_bytes())
    assert json.loads((output / 'preparation-manifest.json').read_text()) == result


def test_mid_copy_mutation_does_not_finish_preparation(candidate, monkeypatch):
    original = installer.read_plain
    reads = 0

    def mutate(path):
        nonlocal reads
        if path == candidate[1] / 'LICENSE':
            reads += 1
            if reads == 2:
                path.write_bytes(b'changed during copy')
        return original(path)

    monkeypatch.setattr(installer, 'read_plain', mutate)
    with pytest.raises(ValueError, match='changed during snapshot'):
        installer.prepare(*candidate[:3])
    assert not (candidate[2] / 'preparation-manifest.json').exists()


def test_windows_separator_manifest(candidate):
    candidate[3]['architecture'] = 'x86_64'
    candidate[3]['bundleHashes'] = {n.replace('/', '\\'): d for n, d in candidate[3]['bundleHashes'].items()}
    rewrite(candidate)
    _, hashes = installer.validate_bundle(candidate[0], candidate[1])
    assert '_internal/sub/library.dat' in hashes
    installer.prepare(*candidate[:3])


def test_manifest_parse_and_digest_share_one_byte_snapshot(candidate, monkeypatch):
    """Regression: A -> B -> A reads formerly recorded A but used B's identity."""
    original = installer.read_plain
    alternative = dict(candidate[3])
    alternative['bundleHashes'] = {n.replace('/', '\\'): d for n, d in alternative['bundleHashes'].items()}
    alternate_bytes = json.dumps(alternative).encode()
    reads = 0

    def alternate(path):
        nonlocal reads
        if path == candidate[0]:
            reads += 1
            if reads == 2:
                return alternate_bytes
        return original(path)

    monkeypatch.setattr(installer, 'read_plain', alternate)
    with pytest.raises(ValueError, match='Manifest changed during snapshot'):
        installer.prepare(*candidate[:3])
    assert reads == 2  # One snapshot supplies BOTH parsing and digest; second is an end check.
    assert not (candidate[2] / 'preparation-manifest.json').exists()


def test_verify_prepared_positive_and_cli(candidate):
    import subprocess
    import sys
    result = installer.prepare(*candidate[:3])
    assert installer.verify_prepared(candidate[2]) == result
    process = subprocess.run([sys.executable, str(MODULE), '--verify-prepared', str(candidate[2])],
                             capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {'status': 'PREPARED_UNTESTED', 'identity': result['identity'],
                                        'operation': 'integrity_verified'}
    assert not list(candidate[2].glob('*.exe'))


@pytest.mark.parametrize('mutation', ['payload', 'extra', 'missing', 'script', 'script_and_digest',
                                     'unsafe_path', 'case_collision', 'approved_status', 'tested_flag'])
def test_verify_prepared_rejects_tampering(candidate, mutation):
    result = installer.prepare(*candidate[:3])
    output = candidate[2]
    metadata = output / 'preparation-manifest.json'
    if mutation == 'payload':
        (output / 'payload/LICENSE').write_text('modified')
    elif mutation == 'extra':
        (output / 'payload/unlisted.txt').write_text('extra')
    elif mutation == 'missing':
        (output / 'payload/LICENSE').unlink()
    elif mutation in ('script', 'script_and_digest'):
        altered = (output / 'installer.nsi').read_bytes() + b'\n; modification\n'
        (output / 'installer.nsi').write_bytes(altered)
        if mutation == 'script_and_digest':
            result['installerSourceSha256'] = installer.sha(altered)
    elif mutation == 'unsafe_path':
        result['bundleHashes']['../escape'] = 'a' * 64
    elif mutation == 'case_collision':
        result['bundleHashes']['license'] = result['bundleHashes']['LICENSE']
    elif mutation == 'approved_status':
        result['status'] = 'WINDOWS_PASS'
    elif mutation == 'tested_flag':
        result['windowsTested'] = True
    metadata.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        installer.verify_prepared(output)


def test_verify_prepared_cli_rejects_mixed_mode(candidate):
    import subprocess
    import sys
    process = subprocess.run([sys.executable, str(MODULE), '--verify-prepared', str(candidate[2]),
                              '--manifest', str(candidate[0])], capture_output=True, text=True)
    assert process.returncode == 2
    assert 'cannot be combined' in process.stderr


@pytest.mark.parametrize('name', ['LogicLib.nsh', 'x64.nsh', 'extra.exe', '.DS_Store'])
def test_verify_prepared_rejects_unmanifested_top_level(candidate, name):
    installer.prepare(*candidate[:3])
    (candidate[2] / name).write_text('not part of preparation')
    with pytest.raises(ValueError, match='top-level file list'):
        installer.verify_prepared(candidate[2])


def test_includes_are_bound_to_nsis_installation(candidate):
    installer.prepare(*candidate[:3])
    nsi = (candidate[2] / 'installer.nsi').read_text()
    assert '!include "${NSISDIR}/Include/LogicLib.nsh"' in nsi
    assert '!include "${NSISDIR}/Include/x64.nsh"' in nsi
    assert '!include "LogicLib.nsh"' not in nsi
    assert '!include "x64.nsh"' not in nsi


def test_registry_removal_is_idempotent_and_preserves_retry(candidate):
    """Structural NSIS branches; real missing-key/denied-key behavior needs Windows."""
    installer.prepare(*candidate[:3])
    nsi = (candidate[2] / 'installer.nsi').read_text()
    uninstall = nsi.split('Section "Uninstall"', 1)[1]
    registry = uninstall.split('  DeleteRegKey HKCU', 1)[1].split('  Delete "$INSTDIR\\Uninstall.exe"', 1)[0]
    # First uninstall with no registry key, or retry after failed self deletion:
    # DeleteRegKey errors -> query same view -> absent(2) -> clear old error -> self delete.
    assert 'IfErrors un_registry_check un_registry_removed' in registry
    assert registry.index('un_registry_check:') < registry.index('RegOpenKeyExW')
    assert 'p 0x80000001' in registry and 'i 0x20119, *p .r2' in registry
    assert 'StrCmp $0 2 un_registry_removed' in registry
    assert 'StrCmp $0 3 un_registry_removed' not in registry
    assert 'StrCmp $0 5 un_registry_removed' not in registry
    assert registry.index('${If} $0 == 0') < registry.index('RegCloseKey') < registry.index('Goto un_failed')
    assert registry.index('Goto un_failed') < registry.index('un_registry_removed:') < registry.index('ClearErrors')
    # The missing-EXE retry path remains earlier than ANY deletion. Self remains
    # on all registry failures, including access denied and an existing key.
    assert uninstall.index('un_runtime_ready:') < uninstall.index('  Delete ')
    assert uninstall.index('SetRegView 64') < uninstall.index('DeleteRegKey HKCU')
    assert uninstall.index('DeleteRegKey HKCU') < uninstall.index('Delete "$INSTDIR\\Uninstall.exe"')


def test_uninstall_retry_missing_runtime_and_shortcut_working_directory(candidate):
    installer.prepare(*candidate[:3])
    nsi = (candidate[2] / 'installer.nsi').read_text()
    uninstall = nsi.split('Section "Uninstall"', 1)[1]
    # Both Win32 calls capture last error via ?e, then immediately pop it.
    # FILE_NOT_FOUND (2) / PATH_NOT_FOUND (3) are the only failures that proceed.
    import re
    preflight = uninstall.split('  ClearErrors', 1)[0]
    assert re.search(r'GetFileAttributesW\(.*\) i \.r0 \?e\'\n  Pop \$1', preflight)
    assert re.search(r'CreateFileW\(.*\) p \.r0 \?e\'\n  Pop \$1', preflight)
    assert preflight.count('StrCmp $1 2 un_runtime_ready') == 2
    assert preflight.count('StrCmp $1 3 un_runtime_ready') == 2
    assert 'StrCmp $1 5 un_runtime_ready' not in preflight  # Access denied must abort.
    assert 'StrCmp $1 32 un_runtime_ready' not in preflight  # Sharing violation must abort.
    assert preflight.index('GetFileAttributesW') < preflight.index('CreateFileW')
    assert preflight.index('CreateFileW') < preflight.index('un_runtime_ready:')
    assert 'Delete ' not in preflight
    assert 'Abort "Cannot inspect preview runtime.' in preflight
    assert 'Abort "Close the preview runtime or correct access' in preflight
    assert uninstall.index('DeleteRegKey HKCU') < uninstall.index('Delete "$INSTDIR\\Uninstall.exe"')
    assert uninstall.index('Delete "$SMPROGRAMS') < uninstall.index('Delete "$INSTDIR\\Uninstall.exe"')
    assert re.search(r'SetOutPath "\$INSTDIR"\n  IfErrors install_failed\n  CreateShortcut', nsi)


def test_script_scope_lock_and_explicit_nonrecursive_uninstall(candidate):
    result = installer.prepare(*candidate[:3])
    nsi = (candidate[2] / 'installer.nsi').read_text()
    identity = result['identity']
    assert 'RequestExecutionLevel user' in nsi
    assert 'SetShellVarContext current' in nsi
    assert 'SetOverwrite off' in nsi
    assert f'$LOCALAPPDATA\\Programs\\MeritzStrategyPlatformPreview\\{identity}' in nsi
    assert f'Uninstall\\MeritzStrategyPlatformPreview-{identity}' in nsi
    for forbidden in ('HKLM', 'RMDir /r', '/REBOOTOK', 'taskkill', 'netsh', 'ExecWait', 'MCP_TYPE'):
        assert forbidden not in nsi
    assert 'File "/oname=a space value.txt"' in nsi
    uninstall = nsi.split('Section "Uninstall"', 1)[1]
    assert uninstall.index('StrCmp $INSTDIR') < uninstall.index('CreateFileW') < uninstall.index('  Delete ')
    assert 'i 0xC0000000, i 0, p 0, i 3' in uninstall
    assert uninstall.index('IfErrors un_failed') < uninstall.index('Delete "$INSTDIR\\LICENSE"')
    import re
    deleted = re.findall(r'^  Delete "\$INSTDIR\\(.*)"$', uninstall, re.M)
    expected = {installer.nsis_path(n) for n in result['bundleHashes']} | {installer.UNINSTALLER}
    assert set(deleted) == expected
    assert len(deleted) == len(expected)
    # Model the explicit script file deletions; this is not execution of NSIS.
    installed = expected | {'.env', 'customer.sqlite', '_internal\\user-note.txt'}
    assert installed - set(deleted) == {'.env', 'customer.sqlite', '_internal\\user-note.txt'}
    assert uninstall.index('RMDir "$INSTDIR\\_internal\\sub"') < uninstall.index('RMDir "$INSTDIR\\_internal"')
    assert 'StrCmp $INSTDIR' in nsi.split('Function un.onInit')[1].split('FunctionEnd')[0]
    assert 'ReadRegStr' not in nsi.split('Function un.onInit')[1].split('FunctionEnd')[0]
