"""메리츠증권 Open API 예제에서 공통으로 쓰는 최소 코드.

라이브러리가 아니다. 예제가 매번 같은 인증·판정 코드를 베끼지 않도록 모아 둔 것이다.
"""
from .core.catalog import load_catalog
from .core.client import ApiClient, MeritzError, paginate
from .core.config import settings

__all__ = ["load_catalog", "ApiClient", "MeritzError", "settings", "client",
           "paginate"]


def client() -> ApiClient:
    """설정과 카탈로그를 붙인 호출기. 예제는 이것만 쓰면 된다."""
    return ApiClient(settings())
