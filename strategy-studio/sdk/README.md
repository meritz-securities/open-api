# Meritz Strategy SDK — 베타

이 SDK는 Runtime에 반환할 가상 주문 의도를 작성하는 순수 Python 도우미입니다.
자체 백테스팅 엔진·네트워크 호출·실주문 기능은 포함하지 않습니다.
전략 실행은 Studio/MCP/CLI의 공통 Runtime이 담당합니다.

별도 가상환경에서 제공된 wheel을 설치합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install /path/to/meritz_strategy_sdk-0.1.0-py3-none-any.whl
```

Python 3.11 이상이 필요합니다. Windows wheel 설치 시험은 아직 수행하지 않았습니다.
Windows 개발 환경에서는 `.venv\Scripts\python.exe`를 사용합니다. SDK의 순수 함수
설치와 고객 Python 전략의 OS 격리 실행 지원은 별개입니다. 현재 Runtime의 Python
전략 실행은 macOS만 지원하며 Windows에서 격리 없이 대신 실행하지 않습니다.

```python
from meritz_strategy import Context


def initialize(observation):
    # 첫 봉의 on_bar 이전 한 번 호출됩니다.
    assert Context(observation).get_cash() > 0


def on_bar(observation):
    ctx = Context(observation)
    average = ctx.get_indicator("sma", period=20)
    if average is None:
        return {"action": "hold"}
    if ctx.bar["close"] > average and ctx.get_position()["quantity"] == 0:
        return ctx.buy(1, reason="확정 종가가 SMA20 위")
    if ctx.bar["close"] < average:
        return ctx.close_position(reason="확정 종가가 SMA20 아래")
    return {"action": "hold"}
```

`buy`, `sell`, `close_position`은 주문을 전송하지 않고 dict를 반환합니다.
시장가 의도는 다음 봉 시가에 비용·슬리피지를 적용해 가상 체결합니다.
`get_cash`, `get_position`, `get_portfolio`, `get_indicator`는 전달받은 관측을 사용합니다.
`initialize`/`on_bar` 호출은 Runtime의 책임입니다. 예제 파일을 Python으로 직접 실행하면
함수만 정의되며 백테스트가 수행되는 것은 아닙니다.

현재 공통 지표 패키지 이름은 `indicators`입니다. 동일 이름의 제3자 패키지와 섞지 않도록
전용 가상환경을 사용합니다. wheel은 저장소의 공통 지표 파일을 그대로 포함하며
독립적인 계산 구현을 추가하지 않습니다. 고객 앱에는 필요한 SDK가 이미 동봉됩니다.

저장소 전체 체크아웃에서 wheel만 빌드합니다. 독립 sdist 배포는 아직 제공하지 않습니다.

```sh
python -m build --wheel sdk --outdir /path/to/output
```

빌드 단계는 build/hatchling 설치를 위해 네트워크 또는 사전 준비한 캐시가 필요합니다.
오프라인 검증은 이미 생성된 wheel의 설치 단계에 해당합니다. `--wheel`을 생략한 sdist
빌드와 editable 설치는 이 배포 절차에서 지원·검증하지 않습니다. 배포 버전 0.1.0은
SDK 코드 버전과 일치시킵니다. SDK 는 Studio 와 함께 베타 상태이며, 지원 OS·라이선스 검증은 정식 배포 전에 마칩니다.
