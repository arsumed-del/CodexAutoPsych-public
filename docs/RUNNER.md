# Experiment runner (live studies on this Mac)

Runs ONE approved study at a time, the way Studies 1-3 were run. It never designs,
publishes, uploads, or calls Codex/Claude. The fake-model demo in tests is synthetic.

## A study folder contains
- `study.json` — name, research question, `designed_by` (must be "claude"), `reviewed_by`
  (an independent reviewer, e.g. "codex"), the files to freeze, and the stages in order
  (for example environment → smoke → calibrate → main).
- `adapter.py` — the study's code: which cells to run, how to run one cell, the pass/fail
  check after each stage, and the final analysis.
- The preregistration and any prompts listed as frozen files.

## Arthur's steps
1. Approve (records fingerprints of every frozen file):
   `python3 -m codexautopsych --lab .lab/research live-approve path/to/study`
2. Start in the background (keeps the Mac awake, survives closing windows):
   `python3 -m codexautopsych --lab .lab/research live-start path/to/study`
3. Check progress: `... live-status path/to/study` (or open the study's RUN_STATUS.md).
4. Stop: `... live-stop path/to/study` — it stops after the current cell. `live-start` resumes.

## Safety rules it enforces
- Refuses to start without your approval, or if any frozen file changed afterward
  (checked at start and before every stage). A changed design needs a new study folder.
- A failed stage check stops the study for good; it is never retried with other settings.
- Every finished cell is saved immediately with a fingerprint; a crash or stop resumes
  where it left off with no repeated cells; tampered records are detected.
- Only one study at a time, and it waits if the Mac is busy.
- When done, the outcome goes into the engine, which queues an independent review.
