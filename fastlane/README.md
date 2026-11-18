# AutoPsych fast lane

Automated exploratory mini-experiments (owner decision 2026-11-14). Separate from the
preregistered studies. Everything it produces is labeled AI-run, exploratory and not preregistered.

## The loop (runs by itself on every 30-minute pass)
1. **Propose** - the next experiment comes from `seeds/` (five starter experiments), then from Codex,
   which may only fill in the form in `PARADIGM.md` (it never writes code). About 70% of proposals see
   the program's research questions (theory lane); 30% see only public preprints (blind lane).
2. **Register** - the plan is frozen with a hash and committed to git before any model runs.
3. **Run** - the harness steers a local model while it writes replies, then asks rating questions
   with steering off. One experiment is roughly 100-200 short conversations.
4. **Analyze** - fixed analysis, one primary contrast, same rule for every experiment.
5. **Replicate** - an effect is rerun on held-out prompts in the same model, then in a second model.
   Nulls are kept and reported.
6. **Write and review** - numbers and tables come straight from the data. Codex writes the
   interpretation; a separate Codex session reviews it; text with a number that is not in the data is
   refused. If the review does not approve, only the plain report is released, with the reviewer's
   concerns printed in it.
7. **Release** - during the trial period each report waits for Arthur ("approve E0001" /
   "reject E0001 because ..."). Released reports go into a weekly Zenodo deposit (engine as creator,
   Arthur as Supervisor), then up to two posts a week on Agent4Science from @constraintlab citing the DOI.

## Changes since the first version (2026-11-16)
- Thinking mode: the model writes a thinking block before its answer and is steered during both.
- One Zenodo record per experiment (not a weekly bundle), at most `max_deposits_per_week`. Each record
  carries LEDGER.md, the list of every experiment run so far. Failed manipulations, invalid measurements,
  failed runs and aborts are listed in the ledger only and are never deposited.
- Replications use more prompts than the first run (`replication_prompts`, default 24).
- A replicated effect automatically queues two follow-up controls ahead of everything else: the same
  experiment with thinking length held equal across arms, and at a lower dose.
- Smoke check: after the first six conversations of a run, the rating must be usable (digits answered, not
  stuck at an end of the scale), otherwise the run stops as failed.
- Suggested promotion rule for a formal preregistered study: replicated, still present in the length
  control, and smaller (not absent, not larger) at the lower dose.

## Where things are
- `STATUS.md` - what it is doing now and anything waiting for you (rewritten every pass).
- `experiments/E0001-.../paper.md` - each report. `spec.json` is the frozen plan; `runs/` the raw records.
- `LEDGER.jsonl` - every event, including nulls, failures and your decisions.
- `config.json` - switches (see below). `digests/` - what was deposited.

## Switches
- Kill switch: create a file named `STOP` in this folder. Runs stop after the current conversation and
  nothing is published. Remove the file to continue.
- `python3 -m codexautopsych --lab .lab/research fast-config enabled false` - off (true - on).
- `... fast-config approval_required false` - end the trial period (reports release without you).
- `... fast-config max_experiments_per_day 2`, `max_posts_per_week 2`, `deposit_interval_days 7`.
- `... fast-status`, `... fast-approve E0001`, `... fast-reject E0001 --reason "..."`.
- Stop one experiment for an engineering reason (a bug, a broken run): put a file `ABORT.json` with
  `{"reason": "...", "by": "..."}` in its folder. The data are kept, the reason goes in the ledger and in
  `DEVIATION.md`, and the experiment is not used for any result.

## Outcomes a report can have
replicated in two models; replicated in the same model only; not replicated; null; manipulation check
failed (the steering did not change what the model wrote, so a null says nothing); invalid measurement;
failed run; aborted.
