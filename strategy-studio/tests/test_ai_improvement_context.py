"""Doctor context is recomputed locally and strictly minimized before fake HTTP."""
import copy
import json

import pytest

from analytics import analyze
from runtime import ai_generation, ai_improvement_context as context
from runtime.service import StrategyRuntime
from runtime.storage import canonical, digest, encode_body
from test_ai_http_improvement import system, preview, invoke
from test_ai_generation import fake


def doctor(system):
    service,run,_,_,_=system
    data=service._dataset(run['datasetId'])
    return analyze(run['report'],run['manifest'],bars=data['bars'],settings=run['settings'])


def test_independent_doctor_expectations_and_wire(system):
    service,baseline,ident,_,state=system
    p=preview(system);payload=json.loads(p['prompt'])
    assert p['consentVersion']=='http-dsl-improvement-v2'
    diagnostics=payload['diagnostics']
    assert diagnostics['ruleVersion']=='doctor-v1'
    assert len(diagnostics['items'])==12
    items={item['id']:item for item in diagnostics['items']}
    assert items['sample-size']['evidence']=={'sellFillCount':0}
    assert items['sample-size']['status']=='triggered' and items['sample-size']['threshold']==20
    assert items['cash-depletion']['evidence']=={'minimumCashPct':80}
    assert items['cash-depletion']['status']=='clear'
    assert items['drawdown']['evidence']=={'maxDrawdownPct':0}
    assert items['drawdown']['status']=='clear'
    assert items['cost-burden']['evidence']=={'feeAndTax':0,'initialCapitalPct':0}
    assert items['corporate-actions']['evidence']=={}
    assert items['fill-model-reconciliation']['evidence']=={}
    assert items['fill-model-reconciliation']['status']=='clear'
    assert set(items['accounting-reconciliation']['evidence'])=={'checkedFields'}
    assert 'sample limitations' in payload['instruction'] and 'not a profit guarantee' in payload['instruction']
    assert state['calls']==[]
    result=invoke(service,ident,p)
    assert result['status']=='succeeded'
    assert json.loads(state['calls'][0]['messages'][0]['content'])==payload
    assert service.get_run(baseline['id'])==baseline


def test_summary_never_copies_provenance_or_user_strings(system):
    analysis=doctor(system)
    for item in analysis['diagnostics']:
        item['evidence'].update(tradeIds=['PRIVATE_TRADE_ID'],reason='PRIVATE_USER_REASON',
                               source={'label':'PRIVATE_SOURCE_LABEL','account':'PRIVATE_ACCOUNT'},
                               adjustment='PRIVATE_ADJUSTMENT',rawPrice=12345678,
                               bars=[{'close':12345678}],key='PRIVATE_KEY')
    output=context.summary(analysis);text=canonical(output)
    assert 'PRIVATE_' not in text and '12345678' not in text
    assert all(type(value) in (int,float,type(None)) for item in output['items'] for value in item['evidence'].values())
    assert len(text.encode('utf-8'))<=context.MAX_SUMMARY_BYTES


@pytest.mark.parametrize('mutation',[
    lambda a:a['diagnostics'][0].update(title='PRIVATE_KEY'),
    lambda a:a['diagnostics'][0].update(message='PRIVATE_USER_REASON'),
    lambda a:a['diagnostics'][0].update(status='PRIVATE_STATUS'),
    lambda a:a['diagnostics'][0].update(threshold=True),
    lambda a:a['diagnostics'][0]['evidence'].update(sellFillCount=True),
    lambda a:a['diagnostics'][0]['evidence'].update(sellFillCount='PRIVATE_TEXT'),
    lambda a:a['diagnostics'][0]['evidence'].update(sellFillCount=float('inf')),
    lambda a:a['diagnostics'][0]['evidence'].update(sellFillCount=float('nan')),
    lambda a:a['diagnostics'][0]['evidence'].update(sellFillCount={'key':'PRIVATE'}),
    lambda a:a['diagnostics'].append(copy.deepcopy(a['diagnostics'][0])),
    lambda a:a['diagnostics'][0].update(id='unrecognized-rule'),
    lambda a:a['assumptions'].update(ruleVersion='unrecognized-version'),
])
def test_context_policy_fails_closed(system,mutation):
    analysis=doctor(system);mutation(analysis)
    with pytest.raises(ValueError,match='AI_GENERATION_DIAGNOSTICS_INVALID') as caught:
        context.summary(analysis)
    assert caught.value.__context__ is None


def test_fixed_size_limit_applied(system,monkeypatch):
    monkeypatch.setattr(context,'MAX_SUMMARY_BYTES',10)
    with pytest.raises(ValueError,match='DIAGNOSTICS_INVALID'):context.summary(doctor(system))


def test_recomputed_not_stored_analysis(system):
    service,baseline,_,_,_=system
    row=service.store.get('run',baseline['id']);row['analysis']={'diagnostics':'PRIVATE_STORED_ANALYSIS'}
    with service.store.connection() as con:
        con.execute("UPDATE objects SET body=? WHERE kind='run' AND id=?",(encode_body(row),baseline['id']))
    p=preview(system)
    assert 'PRIVATE_STORED_ANALYSIS' not in p['prompt']
    assert json.loads(p['prompt'])['diagnostics']['items'][0]['evidence']=={'sellFillCount':0}


def test_report_hash_checked_again_before_analysis(system,monkeypatch):
    service,baseline,ident,_,_=system
    request=service.store.get('ai-request',ident);request['resultHash']='bad'
    monkeypatch.setattr(context,'analyze',lambda *a,**k:pytest.fail('cannot analyze unverified report'))
    with pytest.raises(ValueError,match='INTEGRITY_ERROR'):
        context.prompt(request,baseline,service.get_strategy(baseline['revisionId']),service._dataset(baseline['datasetId']))


def test_source_tamper_after_preview_before_sender(system):
    service,baseline,ident,_,state=system;p=preview(system)
    row=service.store.get('run',baseline['id']);row['report']['metrics']['finalEquity']=999
    with service.store.connection() as con:
        con.execute("UPDATE objects SET body=? WHERE kind='run' AND id=?",(encode_body(row),baseline['id']))
    with pytest.raises(ValueError):invoke(service,ident,p)
    assert not state['calls']
    assert service.store.list('ai-generation')==[]


def test_same_helper_revalidates_preview(system,monkeypatch):
    service,_,ident,_,state=system;p=preview(system)
    original=ai_generation.improvement_prompt
    def changed(*args):
        value=json.loads(original(*args));value['diagnostics']['items'][0]['evidence']['sellFillCount']=123
        return canonical(value)
    monkeypatch.setattr(ai_generation,'improvement_prompt',changed)
    with pytest.raises(ValueError,match='CONSENT_MISMATCH'):invoke(service,ident,p)
    assert not state['calls']


def test_cli_prompt_and_hash_unchanged(system,tmp_path):
    service,baseline,ident,_,state=system
    service._ai_provider,_=fake(tmp_path)
    p=service.preview_ai_generation(ident)
    request=service.store.get('ai-request',ident);revision=service.get_strategy(baseline['revisionId'])
    prompt=canonical({'purpose':request['purpose'],'definition':revision['definition'],
                      'metrics':{k:v for k,v in baseline['report']['metrics'].items() if type(v) in (int,float)},
                      'instruction':'Propose 1 to 3 DSL improvements. Data and costs stay fixed.'})
    expected={'requestId':ident,'prompt':prompt,'consentVersion':'local-dsl-improvement-v1',
              'model':'sonnet','maxBudgetUsd':1.0,'timeoutSeconds':10,
              'notice':'외부 모델 전송·과금 및 동일 데이터/비용의 실제 재백테스트에 동의합니다.'}
    assert p=={**expected,'promptHash':digest(expected)}
    assert 'diagnostics' not in json.loads(p['prompt']) and not state['calls']


def make_legacy_preview(service,ident,p,ready=False):
    old=copy.deepcopy(p);payload=json.loads(old['prompt']);payload.pop('diagnostics')
    payload['instruction']='Propose 1 to 3 DSL improvements. Data and costs stay fixed.'
    old['prompt']=canonical(payload);old['consentVersion']='http-dsl-improvement-v1'
    old['promptHash']=digest({k:v for k,v in old.items() if k!='promptHash'})
    with service.store.connection() as con:
        con.execute("UPDATE objects SET body=? WHERE kind='ai-generation-preview' AND id=?",(encode_body(old),ident))
        if ready:
            generation=service.store.get('ai-generation',ident)
            generation.update(preview=old,consentVersion=old['consentVersion'],promptHash=old['promptHash'])
            con.execute("UPDATE objects SET body=? WHERE kind='ai-generation' AND id=?",(encode_body(generation),ident))
    return old


def test_unstarted_legacy_preview_requires_new_request(system):
    service,_,ident,_,state=system;old=make_legacy_preview(service,ident,preview(system))
    assert service._ai_studio.preview_ai_http_improvement(requestId=ident)==old
    with pytest.raises(ValueError,match='CONSENT_MISMATCH'):invoke(service,ident,old)
    assert not state['calls'] and service.store.list('ai-generation')==[]


def test_ready_legacy_consent_replays_without_new_model_or_connection(system):
    service,_,ident,_,state=system;p=preview(system)
    with service.store.execution_lock():assert invoke(service,ident,p)['status']=='ready'
    old=make_legacy_preview(service,ident,p,ready=True)
    service._ai_studio.disconnect_ai_session();restored=StrategyRuntime(service.store.root)
    assert restored._ai_studio.preview_ai_http_improvement(requestId=ident)==old
    assert invoke(restored,ident,old)['status']=='succeeded'
    assert len(state['calls'])==1


def test_actual_analysis_raw_source_adjustment_and_reasons_stay_local(system):
    service,baseline,ident,_,_=system
    run=copy.deepcopy(baseline)
    run['report']['source']['adjustment']='PRIVATE_CORPORATE_ACTION_KEY'
    run['report']['source']['label']='PRIVATE_SOURCE_LABEL'
    for trade in run['report']['trades']:
        trade['reason']='PRIVATE_USER_REASON'
    request=service.store.get('ai-request',ident)
    request['resultHash']=digest(run['report'])
    text=context.prompt(request,run,service.get_strategy(run['revisionId']),service._dataset(run['datasetId']))
    assert 'PRIVATE_' not in text
    items={item['id']:item for item in json.loads(text)['diagnostics']['items']}
    assert items['corporate-actions']['status']=='unsupported'
    assert items['corporate-actions']['evidence']=={}
    assert 'tradeIds' not in text and 'lastClose' not in text and 'mismatches' not in text


@pytest.mark.parametrize('phase',['fresh','ready','disconnected','restarted'])
def test_report_body_tamper_blocks_every_improvement_phase(system,monkeypatch,phase):
    service,baseline,ident,_,state=system;p=preview(system)
    if phase!='fresh':
        with service.store.execution_lock():assert invoke(service,ident,p)['status']=='ready'
    if phase in ('disconnected','restarted'):service._ai_studio.disconnect_ai_session()
    if phase=='restarted':service=StrategyRuntime(service.store.root)
    before_calls=len(state['calls']);before_runs=len(service.list_runs()['runs']);before_revisions=len(service.list_strategies()['strategies'])
    row=service.store.get('run',baseline['id']);stored_manifest=copy.deepcopy(row['manifest'])
    row['report']['metrics']['finalEquity']=999
    with service.store.connection() as con:
        con.execute("UPDATE objects SET body=? WHERE kind='run' AND id=?",(encode_body(row),baseline['id']))
    # Codec accepts valid encoded JSON; semantic integrity belongs to Runtime.
    assert service.store.get('run',baseline['id'])['report']['metrics']['finalEquity']==999
    assert service.store.get('run',baseline['id'])['manifest']==stored_manifest
    monkeypatch.setattr(service,'_run_locked',lambda *a,**k:pytest.fail('no engine after source tamper'))
    if phase=='fresh':
        with pytest.raises(ValueError):invoke(service,ident,p)
    else:
        result=invoke(service,ident,p)
        assert result['status']=='failed' and result['job']['stage']=='source'
        assert result['result'] is None
    assert len(state['calls'])==before_calls
    assert len(service.list_runs()['runs'])==before_runs
    assert len(service.list_strategies()['strategies'])==before_revisions

