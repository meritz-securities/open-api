"""Stored research setup. Training and held-out phases share the existing engine.

Creation is storage-only. The held-out suffix is described and hashed but is not
materialized as an independently executable dataset before candidate freezing.
"""
from __future__ import annotations
import copy
import math
import threading
import uuid

from optimizer import chronological_split
from .storage import digest


def _optional(store, kind, ident):
    try:
        return store.get(kind, ident)
    except ValueError as error:
        if str(error) == 'Snapshot not found':
            return None
        raise


def _identifier(value):
    if not isinstance(value, str) or len(value) != 32 or any(char not in '0123456789abcdef' for char in value):
        raise ValueError('researchId must be lowercase 32-character hex')
    return value


def _versions():
    from . import VERSION
    from meritz.strategy_engine import ENGINE_VERSION
    return {'runtimeVersion': VERSION, 'engineVersion': ENGINE_VERSION}


def _source(service, source):
    from .service import settings_contract
    if type(source) is not dict:
        raise ValueError('research source must be an object')
    run = None
    if set(source) == {'runId'}:
        run = service.get_run(source['runId'])
        if run.get('status') != 'succeeded':
            raise ValueError('research requires a successful source run')
        revision = service.get_strategy(run['revisionId'])
        data = service._dataset(run['datasetId'])
        settings = settings_contract(run['settings'])
        manifest = run['manifest']
        if (manifest['strategyHash'] != revision['hash'] or manifest['dataHash'] != data['contentHash']
                or manifest['settings'] != settings or run['settings'] != settings):
            raise ValueError('research source run provenance mismatch')
        normalized = {'runId': run['id']}
        source_kind = 'completed_run'
    elif {'revisionId', 'datasetId'} <= set(source) <= {'revisionId', 'datasetId', 'settings'}:
        revision = service.get_strategy(source['revisionId'])
        data = service._dataset(source['datasetId'])
        settings = settings_contract(source.get('settings', {}))
        normalized = {'revisionId': revision['id'], 'datasetId': data['id'], 'settings': settings}
        source_kind = 'saved_strategy'
    else:
        raise ValueError('specify either runId or revisionId/datasetId/settings, not both')
    if revision['definition']['kind'] != 'dsl':
        raise ValueError('research separation currently supports DSL strategies only')
    binding = {'sourceKind': source_kind, 'source': normalized, 'revisionId': revision['id'],
               'datasetId': data['id'], 'settings': settings, 'settingsHash': digest(settings),
               'strategyHash': revision['hash'], 'revisionSnapshotHash': digest(revision),
               'dataHash': data['contentHash'], 'datasetSnapshotHash': digest(data)}
    if run is not None:
        binding.update(runId=run['id'], runSnapshotHash=digest(run), resultHash=run['manifest']['resultHash'])
    return binding, revision, data


def _partition(data, bars, label):
    source = {**data['source'], 'startDate': bars[0]['date'], 'endDate': bars[-1]['date'],
              'label': data['source']['label'][:160] + ' · ' + label}
    stable = {key: value for key, value in source.items() if key != 'fetchedAt'}
    return {'startDate': bars[0]['date'], 'endDate': bars[-1]['date'], 'barCount': len(bars),
            'barsHash': digest(bars), 'expectedContentHash': digest({'bars': bars, 'source': stable}),
            'source': source, 'qualityHash': digest(data.get('warnings', []))}


def _check_plan(service, research_id):
    ident = _identifier(research_id)
    try:
        plan = service.store.get('research-plan', ident)
    except ValueError as exc:
        # Only absence of the plan allows clients to choose a new session ID.
        # Missing bound snapshots below must retain their original failure.
        if str(exc) == 'Snapshot not found':
            raise ValueError('RESEARCH_NOT_FOUND') from exc
        raise
    if digest({key: value for key, value in plan.items() if key != 'planHash'}) != plan.get('planHash'):
        raise ValueError('research plan integrity check failed')
    binding, revision, data = _source(service, plan['binding']['source'])
    if binding != plan['binding']:
        raise ValueError('research source snapshot changed')
    train, heldout = chronological_split(data['bars'], plan['split']['trainFraction'])
    for key, bars, label in [('train', train, '학습 구간'), ('holdout', heldout, '검증 구간')]:
        expected = _partition(data, bars, label)
        actual = {k: v for k, v in plan['split'][key].items() if k not in ('datasetId', 'datasetSnapshotHash')}
        if actual != expected:
            raise ValueError('research partition binding changed')
    dataset = service._dataset(plan['split']['train']['datasetId'])
    if (digest(dataset) != plan['split']['train']['datasetSnapshotHash']
            or dataset['contentHash'] != plan['split']['train']['expectedContentHash']
            or digest(dataset['bars']) != plan['split']['train']['barsHash']):
        raise ValueError('research training snapshot changed')
    return plan, revision, data


def _view(plan):
    """Metadata only: no bars, full reports, provider configuration or credentials."""
    return {'session': copy.deepcopy(plan),
            'training': {'jobId': plan['trainingJobId'], 'jobStatus': None,
                         'baselineRunId': plan['trainingRunId'], 'baselineStatus': 'not_run',
                         'aiRequestId': plan['aiRequestId'], 'aiRequestStatus': None,
                         'attempts': [], 'cancellationRequested': False},
            'frozen': None,
            'evaluation': {'jobId': plan['evaluationJobId'], 'status': 'not_run',
                           'slots': [], 'result': None, 'cancellationRequested': False},
            'eligibility': {'canPrepareTraining': True, 'canFreeze': False, 'canEvaluate': False,
                            'canCancelTraining': True, 'canCancelEvaluation': False},
            'warnings': copy.deepcopy(plan['disclosures']['limitations'])}


def create(service, source, purpose, trainFraction=.7, researchId=None):
    from .service import now
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 2000:
        raise ValueError('research purpose must contain 1..2000 characters')
    try:
        purpose.encode('utf-8')
    except UnicodeError:
        raise ValueError('research purpose must be valid UTF-8') from None
    ident = uuid.uuid4().hex if researchId is None else _identifier(researchId)
    binding, revision, data = _source(service, source)
    # Existing split helper enforces finite fraction .2.. .8 and 4..5000 bars.
    train, heldout = chronological_split(data['bars'], trainFraction)
    request_hash = digest({'source': binding['source'], 'purpose': purpose, 'trainFraction': trainFraction})
    existing = _optional(service.store, 'research-plan', ident)
    if existing is not None:
        if existing.get('requestHash') != request_hash:
            raise ValueError('researchId already belongs to a different request')
        return get(service, ident)
    with service.store.execution_lock():
        existing = _optional(service.store, 'research-plan', ident)
        if existing is not None:
            if existing.get('requestHash') != request_hash:
                raise ValueError('researchId already belongs to a different request')
            return get(service, ident)
        # Re-read immutable source under the shared writer lock before binding it.
        checked, revision, data = _source(service, source)
        if checked != binding:
            raise ValueError('research source changed during creation')
        counts = service.store.research_history_counts(data['id'])
        train_info = _partition(data, train, '학습 구간')
        heldout_info = {**_partition(data, heldout, '검증 구간'), 'datasetId': None}
        training_job = 'research-training-' + ident
        evaluation_job = 'research-evaluation-' + ident
        saved = service._save_data(train, train_info['source'], data.get('warnings', []),
                                   derivedFrom=data['id'], validationJobId=training_job)
        saved_data = service._dataset(saved['id'])
        if saved_data['contentHash'] != train_info['expectedContentHash']:
            raise ValueError('research training partition hash mismatch')
        train_info.update(datasetId=saved_data['id'], datasetSnapshotHash=digest(saved_data))
        plan = {'id': ident, 'method': 'research_separation', 'createdAt': now(),
                'requestHash': request_hash, 'binding': binding, 'purpose': purpose,
                'revisionId': revision['id'], 'strategyName': revision['definition']['name'], 'datasetId': data['id'], 'versions': _versions(),
                'split': {'trainFraction': trainFraction, 'pivotIndex': len(train),
                          'train': train_info, 'holdout': heldout_info},
                'trainingJobId': training_job, 'trainingRunId': uuid.uuid4().hex,
                'evaluationJobId': evaluation_job, 'aiRequestId': uuid.uuid4().hex,
                'disclosures': {'strictBlindEvaluation': False,
                    'sourceWasCompletedRun': binding['sourceKind'] == 'completed_run',
                    'fullPeriodObserved': binding['sourceKind'] == 'completed_run' or counts['priorSuccessfulFullDatasetRunCount'] > 0,
                    **counts, 'humanExposureUnknown': True,
                    'purposeFreeTextMayEncodePriorKnowledge': True,
                    'counterScope': 'Lower bounds for this exact stored dataset ID; aliases and external exposure are not tracked.',
                    'limitations': [
                        '이미 수행한 전체 기간 분석과 사용자 작성 전략·목적에 검증 구간의 정보가 포함됐을 수 있습니다.',
                        '앱의 구간 분리는 사람이 이전에 자료를 보지 않았다는 보장이 아닙니다.',
                        '원본 데이터의 품질 경고는 학습 데이터에 복사되며 전체 기간의 날짜를 언급할 수 있습니다. 기존 AI 요청에는 이 경고 원문을 보내지 않습니다.',
                        '같은 자료에 대한 이전 실행·연구 횟수는 현재 데이터 ID의 기록에 한정한 최소치입니다.']}}
        if binding.get('runId'):
            plan['runId'] = binding['runId']
        plan['planHash'] = digest(plan)
        service.store.put('research-plan', ident, plan)
    return get(service, ident)


def get(service, researchId):
    plan, _, _ = _check_plan(service, researchId)
    return _project(service, plan)


def list_sessions(service, limit=100, source=None):
    plans = service.store.research_plans(limit, source)
    return {'sessions': [{'id': p['id'], 'createdAt': p['createdAt'], 'purpose': p['purpose'],
                          'source': p['binding']['source'], 'sourceKind': p['binding']['sourceKind'], 'strategyName': p['strategyName'],
                          'revisionId': p['revisionId'], 'datasetId': p['datasetId'],
                          'split': {key: {k: p['split'][key][k] for k in ('startDate', 'endDate', 'barCount')}
                                    for key in ('train', 'holdout')},
                          'disclosures': p['disclosures']} for p in plans]}


PHASE_TIMEOUT_SECONDS = 120
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


class _Stopped(BaseException):
    pass


def _record(service, kind, ident):
    value = _optional(service.store, kind, ident)
    if value is not None and digest({k: v for k, v in value.items() if k != 'recordHash'}) != value.get('recordHash'):
        raise ValueError('research record integrity check failed')
    return value


def _save_record(service, kind, ident, value):
    return service.store.put(kind, ident, {**value, 'recordHash': digest(value)})


def _cancelled(service, plan, phase):
    return _record(service, 'research-cancel', plan['id'] + '-' + phase) is not None


def _current(plan):
    if plan['versions'] != _versions():
        raise ValueError('RESEARCH_VERSION_CHANGED')


def _baseline_slot(plan):
    return {'index': 0, 'role': 'baseline', 'revisionId': plan['revisionId'], 'runId': plan['trainingRunId']}


def _run_for_slot(service, plan, phase, slot, dataset_id):
    value = _optional(service.store, 'run', slot['runId'])
    if value is None:
        return None
    value = service.get_run(slot['runId'])
    job_id = plan['trainingJobId'] if phase == 'training' else plan['evaluationJobId']
    if (value.get('researchId') != plan['id'] or value.get('researchPhase') != phase or
            value.get('researchSlot') != slot['index'] or value.get('validationJobId') != job_id or
            value.get('revisionId') != slot['revisionId'] or value.get('datasetId') != dataset_id or
            value.get('settings') != plan['binding']['settings']):
        raise ValueError('research run slot ownership mismatch')
    if value.get('status') == 'succeeded':
        revision = service.get_strategy(slot['revisionId']); data = service._dataset(dataset_id)
        if (value['manifest']['strategyHash'] != revision['hash'] or
                value['manifest']['dataHash'] != data['contentHash'] or
                value['manifest']['settings'] != plan['binding']['settings'] or
                any(value['manifest'].get(key) != expected for key, expected in plan['versions'].items())):
            raise ValueError('research run provenance mismatch')
    return value


def _expected_request(plan, baseline):
    return {'id': plan['aiRequestId'], 'runId': baseline['id'], 'revisionId': baseline['revisionId'],
            'datasetId': baseline['datasetId'], 'strategyHash': baseline['manifest']['strategyHash'],
            'dataHash': baseline['manifest']['dataHash'], 'resultHash': baseline['manifest']['resultHash'],
            'settingsHash': digest(baseline['settings']), 'purpose': plan['purpose']}


def _owned_request(service, plan, baseline):
    if baseline is None or baseline.get('status') != 'succeeded':
        return None
    request = _optional(service.store, 'ai-request', plan['aiRequestId'])
    if request is None:
        return None
    expected = _expected_request(plan, baseline)
    if any(request.get(key) != value for key, value in expected.items()):
        raise ValueError('TRAINING_REQUEST_CONFLICT')
    from .ai_requests import source
    source(service, request)
    return request


def _binding(service, plan, baseline):
    record = _record(service, 'research-training', plan['id'])
    if record is not None:
        if (baseline is None or baseline.get('status') != 'succeeded' or record['planHash'] != plan['planHash']
                or record['baselineSnapshotHash'] != digest(baseline) or record['requestId'] != plan['aiRequestId']):
            raise ValueError('research training binding changed')
        if _owned_request(service, plan, baseline) is None:
            raise ValueError('research training request missing')
    return record


def _ready_baseline(service, plan):
    baseline = _run_for_slot(service, plan, 'training', _baseline_slot(plan), plan['split']['train']['datasetId'])
    if baseline is None or baseline.get('status') != 'succeeded' or _binding(service, plan, baseline) is None:
        raise ValueError('TRAINING_BASELINE_NOT_READY')
    if _record(service, 'research-training-error', plan['id']) is not None:
        raise ValueError('TRAINING_BINDING_FAILED')
    _owned_request(service, plan, baseline)
    return baseline


def _pool(service, plan, original_only=False):
    baseline = _ready_baseline(service, plan)
    request = service.get_ai_request(plan['aiRequestId'])
    job = request['job']; submission = request['submission']; attempts = request['attempts']
    if original_only:
        # 후보 0건 확정: 원본 전략만으로 풀을 만든다. 제출·생성이 하나도 시작되지 않은 요청에서만 허용한다.
        # (AI 미연결 고객 앱에서 학습 구간 원본 실행만으로 검증 단계로 넘어가는 경로.)
        if not _original_only_allowed(request):
            raise ValueError('TRAINING_CANDIDATES_NOT_COMPLETE')
        return _pool_rows(service, plan, [baseline]), None
    if (job is None or job.get('status') != 'succeeded' or job.get('outcome') != 'completed' or
            submission is None or not 1 <= len(submission['candidates']) <= 3 or
            len(attempts) != len(submission['candidates']) or any(a.get('status') != 'succeeded' for a in attempts)):
        raise ValueError('TRAINING_CANDIDATES_NOT_COMPLETE')
    runs = [baseline]
    for index, (definition, attempt) in enumerate(zip(submission['candidates'], attempts)):
        if attempt.get('index') != index or attempt.get('definitionHash') != digest(definition):
            raise ValueError('research candidate submission mismatch')
        revision = service.get_strategy(attempt['revisionId'])
        from .dsl import validate_definition
        if revision['definition'] != validate_definition(definition):
            raise ValueError('research candidate definition mismatch')
        run = service.get_run(attempt['runId'])
        if (run.get('status') != 'succeeded' or run.get('revisionId') != revision['id'] or
                run.get('aiRequestId') != plan['aiRequestId'] or run.get('aiJobId') != 'ai-' + plan['aiRequestId'] or
                run.get('parentRunId') != baseline['id'] or run['datasetId'] != baseline['datasetId'] or
                run['settings'] != baseline['settings'] or run['manifest']['settings'] != baseline['settings'] or
                run['manifest']['strategyHash'] != revision['hash'] or
                run['manifest']['dataHash'] != baseline['manifest']['dataHash'] or
                attempt.get('dataHash') != run['manifest']['dataHash'] or attempt.get('metrics') != run['report']['metrics'] or
                any(run['manifest'].get(key) != expected for key, expected in plan['versions'].items())):
            raise ValueError('research training candidate provenance mismatch')
        runs.append(run)
    return _pool_rows(service, plan, runs), submission['submissionHash']


def _original_only_allowed(request):
    """제출 후보 0건: 외부 제출·로컬 생성·취소가 전혀 없는 '후보 대기' 상태여야 한다."""
    return (request['job'] is None and request['submission'] is None and request['generation'] is None
            and not request['attempts'] and request['status'] == 'awaiting_candidates')


def _pool_rows(service, plan, runs):
    pool = []
    for index, run in enumerate(runs):
        score = run['report']['metrics']['totalReturnPct']
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError('research requires finite training returns')
        revision = service.get_strategy(run['revisionId'])
        pool.append({'index': index, 'role': 'baseline' if index == 0 else 'candidate',
                     'name': revision['definition']['name'], 'revisionId': revision['id'],
                     'trainingRunId': run['id'], 'strategyHash': revision['hash'],
                     'revisionSnapshotHash': digest(revision), 'trainingSnapshotHash': digest(run),
                     'resultHash': run['manifest']['resultHash'], 'metrics': run['report']['metrics']})
    return pool


def _pool_mode(service, plan):
    """확정 전 풀 판정: ('candidates', pool, hash) | ('original', pool, None). 둘 다 불가하면 ValueError."""
    try:
        pool, submission_hash = _pool(service, plan)
        return 'candidates', pool, submission_hash
    except ValueError as error:
        if str(error) != 'TRAINING_CANDIDATES_NOT_COMPLETE':
            raise
    pool, _ = _pool(service, plan, original_only=True)
    return 'original', pool, None


def _frozen(service, plan):
    frozen = _record(service, 'research-freeze', plan['id'])
    if frozen is None:
        return None
    if frozen.get('candidateCount') == 0:
        # 후보 0건(원본만) 확정: 이후 같은 AI 요청에 후보가 제출되더라도 확정 풀은 원본 하나로 고정된다.
        pool, submission_hash = _pool_rows(service, plan, [_ready_baseline(service, plan)]), None
    else:
        pool, submission_hash = _pool(service, plan)
    if frozen['planHash'] != plan['planHash'] or frozen['submissionHash'] != submission_hash:
        raise ValueError('research freeze binding changed')
    for actual, expected in zip(frozen['candidates'], pool):
        if any(actual.get(key) != value for key, value in expected.items()):
            raise ValueError('research frozen training pool changed')
    if len(pool) != len(frozen['candidates']):
        raise ValueError('research frozen pool size changed')
    winner = max(pool, key=lambda item: item['metrics']['totalReturnPct'])['revisionId']
    if frozen['selectedRevisionId'] != winner:
        raise ValueError('research frozen selection changed')
    if 'candidateCount' in frozen and (frozen['candidateCount'] != len(pool) - 1 or
            frozen.get('selection') != ('original' if frozen['selectedRevisionId'] == plan['revisionId'] else 'candidate')):
        raise ValueError('research freeze summary changed')
    eligible = []
    for candidate in frozen['candidates']:
        selected = candidate['revisionId'] == winner
        allowed = candidate['index'] == 0 or selected
        if candidate.get('selected') is not selected or candidate.get('evaluationEligible') is not allowed:
            raise ValueError('research frozen eligibility changed')
        if allowed:
            eligible.append(candidate)
    slots = frozen['evaluationSlots']
    if not isinstance(slots, list) or len(slots) != len(eligible) or not 1 <= len(slots) <= 2:
        raise ValueError('research heldout slot count changed')
    seen = set()
    for index, (slot, candidate) in enumerate(zip(slots, eligible)):
        _identifier(slot.get('runId'))
        if (slot['runId'] in seen or slot['runId'] == plan['trainingRunId'] or slot.get('index') != index or
                slot.get('revisionId') != candidate['revisionId'] or slot.get('trainingRunId') != candidate['trainingRunId'] or
                slot.get('role') != ('baseline' if index == 0 else 'selected')):
            raise ValueError('research heldout slot binding changed')
        seen.add(slot['runId'])
    return frozen


def _evaluation_data(service, plan):
    value = _record(service, 'research-evaluation-data', plan['id'])
    if value is None:
        return None
    data = service._dataset(value['datasetId'])
    if (value['planHash'] != plan['planHash'] or digest(data) != value['datasetSnapshotHash'] or
            data['contentHash'] != plan['split']['holdout']['expectedContentHash']):
        raise ValueError('research heldout data changed')
    return data


def validate_reserved_execution(service, run_id, research_id, phase, index, revision, data, settings, job_id):
    _identifier(run_id)
    plan, _, _ = _check_plan(service, research_id); _current(plan)
    if type(index) is not int or phase not in ('training', 'evaluation'):
        raise ValueError('invalid research execution slot')
    if phase == 'training':
        slots = [_baseline_slot(plan)]; expected_data = plan['split']['train']['datasetId']; expected_job = plan['trainingJobId']
    else:
        frozen = _frozen(service, plan); bound_data = _evaluation_data(service, plan)
        if frozen is None or bound_data is None:
            raise ValueError('research evaluation is not prepared')
        slots = frozen['evaluationSlots']; expected_data = bound_data['id']; expected_job = plan['evaluationJobId']
    if (not 0 <= index < len(slots) or slots[index]['runId'] != run_id or
            slots[index]['revisionId'] != revision['id'] or data['id'] != expected_data or
            job_id != expected_job or settings != plan['binding']['settings']):
        raise ValueError('reserved research execution does not match owner plan')
    if _optional(service.store, 'run', run_id) is not None:
        raise ValueError('reserved research run ID is already occupied')


def _slot_projection(service, plan, phase, slot, dataset_id, job):
    run = _run_for_slot(service, plan, phase, slot, dataset_id) if dataset_id else None
    value = {**slot, 'status': run['status'] if run else 'not_run', 'metrics': None, 'resultHash': None, 'error': None}
    if run:
        if run['status'] == 'succeeded':
            value.update(metrics=run['report']['metrics'], resultHash=run['manifest']['resultHash'])
        else:
            value['error'] = run.get('error')
    else:
        ident = f"{plan['id']}-{phase}-{slot['index']}"
        outcome = _record(service, 'research-slot-outcome', ident)
        started = _record(service, 'research-slot-start', ident)
        if outcome:
            value.update(status=outcome['status'], error=outcome.get('error'))
        elif started and job and job.get('status') in TERMINAL:
            value.update(status='interrupted', error='실행 기록 생성 전에 처리가 중단되었습니다.')
    return value


def _claim_phase(service, plan, phase, slots):
    from .service import now
    job_id = plan['trainingJobId'] if phase == 'training' else plan['evaluationJobId']
    if _optional(service.store, 'validation-job', job_id) is not None:
        return None
    for slot in slots:
        if _optional(service.store, 'run', slot['runId']) is not None:
            raise ValueError('reserved research run ID is already occupied')
    job = {'id': job_id, 'method': 'research_' + phase, 'status': 'running', 'createdAt': now(),
           'datasetId': plan['datasetId'], 'options': {'researchId': plan['id'], 'phase': phase},
           'childRunIds': [], 'reservedRunIds': [slot['runId'] for slot in slots]}
    if phase == 'evaluation':
        job.update(holdoutAccessAttempted=True, historyAtEvaluation=service.store.research_history_counts(plan['datasetId']))
    outcome = service.store._research_decision(plan['id'], 'claim_' + phase, job)
    if outcome['status'] == 'blocked':
        raise ValueError('RESEARCH_CANCELLED')
    return job if outcome['status'] == 'committed' else None


def _commit_evaluation_result(service, plan, slots, data, job, status, error=None):
    """Atomically order aggregate completion against persistent cancellation."""
    from .service import now
    frozen = _frozen(service, plan)
    dataset_id = data['id'] if data else None
    rows = [_slot_projection(service, plan, 'evaluation', slot, dataset_id, job) for slot in slots]
    value = {'researchId': plan['id'], 'planHash': plan['planHash'], 'freezeHash': frozen['recordHash'],
             'selectedRevisionId': frozen['selectedRevisionId'], 'datasetId': dataset_id,
             'status': status, 'slots': rows, 'historyAtEvaluation': job['historyAtEvaluation'],
             'assumptions': ['검증 구간에서는 학습 단계에서 확정한 전략과 원본만 평가합니다. 검증 결과로 다시 순위를 고르지 않습니다.',
                             '각 계정은 초기자본·전략 상태·지표 준비 구간을 독립적으로 다시 시작합니다.'],
             'createdAt': now(), 'cancellationWon': False}
    if error:
        value['error'] = error
    outcome = service.store._research_decision(plan['id'], 'result', {**value, 'recordHash': digest(value)})
    result = outcome['value']
    job['status'] = 'cancelled' if result['status'] == 'cancelled' else 'succeeded' if result['status'] == 'succeeded' else 'failed'
    if result.get('error'):
        job['error'] = result['error']
    return result


def _forward_training_cancel(service, plan, baseline):
    try:
        owned = _owned_request(service, plan, baseline)
    except ValueError:
        owned = None
    if owned is not None:
        try:
            service.cancel_ai_request(plan['aiRequestId'])
            return True
        except ValueError:
            # Busy engine or another process's Event: intent remains persistent.
            pass
    return False


def _execute_phase(service, plan, phase, slots, data, job=None):
    """Caller owns execution_lock. Claim and every reserved slot are one-attempt."""
    from .service import now
    job = _claim_phase(service, plan, phase, slots) if job is None else job
    if job is None:
        return
    job_id = job['id']
    event = threading.Event(); expired = threading.Event()
    with service._mutex:
        service._active[job_id] = event
    def timeout():
        expired.set(); event.set()
    timer = threading.Timer(PHASE_TIMEOUT_SECONDS, timeout); timer.daemon = True; timer.start()
    def checkpoint():
        if _cancelled(service, plan, phase):
            event.set()
        if event.is_set():
            raise _Stopped()
    try:
        for slot in slots:
            checkpoint()
            ident = f"{plan['id']}-{phase}-{slot['index']}"
            _save_record(service, 'research-slot-start', ident, {'researchId': plan['id'], 'phase': phase,
                         'slot': slot['index'], 'runId': slot['runId'], 'createdAt': now()})
            try:
                revision = service.get_strategy(slot['revisionId'])
                value = service._run_locked(revision, data, copy.deepcopy(plan['binding']['settings']),
                    parentRunId=plan['binding'].get('runId') if phase == 'training' else plan['trainingRunId'],
                    cancel=event, validationJobId=job_id, runId=slot['runId'], researchId=plan['id'],
                    researchPhase=phase, researchSlot=slot['index'])
                job['childRunIds'].append(value['id'])
                outcome = {'status': 'succeeded', 'runId': value['id']}
            except Exception:
                child = _optional(service.store, 'run', slot['runId'])
                if child:
                    job['childRunIds'].append(child['id'])
                outcome = {'status': 'cancelled' if event.is_set() else 'failed', 'runId': slot['runId'] if child else None,
                           'error': '연구 후보 실행에 실패했습니다.'}
            _save_record(service, 'research-slot-outcome', ident, {'researchId': plan['id'], 'phase': phase,
                         'slot': slot['index'], **outcome})
            if slot is not slots[-1] or outcome['status'] != 'succeeded':
                checkpoint()
        rows = [_slot_projection(service, plan, phase, slot, data['id'], job) for slot in slots]
        success = all(row['status'] == 'succeeded' for row in rows)
        if phase == 'evaluation':
            _commit_evaluation_result(service, plan, slots, data, job, 'succeeded' if success else 'completed_with_failures')
        else:
            job.update(status='succeeded' if success else 'failed')
    except _Stopped:
        status = 'failed' if expired.is_set() else 'cancelled'
        error = '연구 단계 시간 한도를 넘었습니다.' if expired.is_set() else '연구 단계가 취소되었습니다.'
        job.update(status=status, error=error)
        if phase == 'evaluation':
            _commit_evaluation_result(service, plan, slots, data, job, status, error)
    except Exception:
        job.update(status='failed', error='연구 단계 기록 또는 무결성을 확인할 수 없습니다.')
    except BaseException:
        # Abrupt process death cannot guarantee an aggregate result. Existing
        # store-start recovery and exact slot records remain the read-only fallback.
        raise
    finally:
        timer.cancel()
        try:
            if job['status'] != 'running':
                job['finishedAt'] = now()
                service.store.finish_run(job_id, job, kind='validation-job')
        finally:
            with service._mutex:
                service._active.pop(job_id, None)


def prepare_training(service, researchId):
    plan, _, _ = _check_plan(service, researchId); _current(plan)
    if _cancelled(service, plan, 'training'):
        return get(service, researchId)
    existing_job = _optional(service.store, 'validation-job', plan['trainingJobId'])
    if existing_job is not None and existing_job.get('status') == 'running':
        # A saved child is not proof that its owner has released the engine lock.
        return get(service, researchId)
    if existing_job is None:
        with service.store.execution_lock():
            plan, _, _ = _check_plan(service, researchId); _current(plan)
            if not _cancelled(service, plan, 'training'):
                _execute_phase(service, plan, 'training', [_baseline_slot(plan)], service._dataset(plan['split']['train']['datasetId']))
    # Existing AI creation takes execution_lock itself: never nest it, and the
    # normal training job must already be finalized before reaching this point.
    baseline = _run_for_slot(service, plan, 'training', _baseline_slot(plan), plan['split']['train']['datasetId'])
    if (baseline is None or baseline.get('status') != 'succeeded' or _cancelled(service, plan, 'training') or
            _record(service, 'research-training-error', plan['id']) is not None):
        return get(service, researchId)
    if _binding(service, plan, baseline) is None:
        try:
            _owned_request(service, plan, baseline)
        except ValueError:
            _save_record(service, 'research-training-error', plan['id'], {'researchId': plan['id'],
                         'planHash': plan['planHash'], 'error': 'TRAINING_REQUEST_CONFLICT'})
            return get(service, researchId)
        try:
            service.create_ai_request(baseline['id'], plan['purpose'], requestId=plan['aiRequestId'])
        except ValueError:
            # A race may occupy the disclosed ID between the preflight and create.
            try:
                _owned_request(service, plan, baseline)
            except ValueError:
                _save_record(service, 'research-training-error', plan['id'], {'researchId': plan['id'],
                             'planHash': plan['planHash'], 'error': 'TRAINING_REQUEST_CONFLICT'})
                return get(service, researchId)
            raise
        if _cancelled(service, plan, 'training'):
            forwarded = _forward_training_cancel(service, plan, baseline)
            value = get(service, researchId)
            value['cancellation'] = {'phase': 'training', 'requested': True, 'forwarded': forwarded,
                                     'effective': True, 'reason': 'CANCELLATION_REQUESTED'}
            return value
        _save_record(service, 'research-training', plan['id'], {'researchId': plan['id'],
                     'planHash': plan['planHash'], 'baselineSnapshotHash': digest(baseline),
                     'requestId': plan['aiRequestId']})
    return get(service, researchId)


def freeze(service, researchId):
    from .service import now
    plan, _, _ = _check_plan(service, researchId)
    if _frozen(service, plan) is not None:
        return get(service, researchId)
    _current(plan)
    with service.store.execution_lock():
        plan, _, _ = _check_plan(service, researchId); _current(plan)
        if _frozen(service, plan) is not None:
            return get(service, researchId)
        if _cancelled(service, plan, 'training'):
            raise ValueError('TRAINING_CANCELLED')
        mode, pool, submission_hash = _pool_mode(service, plan)
        winner = max(pool, key=lambda candidate: candidate['metrics']['totalReturnPct'])
        selected = winner['revisionId']
        for candidate in pool:
            candidate.update(selected=candidate['revisionId'] == selected,
                             evaluationEligible=candidate['index'] == 0 or candidate['revisionId'] == selected)
        slots = [{'index': index, 'role': 'baseline' if item['index'] == 0 else 'selected',
                  'revisionId': item['revisionId'], 'trainingRunId': item['trainingRunId'], 'runId': uuid.uuid4().hex}
                 for index, item in enumerate(item for item in pool if item['evaluationEligible'])]
        value = {'researchId': plan['id'], 'planHash': plan['planHash'], 'createdAt': now(),
                 'aiRequestId': plan['aiRequestId'], 'submissionHash': submission_hash,
                 'selectionMetric': 'totalReturnPct', 'tieBreak': 'baseline then submitted order',
                 'selectedRevisionId': selected, 'candidates': pool, 'evaluationSlots': slots,
                 # candidateCount=0 + selection='original' 이면 AI 후보 없이 원본만 확정한 것이다(UI 판단 필드).
                 'candidateCount': len(pool) - 1, 'selection': 'original' if selected == plan['revisionId'] else 'candidate',
                 'freezeMode': mode,
                 'otherAiRequestsOnTrainingRunCount': service.store.other_ai_requests_on_run(plan['trainingRunId'], plan['aiRequestId'])}
        outcome = service.store._research_decision(plan['id'], 'freeze', {**value, 'recordHash': digest(value)})
        if outcome['status'] == 'blocked':
            raise ValueError('TRAINING_CANCELLED')
    return get(service, researchId)


def evaluate(service, researchId):
    plan, _, _ = _check_plan(service, researchId)
    frozen = _frozen(service, plan)
    if frozen is None:
        raise ValueError('POOL_NOT_FROZEN')
    if _optional(service.store, 'validation-job', plan['evaluationJobId']) is not None:
        return get(service, researchId)
    _current(plan)
    if _cancelled(service, plan, 'evaluation'):
        raise ValueError('RESEARCH_CANCELLED')
    with service.store.execution_lock():
        plan, _, source_data = _check_plan(service, researchId); _current(plan)
        frozen = _frozen(service, plan)
        if _optional(service.store, 'validation-job', plan['evaluationJobId']) is not None:
            return get(service, researchId)
        if _cancelled(service, plan, 'evaluation'):
            raise ValueError('RESEARCH_CANCELLED')
        for slot in frozen['evaluationSlots']:
            if _optional(service.store, 'run', slot['runId']) is not None:
                raise ValueError('reserved research run ID is already occupied')
        job = _claim_phase(service, plan, 'evaluation', frozen['evaluationSlots'])
        if job is None:
            return get(service, researchId)
        try:
            if _cancelled(service, plan, 'evaluation'):
                from .service import now
                _commit_evaluation_result(service, plan, frozen['evaluationSlots'], None, job,
                                          'cancelled', '검증 실행 전에 취소되었습니다.')
                job['finishedAt'] = now()
                service.store.finish_run(job['id'], job, kind='validation-job')
                return get(service, researchId)
            data = _evaluation_data(service, plan)
            if data is None:
                _, suffix = chronological_split(source_data['bars'], plan['split']['trainFraction'])
                saved = service._save_data(suffix, plan['split']['holdout']['source'], source_data.get('warnings', []),
                                           derivedFrom=plan['datasetId'], validationJobId=plan['evaluationJobId'])
                data = service._dataset(saved['id'])
                if data['contentHash'] != plan['split']['holdout']['expectedContentHash']:
                    raise ValueError('research heldout materialization mismatch')
                _save_record(service, 'research-evaluation-data', plan['id'], {'researchId': plan['id'],
                             'planHash': plan['planHash'], 'datasetId': data['id'], 'datasetSnapshotHash': digest(data)})
            _execute_phase(service, plan, 'evaluation', frozen['evaluationSlots'], data, job=job)
        except Exception:
            current = service.store.get('validation-job', job['id'])
            if current['status'] == 'running':
                from .service import now
                current.update(status='failed', finishedAt=now(), error='검증 데이터 또는 실행 준비에 실패했습니다.')
                service.store.finish_run(job['id'], current, kind='validation-job')
            raise
    return get(service, researchId)


def cancel(service, researchId, phase):
    from .service import now
    if phase not in ('training', 'evaluation'):
        raise ValueError('research cancellation phase must be training or evaluation')
    plan, _, _ = _check_plan(service, researchId)
    marker = {'researchId': plan['id'], 'phase': phase, 'createdAt': now()}
    decision = service.store._research_decision(plan['id'], 'cancel_' + phase, {**marker, 'recordHash': digest(marker)})
    if decision['status'] == 'blocked':
        value = get(service, researchId)
        value['cancellation'] = {'phase': phase, 'requested': False, 'forwarded': False,
                                 'effective': False, 'reason': 'POOL_NOT_FROZEN' if decision.get('guard') == 'pool-not-frozen' else 'ALREADY_FROZEN' if phase == 'training' else 'EVALUATION_FINISHED'}
        return value
    job_id = plan['trainingJobId'] if phase == 'training' else plan['evaluationJobId']
    forwarded = False
    with service._mutex:
        event = service._active.get(job_id)
        if event is not None:
            event.set(); forwarded = True
    if phase == 'training':
        baseline = _run_for_slot(service, plan, phase, _baseline_slot(plan), plan['split']['train']['datasetId'])
        forwarded = _forward_training_cancel(service, plan, baseline) or forwarded
    value = get(service, researchId)
    value['cancellation'] = {'phase': phase, 'requested': True, 'forwarded': forwarded,
                             'effective': True, 'reason': 'CANCELLATION_REQUESTED'}
    return value


def _project(service, plan):
    value = _view(plan)
    training_job = _optional(service.store, 'validation-job', plan['trainingJobId'])
    baseline = _run_for_slot(service, plan, 'training', _baseline_slot(plan), plan['split']['train']['datasetId'])
    binding_error = _record(service, 'research-training-error', plan['id'])
    try:
        owned = _owned_request(service, plan, baseline)
    except ValueError:
        owned = None
    binding = _binding(service, plan, baseline) if binding_error is None and owned is not None else None
    request = service.get_ai_request(plan['aiRequestId']) if owned is not None else None
    training_cancel = _cancelled(service, plan, 'training')
    frozen = _frozen(service, plan)
    evaluation_job = _optional(service.store, 'validation-job', plan['evaluationJobId'])
    evaluation_data = _evaluation_data(service, plan)
    result = _record(service, 'research-result', plan['id'])
    evaluation_cancel = _cancelled(service, plan, 'evaluation')
    value['training'].update(jobStatus=training_job['status'] if training_job else None,
        baselineStatus=baseline['status'] if baseline else 'not_run', baselineMetrics=baseline['report']['metrics'] if baseline and baseline['status'] == 'succeeded' else None,
        aiRequestStatus=request['status'] if request else None, attempts=request['attempts'] if request else [],
        bindingStatus='training_binding_failed' if binding_error else 'bound' if binding else 'not_bound',
        bindingError=binding_error['error'] if binding_error else None, cancellationRequested=training_cancel)
    value['frozen'] = {**frozen, 'freezeHash': frozen['recordHash']} if frozen else None
    if evaluation_data:
        value['session']['split']['holdout']['datasetId'] = evaluation_data['id']
    slots = [_slot_projection(service, plan, 'evaluation', slot, evaluation_data['id'] if evaluation_data else None, evaluation_job)
             for slot in frozen['evaluationSlots']] if frozen else []
    if result:
        if (frozen is None or result['freezeHash'] != frozen['recordHash'] or result['planHash'] != plan['planHash']
                or result['slots'] != slots):
            raise ValueError('research evaluation result binding changed')
    value['session']['disclosures']['holdoutAccessAttempted'] = bool(evaluation_job)
    value['evaluation'].update(jobStatus=evaluation_job['status'] if evaluation_job else None,
        status=result['status'] if result else evaluation_job['status'] if evaluation_job else 'not_run',
        datasetId=evaluation_data['id'] if evaluation_data else None, slots=slots, result=result,
        cancellationRequested=evaluation_cancel)
    version_changed = plan['versions'] != _versions()
    reasons = {'prepareTraining': [], 'freeze': [], 'evaluate': [], 'cancelTraining': [], 'cancelEvaluation': []}
    if version_changed:
        for key in ('prepareTraining', 'freeze', 'evaluate'): reasons[key].append('RESEARCH_VERSION_CHANGED')
    if training_cancel:
        reasons['prepareTraining'].append('TRAINING_CANCELLED')
        if frozen is None: reasons['freeze'].append('TRAINING_CANCELLED')
    if binding_error:
        reasons['prepareTraining'].append('TRAINING_BINDING_FAILED'); reasons['freeze'].append('TRAINING_BINDING_FAILED')
    if binding:
        reasons['prepareTraining'].append('TRAINING_ALREADY_PREPARED')
    elif training_job and training_job.get('status') == 'running':
        reasons['prepareTraining'].append('TRAINING_IN_PROGRESS')
    elif training_job and (baseline is None or baseline.get('status') != 'succeeded'):
        reasons['prepareTraining'].append('TRAINING_ATTEMPT_ALREADY_CLAIMED')
    freeze_mode = None
    if frozen:
        reasons['freeze'].append('POOL_ALREADY_FROZEN'); reasons['cancelTraining'].append('POOL_ALREADY_FROZEN')
    else:
        try: freeze_mode = _pool_mode(service, plan)[0]
        except ValueError: reasons['freeze'].append('TRAINING_CANDIDATES_NOT_COMPLETE')
        reasons['evaluate'].append('POOL_NOT_FROZEN'); reasons['cancelEvaluation'].append('POOL_NOT_FROZEN')
    if evaluation_cancel: reasons['evaluate'].append('EVALUATION_CANCELLED')
    if evaluation_job: reasons['evaluate'].append('EVALUATION_ATTEMPT_ALREADY_CLAIMED')
    if result or (evaluation_job and evaluation_job['status'] in TERMINAL): reasons['cancelEvaluation'].append('EVALUATION_FINISHED')
    submitted = len(request['submission']['candidates']) if request and request.get('submission') else 0
    value['training']['submittedCandidateCount'] = submitted
    value['eligibility'] = {'canPrepareTraining': not reasons['prepareTraining'], 'canFreeze': not reasons['freeze'],
                           'canEvaluate': not reasons['evaluate'], 'canCancelTraining': not reasons['cancelTraining'],
                           'canCancelEvaluation': not reasons['cancelEvaluation'], 'reasons': reasons,
                           # 'original': 후보 0건으로 원본만 확정 가능 / 'candidates': 제출 후보 포함 확정 가능 / None: 확정 불가·이미 확정
                           'freezeMode': freeze_mode if not reasons['freeze'] else None}
    return value
