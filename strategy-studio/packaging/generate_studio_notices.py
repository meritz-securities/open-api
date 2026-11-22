"""Emit installed production dependency license files after verifying lock versions.

Run after npm ci. Includes type-only dependencies conservatively. This inventory
is not a legal compliance certification or a complete vendored-code analysis.
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--studio-root', type=Path, default=Path(__file__).resolve().parents[1] / 'studio')
    args = parser.parse_args()
    root = args.studio_root.resolve()
    lock = json.loads((root / 'package-lock.json').read_text())
    sections = ['Meritz Strategy Studio — bundled dependency notices\n\n'
                'Installed production packages (including type declarations).\n'
                'Original license and notice text follows. Not a legal compliance certification.\n']
    for name, entry in sorted(lock['packages'].items()):
        if not name or entry.get('dev'):
            continue
        package = root / name
        if not package.resolve().is_relative_to(root / 'node_modules'):
            raise SystemExit(f'Unexpected package location: {name}')
        meta = json.loads((package / 'package.json').read_text())
        if meta['version'] != entry['version']:
            raise SystemExit(f'Installed version differs from lock: {name}')
        files = sorted(p for p in package.iterdir() if p.is_file() and
                       (p.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')) or p.name == 'ThirdPartyNotices.txt'))
        if not files:
            raise SystemExit(f'License text missing: {name}')
        sections.append('\n' + '=' * 70 + f"\n{meta['name']} {meta['version']} — {meta.get('license', 'undeclared')}\n")
        for file in files:
            sections.append(f'\n--- {file.name} ---\n' + file.read_text(encoding='utf-8') + '\n')
    print(''.join(sections), end='')


if __name__ == '__main__':
    main()
