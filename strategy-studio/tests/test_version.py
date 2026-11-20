"""판 번호가 세 곳에서 어긋나지 않는지 본다.

릴리스와 서버가 서로 다른 번호를 답한 적이 있다. 사람이 세 파일을 같이
고쳐야 하는 구조였기 때문이다. __init__ 이 정본이고 나머지가 그것과 같은지
여기서 검사한다.
"""
from __future__ import annotations

import json
import pathlib
import re

from meritz import __version__

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pyproject_matches_the_package_version():
    found = re.search(r'^version\s*=\s*"([^"]+)"',
                      (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert found and found.group(1) == __version__


def test_manifest_matches_the_package_version():
    data = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == __version__


def test_shipped_corrections_carry_no_audit_records():
    """동봉되는 데이터에 감사 기록이 없어야 한다.

    도구 응답만 막으면 파일은 여전히 배포물에 들어가고, llms.txt 가 그 파일을
    읽으라고 안내한다. 경로만 옮겨질 뿐 막힌 게 아니다.
    """
    data = json.loads((ROOT / "meritz/data/corrections.json").read_text(encoding="utf-8"))
    assert "resolved" not in data, "해소된 결함 이력은 배포본에 넣지 않는다"
    assert "checked_at" not in data and "how_checked" not in data
    for fix in data["corrections"]:
        bad = [k for k in fix if k.startswith("note_") or k in ("verified", "note")]
        assert not bad, f"감사 칸이 남아 있습니다: {bad}"
