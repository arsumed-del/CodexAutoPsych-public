"""SQLite state, role isolation, append-only records, and OS owner locks.

Local filesystem only. flock is supported on Linux and macOS; network shares
and copying a live database are intentionally unsupported.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import fcntl

ROLES = ('coordinator', 'coding-worker', 'independent-reviewer')

def now():
    return datetime.now(timezone.utc).isoformat()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def protocol_hash(protocol):
    return digest(canonical(protocol).encode())

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, role TEXT NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 owner TEXT, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
 created TEXT NOT NULL, updated TEXT NOT NULL, result TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS dependencies (
 task INTEGER NOT NULL REFERENCES tasks(id), dependency INTEGER NOT NULL REFERENCES tasks(id),
 PRIMARY KEY(task,dependency), CHECK(task != dependency));
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, category TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, label TEXT NOT NULL, protocol TEXT NOT NULL, protocol_hash TEXT NOT NULL,
 approval TEXT NOT NULL, status TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
 execution_count INTEGER NOT NULL DEFAULT 0, owner TEXT, created TEXT NOT NULL,
 started TEXT, finished TEXT, log_path TEXT, raw_hash TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS claims (
 id INTEGER PRIMARY KEY, record TEXT NOT NULL, timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lock_history (
 id INTEGER PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL, acquired TEXT NOT NULL,
 released TEXT);
CREATE TABLE IF NOT EXISTS research_questions (
 id TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_outcomes (
 id TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_withdrawals (
 outcome_id TEXT PRIMARY KEY REFERENCES research_outcomes(id), record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_syntheses (
 review_task INTEGER PRIMARY KEY REFERENCES tasks(id), record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS program_actions (
 key TEXT PRIMARY KEY, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS program_studies (
 slug TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS services (
 name TEXT PRIMARY KEY, paused_until TEXT, reason TEXT, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS service_usage (
 service TEXT NOT NULL, day TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(service, day));
'''

# Columns added for routine (model-backed) work. Existing labs are migrated in place.
TASK_COLUMNS = (('service', "TEXT NOT NULL DEFAULT 'deterministic'"), ('not_before', 'TEXT'))

# A dependency in any of these states can never succeed, so dependents are blocked.
BLOCKING_STATES = ('failed', 'blocked', 'rejected')

class Busy(RuntimeError):
    pass

class Lab:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._held_locks = set()
        self.db = sqlite3.connect(self.root / 'lab.sqlite', timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        existing = {r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}
        for name, spec in TASK_COLUMNS:
            if name not in existing:
                self.db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {spec}')
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        nested = self.db.in_transaction
        self.db.execute('SAVEPOINT nested_transaction' if nested else 'BEGIN IMMEDIATE')
        try:
            yield
            if nested:
                self.db.execute('RELEASE nested_transaction')
            else:
                self.db.commit()
        except BaseException:
            if nested:
                self.db.execute('ROLLBACK TO nested_transaction')
                self.db.execute('RELEASE nested_transaction')
            else:
                self.db.rollback()
            raise

    def event(self, category, detail):
        self.db.execute('INSERT INTO events(timestamp,category,detail) VALUES(?,?,?)',
                        (now(), category, canonical(detail)))

    @contextmanager
    def lock(self, name, owner):
        if name not in ('cycle', 'execution'):
            raise ValueError('Unknown lock')
        if not owner.strip():
            raise ValueError('Owner must be nonempty')
        with (self.root / (name + '.lock')).open('a+') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Busy(f'{name} already has an owner') from exc
            history = None
            try:
                if name == 'execution':
                    with self.transaction():
                        # Obtaining flock proves every prior holder released or died.
                        self.db.execute('UPDATE lock_history SET released=? WHERE released IS NULL', (now(),))
                        history = self.db.execute(
                            'INSERT INTO lock_history(owner,pid,acquired) VALUES(?,?,?)',
                            (owner, os.getpid(), now())).lastrowid
                self._held_locks.add(name)
                yield
            finally:
                self._held_locks.discard(name)
                if history is not None:
                    with self.transaction():
                        self.db.execute('UPDATE lock_history SET released=? WHERE id=?', (now(), history))
                fcntl.flock(handle, fcntl.LOCK_UN)

    def enqueue(self, kind, role, payload, dependencies=(), max_attempts=2):
        kinds = {'plan': 'coding-worker', 'execute': 'coordinator',
                 'audit': 'independent-reviewer', 'report': 'coordinator'}
        if kinds.get(kind) != role:
            raise ValueError('Kind requires its dedicated role')
        if not 1 <= max_attempts <= 10:
            raise ValueError('Retries must be bounded: max_attempts 1..10')
        deps = list(dict.fromkeys(dependencies))
        with self.transaction():
            for dep in deps:
                if not self.db.execute('SELECT 1 FROM tasks WHERE id=?', (dep,)).fetchone():
                    raise ValueError(f'Unknown dependency {dep}')
            tid = self.db.execute(
                'INSERT INTO tasks(kind,role,payload,max_attempts,created,updated) VALUES(?,?,?,?,?,?)',
                (kind, role, canonical(payload), max_attempts, now(), now())).lastrowid
            self.db.executemany('INSERT INTO dependencies VALUES(?,?)', [(tid, dep) for dep in deps])
            self.event('enqueue', {'task': tid, 'role': role, 'dependencies': deps})
        return tid

    def _recover_tasks(self):
        # Called only while holding the exclusive cycle lock, never on a timeout.
        if 'cycle' not in self._held_locks:
            raise ValueError('Task recovery requires the cycle lock')
        with self.transaction():
            rows = self.db.execute("SELECT * FROM tasks WHERE state='running'").fetchall()
            for row in rows:
                state = 'queued' if row['attempts'] < row['max_attempts'] else 'failed'
                self.db.execute('UPDATE tasks SET state=?,owner=NULL,error=?,updated=? WHERE id=?',
                                (state, 'Interrupted worker recovered', now(), row['id']))
                self.event('recovery', {'task': row['id'], 'state': state})

    def claim(self, role, owner):
        if 'cycle' not in self._held_locks:
            raise ValueError('Claiming a task requires the cycle lock')
        if role not in ROLES or not owner.strip():
            raise ValueError('Valid role and nonempty owner required')
        with self.transaction():
            self.db.execute("""UPDATE tasks SET state='blocked',updated=? WHERE state='queued'
                AND EXISTS (SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.dependency
                    WHERE d.task=tasks.id AND p.state IN ('failed','blocked','rejected'))""", (now(),))
            row = self.db.execute("""SELECT t.* FROM tasks t WHERE t.role=? AND t.state='queued'
                AND t.service='deterministic'
                AND t.attempts<t.max_attempts AND NOT EXISTS (
                    SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.dependency
                    WHERE d.task=t.id AND p.state!='succeeded') ORDER BY t.id LIMIT 1""", (role,)).fetchone()
            if not row:
                return None
            self.db.execute("UPDATE tasks SET state='running',owner=?,attempts=attempts+1,updated=? WHERE id=?",
                            (owner, now(), row['id']))
            self.event('claim', {'task': row['id'], 'owner': owner, 'role': role})
        return self.task(row['id'])

    def task(self, tid):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if row is None:
            raise ValueError('Unknown task')
        return dict(row)

    def context(self, task):
        # New context each time: no conversation/session reuse or unrelated tasks.
        return {'task_id': task['id'], 'role': task['role'], 'kind': task['kind'],
                'payload': json.loads(task['payload']),
                'dependencies': [dict(r) for r in self.db.execute(
                    'SELECT p.id,p.role,p.owner,p.result FROM dependencies d JOIN tasks p '
                    'ON p.id=d.dependency WHERE d.task=?', (task['id'],))],
                'worker_type': 'deterministic synthetic role scaffold; NOT a live AI agent'}

    def finish(self, tid, owner, result=None, error=None):
        with self.transaction():
            row = self.task(tid)
            if row['state'] != 'running' or row['owner'] != owner:
                raise ValueError('Only the current task owner can finish a running task')
            state = 'succeeded' if error is None else (
                'queued' if row['attempts'] < row['max_attempts'] else 'failed')
            self.db.execute('UPDATE tasks SET state=?,result=?,error=?,updated=? WHERE id=?',
                            (state, canonical(result) if error is None else None, error, now(), tid))
            self.event('finish', {'task': tid, 'state': state, 'error': error})

    def register(self, eid, protocol, approval='synthetic-demo'):
        if not eid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in eid):
            raise ValueError('Experiment ID must use letters, digits, hyphen, underscore')
        if approval != 'synthetic-demo' or protocol.get('synthetic') is not True:
            raise ValueError('V1 runs only explicitly labeled synthetic fixtures; real protocols require approval and a separate runner')
        if protocol.get('phase') not in ('exploratory', 'confirmation'):
            raise ValueError('Protocol must declare exploratory or confirmation phase')
        with self.transaction():
            self.db.execute('INSERT INTO experiments(id,label,protocol,protocol_hash,approval,status,created) '
                            'VALUES(?,?,?,?,?,?,?)',
                            (eid, 'SYNTHETIC FIXTURE — not Study 3', canonical(protocol),
                             protocol_hash(protocol), approval, 'registered', now()))
            self.event('decision', {'experiment': eid, 'decision': 'Synthetic-only execution authorized; no real protocol approved'})

    def experiment(self, eid):
        row = self.db.execute('SELECT * FROM experiments WHERE id=?', (eid,)).fetchone()
        if row is None:
            raise ValueError('Unknown experiment')
        return dict(row)

    def add_claim(self, record):
        required = ('claim', 'sources', 'competing_explanations', 'predictions', 'evidence', 'falsifiers', 'scope')
        if any(not record.get(k) for k in required):
            raise ValueError('Claims require sources, alternatives, predictions, evidence, falsifiers, and scope')
        for key in ('sources', 'competing_explanations', 'predictions', 'evidence', 'falsifiers'):
            if not isinstance(record[key], list):
                raise ValueError(f'{key} must be a nonempty list')
        for source in record['sources']:
            if not isinstance(source, dict) or not source.get('reference') or not source.get('locator'):
                raise ValueError('Each source needs a reference and a specific locator')
        with self.transaction():
            cid = self.db.execute('INSERT INTO claims(record,timestamp) VALUES(?,?)', (canonical(record), now())).lastrowid
            self.event('claim-recorded', {'claim_id': cid})
        return cid

    def cycle(self, role, owner, limit=1):
        if not 1 <= limit <= 100:
            raise ValueError('Cycle limit must be 1..100')
        from .workers import handle
        results = []
        with self.lock('cycle', owner):
            self._recover_tasks()
            for _ in range(limit):
                task = self.claim(role, owner)
                if task is None:
                    break
                try:
                    result = handle(self, self.context(task), owner)
                except Exception as exc:
                    self.finish(task['id'], owner, error=f'{type(exc).__name__}: {exc}')
                else:
                    self.finish(task['id'], owner, result=result)
                results.append(self.task(task['id']))
        return results

    def status(self):
        return {'tasks': [dict(r) for r in self.db.execute('SELECT * FROM tasks ORDER BY id')],
                'experiments': [dict(r) for r in self.db.execute('SELECT * FROM experiments ORDER BY id')],
                'claims': [dict(r) for r in self.db.execute('SELECT * FROM claims ORDER BY id')],
                'execution_owners': [dict(r) for r in self.db.execute('SELECT * FROM lock_history WHERE released IS NULL')],
                'note': 'Lock history is observational; OS flock is authoritative. No verified connection to Study 3.'}

    def reports(self):
        status = self.status()
        write_atomic(self.root / 'STATUS.json', canonical(status) + '\n')
        events = [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY id')]
        write_atomic(self.root / 'DECISIONS.jsonl', ''.join(canonical(r) + '\n' for r in events))
        write_atomic(self.root / 'HANDOFF.md', '# Coordinator handoff\n\n'
            'SYNTHETIC WORKFLOW ONLY. Study 3 is unconnected; no live status is known.\n\n'
            f'Tasks: {len(status["tasks"])}; experiments: {len(status["experiments"])}.\n'
            'Read STATUS.json and DECISIONS.jsonl. Start bounded cycles with distinct role owners.\n'
            'Never relaunch an interrupted experiment. Preserve its raw artifacts for reconciliation.\n')
        return status


def write_atomic(path, text):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.', delete=False) as out:
        temp = Path(out.name)
        try:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
