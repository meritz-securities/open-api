"""R-03: AI 후보 없이(제출 0건) 학습 구간 원본 실행만으로 연구를 확정하고 검증까지 이어간다.

고객용 앱(ai_studio=False)은 상용 AI 가 없으므로 후보 제출이 일어나지 않는다. 그 경우에도
원본 전략만으로 freeze → evaluate 가 동작해야 하며, 후보가 1개 이상 제출되면 기존 규칙(전부 완료)을 유지한다.
"""
import pytest

from runtime.service import StrategyRuntime
from test_research_setup import setup


def _runtime_and_source(path, prices=(10, 20, 40, 100, 110, 90)):
    service, source, bars = setup(path, prices)
    assert service.ai_studio_enabled is False          # 기본값 fail-closed
    return service, source


def _prepared(path):
    service, source = _runtime_and_source(path)
    ident = service.create_research(source, '원본만 검증', .5)['session']['id']
    view = service.prepare_research_training(ident)
    return service, source, ident, view


def test_zero_candidate_freeze_and_evaluate_with_ai_studio_disabled(tmp_path):
    service, source, ident, view = _prepared(tmp_path)
    assert view['training']['jobStatus'] == 'succeeded' and view['training']['bindingStatus'] == 'bound'
    assert view['training']['submittedCandidateCount'] == 0
    assert view['eligibility']['canFreeze'] is True
    assert view['eligibility']['freezeMode'] == 'original'
    assert 'TRAINING_CANDIDATES_NOT_COMPLETE' not in view['eligibility']['reasons']['freeze']

    frozen_view = service.freeze_research(ident)
    frozen = frozen_view['frozen']
    assert frozen['candidateCount'] == 0 and frozen['selection'] == 'original' and frozen['freezeMode'] == 'original'
    assert frozen['submissionHash'] is None
    assert frozen['selectedRevisionId'] == source['revisionId']
    assert [c['role'] for c in frozen['candidates']] == ['baseline']
    assert frozen['candidates'][0]['selected'] is True and frozen['candidates'][0]['evaluationEligible'] is True
    assert [s['role'] for s in frozen['evaluationSlots']] == ['baseline']
    assert frozen_view['eligibility']['canEvaluate'] is True and frozen_view['eligibility']['freezeMode'] is None

    result = service.evaluate_research(ident)
    assert result['evaluation']['status'] == 'succeeded'
    assert len(result['evaluation']['slots']) == 1 and result['evaluation']['slots'][0]['status'] == 'succeeded'
    assert result['evaluation']['result']['selectedRevisionId'] == source['revisionId']
    assert len(service.list_runs()['runs']) == 2      # 학습 원본 + 검증 원본
    # 재기동 후에도 같은 확정·결과를 읽는다.
    assert StrategyRuntime(tmp_path).get_research(ident) == result


def test_freeze_blocked_before_training_baseline_exists(tmp_path):
    service, source = _runtime_and_source(tmp_path)
    ident = service.create_research(source, '학습 전', .5)['session']['id']
    view = service.get_research(ident)
    assert view['eligibility']['canFreeze'] is False and view['eligibility']['freezeMode'] is None
    with pytest.raises(ValueError, match='TRAINING_BASELINE_NOT_READY'):
        service.freeze_research(ident)


def test_submitted_candidates_keep_all_complete_rule(tmp_path):
    service, source, ident, view = _prepared(tmp_path)
    original = service.get_strategy(view['session']['revisionId'])['definition']
    outcome = service.submit_ai_candidates(view['training']['aiRequestId'], [original, {**original, 'quantity': True}])
    assert [a['status'] for a in outcome['attempts']] == ['succeeded', 'failed']
    view = service.get_research(ident)
    assert view['training']['submittedCandidateCount'] == 2
    assert view['eligibility']['canFreeze'] is False and view['eligibility']['freezeMode'] is None
    with pytest.raises(ValueError, match='TRAINING_CANDIDATES_NOT_COMPLETE'):
        service.freeze_research(ident)


def test_completed_candidates_freeze_reports_candidate_count_and_selection(tmp_path):
    service, source, ident, view = _prepared(tmp_path)
    original = service.get_strategy(view['session']['revisionId'])['definition']
    service.submit_ai_candidates(view['training']['aiRequestId'], [{**original, 'name': 'q3', 'quantity': 3}])
    view = service.get_research(ident)
    assert view['eligibility']['freezeMode'] == 'candidates' and view['training']['submittedCandidateCount'] == 1
    frozen = service.freeze_research(ident)['frozen']
    assert frozen['candidateCount'] == 1 and frozen['freezeMode'] == 'candidates'
    assert frozen['selection'] == 'candidate' and frozen['selectedRevisionId'] != source['revisionId']
    assert frozen['submissionHash'] is not None


def test_cancelled_ai_request_does_not_qualify_as_original_only(tmp_path):
    service, source, ident, view = _prepared(tmp_path)
    service.cancel_ai_request(view['training']['aiRequestId'])
    view = service.get_research(ident)
    assert view['training']['aiRequestStatus'] == 'cancelled'
    assert view['eligibility']['canFreeze'] is False
    with pytest.raises(ValueError, match='TRAINING_CANDIDATES_NOT_COMPLETE'):
        service.freeze_research(ident)


def test_late_submission_after_original_freeze_does_not_change_frozen_pool(tmp_path):
    service, source, ident, view = _prepared(tmp_path)
    frozen = service.freeze_research(ident)['frozen']
    original = service.get_strategy(view['session']['revisionId'])['definition']
    service.submit_ai_candidates(view['training']['aiRequestId'], [{**original, 'name': 'late', 'quantity': 3}])
    after = service.get_research(ident)
    assert after['frozen'] == {**frozen, 'freezeHash': frozen['recordHash']} or after['frozen'] == frozen
    assert after['frozen']['candidateCount'] == 0 and after['training']['submittedCandidateCount'] == 1
    result = service.evaluate_research(ident)
    assert result['evaluation']['status'] == 'succeeded' and len(result['evaluation']['slots']) == 1
