"""On-demand full text for Zotero items the theory lane is about to read.

Zotero holds citations and links only. When an item is selected for planning and has no text,
its DOI/URL is resolved to a free legal copy (arXiv, Unpaywall, Semantic Scholar open-access
PDF), which is saved inside the lab and read from there. Nothing is written to the Zotero
folder or to Zotero storage. Items with no open copy go on a list for the owner.

No models and no PDF execution: PDFs are stored as bytes and only their text is extracted.
Downloaded text is untrusted source material, never instructions.
"""
from datetime import datetime, timedelta
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .core import canonical, digest, write_atomic
from . import program

MAX_PDF = 40_000_000
MAX_JSON = 2_000_000
MAX_TEXT = 2_000_000
MIN_TEXT = 200
ARXIV_ID = r'(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})'
ARXIV_DOI = re.compile(r'10\.48550/arxiv\.' + ARXIV_ID, re.IGNORECASE)
ARXIV_URL = re.compile(r'^https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/' + ARXIV_ID, re.IGNORECASE)
DOI = re.compile(r'10\.\d{4,9}/\S{1,300}')
EMAIL = re.compile(r'[^@\s]{1,100}@[^@\s]{1,100}\.[^@\s]{2,30}')
KEY = re.compile(r'[A-Z0-9]{8}')
JXA = ("ObjC.import('Quartz'); function run(argv) { var d = $.PDFDocument.alloc.initWithURL("
       "$.NSURL.fileURLWithPath(argv[0])); if (d.isNil()) { throw new Error('unreadable PDF'); } "
       "var s = d.string; return s.isNil() ? '' : s.js; }")


def validate_setting(key, value):
    valid = ((key == 'enabled' and type(value) is bool) or
             (key == 'unpaywall_email' and isinstance(value, str) and (value == '' or EMAIL.fullmatch(value))) or
             (key == 'max_fetches_per_pass' and type(value) is int and 0 <= value <= 20) or
             (key == 'retry_days' and type(value) is int and 1 <= value <= 365))
    if not valid:
        raise ValueError('Invalid fulltext setting: ' + key)


def _check(url):
    """HTTPS to a public address only: no credentials in the URL, no local or private hosts."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Non-HTTPS or credentialed URL refused')
    for info in socket.getaddrinfo(u.hostname, 443, proto=socket.IPPROTO_TCP):
        if not ipaddress.ip_address(info[4][0].split('%')[0]).is_global:
            raise ValueError('Non-public address refused')


class _Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit):
    """Bounded GET with no proxy credentials, cookies or ambient auth."""
    _check(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirect())
    request = urllib.request.Request(url, headers={
        'User-Agent': 'CodexAutoPsych/0.1 (bounded open-access reader)', 'Accept-Encoding': 'identity'})
    with opener.open(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Response exceeds byte limit')
    return data


def pdf_text(path):
    """Text of a saved PDF through macOS PDFKit. Raises where that is unavailable."""
    if sys.platform != 'darwin' or not shutil.which('osascript'):
        raise OSError('No PDF text extractor on this machine')
    done = subprocess.run(['osascript', '-l', 'JavaScript', '-e', JXA, str(path)],
                          capture_output=True, timeout=120)
    if done.returncode:
        raise ValueError('PDF text extraction failed')
    return done.stdout.decode('utf-8', errors='replace')


def _path(lab, name):
    return program._safe(lab.root / 'fulltext' / name, lab.root)


def status(lab):
    p = _path(lab, 'status.json')
    return json.loads(p.read_text()) if p.exists() else {'items': {}}


def arxiv_id(item):
    match = ARXIV_DOI.search(item.get('DOI', '')) or ARXIV_URL.match(item.get('url', ''))
    return match.group(1) if match else None


def _doi(item):
    match = DOI.search(item.get('DOI', ''))
    return match.group(0).rstrip('.,;') if match else None


def _lookup(fetcher, url):
    """JSON from a resolver. None means a definite 'not known here'; other failures raise."""
    try:
        data = json.loads(fetcher(url, MAX_JSON))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404, 422):
            return None
        raise
    return data if isinstance(data, dict) else None


def _unpaywall(fetcher, doi, email):
    data = _lookup(fetcher, 'https://api.unpaywall.org/v2/' + urllib.parse.quote(doi, safe='/') +
                   '?' + urllib.parse.urlencode({'email': email})) or {}
    places = [data.get('best_oa_location')] + (data.get('oa_locations') or [])
    return [p['url_for_pdf'] for p in places if isinstance(p, dict) and isinstance(p.get('url_for_pdf'), str)]


def _semantic_scholar(fetcher, doi):
    data = _lookup(fetcher, 'https://api.semanticscholar.org/graph/v1/paper/DOI:' +
                   urllib.parse.quote(doi, safe='/') + '?fields=openAccessPdf,externalIds') or {}
    urls = []
    preprint = (data.get('externalIds') or {}).get('ArXiv')
    if isinstance(preprint, str) and re.fullmatch(ARXIV_ID, preprint):
        urls.append('https://arxiv.org/pdf/' + preprint)
    link = (data.get('openAccessPdf') or {}).get('url')
    if isinstance(link, str) and link:
        urls.append(link)
    return urls


def resolve(item, email, fetcher, sleep):
    """Find and download a free legal PDF. Returns (source, bytes), or (None, reason)."""
    doi, preprint = _doi(item), arxiv_id(item)
    finders = []
    if preprint:
        finders.append(('arXiv', lambda: ['https://arxiv.org/pdf/' + preprint]))
    if doi and email:
        finders.append(('Unpaywall', lambda: _unpaywall(fetcher, doi, email)))
    if doi:
        finders.append(('Semantic Scholar', lambda: _semantic_scholar(fetcher, doi)))
    if not finders:
        return None, 'no DOI or arXiv link on the Zotero item'
    unreachable, refused, tried = False, False, set()
    for source, find in finders:
        sleep(3.1)
        try:
            candidates = find()
        except (OSError, ValueError):
            unreachable = True
            continue
        for url in candidates[:4]:
            if url in tried:
                continue
            tried.add(url)
            try:
                data = fetcher(url, MAX_PDF)
            except (OSError, ValueError):
                refused = True
                continue
            if b'%PDF-' in data[:1024]:
                return source, data
            refused = True
    if unreachable:
        return None, 'retry'
    return None, ('an open copy is listed but could not be downloaded automatically' if refused
                  else 'no open-access copy found')


def _store(lab, data, source, extractor):
    sha = digest(data)
    program._save_once(_path(lab, f'pdf/{sha}.pdf'), data, lab.root)
    record = {'state': 'saved', 'source': source, 'pdf': f'pdf/{sha}.pdf', 'sha256': sha, 'text': None}
    try:
        text = extractor(_path(lab, record['pdf']))[:MAX_TEXT].strip()
        if len(' '.join(text.split())) < MIN_TEXT:
            raise ValueError('No readable text layer')
        _path(lab, 'text').mkdir(parents=True, exist_ok=True)
        write_atomic(_path(lab, f'text/{sha}.txt'), text + '\n')
        record['text'] = f'text/{sha}.txt'
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record['text_error'] = type(exc).__name__
    return record


def _report(lab, state, email):
    def line(r):
        link = 'https://doi.org/' + r['doi'] if r.get('doi') else r.get('url', '')
        return (f"- {r.get('title') or '(untitled)'} ({r.get('year') or 'n.d.'}) - {link or 'no link'}\n"
                f"  Zotero key {r['key']}; checked {r['checked'][:10]}; {r.get('reason', '')}".rstrip('; '))
    records = sorted(({**r, 'key': k} for k, r in state['items'].items()), key=lambda r: r['checked'], reverse=True)
    manual = [r for r in records if r['state'] == 'needs_manual']
    unread = [r for r in records if r['state'] == 'saved' and not r.get('text')]
    saved = sum(1 for r in records if r['state'] == 'saved' and r.get('text'))
    body = ['# Needs manual PDF', '',
            f'Saved locally and readable: {saved}. Need a manual PDF: {len(manual)}. '
            f'Saved but unreadable: {len(unread)}.', '',
            'The engine looked for a free legal copy of each paper below (arXiv, Unpaywall, Semantic Scholar) '
            'and found none it could download. To supply one, save the PDF as '
            '`.lab/research/fulltext/manual/<Zotero key>.pdf` (for example `ABCD1234.pdf`). '
            'Do not attach it in Zotero: that uses Zotero storage.', '']
    if not email:
        body += ['Unpaywall is off until an address is set (`python3 -m codexautopsych --lab .lab/research config fulltext.unpaywall_email you@example.org`), '
                 'so some papers listed here may still have a free copy.', '']
    body += [line(r) for r in manual] or ['- none']
    if unread:
        body += ['', '## Saved, but no text could be extracted (scanned or image-only PDF)', '']
        body += [line(r) for r in unread]
    write_atomic(lab.root / 'NEEDS_MANUAL_PDF.md', '\n'.join(body) + '\n')
    return {'saved': saved, 'needs_manual': len(manual), 'unreadable': len(unread)}


def fill(lab, cfg, items, excerpt, clock, *, fetcher=None, extractor=None, sleep=time.sleep):
    """Give each selected item without text a locally saved open-access copy, within a per-pass bound."""
    options = cfg.get('fulltext', {})
    for k, v in options.items():
        validate_setting(k, v)
    if not options.get('enabled'):
        return None
    fetcher, extractor = fetcher or fetch, extractor or pdf_text
    state, now, budget = status(lab), clock(), options['max_fetches_per_pass']
    email = options['unpaywall_email']
    for item in items:
        if 'fulltext' in item or not KEY.fullmatch(item['key']):
            continue
        record = state['items'].get(item['key'])
        if not record or record['state'] != 'saved':
            fresh, manual = None, _path(lab, f"manual/{item['key']}.pdf")
            if manual.is_file() and manual.stat().st_size <= MAX_PDF and b'%PDF-' in manual.read_bytes()[:1024]:
                fresh = _store(lab, manual.read_bytes(), 'owner-supplied PDF', extractor)
            elif record and now < datetime.fromisoformat(record['checked']) + timedelta(
                    days=1 if record['state'] == 'retry' else options['retry_days']):
                continue
            elif budget > 0:
                budget -= 1
                source, found = resolve(item, email, fetcher, sleep)
                fresh = _store(lab, found, source, extractor) if source else {
                    'state': 'retry' if found == 'retry' else 'needs_manual',
                    'reason': 'lookup service unreachable; will retry' if found == 'retry' else found}
            if not fresh:
                continue
            fresh.update(checked=now.isoformat(), title=' '.join(item.get('title', '').split())[:200],
                         year=item.get('date', '')[:4], doi=_doi(item), url=item.get('url', '')[:300])
            record = state['items'][item['key']] = fresh
        if record['state'] == 'saved' and record.get('text') and _path(lab, record['text']).is_file():
            item['fulltext'] = _path(lab, record['text']).read_text(errors='replace')[:excerpt]
            item['fulltext_coverage'] = (f"first {excerpt} characters of text extracted from a locally saved "
                                         f"open-access PDF ({record['source']}); equations, tables and figures "
                                         'may be garbled or missing')
    _path(lab, 'status.json').parent.mkdir(parents=True, exist_ok=True)
    write_atomic(_path(lab, 'status.json'), canonical(state) + '\n')
    return _report(lab, state, email)
