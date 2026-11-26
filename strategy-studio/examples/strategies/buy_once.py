"""Interface example: buy once at the next open, then retain the position."""


def on_bar(ctx):
    if ctx["index"] == 0:
        return {"action": "buy", "reason": "첫 관측 다음 봉에 매수하는 계약 예제"}
    return {"action": "hold"}
