"""Memory-only model connection; trusted host wiring supplies the transport.

No HTTP registration, credential discovery, automatic authentication, or model call.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
import uuid


@dataclass(frozen=True)
class _Lease:
    metadata: dict
    _config: object = field(repr=False)
    _send: object = field(repr=False)
    _active: threading.Event = field(repr=False)

    def generate(self, prompt, cancel_event):
        if not self._active.is_set():
            raise ValueError('AI_CONNECTION_LEASE_CLOSED')
        return self._send(self._config, prompt, cancel_event)


class AISession:
    def __init__(self, *, config_factory, send, models, management_allowed=False):
        if (type(models) not in (list, tuple) or not models or
                any(not isinstance(model, str) or not model for model in models)):
            raise ValueError('AI_CONNECTION_MODELS_INVALID')
        self._factory, self._send = config_factory, send
        self._models = tuple(models)
        self._allowed = management_allowed is True
        self._lock = threading.RLock()
        self._config = None
        self._connection_id = None
        self._busy = False

    def _metadata(self):
        if self._config is None:
            raise ValueError('AI_CONNECTION_REQUIRED')
        return {'provider': 'claude-api', 'model': self._config.model,
                'maxOutputTokens': self._config.max_tokens,
                'timeoutSeconds': self._config.timeout_seconds,
                'connectionId': self._connection_id}

    def status(self):
        with self._lock:
            return {'managementAllowed': self._allowed,
                    'configured': self._config is not None,
                    'authenticated': None, 'busy': self._busy,
                    'models': list(self._models),
                    'configuration': self._metadata() if self._config is not None else None,
                    'notice': '설정만으로 인증하거나 모델을 호출하지 않습니다. 생성 요청은 별도 과금될 수 있습니다.'}

    def _modifiable(self):
        if not self._allowed:
            raise ValueError('AI_CONNECTION_MANAGEMENT_DISABLED')
        if self._busy:
            raise ValueError('AI_CONNECTION_BUSY')

    def connect(self, *, apiKey, model, workspaceId=None,
                maxOutputTokens=4096, timeoutSeconds=120):
        with self._lock:
            self._modifiable()
            if model not in self._models:
                raise ValueError('AI_CONNECTION_MODEL_UNSUPPORTED')
            valid = False
            try:
                config = self._factory(api_key=apiKey, model=model,
                                       workspace_id=workspaceId,
                                       max_tokens=maxOutputTokens,
                                       timeout_seconds=timeoutSeconds)
                config.validate()
                valid = True
            except Exception:
                pass
            # Do not retain a validation exception containing the submitted key.
            if not valid:
                raise ValueError('AI_CONNECTION_CONFIG_INVALID')
            self._config = config
            self._connection_id = uuid.uuid4().hex
            return self.status()

    def disconnect(self):
        with self._lock:
            self._modifiable()
            self._config = None
            self._connection_id = None
            return self.status()

    @contextmanager
    def lease(self, connectionId):
        """Pin one explicit connection during generation, never send on acquisition."""
        with self._lock:
            if self._busy:
                raise ValueError('AI_CONNECTION_BUSY')
            metadata = self._metadata()
            if not isinstance(connectionId, str) or connectionId != self._connection_id:
                raise ValueError('AI_CONNECTION_CHANGED')
            active = threading.Event()
            active.set()
            lease = _Lease(metadata, self._config, self._send, active)
            self._busy = True
        try:
            yield lease
        finally:
            with self._lock:
                active.clear()
                self._busy = False
