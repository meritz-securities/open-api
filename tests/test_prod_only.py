"""공개 저장소에는 운영 기준만 들어간다.

개발 서버 주소는 사내에서만 닿는다. 그것이 예제·명세·문서에 남으면
고객은 붙지 않는 주소로 먼저 시도하고, 기계가 읽는 openapi.json /
asyncapi.json 에 남으면 AI 도구가 그 주소를 골라 쓴다.
한 번 지우는 것으로는 다시 들어오는 것을 막지 못하므로 여기서 잠근다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MERITZ_NO_ENV_FILE", "1")
sys.path.insert(0, str(ROOT))

# 문자열을 쪼개 둔다 — 이 파일 자신이 검사에 걸리지 않게 하려는 것이고,
# 덕분에 저장소 전체 grep 도 이 파일 때문에 0 건이 깨지지 않는다.
DEV_HOST = "dev" + "api"

PROD_HTTP = "https://openapi.imeritz.com:9443"
PROD_WS = "wss://openapi.imeritz.com:29443/websocket"

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
               ".zip", ".gz", ".whl", ".woff", ".woff2", ".ttf"}


def _tracked_files() -> list[Path]:
    """커밋된 파일만 본다 — 무시된 산출물·가상환경까지 뒤지지 않는다."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git 을 실행할 수 없습니다")
    if out.returncode != 0:  # pragma: no cover
        pytest.skip("git 저장소가 아닙니다")
    return [ROOT / n for n in out.stdout.split("\0") if n]


def _text_files() -> list[Path]:
    me = Path(__file__).resolve()
    files = []
    for p in _tracked_files():
        if not p.is_file() or p.resolve() == me or p.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue                      # 텍스트가 아닌 것은 대상이 아니다
        files.append(p)
    return files


def test_tracked_files_have_no_dev_host():
    """커밋된 텍스트 파일 어디에도 개발 서버 주소가 없어야 한다."""
    hits = []
    for p in _text_files():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if DEV_HOST in line:
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert not hits, ("개발 서버 주소가 남아 있습니다. 운영 기준만 커밋합니다:\n  "
                      + "\n  ".join(hits))


def test_catalog_domains_are_prod_only():
    cat = json.loads((ROOT / "meritz" / "data" / "catalog.json").read_text(encoding="utf-8"))
    apis = cat["apis"]
    assert apis, "카탈로그가 비어 있습니다"
    bad = [a.get("key") for a in apis if set(a.get("domain") or {}) != {"prod"}]
    assert not bad, f"domain 은 prod 만 담습니다: {bad}"


def test_machine_specs_list_only_prod_server():
    """기계가 읽는 명세다 — 목록에 있는 주소는 그대로 호출된다."""
    api = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert [s["url"] for s in api["servers"]] == [PROD_HTTP]

    ws = json.loads((ROOT / "asyncapi.json").read_text(encoding="utf-8"))
    assert list(ws["servers"]) == ["prod"]
    assert ws["servers"]["prod"]["host"] == "openapi.imeritz.com:29443/websocket"


def test_default_settings_point_at_prod(monkeypatch):
    """환경변수가 없으면 운영으로 나간다."""
    monkeypatch.setenv("MERITZ_NO_ENV_FILE", "1")
    for k in ("ENV", "MERITZ_BASE_URL", "MERITZ_WS_URL"):
        monkeypatch.delenv(k, raising=False)
    from meritz.core.config import settings
    s = settings()
    assert (s.env, s.base_url, s.ws_url) == ("prod", PROD_HTTP, PROD_WS)


def test_env_var_no_longer_switches_server(monkeypatch):
    """ENV 분기는 사라졌다 — 남아 있으면 개발 주소가 코드로 되돌아온다."""
    monkeypatch.setenv("MERITZ_NO_ENV_FILE", "1")
    monkeypatch.setenv("ENV", "dev")
    for k in ("MERITZ_BASE_URL", "MERITZ_WS_URL"):
        monkeypatch.delenv(k, raising=False)
    from meritz.core.config import settings
    assert settings().base_url == PROD_HTTP


def test_base_url_override_still_works(monkeypatch):
    """사내 테스트 경로는 남긴다 — 두 변수로 직접 지정한다."""
    monkeypatch.setenv("MERITZ_NO_ENV_FILE", "1")
    monkeypatch.setenv("MERITZ_BASE_URL", "https://example.invalid:9443")
    monkeypatch.setenv("MERITZ_WS_URL", "wss://example.invalid:29443/websocket")
    from meritz.core.config import settings
    s = settings()
    assert s.base_url == "https://example.invalid:9443"
    assert s.ws_url == "wss://example.invalid:29443/websocket"
    assert s.env == "custom"
