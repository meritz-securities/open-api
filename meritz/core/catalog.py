"""카탈로그 로딩.

원천은 **포털 등록분**이다.
저장소가 포털과 다르면 고객이 혼란스러우므로, 여기서 규격을 새로 만들지 않는다.

포털 등록 내용이 실호출과 다른 지점은 `corrections.json` 에 따로 담아,
고객이 예시를 그대로 쓰기 전에 알 수 있게 한다.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 동봉본이 먼저다. 공개 저장소는 따로 클론되므로 상위 폴더를 기대할 수 없다.
#
# 설치 형태에 따라 data/ 가 패키지 옆에 있을 수도, 한 단계 위에 있을 수도 있다.
_SEARCH = [
    Path(__file__).resolve().parent / "data" / "catalog.json",          # 패키지 동봉본
    Path(__file__).resolve().parents[1] / "data" / "catalog.json",      # 대체 경로
]


def _find(name: str) -> Path | None:
    override = os.getenv("MERITZ_CATALOG")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    for base in _SEARCH:
        p = base.parent / name
        if p.is_file():
            return p
    return None


@dataclass
class Api:
    raw: dict[str, Any]

    def __getitem__(self, k):     return self.raw[k]
    def get(self, k, d=None):     return self.raw.get(k, d)

    @property
    def key(self) -> str:         return self.raw["key"]
    @property
    def name(self) -> str:        return self.raw["name"]
    @property
    def path(self) -> str:        return self.raw["path"]
    @property
    def method(self) -> str:      return self.raw["method"]
    @property
    def is_websocket(self) -> bool: return self.raw["protocol"] == "WEBSOCKET"

    @property
    def is_state_changing(self) -> bool:
        """규칙은 safety 한 곳에만 둔다 — 여기서 다시 정의하면 갈라진다."""
        from .safety import is_state_changing as _rule
        return _rule(self)

    @property
    def params(self) -> list[dict]:
        return self.raw.get("request", {}).get("params", [])

    def param(self, name: str) -> dict | None:
        return next((p for p in self.params if p["name"] == name), None)

    @property
    def required(self) -> list[str]:
        """포털의 required 에 corrections 의 actual_required 를 더한다.

        포털이 requireYn 을 N 으로 등록해 둔 필수값이 있다. 그대로 믿으면
        검증이 통과시키고 게이트웨이가 HTTP 500 / IGW50004 로 떨어진다.
        어느 것이 진짜 필수인지는 corrections.json 이 실호출 근거로 적어 둔다.
        """
        names = [p["name"] for p in self.params if p.get("required")]
        for n in self.raw.get("_actual_required") or []:
            if n not in names:
                names.append(n)
        return names

    def query_params(self, given: dict) -> dict:
        names = {p["name"] for p in self.params if p.get("location") == "query"}
        return {k: v for k, v in given.items() if k in names and v not in (None, "")}

    def body_params(self, given: dict) -> dict:
        names = {p["name"] for p in self.params if p.get("location") == "body"}
        return {k: v for k, v in given.items() if k in names and v is not None}


class Catalog:
    def __init__(self, data: dict, corrections: dict | None = None):
        self.data = data
        self.corrections = corrections or {}
        self._by_key: dict[str, Api] = {}
        for a in data["apis"]:
            k = a["key"]
            if k in self._by_key:
                raise ValueError(
                    f"카탈로그에 api_type 이 중복됩니다: {k}\n"
                    f"  그대로 두면 하나가 조용히 사라집니다. 카탈로그를 다시 만드세요.")
            self._by_key[k] = Api(a)
        self._fix_by_key: dict[str, list[dict]] = {}
        for c in (self.corrections.get("corrections") or []):
            for k in c.get("keys", []):
                self._fix_by_key.setdefault(k, []).append(c)
                # 정정이 "진짜 필수" 를 적어 뒀으면 그것도 필수로 친다.
                api = self._by_key.get(k)
                if api is not None and c.get("actual_required"):
                    known = {p["name"] for p in api.params}
                    api.raw.setdefault("_actual_required", [])
                    api.raw["_actual_required"] += [n for n in c["actual_required"]
                                                    if n in known
                                                    and n not in api.raw["_actual_required"]]

    def __len__(self) -> int:                 return len(self._by_key)
    def __iter__(self) -> Iterator[Api]:      return iter(self._by_key.values())
    def __contains__(self, key: str) -> bool: return key in self._by_key

    def get(self, key: str) -> Api | None:    return self._by_key.get(key)
    def keys(self) -> list[str]:              return list(self._by_key)

    def rest(self) -> list[Api]:
        return [a for a in self if not a.is_websocket]

    def websockets(self) -> list[Api]:
        return [a for a in self if a.is_websocket]

    def by_category(self, category: str) -> list[Api]:
        return [a for a in self if a["category"] == category]

    def corrections_for(self, key: str) -> list[dict]:
        """포털 예시를 그대로 쓰면 실패하는 지점."""
        return self._fix_by_key.get(key, [])

    @property
    def source(self) -> str:
        return self.data.get("source", "")

    @property
    def generated(self) -> str:
        return self.data.get("generated", "")


def load_catalog() -> Catalog:
    p = _find("catalog.json")
    if not p:
        raise FileNotFoundError(
            "catalog.json 을 찾을 수 없습니다.\n"
            "  설치가 온전한지 확인하거나, MERITZ_CATALOG 로 경로를 직접 지정하세요.\n"
            "  찾아본 곳: " + " · ".join(str(x) for x in _SEARCH))
    data = json.loads(p.read_text(encoding="utf-8"))

    def _side(name: str):
        f = p.parent / name
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None

    return Catalog(data, _side("corrections.json"))
