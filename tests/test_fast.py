"""Fast lane: spec, analysis, the full loop with a synthetic runner, reports, publishing (no network)."""
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from codexautopsych.fast import engine as E
from codexautopsych.fast import harness as H
from codexautopsych.fast import paper, publish, stats
from codexautopsych.fast import spec as S

REAL_FAST = Path(__file__).resolve().parents[1] / 'fastlane'
SEED_FILES = sorted(p for p in (REAL_FAST / 'seeds').glob('*.json') if p.name[2] == '-' and not p.name.startswith('00-'))   # the original five
SEED = json.loads(SEED_FILES[0].read_text())


class FakeLab:
    def __init__(self, root):
        self.root = Path(root)


def synthetic_rows(spec, prompts, label, effect):
    """Cell records as the harness would write them, with a chosen effect of the direction on ratings."""
    rng = random.Random(S.seed('synthetic', spec['slug'], label))
    rows = []
    for cell in S.cells(spec, prompts):
        arm = cell['arm']
        directed = arm not in (S.ARM_NONE, S.ARM_RANDOM)
        base = 6.0 + rng.gauss(0, 0.3) + (effect if directed else 0.0)
        rows.append({'key': cell['key'], 'prompt_id': cell['prompt']['id'], 'prompt_type': 'x',
                     'condition': cell['condition']['name'], 'arm': arm, 'seed': 1, 'reply_text': 'text',
                     'reply_tokens': 40 + (8 if directed else 0), 'reply_truncated': False, 'distinct_ratio': 0.8,
                     'reply_nll': 2.0, 'projection': {d['name']: (0.3 if arm == d['name'] else 0.0) + rng.gauss(0, 0.02)
                                                      for d in spec['directions']},
                     'emotion': {'thinking': {'hostile': 0.5 if directed else 0.1, 'warm': 0.1}, 'reply': {'hostile': 0.1, 'warm': 0.3}},
                     'ratings': {m['name']: {'rating': round(base, 4), 'valid_mass': 0.9} for m in spec['measures']},
                     'seconds': 1.0})
    return rows


class SpecTests(unittest.TestCase):
    def test_seed_files_are_valid_and_distinct(self):
        keys = set()
        for path in sorted((REAL_FAST / 'seeds').glob('*.json')):
            spec = S.validate(json.loads(path.read_text()))
            keys.add(S.design_key(spec))
        self.assertGreaterEqual(len(keys), 7)

    def test_refuses_unknown_keys_code_and_bad_primary(self):
        for change in ({'code': 'import os'}, {'directions': ['rage']}, {'n_prompts': 500},
                       {'primary': {'measure': 'liking_user', 'type': 'steer', 'direction': 'hostility', 'condition': 'nope'}},
                       {'measures': [{'name': 'x1', 'question': 'How are you today, on any scale you like at all?'}]}):
            with self.assertRaises(S.SpecError):
                S.validate(dict(SEED, **change))

    def test_prompt_splits_are_disjoint_and_stable(self):
        spec, pools = S.validate(SEED), H.load_pools(REAL_FAST / 'prompts')
        a, b = S.choose_prompts(spec, pools, 0), S.choose_prompts(spec, pools, 1)
        self.assertEqual(len(a), 16)
        self.assertFalse({p['id'] for p in a} & {p['id'] for p in b})
        self.assertEqual(a, S.choose_prompts(spec, pools, 0))
        mixed = S.validate(dict(SEED, prompt_set='mixed', n_prompts=24))
        self.assertEqual(len(S.choose_prompts(mixed, pools, 1)), 24)


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.spec, pools = S.validate(SEED), H.load_pools(REAL_FAST / 'prompts')
        self.prompts = S.choose_prompts(self.spec, pools, 0)

    def test_effect_and_null_are_told_apart(self):
        hit = stats.analyze(self.spec, synthetic_rows(self.spec, self.prompts, 'a', -2.0))
        self.assertEqual(hit['primary']['verdict'], 'effect')
        self.assertTrue(hit['primary']['matches_prediction'])
        self.assertLess(hit['primary']['summary']['ci95'][1], 0)
        null = stats.analyze(self.spec, synthetic_rows(self.spec, self.prompts, 'b', 0.0))
        self.assertEqual(null['primary']['verdict'], 'null')
        self.assertEqual(stats.analyze(self.spec, synthetic_rows(self.spec, self.prompts, 'a', -2.0)), hit)

    def test_invalid_when_digits_get_no_probability(self):
        rows = synthetic_rows(self.spec, self.prompts, 'a', -2.0)
        for r in rows:
            for m in r['ratings'].values():
                m['valid_mass'] = 0.1
        self.assertEqual(stats.analyze(self.spec, rows)['primary']['verdict'], 'invalid')

    def test_interaction_sign_follows_listed_order(self):
        spec = S.validate(json.loads(SEED_FILES[1].read_text()))
        prompts = S.choose_prompts(spec, H.load_pools(REAL_FAST / 'prompts'), 0)
        rows = synthetic_rows(spec, prompts, 'a', 0.0)
        for r in rows:
            if r['condition'] == 'negation' and r['arm'] == 'hostility':
                r['ratings']['liking_user']['rating'] -= 2.0
        out = stats.analyze(spec, rows)
        self.assertEqual(out['primary']['sign'], '-')
        self.assertEqual(out['primary']['verdict'], 'effect')

    def test_classify(self):
        def a(verdict, sign='-'):
            return {'primary': {'verdict': verdict, 'sign': sign}}
        self.assertEqual(stats.classify(a('null'), None, None), 'null')
        self.assertEqual(stats.classify(a('effect'), a('null'), None), 'not-replicated')
        self.assertEqual(stats.classify(a('effect'), a('effect', '+'), None), 'not-replicated')
        self.assertEqual(stats.classify(a('effect'), a('effect'), a('null')), 'replicated-same-model')
        self.assertEqual(stats.classify(a('effect'), a('effect'), a('effect')), 'replicated-two-models')


class LoopTests(unittest.TestCase):
    """The whole loop with a synthetic runner, fake Codex, and a fake network."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = {k: getattr(E, k) for k in ('FAST', 'REPO', 'launch', 'machine_free', 'codex', 'git_commit')}
        self.saved_http = publish.http
        E.FAST = self.tmp / 'fastlane'
        shutil.copytree(REAL_FAST / 'prompts', E.FAST / 'prompts')
        (E.FAST / 'seeds').mkdir()
        for name in ('PARADIGM.md', 'WRITING_GUIDE.md', 'REVIEW_GUIDE.md', 'README.md'):
            shutil.copy(REAL_FAST / name, E.FAST / name)
        E.REPO = Path(__file__).resolve().parents[1]
        self.lab = FakeLab(self.tmp / 'lab')
        self.lab.root.mkdir()
        self.main = {'notify': False, 'codex': {}}
        self.effects = {'primary': -2.0, 'rep_same': -2.0, 'rep_other': -2.0}
        self.codex_calls, self.review_answers, self.requests = [], [], []
        self.bad_number = False
        self.flat_projection = False
        E.machine_free = lambda lab, cfg: {'free': True, 'reason': 'clear'}
        E.git_commit = lambda paths, message: 'c0ffee' * 6 + 'c0ff'
        E.launch = self.fake_launch
        E.codex = self.fake_codex
        publish.http = self.fake_http
        E.write_json(E.FAST / 'config.json', {'enabled': True, 'approval_required': True, 'alternate_codex': False, 'reference_directions': {},
                                             'zenodo_token_file': str(self.tmp / 'tokens.txt'),
                                             'a4s_key_file': str(self.tmp / 'a4s.txt')})
        (self.tmp / 'tokens.txt').write_text('OSF=x\nZENODO=secret-zenodo-token\n')
        (self.tmp / 'a4s.txt').write_text('scb_secret_key\n')

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(E, k, v)
        publish.http = self.saved_http
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_seed(self, index=0, **change):
        raw = json.loads(SEED_FILES[index].read_text())
        raw.update(change)
        (E.FAST / 'seeds' / f"{raw['slug']}.json").write_text(json.dumps(raw))

    def fake_launch(self, lab, cfg, folder, exp, label):
        spec = E.read_json(folder / 'spec.json')
        prompts = S.choose_prompts(spec, H.load_pools(E.FAST / 'prompts'), E.RUN_SPLITS[label])
        out = folder / 'runs' / label
        out.mkdir(parents=True, exist_ok=True)
        for row in synthetic_rows(spec, prompts, label, self.effects[label]):
            if self.flat_projection:
                row['projection'] = {k: 0.0 for k in row['projection']}
            H.append_row(out / 'cells.jsonl', row)
        model = cfg['replication_model'] if label == 'rep_other' else cfg['primary_model']
        E.write_json(out / 'meta.json', {
            'model': model, 'model_source': S.MODELS[model]['id'], 'layer_index': 18, 'n_layers': 36,
            'steering_size': {d['name']: 12.5 for d in spec['directions']},
            'calibration': {d['name']: {'alpha_max': 0.08, 'norm': 200.0, 'censored': False} for d in spec['directions']},
            'thinking_cap': 384, 'direction_separation': {d['name']: 0.4 for d in spec['directions']},
            'prompt_ids': [p['id'] for p in prompts]})
        E.write_json(out / 'DONE', {'cells': len(S.cells(spec, prompts))})
        exp['runs'][label] = {'model': model, 'restarts': 0, 'status': 'running', 'pid': None}

    def fake_codex(self, lab, main_cfg, cfg, state, task, ws, sandbox='workspace-write'):
        E.require_budget(cfg, state)
        state['codex_calls'] = {E.today(): state['codex_calls'].get(E.today(), 0) + 1}
        self.codex_calls.append(task[:40])
        if task.startswith('Propose'):
            raw = json.loads(SEED_FILES[2].read_text())
            raw['slug'] = 'codex-proposed-washout'
            (ws / 'outputs' / 'spec.json').write_text(json.dumps(raw))
            return 'done'
        if task.startswith('Write'):
            results = json.loads((ws / 'inputs' / 'results.json').read_text())
            s = results['runs']['primary']['analysis']['primary']['summary']
            number = '9.87' if self.bad_number else f"{s['mean']:.2f}"
            text = {'title': 'Hostility present only during the reply lowered later liking',
                    'abstract': ('We asked whether a steered state carries into a later rating through text alone. '
                                 f'The primary contrast was {number} in the first run and held on held-out prompts. '
                                 'The effect matched the registered prediction and was seen in both models tested.'),
                    'interpretation': ('The rating fell after hostility steering relative to random steering, and the '
                                       'steered replies still carried the direction when re-read without steering. '
                                       'Reply length also differed between arms, so length is an alternative path.'),
                    'takeaway': 'In this setup, a hostile state present only while the reply was written lowered later liking.',
                    'limitations': ['Only one rating question was primary.'], 'open_questions': ['Does it survive length matching?']}
            (ws / 'outputs' / 'text.json').write_text(json.dumps(text))
            return 'done'
        self.assertEqual(sandbox, 'read-only')
        return 'Here is my review: ' + json.dumps(self.review_answers.pop(0) if self.review_answers else
                                                  {'decision': 'approve', 'summary': 'Accurate and cautious.',
                                                   'required_changes': [], 'concerns': ['Length differs between arms.']})

    def fake_http(self, method, url, headers=None, data=None, timeout=120):
        self.requests.append((method, url, dict(headers or {}), data))
        if url.endswith('/deposit/depositions') and method == 'POST':
            return 201, json.dumps({'id': 777, 'links': {'bucket': 'https://zenodo.org/api/files/bucket-1'}}).encode()
        if '/files/bucket-1/' in url:
            return 201, b'{}'
        if url.endswith('/depositions/777') and method == 'PUT':
            return 200, b'{}'
        if url.endswith('/actions/publish'):
            return 202, json.dumps({'doi': '10.5281/zenodo.777', 'links': {'record_html': 'https://zenodo.org/records/777'}}).encode()
        if url.endswith('/agents/constraintlab'):
            return 200, json.dumps({'agent': {'sciencesubs': [{'slug': 'physics'}, {'slug': 'language-models'}]}}).encode()
        if url.endswith('/takes'):
            return 201, json.dumps({'success': True, 'take': {'id': 'take_abc'}}).encode()
        return 404, b'{}'

    def steps(self, n=12):
        out = None
        for _ in range(n):
            out = E.step(self.lab, self.main)
        return out

    def exp(self, exp_id='E0001'):
        return E.load_exp(E.exp_dir(exp_id))

    def test_off_and_kill_switch_do_nothing(self):
        self.add_seed()
        E.set_config('enabled', False)
        self.assertEqual(E.step(self.lab, self.main), {'enabled': False})
        E.set_config('enabled', True)
        (E.FAST / 'STOP').write_text('stop')
        self.assertEqual(E.step(self.lab, self.main), {'enabled': False})
        self.assertFalse(E.exp_dirs())
        self.assertIn('STOPPED', (E.FAST / 'STATUS.md').read_text())

    def test_replicated_effect_waits_for_approval_then_publishes(self):
        self.add_seed()
        self.steps(6)
        exp = self.exp()
        self.assertEqual(exp['outcome'], 'replicated-two-models')
        self.assertEqual(exp['stage'], 'awaiting_approval')
        self.assertEqual(exp['review']['decision'], 'approve')
        self.assertTrue(exp['audit']['passed'])
        self.assertEqual(set(exp['runs']), {'primary', 'rep_same', 'rep_other'})
        self.assertFalse(self.requests, 'nothing may reach the network before approval')
        folder = E.exp_dir('E0001')
        reg = E.read_json(folder / 'REGISTERED.json')
        self.assertEqual(reg['spec_sha256'], S.sha(E.read_json(folder / 'spec.json')))
        text = (folder / 'paper.md').read_text()
        for needle in ('Authorship and status', 'not publicly preregistered', 'Reviewer concern: Length differs',
                       'replication (second model', 'How many experiments the engine has run'):
            self.assertIn(needle, text)
        self.assertIn('Needs you', (E.FAST / 'STATUS.md').read_text())

        E.decide('E0001', 'approve', 'Arthur')
        self.steps(2)
        exp = self.exp()
        self.assertEqual(exp['stage'], 'posted')
        self.assertEqual(exp['published']['doi'], '10.5281/zenodo.777')
        self.assertEqual(exp['published']['a4s_take'], 'take_abc')
        meta = json.loads(next(d for m, u, h, d in self.requests if u.endswith('/depositions/777')))['metadata']
        self.assertEqual(meta['creators'], [{'name': 'AutoPsych (automated research engine)'}])
        self.assertTrue(meta['title'].startswith('Hostility present only during the reply lowered later liking (AutoPsych'))
        self.assertEqual(meta['contributors'], [{'name': 'Su, Arthur Y.', 'type': 'Supervisor'}])
        self.assertIn('not publicly preregistered', meta['description'])
        take = json.loads(next(d for m, u, h, d in self.requests if u.endswith('/takes')))
        self.assertEqual(take['tags'][0], 'language-models')
        self.assertEqual(take['citations'], ['10.5281/zenodo.777'])
        self.assertTrue(take['title'].startswith('[Exploratory, AI-run]'))
        uploads = [u.rsplit('/', 1)[1] for m, u, h, d in self.requests if '/files/bucket-1/' in u]
        self.assertEqual(sorted(uploads), ['E0001-warmth-constraint-text-carryover.md', 'LEDGER.md', 'materials.zip'])
        self.assertIn('released this report during', (E.exp_dir('E0001') / 'paper.md').read_text())
        state = json.dumps(E.load_state(self.lab)) + (E.FAST / 'LEDGER.jsonl').read_text()
        self.assertNotIn('secret', state)
        self.assertEqual(E.trust(), {'approved': 1, 'rejected': 0, 'streak': 1})

    def test_null_gets_template_report_and_no_codex_text(self):
        self.add_seed()
        E.set_config('max_experiments_per_day', 1)
        self.effects['primary'] = 0.0
        self.steps(4)
        exp = self.exp()
        self.assertEqual(exp['outcome'], 'null')
        self.assertEqual(set(exp['runs']), {'primary'})
        self.assertEqual(exp['text_source'], 'template')
        self.assertEqual(self.codex_calls, [])
        self.assertIn('null result', (E.exp_dir('E0001') / 'paper.md').read_text())

    def test_failed_replication_is_reported_as_such(self):
        self.add_seed()
        self.effects['rep_same'] = 0.0
        self.steps(6)
        exp = self.exp()
        self.assertEqual(exp['outcome'], 'not-replicated')
        self.assertEqual(set(exp['runs']), {'primary', 'rep_same'})
        self.assertIn('did NOT replicate', (E.exp_dir('E0001') / 'paper.md').read_text())

    def test_unsupported_number_falls_back_to_template(self):
        self.add_seed()
        self.bad_number = True
        self.steps(6)
        exp = self.exp()
        self.assertEqual(exp['text_source'], 'template')
        self.assertIn('9.87', exp['text_error'])
        self.assertNotIn('9.87', (E.exp_dir('E0001') / 'paper.md').read_text())

    def test_review_rejection_strips_interpretation_and_prints_concerns(self):
        self.add_seed()
        self.review_answers = [{'decision': 'revise', 'summary': 'Overclaims.', 'required_changes': ['Say length is confounded.'], 'concerns': []},
                               {'decision': 'reject', 'summary': 'Still overclaims.', 'required_changes': [], 'concerns': ['Confound not addressed.']}]
        self.steps(8)
        exp = self.exp()
        self.assertEqual(exp['review']['round'], 2)
        self.assertEqual(exp['text_source'], 'template')
        text = (E.exp_dir('E0001') / 'paper.md').read_text()
        self.assertIn('Reviewer concern: Confound not addressed.', text)
        self.assertIn('interpretive text removed', text)

    def test_rejection_closes_report_and_never_publishes(self):
        self.add_seed()
        self.steps(6)
        with self.assertRaises(ValueError):
            E.decide('E0001', 'reject', 'Arthur')
        E.decide('E0001', 'reject', 'Arthur', 'effect too small to post')
        self.steps(2)
        self.assertEqual(self.exp()['stage'], 'closed')
        self.assertFalse(self.requests)
        self.assertEqual(E.trust()['rejected'], 1)

    def test_autonomous_mode_and_weekly_limits(self):
        E.set_config('approval_required', False)
        E.set_config('max_experiments_per_day', 5)
        for i in range(3):
            self.add_seed(i)
        E.set_config('max_deposits_per_week', 2)
        self.steps(40)
        state = E.load_state(self.lab)
        self.assertEqual([d['ids'] for d in state['deposits']], [['E0001'], ['E0002']], 'one experiment per record, capped per week')
        self.assertEqual(len(state['posts']), 1)
        self.assertEqual(len(E.exp_dirs()), 5, 'three starters and the two follow-up controls of the first')
        meta = json.loads(next(d for m, u, h, d in self.requests if u.endswith('/depositions/777')))['metadata']
        self.assertIn('AutoPsych exploratory report E0001', meta['title'])

    def test_daily_cap_then_codex_proposal_and_duplicate_refusal(self):
        E.set_config('max_experiments_per_day', 2)
        self.add_seed(0)
        self.effects['primary'] = 0.0
        self.steps(8)
        self.assertEqual(len(E.exp_dirs()), 2)
        self.assertEqual(self.exp('E0002')['source'], 'codex')
        self.assertEqual(self.exp('E0002')['lane'], 'theory')
        state = E.load_state(self.lab)
        state['started'] = {}
        E.save_state(self.lab, state)
        self.steps(3)
        self.assertEqual(len(E.exp_dirs()), 2, 'a repeated design is refused')
        self.assertIn('already run', E.load_state(self.lab)['proposal_feedback'])

    def test_null_with_failed_manipulation_is_not_called_a_null(self):
        self.add_seed()
        E.set_config('max_experiments_per_day', 1)
        self.effects['primary'] = 0.0
        self.flat_projection = True
        self.steps(4)
        exp = self.exp()
        self.assertEqual(exp['outcome'], 'manipulation-failed')
        text = (E.exp_dir('E0001') / 'paper.md').read_text()
        self.assertIn('Manipulation check failed', text)
        self.assertIn('**FAILED**', text)
        E.decide('E0001', 'approve', 'Arthur')
        self.steps(3)
        self.assertEqual(self.exp()['stage'], 'closed')
        self.assertFalse(self.requests, 'a failed manipulation is listed in the ledger only')

    def test_abort_file_closes_experiment_and_frees_the_seed(self):
        self.add_seed()
        E.set_config('max_experiments_per_day', 5)
        self.steps(1)
        folder = E.exp_dir('E0001')
        E.write_json(folder / 'ABORT.json', {'reason': 'dose calibration hit its ceiling', 'by': 'Claude'})
        self.steps(1)
        self.assertEqual(self.exp('E0001')['outcome'], 'aborted')
        self.assertIn('dose calibration', (folder / 'DEVIATION.md').read_text())
        self.assertEqual(self.exp('E0002')['slug'], self.exp('E0001')['slug'], 'the same design may run again')
        self.steps(6)
        self.assertEqual(self.exp('E0002')['outcome'], 'replicated-two-models')
        self.assertEqual(self.exp('E0001')['stage'], 'closed')

    def test_followup_controls_and_larger_replication(self):
        self.add_seed()
        E.set_config('max_experiments_per_day', 1)
        self.steps(6)
        names = sorted(p.name for p in (E.FAST / 'seeds').glob('00-followup-*'))
        self.assertEqual(names, ['00-followup-E0001-lenctl.json', '00-followup-E0001-lowdose.json'])
        ctl = S.validate(json.loads((E.FAST / 'seeds' / names[0]).read_text()))
        self.assertEqual(ctl['thinking_length'], 'fixed')
        self.assertEqual(ctl['source'], 'followup:E0001')
        self.assertNotEqual(S.design_key(ctl), S.design_key(S.validate(SEED)))
        low = S.validate(json.loads((E.FAST / 'seeds' / names[1]).read_text()))
        self.assertEqual(low['dose'], 0.4)
        pools = H.load_pools(REAL_FAST / 'prompts')
        spec = S.validate(SEED)
        first, rep = S.choose_prompts(spec, pools, 0, 24), S.choose_prompts(spec, pools, 1, 24)
        self.assertEqual((len(first), len(rep)), (16, 24))
        self.assertFalse({p['id'] for p in first} & {p['id'] for p in rep})
        self.assertEqual(S.choose_prompts(spec, pools, 1)[:16], rep[:16])

    def test_codex_and_queued_experiments_take_turns(self):
        E.set_config('alternate_codex', True)
        E.set_config('max_experiments_per_day', 3)
        self.effects['primary'] = 0.0
        for i in (0, 1):
            self.add_seed(i)
        self.steps(8)
        self.assertEqual([self.exp(f'E000{n}')['source'] for n in (1, 2, 3)], ['seed', 'codex', 'seed'])

    def test_tampered_records_are_caught(self):
        self.add_seed()
        self.steps(3)
        folder = E.exp_dir('E0001')
        path = folder / 'runs' / 'primary' / 'cells.jsonl'
        path.write_text(path.read_text().replace('"reply_tokens": 40', '"reply_tokens": 41', 1))
        with self.assertRaises(H.HarnessError):
            E.analyze_run(folder, 'primary')


class TextTests(unittest.TestCase):
    def test_number_check(self):
        results = {'a': {'mean': -1.2345, 'ci95': [-2.0, -0.4567]}, 'n': 16}
        self.assertEqual(paper.unsupported_numbers('mean -1.23, CI -2.0 to -0.46, 16 prompts', results), [])
        self.assertEqual(paper.unsupported_numbers('a drop of 1.2 (1.235)', results), [])
        self.assertEqual(paper.unsupported_numbers('p = 0.03', results), ['0.03'])


if __name__ == '__main__':
    unittest.main()
