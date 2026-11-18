"""Bounded autonomous SYNTHETIC computation pilot, with no AI workers.

This proves execution, independent numerical audit, automatic collection, and
adaptive follow-up mechanics. It does not test a psychological hypothesis.
Use a dedicated lab: it must never drain the live research worker queue.
"""
import json
from .core import canonical, digest, protocol_hash, write_atomic
from . import dynamics, research, program, routine
from .runner import verify_artifacts
from .workers import handle

PREFIX = 'synthetic-feedback-pilot-'
QUESTION = {'id': 'synthetic-feedback-software-check',
            'question': 'Does the fixture implementation recover its built-in growth threshold?',
            'falsifier': 'Raw trajectories disagree with the closed form or the null fails to decay.'}
MAX_ROUNDS = 3


def _ensure_round(lab, number, gains):
    eid = PREFIX + str(number)
    protocol = {'synthetic': True, 'runner': dynamics.RUNNER, 'phase': 'exploratory',
                'gains': gains, 'damping': 0.5, 'steps': 24,
                'description': dynamics.LIMITATION,
                'decision_rule': 'Growth means terminal value > initial value + 1e-12; reset null must decay',
                'stopping_rule': 'At most three rounds, or stop when no growth bracket exists',
                'adaptation': 'Next round evaluates the two bracket endpoints and their midpoint'}
    existing = lab.db.execute('SELECT protocol_hash FROM experiments WHERE id=?', (eid,)).fetchone()
    if existing:
        if existing[0] != protocol_hash(protocol):
            raise ValueError('Existing pilot protocol differs; original preserved')
        return eid
    with lab.transaction():
        lab.register(eid, protocol)
        plan = lab.enqueue('plan', 'coding-worker', {'experiment': eid})
        execute = lab.enqueue('execute', 'coordinator', {'experiment': eid}, [plan])
        audit = lab.enqueue('audit', 'independent-reviewer', {'experiment': eid}, [plan, execute])
        lab.enqueue('report', 'coordinator', {'experiment': eid}, [audit])
    return eid


def _drain_round(lab):
    # Dedicated deterministic task contexts and coordination identities only.
    for role, owner in [('coding-worker', 'synthetic-pilot-author'),
                        ('coordinator', 'synthetic-pilot-executor'),
                        ('independent-reviewer', 'synthetic-pilot-auditor'),
                        ('coordinator', 'synthetic-pilot-reporter')]:
        task = lab.claim(role, owner)
        if task is None:
            continue
        try:
            result = handle(lab, lab.context(task), owner)
        except Exception as exc:
            lab.finish(task['id'], owner, error=f'{type(exc).__name__}: {exc}')
            return False
        lab.finish(task['id'], owner, result=result)
    return True


def _collect(lab, eid, analysis):
    exp = lab.experiment(eid)
    manifest = verify_artifacts(lab, exp)
    audits = [dict(r) for r in lab.db.execute("SELECT * FROM tasks WHERE kind='audit' AND state='succeeded'")
              if json.loads(r['payload']).get('experiment') == eid]
    if len(audits) != 1 or not json.loads(audits[0]['result']).get('audit_passed'):
        raise ValueError('Collection requires an independent successful artifact audit')
    audit = audits[0]
    if audit['owner'] == exp['owner']:
        raise ValueError('Artifact auditor must be independent of execution owner')
    report = {'question_id': QUESTION['id'], 'scope': 'synthetic',
              'result': 'negative' if analysis['negative_result'] else 'positive',
              'summary': 'Synthetic software check: ' + canonical(analysis),
              'methods': 'Bounded scalar recurrence and reset null; separate closed-form artifact audit',
              'limitations': dynamics.LIMITATION, 'experiment_id': eid,
              'raw_sha256': manifest['raw_hash'], 'protocol_sha256': exp['protocol_hash'],
              'audit_task': audit['id']}
    data = (canonical(report) + '\n').encode()
    oid = digest(data)
    path = lab.root / 'research' / 'outcomes' / (oid + '.json')
    program._save_once(path, data, lab.root)
    record = {**report, 'id': oid, 'snapshot': str(path.relative_to(lab.root)), 'sha256': oid,
              'source': 'automatic synthetic-runner collection',
              'evidence_status': 'artifact-verified synthetic software fixture; no psychological evidence'}
    key = 'synthetic-pilot-outcome:' + eid
    with lab.transaction():
        if not program._done(lab, key):
            lab.db.execute('INSERT INTO research_outcomes VALUES(?,?)', (oid, canonical(record)))
            lab.add_claim({'claim': 'Synthetic feedback fixture passed its independent closed-form artifact audit',
                'scope': dynamics.LIMITATION, 'status': 'verified synthetic software behavior only',
                'sources': [{'reference': str(lab.root / 'runs' / eid / 'raw.jsonl'),
                             'locator': 'sha256:' + manifest['raw_hash']},
                            {'reference': 'task:' + str(audit['id']), 'locator': 'audit_passed=true'}],
                'competing_explanations': ['Threshold is constructed by the equation, not discovered in psychology'],
                'predictions': ['Future fixture runs must satisfy the registered closed form'],
                'evidence': ['Numerical trajectories checked independently against closed form'],
                'falsifiers': ['Any trajectory mismatch or nondecaying reset null']})
            program._record(lab, key, {'experiment': eid, 'outcome': oid, 'synthetic': True})
    return record


def run(lab):
    """Execute up to three fixed-budget rounds without models, subprocesses or network."""
    summary = {'label': 'SYNTHETIC AUTONOMOUS COMPUTATION PILOT — not a psychological experiment',
               'worker_type': 'deterministic role scaffolds, not live AI agents',
               'solved': False, 'rounds': [], 'max_rounds': MAX_ROUNDS,
               'limitation': dynamics.LIMITATION, 'status': 'running'}
    with lab.lock('cycle', 'synthetic-pilot-controller'):
        # Refuse mixed labs before recovery can alter any unrelated task.
        if any(not r[0].startswith(PREFIX) for r in lab.db.execute('SELECT id FROM experiments')):
            raise ValueError('Use a dedicated pilot lab; unrelated experiments found')
        for row in lab.db.execute('SELECT payload,service FROM tasks'):
            if row['service'] != 'deterministic' or not json.loads(row['payload']).get('experiment', '').startswith(PREFIX):
                raise ValueError('Use a dedicated pilot lab; unrelated tasks found')
        lab._recover_tasks()
        research.add_question(lab, QUESTION)
        gains = [0.25, 0.75]
        for number in range(1, MAX_ROUNDS + 1):
            eid = _ensure_round(lab, number, gains)
            try:
                if not _drain_round(lab):
                    raise ValueError('Pilot task failed; inspect preserved artifacts and task status')
                exp = lab.experiment(eid)
                if exp['status'] != 'completed':
                    raise ValueError('Pilot execution incomplete; automatic relaunch is prohibited')
                verify_artifacts(lab, exp)
                protocol = json.loads(exp['protocol'])
                rows = [json.loads(line) for line in (lab.root / 'runs' / eid / 'raw.jsonl').read_text().splitlines()]
                analysis = dynamics.analyze(protocol, rows)
                if not analysis['null_decays']:
                    raise ValueError('Synthetic null validation failed')
                outcome = _collect(lab, eid, analysis)
            except (ValueError, OSError, KeyError) as exc:
                summary.update({'status': 'blocked', 'blocked_experiment': eid, 'reason': str(exc)})
                break
            summary['rounds'].append({'experiment': eid, 'outcome_id': outcome['id'],
                'execution_count': exp['execution_count'], 'growth_bracket': analysis['growth_bracket']})
            if analysis['growth_bracket'] is None:
                summary['status'] = 'stopped: no bracket'
                break
            low, high = analysis['growth_bracket']
            gains = [low, (low + high) / 2, high]
        else:
            summary['status'] = 'completed: bounded synthetic pilot only'
        lab.reports()
        research.write_report(lab, routine.load_config(lab))
        write_atomic(lab.root / 'PILOT.json', canonical(summary) + '\n')
        lines = ['# Synthetic autonomous computation pilot', '', summary['label'], '',
                 f'Status: {summary["status"]}', '', dynamics.LIMITATION, '',
                 'Execution, artifact audit, result collection and bracket refinement ran without AI workers.',
                 'This does not validate a psychological theory or make the live research engine operational.', '']
        for item in summary['rounds']:
            lines.append(f'- {item["experiment"]}: growth bracket {item["growth_bracket"]}; execution count {item["execution_count"]}')
        if summary.get('reason'):
            lines += ['', 'Blocked: ' + summary['reason']]
        write_atomic(lab.root / 'PILOT.md', '\n'.join(lines) + '\n')
    return summary
