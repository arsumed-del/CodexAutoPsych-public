# Build spec — research director (for Codex)

Read AGENTS.md, README.md, docs/ROUTINE_WORK.md, program/CHARTER.md and the code
in codexautopsych/ first. Extend; do not weaken existing invariants or tests.

## Hard rules for this build
- Work only inside this folder. Do not install software, change launchd, use
  the network, call Codex/Claude for real, or run any language model.
- Never read, run, or modify ~/Projects/affect-suppression-steering or anything
  Study 3. Do not touch other ChatGPT chats or scheduled tasks.
- All tests use fakes (see tests/test_routine.py style). Stdlib only, Python 3.11+.
- Finish with: python3 -m unittest discover -s tests -v  (all must pass), then
  git add -A && git commit with a clear message. Do not push.

## 1. Program config (extend routine.DEFAULT_CONFIG)
"program": {"enabled": false, "director_interval_days": 7,
 "max_proposals": 5, "auto_approve_in_silico": true,
 "execute_experiments": false, "study3_process_patterns":
 ["affect-suppression-steering", "study3"]}

## 2. Director (new module codexautopsych/program.py)
- `director_due(lab, cfg, clock)` — true if enabled and no director task
  created in the last interval_days.
- `routine.run()` calls `program.maybe_enqueue_director(...)` at the start of
  each pass (inside the cycle lock). It enqueues ONE Codex task, kind
  "analysis", title "Director: propose next studies", checkpoint "none", whose
  inputs are copies of: program/CHARTER.md, every file in program/sources/
  (corpus notes, owner-placed results, later Zotero exports), and a fresh
  export of the claims ledger + recent study outcomes (program/state.json,
  generated). Instructions: propose up to max_proposals studies, each written to
  outputs/proposals/<slug>.md as a preregistration draft with: question,
  theory link (cite the source file), hypotheses with exact statistics,
  capacity-matched nulls, controls, dose/threshold design, decision rule,
  stopping rule, compute estimate (GPU hours), and a JSON block
  {"slug","title","in_silico":true,"gpu_hours":N}.
- When the director task succeeds, a follow-up step (in the run loop, not a
  model call) parses outputs/proposals/*.md and, for each valid proposal,
  enqueues a "draft" Codex task "Refine preregistration: <title>" with the
  proposal as input and checkpoint "review" (Claude red-team).
- Invalid/missing JSON blocks are logged and skipped, never crash the pass.

## 3. Delegated approval
- When a refine task's review is approved and auto_approve_in_silico is true
  and the proposal says in_silico true: write
  program/studies/<slug>/PREREG.md (the reviewed text), APPROVAL.json
  {"policy":"owner delegation 2026-10-08","sha256":...,"review_task":...,
   "status":"internally preregistered (local hash) - not publicly deposited"},
  append a claim-ledger entry via Lab.add_claim with status pending, and add
  an ATTENTION.md line listing it as ready for the owner to deposit publicly.
- Otherwise leave it awaiting the owner (existing awaiting_human path).

## 4. Build step (no model execution)
- For each approved study enqueue a "code" Codex task: build the study harness
  under program/studies/<slug>/harness/, analysis code validated on synthetic
  data with planted and null effects, and freeze code by SHA-256 into
  APPROVAL.json. Checkpoint "review".

## 5. Execution gate (scaffold only in this build)
- `gpu_free(cfg)`: false if any running process command line (from
  `ps -axo command`) contains any study3_process_patterns entry, or if the lab
  execution lock is held. Read-only check; never signals or kills.
- If execute_experiments is false (default), approved+built studies stop at
  status "ready to run" in ATTENTION.md. Do not implement model execution yet.

## 6. Tests (tests/test_program.py, fakes only)
director due/not due; director enqueued once per interval; proposals parsed
into refine tasks; malformed proposal skipped; delegated approval writes
PREREG.md + APPROVAL.json with correct hash and pending claim; approval NOT
automatic when auto_approve_in_silico false or in_silico false; gpu_free false
when a fake ps output contains "affect-suppression-steering"; Study 3 paths
still refused as inputs; nothing ever marked published.

## 7. Docs
Add docs/PROGRAM.md (plain language: what runs alone, what needs Arthur) and a
README pointer. Zotero intake is future work: leave a stub note that
program/sources/zotero/ will hold exported items.
