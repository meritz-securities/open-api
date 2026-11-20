"""Actual Runtime with isolated store and explicit synthetic/imported fixtures only."""
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path
from runtime.service import StrategyRuntime
from runtime.server import create_server


def no_outbound(*args,**kwargs): raise RuntimeError('No outbound access in graph tests')
socket.socket.connect=no_outbound
with tempfile.TemporaryDirectory(prefix='meritz-graph-test-') as root:
    service=StrategyRuntime(root)
    krw=service.demo_data()
    bars=service._dataset(krw['id'])['bars']
    usd=service.import_data(bars,{'kind':'imported','label':'USD fixture','currency':'USD'})
    unknown=service.import_data(bars,{'kind':'imported','label':'Unknown fixture'})
    unknown2=service.import_data(bars,{'kind':'imported','label':'Other unknown fixture'})
    server=create_server(service,page=Path(sys.argv[1]).read_bytes())
    threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':server.issue_ticket(),'krw':krw['id'],'usd':usd['id'],'unknown':unknown['id'],'unknown2':unknown2['id']}),flush=True)
    try:
        for line in sys.stdin:
            message=json.loads(line)
            if message['action']=='stop':break
            if message['action']=='ticket':value={'url':server.issue_ticket()}
            elif message['action']=='equivalence':
                run=service.get_run(message['runId'])
                revision=service.get_strategy(run['revisionId'])
                plain=service.save_strategy(revision['definition'])
                repeated=service.run(plain['id'],run['datasetId'],run['settings'])
                value={'definition':revision['definition'],'layout':revision.get('editorLayout'),'sameDefinitionHash':revision['hash']==plain['hash'],'sameResultHash':run['manifest']['resultHash']==repeated['manifest']['resultHash']}
            else: value=service.dispatch(message['action'],message.get('arguments',{}))
            print(json.dumps(value,ensure_ascii=False),flush=True)
    finally:server.shutdown();server.server_close()
