"""Read-only daily prices using the existing MCP account registry and API client."""
from __future__ import annotations
import datetime as dt
import math
import re

from .client import MeritzError

# Only today's Korean calendar date is needed, not historical timezone conversion.
# Avoid requiring an OS IANA database on Windows binary installations.
SEOUL = dt.timezone(dt.timedelta(hours=9), name="KST")

class DataError(ValueError):
    """Fixed public messages; never expose provider response bodies or credentials."""


# ApiClient 오류 분류 코드(meritz/client.py: MeritzError.code / judge()["error"])를 고객 안내 문구로 바꾼다.
# 공급자 응답 원문(rsp_msg·body)은 절대 싣지 않는다 — 코드만 보고 고정 문구를 고른다.
_RATE_LIMIT_CODES = frozenset({"RATE_LIMITED"})                      # EGW00200 초당 거래건수 초과
_AUTH_CODES = frozenset({"AUTH_FAILED", "AUTH_BAD_RESPONSE", "NO_CREDENTIALS"})
_NETWORK_CODES = frozenset({"TIMEOUT", "UNREACHABLE", "AUTH_UNREACHABLE"})
_AUTH_HTTP_STATUS = frozenset({401, 403})


def provider_failure_message(code: object, http_status: object = None, *, prefix: str = "") -> str:
    """client.call 실패(예외 또는 ok=False 응답)를 고정 한국어 안내로 바꾼다. 분류 불가면 일반 문구."""
    if code in _RATE_LIMIT_CODES:
        return f"{prefix}시세 조회 호출 한도를 초과했습니다. 잠시 후 다시 시도하세요."
    if code in _AUTH_CODES or http_status in _AUTH_HTTP_STATUS:
        return f"{prefix}시세 조회 권한이 확인되지 않습니다. 고객 키의 권한을 확인하세요."
    if code in _NETWORK_CODES:
        return f"{prefix}시세 서버에 연결하지 못했습니다. 네트워크 연결을 확인하세요."
    return f"{prefix}시세 조회에 실패했습니다. 고객 키의 권한과 네트워크 연결을 확인하십시오."


def call_provider(client, api, params, *, prefix: str = "") -> dict:
    """client.call 을 감싸 실패를 DataError(고정 문구)로 바꾼다. 성공 응답(ok=True, body dict)만 돌려준다."""
    try:
        result = client.call(api, params)
    except MeritzError as error:
        raise DataError(provider_failure_message(error.code, error.status, prefix=prefix)) from None
    except Exception:
        raise DataError(provider_failure_message(None, prefix=prefix)) from None
    if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(result.get("body"), dict):
        code = result.get("error") if isinstance(result, dict) else None
        status = result.get("http_status") if isinstance(result, dict) else None
        raise DataError(provider_failure_message(code, status, prefix=prefix))
    return result

def parse_request(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"symbol", "startDate", "endDate", "market"}:
        raise DataError("종목·시작일·종료일·시장만 지정하십시오. 고객 키는 기존 MCP 계좌 설정을 사용합니다.")
    if not isinstance(value["symbol"], str) or not re.fullmatch(r"[0-9]{6}", value["symbol"]):
        raise DataError("종목 코드는 숫자 6자리여야 합니다.")
    if value["market"] not in ("J", "NJ", "TJ"):
        raise DataError("지원하는 시장 코드는 J, NJ, TJ입니다.")
    try:
        for key in ("startDate", "endDate"):
            if not isinstance(value[key], str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value[key]):
                raise ValueError
        start, end = dt.date.fromisoformat(value["startDate"]), dt.date.fromisoformat(value["endDate"])
        if start.year < 1900 or not 0 <= (end - start).days <= 365:
            raise ValueError
    except (ValueError, TypeError):
        raise DataError("조회 기간은 유효한 YYYY-MM-DD이며 시작일 이후 최대 365일이어야 합니다.") from None
    return dict(value)


def normalize(rows: object, request: dict, now: dt.datetime, *,
              timezone: dt.tzinfo = SEOUL, close_field: str = "stck_clpr",
              base_warnings: tuple[str, ...] | None = None) -> tuple[list[dict], list[str]]:
    if not isinstance(rows, list) or len(rows) > 5000:
        raise DataError("일봉 응답 형식 또는 크기가 지원 범위를 벗어났습니다.")
    today = now.astimezone(timezone).date().isoformat()
    by_date: dict[str, dict] = {}
    excluded = 0
    duplicates = 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str) or not re.fullmatch(r"[0-9]{8}", row["date"]):
            raise DataError("일봉 날짜가 누락되었거나 올바르지 않습니다.")
        try:
            date = dt.datetime.strptime(row["date"], "%Y%m%d").date().isoformat()
        except ValueError:
            raise DataError("일봉 날짜가 누락되었거나 올바르지 않습니다.") from None
        if date >= today or date < request["startDate"] or date > request["endDate"]:
            excluded += 1
            continue
        candle: dict = {"date": date}
        for output, raw in (("open", "oprc"), ("high", "hprc"), ("low", "lprc"), ("close", close_field)):
            value = row.get(raw)
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise DataError("일봉 가격이 누락되었거나 올바르지 않습니다.")
            try:
                price = float(value)
                if not math.isfinite(price) or not 0.01 <= price <= 1e9:
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                raise DataError("일봉 가격이 누락되었거나 올바르지 않습니다.") from None
            candle[output] = price
        raw_volume = row.get('acml_vol')
        if raw_volume is not None:
            if isinstance(raw_volume, bool) or not isinstance(raw_volume, (int, float, str)):
                raise DataError('일봉 거래량이 올바르지 않습니다.')
            try:
                volume = float(raw_volume)
                if not math.isfinite(volume) or volume < 0 or volume > 1e15:
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                raise DataError('일봉 거래량이 올바르지 않습니다.') from None
            candle['volume'] = volume
        if candle["low"] > min(candle["open"], candle["close"]) or candle["high"] < max(candle["open"], candle["close"]):
            raise DataError("일봉 OHLC 가격 관계가 올바르지 않습니다.")
        if date in by_date:
            if candle != by_date[date]:
                raise DataError("동일 날짜에 서로 다른 가격이 있어 조회 결과를 사용할 수 없습니다.")
            duplicates += 1
        by_date[date] = candle
    if not by_date:
        raise DataError("요청 기간에 사용할 수 있는 확정 일봉이 없습니다.")
    warnings = ["당일·미래 일봉은 제외합니다. 거래소 달력 대조를 하지 않아 기간 내 누락 여부는 확정할 수 없습니다.",
                "수정주가를 요청하지만 모든 OHLC의 수정 범위와 기업행사 반영을 별도로 검증하지 않았습니다. 배당 현금흐름·보유 수량 변동은 재현하지 않습니다."]
    if base_warnings is not None:
        warnings = list(base_warnings)
    if base_warnings is None and request["market"] != "TJ":
        warnings.append("선택 시장의 종가에는 애프터마켓 체결이 포함될 수 있습니다. 정규장 기준은 TJ와 Y 조합입니다.")
    if excluded:
        warnings.append(f"당일·미래 또는 요청 기간 밖 일봉 {excluded}건을 제외했습니다.")
    if any('volume' not in candle for candle in by_date.values()):
        warnings.append('거래량이 누락된 일봉이 있습니다. 거래량 지표 전략은 실행할 수 없습니다.')
    if duplicates:
        warnings.append(f"동일 가격의 중복 일봉 {duplicates}건을 제거했습니다.")
    return [by_date[day] for day in sorted(by_date)], warnings


def load_market_data(catalog, registry, symbol, start_date, end_date, account=None):
    request = parse_request({"symbol": symbol, "startDate": start_date, "endDate": end_date, "market": "TJ"})
    api = catalog.get("market_candles_days")
    if api is None or api.path != "/market/v1/candles/days" or api.method != "GET":
        raise DataError("동봉된 일봉 API 명세를 확인하십시오.")
    if not all(api.param(key) for key in ("from", "to", "exch_use_yn", "mod_stpr_cls_code")):
        raise DataError("동봉된 명세가 기간 조회를 지원하지 않습니다. 카탈로그를 업데이트하십시오.")
    try:
        client = registry.resolve(account, require_explicit=True)
    except MeritzError as error:
        messages = {
            "ACCOUNT_REQUIRED": "등록 계좌가 여럿입니다. 기존 MCP 계좌 이름을 account에 지정하십시오.",
            "UNKNOWN_ACCOUNT": "등록되지 않은 MCP 계좌 이름입니다.",
            "NO_CREDENTIALS": "기존 MCP 설정에서 고객 API 키와 시크릿을 확인하십시오.",
        }
        raise DataError(messages.get(error.code, "MCP 계좌 설정을 확인하십시오.")) from None
    params = {"iscd": symbol, "mrkt_div_code": "TJ", "exch_use_yn": "Y", "mod_stpr_cls_code": "0",
              "from": start_date.replace("-", ""), "to": end_date.replace("-", "")}
    result = call_provider(client, api, params)
    current = dt.datetime.now(SEOUL)
    candles, warnings = normalize(result["body"].get("data"), request, current)
    if len(result["body"].get('data', [])) == 40:
        warnings.append('응답이 40건입니다. 기본 최근 40건 응답 또는 기간 잘림 가능성을 확인하십시오. 40건이라는 사실만으로 잘림을 확정할 수는 없습니다.')
    body = result['body']
    if body.get('rsp_cd') == '5762':
        warnings.append('응답에서 다음 페이지 가능성이 표시됐지만 추가 조회는 하지 않았습니다. 저장된 기간이 불완전할 수 있습니다.')
    if body.get('rsp_sub_msg') or result.get('note'):
        warnings.append('시세 응답에 추가 안내가 있어 기간 완전성을 확인할 수 없습니다. 안내 원문은 저장하지 않았습니다.')
    # ApiClient.judge marks unreadable response codes as verified=False when a known
    # degraded quote endpoint still returned data. Do not relay provider text or claim
    # that a missing/true flag certifies completeness or corporate-action adjustment.
    if result.get("verified") is False:
        warnings.append("API 응답 코드를 확인할 수 없어 성공 여부를 코드로 검증하지 못했습니다. 반환 일봉의 형식·가격 관계만 검사했습니다.")
    return {"candles": candles, "source": {"kind": "meritz", "label": "메리츠 Open API · 고객 키 조회 · 수정주가 요청",
            **request, "adjustment": "requested-adjusted", "fetchedAt": current.isoformat()}, "warnings": warnings}
