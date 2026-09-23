"""접근 토큰 발급.

  export MERITZ_APP_KEY=발급받은_앱키
  export MERITZ_APP_SECRET=발급받은_시크릿
  python examples/01_auth.py

토큰은 만료 전까지 재사용한다. **요청마다 새로 받지 않는다.**
TokenManager 가 캐시와 만료를 알아서 처리한다.
"""
from meritz import MeritzError, settings
from meritz.core.client import TokenManager

s = settings()
print(f"서버 {s.env}  {s.base_url}")

try:
    token = TokenManager(s).get()
except MeritzError as e:
    raise SystemExit(f"발급 실패: {e}") from None

# 토큰은 자격증명이다. 로그·화면에 전문을 남기지 않는다.
print(f"발급 성공  {token[:12]}…")
