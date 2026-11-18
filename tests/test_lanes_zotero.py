"""SYNTHETIC fixtures only: theory/blind lanes, shelving, Zotero intake and the status page."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from codexautopsych import program, routine, zotero
from codexautopsych.core import Lab
from tests.test_program import proposal


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'program'
        (self.folder / 'sources').mkdir(parents=True)
        (self.folder / 'CHARTER.md').write_text('SYNTHETIC owner charter with OWNER-THEORY marker.')
        (self.folder / 'CHARTER_BLIND.md').write_text('SYNTHETIC blind charter.')
        (self.folder / 'sources' / 'theory.md').write_text('SYNTHETIC OWNER-THEORY source.')
        self.lab = Lab(self.root / '.lab')
        self.time = datetime(2026, 10, 12, tzinfo=timezone.utc)
        patch.object(program, 'PROJECT_ROOT', self.root).start()
        self.prompts = []
        patch.object(routine.svc, 'run_codex', side_effect=self.worker).start()
        patch.object(routine.svc, 'run_claude_review', return_value={
            'decision': 'revise', 'summary': 'SYNTHETIC fake review', 'required_changes': ['x']}).start()
        self.addCleanup(patch.stopall)
        for k, v in [('notify', False), ('program.enabled', True), ('codex.enabled', True),
                     ('claude.mode', 'cli'), ('program.director_interval_days', 1)]:
            routine.set_config(self.lab, k, v)

    def tearDown(self):
        self.lab.close()
        self.tmp.cleanup()

    def worker(self, prompt, workspace, **kwargs):
        self.prompts.append(prompt)
        inputs = [p.read_text(errors='replace') for p in (workspace / 'inputs').iterdir() if p.is_file()]
        self.inputs = '\n'.join(inputs)
        out = workspace / 'outputs'
        if 'Title: Director:' in prompt:
            (out / 'proposals').mkdir()
            slug = f'synthetic-{len(self.prompts)}'
            (out / 'proposals' / f'{slug}.md').write_text(proposal(slug))
        else:
            (out / 'PREREG.md').write_text('SYNTHETIC')
        return 'SYNTHETIC fake worker output.'

    def run_pass(self):
        return routine.run(self.lab, 'synthetic-scheduler', clock=lambda: self.time, max_tasks=1)

    def directors(self):
        return [json.loads(r['payload'])['program'] for r in self.lab.db.execute('SELECT payload FROM tasks')
                if json.loads(r['payload']).get('program', {}).get('stage') == 'director']

    def test_first_round_theory_second_blind_and_blind_sees_no_owner_material(self):
        self.run_pass()
        self.assertEqual(self.directors()[0]['lane'], 'theory')
        self.assertIn('OWNER-THEORY', self.inputs)
        self.assertIn('THEORY LANE', self.prompts[-1])
        program.shelve(self.lab, 'synthetic-1', 'SYNTHETIC test shelving')
        self.time += timedelta(days=2)
        self.run_pass()
        self.assertEqual(self.directors()[1]['lane'], 'blind')
        self.assertIn('BLIND LANE', self.prompts[-1])
        self.assertNotIn('OWNER-THEORY', self.inputs)
        record = json.loads(self.lab.db.execute("SELECT record FROM program_studies WHERE slug='synthetic-2'").fetchone()[0])
        self.assertEqual(record['lane'], 'blind')

    def test_shelve_cancels_queued_work_and_frees_capacity(self):
        routine.set_config(self.lab, 'research.enabled', True)
        routine.set_config(self.lab, 'research.max_open_studies', 1)
        routine.set_config(self.lab, 'research.hosted_planning_during_local_work', True)
        self.run_pass()
        from codexautopsych.research import planning_status
        self.assertEqual(planning_status(self.lab, routine.load_config(self.lab))['capacity'], 0)
        cancelled = program.shelve(self.lab, 'synthetic-1', 'SYNTHETIC')
        self.assertTrue(cancelled)
        states = {r['state'] for r in self.lab.db.execute('SELECT state FROM tasks WHERE id IN (%s)' % ','.join(map(str, cancelled)))}
        self.assertEqual(states, {'cancelled'})
        self.assertEqual(planning_status(self.lab, routine.load_config(self.lab))['capacity'], 1)

    def test_blind_share_zero_keeps_theory(self):
        routine.set_config(self.lab, 'program.blind_share', 0)
        self.assertEqual(program.next_lane(self.lab, routine.load_config(self.lab)), 'theory')


def make_zotero(folder):
    """A tiny SYNTHETIC Zotero-like database with one paper, one note and one cached PDF text."""
    db = sqlite3.connect(folder / 'zotero.sqlite')
    db.executescript('''
    CREATE TABLE itemTypes(itemTypeID INTEGER, typeName TEXT);
    CREATE TABLE items(itemID INTEGER, itemTypeID INTEGER, key TEXT, dateAdded TEXT, dateModified TEXT);
    CREATE TABLE fieldsCombined(fieldID INTEGER, fieldName TEXT);
    CREATE TABLE itemDataValues(valueID INTEGER, value TEXT);
    CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER);
    CREATE TABLE creators(creatorID INTEGER, firstName TEXT, lastName TEXT);
    CREATE TABLE itemCreators(itemID INTEGER, creatorID INTEGER, creatorTypeID INTEGER, orderIndex INTEGER);
    CREATE TABLE tags(tagID INTEGER, name TEXT);
    CREATE TABLE itemTags(itemID INTEGER, tagID INTEGER);
    CREATE TABLE itemNotes(itemID INTEGER, parentItemID INTEGER, note TEXT, title TEXT);
    CREATE TABLE itemAttachments(itemID INTEGER, parentItemID INTEGER, contentType TEXT, path TEXT);
    CREATE TABLE deletedItems(itemID INTEGER);
    INSERT INTO itemTypes VALUES (1,'journalArticle'),(2,'note'),(3,'attachment'),(4,'webpage');
    INSERT INTO items VALUES (1,1,'AAAA1111','2026-10-01','2026-10-01'),(2,2,'NOTE0001','2026-10-02','2026-10-02'),
                             (3,3,'PDF00001','2026-10-02','2026-10-02'),(4,1,'GONE0001','2026-10-03','2026-10-03'),
                             (5,4,'WEB00001','2026-10-04','2026-10-04'),(6,1,'DUPE0001','2025-01-01','2025-01-01');
    INSERT INTO fieldsCombined VALUES (1,'title'),(2,'abstractNote'),(3,'date');
    INSERT INTO itemDataValues VALUES (1,'SYNTHETIC rumination paper'),(2,'SYNTHETIC abstract'),(3,'2025-05-01'),(4,'Deleted');
    INSERT INTO itemDataValues VALUES (5,'SYNTHETIC private account page');
    INSERT INTO itemData VALUES (1,1,1),(1,2,2),(1,3,3),(4,1,4),(5,1,5),(6,1,1);
    INSERT INTO creators VALUES (1,'Ada','Fixture');
    INSERT INTO itemCreators VALUES (1,1,1,0);
    INSERT INTO tags VALUES (1,'mood');
    INSERT INTO itemTags VALUES (1,1);
    INSERT INTO itemNotes VALUES (2,1,'<p>SYNTHETIC owner note: <b>key point</b></p>','n');
    INSERT INTO itemAttachments VALUES (3,1,'application/pdf','storage:x.pdf');
    INSERT INTO deletedItems VALUES (4);
    ''')
    db.commit()
    db.close()
    (folder / 'storage' / 'PDF00001').mkdir(parents=True)
    (folder / 'storage' / 'PDF00001' / '.zotero-ft-cache').write_text('SYNTHETIC full text ' * 10)


class ZoteroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.zdir = Path(self.tmp.name) / 'Zotero'
        self.zdir.mkdir()
        make_zotero(self.zdir)
        self.lab = Lab(Path(self.tmp.name) / '.lab')
        self.addCleanup(self.lab.close)
        routine.set_config(self.lab, 'zotero.enabled', True)
        routine.set_config(self.lab, 'zotero.data_dir', str(self.zdir))

    def test_rank_prefers_relevant_old_items_and_keeps_recent(self):
        items = [{'key': f'r{i}', 'title': f'Unrelated recent paper {i} on birds', 'tags': [], 'notes': []}
                 for i in range(10)]
        items.append({'key': 'old', 'title': 'Steering vectors and affect carryover in reasoning traces',
                      'tags': ['steering'], 'notes': []})
        picked = zotero.rank(items, zotero._words('Does a steered affect carryover persist in traces?'), 4)
        keys = [i['key'] for i in picked]
        self.assertIn('old', keys)
        self.assertEqual(keys[0], 'r0')
        self.assertEqual(len(keys), 4)
        self.assertEqual([i['key'] for i in zotero.rank(items, [], 3)], ['r0', 'r1', 'r2'])

    def test_read_only_intake(self):
        before = (self.zdir / 'zotero.sqlite').read_bytes()
        result = zotero.refresh(self.lab, routine.load_config(self.lab))
        self.assertEqual((result['state'], result['items'], result['with_fulltext'], result['notes']), ('ok', 1, 1, 1))
        packet = json.loads((self.lab.root / 'zotero' / 'ZOTERO_PACKET.json').read_text())
        item = packet['items'][0]
        self.assertEqual(item['title'], 'SYNTHETIC rumination paper')
        self.assertEqual(item['notes'], ['SYNTHETIC owner note: key point'])
        self.assertIn('SYNTHETIC full text', item['fulltext'])
        self.assertEqual((self.zdir / 'zotero.sqlite').read_bytes(), before)
        index = (self.lab.root / 'zotero' / 'LIBRARY_INDEX.json').read_text()
        self.assertNotIn('private account page', index)  # web pages never reach planning
        self.assertEqual(result['generation'], 1)
        self.assertEqual(zotero.refresh(self.lab, routine.load_config(self.lab))['generation'], 1)

    def test_missing_library_is_reported_not_raised(self):
        routine.set_config(self.lab, 'zotero.data_dir', str(self.zdir / 'missing'))
        self.assertEqual(zotero.refresh(self.lab, routine.load_config(self.lab))['state'], 'error')

    def test_settings_validated(self):
        for key, value in [('enabled', 1), ('max_items', 0), ('data_dir', '')]:
            with self.assertRaises(ValueError):
                routine.set_config(self.lab, 'zotero.' + key, value)
