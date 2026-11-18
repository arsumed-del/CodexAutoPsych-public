"""Live runner tests. SYNTHETIC CPU-only fake adapter; no model downloads, no network, no AI calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codexautopsych import live, program, research
from codexautopsych.core import Lab

ADAPTER = '''# SYNTHETIC fake adapter for tests only - not a real model
import json, os
CALLS = os.environ.get("LIVE_TEST_CALLS")
def cells(stage, ctx):
    n = ctx["config"].get(stage, 3)
    return [{"id": f"{stage}-{i}", "x": i} for i in range(n)]
def run_cell(stage, cell, ctx):
    if CALLS:
        open(CALLS, "a").write(cell["id"] + "\\n")
    crash = ctx["config"].get("crash_at")
    if crash == cell["id"] and not os.path.exists(ctx["study_dir"] + "/crashed"):
        open(ctx["study_dir"] + "/crashed", "w").write("1")
        raise KeyboardInterrupt("SYNTHETIC crash")
    return {"y": cell["x"] * 2}
def gate(stage, records, ctx):
    if stage == "calibrate":
        ok = ctx["config"].get("gate_ok", True)
        return ok, "SYNTHETIC gate", {"alpha_max": 0.1}
    return True, "ok"
def analyze(records, ctx):
    total = sum(r["output"]["y"] for r in records["main"])
    return {"result": "null", "summary": f"SYNTHETIC total {total}", "methods": "SYNTHETIC fake adapter",
            "limitations": "SYNTHETIC test only"}
'''


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'program/sources').mkdir(parents=True)
        (self.root / 'program/CHARTER.md').write_text('SYNTHETIC charter fixture')
        self.lab = Lab(self.root / '.lab')
        self.addCleanup(self.lab.close)
        p = patch.object(program, 'PROJECT_ROOT', self.root)
        p.start(); self.addCleanup(p.stop)
        research.add_question(self.lab, {'id': 'synthetic-q', 'question': 'SYNTHETIC question?',
                                         'falsifier': 'SYNTHETIC falsifier'})
        self.calls = self.root / 'calls.txt'
        e = patch.dict('os.environ', {'LIVE_TEST_CALLS': str(self.calls)})
        e.start(); self.addCleanup(e.stop)

    def study(self, name='s1', designed_by='claude', reviewed_by='codex', **config):
        d = self.root / name
        d.mkdir()
        (d / 'adapter.py').write_text(ADAPTER)
        (d / 'PREREG.md').write_text('SYNTHETIC prereg')
        (d / 'study.json').write_text(json.dumps({
            'slug': name, 'question_id': 'synthetic-q', 'designed_by': designed_by,
            'reviewed_by': reviewed_by, 'frozen_files': ['PREREG.md'],
            'stages': [{'name': 'calibrate'}, {'name': 'main'}], 'config': config}))
        return d

    def run_it(self, d, free=True):
        return live.Run(d, self.lab).execute(resource_check=lambda: {'free': free, 'reason': 'test'})

    def test_refuses_without_approval(self):
        d = self.study()
        with self.assertRaises(live.Refused):
            self.run_it(d)
        self.assertFalse(self.calls.exists())

    def test_approval_requires_claude_design_and_independent_review(self):
        with self.assertRaises(live.Refused):
            live.approve(self.study('a', designed_by='codex'))
        with self.assertRaises(live.Refused):
            live.approve(self.study('b', reviewed_by='claude'))

    def test_changed_file_after_approval_stops(self):
        d = self.study(); live.approve(d)
        (d / 'PREREG.md').write_text('SYNTHETIC edited')
        with self.assertRaisesRegex(live.Refused, 'PREREG.md'):
            self.run_it(d)
        self.assertFalse(self.calls.exists())
        with self.assertRaises(live.Refused):
            live.approve(d)

    def test_full_run_records_outcome_and_queues_review_input(self):
        d = self.study(); live.approve(d)
        state = self.run_it(d)
        self.assertEqual(state['status'], 'complete', state)
        out = json.loads((d / 'results/outcome.json').read_text())
        self.assertEqual(out['summary'], 'SYNTHETIC total 6')
        self.assertEqual(self.lab.db.execute('SELECT COUNT(*) FROM research_outcomes').fetchone()[0], 1)
        self.assertEqual(json.loads((d / 'data/calibrate.gate.json').read_text())['saved'], {'alpha_max': 0.1})
        n = len(self.calls.read_text().split())
        self.assertEqual(self.run_it(d)['status'], 'complete')      # rerun does nothing
        self.assertEqual(len(self.calls.read_text().split()), n)

    def test_crash_and_resume_no_duplicate_cells(self):
        d = self.study(crash_at='main-1'); live.approve(d)
        with self.assertRaises(KeyboardInterrupt):
            self.run_it(d)
        with (d / 'data/main.jsonl').open('a') as fh:
            fh.write('{"torn')                       # simulate a write cut off mid-line
        self.assertEqual(self.run_it(d)['status'], 'complete')
        calls = self.calls.read_text().split()
        self.assertEqual(calls.count('main-0'), 1)
        self.assertEqual(len((d / 'data/main.jsonl').read_text().splitlines()), 3)
        self.assertTrue(list((d / 'data').glob('main.torn-*')))

    def test_failed_gate_stops_without_retry(self):
        d = self.study(gate_ok=False); live.approve(d)
        state = self.run_it(d)
        self.assertEqual(state['status'], 'blocked')
        self.assertIn('no retry', state['reason'])
        self.assertFalse((d / 'data/main.jsonl').exists())
        before = self.calls.read_text()
        self.assertEqual(self.run_it(d)['status'], 'blocked')        # gate result is immutable
        self.assertEqual(self.calls.read_text(), before)

    def test_stop_request_and_resume(self):
        d = self.study(); live.approve(d)
        live.request_stop(d)
        self.assertEqual(self.run_it(d)['status'], 'stopped')
        (d / 'STOP').unlink()
        self.assertEqual(self.run_it(d)['status'], 'complete')

    def test_lock_and_busy_mac(self):
        d = self.study(); live.approve(d)
        self.assertEqual(self.run_it(d, free=False)['status'], 'waiting')
        with self.lab.lock('execution', 'other-study'):
            self.assertEqual(self.run_it(d)['status'], 'waiting')
        self.assertEqual(self.run_it(d)['status'], 'complete')

    def test_tampered_record_detected(self):
        d = self.study(crash_at='main-1'); live.approve(d)
        with self.assertRaises(KeyboardInterrupt):
            self.run_it(d)
        path = d / 'data/main.jsonl'
        path.write_text(path.read_text().replace('"y":0', '"y":5'))
        with self.assertRaisesRegex(live.Refused, 'integrity'):
            self.run_it(d)


if __name__ == '__main__':
    unittest.main()
