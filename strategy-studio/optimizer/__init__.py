"""Bounded research helpers. Accounting always belongs to the injected runtime."""
import copy
import random
import math


def monte_carlo(report, seed=0, iterations=200, cancel=None):
    if type(seed) is not int or type(iterations) is not int or not 1<=iterations<=500:
        raise ValueError('seed must be integer; iterations 1..500')
    values=[x['equity'] for x in report.get('equity',[])]
    if len(values)<3 or any(x<=0 for x in values):
        return {'status':'insufficient_data','reason':'requires at least three positive equity observations','samples':[]}
    returns=[b/a-1 for a,b in zip(values,values[1:])]
    if len(returns)>5000:raise ValueError('too many observations')
    rng=random.Random(seed); samples=[]
    for _ in range(iterations):
        if cancel is not None: cancel()
        log_wealth=log_peak=0.0; mdd=0
        for _ in returns:
            log_wealth+=math.log1p(rng.choice(returns));log_peak=max(log_peak,log_wealth)
            mdd=max(mdd,-math.expm1(log_wealth-log_peak))
        try:
            value=math.expm1(log_wealth)*100
            if not math.isfinite(value):value=None
        except OverflowError:value=None
        sample={'returnPct':value,'maxDrawdownPct':mdd*100}
        if value is None:sample['reason']='resampled cumulative return exceeds finite numeric range'
        samples.append(sample)
    return {'status':'completed','method':'daily-return-bootstrap','seed':seed,'iterations':iterations,'samples':samples,'assumptions':['Independent resampling with replacement destroys serial dependence.','Descriptive resampling of observed strategy returns, not future prediction or order simulation.']}


def cost_sensitivity(settings, rerun, fee_multipliers=(0.5,1,2)):
    if not callable(rerun) or not 1<=len(fee_multipliers)<=12:raise ValueError('bounded rerun callback required')
    results=[]
    for multiplier in fee_multipliers:
        if type(multiplier) not in (int,float) or not 0<=multiplier<=10:raise ValueError('invalid fee multiplier')
        candidate=copy.deepcopy(settings);candidate['feeBps']=settings['feeBps']*multiplier
        try:results.append({'settings':candidate,'status':'succeeded','result':rerun(candidate)})
        except Exception as error:
            failure={'settings':candidate,'status':'failed','error':'candidate execution failed'}
            if getattr(error,'run_id',None):failure['runId']=error.run_id
            results.append(failure)
    return {'method':'fee-sensitivity','results':results,'assumption':'Full strategy reruns; changed portfolio feedback can change decisions. No improvement is assumed.'}


def chronological_split(bars, fraction=.7):
    if type(fraction) not in (int,float) or not .2<=fraction<=.8 or not 4<=len(bars)<=5000:
        raise ValueError('split requires 4..5000 bars and fraction .2.. .8')
    pivot=int(len(bars)*fraction)
    if pivot<2 or len(bars)-pivot<2:raise ValueError('each partition requires two bars')
    return copy.deepcopy(bars[:pivot]),copy.deepcopy(bars[pivot:])


def out_of_sample(bars, rerun, fraction=.7):
    train,test=chronological_split(bars,fraction)
    results=[]
    for label,part in [('train',train),('test',test)]:
        try:results.append({'partition':label,'status':'succeeded','result':rerun(part)})
        except Exception as error:
            failure={'partition':label,'status':'failed','error':'partition execution failed'}
            if getattr(error,'run_id',None):failure['runId']=error.run_id
            results.append(failure)
    return {'method':'chronological-fixed-strategy','results':results,'splitDate':test[0]['date'],'assumption':'Fixed strategy; independent cash/state restart and warmup per partition. No fitting or model selection is performed.'}


def walk_forward(bars, candidate_ids, rerun, *, trainBars, testBars, cancel=None):
    """Select fixed revisions on each rolling training window, then test its winner.

    The caller owns actual execution, immutable runs, costs and cancellation.
    This helper never joins independent accounts into a portfolio equity curve.
    """
    if (not isinstance(bars, list) or not 4 <= len(bars) <= 5000 or
            any(not isinstance(bar, dict) or not isinstance(bar.get('date'), str)
                or not bar['date'] for bar in bars)):
        raise ValueError('walk-forward requires 4..5000 dated bars')
    dates = [bar['date'] for bar in bars]
    if any(a >= b for a, b in zip(dates, dates[1:])):
        raise ValueError('walk-forward bars must have strictly increasing dates')
    if (not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= 4 or
            any(not isinstance(ident, str) or not ident.strip() for ident in candidate_ids) or
            len(set(candidate_ids)) != len(candidate_ids)):
        raise ValueError('walk-forward requires 1..4 unique revision IDs')
    if type(trainBars) is not int or type(testBars) is not int or min(trainBars, testBars) < 2:
        raise ValueError('trainBars and testBars must be integers of at least two')
    fold_count = (len(bars) - trainBars) // testBars
    if not 1 <= fold_count <= 6:
        raise ValueError('walk-forward requires 1..6 complete test windows; adjust trainBars/testBars')
    if not callable(rerun) or (cancel is not None and not callable(cancel)):
        raise ValueError('walk-forward requires execution and optional cancellation callbacks')

    def checkpoint():
        if cancel is not None:
            cancel()

    def execute(ident, part):
        checkpoint()
        value = None
        try:
            # Each candidate receives its own data copy; callback mutation must
            # neither alter another candidate's input nor leak future bars.
            value = rerun(ident, copy.deepcopy(part))
            if (not isinstance(value, dict) or value.get('status', 'succeeded') != 'succeeded'
                    or not isinstance(value.get('runId'), str) or not value['runId']):
                raise ValueError('invalid execution result')
            score = value.get('metrics', {}).get('totalReturnPct')
            if type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError('finite totalReturnPct required')
            # Never copy a full report into the fold response. Its run ID is
            # sufficient to retrieve persisted detail through the Runtime.
            result = {key: copy.deepcopy(value[key]) for key in
                      ('runId', 'status', 'metrics', 'settings', 'dataHash', 'detail') if key in value}
        except Exception as error:
            # An execution callback may surface cancellation as an Exception.
            # The authoritative checkpoint must re-raise it, not turn it into
            # a failed candidate and continue doing work.
            checkpoint()
            failure = {'status': 'failed', 'error': 'candidate execution or metric validation failed'}
            run_id = getattr(error, 'run_id', None)
            if not run_id and isinstance(value, dict):
                run_id = value.get('runId')
            if isinstance(run_id, str) and run_id:
                failure['runId'] = run_id
            return failure
        checkpoint()
        return {'status': 'succeeded', 'result': result}

    folds = []
    for index in range(fold_count):
        checkpoint()
        start = index * testBars
        split = start + trainBars
        stop = split + testBars
        train = bars[start:split]
        test = bars[split:stop]
        fold = {'index': index + 1, 'status': 'failed',
                'train': {'startDate': dates[start], 'endDate': dates[split - 1], 'barCount': trainBars},
                'testWindow': {'startDate': dates[split], 'endDate': dates[stop - 1], 'barCount': testBars},
                'training': [], 'selectedRevisionId': None, 'test': None}
        for ident in candidate_ids:
            fold['training'].append({'revisionId': ident, **execute(ident, train)})
        if all(item['status'] == 'succeeded' for item in fold['training']):
            # max preserves input order for ties. No test result participates
            # in selection, and only the selected revision sees this test window.
            winner = max(fold['training'], key=lambda item: item['result']['metrics']['totalReturnPct'])
            fold['selectedRevisionId'] = winner['revisionId']
            fold['test'] = execute(winner['revisionId'], test)
            fold['status'] = fold['test']['status']
        else:
            fold['error'] = 'selection skipped because at least one training candidate failed'
        folds.append(fold)
    checkpoint()
    return {'method': 'rolling-train-selection-test',
            'status': 'completed' if all(fold['status'] == 'succeeded' for fold in folds) else 'completed_with_failures',
            'selectionMetric': 'totalReturnPct', 'tieBreak': 'candidate input order',
            'candidateRevisionIds': list(candidate_ids), 'trainBars': trainBars, 'testBars': testBars,
            'folds': folds, 'droppedTailBars': len(bars) - trainBars - fold_count * testBars,
            'assumptions': [
                'Each training and test run restarts cash, strategy state and indicator warmup independently.',
                'Selection uses training totalReturnPct only. Test windows are disjoint; later training may use earlier test observations.',
                'The fixed candidate pool may have been designed after observing the full history. This is retrospective research, not an untouched holdout guarantee.',
                'Independent test accounts are not combined into a continuous portfolio or aggregate equity curve.'
            ]}
