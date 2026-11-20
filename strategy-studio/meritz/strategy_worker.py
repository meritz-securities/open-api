"""Untrusted-code worker. Launched only by strategy_runner inside the OS sandbox.

Stdout is an order-intent protocol, never an accounting authority. This module
does not import the MCP server, registry, client, credentials or market history.
"""
from __future__ import annotations

import __future__
import json
import os
import random
import sys
import traceback


def main():
    if sys.platform != 'wasi':
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        # macOS enforcement varies; the parent independently watches RSS and time.
        try:
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        except (ValueError, OSError):
            pass
    random.seed(0)
    wire = sys.stdout
    sys.stdout = sys.stderr

    def send(value):
        wire.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + '\n')
        wire.flush()

    def read():
        line = sys.stdin.buffer.readline(1_048_577)
        if not line or len(line) > 1_048_576:
            raise ValueError('worker input limit')
        return json.loads(line)

    if sys.platform == 'wasi':
        import errno
        path = '/python/lib/python313.zip'
        with open(path, 'rb') as stdlib:
            is_zip = stdlib.read(2) == b'PK'
        try:
            with open(path, 'r+b'):
                readonly = False
        except OSError as error:
            readonly = error.errno in (errno.EACCES, errno.EPERM, errno.EROFS,
                                       getattr(errno, 'ENOTCAPABLE', -1))
        send({'checks': {'wasi': is_zip, 'readonly': readonly,
                         'environment': set(os.environ) <= {'PYTHONPATH', 'PYTHONHOME'}}})
    first = read()
    if first.get('probe'):
        import socket
        import errno
        checks = {}
        def denied(fn):
            try:
                fn()
            except OSError as error:
                return error.errno in (errno.EPERM, errno.EACCES)
            return False
        checks['read'] = denied(lambda: open(first['canary'], 'rb'))
        checks['write'] = denied(lambda: open(first['write'], 'wb'))
        checks['metadata'] = denied(lambda: os.stat(first['canary']))
        checks['home'] = denied(lambda: os.listdir(first['home']))
        def network():
            with socket.socket() as sock:
                sock.settimeout(.5)
                sock.connect(('127.0.0.1', first['port']))
        checks['network'] = denied(network)
        def fork():
            pid = os.fork()
            if pid == 0:
                os._exit(0)
            os.waitpid(pid, 0)
        checks['fork'] = denied(fork)
        checks['parent_signal'] = denied(lambda: os.kill(os.getppid(), 0))
        # Load the binding outside denied(): a failed import is not evidence
        # that the actual process-arguments syscall was denied.
        import ctypes
        libc=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
        sysctl=libc.sysctl
        sysctl.argtypes=[ctypes.POINTER(ctypes.c_int),ctypes.c_uint,ctypes.c_void_p,
                         ctypes.POINTER(ctypes.c_size_t),ctypes.c_void_p,ctypes.c_size_t]
        sysctl.restype=ctypes.c_int
        def procargs():
            mib=(ctypes.c_int*3)(1,49,os.getppid())  # CTL_KERN, KERN_PROCARGS2
            data=ctypes.create_string_buffer(1024*1024);size=ctypes.c_size_t(len(data))
            if sysctl(mib,3,data,ctypes.byref(size),None,0)!=0:
                raise OSError(ctypes.get_errno(),'parent process arguments denied')
        checks['procargs'] = denied(procargs)
        checks['environment'] = os.environ.get('MERITZ_NO_ENV_FILE')=='1' and not any(
            k!='MERITZ_NO_ENV_FILE' and k.startswith(('MERITZ_', 'AWS_', 'OPENAI_', 'ANTHROPIC_', 'DYLD_', 'PYTHONPATH')) for k in os.environ)
        send({'probe': checks})
        return

    # Trusted host-provided helper module, not a filesystem sandbox exception.
    if first.get('sdk') is not None:
        import types
        if first.get('indicators') is not None:
            package = types.ModuleType('indicators')
            package.__path__ = []
            calc = types.ModuleType('indicators.calc')
            exec(compile(first['indicators'], 'indicators/calc.py', 'exec'), calc.__dict__)
            package.calc = calc
            sys.modules['indicators'] = package
            sys.modules['indicators.calc'] = calc
        sdk = types.ModuleType('meritz_strategy')
        exec(compile(first['sdk'], 'meritz_strategy/__init__.py', 'exec'), sdk.__dict__)
        sys.modules['meritz_strategy'] = sdk
    namespace = {'__name__': '__strategy__', '__file__': 'strategy.py'}
    seq = -1
    try:
        exec(compile(first['code'], '<strategy>', 'exec',
                     flags=__future__.annotations.compiler_flag, dont_inherit=True, optimize=0), namespace)
        callback = namespace.get('on_bar')
        if not callable(callback):
            raise ValueError('on_bar(ctx) is required')
        initialize = namespace.get('initialize')
        if initialize is not None and not callable(initialize):
            raise ValueError('initialize must be callable')
        initialized = False
        send({'ready': True})
        while True:
            request = read()
            if request.get('stop') is True:
                return
            seq = request['seq']
            if not initialized:
                if initialize is not None:
                    initialize(request['observation'])
                initialized = True
            decision = callback(request['observation'])
            send({'seq': seq, 'decision': decision})
    except BaseException as error:
        frames = traceback.extract_tb(error.__traceback__)
        lines = [frame.lineno for frame in frames if frame.filename == '<strategy>']
        # Exception messages are arbitrary code-controlled text; send only a type and source line.
        send({'seq': seq, 'error': {'type': type(error).__name__[:80], 'line': lines[-1] if lines else None}})


if __name__ == '__main__':
    main()
