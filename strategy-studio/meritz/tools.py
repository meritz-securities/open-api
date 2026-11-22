"""MCP 도구 등록.

카테고리마다 도구 하나를 만든다. 각 도구는 `api_type` 으로 어느 API 를 부를지 정한다.
도구 설명에 그 카테고리의 API 목록을 실어, 모델이 목록을 따로 묻지 않아도 되게 한다.
"""
from __future__ import annotations

import functools
import json
from typing import Any, NoReturn

import anyio.to_thread
from fastmcp.exceptions import ToolError

from .accounts import key_hint
from .catalog import Api, Catalog
from .client import ApiClient, MeritzError
from .safety import blocked, is_state_changing

CATEGORY_KO = {
    "account": "계좌", "domestic_market": "국내 시세(주식·지수)", "trading": "주문",
    "forex": "환전", "reference": "기준정보", "overseas_market": "해외 시세(주식·지수·원자재·환율)",
}
# 카탈로그 카테고리 → 도구 이름
TOOL_OF = {
    "account": "meritz_account",
    "domestic_market": "meritz_market",
    # 해외 시세는 국내와 종목코드·거래소 코드 체계가 달라 한 도구에 섞으면
    # 모델이 국내 파라미터를 그대로 넣는다. 카테고리대로 따로 낸다.
    "overseas_market": "meritz_overseas_market",
    "trading": "meritz_trading",
    "forex": "meritz_forex",
    "reference": "meritz_reference",
}
# 도구를 일부러 만들지 않는 카테고리. 빠뜨린 것과 구분하려고 이유까지 적어 둔다.
NO_TOOL = {
    "oauth2": "토큰 발급·폐기는 클라이언트가 캐시로 관리한다. "
              "도구로 노출하면 revoke 가 호출돼 진행 중인 세션이 전부 끊긴다.",
    "realtime": "웹소켓은 요청·응답 모델이 달라 별도 경로로 다룬다.",
}
_META = frozenset({"api_type", "confirm", "confirm_token", "tr_cont", "tr_cont_key"})

# 카탈로그의 파라미터 설명은 describe 에서 200자로 잘린다. 그 뒤에 있는 핵심 사용법을
# 모델이 못 보지 않도록 여기서 따로 안내한다. 카탈로그는 포털 명세에서 다시 만들므로
# 카탈로그를 고치지 않고 이 표에 둔다.
API_HINT = {
    "market_candles_days": (
        "기본은 KRX 정규장 종가(15:30)입니다. mrkt_div_code 는 생략할 수 있고, 생략하거나 'J' 로 "
        "보내도 'TJ'(exch_use_yn='Y')로 조회합니다. 'J' 는 exch_use_yn 과 무관하게 애프터마켓까지 "
        "포함한 마지막 체결가가 내려오기 때문입니다. 애프터마켓 포함 값이 필요하면 "
        "exch_use_yn='N' 을 넣으십시오. 'TJ' 의 거래량은 KRX+NXT 합산입니다."),
}
# 도구 설명(카테고리 단위)에 싣는 한 줄. 자세한 내용은 describe 에서 본다.
CATEGORY_HINT = {
    "domestic_market": "국내 일봉(market_candles_days)은 기본이 KRX 정규장 종가입니다 "
                       "(mrkt_div_code 를 생략하면 'TJ').",
}
# 모델이 매번 고르게 두면 같은 질문에도 J 와 TJ 가 갈린다. 기본 종가가 KRX 정규장 종가가 되도록
# 여기서 고정한다. 값은 카탈로그가 아니라 이 표가 정한다(카탈로그는 포털 명세에서 재생성된다).
API_DEFAULTS = {
    "market_candles_days": {"mrkt_div_code": "TJ", "exch_use_yn": "Y"},
}


def apply_defaults(api_key: str, params: dict) -> tuple[dict, list[str]]:
    """기본 종가를 KRX 정규장 종가로 맞춘다. 바꾼 내용은 notes 로 돌려준다.

    - 생략한 값은 API_DEFAULTS 로 채운다.
    - 'J' 는 exch_use_yn 과 무관하게 애프터마켓 포함 값이 내려온다. 정규장 종가를 뜻하는
      요청(exch_use_yn 이 생략됐거나 'Y')이면 'TJ' 로 바꾼다.
      애프터마켓 포함 값이 필요하면 exch_use_yn='N' 을 명시한다. 그때는 'J' 를 그대로 둔다.
    """
    defaults = API_DEFAULTS.get(api_key)
    if not defaults:
        return params, []
    out, notes = dict(params), []
    for k, v in defaults.items():
        if str(out.get(k) if out.get(k) is not None else "").strip() == "":
            out[k] = v
            notes.append(f"{k} 를 생략해 기본값 '{v}' 로 조회했습니다.")
    if (str(out["mrkt_div_code"]).strip().upper() == "J"
            and str(out["exch_use_yn"]).strip().upper() == "Y"):
        out["mrkt_div_code"] = "TJ"
        notes.append("mrkt_div_code 'J' 는 정규장 종가가 아니라 애프터마켓 포함 값이 내려와 'TJ' 로 "
                     "바꿔 조회했습니다(KRX 정규장 종가, 거래량은 KRX+NXT 합산). "
                     "애프터마켓 포함 값이 필요하면 exch_use_yn='N' 을 넣으십시오.")
    return out, notes


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "y", "yes", "예")


def describe(api: Api, catalog: Catalog, verbose: bool = False) -> str:
    """API 한 건의 사용법.

    응답 필드 설명까지 전부 실으면 한 번에 30KB 가 넘어 대화 컨텍스트를
    몇 번 만에 소진한다. 호출에 필요한 것(요청 파라미터·주의사항)은 늘
    싣고, 응답 필드 설명은 verbose 로 요청할 때만 싣는다.
    """
    lines = [f"{api.name}  [{api.key}]",
             f"  {api['method']} {api.path}   TR {api['tr_id']}"]
    if api.get("tps"):
        lines[-1] += f"   TPS {api['tps']}"
    if is_state_changing(api):
        lines.append("  ⚠ 상태변경 — 먼저 그대로 호출해 미리보기를 받으십시오. "
                     "응답의 confirm_token 을 params 에 넣어 다시 불러야 전송됩니다.")
    if api.key in API_HINT:
        lines.append(f"  ★ 사용 안내 — {API_HINT[api.key]}")

    if api.params:
        lines.append("  요청 파라미터")
        for p in api.params:
            mark = "필수" if p.get("required") else "선택"
            loc = p.get("location") or ""
            lines.append(f"    {p['name']:<24} {mark} {p.get('type','')} [{loc}] "
                         f"{p.get('name_ko') or ''}")
            if p.get("description"):
                d = " ".join(str(p["description"]).replace("<br/>", " ").split())
                lines.append(f"        {d[:200]}")

    # 응답 필드 설명이 없으면 결과가 숫자 뭉치로만 보인다. 카탈로그에 다 있다.
    res = [f for f in (api.get("response") or {}).get("body", [])
           if f["name"] not in ("data", "rsp_cd", "rsp_msg", "tr_cont", "tr_cont_key")]
    if res:
        lines.append(f"  응답 필드 ({len(res)}개)")
        for f in res:
            lines.append(f"    {f['name']:<24} {f.get('type','')} {f.get('name_ko') or ''}")
            if verbose and f.get("description"):
                d = " ".join(str(f["description"]).replace("<br/>", " ").split())
                lines.append(f"        {d[:200]}")
        if not verbose:
            lines.append("    (필드 설명을 보시려면 params 에 verbose=true 를 넣으십시오)")

    for fx in catalog.corrections_for(api.key):
        lines.append(f"  ⚠ 응답 처리 참고사항 — {fx['kind']}")
        if fx.get("working_example"):
            lines.append(f"      적용 예시: {fx['working_example']}")
        if fx.get("actual_parameter"):
            lines.append(f"      실제 파라미터명: {fx['actual_parameter']}")
        if fx.get("if_you_follow_the_portal"):
            lines.append(f"      일반적인 처리 방식으로 호출하면: {fx['if_you_follow_the_portal']}")
        if fx.get("workaround"):
            lines.append(f"      대응: {fx['workaround']}")

    ex = (api.get("example") or {}).get("request")
    if ex:
        lines.append("  요청 예시")
        lines += [f"    {ln}" for ln in str(ex).splitlines()]
    return "\n".join(lines)


def validate(api: Api, params: dict) -> list[str]:
    """명세와 어긋나는 점을 모아 돌려준다. 판정은 client 와 같은 것을 쓴다."""
    return ApiClient.validate(api, params)


def _fail(payload: dict) -> NoReturn:
    """실패를 MCP 오류(isError)로 올린다.

    ``{"ok": false}`` 를 정상 응답으로 돌려주면 프로토콜 수준에서는 성공이라
    호스트가 실패를 세거나 재시도를 판단할 근거가 없다. 사람이 읽을 한 줄을
    앞에 두고, 기계가 쓸 세부는 JSON 으로 함께 싣는다.
    """
    head = payload.get("message") or payload.get("error") or "요청을 처리하지 못했습니다."
    raise ToolError(f"{head}\n{json.dumps(payload, ensure_ascii=False, indent=2)}")


def register(mcp, catalog: Catalog, registry, settings) -> list[str]:
    """카테고리별 도구를 등록하고 등록된 도구 이름을 돌려준다."""
    made = []
    for category, tool_name in TOOL_OF.items():
        apis = [a for a in catalog.by_category(category) if not a.is_websocket]
        if not apis:
            continue
        listing = "\n".join(
            f"  {a.key:<24} {a['method']:<5} {a.name}"
            + ("   ⚠ 상태변경" if is_state_changing(a) else "")
            for a in sorted(apis, key=lambda x: x.key))
        accounts = ""
        if registry.many:
            accounts = (
                "\n\n등록된 계좌: " + " · ".join(registry.labels()) +
                f"\naccount 로 고릅니다. 생략하면 기본({registry.default.label})으로 조회합니다."
                "\n주문·환전은 생략할 수 없습니다 — 되돌릴 수 없으므로 반드시 지정하십시오.")
        hint = f"\n★ {CATEGORY_HINT[category]}" if category in CATEGORY_HINT else ""
        desc = (f"메리츠 Open API — {CATEGORY_KO.get(category, category)} ({len(apis)}건).\n"
                f"api_type 으로 호출할 API 를 고르고 params 에 파라미터를 넣습니다.\n"
                f"api_type='describe' 로 부르면 파라미터 설명을 돌려줍니다."
                f"{hint}{accounts}\n\n{listing}")

        def make(category=category, apis=apis):
            names = {a.key for a in apis}

            async def run(api_type: str, params: dict | None = None,
                          account: str | None = None) -> dict:
                params = dict(params or {})
                if api_type in ("describe", "list", "help"):
                    target = params.get("api_type")
                    if target and target in names:
                        return {"ok": True,
                                "detail": describe(catalog.get(target), catalog,
                                                   verbose=_truthy(params.get("verbose")))}
                    if target:
                        # 조용히 목록으로 되돌아가면 모델은 자기가 요청한 상세를
                        # 받았다고 착각한다. 못 찾았다는 사실을 말한다.
                        _fail({"ok": False, "error": "UNKNOWN_API_TYPE",
                               "message": f"이 도구에 '{target}' 은 없습니다. "
                                          f"아래 이름 중에서 고르십시오.",
                               "available": sorted(names)})
                    return {"ok": True, "apis": sorted(names),
                            "hint": "params={'api_type': '<이름>'} 으로 상세를 봅니다."}
                if api_type not in names:
                    _fail({"ok": False, "error": "UNKNOWN_API_TYPE",
                           "message": f"이 도구에 없는 api_type: {api_type}",
                           "available": sorted(names)})

                api = catalog.get(api_type)
                # 계좌를 여기서 한 번만 해석하고, 그 뒤로는 같은 클라이언트만
                # 쓴다. 지문 계산·토큰 발급·헤더가 모두 같은 설정을 보게 묶어야
                # 미리보기와 전송 사이에 계좌가 갈라지지 않는다.
                state_changing = is_state_changing(api)
                try:
                    client = registry.resolve(account, require_explicit=state_changing)
                except MeritzError as e:
                    _fail(dict(e.as_dict(), available=registry.labels()))
                if client.s.read_only and state_changing:
                    _fail(dict(blocked(api), account=client.s.label))

                # 기본 종가를 KRX 정규장 종가로 고정한다. 필수 검사보다 먼저 해야
                # mrkt_div_code 를 생략한 요청이 INVALID_PARAMS 로 떨어지지 않는다.
                params, notes = apply_defaults(api_type, params)

                problems = validate(api, params)
                if problems:
                    # describe 전문을 실으면 오류 하나가 15KB 가 되어 대화
                    # 컨텍스트를 먹는다. 무엇이 잘못됐는지만 말하고, 전체는
                    # 필요할 때 describe 로 보게 한다.
                    _fail({"ok": False, "error": "INVALID_PARAMS",
                           "message": "; ".join(problems),
                           "problems": problems,
                           "hint": f"params={{'api_type': '{api_type}'}} 으로 "
                                   f"describe 를 부르면 전체 파라미터를 봅니다."})

                # 확인 게이트는 ApiClient.call() 안에 있다. 여기서 따로 부르지
                # 않는다 — 토큰은 1회용이라 두 번 검사하면 두 번째가 실패한다.
                # 끌 수 있는 스위치는 두지 않는다. 있으면 언젠가 그 상태로
                # 돌아가고, 그때 주문이 무확인으로 나간다.
                try:
                    # 동기 HTTP 를 코루틴에서 그대로 부르면 이벤트 루프가 멈춘다.
                    # HTTP 로 여러 클라이언트를 붙였을 때 한 사람의 느린 조회가
                    # 나머지 전원을 세운다.
                    out = await anyio.to_thread.run_sync(
                        functools.partial(client.call, api, params))
                    if isinstance(out, dict):
                        out["account"] = client.s.label
                        if notes:
                            out["안내"] = notes
                    return out
                except MeritzError as e:
                    if e.code == "NEEDS_CONFIRMATION":
                        # 미리보기는 실패가 아니다. 모델이 사용자에게 보이고
                        # 동의를 받아 confirm_token 과 함께 다시 부르면 된다.
                        out = dict(e.body or {})
                        out["account"] = client.s.label
                        out["app_key_hint"] = key_hint(client.s.app_key)
                        if params.get("confirm_token") or _truthy(params.get("confirm")):
                            out["rejected"] = str(e)
                        return out
                    _fail(dict(e.as_dict(), account=client.s.label))
                except ToolError:
                    raise
                except Exception as e:                      # 네트워크 등
                    _fail({"ok": False, "error": type(e).__name__, "message": str(e)})
            return run

        # 힌트는 카테고리 이름이 아니라 실제 내용으로 정한다.
        # 조회 전용 카테고리에 상태변경 API 가 하나 추가돼도 자동으로 따라간다.
        writes = any(is_state_changing(a) for a in apis)
        mcp.tool(name=tool_name, description=desc,
                 annotations={"title": f"메리츠 {CATEGORY_KO.get(category, category)}",
                              "readOnlyHint": not writes,
                              "destructiveHint": writes,
                              "idempotentHint": not writes,
                              "openWorldHint": True})(make())
        made.append(tool_name)
    return made
