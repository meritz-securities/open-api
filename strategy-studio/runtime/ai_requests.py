"""Explicit external-host candidate submissions. No model, network or order calls."""
import copy
import threading
import uuid
from .storage import digest, canonical

ERRORS = {'validation': '후보 전략 규격 검증에 실패했습니다.', 'revision': '후보 리비전을 저장하지 못했습니다.',
          'execution': '후보 실행에 실패했습니다.', 'comparison': '후보 비교에 실패했습니다.',
          'source': '기준 실행·전략·데이터 무결성을 확인할 수 없습니다.', 'persistence': '후보 결과를 저장하지 못했습니다.'}


def optional(store, kind, ident):
    try:
        return store.get(kind, ident)
    except ValueError as error:
        if str(error) == 'Snapshot not found': return None
        raise


def source(service, request):
    run = service.get_run(request['runId'])
    revision = service.store.get('revision', run['revisionId'])
    data = service._dataset(run['datasetId'])
    if (run['status'] != 'succeeded' or run['revisionId'] != request['revisionId'] or
        run['datasetId'] != request['datasetId'] or digest(revision['definition']) != request['strategyHash'] or
        revision['hash'] != request['strategyHash'] or data['contentHash'] != request['dataHash'] or
        digest(run['settings']) != request['settingsHash'] or run['settings'] != run['manifest']['settings'] or run['manifest']['resultHash'] != request['resultHash'] or
        run['manifest']['strategyHash'] != request['strategyHash'] or run['manifest']['dataHash'] != request['dataHash']):
        raise ValueError(ERRORS['source'])
    return run, revision, data


def create(service, runId, purpose, requestId=None):
    from .service import now
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 2000:
        raise ValueError('개선 목적은 1~2000자입니다.')
    try: purpose.encode('utf-8')
    except UnicodeError: raise ValueError('개선 목적은 올바른 UTF-8이어야 합니다.') from None
    ident = uuid.uuid4().hex if requestId is None else requestId
    if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('requestId는 소문자 32자리 hex입니다.')
    run = service.get_run(runId)
    if run['status'] != 'succeeded': raise ValueError('완료된 기준 실행이 필요합니다.')
    request = {'id':ident,'runId':runId,'revisionId':run['revisionId'],'datasetId':run['datasetId'],
               'strategyHash':run['manifest']['strategyHash'],'dataHash':run['manifest']['dataHash'],
               'resultHash':run['manifest']['resultHash'],'settingsHash':digest(run['settings']),'purpose':purpose}
    source(service, request)
    with service.store.execution_lock():
        old = optional(service.store, 'ai-request', ident)
        if old:
            if {k:old[k] for k in request} != request: raise ValueError('requestId가 다른 요청에 이미 사용되었습니다.')
        else: service.store.put('ai-request',ident,{**request,'createdAt':now()})
    return read(service,ident)


def read(service, requestId):
    request = service.store.get('ai-request', requestId)
    job = optional(service.store, 'ai-request-job', 'ai-'+requestId)
    submission = optional(service.store, 'ai-submission', requestId)
    if submission is not None:
        try:
            valid = (job is not None and digest(submission['candidates']) ==
                     submission['submissionHash'] == job['submissionHash'])
        except (KeyError, TypeError, ValueError, RecursionError):
            valid = False
        if not valid:
            raise ValueError('제출 후보 snapshot 무결성 검사 실패')
    attempts = [value for i in range(3) if (value:=optional(service.store,'ai-attempt',requestId+'-'+str(i)))]
    from .ai_generation import record, cancelled, preview_record
    generation = record(service, requestId)
    return {'request':request,'status':job['status'] if job else generation['status'] if generation else 'cancelled' if cancelled(service,requestId) else 'awaiting_candidates','generation':generation,'generationPreview':preview_record(service,requestId),'cancellationRequested':cancelled(service,requestId),'job':job,'attempts':attempts,
            # These immutable checkpoints prove revision persistence, not an
            # execution outcome. Older development records copied 'failed'
            # from the initial attempt; project their actual checkpoint meaning
            # without rewriting the stored evidence.
            'prepared':[{**v, 'status':'prepared'} for i in range(3) if (v:=optional(service.store,'ai-prepared',requestId+'-'+str(i)))],
            'runs':service.store.ai_job_runs('ai-'+requestId),
            'submission':submission,
            'result':optional(service.store,'ai-result',requestId),
            'ai': ('local HTTP attempt; model invocation unverified' if generation.get('provider') == 'claude-api' else 'local CLI attempt; model invocation unverified') if generation else 'external host submission; model invocation unverified'}


def submit(service, requestId, candidates, *, _generation_event=None, _generation_origin='local-cli-generated'):
    from .service import now
    service.store.get('ai-request',requestId)
    if not isinstance(candidates,list) or not 1<=len(candidates)<=3 or any(not isinstance(c,dict) for c in candidates):
        raise ValueError('후보 정의는 1~3개 객체 배열입니다.')
    try:
        if len(canonical(candidates).encode('utf-8'))>240_000: raise ValueError()
        submitted = copy.deepcopy(candidates); fingerprint = digest(submitted)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise ValueError('후보는 크기가 제한된 유한 JSON이어야 합니다.') from None
    job_id = 'ai-'+requestId
    def previous():
        job=optional(service.store,'ai-request-job',job_id)
        if job:
            if job.get('submissionHash') not in (None,fingerprint): raise ValueError('이미 제출된 후보와 다릅니다. 새 요청을 만드세요.')
            return read(service,requestId)
    existing=previous()
    if existing: return existing
    with service.store.execution_lock():
        with service.store.ai_request_gate(requestId):
            existing=previous()
            if existing: return existing
            from .ai_generation import cancelled
            generation=optional(service.store,'ai-generation',requestId)
            preview=optional(service.store,'ai-generation-preview',requestId)
            if (generation is not None or preview is not None) and _generation_event is None:
                raise ValueError('로컬 생성이 소유한 요청입니다. 새 외부 요청을 만드세요.')
            if _generation_event is not None:
                expected_origin = 'local-http-generated' if generation and generation.get('provider') == 'claude-api' else 'local-cli-generated'
                if generation is None or _generation_origin != expected_origin:
                    raise ValueError('AI_GENERATION_PROVIDER_CONFLICT')
            if _generation_event is not None and getattr(_generation_event, 'control_error', None):
                raise ValueError('CANCEL_STATE_UNAVAILABLE')
            if cancelled(service,requestId) or (_generation_event is not None and _generation_event.is_set()):
                return read(service,requestId)
            request=service.store.get('ai-request',requestId)
            event=_generation_event if _generation_event is not None else threading.Event()
            job={'id':job_id,'runId':request['runId'],'method':'ai_candidates','status':'running','createdAt':now(),
                 'submissionHash':fingerprint,'candidateCount':len(submitted),'childRunIds':[]}
            service.store.put('ai-request-job',job_id,job)
            with service._mutex: service._active[job_id]=event
        attempts=[]; stage='source'
        def classify_control_failure():
            if job['status'] == 'cancelled' and getattr(event, 'control_error', None):
                job.update(status='failed', stage='control', error='CANCEL_STATE_UNAVAILABLE', outcome='control_failed')
        try:
            stage='persistence';service.store.put('ai-submission',requestId,{'candidates':submitted,'submissionHash':fingerprint,'origin':_generation_origin if _generation_event is not None else 'external-host-submitted','modelInvocationVerified':False})
            stage='source';baseline, original, data=source(service,request)
            for index,definition in enumerate(submitted):
                if event.is_set(): break
                attempt={'index':index,'definitionHash':digest(definition),'status':'failed','stage':'validation'}
                try:
                    stage='validation'
                    validation=service.validate_strategy(definition)
                    if not validation['valid']: raise ValueError(ERRORS[stage])
                    stage='revision';attempt['stage']=stage
                    revision=service.save_strategy(validation['definition'],original['id'])
                    attempt['revisionId']=revision['id']
                    # Durable preparation survives interruption before the engine result.
                    service.store.put('ai-prepared',requestId+'-'+str(index),{**copy.deepcopy(attempt), 'status':'prepared'})
                    stage='execution';attempt['stage']=stage
                    value=service._run_locked(revision,data,copy.deepcopy(baseline['settings']),baseline['id'],cancel=event,aiRequestId=requestId,aiJobId=job_id)
                    attempt.update(status='succeeded',runId=value['id'],metrics=value['report']['metrics'],dataHash=value['manifest']['dataHash'])
                    job['childRunIds'].append(value['id'])
                except Exception as error:
                    attempt.update(status='cancelled' if event.is_set() else 'failed',stage=stage,error=ERRORS[stage])
                    if getattr(error,'run_id',None):
                        attempt['runId']=error.run_id;job['childRunIds'].append(error.run_id)
                stage='persistence'
                service.store.put('ai-attempt',requestId+'-'+str(index),attempt);attempts.append(attempt)
            stage='comparison'
            successful=[a['runId'] for a in attempts if a['status']=='succeeded']
            comparison_skipped = bool(successful) and event.is_set()
            comparison=service.compare([baseline['id'],*successful]) if successful and not comparison_skipped else None
            cut_short = (len(attempts) < len(submitted) or
                         any(a['status'] == 'cancelled' for a in attempts) or comparison_skipped)
            # Decide once from work actually interrupted, before persisting the result.
            # A later watcher/user Event cannot undo completed attempts/comparison.
            job.update(status='cancelled' if cut_short else 'succeeded',
                       outcome='cancelled' if cut_short else 'completed_with_failures' if any(a['status']!='succeeded' for a in attempts) else 'completed')
            classify_control_failure()
            result={'requestId':requestId,'attempts':attempts,'comparison':comparison,'unattemptedCount':len(submitted)-len(attempts)}
            stage='persistence';service.store.put('ai-result',requestId,result)
        except Exception:
            cancelled_on_error = event.is_set()
            job.update(status='cancelled' if cancelled_on_error else 'failed',
                       outcome='cancelled' if cancelled_on_error else 'failed',stage=stage,error=ERRORS[stage])
            classify_control_failure()
        except BaseException:
            # A caught process-level interruption has already unwound the engine.
            for summary in service.store.ai_job_runs(job_id):
                if summary['status'] in ('queued','running'):
                    interrupted=service.store.get('run',summary['id'])
                    interrupted.update(status='interrupted',error='후보 실행이 중단되었습니다.')
                    service.store.finish_run(summary['id'],interrupted)
            job.update(status='interrupted',outcome='interrupted',stage=stage,error='후보 제출 처리가 중단되었습니다.')
            raise
        finally:
            try:
                job['finishedAt']=now();service.store.finish_run(job_id,job,kind='ai-request-job')
            finally:
                with service._mutex: service._active.pop(job_id,None)
    return read(service,requestId)


def cancel(service, requestId):
    from .service import now
    request=service.store.get('ai-request',requestId)
    with service.store.ai_request_gate(requestId):
        generation=optional(service.store,'ai-generation',requestId)
        if generation is not None:
            job=optional(service.store,'ai-request-job','ai-'+requestId)
            if job and job['status'] != 'running':
                return read(service,requestId)
            if generation['status'] in ('failed','cancelled','interrupted'):
                return read(service,requestId)
            service.store.put('ai-generation-cancel',requestId,{'requestId':requestId})
            with service._mutex:
                for key in ('generation-'+requestId, 'ai-'+requestId):
                    event=service._active.get(key)
                    if event is not None: event.set()
            if job is None and generation['status']=='ready':
                from .ai_generation import _finish
                _finish(service,requestId,'cancelled')
            return read(service,requestId)
        job_id='ai-'+requestId
        job=optional(service.store,'ai-request-job',job_id)
        if job:
            if job['status']=='running': service.cancel(jobId=job_id)
            return read(service,requestId)
        with service.store.execution_lock():
            job=optional(service.store,'ai-request-job',job_id)
            if job is None:
                service.store.put('ai-request-job',job_id,{'id':job_id,'runId':request['runId'],'status':'cancelled','createdAt':now(),'finishedAt':now(),'method':'ai_candidates','childRunIds':[]})
        return read(service,requestId)


def list_requests(service):
    from .ai_generation import record, cancelled, preview_record
    rows=[]
    for request in service.store.list('ai-request'):
        job=optional(service.store,'ai-request-job','ai-'+request['id'])
        generation=record(service,request['id'])
        rows.append({'request':request,
                     'status':job['status'] if job else generation['status'] if generation else 'cancelled' if cancelled(service,request['id']) else 'awaiting_candidates',
                     'outcome':job.get('outcome') if job else None, 'generation':generation,
                     'generationPreview':preview_record(service,request['id']),
                     'cancellationRequested':cancelled(service,request['id'])})
    return {'requests':rows,'scope':'latest 100 requests; not a complete history'}
