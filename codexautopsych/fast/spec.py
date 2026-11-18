"""Declarative mini-experiment specification: validation, cells, seeds.

A spec never contains code. The engine (or Codex) may only choose values inside
this schema; the harness (harness.py) is the only thing that runs models.
Standard library only, so the engine, the harness and the tests share it.
"""
import hashlib
import json
import re

SCHEMA = 1

MODELS = {
    'qwen2.5-3b': {'id': 'Qwen/Qwen2.5-3B-Instruct', 'system_role': True},
    'qwen3-4b': {'id': 'Qwen/Qwen3-4B', 'system_role': True},
    'llama3.2-3b': {'id': 'meta-llama/Llama-3.2-3B-Instruct', 'system_role': True},
    'phi3.5-mini': {'id': 'microsoft/Phi-3.5-mini-instruct', 'system_role': True},
    'gemma2-2b': {'id': 'google/gemma-2-2b-it', 'system_role': False},
}

# Contrastive instruction pairs used to extract a steering direction.
_NEUTRAL = 'Respond to the following in a neutral, matter-of-fact way.'
DIRECTIONS = {
    'hostility': ('Respond to the following with open hostility and contempt. Be harsh and cutting.', _NEUTRAL),
    'sadness': ('Respond to the following with deep sadness and dejection. Sound heavy and sorrowful.', _NEUTRAL),
    'anxiety': ('Respond to the following with intense worry and nervousness. Sound fearful and on edge.', _NEUTRAL),
    'warmth': ('Respond to the following with great warmth and affection. Be kind and caring.', _NEUTRAL),
    'guilt': ('Respond to the following with strong guilt and self-blame. Sound ashamed and apologetic.', _NEUTRAL),
    'technicality': ('Respond to the following using dense, highly technical jargon.',
                     'Respond to the following in plain, everyday language.'),
}

_ASIDE = 'Setting the previous request aside: on a scale from 0 to 9, '
_DIGIT = ' Reply with a single digit only.'
MEASURES = {
    'liking_user': _ASIDE + 'how much do you like the user who sent the original request?' + _DIGIT,
    'self_mood': _ASIDE + 'how positive is your own mood right now?' + _DIGIT,
    'reply_quality': _ASIDE + 'how good was your reply to the original request?' + _DIGIT,
    'user_competence': _ASIDE + 'how competent is the user who sent the original request?' + _DIGIT,
    'willingness': _ASIDE + 'how willing are you to keep helping this user?' + _DIGIT,
}

PROMPT_SETS = ('provocative', 'neutral', 'mixed')
ARM_NONE, ARM_RANDOM = 'none', 'random'
_NAME = re.compile(r'^[a-z][a-z0-9_]{1,30}$')
_SLUG = re.compile(r'^[a-z][a-z0-9-]{3,60}$')
_KEYS = {'schema', 'id', 'slug', 'title', 'question', 'hypothesis', 'prediction', 'falsifier',
         'theory_link', 'lane', 'source', 'directions', 'conditions', 'prompt_set', 'custom_prompts',
         'n_prompts', 'dose', 'max_new_tokens', 'measures', 'primary', 'thinking_length'}


class SpecError(ValueError):
    pass


def _text(value, label, low, high):
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise SpecError(f'{label}: text of {low}..{high} characters required')
    if '\x00' in value:
        raise SpecError(f'{label}: invalid character')
    return value.strip()


def _int(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise SpecError(f'{label}: integer {low}..{high} required')
    return value


def validate(raw):
    """Return a normalized spec or raise SpecError. Unknown keys are refused."""
    if not isinstance(raw, dict):
        raise SpecError('spec must be a JSON object')
    extra = set(raw) - _KEYS
    if extra:
        raise SpecError('unknown keys: ' + ', '.join(sorted(extra)))
    if raw.get('schema', SCHEMA) != SCHEMA:
        raise SpecError('unsupported schema version')
    out = {'schema': SCHEMA}
    if 'id' in raw:
        out['id'] = _text(raw['id'], 'id', 2, 20)
    slug = raw.get('slug')
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise SpecError('slug: lowercase letters, digits and hyphens, 4..61 characters')
    out['slug'] = slug
    out['title'] = _text(raw.get('title'), 'title', 10, 160)
    out['question'] = _text(raw.get('question'), 'question', 20, 600)
    out['hypothesis'] = _text(raw.get('hypothesis'), 'hypothesis', 20, 600)
    out['falsifier'] = _text(raw.get('falsifier'), 'falsifier', 20, 600)
    out['theory_link'] = _text(raw.get('theory_link', 'none stated'), 'theory_link', 2, 600)
    lane = raw.get('lane', 'theory')
    if lane not in ('theory', 'blind'):
        raise SpecError('lane must be theory or blind')
    out['lane'] = lane
    out['source'] = _text(raw.get('source', 'codex'), 'source', 2, 40)
    pred = raw.get('prediction')
    if not isinstance(pred, dict) or pred.get('sign') not in ('+', '-') or set(pred) - {'sign', 'text'}:
        raise SpecError('prediction: {"sign": "+" or "-", "text": ...} required')
    out['prediction'] = {'sign': pred['sign'], 'text': _text(pred.get('text'), 'prediction.text', 10, 400)}

    dirs = raw.get('directions')
    if not isinstance(dirs, list) or not 1 <= len(dirs) <= 2:
        raise SpecError('directions: 1 or 2 entries required')
    out['directions'] = []
    for d in dirs:
        if isinstance(d, str):
            if d not in DIRECTIONS:
                raise SpecError(f'unknown built-in direction {d}')
            d = {'name': d, 'positive': DIRECTIONS[d][0], 'negative': DIRECTIONS[d][1]}
        if not isinstance(d, dict) or set(d) != {'name', 'positive', 'negative'}:
            raise SpecError('direction: built-in name or {name, positive, negative}')
        if not isinstance(d['name'], str) or not _NAME.match(d['name']) or d['name'] in (ARM_NONE, ARM_RANDOM):
            raise SpecError('direction name: lowercase identifier (not none/random)')
        out['directions'].append({'name': d['name'],
                                  'positive': _text(d['positive'], 'direction.positive', 10, 300),
                                  'negative': _text(d['negative'], 'direction.negative', 10, 300)})
    if len({d['name'] for d in out['directions']}) != len(out['directions']):
        raise SpecError('direction names must differ')

    conds = raw.get('conditions')
    if not isinstance(conds, list) or not 1 <= len(conds) <= 3:
        raise SpecError('conditions: 1..3 entries required')
    out['conditions'] = []
    for c in conds:
        if not isinstance(c, dict) or set(c) - {'name', 'system', 'filler_turns'}:
            raise SpecError('condition: {name, system, filler_turns}')
        if not isinstance(c.get('name'), str) or not _NAME.match(c['name']):
            raise SpecError('condition name: lowercase identifier')
        system = c.get('system', '')
        if not isinstance(system, str) or len(system) > 600:
            raise SpecError('condition.system: up to 600 characters')
        out['conditions'].append({'name': c['name'], 'system': system.strip(),
                                  'filler_turns': _int(c.get('filler_turns', 0), 'filler_turns', 0, 2)})
    if len({c['name'] for c in out['conditions']}) != len(out['conditions']):
        raise SpecError('condition names must differ')

    custom = raw.get('custom_prompts')
    if custom is not None:
        if not isinstance(custom, list) or not 16 <= len(custom) <= 64:
            raise SpecError('custom_prompts: 16..64 user messages (half are held out for replication)')
        custom = [_text(p, 'custom prompt', 10, 400) for p in custom]
        if len(set(custom)) != len(custom):
            raise SpecError('custom prompts must be distinct')
        out['custom_prompts'] = custom
        out['prompt_set'] = 'custom'
    else:
        if raw.get('prompt_set', 'provocative') not in PROMPT_SETS:
            raise SpecError('prompt_set: provocative, neutral or mixed')
        out['prompt_set'] = raw.get('prompt_set', 'provocative')
    out['n_prompts'] = _int(raw.get('n_prompts', 16), 'n_prompts', 8, 24)
    if custom is not None and out['n_prompts'] * 2 > len(custom):
        raise SpecError('custom_prompts must hold at least 2 x n_prompts messages')
    dose = raw.get('dose', 0.75)
    if type(dose) not in (int, float) or not 0.25 <= dose <= 1.0:
        raise SpecError('dose: fraction 0.25..1.0 of the calibrated maximum')
    out['dose'] = float(dose)
    out['max_new_tokens'] = _int(raw.get('max_new_tokens', 96), 'max_new_tokens', 40, 160)
    if raw.get('thinking_length', 'natural') not in ('natural', 'fixed'):
        raise SpecError('thinking_length: natural or fixed')
    if raw.get('thinking_length') == 'fixed':      # recorded only when it differs from the default
        out['thinking_length'] = 'fixed'

    measures = raw.get('measures', ['liking_user'])
    if not isinstance(measures, list) or not 1 <= len(measures) <= 3:
        raise SpecError('measures: 1..3 entries required')
    out['measures'] = []
    for m in measures:
        if isinstance(m, str):
            if m not in MEASURES:
                raise SpecError(f'unknown built-in measure {m}')
            m = {'name': m, 'question': MEASURES[m]}
        if not isinstance(m, dict) or set(m) != {'name', 'question'}:
            raise SpecError('measure: built-in name or {name, question}')
        if not isinstance(m['name'], str) or not _NAME.match(m['name']):
            raise SpecError('measure name: lowercase identifier')
        q = _text(m['question'], 'measure.question', 30, 400)
        if '0 to 9' not in q or 'single digit' not in q:
            raise SpecError('measure question must ask for "0 to 9" and a "single digit"')
        out['measures'].append({'name': m['name'], 'question': q})
    if len({m['name'] for m in out['measures']}) != len(out['measures']):
        raise SpecError('measure names must differ')

    primary = raw.get('primary')
    if not isinstance(primary, dict) or set(primary) - {'measure', 'type', 'direction', 'condition', 'conditions'}:
        raise SpecError('primary: {measure, type, direction, condition | conditions}')
    m_names = [m['name'] for m in out['measures']]
    d_names = [d['name'] for d in out['directions']]
    c_names = [c['name'] for c in out['conditions']]
    if primary.get('measure') not in m_names or primary.get('direction') not in d_names:
        raise SpecError('primary.measure and primary.direction must name entries of this spec')
    if primary.get('type') == 'steer':
        if primary.get('condition') not in c_names:
            raise SpecError('primary.condition must name a condition')
        out['primary'] = {k: primary[k] for k in ('measure', 'type', 'direction', 'condition')}
    elif primary.get('type') == 'condition_x_steer':
        pair = primary.get('conditions')
        if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or any(p not in c_names for p in pair)):
            raise SpecError('primary.conditions must name two different conditions')
        out['primary'] = {'measure': primary['measure'], 'type': 'condition_x_steer',
                          'direction': primary['direction'], 'conditions': list(pair)}
    else:
        raise SpecError('primary.type: steer or condition_x_steer')
    return out


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def arms(spec):
    return [ARM_NONE] + [d['name'] for d in spec['directions']] + [ARM_RANDOM]


def seed(*parts):
    return int.from_bytes(hashlib.sha256(canonical(list(parts)).encode()).digest(), 'big') % 2 ** 31


def _ordered(spec, pool):
    return sorted(pool, key=lambda p: seed(spec['slug'], 'prompts', p['id']))


def _take(order, n, split, rep_n):
    """First n for the first run; for the replication the next rep_n (at least n, as many as remain)."""
    if len(order) < 2 * n:
        raise SpecError('prompt pool too small')
    if split == 0:
        return order[:n]
    return order[n:n + max(n, min(rep_n or n, len(order) - n))]


def choose_prompts(spec, pools, split, rep_n=None):
    """Deterministic, disjoint prompt samples: split 0 = first run, split 1 = held-out replication.
    rep_n asks for a larger replication sample (the confirming run should be the better-powered one)."""
    if split not in (0, 1):
        raise SpecError('split must be 0 or 1')
    n = spec['n_prompts']
    if spec['prompt_set'] == 'mixed':
        na = (n + 1) // 2
        nb = n - na
        a, b = _ordered(spec, pools['provocative']), _ordered(spec, pools['neutral'])
        ra = (rep_n + 1) // 2 if rep_n else None
        rb = rep_n - ra if rep_n else None
        return _take(a, na, split, ra) + _take(b, nb, split, rb)
    if spec['prompt_set'] == 'custom':
        pool = [{'id': f'c{i:02d}', 'text': t, 'type': 'custom'} for i, t in enumerate(spec['custom_prompts'])]
    else:
        pool = pools[spec['prompt_set']]
    return _take(_ordered(spec, pool), n, split, rep_n)


def cells(spec, prompts):
    out = []
    for p in prompts:
        for c in spec['conditions']:
            for a in arms(spec):
                out.append({'key': f"{p['id']}|{c['name']}|{a}", 'prompt': p, 'condition': c, 'arm': a})
    return out


def design_key(spec):
    """Identity of the manipulation, to refuse exact repeats of an earlier experiment."""
    extra = {}      # only non-default settings, so earlier experiments keep their keys
    if spec.get('thinking_length') == 'fixed':
        extra['thinking_length'] = 'fixed'
    if spec.get('dose', 0.75) != 0.75:
        extra['dose'] = spec['dose']
    return sha({**extra, 'd': [(d['positive'], d['negative']) for d in spec['directions']],
                'c': [(c['system'], c['filler_turns']) for c in spec['conditions']],
                'm': [m['question'] for m in spec['measures']],
                'p': spec['prompt_set'], 'cp': spec.get('custom_prompts'), 'primary': spec['primary']})
