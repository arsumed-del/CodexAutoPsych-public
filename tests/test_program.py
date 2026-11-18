"""Synthetic director fixtures and fake service responses; no live model calls."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codexautopsych import program, routine
from codexautopsych.core import Lab, Busy, canonical, digest


def proposal(slug='synthetic-loop', in_silico=True):
    text = '# SYNTHETIC preregistration fixture; not psychological evidence\n'
    for section in program.SECTIONS:
        text += f'\n## {section}\nSynthetic fixture citing program/sources/theory.md: arithmetic offset = 0.\n'
    return text + '\n```json\n' + json.dumps({'slug': slug, 'title': 'Synthetic loop',
        'in_silico': in_silico, 'gpu_hours': 0}) + '\n```\n'


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'program'
        (self.folder / 'sources').mkdir(parents=True)
        (self.folder / 'CHARTER.md').write_text('SYNTHETIC charter fixture for coordinator tests.')
        (self.folder / 'sources' / 'theory.md').write_text('SYNTHETIC theory fixture.')
        self.lab = Lab(self.root / '.lab')
        self.time = datetime(2026, 10, 8, tzinfo=timezone.utc)
        self.clock = lambda: self.time
        self.patch_root = patch.object(program, 'PROJECT_ROOT', self.root)
        self.patch_root.start()
        self.fake_codex = patch.object(routine.svc, 'run_codex', side_effect=self.worker).start()
        self.fake_claude = patch.object(routine.svc, 'run_claude_review', return_value={
            'decision': 'approve', 'summary': 'SYNTHETIC fake review', 'required_changes': []}).start()
        self.addCleanup(patch.stopall)
        routine.set_config(self.lab, 'notify', False)
        routine.set_config(self.lab, 'program.enabled', True)
        routine.set_config(self.lab, 'codex.enabled', True)
        routine.set_config(self.lab, 'claude.mode', 'cli')
        self.in_silico = True
        self.bad_validation = False
        self.evidence = "SYNTHETIC mocked validation record, not actual analysis."
        self.drafts = None

    def tearDown(self):
        self.lab.close()
        self.tmp.cleanup()

    def worker(self, prompt, workspace, **kwargs):
        """Explicitly mocked worker; never executes generated code."""
        out = workspace / 'outputs'
        if 'Title: Director:' in prompt:
            (out / 'proposals').mkdir()
            for name, text in (self.drafts or {'synthetic-loop.md': proposal(in_silico=self.in_silico)}).items():
                (out / 'proposals' / name).write_text(text)
        elif 'Title: Refine preregistration:' in prompt:
            (out / 'PREREG.md').write_text(proposal(in_silico=self.in_silico))
        elif 'Title: Build study harness:' in prompt:
            (out / 'harness').mkdir()
            code = b'# SYNTHETIC fake harness; never executed\n'
            (out / 'harness' / 'analysis.py').write_bytes(code)
            (out / 'VALIDATION.json').write_text(json.dumps({
                'synthetic': True, 'planted_effect_passed': not self.bad_validation,
                'null_effect_passed': True, 'harness_sha256': {'analysis.py': digest(code)},
                'evidence': self.evidence}))
        else:
            raise AssertionError('Unexpected fake worker prompt')
        return 'SYNTHETIC fake worker output.'

    def run_pass(self, **kw):
        return routine.run(self.lab, 'synthetic-scheduler', clock=self.clock, **kw)

    def tasks(self, stage):
        return [dict(r) for r in self.lab.db.execute('SELECT * FROM tasks')
                if json.loads(r['payload']).get('program', {}).get('stage') == stage]

    def test_defaults_and_validation(self):
        self.assertFalse(routine.DEFAULT_CONFIG['program']['enabled'])
        self.assertFalse(routine.DEFAULT_CONFIG['program']['execute_experiments'])
        for key, value in [('max_proposals', 0), ('enabled', 'true'),
                           ('director_interval_days', True), ('study3_process_patterns', [])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                routine.set_config(self.lab, 'program.' + key, value)

    def test_program_configuration_does_not_enable_other_labs(self):
        other = Lab(self.root / '.lab' / 'other')
        try:
            routine.set_config(other, 'program.enabled', True)
            self.assertFalse(routine.DEFAULT_CONFIG['program']['enabled'])
            (other.root / 'config.json').unlink()
            self.assertFalse(routine.load_config(other)['program']['enabled'])
        finally:
            other.close()

    def test_due_and_interval_including_disabled_and_boundary(self):
        cfg = routine.load_config(self.lab)
        self.assertTrue(program.director_due(self.lab, cfg, self.clock))
        routine.set_config(self.lab, 'codex.enabled', False)
        self.run_pass()
        self.run_pass()
        self.assertEqual(len(self.tasks('director')), 1)
        self.assertFalse(program.director_due(self.lab, cfg, self.clock))
        self.time += timedelta(days=7)
        self.assertTrue(program.director_due(self.lab, cfg, self.clock))
        self.run_pass()
        self.assertEqual(len(self.tasks('director')), 2)
        cfg['program']['enabled'] = False
        self.time += timedelta(days=7)
        self.assertFalse(program.director_due(self.lab, cfg, self.clock))

    def test_disabled_does_not_read_program_or_call_workers(self):
        routine.set_config(self.lab, 'program.enabled', False)
        with patch.object(program, '_folder', side_effect=AssertionError('Unexpected access')):
            self.run_pass()
        self.fake_codex.assert_not_called()
        self.fake_claude.assert_not_called()

    def test_requires_cycle_lock(self):
        with self.assertRaises(ValueError):
            program.maybe_enqueue_director(self.lab, routine.load_config(self.lab), self.clock)
        with self.lab.lock('cycle', 'other'):
            with self.assertRaises(Busy):
                self.run_pass()

    def test_snapshot_preserves_sources_with_same_basename(self):
        (self.folder / 'sources' / 'nested').mkdir()
        (self.folder / 'sources' / 'nested' / 'theory.md').write_text('Second SYNTHETIC source')
        routine.set_config(self.lab, 'codex.enabled', False)
        self.run_pass()
        payload = json.loads(self.tasks('director')[0]['payload'])
        inputs = [Path(p) for p in payload['inputs']]
        self.assertEqual(len(set(p.name for p in inputs)), len(inputs))
        (self.folder / 'sources' / 'theory.md').write_text('Changed')
        contents = [p.read_text() for p in inputs]
        self.assertIn('SYNTHETIC theory fixture.', contents)
        state = json.loads((self.folder / 'state.json').read_text())
        self.assertIn('claims', state)
        self.assertIn('recent_study_outcomes', state)
        self.assertEqual(self.tasks('director')[0]['kind'], 'analysis')

    def test_proposals_become_refine_review_and_malformed_skipped(self):
        self.drafts = {'synthetic-loop.md': proposal(), 'broken.md': 'SYNTHETIC missing JSON'}
        self.run_pass(max_tasks=1)
        self.assertEqual(len(self.tasks('refine')), 1)
        refine = self.tasks('refine')[0]
        self.assertEqual(refine['kind'], 'draft')
        reviews = self.lab.db.execute("SELECT * FROM tasks WHERE kind='review'").fetchall()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]['service'], 'claude')
        self.assertTrue(self.lab.db.execute("SELECT 1 FROM events WHERE category='program-proposal-skipped'").fetchone())

    def test_proposal_cap(self):
        routine.set_config(self.lab, 'program.max_proposals', 1)
        self.drafts = {f'{s}.md': proposal(s) for s in ['a', 'b']}
        self.run_pass(max_tasks=1)
        self.assertEqual(len(self.tasks('refine')), 1)

    def test_bad_metadata_and_missing_sections(self):
        for text in [proposal().replace('"gpu_hours": 0', '"gpu_hours": NaN'),
                     proposal().replace('"in_silico": true', '"in_silico": "true"'),
                     proposal('../escape'), proposal().replace('## Controls', '## Other')]:
            with self.subTest(text=text[-100:]), self.assertRaises(ValueError):
                program.parse_proposal(text)

    def test_delegated_approval_hash_claim_and_build_queue(self):
        self.run_pass(max_tasks=3)
        study = self.folder / 'studies' / 'synthetic-loop'
        approval = json.loads((study / 'APPROVAL.json').read_text())
        self.assertEqual(approval['sha256'], digest((study / 'PREREG.md').read_bytes()))
        self.assertEqual(approval['policy'], program.POLICY)
        self.assertEqual(approval['status'], program.STATUS)
        self.assertEqual(self.lab.task(approval['review_task'])['state'], 'succeeded')
        claims = list(self.lab.db.execute('SELECT record FROM claims'))
        self.assertEqual(len(claims), 1)
        self.assertEqual(json.loads(claims[0]['record'])['status'], 'pending')
        self.assertEqual(len(self.tasks('build')), 1)
        self.assertEqual(self.tasks('build')[0]['kind'], 'code')
        self.assertIn('deposit publicly', (self.lab.root / 'ATTENTION.md').read_text())

    def test_no_automatic_approval_when_policy_disabled(self):
        routine.set_config(self.lab, 'program.auto_approve_in_silico', False)
        self.run_pass(max_tasks=10)
        self.assertFalse((self.folder / 'studies').exists())
        self.assertEqual(self.tasks('build'), [])
        self.assertTrue(self.lab.db.execute("SELECT 1 FROM tasks WHERE state='awaiting_human'").fetchone())

    def test_no_automatic_approval_for_non_in_silico(self):
        self.in_silico = False
        self.run_pass(max_tasks=10)
        self.assertFalse((self.folder / 'studies').exists())
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM claims').fetchone()[0], 0)

    def test_build_freeze_ready_no_execution_or_publication_even_when_enabled(self):
        routine.set_config(self.lab, 'program.execute_experiments', True)
        self.run_pass(max_tasks=10)
        study = self.folder / 'studies' / 'synthetic-loop'
        approval = json.loads((study / 'APPROVAL.json').read_text())
        hashes = {'analysis.py': routine._sha(study / 'harness' / 'analysis.py')}
        self.assertEqual(approval['harness_sha256'], hashes)
        self.assertEqual(approval['code_sha256'], digest(canonical(hashes).encode()))
        self.assertIn('ready to run', (self.lab.root / 'ATTENTION.md').read_text())
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM experiments').fetchone()[0], 0)
        self.assertFalse(self.lab.db.execute("SELECT 1 FROM tasks WHERE kind='publish' OR state='published'").fetchone())

    def test_structured_synthetic_evidence_preserved_on_install(self):
        self.evidence = {'explanation': 'SYNTHETIC mocked validation.', 'tests_run': 13}
        self.run_pass(max_tasks=10)
        study = self.folder / 'studies/synthetic-loop'
        validation = json.loads((study / 'VALIDATION.json').read_text())
        self.assertEqual(validation['evidence'], self.evidence)
        self.assertIn('code_sha256', json.loads((study / 'APPROVAL.json').read_text()))

    def test_structured_evidence_requires_nonempty_explanation(self):
        self.evidence = {'explanation': '   ', 'tests_run': 13}
        self.run_pass(max_tasks=10)
        approval = json.loads((self.folder / 'studies/synthetic-loop/APPROVAL.json').read_text())
        self.assertNotIn('code_sha256', approval)
        self.assertIn('artifact checks failed', (self.lab.root / 'ATTENTION.md').read_text())

    def test_invalid_synthetic_validation_waits_for_owner(self):
        self.bad_validation = True
        self.run_pass(max_tasks=10)
        approval = json.loads((self.folder / 'studies/synthetic-loop/APPROVAL.json').read_text())
        self.assertNotIn('code_sha256', approval)
        self.assertIn('artifact checks failed', (self.lab.root / 'ATTENTION.md').read_text())

    def test_restart_does_not_duplicate_claims_tasks_or_overwrite_prereg(self):
        self.run_pass(max_tasks=10)
        before = self.lab.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        self.lab.close()
        self.lab = Lab(self.root / '.lab')
        self.run_pass(max_tasks=10)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], before)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM claims').fetchone()[0], 1)
        self.time += timedelta(days=7)
        self.run_pass(max_tasks=10)
        self.assertEqual(len(self.tasks('refine')), 1)

    def test_revisions_use_latest_reviewed_prereg(self):
        self.fake_claude.side_effect = [
            {'decision': 'revise', 'summary': 'SYNTHETIC revision', 'required_changes': ['Clarify nulls']},
            {'decision': 'approve', 'summary': 'SYNTHETIC approval', 'required_changes': []},
            {'decision': 'approve', 'summary': 'SYNTHETIC build approval', 'required_changes': []}]
        self.run_pass(max_tasks=10)
        approval = json.loads((self.folder / 'studies/synthetic-loop/APPROVAL.json').read_text())
        review = self.lab.task(approval['review_task'])
        subject = self.lab.task(json.loads(review['payload'])['subject'])
        self.assertEqual(subject['kind'], 'revise')
        self.assertEqual(len(self.tasks('build')), 1)

    def test_continuous_mode_shelves_at_revision_limit_instead_of_waiting(self):
        routine.set_config(self.lab, 'continuous', True)
        rev = {'decision': 'revise', 'summary': 'SYNTHETIC revision', 'required_changes': ['More']}
        self.fake_claude.side_effect = [rev, rev, rev, rev]
        self.run_pass(max_tasks=12)
        self.assertFalse(self.lab.db.execute("SELECT 1 FROM tasks WHERE state='awaiting_human'").fetchone())
        record = json.loads(self.lab.db.execute(
            "SELECT record FROM program_studies WHERE slug='synthetic-loop'").fetchone()[0])
        self.assertEqual(record['phase'], 'shelved')
        self.assertIn('continuous mode', record['shelved_reason'])

    def test_manual_review_follow_up_next_pass(self):
        routine.set_config(self.lab, 'claude.mode', 'manual')
        self.run_pass(max_tasks=3)
        review = dict(self.lab.db.execute("SELECT * FROM tasks WHERE kind='review'").fetchone())
        self.assertEqual(review['state'], 'awaiting_human')
        routine.decide(self.lab, review['id'], 'approve', 'SYNTHETIC relayed review')
        self.run_pass(max_tasks=1)
        self.assertTrue((self.folder / 'studies/synthetic-loop/PREREG.md').exists())
        self.fake_claude.assert_not_called()

    def test_tampered_outputs_never_approved(self):
        self.run_pass(max_tasks=2)
        task = self.tasks('refine')[0]
        outputs = Path(json.loads(task['result'])['outputs_dir'])
        (outputs / 'PREREG.md').write_text(proposal() + '\nChanged after completion')
        self.run_pass(max_tasks=3)
        self.assertFalse((self.folder / 'studies').exists())

    def test_frozen_prereg_preserved_on_build(self):
        self.run_pass(max_tasks=3)
        path = self.folder / 'studies/synthetic-loop/PREREG.md'
        path.write_text('Owner changed file; preserve and flag')
        self.run_pass(max_tasks=10)
        self.assertEqual(path.read_text(), 'Owner changed file; preserve and flag')
        self.assertIn('artifact checks failed', (self.lab.root / 'ATTENTION.md').read_text())

    def test_symlink_source_refused_without_reading_target(self):
        (self.folder / 'sources' / 'linked').symlink_to(self.root / 'nonexistent')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.run_pass()
        self.fake_codex.assert_not_called()

    def test_completed_study3_results_are_readable(self):
        for name in ['affect-suppression-steering', 'Study3_project']:
            with self.subTest(name=name):
                target = self.root / name / 'results.md'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('results')
                self.assertEqual(routine.guard_input(routine.DEFAULT_CONFIG, target), str(target.resolve()))

    def test_resource_diagnostics_distinguish_permission_from_workload(self):
        import subprocess
        cfg = routine.load_config(self.lab)
        with patch.object(program.subprocess, 'run', side_effect=PermissionError):
            self.assertEqual(program.resource_status(cfg),
                             {'free': False, 'reason': 'process-inspection-denied'})
        with patch.object(program.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'study3')):
            self.assertEqual(program.resource_status(cfg),
                             {'free': False, 'reason': 'protected-workload-detected'})
        with patch.object(program.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '')):
            self.assertEqual(program.resource_status(cfg)['reason'], 'process-inspection-failed')

    def test_gpu_check_fake_processes_and_lock(self):
        cfg = routine.load_config(self.lab)
        cfg['lab_root'] = str(self.lab.root)
        with self.lab.lock('execution', 'fixture'):
            with patch.object(program.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'idle\n')):
                self.assertFalse(program.gpu_free(cfg))
        with patch.object(program.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, 'python affect-suppression-steering/worker.py\n')) as ps:
            self.assertFalse(program.gpu_free(cfg))
            self.assertEqual(ps.call_args.args[0], ['ps', '-axo', 'command'])
        with patch.object(program.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'idle\n')):
            self.assertTrue(program.gpu_free(cfg))
        with patch.object(program.subprocess, 'run', side_effect=OSError):
            self.assertFalse(program.gpu_free(cfg))

    def test_nested_transaction_rolls_back_follow_up_and_claim(self):
        self.run_pass(max_tasks=2)
        with patch.object(program, '_queue', side_effect=ValueError('Synthetic interrupted enqueue')):
            self.run_pass(max_tasks=1)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM claims').fetchone()[0], 0)
        self.assertEqual(self.tasks('build'), [])


if __name__ == '__main__':
    unittest.main()
