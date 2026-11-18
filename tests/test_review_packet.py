"""Review packets must not hide the proposal, tests or sources behind one huge file (2026-10-13)."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from codexautopsych import routine


class FakeLab:
    def __init__(self, tasks):
        self.tasks = tasks

    def task(self, tid):
        return self.tasks[tid]


class ReviewPacketTest(unittest.TestCase):
    def test_small_files_survive_a_huge_file_and_all_are_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'outputs'
            (out / '__pycache__').mkdir(parents=True)
            files = {'A_huge.json': 'x' * 300, 'PREREG.md': 'the proposal', 'test_analysis.py': 'def test(): pass',
                     '__pycache__/t.pyc': 'junk'}
            for name, text in files.items():
                (out / name).write_text(text)
            outputs = [{'path': n, 'sha256': '0' * 64, 'bytes': len(t)} for n, t in files.items()]
            final = Path(tmp) / 'final.md'
            final.write_text('summary')
            subject = {'id': 1, 'kind': 'draft', 'payload': json.dumps({'title': 'T', 'instructions': 'do it'}),
                       'result': json.dumps({'final': str(final), 'outputs_dir': str(out), 'outputs': outputs})}
            review = {'id': 2, 'payload': json.dumps({'subject': 1, 'round': 1})}
            lab = FakeLab({1: subject})
            with patch.object(routine, 'PACKET_TEXT_LIMIT', 100):
                packet = routine.build_packet(lab, review)
            self.assertIn('the proposal', packet)
            self.assertIn('def test(): pass', packet)
            self.assertIn('full copy at files/A_huge.json', packet)
            self.assertNotIn('t.pyc', packet)
            inputs = Path(tmp) / 'inputs'
            inputs.mkdir()
            (inputs / 'source.md').write_text('a source')
            (out / 'link.md').symlink_to(inputs / 'source.md')
            dest = Path(tmp) / 'review'
            self.assertEqual(routine.copy_review_files(lab, review, dest), 4)
            self.assertEqual((dest / 'files/_author_inputs/source.md').read_text(), 'a source')
            self.assertFalse((dest / 'files/link.md').exists())
            self.assertEqual((dest / 'files/A_huge.json').read_text(), 'x' * 300)
            self.assertFalse((dest / 'files/__pycache__').exists())


if __name__ == '__main__':
    unittest.main()
