"""상태변경 판별과 안전장치.

**게이트는 두 겹이다.** 하나가 틀려도 다른 하나가 막는다.

  ① 경로 패턴 — 경로는 그 요청이 무엇을 하는지 확정한다
  ② HTTP 메서드 — POST 면 상태변경

둘 중 하나가 어긋나도 다른 하나가 막는다.

  상태변경   POST /trading/v1/orders/{buy|sell|modify|cancel}
            POST /trading/v1/credit-orders/{buy|sell}
            POST /trading/v1/overseas/orders/{buy|sell|modify|cancel}
            POST /forex/v1/exchanges                     환전 신청
  조회      GET  …/orders/{history|estimate|detail|today} · …/exchanges/history
  예외      category "oauth2" — 아래 _EXEMPT_CATEGORIES 참고
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Any

# 경로만으로 상태변경이 확실한 것 — 메서드를 믿지 않는다
_ACTION_PATH = re.compile(
    r"^/(?:trading|forex)/v\d+/"
    r"(?:"
    r"(?:overseas/)?(?:credit-)?(?:reserved-)?orders/(?:buy|sell|modify|cancel)"
    r"|exchanges"
    r")$"
)

CONFIRM_TTL = 180.0          # 미리보기 토큰 유효 시간(초)

# 메서드만 보고 상태변경으로 몰면 안 되는 분류.
#
# oauth2_token·oauth2_revoke 는 카탈로그에 들어와 있고 둘 다 POST 다. 그대로 두면
# 자기 세션의 토큰을 받고 버리는 호출이 «상태를 바꾸는 요청» 으로 분류되어
# `meritz api list --state-changing` 목록에 주문·환전과 나란히 서고, 확인 게이트가
# 걸려 인증부터 막힌다. 바꾸는 것은 시장이나 계좌가 아니라 호출자 자신의
# 토큰뿐이므로 판정에서 뺀다.
#
# oauth2_revoke 는 전 세션을 끊는다는 점에서 여전히 주의가 필요하지만, 그 경고는
# corrections.json 이 담당한다 — 주문과 같은 확인 게이트에 태우지는 않는다.
_EXEMPT_CATEGORIES = frozenset({"oauth2"})

# 카테고리 면제를 뚫고 상태변경으로 되돌리는 것.
#
# oauth2 를 통째로 면제한 이유는 토큰 발급이 모든 호출의 전제이기 때문이다.
# 그런데 폐기는 성격이 다르다 — 같은 앱키로 발급된 토큰은 하나뿐이라,
# 한 번 부르면 다른 프로세스·세션이 쓰던 토큰까지 함께 끊긴다.
# 조회 전용으로 두고 쓰던 사람이 시험 삼아 부를 수 있는 자리가 아니다.
_ALWAYS_STATE_CHANGING = frozenset({"oauth2_revoke"})


class _MemoryStore:
    """발급된 확인 토큰을 담는 기본 저장소 — 한 프로세스 안에서만 유효하다.

    CLI 처럼 미리보기와 전송이 **다른 프로세스**에서 일어나는 실행 형태는
    set_confirm_store() 로 파일 기반 저장소를 끼운다. 저장소를 바꿔도
    발급·검증 규칙은 이 모듈 하나에만 있다.
    """

    def __init__(self) -> None:
        self._d: dict[str, tuple[str, float]] = {}

    def get(self, token: str) -> tuple[str, float] | None:
        return self._d.get(token)

    def put(self, token: str, api_key: str, expires_at: float) -> None:
        self._d[token] = (api_key, expires_at)

    def pop(self, token: str) -> None:
        self._d.pop(token, None)

    def purge(self, now: float) -> None:
        for k, (_, exp) in list(self._d.items()):
            if exp < now:
                self._d.pop(k, None)


_store: Any = _MemoryStore()


def set_confirm_store(store: Any) -> None:
    """확인 토큰 저장소를 갈아 끼운다. get/put/pop/purge 를 갖추면 된다."""
    global _store
    _store = store


def josa(word: str, pair: str = "은는") -> str:
    """받침에 따라 조사를 고른다. API 이름이 값으로 들어가므로 고정할 수 없다."""
    if not word:
        return pair[1]
    last = word.strip().rstrip("'\"")[-1:]
    if not last or not ("가" <= last <= "힣"):
        return pair[1]
    return pair[0] if (ord(last) - 0xAC00) % 28 else pair[1]


def is_state_changing(api: Any) -> bool:
    """이 API 가 외부 상태를 바꾸는가. 둘 중 하나라도 걸리면 참이다."""
    if isinstance(api, str):
        raise TypeError("is_state_changing 에는 카탈로그 항목을 넘기세요 (문자열 아님)")
    if api.get("protocol") == "WEBSOCKET":
        return False
    if (api.get("key") or "") in _ALWAYS_STATE_CHANGING:
        return True
    if (api.get("category") or "").strip().lower() in _EXEMPT_CATEGORIES:
        return False
    path = (api.get("path") or "").rstrip("/")
    if _ACTION_PATH.match(path):
        return True
    return (api.get("method") or "").upper() == "POST"


def read_only(settings) -> bool:
    """설정에서 조회 전용 여부를 읽는다. 세 저장소가 같은 이름으로 내보낸다."""
    return settings.read_only


# ---------------------------------------------------------------- 확인 토큰
def _app_key_digest(app_key: str | None) -> str:
    """앱키를 지문에 넣되 **원문은 저장하지 않는다.**

    확인 토큰 저장소는 파일일 수 있다(CLI). 거기에 앱키가 평문으로 남으면
    확인 게이트를 만들려다 자격증명을 흘리는 꼴이 된다. 해시만 넣는다.
    """
    if not app_key:
        return ""
    return hashlib.sha256(str(app_key).encode("utf-8")).hexdigest()[:16]


def _fingerprint(api_key: str, body: dict, base_url: str,
                 app_key: str | None = None) -> str:
    """무엇을·어디로·**누구의 계좌로** 보내는지의 지문.

    base_url 을 넣는 이유 — 미리보기와 전송이 서로 다른 서버로 갈라지는 일을
    막기 위해서다. 기본값이 운영이라, 두 번째 명령에서 환경변수를 빠뜨리면
    사용자가 본 화면과 다른 서버로 주문이 나갈 수 있다.

    app_key 를 넣는 이유도 정확히 같다 — **계좌를 정하는 것이 앱키다.**
    CLI 처럼 미리보기와 전송이 다른 프로세스면 그 사이에 MERITZ_APP_KEY 나
    MERITZ_STATE_DIR 이 바뀔 수 있고, 그러면 A 계좌로 확인한 주문이 B 계좌로
    나간다. 앱키 원문 대신 해시를 넣는다(_app_key_digest 참고).

    app_key 를 넘기지 않으면 계좌 확인 없이 지문이 만들어진다. 호출부는
    반드시 settings.app_key 를 넘긴다 — 기본값은 이 모듈을 직접 쓰는
    바깥 도구(감사 스크립트 등)가 깨지지 않게 두는 것뿐이다.
    """
    blob = json.dumps({"api": api_key, "body": body, "base_url": base_url,
                       "app_key": _app_key_digest(app_key)},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def issue_confirm_token(api_key: str, body: dict, base_url: str,
                        app_key: str | None = None) -> str:
    """미리보기에서 발급한다. 이 토큰이 있어야 전송된다.

    토큰은 난수다. 예측 가능한 값이면 미리보기를 거치지 않고도 만들 수 있어,
    "토큰이 있다 = 내용을 확인했다" 가 성립하지 않는다.
    """
    now = time.time()
    _store.purge(now)
    token = secrets.token_hex(8)
    _store.put(token, _fingerprint(api_key, body, base_url, app_key),
               now + CONFIRM_TTL)
    return token


def check_confirm(api_key: str, body: dict, token: str | None,
                  base_url: str, app_key: str | None = None) -> str | None:
    """전송해도 되는지 판정한다. 문제가 있으면 사유 문자열, 없으면 None."""
    if not token:
        return "confirm_token 이 없습니다. 먼저 confirm 없이 호출해 내용을 확인하세요."
    entry = _store.get(token)
    if not entry:
        return ("confirm_token 이 유효하지 않거나 만료됐습니다"
                f"(유효 {int(CONFIRM_TTL)}초). 미리보기를 다시 받으세요.")
    fingerprint, exp = entry
    if time.time() > exp:
        _store.pop(token)
        return "confirm_token 이 만료됐습니다. 미리보기를 다시 받으세요."
    if fingerprint != _fingerprint(api_key, body, base_url, app_key):
        _store.pop(token)
        return ("미리보기에서 확인한 내용과 요청이 다릅니다. "
                "요청 값·대상 서버·앱키(계좌) 중 하나라도 바뀌었으면 "
                "미리보기를 다시 받아야 합니다.")
    _store.pop(token)                 # 1회용
    return None


def preview(api, body: dict, base_url: str, app_key: str | None = None) -> dict:
    """전송 전 미리보기. 여기서 받은 confirm_token 이 있어야 실제로 나간다."""
    token = issue_confirm_token(api["key"], body, base_url, app_key)
    return {
        # ok 는 모든 반환에 있어야 한다 — 없으면 호출자가 KeyError 를 맞는다.
        "ok": False,
        "needs_confirmation": True,
        "message": (f"'{api['name']}'{josa(api['name'])} 실제로 전송되는 요청입니다. "
                    f"내용을 사용자에게 보이고 동의를 받은 뒤, "
                    f"params 에 confirm_token 을 넣어 다시 호출하세요."),
        "confirm_token": token,
        "expires_in_sec": int(CONFIRM_TTL),
        "will_send": {
            "method": api["method"], "url": base_url + api["path"],
            "tr_id": api["tr_id"], "body": body,
        },
        "note": ("앱키에 묶인 계좌로 전송됩니다. 실계좌입니다. "
                 "값·대상 서버·앱키가 하나라도 바뀌면 토큰이 무효가 됩니다."),
    }


def blocked(api) -> dict:
    return {
        "ok": False, "error": "READ_ONLY",
        "message": (f"'{api['name']}'{josa(api['name'])} 상태를 바꾸는 API 라 "
                    f"조회 전용 모드에서 막혀 있습니다. "
                    f"MERITZ_READ_ONLY=0 으로 실행하면 사용할 수 있습니다."),
        "api_type": api["key"],
    }
