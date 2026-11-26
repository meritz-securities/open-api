"""Actual isolated Runtime, test-authored candidates; no AI or market calls."""
import copy
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path
from runtime.service import StrategyRuntime
from runtime.server import create_server
from runtime.storage import digest


def no_outbound(*args, **kwargs): raise RuntimeError('Outbound forbidden')
socket.socket.connect=no_outbound


class Runtime(StrategyRuntime):
    delay=False
    lose_response=False
    started=threading.Event()
    release=threading.Event()
    def create_ai_request(self,*args,**kwargs):
        result=super().create_ai_request(*args,**kwargs)
        if self.lose_response:
            self.lose_response=False
            raise ValueError('시험: 저장 후 응답 유실')
        if self.delay:
            self.started.set()
            if not self.release.wait(15): raise ValueError('test delay timed out')
        return result


with tempfile.TemporaryDirectory(prefix='meritz-ai-browser-') as root:
    service=Runtime(root,ai_studio=True)
    definition=service.templates()['templates'][0]
    revision=service.save_strategy(definition)
    data=service.demo_data()
    baseline=service.run(revision['id'],data['id'])
    other=service.run(revision['id'],data['id'])
    original=digest(baseline)
    server=create_server(service,page=Path(sys.argv[1]).read_bytes())
    threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':server.issue_ticket(),'baseline':baseline['id'],'other':other['id']}),flush=True)
    external_release=threading.Event();external_thread=None
    try:
        for line in sys.stdin:
            words=line.strip().split(' ',1);command=words[0]
            if command=='stop': break
            if command=='ticket': value={'url':server.issue_ticket()}
            elif command=='inspect': value={'requests':service.list_ai_requests()['requests'],'runs':service.list_runs()['runs'],'baselineUnchanged':digest(service.get_run(baseline['id']))==original}
            elif command=='lose_response': service.lose_response=True;value={'ok':True}
            elif command=='delay': service.delay=True;service.release.clear();service.started.clear();value={'ok':True}
            elif command=='started': value={'started':service.started.wait(5)}
            elif command=='release': service.delay=False;service.release.set();value={'ok':True}
            elif command=='signed_baseline':
                hold=copy.deepcopy(definition)
                hold['entry']={'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':999}}
                hold['exit']={'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}}
                hold['quantity']=1
                revision=service.save_strategy(hold)
                data=service.import_data([{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,5])],{'kind':'imported','label':'signed return fixture'})
                value=service.run(revision['id'],data['id'],{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0})
            elif command=='signed_submit':
                external=StrategyRuntime(root)
                base=external.get_ai_request(words[1])['request']
                loss=external.get_strategy(base['revisionId'])['definition'];loss['name']='손실 후보';loss['entry']['right']['value']=0
                gain=copy.deepcopy(loss);gain['name']='수익 후보';gain['exit']={'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':15}}
                value=external.submit_ai_candidates(words[1],[gain,loss])
            elif command=='external':
                external=StrategyRuntime(root)
                good=copy.deepcopy(definition);good['name']='시험 작성 성공 후보'
                bad=copy.deepcopy(definition);bad['name']='시험 작성 실패 후보'
                calculate=external._calculate
                def fail(definition,*args,**kwargs):
                    if definition['name']=='시험 작성 실패 후보': raise ValueError('fixture execution failure')
                    return calculate(definition,*args,**kwargs)
                external._calculate=fail
                value=external.submit_ai_candidates(words[1],[good,bad])
            elif command in ('start_external','start_local'):
                external=service if command=='start_local' else StrategyRuntime(root);started=threading.Event();external_release.clear();calculate=external._calculate
                def slow(*args,**kwargs):
                    started.set()
                    if not external_release.wait(15): raise ValueError('test deadline')
                    return calculate(*args,**kwargs)
                external._calculate=slow
                external_thread=threading.Thread(target=lambda:external.submit_ai_candidates(words[1],[definition]),daemon=True);external_thread.start()
                value={'started':started.wait(5)}
            elif command=='finish_external': external_release.set();external_thread.join(5);external._calculate=calculate;value={'done':not external_thread.is_alive()}
            else: raise ValueError(command)
            print(json.dumps(value,ensure_ascii=False),flush=True)
    finally:
        service.release.set();external_release.set()
        if external_thread: external_thread.join(5)
        server.shutdown();server.server_close()
