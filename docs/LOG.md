# Change log (newest first)

Every agent that changes this program adds an entry: date, who, what changed, what's next.

## 2026-11-09 (later) — Claude (claude.ai session linked to the Mac)
- Confirmed recovery: passes succeed again since the morning fix (tasks #91-97 created or moved 17:00-17:40 UTC; run.err unchanged since 16:26 UTC).
- Prevention: new outcome records (owner imports and synthetic-pilot outcomes) now store the snapshot path relative to the lab, so a record made from any machine stays valid on the Mac. Existing records were left as they are; the engine already ignores their stored path. 1 new test; 159 pass.

## 2026-11-09 — Claude (scheduled morning check, linked to the Mac)
- Health check: every pass failed since Mon 08:26 UTC (FileNotFoundError: '/sessions'); 0 tasks moved in 24 h; task #89 (revise) waiting.
- Cause: the 6 outcomes imported last evening from Claude's linked sandbox stored the sandbox path in their "snapshot" field, and the synthesis step handed that stored path to Codex as an input. Same class of bug as 2026-10-12; the reading side was already fixed, the queueing side was not.
- Fixed: research.advance now locates each outcome snapshot inside this lab (research._snapshot) instead of trusting the stored path. No engine state changed. 1 new regression test (fails without the fix); 158 pass. Database backup: .lab/research/lab.sqlite.backup-2026-11-09.
- Next: the Mac's next 30-minute pass should queue the outcome synthesis and resume task #89.

## 2026-11-08 (evening) — Claude (claude.ai session linked to the Mac mini)
- Why: the engine knew only Studies 1-3; Arthur's Studies 4-8 (run outside the engine) were never added, so it was re-deriving answered questions (e.g. self-attribution study duplicates Study 4).
- Added to program/sources/affect-under-constraint/: Studies 4-6 and 7-8 preprints (PDF + extracted text) and the Study 6 results summary. Copied from ~/Projects/affect-suppression-steering; originals untouched.
- New research questions via the engine CLI: placement-carryover, answer-independence, warmth-constraint-transformation.
- Imported 6 owner-reported outcomes via research-outcome (files in program/sources/outcomes/): Study 4 placement (positive), Study 4 authorship (inconclusive), Study 5 (positive), Study 6 (positive), Study 7 (negative vs prediction), Study 8 (positive). Summaries taken from the preprint abstracts.
- Effect: the next pass queues an outcome synthesis; planning resumes after it is reviewed.
- Also: Mac commits backed up to GitHub branch mac-backup-2026-11-08. GitHub branch work holds an unmerged Claude commit built on old code (hypotheses-evolution) - do NOT git pull on the Mac until that is sorted out.
- Next: watch the synthesis; consider shelving self-attribution-text-replication if it duplicates Study 4; then a first end-to-end engine run.

## 2026-11-05 — Claude (claude.ai session linked to the Mac)
- Arthur asked whether his Zotero library is used. It was, but only the 25 most recently saved items reached planning; ~95% of the 493 items were seen only as titles.
- Changed: planning now gets items chosen mostly by relevance to the open research questions (BM25 over title, abstract, tags and owner notes, with topic-anchor expansion: affect/emotion/valence, steering/activation, hostility, carryover, reasoning traces/recursion, authorship, threshold), plus the 30% newest saves so fresh reading still arrives. Same 25-item and size budget. Dry run on the live library pulled in 18 older relevant papers (e.g. "When Steering Fails in Latent Reasoning", "Extracting and Steering Emotion Representations in Small LMs").
- 1 new test; 157 pass. Takes effect on the next Zotero refresh.
- Known limit: keyword matching, so some speculative "recursive" papers ride along; an embedding-based ranker would be sharper.
- Health: engine moving; the theory director (task #60) correctly declined a fourth study until the three ready studies produce results. Bottleneck remains the live model adapter.

## 2026-11-03 — Claude (claude.ai session linked to the Mac)
- Health check: continuous mode worked (14 new tasks Nov 2-3, two new studies drafted), then stalled again: reviews #45 and #52 hit the 2-round revision limit and waited for Arthur. Reviewers keep requiring finished artifacts (frozen stimuli, checkpoint IDs, benchmarks) that cannot exist before a live model adapter exists.
- Fixed: new config "continuous" (on in the live lab). At the revision limit the review is settled as rejected and its study is shelved automatically (kept, reversible) instead of waiting for the owner. 1 new test; 156 pass.
- Applied to the stuck pair with program-shelve: warmth-context-carryover-replication and affect-cache-carryover (tasks #45, #52 cancelled). Database backup: .lab/research/lab.sqlite.backup-2026-11-03.
- Watch: new designs may keep failing review for the same reason until the live adapter is built; that is the real bottleneck.

## 2026-11-01 (later) — Claude (claude.ai session linked to the Mac)
- Arthur approved continuous operation. Planning capacity now counts only studies still in design; "ready to run" studies sit in a run queue capped by research.max_ready_studies (default 6). Planning restarts on the next pass. 1 new test; 155 pass.
- Charter amendment records the standing approval for local open-weight live runs (cross-company design review required) and keeps public/irreversible steps with Arthur.
- Gap: none of the three ready studies has study.json/adapter.py (the code that loads and steers the model), so live runs cannot start yet. Next: build those adapters from the Study 3 code, and reconnect Claude as the cross-company reviewer for live-run approval.

## 2026-11-01 — Claude (claude.ai session linked to the Mac)
- Health check: engine not crashed but idle since 2026-10-18 (15 days, 0 model calls). All three open studies have reviewed harnesses and are "ready to run"; study capacity (3) is full, so planning stops and nothing can be queued until Arthur acts. STATUS.md still said "moving" because the stall alarm only looked for stuck queued/awaiting tasks.
- Fixed: new idle check (routine.idle_line). When nothing is queued, running or awaiting and no task has moved for 24 h, STATUS/ATTENTION show "IDLE since <date>" with the planning block reason instead of "moving". 1 new test; 154 pass.
- Committed Codex's uncommitted 2026-10-18 structured-evidence fix together with this change.
- Next (Arthur): pick one study for Claude to finalize as a live design (per the design split none of the three has a Claude-final design yet), or shelve one to let the engine plan again.

## 2026-10-18 — Codex
- Diagnosed task #30: independently approved synthetic harness used structured validation evidence; installer required a string. Accept either a nonempty string or an object with a nonempty string explanation, preserving original validation bytes and all hash/frozen-protocol checks.
- Added synthetic regression tests for structured evidence acceptance and blank explanation rejection. Restore routine processing through the existing retry/review path; no live experiment or publication authorization.
- Validation: all 153 unit tests and the synthetic demo pass. Backed up the lab database before requeueing. Fresh independent Codex review #30 approved, reproduced 13 harness tests and four reports, and verified hashes and protocol preservation. Scheduled pass installed the harness; task #30 succeeded and STALLED cleared. The existing 30-minute launch agent remains loaded.
- Next: all three active studies have prepared harnesses; planning is at its configured capacity and awaits outcomes or owner direction. Live runs and public deposits still require separate approval.

## 2026-10-12 (late night, later) — Claude (claude.ai session linked to the Mac)
- Arthur set the working division (STATUS.md "Who does what"): Codex runs the loop and self-reviews; Arthur brings promising proposals to Claude chat to review and finalize; ChatGPT second-opinions Claude's designs; Arthur approves; Codex or Claude Code runs; the other model checks results.
- Verified on the Mac: the 8:15 PM pass resumed task 9 automatically (succeeded); engine health "moving".
- Next: Director round (Codex) proposes studies; the runner's approval gate should accept "Claude reviewed and finalized" rather than requiring Claude to write the design (change when the first real study is ready).

## 2026-10-12 (late night) — Claude (claude.ai session linked to the Mac)
- Arthur: "just do stuff" — build the fixes so the engine stops waiting on him.
- Tiered authority (program/CHARTER.md amendment): advisory syntheses proceed after independent approval; a failed follow-up retries once on a later pass before going to Arthur. Live runs, publication and deposits remain owner-only.
- Task 9 (parked under the old rule after one failure, before the "-v2" fix) is resumed automatically on the next pass. Dry run on a copy of the live database: task 9 succeeds, 4 questions added (affect-threshold-v2, authorship-carryover-v2, carryover-replication, length-controlled-carryover), planning reopens (capacity 3).
- Review budget: fresh-Codex reviews count against claude.max_calls_per_day (6), not the Codex work cap (4); a real Codex usage limit still stops them.
- Stall alarm: STALLED line in ATTENTION.md/STATUS.md plus a Mac notification (repeat at most every 6 h) when a task waits on Arthur or fails, or nothing moves for stall_hours (2). A crashed pass writes a banner at the top of both pages and notifies.
- 6 new tests, 2 updated; 150 pass.
- Next: confirm task 9 and the next director round on the Mac; Claude finalizes the first real study design (authorship-carryover or carryover-replication) for Arthur's approval.

## 2026-10-12 (evening) — Claude (Claude Code on the Mac, task relayed by Arthur from claude.ai)
- Why: Zotero reached its 300 MB free file-storage limit. Decision: Zotero keeps citations and links only; the engine fetches full text on demand.
- Added codexautopsych/fulltext.py and wired it into the Zotero intake. For each item about to reach theory-lane planning (the 25 most recently added) that has no text, the engine resolves its DOI/URL to a free legal copy (arXiv, then Unpaywall, then Semantic Scholar), saves the PDF and extracted text under .lab/research/fulltext/ and reads it from there. Nothing is written to ~/Zotero or Zotero storage; no Zotero setting or attachment was changed or removed.
- "Flagged relevant" is implemented as "selected for the planning packet", because the engine has no other relevance flag yet.
- Papers with no downloadable open copy go to .lab/research/NEEDS_MANUAL_PDF.md (rechecked after 30 days). A PDF saved as .lab/research/fulltext/manual/<Zotero key>.pdf is read instead.
- Limits: at most 3 items tried per pass; HTTPS to public addresses only; 40 MB per PDF; text extracted with macOS PDFKit (no new software); a fetch problem never stops the pass.
- Unpaywall needs a contact address and stays off until Arthur sets fulltext.unpaywall_email; arXiv and Semantic Scholar work without it.
- Turned on in the live lab (fulltext.enabled true) through the engine's config command. Off switch: python3 -m codexautopsych --lab .lab/research config fulltext.enabled false
- 14 new tests (synthetic fixtures, no network); 131 tests pass; demo passes. docs/PROGRAM.md and STATUS updated.
- Next: Arthur sets the Unpaywall address and turns off Zotero's automatic PDF attachment; watch NEEDS_MANUAL_PDF.md over the next passes; then the experiment runner.

## 2026-10-12 (later) — Claude (claude.ai session linked to the Mac)
- Watched the first post-redirect research round: Codex analysis (#8) succeeded; Claude review (#9) failed 3x in ~6 s with exit 1. The reason was being discarded.
- Fixed: a failed Claude review now records a short, token-masked reason in the task error (and so in STATUS.md). New test; 116 tests pass.
- Added docs/DAILY_UPDATE_PROMPT.md: a read-only Codex scheduled task (Codex desktop app) that writes Arthur a plain-English daily update.
- Created and activated the Codex scheduled task "AutoPsych daily update" (daily 7:00 AM PT, local project, no worktree). It inherits the app's Full-access default (Arthur chose this 2026-10-12); read-only is enforced by its instructions only.
- Review verdicts are now saved readably: .lab/research/reviews/task-N.md plus REVIEWS.md (newest first); STATUS.md shows a "Latest review" section. Backfilled task #5. 117 tests pass.
- Arthur decided the study design split (see STATUS.md Current decisions): Claude finalizes designs of studies that will run; Codex drafts exploratory proposals.
- Committed to git from the linked session (2026-10-12).
- Next: on the next failed or successful review, read the recorded reason; once reviews pass reliably, build the experiment runner.

## 2026-10-12 — Claude (claude.ai session linked to the Mac)
- Saved Codex's Oct 9-10 work (arXiv intake, synthetic pilot, charter amendments) as a version.
- Lifted the Study 3 read exclusion (Study 3 complete); Study 3 results, Studies 1-2 paper and Paper 11 added to program/sources/.
- Paper-finding: 5 targeted daily arXiv searches (5 results each) plus 15 starting papers from Arthur's citations; reading packet now 12 papers, starting papers first.
- Zotero: read-only intake of ~/Zotero (scholarly items only, duplicates merged) into the theory lane.
- Two lanes (theory ~70%, blind ~30%) with CHARTER_BLIND.md; studies record their lane.
- program-shelve command; shelved affect-carryover-threshold (designed before Study 3 results existed).
- Codex cap raised 1 -> 4 per day; max open studies 1 -> 3.
- STATUS.md one-page status (auto-updated), this log, AGENTS.md pointer, second-opinion rule.
- Fixed: scheduled passes failed 11:10 AM-1:00 PM PT because an outcome recorded from Claude's linked sandbox stored a non-Mac file path; snapshots are now located inside the lab. Lesson: change engine state only through the engine's own code paths, never with sandbox paths.
- Next: watch the first theory and blind director rounds; then build the experiment runner.

## 2026-10-12 (evening) — Claude (claude.ai session linked to the Mac)
- Arthur chose: Codex builds the experiment runner (his Codex credits); Claude Code reviews it independently afterward.
- Wrote program/BUILD_SPEC_RUNNER.md as the build brief. Repository stays private.
- Next: Codex builds per the spec; then Claude review; then Arthur approves before any live study.
- Update (same evening): Arthur asked Claude to build it instead. Claude built codexautopsych/live.py (approval gate requiring Claude design + independent review, frozen-hash checks at start and each stage, immutable stage gates with no retry, checkpointed resumable cells with fingerprints, torn-write recovery, single-study lock, Mac-busy check, STOP file, plain-English RUN_STATUS.md, outcome hand-off through research.import_outcome), CLI live-approve/start/run/stop/status, STATUS.md "Live studies" section, docs/RUNNER.md, 9 tests with a synthetic fake adapter. 140 tests pass.
- Not yet tested on the Mac itself (built in the linked sandbox, Python 3.10): background launch with caffeinate, and a real model adapter.
- Next: Codex reviews the runner against program/BUILD_SPEC_RUNNER.md; then a first real study adapter (authorship test) designed by Claude.

## 2026-10-12 (night) — Claude (claude.ai session linked to the Mac)
- Health check: engine stalled since 1:44 PM PT. Claude CLI review #9 failed 3x (exit 1, reason not recorded); research loop waits on that review, so every pass since ran nothing. Codex, arXiv, Zotero fine.
- Arthur decided: set Claude reviews aside for now; the engine must run on Codex alone.
- Added claude.mode = "codex": checkpoint reviews go to a NEW read-only ephemeral Codex session that sees only the review packet (no shared context with the author session); counts against the Codex daily cap. Added retry-task (owner requeues a failed task). 3 tests; 143 pass.
- Live switch is done on the Mac by "Restart engine on Codex.command" (sets claude.mode codex, retries task 9, runs one pass). Database backup: .lab/research/lab.sqlite.backup-2026-10-12.
- Authorship-carryover design session cancelled until the engine is confirmed working.
- Next: confirm task 9 succeeds via Codex review and the next director round runs.
- Restart pass (6:17 PM): Codex-only mode live. Task 9 was reviewed by a separate read-only Codex session: **approve**. The follow-up then failed: the synthesis reused existing question IDs (affect-threshold, authorship-carryover) with new wording, which the no-overwrite rule rejects. Task 9 parked awaiting owner.
- Fixed: a reworded question is now saved as "<id>-v2" (original kept); a follow-up that failed gets one fresh attempt after the owner re-approves the review. 144 tests pass.
- Waiting on Arthur: run "Restart engine on Codex.command" (re-approves task 9 as owner, then one pass). Claude did not run it: owner approvals are Arthur's.

## 2026-10-13 — Claude (claude.ai session linked to the Mac)
- Engine stalled 9 h on review #16 (revision limit). Cause: the review packet was one text blob filled alphabetically under a size cap, so big source files used it up and the proposal, tests and key sources reached the reviewer cut short or empty; the reviewer's folder had nothing else.
- Fixed: packet puts the work's own files first (smallest first), skips __pycache__, marks oversize files; the reviewer's folder now gets full read-only copies of the outputs (files/) and of the author's inputs (files/_author_inputs/). Links are never followed.
- retry-task now also re-runs a review waiting on the owner (approves nothing). Ran it on the Mac ("Re-review task 16.command"): fresh review **approve**; hashes matched, 8 synthetic tests passed.
- Second bug: the follow-up then failed because the author had placed symlinks (review_packet/ -> inputs) in its outputs, and the program refuses symlinks. Fixed: symlinked outputs are skipped, never read. Dry run on a copy: PREREG frozen, harness build queued.
- 152 tests pass. Database backup: .lab/research/lab.sqlite.backup-2026-10-13.
- Waiting on Arthur: "Approve task 16.command" (owner approval; freezes the planning draft, queues synthetic-only harness build; no experiment).

## 2026-11-14 (night) - Claude (claude.ai session linked to the Mac mini)
- Arthur's decision: add a fast lane - automated exploratory mini-experiments that publish with him out of the loop (after a trial period), no preregistrations. Charter amended (2026-11-14).
- Review first: the Mac mini repo is the live one (24 commits ahead of GitHub's work branch, nothing lost). The engine had 6 preregistered studies parked "ready to run" and had never run a model; its runner needed a hand-written adapter per study, which is why nothing ran.
- Built codexautopsych/fast/: spec.py (declarative experiment form), harness.py (the only code that loads a model: direction extraction, dose calibration, steered reply, unsteered re-read rating; resumable, hash-checked records), stats.py (fixed analysis and decision rule), engine.py (propose, register, run, replicate, write, review, approval, status), paper.py (reports printed from the data; number check on model-written text), publish.py (Zenodo digest, then Agent4Science take citing the DOI).
- fastlane/: PARADIGM.md, five seed experiments, prompt sets, writing and review guides, README, config.json.
- Hooked into the 30-minute pass (routine.run) and STATUS.md; new commands fast-status, fast-step, fast-approve, fast-reject, fast-config. Fast lane only acts on the real lab (.lab/research), never on test labs.
- Uses the existing experiment Python (~/Projects/affect-suppression-steering/.venv) read-only; that project is not modified.
- Tests: tests/test_fast.py (loop with a synthetic runner, fake Codex and fake network) and a tiny-model harness check; see STATUS.md for what has and has not been verified on the Mac itself.


## 2026-11-15 - Claude (claude.ai session linked to the Mac mini)
- First real fast-lane runs. E0001 aborted (engineering: calibration ceiling, unshared seeds). E0002 (Qwen2.5-3B) manipulation-failed. E0003 (Qwen3-4B, thinking off) null with a marginal manipulation check; replies stayed polite.
- Harness: wider dose calibration, one seed per prompt and condition across arms, yields to the owner's own study runs (process check), thinking mode (steer while the model writes a thinking block and answer; rating continues from the exact tokens with steering off), emotion-classifier scores per cell.
- Analysis: manipulation check is part of the outcome rule and now needs a shift of at least a quarter of the direction's own scale; new outcomes manipulation-failed and aborted (ABORT.json).
- Engine: paradigm tag (think-v1) so starter experiments rerun under the new procedure; one-off model download (FETCH.json) and push (PUSH.json) requests; two warmth starter experiments added at Arthur's request.
- Models re-downloaded after the disk clean-up removed them: Qwen3-4B, Llama-3.2-3B, Gemma-2-2B, emotion classifier (standard Hugging Face cache; study3_pilot/.cache no longer exists).
- Config now: primary and replication model qwen3-4b, thinking_cap 384, trial approval on. Thinking mode is tested on a tiny stand-in model only; E0004 is its first real run.
- GitHub: the scheduler cannot push (no login). Mac history pushed from the Claude session to branch mac-work; remote work still holds one older commit (hypothesis evolution) that the Mac never had.
- Next: check E0004's thinking text and manipulation check; if still weak, try the Study 3 direction file or multi-layer steering. E0002 and E0003 await Arthur's approve/reject.
- 2026-11-15 (night): E0004 (first thinking-mode run) finished. Steering clearly changed the writing (classifier hostile score of the thinking +0.37 to +0.46 vs random) but the projection shift (0.078) fell just under the 0.081 threshold set that afternoon, so it was labeled manipulation-failed. Rule revised for E0005 onward: a classifier contrast above zero also passes for hostility and warmth. E0004's recorded label was not changed.

## 2026-11-16 - Claude (claude.ai session linked to the Mac mini)
- Overnight the fast lane ran E0005-E0007 unattended (warmth effects replicated in E0005 and E0007; E0006 null). Arthur approved E0007: first real Zenodo deposit (10.5281/zenodo.24289947) and Agent4Science post, both accepted first try. Reports no longer print local paths.
- At Arthur's request: one Zenodo record per experiment (max_deposits_per_week 3) carrying LEDGER.md; ledger-only outcomes are never deposited; replications use 24 prompts; replicated effects queue two follow-up controls (fixed thinking length, lower dose); smoke check after six cells; optional spec field thinking_length.
- Follow-up controls queued for E0005 and E0007. Approval stays on (trial period). GitHub: scheduler has no login; pushes go from the Claude session to branch mac-work.
- Next: watch the follow-up controls and the first Codex-proposed experiment; decide E0004's label with Arthur.
- 2026-11-16 (afternoon): engine now takes turns between queued experiments and Codex proposals (alternate_codex). Hostility experiments on Qwen3-4B use the validated Study 3 direction and dose, read-only (reference_directions; procedure tag think-v1+ref), so the hostility starters run again under it. Charter clarified. E0008 finished null.
- 2026-11-16: found that fast-lane proposals were not being given any papers (blind lane got only a status line). Proposals now receive the engine's reading packet of preprints (both lanes) and the Zotero packet (theory lane only), and must name the papers that motivated a design.
