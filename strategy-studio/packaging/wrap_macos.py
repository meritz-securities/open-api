"""Wrap a verified native onedir into an unsigned macOS beta app and DMG.

App name, bundle version and DMG name come from strategy-studio/pyproject.toml so the
release asset always carries the version that was built.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

APP_NAME = 'Meritz Strategy Studio Beta'
BUNDLE_ID = 'com.meritz.strategy.studio-beta'


def studio_version(source_root):
    data = tomllib.loads((source_root / 'pyproject.toml').read_text(encoding='utf-8'))
    version = data['project']['version']
    # CFBundleShortVersionString 은 숫자.숫자.숫자 만 허용된다. 베타 접미(b1)는 CFBundleVersion 에 남긴다.
    short = re.match(r'\d+\.\d+\.\d+', version).group(0)
    return version, short


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin': parser.error('Native macOS required')
    bundle=args.bundle.resolve();output=args.output.resolve()
    if output.exists():parser.error('Output must be a new directory')
    if not (bundle/'meritz-strategy-platform').is_file() or not (bundle/'_internal/libpython3.11.dylib').exists():
        found = sorted(p.name for p in (bundle/'_internal').glob('*ython*')) if (bundle/'_internal').is_dir() else []
        parser.error('Expected the verified Python3.11 native onedir layout (_internal/libpython3.11.dylib). '
                     f'Found: {found or "no _internal"}. Build with a uv-managed CPython 3.11, not a framework build.')
    version, short = studio_version(Path(__file__).resolve().parents[1])
    arch = {'arm64': 'arm64', 'x86_64': 'x64'}[platform.machine()]
    chip = {'arm64': 'Apple 칩', 'x64': 'Intel'}[arch]
    contents=output/f'review/{APP_NAME}.app/Contents'
    contents.mkdir(parents=True)
    (contents/'MacOS').mkdir()
    shutil.copytree(bundle,contents/'MacOS/runtime',symlinks=True)
    # Keep the already verified onedir intact below MacOS/runtime. Directly
    # placing its executable in MacOS makes the bootloader switch _MEIPASS to
    # Frameworks and can break the deliberately narrow worker sandbox.
    launcher=contents/'MacOS/launch'
    launcher.write_text('#!/bin/sh\nexec "$(dirname "$0")/runtime/meritz-strategy-platform" "$@"\n')
    launcher.chmod(0o755)
    (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'launch',
        'CFBundleIdentifier':BUNDLE_ID,'CFBundleName':APP_NAME,'CFBundleDisplayName':APP_NAME,
        'CFBundlePackageType':'APPL','CFBundleShortVersionString':short,'CFBundleVersion':version,
        'LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True}))
    env={k:v for k,v in os.environ.items() if not k.startswith('MERITZ_')}
    env.update(MERITZ_NO_ENV_FILE='1',MERITZ_READ_ONLY='1')
    with tempfile.TemporaryDirectory(prefix='meritz-app-wrap-check-') as root:
        result=subprocess.run([str(contents/'MacOS/runtime/meritz-strategy-platform'),'--cli','templates','--root',root],
                              env=env,capture_output=True,text=True,check=True,timeout=20)
        if not json.loads(result.stdout)['result']['templates']:raise RuntimeError('Packaged templates absent')
    (output/'review/READ-ME.txt').write_text(
        f'Meritz Strategy Studio 베타 {version} (macOS, {chip})\n\n'
        '코드 서명·공증을 하지 않은 베타 빌드입니다. 처음 열 때 "열 수 없습니다" 가 뜨면\n'
        '시스템 설정 → 개인정보 보호 및 보안 아래쪽의 "그래도 열기" 를 한 번 누르십시오.\n'
        '앱을 응용 프로그램 폴더로 복사한 뒤 더블클릭하면 기본 브라우저에 Studio 가 열립니다.\n'
        '앱 패키지는 그대로 두십시오. 기존 MCP 설정은 바꾸지 않습니다.\n'
        '이용 조건은 앱 안의 DISCLAIMER.md 를 보십시오.\n', encoding='utf-8')
    from claude_code_launcher import write_launcher
    write_launcher(output/'review', 'Darwin',
                   f'{APP_NAME}.app/Contents/MacOS/runtime/meritz-strategy-platform')
    image=output/f'Meritz-Strategy-Studio-macOS-{arch}-beta.dmg'
    subprocess.run(['hdiutil','create','-volname',APP_NAME,'-srcfolder',str(output/'review'),'-format','UDZO',str(image)],check=True)
    subprocess.run(['hdiutil','verify',str(image)],check=True)
    print(image)


if __name__=='__main__':main()
