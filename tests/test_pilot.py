"""Tests of the explicitly SYNTHETIC computational pilot; no live AI agents."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codexautopsych.core import Lab, Busy, canonical, digest
from codexautopsych import pilot, dynamics, routine
from codexautopsych.runner import execute, verify_artifacts


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Lab(Path(self.tmp.name) / '.lab')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.lab.close)

    def protocol(self, gains=None):
        return {'synthetic': True, 'runner': dynamics.RUNNER, 'phase': 'exploratory',
                'gains': gains or [0.25, 0.5, 0.75], 'damping': 0.5, 'steps': 24}

    def test_complete_autonomous_synthetic_loop_without_models(self):
        with patch('subprocess.Popen', side_effect=AssertionError('No subprocess permitted')), \
             patch.object(routine.svc, 'run_codex', side_effect=AssertionError('No model permitted')), \
             patch.object(routine.svc, 'run_claude_review', side_effect=AssertionError('No model permitted')):
            result = pilot.run(self.lab)
        self.assertEqual(len(result['rounds']), 3)
        self.assertEqual(result['rounds'][-1]['growth_bracket'], [0.5, 0.625])
        self.assertFalse(result['solved'])
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM research_outcomes').fetchone()[0], 3)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM claims').fetchone()[0], 3)
        self.assertEqual(self.lab.db.execute("SELECT COUNT(*) FROM tasks WHERE state='succeeded'").fetchone()[0], 12)
        for row in self.lab.db.execute('SELECT record FROM research_outcomes'):
            outcome = json.loads(row[0])
            self.assertEqual(outcome['scope'], 'synthetic')
            self.assertEqual(outcome['source'], 'automatic synthetic-runner collection')
            self.assertEqual(outcome['sha256'], digest((self.lab.root / outcome['snapshot']).read_bytes()))

    def test_restart_does_not_duplicate_launches_tasks_claims_or_outcomes(self):
        first = pilot.run(self.lab)
        second = pilot.run(self.lab)
        self.assertEqual(first, second)
        self.assertEqual(self.lab.db.execute('SELECT SUM(execution_count) FROM experiments').fetchone()[0], 3)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 12)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM claims').fetchone()[0], 3)

    def test_negative_results_are_recorded(self):
        self.lab.register('synthetic-negative', self.protocol([0.1, 0.2]))
        execute(self.lab, 'synthetic-negative', 'synthetic-executor')
        exp = self.lab.experiment('synthetic-negative')
        self.assertTrue(verify_artifacts(self.lab, exp)['negative_result'])
        rows = [json.loads(s) for s in Path(exp['log_path']).read_text().splitlines()]
        analysis = dynamics.analyze(self.protocol([0.1, 0.2]), rows)
        self.assertTrue(analysis['negative_result'])
        self.assertIsNone(analysis['growth_bracket'])

    def test_independent_closed_form_catches_bad_recurrence(self):
        rows = list(dynamics.generate(self.protocol()))
        rows[-1]['value'] = 99
        with self.assertRaisesRegex(ValueError, 'closed-form'):
            dynamics.verify(self.protocol(), rows)

    def test_mutated_raw_cannot_be_collected_on_restart(self):
        pilot.run(self.lab)
        path = self.lab.root / 'runs' / (pilot.PREFIX + '1') / 'raw.jsonl'
        path.write_text('Preserve this altered original for investigation')
        result = pilot.run(self.lab)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(path.read_text(), 'Preserve this altered original for investigation')
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM research_outcomes').fetchone()[0], 3)

    def test_interrupted_launch_never_resets_or_retries(self):
        with self.lab.lock('cycle', 'synthetic-test'):
            eid = pilot._ensure_round(self.lab, 1, [0.25, 0.75])
        with self.lab.transaction():
            self.lab.db.execute("UPDATE experiments SET status='interrupted',execution_count=1 WHERE id=?", (eid,))
        result = pilot.run(self.lab)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.lab.experiment(eid)['status'], 'interrupted')
        self.assertEqual(self.lab.experiment(eid)['execution_count'], 1)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM research_outcomes').fetchone()[0], 0)

    def test_distinct_author_executor_and_auditor(self):
        pilot.run(self.lab)
        owners = {r['kind']: r['owner'] for r in self.lab.db.execute('SELECT kind,owner FROM tasks')}
        self.assertEqual(len({owners['plan'], owners['execute'], owners['audit']}), 3)
        self.assertIn('scaffolds', json.loads((self.lab.root / 'PILOT.json').read_text())['worker_type'])

    def test_refuses_to_drain_live_research_queue(self):
        ids = routine.enqueue_work(self.lab, 'analysis', 'SYNTHETIC pending work')
        with self.assertRaisesRegex(ValueError, 'dedicated'):
            pilot.run(self.lab)
        self.assertEqual(self.lab.task(ids['work'])['attempts'], 0)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM experiments').fetchone()[0], 0)

    def test_execution_lock_still_enforced(self):
        self.lab.register('synthetic-lock-test', self.protocol())
        with self.lab.lock('execution', 'synthetic-other'):
            with self.assertRaises(Busy):
                execute(self.lab, 'synthetic-lock-test', 'synthetic-executor')
        self.assertEqual(self.lab.experiment('synthetic-lock-test')['execution_count'], 0)

    def test_invalid_parameters_and_real_protocols_refused(self):
        for changes in ({'synthetic': False}, {'steps': 1000}, {'gains': [float('nan')]},
                        {'damping': 0}, {'gains': [True]}, {'gains': [0.5, 0.5]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                dynamics.validate({**self.protocol(), **changes})
        with self.assertRaises(ValueError):
            self.lab.register('real', {**self.protocol(), 'synthetic': False})

    def test_original_protocol_preserved_if_next_round_would_differ(self):
        with self.lab.lock('cycle', 'synthetic-test'):
            eid = pilot._ensure_round(self.lab, 1, [0.25, 0.75])
            original = self.lab.experiment(eid)['protocol']
            with self.assertRaises(ValueError):
                pilot._ensure_round(self.lab, 1, [0.1, 0.9])
        self.assertEqual(self.lab.experiment(eid)['protocol'], original)


if __name__ == '__main__':
    unittest.main()
