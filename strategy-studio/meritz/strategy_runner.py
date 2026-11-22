"""Isolated Python strategy sessions with bounded IPC on macOS and Windows."""
from __future__ import annotations

import ast
import __future__
import io
import ctypes
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tokenize

MAX_CODE = 65_536
SANDBOX = '/usr/bin/sandbox-exec'
PROFILE = '''(version 1)
(deny default)
(deny process-info*)
(allow process-exec (literal (param "EXECUTABLE")))
(allow file-read* (literal "/") (literal (param "EXECUTABLE")) (subpath (param "RUNTIME"))
  (subpath "/System/Library") (subpath "/usr/lib")
  (literal "/dev/null") (literal "/dev/random") (literal "/dev/urandom"))
(allow sysctl-read (sysctl-name "kern.ostype") (sysctl-name "kern.osrelease")
  (sysctl-name "kern.version") (sysctl-name "kern.hostname") (sysctl-name "hw.machine"))
(allow signal (target self))
'''


class StrategyError(ValueError):
    pass


class _RusageV0(ctypes.Structure):
    # macOS SDK sys/resource.h: RUSAGE_INFO_V0, includes compressed-memory footprint.
    _fields_ = [('uuid', ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        'user_time','system_time','pkg_idle_wkups','interrupt_wkups','pageins','wired_size',
        'resident_size','phys_footprint','proc_start_abstime','proc_exit_abstime')]


def _memory_footprint(pid):
    library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    call = library.proc_pid_rusage
    call.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    call.restype = ctypes.c_int
    value = _RusageV0()
    if call(pid, 0, ctypes.byref(value)) != 0:
        raise OSError(ctypes.get_errno(), 'resource monitor unavailable')
    return value.phys_footprint


class _SyntaxBudgetError(ValueError):
    def __init__(self, message, token):
        super().__init__(message)
        self.line, self.column = token.start[0], token.start[1] + 1


def _check_syntax_budget(code):
    """Iterative lexical budgets before CPython parsing; f-strings are opaque.

    Normal parameter tables can be long without deep grammar. Separate unary,
    operator and elif budgets bound recursive forms rather than only token count.
    """
    statement = total = indents = unary = operators = expressions = conditionals = 0
    brackets = []
    elif_counts = {}
    ignored = (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
               tokenize.DEDENT, tokenize.COMMENT, tokenize.ENDMARKER)
    try:
        for token in tokenize.generate_tokens(io.StringIO(code).readline):
            if token.type == tokenize.NEWLINE:
                statement = unary = operators = expressions = 0
            if token.type == tokenize.INDENT: indents += 1
            elif token.type == tokenize.DEDENT: indents -= 1
            if token.type == tokenize.NAME and token.string in ('if', 'elif'):
                conditionals += 1
                if conditionals > 256:
                    raise _SyntaxBudgetError('전체 if/elif 조건은 256개 이하로 줄여 주세요.', token)
            if token.type == tokenize.NAME and statement == 0:
                if token.string == 'if': elif_counts[indents] = 0
                elif token.string == 'elif':
                    elif_counts[indents] = elif_counts.get(indents, 0) + 1
                    if elif_counts[indents] > 128:
                        raise _SyntaxBudgetError('elif 연결은 128개 이하로 나눠 주세요.', token)
            if token.type == tokenize.OP:
                if token.string in ('(', '[', '{'): brackets.append(token)
                elif token.string in (')', ']', '}'):
                    if not brackets:
                        raise SyntaxError(f"unmatched '{token.string}'",
                                          ('<strategy>', token.start[0], token.start[1] + 1, token.line))
                    if brackets[-1].string != {')': '(', ']': '[', '}': '{'}[token.string]:
                        raise SyntaxError('closing bracket does not match opening bracket',
                                          ('<strategy>', token.start[0], token.start[1] + 1, token.line))
                    brackets.pop()
            if token.type == tokenize.STRING:
                prefix = token.string.split('"', 1)[0].split("'", 1)[0].lower()
                if 'f' in prefix and len(token.string) > 256:
                    raise _SyntaxBudgetError('f-string은 256자 이하로 나눠 주세요.', token)
            if token.type not in ignored:
                statement += 1
                total += 1
                unary = unary + 1 if token.string in ('not', '~', '+', '-') else 0
                if token.type == tokenize.OP and token.string in ('+', '-', '*', '/', '//', '%', '**', '<<', '>>', '&', '|', '^', '@'):
                    operators += 1
                if token.type == tokenize.NAME and token.string in ('lambda', 'if', 'for'):
                    expressions += 1
            if unary > 64 or operators > 128 or expressions > 32:
                raise _SyntaxBudgetError('연산과 조건을 여러 문장으로 나눠 주세요.', token)
            if statement > 512:
                raise _SyntaxBudgetError('한 문장은 512토큰 이하로 나눠 주세요.', token)
            if total > 8192:
                raise _SyntaxBudgetError('전체 코드는 8192토큰 이하여야 합니다.', token)
            if len(brackets) > 32 or indents > 32:
                raise _SyntaxBudgetError('괄호와 들여쓰기 중첩을 32단계 이하로 줄여 주세요.', token)
    except tokenize.TokenError as error:
        # 3.11 순수 Python tokenize 는 'EOF in multi-line statement', 3.12+ C tokenizer 는 'unexpected EOF …' 를 준다.
        if error.args[0] in ('EOF in multi-line statement', 'unexpected EOF in multi-line statement'):
            if brackets:
                opening = brackets[-1]
                raise SyntaxError(f"'{opening.string}' was never closed",
                                  ('<strategy>', opening.start[0], opening.start[1] + 1, opening.line)) from None
            # 괄호가 없으면 줄 끝 역슬래시(줄 이음) 뒤에서 끝난 경우다. 3.11 은 (다음 줄, 0), 3.12+ 는 (그 줄, 끝) 을 주므로
            # 직접 마지막 역슬래시 줄을 찾아 compile() 과 같은 위치(그 줄, 줄 길이 + 1)로 고정한다.
            source_lines = code.split('\n')
            for number in range(len(source_lines), 0, -1):
                text = source_lines[number - 1]
                if text.rstrip().endswith('\\'):
                    raise SyntaxError('unexpected EOF while parsing',
                                      ('<strategy>', number, len(text.rstrip()) + 1, text)) from None
        raise


def validate_code(code):
    errors = []
    if not isinstance(code, str) or not code.strip():
        errors.append({'line': None, 'message': '전략 코드를 입력하세요.'})
    elif any(0xD800 <= ord(char) <= 0xDFFF for char in code):
        errors.append({'line': None, 'message': '전략 코드는 올바른 UTF-8 문자열이어야 합니다.'})
    elif len(code.encode('utf-8')) > MAX_CODE:
        errors.append({'line': None, 'message': '전략 파일은 UTF-8 64KiB 이하여야 합니다.'})
    else:
        compiling = False
        source = code.replace('\r\n', '\n').replace('\r', '\n')
        lines = source.split('\n')
        try:
            _check_syntax_budget(source)
            tree = ast.parse(source, filename='<strategy>')
            # Parsing alone accepts e.g. top-level return or an unbound nonlocal.
            # Compile without executing to validate the actual Python contract.
            compiling = True
            # Preserve the worker's existing postponed-annotation semantics.
            # Compile source, avoiding a second Python-AST-to-C-AST conversion.
            compile(source, '<strategy>', 'exec', flags=__future__.annotations.compiler_flag,
                    dont_inherit=True, optimize=0)
            functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'on_bar']
            if len(functions) != 1 or isinstance(functions[0], ast.AsyncFunctionDef):
                errors.append({'line': None, 'message': '최상위에 def on_bar(ctx): 함수를 하나 정의하세요.'})
            else:
                node = functions[0]
                if len(node.args.args) + len(node.args.posonlyargs) != 1 or node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
                    errors.append({'line': node.lineno, 'message': 'on_bar 함수는 ctx 인자 하나만 받아야 합니다.'})
            initializers = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'initialize']
            if initializers:
                init = initializers[0]
                if len(initializers) != 1 or isinstance(init, ast.AsyncFunctionDef) or len(init.args.args) + len(init.args.posonlyargs) != 1 or init.args.vararg or init.args.kwarg or init.args.kwonlyargs:
                    errors.append({'line': init.lineno, 'message': 'initialize 함수는 선택 항목이며 ctx 인자 하나를 받는 동기 함수여야 합니다.'})
        except _SyntaxBudgetError as error:
            errors.append({'line': error.line, 'column': error.column, 'message': str(error)})
        except tokenize.TokenError as error:
            position = error.args[1] if len(error.args) > 1 else (None, None)
            # 3.11 순수 Python tokenize 는 0 기준 열, 3.12+ C tokenizer 는 SyntaxError.offset(1 기준) 을 준다.
            column = None
            if isinstance(position[1], int):
                column = position[1] + 1 if sys.version_info < (3, 12) else max(position[1], 1)
            errors.append({'line': position[0],
                           'column': column,
                           'message': 'Python 토큰 오류: ' + str(error.args[0])[:160]})
        except SyntaxError as error:
            # CPython 3.11 compiler-stage locations use UTF-8 bytes, whereas
            # parser-stage locations use Unicode characters. Normalize both.
            column = error.offset if error.offset and error.offset > 0 else None
            if compiling and column and error.lineno and 1 <= error.lineno <= len(lines):
                text = lines[error.lineno - 1]
                column = len(text.encode('utf-8')[:column - 1].decode('utf-8', errors='ignore')) + 1
            errors.append({'line': error.lineno,
                           'column': column,
                           'message': 'Python 문법 오류: ' + error.msg[:160]})
        except (ValueError, RecursionError, MemoryError):
            errors.append({'line': None, 'message': '코드 구조가 너무 복잡하거나 파싱할 수 없습니다.'})
    return {'valid': not errors, 'errors': errors, 'interface': 'on_bar_v1',
            'codeHash': hashlib.sha256(code.encode()).hexdigest() if isinstance(code, str) and not errors else None,
            'limits': {'codeBytes': MAX_CODE, 'wallSeconds': 20, 'barSeconds': 2, 'memoryMiB': 256,
                       'tokensPerStatement': 512, 'unaryChain': 64, 'operatorsPerStatement': 128, 'expressionsPerStatement': 32, 'elifChain': 128, 'totalConditionals': 256, 'totalTokens': 8192, 'syntaxNesting': 32, 'fStringCharacters': 256,
                       'platform': 'Windows' if sys.platform == 'win32' else 'macOS', 'libraries': 'Python 표준 라이브러리 및 동봉 meritz_strategy SDK',
                       'memoryNote': 'WASM 선형 메모리 상한입니다.' if sys.platform == 'win32' else 'RSS를 약 20ms 간격으로 감시하는 기준이며 OS가 강제하는 하드 메모리 상한이 아닙니다.',
                       'note': '문법 검사는 보안 판정이 아닙니다. OS 격리 자체 검사를 별도로 수행합니다.'}}


def ensure_supported():
    if sys.platform == 'win32':
        from .windows_strategy import available, SETUP
        if not available():
            raise StrategyError(SETUP)
        return
    if sys.platform != 'darwin' or not Path(SANDBOX).is_file():
        raise StrategyError('현재 고객 코드 실행은 검증된 macOS 환경에서 지원합니다. 격리 없이 대신 실행하지 않습니다.')
    if getattr(sys, 'frozen', False):
        expected = Path(sys.executable).resolve().parent / '_internal'
        if Path(sys._MEIPASS).resolve() != expected:
            raise StrategyError('고객 코드 실행은 전용 폴더형 번들이 필요합니다. 기존 단일 파일 설치본에서는 실행하지 않습니다.')


class _Worker:
    def __init__(self, wall_seconds=20, bar_seconds=2, memory_mib=256):
        ensure_supported()
        self.wall_seconds, self.bar_seconds = wall_seconds, bar_seconds
        self.memory_bytes = memory_mib * 1024**2
        self.started = time.monotonic()
        self.last_rss = 0.0
        self.total = 0
        self.buffer = bytearray()
        self.stderr_size = 0
        self.temp = tempfile.TemporaryDirectory(prefix='meritz-sandbox-')
        root = Path(self.temp.name)
        profile = root/'profile.sb'
        frozen = bool(getattr(sys, 'frozen', False))
        executable = str(Path(sys.executable).resolve())
        if frozen:
            # The deliverable is onedir: no extraction or host writes are needed in the sandbox.
            runtime = str(Path(sys._MEIPASS).resolve())
            args = [executable, '--strategy-worker']
        else:
            runtime = str(Path(sys.base_prefix).resolve())
            bootstrap = Path(__file__).with_name('strategy_worker.py').read_text()
            args = [executable, '-I', '-S', '-B', '-c', bootstrap]
        # Native loader resolves its executable path; only ancestor metadata is needed.
        # No directory contents or arbitrary sibling metadata are readable.
        ancestors = set(Path(executable).parents) | set(Path(runtime).parents)
        metadata = '\n(allow file-read-metadata ' + ' '.join(
            '(literal '+json.dumps(str(path),ensure_ascii=False)+')' for path in sorted(ancestors)) + ')\n'
        command = [SANDBOX, '-D', 'EXECUTABLE='+executable, '-D', 'RUNTIME='+runtime, '-f', str(profile), *args]
        try:
            profile.write_text(PROFILE+metadata)
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True, start_new_session=True, cwd='/',
                env={'PATH':'/usr/bin:/bin', 'LANG':'en_US.UTF-8', 'LC_ALL':'en_US.UTF-8',
                     'MERITZ_NO_ENV_FILE':'1'})
            self.selector = selectors.DefaultSelector()
            for stream in (self.process.stdout, self.process.stderr, self.process.stdin):
                os.set_blocking(stream.fileno(), False)
            self.selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
            self.selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')
        except BaseException:
            self.close()
            raise

    def _check(self, deadline):
        now = time.monotonic()
        if now > deadline or now-self.started > self.wall_seconds:
            raise StrategyError('전략 실행 시간 제한을 초과하여 종료했습니다. 무한 반복이나 긴 대기를 확인하세요.')
        if now-self.last_rss > .02:
            self.last_rss = now
            try:
                rss = _memory_footprint(self.process.pid)
            except (OSError, ValueError):
                if self.process.poll() is None:
                    raise StrategyError('실행 자원 감시를 확인할 수 없어 전략을 중단했습니다.') from None
                rss = 0
            if rss > self.memory_bytes:
                raise StrategyError('전략 메모리 제한을 초과하여 종료했습니다.')

    def send(self, value):
        data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()+b'\n'
        if len(data)>1_048_576:
            raise StrategyError('전략 입력 크기 제한을 초과했습니다.')
        deadline = time.monotonic()+self.bar_seconds
        view = memoryview(data)
        while view:
            self._check(deadline)
            try:
                n = os.write(self.process.stdin.fileno(), view)
                view = view[n:]
            except BlockingIOError:
                time.sleep(.005)
            except BrokenPipeError:
                raise StrategyError('전략 프로세스가 입력을 받기 전에 종료됐습니다.') from None

    def receive(self):
        deadline = time.monotonic()+self.bar_seconds
        while True:
            self._check(deadline)
            if b'\n' in self.buffer:
                line, _, rest = self.buffer.partition(b'\n'); self.buffer = bytearray(rest)
                if len(line)>8192:
                    raise StrategyError('전략 응답 크기 제한을 초과했습니다.')
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):raise ValueError
                    return value
                except (ValueError, RecursionError, UnicodeError):
                    raise StrategyError('전략 응답이 올바른 주문 판단 형식이 아닙니다.') from None
            for key, _ in self.selector.select(.01):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    continue
                self.total += len(chunk)
                if self.total > 1_048_576:
                    raise StrategyError('전략 전체 출력 제한을 초과했습니다.')
                if key.data == 'stdout':
                    self.buffer.extend(chunk)
                    if len(self.buffer)>8192:
                        raise StrategyError('전략 응답 크기 제한을 초과했습니다.')
                else:
                    self.stderr_size += len(chunk)
                    if self.stderr_size>8192:
                        raise StrategyError('전략 로그 출력 제한을 초과했습니다. 반복 print를 줄이세요.')
            if not self.selector.get_map():
                raise StrategyError('전략이 정상 응답 없이 종료됐습니다. 자원 제한 또는 코드 오류를 확인하세요.')

    def close(self):
        process = getattr(self, 'process', None)
        try:
            if process is not None:
                try:
                    if process.poll() is None:
                        try: os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        except PermissionError:
                            # macOS can reject a group signal as its final member exits.
                            if process.poll() is None: process.kill()
                    process.wait(timeout=5)
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None: stream.close()
                    if hasattr(self, 'selector'): self.selector.close()
        finally:
            self.temp.cleanup()


def sandbox_self_test():
    if sys.platform == 'win32':
        from .windows_strategy import WindowsWorker
        worker = WindowsWorker(10, 3, 256)
        try:
            return worker.sandbox
        finally:
            worker.close()
    ensure_supported()
    with tempfile.TemporaryDirectory(prefix='meritz-private-probe-') as folder, socket.socket() as listener:
        root=Path(folder); canary=root/'private.txt';canary.write_text('sandbox-canary')
        listener.bind(('127.0.0.1',0));listener.listen(1)
        worker=_Worker(wall_seconds=10,bar_seconds=3)
        try:
            worker.send({'probe': True, 'canary':str(canary), 'write':str(root/'write.txt'),
                         'home':str(Path.home()),'port':listener.getsockname()[1]})
            result=worker.receive()
            checks=result.get('probe',{})
            expected={'read','write','metadata','home','network','fork','parent_signal','procargs','environment'}
            if set(checks)!=expected or any(value is not True for value in checks.values()):
                raise StrategyError('OS 격리 자체 검사에 실패했습니다. 안전하지 않은 대체 실행은 하지 않습니다.')
            return {'platform':'macOS','mechanism':'sandbox-exec','checks':checks}
        finally:
            worker.close()


class StrategySession:
    def __init__(self, code, *, wall_seconds=20, bar_seconds=2, memory_mib=256, verified_sandbox=None):
        validation=validate_code(code)
        if not validation['valid']:
            raise StrategyError('; '.join(str(e['message']) for e in validation['errors']))
        self.validation=validation
        if sys.platform == 'win32':
            from .windows_strategy import WindowsWorker
            self.worker = WindowsWorker(wall_seconds, bar_seconds, memory_mib)
            self.sandbox = self.worker.sandbox
        else:
            self.sandbox=verified_sandbox if verified_sandbox is not None else sandbox_self_test()
            self.worker=_Worker(wall_seconds,bar_seconds,memory_mib)
        self.seq=0
        try:
            sdk_path = Path(__file__).resolve().parent.parent / 'sdk' / 'meritz_strategy' / '__init__.py'
            if not sdk_path.is_file():
                # Frozen packagers may include the importable SDK instead of the source layout.
                import importlib.util
                spec = importlib.util.find_spec('meritz_strategy')
                sdk_path = Path(spec.origin) if spec and spec.origin else sdk_path
            sdk_source = sdk_path.read_text(encoding='utf-8') if sdk_path.is_file() else None
            indicator_path = Path(__file__).resolve().parent.parent / 'indicators' / 'calc.py'
            indicator_source = indicator_path.read_text(encoding='utf-8') if indicator_path.is_file() else None
            if sdk_source is not None and indicator_source is None:
                raise StrategyError('동봉 SDK 지표 파일을 찾을 수 없습니다. 실행본 구성을 확인하세요.')
            self.worker.send({'code':code, 'sdk':sdk_source, 'indicators':indicator_source})
            result=self.worker.receive()
            if result != {'ready':True}:
                self._error(result)
                raise StrategyError('전략 초기화 응답 형식이 잘못되었습니다.')
        except BaseException:
            self.worker.close()
            raise

    @staticmethod
    def _error(result):
        if 'error' in result:
            error=result['error']
            line=error.get('line') if isinstance(error,dict) else None
            line_text=f' {line}행' if type(line) is int and 0<line<100000 else ''
            raise StrategyError(f'전략 코드{line_text} 실행 중 오류가 발생했습니다. import·계산·파일 접근 등을 확인하세요.')

    def decide(self, observation):
        seq=self.seq;self.seq+=1
        self.worker.send({'seq':seq,'observation':observation})
        result=self.worker.receive()
        self._error(result)
        if set(result)!={'seq','decision'} or type(result['seq']) is not int or result['seq']!=seq:
            raise StrategyError('전략이 예상 순서와 다른 응답을 반환했습니다.')
        return result['decision']

    def close(self):self.worker.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
