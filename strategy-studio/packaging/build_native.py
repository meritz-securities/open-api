"""Build an unsigned native onedir review bundle; Windows build fetches the pinned guest runtime."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import re
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from timezone_support import PYINSTALLER_ARGS, validate_bundle


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_third_party_notices(python, source, dest):
    """Generate third-party notices from this target OS's resolved build environment."""
    result = subprocess.run([str(python), str(Path(source) / 'gen_notices.py')], cwd=str(source),
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'},
                            capture_output=True, check=True)
    text = result.stdout.decode('utf-8')
    if '구성요소별 라이선스 전문' not in text:
        raise SystemExit('gen_notices.py did not produce a complete third-party notice file.')
    dest = Path(dest)
    dest.write_text(text, encoding='utf-8')
    return dest


def check_webview_notice(bundle, notices_path, target_os=None):
    """번들에 pywebview 패키지(_internal/webview)가 수집됐으면 번들 고지에 'pywebview' 가 있어야 한다. 없으면 비정상 종료."""
    if not (Path(bundle) / '_internal' / 'webview').is_dir():
        return False
    # 배포판 이름은 메타데이터에 따라 '-' 와 '_' 가 섞인다(clr-loader 의 METADATA Name 은 clr_loader).
    # 고지 본문과 요구 이름을 같은 규칙으로 정규화해 비교한다.
    normalize = lambda value: re.sub(r'[-_.]+', '-', value.lower())
    text = normalize(Path(notices_path).read_text(encoding='utf-8', errors='replace'))
    required = ['pywebview']
    if (target_os or platform.system()) == 'Windows':
        required.extend(['pythonnet', 'clr-loader'])
    missing = [name for name in required if normalize(name) not in text]
    if missing:
        raise SystemExit(f"Bundle contains the webview package but {Path(notices_path).name} is missing notices: {', '.join(missing)}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output-root', type=Path, required=True, help='A new, empty build directory')
    args = parser.parse_args()
    source, output = args.source_root.resolve(), args.output_root.resolve()
    if platform.system() not in ('Darwin', 'Windows'):
        parser.error('Run on the target macOS or Windows host; cross compilation is not supported.')
    if output == source or source in output.parents:
        parser.error('Output must be outside the source checkout.')
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory must be empty; existing builds are never overwritten.')
    required = ['platform_entry.py', 'studio/dist/index.html', 'sdk/meritz_strategy/__init__.py',
                'indicators/calc.py', 'pyproject.toml', 'uv.lock', 'packaging/macos-arm64-build-requirements.txt', 'packaging/export_macos_pins.py', 'packaging/build_native.py', 'packaging/timezone_support.py', 'packaging/TZDATA-NOTICES.txt', 'meritz/data/catalog.json', 'strategies/sma-cross.json', 'strategies/template-details.toml', 'LICENSE', 'DISCLAIMER.md', 'THIRD-PARTY-NOTICES.txt', 'gen_notices.py', 'packaging/PLATFORM-NOTICES.txt']
    for name in required:
        if not (source / name).is_file():
            parser.error(f'Missing required input: {name}; build Studio first.')
    roots = ['meritz', 'runtime', 'analytics', 'optimizer', 'reports', 'indicators', 'strategies', 'sdk/meritz_strategy', 'studio/dist']
    inputs = {source / name for name in required}
    for root in roots:
        inputs.update(p for p in (source / root).rglob('*') if p.is_file() and
                      '__pycache__' not in p.parts and p.suffix in ('.py', '.json', '.html', '.css', '.js', '.txt', '.toml'))
    before = {str(p.relative_to(source)): digest(p) for p in sorted(inputs)}
    def environment():
        result = {}
        for dist in importlib.metadata.distributions():
            name = re.sub(r'[-_.]+', '-', dist.metadata['Name']).lower()
            if name in result:
                parser.error(f'Duplicate installed distribution: {name}')
            result[name] = dist.version
        return result
    dependencies = environment()
    if platform.system() == 'Darwin' and platform.machine() == 'arm64':
        pins = dict(line.split('==', 1) for line in
                    (source / 'packaging/macos-arm64-build-requirements.txt').read_text().splitlines()
                    if line.strip() and not line.startswith('#'))
        if pins != dependencies:
            parser.error('Build environment differs from the pinned macOS arm64 environment.')
    timezone_evidence = json.loads(subprocess.check_output(
        [sys.executable, str(source / 'packaging/timezone_support.py')],
        env={**os.environ, 'PYTHONTZPATH': ''}, text=True))
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--onedir', '--name', 'meritz-strategy-platform',
               '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
               '--specpath', str(output), '--paths', str(source), '--paths', str(source / 'sdk'),
               '--collect-all', 'fastmcp', '--collect-submodules', 'mcp.server',
               '--collect-submodules', 'mcp.shared', '--collect-submodules', 'mcp.client',
               '--hidden-import', 'mcp.types', '--collect-all', 'pydantic',
               '--collect-all', 'meritz', '--hidden-import', 'statistics', '--hidden-import', 'decimal',
               '--hidden-import', 'fractions', '--log-level', 'WARN']
    command += PYINSTALLER_ARGS
    # 자체 창(임베디드 웹뷰). 핀 환경에 pywebview 가 없으면 건너뛰고 알린다 — 앱은 브라우저 모드로 폴백하므로 빌드는 유지된다.
    # (import 는 모듈 최상단에 둔다: 함수 안에서 import 하면 importlib 이 지역변수가 되어 중첩함수 environment() 가 깨진다.)
    if importlib.util.find_spec('webview') is None:
        print('[build_native] pywebview not in build environment: bundling without the native window (browser fallback). '
              'Add pywebview to the pinned requirements to ship window mode.', flush=True)
    else:
        command += ['--collect-all', 'webview', '--collect-submodules', 'webview']
        if platform.system() == 'Darwin':
            command += [arg for pkg in ('objc', 'Foundation', 'AppKit', 'WebKit') if importlib.util.find_spec(pkg)
                        for arg in ('--collect-all', pkg)]
            command += ['--hidden-import', 'webview.platforms.cocoa']
        else:
            command += [arg for pkg in ('clr_loader', 'pythonnet') if importlib.util.find_spec(pkg)
                        for arg in ('--collect-all', pkg)]
            command += ['--hidden-import', 'webview.platforms.winforms', '--hidden-import', 'webview.platforms.edgechromium']
    for module in ('runtime', 'analytics', 'optimizer', 'reports', 'indicators', 'meritz_strategy'):
        command += ['--collect-submodules', module]
    for src, dest in [('studio/dist/index.html', 'studio/dist'),
                      ('runtime/strategy.schema.json', 'runtime'), ('strategies', 'strategies'),
                      ('sdk/meritz_strategy/__init__.py', 'sdk/meritz_strategy'),
                      ('meritz/strategy_worker.py', 'meritz'),
                      ('indicators/calc.py', 'indicators')]:
        command += ['--add-data', str(source / src) + os.pathsep + dest]
    if platform.system() == 'Windows':
        from windows.wasi_bundle import prepare, verify
        wasi = prepare(output)
        command += ['--paths', str(wasi / 'host'), '--hidden-import', 'wasmtime',
                    '--add-data', str(wasi) + os.pathsep + 'strategy_wasi']
    command += [str(source / 'platform_entry.py')]
    subprocess.run(command, cwd=source, check=True)
    if environment() != dependencies:
        raise SystemExit('Dependencies changed during build; discard this candidate.')
    changed = [name for name, old in before.items() if not (source / name).exists() or digest(source / name) != old]
    bundle = output / 'dist/meritz-strategy-platform'
    if platform.system() == 'Windows':
        verify(bundle)
    validate_bundle(bundle, timezone_evidence)
    (bundle / 'TZDATA-NOTICES.txt').write_bytes((source / 'packaging/TZDATA-NOTICES.txt').read_bytes())
    (bundle / 'LICENSE').write_bytes((source / 'LICENSE').read_bytes())
    (bundle / 'DISCLAIMER.md').write_bytes((source / 'DISCLAIMER.md').read_bytes())
    generated_notices = generate_third_party_notices(sys.executable, source, output / 'THIRD-PARTY-NOTICES.generated.txt')
    (bundle / 'THIRD-PARTY-NOTICES.txt').write_bytes(generated_notices.read_bytes())
    (bundle / 'PLATFORM-NOTICES.txt').write_bytes((source / 'packaging/PLATFORM-NOTICES.txt').read_bytes())
    webview_bundled = check_webview_notice(bundle, bundle / 'THIRD-PARTY-NOTICES.txt')
    if platform.system() == 'Darwin':
        launcher = bundle / 'Start.command'
        launcher.write_text('#!/bin/zsh\ncd "${0:A:h}"\nexec ./meritz-strategy-platform\n')
        launcher.chmod(0o755)
        claude_launcher = bundle / 'Connect-to-Claude.command'
        claude_launcher.write_text(
            '#!/bin/zsh\n'
            'cd "${0:A:h}"\n'
            './meritz-strategy-platform --install-claude-strategy-mcp\n'
            'result_code=$?\n'
            'echo\n'
            'if [[ $result_code -eq 0 ]]; then\n'
            '  echo "완료: Claude Desktop을 완전히 종료한 뒤 다시 열어 주세요."\n'
            'else\n'
            '  echo "등록에 실패했습니다. 위 안내를 확인해 주세요."\n'
            'fi\n'
            'read -k 1 "?아무 키나 누르면 이 창을 닫습니다."\n'
            'echo\n'
            'exit $result_code\n')
        claude_launcher.chmod(0o755)
    else:
        (bundle / 'Start.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\nmeritz-strategy-platform.exe\r\n')
    from claude_code_launcher import write_launcher
    write_launcher(bundle, platform.system())
    manifest = {'status': 'SOURCE_CHANGED_REBUILD_REQUIRED' if changed else 'UNSIGNED_REVIEW_CANDIDATE',
                'os': platform.system(), 'architecture': platform.machine(), 'python': sys.version,
                'dependencies': dependencies, 'timezoneSupport': timezone_evidence, 'dependencyValidation': 'exact macOS arm64 pins' if platform.system() == 'Darwin' and platform.machine() == 'arm64' else 'inventory only; target-platform validation required',
                'standardLibraryRoot': str(Path(os.__file__).parent.resolve()),
                'sourceRoot': str(source),
                'bundleHashes': {str(p.relative_to(bundle)): digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()},
                'thirdPartyNoticesSource': 'gen_notices.py on target OS build environment',
                'sourceHashes': before, 'changedDuringBuild': changed, 'command': command,
                'installer': False, 'signedOrNotarized': False, 'windowsTested': False, 'webviewBundled': webview_bundled,
                'validation': 'Build only; functional and sandbox tests required separately.'}
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(bundle)
    if changed:
        raise SystemExit('Source changed during build; do not distribute this candidate.')


if __name__ == '__main__':
    main()
