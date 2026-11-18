# The research director

The director queues research planning from the local charter and source notes.
It is **disabled by default**. This build has not enabled services, changed any
scheduler, or run a real worker. Tests use explicitly synthetic, mocked workers.
The authority for automatic decisions is [the charter](../program/CHARTER.md),
owner delegation of 2026-10-08.

When Arthur enables `program.enabled` in the lab configuration, each routine
pass checks whether a director task was created in the last seven days. It
queues at most one per interval. Enabling the director does not enable Codex:
that separate existing subscription setting still controls whether work runs.
Claude uses the existing selected CLI or manual review mode. All subscription
route guards, service pauses, daily limits and revision limits still apply.

The director receives copies of the charter, files under `program/sources/`,
and a fresh `program/state.json` export of pending claims and local outcomes.
Its immutable input snapshot includes a source map so equal filenames in
separate folders keep their identities. Sources must be local regular files;
symlinks and protected study paths are refused. Only owner-placed copies of
external results belong here.

Zotero (theory lane only) is read from a fresh copy of `~/Zotero`, read-only. Zotero holds
citations and links; full text is fetched on demand (`codexautopsych/fulltext.py`, setting
`fulltext.enabled`). The items about to reach planning (the `zotero.max_items` most recently
added) count as relevant. For each one without text, the engine resolves its DOI/URL to a free
legal copy, trying arXiv, then Unpaywall (only when `fulltext.unpaywall_email` is set), then
Semantic Scholar, and saves the PDF and its extracted text under `.lab/research/fulltext/`,
never in Zotero storage. At most `fulltext.max_fetches_per_pass` items are tried per pass.
Items with no downloadable open copy are listed in `.lab/research/NEEDS_MANUAL_PDF.md`
(rechecked after `fulltext.retry_days`); a PDF saved as
`.lab/research/fulltext/manual/<Zotero key>.pdf` is read instead. Text extraction uses macOS
PDFKit; fetched text is untrusted source material and never reaches the blind lane.

The director proposes up to five preregistrations, with theory citations,
exact statistics, capacity-matched nulls, controls, dose/threshold designs,
decision rules, stopping rules and GPU-hour estimates. Estimates authorize no
spending. Each valid proposal becomes a refinement task with an independent
Claude review checkpoint. Missing or malformed proposal metadata is logged
and skipped. Duplicate slugs cannot replace an existing study. A revision
keeps its study identity; the final approved revision supplies the frozen text.

After an approved review, an in-silico proposal is recorded under
`program/studies/<slug>/` with `PREREG.md` and `APPROVAL.json`. Approval records
cite the charter, review task and SHA-256 of the exact reviewed bytes. The
claims ledger receives a **pending hypothesis**, not a finding. These are
“internally preregistered (local hash) - not publicly deposited.” If automatic
in-silico approval is disabled, or either the original or reviewed proposal
is not in-silico, the review stays `awaiting_human`. Repeating the review
approval alone does not override that policy. Arthur must resolve the scope
and authorize any new nondelegated workflow separately.

An approved preregistration queues a harness build with another independent
review. The worker delivers `outputs/harness/` and `outputs/VALIDATION.json`.
Validation must declare `synthetic`, `planted_effect_passed` and
`null_effect_passed` as true, include a nonempty `evidence` explanation (a string or an object with a nonempty string `explanation`), and
provide `harness_sha256`, a mapping from every harness-relative filename to
its SHA-256. The reviewer checks synthetic validation and protocol fidelity.
The coordinator checks the recorded output hashes and validation manifest,
copies the reviewed harness, and adds the file hashes, aggregate code hash,
validation hash and build review ID to `APPROVAL.json`. It does not execute
worker-generated code or independently prove the worker's validation claims.
Missing validation or changed artifacts require owner attention.

Approved and built studies stop at **ready to run**. Experiment execution is
not implemented, even if `program.execute_experiments` is set to true. The
read-only `gpu_free(cfg)` helper checks a fakeable `ps -axo command` response
and the lab execution lock; configuration loaded through `load_config` carries
the lab location. Missing lock files or failed probes return false. The check
never signals a process and is not a launch authorization. It is not invoked
by the planning/build flow. A future executor must hold the actual execution
lock and enforce the resource rule; this helper cannot reserve GPU resources.

Arthur must deposit preregistrations publicly, approve any publication, and
approve human research, spending or new services. `ATTENTION.md` lists studies
ready for public deposition, execution readiness and tasks awaiting decisions.
Nothing posts, pushes, uploads, or marks a study published. The external
protected study, its chat and its scheduled task remain unconnected and untouched.

SQLite under `.lab/` tracks completed follow-ups so restarts do not duplicate
claims, refinement tasks or build tasks. Original task outputs and frozen
preregistrations are preserved. Conflicting files or a partly completed
filesystem write require owner reconciliation; they are never silently
replaced. Use a single lab for this checkout's program: `program/studies/` is
shared by the checkout, while queue locks are scoped to the lab. Generated
state and study artifacts are ignored by Git and must be preserved locally.

See [Research engine](RESEARCH_ENGINE.md) for optional outcome-driven replanning,
a persistent question list, and a cap on open studies. These rules apply only
when `research.enabled` is also true.
