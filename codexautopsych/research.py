"""Persistent, bounded research feedback loop; never an experiment executor.

Imported outcomes are owner-provided reports, not independently verified facts.
Review can guide the next question, never certify a field as solved.
"""
import json
from pathlib import Path
import re
from .core import canonical, digest, write_atomic
from . import program, routine


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value) or len(value) > 80:
        raise ValueError('Research IDs require lowercase letters, numbers and hyphens')
    return value


def _text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 20000:
        raise ValueError(f'{key} requires nonempty text up to 20000 characters')
    return value


def add_question(lab, record):
    q = {'id': _id(record.get('id')), 'question': _text(record, 'question'),
         'falsifier': _text(record, 'falsifier'), 'scope': 'model mechanisms only', 'status': 'open'}
    with lab.transaction():
        existing = lab.db.execute('SELECT record FROM research_questions WHERE id=?', (q['id'],)).fetchone()
        if existing:
            if json.loads(existing['record']) != q:
                raise ValueError('Question identity already exists; preserve original and use a new ID')
        else:
            lab.db.execute('INSERT INTO research_questions VALUES(?,?)', (q['id'], canonical(q)))
            lab.event('research-question', {'id': q['id'], 'charter': 'program/CHARTER.md', 'policy': program.POLICY})
    return q


def add_question_or_variant(lab, record):
    """Keep an existing question untouched; save a reworded one under the next free '-vN' ID."""
    try:
        return add_question(lab, record)
    except ValueError as exc:
        if 'already exists' not in str(exc):
            raise
    base = _id(record.get('id'))
    for n in range(2, 100):
        candidate = {**record, 'id': f'{base}-v{n}'}
        try:
            return add_question(lab, candidate)
        except ValueError as exc:
            if 'already exists' not in str(exc):
                raise
    raise ValueError('Too many variants of one question')


def _snapshot(lab, outcome):
    """Locate an outcome snapshot inside this lab, whatever machine recorded the record.

    New records store the lab-relative path; older ones may hold an absolute path from
    another machine, so the stored value is never used to find the file."""
    return lab.root / 'research' / 'outcomes' / (outcome['id'] + '.json')


def import_outcome(lab, path):
    """Explicit owner intake from local copies only; immutable and content-addressed."""
    root = program._folder() / 'sources'
    source = program._safe(Path(path).absolute(), root)
    routine.guard_input(routine.load_config(lab), source)
    data = source.read_bytes()
    record = json.loads(data)
    if not isinstance(record, dict):
        raise ValueError('Outcome must be an object')
    question = _id(record.get('question_id'))
    if not lab.db.execute('SELECT 1 FROM research_questions WHERE id=?', (question,)).fetchone():
        raise ValueError('Unknown research question')
    if record.get('scope') not in ('synthetic', 'model'):
        raise ValueError('Only synthetic or model outcomes accepted; human data is outside this workflow')
    if record.get('result') not in ('positive', 'negative', 'null', 'inconclusive', 'interrupted'):
        raise ValueError('Outcome result must include a supported result category')
    saved = {k: _text(record, k) for k in ('summary', 'methods', 'limitations')}
    saved.update({k: record[k] for k in ('question_id', 'scope', 'result')})
    if record.get('study_slug') is not None:
        slug = _id(record['study_slug'])
        study = program._study(lab, slug)
        if study['phase'] != 'ready to run':
            raise ValueError('Linked study must have a frozen reviewed build')
        saved['study_slug'] = slug
    oid = digest(data)
    target = lab.root / 'research' / 'outcomes' / (oid + '.json')
    program._save_once(target, data, lab.root)
    saved.update({'id': oid, 'source': str(source.relative_to(program.PROJECT_ROOT)),
                  'snapshot': str(target.relative_to(lab.root)), 'sha256': oid,
                  'evidence_status': 'owner-provided report; not independently verified'})
    with lab.transaction():
        inserted = lab.db.execute('INSERT OR IGNORE INTO research_outcomes VALUES(?,?)', (oid, canonical(saved)))
        if inserted.rowcount:
            lab.event('research-outcome', {'id': oid, 'question_id': question,
                'scope': saved['scope'], 'result': saved['result'],
                'charter': 'program/CHARTER.md', 'policy': program.POLICY})
    return saved


def withdraw_outcome(lab, oid, reason):
    """Owner-only explicit disposition, preserving the original report and its review history."""
    _text({'reason': reason}, 'reason')
    record = {'outcome_id': oid, 'reason': reason, 'source': 'explicit owner command',
              'charter': 'program/CHARTER.md', 'policy': program.POLICY}
    with lab.transaction():
        if not lab.db.execute('SELECT 1 FROM research_outcomes WHERE id=?', (oid,)).fetchone():
            raise ValueError('Unknown outcome')
        lab.db.execute('INSERT OR IGNORE INTO research_withdrawals VALUES(?,?)', (oid, canonical(record)))
        lab.event('research-outcome-withdrawn', record)
    return record


def state(lab):
    questions = [json.loads(r[0]) for r in lab.db.execute('SELECT record FROM research_questions ORDER BY id')]
    outcomes = [json.loads(r[0]) for r in lab.db.execute('SELECT record FROM research_outcomes ORDER BY rowid')]
    syntheses = [json.loads(r[0]) for r in lab.db.execute('SELECT record FROM research_syntheses ORDER BY review_task')]
    withdrawals = [json.loads(r[0]) for r in lab.db.execute('SELECT record FROM research_withdrawals ORDER BY rowid')]
    return {'questions': questions, 'outcomes': outcomes, 'syntheses': syntheses, 'withdrawals': withdrawals,
            'solved': False, 'scope': 'Computational model mechanisms; no conclusions about humans',
            'completion_rule': 'No automatic declaration that psychology is solved'}


def _covered(lab):
    covered = {r[0] for r in lab.db.execute('SELECT outcome_id FROM research_withdrawals')}
    for row in lab.db.execute("SELECT payload FROM tasks WHERE service='codex'"):
        meta = json.loads(row[0]).get('program', {})
        if meta.get('stage') == 'synthesis':
            covered.update(meta['outcomes'])
    return covered


def advance(lab, cfg):
    if 'cycle' not in lab._held_locks:
        raise ValueError('Research advance requires the cycle lock')
    if not cfg['research']['enabled'] or not cfg['program']['enabled']:
        return
    if not state(lab)['questions']:
        add_question(lab, {'id': 'affect-threshold',
            'question': 'Does affect persistence in an open-weight model show a threshold beyond capacity-matched nulls?',
            'falsifier': 'No preregistered threshold, or an equivalent effect in capacity-matched structural nulls.'})
    covered = _covered(lab)
    pending = [o for o in state(lab)['outcomes'] if o['id'] not in covered][:100]
    if not pending:
        return
    for outcome in pending:
        path = program._safe(_snapshot(lab, outcome), lab.root)
        if routine._sha(path) != outcome['sha256']:
            raise ValueError('Preserved outcome checksum mismatch')
    ids = [o['id'] for o in pending]
    snapshot = lab.root / 'research' / 'rounds' / digest(canonical(ids).encode()) / 'EVIDENCE.json'
    program._save_once(snapshot, canonical(state(lab)).encode(), lab.root)
    with lab.transaction():
        tasks = program._queue(lab, 'analysis', 'Research: evaluate outcomes and choose next questions',
            'Evaluate the provided owner reports, including null, negative, inconclusive and interrupted outcomes. '
            'Do not treat a report, AI agreement or synthetic fixture as verified psychological evidence. '
            'Identify alternative explanations, methodological weaknesses, and replication needs. '
            'Write outputs/SYNTHESIS.json with summary (text), limitations (text), outcome_ids '
            '(exactly the supplied IDs), and next_questions (1..5 objects with id, question, falsifier). '
            'All questions must remain within model-mechanism scope under the charter. Never declare psychology '
            'solved, infer human treatments, alter frozen protocols or execute experiments. '
            'The reviewer must check each interpretation against the supplied reports. '
            'Outcome IDs for this round: ' + canonical(ids),
            [snapshot, program._folder() / 'CHARTER.md', *[_snapshot(lab, o) for o in pending]],
            {'stage': 'synthesis', 'outcomes': ids})
        program._record(lab, f'research-round:{tasks["work"]}', {'outcomes': ids, 'task': tasks['work']})


def review_context(lab, meta):
    """Give the independent reviewer the actual preserved reports, not just prose conclusions."""
    reports = []
    for oid in meta['outcomes']:
        row = lab.db.execute('SELECT record FROM research_outcomes WHERE id=?', (oid,)).fetchone()
        if row is None:
            raise ValueError('Unknown outcome in review')
        outcome = json.loads(row[0])
        path = program._safe(_snapshot(lab, outcome), lab.root)
        data = path.read_bytes()
        if digest(data) != outcome['sha256']:
            raise ValueError('Preserved outcome checksum mismatch')
        reports.append({'id': oid, 'evidence_status': outcome['evidence_status'],
                        'preserved_report': json.loads(data)})
    return canonical(reports)


def accept_synthesis(lab, review, subject, meta):
    review_context(lab, meta)  # Recheck evidence before accepting a review, including manual decisions.
    if any(lab.db.execute('SELECT 1 FROM research_withdrawals WHERE outcome_id=?', (oid,)).fetchone()
           for oid in meta['outcomes']):
        raise ValueError('Synthesis contains an outcome withdrawn by the owner')
    files = program._outputs(lab, subject)
    result = json.loads(files['SYNTHESIS.json'].read_text())
    if not isinstance(result, dict) or set(result) != {'summary', 'limitations', 'outcome_ids', 'next_questions'}:
        raise ValueError('Synthesis schema mismatch')
    if not isinstance(result['outcome_ids'], list) or sorted(result['outcome_ids']) != sorted(meta['outcomes']):
        raise ValueError('Synthesis must account for every outcome in its round')
    summary, limitations = _text(result, 'summary'), _text(result, 'limitations')
    questions = result['next_questions']
    if not isinstance(questions, list) or not 1 <= len(questions) <= 5 or any(not isinstance(q, dict) for q in questions):
        raise ValueError('Synthesis needs 1..5 falsifiable next questions')
    added = []
    for q in questions:
        added.append(add_question_or_variant(lab, q)['id'])
    saved = {'review_task': review['id'], 'subject': subject['id'], 'summary': summary,
             'limitations': limitations, 'outcome_ids': meta['outcomes'],
             'next_questions': added, 'status': 'reviewed advisory synthesis',
             'sha256': routine._sha(files['SYNTHESIS.json'])}
    lab.db.execute('INSERT INTO research_syntheses VALUES(?,?)', (review['id'], canonical(saved)))
    for oid in meta['outcomes']:
        outcome = json.loads(lab.db.execute('SELECT record FROM research_outcomes WHERE id=?', (oid,)).fetchone()[0])
        if outcome.get('study_slug'):
            study = program._study(lab, outcome['study_slug'])
            program._update_study(lab, {**study, 'phase': 'outcome reviewed (advisory)'})


def planning_status(lab, cfg):
    data = state(lab)
    records = [json.loads(r[0]) for r in lab.db.execute('SELECT record FROM program_studies')]
    open_studies = [r for r in records if r['phase'] not in ('outcome reviewed (advisory)', 'shelved')]
    # Continuous mode (Arthur, 2026-11-01): a finished, reviewed "ready to run" study waits in a
    # run queue and no longer blocks planning; a separate cap keeps that queue from piling up.
    ready = [r for r in open_studies if r['phase'] == 'ready to run']
    in_design = len(open_studies) - len(ready)
    capacity = max(0, cfg['research']['max_open_studies'] - in_design)
    if len(ready) >= int(cfg['research'].get('max_ready_studies', 6)):
        capacity = 0
    reviewed = {oid for s in data['syntheses'] for oid in s['outcome_ids']}
    disposed = reviewed | {w['outcome_id'] for w in data['withdrawals']}
    waiting = [o['id'] for o in data['outcomes'] if o['id'] not in disposed]
    active_director = any(json.loads(r['payload']).get('program', {}).get('stage') == 'director'
                          for r in lab.db.execute("SELECT payload FROM tasks WHERE state IN ('queued','running','awaiting_human')"))
    reason = ('Outcome synthesis requires review or owner attention' if waiting else
              'Existing director is unfinished' if active_director else
              'Study capacity reached; waiting for outcomes or owner action' if capacity == 0 else None)
    if capacity == 0 and len(ready) >= int(cfg['research'].get('max_ready_studies', 6)):
        reason = 'Run queue full: ready studies are waiting to be run'
    return {'capacity': capacity, 'blocked_reason': reason,
            'generation': len(data['syntheses']), 'open_studies': len(open_studies)}


def write_report(lab, cfg):
    data = state(lab)
    status = planning_status(lab, cfg)
    gate = lab.root / 'RESOURCE_GATE.json'
    data['resource_gate'] = json.loads(gate.read_text()) if gate.exists() else {'waiting': None}
    data['planning'] = status
    data['enabled'] = cfg['research']['enabled'] and cfg['program']['enabled']
    write_atomic(lab.root / 'RESEARCH.json', canonical(data) + '\n')
    lines = ['# Research progress', '', 'Scope: model mechanisms only. Psychology is not declared solved.',
             f'Engine enabled: {data["enabled"]}. Experiment execution remains unavailable.',
             f'Questions: {len(data["questions"])}; reported outcomes: {len(data["outcomes"])}; '
             f'reviewed advisory syntheses: {len(data["syntheses"])}.', '',
             status['blocked_reason'] or 'Planning capacity available; director interval and service limits still apply.',
             '', '## Open questions']
    if data['resource_gate'].get('waiting'):
        lines.insert(4, 'Workers are waiting for the resource gate: ' + str(data['resource_gate'].get('reason', 'unknown')) + '. No model calls start while it is blocked.')
    for q in data['questions']:
        lines += [f'- {q["id"]}: {q["question"]}', f'  Falsifier: {q["falsifier"]}']
    waiting = []
    for row in lab.db.execute("SELECT * FROM tasks WHERE state IN ('queued','running','awaiting_human','failed','blocked','rejected') ORDER BY id"):
        task = dict(row)
        payload = json.loads(task['payload'])
        if task['kind'] == 'review':
            payload = json.loads(lab.task(payload['subject'])['payload'])
        if payload.get('program', {}).get('stage') == 'synthesis':
            waiting.append(f'- Task {task["id"]}: {task["state"]} ({task["kind"]})')
    if waiting:
        lines += ['', '## Outcome review tasks', *waiting]
    if data['withdrawals']:
        lines += ['', '## Owner-withdrawn outcomes']
        lines += [f'- {w["outcome_id"]}: {w["reason"]}' for w in data['withdrawals']]
    lines += ['', 'Outcome records retain their provenance: imported reports are unverified; audited synthetic fixtures validate software only.',
              'No model result or review establishes a human mechanism, treatment, or cure.',
              'Authority: program/CHARTER.md; ' + program.POLICY + '.']
    write_atomic(lab.root / 'RESEARCH.md', '\n'.join(lines) + '\n')
    return data
