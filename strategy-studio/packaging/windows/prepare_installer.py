"""Prepare NSIS 3 source from a verified Windows review bundle; never compile/run it.

The snapshot is immutable relative to later source changes, not a security boundary
against another process modifying the preparation directory. Compile only a freshly
reverified preparation on Windows. See docs/release/windows-installation.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import unicodedata


EXE = 'meritz-strategy-platform.exe'
UNINSTALLER = 'Uninstall.exe'
LEGAL = {'LICENSE', 'THIRD-PARTY-NOTICES.txt', 'PLATFORM-NOTICES.txt'}
RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?:\.|$)', re.I)
# Conservative subset of Windows names: also exclude NSIS/preprocessor quoting
# characters rather than relying on multiple layers of runtime/compiler escaping.
BAD = set('<>:"|?*$\'`~')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(name):
    if not isinstance(name, str) or not name or len(name) > 200:
        raise ValueError('Invalid Windows relative path')
    name = name.replace('\\', '/')
    parts = name.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or part[-1] in '. '
                or len(part) > 150 or RESERVED.match(part)
                or RESERVED.match(part.split('.')[0].rstrip(' '))
                or any(c in BAD or ord(c) < 32 or ord(c) == 127
                       or unicodedata.category(c).startswith('C') for c in part)
                or unicodedata.normalize('NFC', part) != part):
            raise ValueError(f'Unsafe Windows path: {name!r}')
        lower = part.casefold()
        if (lower == '.env' or lower.startswith('.env.') or
                re.search(r'\.(?:sqlite3?|db)(?:-wal|-shm|-journal)?$', lower)):
            raise ValueError(f'Customer data is not a payload: {name!r}')
    if name.casefold() == UNINSTALLER.casefold():
        raise ValueError('Payload conflicts with generated uninstaller')
    return name


def plain(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError(f'Symlink/reparse point rejected: {path}')
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError(f'Non-regular payload rejected: {path}')
    return info


def check_ancestors(path):
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            plain(parent)


def read_plain(path):
    plain(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Payload changed to a non-regular file')
        return stream.read()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def load_manifest(path, data=None):
    check_ancestors(path)
    manifest = json.loads(read_plain(path) if data is None else data, object_pairs_hook=unique_object)
    if not isinstance(manifest, dict):
        raise ValueError('Manifest must be an object')
    if (manifest.get('status') != 'UNSIGNED_REVIEW_CANDIDATE'
            or manifest.get('changedDuringBuild') != []
            or manifest.get('os') != 'Windows'
            or manifest.get('architecture') not in ('AMD64', 'x86_64')):
        raise ValueError('Requires unchanged Windows AMD64 unsigned review candidate')
    return manifest, validate_hashes(manifest.get('bundleHashes'))


def validate_hashes(raw):
    if not isinstance(raw, dict) or not raw:
        raise ValueError('Non-empty bundleHashes required')
    hashes = {}
    spelling = {}
    for original, digest in raw.items():
        name = safe_path(original)
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Expected lowercase SHA-256')
        # Check directory components too: A/x and a/y cannot create two dirs.
        parts = name.split('/')
        for i in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:i])
            key = prefix.casefold()
            if key in spelling and spelling[key] != prefix:
                raise ValueError('Windows case collision')
            spelling[key] = prefix
        if name in hashes:
            raise ValueError('Duplicate normalized Windows path')
        hashes[name] = digest
    for name in hashes:
        if any('/'.join(name.split('/')[:i]) in hashes for i in range(1, len(name.split('/')))):
            raise ValueError('File/directory collision')
    if not (LEGAL | {EXE}) <= hashes.keys():
        raise ValueError('Runtime EXE and all three bundled license notices are required')
    return dict(sorted(hashes.items()))


def inventory(bundle):
    check_ancestors(bundle)
    if not bundle.is_dir():
        raise ValueError('Bundle must be a directory')
    files = {}
    for path in bundle.rglob('*'):
        info = plain(path)
        name = safe_path(path.relative_to(bundle).as_posix())
        if stat.S_ISREG(info.st_mode):
            files[name] = path
    return files


def require_amd64_pe(data):
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Runtime must be a Windows AMD64 PE executable')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset < 64 or offset + 26 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    machine, sections = struct.unpack_from('<HH', data, offset + 4)
    optional_size, characteristics = struct.unpack_from('<HH', data, offset + 20)
    if (machine != 0x8664 or not sections or optional_size < 112
            or offset + 24 + optional_size + 40 * sections > len(data)
            or struct.unpack_from('<H', data, offset + 24)[0] != 0x20b
            or not characteristics & 2 or characteristics & 0x2000):
        raise ValueError('Requires executable AMD64 PE32+ header (not DLL)')


def nsis_path(name):
    # All untrusted inputs already pass safe_path; retain literal-dollar defense.
    return name.replace('/', '\\').replace('$', '$$')


def script(hashes, identity):
    names = sorted(hashes)
    directories = sorted({'/'.join(n.split('/')[:i]) for n in names
                          for i in range(1, len(n.split('/')))}, key=lambda n: (n.count('/'), n))
    fixed = rf'$LOCALAPPDATA\Programs\MeritzStrategyPlatformPreview\{identity}'
    reg = rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\MeritzStrategyPlatformPreview-{identity}'
    shortcut = rf'$SMPROGRAMS\Meritz Strategy Platform PREVIEW {identity}.lnk'
    lines = [
        '# -*- coding: utf-8 -*-',
        '; PREPARED_UNTESTED: NSIS 3 source only. Windows compilation/runtime tests pending.',
        '; Python strategy runtime is included in the bundle.',
        '!include "${NSISDIR}/Include/LogicLib.nsh"', '!include "${NSISDIR}/Include/x64.nsh"',
        '!if ${NSIS_PACKEDVERSION} < 0x03000000', '!error "NSIS 3 or newer required"', '!endif',
        'Unicode true', 'RequestExecutionLevel user', 'SetCompressor zlib',
        f'Name "Meritz Strategy Platform PREVIEW {identity}"',
        f'OutFile "MeritzStrategyPlatformPreview-{identity}-UNTESTED.exe"',
        f'InstallDir "{fixed}"', 'ShowInstDetails show', 'ShowUninstDetails show',
        'Page instfiles', 'UninstPage uninstConfirm', 'UninstPage instfiles',
        'Var RuntimeHandle',
        '',
        '; Reject reparse points in every existing destination ancestor/file.',
        '; This is a cooperative-user preflight, not protection against concurrent local attackers.',
        '!macro CheckPlain Path',
        '  System::Call \'kernel32::GetFileAttributesW(w "${Path}") i .r0\'',
        '  ${If} $0 != -1',
        '    IntOp $1 $0 & 0x400',
        '    ${If} $1 != 0',
        '      Abort "Reparse point in destination; no automatic cleanup performed."',
        '    ${EndIf}',
        '  ${EndIf}',
        '!macroend',
        '',
        'Function .onInit',
        '  SetShellVarContext current',
        '  SetRegView 64',
        '  ${IfNot} ${RunningX64}',
        '    Abort "This preview requires 64-bit Windows."',
        '  ${EndIf}',
        'FunctionEnd',
        '',
        'Section "Install PREVIEW"',
        '  SetShellVarContext current', '  SetRegView 64',
        f'  StrCpy $INSTDIR "{fixed}" ; Ignore any /D override; immutable side-by-side identity.',
        f'  StrLen $0 "$INSTDIR\\{nsis_path(max(names, key=len))}"',
        '  ${If} $0 > 240', '    Abort "Preview destination path is too long."', '  ${EndIf}',
    ]
    parents = ['$LOCALAPPDATA', r'$LOCALAPPDATA\Programs', r'$LOCALAPPDATA\Programs\MeritzStrategyPlatformPreview']
    for parent in parents:
        lines.append(f'  !insertmacro CheckPlain "{parent}"')
    lines += [
        '  System::Call \'kernel32::GetFileAttributesW(w "$INSTDIR") i .r0\'',
        '  ${If} $0 != -1',
        '    Abort "This preview path already exists. Nothing was overwritten."', '  ${EndIf}',
        f'  ReadRegStr $0 HKCU "{reg}" "UninstallString"',
        '  ${If} $0 != ""', '    Abort "This preview registration already exists."', '  ${EndIf}',
        f'  IfFileExists "{shortcut}" 0 +2', '    Abort "This preview shortcut already exists."',
        '  SetOverwrite off', '  ClearErrors', '  CreateDirectory "$INSTDIR"',
        '  IfErrors install_failed',
    ]
    for name in names:
        directory, _, basename = name.rpartition('/')
        destination = '$INSTDIR' + ('\\' + nsis_path(directory) if directory else '')
        lines += [f'  SetOutPath "{destination}"', '  IfErrors install_failed',
                  f'  File "/oname={nsis_path(basename)}" "payload\\{nsis_path(name)}"',
                  '  IfErrors install_failed']
    lines += [
        f'  WriteUninstaller "$INSTDIR\\{UNINSTALLER}"', '  IfErrors install_failed',
        '  SetOutPath "$INSTDIR"', '  IfErrors install_failed',
        f'  CreateShortcut "{shortcut}" "$INSTDIR\\{EXE}"', '  IfErrors install_failed',
        f'  WriteRegStr HKCU "{reg}" "DisplayName" "Meritz Strategy Platform PREVIEW {identity}"',
        f'  WriteRegStr HKCU "{reg}" "DisplayVersion" "{identity}"',
        f'  WriteRegStr HKCU "{reg}" "InstallLocation" "$INSTDIR"',
        f'  WriteRegStr HKCU "{reg}" "UninstallString" \'$\\"$INSTDIR\\{UNINSTALLER}$\\"\'',
        f'  WriteRegDWORD HKCU "{reg}" "NoModify" 1',
        f'  WriteRegDWORD HKCU "{reg}" "NoRepair" 1',
        '  IfErrors install_failed', '  Goto install_done',
        'install_failed:',
        '  Abort "Preview installation incomplete. Files retained for inspection; no automatic recursive cleanup."',
        'install_done:', 'SectionEnd', '',
        'Function un.onInit', '  SetShellVarContext current', '  SetRegView 64',
        '; NSIS initializes $INSTDIR to the ORIGINAL uninstaller directory before its temp copy.',
        '; Do not replace it with a registry value: a moved uninstaller must abort.',
        f'  StrCmp $INSTDIR "{fixed}" un_path_ok',
        '    Abort "Uninstaller is outside its expected preview directory."',
        'un_path_ok:',
        f'  IfFileExists "$INSTDIR\\{UNINSTALLER}" un_file_ok',
        '    Abort "Expected preview uninstaller is missing."',
        'un_file_ok:', 'FunctionEnd', '',
        'Section "Uninstall"', '  SetShellVarContext current', '  SetRegView 64',
        f'  StrCmp $INSTDIR "{fixed}" un_section_path_ok',
        '    Abort "Unexpected uninstall path."', 'un_section_path_ok:',
    ]
    for path in [*parents, '$INSTDIR', *['$INSTDIR\\' + nsis_path(d) for d in directories],
                 *['$INSTDIR\\' + nsis_path(n) for n in names], '$INSTDIR\\' + UNINSTALLER]:
        lines.append(f'  !insertmacro CheckPlain "{path}"')
    lines += [
        '; Allow retry after partial uninstall removed EXE. Only Win32 errors 2/3 mean missing.',
        '; No file, shortcut or registry deletion precedes this exclusive-open preflight.',
        f'  System::Call \'kernel32::GetFileAttributesW(w "$INSTDIR\\{EXE}") i .r0 ?e\'',
        '  Pop $1 ; GetLastError captured by System immediately after the API call.',
        '  ${If} $0 == -1',
        '    StrCmp $1 2 un_runtime_ready',
        '    StrCmp $1 3 un_runtime_ready',
        '    Abort "Cannot inspect preview runtime. Nothing removed; correct access and retry."',
        '  ${EndIf}',
        '; GENERIC_READ|GENERIC_WRITE, share=0, OPEN_EXISTING: fail on running/locked EXE.',
        f'  System::Call \'kernel32::CreateFileW(w "$INSTDIR\\{EXE}", i 0xC0000000, i 0, p 0, i 3, i 0x80, p 0) p .r0 ?e\'',
        '  Pop $1',
        '  StrCpy $RuntimeHandle $0',
        '  ${If} $RuntimeHandle == -1',
        '    StrCmp $1 2 un_runtime_ready',
        '    StrCmp $1 3 un_runtime_ready',
        '    Abort "Close the preview runtime or correct access and retry. Nothing removed."',
        '  ${EndIf}',
        '; Windows must release the handle before Delete; a cooperative close/delete race remains.',
        '  System::Call \'kernel32::CloseHandle(p $RuntimeHandle) i .r0\'',
        '  ${If} $0 == 0', '    Abort "Runtime handle could not be closed. Nothing removed."', '  ${EndIf}',
        'un_runtime_ready:',
        '  ClearErrors',
        f'  Delete "$INSTDIR\\{EXE}"', '  IfErrors un_failed',
    ]
    for name in names:
        if name != EXE:
            lines += [f'  Delete "$INSTDIR\\{nsis_path(name)}"', '  IfErrors un_failed']
    # Retain the uninstaller until files, shortcut and registration are removed,
    # so an error in any of those steps remains retryable after EXE deletion.
    lines += [f'  Delete "{shortcut}"', '  IfErrors un_failed',
              f'  DeleteRegKey HKCU "{reg}"',
              '  IfErrors un_registry_check un_registry_removed',
              'un_registry_check:',
              '; DeleteRegKey also errors for an already absent key: distinguish absence from access failure.',
              '; HKEY_CURRENT_USER, KEY_READ | KEY_WOW64_64KEY matches SetRegView 64.',
              f'  System::Call \'advapi32::RegOpenKeyExW(p 0x80000001, w "{reg}", i 0, i 0x20119, *p .r2) i .r0\'',
              '  StrCmp $0 2 un_registry_removed ; ERROR_FILE_NOT_FOUND: already absent is success.',
              '  ${If} $0 == 0',
              '    System::Call \'advapi32::RegCloseKey(p r2) i .r0\'',
              '  ${EndIf}',
              '  Goto un_failed ; Key still exists, access denied, or any other error: preserve self for retry.',
              'un_registry_removed:',
              '  ClearErrors ; Do not let the expected missing-key error poison self deletion.',
              f'  Delete "$INSTDIR\\{UNINSTALLER}"', '  IfErrors un_failed']
    for directory in reversed(directories):
        lines.append(f'  RMDir "$INSTDIR\\{nsis_path(directory)}"')
    lines += ['  SetOutPath "$TEMP"', '  RMDir "$INSTDIR"',
              '  ; Non-empty directories deliberately remain: unlisted/customer files are preserved.',
              '  Goto un_done', 'un_failed:',
              '  Abort "Some preview files could not be removed. Remaining files retained; no recursive cleanup."',
              'un_done:', 'SectionEnd', '']
    return '\n'.join(lines)


def validate_bundle(manifest_path, bundle, *, manifest_data=None):
    """Read-only validation. Return (original_manifest, canonical_relative_hashes).

    No claim of Windows execution, dependency approval or source authenticity.
    Call immediately before use; callers must prevent subsequent mutation.
    """
    manifest_path, bundle = map(Path, (manifest_path, bundle))
    manifest, hashes = load_manifest(manifest_path, manifest_data)
    validate_files(bundle, hashes)
    return manifest, hashes


def validate_files(bundle, hashes):
    files = inventory(bundle)
    if set(files) != set(hashes):
        raise ValueError('Bundle file list differs from manifest')
    for name, path in files.items():
        data = read_plain(path)
        if sha(data) != hashes[name]:
            raise ValueError(f'Bundle hash mismatch: {name}')
        if name.lower().endswith('.exe'):
            require_amd64_pe(data)


def prepare(manifest_path, bundle, output):
    manifest_path, bundle, output = map(lambda p: Path(os.path.abspath(p)), (manifest_path, bundle, output))
    check_ancestors(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Output must be a new path')
    if bundle == output or bundle in output.parents or output in bundle.parents:
        raise ValueError('Output and bundle must be separate trees')
    check_ancestors(manifest_path)
    manifest_data = read_plain(manifest_path)
    manifest_digest = sha(manifest_data)
    manifest, hashes = validate_bundle(manifest_path, bundle, manifest_data=manifest_data)
    files = inventory(bundle)
    if set(files) != set(hashes):
        raise ValueError('Bundle file list differs from manifest')
    # Hash the canonical original manifest map, retaining build_native Windows separators.
    identity = sha(json.dumps(manifest['bundleHashes'], sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True).encode())[:12]
    # Snapshot bytes are re-hashed on copy after read-only validation.
    output.mkdir(parents=True, exist_ok=False)
    payload = output / 'payload'
    payload.mkdir()
    for name, path in sorted(files.items()):
        data = read_plain(path)
        if sha(data) != hashes[name]:
            raise ValueError(f'Bundle changed during snapshot: {name}; discard incomplete output')
        destination = payload / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(data)
    if set(inventory(bundle)) != set(hashes):
        raise ValueError('Bundle file list changed during snapshot; discard incomplete output')
    if sha(read_plain(manifest_path)) != manifest_digest:
        raise ValueError('Manifest changed during snapshot; discard incomplete output')
    snapshot = inventory(payload)
    if set(snapshot) != set(hashes):
        raise ValueError('Snapshot file list mismatch; discard incomplete output')
    for name, path in snapshot.items():
        if sha(read_plain(path)) != hashes[name]:
            raise ValueError('Snapshot hash mismatch; discard incomplete output')
    nsi = script(hashes, identity).encode('utf-8')
    (output / 'installer.nsi').write_bytes(nsi)
    preparation = {
        'status': 'PREPARED_UNTESTED', 'identity': identity,
        'installerCompiled': False, 'windowsTested': False, 'signed': False,
        'dependencyValidation': 'Not performed by this generator; Windows freeze/audit required',
        'runtimeValidation': 'Not performed; Windows Python strategies require native bundled-runtime verification',
        'bundleHashes': hashes, 'installerSourceSha256': sha(nsi),
        'inputManifestSha256': manifest_digest,
        'manualCompile': 'Run prepare_installer.py --verify-prepared PATH, then on a Windows test host with NSIS 3 run: makensis installer.nsi',
        'limitations': ['PE headers and hashes do not establish executable functionality or provenance.',
                        'Exclusive-open preflight is cooperative; close-to-delete race remains.',
                        'Preparation directory must remain trusted and unchanged before compilation.',
                        'No compiler, installer, runtime, signing or uninstall was executed.'],
    }
    (output / 'preparation-manifest.json').write_text(json.dumps(preparation, indent=2) + '\n', encoding='utf-8')
    return preparation


def verify_prepared(prepared):
    """Verify preparation integrity, NOT Windows approval or publisher authenticity.

    Both the metadata and directory must come from a trusted preparation. An
    attacker able to replace both can substitute a different candidate. No code
    is compiled or executed. Re-run immediately before manual compilation.
    """
    prepared = Path(prepared)
    check_ancestors(prepared)
    if {path.name for path in prepared.iterdir()} != {'installer.nsi', 'payload', 'preparation-manifest.json'}:
        raise ValueError('Preparation top-level file list differs; compile only the exact verified preparation')
    for path in prepared.iterdir():
        plain(path)
    metadata = json.loads(read_plain(prepared / 'preparation-manifest.json'), object_pairs_hook=unique_object)
    if (not isinstance(metadata, dict) or metadata.get('status') != 'PREPARED_UNTESTED'
            or any(metadata.get(key) is not False for key in ('installerCompiled', 'windowsTested', 'signed'))):
        raise ValueError('Expected untested preparation metadata; this is not Windows approval')
    identity = metadata.get('identity')
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{12}', identity):
        raise ValueError('Invalid preparation identity')
    hashes = validate_hashes(metadata.get('bundleHashes'))
    validate_files(prepared / 'payload', hashes)
    nsi = read_plain(prepared / 'installer.nsi')
    if sha(nsi) != metadata.get('installerSourceSha256'):
        raise ValueError('Installer source hash mismatch')
    if nsi != script(hashes, identity).encode('utf-8'):
        raise ValueError('Installer source differs from current generator; regenerate preparation')
    if not isinstance(metadata.get('inputManifestSha256'), str) or not re.fullmatch('[0-9a-f]{64}', metadata['inputManifestSha256']):
        raise ValueError('Invalid input manifest digest')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify-prepared', type=Path, help='Verify existing preparation integrity only; does not approve Windows use')
    args = parser.parse_args()
    if args.verify_prepared is not None:
        if any(value is not None for value in (args.manifest, args.bundle, args.output)):
            parser.error('--verify-prepared cannot be combined with preparation inputs')
    elif any(value is None for value in (args.manifest, args.bundle, args.output)):
        parser.error('--manifest, --bundle and --output are required for preparation')
    try:
        result = verify_prepared(args.verify_prepared) if args.verify_prepared is not None else prepare(args.manifest, args.bundle, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Preparation rejected: {error}\n')
    print(json.dumps({'status': result['status'], 'identity': result['identity'],
                      'operation': 'integrity_verified' if args.verify_prepared is not None else 'prepared'}))


if __name__ == '__main__':
    main()
