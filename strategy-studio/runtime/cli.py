"""JSON command interface; same service and persisted snapshots as Studio/MCP."""
import argparse
import json
import os
from pathlib import Path
import sys
from .service import StrategyRuntime


def main():
    parser=argparse.ArgumentParser(description='Meritz Strategy Runtime — local research only')
    parser.add_argument('action');parser.add_argument('--arguments',default='{}',help='JSON object or @JSON-file')
    parser.add_argument('--root',type=Path)
    args=parser.parse_args()
    try:
        text=Path(args.arguments[1:]).read_text(encoding='utf-8') if args.arguments.startswith('@') else args.arguments
        if len(text.encode())>4_000_000:raise ValueError('Input exceeds4MB')
        arguments=json.loads(text)
        from meritz import load_catalog
        from meritz.accounts import Registry
        service=StrategyRuntime(args.root,catalog=load_catalog(),registry=Registry())
        result=service.dispatch(args.action,arguments)
        print(json.dumps({'ok':True,'result':result},ensure_ascii=True,allow_nan=False))
    except (ValueError,TypeError,OSError) as error:
        # 기본은 일반 문구만 낸다. 빌드 검사(packaging/windows/wasi_bundle.py)처럼 원인이 필요한 곳에서만
        # MERITZ_CLI_DIAGNOSTICS=1 로 예외 종류와 문구를 stderr 에 낸다. stdout 의 JSON 계약은 그대로다.
        if os.environ.get('MERITZ_CLI_DIAGNOSTICS')=='1':
            print(f'{type(error).__name__}: {str(error)[:2000]}',file=sys.stderr,flush=True)
        print(json.dumps({'ok':False,'error':{'code':'INVALID_REQUEST','message':'입력·경로·지원 범위를 확인하세요.'}},ensure_ascii=True));return 1
    return 0


if __name__=='__main__':sys.exit(main())
