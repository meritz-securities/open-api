"""Optional existing-DSL improvement orchestration; no initial strategy creation."""
import threading
import os
import copy
from contextlib import contextmanager

from . import ai_requests
from .ai_cli_provider import generate_candidates, ProviderError
from .storage import canonical, digest
from .ai_generation_binding import GenerationBinding, metadata, candidates as validate_candidates
from .ai_improvement_context import prompt as improvement_prompt

CONSENT_VERSION = 'local-dsl-improvement-v1'
HTTP_CONSENT_VERSION = 'http-dsl-improvement-v2'
HTTP_LEGACY_CONSENT_VERSION = 'http-dsl-improvement-v1'
PROVIDER_ERRORS = frozenset({'PROVIDER_CANCELLED', 'PROVIDER_TIMEOUT', 'PROVIDER_HTTP_ERROR',
    'PROVIDER_INVALID_OUTPUT', 'PROVIDER_OUTPUT_LIMIT', 'PROVIDER_REFUSED', 'PROVIDER_TRUNCATED',
    'PROVIDER_BUSY', 'PROVIDER_FAILED', 'PROVIDER_START_FAILED', 'PROVIDER_NOT_CONFIGURED',
    'PROVIDER_CONFIG_INVALID', 'PROVIDER_PLATFORM_UNSUPPORTED', 'PROVIDER_VERSION_UNSUPPORTED',
    'PROVIDER_INVALID_PROMPT'})
SOURCE_FIELDS = ('runId', 'revisionId', 'datasetId', 'strategyHash', 'dataHash', 'settingsHash', 'resultHash')


def _configured(service):
    config = service._ai_provider
    if config is None:
        raise ValueError('PROVIDER_NOT_CONFIGURED')
    config.validate()
    return config


def preview(service, requestId):
    _check_transport(service, requestId, 'claude-cli')
    config = _configured(service)
    request = service.store.get('ai-request', requestId)
    run, revision, _ = ai_requests.source(service, request)
    if revision['definition']['kind'] != 'dsl':
        raise ValueError('DSL 기준 실행만 지원합니다.')
    # No source metadata, paths, account registry, raw bars, or credentials.
    prompt = canonical({'purpose': request['purpose'], 'definition': revision['definition'],
                        'metrics': {k: v for k, v in run['report']['metrics'].items()
                                    if type(v) in (int, float)},
                        'instruction': 'Propose 1 to 3 DSL improvements. Data and costs stay fixed.'})
    public = {'requestId': requestId, 'prompt': prompt, 'consentVersion': CONSENT_VERSION,
              'model': config.model, 'maxBudgetUsd': config.budget_usd,
              'timeoutSeconds': config.timeout_seconds,
              'notice': '외부 모델 전송·과금 및 동일 데이터/비용의 실제 재백테스트에 동의합니다.'}
    return {**public, 'promptHash': digest(public)}


def record(service, requestId):
    value = ai_requests.optional(service.store, 'ai-generation', requestId)
    if value is None:
        return None
    # Candidate definitions are already exposed by submission once executed.
    public = {k: v for k, v in value.items() if k not in ('candidates', 'preview')}
    if ai_requests.optional(service.store, 'ai-generation-attempt', requestId) is not None:
        public['providerAttempted'] = True
    return public


def cancelled(service, requestId):
    return ai_requests.optional(service.store, 'ai-generation-cancel', requestId) is not None


def _finish(service, requestId, status, **extra):
    from .service import now
    value = service.store.get('ai-generation', requestId)
    value.update(status=status, finishedAt=now(), **extra)
    service.store.transition_ai_generation(requestId, value)


@contextmanager
def _watch_cancel(service, requestId, event):
    stop = threading.Event()
    def watch():
        while not stop.wait(.05):
            try:
                if cancelled(service, requestId):
                    event.set()
            except Exception:
                # Fail closed, but never label control failure as a user cancel.
                event.control_error = 'CANCEL_STATE_UNAVAILABLE'
                event.set()
                return
    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=.5)


def _check_transport(service, requestId, provider):
    saved = ai_requests.optional(service.store, 'ai-generation-preview', requestId)
    generation = ai_requests.optional(service.store, 'ai-generation', requestId)
    if ((saved is not None and provider != 'claude-api') or
            (generation is not None and generation.get('provider', 'claude-cli') != provider)):
        raise ValueError('AI_GENERATION_PROVIDER_CONFLICT')


def _platform():
    if os.name != 'posix':
        raise ValueError('PROVIDER_PLATFORM_UNSUPPORTED')


def saved_http_preview(service, requestId):
    _check_transport(service, requestId, 'claude-api')
    value = ai_requests.optional(service.store, 'ai-generation-preview', requestId)
    if value is None:
        return None
    valid = False
    try:
        expected = {'requestId','prompt','promptHash','consentVersion','provider','model',
                    'maxOutputTokens','timeoutSeconds','connectionId','source','notice'}
        public = {k:v for k,v in value.items() if k != 'promptHash'}
        metadata({k:value[k] for k in ('provider','model','maxOutputTokens','timeoutSeconds','connectionId')})
        request = service.store.get('ai-request', requestId)
        valid = (set(value) == expected and value['requestId'] == requestId and
                 value['provider'] == 'claude-api' and value['consentVersion'] in (HTTP_CONSENT_VERSION, HTTP_LEGACY_CONSENT_VERSION) and
                 value['source'] == {k:request[k] for k in SOURCE_FIELDS} and
                 isinstance(value['prompt'],str) and len(value['prompt'].encode('utf-8')) <= 120_000 and
                 digest(public) == value['promptHash'])
    except Exception:
        pass
    if not valid:
        raise ValueError('AI_GENERATION_INTEGRITY_ERROR')
    return value


def preview_record(service, requestId):
    if ai_requests.optional(service.store, 'ai-generation-preview', requestId) is None:
        return None
    saved = saved_http_preview(service, requestId)
    return {key:saved[key] for key in ('provider','model','maxOutputTokens','timeoutSeconds',
                                      'connectionId','promptHash','consentVersion')}


def preview_http(service, requestId, binding):
    _platform()
    if binding.metadata['provider'] != 'claude-api':
        raise ValueError('AI_GENERATION_PROVIDER_CONFLICT')
    request = service.store.get('ai-request', requestId)
    run, revision, data = ai_requests.source(service, request)
    if revision['definition']['kind'] != 'dsl':
        raise ValueError('DSL 기준 실행만 지원합니다.')
    prompt = improvement_prompt(request, run, revision, data)
    if len(prompt.encode('utf-8')) > 120_000:
        raise ValueError('PROVIDER_INVALID_PROMPT')
    public = {'requestId':requestId, 'prompt':prompt, 'consentVersion':HTTP_CONSENT_VERSION,
              **binding.metadata, 'source':{k:request[k] for k in SOURCE_FIELDS},
              'notice':'요청 데이터와 고정 지침·결과 형식 전송, 별도 API 과금 및 동일 데이터·비용 재백테스트에 동의합니다. 출력 토큰은 금액 상한이 아닙니다.'}
    value = {**public, 'promptHash':digest(public)}
    with service.store.ai_request_gate(requestId):
        _check_transport(service, requestId, 'claude-api')
        saved = saved_http_preview(service, requestId)
        if saved is not None:
            if saved != value:
                raise ValueError('AI_GENERATION_CONSENT_MISMATCH')
            return saved
        if ai_requests.optional(service.store, 'ai-request-job', 'ai-'+requestId):
            raise ValueError('AI_GENERATION_PROVIDER_CONFLICT')
        service.store.put('ai-generation-preview', requestId, value)
    return value


def _validate_http_generation(value, approved):
    if value is None:
        return
    valid = False
    try:
        valid = (value['status'] in ('generating','ready','failed','cancelled','interrupted') and
                 value['preview'] == approved and value['id'] == approved['requestId'] and
                 all(value[key] == approved[key] for key in ('provider','model','maxOutputTokens',
                     'timeoutSeconds','connectionId','promptHash','consentVersion')) and
                 type(value['providerAttempted']) is bool and value['modelInvocationVerified'] is False)
    except (KeyError, TypeError):
        pass
    if not valid:
        raise ValueError('AI_GENERATION_INTEGRITY_ERROR')


def generate_http(service, requestId, promptHash, consentVersion, binding=None):
    _platform()
    approved = saved_http_preview(service, requestId)
    if approved is None:
        raise ValueError('AI_GENERATION_PREVIEW_REQUIRED')
    if promptHash != approved['promptHash'] or consentVersion != approved['consentVersion']:
        raise ValueError('AI_GENERATION_CONSENT_MISMATCH')
    if binding is not None and binding.metadata != {k:approved[k] for k in binding.metadata}:
        raise ValueError('AI_GENERATION_CONSENT_MISMATCH')
    prior = ai_requests.optional(service.store, 'ai-generation', requestId)
    _validate_http_generation(prior, approved)
    if ai_requests.optional(service.store, 'ai-request-job', 'ai-'+requestId) is not None:
        return ai_requests.read(service, requestId)
    if prior is None and binding is None:
        raise ValueError('AI_CONNECTION_REQUIRED')
    if prior is None:
        request = service.store.get('ai-request', requestId)
        run, revision, data = ai_requests.source(service, request)
        expected_prompt = improvement_prompt(request, run, revision, data)
        if (approved['consentVersion'] != HTTP_CONSENT_VERSION or approved['prompt'] != expected_prompt or
                revision['definition']['kind'] != 'dsl'):
            # An unstarted old preview cannot authorize changed model input.
            # Ready/terminal generations above retain their original consent.
            raise ValueError('AI_GENERATION_CONSENT_MISMATCH')
    # The stored source is independently checked again by common submit before runs.
    return _generate(service, requestId, approved, binding, http=True)


def generate(service, requestId, promptHash, consentVersion):
    from .service import now
    approved = preview(service, requestId)
    if consentVersion != CONSENT_VERSION or promptHash != approved['promptHash']:
        raise ValueError('생성 문안 또는 설정이 변경되었습니다. 다시 확인하세요.')
    config = _configured(service)
    binding = GenerationBinding({'provider':'claude-cli', 'model':config.model,
                                 'maxBudgetUsd':config.budget_usd, 'timeoutSeconds':config.timeout_seconds},
                                lambda prompt, event: generate_candidates(config, prompt, event))
    return _generate(service, requestId, approved, binding)


def _generate(service, requestId, approved, binding, *, http=False):
    from .service import now
    promptHash, consentVersion = approved['promptHash'], approved['consentVersion']
    # A separate lock owns model work; never hold the engine lock during generation.
    with service.store.ai_generation_lock():
        with service.store.ai_request_gate(requestId):
            _check_transport(service, requestId, 'claude-api' if http else 'claude-cli')
            prior = ai_requests.optional(service.store, 'ai-generation', requestId)
            if http:
                _validate_http_generation(prior, approved)
            if prior and prior['status'] in ('failed', 'cancelled', 'interrupted'):
                return ai_requests.read(service, requestId)
            if prior and prior['promptHash'] != promptHash:
                raise ValueError('기존 생성 동의와 다릅니다. 새 요청을 만드세요.')
            job = ai_requests.optional(service.store, 'ai-request-job', 'ai-'+requestId)
            if job:
                return ai_requests.read(service, requestId)
            if cancelled(service, requestId):
                return ai_requests.read(service, requestId)
            event = threading.Event()
            event.control_error = None
            if prior is None:
                service.store.put('ai-generation', requestId, {
                    'id': requestId, 'status': 'generating', 'createdAt': now(),
                    'promptHash': promptHash, 'consentVersion': consentVersion,
                    **({'provider':'claude-api', 'preview':copy.deepcopy(approved),
                        'model':approved['model'], 'maxOutputTokens':approved['maxOutputTokens'],
                        'connectionId':approved['connectionId'], 'timeoutSeconds':approved['timeoutSeconds']} if http else
                       {'model':approved['model'], 'maxBudgetUsd':approved['maxBudgetUsd']}),
                    'providerAttempted': False, 'modelInvocationVerified': False})
            with service._mutex:
                service._active['generation-'+requestId] = event
        try:
            with _watch_cancel(service, requestId, event):
                if prior is None:
                    try:
                        if event.is_set() or cancelled(service, requestId):
                            raise ProviderError('PROVIDER_CANCELLED')
                        if binding is None:
                            raise ValueError('AI_CONNECTION_REQUIRED')
                        with service.store.ai_request_gate(requestId):
                            if event.is_set() or cancelled(service, requestId):
                                raise ProviderError('PROVIDER_CANCELLED')
                            service.store.put('ai-generation-attempt', requestId, {'id':requestId, 'providerAttempted':True})
                        candidates = validate_candidates(binding.send(approved['prompt'], event))
                    except Exception as error:
                        with service.store.ai_request_gate(requestId):
                            if event.control_error:
                                status, code = 'failed', event.control_error
                            else:
                                status = 'cancelled' if event.is_set() or cancelled(service, requestId) else 'failed'
                                code = str(error) if isinstance(error, ProviderError) and str(error) in PROVIDER_ERRORS else 'PROVIDER_INVALID_OUTPUT' if str(error) == 'PROVIDER_INVALID_OUTPUT' else 'PROVIDER_FAILED'
                            _finish(service, requestId, status, error=code)
                        return ai_requests.read(service, requestId)
                    with service.store.ai_request_gate(requestId):
                        if event.control_error:
                            _finish(service, requestId, 'failed', error=event.control_error)
                            return ai_requests.read(service, requestId)
                        if event.is_set() or cancelled(service, requestId):
                            _finish(service, requestId, 'cancelled')
                            return ai_requests.read(service, requestId)
                        _finish(service, requestId, 'ready', candidates=candidates,
                                candidateHash=digest(candidates), validatedOutputReceived=True)
                else:
                    candidates = prior['candidates']
                    if digest(candidates) != prior['candidateHash']:
                        raise ValueError('생성 후보 무결성 검사 실패')
                    candidates = validate_candidates(candidates)
                # submit atomically checks the persistent marker under the same gate.
                try:
                    return ai_requests.submit(service, requestId, candidates,
                                              _generation_event=event,
                                              _generation_origin='local-http-generated' if http else 'local-cli-generated')
                except ValueError as error:
                    if str(error) == 'Another local run is active':
                        return ai_requests.read(service, requestId)  # ready: retry without model
                    raise
        finally:
            with service._mutex:
                service._active.pop('generation-'+requestId, None)
