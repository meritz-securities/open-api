"""메리츠 Open API MCP — 공용 모듈."""

# 판 번호는 여기 하나뿐이다. pyproject.toml·manifest.json 과 어긋나면
# tests 가 걸어 준다.
__version__ = "0.5.0b1"

from .catalog import Catalog, load_catalog
from .client import ApiClient, MeritzError, paginate
from .codes import normalize_iscd, to_exchange_id, to_vendor_code
from .config import Settings, settings
from .safety import is_state_changing, read_only

__all__ = ["Catalog", "load_catalog", "Settings", "settings",
           "ApiClient", "MeritzError", "is_state_changing", "read_only",
           "paginate", "normalize_iscd", "to_vendor_code", "to_exchange_id"]
