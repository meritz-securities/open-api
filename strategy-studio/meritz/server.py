"""메리츠증권 Open API MCP 서버.

메리츠 Open API 를 자연어로 호출할 수 있게 해 준다.
API 규격은 **포털 등록분**(`meritz/data/catalog.json`)을 그대로 쓴다 — 저장소가 포털과
다르면 이용자가 혼란스럽기 때문이다.

실시간(웹소켓)은 이 서버가 다루지 않는다. MCP 는 요청·응답 모델이라 구독 스트림과
맞지 않는다 — 실시간 코드는 `open-api-codegen-mcp` 가 만들어 준다.

실행
  MERITZ_APP_KEY=... MERITZ_APP_SECRET=... uv run python -m meritz.server
  MCP_TYPE=streamable-http uv run python -m meritz.server      # HTTP /mcp

환경변수
  MERITZ_BASE_URL       도메인 직접 지정 (기본은 운영)
  MERITZ_WS_URL         실시간 주소 직접 지정
  MERITZ_READ_ONLY=1    주문·환전을 아예 막는다
"""
from __future__ import annotations

import logging
import sys

from fastmcp import FastMCP

from . import __version__, load_catalog, settings
from .accounts import Registry
from .config import set_read_only_default
from .tools import NO_TOOL, TOOL_OF, register
from .backtest_tools import register_backtest

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("meritz")


def build_server(*, enable_platform=False, platform_runtime=None):
    s = settings()
    catalog = load_catalog()
    registry = Registry()

    guard = []
    if s.read_only:
        guard.append("조회 전용(MERITZ_READ_ONLY=1) — 주문·환전이 막혀 있습니다.")
    else:
        guard.append("주문·환전은 미리보기에서 받은 confirm_token 을 넘겨야 전송됩니다.")

    mcp = FastMCP(
        name="Meritz Open API",
        instructions=(
            "메리츠증권 Open API 서버입니다. "
            "각 도구의 설명에 그 분야의 api_type 목록이 있고, "
            "api_type='describe' 로 부르면 파라미터 설명을 돌려줍니다. "
            "국내 일봉(market_candles_days)은 기본이 KRX 정규장 종가이며 mrkt_div_code 는 생략할 수 있습니다. "
            + " ".join(guard) +
            " 실시간(웹소켓)은 이 서버가 다루지 않습니다 — 코드 생성 MCP 를 쓰세요."
        ),
        version=__version__,
    )

    tools = register(mcp, catalog, registry, s)
    # Cache isolation currently targets the local Desktop stdio connection only.
    if s.transport == "stdio":
        if enable_platform:
            # The platform MCP exposes the persistent shared StrategyRuntime.
            # Do not publish the older in-memory backtest executor alongside it:
            # identical customer code would otherwise have different limits and
            # insufficient-order policies depending on which tool an AI host picks.
            from runtime.mcp import register_platform
            tools += register_platform(mcp, catalog, registry, platform_runtime)
        else:
            # Compatibility path for existing standalone MCP users.
            tools += register_backtest(mcp, catalog, registry)

    @mcp.tool(name="meritz_info",
              description="서버 설정과 API 카탈로그 요약, 알려진 문제를 돌려줍니다.",
              annotations={"readOnlyHint": True, "openWorldHint": False})
    async def info() -> dict:
        # 카탈로그 건수와 도구로 부를 수 있는 건수는 다르다. 둘을 같이 적지 않으면
        # "REST 109건" 을 그대로 "109건을 호출할 수 있다" 로 읽는다.
        exposed = sum(1 for a in catalog.rest() if a["category"] in TOOL_OF)
        return {
            "환경": s.env, "도메인": s.base_url,
            "자격증명": "설정됨" if s.has_credentials else "없음 — MERITZ_APP_KEY/SECRET 필요",
            "조회전용": s.read_only,
            "카탈로그": {"원천": catalog.source, "생성": catalog.generated,
                        "REST": len(catalog.rest()), "실시간": len(catalog.websockets()),
                        "도구로_노출": exposed,
                        "도구_없음": {c: why for c, why in NO_TOOL.items()
                                    if any(a["category"] == c for a in catalog.rest())}},
            "계좌": {"등록": registry.listing(),
                    "기본": registry.default.label,
                    "안내": ("일반 조회는 account 를 생략하면 기본 계좌로 갑니다. 백테스트 조회는 계좌를 명시해야 합니다. "
                           "주문·환전은 생략할 수 없습니다."
                           if registry.many else
                           "계좌가 하나라 account 를 쓰지 않아도 됩니다.")},
            "도구": tools + ["meritz_info"],
            "백테스트": "로컬 stdio에서 사용 가능" if s.transport == "stdio" else "HTTP/SSE에서는 결과 격리 미지원으로 비활성",
            "주의": "실시간은 이 서버가 다루지 않습니다. 코드 생성 MCP 를 쓰세요.",
        }

    log.info("도구 %s · API %d건 · %s(%s)", tools, len(catalog.rest()), s.env, s.base_url)
    if not s.has_credentials:
        log.warning("자격증명이 없습니다. MERITZ_APP_KEY / MERITZ_APP_SECRET 을 설정하세요.")
    return mcp, s


# 모듈 기본값이 이미 조회 전용이다(config._READ_ONLY_DEFAULT).
# 여기서 한 번 더 못을 박는 것은, 테스트나 다른 코드가 기본값을 되돌려 놓은
# 상태로 서버가 뜨는 일을 막기 위해서다.
set_read_only_default(True)


def main(*, enable_platform=False) -> None:
    mcp, s = build_server(enable_platform=enable_platform)
    if s.transport == "stdio":
        mcp.run(transport="stdio")
    elif s.transport in ("sse", "streamable-http"):
        mcp.run(transport=s.transport, host=s.host, port=s.port, path=s.path)
    else:
        log.error("지원하지 않는 MCP_TYPE: %s", s.transport)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("중단")
