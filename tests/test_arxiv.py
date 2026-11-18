"""Synthetic Atom/HTML fixtures only; no network or model calls."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from codexautopsych import arxiv, routine, program
from codexautopsych.core import Lab


def feed(version=1):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>http://arxiv.org/abs/2601.12345v{version}</id><title>SYNTHETIC steering paper</title>
    <summary>SYNTHETIC abstract, not research evidence.</summary><updated>2026-10-0{version}T00:00:00Z</updated>
    <author><name>Fixture Author</name></author></entry></feed>'''.encode()


class ArxivTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lab = Lab(self.root / '.lab')
        self.addCleanup(self.lab.close)
        self.time = datetime(2026, 10, 9, tzinfo=timezone.utc)
        self.clock = lambda: self.time
        routine.set_config(self.lab, 'arxiv.enabled', True)
        self.fetch = Mock(side_effect=[feed(), b'<article><script>BAD SCRIPT</script><p>' + b'SYNTHETIC paper text. ' * 40 + b'</p></article>'])

    def poll(self, fetcher=None):
        with self.lab.lock('cycle', 'test'):
            return arxiv.poll(self.lab, routine.load_config(self.lab), self.clock,
                              fetcher=fetcher or self.fetch, sleep=lambda _: None)

    def test_disabled_has_no_network(self):
        routine.set_config(self.lab, 'arxiv.enabled', False)
        self.assertEqual(self.poll()['state'], 'disabled')
        self.fetch.assert_not_called()

    def test_html_preserved_and_packet_labelled(self):
        result = self.poll()
        self.assertEqual(result['added'], 1)
        text = arxiv.packet(self.lab).read_text()
        self.assertIn('HTML article text', text)
        self.assertIn('Untrusted literature', text)
        self.assertNotIn('BAD SCRIPT', text)
        self.assertEqual(len(list((self.lab.root / 'arxiv/raw').iterdir())), 2)

    def test_daily_bound_and_duplicate(self):
        self.poll()
        self.poll()
        self.assertEqual(self.fetch.call_count, 2)
        self.time += timedelta(days=1)
        fetcher = Mock(return_value=feed())
        self.assertEqual(self.poll(fetcher)['added'], 0)
        self.assertEqual(fetcher.call_count, 1)

    def test_versions_retained_packet_latest_only(self):
        self.poll()
        self.time += timedelta(days=1)
        newer = Mock(side_effect=[feed(2), OSError('unavailable')])
        self.assertEqual(self.poll(newer)['generation'], 2)
        self.assertEqual(len(list((self.lab.root / 'arxiv/papers').iterdir())), 2)
        packet = arxiv.packet(self.lab).read_text()
        self.assertIn('2601.12345v2', packet)
        self.assertNotIn('2601.12345v1', packet)
        self.assertIn('abstract only', packet)

    def test_error_backoff_no_exception_details(self):
        result = self.poll(Mock(side_effect=OSError('SECRET')))
        self.assertEqual(result['state'], 'error')
        self.assertNotIn('SECRET', str(result))
        again = Mock()
        self.poll(again)
        again.assert_not_called()

    def test_non_article_html_is_abstract_only(self):
        r = self.poll(Mock(side_effect=[feed(), b'<html>Not a paper</html>']))
        self.assertEqual(r['papers'][0]['coverage'], 'abstract only')

    def test_unsafe_ids_entities_and_endpoints(self):
        for content in [feed().replace(b'2601.12345v1', b'../../secret'), b'<!DOCTYPE feed><feed/>']:
            with self.assertRaises(ValueError):
                arxiv.parse_feed(content)
        for url in ['https://evil.example/html/2601.12345v1', 'file:///etc/passwd',
                    'https://arxiv.org/html/../../secret', 'https://user:secret@arxiv.org/api/query']:
            with self.assertRaises(ValueError):
                arxiv.fetch(url)
        with self.assertRaises(ValueError):
            arxiv.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example')

    def test_tampered_source_refused(self):
        self.poll()
        next((self.lab.root / 'arxiv/papers').iterdir()).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            arxiv.packet(self.lab)

    def test_symlink_refused(self):
        (self.lab.root / 'arxiv').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.poll()
        self.fetch.assert_not_called()

    def test_settings_and_cycle_lock(self):
        for key, value in [('enabled', 1), ('max_results', 11), ('query', ''), ('queries', ['']), ('seed_ids', ['not-an-id']), ('queries', ['q'] * 9)]:
            with self.assertRaises(ValueError):
                routine.set_config(self.lab, 'arxiv.' + key, value)
        with self.assertRaises(ValueError):
            arxiv.poll(self.lab, routine.load_config(self.lab), self.clock)

    def test_director_receives_reading_and_new_generation(self):
        self.poll()
        (self.root / 'program/sources').mkdir(parents=True)
        (self.root / 'program/CHARTER.md').write_text('SYNTHETIC charter')
        routine.set_config(self.lab, 'program.enabled', True)
        cfg = routine.load_config(self.lab)
        with patch.object(program, 'PROJECT_ROOT', self.root), self.lab.lock('cycle', 'test'):
            ids = program.maybe_enqueue_director(self.lab, cfg, self.clock)
            self.assertIsNotNone(ids)
            import json
            payload = json.loads(self.lab.task(ids['work'])['payload'])
            self.assertTrue(any('READING_PACKET' in p for p in payload['inputs']))
            self.assertFalse(program.director_due(self.lab, cfg, self.clock))
        self.lab.db.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (ids['work'],))
        self.time += timedelta(days=1)
        self.poll(Mock(side_effect=[feed(2), OSError()]))
        with patch.object(program, 'PROJECT_ROOT', self.root):
            self.assertTrue(program.director_due(self.lab, cfg, self.clock))

    def test_fallback_rejects_malformed_atom(self):
        with patch.object(arxiv.ET, 'fromstring', side_effect=ImportError()):
            self.assertEqual(arxiv.parse_feed(feed())[0]['id'], '2601.12345v1')
            for bad in [feed().replace(b'</entry>', b'</wrong>'), feed()[:-7], b'<feed/>']:
                with self.assertRaises(ValueError):
                    arxiv.parse_feed(bad)

    def test_study3_titles_are_ingested(self):
        fetcher = Mock(return_value=feed().replace(b'SYNTHETIC steering paper', b'Study 3'))
        self.assertEqual(self.poll(fetcher)['added'], 1)
        self.assertEqual(fetcher.call_count, 2)  # feed plus article HTML


class ArxivSeedAndQueryTests(unittest.TestCase):
    """SYNTHETIC fixtures: starting papers are fetched once; several daily searches run."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lab = Lab(Path(self.tmp.name) / '.lab')
        self.addCleanup(self.lab.close)
        self.time = datetime(2026, 10, 9, tzinfo=timezone.utc)
        routine.set_config(self.lab, 'arxiv.enabled', True)
        routine.set_config(self.lab, 'arxiv.queries', ['abs:"emotion"', 'abs:"rumination"'])
        routine.set_config(self.lab, 'arxiv.seed_ids', ['2601.12345'])

    def poll(self, fetcher):
        with self.lab.lock('cycle', 'test'):
            return arxiv.poll(self.lab, routine.load_config(self.lab), lambda: self.time,
                              fetcher=fetcher, sleep=lambda _: None)

    def test_seed_once_and_each_query(self):
        urls = []
        def fetcher(url):
            urls.append(url)
            if '/html/' in url:
                raise OSError('no html')
            return feed(2) if 'search_query' in url else feed(1)
        result = self.poll(fetcher)
        api = [u for u in urls if 'export.arxiv.org' in u]
        self.assertEqual(len(api), 3)
        self.assertIn('id_list=2601.12345', api[0])
        self.assertTrue(any(p.get('seed') for p in result['papers']))
        self.assertEqual(result['added'], 2)  # seed v1 plus a newer v2 from the searches
        self.time += timedelta(days=1)
        urls.clear()
        self.poll(fetcher)
        self.assertFalse(any('id_list' in u for u in urls))
        packet = arxiv.packet(self.lab).read_text()
        self.assertIn('2601.12345', packet)

