"""Verify an SDK wheel against committed sources and install in a clean venv."""
import argparse
import base64
import csv
import datetime
import email
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile

CHECK = '''from meritz_strategy import Context, __version__
from importlib.metadata import version
assert __version__==version('meritz-strategy-sdk')
ctx=Context({'index':2,'date':'2025-01-03','bar':{'close':14},'history':[{'close':10},{'close':12},{'close':14}],'portfolio':{'cash':1000,'shares':2,'equity':1028}})
assert ctx.get_indicator('sma',3)==12 and ctx.get_indicator('ema',3)==12
assert ctx.get_indicator('rsi',2)==100 and ctx.get_indicator('sma',4) is None
assert ctx.get_cash()==1000 and ctx.get_position()['quantity']==2
assert ctx.buy(1)=={'action':'buy','reason':'','orderType':'market','quantity':1}
assert ctx.sell(1)['action']=='sell' and ctx.close_position()['action']=='sell'
copy=ctx.get_portfolio();copy['cash']=0;assert ctx.get_cash()==1000
try:ctx.get_indicator('unknown',3)
except ValueError:pass
else:raise AssertionError('unsupported indicator accepted')
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('wheel',type=Path)
    wheel=parser.parse_args().wheel.resolve();source=Path(__file__).resolve().parents[1]
    def git(*args):return subprocess.check_output(['git',*args],cwd=source)
    commit=git('rev-parse','HEAD').decode().strip()
    config=tomllib.loads((source/'sdk/pyproject.toml').read_text())['project'];version=config['version']
    prefix='meritz_strategy_sdk-'+version+'.dist-info/'
    mapping={'sdk/meritz_strategy/__init__.py':'meritz_strategy/__init__.py',
             'indicators/calc.py':'indicators/calc.py','indicators/__init__.py':'indicators/__init__.py',
             'indicators/PROVENANCE.md':'indicators/PROVENANCE.md','sdk/LICENSE':prefix+'licenses/LICENSE'}
    paths=[*mapping,'sdk/pyproject.toml','sdk/README.md','packaging/check_sdk_wheel.py']
    hashes={}
    for name in paths:
        raw=(source/name).read_bytes()
        if raw!=git('show',commit+':'+name):raise ValueError('Uncommitted checker/input: '+name)
        hashes[name]=hashlib.sha256(raw).hexdigest()
    with zipfile.ZipFile(wheel) as archive:
        expected=set(mapping.values())|{prefix+'METADATA',prefix+'WHEEL',prefix+'RECORD'}
        if len(archive.namelist())!=len(expected) or set(archive.namelist())!=expected:raise ValueError('Unexpected wheel members')
        for local,inside in mapping.items():
            if archive.read(inside)!=(source/local).read_bytes():raise ValueError('Source mismatch: '+inside)
        metadata=email.message_from_bytes(archive.read(prefix+'METADATA'))
        if metadata['Version']!=version or metadata.get_all('Requires-Dist') or metadata['Requires-Python']!=config['requires-python']:
            raise ValueError('Wheel metadata mismatch')
        rows=list(csv.reader(io.StringIO(archive.read(prefix+'RECORD').decode())))
        if len(rows)!=len(expected) or {row[0] for row in rows}!=expected:raise ValueError('Invalid RECORD inventory')
        for name,hashed,size in rows:
            if name==prefix+'RECORD':
                if hashed or size:raise ValueError('Invalid RECORD self entry')
                continue
            raw=archive.read(name);expected_hash='sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
            if hashed!=expected_hash or size!=str(len(raw)):raise ValueError('RECORD checksum mismatch')
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME') and not k.startswith('MERITZ_')}
    with tempfile.TemporaryDirectory(prefix='meritz-sdk-check-') as root:
        def run(args):
            result=subprocess.run(args,env=env,cwd=root,capture_output=True,text=True)
            if result.returncode:raise RuntimeError('SDK check subprocess failed: '+result.stderr[-2000:])
        run([sys.executable,'-m','venv',root+'/venv'])
        python=Path(root)/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        run([str(python),'-m','pip','install','--no-index','--no-deps',str(wheel)])
        run([str(python),'-I','-c',CHECK])
    print(json.dumps({'status':'PASS','exitCode':0,'commit':commit,'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'pythonVersion':sys.version,'wheelSha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'inputHashes':hashes,
        'sourceIdentity':True,'recordIntegrity':True,'offlineCleanVenv':True,'platform':sys.platform}))


if __name__=='__main__':main()
