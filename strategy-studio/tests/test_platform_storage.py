"""Lossless snapshots retain full evidence while bounding disk and decoded bytes."""
import json
import sqlite3
import zlib

import pytest

from runtime import storage
from runtime.storage import Store, canonical, decode_body, digest, encode_body


def test_large_full_trace_roundtrip_and_hashes(tmp_path):
    nodes = [{'nodeId': f'condition-{n}', 'value': True, 'left': 123.456,
              'right': 120.0, 'reason': 'condition evaluated without dropping evidence'} for n in range(64)]
    value = {'id': 'large', 'status': 'succeeded', 'datasetId': 'data',
             'report': {'traces': [{'barIndex': i, 'nodes': nodes} for i in range(5000)]}}
    expected_hash = digest(value)
    assert len(canonical(value).encode()) > 16_000_000
    store = Store(tmp_path)
    store.put('run', 'large', value)
    with store.connection() as con:
        body, kind = con.execute('SELECT body,typeof(body) FROM objects').fetchone()
    assert kind == 'blob' and body.startswith(storage.COMPRESSED_PREFIX)
    assert len(body) <= storage.MAX_STORED_BYTES
    assert encode_body(value) == body
    reopened = Store(tmp_path)
    restored = reopened.get('run', 'large')
    assert restored == value and digest(restored) == expected_hash
    assert reopened.list('run') == [value]
    assert reopened.list_summaries('run') == [{'id': 'large', 'status': 'succeeded', 'datasetId': 'data'}]
    reopened.put('run', 'large', value)
    with pytest.raises(ValueError, match='Immutable'):
        reopened.put('run', 'large', dict(value, datasetId='other'))


def test_immutable_put_accepts_legacy_text_and_compressed_equivalent(tmp_path, monkeypatch):
    store = Store(tmp_path)
    value = {'id': 'same', 'report': {'evidence': '한글' * 1000}}
    store.put('run', 'same', value)
    monkeypatch.setattr(storage, 'COMPRESSION_THRESHOLD', 1)
    assert isinstance(encode_body(value), bytes)
    store.put('run', 'same', value)
    with store.connection() as con:
        assert con.execute('SELECT typeof(body) FROM objects').fetchone()[0] == 'text'
        con.execute('UPDATE objects SET body=?', (encode_body(value),))
    monkeypatch.setattr(storage, 'COMPRESSION_THRESHOLD', 100_000)
    store.put('run', 'same', value)


def test_compressed_finish_and_orphan_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'COMPRESSION_THRESHOLD', 1)
    store = Store(tmp_path)
    evidence = {'traces': ['full evidence' * 1000]}
    for kind in ('run', 'validation-job'):
        store.put(kind, kind, {'id': kind, 'status': 'running', **evidence})
        store.finish_run(kind, {'id': kind, 'status': 'succeeded', **evidence}, kind=kind)
        with pytest.raises(ValueError, match='Finalized'):
            store.finish_run(kind, {'status': 'succeeded'}, kind=kind)
        store.put(kind, kind + '-orphan', {'id': kind + '-orphan', 'status': 'running', **evidence})
    restarted = Store(tmp_path)
    for kind in ('run', 'validation-job'):
        assert restarted.get(kind, kind)['status'] == 'succeeded'
        orphan = restarted.get(kind, kind + '-orphan')
        assert orphan['status'] == 'interrupted' and orphan['traces'] == evidence['traces']


@pytest.mark.parametrize('version', [0, 1, 2])
def test_multirecord_migration_preserves_metadata_and_originals(tmp_path, version):
    values = [{'id': f'old-{i}', 'datasetId': f'data-{i % 2}', 'status': 'succeeded',
               'createdAt': '2026-01-01', 'report': {'traces': ['full'] * 50}}
              for i in range(7)]
    with sqlite3.connect(tmp_path / 'strategy.sqlite3') as con:
        con.execute('CREATE TABLE objects(kind TEXT,id TEXT,body TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(kind,id))')
        if version == 2:
            for column in ('status', 'dataset_id', 'summary'):
                con.execute(f'ALTER TABLE objects ADD COLUMN {column} TEXT')
        con.execute(f'PRAGMA user_version={version}')
        for value in values:
            con.execute('INSERT INTO objects(kind,id,body) VALUES(?,?,?)', ('run', value['id'], json.dumps(value)))
            if version == 2:
                con.execute('UPDATE objects SET status=?,dataset_id=?,summary=? WHERE id=?', (*Store.metadata(value), value['id']))
    store = Store(tmp_path)
    assert store.list('run') == values[::-1]
    assert store.list_summaries('run') == [json.loads(Store.metadata(v)[2]) for v in values[::-1]]
    assert store.count_by_dataset('run', 'data-0') == 4
    with store.connection() as con:
        assert con.execute('PRAGMA user_version').fetchone()[0] == 3
    assert Store(tmp_path).list('run') == values[::-1]


def test_bounded_decoding_rejects_bombs_and_corruption(monkeypatch):
    monkeypatch.setattr(storage, 'MAX_RAW_BYTES', 1000)
    monkeypatch.setattr(storage, 'MAX_STORED_BYTES', 2000)
    prefix = storage.COMPRESSED_PREFIX
    exact = '"' + 'x' * 998 + '"'
    assert decode_body(prefix + zlib.compress(exact.encode())) == 'x' * 998
    with pytest.raises(ValueError, match='decoded storage limit'):
        decode_body(prefix + zlib.compress(b'"' + b'x' * 10000 + b'"'))
    valid = prefix + zlib.compress(b'{"id":"ok"}')
    for corrupt in (valid[:-1], valid + b'trailing', valid + zlib.compress(b'{}'),
                    prefix + b'invalid', b'unknown', prefix + zlib.compress(b'\xff')):
        with pytest.raises(ValueError):
            decode_body(corrupt)
    with pytest.raises(ValueError):
        decode_body('"' + 'x' * 1000 + '"')
    monkeypatch.setattr(storage, 'MAX_STORED_BYTES', len(valid) - 1)
    with pytest.raises(ValueError, match='local storage limit'):
        decode_body(valid)


def test_encoding_enforces_raw_and_stored_limits(monkeypatch):
    monkeypatch.setattr(storage, 'MAX_RAW_BYTES', 1000)
    monkeypatch.setattr(storage, 'MAX_STORED_BYTES', 100)
    monkeypatch.setattr(storage, 'COMPRESSION_THRESHOLD', 1)
    with pytest.raises(ValueError, match='decoded storage limit'):
        encode_body({'evidence': 'x' * 1000})
    # Repetitive evidence fits stored bounds, but varied content cannot.
    assert decode_body(encode_body({'evidence': 'x' * 900})) == {'evidence': 'x' * 900}
    with pytest.raises(ValueError, match='local storage limit'):
        encode_body({'evidence': list(range(100))})
    monkeypatch.setattr(storage, 'COMPRESSION_THRESHOLD', 1000)
    with pytest.raises(ValueError, match='local storage limit'):
        encode_body({'evidence': 'x' * 100})
