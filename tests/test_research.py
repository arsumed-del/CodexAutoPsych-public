"""Synthetic feedback-loop tests. Workers and reviews are mocked, never live."""
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codexautopsych import research, routine, program
from codexautopsych.core import Lab
from codexautopsych.services import UsageLimit
from test_program import proposal


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'program/sources').mkdir(parents=True)
        (self.root / 'program/CHARTER.md').write_text('SYNTHETIC charter fixture')
        self.lab = Lab(self.root / '.lab')
        self.time = datetime(2026, 10, 9, tzinfo=timezone.utc)
        self.clock = lambda: self.time
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.lab.close)
        self.patches = [patch.object(program, 'resource_status', return_value={'free': True, 'reason': 'clear'}),
                        patch.object(program, 'PROJECT_ROOT', self.root),
                        patch.object(routine.svc, 'run_codex', side_effect=self.worker),
                        patch.object(routine.svc, 'run_claude_review', return_value={
                            'decision': 'approve', 'summary': 'SYNTHETIC fake review', 'required_changes': []})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        for key, value in [('notify', False), ('research.enabled', True), ('program.enabled', True),
                           ('codex.enabled', True), ('claude.mode', 'cli')]:
            routine.set_config(self.lab, key, value)
        research.add_question(self.lab, {'id': 'synthetic-question', 'question': 'SYNTHETIC effect?',
                                         'falsifier': 'SYNTHETIC null matches effect'})
        self.bad_synthesis = False
        self.reuse_id = None
        self.synthesis_count = 0

    def worker(self, prompt, workspace, **kw):
        out = workspace / 'outputs'
        if 'Title: Research:' in prompt:
            data = json.loads((workspace / 'inputs/EVIDENCE.json').read_text())
            self.synthesis_count += 1
            result = {'summary': 'SYNTHETIC advisory reading, no human inference',
                      'limitations': 'SYNTHETIC reports only; independent replication needed',
                      'outcome_ids': json.loads(re.search(r'Outcome IDs for this round: (\[.*?\])', prompt)[1]),
                      'next_questions': [{'id': self.reuse_id or f'synthetic-next-{self.synthesis_count}',
                                          'question': 'SYNTHETIC alternative explanation?',
                                          'falsifier': 'SYNTHETIC matched null survives'}]}
            if self.bad_synthesis:
                result['solved'] = True
            (out / 'SYNTHESIS.json').write_text(json.dumps(result))
        elif 'Title: Director:' in prompt:
            (out / 'proposals').mkdir()
            # No study execution, even in this fake.
            (out / 'proposals/synthetic-loop.md').write_text(proposal())
        else:
            raise AssertionError('This test must stop before build/refinement calls')
        return 'SYNTHETIC mocked analysis'

    def import_report(self, result='null', scope='synthetic', **kw):
        record = {'question_id': 'synthetic-question', 'scope': scope, 'result': result,
                  'summary': 'SYNTHETIC supplied observation', 'methods': 'SYNTHETIC arithmetic fixture',
                  'limitations': 'SYNTHETIC data only, no psychological evidence', **kw}
        path = self.root / 'program/sources/outcome.json'
        path.write_text(json.dumps(record))
        return research.import_outcome(self.lab, path)

    def run_pass(self, n=1):
        return routine.run(self.lab, max_tasks=n, clock=self.clock)

    def stage_tasks(self, stage):
        return [dict(r) for r in self.lab.db.execute('SELECT * FROM tasks')
                if json.loads(r['payload']).get('program', {}).get('stage') == stage]

    def test_resource_gate_preserves_queue_until_clear(self):
        self.import_report()
        with patch.object(program, 'resource_status', return_value={'free': False, 'reason': 'process-inspection-denied'}), \
                patch.object(routine.svc, 'run_codex') as worker:
            result = self.run_pass()
            worker.assert_not_called()
        self.assertTrue(result['resource_wait'])
        self.assertNotIn('Nothing is waiting for you.', (self.lab.root / 'ATTENTION.md').read_text())
        self.assertIn('process-inspection-denied', (self.lab.root / 'ATTENTION.md').read_text())
        task = self.stage_tasks('synthesis')[0]
        self.assertEqual((task['state'], task['attempts']), ('queued', 0))
        self.assertIn('waiting for the resource gate', (self.lab.root / 'ATTENTION.md').read_text())
        result = self.run_pass()
        self.assertFalse(result['resource_wait'])
        self.assertEqual(self.lab.task(task['id'])['state'], 'succeeded')

    def test_owner_authorized_hosted_planning_does_not_probe_local_workload(self):
        routine.set_config(self.lab, 'research.hosted_planning_during_local_work', True)
        self.import_report()
        with patch.object(program, 'resource_status', side_effect=AssertionError('No local process inspection needed')):
            result = self.run_pass()
        self.assertFalse(result['resource_wait'])
        self.assertEqual(self.stage_tasks('synthesis')[0]['state'], 'succeeded')
        gate = json.loads((self.lab.root / 'RESOURCE_GATE.json').read_text())
        self.assertFalse(gate['local_execution_authorized'])
        self.assertFalse(routine.load_config(self.lab)['program']['execute_experiments'])
        self.assertIn('do not load or run local models', routine.svc.ROUTINE_RULES)
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM experiments').fetchone()[0], 0)

    def test_preserved_idempotent_outcome_and_negative_result(self):
        first = self.import_report(result='negative')
        self.assertEqual(self.import_report(result='negative')['id'], first['id'])
        self.assertEqual(len(research.state(self.lab)['outcomes']), 1)
        (self.root / 'program/sources/outcome.json').write_text('Changed original')
        self.assertEqual(json.loads(research._snapshot(self.lab, first).read_text())['result'], 'negative')
        self.assertFalse(research.state(self.lab)['solved'])

    def test_all_result_categories_preserved(self):
        for result in ('positive', 'negative', 'null', 'inconclusive', 'interrupted'):
            self.import_report(result=result)
        self.assertEqual(len(research.state(self.lab)['outcomes']), 5)

    def test_human_data_and_unknown_questions_refused(self):
        with self.assertRaises(ValueError):
            self.import_report(scope='human')
        with self.assertRaises(ValueError):
            self.import_report(question_id='unknown')
        self.assertEqual(research.state(self.lab)['outcomes'], [])

    def test_source_must_be_an_owner_placed_local_copy(self):
        path = self.root / 'outside.json'
        path.write_text('{}')
        with self.assertRaises(ValueError):
            research.import_outcome(self.lab, path)
        link = self.root / 'program/sources/link.json'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            research.import_outcome(self.lab, link)

    def test_outcome_record_stores_lab_relative_snapshot(self):
        outcome = self.import_report()
        self.assertFalse(Path(outcome['snapshot']).is_absolute())
        self.assertEqual(self.lab.root / outcome['snapshot'], research._snapshot(self.lab, outcome))

    def test_outcome_recorded_on_another_machine_still_queues_synthesis(self):
        outcome = self.import_report()
        foreign = dict(outcome, snapshot='/sessions/other-machine/mnt/x/.lab/research/outcomes/' + outcome['id'] + '.json')
        with self.lab.transaction():
            self.lab.db.execute('UPDATE research_outcomes SET record=? WHERE id=?',
                                (json.dumps(foreign, sort_keys=True), outcome['id']))
        routine.set_config(self.lab, 'codex.enabled', False)
        self.run_pass()
        self.assertEqual(len(self.stage_tasks('synthesis')), 1)

    def test_one_synthesis_round_survives_repeated_passes(self):
        self.import_report()
        routine.set_config(self.lab, 'codex.enabled', False)
        self.run_pass()
        self.run_pass()
        self.assertEqual(len(self.stage_tasks('synthesis')), 1)
        self.assertEqual(self.stage_tasks('director'), [])

    def test_reworded_existing_question_is_saved_as_variant(self):
        self.reuse_id = 'synthetic-question'      # same ID, different wording (live bug 2026-10-12)
        self.import_report()
        self.run_pass(n=2)
        data = research.state(self.lab)
        self.assertEqual(len(data['syntheses']), 1)
        ids = {q['id']: q for q in data['questions']}
        self.assertEqual(ids['synthetic-question']['question'], 'SYNTHETIC effect?')   # original kept
        self.assertIn('synthetic-question-v2', ids)

    def test_approved_synthesis_reopens_planning_and_preserves_open_questions(self):
        self.import_report()
        self.run_pass(n=2)
        data = research.state(self.lab)
        self.assertEqual(len(data['syntheses']), 1)
        self.assertEqual(len(data['questions']), 2)
        self.assertTrue(all(q['status'] == 'open' for q in data['questions']))
        self.run_pass(n=1)
        self.assertEqual(len(self.stage_tasks('director')), 1)
        saved = json.loads((self.root / 'program/state.json').read_text())
        self.assertEqual(saved['research']['outcomes'][0]['result'], 'null')
        self.assertFalse(saved['research']['solved'])
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM experiments').fetchone()[0], 0)

    def test_reviewer_receives_preserved_evidence_and_limitations(self):
        outcome = self.import_report(result='negative')
        self.run_pass(n=1)
        review = dict(self.lab.db.execute("SELECT * FROM tasks WHERE kind='review'").fetchone())
        packet = routine.build_packet(self.lab, review)
        self.assertIn(outcome['id'], packet)
        self.assertIn('SYNTHETIC arithmetic fixture', packet)
        self.assertIn('SYNTHETIC data only, no psychological evidence', packet)
        research._snapshot(self.lab, outcome).write_text('Changed after model analysis')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            routine.build_packet(self.lab, review)

    def test_second_round_preserves_first_and_uses_new_outcomes(self):
        first = self.import_report(result='null')
        self.run_pass(n=2)
        second = self.import_report(result='negative')
        self.run_pass(n=2)
        data = research.state(self.lab)
        self.assertEqual(len(data['syntheses']), 2)
        self.assertEqual(data['syntheses'][0]['outcome_ids'], [first['id']])
        self.assertEqual(data['syntheses'][1]['outcome_ids'], [second['id']])
        self.assertEqual(len(data['questions']), 3)
        self.assertFalse(data['solved'])

    def test_revision_keeps_evidence_ids_and_accepts_latest_review(self):
        outcome = self.import_report()
        with patch.object(routine.svc, 'run_claude_review', side_effect=[
            {'decision': 'revise', 'summary': 'SYNTHETIC revision', 'required_changes': ['Check null']},
            {'decision': 'approve', 'summary': 'SYNTHETIC approval', 'required_changes': []}]):
            self.run_pass(n=4)
        synthesis = research.state(self.lab)['syntheses'][0]
        self.assertEqual(synthesis['outcome_ids'], [outcome['id']])
        self.assertEqual(self.lab.task(synthesis['subject'])['kind'], 'revise')

    def parked(self):
        return self.lab.db.execute("SELECT 1 FROM tasks WHERE kind='review' AND state='awaiting_human'").fetchone()

    def test_synthesis_cannot_mark_psychology_solved(self):
        self.import_report()
        self.bad_synthesis = True
        self.run_pass(n=2)
        self.assertEqual(research.state(self.lab)['syntheses'], [])
        self.assertFalse(self.parked())        # advisory step: one more automatic try on a later pass
        self.run_pass(n=1)
        self.assertEqual(research.state(self.lab)['syntheses'], [])
        self.assertTrue(self.parked())         # failed twice: now Arthur's call
        self.assertFalse(research.state(self.lab)['solved'])

    def test_rejected_review_cannot_create_questions_or_release_planning(self):
        self.import_report()
        with patch.object(routine.svc, 'run_claude_review', return_value={
                'decision': 'reject', 'summary': 'SYNTHETIC rejection', 'required_changes': []}):
            self.run_pass(n=2)
        self.assertEqual(len(research.state(self.lab)['questions']), 1)
        self.assertIsNotNone(research.planning_status(self.lab, routine.load_config(self.lab))['blocked_reason'])

    def test_owner_can_withdraw_rejected_report_without_erasing_it(self):
        outcome = self.import_report()
        with patch.object(routine.svc, 'run_claude_review', return_value={
                'decision': 'reject', 'summary': 'SYNTHETIC rejection', 'required_changes': []}):
            self.run_pass(n=2)
        research.withdraw_outcome(self.lab, outcome['id'], 'SYNTHETIC incorrect methods; preserve history')
        self.assertEqual(len(research.state(self.lab)['outcomes']), 1)
        self.assertTrue(research._snapshot(self.lab, outcome).exists())
        self.assertIsNone(research.planning_status(self.lab, routine.load_config(self.lab))['blocked_reason'])
        self.assertEqual(len(research.state(self.lab)['withdrawals']), 1)

    def test_no_unbounded_directors_when_previous_is_waiting(self):
        routine.set_config(self.lab, 'codex.enabled', False)
        self.run_pass()
        self.time += timedelta(days=30)
        self.run_pass()
        self.assertEqual(len(self.stage_tasks('director')), 1)

    def test_ready_studies_do_not_block_planning(self):
        routine.set_config(self.lab, 'research.max_open_studies', 1)
        with self.lab.transaction():
            self.lab.db.execute('INSERT INTO program_studies VALUES(?,?)',
                                ('synthetic-study', json.dumps({'slug': 'synthetic-study', 'phase': 'ready to run'})))
        status = research.planning_status(self.lab, routine.load_config(self.lab))
        self.assertEqual(status['capacity'], 1)
        routine.set_config(self.lab, 'research.max_ready_studies', 1)
        status = research.planning_status(self.lab, routine.load_config(self.lab))
        self.assertEqual(status['capacity'], 0)
        self.assertIn('Run queue full', status['blocked_reason'])

    def test_capacity_blocks_new_studies_until_outcome_review(self):
        routine.set_config(self.lab, 'research.max_open_studies', 1)
        routine.set_config(self.lab, 'research.max_ready_studies', 1)
        with self.lab.transaction():
            self.lab.db.execute('INSERT INTO program_studies VALUES(?,?)',
                                ('synthetic-study', json.dumps({'slug': 'synthetic-study', 'phase': 'ready to run'})))
        self.run_pass()
        self.assertEqual(self.stage_tasks('director'), [])
        self.import_report(study_slug='synthetic-study')
        self.run_pass(n=2)
        status = research.planning_status(self.lab, routine.load_config(self.lab))
        self.assertEqual(status['capacity'], 1)
        self.assertIsNone(status['blocked_reason'])

    def test_new_reviewed_evidence_can_trigger_planning_before_weekly_interval(self):
        # Completed director from this instant; no pending refinement.
        cfg = routine.load_config(self.lab)
        with self.lab.lock('cycle', 'fixture'):
            ids = program.maybe_enqueue_director(self.lab, cfg, self.clock)
        with self.lab.transaction():
            routine.settle(self.lab, ids['work'], 'failed', error='SYNTHETIC stopped round')
        self.assertFalse(program.director_due(self.lab, cfg, self.clock))
        self.import_report()
        self.run_pass(n=2)
        self.assertTrue(program.director_due(self.lab, cfg, self.clock))

    def test_limit_does_not_spend_attempt_or_spawn_duplicate_round(self):
        self.import_report()
        with patch.object(routine.svc, 'run_codex', side_effect=UsageLimit(
                'codex', self.time + timedelta(hours=2), 'SYNTHETIC limit')):
            self.run_pass()
        self.run_pass()
        task = self.stage_tasks('synthesis')[0]
        self.assertEqual((task['state'], task['attempts']), ('queued', 0))
        self.assertEqual(len(self.stage_tasks('synthesis')), 1)

    def test_questions_are_immutable_and_report_is_candid(self):
        with self.assertRaises(ValueError):
            research.add_question(self.lab, {'id': 'synthetic-question', 'question': 'Changed', 'falsifier': 'Changed'})
        report = research.write_report(self.lab, routine.load_config(self.lab))
        self.assertFalse(report['solved'])
        self.assertIn('not declared solved', (self.lab.root / 'RESEARCH.md').read_text())

    def test_tampered_preserved_outcome_refused(self):
        outcome = self.import_report()
        research._snapshot(self.lab, outcome).write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.run_pass()
        self.assertEqual(self.stage_tasks('synthesis'), [])

    def test_disabled_feedback_engine_does_not_enqueue_synthesis(self):
        routine.set_config(self.lab, 'research.enabled', False)
        routine.set_config(self.lab, 'program.enabled', False)
        self.import_report()
        self.run_pass()
        self.assertEqual(self.stage_tasks('synthesis'), [])


    def test_advisory_step_recovers_on_next_pass_after_a_fix(self):
        self.import_report()
        self.bad_synthesis = True
        with patch.object(research, 'accept_synthesis', side_effect=ValueError('SYNTHETIC engine bug')):
            self.run_pass(n=2)
        self.assertFalse(self.parked())
        self.assertEqual(research.state(self.lab)['syntheses'], [])
        self.bad_synthesis = False
        # "Fixed" code: the saved output is still the bad one, so swap in the accepted path directly.
        with patch.object(research, 'accept_synthesis', return_value=None) as fixed:
            self.run_pass(n=1)
            self.assertEqual(fixed.call_count, 1)
        self.assertFalse(self.parked())

    def test_previously_parked_advisory_review_is_resumed_once(self):
        """Live case 2026-10-12: task 9 parked under the old rule after one failed follow-up."""
        self.import_report()
        with patch.object(research, 'accept_synthesis', side_effect=ValueError('SYNTHETIC engine bug')):
            self.run_pass(n=2)
            self.run_pass(n=1)
        self.assertTrue(self.parked())
        tid = self.lab.db.execute("SELECT id FROM tasks WHERE kind='review' AND state='awaiting_human'").fetchone()[0]
        with self.lab.transaction():   # rewrite to the old one-failure record the live lab holds
            self.lab.db.execute("UPDATE program_actions SET detail=? WHERE key=?",
                                ('{"error":"ValueError","stage":"synthesis","task":%d}' % tid, f'follow-up:{tid}'))
        with patch.object(research, 'accept_synthesis', return_value=None) as fixed:
            self.run_pass(n=1)
            self.assertEqual(fixed.call_count, 1)
        self.assertEqual(self.lab.task(tid)['state'], 'succeeded')
        self.assertFalse(self.parked())


if __name__ == '__main__':
    unittest.main()
