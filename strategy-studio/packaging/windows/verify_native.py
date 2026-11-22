"""Windows-only synthetic smoke checks. Does not install or approve a release."""
import argparse
import asyncio
import hashlib
import json
import os
import platform
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from prepare_installer import validate_bundle


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_opener():
    """Never send loopback tickets/tokens through environment or registry proxies."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


async def verify_python_refusal(client, run_args, canary):
    refused=await client.call_tool('meritz_platform_backtest',run_args,raise_on_error=False)
    require(refused.is_error and any('unsupported_platform' in getattr(item,'text','') for item in refused.content),
            'Python failure was not the explicit unsupported_platform refusal')
    require(not canary.exists(), 'MCP executed unsupported Python canary')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New developer evidence JSON, outside bundle')
    parser.add_argument('--require-python', action='store_true', help='Require bundled Windows Python strategy acceptance')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Run on native Windows; this check cannot certify Windows from another OS.')
    bundle = args.bundle.resolve(); output = args.output.resolve()
    if output.exists() or output.is_relative_to(bundle):
        parser.error('Evidence output must be a new file outside the bundle.')
    manifest, hashes = validate_bundle(args.manifest, bundle)
    executable = bundle / 'meritz-strategy-platform.exe'
    evidence = {'status':'RUNNING', 'os':sys.platform, 'bundleArchitecture':manifest['architecture'],
                'hostMachine':platform.machine(), 'hostVersion':platform.version(),
                'binarySha256':hashlib.sha256(executable.read_bytes()).hexdigest(),
                'manifestSha256':hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                'bundleFiles':len(hashes), 'data':'synthetic independent oracle, no live quotes',
                'checks':{}, 'releaseApproved':False,
                'notVerified':['installation/uninstall/update','code signing','Windows ACLs','browser JavaScript interaction','real market data','complete security review']}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='meritz-windows-native-check-') as directory:
            root = Path(directory)
            env = {k:v for k,v in os.environ.items() if not k.upper().startswith(('MERITZ_', 'PYTHON'))}
            require(bool(env.get('SystemRoot') or env.get('SYSTEMROOT')), 'Windows SystemRoot is required')
            system_root = env.get('SystemRoot', env.get('SYSTEMROOT'))
            env.update(MERITZ_NO_ENV_FILE='1', MERITZ_READ_ONLY='1', MERITZ_STRATEGY_HOME=str(root / 'data'),
                       PATH=os.pathsep.join([str(Path(system_root) / 'System32'), str(bundle)]))
            def call(action, arguments=None, failure=False):
                request = root / 'request.json'
                request.write_text(json.dumps(arguments or {}, ensure_ascii=True), encoding='utf-8')
                process = subprocess.run([str(executable), '--cli', action, '--root', str(root / 'data'),
                                          '--arguments', '@' + str(request)], cwd=root, env=env,
                                         capture_output=True, text=True, encoding='utf-8', timeout=45)
                result = json.loads(process.stdout)
                require(process.returncode != 0 and result.get('ok') is False if failure
                        else process.returncode == 0 and result.get('ok') is True,
                        'Unexpected CLI outcome: ' + action)
                return result.get('result', result)
            capabilities = call('capabilities')
            require(capabilities['platform'] == 'Windows',
                    'Expected Windows runtime')
            python_ready = capabilities['pythonStrategy']
            require(python_ready, 'The Windows bundle is missing its Python worker')
            require(bool(call('templates')['templates']), 'Templates missing')
            bars = [{'date':f'2025-01-0{i+1}', 'open':p,'high':p,'low':p,'close':p}
                    for i,p in enumerate([10,20,30,40])]
            dataset = call('import_data', {'bars':bars,'source':{'kind':'imported','label':'synthetic Windows oracle'}})
            definition = {'schemaVersion':1,'kind':'dsl','name':'Windows oracle',
                          'entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},
                          'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},
                          'quantity':1,'allowPyramiding':False}
            revision = call('save_strategy', {'definition':definition})
            run_args = {'revisionId':revision['id'],'datasetId':dataset['id'],
                        'settings':{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0}}
            first = call('run', run_args); second = call('run', run_args)
            require(first['report']['metrics']['finalEquity'] == 120, 'Independent equity oracle failed')
            require(first['report']['finalPortfolio']['cash'] == 80, 'Independent cash oracle failed')
            require(first['manifest']['resultHash'] == second['manifest']['resultHash'], 'CLI replay differs')
            require(call('get_run', {'runId':first['id']}) == first, 'Persistence replay differs')
            evidence['checks']['cli'] = {'expectedEquity':120,'expectedCash':80,'replay':True,'pathExcludesPython':True}
            canary = root / 'must-not-execute.txt'
            code = f'open({str(canary)!r}, "w").write("executed")\ndef on_bar(ctx): return {{"action":"hold"}}'
            py_revision = call('save_strategy', {'definition':{'schemaVersion':1,'kind':'python','name':'must refuse','code':code}})
            call('run', {**run_args,'revisionId':py_revision['id']}, failure=True)
            require(not canary.exists(), 'Unsupported Python code executed')
            evidence['checks']['hostFileWriteBlocked'] = True
            if python_ready:
                slow = 'import time\ndef on_bar(ctx):\n time.sleep(60)'
                slow_revision = call('save_strategy', {'definition':{'schemaVersion':1,'kind':'python','name':'Timeout','code':slow}})
                call('run', {**run_args,'revisionId':slow_revision['id']}, failure=True)
                buy = 'from meritz_strategy import Context\ndef on_bar(ctx):\n return Context(ctx).buy(1) if ctx["index"] == 0 else {"action":"hold"}'
                revision = call('save_strategy', {'definition':{'schemaVersion':1,'kind':'python','name':'Windows Python oracle','code':buy}})
                python_args = {**run_args,'revisionId':revision['id']}
                result = call('run', python_args)
                require(result['report']['metrics']['finalEquity'] == 120, 'Python equity oracle failed')
                evidence['checks']['pythonEquity'] = 120
            async def mcp():
                from fastmcp import Client
                from fastmcp.client.transports import StdioTransport
                async with Client(StdioTransport(command=str(executable),args=['--mcp'],env=env),timeout=45) as client:
                    names={tool.name for tool in await client.list_tools()}
                    legacy={'meritz_backtest_demo','meritz_backtest_run','meritz_backtest_compare',
                            'meritz_backtest_trade','meritz_backtest_validate','meritz_backtest_code'}
                    require(not (names & legacy), f'Platform MCP exposes legacy tools: {sorted(names & legacy)}')
                    require({'meritz_platform_backtest','meritz_platform_data','meritz_strategy_templates'} <= names,
                            'Shared Runtime MCP tools are missing')
                    if not python_ready:
                        await verify_python_refusal(client,{**run_args,'revisionId':py_revision['id']},canary)
                        evidence['checks']['pythonRefusalReason']='unsupported_platform'
                    result=(await client.call_tool('meritz_platform_backtest',run_args)).data
                    require(result['manifest']['resultHash'] == first['manifest']['resultHash'], 'MCP calculation differs')
                    record=(await client.call_tool('meritz_strategy_robustness',{'runId':first['id'],'method':'walk_forward','options':{'trainBars':2,'testBars':2}})).data
                    replay=(await client.call_tool('meritz_strategy_validation_result',{'validationId':record['id']})).data
                    require(record == replay and record['result']['childRunCount'] == 2, 'MCP validation replay failed')
                    evidence['checks']['mcp']={'sharedResultHash':True,'walkForwardReplay':True,'tools':len(await client.list_tools())}
            asyncio.run(mcp())
            server = subprocess.Popen([str(executable),'--no-browser'], cwd=root, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
            try:
                lines = queue.Queue()
                def read_lines():
                    for line in server.stdout: lines.put(line)
                    lines.put(None)
                threading.Thread(target=read_lines,daemon=True).start()
                launch = None
                for _ in range(10):
                    line = lines.get(timeout=45)
                    if line is None: break
                    match = re.search(r'http://127\.0\.0\.1:\d+/#ticket=[\w-]+', line)
                    if match: launch=match.group(0);break
                require(launch is not None, 'Server did not issue a launch ticket')
                parsed=urllib.parse.urlsplit(launch); origin='http://'+parsed.netloc
                ticket=urllib.parse.parse_qs(parsed.fragment)['ticket'][0]
                opener=local_opener()
                with opener.open(origin,timeout=15) as response:
                    require('sha256-' in response.headers['Content-Security-Policy'], 'Missing hash CSP')
                    require(b'<div id="root"></div>' in response.read(), 'Studio page missing')
                def post(path, payload, token=None, request_origin=None):
                    headers={'Content-Type':'application/json','Origin':request_origin or origin}
                    if token: headers['X-Platform-Token']=token
                    request=urllib.request.Request(origin+path, json.dumps(payload).encode(), headers)
                    with opener.open(request,timeout=45) as response: return json.load(response)
                token=post('/api/session',{'ticket':ticket})['result']['token']
                require(post('/api/platform',{'action':'templates','arguments':{}},token)['ok'], 'Authenticated HTTP failed')
                if python_ready:
                    response = post('/api/platform', {'action':'run','arguments':python_args}, token)
                    require(response['result']['report']['metrics']['finalEquity'] == 120, 'Studio HTTP Python oracle failed')
                for path,payload,secret,wrong_origin in [('/api/session',{'ticket':ticket},None,None),
                        ('/api/platform',{'action':'templates','arguments':{}},token,'https://example.invalid')]:
                    try: post(path,payload,secret,wrong_origin)
                    except urllib.error.HTTPError as error: require(error.code in (401,403), 'Unexpected authentication rejection')
                    else: raise ValueError('Ticket replay or foreign Origin was accepted')
                evidence['checks']['http']={'page':True,'hashCsp':True,'ticketReplayRejected':True,'foreignOriginRejected':True}
            finally:
                server.terminate()
                try: server.wait(timeout=15)
                except subprocess.TimeoutExpired: server.kill();server.wait(timeout=15)
                if server.stdout: server.stdout.close()
            validate_bundle(args.manifest, bundle)
            evidence['status']='PASS_SCOPED_WINDOWS_NATIVE_SMOKE'
    except Exception as error:
        evidence.update(status='FAILED', errorType=type(error).__name__)
        raise
    finally:
        with output.open('x',encoding='utf-8') as stream: json.dump(evidence,stream,ensure_ascii=False,indent=2)


if __name__ == '__main__':
    main()
