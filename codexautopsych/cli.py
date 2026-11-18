import argparse
import json
from pathlib import Path
from .core import Lab, ROLES, canonical
from .runner import recover


def demo(root):
    lab = Lab(root)
    eid = 'synthetic-demo-v1'
    # Stable ID makes repeated demonstrations resume/reconcile instead of launch again.
    existing = lab.db.execute('SELECT 1 FROM experiments WHERE id=?', (eid,)).fetchone()
    if not existing:
        protocol = {'synthetic': True, 'runner': 'synthetic-offset-v1', 'phase': 'exploratory',
                    'values': [-2, 0, 2, 4], 'offset': 0,
                    'description': 'SYNTHETIC negative-result arithmetic fixture. Not Study 3.'}
        lab.register(eid, protocol)
        plan = lab.enqueue('plan', 'coding-worker', {'experiment': eid})
        run = lab.enqueue('execute', 'coordinator', {'experiment': eid}, [plan])
        audit = lab.enqueue('audit', 'independent-reviewer', {'experiment': eid}, [plan, run])
        lab.enqueue('report', 'coordinator', {'experiment': eid}, [audit])
        lab.add_claim({'claim': 'The synthetic zero-offset runner preserves input values.',
            'scope': 'Synthetic exploratory software fixture only; no affect or cognition inference',
            'sources': [{'reference': 'registered-protocol:synthetic-demo-v1', 'locator': 'offset=0; values'},
                        {'reference': 'runs/synthetic-demo-v1/raw.jsonl', 'locator': 'rows 0..3; verify audit before use'}],
            'competing_explanations': ['Arithmetic identity, not a persistent affect state', 'Possible runner bug'],
            'predictions': ['Every output equals its input'],
            'evidence': ['Pending artifact verification; use audit result for verified evidence'],
            'falsifiers': ['Any output differs from its registered input']})
    lab.cycle('coding-worker', 'synthetic-coder', 4)
    lab.cycle('coordinator', 'synthetic-coordinator', 4)
    lab.cycle('independent-reviewer', 'synthetic-reviewer', 4)
    lab.cycle('coordinator', 'synthetic-coordinator', 4)
    status = lab.reports()
    if any(t['state'] != 'succeeded' for t in status['tasks']):
        raise RuntimeError('Demo has failed or incomplete tasks; inspect status')
    if lab.db.execute('SELECT COUNT(*) FROM claims').fetchone()[0] == 1:
        exp = lab.experiment(eid)
        lab.add_claim({'claim': 'The synthetic zero-offset result passed independent artifact audit.',
            'scope': 'Verified synthetic software behavior only; not psychological evidence or replication',
            'sources': [{'reference': 'runs/synthetic-demo-v1/raw.jsonl', 'locator': 'SHA256 ' + exp['raw_hash']},
                        {'reference': 'task:3', 'locator': 'audit_passed=true; independent synthetic reviewer'}],
            'competing_explanations': ['Arithmetic identity', 'Shared implementation assumptions despite artifact review'],
            'predictions': ['All four outputs equal inputs; restart keeps execution_count=1'],
            'evidence': ['Four raw rows independently checked against registered protocol; audit passed'],
            'falsifiers': ['A mismatched raw row, invalid hash, or duplicate launch']})
    before = lab.experiment(eid)['execution_count']
    lab.close()
    lab = Lab(root)
    with lab.lock('execution', 'synthetic-restart'):
        recover(lab)
    lab.cycle('coordinator', 'synthetic-coordinator', 4)
    after = lab.experiment(eid)['execution_count']
    if before != 1 or after != 1:
        raise RuntimeError('Duplicate execution detected')
    summary = {'label': 'SYNTHETIC ONLY — no live AI agents or real experiment',
               'pipeline': 'enqueue → execute → independent artifact audit → report → restart',
               'tasks_succeeded': len(status['tasks']), 'execution_count_before_restart': before,
               'execution_count_after_restart': after, 'negative_result': True,
               'study_3': 'unconnected; no access or live status verified'}
    lab.reports()
    lab.close()
    return summary


ROUTINE_COMMANDS = ('retry-task', 'enqueue-work', 'run', 'services', 'resume-service', 'config', 'packet', 'decide',
                    'approve-publish', 'decline-publish', 'doctor')


def routine_command(lab, args):
    from . import routine
    from .core import Busy
    from .services import local_now
    if args.command == 'enqueue-work':
        if bool(args.instructions) == bool(args.instructions_file):
            raise ValueError('Give exactly one of --instructions or --instructions-file')
        text = args.instructions or Path(args.instructions_file).read_text()
        return routine.enqueue_work(lab, args.kind, text, title=args.title, inputs=args.input,
                                    depends=args.depends, checkpoint=args.checkpoint,
                                    max_attempts=args.max_attempts)
    if args.command == 'run':
        try:
            return routine.run(lab, args.owner, args.max_tasks, args.max_minutes)
        except Busy:
            return {'skipped': 'another pass is already running; state untouched'}
        except Exception as exc:  # noqa: BLE001 - a crashed pass must be visible, then still fail loudly
            routine.record_pass_error(lab, exc)
            raise
    if args.command == 'services':
        return routine.service_status(lab, routine.load_config(lab), local_now)
    if args.command == 'retry-task':
        routine.retry_task(lab, args.task)
        print(f'Task {args.task} requeued')
        return 0
    if args.command == 'resume-service':
        routine.resume_service(lab, args.service)
        return routine.service_status(lab, routine.load_config(lab), local_now)
    if args.command == 'config':
        if args.key is None:
            return routine.load_config(lab)
        if args.value is None:
            raise ValueError('Give a value to change a setting')
        try:
            value = json.loads(args.value)
        except ValueError:
            value = args.value
        return routine.set_config(lab, args.key, value)
    if args.command == 'packet':
        task = lab.task(args.task)
        if task['kind'] != 'review':
            raise ValueError('Packets are for review tasks')
        path, _ = routine.write_packet(lab, task)
        return {'packet': str(path), 'next': f'Give it to Claude, then: decide {args.task} approve|revise|reject --summary ...'}
    if args.command == 'decide':
        return routine.decide(lab, args.task, args.decision, args.summary, args.change)
    if args.command == 'approve-publish':
        return routine.owner_publish(lab, args.task, True, args.by)
    if args.command == 'decline-publish':
        return routine.owner_publish(lab, args.task, False, 'owner')
    if args.command == 'doctor':
        return routine.doctor(lab)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Persistent research coordinator: Codex routine work, Claude checkpoints; never launches Study 3')
    parser.add_argument('--lab', default='.lab', help='Local state directory (not a network share)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    sub.add_parser('status')
    sub.add_parser('report')
    sub.add_parser('demo')
    sub.add_parser('computational-pilot', help='Run bounded synthetic feedback computation, audit and adaptive follow-up; no AI models')
    sub.add_parser('recover', help='Reconcile interrupted artifacts without relaunch')
    enqueue = sub.add_parser('enqueue')
    enqueue.add_argument('kind', choices=['plan', 'execute', 'audit', 'report'])
    enqueue.add_argument('--role', required=True, choices=ROLES)
    enqueue.add_argument('--payload', required=True, help='JSON object')
    enqueue.add_argument('--depends', type=int, nargs='*', default=[])
    enqueue.add_argument('--max-attempts', type=int, default=2)
    cycle = sub.add_parser('cycle', help='Deterministic role scaffold, NOT a live AI agent')
    cycle.add_argument('--role', required=True, choices=ROLES)
    cycle.add_argument('--owner', required=True)
    cycle.add_argument('--limit', type=int, default=1)
    register = sub.add_parser('register')
    register.add_argument('id')
    register.add_argument('--protocol', required=True)
    claim = sub.add_parser('claim')
    claim.add_argument('--file', required=True)
    work = sub.add_parser('enqueue-work', help='Queue routine work for Codex, optionally with checkpoints')
    work.add_argument('kind', choices=['draft', 'code', 'analysis', 'summary', 'routine'])
    work.add_argument('--instructions', help='Task text')
    work.add_argument('--instructions-file', help='Read task text from a file')
    work.add_argument('--title')
    work.add_argument('--input', action='append', default=[], help='File given to Codex read-only (repeatable)')
    work.add_argument('--depends', type=int, nargs='*', default=[])
    work.add_argument('--checkpoint', choices=['none', 'review', 'publish'], default='none',
                      help='review: Claude reviews the result; publish: Claude review + your approval')
    work.add_argument('--max-attempts', type=int, default=3)
    runp = sub.add_parser('run', help='One bounded pass over routine work and checkpoints')
    runp.add_argument('--owner', default='scheduler')
    runp.add_argument('--max-tasks', type=int, default=5)
    runp.add_argument('--max-minutes', type=int, default=45)
    sub.add_parser('services', help='Show Codex/Claude availability, pauses and daily usage')
    sub.add_parser('retry-task', help='Owner: requeue a failed task, or re-run a review waiting on you').add_argument('task', type=int)
    resume = sub.add_parser('resume-service', help='Clear a pause early (e.g. after a limit reset)')
    resume.add_argument('service', choices=['codex', 'claude'])
    cfg = sub.add_parser('config', help='Show or change settings')
    cfg.add_argument('key', nargs='?')
    cfg.add_argument('value', nargs='?', help='JSON value, e.g. true, "cli", 10')
    packet = sub.add_parser('packet', help='Write the Claude review packet for a review task')
    packet.add_argument('task', type=int)
    dec = sub.add_parser('decide', help="Record a review checkpoint decision (e.g. Claude's answer in the app)")
    dec.add_argument('task', type=int)
    dec.add_argument('decision', choices=['approve', 'revise', 'reject'])
    dec.add_argument('--summary', required=True)
    dec.add_argument('--change', action='append', default=[], help='Required change (repeatable, for revise)')
    ap = sub.add_parser('approve-publish', help='Record your approval of a reviewed publication package')
    ap.add_argument('task', type=int)
    ap.add_argument('--by', required=True)
    dp = sub.add_parser('decline-publish')
    dp.add_argument('task', type=int)
    sub.add_parser('arxiv-fetch', help='Fetch bounded arXiv literature; no model calls')
    sub.add_parser('arxiv-status', help='Show arXiv intake status')
    shelve = sub.add_parser('program-shelve', help='Owner: set a program study aside and cancel its queued work')
    shelve.add_argument('slug')
    shelve.add_argument('--reason', required=True)
    sub.add_parser('zotero-refresh', help='Re-read the Zotero library copy now (read-only)')
    sub.add_parser('research-status', help='Show local questions, reported outcomes and research blockers')
    question = sub.add_parser('research-question', help='Add a falsifiable model-mechanism question')
    question.add_argument('id')
    question.add_argument('--question', required=True)
    question.add_argument('--falsifier', required=True)
    outcome = sub.add_parser('research-outcome', help='Import an owner-placed outcome JSON under program/sources')
    outcome.add_argument('--file', required=True)
    withdrawal = sub.add_parser('research-withdraw', help='Record owner withdrawal of an outcome without deleting it')
    withdrawal.add_argument('id')
    withdrawal.add_argument('--reason', required=True)
    for name, hlp in (('live-approve', 'Arthur: approve a Claude-designed, reviewed study (records frozen hashes)'),
                      ('live-start', 'Start an approved study in the background (resumes if interrupted)'),
                      ('live-run', 'Run an approved study in the foreground (used by live-start)'),
                      ('live-stop', 'Ask a running study to stop after its current cell'),
                      ('live-status', 'Show a live study\'s progress')):
        sub.add_parser(name, help=hlp).add_argument('study')
    sub.add_parser('doctor', help='Check Python, Codex/Claude installs and subscription logins (no model calls)')
    sub.add_parser('fast-status', help='Fast lane: what it is doing and what waits for you')
    sub.add_parser('fast-step', help='Fast lane: run one step now (normally the scheduler does this)')
    fa = sub.add_parser('fast-approve', help='Fast lane (trial period): release a finished report')
    fa.add_argument('id')
    fa.add_argument('--by', default='Arthur')
    fr = sub.add_parser('fast-reject', help='Fast lane (trial period): do not release a finished report')
    fr.add_argument('id')
    fr.add_argument('--reason', required=True)
    fr.add_argument('--by', default='Arthur')
    fc = sub.add_parser('fast-config', help='Fast lane: show or change a switch')
    fc.add_argument('key', nargs='?')
    fc.add_argument('value', nargs='?', help='JSON value, e.g. true, false, 2')
    adapter = sub.add_parser('codex-task', help='Optional advisory model call; disabled unless explicitly enabled')
    adapter.add_argument('--context', required=True, help='Role-scoped JSON file')
    adapter.add_argument('--workspace', required=True)
    adapter.add_argument('--enable-chatgpt-codex', action='store_true')
    adapter.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args(argv)
    lab = None
    try:
        if args.command.startswith('live-'):
            from . import live
            if args.command == 'live-approve':
                print(json.dumps(live.approve(args.study), indent=2)); return 0
            if args.command == 'live-stop':
                live.request_stop(args.study); print('Stop requested; it stops after the current cell.'); return 0
            if args.command == 'live-status':
                print((Path(args.study) / 'RUN_STATUS.md').read_text() if (Path(args.study) / 'RUN_STATUS.md').exists() else 'Not started'); return 0
            if args.command == 'live-start':
                print('Started in background, process', live.start_background(args.lab, args.study)); return 0
            lab = Lab(args.lab)
            try:
                from . import program, routine
                state = live.Run(args.study, lab).execute(
                    resource_check=lambda: program.resource_status(routine.load_config(lab)))
                print(json.dumps(state, indent=2)); return 0
            finally:
                lab.close()
        if args.command == 'demo':
            output = demo(args.lab)
        elif args.command == 'codex-task':
            from .adapter import codex_task
            output = codex_task(json.loads(Path(args.context).read_text()), args.workspace,
                                enabled=args.enable_chatgpt_codex, timeout=args.timeout)
        else:
            lab = Lab(args.lab)
            if args.command == 'init':
                output = {'initialized': str(lab.root), 'study_3': 'complete; results readable as program sources'}
            elif args.command == 'computational-pilot':
                from .pilot import run as run_pilot
                output = run_pilot(lab)
            elif args.command == 'status':
                output = lab.status()
            elif args.command == 'report':
                output = lab.reports()
            elif args.command == 'enqueue':
                output = {'task_id': lab.enqueue(args.kind, args.role, json.loads(args.payload), args.depends, args.max_attempts)}
            elif args.command == 'cycle':
                output = lab.cycle(args.role, args.owner, args.limit)
                lab.reports()
            elif args.command == 'register':
                lab.register(args.id, json.loads(Path(args.protocol).read_text()))
                output = lab.experiment(args.id)
            elif args.command == 'claim':
                output = {'claim_id': lab.add_claim(json.loads(Path(args.file).read_text()))}
            elif args.command in ('arxiv-fetch', 'arxiv-status'):
                from . import arxiv, routine
                from .services import local_now
                with lab.lock('cycle', 'arxiv-intake'):
                    output = arxiv.poll(lab, routine.load_config(lab), local_now) if args.command == 'arxiv-fetch' else arxiv.status(lab)
            elif args.command == 'program-shelve':
                from . import program
                output = {'shelved': args.slug, 'cancelled_tasks': program.shelve(lab, args.slug, args.reason)}
            elif args.command == 'zotero-refresh':
                from . import zotero, routine
                with lab.lock('cycle', 'zotero-intake'):
                    output = zotero.refresh(lab, routine.load_config(lab))
            elif args.command.startswith('research-'):
                from . import research, routine
                with lab.lock('cycle', 'research-owner-intake'):
                    if args.command == 'research-question':
                        research.add_question(lab, {'id': args.id, 'question': args.question, 'falsifier': args.falsifier})
                    elif args.command == 'research-withdraw':
                        research.withdraw_outcome(lab, args.id, args.reason)
                    elif args.command == 'research-outcome':
                        research.import_outcome(lab, args.file)
                    output = research.write_report(lab, routine.load_config(lab))
            elif args.command.startswith('fast-'):
                from .fast import engine as fast
                from . import routine
                if args.command == 'fast-step':
                    output = fast.step(lab, routine.load_config(lab))
                elif args.command == 'fast-status':
                    output = {'status': fast.status_lines(lab)}
                elif args.command == 'fast-approve':
                    output = fast.decide(args.id, 'approve', args.by)
                elif args.command == 'fast-reject':
                    output = fast.decide(args.id, 'reject', args.by, args.reason)
                elif args.key is None:
                    output = fast.load_config()
                elif args.value is None:
                    raise ValueError('Give a value to change a setting')
                else:
                    output = fast.set_config(args.key, json.loads(args.value))
            elif args.command in ROUTINE_COMMANDS:
                output = routine_command(lab, args)
            elif args.command == 'recover':
                with lab.lock('cycle', 'manual-recovery'):
                    lab._recover_tasks()
                    with lab.lock('execution', 'manual-recovery'):
                        recover(lab)
                output = lab.reports()
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')
    finally:
        if lab:
            lab.close()

if __name__ == '__main__':
    main()
