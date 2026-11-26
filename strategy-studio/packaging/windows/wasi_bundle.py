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
    env.update(PATH='', MERITZ_NO_ENV_FILE='1', MERITZ_READ_ONLY='1', MERITZ_CLI_DIAGNOSTICS='1')
    with tempfile.TemporaryDirectory() as root:
        def call(action, args):
            request = Path(root) / 'request.json'
            request.write_text(json.dumps(args), encoding='utf-8')
            run = subprocess.run([str(bundle / 'meritz-strategy-platform.exe'), '--cli', action,
                                  '--arguments', '@' + str(request), '--root', root],
                                 capture_output=True, timeout=85, env=env)
            if run.returncode != 0:
                # CI 로그에서 원인을 볼 수 있게 출력 꼬리를 남긴다. 고객 키·경로는 이 검사에 없다(합성 데이터·빈 PATH).
                raise RuntimeError(f'bundled worker check {action!r} exited {run.returncode}\n'
                                   f'--- stdout ---\n{run.stdout.decode("utf-8", "replace")[-2000:]}\n'
                                   f'--- stderr ---\n{run.stderr.decode("utf-8", "replace")[-4000:]}')
            return json.loads(run.stdout)['result']

        # 먼저 동봉 WebAssembly 워커만 직접 띄워 본다. 전략 실행 경로는 워커 오류를 고객에게 숨기므로
        # (설계) 패키징 단계에서는 워커 자체의 stderr 로 원인을 드러내야 한다. 고객 코드는 없다.
        probe = subprocess.run([str(bundle / 'meritz-strategy-platform.exe'), '--wasi-worker', '256'],
                               input=(json.dumps("print('wasi-probe-ok')") + '\n').encode(),
                               capture_output=True, timeout=85, env=env)
        if probe.returncode != 0 or b'wasi-probe-ok' not in probe.stdout:
            raise RuntimeError(f'bundled WASI worker probe exited {probe.returncode}\n'
                               f'--- stdout ---\n{probe.stdout.decode("utf-8", "replace")[-1000:]}\n'
                               f'--- stderr ---\n{probe.stderr.decode("utf-8", "replace")[-4000:]}')

        # 두 번째: 실제 WindowsWorker 와 같은 조건(워커 부트스트랩 전송, SYSTEMROOT 등만 남긴 환경)으로 띄워
        # 첫 응답(checks)을 확인한다. 전략 실행 경로는 이 단계의 실패를 '정상 응답 없이 종료' 로만 보고한다.
        bootstrap = (bundle / '_internal' / 'meritz' / 'strategy_worker.py').read_text(encoding='utf-8')
        from meritz.windows_strategy import worker_environment
        worker_env = worker_environment()
        for label, probe_env in (('verify-env', env), ('worker-env', worker_env)):
            started = subprocess.run([str(bundle / 'meritz-strategy-platform.exe'), '--wasi-worker', '256'],
                                     input=(json.dumps(bootstrap) + '\n').encode(),
                                     capture_output=True, timeout=85, env=probe_env)
            first_line = started.stdout.split(b'\n', 1)[0]
            try:
                checks = json.loads(first_line).get('checks')
            except ValueError:
                checks = None
            if not isinstance(checks, dict) or set(checks) != {'wasi', 'readonly', 'environment'} \
                    or not all(v is True for v in checks.values()):
                raise RuntimeError(f'bundled worker bootstrap probe ({label}) failed: checks={checks!r} exit={started.returncode}\n'
                                   f'--- stdout ---\n{started.stdout.decode("utf-8", "replace")[-1000:]}\n'
                                   f'--- stderr ---\n{started.stderr.decode("utf-8", "replace")[-4000:]}')
            print(f'[wasi_bundle] worker bootstrap probe ({label}) ok: {checks}', flush=True)

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
