# Automatic arXiv reading

The existing routine pass now checks arXiv once a day when `arxiv.enabled` is
true. No new scheduler, credentials, paid APIs, dependencies or model calls are
needed for retrieval. Defaults remain disabled for other labs.

The initial query targets activation steering, emotion representations, and
affect regulation in language models. It requests the three most recently
updated matches per daily check (configurable 1–5). This is a small discovery
feed, not an exhaustive historical search. Failed searches wait one hour; there is no tight retry loop. Requests are sequential and spaced by at
least three seconds; responses are limited to 2 MB and a 15-second timeout.
Redirects and external links are not followed. Protected-study titles are skipped.

```sh
python3 -m codexautopsych --lab .lab/research config arxiv.enabled true
python3 -m codexautopsych --lab .lab/research arxiv-fetch
python3 -m codexautopsych --lab .lab/research arxiv-status
```

`arxiv-fetch` performs only intake, never starts workers, and respects the daily
check time. The existing `run` pass performs the same check automatically. Set
`arxiv.enabled false` to stop retrieval and omit literature from future director
packets. Already queued immutable input snapshots remain intact.

Sources and original fetched responses are preserved under ignored
`.lab/research/arxiv/`, with hashes and versioned IDs. `ARXIV.md` reports intake
status. Available HTML article text is extracted without executing HTML or
scripts. Missing HTML yields an explicitly **abstract-only** record; PDF-only
papers are not parsed. Equations, figures, and layout may be missing even from
HTML text. An abstract-only version is not retried for HTML automatically.

The director receives up to eight latest paper versions, at most 12,000 text
characters each, with truncation and coverage labels. Older versions remain
archived. New versions can trigger planning before the weekly interval, but
unfinished-director, study-capacity, and subscription limits still apply. Intake
alone is not a scientific review; interpretation happens during the director's
next permitted subscription task. It must cite versions, treat the paper as
untrusted evidence, and never obey instructions embedded in it.

No findings become verified claims automatically. No experiments, publication,
paid routes, protected project access or additional workers are authorized.
API reference: https://info.arxiv.org/help/api/user-manual.html
