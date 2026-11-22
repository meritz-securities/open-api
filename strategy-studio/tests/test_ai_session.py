import json
import threading
from dataclasses import dataclass, field

import pytest

from runtime.ai_session import AISession


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    model: str
    workspace_id: str | None = None
    max_tokens: int = 4096
    timeout_seconds: float = 120

    def validate(self):
        if self.api_key == 'invalid-secret':
            raise ValueError(self.api_key)


def setup_session(allowed=True):
    calls = []
    def send(config, prompt, event):
        calls.append((config, prompt, event))
        return [{'fixture': True}]
    return AISession(config_factory=Config, send=send, models=['fixture-model'],
                     management_allowed=allowed), calls


def connect(session, key='private-test-key'):
    return session.connect(apiKey=key, model='fixture-model')


def test_memory_configuration_never_calls_provider_and_is_not_authenticated():
    session, calls = setup_session()
    assert not session.status()['configured']
    first = connect(session)
    assert first['configured'] and first['authenticated'] is None and not first['busy']
    assert 'private-test-key' not in json.dumps(first) + repr(session)
    first['configuration']['model'] = 'mutated'
    assert session.status()['configuration']['model'] == 'fixture-model'
    assert calls == []
    assert not session.disconnect()['configured']
    assert calls == []


def test_management_is_disabled_by_default_and_existing_config_unchanged_on_error():
    session, _ = setup_session(False)
    with pytest.raises(ValueError, match='MANAGEMENT_DISABLED'):
        connect(session)
    with pytest.raises(ValueError, match='MANAGEMENT_DISABLED'):
        session.disconnect()
    session, calls = setup_session()
    before = connect(session)
    with pytest.raises(ValueError, match='CONFIG_INVALID') as error:
        connect(session, 'invalid-secret')
    assert error.value.__context__ is None
    assert session.status() == before and calls == []
    with pytest.raises(ValueError, match='MODEL_UNSUPPORTED'):
        session.connect(apiKey='hidden', model='https://untrusted.invalid')
    assert session.status() == before


def test_explicit_pinned_generation_only_and_reconfiguration_blocked():
    session, calls = setup_session()
    config = connect(session)['configuration']
    event = threading.Event()
    with session.lease(config['connectionId']) as lease:
        assert calls == [] and session.status()['busy']
        assert 'private-test-key' not in repr(lease)
        with pytest.raises(ValueError, match='BUSY'):
            connect(session, 'another-key')
        with pytest.raises(ValueError, match='BUSY'):
            session.disconnect()
        with pytest.raises(ValueError, match='BUSY'):
            with session.lease(config['connectionId']):
                pass
        assert lease.generate('approved input', event) == [{'fixture': True}]
    assert not session.status()['busy']
    assert len(calls) == 1 and calls[0][1:] == ('approved input', event)
    assert calls[0][0].api_key == 'private-test-key'
    with pytest.raises(ValueError, match='LEASE_CLOSED'):
        lease.generate('stale', event)
    assert len(calls) == 1


def test_reconnect_changes_binding_and_interruption_releases_busy():
    session, _ = setup_session()
    old = connect(session)['configuration']['connectionId']
    new = connect(session)['configuration']['connectionId']
    assert old != new
    with pytest.raises(ValueError, match='CHANGED'):
        with session.lease(old):
            pass
    with pytest.raises(KeyboardInterrupt):
        with session.lease(new):
            raise KeyboardInterrupt()
    assert not session.status()['busy']
    session.disconnect()
    with pytest.raises(ValueError, match='REQUIRED'):
        with session.lease(new):
            pass
