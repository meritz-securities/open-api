"""Prepare the pinned, offline-at-runtime Windows strategy interpreter during build."""
import hashlib
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path
import urllib.request
import zipfile

PYTHON_VERSION = '3.13.11'
WHEEL = 'wasmtime-50.0.1-py3-none-win_amd64.whl'
WHEEL_HASH = 'a8999581230471f4ff62073b1111eff3a01d45ccec0a908da3f2ae3a499d459c'


def fetch(url):
    with urllib.request.urlopen(url, timeout=90) as response:
        return response.read()


def archive(url, checksum):
    raw = fetch(url)
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError('Runtime asset checksum mismatch')
    return zipfile.ZipFile(io.BytesIO(raw))


def prepare(output):
    root = Path(output) / 'strategy_wasi'
    lib = root / 'stdlib/lib'
    lib.mkdir(parents=True)
    metadata = json.loads(fetch('https://pypi.org/pypi/wasmtime/50.0.1/json'))
    wheel = next(asset for asset in metadata['urls'] if asset['filename'] == WHEEL)
    with archive(wheel['url'], WHEEL_HASH) as files:
        files.extractall(root / 'host')
    name = f'python-{PYTHON_VERSION}-wasi_sdk-24.zip'
    release = json.loads(fetch('https://api.github.com/repos/brettcannon/cpython-wasi-build/'
                               f'releases/tags/v{PYTHON_VERSION}'))
    asset = next(asset for asset in release['assets'] if asset['name'] == name)
    with archive(asset['browser_download_url'], asset['digest'].removeprefix('sha256:')) as files:
        paths = files.namelist()
        wasm, = [p for p in paths if Path(p).name == 'python.wasm']
        (root / 'python.wasm').write_bytes(files.read(wasm))
        target = lib / 'python313.zip'
        bundled = next((p for p in paths if Path(p).name == target.name), None)
        if bundled:
            target.write_bytes(files.read(bundled))
        else:
            encodings, = [p for p in paths if p.endswith('encodings/__init__.py')]
            prefix = encodings.removesuffix('encodings/__init__.py')
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as stdlib:
                for p in paths:
                    if p.startswith(prefix) and not p.endswith('/'):
                        stdlib.writestr(p[len(prefix):], files.read(p))
    (root / 'PYTHON-LICENSE.txt').write_bytes(fetch(
        f'https://raw.githubusercontent.com/python/cpython/v{PYTHON_VERSION}/LICENSE'))
    return root

def verify(bundle):
    """A successful Windows build must run the shipped worker with an empty PATH."""
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('MERITZ_', 'PYTHON'))}
    env.update(PATH='', MERITZ_NO_ENV_FILE='1', MERITZ_READ_ONLY='1')
    with tempfile.TemporaryDirectory() as root:
        def call(action, args):
            request = Path(root) / 'request.json'
            request.write_text(json.dumps(args), encoding='utf-8')
            run = subprocess.run([str(bundle / 'meritz-strategy-platform.exe'), '--cli', action,
                                  '--arguments', '@' + str(request), '--root', root],
                                 check=True, capture_output=True, timeout=85, env=env)
            return json.loads(run.stdout)['result']

        code = ('from meritz_strategy import Context\n'
                'def on_bar(ctx):\n'
                ' return Context(ctx).buy(1) if ctx["index"] == 0 else {"action":"hold"}')
        revision = call('save_strategy', {'definition':{'schemaVersion':1, 'kind':'python',
                                                        'name':'Bundled Python', 'code':code}})
        bars = [dict(date=f'2025-01-0{i}', open=p, high=p, low=p, close=p)
                for i,p in enumerate([10,20,30,40], 1)]
        data = call('import_data', {'bars':bars, 'source':{'kind':'imported','label':'Build fixture'}})
        result = call('run', {'revisionId':revision['id'], 'datasetId':data['id'],
                              'settings':{'initialCash':100,'feeBps':0,'taxBps':0,'slippageBps':0}})
        if result['report']['metrics']['finalEquity'] != 120:
            raise ValueError('Bundled Python strategy failed its equity check')
