"""Deterministic synthetic runner. No model calls, shell execution, or real studies."""
import json
import math
from pathlib import Path
from .core import canonical, digest, now, protocol_hash, write_atomic
from . import dynamics


def validate(protocol):
    if protocol.get('synthetic') is not True or protocol.get('runner') != 'synthetic-offset-v1':
        raise ValueError('Only synthetic-offset-v1 is supported')
    values = protocol.get('values')
    if not isinstance(values, list) or not 1 <= len(values) <= 10000:
        raise ValueError('Need 1..10000 synthetic input values')
    if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e9 for v in values):
        raise ValueError('Values must be finite bounded numbers')
    offset = protocol.get('offset')
    if type(offset) not in (int, float) or not math.isfinite(offset) or abs(offset) > 1e9:
        raise ValueError('Offset must be finite and bounded')
    return values, offset


def verify_artifacts(lab, exp):
    folder = lab.root / 'runs' / exp['id']
    manifest = json.loads((folder / 'manifest.json').read_text())
    raw = (folder / 'raw.jsonl').read_bytes()
    saved_protocol = json.loads((folder / 'protocol.json').read_text())
    expected = exp['protocol_hash']
    if protocol_hash(json.loads(exp['protocol'])) != expected or protocol_hash(saved_protocol) != expected:
        raise ValueError('Protocol integrity failure')
    if manifest['protocol_hash'] != expected or manifest['raw_hash'] != digest(raw):
        raise ValueError('Artifact integrity failure')
    if manifest['experiment_id'] != exp['id'] or manifest['synthetic'] is not True:
        raise ValueError('Manifest identity mismatch')
    protocol = json.loads(exp['protocol'])
    feedback = protocol.get('runner') == dynamics.RUNNER
    if feedback:
        dynamics.validate(protocol)
        negative = dynamics.negative_result(protocol)
    else:
        values, offset = validate(protocol)
        negative = offset == 0
    if manifest.get('negative_result') != negative or manifest.get('deviations') != []:
        raise ValueError('Manifest outcome metadata mismatch')
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    if feedback:
        dynamics.verify(protocol, rows)
    else:
        expected_rows = [{'index': i, 'input': v, 'output': v + offset, 'synthetic': True}
                         for i, v in enumerate(values)]
        if rows != expected_rows:
            raise ValueError('Raw results do not match deterministic protocol')
    if exp['raw_hash'] is not None and exp['raw_hash'] != digest(raw):
        raise ValueError('Registry raw checksum mismatch')
    return manifest


def recover(lab):
    """Caller must own execution lock. Never execute during reconciliation."""
    if 'execution' not in lab._held_locks:
        raise ValueError('Experiment recovery requires the execution lock')
    pending = lab.db.execute("SELECT * FROM experiments WHERE status='running'").fetchall()
    for row in pending:
        exp = dict(row)
        try:
            manifest = verify_artifacts(lab, exp)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            with lab.transaction():
                lab.db.execute("UPDATE experiments SET status='interrupted',verified=0,error=?,finished=? WHERE id=?",
                               (f'Uncertain interrupted execution: {exc}', now(), exp['id']))
                lab.event('deviation', {'experiment': exp['id'], 'detail': 'Interrupted; automatic relaunch prohibited'})
        else:
            with lab.transaction():
                lab.db.execute("UPDATE experiments SET status='completed',verified=1,raw_hash=?,finished=? WHERE id=?",
                               (manifest['raw_hash'], now(), exp['id']))
                lab.event('reconciled', {'experiment': exp['id'], 'detail': 'Verified existing artifacts; no re-execution'})


def execute(lab, eid, owner):
    with lab.lock('execution', owner):
        recover(lab)
        exp = lab.experiment(eid)
        protocol = json.loads(exp['protocol'])
        if protocol_hash(protocol) != exp['protocol_hash']:
            raise ValueError('Protocol integrity failure before execution')
        if exp['status'] == 'completed':
            verify_artifacts(lab, exp)
            return {'experiment': eid, 'reused': True, 'execution_count': exp['execution_count']}
        if exp['status'] != 'registered' or exp['execution_count'] != 0:
            raise ValueError('Experiment not launchable; interrupted/failed runs require reconciliation, never automatic retry')
        if exp['approval'] != 'synthetic-demo':
            raise ValueError('No synthetic execution approval')
        feedback = protocol.get('runner') == dynamics.RUNNER
        if feedback:
            dynamics.validate(protocol)
            rows = dynamics.generate(protocol)
            negative = dynamics.negative_result(protocol)
        else:
            values, offset = validate(protocol)
            rows = ({'index': i, 'input': v, 'output': v + offset, 'synthetic': True}
                    for i, v in enumerate(values))
            negative = offset == 0
        folder = lab.root / 'runs' / eid
        folder.parent.mkdir(exist_ok=True)
        # Durable intent BEFORE artifacts. A crash here consumes the launch token.
        with lab.transaction():
            lab.db.execute("UPDATE experiments SET status='running',owner=?,started=?,execution_count=1,log_path=? WHERE id=?",
                           (owner, now(), str(folder / 'raw.jsonl'), eid))
            lab.event('execution-started', {'experiment': eid, 'owner': owner})
        try:
            folder.mkdir(exist_ok=False)
            # Exclusive writes: preserve original raw outputs and approved protocol.
            with (folder / 'protocol.json').open('x') as out:
                out.write(canonical(protocol) + '\n')
                out.flush()
                import os
                os.fsync(out.fileno())
            with (folder / 'raw.jsonl').open('x') as out:
                for row in rows:
                    out.write(canonical(row) + '\n')
                out.flush()
                os.fsync(out.fileno())
            raw_hash = digest((folder / 'raw.jsonl').read_bytes())
            write_atomic(folder / 'manifest.json', canonical({
                'experiment_id': eid, 'synthetic': True, 'protocol_hash': exp['protocol_hash'],
                'raw_hash': raw_hash, 'finished': now(), 'negative_result': negative,
                'deviations': [], 'interpretation': dynamics.LIMITATION if feedback else 'Arithmetic fixture only; no psychology evidence'}) + '\n')
            verify_artifacts(lab, lab.experiment(eid))
        except Exception as exc:
            with lab.transaction():
                lab.db.execute("UPDATE experiments SET status='failed',verified=0,error=?,finished=? WHERE id=?",
                               (str(exc), now(), eid))
                lab.event('deviation', {'experiment': eid, 'detail': str(exc)})
            raise
        with lab.transaction():
            lab.db.execute("UPDATE experiments SET status='completed',verified=1,raw_hash=?,finished=? WHERE id=?",
                           (raw_hash, now(), eid))
            lab.event('execution-completed', {'experiment': eid, 'raw_hash': raw_hash, 'negative_result': negative})
        return {'experiment': eid, 'reused': False, 'execution_count': 1}
