"""Live experiment runner: executes ONE owner-approved, frozen study on this Mac.

Separate from the synthetic runner (runner.py) and from all AI workers: no model
service calls, no publishing, no uploads, no credentials. Science lives in a
per-study adapter; this module only enforces approval, frozen hashes, staged gates,
checkpointed resumable cells, a single-study lock, status and the outcome hand-off.

Study folder layout (see docs/RUNNER.md):
  study.json     slug, question_id, designed_by, reviewed_by, frozen_files, stages
  adapter.py     cells(stage, ctx), run_cell(stage, cell, ctx), optional gate(...),
                 optional environment(ctx), analyze(records, ctx)
  APPROVED.json  written only by `live-approve` (Arthur): hashes of every frozen file
  data/          <stage>.jsonl append-only cell records, <stage>.gate.json
  results/       outcome.json
  RUN_STATUS.md  plain-English progress;  DEVIATIONS.md  engineering notes;  STOP  stop request
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .core import Busy, canonical, digest, now, write_atomic

DESIGNER = 'claude'          # owner decision 2026-10-12: Claude finalizes designs that run
REQUIRED = ('slug', 'question_id', 'designed_by', 'reviewed_by', 'frozen_files', 'stages')


class Refused(RuntimeError):
    """Raised before any model load when a study may not run."""


def _sha(path):
    return digest(Path(path).read_bytes())


def load_study(folder):
    folder = Path(folder).absolute()
    if (folder / 'study.json').is_symlink():
        raise Refused('study.json may not be a symlink')
    try:
        spec = json.loads((folder / 'study.json').read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f'Cannot read study.json: {exc}') from exc
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        raise Refused('study.json missing: ' + ', '.join(missing))
    if not spec['stages'] or not all(isinstance(s, dict) and s.get('name') for s in spec['stages']):
        raise Refused('stages must be a nonempty list of {"name": ...}')
    names = [s['name'] for s in spec['stages']]
    if len(set(names)) != len(names) or any(not n.replace('_', '').replace('-', '').isalnum() for n in names):
        raise Refused('Stage names must be unique and alphanumeric')
    return folder, spec


def frozen_paths(folder, spec):
    files = sorted(set(['study.json', 'adapter.py', *spec['frozen_files']]))
    out = {}
    for rel in files:
        p = (folder / rel).absolute()
        if '..' in Path(rel).parts or not p.is_relative_to(folder) or p.is_symlink() or not p.is_file():
            raise Refused(f'Frozen file missing or unsafe: {rel}')
        out[rel] = p
    return out


def approve(folder, approver='Arthur'):
    """Owner action: record the hashes Arthur approves. Refuses re-approval of changed files."""
    folder, spec = load_study(folder)
    if str(spec['designed_by']).strip().lower() != DESIGNER:
        raise Refused('Final design must come from Claude (owner decision 2026-10-12)')
    reviewer = str(spec['reviewed_by']).strip()
    if not reviewer or reviewer.lower() == DESIGNER:
        raise Refused('Design needs an independent reviewer (not the designer)')
    hashes = {rel: _sha(p) for rel, p in frozen_paths(folder, spec).items()}
    target = folder / 'APPROVED.json'
    record = {'slug': spec['slug'], 'approved_by': approver, 'approved_at': now(),
              'designed_by': spec['designed_by'], 'reviewed_by': reviewer, 'hashes': hashes}
    if target.exists():
        old = json.loads(target.read_text())
        if old.get('hashes') != hashes:
            raise Refused('Already approved with different files; a changed design needs a new study folder')
        return old
    with target.open('x') as out:
        out.write(json.dumps(record, indent=2, sort_keys=True))
    return record


def verify(folder, spec):
    target = folder / 'APPROVED.json'
    if not target.is_file() or target.is_symlink():
        raise Refused('Not approved: APPROVED.json is missing (Arthur runs live-approve)')
    approved = json.loads(target.read_text())
    if approved.get('slug') != spec['slug']:
        raise Refused('Approval belongs to a different study')
    current = {rel: _sha(p) for rel, p in frozen_paths(folder, spec).items()}
    if approved.get('hashes') != current:
        changed = sorted(k for k in set(current) | set(approved.get('hashes', {}))
                         if current.get(k) != approved.get('hashes', {}).get(k))
        raise Refused('Frozen files changed after approval: ' + ', '.join(changed))
    return approved


def _adapter(folder):
    spec = importlib.util.spec_from_file_location('study_adapter_' + digest(str(folder).encode())[:8],
                                                  folder / 'adapter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('cells', 'run_cell', 'analyze'):
        if not callable(getattr(module, name, None)):
            raise Refused(f'adapter.py must define {name}()')
    return module


def _read_records(path):
    """Verified records keyed by cell id. A torn last line (crash mid-write) is moved aside."""
    records = {}
    if not path.exists():
        return records
    raw = path.read_bytes()
    lines = raw.split(b'\n')
    tail = lines.pop() if lines else b''
    if tail:  # no trailing newline: incomplete write
        torn = path.with_suffix('.torn-' + now().replace(':', '') + '.txt')
        torn.write_bytes(tail)
        with path.open('r+b') as fh:
            fh.truncate(len(raw) - len(tail))
    for i, line in enumerate(lines):
        rec = json.loads(line)
        body = {k: v for k, v in rec.items() if k != 'sha'}
        if digest(canonical(body).encode()) != rec.get('sha'):
            raise Refused(f'Record integrity failure in {path.name} line {i + 1}')
        if rec['cell'] in records:
            raise Refused(f'Duplicate cell {rec["cell"]} in {path.name}')
        records[rec['cell']] = rec
    return records


def _append(path, stage, cell_id, output):
    body = {'stage': stage, 'cell': cell_id, 'time': now(), 'output': output}
    body['sha'] = digest(canonical(body).encode())
    with path.open('a') as fh:
        fh.write(canonical(body) + '\n')
        fh.flush()
        os.fsync(fh.fileno())
    return body


class Run:
    def __init__(self, folder, lab):
        self.folder, self.spec = load_study(folder)
        self.lab = lab
        self.data = self.folder / 'data'
        self.state = {'slug': self.spec['slug'], 'stage': None, 'done': 0, 'total': 0,
                      'status': 'starting', 'reason': '', 'started': now(), 'updated': now()}

    # ---- status -------------------------------------------------------------
    def status(self, **kw):
        self.state.update(kw, updated=now())
        s = self.state
        lines = [f"# {s['slug']} — live run status", '',
                 f"**{s['status'].upper()}** — stage: {s['stage'] or '-'}; cells {s['done']}/{s['total']}",
                 f"Updated: {s['updated']}"]
        if s.get('eta'):
            lines.append(f"Estimated time left in this stage: {s['eta']}")
        if s['reason']:
            lines += ['', 'Reason: ' + s['reason']]
        write_atomic(self.folder / 'RUN_STATUS.md', '\n'.join(lines) + '\n')
        live = self.lab.root / 'live'
        live.mkdir(exist_ok=True)
        write_atomic(live / (s['slug'] + '.json'), json.dumps(s, indent=2))

    def deviation(self, text):
        with (self.folder / 'DEVIATIONS.md').open('a') as fh:
            fh.write(f'- {now()} (engineering, no scientific setting changed): {text}\n')

    # ---- main ---------------------------------------------------------------
    def execute(self, resource_check=None):
        approved = verify(self.folder, self.spec)        # before any model load
        if resource_check is not None:
            res = resource_check()
            if not res.get('free'):
                self.status(status='waiting', reason='Mac busy: ' + res.get('reason', 'unknown'))
                return self.state
        try:
            with self.lab.lock('execution', 'live:' + self.spec['slug']):
                return self._execute(approved)
        except Busy:
            self.status(status='waiting', reason='Another study holds the execution lock')
            return self.state

    def _execute(self, approved):
        self.data.mkdir(exist_ok=True)
        (self.folder / 'results').mkdir(exist_ok=True)
        adapter = _adapter(self.folder)
        ctx = {'study_dir': str(self.folder), 'data_dir': str(self.data), 'gates': {},
               'config': self.spec.get('config', {})}
        outcome_path = self.folder / 'results' / 'outcome.json'
        if outcome_path.exists():
            self.status(status='complete', reason='Already complete; nothing rerun')
            return self.state
        if hasattr(adapter, 'environment'):
            env = adapter.environment(ctx)
            if env is not None and not env.get('ok', True):
                return self._block('environment', 'Environment check failed: ' + str(env.get('reason')))
        all_records = {}
        for stage_spec in self.spec['stages']:
            stage = stage_spec['name']
            verify(self.folder, self.spec)               # re-check frozen files at each stage
            gate_file = self.data / (stage + '.gate.json')
            path = self.data / (stage + '.jsonl')
            records = _read_records(path)
            cells = list(adapter.cells(stage, ctx))
            ids = [c['id'] for c in cells]
            if len(set(ids)) != len(ids):
                return self._block(stage, 'Adapter produced duplicate cell ids')
            stray = set(records) - set(ids)
            if stray:
                return self._block(stage, f'Saved cells not in the design: {sorted(stray)[:5]}')
            self.status(status='running', stage=stage, done=len(records), total=len(cells), reason='')
            t0, n0 = now_s(), len(records)
            for cell in cells:
                if cell['id'] in records:
                    continue
                if (self.folder / 'STOP').exists():
                    return self._stopped(stage)
                records[cell['id']] = _append(path, stage, cell['id'], adapter.run_cell(stage, cell, ctx))
                done = len(records)
                rate = (now_s() - t0) / max(done - n0, 1)
                self.status(done=done, eta=_fmt((len(cells) - done) * rate))
            all_records[stage] = [records[i] for i in ids]
            if gate_file.exists():
                gate = json.loads(gate_file.read_text())
            elif hasattr(adapter, 'gate'):
                result = adapter.gate(stage, all_records[stage], ctx)
                ok, reason, saved = (list(result) + [None])[:3]
                gate = {'stage': stage, 'passed': bool(ok), 'reason': str(reason), 'saved': saved, 'time': now()}
                with gate_file.open('x') as out:          # gate results are immutable
                    out.write(json.dumps(gate, indent=2, sort_keys=True))
            else:
                gate = {'stage': stage, 'passed': True, 'reason': 'no gate', 'saved': None}
            if not gate['passed']:
                return self._block(stage, 'Gate failed: ' + gate['reason'] +
                                   ' — stopped as registered; no retry with changed settings')
            ctx['gates'][stage] = gate.get('saved')
        outcome = adapter.analyze(all_records, ctx)
        outcome = {**outcome, 'question_id': self.spec['question_id'], 'scope': 'model',
                   'approval_hashes': approved['hashes']}
        with outcome_path.open('x') as out:
            out.write(json.dumps(outcome, indent=2, sort_keys=True))
        handed = self.hand_off(outcome_path)
        self.status(status='complete', reason=handed)
        return self.state

    def hand_off(self, outcome_path):
        """Record the outcome through the engine's own intake so an independent review is queued."""
        from . import program, research
        try:
            data = outcome_path.read_bytes()
            target = program._folder() / 'sources' / 'outcomes' / f"{self.spec['slug']}-{digest(data)[:12]}.json"
            program._save_once(target, data, program.PROJECT_ROOT)
            saved = research.import_outcome(self.lab, target)
            return f"Outcome recorded ({saved['result']}); independent review will be queued on the next pass"
        except Exception as exc:   # never lose data because hand-off failed
            self.deviation(f'Outcome hand-off failed: {exc}')
            return f'Outcome saved in results/outcome.json but hand-off failed: {exc}. Import it with research-outcome.'

    def _block(self, stage, reason):
        self.status(status='blocked', stage=stage, reason=reason)
        return self.state

    def _stopped(self, stage):
        self.status(status='stopped', stage=stage, reason='Stopped at your request (STOP file). Resume with live-start.')
        return self.state


def now_s():
    import time
    return time.monotonic()


def _fmt(seconds):
    seconds = int(seconds)
    return f'{seconds // 3600} h {seconds % 3600 // 60} min' if seconds >= 3600 else f'{max(seconds // 60, 1)} min'


def start_background(lab_root, folder):
    """Launch live-run detached (survives closing windows); keeps the Mac awake on macOS."""
    folder, spec = load_study(folder)
    verify(folder, spec)
    stop = folder / 'STOP'
    if stop.exists():
        stop.unlink()
    (folder / 'logs').mkdir(exist_ok=True)
    cmd = [sys.executable, '-m', 'codexautopsych', '--lab', str(lab_root), 'live-run', str(folder)]
    if shutil.which('caffeinate'):
        cmd = ['caffeinate', '-i', *cmd]
    log = (folder / 'logs' / 'run.log').open('a')
    proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent))
    (folder / 'logs' / 'run.pid').write_text(str(proc.pid))
    return proc.pid


def request_stop(folder):
    folder, _ = load_study(folder)
    (folder / 'STOP').write_text(now())
