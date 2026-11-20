"""Independent reproductions of Claude R1 cancellation boundary findings."""
from test_research_lifecycle import prepared, submit
from test_research_setup import setup
from runtime.service import StrategyRuntime


def test_cancel_after_last_child_before_result_cannot_report_success(tmp_path, monkeypatch):
    runtime, _, ident, _ = prepared(tmp_path)
    submit(runtime, ident)
    frozen = runtime.freeze_research(ident)
    selected_slot = frozen['frozen']['evaluationSlots'][-1]['runId']
    original = runtime._run_locked
    acknowledgement = {}

    def finish_child_then_cancel(*args, **kwargs):
        child = original(*args, **kwargs)
        if kwargs.get('runId') == selected_slot:
            acknowledgement.update(StrategyRuntime(tmp_path).cancel_research(ident, 'evaluation')['cancellation'])
        return child

    monkeypatch.setattr(runtime, '_run_locked', finish_child_then_cancel)
    result = runtime.evaluate_research(ident)
    assert acknowledgement['effective'] is True
    assert result['evaluation']['status'] == 'cancelled'
    # Completed child evidence must survive even when the phase is cancelled.
    assert [slot['status'] for slot in result['evaluation']['slots']] == ['succeeded', 'succeeded']
    before = {run['id'] for run in runtime.list_runs()['runs']}
    assert StrategyRuntime(tmp_path).evaluate_research(ident) == result
    assert {run['id'] for run in runtime.list_runs()['runs']} == before


def test_unavailable_evaluation_cancel_does_not_consume_future_phase(tmp_path):
    runtime, _, ident, _ = prepared(tmp_path)
    cancelled = runtime.cancel_research(ident, 'evaluation')
    assert cancelled['cancellation']['effective'] is False
    assert cancelled['cancellation']['reason'] == 'POOL_NOT_FROZEN'
    submit(runtime, ident)
    runtime.freeze_research(ident)
    assert runtime.evaluate_research(ident)['evaluation']['status'] == 'succeeded'


def test_training_cancel_between_request_check_and_create_is_forwarded(tmp_path, monkeypatch):
    runtime, source, _ = setup(tmp_path)
    created = runtime.create_research(source, 'Cancel at request creation', .5)
    ident = created['session']['id']
    original = runtime.create_ai_request

    def cancel_then_create(*args, **kwargs):
        runtime.cancel_research(ident, 'training')
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, 'create_ai_request', cancel_then_create)
    result = runtime.prepare_research_training(ident)
    assert result['training']['cancellationRequested'] is True
    request = runtime.get_ai_request(result['training']['aiRequestId'])
    assert request['status'] == 'cancelled'
    assert result['eligibility']['canFreeze'] is False


def test_cooperative_cancel_preserves_terminal_result_without_executing_slots(tmp_path, monkeypatch):
    from runtime import research
    runtime, _, ident, _ = prepared(tmp_path)
    submit(runtime, ident)
    runtime.freeze_research(ident)
    original = research._claim_phase

    def claim_then_cancel(service, plan, phase, slots):
        job = original(service, plan, phase, slots)
        if phase == 'evaluation':
            service.cancel_research(ident, phase)
        return job

    monkeypatch.setattr(research, '_claim_phase', claim_then_cancel)
    result = runtime.evaluate_research(ident)
    assert result['evaluation']['status'] == 'cancelled'
    assert all(slot['status'] == 'not_run' for slot in result['evaluation']['slots'])
    assert len(runtime.list_runs()['runs']) == 3
    terminal = runtime.store.get('research-result', ident)
    assert terminal['status'] == 'cancelled'
    assert terminal['slots'] == result['evaluation']['slots']
