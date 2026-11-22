"""Claude-facing local strategy MCP. It deliberately exposes no account/order tools."""
import logging

from fastmcp import FastMCP

from meritz import __version__, load_catalog
from meritz.accounts import Registry
from runtime.mcp import register_platform
from runtime.service import StrategyRuntime


def build_server():
    catalog = load_catalog()
    registry = Registry()
    runtime = StrategyRuntime(catalog=catalog, registry=registry)
    server = FastMCP(
        name="Meritz Strategy Studio",
        instructions=(
            "고객 PC에서 합성·가져온·메리츠 과거 시세로 전략 검증과 백테스트를 수행합니다. "
            "실제 주문·환전 도구는 제공하지 않습니다. 결과는 이 PC에 저장됩니다."
        ),
        version=__version__,
    )
    tools = register_platform(server, catalog, registry, runtime)
    logging.getLogger("meritz.strategy-mcp").info("전략 도구 %d개 · 로컬 MCP", len(tools))
    return server


def main():
    # MCP communication is stdio-only. The explicit Studio tool may start the local UI server.
    build_server().run(transport="stdio")
