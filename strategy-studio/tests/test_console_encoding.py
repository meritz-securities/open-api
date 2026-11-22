"""--no-browser 안내문(한국어)이 cp1252 파이프에서도 서버를 죽이지 않아야 한다 (Windows 설치 검사에서 발견)."""
import os
import re
import subprocess
import sys
import threading
import queue
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_browser_prints_ticket_under_cp1252_pipe(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('MERITZ_', 'PYTHON'))}
    env.update(MERITZ_NO_ENV_FILE='1', MERITZ_STRATEGY_HOME=str(tmp_path / 'home'),
               PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
    process = subprocess.run if False else subprocess.Popen
    server = process([sys.executable, str(ROOT / 'platform_entry.py'), '--no-browser'], cwd=str(tmp_path),
                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines = queue.Queue()
    def pump():
        for raw in server.stdout:
            lines.put(raw)
        lines.put(None)
    threading.Thread(target=pump, daemon=True).start()
    try:
        launch = None
        for _ in range(10):
            raw = lines.get(timeout=60)
            if raw is None:
                break
            text = raw.decode('utf-8', 'replace')
            if re.search(r'http://127\.0\.0\.1:\d+/#ticket=[\w-]+', text):
                launch = text
                break
        assert launch is not None, server.stderr.read().decode('utf-8', 'replace')[-2000:]
    finally:
        server.kill()
        server.wait(timeout=10)
