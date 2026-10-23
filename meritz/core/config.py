"""실행 설정.

배포 기준은 **운영 도메인**이다. 다른 서버로 붙으려면 MERITZ_BASE_URL 로 지정한다.
앱키는 발급받은 환경에서만 통한다.
"""
from __future__ import annotations

import os
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
    env: str = "prod"
    base_url: str = PROD
    ws_url: str = PROD_WS
    timeout: int = 15
    read_only: bool = True
    # repr=False — 예외 트레이스백·로그에 시크릿이 실리는 것을 막는다
    app_key: str | None = field(default=None, repr=False)
    app_secret: str | None = field(default=None, repr=False)

    @property
    def has_credentials(self) -> bool:
        return bool(self.app_key and self.app_secret)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} 은 정수여야 합니다 (받은 값: {raw!r})") from None


def settings() -> Settings:
    """환경변수에서 설정을 만든다.

      MERITZ_BASE_URL   도메인 직접 지정 (기본은 운영)
      MERITZ_WS_URL     실시간 도메인 직접 지정
      MERITZ_READ_ONLY  1 이면 주문·환전을 막는다 (기본 1)
      MERITZ_TIMEOUT    HTTP 타임아웃 초 (기본 15)
    """
    _load_env_file()
    base, env = PROD, "prod"
    ws: str | None = PROD_WS

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

    return Settings(
        env=env, base_url=base, ws_url=ws_url,
        timeout=_int_env("MERITZ_TIMEOUT", 15),
        read_only=_flag("MERITZ_READ_ONLY", _READ_ONLY_DEFAULT),
        app_key=os.getenv("MERITZ_APP_KEY"),
        app_secret=os.getenv("MERITZ_APP_SECRET"),
    )
