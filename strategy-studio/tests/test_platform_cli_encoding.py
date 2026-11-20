"""JSON values survive redirected non-UTF8 Windows-like output encodings."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('encoding',['ascii','cp1252','cp949','utf-8'])
def test_cli_json_templates_do_not_depend_on_console_encoding(tmp_path,encoding):
    env={k:v for k,v in os.environ.items() if not k.startswith('MERITZ_')}
    env.update(MERITZ_NO_ENV_FILE='1',PYTHONIOENCODING=encoding)
    result=subprocess.run([sys.executable,'-m','runtime.cli','templates','--root',str(tmp_path)],
                          cwd=ROOT,env=env,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
    payload=json.loads(result.stdout.decode('ascii'))
    assert payload['ok'] and payload['result']['templates']
    from runtime.service import StrategyRuntime
    assert payload['result']==StrategyRuntime(tmp_path).templates()


@pytest.mark.parametrize('encoding',['ascii','cp1252','cp949'])
def test_cli_error_json_survives_nonunicode_console(tmp_path,encoding):
    env={k:v for k,v in os.environ.items() if not k.startswith('MERITZ_')}
    env.update(MERITZ_NO_ENV_FILE='1',PYTHONIOENCODING=encoding)
    result=subprocess.run([sys.executable,'-m','runtime.cli','unsupported','--root',str(tmp_path)],
                          cwd=ROOT,env=env,capture_output=True,timeout=15)
    assert result.returncode==1
    payload=json.loads(result.stdout.decode('ascii'))
    assert payload['ok'] is False and payload['error']['code']=='INVALID_REQUEST'
    assert payload['error']['message']=='입력·경로·지원 범위를 확인하세요.'


def test_utf8_request_file_preserves_korean_on_legacy_locale(tmp_path,monkeypatch,capsys):
    from runtime.cli import main
    monkeypatch.setenv('MERITZ_NO_ENV_FILE','1')
    request=tmp_path/'전략 요청.json'
    request.write_text(json.dumps({'definition':{'schemaVersion':1,'kind':'python','name':'한글 전략 😀',
                                               'code':'def on_bar(ctx): return {"action":"hold"}'}},ensure_ascii=False),encoding='utf-8')
    read_text=Path.read_text
    def locale_read(path,*args,**kwargs):
        if path==request and not kwargs.get('encoding'):
            kwargs['encoding']='cp949'
        return read_text(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',locale_read)
    monkeypatch.setattr(sys,'argv',['runtime-cli','save_strategy','--root',str(tmp_path/'data'),'--arguments','@'+str(request)])
    assert main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['result']['definition']['name']=='한글 전략 😀'
