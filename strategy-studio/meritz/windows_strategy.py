"""Bounded pipe transport for the bundled WebAssembly strategy worker."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from .strategy_runner import StrategyError
from .wasi_host import assets

SETUP = '동봉된 Python 실행기를 찾을 수 없습니다. Studio 전체 배포본을 다시 열어 주세요.'
# 워커 프로세스에 넘기는 호스트 환경변수. 고객 키·경로가 새지 않게 최소만 남긴다.
# PROCESSOR_ARCHITECTURE 는 wasmtime 이 platform.machine() 으로 바인딩 DLL 폴더를 고를 때 읽는다 —
# 빠지면 'unsupported architecture for wasmtime: ' 로 워커가 시작하지 못한다.
WORKER_ENV_KEYS = frozenset({'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'SYSTEMDRIVE',
                             'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432'})


def available():
    root = assets()
    return all((root / name).is_file() for name in
               ('python.wasm', 'stdlib/lib/python313.zip', 'host/wasmtime/__init__.py'))


def worker_environment():
    return {k: v for k, v in os.environ.items() if k.upper() in WORKER_ENV_KEYS}


def _command(memory_mib):
    if not available():
        raise StrategyError(SETUP)
    launcher = [sys.executable]
    if not getattr(sys, 'frozen', False):
        launcher.append(str(Path(__file__).resolve().parent.parent / 'platform_entry.py'))
    return [*launcher, '--wasi-worker', str(memory_mib)]


class WindowsWorker:
    def __init__(self, wall_seconds, bar_seconds, memory_mib):
        self.requests = queue.Queue()
        self.responses = queue.Queue()
        self.bar_seconds = bar_seconds
        self.stderr_overflow = threading.Event()
        self.threads = []
        bootstrap = Path(__file__).with_name('strategy_worker.py').read_text(encoding='utf-8')
        command = _command(memory_mib)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, close_fds=True,
                                        env=worker_environment(),
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.deadline = time.monotonic() + 30
            for task in (self._exchange, self._logs):
                thread = threading.Thread(target=task, daemon=True)
                thread.start()
                self.threads.append(thread)
            self.send(bootstrap)
            checks = self.receive(startup=True).get('checks')
            if (not isinstance(checks, dict) or set(checks) != {'wasi', 'readonly', 'environment'}
                    or not all(v is True for v in checks.values())):
                raise StrategyError('Windows Python 격리 자체 검사에 실패했습니다.')
            self.sandbox = {'platform': 'Windows', 'mechanism': 'WebAssembly (WASI)', 'checks': checks}
            self.deadline = time.monotonic() + wall_seconds
        except BaseException:
            self.close()
            raise

    def _logs(self):
        # Bound diagnostic output without exposing code-controlled text.
        try:
            if len(self.process.stderr.read(8193)) > 8192:
                self.stderr_overflow.set()
        except (ValueError, OSError):
            pass

    def _exchange(self):
        total = 0
        try:
            while (request := self.requests.get()) is not None:
                self.process.stdin.write(request)
                self.process.stdin.flush()
                line = self.process.stdout.readline(8193)
                total += len(line)
                if not line.endswith(b'\n') or len(line) > 8192 or total > 1_048_576:
                    break
                response = json.loads(line)
                if not isinstance(response, dict):
                    break
                self.responses.put(response)
        except (ValueError, RecursionError, OSError):
            pass
        self.responses.put(None)

    def send(self, value):
        data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode() + b'\n'
        if len(data) > 1_048_576:
            raise StrategyError('전략 입력 크기 제한을 초과했습니다.')
        self.requests.put(data)

    def receive(self, startup=False):
        deadline = self.deadline if startup else min(self.deadline, time.monotonic() + self.bar_seconds)
        while time.monotonic() < deadline:
            if self.stderr_overflow.is_set():
                raise StrategyError('전략 로그 출력 제한을 초과했습니다.')
            try:
                response = self.responses.get(timeout=min(.05, max(0, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if response is None:
                raise StrategyError('Windows Python 격리 프로세스가 정상 응답 없이 종료됐습니다.')
            return response
        raise StrategyError('전략 실행 시간 제한을 초과했습니다.')

    def close(self):
        self.requests.put(None)
        self.process.kill()
        self.process.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=1)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()
