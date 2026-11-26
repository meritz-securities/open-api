"""Regenerate the macOS arm64 Python 3.11 build pins from uv.lock."""
import argparse
from pathlib import Path
import platform
import subprocess

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'packaging/macos-arm64-build-requirements.txt'


def render():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('Run on macOS arm64 to resolve the target dependency markers.')
    result = subprocess.run(
        ['uv', 'export', '--locked', '--extra', 'dev', '--no-emit-project', '--no-annotate',
         '--no-hashes', '--format', 'requirements-txt', '--python', '3.11'],
        cwd=ROOT, capture_output=True, text=True, check=True)
    environment = default_environment()
    environment.update({'sys_platform': 'darwin', 'platform_system': 'Darwin', 'platform_machine': 'arm64',
                        'python_version': '3.11', 'python_full_version': '3.11.0'})
    pins = []
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith('#'):
            continue
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != '==':
            raise SystemExit(f'Unpinned dependency in lock export: {raw}')
        pins.append(f'{requirement.name}=={specifiers[0].version}')
    header = ('# Generated from uv.lock by packaging/export_macos_pins.py.\n'
              '# macOS arm64, Python 3.11, with the dev packaging group.\n\n')
    return header + '\n'.join(sorted(set(pins), key=str.lower)) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if the checked-in pin list is stale')
    args = parser.parse_args()
    value = render()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding='utf-8') != value:
            raise SystemExit('macOS build pins are stale; run packaging/export_macos_pins.py.')
        print('macOS arm64 build pins match uv.lock.')
    else:
        OUTPUT.write_text(value, encoding='utf-8')
        print(f'Wrote {OUTPUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
