"""Harness mechanics on a tiny random model (CPU). Skipped where torch/transformers are not installed.

Checks hooks, caching, templating, calibration, resumption and record integrity end to end.
It says nothing about real models or the Mac's GPU; that is verified by the first real run.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    import torch
    import transformers
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    READY = True
except ImportError:  # pragma: no cover
    READY = False

from codexautopsych.fast import harness as H
from codexautopsych.fast import spec as S
from codexautopsych.fast import stats

FAST = Path(__file__).resolve().parents[1] / 'fastlane'
SEED_FILES = sorted(p for p in (FAST / 'seeds').glob('*.json') if p.name[2] == '-' and not p.name.startswith('00-'))   # the original five
TEMPLATE = ("{% for m in messages %}<|im_start|>{{ m['role'] }}\n{{ m['content'] }}<|im_end|>\n{% endfor %}"
            "{% if add_generation_prompt %}<|im_start|>assistant\n"
            "{% if enable_thinking is defined and not enable_thinking %}<think>\n\n</think>\n\n{% endif %}{% endif %}")


def build_tiny_model(folder):
    corpus = [json.loads(l)['text'] for name in ('provocative', 'neutral', 'extraction', 'calibration')
              for l in (FAST / 'prompts' / f'{name}.jsonl').read_text().splitlines()]
    corpus += list(S.MEASURES.values()) + [a for pair in S.DIRECTIONS.values() for a in pair] + ['0 1 2 3 4 5 6 7 8 9']
    tok = Tokenizer(models.BPE(unk_token='<unk>'))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    special = ['<unk>', '<|im_start|>', '<|im_end|>', '<|endoftext|>', '<think>', '</think>']
    tok.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=600, special_tokens=special,
                                                        initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    fast = transformers.PreTrainedTokenizerFast(tokenizer_object=tok, unk_token='<unk>', eos_token='<|im_end|>',
                                                pad_token='<|endoftext|>')
    fast.chat_template = TEMPLATE
    fast.save_pretrained(folder)
    torch.manual_seed(0)
    config = transformers.LlamaConfig(vocab_size=len(fast), hidden_size=48, intermediate_size=96,
                                      num_hidden_layers=4, num_attention_heads=4, num_key_value_heads=4,
                                      max_position_embeddings=1024, eos_token_id=fast.eos_token_id,
                                      bos_token_id=fast.eos_token_id, pad_token_id=fast.pad_token_id)
    transformers.LlamaForCausalLM(config).save_pretrained(folder)


@unittest.skipUnless(READY, 'torch/transformers not installed')
class TinyModelHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        build_tiny_model(cls.tmp / 'model')
        raw = json.loads(SEED_FILES[3].read_text())   # two directions, two measures
        raw.update(n_prompts=8, max_new_tokens=40)
        (cls.tmp / 'spec.json').write_text(json.dumps(raw))
        cls.spec = S.validate(raw)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_harness(self, out, *extra):
        return H.main(['--spec', str(self.tmp / 'spec.json'), '--out', str(self.tmp / out), '--run-label', 'primary',
                       '--split', '0', '--model', 'tiny', '--model-path', str(self.tmp / 'model'),
                       '--cache', str(self.tmp / 'cache'), '--prompts', str(FAST / 'prompts'),
                       '--lock', str(self.tmp / 'execution.lock'), '--device', 'cpu', '--skip-smoke', *extra])

    def test_full_run_resume_and_analysis(self):
        # 1. a stop file halts before the first cell, after extraction and calibration were cached
        stop = self.tmp / 'STOP'
        stop.write_text('x')
        self.assertEqual(self.run_harness('run', '--stop', str(stop)), 0)
        self.assertFalse((self.tmp / 'run' / 'DONE').exists())
        self.assertEqual(json.loads((self.tmp / 'run' / 'progress.json').read_text())['status'], 'stopped')
        cached = sorted(p.name for p in (self.tmp / 'cache' / 'tiny').iterdir())
        self.assertEqual(len([c for c in cached if c.startswith('direction-')]), 2)
        self.assertEqual(len([c for c in cached if c.startswith('calibration-v2-')]), 2)
        cal = json.loads((self.tmp / 'cache' / 'tiny' / next(c for c in cached if c.startswith('calibration-v2-'))).read_text())
        self.assertIn('censored', cal)
        self.assertIn('reread_projection_median', cal['steps'][0])
        stop.rename(self.tmp / 'STOP.off')

        # 2. the run completes; every planned cell exists exactly once with sane values
        self.assertEqual(self.run_harness('run', '--stop', str(stop)), 0)
        rows = H.verified_rows(self.tmp / 'run' / 'cells.jsonl')
        self.assertEqual(len(rows), 8 * 1 * 4)
        self.assertEqual(len({r['key'] for r in rows}), len(rows))
        for r in rows:
            for m in ('liking_user', 'self_mood'):
                self.assertTrue(0 <= r['ratings'][m]['rating'] <= 9)
                self.assertTrue(0 < r['ratings'][m]['valid_mass'] <= 1)
            self.assertEqual(set(r['projection']), {'hostility', 'sadness'})
            self.assertGreater(r['reply_tokens'], 0)
        meta = json.loads((self.tmp / 'run' / 'meta.json').read_text())
        self.assertEqual(meta['layer_index'], 2)
        self.assertEqual(meta['total_cells'], 32)
        self.assertGreater(meta['steering_size']['hostility'], 0)
        self.assertEqual(meta['steering_size']['random'], meta['steering_size']['hostility'])

        # 3. arms share one seed per prompt, so a changed reply can only come from the steering;
        #    no steering reproduces the saved reply exactly
        by = {r['key']: r for r in rows}
        first = rows[0]['prompt_id']
        self.assertEqual(len({by[f'{first}|plain|{a}']['seed'] for a in S.arms(self.spec)}), 1)
        changed = sum(by[f"{r['prompt_id']}|plain|none"]['reply_text'] != r['reply_text']
                      for r in rows if r['arm'] == 'hostility')
        self.assertGreater(changed, 0)
        backend = H.Backend('tiny', str(self.tmp / 'model'), 'cpu')
        prompt = next(p for p in S.choose_prompts(self.spec, H.load_pools(FAST / 'prompts'), 0) if p['id'] == first)
        ids = backend.ids(backend.messages('', prompt['text']))
        again = backend.generate(ids, S.seed(self.spec['slug'], 'primary', first, 'plain'), 40)
        self.assertEqual(backend.tok.decode(again, skip_special_tokens=True).strip(),
                         by[f'{first}|plain|none']['reply_text'])

        # 4. rerunning a finished run repeats nothing; analysis runs on real harness records
        before = (self.tmp / 'run' / 'cells.jsonl').read_text()
        self.assertEqual(self.run_harness('run'), 0)
        self.assertEqual((self.tmp / 'run' / 'cells.jsonl').read_text(), before)
        out = stats.analyze(self.spec, rows)
        self.assertIn(out['primary']['verdict'], ('effect', 'null', 'invalid'))
        self.assertEqual(out['quality']['expected_cells'], 32)

        # 5. a run folder cannot be reused for a different spec
        other = json.loads((self.tmp / 'spec.json').read_text())
        other['dose'] = 0.5
        (self.tmp / 'spec.json').write_text(json.dumps(other))
        self.assertEqual(self.run_harness('run'), 1)
        self.assertIn('different spec', json.loads((self.tmp / 'run' / 'FAILED.json').read_text())['error'])

    def test_thinking_mode_keeps_the_thinking_block_in_view(self):
        raw = json.loads(SEED_FILES[2].read_text())      # filler turns 0 and 2
        raw.update(n_prompts=8, max_new_tokens=40, slug='thinking-check')
        (self.tmp / 'think.json').write_text(json.dumps(raw))
        code = H.main(['--spec', str(self.tmp / 'think.json'), '--out', str(self.tmp / 'think'), '--run-label', 'primary',
                       '--split', '0', '--model', 'tiny', '--model-path', str(self.tmp / 'model'),
                       '--cache', str(self.tmp / 'cache'), '--prompts', str(FAST / 'prompts'), '--device', 'cpu',
                       '--thinking-cap', '24', '--skip-smoke'])
        self.assertEqual(code, 0)
        rows = H.verified_rows(self.tmp / 'think' / 'cells.jsonl')
        self.assertEqual(len(rows), 8 * 2 * 3)
        for r in rows:
            self.assertGreater(r['thinking_tokens'], 0)
            self.assertLessEqual(r['thinking_tokens'], 24 + 3)
            self.assertTrue(0 <= r['ratings']['liking_user']['rating'] <= 9)
        meta = json.loads((self.tmp / 'think' / 'meta.json').read_text())
        self.assertEqual(meta['thinking_cap'], 24)
        self.assertGreater(meta['direction_separation']['hostility'], 0)
        backend = H.Backend('tiny', str(self.tmp / 'model'), 'cpu')
        backend.setup_thinking()
        text = backend.tok.decode(backend.user_turn('How are you?'), skip_special_tokens=False)
        self.assertTrue(text.startswith('<|im_start|>user'))
        self.assertTrue(text.endswith('<think>\n\n</think>\n\n'))

    def test_fixed_length_thinking_and_smoke_check(self):
        raw = json.loads(SEED_FILES[0].read_text())
        raw.update(n_prompts=8, max_new_tokens=40, slug='fixed-length-check', thinking_length='fixed',
                   conditions=[{'name': 'plain', 'system': ''}],
                   primary={'measure': 'liking_user', 'type': 'steer', 'direction': 'hostility', 'condition': 'plain'})
        (self.tmp / 'fixed.json').write_text(json.dumps(raw))
        code = H.main(['--spec', str(self.tmp / 'fixed.json'), '--out', str(self.tmp / 'fixed'), '--run-label', 'rep_same',
                       '--split', '1', '--rep-prompts', '12', '--model', 'tiny', '--model-path', str(self.tmp / 'model'),
                       '--cache', str(self.tmp / 'cache'), '--prompts', str(FAST / 'prompts'), '--device', 'cpu',
                       '--thinking-cap', '20', '--skip-smoke'])
        self.assertEqual(code, 0)
        rows = H.verified_rows(self.tmp / 'fixed' / 'cells.jsonl')
        self.assertEqual(len(rows), 12 * 3)
        self.assertEqual({r['thinking_tokens'] for r in rows}, {20 + len(H.Backend('tiny', str(self.tmp / 'model'), 'cpu').tok.encode('</think>\n\n', add_special_tokens=False))})
        with self.assertRaises(H.SmokeFailure):
            H.smoke_check(S.validate(raw), [dict(r, ratings={'liking_user': {'rating': 0.1, 'valid_mass': 0.9}}) for r in rows[:6]])
        with self.assertRaises(H.SmokeFailure):
            H.smoke_check(S.validate(raw), [dict(r, ratings={'liking_user': {'rating': 5.0, 'valid_mass': 0.1}}) for r in rows[:6]])

    def test_reference_direction_replaces_extraction(self):
        torch.save({'vector': torch.randn(48), 'metadata': {}}, self.tmp / 'ref.pt')
        torch.save({'vector': torch.randn(48), 'metadata': {}}, self.tmp / 'ref_random.pt')
        raw = json.loads(SEED_FILES[0].read_text())
        raw.update(n_prompts=8, max_new_tokens=40, slug='reference-check', conditions=[{'name': 'plain', 'system': ''}],
                   primary={'measure': 'liking_user', 'type': 'steer', 'direction': 'hostility', 'condition': 'plain'})
        (self.tmp / 'ref.json').write_text(json.dumps(raw))
        ref = {'hostility': {'vector': str(self.tmp / 'ref.pt'), 'random': str(self.tmp / 'ref_random.pt'),
                             'size': 3.0, 'token_norm': 10.0, 'label': 'reference label'}}
        code = H.main(['--spec', str(self.tmp / 'ref.json'), '--out', str(self.tmp / 'ref'), '--run-label', 'primary',
                       '--split', '0', '--model', 'tiny', '--model-path', str(self.tmp / 'model'),
                       '--cache', str(self.tmp / 'refcache'), '--prompts', str(FAST / 'prompts'), '--device', 'cpu',
                       '--thinking-cap', '16', '--skip-smoke', '--reference', json.dumps(ref)])
        self.assertEqual(code, 0)
        meta = json.loads((self.tmp / 'ref' / 'meta.json').read_text())
        self.assertEqual(meta['calibration']['hostility']['source'], 'reference label')
        self.assertAlmostEqual(meta['steering_size']['hostility'], 3.0, places=3)
        self.assertEqual(meta['steering_size']['random'], meta['steering_size']['hostility'])
        self.assertFalse((self.tmp / 'refcache').exists(), 'nothing extracted or calibrated')
        self.assertEqual(len(H.verified_rows(self.tmp / 'ref' / 'cells.jsonl')), 24)

    def test_lock_busy_exits_without_running(self):
        import fcntl
        with open(self.tmp / 'execution.lock', 'a+') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.run_harness('busy'), H.EXIT_BUSY)
        self.assertFalse((self.tmp / 'busy' / 'cells.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
