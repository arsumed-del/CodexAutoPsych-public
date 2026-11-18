"""Model harness for one fast-lane run. The only fast-lane code that loads a model.

Run with the experiment Python (torch + transformers), from the repository root:

    python -m codexautopsych.fast.harness --spec SPEC.json --out RUN_DIR --run-label primary \
        --split 0 --model qwen2.5-3b --cache CACHE_DIR --prompts fastlane/prompts --lock LOCKFILE

What one cell does (identical for every experiment; only the spec's values vary):
  1. Build the conversation: [condition system text] + user message.
  2. Generate the assistant reply. In a direction arm, a fixed vector is added to the
     residual stream at the middle layer at every position while the reply is written;
     the random arm adds a random vector of the same size; the none arm adds nothing.
  3. Steering is switched OFF. The visible conversation (with the reply as plain text,
     plus any filler turns) is re-read from scratch and each measure question is asked.
     The rating is the expected digit (0-9) under the model's next-token distribution.
So every measure asks: does a state that was present only while the reply was written
still influence a later judgment through the text alone?

Records are append-only with a per-row hash; a stopped or crashed run resumes without
repeating cells. Nothing here calls a network service, Codex or Claude.
"""
import os
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')

import argparse
import fcntl
import gc
import json
import math
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import spec as S

TEMPERATURE, TOP_P = 0.7, 0.95
EXTRACT_TOKENS, CALIBRATION_TOKENS = 40, 48
CANDIDATES = [0.01, 0.02, 0.04, 0.06, 0.08, 0.12, 0.16, 0.24, 0.32, 0.48, 0.64, 0.96, 1.28, 1.92, 2.56]
PPL_RATIO_MAX, DISTINCT_MIN = 4.0, 0.6     # same coherence rule as the Studies 1-2 preregistration (v2.2)
RANDOM_SEED = 777
END_TOKENS = ('<|im_end|>', '<|eot_id|>', '<end_of_turn>', '<|end|>', '<|endoftext|>')
FILLER = [('Thanks. One unrelated thing: what is 17 plus 26?', '17 plus 26 is 43.'),
          ('And what is the capital of Australia?', 'The capital of Australia is Canberra.')]
EXIT_BUSY = 75


OWNER_RUN_PATTERNS = [r'study\d+[./](src|analysis)', r'affect-suppression-steering/(src|study)',
                      r'-m src\.(run_|analyze)', r'mlx_lm']


def competing(patterns=None):
    """Command lines of other model experiments on this machine (the owner's own studies come first)."""
    import re
    import subprocess
    try:
        out = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rx = [re.compile(p) for p in (patterns or OWNER_RUN_PATTERNS)]
    found = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(' ')
        if 'codexautopsych' in cmd or pid == str(os.getpid()):
            continue
        if any(r.search(cmd) for r in rx):
            found.append(cmd[:80])
    return found


class HarnessError(RuntimeError):
    pass


class SmokeFailure(HarnessError):
    """The first few cells show the measurement is unusable; retrying would not help."""


SMOKE_CELLS = 6


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def verified_rows(path):
    """Rows whose hash matches. A torn final line (crash mid-write) is dropped, never trusted."""
    path = Path(path)
    if not path.exists():
        return []
    rows, raw = [], path.read_text()
    lines = raw.split('\n')
    if lines and lines[-1] != '':            # no trailing newline: incomplete write
        torn = lines.pop()
        path.with_name(path.name + '.torn').write_text(torn)
        path.write_text('\n'.join(lines) + ('\n' if lines else ''))
    for i, line in enumerate(l for l in lines if l.strip()):
        row = json.loads(line)
        body = {k: v for k, v in row.items() if k != 'row_sha256'}
        if S.sha(body) != row.get('row_sha256'):
            raise HarnessError(f'Record integrity failure in {path.name} line {i + 1}')
        rows.append(row)
    return rows


def append_row(path, row):
    row = dict(row)
    row['row_sha256'] = S.sha(row)
    with Path(path).open('a') as fh:
        fh.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        fh.flush()
        os.fsync(fh.fileno())
    return row


def write_json(path, obj):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    tmp.replace(path)


def load_pools(folder):
    folder = Path(folder)
    return {name: read_jsonl(folder / f'{name}.jsonl')
            for name in ('provocative', 'neutral', 'extraction', 'calibration')}


def resolve(source):
    """A Hugging Face id -> its local snapshot folder when one is cached (works offline without refs)."""
    if os.path.isdir(source) or '/' not in source:
        return source
    hub = Path(os.environ.get('HF_HOME', Path.home() / '.cache' / 'huggingface')) / 'hub'
    snaps = hub / ('models--' + source.replace('/', '--')) / 'snapshots'
    if snaps.is_dir():
        full = sorted(p for p in snaps.iterdir() if (p / 'config.json').exists())
        if full:
            return str(full[-1])
    return source


class Backend:
    """One local open-weight chat model with mid-layer additive steering."""

    def __init__(self, model_key, model_path=None, device='auto'):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        info = S.MODELS.get(model_key) or {'id': model_key, 'system_role': True}
        source = resolve(model_path or info['id'])
        self.key, self.source, self.system_role = model_key, source, info['system_role']
        if device == 'auto':
            device = 'mps' if torch.backends.mps.is_available() else 'cpu'
        self.device = device
        self.dtype = torch.bfloat16 if device == 'mps' else torch.float32
        self.tok = AutoTokenizer.from_pretrained(source)
        try:
            model = AutoModelForCausalLM.from_pretrained(source, dtype=self.dtype)
        except TypeError:
            model = AutoModelForCausalLM.from_pretrained(source, torch_dtype=self.dtype)
        self.model = model.to(device).eval()
        inner = getattr(self.model, 'model', None)
        layers = getattr(inner, 'layers', None)
        if layers is None:
            raise HarnessError('Unsupported architecture: no model.model.layers')
        self.n_layers = len(layers)
        self.layer_index = round(0.5 * self.n_layers)
        self.layer = layers[min(self.layer_index, self.n_layers - 1)]
        eos = self.model.generation_config.eos_token_id
        eos = set(eos if isinstance(eos, list) else [eos]) - {None}
        if self.tok.eos_token_id is not None:
            eos.add(self.tok.eos_token_id)
        vocab = self.tok.get_vocab()
        eos |= {vocab[t] for t in END_TOKENS if t in vocab}
        if not eos:
            raise HarnessError('No end-of-turn token known for this model')
        self.eos = eos
        self.digits = []
        for d in range(10):
            ids = self.tok.encode(str(d), add_special_tokens=False)
            if not ids or self.tok.decode([ids[-1]]).strip() != str(d):
                raise HarnessError(f'Digit {d} is not a single token for this model')
            self.digits.append(ids[-1])
        self.peak = 0
        # Thinking mode: the model writes a private reasoning block before its answer (Qwen3-style).
        self.think_close = vocab.get('</think>')
        template = self.tok.chat_template if isinstance(self.tok.chat_template, str) else ''
        self.can_think = self.think_close is not None and 'enable_thinking' in template
        self.turn_end = None

    def setup_thinking(self):
        """Token pieces used to continue a conversation from the exact tokens the model wrote."""
        if not self.can_think:
            raise HarnessError('This model has no thinking mode')
        probe = self.ids([{'role': 'user', 'content': 'x'}], thinking=False)
        two = self.ids([{'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'},
                        {'role': 'user', 'content': 'x'}], thinking=False)
        if two[-len(probe):] != probe:
            raise HarnessError('Chat template adds text to a lone user turn; thinking mode unsupported')
        end = self.tok.encode('<|im_end|>\n', add_special_tokens=False)
        if two[-len(probe) - len(end):-len(probe)] != end:
            raise HarnessError('Unexpected end-of-turn tokens; thinking mode unsupported')
        self.turn_end = end
        self.forced_close = self.tok.encode('</think>\n\n', add_special_tokens=False)

    def user_turn(self, text):
        """Tokens for a new user turn plus the assistant prefix with an empty thinking block."""
        return self.ids([{'role': 'user', 'content': text}], thinking=False)

    def generate_thinking(self, prompt_ids, seed, thinking_cap, answer_cap, vector=None, scale=0.0, fixed=False):
        """Thinking block (closed by the model or forced at the cap), then the answer.
        Returns (all new tokens without the final end token, number of thinking tokens, forced)."""
        torch = self.torch
        gen = torch.Generator().manual_seed(int(seed))
        tokens, cache, forced = [], None, False

        def forward(ids):
            nonlocal cache
            out = self.model(input_ids=self.tensor(ids), past_key_values=cache, use_cache=True)
            cache = out.past_key_values
            self._track()
            return out.logits[0, -1].float().cpu()

        banned = sorted(self.eos | {self.think_close})

        def sample(raw, keep_thinking=False):
            if keep_thinking:              # fixed-length thinking: the block may not end before the cap
                raw = raw.clone()
                raw[banned] = -math.inf
            values, indices = (raw / TEMPERATURE).sort(descending=True)
            remove = values.softmax(-1).cumsum(-1) > TOP_P
            remove[1:] = remove[:-1].clone()
            remove[0] = False
            values[remove] = -math.inf
            return indices[torch.multinomial(values.softmax(-1), 1, generator=gen)].item()
        with torch.inference_mode(), self.steering(vector, scale):
            raw = forward(prompt_ids)
            closed = False
            for _ in range(thinking_cap):
                token = sample(raw, fixed)
                if token in self.eos:          # ended without closing: close it for the model
                    break
                tokens.append(token)
                raw = forward([token])
                if token == self.think_close:
                    closed = True
                    break
            if not closed:
                forced = True
                tokens.extend(self.forced_close)
                raw = forward(self.forced_close)
            n_think = len(tokens)
            for _ in range(answer_cap):
                token = sample(raw)
                if token in self.eos:
                    break
                tokens.append(token)
                raw = forward([token])
        del cache
        return tokens, n_think, forced

    # ---- conversation -> token ids -------------------------------------------------
    def messages(self, system, user):
        if system and self.system_role:
            return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
        return [{'role': 'user', 'content': f'{system}\n\n{user}' if system else user}]

    def ids(self, messages, thinking=False):
        out = self.tok.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                           return_dict=False, enable_thinking=thinking)
        if hasattr(out, 'keys'):
            out = out['input_ids']
        if hasattr(out, 'tolist'):
            out = out.tolist()
        if out and isinstance(out[0], list):
            out = out[0]
        return list(out)

    def tensor(self, ids):
        return self.torch.tensor([ids], device=self.device, dtype=self.torch.long)

    # ---- steering and capture ------------------------------------------------------
    @contextmanager
    def steering(self, vector=None, scale=0.0):
        handle = None
        if vector is not None and scale:
            delta = (scale * vector.to(self.device, dtype=self.torch.float32)).to(self.dtype)

            def hook(module, args, output):
                if isinstance(output, tuple):
                    return (output[0] + delta,) + output[1:]
                return output + delta
            handle = self.layer.register_forward_hook(hook)
        try:
            yield
        finally:
            if handle is not None:
                handle.remove()

    def _track(self):
        if self.device == 'mps':
            self.peak = max(self.peak, self.torch.mps.driver_allocated_memory())

    def generate(self, prompt_ids, seed, max_new_tokens, vector=None, scale=0.0):
        """Sample a reply (temperature 0.7, top-p 0.95). Returns the new token ids, end token removed."""
        torch = self.torch
        gen = torch.Generator().manual_seed(int(seed))
        tokens, cache = [], None
        inputs = self.tensor(prompt_ids)
        with torch.inference_mode(), self.steering(vector, scale):
            for _ in range(max_new_tokens):
                out = self.model(input_ids=inputs, past_key_values=cache, use_cache=True)
                cache = out.past_key_values
                raw = out.logits[0, -1].float().cpu()
                values, indices = (raw / TEMPERATURE).sort(descending=True)
                remove = values.softmax(-1).cumsum(-1) > TOP_P
                remove[1:] = remove[:-1].clone()
                remove[0] = False
                values[remove] = -math.inf
                token = indices[torch.multinomial(values.softmax(-1), 1, generator=gen)].item()
                if token in self.eos:
                    break
                tokens.append(token)
                inputs = self.tensor([token])
                self._track()
        del cache
        return tokens

    def reread(self, ids, start, end):
        """Unsteered forward over ids. Returns (mean layer activation over [start,end), mean
        token norm there, mean negative log-likelihood of those tokens)."""
        torch = self.torch
        box = {}

        def hook(module, args, output):
            h = output[0] if isinstance(output, tuple) else output
            box['h'] = h[0, start:end].detach().float().cpu()
        handle = self.layer.register_forward_hook(hook)
        try:
            with torch.inference_mode():
                logits = self.model(input_ids=self.tensor(ids), use_cache=False).logits[0]
        finally:
            handle.remove()
        self._track()
        if end <= start:
            return None, None, None
        logp = logits[start - 1:end - 1].float().log_softmax(-1).cpu()
        targets = torch.tensor(ids[start:end]).unsqueeze(-1)
        nll = -logp.gather(-1, targets).mean().item()
        h = box['h']
        return h.mean(0), h.norm(dim=-1).mean().item(), nll

    def rate(self, ids):
        """Expected digit 0-9 at the next position, and the probability mass on the digits."""
        torch = self.torch
        with torch.inference_mode():
            logits = self.model(input_ids=self.tensor(ids), use_cache=False).logits[0, -1]
        probs = logits.float().softmax(-1).cpu()[self.digits]
        self._track()
        mass = probs.sum().item()
        rating = (probs * torch.arange(10, dtype=probs.dtype)).sum().item() / mass if mass > 0 else float('nan')
        return rating, mass

    def free(self):
        gc.collect()
        if self.device == 'mps':
            self.torch.mps.empty_cache()


class Classifier:
    """Fixed emotion classifier (same model as Studies 4-8). Descriptive check on what was written."""

    def __init__(self, source):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        source = resolve(source)
        self.tok = AutoTokenizer.from_pretrained(source)
        try:      # this classifier ships only the older weight format
            self.model = AutoModelForSequenceClassification.from_pretrained(source, use_safetensors=False).eval()
        except (OSError, TypeError, ValueError):
            self.model = AutoModelForSequenceClassification.from_pretrained(source).eval()

    def score(self, text):
        if not text.strip():
            return {'hostile': None, 'warm': None}
        with self.torch.inference_mode():
            inputs = self.tok(text, return_tensors='pt', truncation=True, max_length=512)
            probs = self.model(**inputs).logits[0].float().softmax(-1).tolist()
        labels = {self.model.config.id2label[i].lower(): p for i, p in enumerate(probs)}
        return {'hostile': round(labels.get('anger', 0) + labels.get('disgust', 0), 6),
                'warm': round(labels.get('joy', 0), 6)}


def distinct_ratio(tokens):
    return len(set(tokens)) / len(tokens) if tokens else 0.0


def median(values):
    values = sorted(values)
    n = len(values)
    return (values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2) if n else float('nan')


def extract_direction(backend, direction, scenarios, cache_dir, log):
    """Generation-based difference of means at the steering layer. Cached per model and wording."""
    torch = backend.torch
    ident = S.sha({'pos': direction['positive'], 'neg': direction['negative'], 'layer': backend.layer_index,
                   'scenarios': [s['text'] for s in scenarios], 'tokens': EXTRACT_TOKENS, 'v': 1})[:16]
    path = Path(cache_dir) / f'direction-{ident}.pt'
    if path.exists():
        saved = torch.load(path)
        return saved['unit'], saved['norm'], ident, saved['raw_size'] / saved['norm']
    log(f"extracting direction '{direction['name']}' ({len(scenarios)} scenarios)")
    means = {'positive': [], 'negative': []}
    norms = []
    for i, sc in enumerate(scenarios):
        for side in ('positive', 'negative'):
            prompt = backend.ids(backend.messages(direction[side], sc['text']))
            reply = backend.generate(prompt, S.seed('extract', ident, sc['id'], side), EXTRACT_TOKENS)
            if len(reply) < 4:
                continue
            mean, norm, _ = backend.reread(prompt + reply, len(prompt), len(prompt) + len(reply))
            means[side].append(mean)
            if side == 'negative':
                norms.append(norm)
        backend.free()
    if len(means['positive']) < 6 or len(means['negative']) < 6:
        raise HarnessError('Direction extraction produced too few usable replies')
    diff = torch.stack(means['positive']).mean(0) - torch.stack(means['negative']).mean(0)
    size = diff.norm().item()
    if not math.isfinite(size) or size == 0:
        raise HarnessError('Direction extraction failed (zero or non-finite difference)')
    unit, norm = diff / size, sum(norms) / len(norms)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    torch.save({'unit': unit, 'norm': norm, 'raw_size': size, 'name': direction['name']}, path)
    return unit, norm, ident, size / norm


def calibrate(backend, unit, norm, ident, prompts, cache_dir, log, thinking_cap=0):
    """Largest steering size (fraction of the mean residual norm) that keeps replies coherent."""
    path = Path(cache_dir) / (f'calibration-v3-think{thinking_cap}-{ident}.json' if thinking_cap
                              else f'calibration-v2-{ident}.json')

    def write(ids, seed, vector=None, scale=0.0):
        if thinking_cap:
            return backend.generate_thinking(ids, seed, min(thinking_cap, 128), CALIBRATION_TOKENS, vector, scale)[0]
        return backend.generate(ids, seed, CALIBRATION_TOKENS, vector, scale)
    if path.exists():
        return json.loads(path.read_text())
    log('calibrating dose')
    base = []
    for p in prompts:
        ids = backend.ids(backend.messages('', p['text']), thinking=bool(thinking_cap))
        reply = write(ids, S.seed('calibrate', ident, p['id']))
        mean, _, nll = backend.reread(ids + reply, len(ids), len(ids) + len(reply))
        base.append({'ids': ids, 'nll': nll, 'distinct': distinct_ratio(reply),
                     'projection': None if mean is None else float((mean @ unit).item()) / norm})
    if any(b['nll'] is None for b in base):
        raise HarnessError('Calibration baseline produced an empty reply')
    d0 = median([b['distinct'] for b in base])
    steps, alpha_max = [], None
    for cand in CANDIDATES:
        ratios, distincts, projections = [], [], []
        for p, b in zip(prompts, base):
            # same seed as the unsteered baseline, so only the steering differs
            reply = write(b['ids'], S.seed('calibrate', ident, p['id']), unit, cand * norm)
            mean, _, nll = backend.reread(b['ids'] + reply, len(b['ids']), len(b['ids']) + len(reply))
            ratios.append(math.exp(min(nll - b['nll'], 50)) if nll is not None else float('inf'))
            distincts.append(distinct_ratio(reply))
            if mean is not None:
                projections.append(float((mean @ unit).item()) / norm)
        backend.free()
        ok = median(ratios) <= PPL_RATIO_MAX and median(distincts) >= DISTINCT_MIN * d0
        steps.append({'alpha': cand, 'ppl_ratio_median': round(median(ratios), 4),
                      'distinct_median': round(median(distincts), 4),
                      'reread_projection_median': round(median(projections), 4) if projections else None,
                      'pass': ok})
        if not ok:
            break
        alpha_max = cand
    if alpha_max is None:
        raise HarnessError('Calibration failed: the smallest steering size already breaks coherence')
    result = {'alpha_max': alpha_max, 'norm': norm, 'baseline_distinct_median': round(d0, 4), 'steps': steps,
              'baseline_reread_projection_median': round(median([b['projection'] for b in base]), 4),
              'censored': alpha_max == CANDIDATES[-1],
              'rule': f'largest candidate with all smaller passing: median perplexity ratio <= {PPL_RATIO_MAX}, '
                      f'median distinct-token ratio >= {DISTINCT_MIN} x baseline'}
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    write_json(path, result)
    return result


def random_unit(backend, size):
    torch = backend.torch
    v = torch.randn(size, generator=torch.Generator().manual_seed(RANDOM_SEED))
    return v / v.norm()


def run_cell(backend, spec, cell, run_label, vectors, scales, thinking_cap=0, classifier=None):
    started = time.monotonic()
    cond, arm, prompt = cell['condition'], cell['arm'], cell['prompt']
    msgs = backend.messages(cond['system'], prompt['text'])
    prompt_ids = backend.ids(msgs, thinking=bool(thinking_cap))
    # common random numbers: every arm of the same prompt and condition samples with the same seed,
    # so arms differ only by the steering (an arm with no effective steering reproduces the none arm)
    seed = S.seed(spec['slug'], run_label, prompt['id'], cond['name'])
    vector, scale = vectors.get(arm), scales.get(arm, 0.0)
    thinking_text, n_think, forced = '', 0, False
    if thinking_cap:
        reply, n_think, forced = backend.generate_thinking(prompt_ids, seed, thinking_cap,
                                                           spec['max_new_tokens'], vector, scale,
                                                           fixed=spec.get('thinking_length') == 'fixed')
        thinking_text = backend.tok.decode(reply[:n_think], skip_special_tokens=True).strip()
        text = backend.tok.decode(reply[n_think:], skip_special_tokens=True).strip()
    else:
        reply = backend.generate(prompt_ids, seed, spec['max_new_tokens'], vector, scale)
        text = backend.tok.decode(reply, skip_special_tokens=True).strip()
    mean, _, nll = backend.reread(prompt_ids + reply, len(prompt_ids), len(prompt_ids) + len(reply))
    projection = {}
    for d in spec['directions']:
        projection[d['name']] = (None if mean is None else
                                 round(float((mean @ vectors[d['name']]).item()) / scales['_norm'], 6))
    ratings = {}
    if thinking_cap:
        # Continue from the exact tokens the model wrote, so its thinking stays in view (steering off).
        history = prompt_ids + reply + backend.turn_end
        for q, a in FILLER[:cond['filler_turns']]:
            history += backend.user_turn(q) + backend.tok.encode(a, add_special_tokens=False) + backend.turn_end
        for m in spec['measures']:
            rating, mass = backend.rate(history + backend.user_turn(m['question']))
            ratings[m['name']] = {'rating': round(rating, 6), 'valid_mass': round(mass, 6)}
    else:
        convo = msgs + [{'role': 'assistant', 'content': text}]
        for q, a in FILLER[:cond['filler_turns']]:
            convo += [{'role': 'user', 'content': q}, {'role': 'assistant', 'content': a}]
        for m in spec['measures']:
            rating, mass = backend.rate(backend.ids(convo + [{'role': 'user', 'content': m['question']}]))
            ratings[m['name']] = {'rating': round(rating, 6), 'valid_mass': round(mass, 6)}
    emotion = {'thinking': classifier.score(thinking_text), 'reply': classifier.score(text)} if classifier else None
    return {'key': cell['key'], 'prompt_id': prompt['id'], 'prompt_type': prompt.get('type'),
            'condition': cond['name'], 'arm': arm, 'seed': seed, 'thinking_text': thinking_text,
            'thinking_tokens': n_think, 'thinking_forced_close': forced, 'reply_text': text,
            'reply_tokens': len(reply), 'reply_truncated': len(reply) - n_think >= spec['max_new_tokens'],
            'distinct_ratio': round(distinct_ratio(reply), 6), 'reply_nll': None if nll is None else round(nll, 6),
            'projection': projection, 'emotion': emotion, 'ratings': ratings,
            'seconds': round(time.monotonic() - started, 2)}


def smoke_check(spec, rows):
    """After the first few cells: is the primary rating usable at all? Stops a wasted hour early."""
    name = spec['primary']['measure']
    ratings = [r['ratings'][name]['rating'] for r in rows]
    mass = sum(r['ratings'][name]['valid_mass'] for r in rows) / len(rows)
    if mass < 0.5:
        raise SmokeFailure(f'Smoke check: the model is not answering with digits (digit probability {mass:.2f})')
    if all(x <= 0.5 for x in ratings) or all(x >= 8.5 for x in ratings):
        raise SmokeFailure(f'Smoke check: every early rating sits at the end of the scale ({min(ratings):.1f}-{max(ratings):.1f})')
    if all(not r['reply_text'] for r in rows):
        raise SmokeFailure('Smoke check: every early reply is empty')


def run(args, log):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    spec = S.validate(json.loads(Path(args.spec).read_text()))
    pools = load_pools(args.prompts)
    prompts = S.choose_prompts(spec, pools, args.split, args.rep_prompts or None)
    plan = S.cells(spec, prompts)
    meta_path = out / 'meta.json'
    identity = {'spec_sha256': S.sha(spec), 'run_label': args.run_label, 'split': args.split,
                'reference': bool(args.reference),
                'thinking_cap': args.thinking_cap,
                'model': args.model, 'prompt_ids': [p['id'] for p in prompts]}
    if meta_path.exists():
        old = json.loads(meta_path.read_text())
        if {k: old.get(k, False if k == 'reference' else None) for k in identity} != identity:
            raise HarnessError('This run folder belongs to a different spec, model or split')
    done = {r['key'] for r in verified_rows(out / 'cells.jsonl')}
    stray = done - {c['key'] for c in plan}
    if stray:
        raise HarnessError(f'Saved cells not in the design: {sorted(stray)[:3]}')
    if (out / 'DONE').exists() and len(done) == len(plan):
        return 'complete'

    backend = Backend(args.model, args.model_path, args.device)
    if args.thinking_cap:
        backend.setup_thinking()
    classifier = Classifier(args.classifier) if args.classifier else None
    separation = {}
    import torch
    import transformers
    cache = Path(args.cache) / args.model.replace('/', '_')
    vectors, calibrations, idents = {}, {}, {}
    norm = None
    reference = json.loads(args.reference) if args.reference else {}
    random_vector = None
    for d in spec['directions']:
        ref = reference.get(d['name'])
        if ref and (d['positive'], d['negative']) == S.DIRECTIONS.get(d['name']):
            # a previously validated direction and steering size (read-only), instead of extracting a new one
            saved = torch.load(os.path.expanduser(ref['vector']), weights_only=True)
            unit = saved['vector'].float()
            unit = unit / unit.norm()
            d_norm, ident = float(ref['token_norm']), 'reference-' + S.sha(ref)[:12]
            vectors[d['name']], idents[d['name']] = unit, ident
            calibrations[d['name']] = {'alpha_max': 1.0, 'norm': float(ref['size']) / 0.75, 'censored': False,
                                       'steps': [], 'source': ref['label']}
            if d['name'] == spec['primary']['direction'] and ref.get('random'):
                random_vector = torch.load(os.path.expanduser(ref['random']), weights_only=True)['vector'].float()
                random_vector = random_vector / random_vector.norm()
            log(f"using reference direction for '{d['name']}': {ref['label']}")
            if d['name'] == spec['primary']['direction']:
                norm = d_norm
            continue
        unit, d_norm, ident, separation[d['name']] = extract_direction(backend, d, pools['extraction'], cache, log)
        vectors[d['name']], idents[d['name']] = unit, ident
        calibrations[d['name']] = calibrate(backend, unit, d_norm, ident, pools['calibration'], cache, log,
                                              args.thinking_cap)
        if d['name'] == spec['primary']['direction']:
            norm = d_norm
    vectors[S.ARM_RANDOM] = (random_vector if random_vector is not None else
                             random_unit(backend, vectors[spec['primary']['direction']].shape[0]))
    scales = {'_norm': norm}
    for d in spec['directions']:
        scales[d['name']] = spec['dose'] * calibrations[d['name']]['alpha_max'] * calibrations[d['name']]['norm']
    scales[S.ARM_RANDOM] = scales[spec['primary']['direction']]
    meta = dict(identity, started=utc(), device=backend.device, dtype=str(backend.dtype),
                model_source=backend.source, layer_index=backend.layer_index, n_layers=backend.n_layers,
                torch=torch.__version__, transformers=transformers.__version__, python=sys.version.split()[0],
                direction_ids=idents, calibration=calibrations, thinking_cap=args.thinking_cap,
                direction_separation={k: round(v, 6) for k, v in separation.items()},
                classifier=args.classifier,
                steering_size={k: round(v, 4) for k, v in scales.items()},
                sampling={'temperature': TEMPERATURE, 'top_p': TOP_P}, total_cells=len(plan))
    if not meta_path.exists():
        write_json(meta_path, meta)

    t0, n0 = time.monotonic(), len(done)
    for cell in plan:
        if cell['key'] in done:
            continue
        if args.stop and Path(args.stop).exists():
            write_json(out / 'progress.json', {'status': 'stopped', 'done': len(done), 'total': len(plan), 'updated': utc()})
            log('stop requested; stopping after the last finished cell')
            return 'stopped'
        if competing():
            write_json(out / 'progress.json', {'status': 'yielded', 'done': len(done), 'total': len(plan), 'updated': utc()})
            log('another experiment started on this Mac; yielding (will resume where it left off)')
            return 'yielded'
        row = run_cell(backend, spec, cell, args.run_label, vectors, scales, args.thinking_cap, classifier)
        append_row(out / 'cells.jsonl', row)
        done.add(cell['key'])
        backend.free()
        if len(done) == min(SMOKE_CELLS, len(plan)) and not args.skip_smoke:
            smoke_check(spec, verified_rows(out / 'cells.jsonl'))
        rate = (time.monotonic() - t0) / max(len(done) - n0, 1)
        eta = int((len(plan) - len(done)) * rate / 60)
        write_json(out / 'progress.json', {'status': 'running', 'done': len(done), 'total': len(plan),
                                           'eta_minutes': eta, 'updated': utc()})
        log(f"{len(done)}/{len(plan)} cells; last {row['seconds']}s; about {eta} min left")
    write_json(out / 'DONE', {'finished': utc(), 'cells': len(done), 'peak_memory_bytes': backend.peak})
    write_json(out / 'progress.json', {'status': 'complete', 'done': len(done), 'total': len(plan), 'updated': utc()})
    return 'complete'


def main(argv=None):
    p = argparse.ArgumentParser(description='Fast-lane model harness (one run of one spec)')
    p.add_argument('--spec', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--run-label', required=True)
    p.add_argument('--split', type=int, choices=(0, 1), default=0)
    p.add_argument('--model', required=True)
    p.add_argument('--model-path')
    p.add_argument('--cache', required=True)
    p.add_argument('--prompts', required=True)
    p.add_argument('--lock')
    p.add_argument('--stop', help='Stop file: the run stops after the current cell when it exists')
    p.add_argument('--device', default='auto')
    p.add_argument('--thinking-cap', type=int, default=0, help='Tokens of private thinking before the answer (0 = off)')
    p.add_argument('--classifier', help='Emotion classifier model id or path (optional)')
    p.add_argument('--reference', help='JSON: validated directions to use instead of extracting (per direction name)')
    p.add_argument('--skip-smoke', action='store_true', help='Tests only: skip the early usability check')
    p.add_argument('--rep-prompts', type=int, default=0, help='Prompts in a replication run (0 = same as the first run)')
    args = p.parse_args(argv)

    def log(message):
        print(f'{utc()} [{args.run_label}] {message}', flush=True)

    lock = None
    if args.lock:
        Path(args.lock).parent.mkdir(parents=True, exist_ok=True)
        lock = open(args.lock, 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('another experiment holds the execution lock; not starting')
            return EXIT_BUSY
    out = Path(args.out)
    try:
        failed = out / 'FAILED.json'
        if failed.exists():
            failed.replace(out / f'FAILED.previous-{int(time.time())}.json')
        log(f'starting: model {args.model}, split {args.split}')
        status = run(args, log)
        log(status)
        return 0
    except Exception as exc:  # noqa: BLE001 - recorded for the engine, then fail loudly
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / 'FAILED.json', {'time': utc(), 'error': f'{type(exc).__name__}: {exc}',
                                         'fatal': isinstance(exc, SmokeFailure)})
        traceback.print_exc()
        return 1
    finally:
        if lock is not None:
            lock.close()


if __name__ == '__main__':
    sys.exit(main())
