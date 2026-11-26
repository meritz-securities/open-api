"""R-06: 빌드 고지 세트 — gen_notices 생성본·PLATFORM/TZDATA/DISCLAIMER/LICENSE 복사·pywebview 고지 검사.
PyInstaller 를 실행하지 않고 빌더 모듈의 단계 함수만 가짜 번들 폴더로 검사한다."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    sys.path.insert(0, str(ROOT / 'packaging'))
    spec = importlib.util.spec_from_file_location(f'notices_test_{name}', ROOT / f'packaging/{name}.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def internal():
    return _load('build_internal')


def _fake_bundle(tmp_path, with_webview):
    bundle = tmp_path / 'dist/meritz-strategy-platform'
    (bundle / '_internal').mkdir(parents=True)
    if with_webview:
        (bundle / '_internal/webview').mkdir()
    return bundle


def test_internal_builder_refuses_nonempty_output_directory(tmp_path):
    import subprocess
    output=tmp_path/'existing-build';output.mkdir();(output/'keep.txt').write_text('keep')
    result=subprocess.run([sys.executable,str(ROOT/'packaging/build_internal.py'),'--output-root',str(output)],capture_output=True,text=True)
    assert result.returncode==2
    assert 'must be empty' in result.stderr
    assert (output/'keep.txt').read_text()=='keep'


def test_generated_notices_come_from_gen_notices_not_repo_file(internal, tmp_path):
    repo = ROOT / 'THIRD-PARTY-NOTICES.txt'
    before = repo.read_bytes()
    dest = internal.generate_third_party_notices(sys.executable, ROOT, tmp_path / 'generated.txt')
    text = dest.read_text(encoding='utf-8')
    assert '구성요소별 라이선스 전문' in text and 'PyInstaller' in text
    assert repo.read_bytes() == before                 # 저장소 고지 원본은 건드리지 않는다.


def test_native_builder_generates_notices_from_target_environment(tmp_path):
    native = _load('build_native')
    dest = native.generate_third_party_notices(sys.executable, ROOT, tmp_path / 'native-notices.txt')
    text = dest.read_text(encoding='utf-8')
    assert '구성요소별 라이선스 전문' in text and 'pywebview' in text.lower()


def test_generate_rejects_output_without_license_section(internal, tmp_path, monkeypatch):
    class Result:
        stdout = '설치된 배포판을 찾지 못했습니다.'.encode('utf-8')
    monkeypatch.setattr(internal.subprocess, 'run', lambda *a, **k: Result())
    with pytest.raises(SystemExit, match='gen_notices'):
        internal.generate_third_party_notices(sys.executable, ROOT, tmp_path / 'x.txt')
    assert not (tmp_path / 'x.txt').exists()


def test_copy_notice_files_places_full_set_and_generated_notices(internal, tmp_path):
    src = tmp_path / 'src'
    for relative, _ in internal.NOTICE_FILES:
        path = src / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture ' + relative)
    (src / 'THIRD-PARTY-NOTICES.txt').write_text('REPO COPY MUST NOT BE USED')
    generated = tmp_path / 'generated.txt'; generated.write_text('generated notices pywebview', encoding='utf-8')
    bundle = _fake_bundle(tmp_path, with_webview=True)
    internal.copy_notice_files(src, bundle, generated)
    assert sorted(p.name for p in bundle.iterdir() if p.is_file()) == ['DISCLAIMER.md', 'LICENSE', 'PLATFORM-NOTICES.txt', 'THIRD-PARTY-NOTICES.txt', 'TZDATA-NOTICES.txt']
    assert (bundle / 'THIRD-PARTY-NOTICES.txt').read_text(encoding='utf-8') == 'generated notices pywebview'
    assert (bundle / 'DISCLAIMER.md').read_text() == 'fixture DISCLAIMER.md'
    assert (src / 'THIRD-PARTY-NOTICES.txt').read_text() == 'REPO COPY MUST NOT BE USED'


def test_copy_notice_files_fails_when_a_notice_is_missing(internal, tmp_path):
    src = tmp_path / 'src'; (src / 'packaging').mkdir(parents=True)
    (src / 'packaging/PLATFORM-NOTICES.txt').write_text('x')
    generated = tmp_path / 'g.txt'; generated.write_text('g')
    with pytest.raises(SystemExit, match='TZDATA-NOTICES'):
        internal.copy_notice_files(src, _fake_bundle(tmp_path, False), generated)


@pytest.mark.parametrize('builder_name', ['build_internal', 'build_native'])
def test_webview_bundle_requires_pywebview_notice(builder_name, tmp_path):
    builder = _load(builder_name)
    bundle = _fake_bundle(tmp_path, with_webview=True)
    notices = bundle / 'THIRD-PARTY-NOTICES.txt'
    notices.write_text('no webview notice here', encoding='utf-8')
    with pytest.raises(SystemExit, match='pywebview'):
        builder.check_webview_notice(bundle, notices)
    notices.write_text('■ pywebview 5.4 — BSD-3-Clause', encoding='utf-8')
    assert builder.check_webview_notice(bundle, notices) is True


@pytest.mark.parametrize('builder_name', ['build_internal', 'build_native'])
def test_bundle_without_webview_skips_notice_gate(builder_name, tmp_path):
    builder = _load(builder_name)
    bundle = _fake_bundle(tmp_path, with_webview=False)
    notices = bundle / 'THIRD-PARTY-NOTICES.txt'; notices.write_text('no webview', encoding='utf-8')
    assert builder.check_webview_notice(bundle, notices) is False


@pytest.mark.parametrize('builder_name', ['build_internal', 'build_native'])
def test_windows_webview_notice_requires_pythonnet_and_clr_loader(builder_name, tmp_path):
    builder = _load(builder_name)
    bundle = _fake_bundle(tmp_path, with_webview=True)
    notices = bundle / 'THIRD-PARTY-NOTICES.txt'
    notices.write_text('pywebview license', encoding='utf-8')
    with pytest.raises(SystemExit, match='pythonnet.*clr-loader'):
        builder.check_webview_notice(bundle, notices, target_os='Windows')
    notices.write_text('pywebview pythonnet clr-loader licenses', encoding='utf-8')
    assert builder.check_webview_notice(bundle, notices, target_os='Windows') is True


def test_repo_notices_already_list_pywebview_for_native_builds():
    """정본 빌드(build_native)는 저장소 THIRD-PARTY-NOTICES.txt 를 그대로 싣는다. 창 모드를 배포하려면 여기에 pywebview 가 있어야 한다."""
    assert 'pywebview' in (ROOT / 'THIRD-PARTY-NOTICES.txt').read_text(encoding='utf-8')


def test_webview_notice_accepts_underscore_distribution_names(tmp_path):
    """gen_notices 는 METADATA 의 Name 그대로 적는다(clr_loader). 검사는 '-'/'_' 를 같은 이름으로 본다."""
    bundle = tmp_path / 'bundle'
    (bundle / '_internal' / 'webview').mkdir(parents=True)
    notices = tmp_path / 'THIRD-PARTY-NOTICES.txt'
    notices.write_text('■ pywebview 6.2.1 — BSD\n■ pythonnet 3.2.1 — MIT\n■ clr_loader 0.3.1 — MIT\n', encoding='utf-8')
    for builder_name in ('build_internal', 'build_native'):
        assert _load(builder_name).check_webview_notice(bundle, notices, target_os='Windows') is True
