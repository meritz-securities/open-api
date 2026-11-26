"""Build input gates exercised with a fake compiler, never a native acceptance claim."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_builder(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'packaging'))
    spec = importlib.util.spec_from_file_location('native_template_test_builder', ROOT/'packaging/build_native.py')
    import windows.wasi_bundle as wasi_bundle
    monkeypatch.setattr(wasi_bundle, 'prepare', lambda output: output / 'strategy_wasi')
    monkeypatch.setattr(wasi_bundle, 'verify', lambda bundle: None)
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    source, output = tmp_path/'source', tmp_path/'output'
    for name in ['platform_entry.py', 'studio/dist/index.html', 'sdk/meritz_strategy/__init__.py',
                 'indicators/calc.py', 'pyproject.toml', 'uv.lock', 'packaging/macos-arm64-build-requirements.txt', 'packaging/export_macos_pins.py',
                 'packaging/build_native.py', 'packaging/timezone_support.py', 'packaging/TZDATA-NOTICES.txt',
                 'meritz/data/catalog.json', 'strategies/sma-cross.json', 'strategies/template-details.toml',
                 'LICENSE', 'DISCLAIMER.md', 'THIRD-PARTY-NOTICES.txt', 'packaging/PLATFORM-NOTICES.txt',
                 'gen_notices.py',
                 'strategies/additional-description.toml']:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture input: '+name)
    monkeypatch.setattr(sys,'argv',['build_native.py','--source-root',str(source),'--output-root',str(output)])
    monkeypatch.setattr(builder.platform,'system',lambda:'Windows')
    monkeypatch.setattr(builder.platform,'machine',lambda:'AMD64')
    monkeypatch.setattr(builder.importlib.metadata,'distributions',lambda:[])
    def generated_notices(_python, _source, dest):
        path=Path(dest);path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture notices')
        return path
    monkeypatch.setattr(builder,'generate_third_party_notices',generated_notices)
    data=b'TZif fixture'
    evidence={'files':{'tzdata/zoneinfo/America/New_York':hashlib.sha256(data).hexdigest()}}
    monkeypatch.setattr(builder.subprocess,'check_output',lambda *a,**k:json.dumps(evidence))
    calls=[]
    def compile_fixture(command, **kwargs):
        calls.append(command)
        bundle=output/'dist/meritz-strategy-platform'
        resource=bundle/'_internal/tzdata/zoneinfo/America/New_York'
        resource.parent.mkdir(parents=True);resource.write_bytes(data)
        (bundle/'meritz-strategy-platform.exe').write_bytes(b'NOT A NATIVE EXECUTABLE')
    monkeypatch.setattr(builder.subprocess,'run',compile_fixture)
    return builder,source,output,calls,compile_fixture


def test_missing_template_text_fails_before_compiler(native_builder):
    builder,source,output,calls,_=native_builder
    (source/'strategies/template-details.toml').unlink()
    with pytest.raises(SystemExit) as error:
        builder.main()
    assert error.value.code == 2
    assert calls == [] and not output.exists()


def test_all_template_toml_files_are_hashed_in_manifest(native_builder):
    builder,source,output,calls,_=native_builder
    builder.main()
    manifest=json.loads((output/'build-manifest.json').read_text())
    for relative in ('strategies/template-details.toml','strategies/additional-description.toml'):
        assert manifest['sourceHashes'][relative] == hashlib.sha256((source/relative).read_bytes()).hexdigest()
    assert manifest['status']=='UNSIGNED_REVIEW_CANDIDATE' and len(calls)==1
    assert any(str(source/'strategies') in arg for arg in calls[0])


@pytest.mark.parametrize('relative',['strategies/template-details.toml','strategies/additional-description.toml'])
def test_template_text_change_during_compilation_rejects_candidate(native_builder, monkeypatch, relative):
    builder,source,output,calls,compile_fixture=native_builder
    def changed_compile(*args,**kwargs):
        compile_fixture(*args,**kwargs)
        (source/relative).write_text('changed during build')
    monkeypatch.setattr(builder.subprocess,'run',changed_compile)
    with pytest.raises(SystemExit,match='Source changed during build'):
        builder.main()
    manifest=json.loads((output/'build-manifest.json').read_text())
    assert manifest['status']=='SOURCE_CHANGED_REBUILD_REQUIRED'
    assert manifest['changedDuringBuild']==[relative]
