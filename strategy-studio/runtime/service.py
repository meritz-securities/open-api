"""Application service shared by Studio, CLI and the existing Meritz MCP.

No live-order API is reachable from this module. What-if reuses immutable bars.
"""
from __future__ import annotations
import copy
import csv
import datetime as dt
import io
import math
import platform
import threading
import time
import uuid
from . import RELEASE, VERSION
from .storage import Store, canonical, digest


WALK_FORWARD_TIMEOUT_SECONDS = 120
PARAMETER_GRID_TIMEOUT_SECONDS = 120


class _ExecutionFailed(ValueError):
    def __init__(self, message, run_id):
        super().__init__(message)
        self.run_id = run_id


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def settings_contract(settings):
    defaults = {'initialCash': 1_000_000, 'feeBps': 15, 'slippageBps': 10, 'taxBps': 0}
    if not isinstance(settings, dict) or set(settings) - set(defaults):
        raise ValueError('설정은 initialCash, feeBps, slippageBps, taxBps만 지원합니다.')
    result = {**defaults, **settings}
    if any(not finite(v) for v in result.values()) or not 1 <= result['initialCash'] <= 1e12 or any(not 0 <= result[k] <= 1000 for k in ('feeBps', 'slippageBps', 'taxBps')):
        raise ValueError('초기자본·비용 범위를 확인하세요.')
    return result


def validate_editor_layout(definition, layout):
    """Validate display-only coordinates against an already validated DSL.

    Paths are enumerated from nodes, never evaluated or treated as file paths.
    The existing definition depth/node limits remain independently applicable.
    """
    if definition['kind'] != 'dsl':
        raise ValueError('editorLayout은 DSL 전략에만 저장할 수 있습니다.')
    if type(layout) is not dict or len(layout) > 128:
        raise ValueError('editorLayout은 최대 128개 노드 좌표의 객체여야 합니다.')
    valid_paths = {'buy', 'sell'}
    pending = [('entry', definition['entry']), ('exit', definition['exit'])]
    while pending:
        path, node = pending.pop()
        valid_paths.add(path)
        if node.get('op') in ('all', 'any'):
            pending.extend((f'{path}.conditions.{index}', child) for index, child in enumerate(node['conditions']))
        elif 'op' in node:
            pending.extend(((f'{path}.left', node['left']), (f'{path}.right', node['right'])))
    result = {}
    for path, point in layout.items():
        if not isinstance(path, str) or path not in valid_paths:
            raise ValueError('editorLayout 경로는 현재 DSL의 조건·피연산자 또는 buy/sell 노드여야 합니다.')
        if type(point) is not dict or set(point) != {'x', 'y'}:
            raise ValueError('editorLayout 좌표에는 x와 y만 지정하세요.')
        if any(type(point[key]) not in (int, float) or not 0 <= point[key] <= 10000
               or not math.isfinite(point[key]) for key in ('x', 'y')):
            raise ValueError('editorLayout x/y는 0~10000 범위의 유한한 숫자여야 합니다.')
        result[path] = {'x': point['x'], 'y': point['y']}
    return result


def normalize_data(bars, source):
    allowed = {'kind', 'label', 'symbol', 'market', 'exchange', 'quoteMode', 'currency', 'timezone', 'startDate', 'endDate', 'fetchedAt', 'adjustment'}
    if not isinstance(source, dict) or set(source) - allowed or source.get('kind') not in ('synthetic', 'meritz', 'imported'):
        raise ValueError('명시적인 데이터 출처가 필요합니다. 계정·인증값은 데이터에 넣지 마세요.')
    if not isinstance(source.get('label'), str) or not 1 <= len(source['label']) <= 200 or any(not isinstance(v, str) or len(v) > 200 for v in source.values()):
        raise ValueError('데이터 출처 형식을 확인하세요.')
    if not isinstance(bars, list) or not 2 <= len(bars) <= 5000:
        raise ValueError('확정 일봉 2~5,000개를 입력하세요.')
    previous = ''; cleaned = []
    for bar in bars:
        if not isinstance(bar, dict) or set(bar) - {'date', 'open', 'high', 'low', 'close', 'volume'}:
            raise ValueError('일봉은 date/OHLC와 선택적 volume만 지원합니다. 기업행동 이벤트는 미지원입니다.')
        date = bar.get('date')
        try:
            if not isinstance(date, str) or dt.date.fromisoformat(date).isoformat() != date or date <= previous:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('일봉 날짜는 중복 없이 오름차순 YYYY-MM-DD여야 합니다.') from None
        for field in ('open', 'high', 'low', 'close'):
            if not finite(bar.get(field)) or not .01 <= bar[field] <= 1e9:
                raise ValueError('일봉 가격 범위를 확인하세요.')
        if bar['low'] > min(bar['open'], bar['close']) or bar['high'] < max(bar['open'], bar['close']):
            raise ValueError('OHLC 가격 관계가 올바르지 않습니다.')
        if 'volume' in bar and (not finite(bar['volume']) or bar['volume'] < 0 or bar['volume'] > 1e15):
            raise ValueError('거래량을 확인하세요.')
        cleaned.append(dict(bar)); previous = date
    return cleaned, dict(source)


class _ValidationCancelled(BaseException):
    """Escape optimizer callbacks that intentionally collect ordinary failures."""


def _python_available():
    system = platform.system()
    if system == 'Windows':
        from meritz.windows_strategy import available
        return available()
    return system == 'Darwin'


class StrategyRuntime:
    def __init__(self, root=None, catalog=None, registry=None, allow_market_connection=False, ai_provider=None, allow_ai_connection=False, ai_studio=False):
        if allow_market_connection and registry is not None:
            raise ValueError('공유 Registry에는 앱 전용 연결을 추가할 수 없습니다.')
        self.store = Store(root); self.catalog = catalog; self.registry = registry
        from .market_connection import MarketConnection
        self._market_connection = MarketConnection() if allow_market_connection else None
        self._active = {}; self._mutex = threading.Lock()
        self._ai_provider = ai_provider
        from .ai_studio import AIStudio
        self._ai_studio = AIStudio(self.store, management_allowed=allow_ai_connection, service=self)
        # Studio 내 AI 기능(상용 AI 키 연결·초안 생성·개선 요청) 전체 스위치. 기본값 False(fail-closed) —
        # 고객에게 별도 상용 AI 키를 요구하지 않는다. AI 활용은 메리츠 MCP 경로(--mcp, meritz.server)로만 제공한다.
        # 개발·테스트 픽스처만 명시적으로 ai_studio=True 를 넘긴다.
        self.ai_studio_enabled = bool(ai_studio)

    def templates(self):
        from .dsl import templates, template_details
        definitions = templates()
        return {'templates': definitions, 'templateDetails': template_details(definitions)}

    def validate_strategy(self, definition):
        from .dsl import StrategyValidationError, validate_definition
        try:
            normalized = validate_definition(definition)
            return {'valid': True, 'errors': [], 'definition': normalized}
        except StrategyValidationError as error:
            return {'valid': False, 'errors': error.errors}
        except (ValueError, TypeError, RecursionError) as error:
            # Schema errors contain no provider responses or host secrets.
            return {'valid': False, 'errors': [{'message': str(error)[:500]}]}

    def save_strategy(self, definition, parentRevisionId=None, editorLayout=None):
        from .dsl import validate_definition
        normalized = validate_definition(definition)
        layout = validate_editor_layout(normalized, editorLayout) if editorLayout is not None else None
        parent = self.store.get('revision', parentRevisionId) if parentRevisionId else None
        ident = uuid.uuid4().hex
        revision = {'id': ident, 'strategyId': parent['strategyId'] if parent else uuid.uuid4().hex,
                    'parentRevisionId': parentRevisionId, 'definition': normalized, 'hash': digest(normalized), 'createdAt': now()}
        if layout is not None:
            revision['editorLayout'] = layout
        return self.store.put('revision', ident, revision)

    def list_strategies(self):
        return {'strategies': self.store.list('revision')}

    def get_strategy(self, revisionId):
        revision = self.store.get('revision', revisionId)
        if digest(revision['definition']) != revision['hash']:
            raise ValueError('전략 snapshot 무결성 검사 실패')
        return revision

    def import_data(self, bars, source):
        if not isinstance(source, dict) or source.get('kind') != 'imported':
            raise ValueError('가져온 파일의 출처는 imported로 표시해야 합니다.')
        return self._save_data(bars, source)

    def _save_data(self, bars, source, warnings=None, derivedFrom=None, validationJobId=None):
        bars, source = normalize_data(bars, source)
        warnings = [] if warnings is None else warnings
        if not isinstance(warnings, list) or len(warnings) > 30 or any(not isinstance(w, str) or len(w) > 500 for w in warnings):
            raise ValueError('Invalid provider data quality warnings')
        stable_source = {k: v for k, v in source.items() if k != 'fetchedAt'}
        content_hash = digest({'bars': bars, 'source': stable_source})
        data = {'id': uuid.uuid4().hex, 'contentHash': content_hash, 'source': source,
                'bars': bars, 'warnings': warnings, 'qualityHash': digest(warnings), 'barCount': len(bars), 'startDate': bars[0]['date'], 'endDate': bars[-1]['date'], 'createdAt': now()}
        if derivedFrom is not None:
            data.update(derivedFrom=derivedFrom, validationJobId=validationJobId)
        self.store.put('dataset', data['id'], data)
        return {k: v for k, v in data.items() if k != 'bars'}

    def import_csv(self, content, source):
        if not isinstance(content, str) or len(content.encode()) > 2_000_000:
            raise ValueError('CSV는 UTF-8 2MB 이하여야 합니다.')
        reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
        if not reader.fieldnames or set(reader.fieldnames) - {'date', 'open', 'high', 'low', 'close', 'volume'} or not {'date', 'open', 'high', 'low', 'close'} <= set(reader.fieldnames) or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError('CSV 열은 date,open,high,low,close 및 선택적 volume입니다.')
        bars = []
        for row in reader:
            if len(bars) >= 5000 or None in row or any(v is None for v in row.values()):
                raise ValueError('CSV 행 수·열 수를 확인하세요.')
            try:
                bars.append({key: value if key == 'date' else float(value) for key, value in row.items()})
            except (ValueError, TypeError):
                raise ValueError('CSV 가격·거래량은 유한한 숫자여야 합니다.') from None
        return self.import_data(bars, source)

    def demo_data(self):
        from meritz.backtest_engine import demo_candles
        return self._save_data(demo_candles(), {'kind': 'synthetic', 'label': '합성 일봉 · 실제 종목/시세 아님', 'currency': 'KRW'})

    def get_market_connection(self):
        if self._market_connection is not None:
            return self._market_connection.status()
        profiles = getattr(self.registry, 'profiles', [])
        return {'managementAllowed': False, 'source': 'inherited',
                'configured': any(profile.has_credentials for profile in profiles),
                'authenticated': None, 'busy': False, 'lastRequest': None,
                'notice': '기존 실행 경로의 연결을 사용합니다. 이 화면에서 변경하지 않습니다.'}

    def connect_market_session(self, appKey, appSecret):
        if self._market_connection is None:
            raise ValueError('이 실행 경로에서는 연결을 변경할 수 없습니다.')
        return self._market_connection.connect(appKey, appSecret)

    def disconnect_market_session(self):
        if self._market_connection is None:
            raise ValueError('이 실행 경로에서는 연결을 변경할 수 없습니다.')
        return self._market_connection.disconnect()

    def fetch_data(self, symbol, startDate, endDate, account=None, market='KR', exchange=None, quoteMode=None):
        if self._market_connection is not None:
            if account is not None:
                raise ValueError('앱 전용 연결에서는 계좌 선택 인자를 지정하지 마세요.')
            with self._market_connection.fetch() as registry:
                return self._fetch_data(registry, symbol, startDate, endDate, account, market, exchange, quoteMode)
        return self._fetch_data(self.registry, symbol, startDate, endDate, account, market, exchange, quoteMode)

    def _fetch_data(self, registry, symbol, startDate, endDate, account=None, market='KR', exchange=None, quoteMode=None):
        # Default KR preserves existing callers. US never guesses exchange or
        # quote entitlement from the symbol, account, or local machine settings.
        if market not in ('KR', 'US'):
            raise ValueError('지원하는 조회 시장은 KR(국내) 또는 US(미국)입니다.')
        if market == 'KR' and (exchange is not None or quoteMode is not None):
            raise ValueError('국내 조회에는 미국 거래소·시세 종류를 지정하지 마세요.')
        if self.catalog is None or registry is None:
            raise ValueError('기존 메리츠 MCP 계정이 연결된 실행 경로에서 시세를 조회하세요.')
        if market == 'US':
            from meritz.backtest_overseas_data import load_overseas_market_data
            data = load_overseas_market_data(self.catalog, registry, symbol, startDate, endDate,
                                            exchange=exchange, quote_mode=quoteMode, account=account)
            source = data['source']  # Preserve USD, New York timezone, exchange and quote mode.
        else:
            from meritz.backtest_data import load_market_data
            data = load_market_data(self.catalog, registry, symbol, startDate, endDate, account)
            source = {**data['source'], 'currency': 'KRW'}
        return self._save_data(data['candles'], source, data.get('warnings', []))

    def list_data(self):
        return {'datasets': [{k: v for k, v in d.items() if k != 'bars'} for d in self.store.list('dataset', exclude_derived=True)]}

    def _dataset(self, ident):
        data = self.store.get('dataset', ident)
        if 'qualityHash' in data and digest(data.get('warnings', [])) != data['qualityHash']:
            raise ValueError('데이터 품질 경고 snapshot 무결성 검사 실패')
        stable_source = {k: v for k, v in data['source'].items() if k != 'fetchedAt'}
        if digest({'bars': data['bars'], 'source': stable_source}) != data['contentHash']:
            raise ValueError('데이터 snapshot 무결성 검사 실패')
        return data

    def run(self, revisionId, datasetId, settings=None, parentRunId=None):
        revision = self.store.get('revision', revisionId)
        if digest(revision['definition']) != revision['hash']:
            raise ValueError('전략 snapshot 무결성 검사 실패')
        if revision['definition'].get('kind') == 'python' and not _python_available():
            raise ValueError('unsupported_platform: Python 전략 격리 환경이 준비되지 않았습니다.')
        data = self._dataset(datasetId); config = settings_contract({} if settings is None else settings)
        if parentRunId:
            parent = self.get_run(parentRunId)
            if parent['datasetId'] != datasetId:
                raise ValueError('실험은 원본과 동일한 데이터 snapshot을 사용해야 합니다.')
        with self.store.execution_lock():
            return self._run_locked(revision, data, config, parentRunId)

    def _run_locked(self, revision, data, config, parentRunId=None, cancel=None, validationJobId=None, aiRequestId=None, aiJobId=None, *, runId=None, researchId=None, researchPhase=None, researchSlot=None):
        """Caller owns the store execution lock; every attempt gets its own record."""
        if runId is not None:
            from .research import validate_reserved_execution
            validate_reserved_execution(self, runId, researchId, researchPhase, researchSlot, revision, data, config, validationJobId)
        elif any(value is not None for value in (researchId, researchPhase, researchSlot)):
            raise ValueError('Research execution requires a reserved run ID')
        ident = uuid.uuid4().hex if runId is None else runId
        cancel = cancel if cancel is not None else threading.Event()
        run = {'id': ident, 'revisionId': revision['id'], 'datasetId': data['id'], 'parentRunId': parentRunId,
               'status': 'running', 'createdAt': now(), 'settings': config}
        if validationJobId is not None: run['validationJobId'] = validationJobId
        if aiRequestId is not None: run.update(aiRequestId=aiRequestId, aiJobId=aiJobId)
        if researchId is not None: run.update(researchId=researchId, researchPhase=researchPhase, researchSlot=researchSlot)
        self.store.put('run', ident, run)
        with self._mutex: self._active[ident] = cancel
        started = time.monotonic()
        try:
            report = self._calculate(revision['definition'], data, config, cancel)
            from analytics import analyze
            from meritz.strategy_engine import ENGINE_VERSION
            manifest = {'strategyHash': revision['hash'], 'dataHash': data['contentHash'], 'engineVersion': ENGINE_VERSION,
                        'runtimeVersion': VERSION, 'resultHash': digest(report), 'settings': config,
                        'executionPlatform': platform.system(), 'dataSource': data['source'], 'dataWarnings': data.get('warnings', []),
                        'fillRule': report['assumptions']['fillRule'], 'corporateActions': 'not simulated',
                        'arithmetic': 'IEEE754 research arithmetic; not exchange tax/fee rounding'}
            run.update(status='succeeded', report=report, manifest=manifest, analysis=analyze(report, manifest, bars=data['bars'], settings=config), elapsedSeconds=round(time.monotonic()-started, 6))
            self.store.finish_run(ident, run)
            return run
        except Exception as error:
            # A failed final write may have rejected a very large report.
            # Persist only the bounded attempt envelope, never retry its payload.
            run = {k: run[k] for k in ('id', 'revisionId', 'datasetId', 'parentRunId', 'createdAt', 'settings', 'validationJobId', 'aiRequestId', 'aiJobId', 'researchId', 'researchPhase', 'researchSlot') if k in run}
            run.update(status='cancelled' if cancel.is_set() else 'failed', error='실행이 취소되었습니다.' if cancel.is_set() else '전략 실행에 실패했습니다. 입력·코드·지원 환경을 확인하세요.')
            self.store.finish_run(ident, run)
            if isinstance(error, ValueError):
                raise _ExecutionFailed(str(error)[:500], ident) from None
            raise _ExecutionFailed(run['error'], ident) from None
        finally:
            with self._mutex: self._active.pop(ident, None)

    def _calculate(self, definition, data, settings, cancel=None):
        from .dsl import make_decider, validate_definition
        from meritz.strategy_engine import run_strategy
        definition = validate_definition(definition)
        info = {'name': definition['name'], 'codeHash': digest(definition), 'interface': 'on_bar_v1'}
        config = {**settings, 'rejectInsufficient': True}
        deadline = time.monotonic() + 60
        def bounded(callback):
            def decide(ctx):
                if (cancel and cancel.is_set()) or time.monotonic() > deadline:
                    raise ValueError('실행 시간 한도를 넘었거나 취소되었습니다.')
                return callback(ctx)
            return decide
        if definition['kind'] == 'python':
            if platform.system() not in ('Darwin', 'Windows'):
                raise ValueError('unsupported_platform: Python 전략 격리 환경이 준비되지 않았습니다.')
            if len(data['bars']) > 1000:
                raise ValueError('Python 전략은 최대 1,000봉까지 실행할 수 있습니다. 더 긴 기간은 비주얼 빌더 전략을 사용하세요.')
            from meritz.strategy_runner import StrategySession
            with StrategySession(definition['code']) as session:
                result = run_strategy(data['bars'], config, bounded(session.decide), data['source'], info)
                result['runtime'] = {'isolation': session.sandbox['platform'] + ' ' + session.sandbox['mechanism'], 'checks': session.sandbox['checks'], 'reproducibility': 'Python may use clock or other nondeterministic inputs'}
                if 'image' in session.sandbox:
                    result['runtime']['image'] = session.sandbox['image']
                result['warnings'].extend(data.get('warnings', []))
                return result
        result = run_strategy(data['bars'], config, bounded(make_decider(definition, bars=data['bars'])), data['source'], info, evidence_origin='dsl-evaluated')
        result['warnings'].extend(data.get('warnings', []))
        return result

    def cancel(self, runId=None, jobId=None):
        if (runId is None) == (jobId is None):
            raise ValueError('runId 또는 jobId 중 하나를 지정하세요.')
        runId = jobId if jobId is not None else runId
        if isinstance(runId, str) and runId.startswith('generation-'):
            raise ValueError('생성 취소는 cancel_ai_request를 사용하세요.')
        with self._mutex:
            event = self._active.get(runId)
            if event is None:
                raise ValueError('이 실행 프로세스에서 진행 중인 작업이 아닙니다.')
            event.set()
        return {'runId': runId, 'jobId': runId, 'cancellationRequested': True}

    def get_run(self, runId):
        run = self.store.get('run', runId)
        if run.get('status') == 'succeeded' and digest(run['report']) != run['manifest']['resultHash']:
            raise ValueError('결과 snapshot 무결성 검사 실패')
        return run

    def list_runs(self):
        return {'runs': self.store.list_summaries('run'), 'validationJobs': self.store.list_summaries('validation-job')}

    def diagnose(self, runId):
        run = self.get_run(runId)
        if run['status'] != 'succeeded': raise ValueError('완료된 결과가 필요합니다.')
        return run['analysis']

    def experiment(self, runId, definition=None, settings=None):
        baseline = self.get_run(runId)
        if baseline['status'] != 'succeeded': raise ValueError('완료된 원본 실행이 필요합니다.')
        original = self.store.get('revision', baseline['revisionId'])
        revision = self.save_strategy(definition or original['definition'], original['id'])
        config = {**baseline['settings'], **(settings or {})}
        return self.run(revision['id'], baseline['datasetId'], config, baseline['id'])

    def propose(self, runId, request='위험을 낮추는 후보 3개'):
        if not isinstance(request, str) or len(request) > 2000: raise ValueError('개선 요청은 2,000자 이내입니다.')
        run = self.get_run(runId); definition = self.store.get('revision', run['revisionId'])['definition']
        candidates = []
        if definition['kind'] == 'dsl':
            for title, patch in [('매수 수량 축소', 'quantity'), ('종가 손절 조건 추가', 'stop'), ('종가 익절 조건 추가', 'take')]:
                candidate = copy.deepcopy(definition)
                if patch == 'quantity': candidate['quantity'] = max(1, int(candidate.get('quantity') or 2) // 2)
                else:
                    candidate.setdefault('risk', {})['stopLossPct' if patch == 'stop' else 'takeProfitPct'] = 5 if patch == 'stop' else 10
                candidate['name'] = (definition['name'][:55] + ' · ' + title)[:80]
                changed = {k: {'before': definition.get(k), 'after': v} for k, v in candidate.items() if k != 'name' and definition.get(k) != v}
                candidates.append({'name': title, 'definition': candidate, 'settings': run['settings'], 'reason': '규칙 기반 실험 후보이며 개선을 보장하지 않습니다.', 'diff': changed, 'unchanged': not changed})
        else:
            for multiplier in (.5, 1.5, 2):
                config = {**run['settings'], 'feeBps': min(1000, run['settings']['feeBps'] * multiplier)}
                candidates.append({'name': f'수수료 {multiplier}배 민감도', 'definition': definition, 'settings': config,
                                   'reason': '코드를 자동 개선하지 않는 비용 민감도 실험입니다.', 'diff': {'feeBps': {'before': run['settings']['feeBps'], 'after': config['feeBps']}}})
        return {'method': 'rule-based', 'baselineRunId': runId, 'request': request, 'candidates': candidates,
                'notice': '자연어 AI 개선은 연결된 Claude가 schema에 맞는 후보를 작성한 뒤 experiment 도구로 실제 검증합니다. 이 후보는 LLM 출력이 아닙니다.'}

    def compare(self, runIds):
        if not isinstance(runIds, list) or not 2 <= len(runIds) <= 12: raise ValueError('비교는 2~12개 실행을 지정하세요.')
        runs = [self.get_run(ident) for ident in runIds]
        if any(r['status'] != 'succeeded' for r in runs): raise ValueError('완료된 실행만 비교할 수 있습니다.')
        first = runs[0]
        return {'sameDataset': all(r['manifest']['dataHash'] == first['manifest']['dataHash'] for r in runs),
                'sameCosts': all(all(r['settings'][k] == first['settings'][k] for k in ('feeBps', 'slippageBps', 'taxBps')) for r in runs),
                'sameSettings': all(r['settings'] == first['settings'] for r in runs),
                'runs': [{'id': r['id'], 'name': r['report']['strategy']['name'], 'metrics': r['report']['metrics'],
                          'settings': r['settings'], 'dataHash': r['manifest']['dataHash'],
                          'deltaReturnPct': r['report']['metrics']['totalReturnPct'] - first['report']['metrics']['totalReturnPct'],
                          'deltaDrawdownPct': r['report']['metrics']['maxDrawdownPct'] - first['report']['metrics']['maxDrawdownPct']} for r in runs]}

    def validate_robustness(self, runId, method, options=None):
        from optimizer import monte_carlo, out_of_sample, cost_sensitivity, walk_forward
        run = self.get_run(runId)
        if run['status'] != 'succeeded': raise ValueError('완료된 실행이 필요합니다.')
        options = {} if options is None else options
        if not isinstance(options, dict): raise ValueError('검증 options 형식 오류')
        revision = self.store.get('revision', run['revisionId']); definition = revision['definition']; data = self._dataset(run['datasetId'])
        if method not in ('monte_carlo', 'out_of_sample', 'cost_sensitivity', 'walk_forward', 'parameter_grid'):
            raise ValueError('지원 검증: monte_carlo, out_of_sample, cost_sensitivity, walk_forward, parameter_grid')
        allowed = {'monte_carlo': {'seed', 'iterations'}, 'out_of_sample': {'fraction'}, 'cost_sensitivity': set(), 'walk_forward': {'candidateRevisionIds', 'trainBars', 'testBars'}, 'parameter_grid': {'axes', 'maxEvaluations'}}
        if set(options) - allowed[method]:
            raise ValueError('지원하지 않는 검증 옵션')
        candidates = {revision['id']: revision}
        grid_plans = None
        if method == 'parameter_grid':
            from optimizer.parameter_grid import plan_grid
            grid_plans = plan_grid(definition, **options)
        if method == 'walk_forward':
            ids = options.get('candidateRevisionIds', [])
            if not isinstance(ids, list) or len(ids) > 3 or any(not isinstance(i, str) for i in ids):
                raise ValueError('추가 후보 리비전은 최대 3개입니다.')
            if len(set(ids)) != len(ids) or revision['id'] in ids:
                raise ValueError('기준 전략을 제외한 서로 다른 후보 리비전을 지정하세요.')
            for candidate_id in ids:
                candidates[candidate_id] = self.store.get('revision', candidate_id)
            if any(type(options.get(k)) is not int or not 2 <= options[k] <= 5000 for k in ('trainBars', 'testBars')):
                raise ValueError('학습/평가 봉 수는 각각 2~5000 정수입니다.')
            folds = (len(data['bars']) - options['trainBars']) // options['testBars']
            if not 1 <= folds <= 6:
                raise ValueError('Walk-forward는 완전한 평가 구간 1~6개가 필요합니다.')
            if any(r['definition']['kind'] == 'python' for r in candidates.values()) and max(options['trainBars'], options['testBars']) > 1000:
                raise ValueError('Python 후보의 각 구간은 1000봉 이하입니다.')
        if any(digest(r['definition']) != r['hash'] for r in candidates.values()):
            raise ValueError('전략 snapshot 무결성 검사 실패')
        with self.store.execution_lock():
            ident = uuid.uuid4().hex; cancel = threading.Event()
            job = {'id': ident, 'runId': runId, 'method': method, 'options': options,
                   'status': 'running', 'createdAt': now(), 'childRunIds': []}
            if grid_plans is not None:
                job['candidatePlan'] = [{'index': p['index'], 'parameterValues': p['parameterValues'],
                                         'strategyHash': digest(p['definition'])} for p in grid_plans]
            self.store.put('validation-job', ident, job)
            with self._mutex: self._active[ident] = cancel
            started = time.monotonic()
            timed_out = threading.Event()
            timer = None
            if method in ('walk_forward', 'parameter_grid'):
                def expire():
                    timed_out.set(); cancel.set()
                timer = threading.Timer(PARAMETER_GRID_TIMEOUT_SECONDS if method == 'parameter_grid' else WALK_FORWARD_TIMEOUT_SECONDS, expire)
                timer.daemon = True
                timer.start()
            def checkpoint():
                if cancel.is_set(): raise _ValidationCancelled()
            partition_cache = {}
            def calculate(bars=None, settings=None, candidate_id=None):
                checkpoint()
                candidate_data = data
                if bars is not None:
                    key = digest(bars)
                    if key not in partition_cache:
                        partition_source = dict(data['source'])
                        for field, value in [('startDate', bars[0]['date']), ('endDate', bars[-1]['date'])]:
                            if field in partition_source: partition_source[field] = value
                        saved = self._save_data(bars, partition_source, data.get('warnings', []), derivedFrom=data['id'], validationJobId=ident)
                        partition_cache[key] = self._dataset(saved['id'])
                    candidate_data = partition_cache[key]
                config = settings_contract(run['settings'] if settings is None else settings)
                try:
                    value = self._run_locked(candidates[candidate_id] if candidate_id else revision, candidate_data, config, parentRunId=runId, cancel=cancel, validationJobId=ident)
                    job['childRunIds'].append(value['id'])
                except Exception as error:
                    if getattr(error, 'run_id', None): job['childRunIds'].append(error.run_id)
                    checkpoint()
                    raise
                checkpoint()
                return {'runId': value['id'], 'status': value['status'], 'metrics': value['report']['metrics'],
                        'settings': value['settings'], 'dataHash': value['manifest']['dataHash'],
                        'detail': 'Full report retained as an individual run; retrieve with get_run/export.'}
            try:
                checkpoint()
                if method == 'monte_carlo':
                    result = monte_carlo(run['report'], cancel=checkpoint, **options)
                elif method == 'parameter_grid':
                    from optimizer.parameter_grid import evaluate_grid
                    def grid_progress(results):
                        job['candidateResults'] = results
                    def grid_calculate(plan):
                        checkpoint()
                        candidate = self.save_strategy(plan['definition'], revision['id'])
                        candidates[candidate['id']] = candidate
                        try:
                            value = calculate(candidate_id=candidate['id'])
                        except Exception as error:
                            error.revision_id = candidate['id']
                            raise
                        manifest = self.get_run(value['runId'])['manifest']
                        return {**value, 'revisionId': candidate['id'],
                                'engineVersion': manifest['engineVersion'], 'runtimeVersion': manifest['runtimeVersion']}
                    result = evaluate_grid(grid_plans, grid_calculate, checkpoint, grid_progress)
                    result.update(baselineReturnPct=run['report']['metrics']['totalReturnPct'],
                                  deadlineSeconds=PARAMETER_GRID_TIMEOUT_SECONDS)
                    best = result['bestCandidateIndex']
                    provenance = ('engineVersion', 'runtimeVersion', 'dataHash')
                    result['baselineComparable'] = all(item['status'] == 'succeeded' and
                        all(isinstance(run['manifest'].get(key), str) and bool(run['manifest'][key]) and
                            item['result'].get(key) == run['manifest'][key] for key in provenance)
                        for item in result['results'])
                    result['bestCandidateImproved'] = (result['results'][best-1]['result']['metrics']['totalReturnPct'] > result['baselineReturnPct']) if best and result['baselineComparable'] else None
                    if not result['baselineComparable']:
                        result['comparisonWarning'] = '일부 후보가 완료되지 않았거나 원본과 계산 버전·시세 정보가 달라 개선 여부를 판단할 수 없습니다.'
                elif method == 'walk_forward':
                    result = walk_forward(data['bars'], list(candidates), lambda candidate_id, bars: calculate(bars=bars, candidate_id=candidate_id),
                                          trainBars=options['trainBars'], testBars=options['testBars'], cancel=checkpoint)
                    result['candidateHashes'] = {i: r['hash'] for i, r in candidates.items()}
                    result['deadlineSeconds'] = WALK_FORWARD_TIMEOUT_SECONDS
                elif method == 'out_of_sample':
                    result = out_of_sample(data['bars'], lambda bars: calculate(bars=bars), **options)
                else:
                    result = cost_sensitivity(run['settings'], lambda settings: calculate(settings=settings))
                checkpoint()
                if method in ('out_of_sample', 'walk_forward'):
                    result.update(holdoutAccessed=True, holdoutContaminated=True, trialCount=self.store.count_by_dataset('run', run['datasetId']), selectionWarning='원본 전략은 전체 기간을 이미 평가에 사용했습니다. 이 결과는 사후 시계열 분할이며 독립적인 미관측 OOS 성과가 아닙니다.')
                if method == 'walk_forward':
                    result['fullSnapshotRunCount'] = result.pop('trialCount')
                    result.update(candidateCount=len(candidates), foldCount=len(result['folds']), childRunCount=len(job['childRunIds']),
                                  selectionEffortScope='Current validation only; fullSnapshotRunCount excludes partition runs. Prior validation selection effort is not aggregated.')
                record = {'id': uuid.uuid4().hex, 'jobId': ident, 'runId': runId, 'method': method, 'options': options, 'result': result, 'createdAt': now()}
                self.store.put('validation', record['id'], record)
                job.update(status='succeeded', validationId=record['id'], finishedAt=now(), elapsedSeconds=round(time.monotonic()-started, 6))
                self.store.finish_run(ident, job, kind='validation-job')
                return record
            except (_ValidationCancelled, Exception) as error:
                job.update(status='failed' if timed_out.is_set() else 'cancelled' if cancel.is_set() else 'failed', finishedAt=now(), elapsedSeconds=round(time.monotonic()-started, 6),
                           error='검증 전체 시간 한도를 넘었습니다.' if timed_out.is_set() else '검증이 취소되었습니다.' if cancel.is_set() else '검증 실행에 실패했습니다.')
                self.store.finish_run(ident, job, kind='validation-job')
                if isinstance(error, ValueError) and not cancel.is_set():
                    raise ValueError(str(error)[:500]) from None
                raise ValueError(job['error']) from None
            finally:
                if timer is not None: timer.cancel()
                with self._mutex: self._active.pop(ident, None)

    def get_validation_job(self, jobId):
        """Recover bounded grid progress from immutable plan and linked child runs."""
        job = self.store.get('validation-job', jobId)
        if job.get('method') != 'parameter_grid':
            return job
        plan = job.get('candidatePlan', [])
        if not isinstance(plan, list) or not 1 <= len(plan) <= 12:
            raise ValueError('Invalid parameter grid plan')
        results = [{**copy.deepcopy(item), 'status': 'pending'} for item in plan]
        by_hash = {item['strategyHash']: item for item in results}
        if len(by_hash) != len(results):
            raise ValueError('Ambiguous parameter grid plan')
        children = self.store.validation_job_runs(jobId)
        for child in children:
            revision = self.get_strategy(child['revisionId'])
            item = by_hash.get(revision['hash'])
            if item is None or item['status'] != 'pending':
                raise ValueError('Child does not match parameter grid plan')
            value = self.get_run(child['id'])
            item.update(status=value['status'], revisionId=revision['id'], runId=value['id'])
            if value['status'] == 'succeeded':
                item['result'] = {'runId': value['id'], 'revisionId': revision['id'], 'status': 'succeeded',
                                  'metrics': value['report']['metrics'], 'settings': value['settings'],
                                  'dataHash': value['manifest']['dataHash'],
                                  'engineVersion': value['manifest']['engineVersion'],
                                  'runtimeVersion': value['manifest']['runtimeVersion']}
            elif value.get('error'):
                item['error'] = value['error']
        # A candidate can fail before a child exists (for example revision storage).
        # Preserve that persisted failure rather than reconstructing it as pending.
        saved_results = job.get('candidateResults', [])
        if not isinstance(saved_results, list) or len(saved_results) > len(plan):
            raise ValueError('Invalid saved parameter grid progress')
        seen = set()
        for saved in saved_results:
            index = saved.get('index') if isinstance(saved, dict) else None
            if type(index) is not int or not 1 <= index <= len(plan) or index in seen:
                raise ValueError('Invalid saved parameter grid index')
            seen.add(index)
            item = results[index - 1]
            if digest(saved.get('parameterValues')) != digest(item['parameterValues']) or saved.get('status') not in ('succeeded', 'failed'):
                raise ValueError('Saved candidate does not match parameter grid plan')
            if saved.get('revisionId') and self.get_strategy(saved['revisionId'])['hash'] != item['strategyHash']:
                raise ValueError('Saved revision does not match parameter grid plan')
            saved_result = saved.get('result', {})
            if not isinstance(saved_result, dict):
                raise ValueError('Invalid saved candidate result')
            saved_run = saved.get('runId') or saved_result.get('runId')
            if item['status'] != 'pending':
                if saved['status'] != item['status'] or saved_run != item['runId']:
                    raise ValueError('Saved candidate disagrees with child run')
            else:
                if saved['status'] != 'failed' or saved_run is not None:
                    raise ValueError('Saved candidate references a missing child run')
                item.update(status='failed', error='candidate execution failed')
                if saved.get('revisionId'):
                    item['revisionId'] = saved['revisionId']
        if job.get('status') not in ('queued', 'running'):
            for item in results:
                if item['status'] == 'pending':
                    item['status'] = 'not_run'
        baseline = self.get_run(job['runId'])
        return {**job, 'childRunIds': [c['id'] for c in children], 'candidateResults': results,
                'candidateCount': len(plan), 'baselineReturnPct': baseline['report']['metrics']['totalReturnPct']}

    def create_research(self, source, purpose, trainFraction=.7, researchId=None):
        from .research import create
        return create(self, source, purpose, trainFraction, researchId)

    def prepare_research_training(self, researchId):
        from .research import prepare_training
        return prepare_training(self, researchId)

    def freeze_research(self, researchId):
        from .research import freeze
        return freeze(self, researchId)

    def evaluate_research(self, researchId):
        from .research import evaluate
        return evaluate(self, researchId)

    def cancel_research(self, researchId, phase):
        from .research import cancel
        return cancel(self, researchId, phase)

    def get_research(self, researchId):
        from .research import get
        return get(self, researchId)

    def list_research(self, limit=100, source=None):
        from .research import list_sessions
        return list_sessions(self, limit, source)

    def get_validation(self, validationId):
        return self.store.get('validation', validationId)

    def export_report(self, runId, format='json'):
        from reports import export_run
        return export_run(self.get_run(runId), format)

    def preview_ai_generation(self, requestId):
        from .ai_generation import preview
        return preview(self, requestId)

    def generate_ai_candidates(self, requestId, promptHash, consentVersion):
        from .ai_generation import generate
        return generate(self, requestId, promptHash, consentVersion)

    def create_ai_request(self, runId, purpose, requestId=None):
        from .ai_requests import create
        return create(self, runId, purpose, requestId)

    def list_ai_requests(self):
        from .ai_requests import list_requests
        return list_requests(self)

    def get_ai_request(self, requestId):
        from .ai_requests import read
        return read(self, requestId)

    def submit_ai_candidates(self, requestId, candidates):
        from .ai_requests import submit
        return submit(self, requestId, candidates)

    def cancel_ai_request(self, requestId):
        from .ai_requests import cancel
        return cancel(self, requestId)

    def reset_local_data(self, confirm=False):
        """Delete all locally stored strategies, datasets, runs, validations, research and AI records.

        Explicit confirm=True is required. Refused while any job runs in this process or another local
        process holds the engine/AI lock. In-memory market/AI session credentials are not stored data.
        """
        if confirm is not True:
            raise ValueError('로컬 데이터 초기화는 confirm 값을 true 로 명시해야 합니다.')
        with self._mutex:
            active = list(self._active)
        if active:
            raise ValueError('실행 중인 작업이 있어 로컬 데이터를 초기화할 수 없습니다. 완료 또는 취소 후 다시 시도하세요.')
        if self._ai_studio.get_ai_connection().get('busy'):
            raise ValueError('AI 생성이 진행 중이어서 로컬 데이터를 초기화할 수 없습니다. 완료 후 다시 시도하세요.')
        try:
            removed = self.store.clear()
        except ValueError as error:
            if str(error) in ('Another local run is active', 'Another AI generation is active'):
                raise ValueError('다른 로컬 프로세스가 작업을 실행 중이어서 초기화할 수 없습니다. 완료 후 다시 시도하세요.') from None
            raise
        return {'cleared': True, 'removedObjects': removed, 'storagePath': str(self.store.path), 'storageEncrypted': False,
                'clearedAt': now(),
                'notice': '로컬 저장소의 전략·시세 snapshot·실행 결과·검증·연구·AI 요청 기록을 모두 삭제했습니다. 복구할 수 없습니다.'}

    def capabilities(self):
        generation = {'configured': self._ai_provider is not None, 'available': False,
                      'reason': 'PROVIDER_NOT_CONFIGURED', 'scope': 'existing-successful-dsl-improvement',
                      'modelInvocationVerified': False}
        if self._ai_provider is not None:
            from .ai_cli_provider import ProviderError
            try:
                self._ai_provider.validate()
                generation.update(available=True, reason=None)
            except ProviderError as error:
                generation['reason'] = str(error)
        return {'version': VERSION, 'platform': platform.system(), 'pythonStrategy': _python_available(),
                'aiStudio': self.ai_studio_enabled,
                'dsl': True, 'liveOrders': False, 'aiGeneration': generation, 'ai': 'external MCP host; local proposals are rule-based',
                'history': '국내: 조회 시작일과 종료일 차이 최대 365일. 미국: OQ/NY/AX 거래소와 시세 구분 0/1을 지정하며 요청 기간은 달력 기준 최대 3년입니다. 통화 USD, 시간대 America/New_York. 공급자 응답의 전체 기간·완전성은 보장하지 않으며 이어 조회는 지원하지 않습니다. 가져온 일봉 데이터는 최대 5,000개입니다.',
                'release': RELEASE, 'storage': 'local SQLite, not encrypted',
                # R-09 고지: 실제 저장 위치와 암호화 여부를 그대로 알린다. 초기화는 reset_local_data({"confirm": true}).
                'storagePath': str(self.store.path), 'storageEncrypted': False}

    def dispatch(self, action, arguments):
        actions = {'templates', 'validate_strategy', 'save_strategy', 'list_strategies', 'get_strategy', 'import_data', 'import_csv',
                   'demo_data', 'fetch_data', 'list_data', 'run', 'get_run', 'list_runs', 'diagnose', 'experiment',
                   'propose', 'compare', 'validate_robustness', 'get_validation', 'get_validation_job', 'create_research', 'prepare_research_training', 'freeze_research', 'evaluate_research', 'cancel_research', 'get_research', 'list_research', 'export_report', 'capabilities', 'reset_local_data', 'cancel', 'create_ai_request', 'get_ai_request', 'submit_ai_candidates', 'cancel_ai_request', 'list_ai_requests', 'preview_ai_generation', 'generate_ai_candidates'}
        if action not in actions or not isinstance(arguments, dict): raise ValueError('지원하지 않는 플랫폼 요청')
        return getattr(self, action)(**arguments)
