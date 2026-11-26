import json
from datetime import datetime

import pytest

from runtime.claude_install import SERVER_NAME, install


def test_register_preserves_existing_config_and_backs_it_up(tmp_path):
    config_path = tmp_path / "claude_desktop_config.json"
    original = {"preferences": {"theme": "dark"}, "mcpServers": {"other": {"command": "/other"}}}
    config_path.write_text(json.dumps(original), encoding="utf-8")
    config_path.chmod(0o600)
    executable = tmp_path / "meritz-strategy-platform"
    executable.write_text("binary", encoding="utf-8")

    result = install(config_path, executable, now=datetime(2026, 11, 17, 20, 1, 2))

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["preferences"] == original["preferences"]
    assert saved["mcpServers"]["other"] == original["mcpServers"]["other"]
    assert saved["mcpServers"][SERVER_NAME] == {
        "command": str(executable.resolve()), "args": ["--strategy-mcp"],
        "env": {"MERITZ_READ_ONLY": "1"}}
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert "주문·환전 도구는 포함하지 않습니다" in result
    backup = config_path.with_name("claude_desktop_config.json.bak-20261117-200102")
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_existing_identical_registration_is_idempotent(tmp_path):
    executable = tmp_path / "app"
    executable.write_text("binary")
    expected = {"command": str(executable.resolve()), "args": ["--strategy-mcp"],
                "env": {"MERITZ_READ_ONLY": "1"}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"mcpServers": {SERVER_NAME: expected}}))
    before = config_path.read_bytes()
    assert "이미 Claude에 등록" in install(config_path, executable)
    assert config_path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*"))


def test_conflicting_registration_is_not_overwritten(tmp_path):
    executable = tmp_path / "app"
    executable.write_text("binary")
    config_path = tmp_path / "config.json"
    original = {"mcpServers": {SERVER_NAME: {"command": "/user/server"}}}
    config_path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="덮어쓰지 않았습니다"):
        install(config_path, executable)
    assert json.loads(config_path.read_text()) == original


@pytest.mark.parametrize("contents", ["{", "[]", '{"mcpServers": []}'])
def test_invalid_config_is_left_untouched(tmp_path, contents):
    executable = tmp_path / "app"
    executable.write_text("binary")
    config_path = tmp_path / "config.json"
    config_path.write_text(contents)
    with pytest.raises(ValueError):
        install(config_path, executable)
    assert config_path.read_text() == contents
