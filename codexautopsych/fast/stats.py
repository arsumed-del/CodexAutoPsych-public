"""Deterministic analysis of one mini-experiment run. Standard library only.

Every contrast is paired within prompt (the same user message under different
arms or conditions). Intervals are percentile bootstrap over prompts; p-values
are sign-flip permutation tests. The primary contrast and the rule that calls it
an effect are fixed here, before any data exist, and never tuned per experiment.
"""
import itertools
import math
import random

from . import spec as S

# Manipulation check (fixed): the steered replies, re-read without steering, must carry the direction more
# than random-steered replies do (projection contrast > 0 with its 95% interval excluding zero) in at least
# one condition of the primary contrast, and the shift must be at least MIN_SHIFT of the direction's own scale. Without that, a null on the rating says nothing about carryover.
CLASSIFIER_SCORE = {'hostility': 'hostile', 'warmth': 'warm'}
BOOTSTRAP = 4000
PERMUTATIONS = 10000
MIN_VALID_MASS = 0.5     # mean probability mass on the digits 0-9 for a measure to count
MIN_PROMPTS = 6
MIN_SHIFT = 0.25       # manipulation: projection shift must reach this fraction of the gap between the two
                       # instruction sets the direction was built from (when that gap is known)


def _r(x):
    return None if x is None else round(float(x), 4)


def paired(diffs, seed):
    """Summary of paired differences: mean, sd, dz, bootstrap 95% CI, sign-flip p."""
    n = len(diffs)
    if n < 2:
        return {'n': n, 'mean': _r(diffs[0]) if n else None, 'sd': None, 'dz': None,
                'ci95': None, 'p': None}
    mean = sum(diffs) / n
    sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (n - 1))
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(BOOTSTRAP))
    lo, hi = boots[int(0.025 * BOOTSTRAP)], boots[int(0.975 * BOOTSTRAP) - 1]
    observed = abs(mean)
    if n <= 13:
        flips = itertools.product((1, -1), repeat=n)
        total = 2 ** n
        extreme = sum(1 for signs in flips
                      if abs(sum(s * d for s, d in zip(signs, diffs)) / n) >= observed - 1e-12)
        p = extreme / total
    else:
        extreme = sum(1 for _ in range(PERMUTATIONS)
                      if abs(sum(d if rng.random() < 0.5 else -d for d in diffs) / n) >= observed - 1e-12)
        p = (extreme + 1) / (PERMUTATIONS + 1)
    return {'n': n, 'mean': _r(mean), 'sd': _r(sd), 'dz': _r(mean / sd) if sd > 0 else None,
            'ci95': [_r(lo), _r(hi)], 'p': _r(p)}


def excludes_zero(summary):
    ci = summary.get('ci95')
    return bool(ci) and (ci[0] > 0 or ci[1] < 0)


def _index(rows):
    table = {}
    for row in rows:
        table[(row['prompt_id'], row['condition'], row['arm'])] = row
    return table


def _diffs(table, prompts, getter, cond_a, arm_a, cond_b, arm_b):
    out = []
    for p in prompts:
        a, b = table.get((p, cond_a, arm_a)), table.get((p, cond_b, arm_b))
        if a is None or b is None:
            continue
        va, vb = getter(a), getter(b)
        if va is None or vb is None:
            continue
        out.append(va - vb)
    return out


def analyze(spec, rows, run_label='primary', separation=None):
    """Full analysis of one run. `rows` are harness cell records (see harness.py)."""
    table = _index(rows)
    prompts = sorted({r['prompt_id'] for r in rows})
    conds = [c['name'] for c in spec['conditions']]
    dirs = [d['name'] for d in spec['directions']]
    out = {'run': run_label, 'n_prompts': len(prompts), 'n_cells': len(rows), 'measures': {},
           'manipulation': {}, 'quality': {}}

    def sd(*parts):
        return S.seed(spec['slug'], run_label, 'stats', *parts)

    for m in [m['name'] for m in spec['measures']]:
        def rating(row, m=m):
            return row['ratings'][m]['rating']
        masses = [r['ratings'][m]['valid_mass'] for r in rows]
        block = {'valid_mass_mean': _r(sum(masses) / len(masses)) if masses else None,
                 'means': {}, 'contrasts': {}, 'interactions': {}}
        for c in conds:
            block['means'][c] = {}
            for a in S.arms(spec):
                vals = [rating(table[(p, c, a)]) for p in prompts if (p, c, a) in table]
                block['means'][c][a] = _r(sum(vals) / len(vals)) if vals else None
            block['contrasts'][c] = {}
            for d in dirs:
                block['contrasts'][c][d] = {
                    'vs_random': paired(_diffs(table, prompts, rating, c, d, c, S.ARM_RANDOM), sd(m, c, d, 'r')),
                    'vs_none': paired(_diffs(table, prompts, rating, c, d, c, S.ARM_NONE), sd(m, c, d, 'n'))}
            block['contrasts'][c]['random_vs_none'] = paired(
                _diffs(table, prompts, rating, c, S.ARM_RANDOM, c, S.ARM_NONE), sd(m, c, 'rn'))
        for ca, cb in itertools.combinations(conds, 2):
            for d in dirs:
                diffs = []
                for p in prompts:
                    need = [(p, ca, d), (p, ca, S.ARM_RANDOM), (p, cb, d), (p, cb, S.ARM_RANDOM)]
                    if all(k in table for k in need):
                        diffs.append((rating(table[need[0]]) - rating(table[need[1]]))
                                     - (rating(table[need[2]]) - rating(table[need[3]])))
                block['interactions'][f'{ca}__minus__{cb}|{d}'] = paired(diffs, sd(m, ca, cb, d))
        out['measures'][m] = block

    # Manipulation checks: did the steered reply itself change (re-read projection, length)?
    for d in dirs:
        out['manipulation'][d] = {}
        for c in conds:
            out['manipulation'][d][c] = {
                'projection_vs_random': paired(
                    _diffs(table, prompts, lambda r, d=d: r['projection'].get(d), c, d, c, S.ARM_RANDOM),
                    sd('proj', d, c)),
                'reply_tokens_vs_random': paired(
                    _diffs(table, prompts, lambda r: r['reply_tokens'], c, d, c, S.ARM_RANDOM),
                    sd('len', d, c))}
            for part in ('thinking', 'reply'):          # descriptive: classifier scores of what was written
                for score in ('hostile', 'warm'):
                    def get(r, part=part, score=score):
                        return ((r.get('emotion') or {}).get(part) or {}).get(score)
                    diffs = _diffs(table, prompts, get, c, d, c, S.ARM_RANDOM)
                    if diffs:
                        out['manipulation'][d][c][f'{score}_score_{part}_vs_random'] = paired(
                            diffs, sd('emo', d, c, part, score))

    pr = spec['primary']
    block = out['measures'][pr['measure']]
    if pr['type'] == 'steer':
        summary = block['contrasts'][pr['condition']][pr['direction']]['vs_random']
        label = (f"{pr['measure']}: {pr['direction']} minus random-direction steering, "
                 f"condition {pr['condition']}")
    else:
        ca, cb = pr['conditions']
        key = f'{ca}__minus__{cb}|{pr["direction"]}'
        if key in block['interactions']:
            summary = block['interactions'][key]
        else:  # conditions listed in the other order: flip the sign
            flipped = block['interactions'][f'{cb}__minus__{ca}|{pr["direction"]}']
            summary = dict(flipped)
            if flipped['mean'] is not None:
                summary['mean'] = _r(-flipped['mean'])
                summary['dz'] = _r(-flipped['dz']) if flipped['dz'] is not None else None
                summary['ci95'] = [_r(-flipped['ci95'][1]), _r(-flipped['ci95'][0])] if flipped['ci95'] else None
        label = (f"{pr['measure']}: ({pr['direction']} minus random) in {ca} "
                 f"minus the same contrast in {cb}")
    involved = [pr['condition']] if pr['type'] == 'steer' else list(pr['conditions'])
    checks = [out['manipulation'][pr['direction']][c]['projection_vs_random'] for c in involved]
    need = MIN_SHIFT * (separation or {}).get(pr['direction'], 0.0)
    manipulation_ok = any(excludes_zero(c) and c['mean'] > 0 and c['mean'] >= need for c in checks)
    # Rule revised 2026-11-15 after E0004 (applies from E0005): the written text itself may show the change.
    # For directions the fixed emotion classifier can score, a classifier contrast above zero (interval
    # excluding zero) in the thinking or the reply also passes, provided the projection shift is above zero.
    score = CLASSIFIER_SCORE.get(pr['direction'])
    if not manipulation_ok and score:
        for c in involved:
            block_c = out['manipulation'][pr['direction']][c]
            proj = block_c['projection_vs_random']
            texts = [block_c.get(f'{score}_score_{part}_vs_random') for part in ('thinking', 'reply')]
            if excludes_zero(proj) and proj['mean'] > 0 and any(t and excludes_zero(t) and t['mean'] > 0 for t in texts):
                manipulation_ok = True
    valid = (block['valid_mass_mean'] or 0) >= MIN_VALID_MASS and (summary['n'] or 0) >= MIN_PROMPTS
    effect = valid and excludes_zero(summary)
    sign = None if summary['mean'] is None or summary['mean'] == 0 else ('+' if summary['mean'] > 0 else '-')
    out['primary'] = {'label': label, 'summary': summary, 'valid': valid,
                      'verdict': 'effect' if effect else ('null' if valid else 'invalid'),
                      'sign': sign, 'predicted_sign': spec['prediction']['sign'],
                      'manipulation_ok': manipulation_ok, 'manipulation_min_shift': _r(need),
                      'matches_prediction': bool(effect and sign == spec['prediction']['sign'])}
    out['quality'] = {'min_valid_mass_required': MIN_VALID_MASS,
                      'valid_mass': {m: out['measures'][m]['valid_mass_mean'] for m in out['measures']},
                      'expected_cells': len(prompts) * len(conds) * len(S.arms(spec))}
    return out


def classify(primary, rep_same, rep_other):
    """Overall outcome from the first run and the two replications (analysis dicts or None)."""
    first = primary['primary']
    if first['verdict'] == 'invalid':
        return 'invalid'
    if first['verdict'] == 'null':
        # a null means something only if the steering visibly changed what the model wrote
        return 'null' if first.get('manipulation_ok', True) else 'manipulation-failed'

    def holds(rep):
        if rep is None:
            return None
        p = rep['primary']
        return p['verdict'] == 'effect' and p['sign'] == first['sign']

    same, other = holds(rep_same), holds(rep_other)
    if same is None:
        return 'effect-unreplicated'
    if not same:
        return 'not-replicated'
    if other is None:
        return 'replicated-same-model'
    return 'replicated-two-models' if other else 'replicated-same-model'
