"""예제가 고객에게 나쁜 본을 보이지 않는지 본다.

예제는 그대로 복사돼 나간다. 여기서 틀린 필드명·빠진 판정이 나가면
그대로 퍼진다.
"""
import ast
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MERITZ_NO_ENV_FILE", "1")

import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from meritz import load_catalog  # noqa: E402

CAT = load_catalog()
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))
ENVELOPE = {"data", "rsp_cd", "rsp_msg", "tr_cont", "tr_cont_key",
            "header", "body", "input"}
# 응답 필드가 아니라 이 저장소가 만들어 넣는 키다 (safety.preview / client.call).
LOCAL_KEYS = {"will_send", "confirm_token", "expires_in_sec", "message",
              "error", "note", "verified"}


def _fields_of(api) -> set[str]:
    """API 하나가 주고받는 필드 이름."""
    out: set[str] = set()

    def walk(node):
        # 필드 목록은 [{"name": ...}, ...] 로 온다. response.containers 처럼
        # 매핑으로 오는 절도 있어 재귀로 훑는다.
        if isinstance(node, dict):
            name = node.get("name")
            if isinstance(name, str):
                out.add(name)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    raw = getattr(api, "raw", api)
    for sec in ("request", "response"):
        walk(raw.get(sec) or {})
    return out


FIELDS_BY_KEY = {api.key: _fields_of(api) for api in CAT}


def _api_keys(src: str) -> set[str]:
    """예제가 실제로 부르는 API 의 키.

    cat.get("market_prices") 처럼 카탈로그에서 꺼내는 자리와, 그 키를 감싼
    도우미 함수(call("market_prices"))에 넘기는 자리를 함께 본다.
    """
    tree = ast.parse(src)
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value in FIELDS_BY_KEY}


def _allowed_fields(src: str) -> set[str]:
    """이 예제가 읽어도 되는 필드.

    전 API 를 한 집합으로 합치면 A 의 필드를 B 에서 읽어도 통과한다.
    실제로 그 구멍 때문에 03 예제의 isnm(ovs_market_prices 에 없는 필드)이
    27개 테스트를 모두 통과했다. 예제가 부르는 API 의 필드로만 대조한다.
    """
    out = set(ENVELOPE) | LOCAL_KEYS
    for key in _api_keys(src):
        out |= FIELDS_BY_KEY[key]
    return out


def test_examples_exist():
    assert len(EXAMPLES) >= 8, "예제가 줄었습니다"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_parses(path):
    ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_reads_only_declared_fields(path):
    """없는 필드를 읽으면 화면에 None 이 찍힌다."""
    src = path.read_text(encoding="utf-8")
    allowed = _allowed_fields(src)
    tree = ast.parse(src)
    snake = re.compile(r"^[a-z][a-z0-9_]{3,}$")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args):
            continue
        recv = node.func.value
        if not (isinstance(recv, ast.Name)
                and recv.id in ("d", "b", "row", "book", "price", "data", "body",
                                "head", "msg", "res", "pv")):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            name = arg.value
            if snake.match(name) and name not in allowed:
                pytest.fail(
                    f"{path.name}: 이 예제가 부르는 API "
                    f"({', '.join(sorted(_api_keys(src))) or '없음'}) 에 "
                    f"없는 필드 {name!r}")


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_mentions_only_real_api_types(path):
    for m in re.finditer(r'cat\.get\("([a-z][a-z0-9_]+)"\)',
                         path.read_text(encoding="utf-8")):
        assert m.group(1) in CAT, f"{path.name}: 없는 api_type {m.group(1)}"


def test_order_example_never_sends_on_import():
    """예제를 실행하는 것만으로 주문이 나가면 안 된다."""
    src = (ROOT / "examples" / "05_order.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_guard = [n for n in tree.body
                  if isinstance(n, ast.If) and "__main__" in ast.dump(n)]
    assert main_guard, "__main__ 가드가 없습니다"
    guarded = ast.dump(main_guard[0])
    assert "send" not in guarded or "preview" in guarded
    assert "'send(body," in src or '"  send(body' in src or "send(body," in src


def test_order_example_checks_the_warning_flow():
    src = (ROOT / "examples" / "05_order.py").read_text(encoding="utf-8")
    assert "ORDER_NOT_ACCEPTED" in src
    assert "warn_cnfr_yn" in src
