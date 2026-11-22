from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_strategy_lab_bundle_includes_platform_runtime_resources():
    script = (ROOT / 'build_strategy_lab.sh').read_text(encoding='utf-8')
    assert 'process.versions.node' in script
    assert 'npm ci && npm run build' in script
    assert script.index('npm ci && npm run build') < script.index('.strategy-build/bin/pyinstaller')
    assert '--specpath "$work_root"' in script
    assert 'STRATEGY_LAB_OUTPUT_ROOT' in script
    assert 'STRATEGY_LAB_WORK_ROOT' in script
    for module in ('runtime', 'analytics', 'optimizer', 'reports', 'indicators', 'meritz_strategy'):
        assert f'--collect-submodules {module}' in script
    assert '--paths "$source_root" --paths "$source_root/sdk"' in script
    for source, destination in (
        ('runtime/strategy.schema.json', 'runtime'),
        ('strategies', 'strategies'),
        ('sdk/meritz_strategy/__init__.py', 'sdk/meritz_strategy'),
        ('indicators/calc.py', 'indicators'),
    ):
        assert (ROOT / source).exists(), source
        assert f'--add-data "$source_root/{source}:{destination}"' in script
    assert (ROOT / 'studio/index.html').exists()
    assert '--add-data "$source_root/studio/dist/index.html:studio/dist"' in script
