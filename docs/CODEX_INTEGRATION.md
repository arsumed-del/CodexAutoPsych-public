# Optional bounded Codex integration

The supported integration chosen is the official **Codex CLI noninteractive
`codex exec`** interface, not a desktop automation or deprecated API. Each call
starts a fresh ephemeral session, receives one role-scoped context, uses the
read-only sandbox with approvals disabled, and terminates the entire process
group at a wall-clock timeout. Output is an advisory proposal/review; the adapter
does not claim/finish queue tasks or approve/execute a protocol. A human or a
future explicit orchestration adapter must assess it before importing a decision.

The main pipeline deliberately remains offline deterministic role scaffolding.
The CLI itself can use tools; a read-only sandbox is not a guarantee that
untrusted prompts cannot read sensitive files. Use a small sanitized checkout
and vetted inputs, never the Study 3 project or a directory containing secrets.
Do not supply confidential model output without an appropriate data policy.

## Evidence inspected during implementation (2026-10-08)

- Installed CLI: `codex-cli 0.159.0-alpha.3`; inspected `codex exec --help`.
  Supports `--ephemeral`, `--ignore-user-config`, `--sandbox read-only`, `-C`,
  `-o`, stdin prompts, and configuration overrides.
- `codex login status` reported **Logged in using ChatGPT**; no credential
  contents were read or printed. No model call was made.
- Official noninteractive documentation:
  <https://developers.openai.com/codex/noninteractive>.
  Official authentication documentation: <https://developers.openai.com/codex/auth>.
  Direct retrieval of both returned a proxy tunnel **403 Forbidden** here.
- OpenAI's current repository documentation was retrieved successfully:
  <https://github.com/openai/codex/blob/main/docs/exec.md>, which directs readers
  to the noninteractive URL above.
- OpenAI's current configuration schema was retrieved:
  <https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json>.
  It defines `forced_login_method`, `model_provider`, and `approval_policy`.

No undocumented SDK or desktop clicking is required. Full developer-site docs
and a live call were not verified; Mac CLI version/authentication must be checked
locally. The installed alpha version's flags may differ from older stable builds.

## Disabled by default

Inspect `codex --version`, `codex exec --help`, and `codex login status` first.
Create a sanitized context file, e.g.:

```json
{"role":"independent-reviewer","task_id":1,"kind":"audit","payload":{"request":"Review the synthetic fixture design only"},"dependencies":[]}
```

The command below is documentation only; **do not run it without explicit consent
to consume the account's Codex entitlement**:

```sh
python3 -m codexautopsych codex-task --context context.json --workspace /path/to/sanitized/checkout --enable-chatgpt-codex --timeout 120
```

The adapter checks login status, refuses `OPENAI_API_KEY` or `CODEX_API_KEY`
presence, ignores user configuration, pins the OpenAI provider and ChatGPT login
method, and never retries via API keys. If authentication is absent, flags are
unsupported, a quota is exhausted, or the process fails, it stops. No automatic
login, recharge, provider switch, or paid fallback exists. It captures a final
response only, avoiding arbitrary subprocess logs in failure messages.

ChatGPT-authenticated Codex uses the account's applicable Codex entitlement and
limits. **ChatGPT Pro is not API funding.** API-key usage is separately billed by
its API project; this adapter refuses that route. The reported 62,500-credit
balance, its eligibility, expiry, and availability are unverified. No balance
claim is made and no credit is assumed usable. Timeout bounds wall time, not a
monetary budget; this v1 does not enforce a token or account-credit budget. Keep
it disabled until billing/entitlement is verified and approved.

Tests exercise disabled/API-route refusal and mocked subprocess routing/timeout.
They do not establish live model functionality. Actual Codex advisory tasks,
model availability, account quota, and Mac sandbox compatibility remain untested.
