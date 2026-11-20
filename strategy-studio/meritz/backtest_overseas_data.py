"""US daily snapshot adapter; no live availability or full-range guarantee.

Three calendar years is a local request limit, not a provider guarantee. A leap
start date's third anniversary is clamped to February 28. Prices retain the
engine's supported [0.01, 1e9] range; unsupported values are never corrected.
America/New_York must be available (including in Windows/frozen distributions).
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .backtest_data import DataError, call_provider, normalize
from .client import MeritzError

TIMEZONE = 'America/New_York'
BASE_WARNINGS = (
    '미국 현지 날짜의 당일·미래 일봉은 제외합니다. 거래소 달력 대조를 하지 않아 기간 내 누락 여부는 확정할 수 없습니다.',
    '분할 보정 주가를 요청하지만 모든 OHLC의 보정 범위를 별도로 검증하지 않았습니다. 배당 현금흐름·기업행동에 따른 보유 수량 변동은 재현하지 않습니다.',
    '최대 3년은 앱의 요청 한도입니다. 제공자의 최대 기간·반환 건수와 요청 기간 전체의 응답 완전성을 확인하지 않았습니다. 분할·연속 조회는 수행하지 않습니다.',
    '가격은 엔진 지원 범위인 0.01~1,000,000,000 USD만 허용하며 범위 밖 수정주가는 보정하지 않고 거절합니다.',
)


def parse_request(value: object) -> dict:
    fields = {'symbol', 'exchange', 'startDate', 'endDate', 'quoteMode'}
    if not isinstance(value, dict) or set(value) != fields:
        raise DataError('미국 종목·거래소·시작일·종료일·시세 종류만 지정하십시오.')
    symbol = value['symbol']
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.\-]{0,15}', symbol):
        raise DataError('미국 심볼은 영문·숫자로 시작하는 1~16자리 영문·숫자·점·하이픈만 지원합니다.')
    if value['exchange'] not in ('OQ', 'NY', 'AX'):
        raise DataError('미국 거래소 OQ·NY·AX 중 하나를 명시하십시오.')
    if value['quoteMode'] not in ('0', '1'):
        raise DataError('시세 종류 quoteMode는 실시간 요청 "0" 또는 지연 요청 "1"을 명시하십시오.')
    try:
        for key in ('startDate', 'endDate'):
            if not isinstance(value[key], str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value[key]):
                raise ValueError
        start, end = dt.date.fromisoformat(value['startDate']), dt.date.fromisoformat(value['endDate'])
        # February 29 -> February 28, three years later. Never a rolling 1095 days.
        anniversary = start.replace(year=start.year + 3, day=28 if start.month == 2 and start.day == 29 else start.day)
        if start.year < 1900 or not start <= end <= anniversary:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise DataError('조회 기간은 유효한 YYYY-MM-DD이며 시작일 이후 달력 기준 최대 3년입니다. 2월 29일의 3년 후는 2월 28일입니다.') from None
    return {**value, 'symbol': symbol.upper()}


def new_york_timezone():
    try:
        return ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        raise DataError('미국 시간대 데이터를 사용할 수 없습니다. America/New_York 시간대가 포함된 설치본이 필요합니다.') from None


def load_overseas_market_data(catalog, registry, symbol, start_date, end_date, *, exchange, quote_mode, account=None):
    request = parse_request({'symbol': symbol, 'exchange': exchange, 'startDate': start_date,
                             'endDate': end_date, 'quoteMode': quote_mode})
    timezone = new_york_timezone()  # Fail closed before resolving accounts or calling a provider.
    api = catalog.get('ovs_market_candles_days')
    if api is None or api.path != '/market/v1/overseas/candles/days' or api.method != 'GET':
        raise DataError('동봉된 미국 일봉 API 명세를 확인하십시오.')
    if not all(api.param(key) for key in ('mrkt_div_code', 'mrkt_cls_code', 'iscd', 'dely_rltm_cls_code', 'mod_stpr_cls_code', 'from', 'to')):
        raise DataError('동봉된 미국 일봉 명세가 필요한 조회 필드를 지원하지 않습니다. 카탈로그를 업데이트하십시오.')
    try:
        client = registry.resolve(account, require_explicit=True)
    except MeritzError as error:
        messages = {
            'ACCOUNT_REQUIRED': '등록 계좌가 여럿입니다. 기존 MCP 계좌 이름을 account에 지정하십시오.',
            'UNKNOWN_ACCOUNT': '등록되지 않은 MCP 계좌 이름입니다.',
            'NO_CREDENTIALS': '기존 MCP 설정에서 고객 API 키와 시크릿을 확인하십시오.',
        }
        raise DataError(messages.get(error.code, 'MCP 계좌 설정을 확인하십시오.')) from None
    params = {'iscd': request['symbol'], 'mrkt_div_code': 'OV', 'mrkt_cls_code': exchange,
              'dely_rltm_cls_code': quote_mode, 'mod_stpr_cls_code': '0',
              'from': start_date.replace('-', ''), 'to': end_date.replace('-', '')}
    result = call_provider(client, api, params, prefix='미국 ')
    body = result['body']
    code = body.get('rsp_cd')
    readable_code = isinstance(code, str) and code.isprintable() and bool(code.strip())
    # The shared judge accepts success codes from many endpoints. This endpoint's
    # public contract lists only 0000; in particular 5762 must not start paging.
    # Preserve the established unreadable-code, verified=False warning policy.
    if code != '0000' and not (result.get('verified') is False and not readable_code):
        raise DataError('미국 일봉 명세의 정상 응답 코드 0000을 확인하지 못했습니다. 이 응답은 저장하지 않으며 분할·연속 조회도 수행하지 않습니다.')
    current = dt.datetime.now(timezone)
    rows = result['body'].get('data')
    # prpr is a price string. dcpn_cnt describes decimal places; it is NOT an
    # instruction to divide an already decimal price by a power of ten.
    candles, warnings = normalize(rows, request, current, timezone=timezone, close_field='prpr', base_warnings=BASE_WARNINGS)
    warnings.append(f"요청 기간: {start_date} ~ {end_date}; 저장 대상 실제 기간: {candles[0]['date']} ~ {candles[-1]['date']} · {len(candles)}봉. 휴장·상장일·잘림 여부는 별도 확인이 필요합니다.")
    if any(key in value for value, key in ((result, 'note'), (result, 'rsp_sub_msg'), (body, 'rsp_sub_msg'))):
        warnings.append('제공자가 추가 안내 또는 상세 메시지를 반환했습니다. 입력 조건과 응답 범위를 별도로 확인하십시오. 메시지 원문은 저장하지 않습니다.')
    if len(rows) == 40:
        warnings.append('응답이 40건입니다. 기본 최근 40건 응답 또는 기간 잘림 가능성을 확인하십시오. 40건이라는 사실만으로 잘림을 확정할 수는 없습니다.')
    if result.get('verified') is False:
        warnings.append('API 응답 코드를 확인할 수 없어 성공 여부를 코드로 검증하지 못했습니다. 반환 일봉의 형식·가격 관계만 검사했습니다.')
    return {'candles': candles, 'source': {'kind': 'meritz', 'label': '메리츠 Open API · 미국 일봉 · 분할 보정 요청',
            **request, 'market': 'OV', 'currency': 'USD', 'timezone': TIMEZONE,
            'adjustment': 'requested-split-adjusted', 'fetchedAt': current.isoformat()}, 'warnings': warnings}
