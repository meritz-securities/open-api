"""Wrap a verified native onedir into an unsigned macOS preview app and DMG."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin': parser.error('Native macOS required')
    bundle=args.bundle.resolve();output=args.output.resolve()
    if output.exists():parser.error('Output must be a new directory')
    if not (bundle/'meritz-strategy-platform').is_file() or not (bundle/'_internal/libpython3.11.dylib').exists():
        parser.error('Expected the verified Python3.11 native onedir layout')
    contents=output/'review/Meritz Strategy Studio Preview.app/Contents'
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
        'CFBundleIdentifier':'com.meritz.strategy.local-preview','CFBundleName':'Meritz Strategy Studio Preview',
        'CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'4',
        'LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True}))
    env={k:v for k,v in os.environ.items() if not k.startswith('MERITZ_')}
    env.update(MERITZ_NO_ENV_FILE='1',MERITZ_READ_ONLY='1')
    with tempfile.TemporaryDirectory(prefix='meritz-app-wrap-check-') as root:
        result=subprocess.run([str(contents/'MacOS/runtime/meritz-strategy-platform'),'--cli','templates','--root',root],
                              env=env,capture_output=True,text=True,check=True,timeout=20)
        if not json.loads(result.stdout)['result']['templates']:raise RuntimeError('Packaged templates absent')
    (output/'review/READ-ME.txt').write_text('Unsigned development preview. Not notarized or certified for customer release.\nKeep this app intact. Existing MCP installations are not replaced.\n')
    from claude_code_launcher import write_launcher
    write_launcher(output/'review', 'Darwin',
                   'Meritz Strategy Studio Preview.app/Contents/MacOS/runtime/meritz-strategy-platform')
    image=output/'Meritz-Strategy-Studio-macOS-arm64-preview.dmg'
    subprocess.run(['hdiutil','create','-volname','Meritz Strategy Preview','-srcfolder',str(output/'review'),'-format','UDZO',str(image)],check=True)
    subprocess.run(['hdiutil','verify',str(image)],check=True)
    print(image)


if __name__=='__main__':main()
