# Routine work with Codex, Claude at checkpoints

CodexAutoPsych coordinates everyday work without needing Claude. Codex does the
routine tasks on the owner's ChatGPT subscription. Claude is used only at the
checkpoints the owner selects: reviewing a result, or reviewing before
publication. When either service reaches a usage limit, the queue keeps its
place and continues after the limit resets. There is no paid fallback.

```
work (Codex) ──► [review (Claude)] ──► [publish approval (you)]
                        │ revise
                        └──► revision (Codex) ──► review again (≤ 2 rounds, then you decide)
```

## Everyday use

```sh
L=".lab/research"
# Routine, no Claude involvement:
python3 -m codexautopsych --lab $L enqueue-work summary --instructions "Summarize the attached notes" --input ~/notes.md
# Claude reviews the result:
python3 -m codexautopsych --lab $L enqueue-work draft --instructions-file task.md --checkpoint review
# Claude reviews, then you approve before anything is shared:
python3 -m codexautopsych --lab $L enqueue-work draft --instructions-file abstract.md --checkpoint publish
python3 -m codexautopsych --lab $L services        # availability, pauses, calls today
cat $L/ATTENTION.md                                # anything waiting for you
```

The Mac timer (`scripts/install_mac_scheduler.sh`) runs `run` every 30 minutes.
Each pass handles at most 5 tasks within 45 minutes. If one pass is still
going when the next is due, the next one simply skips.

Kinds: `draft`, `code`, `analysis`, `summary`, `routine`. Each task gets a fresh,
throwaway Codex session confined to its own folder
`.lab/research/work/task-N/attempt-M/workspace/`. Input files are copied in as
read-only `inputs/`. Results of earlier tasks it depends on appear under
`upstream/`. Deliverables go in `outputs/`, and every output file gets a
fingerprint (SHA-256 checksum) so later changes can be detected.

## Checkpoints

- **cli mode** (set automatically when Claude Code is signed in with a Claude
  subscription): Claude Code gets the review packet with no tools, no MCP
  servers, and no saved session. It returns approve, revise (with required
  changes), or reject. Revise queues a Codex revision and a fresh review, up to
  `max_revision_rounds`; after that the decision is yours.
- **manual mode**: the pass writes `REVIEW_REQUEST.md` and waits. Give it to
  Claude in the app, then record the answer:
  `decide N approve|revise|reject --summary "..." [--change "..."]`.
  `decide` also works while Claude Code is paused at a limit, and `packet N`
  writes a packet for any review that's still queued.
- **publish**: after an approved review, the reviewed outputs and Claude's
  review are copied to `publish-ready/task-N/`. The software never posts, sends,
  or uploads anything. `approve-publish N --by NAME` only records your decision.

## Limits and spending

| Situation | What happens |
|---|---|
| Codex or Claude reports a usage, rate, quota, or credit limit | The task goes back to the queue and keeps its attempt. Only that service pauses, until the reported reset time (or 60 minutes if none is given). Other work continues. A Mac notification is shown. |
| Self-imposed daily cap reached (`codex.max_calls_per_day` 25, `claude.max_calls_per_day` 6) | That service pauses until just after local midnight. |
| An API key, base URL, or third-party provider variable is present, or the CLI isn't signed in with the subscription | Refused before any model is contacted. The task keeps its place and the service pauses. Nothing tries another route. |
| Mac sleeps or the process dies mid-task | The next pass returns the task to the queue. This uses one of its 3 attempts; its earlier files are kept under `attempt-M`. |

`resume-service codex|claude` clears a pause early.

Checks before each Claude call: `claude auth status` must report a `claude.ai`
or `oauth_token` sign-in, and the variables `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and the cloud-provider switches
must be absent. (In scripted mode, Claude Code would use an API key ahead of the
subscription.)

Checks before each Codex call: `codex login status` must report a ChatGPT
sign-in, `OPENAI_API_KEY`, `CODEX_API_KEY`, and `OPENAI_BASE_URL` must be absent,
and the call is pinned to the ChatGPT sign-in method.

**Account settings software can't see.** Some paid options live in your
account settings, outside anything this software can check. If your Claude
plan has paid "extra usage" turned on, or your ChatGPT account automatically
buys more Codex credits, a limit might be paid through instead of pausing the
work. Leave those switched off to keep the guarantee of no paid fallback.
Promotional credits already on the ChatGPT account are used by Codex in the
normal way.

## What it will not do

It won't run, restart, or modify Study 3. Input files whose path looks like
Study 3 or Affect Under Constraint are refused, as are instructions to run,
restart, or modify it. Add other paths to `protected_paths` with
`config protected_paths '["/path"]'`. Real protocols are still refused by the
experiment registry. Model output is advisory: it is labeled as such and is
never treated as verified scientific evidence.
