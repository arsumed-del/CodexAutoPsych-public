"""Publishing for the fast lane: periodic Zenodo digest (DOI), then Agent4Science posts.

Order is fixed: nothing is posted to Agent4Science without a Zenodo DOI to cite.
Credentials are read from files on the Mac at the moment of use and never logged,
stored in state, or written into any output.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import zipfile

from . import engine as E
from . import paper

ZENODO = 'https://zenodo.org/api'
A4S = 'https://agent4science.org/api/v1'
UA = 'Mozilla/5.0 (Macintosh) autopsych-fastlane/1.0'
LEDGER_ONLY = ('manipulation-failed', 'invalid', 'failed-run', 'aborted')   # listed in the ledger, never deposited
POSTABLE = ['replicated-two-models', 'replicated-same-model', 'not-replicated', 'effect-unreplicated', 'null']
KEYWORDS = ['language models', 'activation steering', 'affect', 'computational psychiatry',
            'automated research', 'AI-generated research', 'exploratory', 'AutoPsych']


class PublishError(RuntimeError):
    pass


def http(method, url, headers=None, data=None, timeout=120):
    """Single network entry point (tests replace it). Returns (status, body bytes)."""
    req = urllib.request.Request(url, data=data, method=method, headers={'User-Agent': UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _call(method, url, token, payload=None, raw=None, content_type='application/json'):
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    data = raw
    if payload is not None:
        data = json.dumps(payload).encode()
    if data is not None:
        headers['Content-Type'] = content_type
    status, body = http(method, url, headers, data)
    if status >= 400:
        raise PublishError(f'{method} {url.split("?")[0]} returned {status}: {body[:300].decode(errors="replace")}')
    return json.loads(body) if body.strip() else {}


def read_secret(path, prefix=None):
    text = Path(os.path.expanduser(path)).read_text()
    if prefix:
        for line in text.splitlines():
            if line.startswith(prefix + '='):
                return line.split('=', 1)[1].strip()
        raise PublishError(f'No {prefix} entry in the credentials file')
    return text.strip()


# ---------------------------------------------------------------- digest

def release_statement(cfg):
    mode = ('Each report in this deposit was released by the operator during the engine\'s supervised trial '
            'period; release is not validation of its claims.' if cfg['approval_required'] else
            'Reports were not individually reviewed by a human before release.')
    return (f'Generated and published by {cfg["engine_name"]}. Experiments were chosen, run, analyzed and written '
            f'up by AI systems. {mode} The operator ({cfg["operator_name"]}) is responsible for the system, not '
            'for the validation of each claim. Exploratory; not publicly preregistered (plans were frozen '
            'internally, with hashes, before each run). Findings concern language models and are not claims '
            'about humans.')


def build_digest(lab, cfg, state, ids):
    """Folder with DIGEST.md, one paper per report and a zip of all materials. Returns its path."""
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    name = f'{stamp}-{ids[0]}' if len(ids) == 1 else stamp
    out = E.FAST / 'deposits' / name
    n = 1
    while out.exists():
        n += 1
        out = E.FAST / 'deposits' / f'{name}-{n}'
    out.mkdir(parents=True)
    included = []
    for exp_id in ids:
        folder = E.exp_dir(exp_id)
        exp, spec = E.load_exp(folder), E.read_json(folder / 'spec.json')
        results, text = E.read_json(folder / 'results.json'), E.read_json(folder / 'text.json')
        (folder / 'paper.md').write_text(paper.render(spec, results, text, exp, cfg))   # final wording
        (out / f'{folder.name}.md').write_text((folder / 'paper.md').read_text())
        included.append((folder, exp, spec, results))
    lines = [f'# AutoPsych fast-lane ledger as of {stamp}', '', '> ' + release_statement(cfg), '',
             f'This deposit contains {len(included)} exploratory mini-experiment report(s) on affect-related '
             'steering in open-weight language models, with all raw records, plans, reviews and code. This page '
             'lists every experiment the engine has run so far, so the report can be read in that light.', '',
             '## Reports in this deposit', '', '| ID | Title | Outcome | Primary estimate (95% CI) |', '|---|---|---|---|']
    for folder, exp, spec, results in included:
        est = (exp.get('summary') or {}).get('primary')
        est = f"{est['mean']} ({est['ci95'][0]} to {est['ci95'][1]})" if est and est.get('ci95') else '-'
        lines.append(f"| {exp['id']} | {spec['title']} | {paper.OUTCOMES.get(results['outcome'], results['outcome'])} | {est} |")
    lines += ['', '## Full ledger to date (every experiment the engine has registered, including nulls and failures)',
              '', '| ID | Title | Lane | Outcome | Status |', '|---|---|---|---|---|']
    for folder in E.exp_dirs():
        e = E.load_exp(folder)
        lines.append(f"| {e['id']} | {e['title']} | {e['lane']} | {e.get('outcome') or 'in progress'} | {e['stage']} |")
    lines += ['', paper.RULE, '', 'Files: one Markdown report per experiment, and `materials.zip` with the frozen '
              'plans, raw cell records (including reply text), run metadata, results, reviews, prompt sets, the '
              'ledger and the harness and analysis code.', '']
    (out / 'LEDGER.md').write_text('\n'.join(lines))
    with zipfile.ZipFile(out / 'materials.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for folder, *_ in included:
            for f in sorted(folder.rglob('*')):
                keep = f.suffix in ('.json', '.jsonl', '.md') or f.name == 'DONE'
                if f.is_file() and keep and f.name not in ('progress.json', 'APPROVAL.json', 'a4s_post.json'):
                    # local folder names are not part of the record
                    text = f.read_text().replace(str(Path.home()), '~')
                    z.writestr(f'experiments/{folder.name}/{f.relative_to(folder)}', text)
        for f in sorted((E.REPO / 'codexautopsych' / 'fast').glob('*.py')):
            z.write(f, f'code/codexautopsych/fast/{f.name}')
        for f in sorted((E.FAST / 'prompts').glob('*.jsonl')):
            z.write(f, f'prompts/{f.name}')
        for name in ('PARADIGM.md', 'README.md', 'LEDGER.jsonl', 'WRITING_GUIDE.md', 'REVIEW_GUIDE.md'):
            if (E.FAST / name).exists():
                z.write(E.FAST / name, name)
    return out


def zenodo_metadata(cfg, digest_dir, ids):
    exps = [E.load_exp(E.exp_dir(i)) for i in ids]
    contributor = {'name': cfg['operator_name'], 'type': 'Supervisor'}
    if cfg.get('operator_orcid'):
        contributor['orcid'] = cfg['operator_orcid']
    if len(ids) == 1:           # one experiment per record: the finding is the title
        folder = E.exp_dir(ids[0])
        spec, results, text = (E.read_json(folder / n) for n in ('spec.json', 'results.json', 'text.json'))
        first = results['runs']['primary']['analysis']['primary']
        title = f"{text['title']} (AutoPsych exploratory report {ids[0]})"
        body = (f"<p><b>Question.</b> {spec['question']}</p>"
                f"<p><b>Outcome.</b> {paper.OUTCOMES.get(results['outcome'], results['outcome'])}. "
                f"Primary contrast ({first['label']}): {paper._fmt(first['summary'])} in the first run.</p>")
    else:
        title = (f'AutoPsych fast-lane reports {digest_dir.name}: {len(ids)} automated exploratory '
                 'mini-experiments on affect in language models')
        body = '<p>Reports in this deposit:</p><ul>' + ''.join(
            f"<li>{e['id']}: {e['title']}</li>" for e in exps) + '</ul>'
    return {'metadata': {
        'title': title[:250], 'upload_type': 'publication', 'publication_type': 'report',
        'description': f'<p>{release_statement(cfg)}</p>{body}'
                       '<p>The report states the question, the plan frozen before the run, the method, all '
                       'numbers, an automated review and the count of experiments run so far. LEDGER.md lists '
                       'every experiment the engine has run, including nulls, failed replications and failures. '
                       'Raw records and code are in materials.zip.</p>',
        'creators': [{'name': cfg['engine_name']}],
        'contributors': [contributor],
        'keywords': KEYWORDS, 'access_right': 'open', 'license': 'cc-by-4.0',
        'notes': 'AI-generated research output. Exploratory; not preregistered; not peer reviewed.'}}


def deposit(lab, cfg, state, ids):
    """Create, fill and publish one Zenodo record. Resumable: the draft is remembered between passes."""
    token = read_secret(cfg['zenodo_token_file'], 'ZENODO')
    draft = state.get('draft')
    if not draft:
        digest_dir = build_digest(lab, cfg, state, ids)
        made = _call('POST', f'{ZENODO}/deposit/depositions', token, payload={})
        draft = {'deposition': made['id'], 'bucket': made['links']['bucket'], 'dir': str(digest_dir),
                 'ids': list(ids), 'uploaded': [], 'metadata': False, 'created': E.utc()}
        state['draft'] = draft
        E.save_state(lab, state)
    digest_dir = Path(draft['dir'])
    for f in sorted(digest_dir.iterdir()):
        if f.is_file() and f.name not in draft['uploaded']:
            _call('PUT', f"{draft['bucket']}/{f.name}", token, raw=f.read_bytes(),
                  content_type='application/octet-stream')
            draft['uploaded'].append(f.name)
            E.save_state(lab, state)
    if not draft['metadata']:
        _call('PUT', f"{ZENODO}/deposit/depositions/{draft['deposition']}", token,
              payload=zenodo_metadata(cfg, digest_dir, draft['ids']))
        draft['metadata'] = True
        E.save_state(lab, state)
    done = _call('POST', f"{ZENODO}/deposit/depositions/{draft['deposition']}/actions/publish", token)
    doi = done.get('doi') or (done.get('metadata') or {}).get('doi')
    if not doi:
        raise PublishError('Zenodo published the record but returned no DOI')
    record = {'doi': doi, 'url': (done.get('links') or {}).get('record_html') or f'https://doi.org/{doi}',
              'ids': draft['ids'], 'time': E.utc(), 'digest': digest_dir.name}
    state['deposits'].append(record)
    state['last_deposit'], state['draft'] = record['time'], None
    for exp_id in record['ids']:
        folder = E.exp_dir(exp_id)
        exp = E.load_exp(folder)
        exp['stage'], exp['published'] = 'deposited', {'doi': doi, 'url': record['url'], 'time': record['time']}
        E.save_exp(folder, exp)
        E.ledger('deposited', exp_id, doi=doi)
    E.save_state(lab, state)
    E.git_commit([digest_dir, E.FAST / 'LEDGER.jsonl'] + [E.exp_dir(i) / 'exp.json' for i in record['ids']],
                 f'Fast lane: deposited {digest_dir.name} ({doi})')
    return record


# ---------------------------------------------------------------- Agent4Science

def a4s_tag(key, handle):
    """First tag must be a sciencesub the agent belongs to."""
    try:
        status, body = http('GET', f'{A4S}/agents/{handle}', {'Accept': 'application/json'})
        data = json.loads(body) if status < 400 else {}
        agent = data.get('agent', data)
        subs = agent.get('sciencesubs') or data.get('sciencesubs') or []
        slugs = [s.get('slug') if isinstance(s, dict) else s for s in subs]
        slugs = [s for s in slugs if isinstance(s, str)]
    except (ValueError, OSError):
        slugs = []
    for want in ('language', 'interpret', 'psych', 'cognit', 'neuro', 'ai'):
        for s in slugs:
            if want in s:
                return s
    return slugs[0] if slugs else 'language-models'


def take_payload(cfg, exp, spec, results, text, doi, tag):
    outcome = paper.OUTCOMES.get(results['outcome'], results['outcome'])
    first = results['runs']['primary']['analysis']['primary']
    summary = [
        f"Exploratory, AI-run mini-experiment {exp['id']} from an automated engine (operator-supervised; not "
        f"preregistered; full report, raw records and code: https://doi.org/{doi}).",
        f"Question: {spec['question']}",
        f"Outcome: {outcome}. Primary contrast ({first['label']}): {paper._fmt(first['summary'])} in the first run."]
    for label, name in (('rep_same', 'Same-model replication'), ('rep_other', 'Second-model replication')):
        if label in results['runs']:
            summary.append(f"{name}: {paper._fmt(results['runs'][label]['analysis']['primary']['summary'])}.")
    ctx = results['ledger_context']
    summary.append(f"Context: one of {ctx['experiments_registered_so_far']} experiments this engine has registered; "
                   'every experiment, including nulls and failures, is listed in the ledger in the same deposit.')
    questions = text.get('open_questions') or ['What control would most directly test whether this is carried by '
                                               'the reply text rather than by reply length or topic?']
    return {'title': f"[Exploratory, AI-run] {text['title']}"[:190], 'tags': [tag, 'activation-steering', 'affect'],
            'stance': 'neutral', 'summary': summary, 'hotTake': text['takeaway'][:300],
            'openQuestions': questions[:3], 'whoShouldCare': 'Interpretability researchers, computational '
            'psychiatrists and anyone studying affect-like states in language models', 'citations': [doi]}


def post(lab, cfg, state, exp_id):
    key = read_secret(cfg['a4s_key_file'])
    folder = E.exp_dir(exp_id)
    exp, spec = E.load_exp(folder), E.read_json(folder / 'spec.json')
    results, text = E.read_json(folder / 'results.json'), E.read_json(folder / 'text.json')
    payload = take_payload(cfg, exp, spec, results, text, exp['published']['doi'], a4s_tag(key, cfg['a4s_handle']))
    made = _call('POST', f'{A4S}/takes', key, payload=payload)
    take = made.get('take') if isinstance(made.get('take'), dict) else made
    take_id = take.get('id') or made.get('id')
    record = {'id': exp_id, 'take': take_id, 'time': E.utc()}
    state['posts'].append(record)
    exp['stage'] = 'posted'
    exp['published']['a4s_take'] = take_id
    E.save_exp(folder, exp)
    E.write_json(folder / 'a4s_post.json', {'payload': payload, 'take': take_id, 'time': record['time']})
    E.ledger('posted', exp_id, venue='agent4science', take=take_id)
    return record


# ---------------------------------------------------------------- one publishing step

def step(lab, main_cfg, cfg, state):
    from .. import routine
    now = datetime.now(timezone.utc)
    report = {}
    exps = {i: E.load_exp(E.exp_dir(i)) for i in state['pending']}
    for exp_id, e in list(exps.items()):       # outcomes that only belong in the ledger are closed, not deposited
        if e['stage'] == 'publishable' and e['outcome'] in LEDGER_ONLY:
            e['stage'], e['note'] = 'closed', 'listed in the ledger only; not deposited'
            E.save_exp(E.exp_dir(exp_id), e)
            E.ledger('ledger-only', exp_id, outcome=e['outcome'])
            state['pending'].remove(exp_id)
            del exps[exp_id]
    ready = sorted(i for i, e in exps.items() if e['stage'] == 'publishable')
    recent = [d for d in state['deposits'] if now - E.parse(d['time']) < timedelta(days=7)]
    due = len(recent) < cfg['max_deposits_per_week']
    if cfg['publish_zenodo'] and (state.get('draft') or (ready and due)):
        try:       # one experiment per record, oldest first, at most one new record per step
            record = deposit(lab, cfg, state, (state.get('draft') or {}).get('ids') or ready[:1])
            report['deposited'] = record['doi']
            routine.notify(main_cfg, 'AutoPsych fast lane: deposited', f"{record['doi']} ({len(record['ids'])} reports)")
        except (PublishError, OSError, ValueError, KeyError) as exc:
            E.note_error(state, 'zenodo deposit', exc)
            return report
        exps = {i: E.load_exp(E.exp_dir(i)) for i in state['pending']}
    # Agent4Science: at most one post per step, capped per week, best-supported results first.
    week = [p for p in state['posts'] if now - E.parse(p['time']) < timedelta(days=7)]
    waiting = [i for i, e in exps.items() if e['stage'] == 'deposited']
    for exp_id in list(waiting):
        e = exps[exp_id]
        stale = now - E.parse(e['published']['time']) > timedelta(days=14)
        if e['outcome'] not in POSTABLE or not cfg['publish_a4s'] or stale:
            state['pending'].remove(exp_id)        # deposited only; done
            waiting.remove(exp_id)
    if waiting and len(week) < cfg['max_posts_per_week']:
        if not state['posts'] or now - E.parse(state['posts'][-1]['time']) > timedelta(minutes=15):
            best = min(waiting, key=lambda i: (POSTABLE.index(exps[i]['outcome']), i))
            try:
                post(lab, cfg, state, best)
                state['pending'].remove(best)
                report['posted'] = best
            except (PublishError, OSError, ValueError, KeyError) as exc:
                E.note_error(state, f'agent4science post {best}', exc)
    return report
