# Synthetic computational pilot

This pilot is an operational, bounded computation loop. It does not make the
live psychology research engine operational. Its three rounds validate software
mechanics on a known scalar recurrence, not psychological theory.

Run in a dedicated lab:

```sh
python3 -m codexautopsych --lab .lab/computational-pilot computational-pilot
```

The controller registers a synthetic protocol, queues deterministic plan,
execution, audit and report tasks, executes the recurrence, independently checks
its trajectories against a closed-form expression, and automatically collects
a hash-bound outcome and synthetic claim. The next round uses the observed
growth bracket to choose its endpoints and midpoint. It stops after three
rounds or when no bracket exists. Questions are never marked solved.

The synthetic equation is `x[t+1] = (1 + gain - damping) * x[t]`, with damping
0.5, initial value 1 and 24 steps. The reset null omits the gain term. The growth
threshold is built into the equation. Recovering it is a regression check,
**not a discovery about affect, people, mental illness or language models**.
The null is a toy software control, not an empirically validated matched control.

The implementation uses bounded standard-library arithmetic. It has no network,
subprocesses, models, GPU work, AI reviewer, code installation or publication.
Author, executor and artifact-auditor coordination identities are distinct;
all are explicitly labeled deterministic scaffolds. The artifact auditor uses
a closed-form calculation separate from the executor's recurrence. This is not
scientific peer review or empirical replication.

`PILOT.md` and `PILOT.json` show results. Original protocols, trajectories and
manifests are preserved under `runs/`. Outcome snapshots are under
`research/outcomes/`, and the local claims ledger identifies their scope as
synthetic software behavior only. No manual copy/import step is needed.

Stable run IDs prevent rerunning on restart. A second invocation verifies and
reuses the same artifacts. An interrupted launch is never reset. Altered outputs
block collection, and the original execution lock remains mandatory. The command
refuses a lab containing unrelated tasks or experiments, so it cannot drain the
live research queue or alter another experiment.

The resource gate for live research workers remains enforced. This pilot uses
the separately permitted synthetic execution path, not a gate override for
language-model work. The live worker environment and a separately approved live
experiment runner remain outstanding. No new real protocol is approved by this
pilot, and no software test count represents scientific evidence.
