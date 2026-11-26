"""Initial DSL drafts only: no baseline, revision persistence, engine, or orders."""
from __future__ import annotations

import copy
from functools import wraps
import json
import math
import os
import re
import threading
import time
import uuid

from .dsl import validate_definition
from .storage import canonical, digest

CONSENT_VERSION = 'initial-dsl-draft-v1'
SCHEMA_VERSION = 1
_PROCESS_TOKEN = uuid.uuid4().hex
_FIELDS = {'provider', 'model', 'maxOutputTokens', 'timeoutSeconds', 'connectionId'}
_TERMINAL = {'ready', 'failed', 'cancelled'}


class DraftError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _safe(method):
    @wraps(method)
    def invoke(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except DraftError as error:
            code = error.code
        except Exception:
            code = 'DRAFT_STORAGE_ERROR'
        # Outside the handler: no provider response/JSON/SQLite exception context.
        raise DraftError(code)
    return invoke


def _identifier(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise DraftError('DRAFT_INVALID_ID')
    return value


def _metadata(value):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise DraftError('DRAFT_INVALID_METADATA')
    for key in ('provider', 'model', 'connectionId'):
        if not isinstance(value[key], str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value[key]) is None:
            raise DraftError('DRAFT_INVALID_METADATA')
    if type(value['maxOutputTokens']) is not int or not 1 <= value['maxOutputTokens'] <= 32768:
        raise DraftError('DRAFT_INVALID_METADATA')
    seconds = value['timeoutSeconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise DraftError('DRAFT_INVALID_METADATA')
    return copy.deepcopy(value)


def _request(value):
    valid = isinstance(value, str) and 1 <= len(value.strip()) <= 2000 and len(value) <= 2000
    try:
        valid = valid and len(value.encode('utf-8')) <= 8000
    except UnicodeError:
        valid = False
    if not valid:
        raise DraftError('DRAFT_INVALID_REQUEST')
    return value


def _prompt(request):
    return canonical({'task': 'Create 1 to 3 initial DSL strategy drafts. No execution.',
                      'request': request, 'schemaVersion': SCHEMA_VERSION})


def _binding(value):
    return {key: value[key] for key in ('id', 'request', 'userPrompt', 'metadata', 'schemaVersion', 'consentVersion')}


def _candidates(value):
    try:
        if not isinstance(value, list) or not 1 <= len(value) <= 3 or len(canonical(value).encode()) > 240_000:
            raise ValueError()
        if any(not isinstance(item, dict) or item.get('kind') != 'dsl' for item in value):
            raise ValueError()
        result = [validate_definition(item) for item in value]
        if len(canonical(result).encode()) > 240_000:
            raise ValueError()
        return result
    except Exception:
        pass
    raise DraftError('DRAFT_INVALID_OUTPUT')


class DraftController:
    """Trusted fixed sender/config. Public HTTP callers must not construct this."""
    @_safe
    def __init__(self, store, provider=None, metadata=None):
        if provider is None and metadata is None:
            self._metadata = None
        else:
            if not callable(provider):
                raise DraftError('AI_DRAFT_PROVIDER_REQUIRED')
            self._metadata = _metadata(metadata)
        self._provider = provider
        self.store = store
        with store.connection() as con:
            con.execute('CREATE TABLE IF NOT EXISTS ai_drafts '
                        '(id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL)')

    def _load(self, con, ident):
        row = con.execute('SELECT state,body FROM ai_drafts WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise DraftError('DRAFT_NOT_FOUND')
        try:
            if not isinstance(row[1], str) or len(row[1].encode()) > 300_000:
                raise ValueError()
            value = json.loads(row[1])
            allowed = {'id','state','request','userPrompt','metadata','schemaVersion','consentVersion',
                       'promptHash','cancelRequested','owner','result','error'}
            if set(value) - allowed or value['id'] != ident or value['state'] != row[0]:
                raise ValueError()
            if value['state'] not in _TERMINAL | {'preview','generating'} or type(value['cancelRequested']) is not bool:
                raise ValueError()
            if type(value['schemaVersion']) is not int or value['schemaVersion'] != SCHEMA_VERSION or value['consentVersion'] != CONSENT_VERSION:
                raise ValueError()
            if 'owner' in value:
                owner=value['owner']
                if (set(owner) != {'pid','instance'} or type(owner['pid']) is not int or owner['pid'] <= 0
                        or not isinstance(owner['instance'],str) or re.fullmatch('[0-9a-f]{32}',owner['instance']) is None):
                    raise ValueError()
            if 'error' in value and value['error'] not in {'DRAFT_PROVIDER_FAILED','DRAFT_INVALID_OUTPUT','DRAFT_TIMEOUT','DRAFT_CANCEL_STATE_UNAVAILABLE','DRAFT_START_FAILED'}:
                raise ValueError()
            state = value['state']
            if state == 'preview' and (value['cancelRequested'] or any(k in value for k in ('owner','error','result'))):
                raise ValueError()
            if state in ('generating','ready','failed') and 'owner' not in value:
                raise ValueError()
            if (state == 'failed') != ('error' in value):
                raise ValueError()
            if state == 'cancelled' and not value['cancelRequested']:
                raise ValueError()
            if state == 'ready' and value['cancelRequested']:
                raise ValueError()
            _metadata(value['metadata'])
            if value['userPrompt'] != _prompt(_request(value['request'])) or digest(_binding(value)) != value['promptHash']:
                raise ValueError()
            if 'result' in value:
                result = value['result']
                validated = _candidates(result['candidates'])
                if (set(result) != {'kind','candidates','candidateHash'} or result['kind'] != 'draft'
                        or validated != result['candidates'] or digest(validated) != result['candidateHash']):
                    raise ValueError()
            if (value['state']=='ready') != ('result' in value):
                raise ValueError()
            return value
        except Exception:
            pass
        raise DraftError('DRAFT_INTEGRITY_ERROR')

    def _write(self, con, value):
        con.execute('UPDATE ai_drafts SET state=?,body=? WHERE id=?',
                    (value['state'], canonical(value), value['id']))

    def _public(self, value):
        value = copy.deepcopy(value)
        owner = value.pop('owner', None)
        value['owner'] = None if owner is None else {
            'pid': owner['pid'], 'currentProcess': owner == {'pid': os.getpid(), 'instance': _PROCESS_TOKEN},
            'liveness': 'unknown', 'notice': '다른 프로세스일 수 있으며 생존 여부를 판정하지 않습니다.'}
        value['previewScope'] = 'user-data-only; sender may add fixed system instructions and output schema'
        value['modelInvocationVerified'] = False
        return value

    @_safe
    def create(self, request, requestId=None):
        if self._provider is None:
            raise DraftError('AI_DRAFT_PROVIDER_REQUIRED')
        request = _request(request)
        ident = uuid.uuid4().hex if requestId is None else _identifier(requestId)
        value = {'id':ident,'state':'preview','request':request,'userPrompt':_prompt(request),
                 'metadata':copy.deepcopy(self._metadata),'schemaVersion':SCHEMA_VERSION,
                 'consentVersion':CONSENT_VERSION,'cancelRequested':False}
        value['promptHash'] = digest(_binding(value))
        with self.store.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            exists = con.execute('SELECT 1 FROM ai_drafts WHERE id=?',(ident,)).fetchone()
            if exists:
                prior = self._load(con,ident)
                if _binding(prior) != _binding(value):
                    raise DraftError('DRAFT_ID_CONFLICT')
                return self._public(prior)
            con.execute('INSERT INTO ai_drafts(id,state,body) VALUES(?,?,?)',
                        (ident,'preview',canonical(value)))
        return self._public(value)

    @_safe
    def get(self, requestId):
        with self.store.connection() as con:
            return self._public(self._load(con,_identifier(requestId)))

    @_safe
    def cancel(self, requestId):
        with self.store.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            value = self._load(con,_identifier(requestId))
            if value['state'] not in _TERMINAL:
                value['cancelRequested'] = True
                if value['state'] == 'preview':
                    value['state'] = 'cancelled'
                self._write(con,value)
        return self._public(value)

    @_safe
    def generate(self, requestId, promptHash, consentVersion):
        if self._provider is None:
            raise DraftError('AI_DRAFT_PROVIDER_REQUIRED')
        ident = _identifier(requestId)
        with self.store.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            value = self._load(con,ident)
            if promptHash != value['promptHash'] or consentVersion != value['consentVersion']:
                raise DraftError('DRAFT_CONSENT_MISMATCH')
            if value['state'] != 'preview':
                return self._public(value)  # No rerun, including uncertain generating.
            if value['metadata'] != self._metadata:
                raise DraftError('DRAFT_CONFIG_CHANGED')
            value.update(state='generating',owner={'pid':os.getpid(),'instance':_PROCESS_TOKEN})
            self._write(con,value)
        sender = self._provider
        event, stop = threading.Event(), threading.Event()
        control = {'error': None}
        deadline = time.monotonic() + self._metadata['timeoutSeconds']
        def watch():
            while not stop.wait(.025):
                if time.monotonic() >= deadline:
                    control['error'] = 'DRAFT_TIMEOUT';event.set();return
                try:
                    state = self.get(ident)
                    if state['cancelRequested']:
                        event.set();return
                except Exception:
                    control['error'] = 'DRAFT_CANCEL_STATE_UNAVAILABLE';event.set();return
        watcher = None
        watcher_started = False
        output, failure = None, None
        try:
            try:
                watcher = threading.Thread(target=watch,daemon=True)
                watcher.start()
                watcher_started = True
            except Exception:
                failure = 'DRAFT_START_FAILED'
            # A failed setup/read is known-unsent. Persist failure if storage
            # permits; if finalization also fails, leave the claim uncertain.
            if failure is None:
                try:
                    if self.get(ident)['cancelRequested']:
                        event.set()
                except Exception:
                    failure = 'DRAFT_CANCEL_STATE_UNAVAILABLE'
            if failure is None and not event.is_set():
                try:
                    output = sender(value['userPrompt'],event)
                except Exception:
                    failure = 'DRAFT_PROVIDER_FAILED'
                if failure is None:
                    try:
                        output = _candidates(output)
                    except DraftError:
                        failure = 'DRAFT_INVALID_OUTPUT'
        finally:
            stop.set()
            if watcher_started:
                watcher.join(timeout=.25)
        # Completion/cancel linearize under one SQLite transaction. Once ready is
        # written, later cancellation observes a terminal row and cannot alter it.
        with self.store.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            latest = self._load(con,ident)
            if control['error']:
                latest.update(state='failed',error=control['error'])
            elif latest['cancelRequested']:
                latest.update(state='cancelled')
            elif time.monotonic() >= deadline:
                latest.update(state='failed',error='DRAFT_TIMEOUT')
            elif failure:
                latest.update(state='failed',error=failure)
            else:
                latest.update(state='ready',result={'kind':'draft','candidates':output,'candidateHash':digest(output)})
            self._write(con,latest)
        return self._public(latest)
