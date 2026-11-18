# CodexAutoPsych status

Read this first (Claude, Codex, or Arthur). The top part is maintained by hand; the "Engine
right now" part below is rewritten automatically on every 30-minute pass.

## Fast lane (Arthur's decision, 2026-11-14) - the engine's main activity now
- The engine runs small exploratory experiments by itself and writes a short report for each: propose, freeze the plan, run on a local model, analyze, replicate any effect (held-out prompts, then a second model), write, automated review. No preregistrations. Nulls and failures are kept.
- Trial period: each finished report waits for Arthur. Tell Claude "approve E0001" or "reject E0001 because ...". After a clean run of approvals he can switch approval off.
- Released reports go into a weekly Zenodo deposit (creator: the engine; Arthur as Supervisor), then at most two posts a week on Agent4Science from @constraintlab citing the DOI. Everything is labeled AI-run, exploratory, not preregistered.
- Kill switch: create a file named STOP in the fastlane folder. Status and anything waiting for him: fastlane/STATUS.md (also summarized below). How it works: fastlane/README.md. Authority: program/CHARTER.md, amendment 2026-11-14.
- The preregistered pipeline below is unchanged and is the route for formal studies (Arthur's own AUC studies, and any fast-lane result worth promoting). Its six "ready to run" studies stay parked.

## Who does what (Arthur's workflow, 2026-10-12)
1. **Codex (automatic engine):** reads papers, proposes studies, drafts preregistrations; a separate fresh Codex session reviews that work. Claude Code's automatic reviewer role is OFF.
2. **Arthur** brings promising proposals to **Claude (claude.ai chat, linked to this Mac)**, which reviews them as an independent model and finalizes the design. Claude also builds and fixes the engine.
3. **ChatGPT** gives the second opinion on Claude's final design; disagreements go back to Claude.
4. **Arthur approves.** Nothing runs or is published without that.
5. **Codex runs the experiment** on this Mac, or **Claude Code** when Arthur wants Claude hands-on.
6. **After a run, a different model checks the results** (Claude if Codex ran it; Codex if Claude ran it).
- Rule: whoever writes a design, a different company's model checks it.
- If Claude is out of usage, Codex in the ChatGPT app can continue from this page and docs/LOG.md.

## Current decisions (2026-10-12)
- Manual studies are paused; focus is building the engine.
- Two planning lanes: theory (tests Arthur's theories, ~70%) and blind (never sees them, ~30%).
- Arthur's Zotero library feeds the theory lane only (read-only, research papers only).
- Zotero stores citations and links only (2026-10-12, storage limit reached): the engine fetches free legal full text on demand into .lab/research/fulltext/; papers with no open copy are listed in .lab/research/NEEDS_MANUAL_PDF.md. Existing Zotero attachments are left alone.
- Codex limited to 4 engine work tasks per day, plus up to 6 independent reviews per day on their own budget (ChatGPT subscription; no paid routes).
- Tiered authority (2026-10-12): internal advisory steps proceed after an independent review approves them; live runs, publication and public deposits still wait for Arthur. See program/CHARTER.md.
- Stall alarm (2026-10-12): STALLED appears below and the Mac shows a notification if the engine stops moving for 2 hours.
- Study design split (2026-10-12): Codex drafts exploratory proposals; any study that will actually run gets its final design from Claude (as Studies 1-3 were), then independent review by Codex, then Arthur's approval.
- Experiment runner built 2026-10-12 (docs/RUNNER.md): runs only approved, frozen studies; not yet used for a real study. Next: independent review by Codex, then first real study design.

## The science so far
- Studies 1-2 (preprint): https://doi.org/10.5281/zenodo.23182205
- Study 3 (preprint): https://doi.org/10.5281/zenodo.23323490 - reasoning carries a steered state forward.
- Study 3 code and data: https://doi.org/10.5281/zenodo.23323488
- All code: https://github.com/arsumed-del/affect-under-constraint
- Open question queued for the engine: authorship test (does the model's OWN trace carry more than a matched trace it did not write?).
