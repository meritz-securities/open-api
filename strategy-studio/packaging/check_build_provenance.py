"""Cross-check a native build manifest, audit and PyInstaller module inputs."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


def norm(value):
    return re.sub(r'[-_.]+', '-', value).lower()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.build / 'build-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    require(not manifest['changedDuringBuild'], 'Source changed during build')
    require(manifest['status'] == 'UNSIGNED_REVIEW_CANDIDATE', 'Build is not an unchanged review candidate')
    audit = json.loads(args.audit.read_text())
    rows = audit['dependencies']
    require(all('vulns' in row and not row['vulns'] and 'skip_reason' not in row for row in rows), 'Audit contains advisories or incomplete entries')
    audited = {norm(row['name']): row['version'] for row in rows}
    require(len(audited) == len(rows), 'Duplicate audit packages')
    require(audited == manifest['dependencies'], 'Audit differs from build environment')
    for name, digest in manifest['sourceHashes'].items():
        require(sha(args.source / name) == digest, f'Changed source: {name}')
    toc = args.build / 'work/meritz-strategy-platform/PYZ-00.toc'
    modules = ast.literal_eval(toc.read_text())[1]
    third_party = [row[1] for row in modules if '/site-packages/' in row[1].replace('\\', '/')]
    environment = Path(manifest['command'][0]).parent.parent.resolve()
    require(third_party and all(Path(p).resolve().is_relative_to(environment) for p in third_party),
            'Third-party module outside audited environment')
    allowed = [environment, args.source.resolve(), Path(manifest['standardLibraryRoot']).resolve()]
    require(all(any(Path(row[1]).resolve().is_relative_to(root) for root in allowed) for row in modules),
            'Module outside permitted build roots')
    bundle = args.build / 'dist/meritz-strategy-platform'
    actual_files = {str(p.relative_to(bundle)): sha(p) for p in bundle.rglob('*') if p.is_file()}
    require(actual_files == manifest['bundleHashes'], 'Bundle changed after build')
    executable = args.build / 'dist/meritz-strategy-platform' / ('meritz-strategy-platform.exe' if manifest['os'] == 'Windows' else 'meritz-strategy-platform')
    commit = subprocess.check_output(['git', '-C', str(args.source), 'rev-parse', 'HEAD'], text=True).strip()
    print(json.dumps({'sourceCommit': commit, 'checkerSha256': sha(Path(__file__)), 'checkedAt': datetime.now(timezone.utc).isoformat(),
                     'status': 'PASS_SCOPED_BUILD_PROVENANCE', 'auditSha256': sha(args.audit),
                     'manifestSha256': sha(manifest_path), 'tocSha256': sha(toc),
                     'binarySha256': sha(executable), 'auditedPackages': len(audited),
                     'thirdPartyPythonModuleCount': len(third_party),
                     'limitations': ['Known-advisory scan only', 'Vendored/native libraries not covered',
                                     'No signing, licensing or Windows acceptance claim']}, indent=2))


if __name__ == '__main__':
    main()
