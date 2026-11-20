#!/usr/bin/env python3
"""양 OS 공용 '내부 테스트' onedir 빌드 (미서명·비release, 핀 게이트 없음).

정본 배포는 packaging/build_native.py(맥 arm64 핀 환경) + wrap_macos.py / windows/prepare_installer.py 를 쓴다.
이 스크립트는 그 add-data 구성을 그대로 복제하되 핀 검증만 생략해, 맥/윈도우 러너(또는 로컬)에서
동일하게 더블클릭 실행 번들을 만든다. 템플릿(DSL) 백테스트·import 는 양 OS 동작,
Windows Python 전략은 동봉된 WASI 실행기를 사용한다.
"""
import argparse, os, platform, subprocess, sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]

# 번들에 함께 넣는 고지·라이선스 문서. (저장소 경로, 번들 내 파일명) — build_native.py 와 같은 세트.
NOTICE_FILES = [("packaging/PLATFORM-NOTICES.txt", "PLATFORM-NOTICES.txt"),
                ("packaging/TZDATA-NOTICES.txt", "TZDATA-NOTICES.txt"),
                ("DISCLAIMER.md", "DISCLAIMER.md"), ("LICENSE", "LICENSE")]


def generate_third_party_notices(python, src, dest):
    """빌드 환경(python=sys.executable)에서 gen_notices.py 를 실행해 결과를 dest(산출물 폴더용 임시 파일)에 쓴다.
    저장소의 THIRD-PARTY-NOTICES.txt 는 건드리지 않는다 — 번들에는 이 생성본이 들어간다."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run([str(python), str(Path(src) / "gen_notices.py")], cwd=str(src), env=env,
                            capture_output=True, check=True)
    text = result.stdout.decode("utf-8")
    if "구성요소별 라이선스 전문" not in text:
        sys.exit("gen_notices.py 출력이 비정상입니다(라이선스 전문 절 없음). 빌드 환경에서 실행했는지 확인하세요.")
    dest = Path(dest)
    dest.write_text(text, encoding="utf-8")
    return dest


def bundle_includes_webview(bundle):
    """PyInstaller onedir 번들에 pywebview 패키지가 실제로 수집됐는지(_internal/webview)."""
    return (Path(bundle) / "_internal" / "webview").is_dir()


def check_webview_notice(bundle, notices_path, target_os=None):
    """번들에 pywebview 가 들어갔으면 번들 고지에 'pywebview' 가 있어야 한다. 없으면 비정상 종료(고지 누락 배포 방지)."""
    if not bundle_includes_webview(bundle):
        return False
    text = Path(notices_path).read_text(encoding="utf-8", errors="replace").lower()
    required = ["pywebview"]
    if (target_os or platform.system()) == "Windows":
        required.extend(["pythonnet", "clr-loader"])
    missing = [name for name in required if name not in text]
    if missing:
        sys.exit(f"번들에 webview 패키지가 포함됐지만 {Path(notices_path).name} 에 다음 고지가 없습니다: {', '.join(missing)}")
    return True


def copy_notice_files(src, bundle, generated_notices):
    """PLATFORM/TZDATA 고지, DISCLAIMER, LICENSE 와 생성된 THIRD-PARTY-NOTICES.txt 를 번들 최상위에 넣는다."""
    src, bundle = Path(src), Path(bundle)
    for relative, name in NOTICE_FILES:
        path = src / relative
        if not path.is_file():
            sys.exit(f"필수 고지 파일이 없습니다: {relative}")
        (bundle / name).write_bytes(path.read_bytes())
    (bundle / "THIRD-PARTY-NOTICES.txt").write_bytes(Path(generated_notices).read_bytes())


def webview_collect_args():
    """네이티브 창(임베디드 웹뷰) 번들 인자. pywebview 가 빌드 환경에 없으면 빈 목록을 주고 알린다
    (앱은 런타임에 브라우저 모드로 폴백하므로 빌드는 깨지지 않는다)."""
    import importlib.util
    if importlib.util.find_spec("webview") is None:
        print("[build_internal] pywebview 미설치 → 자체 창 없이(브라우저 폴백) 빌드합니다. "
              "`pip install pywebview` 후 다시 빌드하면 창 모드가 포함됩니다.")
        return []
    args = ["--collect-all", "webview", "--collect-submodules", "webview"]
    if platform.system() == "Darwin":      # cocoa 백엔드: pyobjc 코어 + 사용하는 프레임워크 바인딩
        for pkg in ("objc", "Foundation", "AppKit", "WebKit"):
            if importlib.util.find_spec(pkg) is not None:
                args += ["--collect-all", pkg]
        args += ["--hidden-import", "webview.platforms.cocoa"]
    elif platform.system() == "Windows":   # edgechromium(WebView2) 백엔드: pythonnet 로더
        for pkg in ("clr_loader", "pythonnet"):
            if importlib.util.find_spec(pkg) is not None:
                args += ["--collect-all", pkg]
        args += ["--hidden-import", "webview.platforms.winforms", "--hidden-import", "webview.platforms.edgechromium"]
    return args


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-root", type=Path, required=True, help="새 빌드 폴더(비어 있어야 함)")
    args = ap.parse_args()
    out = args.output_root.resolve()
    if out.exists() and any(out.iterdir()):
        ap.error('Output directory must be empty; existing builds are never overwritten.')
    out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(SRC / "packaging"))
    from timezone_support import PYINSTALLER_ARGS  # tzdata 수집 인자

    # PyInstaller 실행 전에 빌드 환경 기준 제3자 고지를 산출물 폴더에 생성한다(저장소 파일은 수정하지 않음).
    notices = generate_third_party_notices(sys.executable, SRC, out / "THIRD-PARTY-NOTICES.generated.txt")
    print(f"[build_internal] third-party notices -> {notices} ({notices.stat().st_size} bytes)")

    sep = os.pathsep  # macOS/Linux ':' , Windows ';'
    cmd = [sys.executable, "-m", "PyInstaller", "--onedir", "--name", "meritz-strategy-platform",
           "--distpath", str(out / "dist"), "--workpath", str(out / "work"), "--specpath", str(out),
           "--paths", str(SRC), "--paths", str(SRC / "sdk"),
           "--collect-all", "fastmcp", "--collect-submodules", "mcp", "--collect-all", "pydantic",
           "--collect-all", "meritz",
           "--hidden-import", "statistics", "--hidden-import", "decimal",
           "--hidden-import", "fractions", "--log-level", "WARN", *PYINSTALLER_ARGS]
    cmd += webview_collect_args()
    for module in ("runtime", "analytics", "optimizer", "reports", "indicators", "meritz_strategy"):
        cmd += ["--collect-submodules", module]
    for s, dest in [("studio/dist/index.html", "studio/dist"),
                    ("runtime/strategy.schema.json", "runtime"), ("strategies", "strategies"),
                    ("sdk/meritz_strategy/__init__.py", "sdk/meritz_strategy"),
                    ("meritz/strategy_worker.py", "meritz"),
                    ("indicators/calc.py", "indicators")]:
        cmd += ["--add-data", f"{SRC / s}{sep}{dest}"]
    if platform.system() == 'Windows':
        from windows.wasi_bundle import prepare, verify
        wasi = prepare(out)
        cmd += ['--paths', str(wasi / 'host'), '--hidden-import', 'wasmtime',
                    '--add-data', str(wasi) + os.pathsep + 'strategy_wasi']
    cmd += [str(SRC / "platform_entry.py")]
    subprocess.run(cmd, cwd=str(SRC), check=True)

    bundle = out / "dist" / "meritz-strategy-platform"
    if platform.system() == 'Windows':
        verify(bundle)
    if platform.system() == "Darwin":
        launcher = bundle / "Start.command"
        launcher.write_text("#!/bin/zsh\ncd \"${0:A:h}\"\nexec ./meritz-strategy-platform\n")
        launcher.chmod(0o755)
    else:
        (bundle / "Start.cmd").write_text("@echo off\r\ncd /d \"%~dp0\"\r\nmeritz-strategy-platform.exe\r\n")

    from claude_code_launcher import write_launcher
    write_launcher(bundle, platform.system())
    n = len(list((bundle / "_internal" / "strategies").glob("*.json")))
    copy_notice_files(SRC, bundle, notices)
    webview_bundled = check_webview_notice(bundle, bundle / "THIRD-PARTY-NOTICES.txt")
    print(f"[build_internal] notices: {', '.join(name for _, name in NOTICE_FILES)}, THIRD-PARTY-NOTICES.txt(generated); "
          f"webview bundled={webview_bundled}")
    print(f"[build_internal] OS={platform.system()} arch={platform.machine()} templates={n} (35 기대)")
    print(f"[build_internal] bundle -> {bundle}")
    if n != 35:
        sys.exit(f"템플릿 번들 실패: {n}개 (strategies add-data 확인)")


if __name__ == "__main__":
    main()
