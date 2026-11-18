import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from codexautopsych.core import Lab, Busy
from codexautopsych.runner import execute, recover, verify_artifacts
from codexautopsych.cli import demo
from codexautopsych.adapter import codex_task

PROTOCOL = {'synthetic': True, 'runner': 'synthetic-offset-v1',
            'phase': 'exploratory', 'values': [1, 2, 3], 'offset': 0}

class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Lab(self.tmp.name)
    def tearDown(self):
        self.lab.close()
        self.tmp.cleanup()
    def register(self, eid='fixture'):
        self.lab.register(eid, PROTOCOL)
    def pipeline(self):
        self.register()
        p = self.lab.enqueue('plan', 'coding-worker', {'experiment': 'fixture'})
        e = self.lab.enqueue('execute', 'coordinator', {'experiment': 'fixture'}, [p])
        a = self.lab.enqueue('audit', 'independent-reviewer', {'experiment': 'fixture'}, [p, e], 1)
        return p, e, a
    def test_dependency_waits_and_unknown_dependency_rejected(self):
        p, e, a = self.pipeline()
        self.assertEqual(self.lab.cycle('coordinator', 'runner', 2), [])
        with self.assertRaises(ValueError):
            self.lab.enqueue('plan', 'coding-worker', {}, [999])
        self.lab.cycle('coding-worker', 'coder', 1)
        self.lab.cycle('coordinator', 'runner', 1)
        self.assertEqual(self.lab.task(e)['state'], 'succeeded')
    def test_duplicate_execution_preserves_raw(self):
        self.register()
        first = execute(self.lab, 'fixture', 'runner')
        path = Path(self.lab.experiment('fixture')['log_path'])
        before = path.read_bytes()
        second = execute(self.lab, 'fixture', 'another-runner')
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(self.lab.experiment('fixture')['execution_count'], 1)
        self.assertEqual(path.read_bytes(), before)
    def test_cross_process_execution_owner_lock(self):
        code = ('from codexautopsych.core import Lab,Busy; import sys; l=Lab(sys.argv[1]); '
                '\ntry:\n with l.lock("execution","other"): pass\nexcept Busy: sys.exit(7)')
        with self.lab.lock('execution', 'owner'):
            r = subprocess.run([sys.executable, '-c', code, self.tmp.name], capture_output=True)
            self.assertEqual(r.returncode, 7, r.stderr)
    def test_task_owner_cannot_be_impersonated(self):
        self.lab.enqueue('plan', 'coding-worker', {})
        with self.lab.lock('cycle', 'alice'):
            t = self.lab.claim('coding-worker', 'alice')
            with self.assertRaises(ValueError):
                self.lab.finish(t['id'], 'bob', {})
    def test_reviewer_separation(self):
        p, e, a = self.pipeline()
        self.lab.cycle('coding-worker', 'same-owner', 1)
        self.lab.cycle('coordinator', 'runner', 1)
        self.lab.cycle('independent-reviewer', 'same-owner', 1)
        self.assertEqual(self.lab.task(a)['state'], 'failed')
        self.assertIn('distinct', self.lab.task(a)['error'])
    def test_reviewer_checks_raw_and_integrity(self):
        p, e, a = self.pipeline()
        self.lab.cycle('coding-worker', 'coder', 1)
        self.lab.cycle('coordinator', 'runner', 1)
        Path(self.lab.experiment('fixture')['log_path']).write_text('tampered\n')
        self.lab.cycle('independent-reviewer', 'reviewer', 1)
        self.assertEqual(self.lab.task(a)['state'], 'failed')
        self.assertIn('integrity', self.lab.task(a)['error'])
    def test_manifest_negative_result_cannot_be_changed(self):
        self.register()
        execute(self.lab, 'fixture', 'runner')
        path = Path(self.tmp.name) / 'runs' / 'fixture' / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['negative_result'] = False
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'metadata'):
            verify_artifacts(self.lab, self.lab.experiment('fixture'))

    def test_protocol_tamper_prevents_launch(self):
        self.register()
        with self.lab.transaction():
            self.lab.db.execute('UPDATE experiments SET protocol=? WHERE id=?',
                                (json.dumps(dict(PROTOCOL, offset=1)), 'fixture'))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            execute(self.lab, 'fixture', 'runner')
        self.assertEqual(self.lab.experiment('fixture')['execution_count'], 0)
    def test_bounded_retries_and_failed_dependency_blocks(self):
        t = self.lab.enqueue('plan', 'coding-worker', {'experiment': 'missing'}, max_attempts=2)
        child = self.lab.enqueue('execute', 'coordinator', {}, [t])
        self.lab.cycle('coding-worker', 'coder', 10)
        self.lab.cycle('coordinator', 'runner', 1)
        self.assertEqual(self.lab.task(t)['attempts'], 2)
        self.assertEqual(self.lab.task(t)['state'], 'failed')
        self.assertEqual(self.lab.task(child)['state'], 'blocked')
    def test_process_death_recovers_task_and_releases_lock(self):
        self.register()
        t = self.lab.enqueue('plan', 'coding-worker', {'experiment': 'fixture'})
        code = ('from codexautopsych.core import Lab; import os,sys; l=Lab(sys.argv[1]); '
                '\nwith l.lock("cycle","dead-worker"):\n l.claim("coding-worker","dead-worker"); os._exit(9)')
        r = subprocess.run([sys.executable, '-c', code, self.tmp.name])
        self.assertEqual(r.returncode, 9)
        self.assertEqual(self.lab.task(t)['state'], 'running')
        self.lab.cycle('coding-worker', 'replacement', 1)
        self.assertEqual(self.lab.task(t)['state'], 'succeeded')
        self.assertEqual(self.lab.task(t)['attempts'], 2)
    def test_interrupted_execution_never_relaunches(self):
        self.register()
        code = ('from codexautopsych.core import Lab,now; import os,sys; l=Lab(sys.argv[1]); '
                '\nwith l.lock("execution","dead-runner"):\n'
                ' with l.transaction():\n'
                '  l.db.execute("UPDATE experiments SET status=\'running\',execution_count=1 WHERE id=\'fixture\'")\n'
                ' os._exit(9)')
        subprocess.run([sys.executable, '-c', code, self.tmp.name], check=False)
        with self.assertRaisesRegex(ValueError, 'not launchable'):
            execute(self.lab, 'fixture', 'replacement')
        exp = self.lab.experiment('fixture')
        self.assertEqual(exp['status'], 'interrupted')
        self.assertEqual(exp['execution_count'], 1)
        self.assertFalse(exp['verified'])
    def test_completed_artifacts_reconcile_after_crash_before_commit(self):
        self.register()
        execute(self.lab, 'fixture', 'runner')
        with self.lab.transaction():
            self.lab.db.execute("UPDATE experiments SET status='running',verified=0,raw_hash=NULL")
        with self.lab.lock('execution', 'recovery'):
            recover(self.lab)
        exp = self.lab.experiment('fixture')
        self.assertEqual(exp['status'], 'completed')
        self.assertTrue(exp['verified'])
        self.assertEqual(exp['execution_count'], 1)
    def test_contexts_are_scoped_and_roles_enforced(self):
        self.lab.enqueue('plan', 'coding-worker', {'private': 'first'})
        t = self.lab.enqueue('plan', 'coding-worker', {'private': 'second'})
        context = self.lab.context(self.lab.task(t))
        self.assertNotIn('first', json.dumps(context))
        self.assertIn('NOT a live AI agent', context['worker_type'])
        with self.assertRaises(ValueError):
            self.lab.enqueue('audit', 'coding-worker', {})
    def test_real_protocol_rejected(self):
        with self.assertRaises(ValueError):
            self.lab.register('real', dict(PROTOCOL, synthetic=False), approval='user-approved')
    def test_claim_requires_grounding(self):
        with self.assertRaises(ValueError):
            self.lab.add_claim({'claim': 'Unsupported claim'})
    def test_demo_restart(self):
        first = demo(self.tmp.name)
        second = demo(self.tmp.name)
        self.assertEqual(first['tasks_succeeded'], 4)
        self.assertEqual(second['execution_count_after_restart'], 1)
        manifest = verify_artifacts(self.lab, self.lab.experiment('synthetic-demo-v1'))
        self.assertTrue(manifest['negative_result'])
        self.assertTrue((Path(self.tmp.name) / 'HANDOFF.md').is_file())
    def test_actual_runner_crash_after_raw_write_does_not_repeat(self):
        self.register()
        code = ('from codexautopsych.core import Lab; import codexautopsych.runner as r; import os,sys; '
                'l=Lab(sys.argv[1]); r.write_atomic=lambda *a: os._exit(17); r.execute(l,"fixture","crashed")')
        result = subprocess.run([sys.executable, '-c', code, self.tmp.name])
        self.assertEqual(result.returncode, 17)
        raw = Path(self.lab.experiment('fixture')['log_path'])
        before = raw.read_bytes()
        with self.assertRaisesRegex(ValueError, 'not launchable'):
            execute(self.lab, 'fixture', 'replacement')
        self.assertEqual(before, raw.read_bytes())
        self.assertEqual(self.lab.experiment('fixture')['execution_count'], 1)
        self.assertEqual(self.lab.experiment('fixture')['status'], 'interrupted')

    def test_actual_crash_after_manifest_reconciles_without_launch(self):
        self.register()
        code = ('from codexautopsych.core import Lab; import codexautopsych.runner as r; import os,sys; '
                'l=Lab(sys.argv[1]); r.verify_artifacts=lambda *a: os._exit(18); r.execute(l,"fixture","crashed")')
        result = subprocess.run([sys.executable, '-c', code, self.tmp.name])
        self.assertEqual(result.returncode, 18)
        self.assertEqual(self.lab.experiment('fixture')['status'], 'running')
        result = execute(self.lab, 'fixture', 'replacement')
        self.assertTrue(result['reused'])
        self.assertEqual(self.lab.experiment('fixture')['execution_count'], 1)
        self.assertTrue(self.lab.experiment('fixture')['verified'])

    def test_recovery_refuses_without_lock(self):
        with self.assertRaisesRegex(ValueError, 'lock'):
            recover(self.lab)
        with self.assertRaisesRegex(ValueError, 'lock'):
            self.lab._recover_tasks()

    def test_adapter_routes_chatgpt_and_uses_separate_readonly_context(self):
        seen = {}
        class Process:
            returncode = 0
            pid = 12345
            def __init__(self, args, **kwargs):
                seen['args'] = args
                seen['kwargs'] = kwargs
                Path(args[args.index('-o') + 1]).write_text('Synthetic mocked advisory response')
            def communicate(self, prompt, timeout):
                seen['prompt'] = prompt
                seen['timeout'] = timeout
                return ('', '')
        with patch.dict(os.environ, {}, clear=True), \
             patch('codexautopsych.adapter.shutil.which', return_value='/fake/codex'), \
             patch('codexautopsych.adapter.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='Logged in using ChatGPT')), \
             patch('codexautopsych.adapter.subprocess.Popen', Process):
            result = codex_task({'role': 'independent-reviewer', 'payload': {'synthetic': True}}, self.tmp.name, enabled=True)
        self.assertIn('forced_login_method="chatgpt"', seen['args'])
        self.assertIn('read-only', seen['args'])
        self.assertIn('--ignore-user-config', seen['args'])
        self.assertIn('--ephemeral', seen['args'])
        self.assertIn('independent-reviewer', seen['prompt'])
        self.assertEqual(result['output'], 'Synthetic mocked advisory response')
        self.assertEqual(seen['timeout'], 120)

    def test_adapter_timeout_kills_process_group_without_fallback(self):
        class Process:
            pid = 12345
            calls = 0
            def communicate(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired('synthetic-test', 1)
                return ('', '')
        with patch.dict(os.environ, {}, clear=True), \
             patch('codexautopsych.adapter.shutil.which', return_value='/fake/codex'), \
             patch('codexautopsych.adapter.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='Logged in using ChatGPT', stderr='')), \
             patch('codexautopsych.adapter.subprocess.Popen', return_value=Process()) as popen, \
             patch('codexautopsych.adapter.os.killpg') as kill:
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                codex_task({'role': 'coding-worker'}, self.tmp.name, enabled=True, timeout=1)
            self.assertEqual(popen.call_count, 1)
            kill.assert_called_once()

    def test_adapter_is_disabled_and_blocks_api_route(self):
        with self.assertRaisesRegex(ValueError, 'disabled'):
            codex_task({'role': 'coordinator'}, '.')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-value'}):
            with self.assertRaisesRegex(RuntimeError, 'API key'):
                codex_task({'role': 'coordinator'}, '.', enabled=True)

if __name__ == '__main__':
    unittest.main()
