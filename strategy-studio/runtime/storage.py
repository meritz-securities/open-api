"""Bounded local SQLite snapshots; no credentials or arbitrary tool-selected paths."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import zlib


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


# Hashes always describe canonical JSON, independently of its on-disk encoding.
MAX_RAW_BYTES = 128_000_000
MAX_STORED_BYTES = 16_000_000
COMPRESSION_THRESHOLD = 256_000
COMPRESSED_PREFIX = b'MERITZ-SNAPSHOT-ZLIB-1\x00'


def encode_body(value):
    text = canonical(value)
    raw = text.encode('utf-8')
    if len(raw) > MAX_RAW_BYTES:
        raise ValueError('Snapshot exceeds decoded storage limit')
    if len(raw) >= COMPRESSION_THRESHOLD:
        compressed = COMPRESSED_PREFIX + zlib.compress(raw, level=6)
        if len(compressed) < len(raw):
            if len(compressed) > MAX_STORED_BYTES:
                raise ValueError('Snapshot exceeds local storage limit')
            return compressed
    if len(raw) > MAX_STORED_BYTES:
        raise ValueError('Snapshot exceeds local storage limit')
    return text


def decode_body(body):
    if isinstance(body, str):
        if len(body.encode('utf-8')) > min(MAX_RAW_BYTES, MAX_STORED_BYTES):
            raise ValueError('Snapshot exceeds local storage limit')
        return json.loads(body)
    if not isinstance(body, bytes) or not body.startswith(COMPRESSED_PREFIX):
        raise ValueError('Unsupported snapshot encoding')
    if len(body) > MAX_STORED_BYTES:
        raise ValueError('Snapshot exceeds local storage limit')
    decoder = zlib.decompressobj()
    try:
        # Never use unbounded decompress/flush: even a tiny BLOB can expand
        # beyond memory limits. One extra byte distinguishes the exact limit.
        raw = decoder.decompress(body[len(COMPRESSED_PREFIX):], MAX_RAW_BYTES + 1)
        if len(raw) > MAX_RAW_BYTES or decoder.unconsumed_tail:
            raise ValueError('Snapshot exceeds decoded storage limit')
        if not decoder.eof or decoder.unused_data:
            raise ValueError('Corrupt compressed snapshot')
        return json.loads(raw.decode('utf-8'))
    except (zlib.error, UnicodeDecodeError) as error:
        raise ValueError('Corrupt compressed snapshot') from error


def default_root():
    override = os.environ.get('MERITZ_STRATEGY_HOME')
    if override:
        return Path(override).expanduser()
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MeritzStrategy'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Meritz Strategy Platform'
    return Path.home() / '.local/share/meritz-strategy'


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else default_root()
        if self.root.is_symlink():
            raise ValueError('Storage root must not be a symlink')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / 'strategy.sqlite3'
        if self.path.is_symlink():
            raise ValueError('Database must not be a symlink')
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ValueError('Unsupported database version; do not downgrade')
            con.execute('CREATE TABLE IF NOT EXISTS objects(kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(kind,id))')
            if version < 2:
                for column in ('status', 'dataset_id', 'summary'):
                    con.execute(f'ALTER TABLE objects ADD COLUMN {column} TEXT')
                # One-time migration streams a single snapshot at a time.
                cursor = con.execute('SELECT rowid,body FROM objects')
                for rowid, body in cursor:
                    value = decode_body(body)
                    con.execute('UPDATE objects SET status=?,dataset_id=?,summary=? WHERE rowid=?', (*self.metadata(value), rowid))
            con.execute('CREATE INDEX IF NOT EXISTS objects_kind_status ON objects(kind,status)')
            con.execute('CREATE INDEX IF NOT EXISTS objects_kind_dataset ON objects(kind,dataset_id)')
            con.execute("CREATE INDEX IF NOT EXISTS objects_validation_job ON objects(kind, json_extract(summary, '$.validationJobId'))")
            con.execute("CREATE INDEX IF NOT EXISTS objects_source_run ON objects(kind, json_extract(summary, '$.runId'))")
            con.execute('PRAGMA user_version=3')
        self.path.chmod(0o600)
        # Probe without waiting: another process may still own a valid job.
        with self.execution_lock(allow_busy=True):
            pass

    @staticmethod
    def metadata(value):
        # Immutable payload remains available through get/list; listings use this
        # bounded projection and never deserialize report/equity/bars payloads.
        fields = ('id', 'strategyId', 'revisionId', 'datasetId', 'parentRunId', 'status',
                  'createdAt', 'finishedAt', 'method', 'options', 'settings', 'error',
                  'validationId', 'validationJobId', 'aiRequestId', 'aiJobId', 'researchId', 'researchPhase', 'researchSlot', 'derivedFrom', 'runId', 'elapsedSeconds')
        summary = {key: value[key] for key in fields if key in value}
        return value.get('status'), value.get('datasetId') or value.get('derivedFrom'), canonical(summary)

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.execute('PRAGMA busy_timeout=5000')
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA journal_mode=WAL')
        try:
            with con:
                yield con
        finally:
            con.close()

    def put(self, kind, ident, value):
        encoded = encode_body(value)
        with self.connection() as con:
            existing = con.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, ident)).fetchone()
            if existing:
                if canonical(decode_body(existing[0])) != canonical(value):
                    raise ValueError('Immutable snapshot cannot be overwritten')
                return value
            con.execute('INSERT INTO objects(kind,id,body,status,dataset_id,summary) VALUES(?,?,?,?,?,?)', (kind, ident, encoded, *self.metadata(value)))
        return value

    def finish_run(self, ident, value, kind='run'):
        if kind not in ('run', 'validation-job', 'ai-request-job'):
            raise ValueError('Unsupported mutable job kind')
        encoded = encode_body(value)
        with self.connection() as con:
            row = con.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, ident)).fetchone()
            if not row or decode_body(row[0]).get('status') not in ('queued', 'running'):
                raise ValueError('Finalized run cannot be overwritten')
            con.execute('UPDATE objects SET body=?,status=?,dataset_id=?,summary=? WHERE kind=? AND id=?', (encoded, *self.metadata(value), kind, ident))

    def get(self, kind, ident):
        if not isinstance(ident, str) or not 1 <= len(ident) <= 80:
            raise ValueError('Invalid snapshot identifier')
        with self.connection() as con:
            row = con.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, ident)).fetchone()
        if row is None:
            raise ValueError('Snapshot not found')
        return decode_body(row[0])

    def list(self, kind, limit=100, exclude_derived=False):
        if exclude_derived and kind != 'dataset': raise ValueError('Derived filter is for datasets only')
        where = ' AND dataset_id IS NULL' if exclude_derived else ''
        with self.connection() as con:
            rows = con.execute('SELECT body FROM objects WHERE kind=?' + where + ' ORDER BY rowid DESC LIMIT ?', (kind, limit)).fetchall()
        return [decode_body(row[0]) for row in rows]

    def list_summaries(self, kind, limit=100):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid summary limit')
        with self.connection() as con:
            rows = con.execute('SELECT summary FROM objects WHERE kind=? ORDER BY rowid DESC LIMIT ?', (kind, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def ai_job_runs(self, job_id):
        # At most three candidates per AI submission; summaries only, no report loads.
        with self.connection() as con:
            rows = con.execute("SELECT summary FROM objects WHERE kind='run' AND json_extract(summary, '$.aiJobId')=? ORDER BY rowid LIMIT 3", (job_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def validation_job_runs(self, job_id):
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 80:
            raise ValueError('Invalid validation job identifier')
        with self.connection() as con:
            rows = con.execute("SELECT summary FROM objects WHERE kind='run' AND json_extract(summary, '$.validationJobId')=? ORDER BY rowid LIMIT 13", (job_id,)).fetchall()
        if len(rows) > 12:
            raise ValueError('Parameter grid child count exceeds limit')
        return [json.loads(row[0]) for row in rows]

    def _research_decision(self, research_id, decision, value):
        """Atomically order cancellation against freeze, phase claims and results.

        Fixed domain mappings only: callers cannot supply table names or SQL.
        This transaction is deliberately independent of the long engine lock.
        """
        if not isinstance(research_id, str) or len(research_id) != 32 or any(c not in '0123456789abcdef' for c in research_id):
            raise ValueError('Invalid research identifier')
        choices = {
            'freeze': ('research-freeze', research_id, 'research-cancel', research_id + '-training'),
            'cancel_training': ('research-cancel', research_id + '-training', 'research-freeze', research_id),
            'claim_training': ('validation-job', 'research-training-' + research_id, 'research-cancel', research_id + '-training'),
            'claim_evaluation': ('validation-job', 'research-evaluation-' + research_id, 'research-cancel', research_id + '-evaluation'),
            'cancel_evaluation': ('research-cancel', research_id + '-evaluation', 'research-result', research_id),
            'result': ('research-result', research_id, 'research-cancel', research_id + '-evaluation'),
        }
        if decision not in choices or not isinstance(value, dict):
            raise ValueError('Invalid research decision')
        kind, ident, guard_kind, guard_id = choices[decision]
        if kind == 'validation-job':
            phase = decision.removeprefix('claim_')
            if value.get('id') != ident or value.get('status') != 'running' or value.get('options') != {'researchId': research_id, 'phase': phase}:
                raise ValueError('Invalid research phase claim')
        elif (value.get('researchId') != research_id or
              value.get('recordHash') != digest({k: v for k, v in value.items() if k != 'recordHash'})):
            raise ValueError('Invalid research decision record')
        if kind == 'research-cancel' and value.get('phase') != decision.removeprefix('cancel_'):
            raise ValueError('Invalid research cancellation phase')
        encoded = encode_body(value)
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            guard_exists = con.execute('SELECT 1 FROM objects WHERE kind=? AND id=?', (guard_kind, guard_id)).fetchone() is not None
            if guard_exists and decision != 'result':
                return {'status': 'blocked', 'guard': guard_kind}
            if decision == 'result' and guard_exists:
                # Cancellation committed before the immutable aggregate result.
                # Child runs remain unchanged, including completed successes.
                value = {**value, 'status': 'cancelled', 'cancellationWon': True,
                         'error': '검증 결과 확정 전에 취소되었습니다.'}
                value['recordHash'] = digest({k: v for k, v in value.items() if k != 'recordHash'})
                encoded = encode_body(value)
            if decision == 'cancel_evaluation':
                if not con.execute("SELECT 1 FROM objects WHERE kind='research-freeze' AND id=?", (research_id,)).fetchone():
                    return {'status': 'blocked', 'guard': 'pool-not-frozen'}
                phase = con.execute("SELECT status FROM objects WHERE kind='validation-job' AND id=?", ('research-evaluation-' + research_id,)).fetchone()
                if phase and phase[0] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                    return {'status': 'blocked', 'guard': 'validation-job'}
            old = con.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, ident)).fetchone()
            if old:
                return {'status': 'already_exists', 'value': decode_body(old[0])}
            if decision in ('claim_evaluation', 'result') and not con.execute("SELECT 1 FROM objects WHERE kind='research-freeze' AND id=?", (research_id,)).fetchone():
                raise ValueError('Research pool is not frozen')
            if decision == 'result':
                owner = con.execute("SELECT status FROM objects WHERE kind='validation-job' AND id=?", ('research-evaluation-' + research_id,)).fetchone()
                if not owner or owner[0] != 'running':
                    raise ValueError('Research evaluation result requires its running phase claim')
            con.execute('INSERT INTO objects(kind,id,body,status,dataset_id,summary) VALUES(?,?,?,?,?,?)',
                        (kind, ident, encoded, *self.metadata(value)))
        return {'status': 'committed', 'value': value}

    def other_ai_requests_on_run(self, run_id, owned_request_id):
        with self.connection() as con:
            return con.execute("SELECT count(*) FROM objects WHERE kind='ai-request' AND json_extract(summary, '$.runId')=? AND id!=?", (run_id, owned_request_id)).fetchone()[0]

    def research_history_counts(self, dataset_id):
        with self.connection() as con:
            attempts = con.execute("SELECT count(*) FROM objects WHERE kind='run' AND dataset_id=?", (dataset_id,)).fetchone()[0]
            successes = con.execute("SELECT count(*) FROM objects WHERE kind='run' AND dataset_id=? AND status='succeeded'", (dataset_id,)).fetchone()[0]
            sessions = con.execute("SELECT count(*) FROM objects WHERE kind='research-plan' AND dataset_id=?", (dataset_id,)).fetchone()[0]
        return {'priorFullDatasetRunCount': attempts, 'priorSuccessfulFullDatasetRunCount': successes,
                'priorResearchSessionCount': sessions}

    def research_plans(self, limit=100, source=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('research limit must be an integer from 1 to 100')
        source = {} if source is None else source
        if (type(source) is not dict or set(source) - {'runId', 'revisionId', 'datasetId'} or
                ('runId' in source and len(source) != 1) or
                ('revisionId' in source and 'datasetId' not in source) or
                any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in source.values())):
            raise ValueError('invalid research source filter')
        clauses = ["kind='research-plan'"]; values = []
        for field in ('runId', 'revisionId', 'datasetId'):
            if field in source:
                clauses.append('dataset_id=?' if field == 'datasetId' else "json_extract(summary, '$." + field + "')=?")
                values.append(source[field])
        with self.connection() as con:
            rows = con.execute('SELECT body FROM objects WHERE ' + ' AND '.join(clauses) + ' ORDER BY rowid DESC LIMIT ?', (*values, limit)).fetchall()
        return [decode_body(row[0]) for row in rows]

    def count_by_dataset(self, kind, dataset_id):
        with self.connection() as con:
            return con.execute('SELECT count(*) FROM objects WHERE kind=? AND dataset_id=?', (kind, dataset_id)).fetchone()[0]

    def clear(self):
        """Delete every stored object and reclaim disk space. Refuses while another engine/AI process holds a lock.

        Only this store's own SQLite objects are removed; no other path is touched. The schema and
        indexes stay, so the same Store keeps working after the reset.
        """
        with self.execution_lock():
            if os.name == 'posix':
                with self._ai_lock('ai-generation.lock'):
                    return self._clear_objects()
            return self._clear_objects()

    def _clear_objects(self):
        with self.connection() as con:
            removed = con.execute('SELECT count(*) FROM objects').fetchone()[0]
            con.execute('DELETE FROM objects')
            con.commit()
            con.execute('VACUUM')   # VACUUM cannot run inside the implicit transaction above.
        with self.connection() as con:
            if con.execute('SELECT count(*) FROM objects').fetchone()[0] != 0:
                raise ValueError('Local data reset did not complete')
        return removed

    @contextmanager
    def execution_lock(self, allow_busy=False):
        """One running engine per user store across Studio/MCP/CLI processes."""
        path = self.root / 'execution.lock'
        if path.is_symlink():
            raise ValueError('Unsafe execution lock')
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        handle = os.fdopen(descriptor, 'r+b')
        acquired = False
        try:
            if sys.platform == 'win32':
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    if allow_busy:
                        yield False
                        return
                    raise ValueError('Another local run is active') from None
            else:
                import fcntl
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if allow_busy:
                        yield False
                        return
                    raise ValueError('Another local run is active') from None
            acquired = True
            # The lock proves no other runtime currently owns these attempts.
            with self.connection() as con:
                rows = con.execute("SELECT kind,id,body FROM objects WHERE kind IN ('run','validation-job','ai-request-job') AND status IN ('queued','running')").fetchall()
                for kind, ident, body in rows:
                    value = decode_body(body)
                    if value.get('status') in ('queued', 'running'):
                        value.update(status='interrupted', error='앱이 종료되기 전에 실행이 완료되지 않았습니다.')
                        con.execute('UPDATE objects SET body=?,status=?,dataset_id=?,summary=? WHERE kind=? AND id=?', (encode_body(value), *self.metadata(value), kind, ident))
            yield
        finally:
            if acquired and sys.platform == 'win32':
                import msvcrt
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()

    @contextmanager
    def _ai_lock(self, name, blocking=False):
        # This optional provider is POSIX-only; never weaken Windows locking.
        if os.name != 'posix':
            raise ValueError('PROVIDER_PLATFORM_UNSUPPORTED')
        import fcntl
        descriptor = os.open(self.root / name, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(descriptor, 'r+b') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise ValueError('Another AI generation is active') from None
            yield

    @contextmanager
    def ai_request_gate(self, ident):
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Invalid AI request identifier')
        if os.name != 'posix':
            # Local generation is unavailable; preserve legacy external submission.
            yield
            return
        with self._ai_lock('ai-request-'+ident+'.lock', blocking=True):
            yield

    @contextmanager
    def ai_generation_lock(self):
        with self._ai_lock('ai-generation.lock'):
            # Ownership is proven by THIS lock, not the unrelated engine lock.
            with self.connection() as con:
                rows = con.execute("SELECT id,body FROM objects WHERE kind='ai-generation' AND status='generating'").fetchall()
                for ident, body in rows:
                    value = decode_body(body)
                    value.update(status='interrupted', error='Previous generation stopped before finalization')
                    con.execute("UPDATE objects SET body=?,status=?,summary=? WHERE kind='ai-generation' AND id=?",
                                (encode_body(value), 'interrupted', self.metadata(value)[2], ident))
            yield

    def transition_ai_generation(self, ident, value):
        if value.get('status') not in ('ready', 'failed', 'cancelled'):
            raise ValueError('Invalid generation transition')
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT body FROM objects WHERE kind='ai-generation' AND id=?", (ident,)).fetchone()
            previous = decode_body(row[0])['status'] if row else None
            allowed = {'generating': {'ready', 'failed', 'cancelled'}, 'ready': {'cancelled'}}
            if value.get('status') not in allowed.get(previous, set()):
                raise ValueError('Invalid generation transition')
            con.execute("UPDATE objects SET body=?,status=?,summary=? WHERE kind='ai-generation' AND id=?",
                        (encode_body(value), value['status'], self.metadata(value)[2], ident))
