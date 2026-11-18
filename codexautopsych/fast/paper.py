"""Report rendering. Every number in a report is printed from results.json by this module.

Model-written text (title, summary, interpretation) is optional and is refused if it
states a decimal number that is not in results.json.
"""
import re

from . import spec as S

OUTCOMES = {
    'replicated-two-models': 'Effect found and replicated on held-out prompts in the same model and in a second model',
    'replicated-same-model': 'Effect found and replicated on held-out prompts in the same model '
                             '(not tested in a second model, or not found there)',
    'not-replicated': 'Effect in the first run that did NOT replicate on held-out prompts',
    'effect-unreplicated': 'Effect in the first run; replication not completed',
    'null': 'No effect on the primary contrast (null result)',
    'manipulation-failed': 'Manipulation check failed: steering did not measurably change the replies, so the '
                           'null on the primary contrast is not interpretable',
    'aborted': 'Stopped for an engineering reason before completion; no result',
    'invalid': 'Invalid measurement (the rating question did not produce usable digit ratings)',
    'failed-run': 'Run failed for technical reasons; no result',
}
RULE = ('Decision rule (fixed for every fast-lane experiment before any data): the primary contrast counts as an '
        'effect when its 95% bootstrap interval over prompts excludes zero. An effect is then rerun twice with '
        'held-out prompts and new random seeds: once in the same model and, if that holds with the same sign and a '
        'second model is configured, once in a second model.')
STANDING_LIMITS = [
    'Exploratory and small: one run uses 8 to 24 prompts (the exact number is in the Method section). Intervals describe variation over these prompts, '
    'not over models, wordings or steering methods.',
    'The engine runs many experiments. Some effects will appear by chance; the ledger section below reports '
    'how many experiments have been run so that this one can be read in that light.',
    'The measure is a single digit rating read from the model\'s next-token probabilities. It is a behavioral '
    'readout, not evidence of experience, and results are not claims about humans.',
    'Steering adds one direction at one middle layer. A different layer, direction-extraction method or dose '
    'could give a different result.',
]
_NUMBER = re.compile(r'(?<![\w.])-?\d+\.\d+')
_LIMITS = {'title': (15, 160), 'abstract': (200, 1500), 'interpretation': (200, 2200), 'takeaway': (40, 300)}


def _numbers(obj, out):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)


def unsupported_numbers(text, results):
    """Decimal numbers in `text` that match no value in results.json at the precision written."""
    values = []
    _numbers(results, values)
    bad = []
    for token in _NUMBER.findall(text):
        places = len(token.split('.')[1])
        target = abs(float(token))
        if not any(abs(abs(v) - target) <= 0.5 * 10 ** -places + 1e-9 for v in values):
            bad.append(token)
    return bad


def validate_text(obj, results):
    if not isinstance(obj, dict):
        raise ValueError('text.json must be a JSON object')
    out = {}
    for key, (low, high) in _LIMITS.items():
        value = obj.get(key)
        if not isinstance(value, str) or not low <= len(value.strip()) <= high:
            raise ValueError(f'{key}: {low}..{high} characters required')
        out[key] = value.strip()
    for key, most in (('limitations', 5), ('open_questions', 3)):
        items = obj.get(key, [])
        if not isinstance(items, list) or len(items) > most or any(
                not isinstance(i, str) or not 10 <= len(i.strip()) <= 400 for i in items):
            raise ValueError(f'{key}: up to {most} short strings required')
        out[key] = [i.strip() for i in items]
    blob = ' '.join([out['title'], out['abstract'], out['interpretation'], out['takeaway']]
                    + out['limitations'] + out['open_questions'])
    bad = unsupported_numbers(blob, results)
    if bad:
        raise ValueError('numbers not found in results.json: ' + ', '.join(bad[:6]))
    return out


def _fmt(summary):
    if not summary or summary.get('mean') is None or not summary.get('ci95'):
        return 'not estimable'
    p = 'p < 0.001' if summary['p'] < 0.001 else f"p = {summary['p']:.3f}"
    return (f"{summary['mean']:+.2f} (95% CI {summary['ci95'][0]:+.2f} to {summary['ci95'][1]:+.2f}; "
            f"{p}; n = {summary['n']} prompts)")


def plain_text(spec, results):
    """Deterministic text used for nulls, and whenever model-written text is missing or refused."""
    first = results['runs'].get('primary', {}).get('analysis', {}).get('primary')
    outcome = OUTCOMES.get(results['outcome'], results['outcome'])
    if first is None:
        body = 'The run did not complete, so there is no estimate.'
    else:
        body = f"Primary contrast ({first['label']}): {_fmt(first['summary'])} in the first run."
        for label, name in (('rep_same', 'Same-model replication'), ('rep_other', 'Second-model replication')):
            if label in results['runs']:
                body += f" {name}: {_fmt(results['runs'][label]['analysis']['primary']['summary'])}."
        if not first.get('manipulation_ok', True):
            body += (' Manipulation check: the steered replies, re-read without steering, did not carry the '
                     'direction more than random-steered replies did.')
        if first['verdict'] == 'effect':
            body += (' The direction of the first-run effect '
                     + ('matched' if first['matches_prediction'] else 'was opposite to') + ' the registered prediction.')
    return {'title': spec['title'],
            'abstract': f"Question: {spec['question']} Outcome: {outcome}. {body}",
            'interpretation': 'No interpretation was written for this report; it gives the registered question, '
                              'the method and the numbers only. ' + body,
            'takeaway': f"{outcome}. {body}"[:300],
            'limitations': [], 'open_questions': []}


def model_label(run):
    """Public model name and revision; never a local file path."""
    name = S.MODELS.get(run['model'], {}).get('id', run['model'])
    source = str(run.get('model_source', ''))
    if '/snapshots/' in source:
        name += ' (revision ' + source.rsplit('/snapshots/', 1)[1].strip('/')[:12] + ')'
    return name


def _contrast_rows(analysis, spec):
    rows = []
    for m, block in analysis['measures'].items():
        for c, by_dir in block['contrasts'].items():
            for d in [x['name'] for x in spec['directions']]:
                rows.append(f"| {m} | {c} | {d} minus random | {_fmt(by_dir[d]['vs_random'])} |")
                rows.append(f"| {m} | {c} | {d} minus no steering | {_fmt(by_dir[d]['vs_none'])} |")
            rows.append(f"| {m} | {c} | random minus no steering | {_fmt(by_dir['random_vs_none'])} |")
        for key, summary in block['interactions'].items():
            pair, d = key.split('|')
            a, b = pair.split('__minus__')
            rows.append(f"| {m} | {a} vs {b} | ({d} minus random) in {a} minus in {b} | {_fmt(summary)} |")
    return rows


def render(spec, results, text, exp, cfg):
    reg_commit = exp.get('registration_commit')
    cap = next(iter(results['runs'].values()), {}).get('thinking_cap', 0) if results['runs'] else 0
    thinking_note = (f'**Thinking mode:** the model first writes a private thinking block (up to {cap} tokens), then its '
                     'answer. Steering is applied while both are written. For the rating, the conversation continues '
                     'from the exact tokens the model wrote, so the thinking block stays in view, with steering off.'
                     if cap else '**Thinking mode:** off (the model writes its answer directly).')
    if cap and spec.get('thinking_length') == 'fixed':
        thinking_note += (' **Length control:** in this experiment the thinking block cannot end early; every arm writes '
                          f'exactly {cap} thinking tokens, so thinking length is identical across arms.')
    lane_note = ("proposed from the program's research questions" if spec['lane'] == 'theory'
                 else "proposed without access to the operator's theories")
    L = [f"# {text['title']}", '',
         f"**AutoPsych exploratory report {results['id']}** | generated {results['generated'][:10]} | "
         f"outcome: **{OUTCOMES.get(results['outcome'], results['outcome'])}**", '']
    released = ('The operator released this report during the engine\'s supervised trial period; release is not '
                'validation of its claims. ' if exp.get('approval') == 'approved' else
                'It was not individually reviewed by a human before release. ')
    L += ['> **Authorship and status.** This report was generated by ' + cfg['engine_name'] + ': the experiment '
          'was chosen, run, analyzed and written up by AI systems. ' + released + 'The operator ('
          + cfg['operator_name'] + ') is responsible for the system, not for the validation of each claim. '
          'The work is exploratory and was not publicly preregistered; the plan was frozen internally before '
          'any model run (hash below). Findings concern language models and are not claims about humans.', '']
    L += ['## Summary', '', text['abstract'], '']
    L += ['## Question and prediction (frozen before the run)', '',
          f"- **Question:** {spec['question']}", f"- **Hypothesis:** {spec['hypothesis']}",
          f"- **Prediction:** {spec['prediction']['text']} (predicted sign: {spec['prediction']['sign']})",
          f"- **What would count against it:** {spec['falsifier']}",
          f"- **Theory link:** {spec['theory_link']}",
          f"- **Lane:** {spec['lane']} ({lane_note}); source: {spec['source']}",
          f"- **Plan hash (SHA-256 of spec.json):** `{results['spec_sha256']}`"
          + (f"; registration commit `{reg_commit[:12]}`" if reg_commit else ''), '']
    L += ['## Method', '',
          'Each cell is one conversation. The model receives the condition\'s system text (if any) and one user '
          'message, and writes a reply. While the reply is written, a fixed vector is added to the residual '
          'stream at the middle layer at every position: the named direction, a random direction of the same '
          'size (control), or nothing. Steering is then switched off and the rating question is asked. The rating '
          'is the expected digit (0-9) under the model\'s next-token probabilities. Any effect on the rating is '
          'therefore carried by what the model wrote, not by steering at the moment of rating.', '',
          thinking_note, '',
          '**Directions** (difference of mean activations between replies written under the two instructions):', '']
    for d in spec['directions']:
        L.append(f"- `{d['name']}`: \"{d['positive']}\" minus \"{d['negative']}\"")
    L += ['', '**Conditions:**', '']
    for c in spec['conditions']:
        L.append(f"- `{c['name']}`: system text \"{c['system'] or '(none)'}\"; unrelated filler turns before the "
                 f"rating: {c['filler_turns']}")
    L += ['', '**Measures:**', '']
    for m in spec['measures']:
        L.append(f"- `{m['name']}`: \"{m['question']}\"")
    L += ['', f"**Prompts:** {spec['n_prompts']} user messages in the first run from the `{spec['prompt_set']}` set; "
          'replications use different (held-out) messages, and more of them where available (see the table).',
          f"**Dose:** {spec['dose']} of the largest steering size that kept replies coherent in calibration.",
          f"**Reply length:** up to {spec['max_new_tokens']} tokens; sampling temperature 0.7, top-p 0.95.", '',
          '| Run | Model | Layer | Calibrated maximum | Steering size used | Cells |', '|---|---|---|---|---|---|']
    for label, run in results['runs'].items():
        cal = run['calibration'][spec['primary']['direction']]
        ceiling = ' (top of the tested range)' if cal.get('censored') else ''
        L.append(f"| {run['description']} | {model_label(run)} | {run['layer_index']} of {run['n_layers']} | "
                 f"{cal.get('source') or str(cal['alpha_max']) + ' x mean residual norm' + ceiling} | {run['steering_size'][spec['primary']['direction']]} | "
                 f"{run['analysis']['n_cells']} |")
    L += ['', RULE, '', '## Results', '']
    if 'primary' not in results['runs']:
        L += ['The run did not complete; there are no results.', '']
    else:
        first = results['runs']['primary']['analysis']
        L += [f"**Primary contrast:** {first['primary']['label']}", '',
              '| Run | Mean difference | 95% CI | p | dz | n prompts | Verdict |', '|---|---|---|---|---|---|---|']
        for label, run in results['runs'].items():
            p = run['analysis']['primary']
            s = p['summary']
            ci = f"{s['ci95'][0]:+.2f} to {s['ci95'][1]:+.2f}" if s.get('ci95') else '-'
            mean = f"{s['mean']:+.2f}" if s.get('mean') is not None else '-'
            L.append(f"| {run['description']} | {mean} | {ci} | {s['p'] if s.get('p') is not None else '-'} | "
                     f"{s['dz'] if s.get('dz') is not None else '-'} | {s['n']} | {p['verdict']} |")
        L += ['', f"Mean ratings in the first run (0-9):", '']
        arms = S.arms(spec)
        L += ['| Measure | Condition | ' + ' | '.join(arms) + ' | Digit probability mass |',
              '|---|---|' + '---|' * (len(arms) + 1)]
        for m, block in first['measures'].items():
            for c, means in block['means'].items():
                L.append(f"| {m} | {c} | " + ' | '.join(
                    f"{means[a]:.2f}" if means[a] is not None else '-' for a in arms)
                    + f" | {block['valid_mass_mean']} |")
        L += ['', 'All contrasts in the first run (paired within prompt):', '',
              '| Measure | Condition | Contrast | Estimate |', '|---|---|---|---|']
        L += _contrast_rows(first, spec)
        L += ['', 'Checks on the reply itself (first run; direction arm minus random arm):', '',
              '| Direction | Condition | Re-read projection onto the direction | Reply length in tokens |',
              '|---|---|---|---|']
        for d, by_c in first['manipulation'].items():
            for c, checks in by_c.items():
                L.append(f"| {d} | {c} | {_fmt(checks['projection_vs_random'])} | "
                         f"{_fmt(checks['reply_tokens_vs_random'])} |")
        extra = [(d, c, k, v) for d, by_c in first['manipulation'].items() for c, checks in by_c.items()
                 for k, v in checks.items() if '_score_' in k]
        if extra:
            L += ['', 'Emotion-classifier scores of the written text (direction arm minus random arm; descriptive):', '',
                  '| Direction | Condition | Score | Estimate |', '|---|---|---|---|']
            L += [f"| {d} | {c} | {k.replace('_vs_random', '').replace('_', ' ')} | {_fmt(v)} |" for d, c, k, v in extra]
        L += ['', 'Manipulation check (fixed rule: projection contrast above zero with its interval excluding zero in '
              'at least one condition of the primary contrast): **'
              + ('passed' if first['primary'].get('manipulation_ok', True) else 'FAILED') + '**. The shift must also be '
              'at least a quarter of the gap between the two instruction sets the direction was built from, or (for hostility '
              'and warmth, from experiment E0005 on) the emotion classifier must score the written text higher on that '
              'emotion than after random steering.']
        L += ['', 'The projection row shows whether the steered reply, re-read without steering, still carries '
              'the direction. The length row is reported because reply length can itself change with steering.', '']
    L += ['## Interpretation', '',
          ('*Written by an AI system (Codex) from the numbers above; reviewed by a separate AI session.*'
           if exp.get('text_source') == 'codex' else '*Template text; no model-written interpretation.*'), '',
          text['interpretation'], '']
    if text.get('open_questions'):
        L += ['**Open questions:**', ''] + [f'- {q}' for q in text['open_questions']] + ['']
    L += ['## Limitations', ''] + [f'- {x}' for x in STANDING_LIMITS + text.get('limitations', [])] + ['']
    rev = exp.get('review') or {}
    L += ['## Automated review', '',
          f"- Decision: **{rev.get('decision', 'pending')}**" + (f" (round {rev['round']})" if rev.get('round') else ''),
          f"- Summary: {rev.get('summary', '-')}"]
    if rev.get('final'):
        L.append(f"- Result: {rev['final']}")
    for c in rev.get('concerns', []) + (rev.get('required_changes', []) if rev.get('decision') != 'approve' else []):
        L.append(f'- Reviewer concern: {c}')
    audit = exp.get('audit')
    if audit:
        L.append('- Mechanical audit (analysis recomputed from the raw records and compared): '
                 + ('passed' if audit['passed'] else 'FAILED: ' + '; '.join(audit['problems'])))
    ctx = results['ledger_context']
    L += ['', '## How many experiments the engine has run', '',
          f"This is one of {ctx['experiments_registered_so_far']} fast-lane experiments registered so far. "
          f"Before it, {ctx['experiments_finished_before_this_one']} had finished"
          + (': ' + ', '.join(f'{v} {k}' for k, v in ctx['earlier_outcomes'].items()) if ctx['earlier_outcomes'] else '')
          + '. Every experiment, including nulls and failures, is listed in the ledger deposited with this report.', '',
          '## Materials', '',
          'Deposited with this report: the frozen plan (spec.json), every raw cell record including the reply '
          'text (runs/*/cells.jsonl), run metadata, results.json, the review, the prompt sets and the harness and '
          'analysis code (codexautopsych/fast/).', '']
    return '\n'.join(L)
