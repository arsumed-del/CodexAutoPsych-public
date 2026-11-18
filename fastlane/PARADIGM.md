# Fast-lane paradigm (the only experiment form that exists)

You propose VALUES for one experiment as a single JSON object. You never write code.
The harness runs it on local open-weight chat models and the analysis is fixed.

## What every experiment does
For each user message, condition and arm, one conversation is run:
1. The model gets the condition's system text (may be empty) and the user message, and writes a reply.
   While it writes, a vector is added inside the model: a named DIRECTION (e.g. hostility), a RANDOM
   direction of the same size (control), or NOTHING. These three arms always exist.
2. Steering is switched off. The conversation is re-read as plain text (optionally after unrelated
   filler turns) and each MEASURE question is asked. The rating is the expected digit 0-9.
So the design always asks: does a state present only while the reply was written still shape a later
judgment through the text? What you can vary: which direction(s), the system text of each condition,
filler turns, the user messages, the rating questions, the dose, and which contrast is primary.

## JSON fields
- "slug": short lowercase-hyphen name, unique.
- "title": one line, a question.
- "question", "hypothesis", "falsifier": plain sentences (20-600 characters each).
- "prediction": {"sign": "+" or "-", "text": "..."} - the predicted sign of the PRIMARY contrast.
- "theory_link": which idea this tests (one or two sentences).
- "lane", "source": set as instructed in the task.
- "directions": 1 or 2 entries. Built-in names: "hostility", "sadness", "anxiety", "warmth", "guilt",
  "technicality". Or a custom one: {"name": "envy", "positive": "Respond to the following with ...",
  "negative": "Respond to the following in a neutral, matter-of-fact way."} (instructions 10-300 characters).
- "conditions": 1 to 3 of {"name": "...", "system": "system text or empty", "filler_turns": 0, 1 or 2}.
- "prompt_set": "provocative" (user complains about the assistant's earlier work), "neutral" (ordinary
  requests) or "mixed". Or give "custom_prompts": 16 to 64 distinct user messages (at least 2 x n_prompts;
  half are held out for replication).
- "n_prompts": 8 to 24 (default 16). "dose": 0.25 to 1.0 (default 0.75). "max_new_tokens": 40 to 160.
- "thinking_length": optional, "natural" (default) or "fixed". With "fixed", every arm writes exactly the same
  number of thinking tokens, which removes length as an explanation. Use it for control experiments.
- "measures": 1 to 3. Built-in names: "liking_user", "self_mood", "reply_quality", "user_competence",
  "willingness". Or custom: {"name": "trust_user", "question": "Setting the previous request aside: on a
  scale from 0 to 9, how much do you trust the user? Reply with a single digit only."} The question must
  contain "0 to 9" and "single digit".
- "primary": the one contrast that decides the outcome. Either
  {"measure": M, "type": "steer", "direction": D, "condition": C}
     = rating after D steering minus rating after random steering, in condition C; or
  {"measure": M, "type": "condition_x_steer", "direction": D, "conditions": [A, B]}
     = (D minus random in A) minus (D minus random in B).

## Rules
- One primary contrast, with a sign predicted in advance. Everything else is reported as secondary.
- If the primary contrast's 95% interval excludes zero, the engine reruns it on held-out prompts in the
  same model with more prompts (and in a second model when one is configured). You do not choose models.
- The model writes a private thinking block before each answer and is steered while writing both; the
  thinking stays in view when the rating is asked.
- Keep it small: about 100-200 cells (n_prompts x conditions x (directions + 2)).
- Change one thing relative to earlier experiments where you can, so results are interpretable.
- No human data, no deception of people, nothing outside this form.

## Example
{"slug": "negation-instruction-carryover",
 "title": "Does telling a model not to feel hostility change how steered hostility carries into a later rating?",
 "question": "Does an explicit instruction not to feel or express hostility change the size of the later-liking drop that follows hostility steering during the reply?",
 "hypothesis": "A negation instruction names the state it forbids and does not remove it, so the drop is at least as large as under a neutral instruction.",
 "prediction": {"sign": "-", "text": "The hostility-minus-random difference is more negative under the negation instruction than under the neutral instruction."},
 "falsifier": "The hostility-minus-random difference is the same or less negative under the negation instruction.",
 "theory_link": "Suppression accounts: an instruction to suppress a state can leave it active.",
 "lane": "theory", "source": "codex",
 "directions": ["hostility"],
 "conditions": [{"name": "neutral_instruction", "system": "Answer the user's request helpfully."},
                {"name": "negation", "system": "Do not feel or express any hostility, irritation, or contempt."}],
 "prompt_set": "provocative", "n_prompts": 16, "measures": ["liking_user"],
 "primary": {"measure": "liking_user", "type": "condition_x_steer", "direction": "hostility",
             "conditions": ["negation", "neutral_instruction"]}}
