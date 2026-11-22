"""실행 설정.

배포 기준은 **운영 도메인**이다. 다른 서버로 붙어야 하면 MERITZ_BASE_URL 로 지정한다.
앱키는 발급받은 환경에서만 통한다.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

PROD = "https://openapi.imeritz.com:9443"
PROD_WS = "wss://openapi.imeritz.com:29443/websocket"


def _env_file_candidates() -> list[Path]:
    """.env 를 찾아볼 곳. 앞에 있는 것이 이긴다.

    단일 실행 파일(PyInstaller --onefile)에서는 __file__ 이 실행할 때마다
    새로 만들어지는 **임시 추출 폴더**를 가리킨다. 그 기준으로만 찾으면
    이용자가 실행 파일 옆에 둔 .env 를 영영 읽지 못한다. sys.frozen 일 때
    실행 파일이 있는 폴더를 맨 앞에 넣는다. 소스로 실행할 때의 탐색 경로는
    그대로다.
    """
    here = Path(__file__).resolve().parent
    paths = [here.parent / ".env", here.parent.parent / ".env"]
    if getattr(sys, "frozen", False):
        paths.insert(0, Path(sys.executable).resolve().parent / ".env")
    return paths


def _load_env_file() -> None:
    """_env_file_candidates() 중 처음 찾은 .env 를 읽는다.

    기존 환경변수를 덮지 않는다. MERITZ_NO_ENV_FILE=1 이면 읽지 않는다 —
    배포 기본값을 그대로 확인할 때 쓴다.
    """
    if os.getenv("MERITZ_NO_ENV_FILE", "").strip() in ("1", "true", "yes"):
        return
    for p in _env_file_candidates():
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            k, v = _parse_env_line(line)
            if k:
                os.environ.setdefault(k, v)
        return


def _parse_env_line(line: str) -> tuple[str | None, str]:
    """.env 한 줄을 읽는다.

    export 접두어·따옴표·인라인 주석을 처리한다. 이걸 안 하면
    MERITZ_APP_SECRET="abc" 가 따옴표째 값이 되어 원인 불명의 인증 실패가 난다.
    """
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None, ""
    if line.startswith("export "):
        line = line[7:].lstrip()
    k, v = line.split("=", 1)
    k, v = k.strip(), v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]                      # 따옴표 안은 주석으로 자르지 않는다
    elif " #" in v:
        v = v.split(" #", 1)[0].rstrip()
    return (k or None), v


# 조회 전용으로 시작한다. 모의계좌가 없어 앱키에 묶인 계좌는 언제나 실계좌이고,
# 이 저장소는 AI 에이전트가 실행할 수 있는 경로를 동봉한다. 기본값이 유일한
# 완충재다. 주문·환전을 쓰려면 MERITZ_READ_ONLY=0 으로 명시해서 연다.
#
# 런타임에서 덮어쓰지 않는다 — 진입점에서만 켜면 라이브러리로 직접 임포트해
# 쓰는 경로가 서버 경로보다 덜 안전해진다.
_READ_ONLY_DEFAULT = "1"


def set_read_only_default(value: bool) -> None:
    """기본값을 바꾼다. 환경변수(MERITZ_READ_ONLY)가 있으면 그쪽이 이긴다.

    기본값은 이미 조회 전용이다. 이 함수는 기본값을 **여는** 쪽으로 쓰지
    않는다 — 테스트가 기본값을 되돌릴 때만 쓴다.
    """
    global _READ_ONLY_DEFAULT
    _READ_ONLY_DEFAULT = "1" if value else "0"


def _flag(name: str, default: str = "0") -> bool:
    raw = os.getenv(name)
    raw = default if raw is None or not raw.strip() else raw
    v = raw.strip().lower()
    if v in ("1", "true", "y", "yes", "on"):
        return True
    if v in ("0", "false", "n", "no", "off"):
        return False
    # 알아볼 수 없는 값이면 기본값으로 돌아간다. 안전 스위치가 오타 하나로
    # 열리면 안 된다.
    return default.strip().lower() in ("1", "true", "y", "yes", "on")


@dataclass
class Settings:
    # 계좌를 가리키는 사람 이름. 앱키 하나에 계좌 하나가 묶이므로, 앱키를
    # 여러 개 등록하면 이 라벨이 곧 계좌 선택지가 된다. 비밀이 아니라서
    # repr 에 남긴다 — 오류 메시지에 "어느 계좌였나" 가 보여야 한다.
    label: str = "default"
    env: str = "prod"
    base_url: str = PROD
    ws_url: str = PROD_WS
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    timeout: int = 15
    read_only: bool = True
    # repr=False — 예외 트레이스백·로그에 시크릿이 실리는 것을 막는다
    app_key: str | None = field(default=None, repr=False)
    app_secret: str | None = field(default=None, repr=False)

    @property
    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret)


_PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")


def _env(name: str) -> str | None:
    """환경변수를 읽되 **치환되지 않은 자리표시자는 없는 값으로 본다.**

    Claude Desktop 은 설정 칸이 비어 있으면 치환을 건너뛰고
    "${user_config.label_2}" 같은 문자열을 그대로 넘긴다. 그대로 믿으면
    이름 자리에 자리표시자가 박히고, 앱키 자리에서는 **값이 있는 것처럼
    보여 유령 계좌가 생긴다.**
    """
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or _PLACEHOLDER.match(raw):
        return None
    return raw


def _slot_suffixes() -> list[str]:
    """등록된 슬롯 번호. 접미사 없는 것이 1번이다.

    번호는 설정 배관일 뿐이고, 사람과 모델이 보는 것은 라벨이다.
    빈 슬롯은 건너뛴다 — 2번을 지우고 3번만 남겨도 동작해야 한다.
    """
    out = [""]
    n = 2
    misses = 0
    while misses < 3:            # 번호가 띄엄띄엄해도 몇 칸은 더 본다
        suffix = f"_{n}"
        if _env("MERITZ_APP_KEY" + suffix):
            out.append(suffix)
            misses = 0
        else:
            misses += 1
        n += 1
    return out


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} 은 정수여야 합니다 (받은 값: {raw!r})") from None


def load_profiles() -> list[Settings]:
    """등록된 계좌를 전부 읽는다. 첫 번째가 기본이다.

      MERITZ_APP_KEY   / MERITZ_APP_SECRET   / MERITZ_LABEL      1번(기본)
      MERITZ_APP_KEY_2 / MERITZ_APP_SECRET_2 / MERITZ_LABEL_2    2번
      …

    앱키 하나에 계좌 하나가 묶이므로 **앱키 목록이 곧 계좌 목록**이다.
    라벨이 겹치면 세우지 않는다 — 조용히 하나를 덮으면 주문이 엉뚱한 계좌로
    나간다.
    """
    out = [_build(sfx) for sfx in _slot_suffixes()]
    seen: dict[str, str] = {}
    for st in out:
        if st.label in seen:
            raise ValueError(
                f"계좌 이름이 겹칩니다: {st.label!r}. "
                f"MERITZ_LABEL 로 서로 다른 이름을 주십시오.")
        seen[st.label] = st.label
    return out


def settings() -> Settings:
    """기본 계좌의 설정. 기존 호출부가 그대로 쓰는 경로다.

      MERITZ_BASE_URL   도메인 직접 지정 (기본은 운영)
      MERITZ_WS_URL     실시간 주소 직접 지정
      MERITZ_READ_ONLY  1 이면 주문·환전을 막는다 (기본 1)
      MCP_TYPE / MCP_HOST / MCP_PORT / MCP_PATH
    """
    return _build("")


def _build(suffix: str) -> Settings:
    _load_env_file()
    base, ws, env = PROD, PROD_WS, "prod"

    override = (os.getenv("MERITZ_BASE_URL") or "").rstrip("/")
    if override:
        u = urlparse(override)
        if u.scheme != "https" and (u.hostname or "") not in ("localhost", "127.0.0.1"):
            raise ValueError(
                f"MERITZ_BASE_URL 은 https 여야 합니다 (받은 값: {override}). "
                "http 로 보내면 앱시크릿이 평문으로 나갑니다.")
        base = override
        # 포트를 빠뜨린 주소도 같은 서버로 본다. 문자열 완전일치로 판정하면
        # 다른 서버를 지정하고도 실시간만 운영에 붙는 사고가 난다.
        host = (u.hostname or "").lower()
        if host == urlparse(PROD).hostname:
            env, ws = "prod", PROD_WS
        else:
            env, ws = "custom", None      # 추정하지 않는다 — MERITZ_WS_URL 로 지정해야 한다

    ws_url = os.getenv("MERITZ_WS_URL") or ws
    if ws_url is None:
        ws_url = ""                       # 실시간을 쓰려면 명시해야 한다

    label = _env("MERITZ_LABEL" + suffix) or ""
    if not label:
        # 이름을 안 적으셨을 때. 사람이 외우기 쉬운 것으로 준다 —
        # 모델은 이 이름으로 계좌를 고르므로 "slot_2" 같은 기계 이름은
        # 대화에서 쓰기 나쁘다.
        label = "1번계좌" if suffix == "" else f"{suffix[1:]}번계좌"
    return Settings(
        label=label,
        env=env, base_url=base, ws_url=ws_url,
        transport=os.getenv("MCP_TYPE", "stdio"),
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=_int_env("MCP_PORT", 8000),
        path=os.getenv("MCP_PATH", "/mcp"),
        timeout=_int_env("MERITZ_TIMEOUT", 15),
        # 조회 전용은 전역이 닫혀 있으면 슬롯이 열 수 없다. 슬롯은 잠그는
        # 방향으로만 쓴다 — 소액 계좌 하나 열자고 주력 계좌까지 열리면 안 된다.
        read_only=(_flag("MERITZ_READ_ONLY", _READ_ONLY_DEFAULT)
                   or _flag("MERITZ_READ_ONLY" + suffix, "0")),
        app_key=_env("MERITZ_APP_KEY" + suffix),
        app_secret=_env("MERITZ_APP_SECRET" + suffix),
    )


