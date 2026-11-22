"""Standalone-only in-memory credentials. No persistence or network operations."""
from contextlib import contextmanager
from datetime import datetime, timezone
import threading

from meritz.accounts import Registry
from meritz.config import Settings


class MarketConnection:
    def __init__(self):
        self._lock = threading.Lock()
        self._registry = None
        self._busy = False
        self._last = None

    def _status(self):
        return {'managementAllowed': True, 'source': 'session',
                'configured': self._registry is not None, 'authenticated': None,
                'busy': self._busy, 'lastRequest': dict(self._last) if self._last else None,
                'notice': '이 앱을 여는 동안만 사용합니다. 종료 후 다시 입력하세요.'}

    def status(self):
        with self._lock:
            return self._status()

    @staticmethod
    def _valid_secret(value):
        return (isinstance(value, str) and 1 <= len(value) <= 4096
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value))

    def connect(self, appKey, appSecret):
        if not self._valid_secret(appKey) or not self._valid_secret(appSecret):
            raise ValueError('키와 시크릿은 공백 없는 1~4096자여야 합니다.')
        with self._lock:
            if self._busy:
                raise ValueError('시세 조회 중에는 연결을 변경할 수 없습니다.')
            # Explicit Settings uses official production defaults, no environment
            # loading, caller-provided endpoint, file, or shared MCP mutation.
            self._registry = Registry([Settings(label='현재 연결', app_key=appKey,
                                                app_secret=appSecret, read_only=True)])
            self._last = None
            return self._status()

    def disconnect(self):
        with self._lock:
            if self._busy:
                raise ValueError('시세 조회 중에는 연결을 해제할 수 없습니다.')
            self._registry = None
            self._last = None
            return self._status()

    @contextmanager
    def fetch(self):
        with self._lock:
            if self._busy:
                raise ValueError('시세 조회가 이미 진행 중입니다.')
            if self._registry is None:
                raise ValueError('먼저 메리츠 연결을 설정하세요.')
            registry = self._registry
            self._busy = True
        succeeded = False
        try:
            yield registry
            succeeded = True
        finally:
            with self._lock:
                self._busy = False
                self._last = {'status': 'succeeded' if succeeded else 'failed',
                              'finishedAt': datetime.now(timezone.utc).isoformat(),
                              'errorCode': None if succeeded else 'MARKET_FETCH_FAILED'}
