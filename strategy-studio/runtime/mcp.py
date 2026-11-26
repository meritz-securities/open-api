"""Narrow platform tools added to the existing Meritz local stdio server."""
import asyncio
from pathlib import Path
from typing import Annotated, Literal
import anyio.to_thread
from pydantic import Field
from fastmcp.exceptions import ToolError
from .service import StrategyRuntime


def register_platform(mcp, catalog, registry, runtime=None):
    service = runtime or StrategyRuntime(catalog=catalog, registry=registry)
    names = []
    async def invoke(action, arguments):
        try:
            result = await anyio.to_thread.run_sync(lambda: service.dispatch(action, arguments))
            if action in ('run', 'experiment', 'get_run') and result.get('status') == 'succeeded':
                report = result['report']
                result = {k: v for k, v in result.items() if k != 'report'}
                result['report'] = {k: report[k] for k in ('strategy', 'metrics', 'assumptions', 'warnings') if k in report}
                result['detail'] = {'tradeCount': len(report['trades']), 'tradePreview': report['trades'][:5],
                                    'complete': False, 'notice': 'Summary only. Use Studio or report export for full immutable detail.'}
            return result
        except ValueError as error:
            raise ToolError(str(error)[:500]) from None
        except Exception:
            raise ToolError('로컬 전략 요청을 처리하지 못했습니다. 저장 공간·입력·실행 환경을 확인하세요.') from None

    def tool(name, read_only=True, world=False):
        names.append(name)
        return mcp.tool(name=name, annotations={'readOnlyHint':read_only,'destructiveHint':False,'openWorldHint':world})

    @tool('meritz_strategy_templates')
    async def templates() -> dict:
        """지원하는 공통 DSL 템플릿과 실제 기능 범위. 자연어는 AI host가 DSL/코드로 작성합니다."""
        from .dsl import strategy_schema
        return {**(await invoke('templates', {})), 'schema':strategy_schema(), 'capabilities':service.capabilities()}

    @tool('meritz_strategy_list')
    async def list_strategies(revisionId: str | None = None) -> dict:
        """최근 전략 목록 또는 revisionId로 저장된 단일 전략을 조회합니다. 계정·키는 포함하지 않습니다."""
        if revisionId is not None:
            return await invoke('get_strategy', {'revisionId': revisionId})
        return await invoke('list_strategies', {})

    @tool('meritz_strategy_validate')
    async def validate(definition: dict) -> dict:
        """JSON DSL 또는 on_bar Python 정의를 검증합니다. 검증만으로 수익성/안전을 인증하지 않습니다."""
        return await invoke('validate_strategy', {'definition':definition})

    @tool('meritz_strategy_save', False)
    async def save(definition: dict, parentRevisionId: str | None = None) -> dict:
        """전략을 로컬 immutable revision으로 저장합니다. 원본을 덮어쓰지 않습니다."""
        return await invoke('save_strategy',{'definition':definition,'parentRevisionId':parentRevisionId})

    @tool('meritz_platform_data', False, True)
    async def data(source: Literal['demo','meritz','imported'], symbol: str | None=None, startDate: str | None=None,
                   endDate: str | None=None, account: str | None=None, bars: list[dict] | None=None, metadata: dict | None=None,
                   market: Literal['KR','US'] | None=None, exchange: Literal['OQ','NY','AX'] | None=None,
                   quoteMode: Literal['0','1'] | None=None) -> dict:
        """읽기전용 snapshot. meritz: market 생략/KR=국내365일, US=exchange OQ/NY/AX·quoteMode 0(실시간 요청)/1(지연 요청) 필수, USD·뉴욕 현지일 기준. 미국 최대3년은 앱 요청 한도이며 제공자 기간·응답 완전성은 보장하지 않음, 연속조회 없음. 기존 고객 계정 사용. demo=합성, imported=검증 OHLC."""
        if source == 'demo':
            if any(v is not None for v in (symbol,startDate,endDate,account,bars,metadata,market,exchange,quoteMode)): raise ToolError('합성 시세에 실제 조회 조건을 혼합할 수 없습니다.')
            return await invoke('demo_data',{})
        if source == 'meritz':
            if not all((symbol,startDate,endDate)) or bars is not None or metadata is not None: raise ToolError('메리츠 시세 종목·기간을 명시하세요.')
            return await invoke('fetch_data',{'symbol':symbol,'startDate':startDate,'endDate':endDate,'account':account,
                                             'market':market or 'KR','exchange':exchange,'quoteMode':quoteMode})
        if any(v is not None for v in (symbol,startDate,endDate,account,market,exchange,quoteMode)) or bars is None: raise ToolError('imported는 bars와 metadata만 사용합니다.')
        return await invoke('import_data',{'bars':bars,'source':metadata or {'kind':'imported','label':'사용자 제공 OHLC'}})

    @tool('meritz_platform_backtest', False)
    async def run(revisionId: str, datasetId: str, settings: dict | None=None) -> dict:
        """공유 Runtime에서 저장된 전략·snapshot을 실제로 실행하고 결과를 로컬 저장합니다. 실주문 없음."""
        return await invoke('run',{'revisionId':revisionId,'datasetId':datasetId,'settings':settings})

    @tool('meritz_platform_runs')
    async def runs(runId: str | None=None) -> dict:
        """저장된 실행 목록 또는 실행 결과·provenance를 조회합니다."""
        return await invoke('get_run',{'runId':runId}) if runId else await invoke('list_runs',{})

    @tool('meritz_strategy_diagnose')
    async def diagnose(runId: str) -> dict:
        """호스트 계산값에 근거한 Strategy Doctor. AI는 수치를 새로 계산하지 말고 반환 근거를 설명하세요."""
        return await invoke('diagnose',{'runId':runId})

    @tool('meritz_strategy_propose')
    async def propose(runId: str, request: Annotated[str,Field(max_length=2000)]='위험 감소 후보') -> dict:
        """규칙 기반 후보 3개를 제시합니다. AI 생성이 아닙니다. AI host는 별도 후보를 작성해 experiment로 검증할 수 있습니다."""
        return await invoke('propose',{'runId':runId,'request':request})

    @tool('meritz_strategy_experiment', False)
    async def experiment(runId: str, definition: dict | None=None, settings: dict | None=None) -> dict:
        """원본을 보존하고 동일 snapshot에서 후보 전략/비용을 실제 재실행합니다. 손실 후보도 숨기지 마세요."""
        return await invoke('experiment',{'runId':runId,'definition':definition,'settings':settings})

    @tool('meritz_platform_compare')
    async def compare(runIds: Annotated[list[str],Field(min_length=2,max_length=12)]) -> dict:
        """실제 실행된 결과만 비교합니다. 데이터/설정 동등성 표시를 함께 확인하세요."""
        return await invoke('compare',{'runIds':runIds})

    @tool('meritz_strategy_robustness', False)
    async def robustness(runId: str, method: Literal['monte_carlo','out_of_sample','cost_sensitivity','walk_forward','parameter_grid'], options: dict | None=None) -> dict:
        """seed고정 bootstrap, 기간 분할, 비용 또는 Walk-forward 재실행.
        Walk-forward options: trainBars/testBars 정수, candidateRevisionIds 추가 최대3개. 학습 수익률 선택이며 이미 관측된 데이터의 사후 분석.
        parameter_grid는 DSL 전략 전용 조건 값 비교입니다. options.axes는 서로 다른 path의 1..3항목이며 각 values에는 중복 없는 유한한 숫자 1..12개를 지정합니다.
        전체 조합은 최대12개이며 maxEvaluations(1..12, 기본12)를 초과하면 일부만 실행하지 않고 요청을 거부합니다.
        path는 quantity, risk.stopLossPct, risk.takeProfitPct 또는 현재 DSL의 상수.value/지표.period만 지원합니다.
        예: entry.right.value, entry.conditions.0.right.value, exit.left.period. conditions의 숫자는 0부터 시작하는 배열 위치입니다.
        기존 전략은 meritz_strategy_list(revisionId) 도구로 확인하고 실제 존재하는 상수·지표 경로만 지정하세요. Python 코드는 지원하지 않습니다.
        동일 시세·비용으로 실패·악화 후보도 보존하며 최고 후보는 미래 성과 보장이 아닙니다.
        전체120초의 협조적 실행 한도이며 OS의 강제 종료 시간이 아닙니다. meritz_platform_cancel(jobId) 도구로 같은 MCP 프로세스에서 실행 중인 작업을 취소할 수 있습니다.
        """
        return await invoke('validate_robustness',{'runId':runId,'method':method,'options':options})

    @tool('meritz_strategy_validation_job')
    async def validation_job(jobId: str) -> dict:
        """저장된 검증 작업을 조회합니다. 조건 값 비교의 취소·중단·실패 및 미실행 조합도 보존된 상태로 확인합니다."""
        return await invoke('get_validation_job', {'jobId': jobId})

    @tool('meritz_strategy_validation_result')
    async def validation_result(validationId: str) -> dict:
        """저장된 검증 결과를 재조회합니다. 구간별 개별 실행은 반환된 runId로 조회합니다."""
        return await invoke('get_validation', {'validationId': validationId})

    @tool('meritz_platform_export')
    async def export(runId: str, format: Literal['html','json','csv']='html') -> dict:
        """전략 hash·데이터·비용·거래 포함 보고서 content를 반환합니다. 임의 경로에 파일을 쓰지 않습니다."""
        return await invoke('export_report',{'runId':runId,'format':format})

    @tool('meritz_platform_cancel', False)
    async def cancel(runId: str | None=None, jobId: str | None=None) -> dict:
        """이 MCP 프로세스에서 실행 중인 전략의 취소를 요청합니다. 완료 작업은 변경하지 않습니다."""
        return await invoke('cancel',{'runId':runId,'jobId':jobId})

    @tool('meritz_ai_request_create', False)
    async def ai_request_create(runId: str, purpose: Annotated[str,Field(min_length=1,max_length=2000)], requestId: str | None=None) -> dict:
        """완료 실행의 개선 목적을 저장합니다. 모델을 호출하지 않습니다. requestId는 재시도용32자리hex입니다."""
        return await invoke('create_ai_request', {'runId':runId,'purpose':purpose,'requestId':requestId})

    @tool('meritz_ai_request_get')
    async def ai_request_get(requestId: str | None=None) -> dict:
        """외부 AI 호스트가 개선 목적·기준 hash·제출 결과를 읽습니다. ID 생략시 최근100개 요청 요약. 자동 LLM 연결은 없습니다."""
        return await invoke('get_ai_request', {'requestId':requestId}) if requestId is not None else await invoke('list_ai_requests', {})

    @tool('meritz_ai_candidates_submit', False)
    async def ai_candidates_submit(requestId: str, candidates: Annotated[list[dict],Field(min_length=1,max_length=3)]) -> dict:
        """호스트가 작성한 후보 정의를 명시적으로 제출·검증·가상실행·비교합니다. 같은 요청은 한번만 실행합니다. 실패도 보존하며 실주문하지 않습니다. 실행중 취소는 meritz_platform_cancel의 jobId=ai-요청ID를 사용합니다."""
        return await invoke('submit_ai_candidates', {'requestId':requestId,'candidates':candidates})

    @tool('meritz_ai_request_cancel', False)
    async def ai_request_cancel(requestId: str) -> dict:
        """미제출 요청을 취소하거나 현재 프로세스의 제출 실행 취소를 요청합니다. 완료 결과는 변경하지 않습니다."""
        return await invoke('cancel_ai_request', {'requestId':requestId})

    @tool('meritz_research_create', False)
    async def research_create(source: dict, purpose: str, trainFraction: float=.7, researchId: str | None=None) -> dict:
        """기간을 나눠 개선·검증할 계획만 저장합니다. 엔진·모델을 실행하지 않습니다.
        source는 {runId} 또는 {revisionId,datasetId,settings?} 중 하나만 지정합니다.
        새 흐름은 DSL 전용이며 trainFraction은 .2~.8, 각 기간은 최소2봉입니다.
        같은 researchId와 같은 입력은 재사용하고 변경된 입력은 거부합니다.
        기존 전체 실행 또는 외부 관측 이력이 있을 수 있어 완전한 블라인드 검증이 아닙니다.
        """
        return await invoke('create_research', {'source':source,'purpose':purpose,'trainFraction':trainFraction,'researchId':researchId})

    @tool('meritz_research_prepare', False)
    async def research_prepare(researchId: str) -> dict:
        """개선 기간의 원본을 한 번 계산하고 그 결과에 연결된 기존 AI 요청을 준비합니다.
        반환된 소유 요청 ID만 기존 AI 도구로 생성·제출합니다. 중단된 실행은 자동 재실행하지 않습니다.
        """
        return await invoke('prepare_research_training', {'researchId':researchId})

    @tool('meritz_research_get')
    async def research_get(researchId: str) -> dict:
        """저장된 기간 분할·개선·후보 확정·검증 상태를 조회합니다. 엔진·모델을 실행하거나 작업을 복구 실행하지 않습니다."""
        return await invoke('get_research', {'researchId':researchId})

    @tool('meritz_research_list')
    async def research_list(limit: Annotated[int, Field(ge=1,le=100,strict=True)]=100, source: dict | None=None) -> dict:
        """최근 기간 분리 작업 최대100개를 조회합니다. source는 선택적으로 {runId} 또는 {revisionId?,datasetId} 필터입니다. 원시 시세는 반환하지 않습니다."""
        return await invoke('list_research', {'limit':limit,'source':source})

    @tool('meritz_research_freeze', False)
    async def research_freeze(researchId: str) -> dict:
        """원본과 제출 후보 모두 개선 기간에서 성공했을 때 후보와 선택 결과를 확정합니다.
        후보를 하나도 제출하지 않았고 개선 기간 원본 실행이 완료됐으면 원본만으로 확정합니다(candidateCount=0, selection='original').
        개선 기간 수익률로만 선택하며 동률은 원본, 제출 순서입니다. 검증 기간은 아직 계산하지 않습니다.
        """
        return await invoke('freeze_research', {'researchId':researchId})

    @tool('meritz_research_evaluate', False)
    async def research_evaluate(researchId: str) -> dict:
        """확정된 선택 후보와 원본만 검증 기간에서 각각 새 자금·보유량으로 평가합니다(최대2회).
        검증 결과로 승자를 다시 고르지 않습니다. 같은 작업 재요청은 저장 상태만 반환하고 중단·실패 실행을 재시도하지 않습니다.
        120초 제한은 중단 확인 지점에서 적용되며 강제 프로세스 종료 시각을 보장하지 않습니다.
        """
        return await invoke('evaluate_research', {'researchId':researchId})

    @tool('meritz_research_cancel', False)
    async def research_cancel(researchId: str, phase: Literal['training','evaluation']) -> dict:
        """해당 작업의 개선/검증 단계 취소 의도를 저장합니다. 다른 작업은 취소하지 않습니다.
        다른 프로세스의 현재 계산은 다음 확인 지점까지 계속될 수 있습니다. 완료된 성공 결과는 취소 상태로 바꾸지 않습니다.
        """
        return await invoke('cancel_research', {'researchId':researchId,'phase':phase})

    studio_servers = []
    @tool('meritz_studio_open', False)
    def studio_open() -> dict:
        """같은 MCP 계정·Runtime으로 로컬 Studio를 엽니다. 키를 복사하지 않으며 브라우저 실행 링크만 전달합니다."""
        from .server import create_server
        import threading
        import webbrowser
        if not studio_servers:
            server=create_server(service)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            studio_servers.append(server)
        server=studio_servers[0]
        opened=webbrowser.open(server.issue_ticket())
        return {'opened':bool(opened),'url':f'http://127.0.0.1:{server.server_port}/','note':'인증된 실행 링크는 브라우저에만 전달됩니다. 이 일반 URL만으로는 고객 자료에 접근할 수 없습니다.'}

    return names
