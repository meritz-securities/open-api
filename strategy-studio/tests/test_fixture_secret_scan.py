"""Verify the actual fixture scanner without launching its HTTP server."""
import ast
from pathlib import Path
import pytest

SOURCE = Path(__file__).parents[1] / 'studio/tests/ai_new_runtime_server.py'

def scanner():
    tree = ast.parse(SOURCE.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'no_secrets_in_files')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['no_secrets_in_files']

def test_disappearing_file_does_not_hide_secret_in_surviving_wal(tmp_path, monkeypatch):
    gone = tmp_path / 'removed-shm'; gone.write_bytes(b'clean')
    wal = tmp_path / 'remaining-wal'; wal.write_bytes(b'test-only-secret')
    original = Path.read_bytes
    def read(path):
        if path == gone:
            path.unlink()
            raise FileNotFoundError(str(path))
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    assert scanner()(tmp_path, [b'test-only-secret']) is False

def test_disappearing_clean_file_is_tolerated(tmp_path, monkeypatch):
    item = tmp_path / 'removed-shm'; item.write_bytes(b'clean')
    def read(path):
        path.unlink()
        raise FileNotFoundError(str(path))
    monkeypatch.setattr(Path, 'read_bytes', read)
    assert scanner()(tmp_path, [b'test-only-secret']) is True

def test_unreadable_existing_file_fails_the_scan(tmp_path, monkeypatch):
    (tmp_path / 'remaining-wal').write_bytes(b'anything')
    def read(path):
        raise PermissionError(str(path))
    monkeypatch.setattr(Path, 'read_bytes', read)
    with pytest.raises(PermissionError):
        scanner()(tmp_path, [b'test-only-secret'])
