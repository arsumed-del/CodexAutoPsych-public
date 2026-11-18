"""Routine work and checkpoint tests using fake codex/claude executables.

The fakes are local scripts that imitate the CLIs' login checks, outputs and
limit messages. No real model, subscription, or network is used.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from codexautopsych.core import Lab, Busy
from codexautopsych import routine
from codexautopsych.services import (parse_resume_time, detect_limit, UsageLimit,
                                     CODEX_PAID_ROUTES, CLAUDE_PAID_ROUTES)

FAKE_CODEX = r'''
import json, os, sys
from pathlib import Path
ctrl = json.loads(Path(os.environ['FAKE_CTRL']).read_text())
log = Path(os.environ['FAKE_LOG'])
with log.open('a') as f:
    f.write(json.dumps({'tool': 'codex', 'args': sys.argv[1:]}) + '\n')
if sys.argv[1:3] == ['login', 'status']:
    print('Logged in using ChatGPT' if ctrl.get('codex_login', True) else 'Not logged in')
    sys.exit(0)
if sys.argv[1] == 'exec':
    prompt = sys.stdin.read()
    queue = ctrl.get('codex_script', [])
    step = queue.pop(0) if queue else 'ok'
    ctrl['codex_script'] = queue
    Path(os.environ['FAKE_CTRL']).write_text(json.dumps(ctrl))
    if step != 'ok':
        print(step, file=sys.stderr)
        sys.exit(1)
    cwd = Path(sys.argv[sys.argv.index('-C') + 1])
    if (cwd / 'REVIEW_REQUEST.md').exists():  # synthetic independent review session
        decision = ctrl.get('codex_review', 'approve')
        Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(
            {'decision': decision, 'summary': 'SYNTHETIC codex review', 'required_changes': ['fix'] if decision == 'revise' else []}))
        sys.exit(0)
    (cwd / 'outputs' / 'draft.md').write_text('SYNTHETIC draft\n' + prompt[-200:])
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text('Wrote outputs/draft.md (synthetic fake).')
    sys.exit(0)
sys.exit(2)
'''

FAKE_CLAUDE = r'''
import json, os, sys
from pathlib import Path
ctrl = json.loads(Path(os.environ['FAKE_CTRL']).read_text())
with Path(os.environ['FAKE_LOG']).open('a') as f:
    f.write(json.dumps({'tool': 'claude', 'args': sys.argv[1:]}) + '\n')
if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({'authMethod': ctrl.get('claude_auth', 'claude.ai')}))
    sys.exit(0)
if '-p' in sys.argv:
    packet = sys.stdin.read()
    Path(os.environ['FAKE_LOG']).with_name('last_packet.md').write_text(packet)
    queue = ctrl.get('claude_script', [])
    step = queue.pop(0) if queue else 'approve'
    ctrl['claude_script'] = queue
    Path(os.environ['FAKE_CTRL']).write_text(json.dumps(ctrl))
    if step in ('approve', 'revise', 'reject'):
        review = {'decision': step, 'summary': 'synthetic review',
                  'required_changes': ['Add limitations section'] if step == 'revise' else []}
        print(json.dumps({'type': 'result', 'is_error': False, 'result': '', 'structured_output': review}))
        sys.exit(0)
    print(json.dumps({'type': 'result', 'is_error': True, 'result': step}))
    sys.exit(1)
sys.exit(2)
'''


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 8, 10, 0, tzinfo=timezone(timedelta(hours=-7)))
    def __call__(self):
        return self.t
    def advance(self, **kw):
        self.t += timedelta(**kw)


class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / 'bin'
        self.bin.mkdir()
        for name, body in (('codex', FAKE_CODEX), ('claude', FAKE_CLAUDE)):
            path = self.bin / name
            path.write_text(f'#!{sys.executable}\n' + textwrap.dedent(body))
            path.chmod(0o755)
        self.ctrl = root / 'ctrl.json'
        self.log = root / 'calls.jsonl'
        self.control()
        self.env = patch.dict(os.environ, {'FAKE_CTRL': str(self.ctrl), 'FAKE_LOG': str(self.log)}, clear=False)
        self.env.start()
        for key in CODEX_PAID_ROUTES + CLAUDE_PAID_ROUTES:
            os.environ.pop(key, None)  # restored by patch.dict on teardown
        self.lab = Lab(root / 'lab')
        routine.set_config(self.lab, 'codex.binary', str(self.bin / 'codex'))
        routine.set_config(self.lab, 'claude.binary', str(self.bin / 'claude'))
        routine.set_config(self.lab, 'codex.enabled', True)
        routine.set_config(self.lab, 'claude.mode', 'cli')
        routine.set_config(self.lab, 'notify', False)
        self.clock = Clock()

    def tearDown(self):
        self.env.stop()
        self.lab.close()
        self.tmp.cleanup()

    def control(self, **values):
        self.ctrl.write_text(json.dumps(values))

    def calls(self, tool, sub=None):
        if not self.log.exists():
            return []
        rows = [json.loads(l) for l in self.log.read_text().splitlines()]
        return [r for r in rows if r['tool'] == tool and (sub is None or sub in r['args'])]

    def run_pass(self, **kw):
        return routine.run(self.lab, 'test-scheduler', clock=self.clock, **kw)

    def state(self, tid):
        return self.lab.task(tid)['state']

    def test_routine_work_runs_on_codex_without_claude(self):
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft a methods paragraph')
        self.run_pass()
        task = self.lab.task(ids['work'])
        self.assertEqual(task['state'], 'succeeded')
        result = json.loads(task['result'])
        self.assertEqual(result['outputs'][0]['path'], 'draft.md')
        self.assertEqual(self.calls('claude'), [])
        args = self.calls('codex', 'exec')[0]['args']
        for flag in ('--ephemeral', '--ignore-user-config', '--skip-git-repo-check', 'workspace-write'):
            self.assertIn(flag, args)
        self.assertIn('forced_login_method="chatgpt"', args)

    def test_codex_disabled_means_nothing_runs(self):
        routine.set_config(self.lab, 'codex.enabled', False)
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft')
        self.run_pass()
        self.assertEqual(self.state(ids['work']), 'queued')
        self.assertEqual(self.calls('codex'), [])

    def test_review_and_publish_checkpoints(self):
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft abstract', checkpoint='publish')
        self.run_pass()
        self.assertEqual(self.state(ids['work']), 'succeeded')
        self.assertEqual(self.state(ids['review']), 'succeeded')
        self.assertEqual(self.state(ids['publish']), 'awaiting_human')
        packet = self.log.with_name('last_packet.md').read_text()
        self.assertIn('SYNTHETIC draft', packet)
        claude_args = self.calls('claude', '-p')[0]['args']
        self.assertEqual(claude_args[claude_args.index('--tools') + 1], '')
        self.assertIn('--no-session-persistence', claude_args)
        package = Path(json.loads(self.lab.task(ids['publish'])['result'])['packet'])
        self.assertTrue((package / 'outputs' / 'draft.md').exists())
        self.assertTrue((package / 'claude-review.json').exists())
        routine.owner_publish(self.lab, ids['publish'], True, 'Arthur')
        self.assertEqual(self.state(ids['publish']), 'succeeded')

    def test_revision_loop_repoints_publication(self):
        self.control(claude_script=['revise', 'approve'])
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft discussion', checkpoint='publish')
        self.run_pass(max_tasks=10)
        first = json.loads(self.lab.task(ids['review'])['result'])
        self.assertEqual(self.state(ids['review']), 'revised')
        self.assertEqual(self.state(first['revision_task']), 'succeeded')
        self.assertEqual(self.state(first['next_review']), 'succeeded')
        self.assertEqual(self.state(ids['publish']), 'awaiting_human')
        dep = self.lab.db.execute('SELECT dependency FROM dependencies WHERE task=?', (ids['publish'],)).fetchone()[0]
        self.assertEqual(dep, first['next_review'])
        revise_prompt = self.lab.task(first['revision_task'])['payload']
        self.assertIn('Add limitations section', revise_prompt)

    def test_codex_review_mode_runs_without_claude(self):
        routine.set_config(self.lab, 'claude.mode', 'codex')
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['review']), 'succeeded')
        self.assertEqual(self.calls('claude'), [])
        reviews = [c for c in self.calls('codex', 'exec') if 'read-only' in c['args']]
        self.assertEqual(len(reviews), 1)          # separate, read-only session
        self.assertEqual(routine.usage(self.lab, 'codex', self.clock), 1)   # work budget
        self.assertEqual(routine.usage(self.lab, 'claude', self.clock), 1)  # review budget (own cap)

    def test_codex_review_has_its_own_budget(self):
        routine.set_config(self.lab, 'claude.mode', 'codex')
        routine.set_config(self.lab, 'codex.max_calls_per_day', 1)
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['work']), 'succeeded')
        self.assertEqual(self.state(ids['review']), 'succeeded')   # work cap reached, review still ran

    def test_codex_review_waits_when_review_budget_spent(self):
        routine.set_config(self.lab, 'claude.mode', 'codex')
        routine.set_config(self.lab, 'claude.max_calls_per_day', 0)
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['work']), 'succeeded')
        self.assertEqual(self.state(ids['review']), 'queued')

    def test_codex_review_stops_on_real_codex_limit(self):
        routine.set_config(self.lab, 'claude.mode', 'codex')
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        with self.lab.transaction():
            routine.pause_service(self.lab, 'codex', self.clock() + timedelta(hours=3), 'usage limit reached')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['work']), 'queued')
        self.assertEqual(self.state(ids['review']), 'queued')

    def test_idle_engine_is_reported_not_called_moving(self):
        cfg = routine.load_config(self.lab)
        self.assertIsNone(routine.idle_line(self.lab, cfg, self.clock))
        self.lab.enqueue('plan', 'coding-worker', {})
        self.lab.db.execute("UPDATE tasks SET state='succeeded', updated='2000-01-01T00:00:00+00:00'")
        line = routine.idle_line(self.lab, cfg, self.clock)
        self.assertIn('IDLE since 2000-01-01', line)

    def test_stall_alarm_flags_waiting_task_and_clears(self):
        routine.set_config(self.lab, 'claude.mode', 'manual')
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['review']), 'awaiting_human')
        cfg = routine.load_config(self.lab)
        self.assertEqual(routine.stall_lines(self.lab, cfg, self.clock), [])   # just parked: not a stall
        later = Clock(); later.t = datetime.now(timezone.utc) + timedelta(hours=3)
        lines = routine.stall_lines(self.lab, cfg, later)
        self.assertTrue(any(f'#{ids["review"]}' in l for l in lines))
        with patch.object(routine, 'notify') as note:
            routine._alarm(self.lab, cfg, later, lines)
            routine._alarm(self.lab, cfg, later, lines)          # same problem, soon after: no repeat
            self.assertEqual(note.call_count, 1)
            later.advance(hours=7)
            routine._alarm(self.lab, cfg, later, lines)          # still stuck hours later: reminder
            self.assertEqual(note.call_count, 2)
        routine._alarm(self.lab, cfg, later, [])
        self.assertEqual(json.loads((self.lab.root / 'STALL.json').read_text()), {})
        routine.write_views(self.lab, cfg, later)
        self.assertIn('STALLED', (self.lab.root / 'ATTENTION.md').read_text())

    def test_crashed_pass_is_announced(self):
        (self.lab.root / 'ATTENTION.md').write_text('# Needs your attention\n\nold\n')
        with patch.object(routine, 'notify') as note, \
             patch('codexautopsych.program.PROJECT_ROOT', self.lab.root.parent):
            routine.record_pass_error(self.lab, TypeError('x'), clock=self.clock)
            routine.record_pass_error(self.lab, TypeError('x'), clock=self.clock)
        text = (self.lab.root / 'ATTENTION.md').read_text()
        self.assertEqual(text.count('ENGINE PASS FAILING'), 1)
        self.assertTrue(text.startswith('# Needs your attention'))
        self.assertIn('ENGINE PASS FAILING', (self.lab.root.parent / 'STATUS.md').read_text())

    def test_retry_failed_task(self):
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft')
        self.control(codex_script=['boom', 'boom', 'boom'])
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['work']), 'failed')
        routine.retry_task(self.lab, ids['work'])
        self.assertEqual(self.state(ids['work']), 'queued')
        with self.assertRaises(ValueError):
            routine.retry_task(self.lab, ids['work'])

    def test_revision_limit_hands_to_owner(self):
        routine.set_config(self.lab, 'max_revision_rounds', 1)
        self.control(claude_script=['revise', 'revise'])
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        second = json.loads(self.lab.task(ids['review'])['result'])['next_review']
        self.assertEqual(self.state(second), 'awaiting_human')

    def test_reject_blocks_publication(self):
        self.control(claude_script=['reject'])
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='publish')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['review']), 'rejected')
        self.assertEqual(self.state(ids['publish']), 'blocked')

    def test_codex_limit_preserves_state_and_resumes(self):
        self.control(codex_script=["You've hit your usage limit. Try again in 3 hours 10 minutes."])
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft')
        out = self.run_pass()
        task = self.lab.task(ids['work'])
        self.assertEqual((task['state'], task['attempts']), ('queued', 0))
        self.assertTrue(out['services']['codex']['paused'])
        execs = len(self.calls('codex', 'exec'))
        self.clock.advance(hours=1)
        self.run_pass()
        self.assertEqual(len(self.calls('codex', 'exec')), execs)  # no call while paused
        self.clock.advance(hours=3)
        self.run_pass()
        self.assertEqual(self.state(ids['work']), 'succeeded')

    def test_claude_limit_pauses_only_claude_and_manual_decision_still_works(self):
        reset = int((self.clock() + timedelta(hours=5)).timestamp())
        self.control(claude_script=[f'Claude AI usage limit reached|{reset}'])
        ids = routine.enqueue_work(self.lab, 'draft', 'First', checkpoint='review')
        other = routine.enqueue_work(self.lab, 'summary', 'Second, no checkpoint')
        out = self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['work']), 'succeeded')
        self.assertEqual(self.state(other['work']), 'succeeded')  # Codex kept working
        self.assertEqual(self.state(ids['review']), 'queued')
        self.assertEqual(self.lab.task(ids['review'])['attempts'], 0)
        self.assertTrue(out['services']['claude']['paused'])
        self.assertFalse(out['services']['codex']['paused'])
        routine.decide(self.lab, ids['review'], 'approve', 'Reviewed in the Claude app')
        self.assertEqual(self.state(ids['review']), 'succeeded')

    def test_review_verdict_is_saved_as_readable_file(self):
        self.control(claude_script=['revise', 'approve'])
        routine.enqueue_work(self.lab, 'draft', 'Readable review', checkpoint='review')
        self.run_pass(max_tasks=10)
        reviews = sorted((self.lab.root / 'reviews').glob('task-*.md'))
        self.assertTrue(reviews)
        text = reviews[0].read_text()
        self.assertIn('Readable review', text)
        self.assertIn('**revise**', text)
        self.assertIn('Add limitations section', text)
        index = (self.lab.root / 'REVIEWS.md').read_text()
        self.assertIn('revise', index)

    def test_claude_failure_reason_is_recorded_without_tokens(self):
        reason = 'Prompt is too long sk-' + 'x' * 30
        self.control(claude_script=[reason] * 5)
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        error = self.lab.task(ids['review'])['error'] or ''
        self.assertIn('Prompt is too long', error)
        self.assertIn('[masked]', error)
        self.assertNotIn('x' * 30, error)

    def test_paid_routes_are_refused_before_any_model_call(self):
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-placeholder'}):
            self.run_pass()
        self.assertEqual(self.calls('codex'), [])
        self.assertEqual(self.lab.task(ids['work'])['attempts'], 0)
        self.assertIn('refused', self.lab.task(ids['work'])['error'])
        routine.resume_service(self.lab, 'codex')
        self.run_pass(max_tasks=1)  # work only; review waits for the next pass
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'synthetic-placeholder'}):
            self.run_pass()
        self.assertEqual(self.calls('claude'), [])
        self.assertEqual(self.state(ids['review']), 'queued')

    def test_claude_api_key_login_is_refused(self):
        self.control(claude_auth='api_key')
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.calls('claude', '-p'), [])
        self.assertEqual(self.state(ids['review']), 'queued')

    def test_codex_without_chatgpt_login_is_refused(self):
        self.control(codex_login=False)
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft')
        self.run_pass()
        self.assertEqual(self.calls('codex', 'exec'), [])
        self.assertEqual(self.state(ids['work']), 'queued')

    def test_daily_cap_pauses_until_tomorrow(self):
        routine.set_config(self.lab, 'codex.max_calls_per_day', 1)
        a = routine.enqueue_work(self.lab, 'draft', 'A')
        b = routine.enqueue_work(self.lab, 'draft', 'B')
        out = self.run_pass(max_tasks=10)
        self.assertEqual(self.state(a['work']), 'succeeded')
        self.assertEqual(self.state(b['work']), 'queued')
        self.assertIn('daily call cap', out['services']['codex']['reason'])
        self.clock.advance(days=1)
        self.run_pass()
        self.assertEqual(self.state(b['work']), 'succeeded')

    def test_manual_mode_writes_packet_and_waits(self):
        routine.set_config(self.lab, 'claude.mode', 'manual')
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft', checkpoint='review')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.state(ids['review']), 'awaiting_human')
        self.assertEqual(self.calls('claude'), [])
        packet = Path(json.loads(self.lab.task(ids['review'])['result'])['packet'])
        self.assertIn('Codex summary', packet.read_text())
        self.assertIn(f'decide {ids["review"]}', (self.lab.root / 'ATTENTION.md').read_text())
        routine.decide(self.lab, ids['review'], 'revise', 'Needs work', ['Fix table 2'])
        self.assertEqual(self.state(ids['review']), 'revised')

    def test_study3_is_protected(self):
        study = Path(self.tmp.name) / 'Study3_project'
        study.mkdir()
        (study / 'protocol.md').write_text('approved protocol')
        # Owner amendment 2026-10-12: completed Study 3 results are readable inputs.
        routine.enqueue_work(self.lab, 'analysis', 'Summarize', inputs=[study / 'protocol.md'])
        with self.assertRaisesRegex(ValueError, 'Study 3'):
            routine.enqueue_work(self.lab, 'routine', 'Restart Study 3 with new settings')
        safe = Path(self.tmp.name) / 'notes.md'
        safe.write_text('notes')
        routine.set_config(self.lab, 'protected_paths', [str(Path(self.tmp.name) / 'private')])
        (Path(self.tmp.name) / 'private').mkdir()
        (Path(self.tmp.name) / 'private' / 'x.md').write_text('x')
        with self.assertRaisesRegex(ValueError, 'protected'):
            routine.enqueue_work(self.lab, 'analysis', 'Read', inputs=[Path(self.tmp.name) / 'private' / 'x.md'])
        ids = routine.enqueue_work(self.lab, 'analysis', 'Summarize notes', inputs=[safe])
        self.run_pass()
        copied = Path(json.loads(self.lab.task(ids['work'])['result'])['outputs_dir']).parent / 'inputs' / 'notes.md'
        self.assertEqual(copied.read_text(), 'notes')

    def test_overlapping_pass_is_refused_and_crash_recovers(self):
        ids = routine.enqueue_work(self.lab, 'draft', 'Draft')
        with self.lab.lock('cycle', 'other-pass'):
            with self.assertRaises(Busy):
                self.run_pass()
            task = routine.claim_work(self.lab, 'other-pass', ['codex'], self.clock)
        self.assertEqual(task['state'], 'running')  # simulated crash: lock released mid-task
        self.run_pass()
        self.assertEqual(self.state(ids['work']), 'succeeded')
        self.assertEqual(self.lab.task(ids['work'])['attempts'], 2)

    def test_synthetic_cycles_ignore_model_tasks(self):
        routine.enqueue_work(self.lab, 'code', 'Write a parser')
        self.assertEqual(self.lab.cycle('coding-worker', 'synthetic-coder', 5), [])

    def test_resume_time_parsing(self):
        now = self.clock()
        self.assertEqual(parse_resume_time('try again in 2 hours 5 minutes', now), now + timedelta(minutes=127))
        at = parse_resume_time("You've hit your usage limit. Try again at 3:05 PM.", now)
        self.assertEqual((at.hour, at.minute, at.date()), (15, 7, now.date()))
        early = parse_resume_time('limit reached, resets 9am', now)
        self.assertEqual(early.date(), now.date() + timedelta(days=1))
        self.assertEqual(parse_resume_time('quota exceeded', now, 60), now + timedelta(minutes=60))
        with self.assertRaises(UsageLimit):
            detect_limit('codex', 'stream error: 429 Too Many Requests', now)
        detect_limit('codex', 'syntax error in file', now)  # ordinary errors are not limits


if __name__ == '__main__':
    unittest.main()
