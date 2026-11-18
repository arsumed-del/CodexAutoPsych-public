# CodexAutoPsych

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24330497.svg)](https://doi.org/10.5281/zenodo.24330497)

**An autonomous research engine for in-silico experiments on language-model psychology.**

CodexAutoPsych reads the literature, proposes small experiments on open-weight
language models (for example, whether steering a model toward warmth buffers its
responses to provocation, or whether instructions carry over across turns), runs
them locally, analyzes the results, and writes each one up as a short report.
The "fast lane" (`fastlane/`) is the live loop; each finished experiment lives in
`fastlane/experiments/<ID>/` with its protocol, raw results and paper.

**Authorship and intent.** Experiments and reports in `fastlane/experiments/`
are AI-generated (proposed by Codex, executed and written up by the engine) and
are labeled as automated research. They are exploratory mini-studies without
preregistration, meant to surface leads quickly; anything notable is intended
to get a formal, preregistered human-run follow-up. They are not clinical
evidence and say nothing about human patients.

Created by Arthur Y. Su. Released under the MIT License — please cite via
`CITATION.cff` if you build on it. This is a public snapshot of an actively
developed private working repository.

---

## Original technical README

A portable Python/SQLite research coordinator with a persistent queue. It has
two parts:

- **Routine work** runs through Codex on your ChatGPT subscription. Claude is
  used only at review or publishing checkpoints you choose. When either service
  hits a usage limit, the queue waits and picks up later; there is no paid
  fallback. See [Routine work](docs/ROUTINE_WORK.md).
- An **offline, synthetic-only** deterministic runner for the experiment
  registry. It demonstrates duplicate prevention; real protocols are refused.

The optional [research director](docs/PROGRAM.md) proposes and reviews local
preregistrations, records delegated in-silico approvals, and queues reviewed
harness builds. It is disabled by default; experiment execution is a scaffold.

The [research feedback engine](docs/RESEARCH_ENGINE.md) preserves open questions
and owner-provided outcomes, queues independent analysis reviews, and feeds
reviewed next questions back to the director with a limit on unfinished studies.

Optional [arXiv intake](docs/ARXIV.md) collects versioned abstracts and available
HTML paper text daily for the director, with explicit coverage limits.

A [synthetic computational pilot](docs/COMPUTATIONAL_PILOT.md) now executes,
audits, collects results, and adapts its next run automatically without AI calls.
It validates the pipeline only; live model experiments remain unavailable.

No dependencies at runtime. Python 3.11+ on macOS or Linux; no Docker or
database server required. On a Mac, `bash scripts/install_mac_scheduler.sh`
runs the tests, detects the Codex and Claude CLIs, and schedules a bounded pass
every 30 minutes with launchd (macOS's built-in scheduler).

**Study 3 is unconnected.** This repository has no verified access to its files,
models, or live status. Nothing here modifies, restarts, or duplicates it.

## Run now

From the checkout root:

```sh
python3 -m unittest discover -s tests -v
python3 -m codexautopsych --lab .lab/demo demo
python3 -m codexautopsych --lab .lab/demo status
```

The demo uses synthetic arithmetic (zero offset), records a negative result,
and runs `enqueue → execute → independent artifact audit → report → restart`.
Run it again: the same registered ID is reused, with execution count **1**.
`examples/demo-output.json` is captured output; `examples/demo-artifacts/`
contains a copy of the actual fixture protocol, raw results, manifest, status,
decisions, and handoff. These are software fixtures, not psychological evidence.

Installation is optional: `python3 -m venv .venv`, then
`.venv/bin/python -m pip install -e .`. The source-tree commands above need no
network, packaging tools, credentials, model, or subscription.

## CLI

Global `--lab DIRECTORY` goes before the subcommand. Default state is `.lab/`.
Use one local state directory for one lab and all its processes.

```sh
python3 -m codexautopsych init
python3 -m codexautopsych register arithmetic --protocol examples/synthetic_protocol.json
python3 -m codexautopsych enqueue plan --role coding-worker --payload '{"experiment":"arithmetic"}'
python3 -m codexautopsych cycle --role coding-worker --owner coder-1 --limit 1
python3 -m codexautopsych enqueue execute --role coordinator --payload '{"experiment":"arithmetic"}' --depends 1
python3 -m codexautopsych cycle --role coordinator --owner coordinator-1 --limit 1
python3 -m codexautopsych enqueue audit --role independent-reviewer --payload '{"experiment":"arithmetic"}' --depends 1 2
python3 -m codexautopsych cycle --role independent-reviewer --owner reviewer-1 --limit 1
python3 -m codexautopsych enqueue report --role coordinator --payload '{"experiment":"arithmetic"}' --depends 3
python3 -m codexautopsych cycle --role coordinator --owner coordinator-1 --limit 1
python3 -m codexautopsych report
python3 -m codexautopsych recover
```

IDs above assume a fresh lab. Use the actual IDs returned by `enqueue`.
`claim --file claim.json` appends a source-grounded claim: required keys are
`claim`, `scope`, `sources` (objects with `reference` and `locator`),
`competing_explanations`, `predictions`, `evidence`, and `falsifiers`. The latter
five fields are nonempty arrays. Sources are references, not automatically fetched
or endorsed. Synthetic claims remain scoped to fixtures; pending evidence is
explicitly labeled. A registry entry is not proof of execution.

## State and guarantees

- Tasks: `queued → running → succeeded`, or retry to `queued`, then `failed`
  when the bounded attempt budget is exhausted. Dependents of a failed/blocked
  task become `blocked`. Dependencies must already exist, preventing cycles.
- Each task has a dedicated role, fresh payload/dependency context, current
  owner, attempt count, timestamps, result, and error. The coding worker proposes
  a synthetic execution plan; it does not edit code or implement a real agent.
- Workers are **deterministic role scaffolding, not live AI agents**. Independent
  reviewers must use a different owner from both author and execution owner,
  reread raw artifacts, check checksums, and verify each deterministic result.
  Owner strings are coordination identities, not authenticated security accounts.
- SQLite transactions serialize claims; OS `flock` prevents simultaneous worker
  cycles and maintains a separate single execution-owner lock. Locks release
  when a process dies. Timestamp expiry never authorizes a duplicate launch.
- Launch intent and execution count are committed before creating artifacts.
  A crash before the completion manifest makes the run `interrupted`; it is
  **never automatically relaunched**, even if no output was produced. Complete
  artifacts can be reconciled without executing again. Failed/interrupted IDs
  are not resettable by the CLI. At-most-once launch trades availability for
  duplicate prevention, not a guarantee that every run finishes.
- Approved protocol JSON is canonically SHA-256 hashed and copied into the run.
  Raw files use exclusive creation; manifests contain raw hashes, timestamps,
  negative-result flags, and deviations. Artifact checks compare the registry,
  protocol copy, raw hash, and actual expected rows. Integrity hashes detect
  accidental changes; they are not signatures against a database administrator.
- `verified` records artifact verification, not scientific validity. A later
  tampering audit can fail even if verification previously succeeded; inspect
  task audit outcomes and deviations, not the boolean alone.
- `STATUS.json`, `DECISIONS.jsonl`, and `HANDOFF.md` are generated shared views;
  SQLite is authoritative. Event/claim logs are append-only through the public
  API. Reports are regenerated, while raw output and protocol files are preserved.

Use a local disk, not NFS/iCloud/network shares. Locks protect only processes
using this same lab directory on this host. Do not copy live SQLite state to a
second machine and run both copies: locks do not coordinate different hosts.
Stop coordinator processes before copying state; preserve database/WAL files
consistently (prefer SQLite's backup API). This is a v1 local coordinator, not a
distributed scheduler or secure multiuser service.

## Scientific boundaries

V1 refuses real protocols, even if an arbitrary approval string is supplied.
An actual study needs a separately approved protocol and runner integration.
Do not turn the synthetic demo into a Study 3 launcher. Preserve approved
protocols and raw outputs. Declare `exploratory` or `confirmation`; confirmation
labels require genuine prior approval outside this fixture demonstration.
Record deviations, interruptions, and negative results. AI agreement and artifact
review are not empirical replication. New scientific protocols, publication, and
spending beyond an established budget require the owner's approval.

Routine Codex work is enabled only by running the Mac installer (or
`config codex.enabled true`); that is the owner's authorization to use the
subscription. The older one-off `codex-task` adapter remains advisory and
opt-in. See [Codex integration](docs/CODEX_INTEGRATION.md).
See [Mac handoff](MAC_HANDOFF.md) for the exact next local-Codex prompt.
