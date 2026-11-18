# Build spec: experiment runner (live, local, approved studies only)

Author: Claude (claude.ai session linked to the Mac), 2026-10-12. Builder: Codex.
Reviewer: Claude Code (independent of the builder). Approver: Arthur.

Read first: AGENTS.md, STATUS.md, docs/LOG.md, docs/PROGRAM.md, docs/RESEARCH_ENGINE.md,
codexautopsych/runner.py, codexautopsych/pilot.py. All AGENTS.md rules apply.

## Goal
Let the engine execute an owner-approved, frozen study on this Mac (Apple Silicon, MPS),
the way Studies 1-3 were executed in ~/Projects/affect-suppression-steering, and hand
the preserved outputs back to the research feedback engine. Do NOT run any real study
in this task. Building and testing only.

## Must have
1. Approval gate. A live study runs only if: protocol hash matches the hash Arthur approved
   (recorded approval file, like PREREG_APPROVED), and the study's final design is marked as
   Claude-finalized and independently reviewed. Anything else is refused before any model load.
   Keep the existing synthetic runner unchanged and separate.
2. Frozen spec. Protocol, config, prompts and analysis code are hashed at approval; the runner
   verifies every hash at start and at each resume, and stops if any changed.
3. Staged execution with stop rules: environment check -> smoke test -> calibration/validation
   gates -> main run -> frozen analysis. A failed gate stops the study and reports; it never
   retries with changed settings, alternative doses, thresholds or models.
4. Resumable background jobs: one cell = one checkpointed record (append-only JSONL with hash),
   safe to kill and resume; nohup + caffeinate; a lock so only one live study runs at a time;
   resource check before start (reuse the existing worker resource gate).
5. Study adapter interface: the runner is generic; each study supplies a small adapter
   (load model, run one cell, score). Ship ONE adapter that is a tiny CPU-only labeled
   synthetic model for tests. No real model downloads in tests.
6. Logs and status: plain-English progress lines in the study's STATUS.md and in the engine
   STATUS.md (cells done / total, ETA, current stage, any stop reason). Deviations go to a
   DEVIATIONS.md per study; engineering fixes are allowed only if they change no scientific setting.
7. Outcome hand-off: when analysis finishes, record the outcome through the engine's own code
   path so the research feedback engine queues an independent analysis review.
8. Never publish, never upload, no paid API calls, no credentials anywhere. Study 3 untouched.

## Tests (fake model, no network)
Approval refused on hash mismatch; changed file after approval stops the run; kill-and-resume
produces identical results with no duplicated cells; failed gate stops with no retry; lock
prevents two studies; outcome recorded and review queued. All existing tests still pass;
demo still passes.

## Done means
Tests pass, docs/RUNNER.md written in plain English (how Arthur approves and starts a study,
how to check progress, how to stop it), README/STATUS mention the runner, dated docs/LOG.md
entry, committed locally. Do not push to GitHub and do not change repository visibility.
Then stop and report: what was built, what is untested, anything that needs Arthur.
