# Research engine

The engine adds a persistent feedback loop to the director:

**Question → proposal → preregistration review → harness review → wait for an
approved external experiment → owner-provided outcome → analysis and review
→ next questions.**

It can coordinate repeated research rounds. It cannot guarantee that psychology
will be solved, treat model behavior as a human mechanism, or certify its own
scientific correctness. No outcome automatically closes a question or changes
a pending claim into verified evidence. The charter's current remit remains
computational model mechanisms, particularly affect persistence and thresholds.

## What happens automatically

When enabled, the existing bounded routine pass maintains the question list and
queues an analysis of newly imported outcomes, followed by an independent Claude
checkpoint. Null, negative, inconclusive and interrupted reports are preserved
alongside positive reports. Approved analyses add falsifiable next questions and
feed the next director snapshot. New reviewed evidence can trigger a director
round before its usual weekly interval.

There is at most one unfinished director. At the default limit of five open
studies, new proposals stop until a linked outcome has passed analysis review.
Reviewing an outcome releases planning capacity; it does not validate the
reported experiment or authorize a rerun. Failed or rejected outcome reviews
block replanning until the owner addresses them. Revisions use the existing
bounded review loop. Research questions and outcome reports are append-only;
source reports are preserved by content hash under the ignored lab directory.

The engine reuses the existing subscription login guards, service-specific
usage pauses, attempt limits, daily caps and cycle lock. It never selects a paid
fallback. Nothing changes the approved protocol, resets interrupted experiments,
publishes, or reads the protected external study. There is no new scheduler.

## Local controls

The engine is disabled by default. Building it does not start workers or enable
services. These commands configure future passes; the existing Codex enablement
and Claude checkpoint mode remain separate choices:

```sh
python3 -m codexautopsych --lab .lab/research config program.enabled true
python3 -m codexautopsych --lab .lab/research config research.enabled true
python3 -m codexautopsych --lab .lab/research config research.max_open_studies 5
```

An empty enabled question list is seeded from the charter's affect-threshold
question. To add another question explicitly:

```sh
python3 -m codexautopsych --lab .lab/research research-question carryover-null \
  --question 'Does model carryover exceed a capacity-matched null?' \
  --falsifier 'The matched null reproduces the carryover.'
```

Place outcome JSON copies under `program/sources/`, then import explicitly:

```sh
python3 -m codexautopsych --lab .lab/research research-outcome --file program/sources/outcome.json
python3 -m codexautopsych --lab .lab/research research-status
```

Required outcome fields: `question_id`, `scope` (`synthetic` or `model`), `result`
(`positive`, `negative`, `null`, `inconclusive`, or `interrupted`), and nonempty
`summary`, `methods`, `limitations`. An optional `study_slug` links to a study
with a frozen reviewed harness. Duplicate byte-identical imports are idempotent.
Human-data scope, unknown questions, outside paths and symlinks are refused.
These checks validate intake structure, not the truth or provenance of a report.
Do not include credentials or human data in any source file.

Future routine passes run through the existing `run` command or an already
configured owner-managed timer. This implementation has not enabled one:

```sh
python3 -m codexautopsych --lab .lab/research run --max-tasks 5 --max-minutes 45
```

`RESEARCH.md` and `RESEARCH.json` under the lab show questions, reported outcomes,
reviewed advisory analyses and blocking work. `ATTENTION.md` still lists approval
and publication decisions. Set `research.enabled false` to stop scheduling new
feedback rounds; already queued tasks remain in the routine queue.

## What remains outside this engine

Real experiment execution is still unavailable. Generated harnesses are not
executed by the coordinator. Source collection supports local intake and optional bounded
[arXiv retrieval](ARXIV.md). Outcome interpretation remains advisory even after review;
replication and independent empirical verification require additional work.
Arthur retains publication, spending, human-research and nondelegated decisions.
The system cannot run indefinitely without evidence, compute authorization and
owner decisions: it waits visibly at those boundaries instead of fabricating
progress. The build and tests use synthetic fixtures and mocked workers only.

If a report is rejected or erroneous, the owner can explicitly remove it from
future planning without erasing it or its review history:

```sh
python3 -m codexautopsych --lab .lab/research research-withdraw OUTCOME_SHA256 --reason 'Explain the error'
```

A corrected report is a new import. Withdrawal is never an automatic way to
hide null or negative results. It does not free a linked study's capacity or
approve a failed experiment. Review packets contain the preserved reports and
their limitations; their hashes are rechecked before review and acceptance.
Oversized evidence packets fail closed instead of silently excluding evidence.

## Resource waiting

Enabled research passes check the read-only resource gate before claiming each
worker task. A blocked or unavailable gate leaves tasks queued without using
an attempt or making a model call. The next scheduled pass checks again.
`RESOURCE_GATE.json`, `RESEARCH.md` and `ATTENTION.md` show the waiting state.
This is a preflight check, not a GPU reservation; actual experiment execution
remains unavailable. If the local corpus cannot support a proposal, the director
is instructed to write `RESEARCH_GAPS.md` instead of inventing citations.

Resource diagnostics distinguish `process-inspection-denied` (the operating
environment refused the check), `process-inspection-unavailable` or
`process-inspection-failed`, `protected-workload-detected` (a command-line
pattern matched; the workload identity is not independently verified),
`execution-lock-held`, `execution-lock-unavailable`, and `clear`. A failure in
an interactive session does not establish the scheduled service has the same
failure. Inspect the fresh scheduler-generated status before diagnosing it.

## Hosted planning exception — owner approved 2026-10-09

The amended charter permits hosted subscription planning and review independently
of local experiment resources. `research.hosted_planning_during_local_work`
defaults to false; the owner has explicitly enabled it for the research lab.
When enabled, routine workers do not inspect local process availability. The
resource status is scoped to hosted workers and explicitly records
`local_execution_authorized: false`; it does not report the local GPU as free.
Worker instructions forbid local model loading, GPUs, installations, additional
model workers and experiment launches. Experiment execution remains disabled.
Existing subscription route checks, daily limits and checkpoint rules still apply.
