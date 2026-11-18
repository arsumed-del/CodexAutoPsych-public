# Writing guide for a fast-lane report

You write only the interpretive text. The method and every table are printed from results.json by the
engine, so do not restate tables. Write outputs/text.json as one JSON object with exactly these keys:

- "title" (15-160 characters): states the finding or the question plainly. No hype words.
- "abstract" (200-1500 characters): question, what was done in one sentence, the primary result with its
  estimate and interval for each run, and the outcome in plain words.
- "interpretation" (200-2200 characters): what the result does and does not show. Address: whether the
  direction of the effect matched the registered prediction (results.json: matches_prediction); whether the
  random-direction control and the no-steering arm differ (if random steering alone moves the rating, say
  so); whether reply length or the re-read projection changed (the "manipulation" block); and whether the
  replications agree.
- "takeaway" (40-300 characters): one or two sentences a reader could repeat accurately.
- "limitations" (0-5 short strings): limits specific to THIS experiment (not the standing ones about small
  samples and multiple experiments, which are printed automatically).
- "open_questions" (0-3 short strings): the next experiments this result suggests.

Rules:
- Every decimal number must be copied from results.json exactly as it appears there, or rounded to fewer
  places. A number that is not in results.json causes the text to be refused.
- Calibrate to the outcome field. "replicated-two-models": say it held in two models on held-out prompts,
  in this setup. "replicated-same-model": say the second model did not show it. "not-replicated": the
  first-run effect did not hold; treat it as likely chance. Never describe a non-replicated or null result
  as support for the hypothesis.
- These are language models. Do not claim the model feels anything; describe ratings and text. Do not draw
  conclusions about humans or clinical practice.
- Plain, concrete sentences. No exclamation marks, no "groundbreaking", "novel", "striking", "remarkable".
