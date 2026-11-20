"""계좌(앱키) 레지스트리.

앱키 하나에 계좌 하나가 묶인다. 요청에 계좌번호를 넣지 않아도 그 앱키의
계좌가 조회되고 주문도 그 계좌로 나간다. 그래서 **앱키를 고르는 일이 곧
계좌를 고르는 일**이다.

설계에서 지키는 것 둘.

  ① 현재 계좌를 바꾸는 수단을 두지 않는다. "계좌를 B로 바꿔" 같은 숨은
     상태를 만들면, 바꾸고 주문하는 사이에 턴이 끼는 순간 엉뚱한 계좌로
     주문이 나간다. 되돌릴 수 없는 종류의 실패다. 호출마다 지정하게 한다.

  ② 주문에는 기본 계좌를 적용하지 않는다. 조회가 틀리면 다시 보면 되지만
     주문은 그렇지 않다.
"""
from __future__ import annotations

import hashlib

from .client import ApiClient, MeritzError
from .config import Settings, load_profiles


def key_hint(app_key: str | None) -> str:
    """앱키를 가리키되 원문을 드러내지 않는다.

    REST 응답 어디에도 계좌번호가 없어, 서버는 이 앱키가 실제로 어느 계좌인지
    알 수 없다. 이용자가 미리보기 화면에서 대조할 수 있는 단서는 이것뿐이다.
    """
    if not app_key:
        return "없음"
    return hashlib.sha256(app_key.encode()).hexdigest()[:8]


class Registry:
    """기동 시 한 번 만들고 그 뒤로는 바뀌지 않는다."""

    def __init__(self, profiles: list[Settings] | None = None):
        self.profiles = profiles if profiles is not None else load_profiles()
        # 라벨이 겹치면 세우지 않는다. 조용히 하나를 덮으면 이용자가 고른
        # 이름과 다른 계좌로 주문이 나간다.
        seen: set[str] = set()
        for st in self.profiles:
            if st.label in seen:
                raise ValueError(
                    f"계좌 이름이 겹칩니다: {st.label!r}. "
                    f"MERITZ_LABEL 로 서로 다른 이름을 주십시오.")
            seen.add(st.label)
        self.default = self.profiles[0]
        # TPS 한도가 앱키별인지 전체인지 명세에 없다. 모르는 상태에서 나누면
        # 틀렸을 때 한도를 넘고, 합치면 틀렸을 때 느려질 뿐이라 하나를 쓴다.
        shared_throttle = None
        self._clients: dict[str, ApiClient] = {}
        for st in self.profiles:
            c = ApiClient(st)
            if shared_throttle is None:
                shared_throttle = getattr(c, "_throttle", None)
            elif shared_throttle is not None:
                c._throttle = shared_throttle
            self._clients[st.label] = c

    @property
    def many(self) -> bool:
        return len(self.profiles) > 1

    def labels(self) -> list[str]:
        return [st.label for st in self.profiles]

    def listing(self) -> list[dict]:
        return [{"계좌": st.label,
                 "앱키": key_hint(st.app_key),
                 "자격증명": st.has_credentials,
                 "조회전용": st.read_only,
                 "기본": st.label == self.default.label}
                for st in self.profiles]

    def resolve(self, account: str | None, *, require_explicit: bool) -> ApiClient:
        """라벨로 계좌를 고른다.

        추측하지 않는다 — 부분일치·대소문자 무시로 고르면 비슷한 이름의 다른
        계좌로 주문이 나갈 수 있다. 완전일치만 받는다.
        """
        if account is None or account == "":
            if require_explicit and self.many:
                raise MeritzError(
                    "계좌를 지정하셔야 합니다. 등록된 계좌가 여럿이라 "
                    f"기본값으로 보내지 않습니다. account 에 {' · '.join(self.labels())} "
                    "중 하나를 넣어 다시 부르십시오.",
                    code="ACCOUNT_REQUIRED")
            client = self._clients[self.default.label]
            self._require_credentials(client)
            return client

        client = self._clients.get(account)
        if client is None:
            raise MeritzError(
                f"'{account}' 라는 계좌가 없습니다. "
                f"등록된 계좌는 {' · '.join(self.labels())} 입니다.",
                code="UNKNOWN_ACCOUNT")
        self._require_credentials(client)
        return client

    @staticmethod
    def _require_credentials(client) -> None:
        """키만 넣고 시크릿을 빠뜨린 칸을 여기서 가린다.

        그냥 보내면 게이트웨이가 EGW00103("유효하지 않은 client_id")으로
        거절하는데, 그 메시지로는 "앱키가 틀렸나" 싶어 엉뚱한 곳을 본다.
        무엇이 비었는지 말해 준다.
        """
        st = client.s
        if st.has_credentials:
            return
        missing = []
        if not st.app_key:
            missing.append("App Key")
        if not st.app_secret:
            missing.append("App Secret")
        raise MeritzError(
            f"'{st.label}' 계좌의 {' 와 '.join(missing)} 가 비어 있습니다. "
            f"설정에서 같은 계좌의 키와 시크릿을 짝으로 넣어 주십시오.",
            code="NO_CREDENTIALS")
