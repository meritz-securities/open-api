"""Interface example: observe every bar without placing a virtual order."""


def on_bar(ctx):
    return {"action": "hold", "reason": "관측만 수행하는 계약 예제"}
