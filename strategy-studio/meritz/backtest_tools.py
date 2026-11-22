"""Local backtest tools reuse the existing credential registry and API client."""
from __future__ import annotations

import asyncio
import copy
from collections import OrderedDict
from pathlib import Path
from typing import Annotated
from typing import Literal

import anyio.to_thread
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from pydantic import Field

from .backtest_engine import run_demo, run_backtest
from .backtest_data import DataError, load_market_data
from .strategy_runner import StrategySession, StrategyError, validate_code, ensure_supported, sandbox_self_test
from .strategy_engine import run_strategy
from .backtest_engine import demo_candles

UI_URI = "ui://meritz/backtest.html"
TOOL_NAMES = ["meritz_backtest_demo", "meritz_backtest_run", "meritz_backtest_compare", "meritz_backtest_trade",
              "meritz_backtest_validate", "meritz_backtest_code"]
Period = Annotated[int, Field(ge=2, le=120)]
RunID = Annotated[str, Field(pattern=r"^[a-f0-9]{24}$")]


def register_backtest(mcp, catalog, registry):
    # Memory is bounded and private to this local server instance. No disk paths are tool inputs.
    cache: OrderedDict[str, tuple[dict, list | None, list[str]]] = OrderedDict()
    gate = asyncio.Lock()

    def save(report, candles=None, data_warnings=None):
        cache[report["runId"]] = (copy.deepcopy(report), copy.deepcopy(candles), list(data_warnings or []))
        cache.move_to_end(report["runId"])
        while len(cache) > 16:
            cache.popitem(last=False)
        return report

    def lookup(run_id):
        if run_id not in cache:
            raise ToolError("실행 결과가 없거나 보관 한도를 넘어 만료되었습니다. 다시 실행해 주세요.")
        return cache[run_id]

    def periods(fast, slow):
        if fast >= slow:
            raise ToolError("fast는 slow보다 작아야 합니다.")

    def response(payload):
        reports = [payload[k] for k in ("report", "baseline", "candidate") if k in payload]
        lines = []
        for report in reports:
            metrics = report['metrics']
            st = report['strategy']
            title = f"SMA {st['fast']}/{st['slow']}" if 'fast' in st else f"사용자 코드 {st['codeHash'][:12]}"
            lines.append(f"{report['source']['label']} · {title} · "
                         f"수익률 {metrics['totalReturnPct']:.2f}% · 최대낙폭 {metrics['maxDrawdownPct']:.2f}% · "
                         f"가상 체결 {metrics['tradeCount']}건 · runId={report['runId']}")
            lines.extend(report['warnings'])
        return ToolResult(content=[TextContent(type='text', text='\n'.join(lines))], structured_content=payload)

    @mcp.resource(UI_URI, mime_type="text/html;profile=mcp-app",
                  app={"csp": {"connectDomains": [], "resourceDomains": []}})
    def report_ui() -> str:
        return Path(__file__).with_name("ui").joinpath("backtest.html").read_text(encoding="utf-8")

    @mcp.tool(name=TOOL_NAMES[0], title="합성 시세 백테스트", app={"resourceUri": UI_URI},
              annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    async def demo(fast: Period = 5, slow: Period = 20) -> ToolResult:
        """합성 시세로 보고서를 시연합니다. 실제 종목·고객 시세가 아닙니다."""
        periods(fast, slow)
        return response({"report": save(run_demo(fast, slow))})

    @mcp.tool(name=TOOL_NAMES[1], title="메리츠 시세 백테스트", app={"resourceUri": UI_URI},
              annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True})
    async def run(symbol: Annotated[str, Field(pattern=r"^[0-9]{6}$")],
                  startDate: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")],
                  endDate: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")],
                  account: Annotated[str | None, Field(max_length=80)] = None,
                  fast: Period = 5, slow: Period = 20) -> ToolResult:
        """등록된 고객 계좌의 키로 메리츠 국내 일봉을 조회해 SMA 전략을 검증합니다.

        계좌가 여러 개면 meritz_info로 이름을 확인하여 account를 지정하세요. API 키를 인자로 넣지 마세요.
        TJ/Y 기준, 최대 1년. 실주문 없음. 세금·유동성·기업행사 미반영 연구용 결과입니다.
        """
        periods(fast, slow)
        if gate.locked():
            raise ToolError("다른 시세 조회가 진행 중입니다. 완료 후 다시 실행해 주세요.")
        async with gate:
            try:
                data = await anyio.to_thread.run_sync(
                    lambda: load_market_data(catalog, registry, symbol, startDate, endDate, account))
                settings = {"fast": fast, "slow": slow, "initialCash": 1_000_000,
                            "feeBps": 15, "slippageBps": 10}
                report = run_backtest(data["candles"], settings, data["source"])
                report["warnings"] = list(dict.fromkeys(report["warnings"] + data.get("warnings", [])))
                return response({"report": save(report, data["candles"], data.get("warnings", []))})
            except ToolError:
                raise
            except DataError as error:
                raise ToolError(str(error)) from None
            except Exception:
                # API exceptions can contain bodies, request context or credentials.
                raise ToolError("시세 조회 또는 검증에 실패했습니다. 계좌 설정·종목·기간·봉 수를 확인해 주세요. 합성 시세로 대체하지 않았습니다.") from None

    @mcp.tool(name=TOOL_NAMES[2], title="백테스트 조건 비교", app={"resourceUri": UI_URI},
              annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    async def compare(runId: RunID, fast: Period = 8, slow: Period = 30) -> ToolResult:
        """기존 실행과 동일한 시세·비용으로 이동평균 기간을 바꿔 비교합니다. 시세 재조회 없음."""
        periods(fast, slow)
        baseline, candles, data_warnings = lookup(runId)
        if baseline['strategy'].get('interface') == 'on_bar_v1':
            raise ToolError('사용자 코드 결과는 수정한 코드를 다시 실행해 비교하세요. SMA 조건으로 대체하지 않습니다.')
        try:
            if candles is None:
                candidate = run_demo(fast, slow)
            else:
                a = baseline["assumptions"]
                candidate = run_backtest(candles, {"fast": fast, "slow": slow,
                    "initialCash": a["initialCash"], "feeBps": a["feeBps"],
                    "slippageBps": a["slippageBps"]}, baseline["source"])
                candidate["warnings"] = list(dict.fromkeys(candidate["warnings"] + data_warnings))
        except ValueError:
            raise ToolError("비교 조건에 필요한 시세가 부족하거나 조건이 잘못되었습니다.") from None
        save(candidate, candles, data_warnings)
        return response({"baseline": copy.deepcopy(baseline), "candidate": candidate})

    @mcp.tool(name=TOOL_NAMES[3], title="가상 체결 근거",
              annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    async def trade(runId: RunID,
                    tradeId: Annotated[str, Field(pattern=r"^trade-[1-9][0-9]{0,4}$")]) -> dict:
        """선택한 가상 체결의 신호일·가격·수량·비용·기록된 매매 근거를 조회합니다."""
        report, _, _ = lookup(runId)
        for item in report["trades"]:
            if item["id"] == tradeId:
                return {"trade": copy.deepcopy(item)}
        raise ToolError("해당 실행에 거래가 없습니다.")

    @mcp.tool(name=TOOL_NAMES[4], title='사용자 전략 코드 확인',
              annotations={'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False})
    async def validate(code: Annotated[str, Field(max_length=65536)]) -> dict:
        """on_bar(ctx) Python 전략의 문법·진입점을 검사합니다. 코드를 실행하지 않으며 보안 인증이 아닙니다."""
        return validate_code(code)

    @mcp.tool(name=TOOL_NAMES[5], title='내 전략 코드 백테스트', app={'resourceUri':UI_URI},
              annotations={'readOnlyHint':False,'destructiveHint':False,'openWorldHint':True})
    async def code_run(code: Annotated[str, Field(max_length=65536)],
                       source: Literal['demo','meritz'],
                       symbol: Annotated[str | None, Field(pattern=r'^[0-9]{6}$')]=None,
                       startDate: Annotated[str | None, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]=None,
                       endDate: Annotated[str | None, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]=None,
                       account: Annotated[str | None, Field(max_length=80)]=None,
                       initialCash: Annotated[float, Field(ge=1,le=1e12,allow_inf_nan=False)]=1_000_000,
                       feeBps: Annotated[float, Field(ge=0,le=1000,allow_inf_nan=False)]=15,
                       slippageBps: Annotated[float, Field(ge=0,le=1000,allow_inf_nan=False)]=10) -> ToolResult:
        """고객 Python 코드를 격리 실행 환경에서 백테스트하고 Claude 보고서를 표시합니다.

        단일파일 def on_bar(ctx): → {'action':'buy'|'sell'|'hold','quantity':선택정수,'reason':선택문자열}.
        ctx에는 bar, history(현재까지), portfolio(cash/shares), index가 있습니다.
        실제 코드만 받고 경로나 키는 받지 않습니다. 파일은 사용자가 선택해 읽은 내용을 전달하세요.
        source를 반드시 선택하세요. meritz는 symbol·startDate·endDate를 모두 지정해야 고객 계좌키로 시세를 조회합니다.
        demo는 합성 시세이며 symbol·startDate·endDate·account 값을 함께 지정할 수 없습니다.
        표준 라이브러리만 지원. 파일·네트워크·자식프로세스 접근 차단, 최대20초, 메모리는 20ms 간격 감시(하드 상한 아님), 단일종목 일봉 다음봉 시가 가상체결.
        macOS 및 실행기가 동봉된 Windows 지원. 실패시 격리 없는 실행이나 합성 시세로 대체하지 않습니다.
        """
        if source == 'meritz' and any(value is None for value in (symbol, startDate, endDate)):
            raise ToolError('메리츠 시세는 symbol·startDate·endDate를 모두 명시해야 합니다. 임의 종목이나 기간으로 대신 조회하지 않습니다.')
        if source == 'demo' and any(value is not None for value in (symbol, startDate, endDate, account)):
            raise ToolError('합성 시세에는 symbol·startDate·endDate·account를 지정하지 마세요. 실제 시세 조건을 합성 시세에 적용하지 않습니다.')
        validation=validate_code(code)
        if not validation['valid']:
            details='; '.join(f"{e.get('line') or '-'}행: {e['message']}" for e in validation['errors'])
            raise ToolError(details)
        if gate.locked():
            raise ToolError('다른 백테스트가 실행 중입니다. 완료 후 다시 실행하세요.')
        def work():
            ensure_supported()
            # Probe before a network call, but never leave customer code running
            # unmonitored while the host waits for the provider.
            probe = sandbox_self_test()
            if source=='meritz':
                data=load_market_data(catalog,registry,symbol,startDate,endDate,account)
            else:
                data={'candles':demo_candles(),'source':{'kind':'synthetic','label':'합성 일봉 · 실제 종목/시세 아님'},'warnings':[]}
            with StrategySession(code, verified_sandbox=probe) as session:
                report=run_strategy(data['candles'],{'initialCash':initialCash,'feeBps':feeBps,'slippageBps':slippageBps},
                    session.decide,data['source'],{'name':'내 전략','codeHash':validation['codeHash'],'interface':'on_bar_v1'})
                report['warnings']=list(dict.fromkeys(report['warnings']+data['warnings']))
                report['runtime']={'platform':session.sandbox['platform'],'sandbox':session.sandbox['mechanism'],'checks':session.sandbox['checks'],
                    'limits':validation['limits'],'accounting':'host-computed','reproducibility':'코드가 현재시각 등 외부조건을 사용하면 판단이 달라질 수 있습니다.'}
                return report,data
        async with gate:
            try:
                report,data=await anyio.to_thread.run_sync(work)
                return response({'report':save(report,data['candles'],data['warnings'])})
            except (StrategyError,DataError) as error:
                raise ToolError(str(error)) from None
            except ValueError:
                raise ToolError('전략의 주문 판단·수량·시세 조건이 지원 범위를 벗어났습니다. 매수 가능 금액과 보유 수량을 확인하세요.') from None
            except Exception:
                raise ToolError('고객 코드 실행을 완료하지 못했습니다. 안전하지 않은 대체 실행은 하지 않았습니다.') from None

    return TOOL_NAMES.copy()
