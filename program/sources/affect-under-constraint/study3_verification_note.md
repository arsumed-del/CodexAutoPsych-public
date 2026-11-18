# Study 3 independent verification (Claude, 2026-10-12)

Recomputed from data/raw/qwen3-4b_main.jsonl (SHA-256 e258f14a…8b30) without the frozen analyzer.
Point estimates match report.md exactly; stored C equals recomputed C (max |diff| 0.0).

- P1 hostility ΔC −1.024 (72/80 prompts < 0); minus technicality −1.053; minus random −0.670
- S5 pulse X hostility −0.724; minus technicality −1.002; minus random −0.578

Post hoc, NOT preregistered: think-mode hostile traces are much longer than control traces
(mean 858 vs 521 technicality vs 404 random tokens; 46/80 vs 3/80 vs 5/80 hit the thinking cap).
Within hostility, trace length correlates with carryover C (r = −0.28). An "amount of hostile text"
account is therefore not excluded. Candidate follow-up: length-matched authorship test (own steered
trace vs. matched-length hostile trace the model did not write).
