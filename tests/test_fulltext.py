"""SYNTHETIC fixtures only: on-demand open-access full text for Zotero items. No network, no real PDFs."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.error

from codexautopsych import fulltext, routine, zotero
from codexautopsych.core import Lab
from tests.test_lanes_zotero import make_zotero

PDF = b'%PDF-1.7 SYNTHETIC fixture, not a real paper'
TEXT = 'SYNTHETIC extracted paper text. ' * 20
ARXIV_PDF = 'https://arxiv.org/pdf/2601.00001'
UNPAYWALL = 'https://api.unpaywall.org/v2/10.1234/synthetic.open?email=owner%40example.org'
SCHOLAR = 'https://api.semanticscholar.org/graph/v1/paper/DOI:{}?fields=openAccessPdf,externalIds'


def add_items(folder):
    """Three more SYNTHETIC papers with no attachment: arXiv preprint, open article, paywalled article."""
    db = sqlite3.connect(folder / 'zotero.sqlite')
    db.executescript('''
    INSERT INTO items VALUES (10,1,'ARXV0001','2026-10-10','2026-10-10'),(11,1,'OPEN0001','2026-10-09','2026-10-09'),
                             (12,1,'PAID0001','2026-10-08','2026-10-08');
    INSERT INTO fieldsCombined VALUES (4,'DOI'),(5,'url');
    INSERT INTO itemDataValues VALUES (10,'SYNTHETIC arXiv preprint'),(11,'10.48550/arXiv.2601.00001'),
        (12,'SYNTHETIC open article'),(13,'10.1234/synthetic.open'),(14,'SYNTHETIC paywalled article'),
        (15,'10.1234/synthetic.closed'),(16,'http://arxiv.org/abs/2601.00001');
    INSERT INTO itemData VALUES (10,1,10),(10,4,11),(10,5,16),(11,1,12),(11,4,13),(12,1,14),(12,4,15);
    ''')
    db.commit()
    db.close()


def tree(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


class FullTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.zdir = Path(self.tmp.name) / 'Zotero'
        self.zdir.mkdir()
        make_zotero(self.zdir)
        add_items(self.zdir)
        self.lab = Lab(Path(self.tmp.name) / '.lab')
        self.addCleanup(self.lab.close)
        for key, value in [('zotero.enabled', True), ('zotero.data_dir', str(self.zdir)), ('fulltext.enabled', True),
                           ('fulltext.max_fetches_per_pass', 5)]:
            routine.set_config(self.lab, key, value)
        self.time = datetime(2026, 10, 12, tzinfo=timezone.utc)
        self.calls = []
        self.web = {ARXIV_PDF: PDF,
                    UNPAYWALL: json.dumps({'best_oa_location': {'url_for_pdf': 'https://repo.example.org/open.pdf'}}).encode(),
                    'https://repo.example.org/open.pdf': PDF + b' two',
                    SCHOLAR.format('10.1234/synthetic.open'): json.dumps({'openAccessPdf': {'url': 'https://s2.example.org/open.pdf'}}).encode(),
                    'https://s2.example.org/open.pdf': PDF + b' three',
                    SCHOLAR.format('10.1234/synthetic.closed'): json.dumps({'openAccessPdf': None}).encode()}

    def fetcher(self, url, limit):
        self.calls.append(url)
        if url not in self.web:
            raise urllib.error.HTTPError(url, 404, 'SYNTHETIC not found', {}, io.BytesIO())
        if isinstance(self.web[url], Exception):
            raise self.web[url]
        return self.web[url]

    def refresh(self, extractor=lambda path: TEXT):
        return zotero.refresh(self.lab, routine.load_config(self.lab), lambda: self.time,
                              fetcher=self.fetcher, extractor=extractor, sleep=lambda s: None)

    def packet(self):
        items = json.loads((self.lab.root / 'zotero' / 'ZOTERO_PACKET.json').read_text())['items']
        return {i['key']: i for i in items}

    def manual(self):
        return (self.lab.root / 'NEEDS_MANUAL_PDF.md').read_text()

    def test_open_copy_is_saved_in_the_lab_and_zotero_is_untouched(self):
        before = tree(self.zdir)
        result = self.refresh()
        self.assertEqual(result['fetched'], {'saved': 2, 'needs_manual': 1, 'unreadable': 0})
        self.assertEqual(tree(self.zdir), before)  # no file added, changed or removed in the Zotero folder
        item = self.packet()['ARXV0001']
        self.assertIn('SYNTHETIC extracted paper text', item['fulltext'])
        self.assertIn('arXiv', item['fulltext_coverage'])
        record = fulltext.status(self.lab)['items']['ARXV0001']
        self.assertEqual((self.lab.root / 'fulltext' / record['pdf']).read_bytes(), PDF)
        self.assertEqual(result['with_fulltext'], 3)  # two fetched plus the one Zotero already had text for
        self.assertNotIn('AAAA1111', fulltext.status(self.lab)['items'])  # already readable: never fetched
        self.assertEqual(result['generation'], 1)
        self.calls.clear()
        self.time += timedelta(days=2)
        self.assertEqual(self.refresh()['generation'], 1)
        self.assertEqual(self.calls, [])  # saved copies are read locally, not fetched again

    def test_unpaywall_only_with_an_address_then_semantic_scholar(self):
        self.refresh()
        self.assertFalse([u for u in self.calls if 'unpaywall' in u])
        self.assertEqual(fulltext.status(self.lab)['items']['OPEN0001']['source'], 'Semantic Scholar')
        self.assertIn('Unpaywall is off', self.manual())

    def test_unpaywall_used_when_address_is_set(self):
        routine.set_config(self.lab, 'fulltext.unpaywall_email', 'owner@example.org')
        self.refresh()
        self.assertIn(UNPAYWALL, self.calls)
        self.assertEqual(fulltext.status(self.lab)['items']['OPEN0001']['source'], 'Unpaywall')
        self.assertNotIn('Unpaywall is off', self.manual())

    def test_no_open_copy_goes_on_the_manual_list_and_is_not_retried_early(self):
        self.refresh()
        self.assertIn('SYNTHETIC paywalled article', self.manual())
        self.assertIn('https://doi.org/10.1234/synthetic.closed', self.manual())
        self.assertNotIn('SYNTHETIC arXiv preprint', self.manual())
        self.assertNotIn('fulltext', self.packet()['PAID0001'])
        self.calls.clear()
        self.time += timedelta(days=29)
        self.refresh()
        self.assertEqual(self.calls, [])
        self.time += timedelta(days=2)
        self.refresh()
        self.assertEqual(self.calls, [SCHOLAR.format('10.1234/synthetic.closed')])

    def test_landing_page_instead_of_pdf_is_not_accepted(self):
        self.web['https://s2.example.org/open.pdf'] = b'<html>SYNTHETIC login page</html>'
        self.refresh()
        record = fulltext.status(self.lab)['items']['OPEN0001']
        self.assertEqual(record['state'], 'needs_manual')
        self.assertIn('could not be downloaded', record['reason'])

    def test_unreachable_lookup_is_retried_next_day_not_listed(self):
        self.web[SCHOLAR.format('10.1234/synthetic.closed')] = urllib.error.URLError('SYNTHETIC outage')
        self.refresh()
        self.assertEqual(fulltext.status(self.lab)['items']['PAID0001']['state'], 'retry')
        self.assertNotIn('SYNTHETIC paywalled article', self.manual())
        self.web[SCHOLAR.format('10.1234/synthetic.closed')] = json.dumps({}).encode()
        self.time += timedelta(days=1, minutes=1)
        self.refresh()
        self.assertEqual(fulltext.status(self.lab)['items']['PAID0001']['state'], 'needs_manual')

    def test_fetches_per_pass_are_bounded(self):
        routine.set_config(self.lab, 'fulltext.max_fetches_per_pass', 1)
        self.refresh()
        self.assertEqual(list(fulltext.status(self.lab)['items']), ['ARXV0001'])
        self.refresh()
        self.assertEqual(sorted(fulltext.status(self.lab)['items']), ['ARXV0001', 'OPEN0001'])

    def test_disabled_makes_no_requests(self):
        routine.set_config(self.lab, 'fulltext.enabled', False)
        result = self.refresh()
        self.assertEqual((self.calls, result['fetched']), ([], None))
        self.assertFalse((self.lab.root / 'fulltext').exists())

    def test_owner_supplied_pdf_is_read_from_the_lab(self):
        self.refresh()
        drop = self.lab.root / 'fulltext' / 'manual'
        drop.mkdir()
        (drop / 'PAID0001.pdf').write_bytes(PDF + b' owner')
        self.refresh()
        self.assertIn('SYNTHETIC extracted', self.packet()['PAID0001']['fulltext'])
        self.assertNotIn('SYNTHETIC paywalled article', self.manual())

    def test_pdf_without_extractable_text_is_reported(self):
        def broken(path):
            raise OSError('SYNTHETIC no extractor')
        result = self.refresh(extractor=broken)
        self.assertEqual(result['fetched']['unreadable'], 2)
        self.assertNotIn('fulltext', self.packet()['ARXV0001'])
        self.assertIn('no text could be extracted', self.manual())

    def test_fetch_failure_never_stops_intake(self):
        def explode(url, limit):
            raise RuntimeError('SYNTHETIC unexpected')
        with self.assertRaises(RuntimeError):  # unknown errors stay loud ...
            zotero.refresh(self.lab, routine.load_config(self.lab), lambda: self.time, fetcher=explode,
                           extractor=lambda p: TEXT, sleep=lambda s: None)
        (self.lab.root / 'fulltext').mkdir(exist_ok=True)
        (self.lab.root / 'fulltext' / 'status.json').write_text('not json')
        result = self.refresh()  # ... but a damaged local record only skips fetching
        self.assertEqual((result['state'], result['fetched']['error']), ('ok', 'JSONDecodeError'))

    def test_only_public_https_addresses(self):
        for url in ('http://arxiv.org/pdf/2601.00001', 'https://127.0.0.1/x.pdf', 'https://10.0.0.8/x.pdf',
                    'https://user:pw@192.0.2.1/x.pdf', 'https://169.254.169.254/latest', 'file:///etc/hosts',
                    'https://[::1]/x.pdf', 'https://8.8.8.8:8443/x.pdf'):
            with self.assertRaises(ValueError, msg=url):
                fulltext.fetch(url, 10)

    def test_identifiers(self):
        self.assertEqual(fulltext.arxiv_id({'DOI': '10.48550/arXiv.2604.04064'}), '2604.04064')
        self.assertEqual(fulltext.arxiv_id({'url': 'http://arxiv.org/abs/2603.04722v2'}), '2603.04722')
        self.assertIsNone(fulltext.arxiv_id({'url': 'https://evil.example/arxiv.org/abs/2603.04722'}))
        self.assertIsNone(fulltext.arxiv_id({'DOI': '10.1038/s41586-026-11044-y'}))

    def test_settings_validated(self):
        for key, value in [('enabled', 1), ('unpaywall_email', 'not-an-address'), ('max_fetches_per_pass', 21),
                           ('retry_days', 0)]:
            with self.assertRaises(ValueError):
                routine.set_config(self.lab, 'fulltext.' + key, value)


if __name__ == '__main__':
    unittest.main()
