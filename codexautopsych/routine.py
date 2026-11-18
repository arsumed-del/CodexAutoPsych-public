"""Routine work without Claude; Claude only at selected checkpoints.

Flow for one piece of work::

    work (Codex) --> [review (Claude)] --> [publish approval (owner)]

- ``work`` tasks run through Codex on the owner's ChatGPT subscription.
- A ``review`` checkpoint is added only when requested. Claude reviews in
  ``cli`` mode (Claude Code on the owner's subscription) or, in ``manual`` mode,
  the coordinator writes a review packet the owner can give to Claude in the app.
- A ``publish`` checkpoint adds a Claude review and then a human approval.
  Nothing is ever posted, sent or uploaded by this software.
- A usage limit on either service returns the task to the queue without using
  an attempt, pauses only that service, and records when to resume. No paid
  fallback exists. Daily call caps add a self-imposed ceiling.

All state lives in the lab's SQLite database and files under the lab directory.
"""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from .core import canonical, now, write_atomic, BLOCKING_STATES
from . import services as svc

ROUTINE_KINDS = ('draft', 'code', 'analysis', 'summary', 'routine', 'revise')
CHECKPOINTS = ('none', 'review', 'publish')
MODEL_SERVICES = ('codex', 'claude')
WORKER_LABEL = 'model-backed advisory output via subscription CLI; not verified scientific evidence'

DEFAULT_CONFIG = {
    'codex': {'enabled': False, 'binary': None, 'model': None, 'timeout_seconds': 1200,
              'max_calls_per_day': 25, 'sandbox': 'workspace-write'},
    'claude': {'mode': 'manual', 'binary': None, 'model': None, 'timeout_seconds': 600,
               'max_calls_per_day': 6},
    'max_revision_rounds': 2,
    'continuous': False,
    'default_backoff_minutes': 60,
    'notify': True,
    'stall_hours': 2,
    'protected_paths': [],
    'arxiv': {'enabled': False, 'query': '(all:"activation steering" OR all:"emotion representations" OR all:"affect regulation") AND (all:"language model" OR all:transformer)', 'max_results': 3,
              'queries': [], 'seed_ids': []},
    'zotero': {'enabled': False, 'data_dir': '~/Zotero', 'max_items': 25},
    'fulltext': {'enabled': False, 'unpaywall_email': '', 'max_fetches_per_pass': 3, 'retry_days': 30},
    'status_page': False,
    'research': {'enabled': False, 'max_open_studies': 5, 'max_ready_studies': 6, 'hosted_planning_during_local_work': False},
    'program': {'enabled': False, 'director_interval_days': 7, 'max_proposals': 5,
                'auto_approve_in_silico': True, 'execute_experiments': False, 'blind_share': 0.3,
                'study3_process_patterns': ['affect-suppression-steering', 'study3']},
}

PROTECTED_NAME = re.compile(r'study[\s_-]*(?:3|iii)(?![0-9a-z])|affect-suppression-steering|affect[\s_-]*under[\s_-]*constraint', re.IGNORECASE)
MAX_INPUT_BYTES = 20 * 1024 * 1024
PACKET_TEXT_LIMIT = 200_000


class AwaitHuman(Exception):
    """The task now waits for the owner; ``packet`` says what to look at."""
    def __init__(self, message, packet):
        super().__init__(message)
        self.packet = str(packet)


# ---------------------------------------------------------------- configuration

def _merge(base, extra):
    out = deepcopy(base)
    for key, value in extra.items():
        out[key] = _merge(base[key], value) if isinstance(base.get(key), dict) and isinstance(value, dict) else value
    return out


def load_config(lab):
    path = lab.root / 'config.json'
    cfg = _merge(DEFAULT_CONFIG, json.loads(path.read_text())) if path.exists() else _merge(DEFAULT_CONFIG, {})
    cfg['lab_root'] = str(lab.root)  # runtime context for the read-only execution-lock probe
    return cfg


def set_config(lab, key, value):
    cfg = load_config(lab)
    parts = key.split('.')
    node, default = cfg, DEFAULT_CONFIG
    for part in parts[:-1]:
        if part not in default or not isinstance(default[part], dict):
            raise ValueError(f'Unknown setting {key}')
        node, default = node[part], default[part]
    if parts[-1] not in default:
        raise ValueError(f'Unknown setting {key}')
    if key == 'claude.mode' and value not in ('manual', 'cli', 'codex'):
        raise ValueError('claude.mode must be manual, cli or codex')
    if key == 'codex.sandbox' and value not in ('read-only', 'workspace-write'):
        raise ValueError('codex.sandbox must be read-only or workspace-write')
    if key.endswith('max_calls_per_day') and not (isinstance(value, int) and 0 <= value <= 500):
        raise ValueError('Daily caps must be integers 0..500')
    if key in ('research.enabled', 'research.hosted_planning_during_local_work', 'continuous') and type(value) is not bool:
        raise ValueError(key + ' must be boolean')
    if key == 'research.max_ready_studies' and (type(value) is not int or not 1 <= value <= 100):
        raise ValueError('research.max_ready_studies must be an integer 1..100')
    if key == 'research.max_open_studies' and (type(value) is not int or not 1 <= value <= 100):
        raise ValueError('research.max_open_studies must be an integer 1..100')
    if key.startswith('zotero.'):
        from .zotero import validate_setting as zotero_setting
        zotero_setting(parts[-1], value)
    if key.startswith('fulltext.'):
        from .fulltext import validate_setting as fulltext_setting
        fulltext_setting(parts[-1], value)
    if key == 'status_page' and type(value) is not bool:
        raise ValueError('status_page must be boolean')
    if key.startswith('arxiv.'):
        from .arxiv import validate_setting
        validate_setting(parts[-1], value)
    if key.startswith('program.'):
        from .program import validate_setting
        validate_setting(parts[-1], value)
    node[parts[-1]] = value
    write_atomic(lab.root / 'config.json', json.dumps({k: v for k, v in cfg.items() if k != 'lab_root'}, indent=2, sort_keys=True) + '\n')
    with lab.transaction():
        lab.event('config', {'key': key, 'value': value})
    return cfg


# ---------------------------------------------------------------- enqueueing

def guard_input(cfg, path):
    # Study 3 is complete (owner amendment 2026-10-12): its files may be read as
    # inputs; running, restarting or modifying it stays refused in enqueue_work.
    p = Path(path).expanduser().absolute().resolve(strict=True)
    for protected in cfg['protected_paths']:
        q = Path(protected).expanduser().resolve()
        if p == q or q in p.parents:
            raise ValueError(f'{p} is inside protected path {q}')
    if not p.is_file():
        raise ValueError(f'{p} must be a single file')
    if p.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f'{p} exceeds the 20 MB input limit')
    return str(p)


def _insert(lab, kind, role, service, payload, deps, max_attempts=3):
    for dep in deps:
        if not lab.db.execute('SELECT 1 FROM tasks WHERE id=?', (dep,)).fetchone():
            raise ValueError(f'Unknown dependency {dep}')
    tid = lab.db.execute(
        'INSERT INTO tasks(kind,role,payload,max_attempts,created,updated,service) VALUES(?,?,?,?,?,?,?)',
        (kind, role, canonical(payload), max_attempts, now(), now(), service)).lastrowid
    lab.db.executemany('INSERT INTO dependencies VALUES(?,?)', [(tid, d) for d in dict.fromkeys(deps)])
    lab.event('enqueue', {'task': tid, 'kind': kind, 'service': service, 'dependencies': list(deps)})
    return tid


def enqueue_work(lab, kind, instructions, *, title=None, inputs=(), depends=(), checkpoint='none',
                 max_attempts=3):
    if kind not in ROUTINE_KINDS:
        raise ValueError(f'Routine kind must be one of {", ".join(ROUTINE_KINDS)}')
    if checkpoint not in CHECKPOINTS:
        raise ValueError('Checkpoint must be none, review, or publish')
    if not instructions or not instructions.strip():
        raise ValueError('Instructions are required')
    if PROTECTED_NAME.search(instructions + ' ' + (title or '')) and re.search(
            r'\b(run|launch|restart|start|execute|rerun|re-run|modify|edit)\b', instructions, re.I):
        raise ValueError('Routine work may not run, restart or modify Study 3')
    if not 1 <= max_attempts <= 10:
        raise ValueError('max_attempts must be 1..10')
    cfg = load_config(lab)
    payload = {'title': title or instructions.strip().splitlines()[0][:80], 'instructions': instructions,
               'inputs': [guard_input(cfg, p) for p in inputs], 'round': 0}
    with lab.transaction():
        ids = {'work': _insert(lab, kind, 'coding-worker', 'codex', payload, list(depends), max_attempts)}
        if checkpoint in ('review', 'publish'):
            ids['review'] = _insert(lab, 'review', 'independent-reviewer', 'claude',
                                    {'subject': ids['work'], 'round': 0, 'title': payload['title']},
                                    [ids['work']], max_attempts)
        if checkpoint == 'publish':
            ids['publish'] = _insert(lab, 'publish', 'coordinator', 'human',
                                     {'subject': ids['work'], 'review': ids['review'], 'title': payload['title']},
                                     [ids['review']], 1)
    return ids


# ---------------------------------------------------------------- services

def _utc(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _today(clock):
    return clock().astimezone().date().isoformat()


def usage(lab, service, clock):
    row = lab.db.execute('SELECT calls FROM service_usage WHERE service=? AND day=?',
                         (service, _today(clock))).fetchone()
    return row[0] if row else 0


def record_call(lab, service, clock):
    with lab.transaction():
        lab.db.execute('INSERT INTO service_usage(service,day,calls) VALUES(?,?,1) '
                       'ON CONFLICT(service,day) DO UPDATE SET calls=calls+1', (service, _today(clock)))


def pause_service(lab, service, until, reason):
    lab.db.execute('INSERT INTO services(name,paused_until,reason,updated) VALUES(?,?,?,?) '
                   'ON CONFLICT(name) DO UPDATE SET paused_until=excluded.paused_until, '
                   'reason=excluded.reason, updated=excluded.updated', (service, _utc(until), reason, now()))
    lab.event('service-paused', {'service': service, 'until': _utc(until), 'reason': reason})


def resume_service(lab, service):
    with lab.transaction():
        lab.db.execute('UPDATE services SET paused_until=NULL, reason=NULL, updated=? WHERE name=?',
                       (now(), service))
        lab.db.execute("UPDATE tasks SET not_before=NULL, updated=? WHERE service=? AND state='queued'",
                       (now(), service))
        lab.event('service-resumed', {'service': service, 'by': 'owner'})


def service_status(lab, cfg, clock):
    out = {}
    current = _utc(clock())
    for name in MODEL_SERVICES:
        row = lab.db.execute('SELECT * FROM services WHERE name=?', (name,)).fetchone()
        paused = bool(row and row['paused_until'] and row['paused_until'] > current)
        if name == 'codex':
            mode = 'enabled' if cfg['codex']['enabled'] else 'disabled (routine work waits)'
        else:
            mode = cfg['claude']['mode']
        out[name] = {'mode': mode, 'paused': paused,
                     'paused_until': row['paused_until'] if paused else None,
                     'reason': row['reason'] if paused else None,
                     'calls_today': usage(lab, name, clock),
                     'daily_cap': cfg[name]['max_calls_per_day']}
    return out


def available_services(lab, cfg, clock):
    """Services whose tasks may be claimed now. Hitting a daily cap pauses until local midnight."""
    status = service_status(lab, cfg, clock)
    ready = ['human']
    for name in MODEL_SERVICES:
        st = status[name]
        if name == 'codex' and not cfg['codex']['enabled']:
            continue
        if name == 'claude' and cfg['claude']['mode'] == 'manual':
            ready.append('claude')  # manual packets make no model call
            continue
        if name == 'claude' and cfg['claude']['mode'] == 'codex':
            # Reviews go to a separate fresh Codex session with their OWN daily budget (claude.max_calls_per_day),
            # so checking work never uses up the work cap. A real Codex usage limit still stops them.
            codex = status['codex']
            codex_cap_pause = codex['paused'] and 'daily call cap' in str(codex['reason'] or '')
            codex_open = cfg['codex']['enabled'] and (not codex['paused'] or codex_cap_pause)
            if codex_open and not st['paused'] and st['calls_today'] < st['daily_cap']:
                ready.append('claude')
            continue
        if st['paused']:
            continue
        if st['calls_today'] >= st['daily_cap']:
            local = clock().astimezone()
            midnight = (local + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
            with lab.transaction():
                pause_service(lab, name, midnight, 'daily call cap reached (self-imposed)')
            continue
        ready.append(name)
    return ready


def claim_work(lab, owner, ready, clock):
    if 'cycle' not in lab._held_locks:
        raise ValueError('Claiming requires the cycle lock')
    marks = ','.join('?' * len(ready))
    blocking = ','.join('?' * len(BLOCKING_STATES))
    with lab.transaction():
        lab.db.execute(f"""UPDATE tasks SET state='blocked',updated=? WHERE state='queued'
            AND EXISTS (SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.dependency
                WHERE d.task=tasks.id AND p.state IN ({blocking}))""", (now(), *BLOCKING_STATES))
        row = lab.db.execute(f"""SELECT t.* FROM tasks t WHERE t.state='queued' AND t.service IN ({marks})
            AND t.attempts<t.max_attempts AND (t.not_before IS NULL OR t.not_before<=?)
            AND NOT EXISTS (SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.dependency
                WHERE d.task=t.id AND p.state!='succeeded') ORDER BY t.id LIMIT 1""",
            (*ready, _utc(clock()))).fetchone()
        if not row:
            return None
        lab.db.execute("UPDATE tasks SET state='running',owner=?,attempts=attempts+1,updated=? WHERE id=?",
                       (owner, now(), row['id']))
        lab.event('claim', {'task': row['id'], 'owner': owner, 'service': row['service']})
    return lab.task(row['id'])


def settle(lab, tid, state, result=None, error=None):
    lab.db.execute('UPDATE tasks SET state=?,owner=NULL,result=?,error=?,updated=? WHERE id=?',
                   (state, canonical(result) if result is not None else None, error, now(), tid))
    lab.event('settle', {'task': tid, 'state': state})


# ---------------------------------------------------------------- workspaces and packets

def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _manifest(folder):
    folder = Path(folder)
    return [{'path': str(p.relative_to(folder)), 'sha256': _sha(p), 'bytes': p.stat().st_size}
            for p in sorted(folder.rglob('*')) if p.is_file()]


def task_dir(lab, task):
    return lab.root / 'work' / f'task-{task["id"]}' / f'attempt-{task["attempts"]}'


def _deps(lab, tid):
    return [dict(r) for r in lab.db.execute(
        'SELECT p.* FROM dependencies d JOIN tasks p ON p.id=d.dependency WHERE d.task=? ORDER BY p.id', (tid,))]


def prepare_workspace(lab, task):
    payload = json.loads(task['payload'])
    base = task_dir(lab, task)
    ws = base / 'workspace'
    for sub in ('inputs', 'upstream', 'outputs'):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    for src in payload.get('inputs', []):
        src = guard_input(load_config(lab), src)
        dest = ws / 'inputs' / Path(src).name
        shutil.copy2(src, dest)
        dest.chmod(0o444)
    for dep in _deps(lab, task['id']):
        result = json.loads(dep['result'] or '{}')
        target = ws / 'upstream' / f'task-{dep["id"]}'
        target.mkdir(parents=True, exist_ok=True)
        if result.get('final'):
            shutil.copy2(result['final'], target / 'final.md')
        if result.get('outputs_dir') and Path(result['outputs_dir']).exists():
            shutil.copytree(result['outputs_dir'], target / 'outputs', dirs_exist_ok=True)
        if dep['kind'] == 'review':
            (target / 'review.json').write_text(json.dumps(result.get('review', result), indent=2))
    return base, ws, payload


def work_prompt(payload, ws):
    lines = [f'Title: {payload["title"]}', '', payload['instructions']]
    if payload.get('required_changes'):
        lines += ['', f'This is revision round {payload["round"]}. An independent reviewer required:']
        lines += [f'- {c}' for c in payload['required_changes']]
        lines += ['Revise the prior work (under upstream/) to address every item.']
    files = [str(p.relative_to(ws)) for p in sorted(ws.rglob('*')) if p.is_file()]
    if files:
        lines += ['', 'Available files (read-only data):'] + [f'- {f}' for f in files]
    return '\n'.join(lines)


def _packet_skip(rel):
    parts = Path(rel).parts
    return '__pycache__' in parts or rel.endswith('.pyc')


def copy_review_files(lab, review_task, dest):
    """Copy the reviewed work's output files, in full, read-only, into the reviewer's folder."""
    payload = json.loads(review_task['payload'])
    result = json.loads(lab.task(payload['subject'])['result'] or '{}')
    src = Path(result.get('outputs_dir') or '')
    if not src.is_dir():
        return 0
    count = 0
    # The work's outputs, plus the sources its author was given (inputs/, next to outputs/).
    # Links are never followed (2026-10-13), so nothing outside the lab can be pulled in.
    trees = [(src, dest / 'files')]
    if (src.parent / 'inputs').is_dir() and not (src.parent / 'inputs').is_symlink():
        trees.append((src.parent / 'inputs', dest / 'files' / '_author_inputs'))
    for tree, out in trees:
        for f in sorted(tree.rglob('*')):
            rel = f.relative_to(tree)
            if f.is_symlink() or any(p.is_symlink() for p in f.parents if p != tree and tree in p.parents):
                continue
            if not f.is_file() or _packet_skip(str(rel)):
                continue
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            target.chmod(0o444)
            count += 1
    return count


def build_packet(lab, review_task):
    payload = json.loads(review_task['payload'])
    subject = lab.task(payload['subject'])
    sp = json.loads(subject['payload'])
    result = json.loads(subject['result'] or '{}')
    parts = [f'# CodexAutoPsych review checkpoint — task {review_task["id"]}', '',
             f'Work item: task {subject["id"]} ({subject["kind"]}), round {payload["round"]}',
             f'Title: {sp["title"]}', '', '## Original instructions', sp['instructions']]
    if sp.get('required_changes'):
        parts += ['', '## Changes requested in the previous review'] + [f'- {c}' for c in sp['required_changes']]
    final = Path(result['final']).read_text() if result.get('final') else '(no summary)'
    parts += ['', '## Codex summary', final, '', '## Output files']
    budget = PACKET_TEXT_LIMIT
    # Fix 2026-10-13: the old loop went alphabetically and one huge file used up the whole budget,
    # so the proposal, tests and key sources reached the reviewer cut short or empty. Now caches are
    # skipped, the work's own files go in first (smallest first), then copied sources, and every file is ALSO copied in full into the
    # reviewer's folder under files/ (see codex_review), so nothing is hidden by the size limit.
    items = [i for i in result.get('outputs', []) if not _packet_skip(i['path'])]
    items.sort(key=lambda i: ('/' in i['path'], i['bytes'], i['path']))  # the work itself first, then its sources
    if items:
        parts.append('Every file below is also in full, read-only, under files/ in your folder. '
                     'Files too large to show here are marked; read them there.')
    for item in items:
        path = Path(result['outputs_dir']) / item['path']
        parts.append(f'\n### {item["path"]} (sha256 {item["sha256"][:12]}, {item["bytes"]} bytes)')
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            parts.append(f'(binary file; full copy at files/{item["path"]})')
            continue
        if len(text) > budget:
            parts.append(f'(not shown here to save space; full copy at files/{item["path"]})')
        else:
            parts.append(text)
            budget -= len(text)
    if sp.get('program', {}).get('stage') == 'synthesis':
        from .research import review_context
        evidence = review_context(lab, sp['program'])
        if len(evidence) > budget:
            raise ValueError('Outcome review exceeds packet budget; owner must split the evidence round')
        parts += ['', '## Preserved owner-provided outcome reports (not verified evidence)', evidence]
    parts += ['', '## Requested response',
              'Decide approve, revise, or reject. For revise, list concrete required changes.']
    return '\n'.join(parts)


# ---------------------------------------------------------------- task handlers

def handle_work(lab, task, cfg, clock):
    base, ws, payload = prepare_workspace(lab, task)
    final = svc.run_codex(work_prompt(payload, ws), ws, binary=cfg['codex']['binary'],
                          on_launch=lambda: record_call(lab, 'codex', clock),
                          timeout=cfg['codex']['timeout_seconds'], sandbox=cfg['codex']['sandbox'],
                          model=cfg['codex']['model'], now=clock(),
                          default_backoff=cfg['default_backoff_minutes'])
    final_path = base / 'final.md'
    final_path.write_text(final)
    outputs = ws / 'outputs'
    return {'service': 'codex', 'worker_type': WORKER_LABEL, 'final': str(final_path),
            'final_sha256': _sha(final_path), 'outputs_dir': str(outputs), 'outputs': _manifest(outputs)}


def write_packet(lab, task):
    base = task_dir(lab, task)
    base.mkdir(parents=True, exist_ok=True)
    packet = build_packet(lab, task)
    path = base / 'REVIEW_REQUEST.md'
    write_atomic(path, packet)
    return path, packet


def handle_review(lab, task, cfg, clock):
    path, packet = write_packet(lab, task)
    if cfg['claude']['mode'] == 'manual':
        raise AwaitHuman(f'Review checkpoint ready for Claude: task {task["id"]}', path)
    if cfg['claude']['mode'] == 'codex':
        return codex_review(lab, task, packet, cfg, clock)
    return svc.run_claude_review(packet, binary=cfg['claude']['binary'],
                                 on_launch=lambda: record_call(lab, 'claude', clock),
                                 timeout=cfg['claude']['timeout_seconds'], model=cfg['claude']['model'],
                                 now=clock(), default_backoff=cfg['default_backoff_minutes'])


def codex_review(lab, task, packet, cfg, clock):
    """Independent review by a NEW, read-only, ephemeral Codex session (owner decision 2026-10-12).

    It shares no context with the author session: it sees only the review packet, in its own
    empty workspace, and may not write anything. Counted against the review slot's own daily cap
    (claude.max_calls_per_day), not the Codex work cap (2026-10-12)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='autopsych-review-') as tmp:
        ws = Path(tmp)
        (ws / 'REVIEW_REQUEST.md').write_text(packet)
        copy_review_files(lab, task, ws)
        prompt = (svc.REVIEW_RULES + '\nRead REVIEW_REQUEST.md in this folder. You did not write that work. '
                  'Reply with ONLY one JSON object: {"decision": "approve"|"revise"|"reject", "summary": "...", '
                  '"required_changes": ["..."], "concerns": ["..."]}')
        final = svc.run_codex(prompt, ws, binary=cfg['codex']['binary'], sandbox='read-only',
                              on_launch=lambda: record_call(lab, 'claude', clock),  # the review slot's budget
                              timeout=cfg['codex']['timeout_seconds'], model=cfg['codex']['model'],
                              now=clock(), default_backoff=cfg['default_backoff_minutes'])
    match = re.search(r'\{.*\}', final, re.S)
    review = json.loads(match.group(0)) if match else None
    if not isinstance(review, dict) or review.get('decision') not in ('approve', 'revise', 'reject'):
        raise RuntimeError('Codex review returned no valid decision')
    review.setdefault('required_changes', [])
    return review


def retry_task(lab, tid):
    """Owner action: requeue a FAILED task for a fresh set of attempts. History is kept in events.

    Also allowed (2026-10-13): a review checkpoint waiting on the owner, so it can be reviewed again
    after an engine fix (e.g. the review packet was incomplete). This approves nothing."""
    with lab.transaction():
        row = lab.task(tid)
        rereview = row is not None and row['kind'] == 'review' and row['state'] == 'awaiting_human'
        if row is None or not (row['state'] == 'failed' or rereview):
            raise ValueError('Only a failed task, or a review waiting on the owner, can be retried')
        lab.db.execute("UPDATE tasks SET state='queued',owner=NULL,attempts=0,not_before=NULL,updated=? WHERE id=?",
                       (now(), tid))
        lab.event('retry', {'task': tid, 'previous_error': row['error']})


def handle_publish(lab, task):
    payload = json.loads(task['payload'])
    subject = json.loads(lab.task(payload['subject'])['result'] or '{}')
    review = json.loads(lab.task(payload['review'])['result'] or '{}')
    package = lab.root / 'publish-ready' / f'task-{task["id"]}'
    package.mkdir(parents=True, exist_ok=True)
    if subject.get('outputs_dir'):
        shutil.copytree(subject['outputs_dir'], package / 'outputs', dirs_exist_ok=True)
    if subject.get('final'):
        shutil.copy2(subject['final'], package / 'codex-summary.md')
    (package / 'claude-review.json').write_text(json.dumps(review, indent=2))
    (package / 'README.txt').write_text(
        'Reviewed and ready for YOUR decision. CodexAutoPsych never publishes anything itself.\n'
        f'Approve: python3 -m codexautopsych approve-publish {task["id"]} --by <your name>\n'
        f'Decline: python3 -m codexautopsych decline-publish {task["id"]}\n')
    raise AwaitHuman(f'Publication package ready for your approval: task {task["id"]}', package)


def write_review_record(lab, task, review, source, decision):
    """Readable copy of a checkpoint verdict: reviews/task-N.md plus REVIEWS.md (newest first)."""
    payload = json.loads(task['payload'])
    reviews = lab.root / 'reviews'
    reviews.mkdir(parents=True, exist_ok=True)
    lines = [f"# Review of task #{payload.get('subject', '?')}: {payload.get('title', '')}", '',
             f"- Review task: #{task['id']} (round {payload.get('round', 1)})",
             f"- Reviewer: {source}",
             f"- Decision: **{decision}**",
             f"- Recorded: {_utc(svc.local_now())}", '',
             '## Summary', '', str(review.get('summary', '')).strip() or '(none given)', '']
    for heading, key in (('Required changes', 'required_changes'), ('Concerns', 'concerns')):
        items = [str(i).strip() for i in (review.get(key) or []) if str(i).strip()]
        if items:
            lines += [f'## {heading}', ''] + [f'- {i}' for i in items] + ['']
    write_atomic(reviews / f"task-{task['id']}.md", '\n'.join(lines))
    files = sorted(reviews.glob('task-*.md'), key=lambda f: int(f.stem.split('-')[1]), reverse=True)
    index = ['# Reviews (newest first)', '',
             'Readable copies of each independent review verdict. The engine database remains the record.', '']
    for f in files[:20]:
        text = f.read_text().splitlines()
        title = text[0].lstrip('# ') if text else f.stem
        verdict = next((l.split('**')[1] for l in text if l.startswith('- Decision:') and '**' in l), '?')
        index.append(f'- {f.stem}: {verdict} - {title} (reviews/{f.name})')
    write_atomic(lab.root / 'REVIEWS.md', '\n'.join(index) + '\n')


def _auto_shelve(lab, review_task, reason):
    """Mark the program study behind a review as shelved. Call inside a transaction."""
    try:
        subject = lab.task(json.loads(review_task['payload'])['subject'])
        slug = json.loads(subject['payload']).get('program', {}).get('proposal', {}).get('slug')
    except (KeyError, TypeError, ValueError):
        return None
    row = lab.db.execute('SELECT record FROM program_studies WHERE slug=?', (slug,)).fetchone() if slug else None
    if row is None:
        return None
    record = json.loads(row['record'])
    record.update({'phase': 'shelved', 'shelved_reason': reason})
    lab.db.execute('UPDATE program_studies SET record=? WHERE slug=?',
                   (canonical(record), slug))
    lab.event('program', {'action': f'auto-shelve:{slug}', 'reason': reason, 'review': review_task['id']})
    return slug


def apply_review(lab, task, review, source):
    """Record a checkpoint decision. Call inside a transaction."""
    cfg = load_config(lab)
    payload = json.loads(task['payload'])
    decision = review['decision']
    result = {'service': 'claude', 'source': source, 'review': review, 'decision': decision}
    if decision == 'approve':
        settle(lab, task['id'], 'succeeded', result)
    elif decision == 'reject':
        settle(lab, task['id'], 'rejected', result)
    elif payload['round'] >= cfg['max_revision_rounds'] and cfg.get('continuous'):
        # Continuous mode (Arthur, 2026-11-01): never wait on the owner for a design that
        # could not satisfy review; set the study aside (kept, reversible) and keep planning.
        result['note'] = 'Revision limit reached; study set aside automatically (continuous mode)'
        settle(lab, task['id'], 'rejected', result)
        _auto_shelve(lab, task, 'Revision limit reached without reviewer approval (auto, continuous mode)')
    elif payload['round'] >= cfg['max_revision_rounds']:
        result['note'] = 'Revision limit reached; owner decides whether to continue'
        settle(lab, task['id'], 'awaiting_human', result)
    else:
        subject = lab.task(payload['subject'])
        sp = json.loads(subject['payload'])
        new_round = payload['round'] + 1
        work = _insert(lab, 'revise', 'coding-worker', 'codex',
                       {**sp, 'round': new_round, 'revision_of': subject['id'],
                        'required_changes': review.get('required_changes') or [review.get('summary', '')]},
                       [subject['id']], subject['max_attempts'])
        new_review = _insert(lab, 'review', 'independent-reviewer', 'claude',
                             {'subject': work, 'round': new_round, 'title': sp['title']},
                             [work], task['max_attempts'])
        lab.db.execute('UPDATE dependencies SET dependency=? WHERE dependency=? AND task!=?',
                       (new_review, task['id'], new_review))
        lab.db.execute("UPDATE tasks SET payload=json_set(payload,'$.review',?) WHERE kind='publish' "
                       "AND json_extract(payload,'$.review')=?", (new_review, task['id']))
        result.update({'revision_task': work, 'next_review': new_review})
        settle(lab, task['id'], 'revised', result)
    lab.event('checkpoint', {'task': task['id'], 'decision': decision, 'source': source})
    try:
        write_review_record(lab, task, review, source, decision)
    except OSError:
        pass  # the readable copy is a convenience; the database is the record
    return result


def decide(lab, tid, decision, summary, required_changes=(), source='owner relayed review'):
    """Record a review decision made outside the CLI (e.g. pasted to Claude in the app)."""
    review = {'decision': decision, 'summary': summary, 'required_changes': list(required_changes)}
    if decision not in ('approve', 'revise', 'reject'):
        raise ValueError('Decision must be approve, revise or reject')
    with lab.transaction():
        task = lab.task(tid)
        if task['service'] != 'claude' or task['kind'] != 'review':
            raise ValueError('Only review checkpoints take a decision')
        if task['state'] not in ('awaiting_human', 'queued'):
            raise ValueError(f'Task {tid} is {task["state"]}; nothing to decide')
        if task['state'] == 'queued' and any(d['state'] != 'succeeded' for d in _deps(lab, tid)):
            raise ValueError('The work under review has not finished yet')
        return apply_review(lab, task, review, source)


def owner_publish(lab, tid, approve, by):
    with lab.transaction():
        task = lab.task(tid)
        if task['kind'] != 'publish' or task['state'] != 'awaiting_human':
            raise ValueError('Task is not a publication waiting for approval')
        result = json.loads(task['result'] or '{}')
        result.update({'approved': approve, 'by': by, 'note': 'Recorded only; nothing was published by software'})
        settle(lab, tid, 'succeeded' if approve else 'rejected', result)
    return result


# ---------------------------------------------------------------- the run loop

def notify(cfg, title, message):
    if not cfg.get('notify') or sys.platform != 'darwin' or not shutil.which('osascript'):
        return
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    try:
        subprocess.run(['osascript', '-e', script], timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def run(lab, owner='scheduler', max_tasks=5, max_minutes=45, clock=svc.local_now):
    """One bounded pass. Safe to start on a timer: overlapping passes exit Busy."""
    if not 1 <= max_tasks <= 100 or not 1 <= max_minutes <= 600:
        raise ValueError('max_tasks 1..100 and max_minutes 1..600')
    cfg = load_config(lab)
    deadline = clock() + timedelta(minutes=max_minutes)
    log = []
    resource_wait = False
    from . import program
    with lab.lock('cycle', owner):
        lab._pass_id = now()
        lab._recover_tasks()
        from . import research
        from . import arxiv
        arxiv.poll(lab, cfg, clock)
        from . import zotero
        zotero.refresh(lab, cfg, clock)
        research.advance(lab, cfg)
        program.maybe_enqueue_director(lab, cfg, clock)
        program.follow_up(lab, cfg)
        for _ in range(max_tasks):
            if clock() >= deadline:
                break
            if cfg['research']['enabled']:
                if cfg['research']['hosted_planning_during_local_work']:
                    resource = {'free': True, 'reason': 'hosted-planning-authorized; local experiments disabled'}
                else:
                    resource = program.resource_status(cfg)
                resource_wait = not resource['free']
                write_atomic(lab.root / 'RESOURCE_GATE.json', canonical({
                    'waiting': resource_wait, 'checked': _utc(clock()),
                    'reason': resource['reason'], 'scope': 'routine hosted workers only',
                    'local_execution_authorized': False}) + '\n')
                if resource_wait:
                    break
            task = claim_work(lab, owner, available_services(lab, cfg, clock), clock)
            if task is None:
                break
            entry = {'task': task['id'], 'kind': task['kind'], 'service': task['service']}
            try:
                if task['service'] == 'codex':
                    lab.finish(task['id'], owner, result=handle_work(lab, task, cfg, clock))
                elif task['service'] == 'claude':
                    review = handle_review(lab, task, cfg, clock)
                    source = 'codex-independent-review' if cfg['claude']['mode'] == 'codex' else 'claude-code-cli'
                    with lab.transaction():
                        apply_review(lab, lab.task(task['id']), review, source)
                elif task['service'] == 'human':
                    handle_publish(lab, task)
                else:
                    raise ValueError(f'Unknown service {task["service"]}')
            except AwaitHuman as wait:
                with lab.transaction():
                    settle(lab, task['id'], 'awaiting_human', {'packet': wait.packet, 'message': str(wait)})
                notify(cfg, 'CodexAutoPsych needs you', str(wait))
            except (svc.UsageLimit, svc.ServiceRefused) as stop:
                limited = isinstance(stop, svc.UsageLimit)
                until = stop.resume_at if limited else clock() + timedelta(minutes=cfg['default_backoff_minutes'])
                reason = stop.reason if limited else f'refused: {stop}'
                with lab.transaction():
                    # Not the task's fault: give the attempt back and wait for the service.
                    lab.db.execute("UPDATE tasks SET state='queued',owner=NULL,attempts=attempts-1,"
                                   "not_before=?,error=?,updated=? WHERE id=?",
                                   (_utc(until), reason, now(), task['id']))
                    pause_service(lab, task['service'], until, reason)
                notify(cfg, 'CodexAutoPsych paused', f'{task["service"]}: {reason}; resumes after '
                       f'{until.astimezone().strftime("%a %H:%M")}')
            except Exception as exc:  # noqa: BLE001 - recorded, counted against bounded attempts
                lab.finish(task['id'], owner, error=f'{type(exc).__name__}: {exc}')
            entry['state'] = lab.task(task['id'])['state']
            log.append(entry)
            program.follow_up(lab, cfg)
    fast_lane = None
    try:  # fast lane (charter amendment 2026-11-14); it must never break the main pass
        from .fast import engine as fast
        if fast.is_production(lab):
            fast_lane = fast.step(lab, cfg)
    except Exception as exc:  # noqa: BLE001
        fast_lane = {'error': f'{type(exc).__name__}: {exc}'}
    write_views(lab, cfg, clock)
    return {'ran': log, 'services': service_status(lab, cfg, clock), 'resource_wait': resource_wait,
            'fast_lane': fast_lane}


# ---------------------------------------------------------------- stall alarm (2026-10-12)

NOTIFY_EVERY_HOURS = 6


def _age_hours(stamp, clock):
    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return 0.0
    return (clock() - then).total_seconds() / 3600


def stall_lines(lab, cfg, clock):
    """Plain-English reasons the engine is not moving. Empty list means healthy."""
    hours = float(cfg.get('stall_hours') or 2)
    lines = []
    for row in lab.db.execute("SELECT id, kind, updated FROM tasks WHERE state='awaiting_human' ORDER BY id"):
        age = _age_hours(row['updated'], clock)
        if age >= hours:
            lines.append(f'Task #{row["id"]} ({row["kind"]}) has waited for Arthur for {age:.0f} h; '
                         'the engine cannot continue past it.')
    for row in lab.db.execute("SELECT id, kind, updated FROM tasks WHERE state='failed' ORDER BY id"):
        if _age_hours(row['updated'], clock) < 24:
            lines.append(f'Task #{row["id"]} ({row["kind"]}) failed; ask Claude to look at it.')
    queued = lab.db.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running')").fetchone()[0]
    latest = lab.db.execute('SELECT MAX(updated) FROM tasks').fetchone()[0]
    services = service_status(lab, cfg, clock)
    usage_wait = any(st['paused'] for st in services.values())
    if queued and latest and _age_hours(latest, clock) >= hours and not usage_wait:
        lines.append(f'{queued} task(s) queued but nothing has moved for {_age_hours(latest, clock):.0f} h.')
    return lines


def idle_line(lab, cfg, clock):
    """Nothing queued, running or failing, and no task has moved for a day: say so plainly.

    Added 2026-11-01 after the engine sat idle for two weeks (study capacity full, all
    studies waiting on Arthur) while STATUS.md still read "moving".
    """
    busy = lab.db.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running','awaiting_human')").fetchone()[0]
    latest = lab.db.execute('SELECT MAX(updated) FROM tasks').fetchone()[0]
    if busy or not latest:
        return None
    age = _age_hours(latest, clock)
    if age < 24:
        return None
    try:
        reason = (json.loads((lab.root / 'RESEARCH.json').read_text()).get('planning') or {})
        reason = reason.get('blocked_reason') if isinstance(reason, dict) else None
    except (OSError, ValueError):
        reason = None
    since = latest[:10]
    return (f'IDLE since {since} ({age / 24:.0f} days): nothing queued. '
            + (f'{reason}. ' if isinstance(reason, str) and reason else '')
            + 'Studies are ready and waiting on Arthur (approve a live run, deposit, or shelve one to free capacity).')


def _alarm(lab, cfg, clock, lines, title='CodexAutoPsych is stuck'):
    """Notify on the Mac when a problem is new or every NOTIFY_EVERY_HOURS while it lasts."""
    path = lab.root / 'STALL.json'
    try:
        prior = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        prior = {}
    if not lines:
        if prior:
            write_atomic(path, '{}\n')
        return
    due = prior.get('lines') != lines or _age_hours(prior.get('notified'), clock) >= NOTIFY_EVERY_HOURS \
        or not prior.get('notified')
    if due:
        notify(cfg, title, lines[0][:200])
    write_atomic(path, json.dumps({'lines': lines, 'since': prior.get('since') or _utc(clock()),
                                   'notified': _utc(clock()) if due else prior.get('notified')}, indent=2) + '\n')


def record_pass_error(lab, exc, clock=svc.local_now):
    """A whole pass crashed: say so at the top of ATTENTION.md and STATUS.md and notify (rate-limited)."""
    from .program import PROJECT_ROOT
    message = (f'ENGINE PASS FAILING ({type(exc).__name__}) as of {clock().astimezone().strftime("%a %H:%M")}. '
               'Nothing is moving. Ask Claude to check .lab/research/logs/run.err.')
    try:
        cfg = load_config(lab)
    except Exception:  # noqa: BLE001 - the alarm must not depend on a readable config
        cfg = {'notify': True}
    _alarm(lab, cfg, clock, [message], title='CodexAutoPsych pass failed')
    banner = f'> **{message}**\n\n'
    for path in (lab.root / 'ATTENTION.md', PROJECT_ROOT / 'STATUS.md'):
        try:
            text = path.read_text() if path.exists() else ''
            text = re.sub(r'^> \*\*ENGINE PASS FAILING.*\n\n', '', text, flags=re.M)
            head, _, rest = text.partition('\n')
            write_atomic(path, head + '\n\n' + banner + rest.lstrip('\n') if head else banner)
        except OSError:
            pass


def write_views(lab, cfg, clock):
    lab.reports()
    status = service_status(lab, cfg, clock)
    waiting = [dict(r) for r in lab.db.execute("SELECT * FROM tasks WHERE state='awaiting_human' ORDER BY id")]
    lines = ['# Needs your attention', '',
             ('Routine work runs through Codex; reviews run in a separate, fresh read-only session.' if cfg['claude']['mode'] == 'codex' else 'Routine work runs through Codex; Claude is used only at review checkpoints.'), '']
    stalled = stall_lines(lab, cfg, clock)
    _alarm(lab, cfg, clock, stalled)
    idle = None if stalled else idle_line(lab, cfg, clock)
    if stalled:
        lines.append('- **STALLED:** ' + ' '.join(stalled))
    elif idle:
        lines.append('- **Engine health: ' + idle + '**')
    else:
        lines.append('- Engine health: moving (no stall detected).')
    for name, st in status.items():
        label = 'reviews (fresh Codex session)' if name == 'claude' and st['mode'] == 'codex' else name
        lines.append(f'- **{label}**: {st["mode"]}; ' + (
            f'paused until {st["paused_until"]} ({st["reason"]})' if st['paused'] else 'available') +
            f'; calls today {st["calls_today"]}/{st["daily_cap"]}')
    lines.append('')
    if cfg.get('arxiv', {}).get('enabled'):
        from . import arxiv
        intake = arxiv.status(lab)
        lines.append(f"- arXiv: {intake['state']}; {len(intake['papers'])} stored versions. See ARXIV.md.")
    if cfg.get('zotero', {}).get('enabled'):
        from . import zotero
        z = zotero.status(lab)
        lines.append(f"- Zotero (theory lane only): {z['state']}; {z.get('items', 0)} items. See ZOTERO.md.")
        if z.get('fetched'):
            lines.append(f"- Full text (fetched on demand, kept in the lab): {z['fetched']['saved']} saved; "
                         f"{z['fetched']['needs_manual']} need a manual PDF. See NEEDS_MANUAL_PDF.md.")
    from . import research
    research.write_report(lab, cfg)
    from .program import attention_lines
    program_lines = attention_lines(lab)
    if waiting or program_lines:
        lines.append('- Before approving a preregistration or publication, get a second opinion: paste the file '
                     'into ChatGPT and ask for its weakest points; bring disagreements to Claude.')
    lines.extend(program_lines)
    if cfg['research']['enabled']:
        lines.append('- Research engine: see RESEARCH.md for evidence gaps and blocking work.')
        gate = lab.root / 'RESOURCE_GATE.json'
        if gate.exists() and json.loads(gate.read_text()).get('waiting'):
            lines.append('- Research workers are waiting for the resource gate; no model calls were started.')
    gate = lab.root / 'RESOURCE_GATE.json'
    gate_status = json.loads(gate.read_text()) if gate.exists() else {}
    if cfg['research']['enabled'] and gate_status.get('waiting'):
        lines.append('- Startup blocker: ' + str(gate_status.get('reason', 'unknown')) + '.')
    elif not waiting and not program_lines:
        lines.append('Nothing is waiting for you.')
    for t in waiting:
        result = json.loads(t['result'] or '{}')
        lines.append(f'- Task {t["id"]} ({t["kind"]}): {result.get("message") or result.get("note", "")}')
        if result.get('packet'):
            lines.append(f'  - Open: {result["packet"]}')
        if t['kind'] == 'review':
            lines.append(f'  - Record the decision: `python3 -m codexautopsych decide {t["id"]} '
                         f'approve|revise|reject --summary "..."`')
    blocked = lab.db.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('failed','blocked')").fetchone()[0]
    if blocked:
        lines += ['', f'{blocked} task(s) failed or are blocked; see STATUS.json.']
    write_atomic(lab.root / 'ATTENTION.md', '\n'.join(lines) + '\n')
    if cfg.get('status_page'):
        write_status_page(lab, cfg, lines)
    write_atomic(lab.root / 'SERVICES.json', json.dumps(status, indent=2, sort_keys=True) + '\n')


# ---------------------------------------------------------------- diagnostics

def doctor(lab):
    cfg = load_config(lab)
    report = {'python': platform.python_version(), 'python_ok': sys.version_info >= (3, 11),
              'platform': sys.platform, 'lab': str(lab.root),
              'lab_on_icloud_or_network': bool(re.search(r'Mobile Documents|CloudStorage|/Volumes/', str(lab.root))),
              'paid_route_variables_present': [k for k in svc.CODEX_PAID_ROUTES + svc.CLAUDE_PAID_ROUTES
                                               if os.environ.get(k)],
              'config': cfg}

    def probe(args):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            return r.returncode, (r.stdout + r.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            return -1, str(exc)

    codex = cfg['codex']['binary'] or shutil.which('codex')
    report['codex'] = {'found': codex}
    if codex:
        report['codex']['version'] = probe([codex, '--version'])[1].strip()[:80]
        report['codex']['chatgpt_login'] = 'logged in using chatgpt' in probe([codex, 'login', 'status'])[1].lower()
        help_text = probe([codex, 'exec', '--help'])[1]
        report['codex']['missing_flags'] = [f for f in ('--skip-git-repo-check', '--ignore-user-config',
                                                        '--ephemeral', '--sandbox') if f not in help_text]
    claude = cfg['claude']['binary'] or shutil.which('claude')
    report['claude'] = {'found': claude}
    if claude:
        report['claude']['version'] = probe([claude, '--version'])[1].strip()[:80]
        code, out = probe([claude, 'auth', 'status'])
        try:
            report['claude']['auth_method'] = json.loads(out).get('authMethod')
        except ValueError:
            report['claude']['auth_method'] = None
        report['claude']['subscription_login'] = report['claude']['auth_method'] in svc.CLAUDE_SUBSCRIPTION_METHODS
    return report


def write_status_page(lab, cfg, attention):
    """One page for Arthur, Claude and Codex: owner-maintained summary plus a live engine section."""
    from .program import PROJECT_ROOT
    manual = PROJECT_ROOT / 'docs' / 'STATUS_MANUAL.md'
    head = manual.read_text() if manual.exists() else '# CodexAutoPsych status\n'
    research_md = lab.root / 'RESEARCH.md'
    studies = [json.loads(r['record']) for r in lab.db.execute('SELECT record FROM program_studies')]
    recent = [dict(r) for r in lab.db.execute(
        "SELECT id, kind, service, state, updated FROM tasks ORDER BY id DESC LIMIT 8")]
    body = [head.rstrip(), '', '## Engine right now (updated automatically each pass)', '',
            f'Last pass: {_utc(svc.local_now())}', '']
    body += [line for line in attention if line.startswith('- ')]
    try:
        from .fast import engine as fast
        if fast.is_production(lab):
            body += ['', '### Fast lane (automated mini-experiments; details in fastlane/STATUS.md)']
            body += fast.status_lines(lab)
    except Exception as exc:  # noqa: BLE001 - status must still be written
        body += ['', f'### Fast lane: status unavailable ({type(exc).__name__})']
    body += ['', '### Studies the engine is working on']
    body += [f"- {s_['slug']} ({s_.get('lane', 'theory')} lane): {s_['phase']}" for s_ in studies] or ['- none yet']
    live_runs = sorted((lab.root / 'live').glob('*.json')) if (lab.root / 'live').exists() else []
    if live_runs:
        body += ['', '### Live studies (experiment runner)']
        for f in live_runs:
            r = json.loads(f.read_text())
            body.append(f"- {r['slug']}: {r['status']} - stage {r.get('stage') or '-'}, cells {r['done']}/{r['total']}"
                        + (f" - {r['reason'][:160]}" if r.get('reason') else ''))
    body += ['', '### Most recent tasks']
    for t in recent:
        payload = json.loads(lab.task(t['id'])['payload'])
        body.append(f"- #{t['id']} {t['kind']} via {t['service']}: {t['state']} - {payload.get('title', '')[:90]}")
    latest = sorted((lab.root / 'reviews').glob('task-*.md'), key=lambda f: int(f.stem.split('-')[1]))
    if latest:
        text = latest[-1].read_text().splitlines()
        summary = next((text[i + 2] for i, l in enumerate(text) if l == '## Summary' and i + 2 < len(text)), '')
        verdict = next((l.split('**')[1] for l in text if l.startswith('- Decision:') and '**' in l), '?')
        body += ['', '### Latest review', '', f"- {text[0].lstrip('# ')}: **{verdict}**",
                 f'  {summary[:400]}', f'  Full text: .lab/research/reviews/{latest[-1].name} (all: REVIEWS.md)']
    if research_md.exists():
        body += ['', '### Research questions', '']
        body += [l for l in research_md.read_text().splitlines() if l.startswith('- ') or l.startswith('  Falsifier')]
    body += ['', 'Engine files: .lab/research/ (ATTENTION.md, RESEARCH.md, REVIEWS.md, ARXIV.md, ZOTERO.md, NEEDS_MANUAL_PDF.md, work/).',
             'Change history: docs/LOG.md.']
    write_atomic(PROJECT_ROOT / 'STATUS.md', '\n'.join(body) + '\n')
