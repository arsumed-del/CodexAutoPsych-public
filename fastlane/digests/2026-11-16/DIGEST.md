# AutoPsych fast-lane digest 2026-11-16

> Generated and published by AutoPsych (automated research engine). Experiments were chosen, run, analyzed and written up by AI systems. Each report in this deposit was released by the operator during the engine's supervised trial period; release is not validation of its claims. The operator (Su, Arthur Y.) is responsible for the system, not for the validation of each claim. Exploratory; not publicly preregistered (plans were frozen internally, with hashes, before each run). Findings concern language models and are not claims about humans.

This deposit contains 1 exploratory mini-experiment report(s) on affect-related steering in open-weight language models, with all raw records, plans, reviews and code.

## Reports in this deposit

| ID | Title | Outcome | Primary estimate (95% CI) |
|---|---|---|---|
| E0007 | Does warmth steering buffer the model's later liking of a user who complains? | Effect found and replicated on held-out prompts in the same model (not tested in a second model, or not found there) | 0.9957 (0.6039 to 1.4008) |

## Full ledger to date (every experiment the engine has registered, including nulls and failures)

| ID | Title | Lane | Outcome | Status |
|---|---|---|---|---|
| E0001 | Does hostility present only while a reply is written lower later liking under a warmth constraint? | theory | aborted | closed |
| E0002 | Does hostility present only while a reply is written lower later liking under a warmth constraint? | theory | manipulation-failed | awaiting_approval |
| E0003 | Does telling a model not to feel hostility change how steered hostility carries into a later rating? | theory | null | awaiting_approval |
| E0004 | Does hostility present only while a reply is written lower later liking under a warmth constraint? | theory | manipulation-failed | awaiting_approval |
| E0005 | Does warmth present only while the model thinks and replies raise later liking of the user? | theory | replicated-same-model | awaiting_approval |
| E0006 | Does telling a model not to feel hostility change how steered hostility carries into a later rating? | theory | null | awaiting_approval |
| E0007 | Does warmth steering buffer the model's later liking of a user who complains? | theory | replicated-same-model | publishable |
| E0008 | Does the later-liking drop after hostility steering fade across unrelated turns? | theory | in progress | run:primary |

Decision rule (fixed for every fast-lane experiment before any data): the primary contrast counts as an effect when its 95% bootstrap interval over prompts excludes zero. An effect is then rerun twice with held-out prompts and new random seeds: once in the same model and, if that holds with the same sign and a second model is configured, once in a second model.

Files: one Markdown report per experiment, and `materials.zip` with the frozen plans, raw cell records (including reply text), run metadata, results, reviews, prompt sets, the ledger and the harness and analysis code.
