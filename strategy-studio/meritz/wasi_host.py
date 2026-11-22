"""Start bundled WebAssembly CPython with one read-only standard library."""
import os
from pathlib import Path
import sys
import threading


def assets():
    return Path(__file__).resolve().parent.parent / 'strategy_wasi'


def main(memory_mib):
    if not 1 <= memory_mib <= 512:
        raise ValueError('Invalid guest memory limit')
    # The engine must also exit if Studio disappears while the guest is sleeping.
    watchdog = threading.Timer(75, os._exit, args=(1,))
    watchdog.daemon = True
    watchdog.start()
    root = assets()
    sys.path.insert(0, str(root / 'host'))
    from wasmtime import Config, Engine, Linker, Module, Store, WasiConfig

    config = Config()
    config.consume_fuel = True
    engine = Engine(config)
    store = Store(engine)
    store.set_limits(memory_size=memory_mib * 1024**2, instances=1, memories=1)
    store.set_fuel(200_000_000_000)
    wasi = WasiConfig()
    wasi.preopen_dir(str(root / 'stdlib'), '/python', fs_mutable=False)
    wasi.env = [('PYTHONHOME', '/python'), ('PYTHONPATH', '/python/lib/python313.zip')]
    wasi.argv = ['python', '-B', '-S', '-u', '-c',
                 'import sys,json;exec(json.loads(sys.stdin.buffer.readline()))']
    wasi.inherit_stdin()
    wasi.inherit_stdout()
    wasi.inherit_stderr()
    store.set_wasi(wasi)
    linker = Linker(engine)
    linker.define_wasi()
    module = Module.from_file(engine, str(root / 'python.wasm'))
    instance = linker.instantiate(store, module)
    instance.exports(store)['_start'](store)
