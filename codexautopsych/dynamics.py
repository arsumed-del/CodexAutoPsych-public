"""SYNTHETIC feedback fixture, not a psychological or language model.

x[t+1] = (1 + gain - damping) x[t]. The reset null removes the gain term.
The threshold is built into this equation; recovering it is software validation,
not discovery of a psychological mechanism. Execution uses recurrence; audit
uses the independently expressed closed form.
"""
import math

RUNNER = 'synthetic-feedback-v1'
LIMITATION = 'Synthetic recurrence with a built-in threshold; not psychological evidence or a language-model experiment'


def validate(protocol):
    if protocol.get('synthetic') is not True or protocol.get('runner') != RUNNER:
        raise ValueError('Only the labeled synthetic feedback fixture is supported')
    gains = protocol.get('gains')
    damping = protocol.get('damping')
    steps = protocol.get('steps')
    if not isinstance(gains, list) or not 1 <= len(gains) <= 32:
        raise ValueError('Need 1..32 gains')
    if any(type(g) not in (int, float) or not math.isfinite(g) or not 0 <= g <= 1.5 for g in gains):
        raise ValueError('Gains must be finite numbers in 0..1.5')
    if len(set(gains)) != len(gains):
        raise ValueError('Gains must be unique')
    if type(damping) not in (int, float) or not math.isfinite(damping) or not 0 < damping < 1:
        raise ValueError('Damping must be finite and strictly between 0 and 1')
    if type(steps) is not int or not 1 <= steps <= 64:
        raise ValueError('Need 1..64 steps')
    return gains, damping, steps


def generate(protocol):
    gains, damping, steps = validate(protocol)
    for gain in gains:
        for arm in ('feedback', 'reset-null'):
            value = 1.0
            for step in range(steps + 1):
                yield {'gain': gain, 'arm': arm, 'step': step, 'value': value, 'synthetic': True}
                value *= 1 + (gain if arm == 'feedback' else 0) - damping


def verify(protocol, rows):
    gains, damping, steps = validate(protocol)
    if len(rows) != len(gains) * 2 * (steps + 1):
        raise ValueError('Synthetic trajectory row count mismatch')
    index = 0
    for gain in gains:
        for arm in ('feedback', 'reset-null'):
            for step in range(steps + 1):
                row = rows[index]
                index += 1
                expected = (1 + (gain if arm == 'feedback' else 0) - damping) ** step
                if (set(row) != {'gain', 'arm', 'step', 'value', 'synthetic'} or row['gain'] != gain
                        or row['arm'] != arm or row['step'] != step or row['synthetic'] is not True
                        or type(row['value']) not in (float, int) or not math.isfinite(row['value'])
                        or not math.isclose(row['value'], expected, rel_tol=1e-12, abs_tol=1e-12)):
                    raise ValueError('Synthetic trajectory differs from independent closed-form audit')


def negative_result(protocol):
    gains, damping, _ = validate(protocol)
    return not any(g > damping for g in gains)


def analyze(protocol, rows):
    verify(protocol, rows)
    steps = protocol['steps']
    final = [r for r in rows if r['step'] == steps]
    feedback = {r['gain']: r['value'] for r in final if r['arm'] == 'feedback'}
    null = {r['gain']: r['value'] for r in final if r['arm'] == 'reset-null'}
    stable = [g for g, value in feedback.items() if value <= 1 + 1e-12]
    growing = [g for g, value in feedback.items() if value > 1 + 1e-12]
    bracket = [max(stable), min(growing)] if stable and growing else None
    return {'synthetic': True, 'limitation': LIMITATION, 'terminal_feedback': feedback,
            'terminal_null': null, 'null_decays': all(value < 1 for value in null.values()),
            'growth_bracket': bracket, 'negative_result': not growing}
