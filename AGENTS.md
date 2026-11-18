# Project instructions

Use the existing checkout. Cloud tasks are already isolated; do not create Git
worktrees unless the user explicitly requests one.

Python 3.11+, standard-library runtime. Validate with:
`python3 -m unittest discover -s tests -v` and
`python3 -m codexautopsych --lab .lab/demo demo`.

Preserve approved protocols and original raw outputs. Keep deterministic runners
separate from AI workers. Label every synthetic fixture and mocked/scaffold
worker. Never imply a scaffold is a live AI agent. Reviewer identities and task
contexts must remain independent of authors and execution owners.

Study 3 is complete and published. Its results may be read (copies are in
program/sources/), but never rerun, restart, modify or duplicate it or its files.

Start every session by reading STATUS.md (one-page status; its lower half is
rewritten automatically) and docs/LOG.md. When you change anything in this
program, add a dated entry to docs/LOG.md: who, what changed, what's next.
Keep the blind lane blind: never add owner theory, owner papers or Zotero
material to program/CHARTER_BLIND.md or the blind lane's inputs.

New real protocols, publication, and spending beyond an established budget need
user approval. Do not enable paid API calls. No credential values in code, logs,
commits, reports, or chat. ChatGPT Pro and reported promotional credits do not
establish API funding or eligibility.

Routine work (`codexautopsych/routine.py`) uses Codex only through an existing
ChatGPT login and Claude only through an existing claude.ai subscription login,
only at owner-selected checkpoints. Keep these invariants and their tests:
paid-route variables refused before any model call; a usage limit requeues the
task without spending an attempt and pauses only that service; no fallback to
another provider, model route, or API key; software never publishes. Validate
with `python3 -m unittest discover -s tests -v` (fake CLIs; no real calls).

Keep local SQLite state under ignored `.lab/`; fixture artifacts may be committed
only when clearly labeled synthetic. Never reset interrupted runs to registered
or weaken the execution lock to make a retry pass.
