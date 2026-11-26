"""실행 파일과 함께 배포되는 오픈소스의 라이선스 전문과 저작권 표시를 모읍니다.

  python gen_notices.py > THIRD-PARTY-NOTICES.txt

**빌드 환경(.build-venv)에서 실행해야 합니다.** 개발용 가상환경에서 돌리면
실행 파일에 들어가지 않는 pytest 가 목록에 들어가고, 정작 PyInstaller 와
mcp[cli] 가 끌고 오는 것들이 빠집니다. build_binary.sh 가 빌드 직전에
이 스크립트를 부르는 이유입니다.

왜 이름·버전 목록으로는 부족한가
  MIT·BSD·ISC 는 저작권 표시와 허가문 **전문**을 사본에 동봉할 것을 조건으로
  겁니다. Apache-2.0 은 라이선스 사본 전달과, 원본에 NOTICE 파일이 있으면
  그 내용의 전달을 요구합니다(제4조). 라이선스 이름만 적은 목록은 이 조건을
  채우지 못합니다. 그래서 각 배포판의 dist-info 에 들어 있는 LICENSE·COPYING·
  NOTICE 파일을 찾아 **그대로** 싣습니다.

MPL-2.0 구성요소는 이름을 지목하고 소스 입수 경로를 적습니다(MPL_SOURCE).
PyInstaller 는 GPL-2.0-or-later 이며 bootloader exception 이 붙습니다 —
PYINSTALLER_NOTE 와 아래 실리는 COPYING.txt 전문을 보십시오.
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 라이선스 원문이 들어 있을 만한 파일 이름. 대소문자·확장자는 배포판마다 다르다.
_LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")
_SKIP_SUFFIX = (".py", ".pyc", ".pyo", ".so", ".pyd")

# Apache-2.0 제4조(d)가 전달을 요구하는 파일. 별도 절로 뺀다.
_NOTICE_RE = re.compile(r"(^|[/\\])NOTICE", re.I)

# MPL-2.0 구성요소 — 이름을 지목하고 해당 버전 소스의 입수 경로를 적는다.
MPL_SOURCE = {
    "certifi": "https://pypi.org/project/certifi/#files",
}

HEAD = """메리츠증권 Open API — 제3자 오픈소스 고지

이 실행 파일은 아래 오픈소스를 써서 만들어졌습니다. 각 저작권자에게 권리가
있으며, 각 라이선스 조건에 따라 재배포됩니다. 아래에 구성요소마다 라이선스
전문과 저작권 표시를 그대로 실었습니다.

목록은 빌드 환경에 설치된 배포판 전부입니다. 실행 파일에 실제로 들어가는
것보다 넓은 범위이며, 빠진 것이 없도록 넓은 쪽을 택했습니다.

차례
  1. PyInstaller 와 bootloader exception
  2. MPL-2.0 구성요소와 소스 입수 경로
  3. Apache-2.0 구성요소의 NOTICE
  4. 구성요소별 라이선스 전문
"""

PYINSTALLER_NOTE = """--------------------------------------------------------------------------
1. PyInstaller 와 bootloader exception
--------------------------------------------------------------------------

이 실행 파일은 PyInstaller 로 만들었습니다. PyInstaller 자체는 GNU General
Public License version 2 or later 로 배포되고, --onefile 산출물에는
PyInstaller 의 부트로더(bootloader)와 PyInstaller/loader 의 파일들이 들어
갑니다.

PyInstaller 의 라이선스에는 bootloader exception 이 붙어 있습니다. 부트로더와
그에 딸린 파일을 다른 프로그램과 결합해 배포하는 데 대해 GPL 에서 오는 제약을
두지 않는다는 조항입니다. 이 실행 파일은 부트로더를 수정하지 않은 채 그대로
포함하므로, 이 예외에 따라 이 저장소의 라이선스(MIT)로 배포됩니다.

PyInstaller 가 함께 넣는 run-time hook(PyInstaller/hooks/rthooks)과 run-time
module(PyInstaller/fake-modules)은 Apache License 2.0 입니다.

조항 전문은 아래 «4. 구성요소별 라이선스 전문» 의 pyinstaller 항목에 실린
COPYING.txt 에 있습니다.
"""


def _self_name() -> str:
    """자기 패키지는 제3자가 아니다. pyproject.toml 에서 이름을 읽어 뺀다."""
    try:
        text = (HERE / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
    return _norm(m.group(1)) if m else ""


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _license_expression(m) -> str:
    lic = (m.get("License-Expression") or "").strip()
    if lic:
        return lic
    cls = [c for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
    if cls:
        return " / ".join(sorted({c.split("::")[-1].strip() for c in cls}))
    first = (m.get("License") or "").strip().splitlines()
    return first[0][:80] if first else "(라이선스 표기 없음)"


def _info_dir(dist) -> Path | None:
    """dist-info 디렉터리. 라이선스 파일은 전부 여기 아래에 있다."""
    p = getattr(dist, "_path", None)
    if p is not None:
        p = Path(str(p))
        if p.is_dir():
            return p
    for f in dist.files or []:
        parts = Path(str(f)).parts
        for i, part in enumerate(parts):
            if part.endswith((".dist-info", ".egg-info")):
                return Path(dist.locate_file(Path(*parts[: i + 1])))
    return None


def _license_docs(info_dir: Path | None) -> list[tuple[str, str]]:
    """(표시 이름, 전문) 목록. licenses/ 하위와 dist-info 바로 아래를 함께 본다."""
    if info_dir is None:
        return []
    docs: list[tuple[str, str]] = []
    seen: set[str] = set()
    candidates: list[Path] = []
    lic_dir = info_dir / "licenses"
    if lic_dir.is_dir():
        candidates += sorted(lic_dir.rglob("*"))
    candidates += sorted(info_dir.glob("*"))
    for f in candidates:
        if not f.is_file() or f.suffix.lower() in _SKIP_SUFFIX:
            continue
        if not f.name.upper().startswith(_LICENSE_NAMES):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        rel = f.relative_to(info_dir).as_posix()
        docs.append((rel, text))
    return docs


def _upstream(m) -> str:
    for url in m.get_all("Project-URL") or []:
        label, _, value = url.partition(",")
        if label.strip().lower() in ("homepage", "repository", "source", "source code"):
            return value.strip()
    return (m.get("Home-page") or "").strip()


def _collect() -> list[dict]:
    self_name = _self_name()
    rows: dict[str, dict] = {}
    for dist in md.distributions():
        m = dist.metadata
        name = m["Name"]
        if not name or _norm(name) == self_name:
            continue
        key = _norm(name)
        if key in rows:            # 같은 배포판이 두 경로에서 잡히는 경우
            continue
        rows[key] = {
            "name": name,
            "version": dist.version or "",
            "license": _license_expression(m),
            "upstream": _upstream(m),
            "docs": _license_docs(_info_dir(dist)),
        }
    return [rows[k] for k in sorted(rows)]



def _standard_text(expr: str, rows: list[dict]) -> str | None:
    """라이선스 파일을 휠에 넣지 않은 배포판을 위해 같은 라이선스의 전문을 찾는다.

    Apache-2.0 §4(a) 는 라이선스 **사본** 전달을 요구한다. URL 안내는 사본이
    아니다. Apache-2.0·MPL-2.0 처럼 전문이 한 벌로 고정된 라이선스는 같은
    묶음에 들어 있는 다른 배포판의 사본을 그대로 실으면 의무를 채운다.
    """
    fixed = {"apache-2.0", "apache software license", "mpl-2.0",
             "mozilla public license 2.0 (mpl 2.0)"}
    if expr.strip().lower() not in fixed:
        return None
    for other in rows:
        if other["license"].strip().lower() != expr.strip().lower():
            continue
        for name, text in other["docs"]:
            if _NOTICE_RE.search(name):
                continue
            if "END OF TERMS AND CONDITIONS" in text or "Mozilla Public License" in text:
                return text
    return None

def _rule(title: str) -> str:
    bar = "-" * 74
    return f"{bar}\n{title}\n{bar}\n"


def main() -> int:
    rows = _collect()
    if not rows:
        print("설치된 배포판을 찾지 못했습니다. 빌드 환경에서 실행하십시오.",
              file=sys.stderr)
        return 1

    out: list[str] = [HEAD, "", PYINSTALLER_NOTE, ""]

    # 2. MPL-2.0 ------------------------------------------------------------
    out.append(_rule("2. MPL-2.0 구성요소와 소스 입수 경로"))
    mpl = [r for r in rows
           if "MPL" in r["license"].upper() or "MOZILLA" in r["license"].upper()
           or _norm(r["name"]) in MPL_SOURCE]
    if mpl:
        out.append("MPL-2.0 은 해당 부분의 소스를 받을 수 있게 할 것을 요구합니다.\n")
        for r in mpl:
            url = MPL_SOURCE.get(_norm(r["name"])) or r["upstream"]
            out.append(f"  {r['name']} {r['version']} — {r['license']}")
            out.append(f"    소스: {url}")
        out.append("")
    else:
        out.append("이 빌드에는 MPL-2.0 구성요소가 없습니다.\n")

    # 3. Apache-2.0 NOTICE --------------------------------------------------
    out.append(_rule("3. Apache-2.0 구성요소의 NOTICE"))
    notices = [(r, n, t) for r in rows for n, t in r["docs"] if _NOTICE_RE.search(n)]
    if notices:
        out.append("Apache License 2.0 제4조(d)에 따라 원본의 NOTICE 를 그대로 싣습니다.\n")
        for r, n, t in notices:
            out.append(f"■ {r['name']} {r['version']} — {n}\n")
            out.append(t)
            out.append("")
    else:
        out.append("이 빌드의 Apache-2.0 구성요소 중 NOTICE 파일을 배포에 포함한 것은\n"
                   "없습니다. 각 구성요소의 라이선스 전문은 아래 4장에 있습니다.\n")

    # 4. 전문 ---------------------------------------------------------------
    out.append(_rule("4. 구성요소별 라이선스 전문"))
    missing: list[str] = []
    for r in rows:
        out.append(f"■ {r['name']} {r['version']} — {r['license']}")
        if r["upstream"]:
            out.append(f"  {r['upstream']}")
        out.append("")
        body = [(n, t) for n, t in r["docs"] if not _NOTICE_RE.search(n)]
        if body:
            for n, t in body:
                out.append(f"[{n}]")
                out.append(t)
                out.append("")
        else:
            missing.append(f"{r['name']} {r['version']}")
            out.append(f"  이 배포판은 라이선스 파일을 휠에 넣지 않았습니다. "
                       f"라이선스는 {r['license']} 입니다.")
            fallback = _standard_text(r["license"], rows)
            if fallback:
                out.append("  같은 라이선스의 표준 전문을 아래에 싣습니다.")
                out.append("")
                out.append(f"[{r['license']}]")
                out.append(fallback)
            else:
                out.append("  전문은 위 배포처에서 확인할 수 있습니다.")
            out.append("")
        out.append("")

    print("\n".join(out))
    if missing:
        print("라이선스 파일이 없는 배포판: " + ", ".join(missing), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
